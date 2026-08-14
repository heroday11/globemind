from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Mapping, Sequence

import pytest
from pydantic import ValidationError

from api.features.opinion import (
    METHOD_VERSION,
    OPINION_TRUST_SCHEMA_VERSION,
    RESPONSE_CACHE_STORAGE,
    InMemoryResponseCache,
    OpinionFeedbackPayload,
    OpinionRefreshPayload,
    OpinionTrendQuery,
    OpinionTrendService,
    SqlAlchemyOpinionTrendRepository,
    build_trend_content,
    classify_index_label,
    coerce_date,
    compute_weighted_stance_trend,
    dimension_conditions,
    evaluate_opinion_trust,
    format_signed,
    response_cache_key,
    sanitize_opinion_payload,
    suppress_composite_trend,
)
from api.routes import opinion_v2


@dataclass
class FakeTrendRepository:
    today: date
    latest: date | None
    rows: Sequence[Mapping[str, Any]]
    requests: list[dict[str, Any]] = field(default_factory=list)

    def current_date(self) -> date:
        return self.today

    def latest_score_date(self) -> date | None:
        return self.latest

    def list_trend_articles(self, **parameters: Any) -> Sequence[Mapping[str, Any]]:
        self.requests.append(parameters)
        return self.rows


class FakeResult:
    def __init__(self, *, scalar: Any = None, rows: Sequence[Mapping[str, Any]] = ()):
        self._scalar = scalar
        self._rows = list(rows)

    def scalar(self) -> Any:
        return self._scalar

    def mappings(self) -> FakeResult:
        return self

    def fetchall(self) -> list[Mapping[str, Any]]:
        return self._rows


class SelectOnlySession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, statement: Any, parameters: dict[str, Any] | None = None) -> FakeResult:
        sql = str(statement)
        assert not re.search(
            r"\b(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|MERGE|COPY)\b",
            sql,
            re.IGNORECASE,
        )
        self.calls.append((sql, dict(parameters or {})))
        if "CURRENT_DATE" in sql:
            return FakeResult(scalar=date(2026, 7, 10))
        if "max(published_date)" in sql:
            return FakeResult(scalar=date(2026, 7, 9))
        return FakeResult(
            rows=[
                {
                    "news_id": 1,
                    "pub_date": date(2026, 7, 9),
                    "stance_score": 0.5,
                    "confidence": 0.8,
                    "relevance_score": 0.9,
                    "article_weight": 0.9,
                    "feedback_correction": None,
                }
            ]
        )


def test_request_contracts_keep_route_compatible_defaults() -> None:
    refresh = OpinionRefreshPayload()
    feedback = OpinionFeedbackPayload(
        news_id=7,
        correction="correct",
        purpose="quality_correction",
        training_consent=False,
        training_opt_out=True,
    )

    assert refresh.model_dump() == {
        "days": 60,
        "start_date": None,
        "end_date": None,
        "force": False,
    }
    assert feedback.model_dump() == {
        "news_id": 7,
        "correction": "correct",
        "purpose": "quality_correction",
        "training_consent": False,
        "training_opt_out": True,
    }
    assert opinion_v2.OpinionRefreshPayload is OpinionRefreshPayload
    assert opinion_v2.OpinionFeedbackPayload is OpinionFeedbackPayload


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_feedback_contract_rejects_non_finite_scores(value: float) -> None:
    with pytest.raises(ValidationError):
        OpinionFeedbackPayload(
            news_id=7,
            correction="correct",
            purpose="quality_correction",
            training_consent=False,
            training_opt_out=True,
            current_impact_index=value,
        )
    with pytest.raises(ValidationError):
        OpinionFeedbackPayload(
            news_id=7,
            correction="correct",
            purpose="quality_correction",
            training_consent=False,
            training_opt_out=True,
            sentiment=value,
        )


def test_route_private_feature_aliases_remain_patchable() -> None:
    assert opinion_v2._RESP_CACHE is RESPONSE_CACHE_STORAGE
    assert opinion_v2._build_trend_content is build_trend_content
    assert callable(opinion_v2._cache_get)
    assert callable(opinion_v2._cache_set)
    assert callable(opinion_v2._coerce_date)
    assert callable(opinion_v2._compute_weighted_stance_trend)


