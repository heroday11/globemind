from __future__ import annotations

from datetime import date

from api.features.opinion.analytics import compute_weighted_stance_trend
from api.features.opinion.constants import METHOD_VERSION
from api.features.opinion.semantics import (
    OPINION_SEMANTIC_CONTRACT_VERSION,
    OPINION_SEMANTIC_SCHEMA_VERSION,
    apply_opinion_semantic_contract,
    build_opinion_semantic_dimensions,
    opinion_semantic_method_card,
)


def _trusted_provenance() -> dict[str, object]:
    return {
        "is_computable": True,
        "model_version": METHOD_VERSION,
        "method_version": METHOD_VERSION,
    }


def test_method_card_separates_three_axes_and_forbids_cross_axis_inference() -> None:
    card = opinion_semantic_method_card()

    assert card["schema_version"] == OPINION_SEMANTIC_SCHEMA_VERSION
    assert card["contract_version"] == OPINION_SEMANTIC_CONTRACT_VERSION
    assert card["dimensions"]["stance"] == {
        "meaning": "targeted attitude toward the named target",
        "categories": ["supportive", "neutral", "critical", "unknown"],
        "score_scales": {
            "article_stance": {
                "unit": "dimensionless",
                "minimum": -1.0,
                "maximum": 1.0,
                "neutral_band": [-0.15, 0.15],
            },
            "aggregate_stance_index": {
                "unit": "index_points",
                "minimum": -100.0,
                "maximum": 100.0,
                "neutral_band": [-15.0, 15.0],
            },
        },
        "source_model": METHOD_VERSION,
        "source_table": "public.china_opinion_article_scores",
        "availability": "available_when_trust_gate_passes",
    }
    assert card["dimensions"]["tone"]["categories"] == [
        "positive",
        "neutral",
        "negative",
        "mixed",
        "unknown",
    ]
    assert card["dimensions"]["tone"]["score_scale"] == {
        "state": "not_established",
        "unit": "unknown",
    }
    assert card["dimensions"]["tone"]["source_model"] is None
    assert card["dimensions"]["impact"]["directions"] == [
        "positive",
        "neutral",
        "negative",
        "mixed",
        "unknown",
    ]
    assert card["dimensions"]["impact"]["score_scale"] == {
        "state": "not_established",
        "unit": "unknown",
    }
    assert card["dimensions"]["impact"]["source_model"] is None
    assert card["combination"] == {
        "scope": "response_projection",
        "state": "not_combined",
        "combined_score": None,
        "rules": {
            "stance_from_tone": False,
            "stance_from_impact": False,
            "tone_from_stance": False,
            "tone_from_impact": False,
            "impact_from_stance": False,
            "impact_from_tone": False,
        },
    }
    assert card["assurance"]["upstream_axis_independence_state"] == (
        "not_established"
    )
    assert card["assurance"] == {
        "quality_state": "not_established",
        "fact_truth_state": "not_verified",
        "upstream_axis_independence_state": "not_established",
    }


def test_dimension_projection_fails_only_the_conflicting_exact_axis() -> None:
    projected = build_opinion_semantic_dimensions(
        stance_score=0.72,
        stance_scale="article_stance",
        stance_source_field="stance_score",
        stance_source_model=METHOD_VERSION,
        declared_stance_category="critical",
        tone_category="positive",
        tone_score=0.9,
        impact_direction="positive",
        impact_score=91.0,
    )

    assert projected["stance"]["state"] == "unknown"
    assert projected["stance"]["score"] is None
    assert projected["stance"]["category"] == "unknown"
    assert projected["stance"]["reason_code"] == "STANCE_CATEGORY_CONFLICT"
    assert projected["tone"] == {
        "state": "unknown",
        "category": "unknown",
        "score": None,
        "unit": "unknown",
        "source_field": None,
        "source_model": None,
        "reason_code": "TONE_MODEL_NOT_AVAILABLE",
    }
    assert projected["impact"] == {
        "state": "unknown",
        "direction": "unknown",
        "score": None,
        "unit": "unknown",
        "source_field": None,
        "source_model": None,
        "reason_code": "IMPACT_MODEL_NOT_AVAILABLE",
    }


