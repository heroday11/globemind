from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import core_pipeline.event_evolution_chain as event_evolution_chain  # noqa: E402
from core_pipeline.event_evolution_chain import (  # noqa: E402
    _build_story_relations,
    _date_range_gap_days,
    _filter_ambiguous_micro_chapters,
    _pick_dominant_trigger,
    _split_impure_chapter,
    _split_large_macro_group,
    _split_pair_into_chapters,
    _stable_story_int,
)


def test_get_conn_resolves_password_at_connection_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = object()
    connect_calls: list[dict[str, object]] = []
    password_name_calls: list[tuple[str, ...]] = []

    class PsycopgStub:
        @staticmethod
        def connect(**kwargs: object) -> object:
            connect_calls.append(kwargs)
            return connection

    def password_stub(*names: str) -> str:
        password_name_calls.append(names)
        return "unit-test-password"

    monkeypatch.setattr(event_evolution_chain, "psycopg2", PsycopgStub)
    monkeypatch.setattr(event_evolution_chain, "require_database_password", password_stub)

    assert event_evolution_chain.get_conn() is connection
    assert password_name_calls == [("PG_PASSWORD", "DB_PASSWORD")]
    assert connect_calls == [
        {**event_evolution_chain.DB_CONFIG, "password": "unit-test-password"}
    ]


def _event(
    cluster_id: str,
    day: int,
    hour: int,
    event_type: str = "military",
    trigger: str = "strike",
    time_precision: str = "date_midpoint",
) -> dict:
    return {
        "cluster_id": cluster_id,
        "article_count": 2,
        "event_type": event_type,
        "tone": "negative",
        "trigger_verb": trigger,
        "initiator": "US",
        "target": "Iran",
        "start_date": date(2026, 6, day),
        "end_date": date(2026, 6, day),
        "display_time": datetime(2026, 6, day, hour, 0, tzinfo=timezone.utc),
        "time_precision": time_precision,
    }


class TestStableStoryIds:
    def test_stable_story_int_is_deterministic(self):
        value = "chapter|us|iran|c1|c2|c3"

        assert _stable_story_int(value) == _stable_story_int(value)
        assert _stable_story_int(value) != _stable_story_int(value + "|alt")


class TestTriggerAggregation:
    def test_pick_dominant_trigger_prefers_frequency_over_first_seen(self):
        verbs = ["warns", "launches strikes", "warns", ""]

        assert _pick_dominant_trigger(verbs) == "warns"


class TestChapterization:
    def test_split_pair_into_chapters_breaks_same_day_overcrowding(self):
        events = [
            _event("c1", 1, 1, trigger="strike"),
            _event("c2", 1, 3, trigger="strike"),
            _event("c3", 1, 5, trigger="strike"),
            _event("c4", 1, 7, trigger="strike"),
            _event("c5", 1, 9, trigger="sanction"),
            _event("c6", 1, 11, trigger="sanction"),
        ]
        embeddings = {event["cluster_id"]: [1.0, 0.0, 0.0] for event in events}

        chapters = _split_pair_into_chapters(events, embeddings, max_gap_days=30)

        assert len(chapters) == 2
        assert [len(chapter) for chapter in chapters] == [4, 2]

    def test_split_impure_chapter_breaks_mixed_type_sequence(self):
        chapter = [
            _event("c1", 1, 1, event_type="military", trigger="strike"),
            _event("c2", 1, 3, event_type="military", trigger="strike"),
            _event("c3", 2, 2, event_type="diplomacy", trigger="talks"),
            _event("c4", 2, 4, event_type="diplomacy", trigger="proposal"),
        ]
        embeddings = {
            "c1": [1.0, 0.0, 0.0],
            "c2": [1.0, 0.0, 0.0],
            "c3": [0.0, 1.0, 0.0],
            "c4": [0.0, 1.0, 0.0],
        }

        parts = _split_impure_chapter(chapter, embeddings)

        assert [len(part) for part in parts] == [2, 2]

    def test_split_pair_into_chapters_drops_ambiguous_mixed_micro_chapter(self):
        events = [
            _event("c1", 1, 1, event_type="military", trigger="strike"),
            _event("c2", 1, 3, event_type="trade_conflict", trigger="tariff"),
            _event("c3", 1, 5, event_type="diplomacy", trigger="talks"),
        ]
        embeddings = {
            "c1": [1.0, 0.0, 0.0],
            "c2": [0.0, 1.0, 0.0],
            "c3": [0.0, 0.0, 1.0],
        }

        chapters = _split_pair_into_chapters(events, embeddings, max_gap_days=30)

        assert chapters == []

    def test_filter_ambiguous_micro_chapters_salvages_same_type_run(self):
        chapter = [
            _event("c1", 1, 1, event_type="diplomacy", trigger="talks"),
            _event("c2", 1, 3, event_type="military", trigger="strike"),
            _event("c3", 1, 5, event_type="military", trigger="strike"),
            _event("c4", 1, 7, event_type="trade_conflict", trigger="tariff"),
        ]
        chapters = _filter_ambiguous_micro_chapters([chapter])

        assert [len(chapter) for chapter in chapters] == [2]
        assert [event["cluster_id"] for event in chapters[0]] == ["c2", "c3"]


