from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from api.features.financial import FinancialAlertService, JsonListStore
from api.features.financial.trust import (
    HARD_MINIMUM_SOURCE_COVERAGE,
    apply_dashboard_trust_gate,
    calculate_extracted_wsi,
    composite_method_card,
    dashboard_is_computable,
    short_sample_trend_method_card,
)
from api.routes import financial
from api.services import financial_terminal


def _shared_cache_document(payload: dict[str, object] | None = None) -> str:
    return json.dumps(
        {
            "expires_at": 9_999_999_999,
            "payload": payload or {"mode": "historical"},
        },
        separators=(",", ":"),
    )


@pytest.mark.parametrize("link_kind", ("symlink", "hardlink"))
def test_shared_dashboard_cache_read_rejects_linked_files(
    tmp_path,
    monkeypatch,
    link_kind: str,
) -> None:
    target = tmp_path / "outside-cache.json"
    target.write_text(_shared_cache_document(), encoding="utf-8")
    cache = tmp_path / "financial-dashboard.json"
    if link_kind == "symlink":
        cache.symlink_to(target)
    else:
        cache.hardlink_to(target)
    monkeypatch.setattr(financial_terminal, "SHARED_DASHBOARD_CACHE", cache)

    assert financial_terminal._read_shared_dashboard_cache() is None


@pytest.mark.parametrize(
    "encoded",
    (
        '{"expires_at":1,"expires_at":9999999999,"payload":{"mode":"live"}}',
        '{"expires_at":9999999999,"payload":{"value":NaN}}',
        '{"expires_at":9999999999,"payload":{"value":Infinity}}',
        '{"expires_at":1e400,"payload":{"mode":"live"}}',
    ),
)
def test_shared_dashboard_cache_read_rejects_ambiguous_or_non_finite_json(
    tmp_path,
    monkeypatch,
    encoded: str,
) -> None:
    cache = tmp_path / "financial-dashboard.json"
    cache.write_text(encoded, encoding="utf-8")
    monkeypatch.setattr(financial_terminal, "SHARED_DASHBOARD_CACHE", cache)

    assert financial_terminal._read_shared_dashboard_cache() is None


def test_shared_dashboard_cache_write_does_not_follow_fixed_temp_symlink(
    tmp_path,
    monkeypatch,
) -> None:
    cache = tmp_path / "financial-dashboard.json"
    victim = tmp_path / "unrelated.txt"
    victim.write_text("preserve-me", encoding="utf-8")
    cache.with_suffix(".tmp").symlink_to(victim)
    monkeypatch.setattr(financial_terminal, "SHARED_DASHBOARD_CACHE", cache)

    financial_terminal._write_shared_dashboard_cache({"mode": "historical"})

    assert victim.read_text(encoding="utf-8") == "preserve-me"
    assert cache.is_file()
    assert cache.is_symlink() is False
    assert financial_terminal._read_shared_dashboard_cache() is not None


def test_shared_dashboard_cache_secure_round_trip(tmp_path, monkeypatch) -> None:
    cache = tmp_path / "financial-dashboard.json"
    monkeypatch.setattr(financial_terminal, "SHARED_DASHBOARD_CACHE", cache)

    financial_terminal._write_shared_dashboard_cache({"mode": "historical"})
    cached = financial_terminal._read_shared_dashboard_cache()

    assert cached is not None
    assert cached[0] > 0
    assert cached[1] == {"mode": "historical"}


