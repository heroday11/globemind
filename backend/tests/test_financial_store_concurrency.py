"""Concurrency and durability tests for financial JSON stores."""
from __future__ import annotations

import asyncio
import json
import multiprocessing
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

import api.features.financial.json_store as financial_json_store  # noqa: E402
from api.routes import financial  # noqa: E402


def _rule_mutation_worker(
    store: str,
    operation: str,
    rule_id: str,
    start: Any,
    result_queue: Any,
) -> None:
    from api.routes import financial as worker_financial

    worker_financial.ALERT_RULES_STORE = Path(store)
    try:
        if not start.wait(timeout=30):
            raise TimeoutError("mutation start signal was not received")
        if operation == "create":
            worker_financial._create_user_alert_rule(
                {"id": rule_id, "metric": rule_id, "threshold": 10}
            )
        elif operation == "update":
            worker_financial._update_user_alert_rule(rule_id, {"threshold": 99})
        elif operation == "delete":
            worker_financial._delete_user_alert_rule(rule_id)
        else:
            raise ValueError(f"unsupported operation: {operation}")
    except BaseException:
        result_queue.put(traceback.format_exc())
    else:
        result_queue.put(None)


def _rule_reader_worker(store: str, start: Any, result_queue: Any) -> None:
    from api.routes import financial as worker_financial

    worker_financial.ALERT_RULES_STORE = Path(store)
    try:
        if not start.wait(timeout=30):
            raise TimeoutError("reader start signal was not received")
        for _ in range(200):
            rows = worker_financial._read_json_list(worker_financial.ALERT_RULES_STORE)
            if not rows or any(not isinstance(row, dict) or "id" not in row for row in rows):
                raise AssertionError(f"reader observed an incomplete store: {rows!r}")
    except BaseException:
        result_queue.put(traceback.format_exc())
    else:
        result_queue.put(None)


def _history_refresh_worker(store: str, start: Any, result_queue: Any) -> None:
    from api.routes import financial as worker_financial

    worker_financial.ALERT_HISTORY_STORE = Path(store)
    rule = {
        "id": "shared-breach",
        "metric": "Shared breach",
        "current": 12,
        "threshold": 10,
        "severity": "high",
        "breached": True,
        "source": "system",
    }
    try:
        if not start.wait(timeout=30):
            raise TimeoutError("history start signal was not received")
        worker_financial._refresh_alert_history_store(
            [rule, dict(rule)],
            now=datetime(2026, 7, 10, 2, 0, tzinfo=timezone.utc),
        )
    except BaseException:
        result_queue.put(traceback.format_exc())
    else:
        result_queue.put(None)


def _run_processes(processes: list[Any], start: Any, result_queue: Any) -> None:
    for process in processes:
        process.start()
    start.set()

    for process in processes:
        process.join(timeout=60)
        if process.is_alive():
            process.terminate()
            process.join(timeout=10)
            pytest.fail(f"child process did not finish: {process.name}")
        assert process.exitcode == 0

    errors = [result_queue.get(timeout=10) for _ in processes]
    assert errors == [None] * len(processes), "\n".join(error for error in errors if error)


def test_multiprocess_rule_mutations_do_not_lose_updates_or_expose_partial_json(tmp_path: Path) -> None:
    store = tmp_path / "rules.json"
    financial._write_json_list(
        store,
        [
            {"id": "keep", "threshold": 1},
            {"id": "update-0", "threshold": 1},
            {"id": "update-1", "threshold": 1},
            {"id": "delete-0", "threshold": 1},
            {"id": "delete-1", "threshold": 1},
        ],
    )

    context = multiprocessing.get_context("spawn")
    start = context.Event()
    result_queue = context.Queue()
    operations = [
        ("create", "create-0"),
        ("create", "create-1"),
        ("update", "update-0"),
        ("update", "update-1"),
        ("delete", "delete-0"),
        ("delete", "delete-1"),
    ]
    processes = [
        context.Process(
            target=_rule_mutation_worker,
            args=(str(store), operation, rule_id, start, result_queue),
        )
        for operation, rule_id in operations
    ]
    processes.append(
        context.Process(target=_rule_reader_worker, args=(str(store), start, result_queue))
    )

    _run_processes(processes, start, result_queue)

    rows = financial._read_json_list(store)
    by_id = {row["id"]: row for row in rows}
    assert set(by_id) == {"keep", "update-0", "update-1", "create-0", "create-1"}
    assert by_id["update-0"]["threshold"] == 99
    assert by_id["update-1"]["threshold"] == 99
    json.loads(store.read_text(encoding="utf-8"))


def test_multiprocess_history_refresh_is_idempotent(tmp_path: Path) -> None:
    store = tmp_path / "history.json"
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    result_queue = context.Queue()
    processes = [
        context.Process(target=_history_refresh_worker, args=(str(store), start, result_queue))
        for _ in range(4)
    ]

    _run_processes(processes, start, result_queue)

    rows = financial._read_json_list(store)
    assert len(rows) == 1
    assert rows[0]["rule_id"] == "shared-breach"


def test_history_read_does_not_generate_events(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = tmp_path / "history.json"

    async def unexpected_rules_refresh(refresh: bool = False) -> list[dict[str, Any]]:
        raise AssertionError("a history read must not refresh rules")

    monkeypatch.setattr(financial, "ALERT_HISTORY_STORE", store)
    monkeypatch.setattr(financial, "_financial_alert_rules", unexpected_rules_refresh)

    assert asyncio.run(financial._financial_alert_history(limit=50)) == []
    assert not store.exists()
    assert list(tmp_path.iterdir()) == []


def test_history_refresh_endpoint_requires_authentication() -> None:
    app = FastAPI()
    app.include_router(financial.router)

    with TestClient(app) as client:
        response = client.post("/api/financial/alert/history/refresh")

    assert response.status_code == 401


def test_atomic_replace_failure_preserves_original_and_releases_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = tmp_path / "rules.json"
    original_rows = [{"id": "original"}]
    replacement_rows = [{"id": "replacement"}]
    financial._write_json_list(store, original_rows)
    original_bytes = store.read_bytes()
    original_replace = financial_json_store.os.replace

    def fail_replace(source: Any, destination: Any) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr(financial_json_store.os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected replace failure"):
        financial._write_json_list(store, replacement_rows)

    assert store.read_bytes() == original_bytes
    assert list(tmp_path.glob(f".{store.name}.*.tmp")) == []

    monkeypatch.setattr(financial_json_store.os, "replace", original_replace)
    financial._write_json_list(store, replacement_rows)
    assert financial._read_json_list(store) == replacement_rows


def test_mutation_refuses_to_overwrite_corrupt_store_and_recovers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = tmp_path / "rules.json"
    corrupt_bytes = b'{"not": "a list"}'
    store.write_bytes(corrupt_bytes)
    monkeypatch.setattr(financial, "ALERT_RULES_STORE", store)

    with pytest.raises(financial.JsonStoreError):
        financial._create_user_alert_rule({"id": "new", "metric": "new", "threshold": 1})

    assert store.read_bytes() == corrupt_bytes
    assert list(tmp_path.glob(f".{store.name}.*.tmp")) == []

    financial._write_json_list(store, [])
    financial._create_user_alert_rule({"id": "new", "metric": "new", "threshold": 1})
    assert financial._read_json_list(store)[0]["id"] == "new"
