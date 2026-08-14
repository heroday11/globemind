"""Live capability probe for financial alert persistence."""
from __future__ import annotations

from pathlib import Path

from api.core.environment import string_setting
from api.features import FeatureHealthCheck, probe_mutable_paths, run_feature_probe
from api.features.financial.json_store import JsonListStore


def _financial_store_paths() -> tuple[Path, Path]:
    return (
        Path(
            string_setting(
                "FINANCIAL_ALERT_RULES_STORE",
                "/root/data/web/cache/financial_alert_rules.json",
            )
        ),
        Path(
            string_setting(
                "FINANCIAL_ALERT_HISTORY_STORE",
                "/root/data/web/cache/financial_alert_history.json",
            )
        ),
    )


def probe_financial_health() -> FeatureHealthCheck:
    def operation() -> dict[str, int]:
        paths = _financial_store_paths()
        metrics = probe_mutable_paths(paths)
        for path in paths:
            JsonListStore(path).read_unlocked(strict=True)
        return {**metrics, "stores_parsed": len(paths)}

    return run_feature_probe(
        "financial-alerts",
        ("filesystem:financial-alert-stores",),
        operation,
    )


__all__ = ("probe_financial_health",)
