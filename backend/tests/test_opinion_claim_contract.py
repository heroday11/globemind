from __future__ import annotations

import copy
import json
from datetime import date

from api.features.opinion import (
    OPINION_CLAIM_MAX_CLAIMS,
    assure_opinion_overview_claims,
    evaluate_opinion_trust,
)


def _overview_payload() -> dict:
    trust = evaluate_opinion_trust(
        current_date=date(2026, 8, 9),
        cutoff_date=date(2026, 8, 9),
        article_count=10,
        source_count=3,
        filters={
            "days": 30,
            "china_min_score": 0.4,
            "sentiment_filter": "all",
            "region": None,
            "language": None,
            "media_source": None,
            "event_family": None,
        },
    )
    return {
        "summary": {
            "current_index": 12.5,
            "change_24h": -1.5,
            "growth_pct": 4.0,
            "article_count": 10,
            "source_count": 3,
            "family_count": 1,
            "positive_pct": 40.0,
            "negative_pct": 30.0,
            "neutral_pct": 30.0,
        },
        "target_indices": [
            {"label": "CN", "value": 12.5},
            {"label": "NEG", "value": -30.0},
            {"label": "POS", "value": 40.0},
        ],
        "families": [
            {"event_family": "economic_trade", "article_count": 4, "avg_stance": 0.2}
        ],
        "briefs": [
            {
                "id": 17,
                "title": "display text must not bind identity",
                "stance_score": 0.3,
                "confidence": 0.8,
            }
        ],
        "trust": trust,
        "meta": {"trust": copy.deepcopy(trust)},
    }


def _claim(payload: dict, metric: str) -> dict:
    return next(
        item for item in payload["claim_contract"]["claims"] if item["metric"] == metric
    )


def test_overview_claim_ids_bind_semantics_and_snapshot_not_values_or_display_text() -> None:
    baseline = assure_opinion_overview_claims(_overview_payload())
    changed_display = _overview_payload()
    changed_display["summary"]["current_index"] = -44.0
    changed_display["briefs"][0]["title"] = "attacker supplied prose and feedback text"
    changed_display["briefs"][0]["feedback_correction"] = "too_positive"
    changed = assure_opinion_overview_claims(changed_display)

    baseline_claim = _claim(baseline, "weighted_stance_index")
    changed_claim = _claim(changed, "weighted_stance_index")
    assert baseline_claim["claim_id"] == changed_claim["claim_id"]
    assert baseline_claim["identity"] == changed_claim["identity"]
    serialized_identity = json.dumps(baseline_claim["identity"], sort_keys=True)
    assert "display text" not in serialized_identity
    assert "feedback" not in serialized_identity
    assert baseline_claim["identity"] == {
        "metric": "weighted_stance_index",
        "slice": {
            "population": "china_relevant_direct_articles",
            "window_days": 30,
        },
        "model_version": baseline["trust"]["model_version"],
        "method_version": baseline["trust"]["method_version"],
        "data_cutoff": "2026-08-09",
        "snapshot_id": baseline["trust"]["snapshot_id"],
        "source_id": "public.china_opinion_article_scores",
    }

    changed_snapshot = _overview_payload()
    changed_snapshot["trust"]["snapshot_id"] = "opinion-" + "f" * 64
    changed_snapshot["trust"]["snapshot"]["id"] = "opinion-" + "f" * 64
    changed_snapshot["meta"]["trust"] = copy.deepcopy(changed_snapshot["trust"])
    rebound = assure_opinion_overview_claims(changed_snapshot)
    assert _claim(rebound, "weighted_stance_index")["claim_id"] != baseline_claim["claim_id"]


def test_overview_claims_never_fabricate_citations_or_promote_model_outputs_to_facts() -> None:
    payload = assure_opinion_overview_claims(_overview_payload())

    assert payload["claim_contract"]["status"] == "complete"
    assert payload["claim_contract"]["claims"]
    for claim in payload["claim_contract"]["claims"]:
        assert claim["citation_locator"] is None
        assert claim["citation_status"] == "unavailable"
        assert claim["citation_reason_code"] == "SAFE_CITATION_LOCATOR_UNAVAILABLE"
        assert claim["source_truth_state"] == "not_verified"
        assert claim["claim_state"] in {"derived_not_verified", "explicit_unknown"}
        assert claim["reason_code"] in {
            "DERIVED_VALUE_NOT_SOURCE_VERIFIED",
            "DERIVED_VALUE_UNAVAILABLE",
        }
    assert _claim(payload, "article_count")["claim_state"] == "derived_not_verified"
    assert _claim(payload, "weighted_stance_index")["claim_state"] == "derived_not_verified"


