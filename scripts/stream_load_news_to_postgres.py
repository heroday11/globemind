#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import signal
import socket
import stat
import struct
import sys
import threading
import time
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import psycopg2
from db_runtime_config import (
    require_database_password,
    validate_database_transport,
    validate_loader_database_role,
)
from news_date_cleaning import parse_datetime
from prepare_news_table_rows import load_source_map
from wave1_loader_migrate import (
    CHECKPOINT_SCHEMA_VERSION,
    CHECKPOINT_TABLE,
    RUNTIME_IDENTITY_SCHEMA_VERSION,
    SafetyError,
    atomic_write_json,
    capture_process_identity,
    classify_news_value,
    fingerprint_input,
    inspect_control_socket,
    load_json_object,
    resolve_loader_code_version,
    resolve_loader_config_sha256,
    validate_private_file,
    validate_safe_identifier,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE_MAP = PROJECT_ROOT / "data" / "source_curation" / "historical_wave1_targets.csv"
STOP_REQUESTED = False
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

CHECKPOINT_COLUMNS = (
    "checkpoint_key",
    "schema_version",
    "job_id",
    "run_id",
    "input_path",
    "input_device",
    "input_inode",
    "input_size",
    "input_offset",
    "input_anchor_sha256",
    "code_version",
    "config_sha256",
    "seen",
    "legacy_seen",
    "inserted",
    "duplicate",
    "invalid",
    "quality_rejected",
    "quality_skip_reasons",
    "completed",
    "sealed_final_bytes",
    "sealed_rows",
    "sealed_sha256",
    "last_progress_at",
    "updated_at",
)
CHECKPOINT_SELECT_COLUMNS = ", ".join(CHECKPOINT_COLUMNS)


class CheckpointError(RuntimeError):
    pass


@dataclass(frozen=True)
class Counters:
    seen: int = 0
    inserted: int = 0
    duplicate: int = 0
    invalid: int = 0
    quality_rejected: int = 0

    def validate(self) -> None:
        values = (self.seen, self.inserted, self.duplicate, self.invalid, self.quality_rejected)
        if any(value < 0 for value in values):
            raise CheckpointError("checkpoint counters must be non-negative")
        if self.seen != self.inserted + self.duplicate + self.invalid + self.quality_rejected:
            raise CheckpointError("checkpoint counter invariant failed")


@dataclass(frozen=True)
class Checkpoint:
    checkpoint_key: str
    job_id: str
    run_id: str
    input_path: str
    input_device: int
    input_inode: int
    input_size: int
    input_offset: int
    input_anchor_sha256: str
    code_version: str
    config_sha256: str
    counters: Counters
    quality_skip_reasons: dict[str, int]
    legacy_seen: int | None = None
    completed: bool = False
    sealed_final_bytes: int | None = None
    sealed_rows: int | None = None
    sealed_sha256: str | None = None
    last_progress_at: Any = None
    updated_at: Any = None

    def validate(self) -> None:
        self.counters.validate()
        if self.input_offset < 0 or self.input_size < self.input_offset:
            raise CheckpointError("checkpoint input bounds are invalid")
        for name, value in (
            ("input_anchor_sha256", self.input_anchor_sha256),
            ("config_sha256", self.config_sha256),
        ):
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise CheckpointError(f"{name} is not a lowercase SHA-256 digest")
        if any(value < 0 for value in self.quality_skip_reasons.values()):
            raise CheckpointError("quality rejection counters must be non-negative")
        if self.legacy_seen is not None and not 0 <= self.legacy_seen <= self.counters.seen:
            raise CheckpointError("legacy_seen must be between zero and seen")
        if self.completed:
            if (
                self.sealed_final_bytes != self.input_offset
                or self.sealed_rows != self.counters.seen
                or self.sealed_sha256 != self.input_anchor_sha256
            ):
                raise CheckpointError("completed checkpoint does not match its seal")
        elif any(value is not None for value in (self.sealed_final_bytes, self.sealed_rows, self.sealed_sha256)):
            raise CheckpointError("uncompleted checkpoint contains seal fields")

    def mirror(self) -> dict[str, Any]:
        return {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "checkpoint_key": self.checkpoint_key,
            "job_id": self.job_id,
            "run_id": self.run_id,
            "input": {
                "canonical_path": self.input_path,
                "device": self.input_device,
                "inode": self.input_inode,
                "size": self.input_size,
                "offset": self.input_offset,
                "anchor_sha256": self.input_anchor_sha256,
            },
            "code_version": self.code_version,
            "config_sha256": self.config_sha256,
            "counters": {
                "seen": self.counters.seen,
                "inserted": self.counters.inserted,
                "duplicate": self.counters.duplicate,
                "invalid": self.counters.invalid,
                "quality_rejected": self.counters.quality_rejected,
            },
            "legacy_seen": self.legacy_seen,
            "quality_skip_reasons": self.quality_skip_reasons,
            "completed": self.completed,
            "seal": None
            if not self.completed
            else {
                "final_bytes": self.sealed_final_bytes,
                "rows": self.sealed_rows,
                "sha256": self.sealed_sha256,
            },
            "last_progress_at": _json_time(self.last_progress_at),
            "updated_at": _json_time(self.updated_at),
        }


@dataclass(frozen=True)
class RawRecord:
    start_offset: int
    end_offset: int
    raw: bytes
    value: dict[str, Any] | None
    error: str | None


@dataclass(frozen=True)
class PreparedRecord:
    raw: RawRecord
    classification: str
    normalized: dict[str, Any] | None = None
    quality_reasons: tuple[str, ...] = ()


def _json_time(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def reject_legacy_password_args(argv: Iterable[str]) -> None:
    for token in argv:
        if token == "--password" or token.startswith("--password="):
            print("refused: legacy --password option is forbidden", file=sys.stderr)
            raise SystemExit(2)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    raw_arguments = list(sys.argv[1:] if argv is None else argv)
    reject_legacy_password_args(raw_arguments)
    parser = argparse.ArgumentParser(description="Load a sealed JSONL stream into PostgreSQL safely.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--source-map", type=Path, default=DEFAULT_SOURCE_MAP)
    parser.add_argument("--state-path", type=Path, required=True)
    parser.add_argument("--heartbeat-path", type=Path, required=True)
    parser.add_argument("--ready-path", type=Path, required=True)
    parser.add_argument("--control-socket", type=Path, required=True)
    parser.add_argument("--sealed-manifest", type=Path, required=True)
    parser.add_argument("--dead-letter-dir", type=Path, required=True)
    parser.add_argument("--checkpoint-key", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--code-version", default="")
    parser.add_argument("--poll-sec", type=float, default=15.0)
    parser.add_argument("--heartbeat-sec", type=float, default=30.0)
    parser.add_argument("--completion-grace-sec", type=float, default=3.0)
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--host", default="192.168.207.171")
    parser.add_argument("--port", type=int, default=54333)
    parser.add_argument("--user", default="wave1_loader")
    parser.add_argument("--dbname", default="news")
    parser.add_argument("--sslmode", choices=("verify-full", "require", "disable"), required=True)
    parser.add_argument("--allow-private-scram-transport", action="store_true")
    parser.add_argument("--allow-legacy-postgres-role", action="store_true")
    parser.add_argument("--connect-timeout-sec", type=int, default=10)
    parser.add_argument("--statement-timeout-ms", type=int, default=30000)
    parser.add_argument("--lock-timeout-ms", type=int, default=5000)
    parser.add_argument("--disable-quality-gate", action="store_true")
    parser.add_argument("--min-body-chars", type=int, default=120)
    parser.add_argument("--min-published-year", type=int, default=2000)
    parser.add_argument("--future-grace-days", type=int, default=1)
    return parser.parse_args(raw_arguments)


def validate_args(args: argparse.Namespace) -> None:
    validate_safe_identifier("checkpoint_key", args.checkpoint_key)
    validate_safe_identifier("job_id", args.job_id)
    validate_safe_identifier("run_id", args.run_id)
    if args.code_version:
        validate_safe_identifier("code_version", args.code_version)
    if (
        args.batch_size <= 0
        or args.poll_sec < 0
        or args.heartbeat_sec <= 0
        or args.completion_grace_sec < 0
    ):
        raise CheckpointError("batch and interval settings must be positive")
    if min(args.connect_timeout_sec, args.statement_timeout_ms, args.lock_timeout_ms) <= 0:
        raise CheckpointError("database timeout settings must be positive")
    try:
        validate_loader_database_role(
            args.user,
            allow_legacy_postgres_role=args.allow_legacy_postgres_role,
        )
        validate_database_transport(
            args.host,
            args.sslmode,
            allow_private_scram_transport=args.allow_private_scram_transport,
        )
    except RuntimeError as exc:
        raise CheckpointError(str(exc)) from exc


def _request_stop(_signum: int, _frame: Any) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True


class ControlServer:
    def __init__(self, path: Path, instance_id: str) -> None:
        self.path = path
        self.instance_id = instance_id
        self.identity = capture_process_identity(os.getpid())
        self.listener: socket.socket | None = None
        self.socket_identity: dict[str, Any] | None = None
        self.thread: threading.Thread | None = None
        self.shutdown_requested = threading.Event()
        self.started = threading.Event()
        self.failure: BaseException | None = None

    def _validate_parent(self) -> None:
        try:
            metadata = self.path.parent.lstat()
        except OSError as exc:
            raise CheckpointError("control socket directory is unavailable") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise CheckpointError("control socket parent must be a non-symlink directory")
        if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
            raise CheckpointError("control socket directory must be owner-only mode 0700")
        if self.path.exists() or self.path.is_symlink():
            raise CheckpointError("control socket path already exists")

    def start(self) -> dict[str, Any]:
        self._validate_parent()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(self.path))
            os.chmod(self.path, 0o600)
            listener.listen(4)
            listener.settimeout(0.5)
            self.listener = listener
            self.socket_identity = inspect_control_socket(self.path)
            directory = os.open(
                self.path.parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            self.thread = threading.Thread(
                target=self._serve,
                name="wave1-loader-control",
                daemon=True,
            )
            self.thread.start()
            if not self.started.wait(timeout=2.0):
                raise CheckpointError("control socket thread failed readiness")
            self.ensure_healthy()
            return dict(self.socket_identity)
        except BaseException:
            listener.close()
            self._unlink_owned_socket()
            raise

    def _peer_uid(self, connection: socket.socket) -> int:
        option = getattr(socket, "SO_PEERCRED", None)
        if option is None:
            raise CheckpointError("SO_PEERCRED is unavailable")
        raw = connection.getsockopt(socket.SOL_SOCKET, option, struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", raw)
        return uid

    def _receive_request(self, connection: socket.socket) -> dict[str, Any]:
        connection.settimeout(2.0)
        payload = b""
        while b"\n" not in payload and len(payload) <= 8192:
            block = connection.recv(4096)
            if not block:
                break
            payload += block
        if len(payload) > 8192:
            raise CheckpointError("control request is too large")
        try:
            request = json.loads(payload.split(b"\n", 1)[0].decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise CheckpointError("control request is invalid") from exc
        if not isinstance(request, dict):
            raise CheckpointError("control request must be an object")
        return request

    def _handle(self, connection: socket.socket) -> None:
        if self._peer_uid(connection) != os.geteuid():
            raise CheckpointError("control request UID mismatch")
        request = self._receive_request(connection)
        if request.get("schema_version") != 1:
            raise CheckpointError("control protocol schema mismatch")
        if request.get("instance_id") != self.instance_id:
            raise CheckpointError("control instance mismatch")
        if request.get("boot_id") != self.identity["boot_id"]:
            raise CheckpointError("control boot ID mismatch")
        command = request.get("command")
        if command not in {"status", "stop"}:
            raise CheckpointError("unsupported control command")
        if command == "stop":
            _request_stop(signal.SIGTERM, None)
        response = {
            "ok": True,
            "status": "stopping" if STOP_REQUESTED else "running",
            "instance_id": self.instance_id,
            "pid": os.getpid(),
        }
        connection.sendall(json.dumps(response, sort_keys=True).encode("utf-8") + b"\n")

    def _serve(self) -> None:
        self.started.set()
        try:
            while not self.shutdown_requested.is_set():
                try:
                    assert self.listener is not None
                    connection, _address = self.listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if self.shutdown_requested.is_set():
                        return
                    raise
                with connection:
                    try:
                        self._handle(connection)
                    except (CheckpointError, OSError):
                        try:
                            connection.sendall(b'{"ok":false}\n')
                        except OSError:
                            pass
        except BaseException as exc:
            self.failure = exc

    def ensure_healthy(self) -> None:
        if self.failure is not None:
            raise CheckpointError("control socket thread failed") from self.failure
        if self.thread is None or not self.thread.is_alive():
            raise CheckpointError("control socket thread is not alive")

    def _unlink_owned_socket(self) -> None:
        if self.socket_identity is None:
            return
        try:
            current = inspect_control_socket(self.path)
        except SafetyError:
            return
        if current == self.socket_identity:
            self.path.unlink(missing_ok=True)
            directory = os.open(
                self.path.parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(directory)
            finally:
                os.close(directory)

    def close(self) -> None:
        self.shutdown_requested.set()
        if self.listener is not None:
            self.listener.close()
        if self.thread is not None:
            self.thread.join(timeout=2.0)
        self._unlink_owned_socket()


def install_signal_handlers() -> None:
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)


def sleep_interruptibly(seconds: float) -> None:
    deadline = time.monotonic() + max(0.0, seconds)
    while not STOP_REQUESTED:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.5, remaining))


def code_version(args: argparse.Namespace) -> str:
    return resolve_loader_code_version(args.code_version)


def config_sha256(args: argparse.Namespace, resolved_code_version: str) -> str:
    return resolve_loader_config_sha256(
        input_path=args.input,
        source_map_path=args.source_map,
        checkpoint_key=args.checkpoint_key,
        job_id=args.job_id,
        run_id=args.run_id,
        resolved_code_version=resolved_code_version,
        disable_quality_gate=args.disable_quality_gate,
        min_body_chars=args.min_body_chars,
        min_published_year=args.min_published_year,
        future_grace_days=args.future_grace_days,
    )


def validate_database_secret() -> str:
    configured = (os.getenv("GLOBEMIND_DB_PASSWORD_FILE") or "").strip()
    if not configured:
        raise CheckpointError("GLOBEMIND_DB_PASSWORD_FILE is required for managed loader startup")
    validate_private_file(Path(configured))
    return require_database_password()


def ensure_private_directory(path: Path) -> None:
    if path.is_symlink():
        raise CheckpointError(f"private directory must not be a symlink: {path}")
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid():
        raise CheckpointError(f"private directory owner/type mismatch: {path}")
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        raise CheckpointError(f"private directory must have mode 0700: {path}")


def read_records(path: Path, offset: int, batch_size: int, checkpoint: Checkpoint) -> list[RawRecord]:
    if path.is_symlink():
        raise CheckpointError("input path became a symlink")
    canonical = path.resolve(strict=True)
    if str(canonical) != checkpoint.input_path:
        raise CheckpointError("input canonical path changed")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(canonical, flags)
    records: list[RawRecord] = []
    try:
        metadata = os.fstat(descriptor)
        if (metadata.st_dev, metadata.st_ino) != (checkpoint.input_device, checkpoint.input_inode):
            raise CheckpointError("input device/inode changed")
        if metadata.st_size < checkpoint.input_size or metadata.st_size < offset:
            raise CheckpointError("input was truncated")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            handle.seek(offset)
            while len(records) < batch_size and not STOP_REQUESTED:
                start = handle.tell()
                raw = handle.readline()
                if not raw:
                    break
                if not raw.endswith(b"\n"):
                    break
                end = handle.tell()
                value: dict[str, Any] | None = None
                error: str | None = None
                try:
                    decoded = raw.decode("utf-8")
                    parsed = json.loads(decoded)
                    if not isinstance(parsed, dict):
                        error = "non_object_json"
                    else:
                        value = parsed
                except UnicodeDecodeError:
                    error = "invalid_utf8"
                except json.JSONDecodeError:
                    error = "invalid_json"
                records.append(RawRecord(start, end, raw, value, error))
        after = os.fstat(descriptor)
        if (after.st_dev, after.st_ino) != (checkpoint.input_device, checkpoint.input_inode):
            raise CheckpointError("input identity changed during batch read")
        if after.st_size < checkpoint.input_size:
            raise CheckpointError("input was truncated during batch read")
        return records
    finally:
        os.close(descriptor)


def prepare_records(
    records: list[RawRecord],
    source_map: dict[str, Any],
    args: argparse.Namespace,
) -> list[PreparedRecord]:
    prepared: list[PreparedRecord] = []
    for raw in records:
        if STOP_REQUESTED:
            break
        if raw.error or raw.value is None:
            prepared.append(PreparedRecord(raw, "invalid"))
            continue
        classified = classify_news_value(
            raw.value,
            source_map,
            disable_quality_gate=args.disable_quality_gate,
            min_body_chars=args.min_body_chars,
            min_published_year=args.min_published_year,
            future_grace_days=args.future_grace_days,
        )
        if classified.classification == "database":
            prepared.append(
                PreparedRecord(raw, "database", normalized=classified.normalized)
            )
        elif classified.classification == "quality_rejected":
            prepared.append(
                PreparedRecord(
                    raw,
                    "quality_rejected",
                    quality_reasons=classified.quality_reasons,
                )
            )
        else:
            prepared.append(PreparedRecord(raw, "invalid"))
    return prepared


def write_dead_letter(directory: Path, record: PreparedRecord, run_id: str) -> None:
    ensure_private_directory(directory)
    raw_digest = hashlib.sha256(record.raw.raw).hexdigest()
    record_id = hashlib.sha256(
        f"{run_id}:{record.raw.start_offset}:{record.raw.end_offset}:{raw_digest}".encode()
    ).hexdigest()
    try:
        decoded = record.raw.raw.decode("utf-8")
        raw_payload = {"encoding": "utf-8", "value": decoded}
    except UnicodeDecodeError:
        raw_payload = {"encoding": "base64", "value": base64.b64encode(record.raw.raw).decode("ascii")}
    atomic_write_json(
        directory / f"{record_id}.json",
        {
            "schema_version": 1,
            "record_id": record_id,
            "run_id": run_id,
            "start_offset": record.raw.start_offset,
            "end_offset": record.raw.end_offset,
            "raw_sha256": raw_digest,
            "reason": record.raw.error or "normalization_invalid",
            "raw": raw_payload,
        },
    )


def ensure_media_source(cur: Any, domain: str, region: str | None) -> int:
    cur.execute(
        """
        insert into public.media_source(domain, region_code)
        values (%s, %s)
        on conflict (domain)
        do update set region_code = coalesce(public.media_source.region_code, excluded.region_code)
        returning id
        """,
        (domain, region),
    )
    return int(cur.fetchone()[0])


def insert_news_row(cur: Any, row: dict[str, Any], media_source_id: int) -> bool:
    cur.execute(
        """
        insert into public.news(
            title, body, url, url_hash, published_at, media_source_id, language, region, author
        )
        values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        on conflict (url_hash) do nothing
        returning id
        """,
        (
            row.get("title"),
            row.get("body"),
            row.get("url"),
            row.get("url_hash"),
            parse_datetime(row.get("published_at")),
            media_source_id,
            row.get("language"),
            row.get("region"),
            row.get("author"),
        ),
    )
    return cur.fetchone() is not None


class CheckpointStore:
    def __init__(self, connection: Any, checkpoint_key: str) -> None:
        self.connection = connection
        self.checkpoint_key = checkpoint_key

    def configure_and_preflight(self, args: argparse.Namespace) -> None:
        with self.connection:
            with self.connection.cursor() as cur:
                cur.execute(
                    "SELECT set_config('statement_timeout', %s, false)",
                    (f"{args.statement_timeout_ms}ms",),
                )
                cur.execute(
                    "SELECT set_config('lock_timeout', %s, false)",
                    (f"{args.lock_timeout_ms}ms",),
                )
                cur.execute("SELECT to_regclass(%s) IS NOT NULL", (CHECKPOINT_TABLE,))
                row = cur.fetchone()
                if not row or row[0] is not True:
                    raise CheckpointError("authoritative checkpoint table is missing")
                cur.execute(
                    "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))",
                    (self.checkpoint_key,),
                )
                lock_row = cur.fetchone()
                if not lock_row or lock_row[0] is not True:
                    raise CheckpointError("authoritative checkpoint is already owned")

    def _from_row(self, row: Any) -> Checkpoint | None:
        if row is None:
            return None
        values = dict(zip(CHECKPOINT_COLUMNS, row))
        if int(values["schema_version"]) != CHECKPOINT_SCHEMA_VERSION:
            raise CheckpointError("unsupported database checkpoint schema")
        reasons = values["quality_skip_reasons"] or {}
        if not isinstance(reasons, dict):
            raise CheckpointError("quality_skip_reasons must be an object")
        checkpoint = Checkpoint(
            checkpoint_key=str(values["checkpoint_key"]),
            job_id=str(values["job_id"]),
            run_id=str(values["run_id"]),
            input_path=str(values["input_path"]),
            input_device=int(values["input_device"]),
            input_inode=int(values["input_inode"]),
            input_size=int(values["input_size"]),
            input_offset=int(values["input_offset"]),
            input_anchor_sha256=str(values["input_anchor_sha256"]),
            code_version=str(values["code_version"]),
            config_sha256=str(values["config_sha256"]),
            counters=Counters(
                seen=int(values["seen"]),
                inserted=int(values["inserted"]),
                duplicate=int(values["duplicate"]),
                invalid=int(values["invalid"]),
                quality_rejected=int(values["quality_rejected"]),
            ),
            quality_skip_reasons={str(key): int(value) for key, value in reasons.items()},
            legacy_seen=None if values["legacy_seen"] is None else int(values["legacy_seen"]),
            completed=bool(values["completed"]),
            sealed_final_bytes=values["sealed_final_bytes"],
            sealed_rows=values["sealed_rows"],
            sealed_sha256=values["sealed_sha256"],
            last_progress_at=values["last_progress_at"],
            updated_at=values["updated_at"],
        )
        checkpoint.validate()
        return checkpoint

    def fetch(self, cur: Any, *, for_update: bool = False) -> Checkpoint | None:
        suffix = " FOR UPDATE" if for_update else ""
        cur.execute(
            f"SELECT {CHECKPOINT_SELECT_COLUMNS} FROM {CHECKPOINT_TABLE} "
            f"WHERE checkpoint_key = %s{suffix}",
            (self.checkpoint_key,),
        )
        return self._from_row(cur.fetchone())

    def initialize(self, checkpoint: Checkpoint) -> Checkpoint:
        with self.connection:
            with self.connection.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {CHECKPOINT_TABLE} (
                        checkpoint_key, schema_version, job_id, run_id, input_path,
                        input_device, input_inode, input_size, input_offset,
                        input_anchor_sha256, code_version, config_sha256,
                        seen, inserted, duplicate, invalid, quality_rejected,
                        quality_skip_reasons
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                    ON CONFLICT (checkpoint_key) DO NOTHING
                    """,
                    (
                        checkpoint.checkpoint_key,
                        CHECKPOINT_SCHEMA_VERSION,
                        checkpoint.job_id,
                        checkpoint.run_id,
                        checkpoint.input_path,
                        checkpoint.input_device,
                        checkpoint.input_inode,
                        checkpoint.input_size,
                        checkpoint.input_offset,
                        checkpoint.input_anchor_sha256,
                        checkpoint.code_version,
                        checkpoint.config_sha256,
                        0,
                        0,
                        0,
                        0,
                        0,
                        "{}",
                    ),
                )
                stored = self.fetch(cur, for_update=True)
                if stored is None:
                    raise CheckpointError("failed to initialize authoritative checkpoint")
                return stored

    def update(self, cur: Any, expected: Checkpoint, candidate: Checkpoint) -> Checkpoint:
        candidate.validate()
        cur.execute(
            f"""
            UPDATE {CHECKPOINT_TABLE}
            SET input_size=%s, input_offset=%s, input_anchor_sha256=%s,
                seen=%s, inserted=%s, duplicate=%s, invalid=%s, quality_rejected=%s,
                quality_skip_reasons=%s::jsonb,
                completed=%s, sealed_final_bytes=%s, sealed_rows=%s, sealed_sha256=%s,
                last_progress_at=clock_timestamp(), updated_at=clock_timestamp()
            WHERE checkpoint_key=%s AND input_offset=%s AND input_anchor_sha256=%s
              AND completed=false
            RETURNING {CHECKPOINT_SELECT_COLUMNS}
            """,
            (
                candidate.input_size,
                candidate.input_offset,
                candidate.input_anchor_sha256,
                candidate.counters.seen,
                candidate.counters.inserted,
                candidate.counters.duplicate,
                candidate.counters.invalid,
                candidate.counters.quality_rejected,
                json.dumps(candidate.quality_skip_reasons, sort_keys=True),
                candidate.completed,
                candidate.sealed_final_bytes,
                candidate.sealed_rows,
                candidate.sealed_sha256,
                expected.checkpoint_key,
                expected.input_offset,
                expected.input_anchor_sha256,
            ),
        )
        updated = self._from_row(cur.fetchone())
        if updated is None:
            raise CheckpointError("authoritative checkpoint changed concurrently")
        return updated


def validate_local_mirror(path: Path, database_checkpoint: Checkpoint | None) -> None:
    if not path.exists():
        return
    state = load_json_object(path)
    if state.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise CheckpointError("legacy checkpoint requires explicit one-time migration")
    if database_checkpoint is None:
        raise CheckpointError("local checkpoint exists without an authoritative database row")
    if state.get("checkpoint_key") != database_checkpoint.checkpoint_key:
        raise CheckpointError("local checkpoint key mismatch")
    local_input = state.get("input")
    if not isinstance(local_input, dict):
        raise CheckpointError("local checkpoint input identity is missing")
    local_offset = int(local_input.get("offset", -1))
    if local_offset > database_checkpoint.input_offset:
        raise CheckpointError("local checkpoint is ahead of the authoritative database row")
    if (
        local_offset == database_checkpoint.input_offset
        and local_input.get("anchor_sha256") != database_checkpoint.input_anchor_sha256
    ):
        raise CheckpointError("local checkpoint anchor conflicts with the database row")


def validate_checkpoint_contract(
    checkpoint: Checkpoint,
    args: argparse.Namespace,
    resolved_code_version: str,
    resolved_config_sha256: str,
) -> hashlib._Hash:
    checkpoint.validate()
    expected = {
        "checkpoint_key": args.checkpoint_key,
        "job_id": args.job_id,
        "run_id": args.run_id,
        "input_path": str(args.input.resolve(strict=True)),
        "code_version": resolved_code_version,
        "config_sha256": resolved_config_sha256,
    }
    for field, value in expected.items():
        if getattr(checkpoint, field) != value:
            raise CheckpointError(f"checkpoint {field} mismatch; explicit migration is required")
    fingerprint = fingerprint_input(args.input, offset=checkpoint.input_offset)
    if (fingerprint.device, fingerprint.inode) != (
        checkpoint.input_device,
        checkpoint.input_inode,
    ):
        raise CheckpointError("input device/inode changed")
    if fingerprint.size < checkpoint.input_size:
        raise CheckpointError("input size regressed; truncation is forbidden")
    if fingerprint.anchor_sha256 != checkpoint.input_anchor_sha256:
        raise CheckpointError("input prefix anchor mismatch")
    hasher = hashlib.sha256()
    with args.input.open("rb") as handle:
        remaining = checkpoint.input_offset
        while remaining:
            block = handle.read(min(1024 * 1024, remaining))
            if not block:
                raise CheckpointError("input truncated while reconstructing prefix hash")
            hasher.update(block)
            remaining -= len(block)
    if hasher.hexdigest() != checkpoint.input_anchor_sha256:
        raise CheckpointError("input prefix changed while reconstructing its anchor")
    return hasher


def load_seal(path: Path, checkpoint: Checkpoint) -> dict[str, Any] | None:
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CheckpointError("sealed manifest path is unavailable") from exc
    try:
        validate_private_file(path)
        manifest = load_json_object(path)
    except SafetyError as exc:
        raise CheckpointError("sealed manifest file is invalid") from exc
    if manifest.get("schema_version") != 1 or manifest.get("sealed") is not True:
        raise CheckpointError("sealed manifest schema is invalid")
    if manifest.get("job_id") != checkpoint.job_id or manifest.get("run_id") != checkpoint.run_id:
        raise CheckpointError("sealed manifest job/run mismatch")
    input_data = manifest.get("input")
    if not isinstance(input_data, dict):
        raise CheckpointError("sealed manifest input is missing")
    for field, expected in (
        ("canonical_path", checkpoint.input_path),
        ("device", checkpoint.input_device),
        ("inode", checkpoint.input_inode),
    ):
        if input_data.get(field) != expected:
            raise CheckpointError(f"sealed manifest {field} mismatch")
    final_bytes = int(input_data.get("final_bytes", -1))
    rows = int(input_data.get("rows", -1))
    digest = str(input_data.get("sha256") or "")
    if final_bytes < checkpoint.input_offset or rows < checkpoint.counters.seen:
        raise CheckpointError("sealed manifest is behind the authoritative checkpoint")
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise CheckpointError("sealed manifest digest is invalid")
    metadata = args_stat(checkpoint.input_path)
    if (metadata.st_dev, metadata.st_ino, metadata.st_size) != (
        checkpoint.input_device,
        checkpoint.input_inode,
        final_bytes,
    ):
        raise CheckpointError("sealed input identity or final size mismatch")
    return manifest


def args_stat(canonical_path: str) -> os.stat_result:
    path = Path(canonical_path)
    if path.is_symlink():
        raise CheckpointError("sealed input path is a symlink")
    return path.stat()


def write_readiness(
    path: Path,
    instance_id: str,
    status: str,
    control_socket: dict[str, Any] | None,
) -> None:
    atomic_write_json(
        path,
        {
            "schema_version": RUNTIME_IDENTITY_SCHEMA_VERSION,
            "status": status,
            "instance_id": instance_id,
            "identity": capture_process_identity(os.getpid()),
            "control_socket": control_socket,
            "updated_at": time.time(),
        },
    )


def write_heartbeat(path: Path, checkpoint: Checkpoint, status: str) -> None:
    atomic_write_json(
        path,
        {
            "schema_version": 2,
            "status": status,
            "heartbeat_at": time.time(),
            "last_progress_at": _json_time(checkpoint.last_progress_at),
            "checkpoint_key": checkpoint.checkpoint_key,
            "offset": checkpoint.input_offset,
            "seen": checkpoint.counters.seen,
        },
    )


def initial_checkpoint(
    args: argparse.Namespace,
    resolved_code_version: str,
    resolved_config_sha256: str,
) -> Checkpoint:
    fingerprint = fingerprint_input(args.input, offset=0)
    return Checkpoint(
        checkpoint_key=args.checkpoint_key,
        job_id=args.job_id,
        run_id=args.run_id,
        input_path=fingerprint.canonical_path,
        input_device=fingerprint.device,
        input_inode=fingerprint.inode,
        input_size=fingerprint.size,
        input_offset=0,
        input_anchor_sha256=EMPTY_SHA256,
        code_version=resolved_code_version,
        config_sha256=resolved_config_sha256,
        counters=Counters(),
        quality_skip_reasons={},
    )


def advance_batch(
    connection: Any,
    store: CheckpointStore,
    checkpoint: Checkpoint,
    prepared: list[PreparedRecord],
    prefix_hasher: Any,
    args: argparse.Namespace,
) -> tuple[Checkpoint, Any]:
    candidate_hasher = prefix_hasher.copy()
    counters = checkpoint.counters
    reasons = Counter(checkpoint.quality_skip_reasons)
    media_cache: dict[str, int] = {}

    metadata = args_stat(checkpoint.input_path)
    if (metadata.st_dev, metadata.st_ino) != (checkpoint.input_device, checkpoint.input_inode):
        raise CheckpointError("input identity changed before commit")
    if metadata.st_size < checkpoint.input_size or metadata.st_size < prepared[-1].raw.end_offset:
        raise CheckpointError("input was truncated before commit")

    with connection:
        with connection.cursor() as cur:
            locked = store.fetch(cur, for_update=True)
            if locked is None or (
                locked.input_offset != checkpoint.input_offset
                or locked.input_anchor_sha256 != checkpoint.input_anchor_sha256
            ):
                raise CheckpointError("authoritative checkpoint changed before batch commit")

            seen = counters.seen
            inserted = counters.inserted
            duplicate = counters.duplicate
            invalid = counters.invalid
            quality_rejected = counters.quality_rejected
            consumed: list[PreparedRecord] = []
            for record in prepared:
                if STOP_REQUESTED and consumed:
                    break
                candidate_hasher.update(record.raw.raw)
                consumed.append(record)
                seen += 1
                if record.classification == "invalid":
                    write_dead_letter(args.dead_letter_dir, record, args.run_id)
                    invalid += 1
                    continue
                if record.classification == "quality_rejected":
                    quality_rejected += 1
                    reasons.update(record.quality_reasons)
                    continue
                if record.normalized is None:
                    raise CheckpointError("database record is missing normalized data")
                domain = str(record.normalized["media_source_domain"])
                media_source_id = media_cache.get(domain)
                if media_source_id is None:
                    media_source_id = ensure_media_source(
                        cur,
                        domain,
                        record.normalized.get("region"),
                    )
                    media_cache[domain] = media_source_id
                if insert_news_row(cur, record.normalized, media_source_id):
                    inserted += 1
                else:
                    duplicate += 1

            if not consumed:
                return checkpoint, prefix_hasher

            candidate = replace(
                checkpoint,
                input_size=int(metadata.st_size),
                input_offset=consumed[-1].raw.end_offset,
                input_anchor_sha256=candidate_hasher.hexdigest(),
                counters=Counters(seen, inserted, duplicate, invalid, quality_rejected),
                quality_skip_reasons=dict(reasons),
            )
            committed = store.update(cur, checkpoint, candidate)
    return committed, candidate_hasher


def mark_sealed_complete(
    connection: Any,
    store: CheckpointStore,
    checkpoint: Checkpoint,
    manifest: dict[str, Any],
) -> Checkpoint:
    input_data = manifest["input"]
    final_bytes = int(input_data["final_bytes"])
    rows = int(input_data["rows"])
    digest = str(input_data["sha256"])
    if checkpoint.input_offset != final_bytes:
        raise CheckpointError("cannot complete before consuming the exact sealed final offset")
    if checkpoint.counters.seen != rows or checkpoint.input_anchor_sha256 != digest:
        raise CheckpointError("authoritative checkpoint does not match the sealed content")
    candidate = replace(
        checkpoint,
        completed=True,
        sealed_final_bytes=final_bytes,
        sealed_rows=rows,
        sealed_sha256=digest,
    )
    with connection:
        with connection.cursor() as cur:
            locked = store.fetch(cur, for_update=True)
            if locked is None or (
                locked.input_offset != checkpoint.input_offset
                or locked.input_anchor_sha256 != checkpoint.input_anchor_sha256
            ):
                raise CheckpointError("checkpoint changed before sealed completion")
            return store.update(cur, checkpoint, candidate)


def run(args: argparse.Namespace) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = False
    validate_args(args)
    instance_id = (os.getenv("GLOBEMIND_LOADER_INSTANCE_ID") or "").strip()
    validate_safe_identifier("GLOBEMIND_LOADER_INSTANCE_ID", instance_id)
    password = validate_database_secret()
    ensure_private_directory(args.dead_letter_dir)
    resolved_code_version = code_version(args)
    resolved_config_sha256 = config_sha256(args, resolved_code_version)
    source_map = load_source_map(args.source_map)

    connection = psycopg2.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=password,
        dbname=args.dbname,
        sslmode=args.sslmode,
        connect_timeout=args.connect_timeout_sec,
        application_name="globemind-wave1-loader-v2",
    )
    store = CheckpointStore(connection, args.checkpoint_key)
    failure = False
    checkpoint: Checkpoint | None = None
    control_server: ControlServer | None = None
    control_socket_identity: dict[str, Any] | None = None
    try:
        store.configure_and_preflight(args)
        with connection:
            with connection.cursor() as cur:
                checkpoint = store.fetch(cur)
        validate_local_mirror(args.state_path, checkpoint)
        if checkpoint is None:
            if args.state_path.exists():
                raise CheckpointError("state migration is required before database initialization")
            checkpoint = store.initialize(
                initial_checkpoint(args, resolved_code_version, resolved_config_sha256)
            )
        prefix_hasher = validate_checkpoint_contract(
            checkpoint,
            args,
            resolved_code_version,
            resolved_config_sha256,
        )
        if checkpoint.completed:
            manifest = load_seal(args.sealed_manifest, checkpoint)
            if manifest is None:
                raise CheckpointError("completed checkpoint has no sealed manifest")
            raise CheckpointError("authoritative checkpoint is already complete")
        atomic_write_json(args.state_path, checkpoint.mirror())
        write_heartbeat(args.heartbeat_path, checkpoint, "ready")
        control_server = ControlServer(args.control_socket, instance_id)
        control_socket_identity = control_server.start()
        write_readiness(
            args.ready_path,
            instance_id,
            "ready",
            control_socket_identity,
        )

        last_heartbeat = time.monotonic()
        while not STOP_REQUESTED:
            control_server.ensure_healthy()
            records = read_records(args.input, checkpoint.input_offset, args.batch_size, checkpoint)
            prepared = prepare_records(records, source_map, args)
            if prepared:
                checkpoint, prefix_hasher = advance_batch(
                    connection,
                    store,
                    checkpoint,
                    prepared,
                    prefix_hasher,
                    args,
                )
                atomic_write_json(args.state_path, checkpoint.mirror())
                write_heartbeat(args.heartbeat_path, checkpoint, "running")
                last_heartbeat = time.monotonic()
                continue

            manifest = load_seal(args.sealed_manifest, checkpoint)
            if manifest is not None:
                final_bytes = int(manifest["input"]["final_bytes"])
                if checkpoint.input_offset < final_bytes:
                    raise CheckpointError("sealed input contains an incomplete record before final offset")
                checkpoint = mark_sealed_complete(connection, store, checkpoint, manifest)
                atomic_write_json(args.state_path, checkpoint.mirror())
                write_heartbeat(args.heartbeat_path, checkpoint, "completed")
                sleep_interruptibly(args.completion_grace_sec)
                break

            now = time.monotonic()
            if now - last_heartbeat >= args.heartbeat_sec:
                write_heartbeat(args.heartbeat_path, checkpoint, "idle")
                last_heartbeat = now
            sleep_interruptibly(args.poll_sec)
    except BaseException:
        failure = True
        try:
            write_readiness(
                args.ready_path,
                instance_id,
                "failed",
                control_socket_identity,
            )
        except (OSError, SafetyError):
            pass
        raise
    finally:
        if control_server is not None:
            try:
                write_readiness(
                    args.ready_path,
                    instance_id,
                    "failed" if failure else "stopped",
                    control_socket_identity,
                )
            except (OSError, SafetyError):
                pass
            control_server.close()
        connection.close()


def main() -> None:
    install_signal_handlers()
    run(parse_args())


if __name__ == "__main__":
    main()