def test_shared_dashboard_cache_write_does_not_create_through_parent_symlink(
    tmp_path,
    monkeypatch,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(outside, target_is_directory=True)
    cache = linked_parent / "new-cache-directory" / "financial-dashboard.json"
    monkeypatch.setattr(financial_terminal, "SHARED_DASHBOARD_CACHE", cache)

    financial_terminal._write_shared_dashboard_cache({"mode": "historical"})

    assert (outside / "new-cache-directory").exists() is False


def test_shared_dashboard_cache_rejects_unbounded_future_expiry(
    tmp_path,
    monkeypatch,
) -> None:
    cache = tmp_path / "financial-dashboard.json"
    cache.write_text(_shared_cache_document(), encoding="utf-8")
    monkeypatch.setattr(financial_terminal, "SHARED_DASHBOARD_CACHE", cache)

    assert financial_terminal._read_shared_dashboard_cache() is None


def _source(
    source_id: str,
    now: datetime,
    *,
    status: str = "live",
    records: int = 10,
    cadence: str = "daily",
) -> dict[str, object]:
    return {
        "id": source_id,
        "name": source_id,
        "status": status,
        "records": records,
        "cadence": cadence,
        "last_updated": now.isoformat().replace("+00:00", "Z"),
    }


def _dashboard(now: datetime) -> dict[str, object]:
    sources = [
        _source("ground-news-local", now, cadence="15m-daily"),
        _source("gdelt", now, cadence="15m"),
        _source("worldbank-gdp", now, cadence="annual"),
        _source("worldbank-inflation", now, cadence="annual"),
        _source("usgs-earthquake", now, cadence="2h"),
        _source("worldbank-electricity", now, cadence="annual"),
        _source("opensky", now, cadence="30m"),
        _source("openalex-tech", now, cadence="daily"),
        _source("nvd", now, cadence="daily"),
        _source("nasa-eonet", now, cadence="daily"),
    ]
    return {
        "mode": "live",
        "last_updated": now.isoformat().replace("+00:00", "Z"),
        "bars": [{"time": 1, "open": 50, "high": 52, "low": 49, "close": 51}],
        "ma20": [{"time": 1, "value": 50}],
        "ma50": [],
        "ma200": [],
        "indices": [
            {
                "id": "wsi",
                "metric_id": "IDX-WSI",
                "name": "世界状态综合",
                "value": 50.09,
                "change_pct": 6.1,
                "spark": [48, 50.09],
            }
        ],
        "series": [
            {
                "id": "IDX-WSI",
                "kind": "index",
                "status": "live",
                "latest": 50.09,
                "change_pct": 6.1,
                "points": [{"time": 1, "value": 48}, {"time": 2, "value": 50.09}],
            },
            {
                "id": "USGS-EQ",
                "kind": "metric",
                "status": "live",
                "latest": 12,
                "change_pct": 1.2,
                "points": [],
            },
        ],
        "sources": sources,
        "coverage": {"sources_total": len(sources)},
        "alert_rules": [
            {
                "id": "world-state",
                "metric": "世界状态综合指数",
                "current": 50.09,
                "threshold": 72,
                "breached": False,
            }
        ],
    }


def test_healthy_sources_add_metadata_but_do_not_release_unapproved_composite() -> None:
    now = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)
    result = apply_dashboard_trust_gate(_dashboard(now), cache_state="miss", now=now)

    assert result["trust"]["computability"] == "not_computable"
    assert result["trust"]["trust_status"] == "unavailable"
    assert result["indices"][0]["value"] is None
    assert result["indices"][0]["change_pct"] is None
    assert result["alert_rules"] == []
    assert result["indices"][0]["method_version"] == "world-state-composite-v0.9.0"
    assert result["schema_version"] == "financial-trust-v1"
    assert result["snapshot_id"] == result["trust"]["snapshot_id"]
    assert dashboard_is_computable(result) is False
    assert result["coverage"]["coverage_ratio"] == 1.0


