from __future__ import annotations

import pytest

from api.features.ground_news.source_profile import build_source_profile_contract
from api.routes.story_graph import (
    _blindspot_assessment,
    _make_story_comparison,
    get_ground_news_source_profile,
    search_ground_news_product,
)


def _profile(**overrides: object) -> dict[str, object]:
    profile: dict[str, object] = {
        "domain": "example.test",
        "source_name": "Example News",
        "country": "Exampleland",
        "region": "global",
        "region_code": "GL",
        "source_type": "wire_service",
        "ownership_type": "wire_service",
        "geo_alignment": "western",
        "political_leaning": "center_left",
        "credibility_tier": "high",
        "label_confidence": "high",
        "review_status": "reviewed",
        "profile_version": "media_profile_seed_v1",
        "evidence_url": "https://directory.example.test/source/example",
        "evidence_note": (
            "seeded_from_historical_wave1_targets; "
            "ground_news_rating_v1: third-party directory labels; "
            "future_magic_v9: must not be trusted"
        ),
        "article_count_snapshot": 42,
        "updated_at": "2026-06-26T12:00:00Z",
    }
    profile.update(overrides)
    return profile


def test_known_directory_method_builds_bounded_non_reliability_method_card() -> None:
    result = build_source_profile_contract(_profile(), fallback_domain="fallback.test")

    assert "evidence_note" not in result
    assert result["domain"] == "example.test"
    assert result["profile_version"] == "media_profile_seed_v1"
    assert result["political_leaning"] == "center_left"
    assert result["credibility_tier"] == "high"
    assert result["label_confidence"] == "high"
    card = result["method_card"]
    assert card["schema_version"] == "ground-news-source-profile-method-card-v1"
    assert card["profile_contract_version"] == "ground-news-source-profile-v1"
    assert card["catalog_profile_version"] == "media_profile_seed_v1"
    assert card["catalog_profile_version_state"] == "recognized"
    assert card["overall_state"] == "partial_unknown"
    assert card["unknown_method_count"] == 1
    assert card["method_input_truncated"] is False
    assert [method["method_id"] for method in card["methods"]] == [
        "historical_wave1_seed_v1",
        "ground_news_rating_v1",
    ]
    assert all("future_magic" not in str(value) for value in card.values())
    assert card["assurance"] == {
        "state": "catalog_labels_only",
        "independent_validation": "not_performed",
        "source_reliability_conclusion": "not_established",
        "fact_accuracy_conclusion": "not_established",
        "reason_code": "DIRECTORY_LABELS_ARE_NOT_RELIABILITY_FINDINGS",
    }
    assert card["field_dispositions"]["credibility_tier"] == {
        "state": "third_party_catalog_label",
        "reason_code": "CONTROLLED_THIRD_PARTY_DIRECTORY_METHOD",
        "method_ids": ["ground_news_rating_v1"],
    }
    assert card["field_dispositions"]["political_leaning"] == {
        "state": "third_party_catalog_label",
        "reason_code": "CONTROLLED_THIRD_PARTY_DIRECTORY_METHOD",
        "method_ids": ["ground_news_rating_v1"],
    }


def test_unknown_profile_method_and_quality_values_fail_closed_without_echoing_them() -> None:
    result = build_source_profile_contract(
        _profile(
            profile_version="trusted_seed_v99",
            review_status="certified",
            source_type="elite_source",
            ownership_type="trusted_owner",
            geo_alignment="perfectly_neutral",
            political_leaning="objective",
            credibility_tier="very_high",
            label_confidence="certain",
            evidence_note="ai_certified_v9: flawless factual source",
        ),
        fallback_domain="fallback.test",
    )

    assert result["profile_version"] is None
    assert result["review_status"] == "unknown"
    assert result["source_type"] == "unknown"
    assert result["ownership_type"] == "unknown"
    assert result["geo_alignment"] == "unknown"
    assert result["political_leaning"] == "unknown"
    assert result["credibility_tier"] == "unknown"
    assert result["label_confidence"] == "unknown"
    assert "evidence_note" not in result
    card = result["method_card"]
    assert card["catalog_profile_version"] is None
    assert card["catalog_profile_version_state"] == "unknown"
    assert card["overall_state"] == "unknown"
    assert card["methods"] == []
    assert card["unknown_method_count"] == 1
    assert "ai_certified" not in str(card)
    assert card["field_dispositions"]["credibility_tier"] == {
        "state": "unknown",
        "reason_code": "PROFILE_VERSION_UNKNOWN",
        "method_ids": [],
    }


