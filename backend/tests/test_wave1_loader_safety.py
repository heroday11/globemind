from __future__ import annotations

import hashlib
import json
import os
import signal
import socket
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
CONTROLLER = PROJECT_ROOT / "deploy" / "wave1_loader_ctl.sh"
DAILY_CONTROLLER = PROJECT_ROOT / "deploy" / "daily_news_ingest_loop.sh"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import filter_discovered_urls_existing_db as url_filter  # noqa: E402
import stream_load_news_to_postgres as loader  # noqa: E402
import wave1_loader_migrate as migration  # noqa: E402

ORIGINAL_SOCKET_CONNECT = socket.socket.connect


@pytest.fixture(autouse=True)
def reset_loader_stop_flag() -> None:
    loader.STOP_REQUESTED = False
    yield
    loader.STOP_REQUESTED = False


def _source_map(path: Path) -> Path:
    path.write_text("site_id,domain\n", encoding="utf-8")
    return path


def _provenance(path: Path, payload: bytes) -> tuple[Path, str]:
    path.write_bytes(payload)
    path.chmod(0o600)
    return path, hashlib.sha256(payload).hexdigest()


def _args(tmp_path: Path, input_path: Path) -> SimpleNamespace:
    control_dir = tmp_path / "loader-control"
    control_dir.mkdir(mode=0o700, exist_ok=True)
    control_dir.chmod(0o700)
    return SimpleNamespace(
        input=input_path,
        source_map=_source_map(tmp_path / "source-map.csv"),
        state_path=tmp_path / "loader-state.json",
        heartbeat_path=tmp_path / "loader-heartbeat.json",
        ready_path=tmp_path / "loader-ready.json",
        control_socket=control_dir / "loader.sock",
        sealed_manifest=tmp_path / "input.sealed.json",
        dead_letter_dir=tmp_path / "dead-letters",
        checkpoint_key="wave1:test:news",
        job_id="wave1-test",
        run_id="wave1-test-run",
        code_version="test-build",
        poll_sec=0.01,
        heartbeat_sec=1.0,
        completion_grace_sec=0.0,
        batch_size=10,
        host="127.0.0.1",
        port=1,
        user="wave1_loader",
        dbname="test",
        sslmode="disable",
        allow_private_scram_transport=True,
        allow_legacy_postgres_role=False,
        connect_timeout_sec=1,
        statement_timeout_ms=1000,
        lock_timeout_ms=1000,
        disable_quality_gate=True,
        min_body_chars=120,
        min_published_year=2000,
        future_grace_days=1,
    )


def _checkpoint(
    args: SimpleNamespace,
    *,
    offset: int = 0,
    counters: loader.Counters | None = None,
) -> loader.Checkpoint:
    fingerprint = migration.fingerprint_input(args.input, offset=offset)
    resolved_code = loader.code_version(args)
    resolved_config = loader.config_sha256(args, resolved_code)
    return loader.Checkpoint(
        checkpoint_key=args.checkpoint_key,
        job_id=args.job_id,
        run_id=args.run_id,
        input_path=fingerprint.canonical_path,
        input_device=fingerprint.device,
        input_inode=fingerprint.inode,
        input_size=fingerprint.size,
        input_offset=offset,
        input_anchor_sha256=fingerprint.anchor_sha256,
        code_version=resolved_code,
        config_sha256=resolved_config,
        counters=counters or loader.Counters(),
        quality_skip_reasons={},
    )


def _write_fake_process(
    proc_root: Path,
    pid: int,
    *,
    start_ticks: int,
    executable: Path,
    cwd: Path,
    boot_id: str = "unit-test-boot-id",
    state: str = "S",
) -> dict[str, Any]:
    process_root = proc_root / str(pid)
    (process_root / "ns").mkdir(parents=True)
    (proc_root / "sys" / "kernel" / "random").mkdir(parents=True, exist_ok=True)
    (proc_root / "sys" / "kernel" / "random" / "boot_id").write_text(
        boot_id + "\n",
        encoding="utf-8",
    )
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.touch(exist_ok=True)
    cwd.mkdir(parents=True, exist_ok=True)
    (process_root / "exe").symlink_to(executable)
    (process_root / "cwd").symlink_to(cwd)
    (process_root / "ns" / "pid").symlink_to("pid:[4026532999]")
    tail = [state, "1", str(pid), str(pid), *("0" for _ in range(15)), str(start_ticks), "0"]
    (process_root / "stat").write_text(
        f"{pid} (python loader) {' '.join(tail)}\n",
        encoding="utf-8",
    )
    return migration.capture_process_identity(pid, proc_root=proc_root)


def test_checkpoint_rejects_truncated_input(tmp_path: Path) -> None:
    input_path = tmp_path / "input.jsonl"
    input_path.write_bytes(b'{"id":1}\n{"id":2}\n')
    args = _args(tmp_path, input_path)
    checkpoint = _checkpoint(args, offset=len(b'{"id":1}\n'))
    input_path.write_bytes(b'{"id":1}\n')

    with pytest.raises(loader.CheckpointError, match="size regressed|truncated"):
        loader.validate_checkpoint_contract(
            checkpoint,
            args,
            checkpoint.code_version,
            checkpoint.config_sha256,
        )


def test_checkpoint_rejects_replaced_inode(tmp_path: Path) -> None:
    input_path = tmp_path / "input.jsonl"
    input_path.write_bytes(b'{"id":1}\n')
    args = _args(tmp_path, input_path)
    checkpoint = _checkpoint(args, offset=input_path.stat().st_size)
    replacement = tmp_path / "replacement.jsonl"
    replacement.write_bytes(input_path.read_bytes())
    os.replace(replacement, input_path)

    with pytest.raises(loader.CheckpointError, match="device/inode"):
        loader.validate_checkpoint_contract(
            checkpoint,
            args,
            checkpoint.code_version,
            checkpoint.config_sha256,
        )


def test_checkpoint_rejects_same_inode_prefix_rewrite(tmp_path: Path) -> None:
    input_path = tmp_path / "input.jsonl"
    input_path.write_bytes(b'{"id":1}\n')
    args = _args(tmp_path, input_path)
    checkpoint = _checkpoint(args, offset=input_path.stat().st_size)
    with input_path.open("r+b") as handle:
        handle.seek(6)
        handle.write(b"2")
        handle.flush()
        os.fsync(handle.fileno())

    with pytest.raises(loader.CheckpointError, match="anchor mismatch"):
        loader.validate_checkpoint_contract(
            checkpoint,
            args,
            checkpoint.code_version,
            checkpoint.config_sha256,
        )


def test_checkpoint_binds_code_and_semantic_configuration(tmp_path: Path) -> None:
    input_path = tmp_path / "input.jsonl"
    input_path.write_bytes(b'{"id":1}\n')
    args = _args(tmp_path, input_path)
    checkpoint = _checkpoint(args)

    with pytest.raises(loader.CheckpointError, match="code_version mismatch"):
        loader.validate_checkpoint_contract(
            checkpoint,
            args,
            "different-code-version",
            checkpoint.config_sha256,
        )

    args.min_body_chars += 1
    changed_config = loader.config_sha256(args, checkpoint.code_version)
    with pytest.raises(loader.CheckpointError, match="config_sha256 mismatch"):
        loader.validate_checkpoint_contract(
            checkpoint,
            args,
            checkpoint.code_version,
            changed_config,
        )


def test_legacy_state_is_never_implicitly_trusted(tmp_path: Path) -> None:
    state_path = tmp_path / "legacy.json"
    state_path.write_text('{"offset": 10}\n', encoding="utf-8")

    with pytest.raises(loader.CheckpointError, match="explicit one-time migration"):
        loader.validate_local_mirror(state_path, None)


def test_seal_requires_stable_exact_fingerprint_and_complete_lines(tmp_path: Path) -> None:
    input_path = tmp_path / "input.jsonl"
    input_path.write_bytes(b'{"id":1}\n{"id":2}\n')
    fingerprint = migration.fingerprint_input(input_path, require_stable_size=True)
    output = tmp_path / "input.sealed.json"
    args = SimpleNamespace(
        input=input_path,
        output=output,
        job_id="job",
        run_id="run",
        expected_input_path=fingerprint.canonical_path,
        expected_input_device=fingerprint.device,
        expected_input_inode=fingerprint.inode,
        expected_final_bytes=fingerprint.size,
        expected_rows=fingerprint.rows,
        expected_sha256=fingerprint.anchor_sha256,
    )

    migration.seal_input(args)

    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["sealed"] is True
    assert manifest["input"]["final_bytes"] == input_path.stat().st_size
    assert manifest["input"]["rows"] == 2
    assert stat.S_IMODE(output.stat().st_mode) == 0o600

    input_path.write_bytes(b'{"id":1}')
    with pytest.raises(migration.SafetyError, match="record boundary"):
        migration.fingerprint_input(input_path, require_stable_size=True)


def test_daily_pipeline_seals_after_extractor_and_uses_independent_checkpoint() -> None:
    source = DAILY_CONTROLLER.read_text(encoding="utf-8")
    extractor = source.index('"$PY" -u "${extract_args[@]}"')
    seal = source.index("scripts/wave1_loader_migrate.py seal-input")
    loader_call = source.index("scripts/stream_load_news_to_postgres.py", seal)

    assert extractor < seal < loader_call
    assert '--checkpoint-key "daily-news:$run_id:news"' in source
    assert '--sealed-manifest "$load_seal"' in source
    assert '--control-socket "$load_control_socket"' in source
    assert "--exit-on-eof" not in source
    assert "GLOBEMIND_LOADER_INSTANCE_ID=" in source