def test_short_sample_trend_disclosure_binds_baseline_count_and_uncertainty_without_inference() -> None:
    now = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)
    payload = _dashboard(now)
    payload["indices"][0]["trend_disclosure"] = {
        "approval_status": "approved",
        "confidence_level": 0.95,
    }
    payload["series"][0]["trend_disclosure"] = {
        "sample_size": {"count": 999_999},
    }
    payload["short_sample_trend_method_card"] = {
        "approval_status": "approved",
        "confidence_level": 0.95,
    }
    payload["trust"] = {
        "short_sample_trend_method_card": payload[
            "short_sample_trend_method_card"
        ]
    }

    result = apply_dashboard_trust_gate(payload, cache_state="miss", now=now)
    index_disclosure = result["indices"][0]["trend_disclosure"]
    series_disclosure = result["series"][0]["trend_disclosure"]

    assert result["short_sample_trend_method_card"] == short_sample_trend_method_card()
    assert result["trust"]["short_sample_trend_method_card"] == result[
        "short_sample_trend_method_card"
    ]
    assert index_disclosure == series_disclosure
    assert set(index_disclosure) == {
        "schema_version",
        "semantic_metric_id",
        "snapshot_id",
        "data_cutoff",
        "statistical_method_version",
        "approval_status",
        "trend_status",
        "baseline_period",
        "sample_size",
        "uncertainty",
        "outlier_policy_status",
        "reason_codes",
    }
    assert index_disclosure["schema_version"] == "financial-short-sample-trend-v1"
    assert index_disclosure["semantic_metric_id"] == "IDX-WSI"
    assert index_disclosure["snapshot_id"] == result["snapshot_id"]
    assert index_disclosure["data_cutoff"] == result["data_as_of"]
    assert index_disclosure["statistical_method_version"] is None
    assert index_disclosure["approval_status"] == "not_approved"
    assert index_disclosure["trend_status"] == "not_computable"
    assert index_disclosure["baseline_period"] == {
        "status": "not_established",
        "start": None,
        "end": None,
    }
    assert index_disclosure["sample_size"] == {
        "status": "provided_series_point_count",
        "count": 2,
        "unit": "provided_series_points",
        "independence_status": "not_validated",
    }
    assert index_disclosure["uncertainty"] == {
        "status": "not_computable",
        "confidence_level": None,
        "interval_lower": None,
        "interval_upper": None,
        "reason_code": "UNCERTAINTY_METHOD_NOT_ESTABLISHED",
    }
    assert index_disclosure["outlier_policy_status"] == "not_established"
    assert index_disclosure["reason_codes"] == [
        "BASELINE_PERIOD_NOT_ESTABLISHED",
        "TREND_METHOD_NOT_APPROVED",
        "UNCERTAINTY_METHOD_NOT_ESTABLISHED",
    ]
    assert result["indices"][0]["change_pct"] is None
    assert result["series"][0]["change_pct"] is None


def test_short_sample_trend_disclosure_rejects_unbounded_or_invalid_points() -> None:
    now = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)

    unbounded = _dashboard(now)
    unbounded["series"][0]["points"] = [
        {"time": index, "value": 50.0} for index in range(4097)
    ]
    unbounded_result = apply_dashboard_trust_gate(
        unbounded,
        cache_state="miss",
        now=now,
    )
    disclosure = unbounded_result["series"][0]["trend_disclosure"]
    assert disclosure["sample_size"] == {
        "status": "not_available",
        "count": None,
        "unit": "provided_series_points",
        "independence_status": "not_validated",
    }
    assert disclosure["reason_codes"][-1] == "BOUNDED_SERIES_POINTS_NOT_AVAILABLE"
    assert (
        unbounded_result["indices"][0]["trend_disclosure"]
        == unbounded_result["series"][0]["trend_disclosure"]
    )

    invalid = _dashboard(now)
    invalid["series"][0]["points"] = [
        {"time": 1, "value": 50.0, "hidden_weight": 10},
    ]
    invalid_result = apply_dashboard_trust_gate(
        invalid,
        cache_state="miss",
        now=now,
    )
    assert (
        invalid_result["series"][0]["trend_disclosure"]["sample_size"]["status"]
        == "not_available"
    )


def test_short_sample_trend_method_card_is_defensive_and_explicitly_unapproved() -> None:
    card = short_sample_trend_method_card()
    assert card == {
        "schema_version": "financial-short-sample-trend-method-card-v1",
        "statistical_method_version": None,
        "implementation_status": "disclosure_gate_only",
        "approval_status": "not_approved",
        "baseline_period_status": "not_established",
        "sample_size_semantics": "provided_series_point_count_only",
        "independence_status": "not_validated",
        "minimum_sample_size": None,
        "uncertainty_method_status": "not_established",
        "confidence_level": None,
        "outlier_policy_status": "not_established",
        "release_rule": "suppress_change_pct_until_approved_method",
        "maximum_provided_points": 4096,
    }
    card["approval_status"] = "approved"
    assert short_sample_trend_method_card()["approval_status"] == "not_approved"