def test_overview_claim_contract_is_bounded_and_invalid_identity_fails_closed() -> None:
    oversized = _overview_payload()
    oversized["families"] = [
        {"event_family": f"family_{index}", "article_count": index, "avg_stance": 0.1}
        for index in range(500)
    ]
    oversized["briefs"] = [
        {"id": index + 1, "stance_score": 0.2, "confidence": 0.8}
        for index in range(500)
    ]
    bounded = assure_opinion_overview_claims(oversized)
    assert len(bounded["families"]) <= 8
    assert len(bounded["briefs"]) <= 6
    assert len(bounded["claim_contract"]["claims"]) <= OPINION_CLAIM_MAX_CLAIMS

    invalid = _overview_payload()
    invalid["top_event"] = {
        "chain_id": "chain-1",
        "avg_stance": 0.4,
        "article_count": 8,
        "china_articles": 6,
    }
    invalid["trust"]["snapshot_id"] = "display text"
    invalid["trust"]["snapshot"]["id"] = "display text"
    invalid["meta"]["trust"] = copy.deepcopy(invalid["trust"])
    blocked = assure_opinion_overview_claims(invalid)
    assert blocked["claim_contract"] == {
        "schema_version": "opinion-derived-claim-contract-v1",
        "status": "unavailable",
        "reason_codes": ["CLAIM_IDENTITY_METADATA_UNAVAILABLE"],
        "claims": [],
        "max_claims": OPINION_CLAIM_MAX_CLAIMS,
    }
    assert blocked["summary"]["current_index"] is None
    assert blocked["summary"]["growth_pct"] is None
    assert blocked["summary"]["article_count"] is None
    assert blocked["families"][0]["article_count"] is None
    assert blocked["top_event"]["article_count"] is None
    assert blocked["top_event"]["china_articles"] is None
    assert all(item["value"] is None for item in blocked["target_indices"])


def test_invalid_top_event_identity_clears_every_unclaimed_derivation() -> None:
    payload = _overview_payload()
    payload["top_event"] = {
        "chain_id": "unsafe chain / title",
        "title": "display-only title may remain",
        "avg_stance": 0.8,
        "article_count": 17,
        "china_articles": 9,
    }

    assured = assure_opinion_overview_claims(payload)

    assert assured["top_event"]["title"] == "display-only title may remain"
    assert assured["top_event"]["avg_stance"] is None
    assert assured["top_event"]["article_count"] is None
    assert assured["top_event"]["china_articles"] is None
    assert not any(
        item["metric"].startswith("top_event_")
        for item in assured["claim_contract"]["claims"]
    )


def test_target_indices_are_canonical_unique_and_value_bound_or_fail_closed() -> None:
    reordered = _overview_payload()
    reordered["target_indices"] = [
        {"label": "POS", "value": 40.0, "state": "positive", "trend_values": []},
        {"label": "CN", "value": 12.5, "state": "positive", "trend_values": [9.0, 12.5]},
        {"label": "NEG", "value": -30.0, "state": "negative", "trend_values": []},
    ]
    normalized = assure_opinion_overview_claims(reordered)
    assert [item["label"] for item in normalized["target_indices"]] == ["CN", "NEG", "POS"]
    assert [item["value"] for item in normalized["target_indices"]] == [12.5, -30.0, 40.0]
    assert _claim(normalized, "target_weighted_stance_index")["output_paths"] == [
        "target_indices.CN.value"
    ]
    assert _claim(normalized, "negative_stance_pressure_index")["output_paths"] == [
        "target_indices.NEG.value"
    ]
    assert _claim(normalized, "positive_stance_support_index")["output_paths"] == [
        "target_indices.POS.value"
    ]

    for malformed in (
        [
            {"label": "CN", "value": 12.5},
            {"label": "CN", "value": -30.0},
            {"label": "POS", "value": 40.0},
        ],
        [
            {"label": "CN", "value": 999.0},
            {"label": "NEG", "value": -30.0},
            {"label": "POS", "value": 40.0},
        ],
        [
            {"label": "CN", "value": 12.5},
            {"label": "NEG", "value": -30.0},
            {"label": "OTHER", "value": 40.0},
        ],
    ):
        payload = _overview_payload()
        payload["target_indices"] = malformed
        blocked = assure_opinion_overview_claims(payload)
        assert [item["label"] for item in blocked["target_indices"]] == ["CN", "NEG", "POS"]
        assert all(item["value"] is None for item in blocked["target_indices"])
        assert _claim(blocked, "target_weighted_stance_index")["claim_state"] == "explicit_unknown"
        assert _claim(blocked, "negative_stance_pressure_index")["claim_state"] == "explicit_unknown"
        assert _claim(blocked, "positive_stance_support_index")["claim_state"] == "explicit_unknown"
