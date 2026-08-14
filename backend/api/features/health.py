"""Shared, side-effect-free contracts for feature capability probes."""
from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

_SQL_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")


class FeatureHealthCheck(BaseModel):
    feature_id: str
    status: Literal["up", "degraded", "stale", "down"]
    latency_ms: float = Field(ge=0)
    dependencies: list[str] = Field(min_length=1)
    detail: str | None = None
    metrics: dict[str, int | float | str | bool] = Field(default_factory=dict)

    @property
    def healthy(self) -> bool:
        return self.status == "up"

    @property
    def available(self) -> bool:
        """Whether the capability remains safe to serve to users."""
        return self.status != "down"


class FeatureHealthReport(BaseModel):
    status: Literal["healthy", "degraded", "unhealthy"]
    ready: bool
    checks: dict[str, FeatureHealthCheck]


def run_feature_probe(
    feature_id: str,
    dependencies: Sequence[str],
    operation: Callable[[], Mapping[str, int | float | str | bool] | None],
) -> FeatureHealthCheck:
    """Execute one real capability probe while keeping failures redacted."""
    started = time.monotonic()
    try:
        metrics = dict(operation() or {})
    except Exception:
        return FeatureHealthCheck(
            feature_id=feature_id,
            status="down",
            latency_ms=round((time.monotonic() - started) * 1000, 2),
            dependencies=list(dependencies),
            detail="capability probe failed",
        )
    return FeatureHealthCheck(
        feature_id=feature_id,
        status="up",
        latency_ms=round((time.monotonic() - started) * 1000, 2),
        dependencies=list(dependencies),
        metrics=metrics,
    )


def probe_postgres_relations(
    db: Session,
    requirements: Mapping[str, Sequence[str]],
) -> dict[str, int]:
    """Prove that the current role can read each feature relation and column set."""
    if not requirements:
        raise ValueError("at least one relation is required")
    for raw_relation, raw_columns in requirements.items():
        parts = raw_relation.split(".")
        if len(parts) != 2 or any(_SQL_IDENTIFIER.fullmatch(part) is None for part in parts):
            raise ValueError("relation must use a static schema-qualified identifier")
        columns = tuple(raw_columns)
        if not columns or any(_SQL_IDENTIFIER.fullmatch(column) is None for column in columns):
            raise ValueError("columns must use static SQL identifiers")
        projection = ", ".join(columns)
        db.execute(text(f"SELECT {projection} FROM {raw_relation} LIMIT 1")).first()
    return {"relations_checked": len(requirements)}


def probe_mutable_paths(paths: Sequence[Path]) -> dict[str, int]:
    """Check read/write traversal without creating or modifying feature data."""
    if not paths:
        raise ValueError("at least one mutable path is required")
    checked = 0
    existing = 0
    for raw_path in paths:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            raise ValueError("mutable paths must be absolute")
        checked += 1
        if path.exists():
            existing += 1
            mode = os.R_OK | os.W_OK
            if path.is_dir():
                mode |= os.X_OK
            if not os.access(path, mode):
                raise PermissionError("configured feature path is not accessible")
            parent = path if path.is_dir() else path.parent
        else:
            parent = path.parent
            while not parent.exists() and parent != parent.parent:
                parent = parent.parent
        if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK):
            raise PermissionError("configured feature path cannot be created safely")
    return {"paths_checked": checked, "paths_existing": existing}


def build_feature_health_report(
    checks: Sequence[FeatureHealthCheck],
) -> FeatureHealthReport:
    by_id: dict[str, FeatureHealthCheck] = {}
    for check in checks:
        if check.feature_id in by_id:
            raise ValueError(f"duplicate feature health check: {check.feature_id}")
        by_id[check.feature_id] = check
    if not by_id:
        raise ValueError("feature health report requires at least one check")
    ready = all(check.available for check in by_id.values())
    if not ready:
        status = "unhealthy"
    elif all(check.healthy for check in by_id.values()):
        status = "healthy"
    else:
        status = "degraded"
    return FeatureHealthReport(
        status=status,
        ready=ready,
        checks=by_id,
    )


__all__ = (
    "FeatureHealthCheck",
    "FeatureHealthReport",
    "build_feature_health_report",
    "probe_mutable_paths",
    "probe_postgres_relations",
    "run_feature_probe",
)
