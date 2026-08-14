from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from api.features.financial import (
    AlertRuleNotFound,
    AlertRulePayload,
    FinancialAlertService,
    JsonListStore,
    JsonStoreError,
    enrich_rule,
    metric_lookup_from_dashboard,
)
from api.routes import financial


def _service(tmp_path, dashboard_provider, *, cooldown_hours: int = 6):
    return FinancialAlertService(
        rules_path=tmp_path / "rules.json",
        history_path=tmp_path / "history.json",
        cooldown_hours=cooldown_hours,
        dashboard_provider=dashboard_provider,
    )


def _trusted_dashboard(**content):
    source_ids = [f"source-{index}" for index in range(1, 5)]
    trust = {
        "schema_version": "financial-trust-v1",
        "snapshot_id": "fin-test-snapshot",
        "trust_status": "trusted",
        "freshness_status": "live",
        "computability": "computable",
        "computable": True,
        "data_as_of": "2026-08-09T08:00:00Z",
        "coverage_ratio": 1.0,
        "minimum_coverage_ratio": 0.5,
        "usable_sources": 4,
        "source_total": 4,
        "usable_source_ids": source_ids,
        "unavailable_source_ids": [],
        "source_status": {"live": 4},
        "model_version": "deterministic-ruleset-v0.9.0",
        "method_version": "world-state-composite-v0.9.0",
        "unavailable_reasons": [],
        "alerts_enabled": True,
        "method": {
            "source_inventory_bound": 128,
            "source_weighting": "not_established",
            "contribution_semantics": (
                "availability_gate_only_not_numeric_attribution"
            ),
        },
    }
    return {
        **content,
        "schema_version": trust["schema_version"],
        "snapshot_id": trust["snapshot_id"],
        "trust_status": trust["trust_status"],
        "freshness_status": trust["freshness_status"],
        "computability": trust["computability"],
        "computable": trust["computable"],
        "alerts_enabled": trust["alerts_enabled"],
        "data_as_of": trust["data_as_of"],
        "coverage": {
            "coverage_ratio": trust["coverage_ratio"],
            "minimum_coverage_ratio": trust["minimum_coverage_ratio"],
            "usable_sources": trust["usable_sources"],
            "sources_total": trust["source_total"],
            "source_status": trust["source_status"],
        },
        "sources": [
            {
                "id": source_id,
                "freshness_status": "live",
                "records": 1,
                "contribution_state": "usable",
            }
            for source_id in source_ids
        ],
        "unavailable_reasons": trust["unavailable_reasons"],
        "alerts_suppressed": False,
        "model_version": trust["model_version"],
        "method_version": trust["method_version"],
        "trust": trust,
    }


def test_service_does_not_evaluate_rules_from_self_reported_source_trust(tmp_path) -> None:
    dashboard = _trusted_dashboard(**{
        "indices": [{"id": "risk", "metric_id": "risk-index", "name": "Risk", "value": 12.5}],
        "alert_rules": [
            {"id": "system-only", "metric": "System", "threshold": 20},
            {"id": "custom", "metric": "Default duplicate", "threshold": 30},
        ],
    })

    async def provider(*, refresh: bool = False):
        assert refresh is True
        return dashboard

    JsonListStore(tmp_path / "rules.json").write(
        [{"id": "custom", "metric_id": "risk-index", "threshold": 10}]
    )
    rules = asyncio.run(_service(tmp_path, provider).list_rules(refresh=True))

    assert rules == []


def test_service_mutations_are_transport_independent(tmp_path) -> None:
    async def provider(*, refresh: bool = False):
        return {}

    service = _service(tmp_path, provider)
    created = service.create_rule({"id": "rule-1", "metric": "Risk", "threshold": 5})
    assert created["source"] == "user"
    assert service.update_rule("rule-1", {"threshold": 8})["threshold"] == 8
    assert JsonListStore(tmp_path / "rules.json").read()[0]["threshold"] == 8

    service.delete_rule("rule-1")
    assert JsonListStore(tmp_path / "rules.json").read() == []
    with pytest.raises(AlertRuleNotFound):
        service.update_rule("missing", {"threshold": 1})
    with pytest.raises(AlertRuleNotFound):
        service.delete_rule("missing")