def test_structural_or_institutional_labels_never_become_factual_reliability_ratings() -> None:
    structural = build_source_profile_contract(
        _profile(
            political_leaning="unknown",
            credibility_tier="high",
            evidence_note=(
                "seeded_from_historical_wave1_targets; "
                "structural_review_v1: wire service credibility seed"
            ),
        )
    )
    assert structural["political_leaning"] == "unknown"
    assert structural["credibility_tier"] == "unknown"
    assert structural["label_confidence"] == "unknown"
    assert structural["method_card"]["field_dispositions"]["credibility_tier"] == {
        "state": "unknown",
        "reason_code": "CONTROLLED_RATING_METHOD_MISSING",
        "method_ids": [],
    }

    institutional = build_source_profile_contract(
        _profile(
            political_leaning="state_aligned",
            credibility_tier="medium",
            label_confidence="medium",
            evidence_note=(
                "seeded_from_historical_wave1_targets; "
                "institutional_override_v1: ownership evidence only"
            ),
        )
    )
    assert institutional["political_leaning"] == "state_aligned"
    assert institutional["credibility_tier"] == "unknown"
    assert institutional["label_confidence"] == "medium"
    assert institutional["method_card"]["field_dispositions"]["political_leaning"] == {
        "state": "institutional_catalog_label",
        "reason_code": "CONTROLLED_INSTITUTIONAL_ALIGNMENT_METHOD",
        "method_ids": ["institutional_override_v1"],
    }


def test_oversized_method_input_does_not_release_quality_labels() -> None:
    result = build_source_profile_contract(
        _profile(evidence_note="ground_news_rating_v1: " + ("x" * 9000))
    )

    assert result["credibility_tier"] == "unknown"
    assert result["political_leaning"] == "unknown"
    card = result["method_card"]
    assert card["method_input_truncated"] is True
    assert card["methods"] == []
    assert card["field_dispositions"]["credibility_tier"]["reason_code"] == (
        "METHOD_INPUT_OUT_OF_BOUNDS"
    )


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "javascript:alert(1)",
        "https://reviewer:secret@example.test/method",
        "https://example.test\\@attacker.test/method",
        "https://example.test/method\nnext",
    ],
)
def test_evidence_url_fails_closed_before_entering_public_profile_contract(
    unsafe_url: str,
) -> None:
    result = build_source_profile_contract(_profile(evidence_url=unsafe_url))

    assert result["evidence_url"] is None


def test_evidence_url_is_canonicalized_without_query_or_fragment() -> None:
    result = build_source_profile_contract(
        _profile(
            evidence_url=(
                "HTTPS://Directory.Example.Test/source/example?token=secret#private"
            )
        )
    )

    assert result["evidence_url"] == "https://directory.example.test/source/example"


