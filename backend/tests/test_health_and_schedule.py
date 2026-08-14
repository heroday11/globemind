from __future__ import annotations

import asyncio
import fcntl
import json
import multiprocessing
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from api.routes import dashboard  # noqa: E402
from api.services import assistant_schedule  # noqa: E402


class _ScalarResult:
    def __init__(self, value: Any):
        self.value = value

    def scalar(self) -> Any:
        return self.value

    def fetchall(self) -> list[tuple[str, str]]:
        required = {
            "news": {"id", "title", "body", "url", "published_at", "language"},
            "app_user": {"id", "username", "password_hash", "is_active", "role", "api_keys"},
        }
        return [(table, column) for table, columns in required.items() for column in columns]

    def first(self) -> tuple[int] | None:
        return (1,) if self.value else None


class _FakeDB:
    def __init__(self, value: Any = True, error: Exception | None = None):
        self.value = value
        self.error = error

    def execute(self, _query: Any) -> _ScalarResult:
        if self.error is not None:
            raise self.error
        return _ScalarResult(self.value)


class _ScheduleQuery:
    def filter(self, *_args: Any, **_kwargs: Any) -> "_ScheduleQuery":
        return self

    def first(self) -> None:
        return None


class _ScheduleDB:
    def query(self, *_args: Any, **_kwargs: Any) -> _ScheduleQuery:
        return _ScheduleQuery()


def _schedule_payload(schedule_id: str) -> dict[str, Any]:
    return {
        "id": schedule_id,
        "title": schedule_id,
        "topic": schedule_id,
        "cadence": "manual",
        "enabled": True,
    }


def _concurrent_schedule_worker(
    root: str,
    index: int,
    start: Any,
    results: Any,
) -> None:
    from api.services import assistant_schedule as worker_schedule

    worker_schedule.WORKSPACE_ROOT = Path(root)
    original_read = worker_schedule._read_schedule_file_unlocked

    def delayed_read(username: str) -> list[dict[str, Any]]:
        items = original_read(username)
        time.sleep(0.05)
        return items

    worker_schedule._read_schedule_file_unlocked = delayed_read
    start.wait(timeout=5)
    try:
        saved = worker_schedule.upsert_schedule(
            "concurrent-user",
            7,
            _schedule_payload(f"schedule-{index}"),
        )
        results.put(saved["id"])
    except Exception as exc:  # pragma: no cover - surfaced through the process queue
        results.put(f"error:{type(exc).__name__}:{exc}")


def _response_json(response: Any) -> dict[str, Any]:
    return json.loads(response.body.decode("utf-8"))


def test_runner_lock_has_one_owner_and_supports_handoff(monkeypatch, tmp_path: Path):
    lock_path = tmp_path / "runner.lock"
    monkeypatch.setattr(assistant_schedule, "RUNNER_LOCK_PATH", lock_path)

    first = assistant_schedule._try_acquire_runner_lock()
    assert first is not None
    try:
        assert assistant_schedule._try_acquire_runner_lock() is None
    finally:
        assistant_schedule._release_runner_lock(first)

    replacement = assistant_schedule._try_acquire_runner_lock()
    assert replacement is not None
    assistant_schedule._release_runner_lock(replacement)


