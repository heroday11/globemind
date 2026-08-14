from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.core.db import get_db
from api.features.assistant import (
    AssistantScheduleApplication,
    AssistantScheduleExecutionFailed,
    AssistantScheduleOperations,
)
from api.routes import assistant_schedules
from api.services import assistant_schedule
from api.services.auth import get_current_user_required


class _ScheduleQuery:
    def filter(self, *_args: Any, **_kwargs: Any) -> "_ScheduleQuery":
        return self

    def first(self) -> None:
        return None


class _ScheduleDB:
    def query(self, *_args: Any, **_kwargs: Any) -> _ScheduleQuery:
        return _ScheduleQuery()


def _schedule_payload(schedule_id: str = "sched-safe") -> dict[str, Any]:
    return {
        "id": schedule_id,
        "title": "Bounded briefing",
        "topic": "Bounded briefing",
        "cadence": "manual",
        "enabled": True,
        "favorite_context": {
            "folder": "evidence",
            "items": [
                {
                    "id": "source-1",
                    "title": "Bounded source",
                    "url": "https://example.org/source-1",
                    "abstract": "A bounded source excerpt for a scheduler test.",
                }
            ],
        },
    }


def _write_snapshot(
    root: Path,
    username: str,
    raw: str,
) -> Path:
    user_root = root / username
    user_root.mkdir(parents=True)
    path = user_root / assistant_schedule.SCHEDULE_FILE_NAME
    path.write_text(raw, encoding="utf-8")
    return path


def _valid_snapshot(items: list[dict[str, Any]] | None = None) -> str:
    return json.dumps(
        {
            "version": 1,
            "updated_at": "2026-08-09T00:00:00+00:00",
            "items": items or [],
        }
    )


def test_schedule_list_is_zero_write_for_missing_storage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace-root"
    monkeypatch.setattr(assistant_schedule, "WORKSPACE_ROOT", root)

    assert assistant_schedule.list_schedules("alice", 7) == []
    assert not root.exists()

    root.mkdir()
    assert assistant_schedule.list_schedules("alice", 7) == []
    assert list(root.iterdir()) == []


def test_schedule_get_route_is_zero_write_when_not_configured(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace-root"
    monkeypatch.setattr(assistant_schedule, "WORKSPACE_ROOT", root)
    app = FastAPI()
    app.include_router(assistant_schedules.router)
    app.dependency_overrides[get_current_user_required] = lambda: {
        "username": "alice",
        "user_id": 7,
    }
    app.dependency_overrides[get_db] = lambda: object()

    with TestClient(app) as client:
        response = client.get("/api/assistant/schedules")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "data": []}
    assert not root.exists()