def test_static_producer_can_atomically_seal_without_loader_access(tmp_path: Path) -> None:
    input_path = tmp_path / "articles.jsonl"
    input_path.write_bytes(b'{"id":1}\n')
    output = tmp_path / "articles.sealed.json"

    result = migration.main(
        [
            "seal-input",
            "--input",
            str(input_path),
            "--output",
            str(output),
            "--job-id",
            "daily-news-ingest",
            "--run-id",
            "daily-test",
        ]
    )

    assert result == 0
    assert json.loads(output.read_text())["input"]["sha256"] == hashlib.sha256(
        input_path.read_bytes()
    ).hexdigest()
    assert list(tmp_path.glob(".articles.sealed.json.*.tmp")) == []

    input_path.write_bytes(b'{"id":2}\n')
    refused = migration.main(
        [
            "seal-input",
            "--input",
            str(input_path),
            "--output",
            str(output),
            "--job-id",
            "daily-news-ingest",
            "--run-id",
            "daily-test",
        ]
    )
    assert refused == 3


def test_completion_requires_exact_sealed_offset_rows_and_digest(tmp_path: Path) -> None:
    input_path = tmp_path / "input.jsonl"
    content = b'{"id":1}\n{"id":2}\n'
    input_path.write_bytes(content)
    args = _args(tmp_path, input_path)
    full = _checkpoint(args, offset=len(content), counters=loader.Counters(seen=2, invalid=2))
    migration.atomic_write_json(
        args.sealed_manifest,
        {
            "schema_version": 1,
            "sealed": True,
            "job_id": args.job_id,
            "run_id": args.run_id,
            "input": {
                "canonical_path": full.input_path,
                "device": full.input_device,
                "inode": full.input_inode,
                "final_bytes": len(content),
                "rows": 2,
                "sha256": hashlib.sha256(content).hexdigest(),
            },
        },
    )
    manifest = loader.load_seal(args.sealed_manifest, full)
    assert manifest is not None

    candidate = loader.replace(
        full,
        completed=True,
        sealed_final_bytes=len(content),
        sealed_rows=2,
        sealed_sha256=hashlib.sha256(content).hexdigest(),
    )
    candidate.validate()

    behind = _checkpoint(args, offset=len(b'{"id":1}\n'), counters=loader.Counters(seen=1, invalid=1))
    with pytest.raises(loader.CheckpointError, match="exact sealed final offset"):
        loader.mark_sealed_complete(object(), object(), behind, manifest)

    wrong_digest = {"input": {**manifest["input"], "sha256": "0" * 64}}
    with pytest.raises(loader.CheckpointError, match="sealed content"):
        loader.mark_sealed_complete(object(), object(), full, wrong_digest)


def test_missing_declared_seal_means_the_producer_is_still_active(tmp_path: Path) -> None:
    input_path = tmp_path / "input.jsonl"
    input_path.write_bytes(b'{"id":1}\n')
    args = _args(tmp_path, input_path)
    checkpoint = _checkpoint(args)

    assert not args.sealed_manifest.exists()
    assert loader.load_seal(args.sealed_manifest, checkpoint) is None

    args.sealed_manifest.symlink_to(tmp_path / "not-created.json")
    with pytest.raises(loader.CheckpointError, match="manifest file is invalid"):
        loader.load_seal(args.sealed_manifest, checkpoint)


class _TransactionConnection:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.cursor_value = SimpleNamespace()

    def __enter__(self) -> _TransactionConnection:
        self.events.append("transaction-enter")
        return self

    def __exit__(self, exception_type: Any, *_args: Any) -> None:
        self.events.append("commit" if exception_type is None else "rollback")

    def cursor(self) -> _TransactionConnection:
        return self


class _MemoryStore:
    def __init__(self, checkpoint: loader.Checkpoint) -> None:
        self.authoritative = checkpoint

    def fetch(self, _cur: Any, *, for_update: bool = False) -> loader.Checkpoint:
        assert for_update is True
        return self.authoritative

    def update(
        self,
        _cur: Any,
        _expected: loader.Checkpoint,
        candidate: loader.Checkpoint,
    ) -> loader.Checkpoint:
        self.authoritative = loader.replace(
            candidate,
            last_progress_at="database-commit",
            updated_at="database-commit",
        )
        return self.authoritative


def test_database_checkpoint_survives_commit_to_json_crash_gap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = tmp_path / "input.jsonl"
    raw = b'{"id":1}\n'
    input_path.write_bytes(raw)
    args = _args(tmp_path, input_path)
    checkpoint = _checkpoint(args)
    migration.atomic_write_json(args.state_path, checkpoint.mirror())
    normalized = {
        "title": "title",
        "body": "body",
        "url": "https://example.test/1",
        "url_hash": "hash",
        "media_source_domain": "example.test",
        "region": "test",
    }
    prepared = [
        loader.PreparedRecord(
            loader.RawRecord(0, len(raw), raw, {"id": 1}, None),
            "database",
            normalized=normalized,
        )
    ]
    events: list[str] = []
    connection = _TransactionConnection(events)
    store = _MemoryStore(checkpoint)
    monkeypatch.setattr(loader, "ensure_media_source", lambda *_args: 7)
    monkeypatch.setattr(loader, "insert_news_row", lambda *_args: True)

    committed, _hasher = loader.advance_batch(
        connection,
        store,
        checkpoint,
        prepared,
        hashlib.sha256(),
        args,
    )
    assert events[-1] == "commit"
    assert committed.counters == loader.Counters(seen=1, inserted=1)

    # Simulate a crash before the local mirror write. A lagging mirror is accepted,
    # and the authoritative database row can replace it on restart.
    old_mirror = json.loads(args.state_path.read_text(encoding="utf-8"))
    assert old_mirror["input"]["offset"] == 0
    loader.validate_local_mirror(args.state_path, committed)
    migration.atomic_write_json(args.state_path, committed.mirror())
    assert json.loads(args.state_path.read_text(encoding="utf-8"))["input"]["offset"] == len(raw)


class _MissingTableCursor:
    def __init__(self) -> None:
        self.query = ""

    def __enter__(self) -> _MissingTableCursor:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def execute(self, query: str, _params: Any) -> None:
        self.query = query

    def fetchone(self) -> tuple[bool]:
        return (False,)


class _MissingTableConnection:
    def __init__(self) -> None:
        self.cursor_value = _MissingTableCursor()

    def __enter__(self) -> _MissingTableConnection:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def cursor(self) -> _MissingTableCursor:
        return self.cursor_value


class _FakePostgresCursor:
    def __init__(self, connection: _FakePostgresConnection) -> None:
        self.connection = connection
        self.result: Any = None

    def __enter__(self) -> _FakePostgresCursor:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def execute(self, query: str, params: Any = None) -> None:
        normalized = " ".join(query.lower().split())
        if "set_config(" in normalized:
            self.result = (str(params[0]),)
        elif "to_regclass" in normalized:
            self.result = (True,)
        elif "pg_try_advisory_lock" in normalized:
            self.result = (True,)
        elif normalized.startswith("select checkpoint_key"):
            self.result = self.connection.row_tuple()
        elif normalized.startswith(f"insert into {migration.CHECKPOINT_TABLE}"):
            self.connection.row = {
                "checkpoint_key": params[0],
                "schema_version": params[1],
                "job_id": params[2],
                "run_id": params[3],
                "input_path": params[4],
                "input_device": params[5],
                "input_inode": params[6],
                "input_size": params[7],
                "input_offset": params[8],
                "input_anchor_sha256": params[9],
                "code_version": params[10],
                "config_sha256": params[11],
                "seen": params[12],
                "legacy_seen": None,
                "inserted": params[13],
                "duplicate": params[14],
                "invalid": params[15],
                "quality_rejected": params[16],
                "quality_skip_reasons": json.loads(params[17]),
                "completed": False,
                "sealed_final_bytes": None,
                "sealed_rows": None,
                "sealed_sha256": None,
                "last_progress_at": "initialized",
                "updated_at": "initialized",
            }
            self.result = None
        elif normalized.startswith(f"update {migration.CHECKPOINT_TABLE}"):
            assert self.connection.row is not None
            assert self.connection.row["checkpoint_key"] == params[13]
            assert self.connection.row["input_offset"] == params[14]
            assert self.connection.row["input_anchor_sha256"] == params[15]
            self.connection.row.update(
                {
                    "input_size": params[0],
                    "input_offset": params[1],
                    "input_anchor_sha256": params[2],
                    "seen": params[3],
                    "inserted": params[4],
                    "duplicate": params[5],
                    "invalid": params[6],
                    "quality_rejected": params[7],
                    "quality_skip_reasons": json.loads(params[8]),
                    "completed": params[9],
                    "sealed_final_bytes": params[10],
                    "sealed_rows": params[11],
                    "sealed_sha256": params[12],
                    "last_progress_at": "committed",
                    "updated_at": "committed",
                }
            )
            self.result = self.connection.row_tuple()
        else:
            raise AssertionError(f"unexpected SQL in offline fake: {normalized[:120]}")

    def fetchone(self) -> Any:
        return self.result


class _FakePostgresConnection:
    def __init__(self) -> None:
        self.row: dict[str, Any] | None = None
        self.closed = False
        self.commits = 0
        self.rollbacks = 0

    def __enter__(self) -> _FakePostgresConnection:
        return self

    def __exit__(self, exception_type: Any, *_args: Any) -> None:
        if exception_type is None:
            self.commits += 1
        else:
            self.rollbacks += 1

    def cursor(self) -> _FakePostgresCursor:
        return _FakePostgresCursor(self)

    def row_tuple(self) -> tuple[Any, ...] | None:
        if self.row is None:
            return None
        return tuple(self.row[column] for column in loader.CHECKPOINT_COLUMNS)

    def close(self) -> None:
        self.closed = True