def test_date_and_numeric_analytics_are_json_stable() -> None:
    assert coerce_date(datetime(2026, 7, 10, 23, 59, tzinfo=timezone.utc)) == date(
        2026, 7, 10
    )
    assert coerce_date("2026-07-09T08:30:00Z") == date(2026, 7, 9)
    assert coerce_date("not-a-date") is None

    trend = compute_weighted_stance_trend(
        date(2026, 7, 8),
        date(2026, 7, 10),
        [
            {
                "pub_date": date(2026, 7, 8),
                "stance_score": 0.4,
                "confidence": 0.8,
                "relevance_score": 0.9,
            },
            {
                "pub_date": date(2026, 7, 9),
                "stance_score": float("nan"),
                "confidence": float("inf"),
                "relevance_score": 0.8,
            },
            {
                "pub_date": "invalid",
                "stance_score": -0.9,
                "confidence": 1.0,
                "relevance_score": 1.0,
            },
        ],
    )

    assert [point["date"] for point in trend] == [
        "2026-07-08",
        "2026-07-09",
        "2026-07-10",
    ]
    assert all(
        math.isfinite(point[key])
        for point in trend
        for key in ("weighted_stance_index", "heat")
    )
    json.dumps(trend, allow_nan=False)
    assert classify_index_label(float("nan")) == "中性震荡"
    assert format_signed(float("inf")) == "+0.0"
    assert format_signed(10**10000) == "+0.0"


def test_dimension_conditions_parameterize_dimension_values() -> None:
    hostile_region = "APAC' OR true --"
    conditions, parameters = dimension_conditions(
        min_score=0.42,
        sentiment_filter="negative",
        region=hostile_region,
        language="zh",
        media_source="example.com",
        event_family="trade",
        alias="score",
        stance_expr="effective_stance",
    )

    assert hostile_region not in conditions
    assert "effective_stance < -0.15" in conditions
    assert "score.region = :region" in conditions
    assert "score.language = :language" in conditions
    assert parameters == {
        "method_version": METHOD_VERSION,
        "min_score": 0.42,
        "region": hostile_region,
        "language": "zh",
        "media_source": "example.com",
        "event_family": "trade",
    }


def test_process_cache_key_and_ttl_are_deterministic() -> None:
    now = [10.0]
    storage: dict[str, tuple[float, dict[str, Any]]] = {}
    cache = InMemoryResponseCache(storage, clock=lambda: now[0])
    content = {"ok": True, "values": [1, 2]}

    assert response_cache_key("trend", region="cn", days=30) == response_cache_key(
        "trend", days=30, region="cn"
    )
    cache.set("key", content, ttl=5)
    now[0] = 14.999
    assert cache.get("key") is content
    now[0] = 15.0
    assert cache.get("key") is None
    assert "key" not in storage

    cache.set("non-finite", content, ttl=float("nan"))
    assert cache.get("non-finite") is None


def test_trend_service_preserves_window_filters_and_finite_dto() -> None:
    repository = FakeTrendRepository(
        today=date(2026, 7, 10),
        latest=date(2026, 7, 9),
        rows=[
            {
                "pub_date": date(2026, 7, 8),
                "stance_score": 0.5,
                "confidence": 1.0,
                "relevance_score": 1.0,
                "method_version": METHOD_VERSION,
                "media_domain": "source.example",
            },
            {
                "pub_date": date(2026, 7, 9),
                "stance_score": float("nan"),
                "confidence": float("inf"),
                "relevance_score": float("nan"),
                "method_version": METHOD_VERSION,
            },
        ],
    )
    query = OpinionTrendQuery(
        days=3,
        china_min_score=0.4,
        sentiment_filter="positive",
        region="Asia",
        language="zh",
        media_source="source.example",
        event_family="trade",
    )

    content = OpinionTrendService(repository).build(query)

    assert content["dates"] == ["2026-07-06", "2026-07-07", "2026-07-08"]
    assert content["meta"]["total_articles"] == 1
    assert content["meta"]["last_article_date"] == "2026-07-08"
    assert content["meta"]["invalid_articles"] == 1
    assert content["meta"]["trust"]["is_computable"] is False
    assert "LOW_ARTICLE_COVERAGE" in content["meta"]["trust"]["reason_codes"]
    assert content["meta"]["filters"] == {
        "days": 3,
        "china_min_score": 0.4,
        "sentiment_filter": "positive",
        "region": "Asia",
        "language": "zh",
        "media_source": "source.example",
        "event_family": "trade",
    }
    assert repository.requests == [
        {
            "fetch_start": date(2026, 4, 11),
            "end_date": date(2026, 7, 10),
            "china_min_score": 0.4,
            "sentiment_filter": "positive",
            "region": "Asia",
            "language": "zh",
            "media_source": "source.example",
            "event_family": "trade",
        }
    ]
    json.dumps(content, allow_nan=False)


