"""Shared business-data freshness checks for feature health probes."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any

from api.core.environment import int_setting
from api.features.health import FeatureHealthCheck


def freshness_sla_hours(env_name: str, default: int) -> int:
    return int_setting(env_name, default, minimum=1)


def apply_freshness(
    check: FeatureHealthCheck,
    latest: Any,
    *,
    sla_hours: int,
    metric_name: str,
    now: datetime | None = None,
) -> FeatureHealthCheck:
    """Attach an auditable timestamp without conflating stale data with downtime."""
    if check.status == "down":
        return check
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if isinstance(latest, date) and not isinstance(latest, datetime):
        # A date-only source proves no finer cutoff than the start of that UTC
        # day. Using end-of-day can place the evidence in the future and make
        # an unobserved interval look current.
        latest = datetime.combine(latest, time.min, tzinfo=timezone.utc)
    if isinstance(latest, datetime) and latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    if not isinstance(latest, datetime):
        return check.model_copy(
            update={
                "status": "stale",
                "detail": "business data timestamp is unavailable",
                "metrics": {
                    **check.metrics,
                    "freshness_status": "missing",
                    "freshness_sla_hours": sla_hours,
                    "freshness_threshold_approval_state": "not_approved",
                },
            }
        )
    lag_hours = max(
        0.0,
        (current - latest.astimezone(timezone.utc)).total_seconds() / 3600,
    )
    stale = lag_hours > sla_hours
    return check.model_copy(
        update={
            "status": "stale" if stale else "up",
            "detail": (
                "business data freshness threshold exceeded" if stale else None
            ),
            "metrics": {
                **check.metrics,
                metric_name: latest.astimezone(timezone.utc).isoformat(),
                "freshness_lag_hours": round(lag_hours, 1),
                "freshness_sla_hours": sla_hours,
                "freshness_threshold_approval_state": "not_approved",
                "freshness_status": "stale" if stale else "current",
            },
        }
    )


__all__ = ("apply_freshness", "freshness_sla_hours")