def test_derived_metric_claims_bind_semantic_identity_without_fabricating_citations() -> None:
    now = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)
    payload = _dashboard(now)
    payload["indices"].append(
        {
            "id": "security",
            "metric_id": "IDX-SECURITY",
            "name": "冲突安全压力",
            "value": 61.0,
            "change_pct": 2.0,
            "spark": [60.0, 61.0],
        }
    )
    payload["series"].append(
        {
            "id": "IDX-SECURITY",
            "kind": "index",
            "status": "live",
            "latest": 61.0,
            "change_pct": 2.0,
            "points": [{"time": 1, "value": 60.0}],
        }
    )
    result = apply_dashboard_trust_gate(payload, cache_state="miss", now=now)
    index = result["indices"][0]
    metric = result["series"][0]

    index_claims = {
        item["metric_id"]: item["claim_id"] for item in result["indices"]
    }
    series_claims = {
        item["id"]: item["claim_id"]
        for item in result["series"]
        if item["id"].startswith("IDX-")
    }
    assert index_claims == series_claims
    assert set(index_claims) == {"IDX-WSI", "IDX-SECURITY"}
    assert index["claim_id"] == metric["claim_id"]
    assert index["claim_id"].startswith("fdc_")
    assert len(index["claim_id"]) == 68
    assert set(index["claim_identity"]) == {
        "schema_version",
        "semantic_metric_id",
        "metric_class",
        "method_version",
        "model_version",
        "snapshot_id",
        "data_cutoff",
        "availability",
    }
    assert index["claim_identity"] == {
        "schema_version": "financial-derived-claim-identity-v1",
        "semantic_metric_id": "IDX-WSI",
        "metric_class": "composite_index",
        "method_version": result["method_version"],
        "model_version": result["model_version"],
        "snapshot_id": result["snapshot_id"],
        "data_cutoff": result["data_as_of"],
        "availability": "not_computable",
    }
    assert index["claim_unavailable_reason"] is None
    assert index["citation_locator"] is None
    assert index["citation_locator_state"] == "unavailable"
    assert (
        index["citation_unavailable_reason"]
        == "VERIFIED_NUMERIC_EVIDENCE_LOCATOR_NOT_ESTABLISHED"
    )

    relabelled = _dashboard(now)
    relabelled["indices"][0]["name"] = "unverified display text"
    relabelled["series"][0]["label"] = "another display string"
    relabelled_result = apply_dashboard_trust_gate(
        relabelled,
        cache_state="miss",
        now=now,
    )
    assert relabelled_result["indices"][0]["claim_id"] == index["claim_id"]

    different_metric = _dashboard(now)
    different_metric["indices"][0]["metric_id"] = "IDX-SECURITY"
    different_result = apply_dashboard_trust_gate(
        different_metric,
        cache_state="miss",
        now=now,
    )
    assert different_result["indices"][0]["claim_id"] != index["claim_id"]


def test_derived_metric_claim_identity_fails_closed_without_a_semantic_metric_id() -> None:
    now = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)
    payload = _dashboard(now)
    payload["indices"][0]["metric_id"] = "display text is not a metric id"
    result = apply_dashboard_trust_gate(payload, cache_state="miss", now=now)
    index = result["indices"][0]

    assert index["claim_id"] is None
    assert index["claim_identity"] is None
    assert index["claim_unavailable_reason"] == "SEMANTIC_METRIC_ID_INVALID"
    assert index["citation_locator"] is None
    assert index["value"] is None
    assert index["spark"] == []


def test_composite_cutoff_uses_nth_newest_required_source_observation() -> None:
    now = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)
    payload = _dashboard(now)
    sources = {source["id"]: source for source in payload["sources"]}
    sources["worldbank-gdp"]["last_updated"] = "2025-12-31T00:00:00Z"
    sources["worldbank-inflation"]["last_updated"] = "2024-12-31T00:00:00Z"

    result = apply_dashboard_trust_gate(payload, cache_state="miss", now=now)
    macro = next(
        group
        for group in result["trust"]["critical_inputs"]
        if group["id"] == "macro-baseline"
    )

    assert result["trust"]["computability"] == "not_computable"
    assert macro["as_of"] == "2024-12-31T00:00:00Z"
    assert result["data_as_of"] == "2024-12-31T00:00:00Z"
    assert result["freshness_status"] == "delayed"