def test_stance_projection_rejects_numeric_strings_and_booleans() -> None:
    for damaged_score in ("0.7", "  ", True, False):
        projected = build_opinion_semantic_dimensions(
            stance_score=damaged_score,
            stance_scale="article_stance",
            stance_source_field="stance_score",
            stance_source_model=METHOD_VERSION,
        )

        assert projected["stance"] == {
            "state": "unknown",
            "category": "unknown",
            "score": None,
            "scale": None,
            "unit": "unknown",
            "source_field": None,
            "source_model": None,
            "reason_code": "STANCE_SCORE_INVALID",
        }


def test_legacy_sentiment_and_impact_values_never_seed_another_axis() -> None:
    projected = apply_opinion_semantic_contract(
        {
            "trust": _trusted_provenance(),
            "news": [
                {
                    "id": 7,
                    "sentiment": 0.95,
                    "impact_index": 88.0,
                    "polarity": "positive",
                }
            ],
        }
    )

    news = projected["news"][0]
    assert news["sentiment"] is None
    assert news["impact_index"] is None
    assert news["polarity"] == "unknown"
    assert "stance_score" not in news
    assert news["semantic_dimensions"]["stance"]["state"] == "unknown"
    assert news["semantic_dimensions"]["tone"]["state"] == "unknown"
    assert news["semantic_dimensions"]["impact"]["state"] == "unknown"


def test_weighted_stance_analytics_does_not_name_its_output_impact() -> None:
    points = compute_weighted_stance_trend(
        date(2026, 8, 9),
        date(2026, 8, 9),
        [
            {
                "pub_date": date(2026, 8, 9),
                "stance_score": 0.5,
                "confidence": 1.0,
                "relevance_score": 1.0,
            }
        ],
    )

    assert points == [
        {
            "date": "2026-08-09",
            "weighted_stance_index": 50.0,
            "heat": 1.0,
        }
    ]
    assert "impact" not in points[0]


def test_contract_projects_overview_detail_target_dimension_and_trend_paths() -> None:
    projected = apply_opinion_semantic_contract(
        {
            "trust": _trusted_provenance(),
            "dates": ["2026-08-08", "2026-08-09"],
            "values": [-4.0, 20.0],
            "metric_id": "weighted_target_stance_index",
            "summary": {"current_index": 20.0},
            "target_indices": [
                {"label": "CN", "value": 20.0},
                {"label": "NEG", "value": -32.0},
            ],
            "briefs": [{"id": 1, "stance_score": 0.8}],
            "news": [{"id": 2, "stance_score": -0.6}],
            "events": [{"macro_id": "m1", "weighted_stance_index": -35.0}],
            "dimensions": {
                "sources": [{"key": "example.org", "weighted_stance_index": 18.0}]
            },
        }
    )

    assert projected["semantic_contract"]["contract_version"] == (
        OPINION_SEMANTIC_CONTRACT_VERSION
    )
    assert projected["semantic_dimensions"]["stance"]["score"] == 20.0
    assert projected["summary"]["semantic_dimensions"]["stance"]["category"] == (
        "supportive"
    )
    assert projected["target_indices"][0]["semantic_dimensions"]["stance"][
        "state"
    ] == "available"
    assert projected["target_indices"][1]["semantic_dimensions"]["stance"] == {
        "state": "unknown",
        "category": "unknown",
        "score": None,
        "scale": None,
        "unit": "unknown",
        "source_field": None,
        "source_model": None,
        "reason_code": "TARGET_VALUE_IS_NOT_A_DIRECT_STANCE_SCORE",
    }
    assert projected["briefs"][0]["semantic_dimensions"]["stance"]["score"] == 0.8
    assert projected["news"][0]["semantic_dimensions"]["stance"]["category"] == (
        "critical"
    )
    assert projected["events"][0]["semantic_dimensions"]["stance"]["score"] == -35.0
    assert projected["dimensions"]["sources"][0]["semantic_dimensions"]["stance"][
        "score"
    ] == 18.0
    for record in (
        projected,
        projected["summary"],
        projected["target_indices"][0],
        projected["briefs"][0],
        projected["news"][0],
        projected["events"][0],
        projected["dimensions"]["sources"][0],
    ):
        assert record["semantic_dimensions"]["tone"]["state"] == "unknown"
        assert record["semantic_dimensions"]["impact"]["state"] == "unknown"