def test_missing_authoritative_table_fails_closed_before_ready(tmp_path: Path) -> None:
    input_path = tmp_path / "input.jsonl"
    input_path.write_bytes(b"")
    args = _args(tmp_path, input_path)
    store = loader.CheckpointStore(_MissingTableConnection(), args.checkpoint_key)

    with pytest.raises(loader.CheckpointError, match="table is missing"):
        store.configure_and_preflight(args)
    assert not args.ready_path.exists()


def test_full_offline_flow_initializes_db_checkpoint_then_completes_exact_seal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = tmp_path / "input.jsonl"
    input_path.write_bytes(b"not-json\n")
    args = _args(tmp_path, input_path)
    fingerprint = migration.fingerprint_input(input_path, require_stable_size=True)
    migration.seal_input(
        SimpleNamespace(
            input=input_path,
            output=args.sealed_manifest,
            job_id=args.job_id,
            run_id=args.run_id,
            expected_input_path=fingerprint.canonical_path,
            expected_input_device=fingerprint.device,
            expected_input_inode=fingerprint.inode,
            expected_final_bytes=fingerprint.size,
            expected_rows=fingerprint.rows,
            expected_sha256=fingerprint.anchor_sha256,
        )
    )
    secret = tmp_path / "db-secret"
    secret.write_text("unit-test-password\n", encoding="utf-8")
    secret.chmod(0o600)
    monkeypatch.setenv("GLOBEMIND_DB_PASSWORD_FILE", str(secret))
    monkeypatch.setenv("GLOBEMIND_LOADER_INSTANCE_ID", "offline-test-instance")
    monkeypatch.setattr(loader, "load_source_map", lambda _path: {})
    connection = _FakePostgresConnection()

    def connect(**kwargs: Any) -> _FakePostgresConnection:
        assert kwargs["connect_timeout"] == args.connect_timeout_sec
        assert kwargs["application_name"] == "globemind-wave1-loader-v2"
        assert kwargs["sslmode"] == "disable"
        return connection

    monkeypatch.setattr(loader.psycopg2, "connect", connect)

    loader.run(args)

    assert connection.closed is True
    assert connection.rollbacks == 0
    assert connection.row is not None
    assert connection.row["completed"] is True
    assert connection.row["input_offset"] == fingerprint.size
    assert connection.row["seen"] == 1
    assert connection.row["invalid"] == 1
    mirror = json.loads(args.state_path.read_text())
    assert mirror["completed"] is True
    assert mirror["input"]["anchor_sha256"] == fingerprint.anchor_sha256
    assert json.loads(args.ready_path.read_text())["status"] == "stopped"


def test_bad_json_and_non_object_are_deterministic_dead_letters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = tmp_path / "input.jsonl"
    content = b'{"id":1}\nnot-json\n[1,2]\n\xff\n'
    input_path.write_bytes(content)
    args = _args(tmp_path, input_path)
    checkpoint = _checkpoint(args)
    records = loader.read_records(input_path, 0, 10, checkpoint)
    prepared = loader.prepare_records(records, {}, args)
    prepared[0] = loader.PreparedRecord(
        prepared[0].raw,
        "database",
        normalized={
            "title": "title",
            "url_hash": "hash",
            "media_source_domain": "example.test",
        },
    )
    connection = _TransactionConnection([])
    store = _MemoryStore(checkpoint)
    monkeypatch.setattr(loader, "ensure_media_source", lambda *_args: 7)
    monkeypatch.setattr(loader, "insert_news_row", lambda *_args: False)

    committed, _ = loader.advance_batch(
        connection,
        store,
        checkpoint,
        prepared,
        hashlib.sha256(),
        args,
    )

    assert committed.counters == loader.Counters(seen=4, duplicate=1, invalid=3)
    assert committed.counters.seen == sum(
        (
            committed.counters.inserted,
            committed.counters.duplicate,
            committed.counters.invalid,
            committed.counters.quality_rejected,
        )
    )
    dead_letters = list(args.dead_letter_dir.glob("*.json"))
    assert len(dead_letters) == 3
    assert {json.loads(path.read_text())["reason"] for path in dead_letters} == {
        "invalid_json",
        "non_object_json",
        "invalid_utf8",
    }


def test_idle_heartbeat_does_not_rewrite_checkpoint(tmp_path: Path) -> None:
    input_path = tmp_path / "input.jsonl"
    input_path.write_bytes(b"")
    args = _args(tmp_path, input_path)
    checkpoint = _checkpoint(args)
    migration.atomic_write_json(args.state_path, checkpoint.mirror())
    before = args.state_path.read_bytes()
    before_mtime = args.state_path.stat().st_mtime_ns

    loader.write_heartbeat(args.heartbeat_path, checkpoint, "idle")

    assert args.state_path.read_bytes() == before
    assert args.state_path.stat().st_mtime_ns == before_mtime
    assert json.loads(args.heartbeat_path.read_text())["status"] == "idle"


def test_sigterm_stops_preparation_at_record_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = tmp_path / "input.jsonl"
    input_path.write_bytes(b'{"id":1}\n{"id":2}\n')
    args = _args(tmp_path, input_path)
    checkpoint = _checkpoint(args)
    records = loader.read_records(input_path, 0, 10, checkpoint)
    calls = 0

    def classify(raw: dict[str, Any], _source: Any, **_settings: Any) -> Any:
        nonlocal calls
        calls += 1
        loader._request_stop(signal.SIGTERM, None)
        return migration.NewsRecordClassification(
            "database",
            normalized={
                "title": "title",
                "url": "https://example.test",
                "url_hash": str(raw["id"]),
                "media_source_domain": "example.test",
            },
        )

    monkeypatch.setattr(loader, "classify_news_value", classify)

    prepared = loader.prepare_records(records, {}, args)

    assert calls == 1
    assert len(prepared) == 1
    assert prepared[0].raw.end_offset == len(b'{"id":1}\n')
    loader.STOP_REQUESTED = False


def test_sigterm_during_database_batch_commits_only_current_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = tmp_path / "input.jsonl"
    first = b'{"id":1}\n'
    second = b'{"id":2}\n'
    input_path.write_bytes(first + second)
    args = _args(tmp_path, input_path)
    checkpoint = _checkpoint(args)
    normalized = {
        "title": "title",
        "url_hash": "hash",
        "media_source_domain": "example.test",
    }
    prepared = [
        loader.PreparedRecord(loader.RawRecord(0, len(first), first, {"id": 1}, None), "database", normalized),
        loader.PreparedRecord(
            loader.RawRecord(len(first), len(first + second), second, {"id": 2}, None),
            "database",
            normalized,
        ),
    ]
    store = _MemoryStore(checkpoint)
    monkeypatch.setattr(loader, "ensure_media_source", lambda *_args: 7)

    def insert_and_stop(*_args: Any) -> bool:
        loader._request_stop(signal.SIGTERM, None)
        return True

    monkeypatch.setattr(loader, "insert_news_row", insert_and_stop)

    committed, _ = loader.advance_batch(
        _TransactionConnection([]),
        store,
        checkpoint,
        prepared,
        hashlib.sha256(),
        args,
    )

    assert committed.input_offset == len(first)
    assert committed.counters == loader.Counters(seen=1, inserted=1)
    loader.STOP_REQUESTED = False


def test_pidfd_signal_revalidates_full_identity_and_targets_single_pid(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    identity = _write_fake_process(
        proc_root,
        5151,
        start_ticks=999,
        executable=tmp_path / "bin" / "python",
        cwd=tmp_path / "project",
    )
    meta = tmp_path / "loader.meta"
    migration.atomic_write_json(
        meta,
        {
            "schema_version": 2,
            "instance_name": "wave1_loader",
            "instance_id": "instance",
            "identity": identity,
        },
    )
    read_fd, write_fd = os.pipe()
    sent: list[tuple[int, int]] = []
    try:
        migration.pidfd_send(
            meta,
            signal.SIGTERM,
            proc_root=proc_root,
            pidfd_open=lambda pid, flags: (sent.append((pid, flags)), os.dup(read_fd))[1],
            pidfd_send_signal=lambda fd, sig, _info, _flags: sent.append((fd, sig)),
        )
    finally:
        os.close(read_fd)
        os.close(write_fd)

    assert sent[0] == (5151, 0)
    assert sent[1][1] == signal.SIGTERM
    controller_source = CONTROLLER.read_text(encoding="utf-8")
    helper_source = Path(migration.__file__).read_text(encoding="utf-8")
    assert "pidfd-signal" in helper_source
    assert "socket-control" in controller_source
    assert "pidfd-signal" not in controller_source
    assert "kill " not in controller_source.lower()


def test_runtime_ready_identity_covers_boot_namespace_exe_cwd_sid_and_pgid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket.socket, "connect", ORIGINAL_SOCKET_CONNECT)
    control_dir = tmp_path / "control"
    control_dir.mkdir(mode=0o700)
    server = loader.ControlServer(control_dir / "loader.sock", "instance")
    socket_identity = server.start()
    identity = migration.capture_process_identity(os.getpid())
    meta = tmp_path / "loader.meta"
    ready = tmp_path / "loader.ready"
    migration.atomic_write_json(
        meta,
        {
            "schema_version": 2,
            "instance_name": "wave1_loader",
            "instance_id": "instance",
            "identity": identity,
            "control_socket": socket_identity,
        },
    )
    migration.atomic_write_json(
        ready,
        {
            "schema_version": 2,
            "status": "ready",
            "instance_id": "instance",
            "identity": identity,
            "control_socket": socket_identity,
        },
    )
    try:
        verified = migration.verify_runtime_identity(meta, ready)
        status = migration.socket_control(meta, control_dir / "loader.sock", "status")
        stopped = migration.socket_control(meta, control_dir / "loader.sock", "stop")
    finally:
        server.close()
        loader.STOP_REQUESTED = False

    assert verified["ready_status"] == "ready"
    assert status["status"] == "running"
    assert stopped["status"] == "stopping"
    assert set(("boot_id", "pid_namespace", "exe", "cwd", "sid", "pgid")) <= identity.keys()