def test_stale_dashboard_cache_removes_precise_composites_and_alerts() -> None:
    now = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)
    result = apply_dashboard_trust_gate(_dashboard(now), cache_state="stale", now=now)

    assert result["mode"] == "historical"
    assert result["trust_status"] == "unavailable"
    assert result["freshness_status"] == "stale"
    assert result["trust"]["computability"] == "not_computable"
    assert result["indices"][0]["availability"] == "not_computable"
    assert result["indices"][0]["value"] is None
    assert result["indices"][0]["change_pct"] is None
    assert result["series"][0]["latest"] is None
    assert result["series"][0]["change_pct"] is None
    assert result["series"][0]["points"] == []
    assert result["series"][1]["latest"] == 12
    assert result["bars"] == []
    assert result["alert_rules"] == []
    assert result["alerts_suppressed"] is True
    assert {reason["code"] for reason in result["unavailable_reasons"]} == {
        "STALE_DASHBOARD_CACHE",
        "COMPOSITE_METHOD_NOT_APPROVED",
    }


def test_legacy_cache_without_complete_trust_cannot_be_reissued_as_trusted() -> None:
    now = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)
    legacy = _dashboard(now)

    result = financial_terminal._apply_cached_dashboard(legacy, cache_state="hit")

    assert result["cache"] == "invalid"
    assert result["trust"]["computable"] is False
    assert result["series"][0]["latest"] is None
    assert result["series"][0]["points"] == []
    assert result["bars"] == []
    assert result["alert_rules"] == []
    assert "INVALID_CACHED_TRUST_CONTRACT" in {
        reason["code"] for reason in result["unavailable_reasons"]
    }


def test_missing_or_expired_critical_events_make_index_not_computable() -> None:
    now = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)
    payload = _dashboard(now)
    payload["sources"][0].update(status="degraded", records=0)
    payload["sources"][1].update(
        last_updated=(now - timedelta(days=2)).isoformat().replace("+00:00", "Z")
    )

    result = apply_dashboard_trust_gate(payload, cache_state="miss", now=now)
    codes = {reason["code"] for reason in result["unavailable_reasons"]}

    assert result["trust"]["computable"] is False
    assert result["freshness_status"] == "stale"
    assert "CRITICAL_INPUT_STALE" in codes
    assert result["sources"][1]["freshness_status"] == "stale"
    assert result["data_as_of"] is not None


def test_insufficient_source_coverage_is_a_blocking_reason() -> None:
    now = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)
    payload = _dashboard(now)
    payload["sources"].extend(
        _source(f"disabled-{index}", now, status="disabled", records=0)
        for index in range(11)
    )

    result = apply_dashboard_trust_gate(payload, cache_state="miss", now=now)

    assert result["coverage"]["coverage_ratio"] == 0.4762
    assert result["trust"]["computability"] == "not_computable"
    assert "INSUFFICIENT_SOURCE_COVERAGE" in {
        reason["code"] for reason in result["unavailable_reasons"]
    }
    assert result["indices"][0]["value"] is None


def test_alert_service_does_not_evaluate_rules_when_dashboard_is_untrusted(tmp_path) -> None:
    now = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)
    dashboard = apply_dashboard_trust_gate(
        _dashboard(now), cache_state="stale", now=now
    )

    async def provider(*, refresh: bool = False):
        return dashboard

    rules_path = tmp_path / "rules.json"
    history_path = tmp_path / "history.json"
    JsonListStore(rules_path).write(
        [{"id": "custom", "metric": "Risk", "threshold": 10}]
    )
    service = FinancialAlertService(
        rules_path=rules_path,
        history_path=history_path,
        cooldown_hours=6,
        dashboard_provider=provider,
    )

    assert asyncio.run(service.list_rules(refresh=True)) == []
    assert service.refresh_history_store([], now=now) == []
    assert JsonListStore(history_path).read() == []


def test_missing_or_conflicting_trust_contract_is_never_computable() -> None:
    now = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)
    assert dashboard_is_computable(_dashboard(now)) is False

    trusted = apply_dashboard_trust_gate(_dashboard(now), cache_state="miss", now=now)
    trusted["trust"]["computable"] = False
    assert dashboard_is_computable(trusted) is False

    trusted = apply_dashboard_trust_gate(_dashboard(now), cache_state="miss", now=now)
    trusted["snapshot_id"] = "different-snapshot"
    assert dashboard_is_computable(trusted) is False

    for field, conflicting in (
        ("data_as_of", "2020-01-01T00:00:00Z"),
        ("computability", "not_computable"),
        ("computable", False),
        ("alerts_enabled", False),
        ("unavailable_reasons", [{"code": "CONFLICT", "message": "conflict"}]),
    ):
        trusted = apply_dashboard_trust_gate(_dashboard(now), cache_state="miss", now=now)
        trusted[field] = conflicting
        assert dashboard_is_computable(trusted) is False

    trusted = apply_dashboard_trust_gate(_dashboard(now), cache_state="miss", now=now)
    trusted["coverage"]["coverage_ratio"] = 0.1
    assert dashboard_is_computable(trusted) is False