def test_opinion_trust_gate_rejects_stale_and_low_coverage_composites() -> None:
    stale = evaluate_opinion_trust(
        current_date=date(2026, 8, 9),
        cutoff_date=date(2026, 7, 21),
        article_count=200,
        source_count=20,
    )
    assert stale["is_computable"] is False
    assert stale["display_mode"] == "historical_context"
    assert stale["freshness"]["age_days"] == 19
    assert stale["reason_codes"] == ["STALE_DATA"]

    low_coverage = evaluate_opinion_trust(
        current_date=date(2026, 8, 9),
        cutoff_date=date(2026, 8, 9),
        article_count=4,
        source_count=1,
    )
    assert low_coverage["is_computable"] is False
    assert low_coverage["coverage"]["state"] == "insufficient"
    assert low_coverage["reason_codes"] == [
        "LOW_ARTICLE_COVERAGE",
        "LOW_SOURCE_COVERAGE",
    ]

    content = {
        "dates": ["2026-08-09"],
        "values": [42.7],
        "heat": [1.0],
        "meta": {"trust": stale},
    }
    suppressed = suppress_composite_trend(content, current_date=date(2026, 8, 9))
    assert suppressed["dates"] == []
    assert suppressed["values"] == []
    assert suppressed["heat"] == []
    assert suppressed["meta"]["composite_suppressed"] is True
    assert suppressed["trust"]["is_computable"] is False
    assert suppressed["trust"]["schema_version"] == OPINION_TRUST_SCHEMA_VERSION


def test_opinion_trust_gate_allows_fresh_diverse_sample() -> None:
    trust = evaluate_opinion_trust(
        current_date=date(2026, 8, 9),
        cutoff_date=date(2026, 8, 8),
        article_count=10,
        source_count=3,
    )
    assert trust["status"] == "ready"
    assert trust["is_computable"] is True
    assert trust["reason_codes"] == []


def test_filtered_cutoff_and_terminal_coverage_exclude_invalid_and_rejected_rows() -> None:
    valid_rows = [
        {
            "pub_date": date(2026, 7, 21),
            "stance_score": 0.3,
            "confidence": 0.8,
            "relevance_score": 0.9,
            "method_version": METHOD_VERSION,
            "media_domain": f"source-{index % 3}.example",
        }
        for index in range(10)
    ]
    repository = FakeTrendRepository(
        today=date(2026, 8, 9),
        latest=date(2026, 8, 9),
        rows=[
            *valid_rows,
            {
                "pub_date": date(2026, 8, 9),
                "stance_score": float("nan"),
                "confidence": 0.9,
                "relevance_score": 0.9,
                "method_version": METHOD_VERSION,
            },
            {
                "pub_date": date(2026, 8, 8),
                "stance_score": 0.4,
                "confidence": 0.9,
                "relevance_score": 0.9,
                "method_version": METHOD_VERSION,
                "review_status": "rejected",
            },
            {
                "pub_date": date(2026, 8, 9),
                "stance_score": 0.4,
                "confidence": 0.9,
                "relevance_score": 0.9,
                "method_version": "obsolete-method",
            },
        ],
    )

    content = OpinionTrendService(repository).build(
        OpinionTrendQuery(days=30, china_min_score=0.4, sentiment_filter="all")
    )

    trust = content["meta"]["trust"]
    assert content["meta"]["last_article_date"] == "2026-07-21"
    assert trust["cutoff_date"] == "2026-07-21"
    assert trust["coverage"]["article_count"] == 10
    assert trust["coverage"]["source_count"] == 3
    assert trust["coverage"]["invalid_article_count"] == 2
    assert trust["coverage"]["rejected_article_count"] == 1
    assert trust["reason_codes"] == ["STALE_DATA"]
    assert trust["snapshot"]["filters"]["sentiment_filter"] == "all"


def test_trend_has_null_cutoff_and_window_when_no_effective_terminal_sample() -> None:
    repository = FakeTrendRepository(
        today=date(2026, 8, 9),
        latest=date(2026, 8, 9),
        rows=[
            {
                "pub_date": date(2026, 8, 9),
                "stance_score": float("inf"),
                "confidence": 0.9,
                "relevance_score": 0.9,
                "method_version": METHOD_VERSION,
            },
            {
                "pub_date": date(2026, 8, 8),
                "stance_score": -0.2,
                "confidence": 0.8,
                "relevance_score": 0.8,
                "method_version": METHOD_VERSION,
                "validation_status": "rejected",
            },
        ],
    )

    content = OpinionTrendService(repository).build(
        OpinionTrendQuery(days=7, china_min_score=0.4, sentiment_filter="all")
    )

    assert content["dates"] == []
    assert content["meta"]["last_article_date"] is None
    assert content["meta"]["start_date"] is None
    assert content["meta"]["end_date"] is None
    assert content["meta"]["trust"]["cutoff_date"] is None
    assert "MISSING_CUTOFF" in content["meta"]["trust"]["reason_codes"]