def test_schedule_storage_rejects_dot_users_and_linked_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace-root"
    root.mkdir()
    monkeypatch.setattr(assistant_schedule, "WORKSPACE_ROOT", root)

    with pytest.raises(ValueError, match="安全|safe"):
        assistant_schedule.list_schedules(".", 7)
    with pytest.raises(ValueError, match="安全|safe"):
        assistant_schedule.list_schedules("..", 7)

    outside = tmp_path / "outside"
    outside.mkdir()
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(assistant_schedule, "WORKSPACE_ROOT", linked_root)
    with pytest.raises(ValueError, match="符号|symbolic"):
        assistant_schedule.list_schedules("alice", 7)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize(
    ("case", "raw"),
    [
        (
            "duplicate-key",
            '{"version":1,"updated_at":"2026-08-09T00:00:00+00:00",'
            '"items":[],"items":[]}',
        ),
        (
            "nan",
            '{"version":1,"updated_at":"2026-08-09T00:00:00+00:00",'
            '"items":[],"extra":NaN}',
        ),
        (
            "overflow",
            '{"version":1,"updated_at":"2026-08-09T00:00:00+00:00",'
            '"items":[],"extra":1e400}',
        ),
        (
            "deep",
            '{"version":1,"updated_at":"2026-08-09T00:00:00+00:00",'
            '"items":[],"extra":' + ("[" * 80) + "0" + ("]" * 80) + "}",
        ),
        (
            "too-many",
            _valid_snapshot(
                [
                    {
                        "id": f"sched-{index}",
                        "title": "brief",
                        "topic": "brief",
                        "cadence": "manual",
                        "user_id": 7,
                        "owner": "alice",
                    }
                    for index in range(501)
                ]
            ),
        ),
        (
            "oversize",
            '{"version":1,"updated_at":"2026-08-09T00:00:00+00:00",'
            '"items":[],"extra":"' + ("x" * 1_100_000) + '"}',
        ),
    ],
    ids=[
        "duplicate-key",
        "nan",
        "overflow",
        "deep",
        "too-many",
        "oversize",
    ],
)
def test_schedule_reader_rejects_unbounded_or_ambiguous_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: str,
    raw: str,
) -> None:
    del case
    root = tmp_path / "workspace-root"
    path = _write_snapshot(root, "alice", raw)
    before = path.read_bytes()
    monkeypatch.setattr(assistant_schedule, "WORKSPACE_ROOT", root)

    with pytest.raises(ValueError, match="schedule|调度|JSON|快照"):
        assistant_schedule.list_schedules("alice", 7)

    assert path.read_bytes() == before
    assert sorted(entry.name for entry in path.parent.iterdir()) == [path.name]


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_schedule_reader_rejects_linked_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    link_kind: str,
) -> None:
    root = tmp_path / "workspace-root"
    user_root = root / "alice"
    user_root.mkdir(parents=True)
    victim = tmp_path / "victim.json"
    victim.write_text(_valid_snapshot(), encoding="utf-8")
    path = user_root / assistant_schedule.SCHEDULE_FILE_NAME
    if link_kind == "symlink":
        path.symlink_to(victim)
    else:
        os.link(victim, path)
    before = victim.read_bytes()
    monkeypatch.setattr(assistant_schedule, "WORKSPACE_ROOT", root)

    with pytest.raises(ValueError, match="regular|链接|link|trustworthy"):
        assistant_schedule.list_schedules("alice", 7)

    assert victim.read_bytes() == before


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_schedule_mutation_rejects_linked_file_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    link_kind: str,
) -> None:
    root = tmp_path / "workspace-root"
    user_root = root / "alice"
    user_root.mkdir(parents=True)
    victim = tmp_path / "lock-victim"
    victim.write_text("do-not-touch", encoding="utf-8")
    lock_path = user_root / f"{assistant_schedule.SCHEDULE_FILE_NAME}.lock"
    if link_kind == "symlink":
        lock_path.symlink_to(victim)
    else:
        os.link(victim, lock_path)
    before = victim.read_bytes()
    monkeypatch.setattr(assistant_schedule, "WORKSPACE_ROOT", root)

    with pytest.raises(ValueError, match="lock|锁|link"):
        assistant_schedule.upsert_schedule("alice", 7, _schedule_payload())

    assert victim.read_bytes() == before
    assert not (user_root / assistant_schedule.SCHEDULE_FILE_NAME).exists()


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_runner_lock_rejects_links_without_touching_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    link_kind: str,
) -> None:
    victim = tmp_path / "runner-lock-victim"
    victim.write_text("do-not-touch", encoding="utf-8")
    lock_path = tmp_path / "runner.lock"
    if link_kind == "symlink":
        lock_path.symlink_to(victim)
    else:
        os.link(victim, lock_path)
    monkeypatch.setattr(assistant_schedule, "RUNNER_LOCK_PATH", lock_path)

    assert assistant_schedule._try_acquire_runner_lock() is None
    assert victim.read_text(encoding="utf-8") == "do-not-touch"


def test_runner_status_rejects_future_or_untrusted_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    status_path = tmp_path / "runner-status.json"
    monkeypatch.setattr(assistant_schedule, "RUNNER_STATUS_PATH", status_path)
    monkeypatch.setattr(assistant_schedule, "_runner_task", None)
    monkeypatch.delenv("ASSISTANT_SCHEDULE_DISABLE", raising=False)

    future = datetime.now(timezone.utc) + timedelta(hours=1)
    status_path.write_text(
        json.dumps({"state": "running", "pid": 7, "heartbeat_at": future.isoformat()}),
        encoding="utf-8",
    )
    assert assistant_schedule.get_schedule_runner_status()["healthy"] is False

    status_path.write_text(
        '{"state":"running","heartbeat_at":"2026-08-09T00:00:00Z",'
        '"scan_count":1,"scan_count":2}',
        encoding="utf-8",
    )
    assert assistant_schedule.get_schedule_runner_status()["state"] == "unavailable"