class TestMacroGrouping:
    def test_split_large_macro_group_breaks_long_chronology(self):
        chapter_by_id = {
            1: {
                "start_time": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "end_time": datetime(2026, 1, 2, tzinfo=timezone.utc),
                "entities": {"us", "iran"},
                "entity_pair_set": frozenset({"us", "iran"}),
                "dominant_type": "military",
                "embedding": [1.0, 0.0],
            },
            2: {
                "start_time": datetime(2026, 1, 5, tzinfo=timezone.utc),
                "end_time": datetime(2026, 1, 6, tzinfo=timezone.utc),
                "entities": {"us", "iran"},
                "entity_pair_set": frozenset({"us", "iran"}),
                "dominant_type": "military",
                "embedding": [1.0, 0.0],
            },
            3: {
                "start_time": datetime(2026, 2, 20, tzinfo=timezone.utc),
                "end_time": datetime(2026, 2, 21, tzinfo=timezone.utc),
                "entities": {"us", "iran"},
                "entity_pair_set": frozenset({"us", "iran"}),
                "dominant_type": "military",
                "embedding": [1.0, 0.0],
            },
        }

        groups = _split_large_macro_group([1, 2, 3], chapter_by_id)

        assert groups == [[1, 2], [3]]

    def test_build_story_relations_emits_backbone_and_context_layers(self):
        chapter_by_id = {
            1: {
                "start_time": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "end_time": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "pair": ("us", "iran"),
                "dominant_type": "military",
                "dominant_trigger": "strike",
            },
            2: {
                "start_time": datetime(2026, 1, 3, tzinfo=timezone.utc),
                "end_time": datetime(2026, 1, 3, tzinfo=timezone.utc),
                "pair": ("us", "iran"),
                "dominant_type": "military",
                "dominant_trigger": "strike",
            },
            3: {
                "start_time": datetime(2026, 1, 4, tzinfo=timezone.utc),
                "end_time": datetime(2026, 1, 4, tzinfo=timezone.utc),
                "pair": ("us", "venezuela"),
                "dominant_type": "diplomacy",
                "dominant_trigger": "talks",
            },
        }
        reference_candidates = {
            1: [{"neighbor_story_id": 3, "reason": "shared_entity_context", "score": 0.81}],
            3: [{"neighbor_story_id": 1, "reason": "shared_entity_context", "score": 0.81}],
        }
        macro_groups = {0: [1, 2, 3]}
        pair_story_groups = {
            ("us", "iran"): [1, 2],
            ("us", "venezuela"): [3],
        }

        rows = _build_story_relations(
            chapter_by_id,
            reference_candidates,
            macro_groups,
            pair_story_groups,
        )

        pairs = {(row["story_id"], row["neighbor_story_id"], row["relation_type"], row["layer"]) for row in rows}
        assert (1, 2, "pair_sequence", "backbone") in pairs
        assert (1, 3, "context", "context") in pairs
        assert any(row["relation_type"] == "macro_sequence" and row["layer"] == "backbone" for row in rows)

    def test_build_story_relations_skips_long_gap_pair_sequence(self):
        chapter_by_id = {
            1: {
                "start_time": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "end_time": datetime(2026, 1, 2, tzinfo=timezone.utc),
                "pair": ("us", "china"),
                "dominant_type": "diplomacy",
                "dominant_trigger": "talks",
            },
            2: {
                "start_time": datetime(2026, 3, 20, tzinfo=timezone.utc),
                "end_time": datetime(2026, 3, 21, tzinfo=timezone.utc),
                "pair": ("us", "china"),
                "dominant_type": "diplomacy",
                "dominant_trigger": "talks",
            },
        }

        rows = _build_story_relations(
            chapter_by_id,
            reference_candidates={},
            macro_groups={},
            pair_story_groups={("us", "china"): [1, 2]},
        )

        assert not any(row["relation_type"] == "pair_sequence" for row in rows)

    def test_build_story_relations_drops_weak_context_candidates(self):
        chapter_by_id = {
            1: {
                "start_time": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "end_time": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "pair": ("us", "iran"),
                "dominant_type": "military",
                "dominant_trigger": "strike",
            },
            2: {
                "start_time": datetime(2026, 1, 2, tzinfo=timezone.utc),
                "end_time": datetime(2026, 1, 2, tzinfo=timezone.utc),
                "pair": ("us", "venezuela"),
                "dominant_type": "military",
                "dominant_trigger": "strike",
            },
        }
        reference_candidates = {
            1: [{"neighbor_story_id": 2, "reason": "weak_context", "score": 0.55}],
            2: [{"neighbor_story_id": 1, "reason": "weak_context", "score": 0.55}],
        }

        rows = _build_story_relations(
            chapter_by_id,
            reference_candidates,
            macro_groups={},
            pair_story_groups={},
        )

        assert rows == []


class TestDateRangeGap:
    def test_date_range_gap_days_handles_overlap_and_distance(self):
        start_a = datetime(2026, 6, 1, tzinfo=timezone.utc)
        end_a = datetime(2026, 6, 3, tzinfo=timezone.utc)
        start_b = datetime(2026, 6, 3, tzinfo=timezone.utc)
        end_b = datetime(2026, 6, 5, tzinfo=timezone.utc)
        start_c = datetime(2026, 6, 8, tzinfo=timezone.utc)
        end_c = datetime(2026, 6, 9, tzinfo=timezone.utc)

        assert _date_range_gap_days(start_a, end_a, start_b, end_b) == 0
        assert _date_range_gap_days(start_a, end_a, start_c, end_c) == 5
