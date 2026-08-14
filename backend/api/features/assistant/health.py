"""Live capability probe for assistant schedule storage and leadership."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from api.features import FeatureHealthCheck, probe_mutable_paths, run_feature_probe
from api.features.assistant.config import load_assistant_schedule_settings


def probe_assistant_health(
    runner_status: Mapping[str, Any],
) -> FeatureHealthCheck:
    def operation() -> dict[str, int | str | bool]:
        settings = load_assistant_schedule_settings()
        storage = probe_mutable_paths(
            (
                settings.workspace_root,
                settings.runner_lock_path,
                settings.runner_status_path,
            )
        )
        if not bool(runner_status.get("healthy")):
            raise RuntimeError("assistant scheduler is unavailable")
        return {
            **storage,
            "scheduler_enabled": bool(runner_status.get("enabled")),
            "scheduler_state": str(runner_status.get("state") or "unknown"),
        }

    return run_feature_probe(
        "assistant",
        ("filesystem:workspace", "assistant:scheduler"),
        operation,
    )


__all__ = ("probe_assistant_health",)