def test_history_read_does_not_call_dashboard_and_refresh_is_idempotent(tmp_path) -> None:
    calls = 0

    async def provider(*, refresh: bool = False):
        nonlocal calls
        calls += 1
        return _trusted_dashboard(**{
            "alert_rules": [
                {
                    "id": "breach",
                    "metric": "Risk",
                    "current": 12,
                    "threshold": 10,
                    "breached": True,
                }
            ]
        })

    service = _service(tmp_path, provider)
    assert asyncio.run(service.history(limit=50)) == []
    assert calls == 0

    now = datetime(2026, 7, 10, 2, 0, tzinfo=timezone.utc)
    rules = asyncio.run(service.list_rules(refresh=True))
    first = service.refresh_history_store(rules, now=now)
    second = service.refresh_history_store(rules, now=now)
    assert calls == 1
    assert first == second == []


def test_rule_enrichment_contract_is_deterministic() -> None:
    lookup = metric_lookup_from_dashboard(
        {"watchlist": [{"symbol": "OIL", "label": "Oil", "price": 82, "unit": "USD"}]}
    )
    enriched = enrich_rule(
        {"id": "oil", "metric_id": "OIL", "threshold": 100, "baseline": 80},
        lookup,
    )

    assert enriched == {
        "id": "oil",
        "metric_id": "OIL",
        "threshold": 100.0,
        "baseline": 80.0,
        "metric": "Oil",
        "unit": "USD",
        "current": 82.0,
        "severity": "medium",
        "breached": False,
        "trend": "up",
        "source": "user",
    }


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_alert_payload_rejects_non_finite_numbers(value: float) -> None:
    with pytest.raises(ValueError):
        AlertRulePayload(metric="Risk", threshold=value)


def test_client_derived_fields_are_not_persisted_or_trusted(tmp_path) -> None:
    async def provider(*, refresh: bool = False):
        return _trusted_dashboard(**{
            "indices": [
                {
                    "id": "risk",
                    "metric_id": "risk-index",
                    "name": "Risk",
                    "value": 3,
                }
            ]
        })

    service = _service(tmp_path, provider)
    service.create_rule(
        {
            "id": "custom",
            "metric_id": "risk-index",
            "metric": "Risk",
            "threshold": 10,
            "baseline": 5,
            "current": 999,
            "breached": True,
            "trend": "down",
        }
    )
    service.update_rule(
        "custom",
        {
            "threshold": 8,
            "current": 999,
            "breached": True,
            "trend": "down",
        },
    )

    stored = JsonListStore(tmp_path / "rules.json").read()[0]
    rules = asyncio.run(service.list_rules())

    assert not {"current", "breached", "trend"}.intersection(stored)
    assert stored["threshold"] == 8
    assert rules == []


def test_legacy_dashboard_without_trust_never_evaluates_alert_rules(tmp_path) -> None:
    async def provider(*, refresh: bool = False):
        return {
            "indices": [{"id": "risk", "value": 999}],
            "alert_rules": [{"id": "legacy", "breached": True}],
        }

    assert asyncio.run(_service(tmp_path, provider).list_rules()) == []


def test_non_finite_store_write_fails_before_replacing_existing_data(tmp_path) -> None:
    store = JsonListStore(tmp_path / "rules.json")
    store.write([{"id": "original", "threshold": 1}])
    original = store.path.read_bytes()

    with pytest.raises(ValueError, match="Out of range float values"):
        store.write([{"id": "poison", "threshold": float("nan")}])

    assert store.path.read_bytes() == original
    store.path.write_text('[{"id":"poison","threshold":NaN}]', encoding="utf-8")
    assert store.read() == []
    with pytest.raises(JsonStoreError, match="cannot safely read JSON store"):
        store.mutate(lambda rows: (rows, None))


def test_dashboard_failure_prevents_rule_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    rules_path = tmp_path / "rules.json"
    JsonListStore(rules_path).write([{"id": "existing", "threshold": 1}])

    async def unavailable_dashboard(*, refresh: bool = False):
        raise RuntimeError("dashboard unavailable")

    monkeypatch.setattr(financial, "ALERT_RULES_STORE", rules_path)
    monkeypatch.setattr(financial, "get_dashboard", unavailable_dashboard)

    with pytest.raises(RuntimeError, match="dashboard unavailable"):
        asyncio.run(
            financial.financial_alert_rules_create(
                financial.AlertRulePayload(metric="Risk", threshold=5)
            )
        )
    with pytest.raises(RuntimeError, match="dashboard unavailable"):
        asyncio.run(
            financial.financial_alert_rules_update(
                "existing",
                financial.AlertRulePayload(threshold=9),
            )
        )

    assert JsonListStore(rules_path).read() == [{"id": "existing", "threshold": 1}]