def test_multiprocess_schedule_updates_do_not_lose_records(tmp_path: Path):
    root = tmp_path / "workspace-root"
    ctx = multiprocessing.get_context("spawn")
    start = ctx.Event()
    results = ctx.Queue()
    processes = [
        ctx.Process(
            target=_concurrent_schedule_worker,
            args=(str(root), index, start, results),
        )
        for index in range(6)
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    saved_ids = {results.get(timeout=2) for _ in processes}
    assert saved_ids == {f"schedule-{index}" for index in range(6)}
    payload = json.loads(
        (root / "concurrent-user" / assistant_schedule.SCHEDULE_FILE_NAME).read_text("utf-8")
    )
    assert {item["id"] for item in payload["items"]} == saved_ids
    assert not list((root / "concurrent-user").glob(".*.tmp"))


def test_scan_recovers_running_state_only_after_process_lock_is_released(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr(assistant_schedule, "WORKSPACE_ROOT", tmp_path)
    saved = assistant_schedule.upsert_schedule(
        "recover-user",
        11,
        _schedule_payload("recover-me"),
    )

    def mark_running(item: dict[str, Any]) -> dict[str, Any]:
        item["last_status"] = "running"
        item["next_run_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        item["run_started_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        return item

    assistant_schedule._update_schedule_record(
        "recover-user",
        11,
        saved["id"],
        mark_running,
    )
    run_lock = assistant_schedule._schedule_lock_path("recover-user", saved["id"]).open("a+")
    fcntl.flock(run_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assistant_schedule.due_schedules()
        assert assistant_schedule.list_schedules("recover-user")[0]["last_status"] == "running"
    finally:
        fcntl.flock(run_lock.fileno(), fcntl.LOCK_UN)
        run_lock.close()

    assistant_schedule.due_schedules()
    recovered = assistant_schedule.list_schedules("recover-user")[0]
    assert recovered["last_status"] == "failed"
    assert "自动恢复" in recovered["last_error"]
    assert recovered["run_started_at"] is None


def test_cancelled_schedule_run_clears_running_state(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(assistant_schedule, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(assistant_schedule, "assistant_system_prompt", lambda: "system")
    payload = _schedule_payload("cancel-me")
    payload["favorite_context"] = {
        "folder": "test evidence",
        "items": [
            {
                "id": "cancel-source",
                "title": "Cancellation test source",
                "url": "https://example.org/cancel-source",
                "abstract": "A bounded test excerpt that permits the model call to begin.",
            }
        ],
    }
    saved = assistant_schedule.upsert_schedule(
        "cancel-user",
        13,
        payload,
    )

    async def scenario() -> None:
        entered = asyncio.Event()

        async def blocked_call(**_kwargs: Any) -> str:
            entered.set()
            await asyncio.Event().wait()
            return "never"

        monkeypatch.setattr(assistant_schedule, "call_hermes_once", blocked_call)
        task = asyncio.create_task(
            assistant_schedule.run_schedule(
                "cancel-user",
                13,
                saved["id"],
                _ScheduleDB(),
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    item = assistant_schedule.list_schedules("cancel-user")[0]
    assert item["last_status"] == "failed"
    assert "安全中断" in item["last_error"]
    assert item["run_started_at"] is None


def test_stop_runner_waits_for_and_reaps_active_jobs(monkeypatch):
    monkeypatch.setattr(assistant_schedule, "RUNNER_SHUTDOWN_TIMEOUT_SEC", 1)
    monkeypatch.setattr(assistant_schedule, "_runner_task", None)
    monkeypatch.setattr(assistant_schedule, "_runner_stop", None)
    assistant_schedule._runner_jobs.clear()
    completed: list[bool] = []

    async def scenario() -> None:
        async def finish_during_grace_period() -> None:
            await asyncio.sleep(0.02)
            completed.append(True)

        job = asyncio.create_task(finish_during_grace_period())
        assistant_schedule._runner_jobs.add(job)
        await assistant_schedule.stop_schedule_runner()
        assert job.done()

    asyncio.run(scenario())
    assert completed == [True]
    assert assistant_schedule._runner_jobs == set()


def test_runner_status_reads_fresh_shared_heartbeat(monkeypatch, tmp_path: Path):
    status_path = tmp_path / "runner-status.json"
    status_path.write_text(
        json.dumps(
            {
                "state": "running",
                "pid": 321,
                "instance_id": "321-test",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "last_scan_at": datetime.now(timezone.utc).isoformat(),
                "heartbeat_at": datetime.now(timezone.utc).isoformat(),
                "scan_count": 4,
                "last_due_count": 2,
                "active_runs": 1,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(assistant_schedule, "RUNNER_STATUS_PATH", status_path)
    monkeypatch.setattr(assistant_schedule, "_runner_role", "standby")
    monkeypatch.delenv("ASSISTANT_SCHEDULE_DISABLE", raising=False)

    status = assistant_schedule.get_schedule_runner_status()

    assert status["healthy"] is True
    assert status["state"] == "running"
    assert status["local_role"] == "standby"
    assert status["leader_pid"] == 321
    assert status["scan_count"] == 4


def test_runner_status_rejects_stale_heartbeat(monkeypatch, tmp_path: Path):
    status_path = tmp_path / "runner-status.json"
    status_path.write_text(
        json.dumps(
            {
                "state": "running",
                "pid": 321,
                "heartbeat_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(assistant_schedule, "RUNNER_STATUS_PATH", status_path)
    monkeypatch.setattr(assistant_schedule, "_runner_task", None)
    monkeypatch.delenv("ASSISTANT_SCHEDULE_DISABLE", raising=False)

    status = assistant_schedule.get_schedule_runner_status()

    assert status["healthy"] is False
    assert status["state"] == "unavailable"


def test_readiness_reports_database_and_scheduler(monkeypatch):
    monkeypatch.setattr(
        dashboard,
        "get_schedule_runner_status",
        lambda: {"enabled": True, "healthy": True, "state": "running"},
    )

    response = dashboard.readiness(_FakeDB(True))
    payload = _response_json(response)

    assert response.status_code == 200
    assert payload["status"] == "healthy"
    assert payload["ready"] is True
    assert payload["checks"]["database"]["critical"] is True
    assert payload["checks"]["assistant_scheduler"]["state"] == "running"


def test_readiness_returns_503_when_database_probe_fails(monkeypatch):
    monkeypatch.setattr(
        dashboard,
        "get_schedule_runner_status",
        lambda: {"enabled": True, "healthy": False, "state": "unavailable"},
    )

    response = dashboard.readiness(_FakeDB(error=RuntimeError("secret connection detail")))
    payload = _response_json(response)

    assert response.status_code == 503
    assert payload["status"] == "unhealthy"
    assert payload["ready"] is False
    assert payload["checks"]["database"]["detail"] == "database probe failed"
    assert "secret connection detail" not in response.body.decode("utf-8")


def test_readiness_is_degraded_but_available_when_optional_scheduler_is_down(monkeypatch):
    monkeypatch.setattr(
        dashboard,
        "get_schedule_runner_status",
        lambda: {"enabled": True, "healthy": False, "state": "electing"},
    )

    response = dashboard.readiness(_FakeDB(True))
    payload = _response_json(response)

    assert response.status_code == 200
    assert payload["status"] == "degraded"
    assert payload["ready"] is True
