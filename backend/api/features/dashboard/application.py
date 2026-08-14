"""Dashboard readiness application service."""
from __future__ import annotations

from typing import Any, Mapping

from sqlalchemy.orm import Session

from api.core.environment import app_version, string_setting
from api.features.dashboard.repository import probe_dashboard_health
from api.features.identity import probe_identity_health


def runtime_release() -> dict[str, str]:
    return {
        "version": app_version(),
        "build_id": string_setting("BUILD_ID", "local"),
        "git_sha": string_setting("GIT_SHA", "unknown"),
    }


def database_readiness(db: Session) -> dict[str, Any]:
    checks = (probe_dashboard_health(db), probe_identity_health(db))
    latency_ms = round(sum(check.latency_ms for check in checks), 2)
    if all(check.healthy for check in checks):
        return {"status": "up", "latency_ms": latency_ms}
    return {
        "status": "down",
        "latency_ms": latency_ms,
        "detail": "database probe failed",
    }


def build_dashboard_readiness(
    db: Session,
    scheduler: Mapping[str, Any],
) -> tuple[int, dict[str, Any]]:
    database = database_readiness(db)
    database_ready = database["status"] == "up"
    scheduler_healthy = bool(scheduler.get("healthy"))
    payload = {
        "status": (
            "healthy"
            if database_ready and scheduler_healthy
            else ("degraded" if database_ready else "unhealthy")
        ),
        "ready": database_ready,
        "service": "globemind-api",
        "release": runtime_release(),
        "checks": {
            "database": {**database, "critical": True},
            "assistant_scheduler": {**scheduler, "critical": False},
        },
    }
    return (200 if database_ready else 503), payload


__all__ = ("build_dashboard_readiness", "database_readiness", "runtime_release")