def test_live_empty_or_timestamp_less_sources_do_not_count_as_coverage() -> None:
    now = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)
    payload = _dashboard(now)
    payload["sources"][4]["records"] = 0
    payload["sources"][5]["last_updated"] = None

    result = apply_dashboard_trust_gate(payload, cache_state="miss", now=now)

    assert result["sources"][4]["freshness_status"] == "live"
    assert result["sources"][5]["freshness_status"] == "offline"
    assert result["coverage"]["usable_sources"] == 8
    assert result["coverage"]["coverage_ratio"] == 0.8
    assert result["trust"]["minimum_coverage_ratio"] >= HARD_MINIMUM_SOURCE_COVERAGE
    assert result["trust"]["computable"] is False
    assert "CRITICAL_INPUT_MISSING" in {
        reason["code"] for reason in result["unavailable_reasons"]
    }


def test_future_dated_source_beyond_clock_skew_is_not_trusted() -> None:
    now = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)
    payload = _dashboard(now)
    payload["sources"][0]["last_updated"] = (
        now + timedelta(hours=1)
    ).isoformat().replace("+00:00", "Z")
    payload["sources"][1]["records"] = 0

    result = apply_dashboard_trust_gate(payload, cache_state="miss", now=now)

    assert result["sources"][0]["freshness_status"] == "offline"
    assert result["trust"]["computable"] is False
    assert "CRITICAL_INPUT_MISSING" in {
        reason["code"] for reason in result["unavailable_reasons"]
    }


def test_source_inventory_rejects_duplicates_invalid_rows_and_boolean_counts() -> None:
    now = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)
    payload = _dashboard(now)
    payload["sources"][2]["records"] = True
    payload["sources"].extend(
        [
            _source("ground-news-local", now, records=999),
            {"id": "", "status": "live", "records": 999},
            "not-a-source-record",
        ]
    )

    result = apply_dashboard_trust_gate(payload, cache_state="miss", now=now)

    reason_codes = {reason["code"] for reason in result["unavailable_reasons"]}
    assert "DUPLICATE_SOURCE_ID" in reason_codes
    assert "INVALID_SOURCE_RECORD" in reason_codes
    assert "CRITICAL_INPUT_MISSING" in reason_codes
    assert result["trust"]["computability"] == "not_computable"
    assert result["coverage"]["sources_total"] == 10
    assert len({source["id"] for source in result["sources"]}) == 10


def test_trust_discloses_bounded_source_contribution_without_claiming_attribution() -> None:
    now = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)

    result = apply_dashboard_trust_gate(_dashboard(now), cache_state="miss", now=now)

    assert result["trust"]["source_total"] == 10
    assert result["trust"]["usable_source_ids"] == sorted(
        source["id"] for source in result["sources"]
    )
    assert result["trust"]["unavailable_source_ids"] == []
    assert {
        source["contribution_state"] for source in result["sources"]
    } == {"usable"}
    assert result["trust"]["method"]["source_weighting"] == "not_established"
    assert result["trust"]["method"]["contribution_semantics"] == (
        "availability_gate_only_not_numeric_attribution"
    )


