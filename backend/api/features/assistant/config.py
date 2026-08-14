"""Centralized environment-backed configuration for assistant schedules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from api.core.environment import bool_setting, int_setting, string_setting


@dataclass(frozen=True)
class AssistantScheduleSettings:
    workspace_root: Path
    default_timezone: str
    runner_interval_seconds: int
    runner_concurrency: int
    run_timeout_seconds: int
    runner_shutdown_timeout_seconds: int
    runner_standby_interval_seconds: int
    runner_lock_path: Path
    runner_status_path: Path


def load_assistant_schedule_settings() -> AssistantScheduleSettings:
    workspace_root = Path(
        string_setting("GLOBEMIND_WORKSPACE_ROOT", "/root/data/workspace")
    )
    return AssistantScheduleSettings(
        workspace_root=workspace_root,
        default_timezone=string_setting(
            "ASSISTANT_SCHEDULE_DEFAULT_TZ",
            "Asia/Shanghai",
        ),
        runner_interval_seconds=int_setting(
            "ASSISTANT_SCHEDULE_TICK_SEC",
            60,
            minimum=1,
        ),
        runner_concurrency=int_setting(
            "ASSISTANT_SCHEDULE_CONCURRENCY",
            1,
            minimum=1,
        ),
        run_timeout_seconds=int_setting(
            "ASSISTANT_SCHEDULE_RUN_TIMEOUT_SEC",
            480,
        ),
        runner_shutdown_timeout_seconds=int_setting(
            "ASSISTANT_SCHEDULE_SHUTDOWN_TIMEOUT_SEC",
            30,
            minimum=1,
        ),
        runner_standby_interval_seconds=int_setting(
            "ASSISTANT_SCHEDULE_STANDBY_TICK_SEC",
            10,
            minimum=1,
        ),
        runner_lock_path=Path(
            string_setting(
                "ASSISTANT_SCHEDULE_RUNNER_LOCK",
                str(workspace_root / ".assistant_schedule_runner.lock"),
            )
        ),
        runner_status_path=Path(
            string_setting(
                "ASSISTANT_SCHEDULE_RUNNER_STATUS",
                str(workspace_root / ".assistant_schedule_runner.status.json"),
            )
        ),
    )


def assistant_schedule_runner_disabled() -> bool:
    return bool_setting("ASSISTANT_SCHEDULE_DISABLE")


__all__ = (
    "AssistantScheduleSettings",
    "assistant_schedule_runner_disabled",
    "load_assistant_schedule_settings",
)