def test_runner_status_rejects_linked_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    victim = tmp_path / "runner-status-victim"
    victim.write_text(
        json.dumps(
            {
                "state": "running",
                "pid": 7,
                "heartbeat_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    status_path = tmp_path / "runner-status.json"
    os.link(victim, status_path)
    monkeypatch.setattr(assistant_schedule, "RUNNER_STATUS_PATH", status_path)
    monkeypatch.setattr(assistant_schedule, "_runner_task", None)
    monkeypatch.delenv("ASSISTANT_SCHEDULE_DISABLE", raising=False)

    shared = assistant_schedule._read_runner_status()
    status = assistant_schedule.get_schedule_runner_status()

    assert shared == {}
    assert status["healthy"] is False
    assert status["state"] == "unavailable"


@pytest.mark.parametrize(
    "raw",
    [
        '{"state":"running","state":"stopped"}',
        '{"state":"running","heartbeat_at":NaN}',
        '{"state":"running","extra":' + ("[" * 80) + "0" + ("]" * 80) + "}",
        '{"state":"running","extra":"' + ("x" * 70_000) + '"}',
    ],
    ids=["duplicate", "nonfinite", "deep", "oversize"],
)
def test_runner_status_reader_is_strict_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    raw: str,
) -> None:
    status_path = tmp_path / "runner-status.json"
    status_path.write_text(raw, encoding="utf-8")
    monkeypatch.setattr(assistant_schedule, "RUNNER_STATUS_PATH", status_path)

    assert assistant_schedule._read_runner_status() == {}


def test_future_interval_anchor_is_rebased_to_the_observed_clock() -> None:
    now = datetime(2026, 8, 9, 4, 0, tzinfo=timezone.utc)
    item = {
        "enabled": True,
        "cadence": "hourly",
        "last_run_at": "2099-01-01T00:00:00+00:00",
    }

    next_run = assistant_schedule._parse_dt(
        assistant_schedule.compute_next_run_at(item, after=now)
    )

    assert next_run == now + timedelta(hours=1)


def test_process_clock_high_water_survives_wall_clock_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = datetime(2026, 8, 9, 4, 0, tzinfo=timezone.utc)
    walls = iter([observed, observed - timedelta(hours=1)])
    monotonic_values = iter([100.0, 101.5])
    previous_high_water = assistant_schedule._clock_high_water
    previous_monotonic = assistant_schedule._clock_last_monotonic
    assistant_schedule._clock_high_water = None
    assistant_schedule._clock_last_monotonic = None
    monkeypatch.setattr(assistant_schedule, "_wall_now_utc", lambda: next(walls))
    monkeypatch.setattr(
        assistant_schedule,
        "_monotonic_seconds",
        lambda: next(monotonic_values),
    )
    try:
        first = assistant_schedule._now_utc()
        second = assistant_schedule._now_utc()
    finally:
        assistant_schedule._clock_high_water = previous_high_water
        assistant_schedule._clock_last_monotonic = previous_monotonic

    assert first == observed
    assert second == observed + timedelta(seconds=1.5)


def test_schedule_reader_drops_cross_tenant_records_without_rewriting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace-root"
    raw = _valid_snapshot(
        [
            {
                **_schedule_payload("sched-bob"),
                "owner": "bob",
                "user_id": 99,
            }
        ]
    )
    path = _write_snapshot(root, "alice", raw)
    before = path.read_bytes()
    monkeypatch.setattr(assistant_schedule, "WORKSPACE_ROOT", root)

    assert assistant_schedule.list_schedules("alice", 7) == []
    assert path.read_bytes() == before


def test_due_scan_never_executes_an_unbound_legacy_tenant_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace-root"
    raw = _valid_snapshot(
        [
            {
                **_schedule_payload("sched-unbound"),
                "cadence": "hourly",
                "next_run_at": "2026-01-01T00:00:00+00:00",
                "user_id": 99,
            }
        ]
    )
    _write_snapshot(root, "alice", raw)
    monkeypatch.setattr(assistant_schedule, "WORKSPACE_ROOT", root)

    assert assistant_schedule.due_schedules() == []


def test_schedule_response_drops_unknown_secret_bearing_fields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace-root"
    raw = _valid_snapshot(
        [
            {
                **_schedule_payload("sched-redacted"),
                "owner": "alice",
                "user_id": 7,
                "last_status": "failed",
                "last_error": "postgresql" + "://private-password@example.invalid/db",
                "last_error_code": "PRIVATE_PASSWORD_CANARY",
                "internal_secret": "secret-canary",
                "last_file": {"file_name": "../../secret-canary.md"},
                "last_assurance": {"secret": "private-password"},
            }
        ]
    )
    _write_snapshot(root, "alice", raw)
    monkeypatch.setattr(assistant_schedule, "WORKSPACE_ROOT", root)

    schedule = assistant_schedule.list_schedules("alice", 7)[0]
    serialized = json.dumps(schedule, ensure_ascii=False)

    assert "private-password" not in serialized
    assert "secret-canary" not in serialized
    assert schedule["last_error_code"] == "RUN_FAILED"
    assert schedule["last_file"] is None
    assert schedule["last_assurance"] is None


def test_manual_run_does_not_persist_or_raise_secret_error_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace-root"
    monkeypatch.setattr(assistant_schedule, "WORKSPACE_ROOT", root)
    saved = assistant_schedule.upsert_schedule("alice", 7, _schedule_payload())

    async def failing_call(**_kwargs: Any) -> str:
        raise RuntimeError("postgresql" + "://private-user:private-password@example.invalid/db")

    monkeypatch.setattr(assistant_schedule, "assistant_system_prompt", lambda: "system")
    monkeypatch.setattr(assistant_schedule, "call_hermes_once", failing_call)

    operations = AssistantScheduleOperations(
        list_schedules=assistant_schedule.list_schedules,
        upsert_schedule=assistant_schedule.upsert_schedule,
        delete_schedule=assistant_schedule.delete_schedule,
        run_schedule=assistant_schedule.run_schedule,
    )
    application = AssistantScheduleApplication(operations)
    with pytest.raises(AssistantScheduleExecutionFailed, match="执行失败") as captured:
        asyncio.run(
            application.run(
                {"username": "alice", "user_id": 7},
                saved["id"],
                _ScheduleDB(),
            )
        )
    assert "private-password" not in str(captured.value)

    failed = assistant_schedule.list_schedules("alice", 7)[0]
    serialized = json.dumps(failed, ensure_ascii=False)
    assert "private-password" not in serialized
    assert failed["last_error"] == "报告生成失败；内部错误详情未公开"


def test_schedule_run_lock_directory_cannot_be_a_symlink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace-root"
    user_root = root / "alice"
    user_root.mkdir(parents=True)
    outside = tmp_path / "outside-locks"
    outside.mkdir()
    (user_root / ".assistant_schedule_locks").symlink_to(
        outside,
        target_is_directory=True,
    )
    monkeypatch.setattr(assistant_schedule, "WORKSPACE_ROOT", root)

    with pytest.raises(ValueError, match="锁|lock|symbolic"):
        assistant_schedule._schedule_lock_path("alice", "sched-safe")
    assert list(outside.iterdir()) == []


def test_report_filename_exhaustion_never_removes_existing_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace-root"
    monkeypatch.setattr(assistant_schedule, "WORKSPACE_ROOT", root)
    monkeypatch.setattr(assistant_schedule, "_MAX_REPORT_FILENAME_ATTEMPTS", 2)
    workspace = assistant_schedule._ensure_report_workspace("alice")
    first = workspace / "2026-08-09T04-00-00-Bounded-briefing.md"
    second = workspace / "2026-08-09T04-00-00-Bounded-briefing-1.md"
    first.write_text("first-existing-report", encoding="utf-8")
    second.write_text("second-existing-report", encoding="utf-8")

    with pytest.raises(RuntimeError, match="allocation exhausted"):
        assistant_schedule._save_report(
            "alice",
            {"title": "Bounded briefing"},
            "new report",
            "2026-08-09T04:00:00+00:00",
            assurance={},
        )

    assert first.read_text(encoding="utf-8") == "first-existing-report"
    assert second.read_text(encoding="utf-8") == "second-existing-report"


def test_due_queue_deduplicates_jobs_waiting_for_runner_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        calls: list[tuple[str, str]] = []

        async def blocked_run(username: str, schedule: dict[str, Any]) -> None:
            calls.append((username, str(schedule["id"])))
            entered.set()
            await release.wait()

        monkeypatch.setattr(assistant_schedule, "_run_due_item", blocked_run)
        assistant_schedule._runner_jobs.clear()
        assistant_schedule._running_keys.clear()
        assistant_schedule._queued_keys.clear()
        due = [{"username": "alice", "schedule": {"id": "sched-safe"}}]
        try:
            assistant_schedule._enqueue_due_items(due)
            assistant_schedule._enqueue_due_items(due)
            await asyncio.wait_for(entered.wait(), timeout=1)
            assert calls == [("alice", "sched-safe")]
            assert len(assistant_schedule._runner_jobs) == 1
            assert assistant_schedule._queued_keys == {"alice:sched-safe"}
            release.set()
            await asyncio.gather(*assistant_schedule._runner_jobs)
            await asyncio.sleep(0)
            assert assistant_schedule._runner_jobs == set()
            assert assistant_schedule._queued_keys == set()
        finally:
            release.set()
            await assistant_schedule._drain_runner_jobs(cancel_immediately=True)
            assistant_schedule._running_keys.clear()
            assistant_schedule._queued_keys.clear()

    asyncio.run(scenario())