def test_unified_sanitizer_fails_closed_for_missing_or_conflicting_trust() -> None:
    missing = sanitize_opinion_payload(
        {
            "dates": ["2026-08-09"],
            "values": [31.2],
            "heat": [4.0],
            "summary": {
                "current_index": 31.2,
                "positive_pct": 60.0,
                "negative_pct": 20.0,
                "neutral_pct": 20.0,
            },
            "families": [{"avg_stance": 0.4}],
            "top_event": {"avg_stance": -0.5, "impact_index": -50.0},
            "dimensions": {"sources": [{"impact_index": 44.0}]},
            "news": [
                {
                    "impact_index": 12.0,
                    "confidence": 0.86,
                    "china_index": 0.91,
                    "sentiment": 0.4,
                }
            ],
        },
        current_date=date(2026, 8, 9),
    )
    assert "MISSING_TRUST_METADATA" in missing["trust"]["reason_codes"]
    assert missing["dates"] == []
    assert missing["summary"]["current_index"] is None
    assert missing["summary"]["positive_pct"] is None
    assert missing["families"][0]["avg_stance"] is None
    assert missing["top_event"]["impact_index"] is None
    assert missing["dimensions"]["sources"][0]["impact_index"] is None
    assert missing["news"][0]["confidence"] is None
    assert missing["news"][0]["china_index"] is None

    trust = evaluate_opinion_trust(
        current_date=date(2026, 8, 9),
        cutoff_date=date(2026, 8, 9),
        article_count=10,
        source_count=3,
    )
    conflicting = dict(trust)
    conflicting["status"] = "unavailable"
    sanitized = sanitize_opinion_payload(
        {"values": [22.0], "meta": {"trust": conflicting}},
        current_date=date(2026, 8, 9),
    )
    assert sanitized["values"] == []
    assert "CONFLICTING_STATUS_METADATA" in sanitized["trust"]["reason_codes"]

    downgraded = json.loads(json.dumps(trust))
    downgraded["coverage"]["minimum_articles"] = 1
    downgraded["coverage"]["minimum_sources"] = 1
    downgraded["source"]["id"] = "unverified.shadow_scores"
    downgraded["snapshot"]["id"] = "forged-snapshot"
    downgraded["snapshot_id"] = "forged-snapshot"
    sanitized = sanitize_opinion_payload(
        {"values": [22.0], "meta": {"trust": downgraded}},
        current_date=date(2026, 8, 9),
    )
    assert sanitized["values"] == []
    assert "CONFLICTING_COVERAGE_METADATA" in sanitized["trust"]["reason_codes"]
    assert "CONFLICTING_SOURCE_METADATA" in sanitized["trust"]["reason_codes"]
    assert "CONFLICTING_SNAPSHOT_METADATA" in sanitized["trust"]["reason_codes"]


def test_sanitizer_reages_cached_trust_before_returning_composites() -> None:
    trust = evaluate_opinion_trust(
        current_date=date(2026, 8, 9),
        cutoff_date=date(2026, 8, 8),
        article_count=10,
        source_count=3,
    )
    cached = {
        "dates": ["2026-08-08"],
        "values": [18.5],
        "heat": [2.0],
        "meta": {"last_article_date": "2026-08-08", "trust": trust},
    }

    sanitized = sanitize_opinion_payload(cached, current_date=date(2026, 8, 12))

    assert sanitized["values"] == []
    assert sanitized["trust"]["freshness"]["age_days"] == 4
    assert "STALE_DATA" in sanitized["trust"]["reason_codes"]


def test_sqlalchemy_trend_repository_is_select_only_and_parameterized() -> None:
    session = SelectOnlySession()
    repository = SqlAlchemyOpinionTrendRepository(session)  # type: ignore[arg-type]

    assert repository.current_date() == date(2026, 7, 10)
    assert repository.latest_score_date() == date(2026, 7, 9)
    rows = repository.list_trend_articles(
        fetch_start=date(2026, 6, 1),
        end_date=date(2026, 7, 9),
        china_min_score=0.4,
        sentiment_filter="negative",
        region="Asia",
        language="zh",
        media_source="source.example",
        event_family="trade",
    )

    assert len(rows) == 1
    trend_sql, parameters = session.calls[-1]
    assert trend_sql.lstrip().startswith("WITH")
    assert "s.region = :region" in trend_sql
    assert "source.example" not in trend_sql
    assert parameters == {
        "method_version": METHOD_VERSION,
        "min_score": 0.4,
        "region": "Asia",
        "language": "zh",
        "media_source": "source.example",
        "event_family": "trade",
        "fetch_start": date(2026, 6, 1),
        "end_date": date(2026, 7, 9),
    }
