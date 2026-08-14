from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from api.features.data_governance import (
    NEWS_QUALITY_CATALOG_SCOPE_ID,
    NEWS_QUALITY_PROFILE_PROJECTION_SCHEMA_VERSION,
    build_data_catalog,
)
from scripts.news_ingest_quality import profile_news_rows


NOW = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)


def _row(suffix: str, *, body: str | None = None) -> dict[str, object]:
    return {
        "title": f"Bounded public policy report {suffix}",
        "body": body
        or (
            "Officials published a detailed policy statement with dates, scope, "
            "oversight, and attributed comments for independent inspection. "
        )
        * 4,
        "url": f"https://example.com/news/{suffix}",
        "published_at": "2026-08-09T10:00:00+00:00",
        "language": "en",
    }


def _profile() -> dict[str, object]:
    return profile_news_rows(
        [_row("one"), _row("two"), _row("bad", body="short")],
        now=NOW,
    )


def _digest(profile: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            profile,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _envelope(profile: dict[str, object] | None = None) -> dict[str, object]:
    payload = profile or _profile()
    return {
        "schema_version": NEWS_QUALITY_PROFILE_PROJECTION_SCHEMA_VERSION,
        "target_record_id": "dataset.news_articles",
        "scope_id": NEWS_QUALITY_CATALOG_SCOPE_ID,
        "profile_sha256": _digest(payload),
        "profile": payload,
    }


def _news_record(envelope: dict[str, object]):
    catalog = build_data_catalog(
        generated_at=NOW,
        news_quality_profile_projection=envelope,
    )
    return next(
        item for item in catalog.records if item.record_id == "dataset.news_articles"
    )


def test_valid_complete_v3_profile_projects_only_mechanical_unknown_summary() -> None:
    profile = _profile()
    record = _news_record(_envelope(profile))

    assert record.quality.status == "unknown"
    assert record.quality.evaluated_at == NOW
    assert record.quality.evaluation_version == "news-ingest-quality-profile-v3"
    assert record.quality.metrics == {
        "artifact_state": "mechanically_validated",
        "profile_sha256": _digest(profile),
        "evaluated_rows": 3,
        "good_count": 2,
        "bad_count": 1,
        "good_rate": 0.666667,
        "bad_rate": 0.333333,
        "title_present_rate": 1.0,
        "body_present_rate": 1.0,
        "published_at_present_rate": 1.0,
        "valid_http_url_rate": 1.0,
        "exact_url_duplicate_excess_rows": 0,
        "exact_content_duplicate_excess_rows": 0,
        "publication_cutoff_at": "2026-08-09T10:00:00+00:00",
        "near_duplicate_observation_state": "candidate_pairs_only",
        "near_duplicate_candidate_pairs_observed": profile[
            "near_duplicate_candidates"
        ]["candidate_pairs_observed"],
        "near_duplicate_duplicate_fact_state": "not_established",
        "near_duplicate_threshold_approval_state": "not_approved",
        "profile_threshold_approval_state": "not_approved",
        "profile_release_decision": "not_computable",
    }
    assert record.coverage.status == "partial"
    assert record.coverage.metrics["profile_evaluated_rows"] == 3
    assert record.coverage.metrics["profile_scope_truncated"] is False
    assert record.coverage.metrics["near_duplicate_comparison_overflow"] is False
    assert record.coverage.metrics["near_duplicate_bucket_overflow"] is False
    assert "full_dataset_coverage" in record.coverage.missing_dimensions
    assert record.status.state == "blocked"
    assert record.status.release_eligible is False
    assert "QUALITY_UNVERIFIED" in record.status.reason_codes
    assert "COVERAGE_UNVERIFIED" in record.status.reason_codes

    serialized = json.dumps(record.model_dump(mode="json"), ensure_ascii=False)
    assert "Bounded public policy report" not in serialized
    assert "https://example.com" not in serialized
    assert "near_duplicate_rate" not in serialized
    assert "duplicate_fact_state\": \"confirmed" not in serialized


@pytest.mark.parametrize(
    ("mutator", "reason_code"),
    [
        (
            lambda envelope: envelope.__setitem__("profile_sha256", "0" * 64),
            "PROFILE_HASH_MISMATCH",
        ),
        (
            lambda envelope: envelope.__setitem__("target_record_id", "dataset.other"),
            "PROFILE_SCOPE_MISMATCH",
        ),
        (
            lambda envelope: envelope.__setitem__("scope_id", "other.scope"),
            "PROFILE_SCOPE_MISMATCH",
        ),
        (
            lambda envelope: envelope["profile"].__setitem__(
                "method_version", "untrusted-method"
            ),
            "PROFILE_METHOD_INCOMPATIBLE",
        ),
        (
            lambda envelope: envelope["profile"].__setitem__(
                "generated_at", (NOW - timedelta(days=8)).isoformat()
            ),
            "PROFILE_STALE",
        ),
        (
            lambda envelope: envelope["profile"].__setitem__(
                "generated_at", (NOW + timedelta(seconds=1)).isoformat()
            ),
            "PROFILE_FUTURE_DATED",
        ),
        (
            lambda envelope: envelope["profile"]["scope"].__setitem__(
                "truncated", True
            ),
            "PROFILE_SCOPE_TRUNCATED",
        ),
        (
            lambda envelope: envelope["profile"]["near_duplicate_candidates"].__setitem__(
                "row_evaluation_truncated", True
            ),
            "NEAR_DUPLICATE_ROW_TRUNCATED",
        ),
        (
            lambda envelope: envelope["profile"]["near_duplicate_candidates"].__setitem__(
                "comparison_overflow", True
            ),
            "NEAR_DUPLICATE_COMPARISON_OVERFLOW",
        ),
        (
            lambda envelope: envelope["profile"]["near_duplicate_candidates"].__setitem__(
                "candidate_generation_overflow", True
            ),
            "NEAR_DUPLICATE_BUCKET_OVERFLOW",
        ),
        (
            lambda envelope: envelope["profile"]["near_duplicate_candidates"].__setitem__(
                "text_truncated_rows", 1
            ),
            "NEAR_DUPLICATE_TEXT_TRUNCATED",
        ),
        (
            lambda envelope: envelope["profile"]["slices"]["source_domain"].update(
                {"overflow_values": 1, "overflow_rows": 1}
            ),
            "PROFILE_SLICE_OVERFLOW",
        ),
        (
            lambda envelope: envelope["profile"]["assurance"].__setitem__(
                "threshold_approval_state", "approved"
            ),
            "PROFILE_THRESHOLD_STATE_UNSUPPORTED",
        ),
        (
            lambda envelope: envelope["profile"]["near_duplicate_candidates"].__setitem__(
                "candidate_threshold_approval_state", "approved"
            ),
            "NEAR_DUPLICATE_THRESHOLD_STATE_UNSUPPORTED",
        ),
        (
            lambda envelope: envelope["profile"]["near_duplicate_candidates"].__setitem__(
                "duplicate_fact_state", "confirmed"
            ),
            "NEAR_DUPLICATE_FACT_STATE_UNSUPPORTED",
        ),
        (
            lambda envelope: envelope["profile"]["publication_time"].update(
                {
                    "earliest_at": "2026-07-31T10:00:00+00:00",
                    "cutoff_at": "2026-07-31T10:00:00+00:00",
                }
            ),
            "PROFILE_CUTOFF_STALE",
        ),
        (
            lambda envelope: envelope["profile"]["labels"].__setitem__(
                "good_count", 999
            ),
            "PROFILE_CONTRACT_INVALID",
        ),
        (
            lambda envelope: envelope["profile"]["scope"].__setitem__(
                "row_identifiers_retained", True
            ),
            "PROFILE_CONTRACT_INVALID",
        ),
        (
            lambda envelope: envelope["profile"]["exact_duplicates"]["url"].__setitem__(
                "excess_rows", 1
            ),
            "PROFILE_CONTRACT_INVALID",
        ),
        (
            lambda envelope: envelope["profile"]["source_coverage"].__setitem__(
                "rows_with_valid_domain", 3.0
            ),
            "PROFILE_CONTRACT_INVALID",
        ),
        (
            lambda envelope: envelope["profile"]["near_duplicate_candidates"].__setitem__(
                "profile_evaluated_rows", 3.0
            ),
            "PROFILE_CONTRACT_INVALID",
        ),
        (
            lambda envelope: envelope["profile"]["slices"].__setitem__(
                "max_values_per_dimension", 64.0
            ),
            "PROFILE_CONTRACT_INVALID",
        ),
        (
            lambda envelope: envelope["profile"].__setitem__(
                "unreviewed_extension", {"quality_passed": True}
            ),
            "PROFILE_CONTRACT_INVALID",
        ),
    ],
)
def test_invalid_stale_truncated_overflow_or_scope_mismatch_stays_unknown(
    mutator,
    reason_code: str,
) -> None:
    envelope = _envelope()
    mutator(envelope)
    if reason_code != "PROFILE_HASH_MISMATCH":
        envelope["profile_sha256"] = _digest(envelope["profile"])

    record = _news_record(envelope)

    assert record.quality.status == "unknown"
    assert record.quality.metrics == {}
    assert record.coverage.status == "unknown"
    assert record.coverage.metrics == {}
    assert reason_code in record.quality.known_issues
    assert record.status.state == "blocked"
    assert record.status.release_eligible is False


def test_near_duplicate_candidates_never_turn_quality_passed_or_release_eligible() -> None:
    profile = _profile()
    profile["near_duplicate_candidates"]["candidate_pairs_observed"] = 1
    profile["near_duplicate_candidates"]["candidate_pairs_compared"] = max(
        1, profile["near_duplicate_candidates"]["candidate_pairs_compared"]
    )
    record = _news_record(_envelope(profile))

    assert record.quality.status == "unknown"
    assert record.quality.metrics["near_duplicate_candidate_pairs_observed"] == 1
    assert record.quality.metrics["near_duplicate_duplicate_fact_state"] == (
        "not_established"
    )
    assert record.status.release_eligible is False


def test_projection_envelope_is_strict_and_default_catalog_remains_unknown() -> None:
    extra = _envelope()
    extra["self_approved"] = True
    assert "PROFILE_ENVELOPE_INVALID" in _news_record(extra).quality.known_issues

    default = build_data_catalog(generated_at=NOW)
    news = next(
        item for item in default.records if item.record_id == "dataset.news_articles"
    )
    assert news.quality.status == "unknown"
    assert news.quality.metrics == {}
    assert news.status.release_eligible is False


@pytest.mark.parametrize("invalid_value", [float("nan"), float("inf"), 10**40])
def test_projection_rejects_non_interoperable_json_without_raising(
    invalid_value: object,
) -> None:
    envelope = _envelope()
    envelope["profile"]["labels"]["good_rate"] = invalid_value
    envelope["profile_sha256"] = "0" * 64

    record = _news_record(envelope)

    assert record.quality.metrics == {}
    assert record.coverage.metrics == {}
    assert "PROFILE_ENVELOPE_INVALID" in record.quality.known_issues
    assert record.status.release_eligible is False


def test_projection_rejects_cyclic_or_oversized_envelopes_without_raising() -> None:
    cyclic_profile = _profile()
    cyclic_profile["cycle"] = cyclic_profile
    cyclic_envelope = {
        "schema_version": NEWS_QUALITY_PROFILE_PROJECTION_SCHEMA_VERSION,
        "target_record_id": "dataset.news_articles",
        "scope_id": NEWS_QUALITY_CATALOG_SCOPE_ID,
        "profile_sha256": "0" * 64,
        "profile": cyclic_profile,
    }
    cyclic_record = _news_record(cyclic_envelope)
    assert cyclic_record.quality.metrics == {}
    assert "PROFILE_ENVELOPE_INVALID" in cyclic_record.quality.known_issues

    oversized = _envelope()
    oversized["profile"]["assurance"]["limitations"][0] = "x" * 4_097
    oversized["profile_sha256"] = _digest(oversized["profile"])
    oversized_record = _news_record(oversized)
    assert oversized_record.quality.metrics == {}
    assert "PROFILE_ENVELOPE_INVALID" in oversized_record.quality.known_issues
    assert oversized_record.status.release_eligible is False