def test_unapproved_incomplete_composite_method_is_disclosed_and_fail_closed() -> None:
    now = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)

    result = apply_dashboard_trust_gate(_dashboard(now), cache_state="miss", now=now)
    card = result["trust"]["composite_method_card"]

    assert card["schema_version"] == "financial-composite-method-card-v1"
    assert card["method_version"] == result["trust"]["method_version"]
    assert card["implementation_status"] == "prototype_code_extracted"
    assert card["formula_status"] == "partially_extracted_not_governed"
    assert card["input_units_status"] == "not_dimensionally_validated"
    assert card["baseline_status"] == "not_established"
    assert card["threshold_status"] == "not_approved"
    assert card["frequency_alignment"]["status"] == "not_approved"
    assert card["frequency_alignment"]["interpolation"] == (
        "linear_by_array_position_not_observation_timestamp"
    )
    assert card["revision_policy"] == "not_established"
    assert card["wsi_aggregation"]["weights"] == {
        "diplomacy": 0.14,
        "security": 0.2,
        "energy": 0.14,
        "supply": 0.15,
        "technology": 0.12,
        "society": 0.13,
        "macro": 0.12,
    }
    assert card["test_vectors"] == [
        {
            "id": "wsi-equal-components-v1",
            "inputs": [50.0, 50.0, 50.0, 50.0, 50.0, 50.0, 50.0],
            "expected": 50.0,
        },
        {
            "id": "wsi-ordered-components-v1",
            "inputs": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0],
            "expected": 37.8,
        },
    ]
    assert result["trust"]["computability"] == "not_computable"
    assert "COMPOSITE_METHOD_NOT_APPROVED" in {
        reason["code"] for reason in result["unavailable_reasons"]
    }
    assert result["indices"][0]["value"] is None
    assert result["series"][0]["latest"] is None
    assert result["bars"] == []
    assert result["alert_rules"] == []


def test_extracted_wsi_test_vector_is_bound_and_rejects_unbounded_non_finite_inputs() -> None:
    for vector in composite_method_card()["test_vectors"]:
        assert calculate_extracted_wsi(vector["inputs"]) == pytest.approx(
            vector["expected"], abs=1e-12
        )

    for invalid in (
        [50] * 6,
        [50] * 8,
        [50, 50, 50, 50, 50, 50, True],
        [50, 50, 50, 50, 50, 50, float("nan")],
        [50, 50, 50, 50, 50, 50, float("inf")],
        [50, 50, 50, 50, 50, 50, 101],
        "50,50,50,50,50,50,50",
    ):
        with pytest.raises(ValueError):
            calculate_extracted_wsi(invalid)


def test_method_card_is_defensive_and_cannot_be_replaced_by_payload_self_report() -> None:
    first = composite_method_card()
    first["approval_status"] = "approved"
    assert composite_method_card()["approval_status"] == "not_approved"

    now = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)
    payload = _dashboard(now)
    payload["composite_method_card"] = {
        "approval_status": "approved",
        "formula_status": "complete",
    }
    payload["trust"] = {"composite_method_card": payload["composite_method_card"]}

    result = apply_dashboard_trust_gate(payload, cache_state="miss", now=now)

    assert result["composite_method_card"]["approval_status"] == "not_approved"
    assert result["trust"]["composite_method_card"] == result["composite_method_card"]
    assert result["indices"][0]["value"] is None


def test_computable_contract_rejects_tampered_source_inventory() -> None:
    now = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)
    trusted = apply_dashboard_trust_gate(_dashboard(now), cache_state="miss", now=now)

    trusted["sources"] = []
    assert dashboard_is_computable(trusted) is False

    trusted = apply_dashboard_trust_gate(_dashboard(now), cache_state="miss", now=now)
    trusted["trust"]["usable_source_ids"] = ["ground-news-local"]
    assert dashboard_is_computable(trusted) is False


def test_alert_rules_endpoint_reports_paused_trust_instead_of_silent_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)
    dashboard = apply_dashboard_trust_gate(
        _dashboard(now),
        cache_state="stale",
        now=now,
    )

    async def provider(*, refresh: bool = False):
        return dashboard

    monkeypatch.setattr(financial, "get_dashboard", provider)
    result = asyncio.run(financial.financial_alert_rules(refresh=False))

    assert result["paused"] is True
    assert result["rules"] == []
    assert result["trust_status"] == "unavailable"
    assert result["snapshot_id"] == dashboard["snapshot_id"]
    assert result["unavailable_reasons"]


def test_alert_rules_endpoint_normalizes_missing_trust_to_fail_closed_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def provider(*, refresh: bool = False):
        return {"alert_rules": [{"id": "unsafe"}], "coverage": {}}

    monkeypatch.setattr(financial, "get_dashboard", provider)
    result = asyncio.run(financial.financial_alert_rules(refresh=False))

    assert result["paused"] is True
    assert result["rules"] == []
    assert result["trust"]["trust_status"] == "unavailable"
    assert result["trust"]["computable"] is False
    assert result["trust"]["alerts_enabled"] is False
    assert result["unavailable_reasons"][0]["code"] == "INVALID_TRUST_CONTRACT"