def test_pidfd_refuses_reused_pid_after_open(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    identity = _write_fake_process(
        proc_root,
        5252,
        start_ticks=111,
        executable=tmp_path / "bin" / "python",
        cwd=tmp_path / "project",
    )
    meta = tmp_path / "loader.meta"
    migration.atomic_write_json(meta, {"identity": identity})
    stat_path = proc_root / "5252" / "stat"
    stat_path.write_text(stat_path.read_text().replace(" 111 0", " 222 0"), encoding="utf-8")
    read_fd, write_fd = os.pipe()
    sent: list[int] = []
    try:
        with pytest.raises(migration.SafetyError, match="identity changed"):
            migration.pidfd_send(
                meta,
                signal.SIGTERM,
                proc_root=proc_root,
                pidfd_open=lambda _pid, _flags: os.dup(read_fd),
                pidfd_send_signal=lambda *_args: sent.append(1),
            )
    finally:
        os.close(read_fd)
        os.close(write_fd)
    assert sent == []


def test_reused_pid_proves_old_runtime_identity_dead_without_a_signal(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    identity = _write_fake_process(
        proc_root,
        5292,
        start_ticks=111,
        executable=tmp_path / "bin" / "python",
        cwd=tmp_path / "project",
    )
    meta = tmp_path / "loader.meta"
    migration.atomic_write_json(
        meta,
        {
            "schema_version": 2,
            "instance_name": "wave1_loader",
            "instance_id": "old-instance",
            "identity": identity,
        },
    )
    stat_path = proc_root / "5292" / "stat"
    stat_path.write_text(stat_path.read_text().replace(" 111 0", " 222 0"), encoding="utf-8")

    assert migration.runtime_identity_is_dead(meta, proc_root=proc_root) is True


def test_explicit_legacy_migration_checks_process_input_and_emits_only_sql(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    identity = _write_fake_process(
        proc_root,
        5353,
        start_ticks=333,
        executable=tmp_path / "bin" / "python",
        cwd=tmp_path / "project",
        state="T",
    )
    input_path = tmp_path / "input.jsonl"
    input_path.write_bytes(
        b'{"title":"one","url":"https://example.test/1","domain":"example.test"}\n'
        b'{"title":"two","url":"https://example.test/2","domain":"example.test"}\n'
    )
    fingerprint = migration.fingerprint_input(input_path, require_stable_size=True)
    legacy = tmp_path / "legacy-state.json"
    legacy.write_text(
        json.dumps(
            {
                "offset": fingerprint.offset,
                "seen": 2,
                "inserted": 1,
                "skipped": 1,
                "input": str(input_path),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    state_sha = hashlib.sha256(legacy.read_bytes()).hexdigest()
    audit_output = tmp_path / "legacy-audit.json"
    output_state = tmp_path / "state-v2.json"
    output_sql = tmp_path / "seed.sql"
    args = SimpleNamespace(
        checkpoint_key="wave1:test:news",
        job_id="wave1-test",
        run_id="wave1-run",
        code_version="test-code",
        source_map=_source_map(tmp_path / "legacy-source-map.csv"),
        disable_quality_gate=True,
        min_body_chars=120,
        min_published_year=2000,
        future_grace_days=1,
        expected_pid=5353,
        proc_root=proc_root,
        expected_start_ticks=identity["start_ticks"],
        expected_boot_id=identity["boot_id"],
        require_stopped=True,
        legacy_state=legacy,
        expected_state_sha256=state_sha,
        input=input_path,
        output=audit_output,
    )

    migration.audit_legacy(args)
    audit = json.loads(audit_output.read_text())
    assert audit["classification"]["counters"] == {
        "seen": 2,
        "inserted": 1,
        "duplicate": 1,
        "invalid": 0,
        "quality_rejected": 0,
    }
    assert stat.S_IMODE(audit_output.stat().st_mode) == 0o600

    with input_path.open("ab") as handle:
        handle.write(b'{"title":"producer-continues"}\n')
    args = SimpleNamespace(
        **vars(args),
        audit=audit_output,
        expected_audit_sha256=hashlib.sha256(audit_output.read_bytes()).hexdigest(),
        expected_input_path=fingerprint.canonical_path,
        expected_input_device=fingerprint.device,
        expected_input_inode=fingerprint.inode,
        expected_input_size=fingerprint.size,
        expected_input_anchor_sha256=fingerprint.anchor_sha256,
        output_state=output_state,
        output_sql=output_sql,
    )

    migration.migrate_legacy(args)
    migration.migrate_legacy(args)

    state = json.loads(output_state.read_text())
    sql = output_sql.read_text()
    assert state["schema_version"] == 2
    assert state["code_version"].startswith("test-code:")
    assert len(state["config_sha256"]) == 64
    assert state["input"]["anchor_sha256"] == fingerprint.anchor_sha256
    assert state["input"]["size"] > fingerprint.size
    assert state["legacy_seen"] == 2
    assert state["migrated_from"]["legacy_seen"] == 2
    assert state["migrated_from"]["audit_id"] == audit["audit_id"]
    assert state["legacy_runtime_control"]["automatic_stop_supported"] is False
    assert "manual_maintenance_window" in state["legacy_runtime_control"]["required_action"]
    assert f"INSERT INTO {migration.CHECKPOINT_TABLE}" in sql
    assert "ON CONFLICT (checkpoint_key) DO NOTHING" in sql
    assert "RAISE EXCEPTION" in sql
    assert stat.S_IMODE(output_state.stat().st_mode) == 0o600


def test_legacy_audit_streams_and_classifies_the_frozen_prefix(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    identity = _write_fake_process(
        proc_root,
        5454,
        start_ticks=444,
        executable=tmp_path / "bin" / "python",
        cwd=tmp_path / "project",
        state="T",
    )
    long_body = "body long enough for the configured quality gate"
    rows = [
        {
            "title": "one",
            "body": long_body,
            "url": "https://example.test/1",
            "domain": "example.test",
            "published_at": "2024-01-01T00:00:00Z",
        },
        {
            "title": "two",
            "body": long_body,
            "url": "https://example.test/2",
            "domain": "example.test",
            "published_at": "2024-01-01T00:00:00Z",
        },
        {
            "body": long_body,
            "url": "https://example.test/invalid",
            "domain": "example.test",
            "published_at": "2024-01-01T00:00:00Z",
        },
        {
            "title": "short",
            "body": "x",
            "url": "https://example.test/short",
            "domain": "example.test",
            "published_at": "2024-01-01T00:00:00Z",
        },
    ]
    input_path = tmp_path / "input.jsonl"
    content = b"".join(
        json.dumps(row, separators=(",", ":")).encode() + b"\n" for row in rows[:2]
    )
    content += b"not-json\n"
    content += b"".join(
        json.dumps(row, separators=(",", ":")).encode() + b"\n" for row in rows[2:]
    )
    input_path.write_bytes(content)
    legacy = tmp_path / "legacy.json"
    legacy.write_text(
        json.dumps(
                {
                    "offset": len(content),
                    "seen": 4,
                    "inserted": 1,
                    "skipped": 4,
                    "quality_skipped": 1,
                    "quality_skip_reasons": {"body_too_short": 1},
                    "input": str(input_path),
                }
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "audit.json"
    args = SimpleNamespace(
        checkpoint_key="wave1:audit:news",
        job_id="wave1-audit",
        run_id="wave1-audit-run",
        code_version="test-code",
        source_map=_source_map(tmp_path / "audit-source-map.csv"),
        disable_quality_gate=False,
        min_body_chars=20,
        min_published_year=2000,
        future_grace_days=1,
        expected_pid=5454,
        proc_root=proc_root,
        expected_start_ticks=identity["start_ticks"],
        expected_boot_id=identity["boot_id"],
        require_stopped=True,
        legacy_state=legacy,
        expected_state_sha256=hashlib.sha256(legacy.read_bytes()).hexdigest(),
        input=input_path,
        output=output,
    )

    migration.audit_legacy(args)

    report = json.loads(output.read_text())
    assert report["legacy_state"]["seen"] == 4
    assert report["classification"]["malformed"] == 1
    assert report["classification"]["invalid"] == 1
    assert report["classification"]["quality_rejected"] == 1
    assert report["classification"]["database_candidates"] == 2
    assert report["classification"]["counters"] == {
        "seen": 5,
        "inserted": 1,
        "duplicate": 1,
        "invalid": 2,
        "quality_rejected": 1,
    }
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def _legacy_transition_case(
    tmp_path: Path,
    *,
    eligible_suffix: bool = False,
) -> tuple[SimpleNamespace, migration.InputFingerprint]:
    proc_root = tmp_path / "proc"
    identity = _write_fake_process(
        proc_root,
        5656,
        start_ticks=666,
        executable=tmp_path / "bin" / "python",
        cwd=tmp_path / "project",
        state="T",
    )
    long_body = "A sufficiently long article body for deterministic quality checks. " * 2

    def encoded(
        title: str,
        body: str,
        sequence: int,
        *,
        published_at: str = "2024-01-01T00:00:00Z",
        fetched_at: str | None = None,
    ) -> bytes:
        payload = {
            "title": title,
            "body": body,
            "url": f"https://example.test/{sequence}",
            "domain": "example.test",
            "published_at": published_at,
        }
        if fetched_at is not None:
            payload["fetched_at"] = fetched_at
        return json.dumps(payload, separators=(",", ":")).encode() + b"\n"

    prefix = encoded("pre-one", long_body, 1) + b"  \n" + encoded("pre-two", long_body, 2)
    window_rows = [
        encoded("rejected-before-restart", "x", 3),
        encoded("invariant-one", long_body, 4),
        encoded("invariant-two", long_body, 5),
        encoded("rejected-after-restart", "x", 6),
        encoded("database-duplicate", long_body, 7),
    ]
    window = b"".join(window_rows)
    suffix_database = (
        encoded(
            "post-database",
            long_body,
            8,
            published_at="2026-01-02T12:00:00Z",
            fetched_at="2026-01-01T00:00:00Z",
        )
        if eligible_suffix
        else encoded("post-database", long_body, 8)
    )
    suffix = suffix_database + encoded("post-rejected", "x", 9)
    input_path = tmp_path / "transition.jsonl"
    input_path.write_bytes(prefix + window + suffix)
    fingerprint = migration.fingerprint_input(input_path, require_stable_size=True)
    legacy = tmp_path / "legacy-transition.json"
    legacy.write_text(
        json.dumps(
            {
                "offset": fingerprint.offset,
                "seen": 9,
                "inserted": 6,
                "skipped": 3,
                "quality_skipped": 2,
                "quality_skip_reasons": {"body_too_short": 2},
                "input": str(input_path),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    provenance_file, provenance_sha256 = _provenance(
        tmp_path / "transition-provenance.json",
        b'{"source":"unit-test-transition"}\n',
    )
    return (
        SimpleNamespace(
            checkpoint_key="wave1:transition:news",
            job_id="wave1-transition",
            run_id="wave1-transition-run",
            code_version="test-code",
            source_map=_source_map(tmp_path / "transition-source-map.csv"),
            disable_quality_gate=False,
            min_body_chars=20,
            min_published_year=2000,
            future_grace_days=1,
            expected_pid=5656,
            proc_root=proc_root,
            expected_start_ticks=identity["start_ticks"],
            expected_boot_id=identity["boot_id"],
            require_stopped=True,
            legacy_state=legacy,
            expected_state_sha256=hashlib.sha256(legacy.read_bytes()).hexdigest(),
            input=input_path,
            output=tmp_path / "transition-audit.json",
            legacy_transition_pre_offset=len(prefix),
            legacy_transition_pre_seen=2,
            legacy_transition_pre_inserted=2,
            legacy_transition_pre_skipped=0,
            legacy_transition_post_offset=len(prefix) + len(window),
            legacy_transition_post_seen=7,
            legacy_transition_post_inserted=5,
            legacy_transition_post_skipped=2,
            legacy_transition_post_quality_skipped=1,
            legacy_transition_post_quality_reasons={"body_too_short": 1},
            legacy_transition_provenance_file=provenance_file,
            legacy_transition_provenance_sha256=provenance_sha256,
        ),
        fingerprint,
    )


def test_legacy_transition_audit_proves_an_equivalent_candidate_range_and_migrates(
    tmp_path: Path,
) -> None:
    args, fingerprint = _legacy_transition_case(tmp_path)

    migration.audit_legacy(args)

    audit = json.loads(args.output.read_text())
    transition = audit["legacy_quality_gate_transition"]
    assert audit["schema_version"] == migration.LEGACY_AUDIT_SCHEMA_VERSION
    assert transition["candidate_boundaries"]["count"] == 3
    assert transition["candidate_boundaries"]["all_candidates_semantically_equivalent"] is True
    assert transition["invariant_gap"]["complete_lines"] == 2
    assert transition["invariant_gap"]["quality_sensitive_records"] == 0
    assert transition["post_checkpoint"]["quality_skipped"] == 1
    assert transition["provenance"] == {
        "canonical_path": str(args.legacy_transition_provenance_file.resolve()),
        "sha256": args.legacy_transition_provenance_sha256,
    }
    assert len(transition["evidence_sha256"]) == 64
    assert audit["classification"]["blank_lines"] == 1
    assert audit["classification"]["counters"] == {
        "seen": 10,
        "inserted": 6,
        "duplicate": 1,
        "invalid": 1,
        "quality_rejected": 2,
    }
    assert [
        cohort["legacy_accounting"]["inferred_duplicate"]
        for cohort in audit["classification"]["cohorts"]
    ] == [0, 1, 0]

    migrate_args = SimpleNamespace(
        **vars(args),
        audit=args.output,
        expected_audit_sha256=hashlib.sha256(args.output.read_bytes()).hexdigest(),
        expected_input_path=fingerprint.canonical_path,
        expected_input_device=fingerprint.device,
        expected_input_inode=fingerprint.inode,
        expected_input_size=fingerprint.size,
        expected_input_anchor_sha256=fingerprint.anchor_sha256,
        output_state=tmp_path / "transition-state-v2.json",
        output_sql=tmp_path / "transition-seed.sql",
    )
    migration.migrate_legacy(migrate_args)
    state = json.loads(migrate_args.output_state.read_text())
    assert state["counters"] == audit["classification"]["counters"]
    assert state["legacy_seen"] == 9
    assert state["quality_skip_reasons"] == {"body_too_short": 2}
    assert state["migrated_from"]["legacy_quality_gate_transition"]["count"] == 3
    resolved_code, resolved_config = migration._resolve_contract(migrate_args)
    assert state["migration_id"] == hashlib.sha256(
        (
            migrate_args.expected_state_sha256
            + fingerprint.anchor_sha256
            + resolved_config
            + resolved_code
            + migrate_args.expected_audit_sha256
            + audit["audit_id"]
        ).encode()
    ).hexdigest()


def test_legacy_transition_rejects_non_record_aligned_evidence(tmp_path: Path) -> None:
    args, _fingerprint = _legacy_transition_case(tmp_path)
    args.legacy_transition_pre_offset += 1

    with pytest.raises(migration.SafetyError, match="complete record boundary"):
        migration.audit_legacy(args)


def test_legacy_transition_rejects_quality_evidence_without_a_matching_boundary(
    tmp_path: Path,
) -> None:
    args, _fingerprint = _legacy_transition_case(tmp_path)
    args.legacy_transition_post_quality_reasons = {"page_like_url": 1}

    with pytest.raises(migration.SafetyError, match="no complete-line transition"):
        migration.audit_legacy(args)


def test_legacy_transition_requires_final_quality_reasons_to_match_per_key(
    tmp_path: Path,
) -> None:
    args, _fingerprint = _legacy_transition_case(tmp_path)
    legacy = json.loads(args.legacy_state.read_text())
    legacy["quality_skip_reasons"]["page_like_url"] = 1
    args.legacy_state.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    args.expected_state_sha256 = hashlib.sha256(args.legacy_state.read_bytes()).hexdigest()

    with pytest.raises(migration.SafetyError, match="legacy quality rejection reasons"):
        migration.audit_legacy(args)


def test_legacy_wall_clock_reconciliation_is_explicit_reason_scoped_and_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit_now = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(migration, "_utc_now", lambda: audit_now)
    args, fingerprint = _legacy_transition_case(tmp_path, eligible_suffix=True)
    legacy = json.loads(args.legacy_state.read_text())
    legacy.update(
        {
            "inserted": 5,
            "skipped": 4,
            "quality_skipped": 3,
            "quality_skip_reasons": {
                "body_too_short": 2,
                "published_future_too_far": 1,
            },
        }
    )
    args.legacy_state.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    args.expected_state_sha256 = hashlib.sha256(args.legacy_state.read_bytes()).hexdigest()

    with pytest.raises(migration.SafetyError, match="quality_skipped does not match"):
        migration.audit_legacy(args)

    args.legacy_wall_clock_reconcile_reason = (
        migration.LEGACY_WALL_CLOCK_RECONCILIATION_REASON
    )
    migration.audit_legacy(args)
    audit = json.loads(args.output.read_text())
    reconciliation = audit["legacy_wall_clock_reconciliation"]
    assert audit["loader_contract"]["quality_clock"] == "wall_clock"
    assert audit["audit_quality_now"] == audit_now.isoformat()
    assert reconciliation["mode"] == "aggregate_wall_clock_reason_delta"
    assert reconciliation["delta"] == {
        "quality_rows": 1,
        "reason_count": 1,
        "database_candidates_removed": 1,
    }
    assert reconciliation["unadjusted"]["total"]["quality_rejected"] == 2
    assert reconciliation["adjusted"]["total"]["quality_rejected"] == 3
    assert reconciliation["state_binding"]["sha256"] == args.expected_state_sha256
    assert reconciliation["eligible_candidates"]["count"] == 1
    assert audit["classification"]["counters"] == {
        "seen": 10,
        "inserted": 5,
        "duplicate": 1,
        "invalid": 1,
        "quality_rejected": 3,
    }

    migrate_args = SimpleNamespace(
        **vars(args),
        audit=args.output,
        expected_audit_sha256=hashlib.sha256(args.output.read_bytes()).hexdigest(),
        expected_input_path=fingerprint.canonical_path,
        expected_input_device=fingerprint.device,
        expected_input_inode=fingerprint.inode,
        expected_input_size=fingerprint.size,
        expected_input_anchor_sha256=fingerprint.anchor_sha256,
        output_state=tmp_path / "reconciled-state.json",
        output_sql=tmp_path / "reconciled-seed.sql",
    )
    missing_opt_in = SimpleNamespace(
        **{**vars(migrate_args), "legacy_wall_clock_reconcile_reason": None}
    )
    with pytest.raises(migration.SafetyError, match="audit wall-clock reconciliation"):
        migration.migrate_legacy(missing_opt_in)

    forged_audit = json.loads(json.dumps(audit))
    original_candidate = forged_audit["classification"]["wall_clock_drift_candidates"][
        "records"
    ][0]
    content = args.input.read_bytes()
    forged_start = original_candidate["end_offset"]
    forged_end = content.index(b"\n", forged_start) + 1
    forged_raw = content[forged_start:forged_end]
    forged_proof = migration._wall_clock_candidate_proof(
        [
            {
                "start_offset": forged_start,
                "end_offset": forged_end,
                "raw_sha256": hashlib.sha256(forged_raw).hexdigest(),
            }
        ]
    )
    forged_audit["classification"]["wall_clock_drift_candidates"] = forged_proof
    forged_audit["legacy_wall_clock_reconciliation"]["eligible_candidates"] = forged_proof
    unsigned = {key: value for key, value in forged_audit.items() if key != "audit_id"}
    forged_audit["audit_id"] = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    forged_path = tmp_path / "forged-candidate-audit.json"
    forged_path.write_text(json.dumps(forged_audit, sort_keys=True) + "\n", encoding="utf-8")
    forged_path.chmod(0o600)
    forged_args = SimpleNamespace(
        **{
            **vars(migrate_args),
            "audit": forged_path,
            "expected_audit_sha256": hashlib.sha256(forged_path.read_bytes()).hexdigest(),
            "output_state": tmp_path / "forged-candidate-state.json",
            "output_sql": tmp_path / "forged-candidate-seed.sql",
        }
    )
    with pytest.raises(migration.SafetyError, match="wall-clock drift candidate 0"):
        migration.migrate_legacy(forged_args)

    migration.migrate_legacy(migrate_args)
    state = json.loads(migrate_args.output_state.read_text())
    assert state["migrated_from"]["legacy_wall_clock_reconciliation"]["delta"] == (
        reconciliation["delta"]
    )


def test_legacy_wall_clock_reconciliation_rejects_non_target_reason_drift(
    tmp_path: Path,
) -> None:
    args, _fingerprint = _legacy_transition_case(tmp_path)
    legacy = json.loads(args.legacy_state.read_text())
    legacy["inserted"] = 5
    legacy["skipped"] = 4
    legacy["quality_skipped"] = 3
    legacy["quality_skip_reasons"] = {
        "body_too_short": 2,
        "published_future_too_far": 1,
        "page_like_url": 1,
    }
    args.legacy_state.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    args.expected_state_sha256 = hashlib.sha256(args.legacy_state.read_bytes()).hexdigest()
    args.legacy_wall_clock_reconcile_reason = (
        migration.LEGACY_WALL_CLOCK_RECONCILIATION_REASON
    )

    with pytest.raises(migration.SafetyError, match="row/reason deltas|non-target"):
        migration.audit_legacy(args)


def test_legacy_wall_clock_reconciliation_rejects_unqualified_database_rows(
    tmp_path: Path,
) -> None:
    args, _fingerprint = _legacy_transition_case(tmp_path, eligible_suffix=False)
    legacy = json.loads(args.legacy_state.read_text())
    legacy.update(
        {
            "inserted": 5,
            "skipped": 4,
            "quality_skipped": 3,
            "quality_skip_reasons": {
                "body_too_short": 2,
                "published_future_too_far": 1,
            },
        }
    )
    args.legacy_state.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    args.expected_state_sha256 = hashlib.sha256(args.legacy_state.read_bytes()).hexdigest()
    args.legacy_wall_clock_reconcile_reason = (
        migration.LEGACY_WALL_CLOCK_RECONCILIATION_REASON
    )

    with pytest.raises(migration.SafetyError, match="candidates cannot support"):
        migration.audit_legacy(args)


def test_legacy_transition_preserves_multiple_reasons_for_one_quality_row(
    tmp_path: Path,
) -> None:
    proc_root = tmp_path / "proc"
    identity = _write_fake_process(
        proc_root,
        5757,
        start_ticks=777,
        executable=tmp_path / "bin" / "python",
        cwd=tmp_path / "project",
        state="T",
    )
    input_path = tmp_path / "multi-reason.jsonl"
    input_path.write_text(
        json.dumps(
            {
                "title": "Privacy Policy",
                "body": "x",
                "url": "https://example.test/topic/policy",
                "domain": "example.test",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    reasons = {
        "body_too_short": 1,
        "missing_published_at": 1,
        "page_like_title": 1,
        "page_like_url": 1,
    }
    legacy = tmp_path / "multi-reason-state.json"
    legacy.write_text(
        json.dumps(
            {
                "offset": input_path.stat().st_size,
                "seen": 1,
                "inserted": 0,
                "skipped": 1,
                "quality_skipped": 1,
                "quality_skip_reasons": reasons,
                "input": str(input_path),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    provenance_file, provenance_sha256 = _provenance(
        tmp_path / "multi-reason-provenance.json",
        b'{"source":"multi-reason"}\n',
    )
    args = SimpleNamespace(
        checkpoint_key="wave1:multi-reason:news",
        job_id="wave1-multi-reason",
        run_id="wave1-multi-reason-run",
        code_version="test-code",
        source_map=_source_map(tmp_path / "multi-reason-source-map.csv"),
        disable_quality_gate=False,
        min_body_chars=20,
        min_published_year=2000,
        future_grace_days=1,
        expected_pid=5757,
        proc_root=proc_root,
        expected_start_ticks=identity["start_ticks"],
        expected_boot_id=identity["boot_id"],
        require_stopped=True,
        legacy_state=legacy,
        expected_state_sha256=hashlib.sha256(legacy.read_bytes()).hexdigest(),
        input=input_path,
        output=tmp_path / "multi-reason-audit.json",
        legacy_transition_pre_offset=0,
        legacy_transition_pre_seen=0,
        legacy_transition_pre_inserted=0,
        legacy_transition_pre_skipped=0,
        legacy_transition_post_offset=input_path.stat().st_size,
        legacy_transition_post_seen=1,
        legacy_transition_post_inserted=0,
        legacy_transition_post_skipped=1,
        legacy_transition_post_quality_skipped=1,
        legacy_transition_post_quality_reasons=reasons,
        legacy_transition_provenance_file=provenance_file,
        legacy_transition_provenance_sha256=provenance_sha256,
    )

    migration.audit_legacy(args)

    audit = json.loads(args.output.read_text())
    assert audit["classification"]["quality_rejected"] == 1
    assert audit["classification"]["quality_skip_reasons"] == reasons
    assert sum(audit["classification"]["quality_skip_reasons"].values()) == 4


def test_legacy_transition_matches_production_156_129_22_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit_now = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(migration, "_utc_now", lambda: audit_now)
    proc_root = tmp_path / "proc"
    identity = _write_fake_process(
        proc_root,
        5858,
        start_ticks=888,
        executable=tmp_path / "bin" / "python",
        cwd=tmp_path / "project",
        state="T",
    )
    long_body = "A complete article body with enough text for the quality threshold. " * 2
    sequence = 0

    def row(
        *,
        title: str = "Normal article title",
        body: str = long_body,
        url_path: str | None = None,
        published_at: str | None = "2024-01-01T00:00:00Z",
        fetched_at: str | None = None,
    ) -> bytes:
        nonlocal sequence
        sequence += 1
        payload: dict[str, Any] = {
            "title": title,
            "body": body,
            "url": f"https://example.test/{url_path or f'article-{sequence}'}",
            "domain": "example.test",
        }
        if published_at is not None:
            payload["published_at"] = published_at
        if fetched_at is not None:
            payload["fetched_at"] = fetched_at
        return json.dumps(payload, separators=(",", ":")).encode() + b"\n"

    records: list[bytes] = []
    records.extend(row(body="x") for _ in range(27))
    records.extend(row() for _ in range(21))
    records.extend(row(url_path=f"topic/item-{index}") for index in range(99))
    records.extend(row(body="x") for _ in range(18))
    records.extend(row(title="Privacy Policy") for _ in range(11))
    records.append(row(published_at=None))
    records.extend(row() for _ in range(2123))
    assert len(records) == 2300
    window_payload = b"".join(records)
    suffix_payload = b"".join(
        row(
            published_at="2026-01-02T12:00:00Z",
            fetched_at="2026-01-01T00:00:00Z",
        )
        for _ in range(4)
    )
    input_path = tmp_path / "production-shape.jsonl"
    input_path.write_bytes(window_payload + suffix_payload)
    post_reasons = {
        "body_too_short": 18,
        "missing_published_at": 1,
        "page_like_title": 11,
        "page_like_url": 99,
    }
    final_reasons = {**post_reasons, "published_future_too_far": 4}
    legacy = tmp_path / "production-shape-state.json"
    legacy.write_text(
        json.dumps(
            {
                "offset": input_path.stat().st_size,
                "seen": 2304,
                "inserted": 2170,
                "skipped": 134,
                "quality_skipped": 133,
                "quality_skip_reasons": final_reasons,
                "input": str(input_path),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    provenance_file, provenance_sha256 = _provenance(
        tmp_path / "production-shape-provenance.json",
        b'{"source":"production-shape"}\n',
    )
    args = SimpleNamespace(
        checkpoint_key="wave1:production-shape:news",
        job_id="wave1-production-shape",
        run_id="wave1-production-shape-run",
        code_version="test-code",
        source_map=_source_map(tmp_path / "production-shape-source-map.csv"),
        disable_quality_gate=False,
        min_body_chars=20,
        min_published_year=2000,
        future_grace_days=1,
        expected_pid=5858,
        proc_root=proc_root,
        expected_start_ticks=identity["start_ticks"],
        expected_boot_id=identity["boot_id"],
        require_stopped=True,
        legacy_state=legacy,
        expected_state_sha256=hashlib.sha256(legacy.read_bytes()).hexdigest(),
        input=input_path,
        output=tmp_path / "production-shape-audit.json",
        legacy_transition_pre_offset=0,
        legacy_transition_pre_seen=0,
        legacy_transition_pre_inserted=0,
        legacy_transition_pre_skipped=0,
        legacy_transition_post_offset=len(window_payload),
        legacy_transition_post_seen=2300,
        legacy_transition_post_inserted=2170,
        legacy_transition_post_skipped=130,
        legacy_transition_post_quality_skipped=129,
        legacy_transition_post_quality_reasons=post_reasons,
        legacy_transition_provenance_file=provenance_file,
        legacy_transition_provenance_sha256=provenance_sha256,
        legacy_wall_clock_reconcile_reason=(
            migration.LEGACY_WALL_CLOCK_RECONCILIATION_REASON
        ),
    )

    migration.audit_legacy(args)

    audit = json.loads(args.output.read_text())
    transition = audit["legacy_quality_gate_transition"]
    candidates = transition["candidate_boundaries"]
    window = audit["classification"]["cohorts"][1]
    assert candidates["potential_quality_rejected"] == 156
    assert candidates["quality_rejected_before_canonical"] == 27
    assert candidates["quality_rejected_after_canonical"] == 129
    assert candidates["count"] == 22
    assert transition["invariant_gap"]["complete_lines"] == 21
    assert window["legacy_accounting"]["observed"] == {
        "seen": 2300,
        "inserted": 2170,
        "skipped": 130,
    }
    assert window["legacy_accounting"]["inferred_duplicate"] == 1
    assert audit["legacy_wall_clock_reconciliation"]["delta"] == {
        "quality_rows": 4,
        "reason_count": 4,
        "database_candidates_removed": 4,
    }
    assert audit["legacy_wall_clock_reconciliation"]["eligible_candidates"]["count"] == 4
    assert audit["classification"]["counters"] == {
        "seen": 2304,
        "inserted": 2170,
        "duplicate": 1,
        "invalid": 0,
        "quality_rejected": 133,
    }


def test_legacy_transition_migration_rejects_tampering_and_parameter_drift(
    tmp_path: Path,
) -> None:
    args, fingerprint = _legacy_transition_case(tmp_path)
    migration.audit_legacy(args)
    audit_digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    migrate_args = SimpleNamespace(
        **vars(args),
        audit=args.output,
        expected_audit_sha256=audit_digest,
        expected_input_path=fingerprint.canonical_path,
        expected_input_device=fingerprint.device,
        expected_input_inode=fingerprint.inode,
        expected_input_size=fingerprint.size,
        expected_input_anchor_sha256=fingerprint.anchor_sha256,
        output_state=tmp_path / "state.json",
        output_sql=tmp_path / "seed.sql",
    )
    original_audit = json.loads(args.output.read_text())
    provenance_bytes = args.legacy_transition_provenance_file.read_bytes()
    args.legacy_transition_provenance_file.write_bytes(b'{"source":"tampered"}\n')
    with pytest.raises(migration.SafetyError, match="provenance digest"):
        migration.migrate_legacy(migrate_args)
    args.legacy_transition_provenance_file.write_bytes(provenance_bytes)

    drifted = SimpleNamespace(
        **{
            **vars(migrate_args),
            "legacy_transition_post_inserted": migrate_args.legacy_transition_post_inserted + 1,
        }
    )
    with pytest.raises(migration.SafetyError, match="audit transition evidence digest"):
        migration.migrate_legacy(drifted)

    tampered = json.loads(json.dumps(original_audit))
    tampered["legacy_quality_gate_transition"]["candidate_boundaries"]["count"] += 1
    args.output.write_text(json.dumps(tampered) + "\n", encoding="utf-8")
    with pytest.raises(migration.SafetyError, match="audit file digest"):
        migration.migrate_legacy(migrate_args)
    assert not migrate_args.output_state.exists()
    assert not migrate_args.output_sql.exists()

    forged = json.loads(json.dumps(original_audit))
    forged["legacy_quality_gate_transition"]["pre_checkpoint"]["anchor_sha256"] = "0" * 64
    unsigned = {key: value for key, value in forged.items() if key != "audit_id"}
    forged["audit_id"] = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    args.output.write_text(json.dumps(forged, sort_keys=True) + "\n", encoding="utf-8")
    forged_args = SimpleNamespace(
        **{
            **vars(migrate_args),
            "expected_audit_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
        }
    )
    with pytest.raises(migration.SafetyError, match="input transition pre anchor"):
        migration.migrate_legacy(forged_args)

    forged_gap = json.loads(json.dumps(original_audit))
    forged_gap["legacy_quality_gate_transition"]["invariant_gap"]["sha256"] = "0" * 64
    unsigned_gap = {key: value for key, value in forged_gap.items() if key != "audit_id"}
    forged_gap["audit_id"] = hashlib.sha256(
        json.dumps(unsigned_gap, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    args.output.write_text(json.dumps(forged_gap, sort_keys=True) + "\n", encoding="utf-8")
    forged_gap_args = SimpleNamespace(
        **{
            **vars(migrate_args),
            "expected_audit_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
        }
    )
    with pytest.raises(migration.SafetyError, match="input transition invariant gap sha256"):
        migration.migrate_legacy(forged_gap_args)

    semantic_gap = json.loads(json.dumps(original_audit))
    transition_artifact = semantic_gap["legacy_quality_gate_transition"]
    gap = transition_artifact["invariant_gap"]
    candidates = transition_artifact["candidate_boundaries"]
    content = args.input.read_bytes()
    expanded_end = content.index(b"\n", gap["end_offset"]) + 1
    expanded_payload = content[gap["start_offset"] : expanded_end]
    gap.update(
        {
            "end_offset": expanded_end,
            "bytes": len(expanded_payload),
            "complete_lines": expanded_payload.count(b"\n"),
            "sha256": hashlib.sha256(expanded_payload).hexdigest(),
        }
    )
    candidates["latest_offset"] = expanded_end
    candidates["count"] = gap["complete_lines"] + 1
    unsigned_semantic = {
        key: value for key, value in semantic_gap.items() if key != "audit_id"
    }
    semantic_gap["audit_id"] = hashlib.sha256(
        json.dumps(unsigned_semantic, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    args.output.write_text(json.dumps(semantic_gap, sort_keys=True) + "\n", encoding="utf-8")
    semantic_gap_args = SimpleNamespace(
        **{
            **vars(migrate_args),
            "expected_audit_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
        }
    )
    with pytest.raises(migration.SafetyError, match="quality-sensitive record"):
        migration.migrate_legacy(semantic_gap_args)


def test_legacy_transition_arguments_are_all_or_none(tmp_path: Path) -> None:
    args = SimpleNamespace(legacy_transition_pre_offset=1)

    with pytest.raises(migration.SafetyError, match="must be supplied together"):
        migration._resolve_legacy_quality_gate_transition(args, 10)

    provenance_file, provenance_sha256 = _provenance(
        tmp_path / "argument-provenance.json",
        b'{"source":"argument-test"}\n',
    )
    complete = SimpleNamespace(
        legacy_transition_pre_offset=1,
        legacy_transition_pre_seen=1,
        legacy_transition_pre_inserted=1,
        legacy_transition_pre_skipped=0,
        legacy_transition_post_offset=2,
        legacy_transition_post_seen=2,
        legacy_transition_post_inserted=1,
        legacy_transition_post_skipped=1,
        legacy_transition_post_quality_skipped=1,
        legacy_transition_post_quality_reasons='{"body_too_short":1}',
        legacy_transition_provenance_file=provenance_file,
        legacy_transition_provenance_sha256=provenance_sha256,
    )
    transition = migration._resolve_legacy_quality_gate_transition(complete, 3)
    assert transition is not None
    assert transition.after_quality_reasons == {"body_too_short": 1}


def test_legacy_audit_fails_closed_on_non_object_json_and_normalization_exceptions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        disable_quality_gate=False,
        min_body_chars=20,
        min_published_year=2000,
        future_grace_days=1,
    )
    non_object = tmp_path / "non-object.jsonl"
    non_object.write_bytes(b"[1,2]\n")
    with pytest.raises(migration.SafetyError, match="non-object JSON"):
        migration._audit_input_prefix(non_object, non_object.stat().st_size, {}, settings)
    non_object_fingerprint = migration.fingerprint_input(non_object)
    with pytest.raises(migration.SafetyError, match="invariant gap contains non-object"):
        migration._verify_invariant_gap_classifications(
            non_object,
            (0, non_object.stat().st_size),
            non_object_fingerprint,
            {},
            settings,
            datetime(2026, 7, 10, tzinfo=timezone.utc),
        )

    invalid_utf8 = tmp_path / "invalid-utf8.jsonl"
    invalid_utf8.write_bytes(b"\xff\n")
    with pytest.raises(migration.SafetyError, match="invalid UTF-8"):
        migration._audit_input_prefix(invalid_utf8, invalid_utf8.stat().st_size, {}, settings)
    invalid_utf8_fingerprint = migration.fingerprint_input(invalid_utf8)
    with pytest.raises(migration.SafetyError, match="invariant gap contains invalid UTF-8"):
        migration._verify_invariant_gap_classifications(
            invalid_utf8,
            (0, invalid_utf8.stat().st_size),
            invalid_utf8_fingerprint,
            {},
            settings,
            datetime(2026, 7, 10, tzinfo=timezone.utc),
        )

    valid = tmp_path / "valid.jsonl"
    valid.write_bytes(b'{"title":"value"}\n')

    def fail_classification(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("unit-test normalization failure")

    monkeypatch.setattr(migration, "classify_news_value", fail_classification)
    with pytest.raises(migration.SafetyError, match="unexpected exception"):
        migration._audit_input_prefix(valid, valid.stat().st_size, {}, settings)


def test_legacy_audit_requires_the_verified_process_to_be_stopped(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    _write_fake_process(
        proc_root,
        5555,
        start_ticks=555,
        executable=tmp_path / "bin" / "python",
        cwd=tmp_path / "project",
        state="S",
    )

    with pytest.raises(migration.SafetyError, match="required stopped state"):
        migration.require_process_stopped(5555, proc_root=proc_root)


def test_ddl_is_idempotent_and_never_applied_by_helper(tmp_path: Path) -> None:
    output = tmp_path / "checkpoint.sql"
    result = migration.main(["emit-ddl", "--output", str(output)])

    assert result == 0
    sql = output.read_text()
    assert f"CREATE TABLE IF NOT EXISTS {migration.CHECKPOINT_TABLE}" in sql
    assert "ADD COLUMN IF NOT EXISTS legacy_seen" in sql
    assert "seen = inserted + duplicate + invalid + quality_rejected" in sql
    assert "psycopg" not in migration.Path(migration.__file__).read_text()


def test_password_argv_is_rejected_without_echoing_value(capsys: pytest.CaptureFixture[str]) -> None:
    sentinel = "never-echo-this-password-sentinel"

    with pytest.raises(SystemExit) as error:
        loader.parse_args(["--password", sentinel])

    captured = capsys.readouterr()
    assert error.value.code == 2
    assert sentinel not in captured.out
    assert sentinel not in captured.err
    assert "legacy --password option is forbidden" in captured.err


def test_loader_and_daily_filter_reject_unmanaged_database_users(tmp_path: Path) -> None:
    loader_args = _args(tmp_path, tmp_path / "input.jsonl")
    loader_args.user = "unmanaged_user"
    with pytest.raises(loader.CheckpointError, match="must be wave1_loader"):
        loader.validate_args(loader_args)

    filter_args = url_filter.parse_args(
        [
            "--input",
            str(tmp_path / "input.jsonl"),
            "--output",
            str(tmp_path / "output.jsonl"),
            "--sslmode",
            "require",
            "--user",
            "unmanaged_user",
        ]
    )
    with pytest.raises(RuntimeError, match="must be wave1_loader"):
        url_filter.validate_args(filter_args)


def test_loader_and_daily_filter_default_to_managed_role(tmp_path: Path) -> None:
    filter_args = url_filter.parse_args(
        [
            "--input",
            str(tmp_path / "input.jsonl"),
            "--output",
            str(tmp_path / "output.jsonl"),
            "--sslmode",
            "require",
        ]
    )

    assert filter_args.user == "wave1_loader"
    assert 'parser.add_argument("--user", default="wave1_loader")' in Path(
        loader.__file__
    ).read_text(encoding="utf-8")


def test_secret_file_requires_owner_mode_and_no_symlink(tmp_path: Path) -> None:
    secret = tmp_path / "secret"
    secret.write_text("unit-test-value\n", encoding="utf-8")
    secret.chmod(0o600)
    assert migration.validate_private_file(secret) == secret

    link = tmp_path / "secret-link"
    link.symlink_to(secret)
    with pytest.raises(migration.SafetyError, match="non-symlink"):
        migration.validate_private_file(link)
    secret.chmod(0o640)
    with pytest.raises(migration.SafetyError, match="0600"):
        migration.validate_private_file(secret)


def test_controller_scrubs_password_environment_before_exec(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    environment = {
        **os.environ,
        "GLOBEMIND_HOME": str(PROJECT_ROOT),
        "PYTHON_BIN": sys.executable,
        "WAVE1_LOADER_RUNTIME_DIR": str(runtime_dir),
        "L1_DB_PASSWORD": "unit-test-sentinel-one",
        "DATABASE_URL": "unit-test-sentinel-two",
    }
    script = r'''
source "$1"
scrub_password_environment
for name in L1_DB_PASSWORD PG_WRITE_PASSWORD DB_PASSWORD PG_PASSWORD PGPASSWORD DATABASE_URL SQLALCHEMY_DATABASE_URL PGPASSFILE; do
  [[ -z "${!name+x}" ]] || exit 9
done
[[ "$GLOBEMIND_DB_PASSWORD_FILE" == "$SECRET_FILE" ]]
'''

    result = subprocess.run(
        ["bash", "-c", script, "bash", str(CONTROLLER)],
        cwd=PROJECT_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "unit-test-sentinel" not in result.stdout
    assert "unit-test-sentinel" not in result.stderr
    controller_source = CONTROLLER.read_text(encoding="utf-8")
    assert "--password" not in controller_source


def test_controller_requires_managed_loader_role_unless_legacy_is_explicit() -> None:
    script = r'''
source "$1"
validate_runtime_database_role
'''
    base_environment = {
        **os.environ,
        "GLOBEMIND_HOME": str(PROJECT_ROOT),
        "PYTHON_BIN": sys.executable,
    }

    managed = subprocess.run(
        ["bash", "-c", script, "bash", str(CONTROLLER)],
        cwd=PROJECT_ROOT,
        env={**base_environment, "DB_USER": "wave1_loader"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert managed.returncode == 0

    rejected = subprocess.run(
        ["bash", "-c", script, "bash", str(CONTROLLER)],
        cwd=PROJECT_ROOT,
        env={**base_environment, "DB_USER": "postgres"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert rejected.returncode != 0

    rollback = subprocess.run(
        ["bash", "-c", script, "bash", str(CONTROLLER)],
        cwd=PROJECT_ROOT,
        env={
            **base_environment,
            "DB_USER": "postgres",
            "WAVE1_LOADER_ALLOW_LEGACY_DB_ROLE": "1",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert rollback.returncode == 0


def test_controller_runtime_directory_is_owner_only(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    log_dir = tmp_path / "logs"
    environment = {
        **os.environ,
        "GLOBEMIND_HOME": str(PROJECT_ROOT),
        "PYTHON_BIN": sys.executable,
        "WAVE1_LOADER_CONTROL_PYTHON": sys.executable,
        "WAVE1_LOADER_RUNTIME_DIR": str(runtime_dir),
        "WAVE1_LOADER_LOG_DIR": str(log_dir),
        "WAVE1_LOADER_DEAD_LETTER_DIR": str(tmp_path / "dead-letters"),
    }

    subprocess.run(
        ["bash", "-c", 'source "$1"; prepare_runtime_paths', "bash", str(CONTROLLER)],
        cwd=PROJECT_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )

    assert stat.S_IMODE(runtime_dir.stat().st_mode) == 0o700
    assert runtime_dir.stat().st_uid == os.geteuid()


def test_controller_verifies_authenticated_socket_runtime(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(mode=0o700)
    pid_file = runtime_dir / "wave1_loader.pid"
    meta_file = runtime_dir / "wave1_loader.pid.meta"
    ready_file = runtime_dir / "wave1_loader.pid.ready"
    socket_path = runtime_dir / "wave1_loader.pid.sock"
    server = loader.ControlServer(socket_path, "controller-test-instance")
    socket_identity = server.start()
    identity = migration.capture_process_identity(os.getpid())
    pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")
    pid_file.chmod(0o600)
    migration.atomic_write_json(
        meta_file,
        {
            "schema_version": 2,
            "instance_name": "wave1_loader",
            "instance_id": "controller-test-instance",
            "identity": identity,
            "control_socket": socket_identity,
        },
    )
    migration.atomic_write_json(
        ready_file,
        {
            "schema_version": 2,
            "status": "ready",
            "instance_id": "controller-test-instance",
            "identity": identity,
            "control_socket": socket_identity,
        },
    )
    environment = {
        **os.environ,
        "GLOBEMIND_HOME": str(PROJECT_ROOT),
        "PYTHON_BIN": sys.executable,
        "WAVE1_LOADER_CONTROL_PYTHON": sys.executable,
        "WAVE1_LOADER_RUNTIME_DIR": str(runtime_dir),
        "WAVE1_LOADER_LOG_DIR": str(tmp_path / "logs"),
        "WAVE1_LOADER_LEGACY_PID_FILE": str(tmp_path / "legacy.pid"),
    }
    try:
        result = subprocess.run(
            ["bash", "-c", 'source "$1"; runtime_verified', "bash", str(CONTROLLER)],
            cwd=PROJECT_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
    finally:
        server.close()

    assert result.returncode == 0


def test_controller_delays_and_reverifies_ready_state() -> None:
    source = CONTROLLER.read_text(encoding="utf-8")
    wait_body = source.split("wait_for_ready() {", 1)[1].split("\n}", 1)[0]

    assert wait_body.count("runtime_ready_verified") >= 2
    assert 'sleep "$READY_STABILITY_SEC"' in wait_body
    assert "runtime_ready_verified >/dev/null 2>&1" in wait_body


def test_controller_allows_large_checkpoint_startup_and_retries_authenticated_stop() -> None:
    source = CONTROLLER.read_text(encoding="utf-8")
    stop_body = source.split("stop_fresh_child() {", 1)[1].split("\n}", 1)[0]

    assert 'START_TIMEOUT_SEC="${WAVE1_LOADER_START_TIMEOUT_SEC:-120}"' in source
    assert "while meta_verified >/dev/null 2>&1" in stop_body
    assert "attach-runtime-socket" in stop_body
    assert "socket-control" in stop_body


def test_controller_missing_pid_file_fails_quietly(tmp_path: Path) -> None:
    result = subprocess.run(
        ["bash", "-c", 'source "$1"; pid_file_value', "bash", str(CONTROLLER)],
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "GLOBEMIND_HOME": str(PROJECT_ROOT),
            "WAVE1_LOADER_RUNTIME_DIR": str(tmp_path / "missing-runtime"),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr == ""