def test_story_comparison_source_table_uses_controlled_profile_contract() -> None:
    raw_note = (
        "seeded_from_historical_wave1_targets; "
        "ground_news_rating_v1: third-party directory labels; "
        "private reviewer prose"
    )
    comparison = _make_story_comparison(
        {"cluster_id": "story-1", "title": "Story", "article_count": 1},
        {
            "source_count": 1,
            "article_count": 1,
            "reviewed_known_political_source_count": 1,
        },
        [],
        [
            _profile(
                news_id=7,
                title="Evidence headline",
                url="https://news.example.test/item?session=secret#reader",
                evidence_url=(
                    "https://directory.example.test/source/example?token=secret"
                ),
                evidence_note=raw_note,
                profile_updated_at="2026-06-26T12:00:00Z",
            )
        ],
    )

    row = comparison["source_table"][0]
    assert "evidence_note" not in row
    assert raw_note not in str(comparison)
    assert row["url"] == "https://news.example.test/item"
    assert row["evidence_url"] == "https://directory.example.test/source/example"
    assert row["credibility_tier"] == "high"
    assert row["political_group"] == "left"
    assert row["method_card"]["assurance"]["fact_accuracy_conclusion"] == (
        "not_established"
    )


def test_blindspot_directory_labels_are_not_presented_as_factuality_findings() -> None:
    result = _blindspot_assessment(
        {"article_count": 10, "source_count": 5},
        {
            "article_count": 10,
            "source_count": 5,
            "reviewed_known_political_source_count": 5,
            "credibility_tier_counts": {"low": 8, "unknown": 2},
            "political_group_pct_reviewed_known_sources": {
                "left": 50,
                "center": 0,
                "right": 50,
            },
        },
    )

    assert "low_factuality_risk_pct" not in result
    assert result["directory_low_or_unknown_label_pct"] == 100.0
    assert result["directory_label_assurance"] == {
        "state": "catalog_composition_only",
        "source_reliability_conclusion": "not_established",
        "fact_accuracy_conclusion": "not_established",
    }
    assert any("第三方目录标签" in reason for reason in result["reasons"])
    assert "事实性风险" not in str(result)


class _FakeResult:
    def __init__(self, *, first: object = None, rows: list[dict[str, object]] | None = None):
        self._first = first
        self._rows = rows or []

    def mappings(self) -> "_FakeResult":
        return self

    def first(self) -> object:
        return self._first

    def fetchall(self) -> list[dict[str, object]]:
        return self._rows


class _FakeSession:
    def __init__(self, results: list[_FakeResult]):
        self._results = list(results)

    def execute(self, *_args: object, **_kwargs: object) -> _FakeResult:
        return self._results.pop(0)


def test_source_profile_route_projects_primary_and_peer_rows_through_contract() -> None:
    peer = _profile(
        domain="peer.test",
        source_name="Peer",
        political_leaning="unknown",
        credibility_tier="high",
        evidence_note=(
            "seeded_from_historical_wave1_targets; "
            "structural_review_v1: structural seed only"
        ),
    )
    db = _FakeSession(
        [
            _FakeResult(first=_profile()),
            _FakeResult(rows=[]),
            _FakeResult(rows=[]),
            _FakeResult(rows=[peer]),
        ]
    )

    response = get_ground_news_source_profile(
        "example.test",
        page_size=40,
        l1_run_id="fast_l1_v2",
        db=db,
    )

    assert response["profile"]["credibility_tier"] == "high"
    assert "evidence_note" not in response["profile"]
    assert response["similar_sources"][0]["credibility_tier"] == "unknown"
    assert "evidence_note" not in response["similar_sources"][0]
    assert db._results == []


def test_ground_news_search_projects_source_results_through_method_contract() -> None:
    raw_note = "ai_certified_v9: flawless factual source"
    db = _FakeSession(
        [
            _FakeResult(rows=[]),
            _FakeResult(
                rows=[
                    _profile(
                        credibility_tier="high",
                        political_leaning="center",
                        evidence_note=raw_note,
                    )
                ]
            ),
            _FakeResult(rows=[]),
        ]
    )

    response = search_ground_news_product(
        q="example",
        page_size=20,
        l1_run_id="fast_l1_v2",
        l2_run_id="fast_l2_v1",
        db=db,
    )

    source = response["sources"][0]
    assert source["credibility_tier"] == "unknown"
    assert source["political_leaning"] == "unknown"
    assert source["method_card"]["overall_state"] == "unknown"
    assert "evidence_note" not in source
    assert raw_note not in str(response)
    assert db._results == []
