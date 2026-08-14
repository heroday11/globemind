#!/usr/bin/env python3
"""Offline migration, sealing, and runtime-identity helpers for the Wave1 loader.

No command in this module connects to PostgreSQL. Database changes are emitted as
reviewable SQL and must be applied separately during an approved maintenance step.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import socket
import stat
import struct
import sys
import tempfile
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

CHECKPOINT_SCHEMA_VERSION = 2
RUNTIME_IDENTITY_SCHEMA_VERSION = 2
LEGACY_AUDIT_SCHEMA_VERSION = 4
LEGACY_WALL_CLOCK_RECONCILIATION_REASON = "published_future_too_far"
CHECKPOINT_TABLE = "public.globemind_pipeline_checkpoint"
SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._:/-]+$")
PASSWORD_ENV_NAMES = (
    "L1_DB_PASSWORD",
    "PG_WRITE_PASSWORD",
    "DB_PASSWORD",
    "PG_PASSWORD",
    "PGPASSWORD",
    "DATABASE_URL",
    "SQLALCHEMY_DATABASE_URL",
)

CHECKPOINT_DDL = f"""\
BEGIN;
CREATE TABLE IF NOT EXISTS {CHECKPOINT_TABLE} (
    checkpoint_key text PRIMARY KEY,
    schema_version smallint NOT NULL CHECK (schema_version = 2),
    job_id text NOT NULL,
    run_id text NOT NULL,
    input_path text NOT NULL,
    input_device bigint NOT NULL,
    input_inode bigint NOT NULL,
    input_size bigint NOT NULL CHECK (input_size >= 0),
    input_offset bigint NOT NULL CHECK (input_offset >= 0 AND input_offset <= input_size),
    input_anchor_sha256 char(64) NOT NULL,
    code_version text NOT NULL,
    config_sha256 char(64) NOT NULL,
    seen bigint NOT NULL DEFAULT 0 CHECK (seen >= 0),
    legacy_seen bigint CHECK (legacy_seen IS NULL OR (legacy_seen >= 0 AND legacy_seen <= seen)),
    inserted bigint NOT NULL DEFAULT 0 CHECK (inserted >= 0),
    duplicate bigint NOT NULL DEFAULT 0 CHECK (duplicate >= 0),
    invalid bigint NOT NULL DEFAULT 0 CHECK (invalid >= 0),
    quality_rejected bigint NOT NULL DEFAULT 0 CHECK (quality_rejected >= 0),
    quality_skip_reasons jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    completed boolean NOT NULL DEFAULT false,
    sealed_final_bytes bigint,
    sealed_rows bigint,
    sealed_sha256 char(64),
    last_progress_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT globemind_pipeline_checkpoint_counter_invariant CHECK (
        seen = inserted + duplicate + invalid + quality_rejected
    ),
    CONSTRAINT globemind_pipeline_checkpoint_seal_invariant CHECK (
        (completed = false AND sealed_final_bytes IS NULL AND sealed_rows IS NULL AND sealed_sha256 IS NULL)
        OR
        (completed = true AND sealed_final_bytes = input_offset AND sealed_rows = seen AND sealed_sha256 = input_anchor_sha256)
    )
);
ALTER TABLE {CHECKPOINT_TABLE}
    ADD COLUMN IF NOT EXISTS legacy_seen bigint
    CHECK (legacy_seen IS NULL OR (legacy_seen >= 0 AND legacy_seen <= seen));
COMMIT;
"""


class SafetyError(RuntimeError):
    pass


@dataclass(frozen=True)
class InputFingerprint:
    canonical_path: str
    device: int
    inode: int
    size: int
    offset: int
    anchor_sha256: str
    rows: int


@dataclass(frozen=True)
class NewsRecordClassification:
    classification: str
    normalized: dict[str, Any] | None = None
    quality_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class LegacyCheckpointEvidence:
    offset: int
    seen: int
    inserted: int
    skipped: int


@dataclass(frozen=True)
class LegacyQualityGateTransition:
    before: LegacyCheckpointEvidence
    after: LegacyCheckpointEvidence
    after_quality_skipped: int
    after_quality_reasons: dict[str, int]
    provenance_path: str
    provenance_sha256: str


def atomic_write_bytes(path: Path, payload: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        directory_descriptor = os.open(
            path.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_bytes(
        path,
        (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
    )


def atomic_write_once(path: Path, payload: bytes) -> None:
    if path.exists() or path.is_symlink():
        validate_private_file(path)
        if path.read_bytes() != payload:
            raise SafetyError(f"immutable output already exists with different content: {path}")
        return
    atomic_write_bytes(path, payload)


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SafetyError(f"invalid JSON file: {path}") from exc
    if not isinstance(value, dict):
        raise SafetyError(f"JSON root must be an object: {path}")
    return value


def validate_safe_identifier(name: str, value: str) -> str:
    if not value or not SAFE_IDENTIFIER_RE.fullmatch(value):
        raise SafetyError(f"{name} contains unsupported characters")
    return value


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _audit_quality_now_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise SafetyError("audit quality clock must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def _parse_audit_quality_now(value: Any) -> datetime:
    text = str(value or "")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SafetyError("audit quality clock is invalid") from exc
    if parsed.tzinfo is None:
        raise SafetyError("audit quality clock must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_loader_code_version(label: str) -> str:
    scripts_dir = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for name in (
        "stream_load_news_to_postgres.py",
        "wave1_loader_migrate.py",
        "prepare_news_table_rows.py",
        "news_ingest_quality.py",
        "news_date_cleaning.py",
    ):
        path = scripts_dir / name
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256_file(path).encode("ascii"))
        digest.update(b"\0")
    resolved = digest.hexdigest()
    return f"{label}:{resolved}" if label else resolved


def classify_news_value(
    value: dict[str, Any],
    source_map: dict[str, Any],
    *,
    disable_quality_gate: bool,
    min_body_chars: int,
    min_published_year: int,
    future_grace_days: int,
    fail_on_exception: bool = False,
    quality_now: datetime | None = None,
) -> NewsRecordClassification:
    # Keep runtime-control subcommands usable from a minimal system Python.
    from news_date_cleaning import clean_published_at
    from news_ingest_quality import assess_news_row
    from prepare_news_table_rows import build_news_row

    try:
        normalized = build_news_row(
            value,
            source_map.get(str(value.get("site_id") or "")),
        )
        date_result = clean_published_at(
            {
                **value,
                **normalized,
                "request_url": value.get("request_url") or value.get("url"),
                "response_url": value.get("response_url") or normalized.get("url"),
            },
            now=quality_now,
        )
        normalized["published_at"] = date_result.isoformat() or None
        domain = str(normalized.get("media_source_domain") or "").strip()
        if not domain or not normalized.get("title") or not normalized.get("url_hash"):
            return NewsRecordClassification("invalid")
        if not disable_quality_gate:
            quality = assess_news_row(
                normalized,
                now=quality_now,
                min_body_chars=min_body_chars,
                min_published_year=min_published_year,
                future_grace_days=future_grace_days,
            )
            if not quality.is_good:
                return NewsRecordClassification(
                    "quality_rejected",
                    normalized=normalized,
                    quality_reasons=quality.reasons,
                )
        return NewsRecordClassification("database", normalized=normalized)
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        if fail_on_exception:
            raise SafetyError(
                "legacy normalization raised an exception that the legacy loader could not persist"
            ) from exc
        return NewsRecordClassification("invalid")


def resolve_loader_config_sha256(
    *,
    input_path: Path,
    source_map_path: Path,
    checkpoint_key: str,
    job_id: str,
    run_id: str,
    resolved_code_version: str,
    disable_quality_gate: bool,
    min_body_chars: int,
    min_published_year: int,
    future_grace_days: int,
) -> str:
    source_map = source_map_path.resolve(strict=True)
    payload = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "classification_version": 1,
        "job_id": job_id,
        "run_id": run_id,
        "checkpoint_key": checkpoint_key,
        "input": str(input_path.resolve(strict=True)),
        "source_map": str(source_map),
        "source_map_sha256": _sha256_file(source_map),
        "quality_gate": not disable_quality_gate,
        "quality_clock": "wall_clock",
        "min_body_chars": min_body_chars,
        "min_published_year": min_published_year,
        "future_grace_days": future_grace_days,
        "code_version": resolved_code_version,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def validate_private_file(path: Path, *, expected_owner: int | None = None) -> Path:
    expected_owner = os.geteuid() if expected_owner is None else expected_owner
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise SafetyError(f"private file is unavailable: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise SafetyError(f"private file must be a non-symlink regular file: {path}")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise SafetyError(f"private file must have mode 0600: {path}")
    if metadata.st_uid != expected_owner:
        raise SafetyError(f"private file owner mismatch: {path}")
    return path


def validate_owned_regular_file(path: Path, *, expected_owner: int | None = None) -> Path:
    expected_owner = os.geteuid() if expected_owner is None else expected_owner
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise SafetyError(f"file is unavailable: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise SafetyError(f"file must be a non-symlink regular file: {path}")
    if metadata.st_uid != expected_owner:
        raise SafetyError(f"file owner mismatch: {path}")
    return path


def _open_regular_nofollow(path: Path) -> tuple[int, Path]:
    if path.is_symlink():
        raise SafetyError(f"input must not be a symlink: {path}")
    canonical = path.resolve(strict=True)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(canonical, flags)
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        raise SafetyError(f"input must be a regular file: {canonical}")
    return descriptor, canonical


def _fingerprint_input_with_anchors(
    path: Path,
    *,
    offset: int | None = None,
    require_stable_size: bool = False,
    anchor_offsets: Iterable[int] = (),
    capture_ranges: Iterable[tuple[int, int]] = (),
) -> tuple[
    InputFingerprint,
    dict[int, dict[str, Any]],
    dict[tuple[int, int], dict[str, Any]],
]:
    descriptor, canonical = _open_regular_nofollow(path)
    try:
        before = os.fstat(descriptor)
        limit = before.st_size if offset is None else offset
        if limit < 0 or limit > before.st_size:
            raise SafetyError("input offset is outside the current file")
        targets = sorted(set(int(value) for value in anchor_offsets))
        if any(value < 0 or value > limit for value in targets):
            raise SafetyError("input anchor offset is outside the fingerprinted prefix")
        ranges = sorted(set((int(start), int(end)) for start, end in capture_ranges))
        if any(start < 0 or start > end or end > limit for start, end in ranges):
            raise SafetyError("input capture range is outside the fingerprinted prefix")
        range_digests = {bounds: hashlib.sha256() for bounds in ranges}
        range_bytes = {bounds: 0 for bounds in ranges}
        range_lines = {bounds: 0 for bounds in ranges}
        digest = hashlib.sha256()
        rows = 0
        remaining = limit
        last_byte = b""
        position = 0
        captured = (
            {0: {"anchor_sha256": digest.hexdigest(), "complete_lines": 0}}
            if 0 in targets
            else {}
        )
        while remaining:
            pending = next((target for target in targets if target > position), None)
            block_size = min(1024 * 1024, remaining)
            if pending is not None:
                block_size = min(block_size, pending - position)
            block = os.read(descriptor, block_size)
            if not block:
                raise SafetyError("input was truncated while hashing")
            digest.update(block)
            block_start = position
            block_end = position + len(block)
            for bounds in ranges:
                start, end = bounds
                overlap_start = max(start, block_start)
                overlap_end = min(end, block_end)
                if overlap_start >= overlap_end:
                    continue
                payload = block[overlap_start - block_start : overlap_end - block_start]
                range_digests[bounds].update(payload)
                range_bytes[bounds] += len(payload)
                range_lines[bounds] += payload.count(b"\n")
            rows += block.count(b"\n")
            last_byte = block[-1:]
            remaining -= len(block)
            position += len(block)
            if position in targets:
                if last_byte != b"\n":
                    raise SafetyError("input anchor is not on a complete JSONL record boundary")
                captured[position] = {
                    "anchor_sha256": digest.hexdigest(),
                    "complete_lines": rows,
                }
        if set(captured) != set(targets):
            raise SafetyError("not every requested input anchor was captured")
        for start, end in ranges:
            if range_bytes[(start, end)] != end - start:
                raise SafetyError("not every requested input range was captured")
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise SafetyError("input identity changed while hashing")
        try:
            path_after = os.stat(canonical, follow_symlinks=False)
        except OSError as exc:
            raise SafetyError("input path changed while hashing") from exc
        if (
            not stat.S_ISREG(path_after.st_mode)
            or (path_after.st_dev, path_after.st_ino) != (after.st_dev, after.st_ino)
        ):
            raise SafetyError("input path was replaced while hashing")
        if after.st_size < limit:
            raise SafetyError("input was truncated while hashing")
        if require_stable_size and (
            before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
        ):
            raise SafetyError("input changed while producing a stable fingerprint")
        if limit and last_byte != b"\n":
            raise SafetyError("checkpoint offset is not on a complete JSONL record boundary")
        captured_ranges = {
            bounds: {
                "sha256": range_digests[bounds].hexdigest(),
                "bytes": range_bytes[bounds],
                "complete_lines": range_lines[bounds],
            }
            for bounds in ranges
        }
        return (
            InputFingerprint(
                canonical_path=str(canonical),
                device=int(after.st_dev),
                inode=int(after.st_ino),
                size=int(after.st_size),
                offset=int(limit),
                anchor_sha256=digest.hexdigest(),
                rows=rows,
            ),
            captured,
            captured_ranges,
        )
    finally:
        os.close(descriptor)


def fingerprint_input(
    path: Path,
    *,
    offset: int | None = None,
    require_stable_size: bool = False,
) -> InputFingerprint:
    fingerprint, _anchors, _ranges = _fingerprint_input_with_anchors(
        path,
        offset=offset,
        require_stable_size=require_stable_size,
    )
    return fingerprint


def _read_proc_stat(proc_root: Path, pid: int) -> tuple[str, list[str]]:
    try:
        line = (proc_root / str(pid) / "stat").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SafetyError("process stat is unavailable") from exc
    if ") " not in line:
        raise SafetyError("process stat is malformed")
    prefix, tail = line.rsplit(") ", 1)
    if " (" not in prefix:
        raise SafetyError("process stat is malformed")
    fields = tail.split()
    if len(fields) < 20:
        raise SafetyError("process stat is incomplete")
    return fields[0], fields


def require_process_stopped(pid: int, *, proc_root: Path = Path("/proc")) -> None:
    state, _fields = _read_proc_stat(proc_root, pid)
    if state not in {"T", "t"}:
        raise SafetyError("legacy process is not in the required stopped state")


def capture_process_identity(pid: int, *, proc_root: Path = Path("/proc")) -> dict[str, Any]:
    if pid <= 0:
        raise SafetyError("PID must be positive")
    state, fields = _read_proc_stat(proc_root, pid)
    if state in {"Z", "X", "x"}:
        raise SafetyError("process is not alive")
    process_root = proc_root / str(pid)
    try:
        boot_id = (proc_root / "sys" / "kernel" / "random" / "boot_id").read_text().strip()
        pid_namespace = os.readlink(process_root / "ns" / "pid")
        executable = str((process_root / "exe").resolve(strict=True))
        cwd = str((process_root / "cwd").resolve(strict=True))
    except OSError as exc:
        raise SafetyError("extended process identity is unavailable") from exc
    return {
        "pid": pid,
        "start_ticks": int(fields[19]),
        "boot_id": boot_id,
        "pid_namespace": pid_namespace,
        "exe": executable,
        "cwd": cwd,
        "pgid": int(fields[2]),
        "sid": int(fields[3]),
    }


def identities_match(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    keys = ("pid", "start_ticks", "boot_id", "pid_namespace", "exe", "cwd", "pgid", "sid")
    return all(expected.get(key) == actual.get(key) for key in keys)


def inspect_control_socket(path: Path, *, expected_owner: int | None = None) -> dict[str, Any]:
    expected_owner = os.geteuid() if expected_owner is None else expected_owner
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise SafetyError("control socket is unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISSOCK(metadata.st_mode):
        raise SafetyError("control socket must be a non-symlink UNIX socket")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise SafetyError("control socket must have mode 0600")
    if metadata.st_uid != expected_owner:
        raise SafetyError("control socket owner mismatch")
    return {
        "path": str(path.resolve(strict=True)),
        "device": int(metadata.st_dev),
        "inode": int(metadata.st_ino),
        "owner": int(metadata.st_uid),
        "mode": stat.S_IMODE(metadata.st_mode),
    }


def _socket_peer_credentials(connection: socket.socket) -> tuple[int, int, int]:
    option = getattr(socket, "SO_PEERCRED", None)
    if option is None:
        raise SafetyError("SO_PEERCRED is unavailable")
    raw = connection.getsockopt(socket.SOL_SOCKET, option, struct.calcsize("3i"))
    return struct.unpack("3i", raw)


def socket_control(
    meta_path: Path,
    socket_path: Path,
    command: str,
    *,
    timeout: float = 2.0,
    proc_root: Path = Path("/proc"),
) -> dict[str, Any]:
    if command not in {"status", "stop"}:
        raise SafetyError("unsupported control socket command")
    meta = verify_runtime_identity(meta_path, proc_root=proc_root)
    identity = meta["identity"]
    socket_identity = inspect_control_socket(socket_path)
    declared_socket = meta.get("control_socket")
    if declared_socket is not None and declared_socket != socket_identity:
        raise SafetyError("control socket identity changed")
    request = {
        "schema_version": 1,
        "command": command,
        "instance_id": meta["instance_id"],
        "boot_id": identity["boot_id"],
    }
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(socket_path))
        peer_pid, peer_uid, _peer_gid = _socket_peer_credentials(client)
        if peer_uid != os.geteuid() or peer_pid != identity["pid"]:
            raise SafetyError("control socket peer credentials do not match runtime identity")
        client.sendall(json.dumps(request, sort_keys=True).encode("utf-8") + b"\n")
        response_bytes = b""
        while b"\n" not in response_bytes and len(response_bytes) <= 8192:
            block = client.recv(4096)
            if not block:
                break
            response_bytes += block
    except (OSError, TimeoutError) as exc:
        raise SafetyError("control socket request failed") from exc
    finally:
        client.close()
    if len(response_bytes) > 8192:
        raise SafetyError("control socket response is too large")
    try:
        response = json.loads(response_bytes.split(b"\n", 1)[0].decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SafetyError("control socket response is invalid") from exc
    if not isinstance(response, dict) or response.get("ok") is not True:
        raise SafetyError("control socket refused the request")
    if response.get("instance_id") != meta["instance_id"]:
        raise SafetyError("control socket response instance mismatch")
    return response


def attach_runtime_socket(
    meta_path: Path,
    ready_path: Path,
    socket_path: Path,
    *,
    proc_root: Path = Path("/proc"),
) -> dict[str, Any]:
    meta = verify_runtime_identity(meta_path, proc_root=proc_root)
    ready = load_json_object(ready_path)
    declared = ready.get("control_socket")
    actual = inspect_control_socket(socket_path)
    if declared != actual:
        raise SafetyError("readiness control socket identity mismatch")
    socket_control(meta_path, socket_path, "status", proc_root=proc_root)
    meta["control_socket"] = actual
    atomic_write_json(meta_path, meta)
    return meta


def write_runtime_meta(path: Path, *, pid: int, instance_name: str, instance_id: str) -> dict[str, Any]:
    validate_safe_identifier("instance_name", instance_name)
    validate_safe_identifier("instance_id", instance_id)
    identity = capture_process_identity(pid)
    payload = {
        "schema_version": RUNTIME_IDENTITY_SCHEMA_VERSION,
        "instance_name": instance_name,
        "instance_id": instance_id,
        "identity": identity,
        "created_at": time.time(),
    }
    atomic_write_json(path, payload)
    return payload


def verify_runtime_identity(
    meta_path: Path,
    ready_path: Path | None = None,
    *,
    proc_root: Path = Path("/proc"),
) -> dict[str, Any]:
    meta = load_json_object(meta_path)
    if meta.get("schema_version") != RUNTIME_IDENTITY_SCHEMA_VERSION:
        raise SafetyError("unsupported runtime metadata schema")
    identity = meta.get("identity")
    if not isinstance(identity, dict):
        raise SafetyError("runtime metadata identity is missing")
    actual = capture_process_identity(int(identity.get("pid", 0)), proc_root=proc_root)
    if not identities_match(identity, actual):
        raise SafetyError("runtime process identity mismatch")
    if ready_path is not None:
        ready = load_json_object(ready_path)
        if ready.get("schema_version") != RUNTIME_IDENTITY_SCHEMA_VERSION:
            raise SafetyError("unsupported readiness schema")
        if ready.get("instance_id") != meta.get("instance_id"):
            raise SafetyError("readiness instance mismatch")
        ready_identity = ready.get("identity")
        if not isinstance(ready_identity, dict) or not identities_match(identity, ready_identity):
            raise SafetyError("readiness process identity mismatch")
        if ready.get("status") not in {"ready", "stopping", "stopped", "failed"}:
            raise SafetyError("readiness status is invalid")
        declared_socket = meta.get("control_socket")
        ready_socket = ready.get("control_socket")
        if not isinstance(declared_socket, dict) or ready_socket != declared_socket:
            raise SafetyError("runtime control socket metadata is missing or mismatched")
        if inspect_control_socket(Path(str(declared_socket.get("path") or ""))) != declared_socket:
            raise SafetyError("runtime control socket identity changed")
        meta["ready_status"] = ready.get("status")
    return meta


def runtime_identity_is_dead(meta_path: Path, *, proc_root: Path = Path("/proc")) -> bool:
    meta = load_json_object(meta_path)
    if meta.get("schema_version") != RUNTIME_IDENTITY_SCHEMA_VERSION:
        raise SafetyError("unsupported runtime metadata schema")
    expected = meta.get("identity")
    if not isinstance(expected, dict):
        raise SafetyError("runtime metadata identity is missing")
    pid = int(expected.get("pid", 0))
    stat_path = proc_root / str(pid) / "stat"
    if not stat_path.exists():
        return True
    state, _fields = _read_proc_stat(proc_root, pid)
    if state in {"Z", "X", "x"}:
        return True
    actual = capture_process_identity(pid, proc_root=proc_root)
    core_keys = ("pid", "start_ticks", "boot_id", "pid_namespace")
    return any(expected.get(key) != actual.get(key) for key in core_keys)


def pidfd_send(
    meta_path: Path,
    signal_number: int,
    *,
    proc_root: Path = Path("/proc"),
    pidfd_open: Any = None,
    pidfd_send_signal: Any = None,
) -> None:
    meta = load_json_object(meta_path)
    identity = meta.get("identity")
    if not isinstance(identity, dict):
        raise SafetyError("runtime metadata identity is missing")
    pid = int(identity.get("pid", 0))
    opener = pidfd_open or getattr(os, "pidfd_open", None)
    sender = pidfd_send_signal or getattr(signal, "pidfd_send_signal", None)
    if opener is None or sender is None:
        raise SafetyError("pidfd signaling is unavailable")
    descriptor = opener(pid, 0)
    try:
        actual = capture_process_identity(pid, proc_root=proc_root)
        if not identities_match(identity, actual):
            raise SafetyError("runtime process identity changed before pidfd signal")
        sender(descriptor, signal_number, None, 0)
    finally:
        os.close(descriptor)


def check_pidfd_support() -> None:
    opener = getattr(os, "pidfd_open", None)
    sender = getattr(signal, "pidfd_send_signal", None)
    if opener is None or sender is None:
        raise SafetyError("control Python does not expose pidfd APIs")
    try:
        descriptor = opener(os.getpid(), 0)
    except OSError as exc:
        raise SafetyError(f"kernel pidfd_open unavailable (errno={exc.errno})") from exc
    else:
        os.close(descriptor)


def materialize_database_secret(output: Path) -> None:
    from db_runtime_config import DEFAULT_ENV_FILES, PASSWORD_NAMES, require_database_password

    configured_file = (os.getenv("GLOBEMIND_DB_PASSWORD_FILE") or "").strip()
    direct_environment = any((os.getenv(name) or "").strip() for name in PASSWORD_NAMES)
    if configured_file:
        validate_private_file(Path(configured_file))
    elif not direct_environment:
        for candidate in DEFAULT_ENV_FILES:
            if candidate.exists():
                validate_private_file(candidate)
    password = require_database_password()
    if not password:
        raise SafetyError("database password is empty")
    atomic_write_bytes(output, (password + "\n").encode("utf-8"))


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _checkpoint_values(checkpoint: dict[str, Any]) -> list[Any]:
    return [
        checkpoint["checkpoint_key"],
        CHECKPOINT_SCHEMA_VERSION,
        checkpoint["job_id"],
        checkpoint["run_id"],
        checkpoint["input"]["canonical_path"],
        checkpoint["input"]["device"],
        checkpoint["input"]["inode"],
        checkpoint["input"]["size"],
        checkpoint["input"]["offset"],
        checkpoint["input"]["anchor_sha256"],
        checkpoint["code_version"],
        checkpoint["config_sha256"],
        checkpoint["counters"]["seen"],
        checkpoint.get("legacy_seen"),
        checkpoint["counters"]["inserted"],
        checkpoint["counters"]["duplicate"],
        checkpoint["counters"]["invalid"],
        checkpoint["counters"]["quality_rejected"],
        json.dumps(checkpoint.get("quality_skip_reasons", {}), sort_keys=True),
    ]


def render_checkpoint_seed_sql(checkpoint: dict[str, Any]) -> str:
    columns = (
        "checkpoint_key, schema_version, job_id, run_id, input_path, input_device, input_inode, "
        "input_size, input_offset, input_anchor_sha256, code_version, config_sha256, seen, legacy_seen, "
        "inserted, duplicate, invalid, quality_rejected, quality_skip_reasons"
    )
    sql_values = [_sql_literal(value) for value in _checkpoint_values(checkpoint)]
    sql_values[-1] += "::jsonb"
    values = ", ".join(sql_values)
    key = _sql_literal(checkpoint["checkpoint_key"])
    anchor = _sql_literal(checkpoint["input"]["anchor_sha256"])
    config = _sql_literal(checkpoint["config_sha256"])
    return f"""\
BEGIN;
INSERT INTO {CHECKPOINT_TABLE} ({columns})
VALUES ({values})
ON CONFLICT (checkpoint_key) DO NOTHING;
DO $globemind_checkpoint$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM {CHECKPOINT_TABLE}
        WHERE checkpoint_key = {key}
          AND schema_version = 2
          AND job_id = {_sql_literal(checkpoint['job_id'])}
          AND run_id = {_sql_literal(checkpoint['run_id'])}
          AND input_path = {_sql_literal(checkpoint['input']['canonical_path'])}
          AND input_device = {checkpoint['input']['device']}
          AND input_inode = {checkpoint['input']['inode']}
          AND input_size = {checkpoint['input']['size']}
          AND input_offset = {checkpoint['input']['offset']}
          AND input_anchor_sha256 = {anchor}
          AND code_version = {_sql_literal(checkpoint['code_version'])}
          AND config_sha256 = {config}
          AND seen = {checkpoint['counters']['seen']}
          AND legacy_seen IS NOT DISTINCT FROM {_sql_literal(checkpoint.get('legacy_seen'))}
          AND inserted = {checkpoint['counters']['inserted']}
          AND duplicate = {checkpoint['counters']['duplicate']}
          AND invalid = {checkpoint['counters']['invalid']}
          AND quality_rejected = {checkpoint['counters']['quality_rejected']}
          AND quality_skip_reasons = {_sql_literal(json.dumps(checkpoint.get('quality_skip_reasons', {}), sort_keys=True))}::jsonb
          AND completed = false
    ) THEN
        RAISE EXCEPTION 'checkpoint seed conflicts with existing authoritative row';
    END IF;
END;
$globemind_checkpoint$;
COMMIT;
"""


def _require_expected(name: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        raise SafetyError(f"{name} does not match the explicitly supplied expectation")


def _resolve_contract(args: argparse.Namespace) -> tuple[str, str]:
    validate_safe_identifier("checkpoint_key", args.checkpoint_key)
    validate_safe_identifier("job_id", args.job_id)
    validate_safe_identifier("run_id", args.run_id)
    if args.code_version:
        validate_safe_identifier("code_version", args.code_version)
    resolved_code_version = resolve_loader_code_version(args.code_version)
    resolved_config_sha256 = resolve_loader_config_sha256(
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
    return resolved_code_version, resolved_config_sha256


def _verify_legacy_process(args: argparse.Namespace) -> dict[str, Any]:
    actual_identity = capture_process_identity(args.expected_pid, proc_root=args.proc_root)
    _require_expected("PID start ticks", actual_identity["start_ticks"], args.expected_start_ticks)
    _require_expected("boot ID", actual_identity["boot_id"], args.expected_boot_id)
    if args.require_stopped:
        require_process_stopped(args.expected_pid, proc_root=args.proc_root)
    return actual_identity


def _load_bound_legacy_state(args: argparse.Namespace) -> tuple[dict[str, Any], bytes, int]:
    validate_owned_regular_file(args.legacy_state)
    state_bytes = args.legacy_state.read_bytes()
    _require_expected(
        "legacy state digest",
        hashlib.sha256(state_bytes).hexdigest(),
        args.expected_state_sha256,
    )
    legacy = load_json_object(args.legacy_state)
    if legacy.get("schema_version") == CHECKPOINT_SCHEMA_VERSION:
        raise SafetyError("state is already schema v2")
    offset = int(legacy.get("offset", -1))
    if offset < 0:
        raise SafetyError("legacy checkpoint offset is invalid")
    return legacy, state_bytes, offset


def _resolve_legacy_quality_gate_transition(
    args: argparse.Namespace,
    frozen_offset: int,
) -> LegacyQualityGateTransition | None:
    names = (
        "legacy_transition_pre_offset",
        "legacy_transition_pre_seen",
        "legacy_transition_pre_inserted",
        "legacy_transition_pre_skipped",
        "legacy_transition_post_offset",
        "legacy_transition_post_seen",
        "legacy_transition_post_inserted",
        "legacy_transition_post_skipped",
        "legacy_transition_post_quality_skipped",
        "legacy_transition_post_quality_reasons",
        "legacy_transition_provenance_file",
        "legacy_transition_provenance_sha256",
    )
    supplied = [getattr(args, name, None) for name in names]
    if all(value is None for value in supplied):
        return None
    if any(value is None for value in supplied):
        raise SafetyError("legacy quality-gate transition evidence must be supplied together")
    try:
        before = LegacyCheckpointEvidence(*(int(value) for value in supplied[:4]))
        after = LegacyCheckpointEvidence(*(int(value) for value in supplied[4:8]))
        after_quality_skipped = int(supplied[8])
    except (TypeError, ValueError) as exc:
        raise SafetyError("legacy quality-gate transition counters are invalid") from exc
    raw_reasons = supplied[9]
    if isinstance(raw_reasons, str):
        try:
            raw_reasons = json.loads(raw_reasons)
        except json.JSONDecodeError as exc:
            raise SafetyError("legacy transition quality reasons must be a JSON object") from exc
    if not isinstance(raw_reasons, dict):
        raise SafetyError("legacy transition quality reasons must be a JSON object")
    try:
        after_quality_reasons = dict(
            sorted((str(key), int(value)) for key, value in raw_reasons.items())
        )
    except (TypeError, ValueError) as exc:
        raise SafetyError("legacy transition quality reasons are invalid") from exc
    if (
        min((*asdict(before).values(), *asdict(after).values(), after_quality_skipped)) < 0
        or any(value < 0 for value in after_quality_reasons.values())
    ):
        raise SafetyError("legacy quality-gate transition values must be non-negative")
    if not 0 <= before.offset < after.offset <= frozen_offset:
        raise SafetyError("legacy transition evidence offsets are outside the frozen prefix")
    for checkpoint, label in ((before, "pre"), (after, "post")):
        if checkpoint.inserted > checkpoint.seen:
            raise SafetyError(f"legacy transition {label} inserted exceeds seen")
        if checkpoint.skipped < checkpoint.seen - checkpoint.inserted:
            raise SafetyError(f"legacy transition {label} counters are internally inconsistent")
    for field in ("seen", "inserted", "skipped"):
        if getattr(after, field) < getattr(before, field):
            raise SafetyError("legacy transition checkpoint counters regressed")
    skipped_delta = after.skipped - before.skipped
    if after_quality_skipped > skipped_delta:
        raise SafetyError("legacy transition quality count exceeds the skipped delta")
    if any(value > after_quality_skipped for value in after_quality_reasons.values()):
        raise SafetyError("legacy transition quality reason exceeds the rejected row count")
    if bool(after_quality_reasons) != bool(after_quality_skipped):
        raise SafetyError("legacy transition quality reasons do not match the rejected row count")
    provenance_file = Path(supplied[10])
    validate_private_file(provenance_file)
    provenance_path = str(provenance_file.resolve(strict=True))
    provenance_sha256 = str(supplied[11] or "")
    if len(provenance_sha256) != 64 or any(
        char not in "0123456789abcdef" for char in provenance_sha256
    ):
        raise SafetyError("legacy transition provenance is not a lowercase SHA-256 digest")
    _require_expected(
        "legacy transition provenance digest",
        _sha256_file(Path(provenance_path)),
        provenance_sha256,
    )
    return LegacyQualityGateTransition(
        before=before,
        after=after,
        after_quality_skipped=after_quality_skipped,
        after_quality_reasons=after_quality_reasons,
        provenance_path=provenance_path,
        provenance_sha256=provenance_sha256,
    )


def _legacy_transition_evidence_sha256(transition: LegacyQualityGateTransition) -> str:
    payload = {
        "schema_version": 1,
        "source": "recorded_restart_checkpoint_window",
        "pre_checkpoint": asdict(transition.before),
        "post_checkpoint": {
            **asdict(transition.after),
            "quality_skipped": transition.after_quality_skipped,
            "quality_skip_reasons": transition.after_quality_reasons,
        },
        "provenance": {
            "canonical_path": transition.provenance_path,
            "sha256": transition.provenance_sha256,
        },
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _verify_transition_provenance(transition: LegacyQualityGateTransition | None) -> None:
    if transition is None:
        return
    path = Path(transition.provenance_path)
    validate_private_file(path)
    _require_expected(
        "legacy transition provenance path",
        str(path.resolve(strict=True)),
        transition.provenance_path,
    )
    _require_expected(
        "legacy transition provenance digest",
        _sha256_file(path),
        transition.provenance_sha256,
    )


def _new_audit_cohort(
    name: str,
    start_offset: int,
    end_offset: int,
    *,
    quality_gate: bool | str,
) -> dict[str, Any]:
    return {
        "name": name,
        "start_offset": start_offset,
        "end_offset": end_offset,
        "quality_gate": quality_gate,
        "processed_complete_lines": 0,
        "blank_lines": 0,
        "malformed_reasons": Counter(),
        "invalid_reasons": Counter(),
        "quality_skip_reasons": Counter(),
        "quality_rejected": 0,
        "database_candidates": 0,
    }


def _finish_audit_cohort(cohort: dict[str, Any]) -> dict[str, Any]:
    malformed_reasons = cohort["malformed_reasons"]
    invalid_reasons = cohort["invalid_reasons"]
    quality_reasons = cohort["quality_skip_reasons"]
    malformed = sum(malformed_reasons.values())
    invalid = sum(invalid_reasons.values())
    processed = int(cohort["processed_complete_lines"])
    blank_lines = int(cohort["blank_lines"])
    quality_rejected = int(cohort["quality_rejected"])
    database_candidates = int(cohort["database_candidates"])
    if processed != blank_lines + malformed + invalid + quality_rejected + database_candidates:
        raise SafetyError("legacy audit cohort classification is incomplete")
    return {
        "name": cohort["name"],
        "start_offset": cohort["start_offset"],
        "end_offset": cohort["end_offset"],
        "quality_gate": cohort["quality_gate"],
        "processed_complete_lines": processed,
        "blank_lines": blank_lines,
        "malformed": malformed,
        "invalid": invalid,
        "invalid_total": blank_lines + malformed + invalid,
        "quality_rejected": quality_rejected,
        "database_candidates": database_candidates,
        "legacy_seen": processed - blank_lines - malformed,
        "legacy_skipped_without_duplicate": malformed + invalid + quality_rejected,
        "malformed_reasons": dict(sorted(malformed_reasons.items())),
        "invalid_reasons": dict(sorted(invalid_reasons.items())),
        "quality_skip_reasons": dict(sorted(quality_reasons.items())),
    }


def _merge_reason_counts(cohorts: list[dict[str, Any]], field: str) -> dict[str, int]:
    merged: Counter[str] = Counter()
    for cohort in cohorts:
        reasons = cohort.get(field)
        if not isinstance(reasons, dict):
            raise SafetyError(f"legacy audit cohort {field} is invalid")
        try:
            normalized = {str(key): int(value) for key, value in reasons.items()}
        except (TypeError, ValueError) as exc:
            raise SafetyError(f"legacy audit cohort {field} is invalid") from exc
        if any(value < 0 for value in normalized.values()):
            raise SafetyError(f"legacy audit cohort {field} contains a negative count")
        merged.update(normalized)
    return dict(sorted(merged.items()))


def _nonnegative_int(mapping: dict[str, Any], field: str, context: str) -> int:
    try:
        value = int(mapping.get(field, -1))
    except (TypeError, ValueError) as exc:
        raise SafetyError(f"{context} {field} is invalid") from exc
    if value < 0:
        raise SafetyError(f"{context} {field} is invalid")
    return value


def _normalized_reason_counts(value: Any, context: str) -> dict[str, int]:
    if not isinstance(value, dict):
        raise SafetyError(f"{context} must be an object")
    try:
        reasons = dict(sorted((str(key), int(count)) for key, count in value.items()))
    except (TypeError, ValueError) as exc:
        raise SafetyError(f"{context} is invalid") from exc
    if any(count < 0 for count in reasons.values()):
        raise SafetyError(f"{context} contains a negative count")
    return reasons


def _resolve_wall_clock_reconciliation_reason(
    args: argparse.Namespace,
    transition: LegacyQualityGateTransition | None,
) -> str | None:
    reason = getattr(args, "legacy_wall_clock_reconcile_reason", None)
    if reason is None:
        return None
    if reason != LEGACY_WALL_CLOCK_RECONCILIATION_REASON:
        raise SafetyError("legacy wall-clock reconciliation reason is unsupported")
    if transition is None:
        raise SafetyError("legacy wall-clock reconciliation requires transition evidence")
    return reason


def _quality_accounting_snapshot(classification: dict[str, Any]) -> dict[str, Any]:
    return {
        "quality_rejected": _nonnegative_int(
            classification,
            "quality_rejected",
            "quality classification",
        ),
        "database_candidates": _nonnegative_int(
            classification,
            "database_candidates",
            "quality classification",
        ),
        "quality_skip_reasons": _normalized_reason_counts(
            classification.get("quality_skip_reasons"),
            "quality classification reasons",
        ),
    }


def _adjust_quality_snapshot(
    snapshot: dict[str, Any],
    reason: str,
    delta: int,
) -> dict[str, Any]:
    adjusted = {
        "quality_rejected": int(snapshot["quality_rejected"]) + delta,
        "database_candidates": int(snapshot["database_candidates"]) - delta,
        "quality_skip_reasons": dict(snapshot["quality_skip_reasons"]),
    }
    if adjusted["database_candidates"] < 0 or adjusted["quality_rejected"] < 0:
        raise SafetyError("wall-clock reconciliation exceeds audited classification counts")
    reason_count = int(adjusted["quality_skip_reasons"].get(reason, 0)) + delta
    if reason_count < 0:
        raise SafetyError("wall-clock reconciliation reason count became negative")
    if reason_count:
        adjusted["quality_skip_reasons"][reason] = reason_count
    else:
        adjusted["quality_skip_reasons"].pop(reason, None)
    adjusted["quality_skip_reasons"] = dict(
        sorted(adjusted["quality_skip_reasons"].items())
    )
    return adjusted


def _apply_wall_clock_reconciliation(
    classification: dict[str, Any],
    legacy: dict[str, Any],
    transition: LegacyQualityGateTransition | None,
    args: argparse.Namespace,
    audit_quality_now: datetime,
) -> dict[str, Any] | None:
    reason = _resolve_wall_clock_reconciliation_reason(args, transition)
    if reason is None:
        return None
    if transition is None:
        raise SafetyError("wall-clock reconciliation transition is missing")
    cohorts = classification.get("cohorts")
    if (
        not isinstance(cohorts, list)
        or len(cohorts) != 3
        or any(not isinstance(cohort, dict) for cohort in cohorts)
    ):
        raise SafetyError("wall-clock reconciliation cohorts are missing")
    transition_cohort = cohorts[1]
    _require_expected(
        "unadjusted transition quality count",
        transition_cohort.get("quality_rejected"),
        transition.after_quality_skipped,
    )
    _require_expected(
        "unadjusted transition quality reasons",
        transition_cohort.get("quality_skip_reasons"),
        transition.after_quality_reasons,
    )

    state_quality = _nonnegative_int(
        {"quality_skipped": legacy.get("quality_skipped", -1)},
        "quality_skipped",
        "legacy checkpoint",
    )
    state_reasons = _normalized_reason_counts(
        legacy.get("quality_skip_reasons"),
        "legacy quality_skip_reasons",
    )
    unadjusted_total = _quality_accounting_snapshot(classification)
    unadjusted_after = _quality_accounting_snapshot(cohorts[2])
    audited_reasons = unadjusted_total["quality_skip_reasons"]
    delta_rows = state_quality - int(unadjusted_total["quality_rejected"])
    delta_reason = state_reasons.get(reason, 0) - audited_reasons.get(reason, 0)
    if delta_rows < 0 or delta_rows != delta_reason:
        raise SafetyError("legacy wall-clock row/reason deltas are inconsistent")
    for key in set(state_reasons) | set(audited_reasons):
        if key == reason:
            continue
        if state_reasons.get(key, 0) != audited_reasons.get(key, 0):
            raise SafetyError("legacy wall-clock non-target quality reasons changed")
    candidate_proof = classification.get("wall_clock_drift_candidates")
    if not isinstance(candidate_proof, dict):
        raise SafetyError("legacy wall-clock drift candidate proof is missing")
    candidate_count = _nonnegative_int(
        candidate_proof,
        "count",
        "legacy wall-clock drift candidates",
    )
    _validate_sha256_digest(
        "legacy wall-clock drift candidate digest",
        candidate_proof.get("digest"),
    )
    if candidate_count < delta_rows:
        raise SafetyError("legacy wall-clock drift candidates cannot support the delta")

    adjusted_total = _adjust_quality_snapshot(unadjusted_total, reason, delta_rows)
    adjusted_after = _adjust_quality_snapshot(unadjusted_after, reason, delta_rows)
    for target, adjusted in (
        (classification, adjusted_total),
        (cohorts[2], adjusted_after),
    ):
        target["quality_rejected"] = adjusted["quality_rejected"]
        target["database_candidates"] = adjusted["database_candidates"]
        target["quality_skip_reasons"] = adjusted["quality_skip_reasons"]
        target["legacy_skipped_without_duplicate"] = (
            _nonnegative_int(
                target,
                "legacy_skipped_without_duplicate",
                "quality classification",
            )
            + delta_rows
        )
    _require_expected(
        "adjusted legacy quality count", adjusted_total["quality_rejected"], state_quality
    )
    _require_expected(
        "adjusted legacy quality reasons",
        adjusted_total["quality_skip_reasons"],
        state_reasons,
    )
    return {
        "schema_version": 1,
        "mode": "aggregate_wall_clock_reason_delta",
        "quality_clock": "wall_clock",
        "audit_quality_now": _audit_quality_now_text(audit_quality_now),
        "candidate_semantics": (
            "otherwise_good_at_audit_clock_and_published_after_fetched_at_grace"
        ),
        "historical_processing_clock_proof": "necessary_not_sufficient",
        "allowed_reason": reason,
        "applied_to_cohort": "after_transition_evidence",
        "state_binding": {
            "sha256": args.expected_state_sha256,
            "quality_skipped": state_quality,
            "quality_skip_reasons": state_reasons,
        },
        "unadjusted": {
            "total": unadjusted_total,
            "after_transition": unadjusted_after,
        },
        "delta": {
            "quality_rows": delta_rows,
            "reason_count": delta_reason,
            "database_candidates_removed": delta_rows,
        },
        "eligible_candidates": candidate_proof,
        "adjusted": {
            "total": adjusted_total,
            "after_transition": adjusted_after,
        },
    }


def _validate_wall_clock_reconciliation(
    artifact: Any,
    classification: dict[str, Any],
    legacy: dict[str, Any],
    transition: LegacyQualityGateTransition | None,
    args: argparse.Namespace,
    audit_quality_now: datetime,
) -> None:
    reason = _resolve_wall_clock_reconciliation_reason(args, transition)
    if reason is None:
        _require_expected("audit wall-clock reconciliation", artifact, None)
        return
    if transition is None or not isinstance(artifact, dict):
        raise SafetyError("audit wall-clock reconciliation is missing")
    for field, expected in (
        ("schema_version", 1),
        ("mode", "aggregate_wall_clock_reason_delta"),
        ("quality_clock", "wall_clock"),
        ("audit_quality_now", _audit_quality_now_text(audit_quality_now)),
        (
            "candidate_semantics",
            "otherwise_good_at_audit_clock_and_published_after_fetched_at_grace",
        ),
        ("historical_processing_clock_proof", "necessary_not_sufficient"),
        ("allowed_reason", reason),
        ("applied_to_cohort", "after_transition_evidence"),
    ):
        _require_expected(f"audit wall-clock {field}", artifact.get(field), expected)
    cohorts = classification.get("cohorts")
    if not isinstance(cohorts, list) or len(cohorts) != 3:
        raise SafetyError("audit wall-clock cohorts are missing")
    state_quality = _nonnegative_int(
        {"quality_skipped": legacy.get("quality_skipped", -1)},
        "quality_skipped",
        "legacy checkpoint",
    )
    state_reasons = _normalized_reason_counts(
        legacy.get("quality_skip_reasons"),
        "legacy quality_skip_reasons",
    )
    _require_expected(
        "audit wall-clock state binding",
        artifact.get("state_binding"),
        {
            "sha256": args.expected_state_sha256,
            "quality_skipped": state_quality,
            "quality_skip_reasons": state_reasons,
        },
    )
    delta = artifact.get("delta")
    if not isinstance(delta, dict):
        raise SafetyError("audit wall-clock delta is missing")
    delta_rows = _nonnegative_int(delta, "quality_rows", "audit wall-clock delta")
    for field in ("reason_count", "database_candidates_removed"):
        _require_expected(
            f"audit wall-clock delta {field}",
            _nonnegative_int(delta, field, "audit wall-clock delta"),
            delta_rows,
        )
    candidate_proof = classification.get("wall_clock_drift_candidates")
    if not isinstance(candidate_proof, dict):
        raise SafetyError("audit wall-clock drift candidate proof is missing")
    _require_expected(
        "audit wall-clock drift candidates",
        artifact.get("eligible_candidates"),
        candidate_proof,
    )
    if _nonnegative_int(
        candidate_proof,
        "count",
        "audit wall-clock drift candidates",
    ) < delta_rows:
        raise SafetyError("audit wall-clock drift candidates cannot support the delta")
    _validate_sha256_digest(
        "audit wall-clock drift candidate digest",
        candidate_proof.get("digest"),
    )
    adjusted_total = _quality_accounting_snapshot(classification)
    adjusted_after = _quality_accounting_snapshot(cohorts[2])
    _require_expected(
        "audit wall-clock adjusted accounting",
        artifact.get("adjusted"),
        {"total": adjusted_total, "after_transition": adjusted_after},
    )
    if adjusted_total["quality_rejected"] != state_quality:
        raise SafetyError("audit wall-clock adjusted quality count does not match state")
    _require_expected(
        "audit wall-clock adjusted quality reasons",
        adjusted_total["quality_skip_reasons"],
        state_reasons,
    )
    unadjusted_total = _adjust_quality_snapshot(adjusted_total, reason, -delta_rows)
    unadjusted_after = _adjust_quality_snapshot(adjusted_after, reason, -delta_rows)
    if min(
        unadjusted_total["quality_rejected"],
        unadjusted_after["quality_rejected"],
        unadjusted_total["quality_skip_reasons"].get(reason, 0),
        unadjusted_after["quality_skip_reasons"].get(reason, 0),
    ) < 0:
        raise SafetyError("audit wall-clock reversal became negative")
    _require_expected(
        "audit wall-clock unadjusted accounting",
        artifact.get("unadjusted"),
        {"total": unadjusted_total, "after_transition": unadjusted_after},
    )
    delta_reason = state_reasons.get(reason, 0) - unadjusted_total[
        "quality_skip_reasons"
    ].get(reason, 0)
    if delta_reason != delta_rows or (
        state_quality - unadjusted_total["quality_rejected"] != delta_rows
    ):
        raise SafetyError("audit wall-clock row/reason deltas are inconsistent")
    for key in set(state_reasons) | set(unadjusted_total["quality_skip_reasons"]):
        if key != reason and state_reasons.get(key, 0) != unadjusted_total[
            "quality_skip_reasons"
        ].get(key, 0):
            raise SafetyError("audit wall-clock non-target quality reasons changed")
    _require_expected(
        "audit wall-clock transition quality count",
        cohorts[1].get("quality_rejected"),
        transition.after_quality_skipped,
    )
    _require_expected(
        "audit wall-clock transition quality reasons",
        cohorts[1].get("quality_skip_reasons"),
        transition.after_quality_reasons,
    )


def _legacy_observed_cohort_counters(
    legacy: dict[str, Any],
    transition: LegacyQualityGateTransition | None,
) -> list[dict[str, int]]:
    final = {
        field: _nonnegative_int(legacy, field, "legacy checkpoint")
        for field in ("seen", "inserted", "skipped")
    }
    if transition is None:
        return [final]
    before = {
        field: getattr(transition.before, field) for field in ("seen", "inserted", "skipped")
    }
    window = {
        field: getattr(transition.after, field) - getattr(transition.before, field)
        for field in before
    }
    after = {field: final[field] - getattr(transition.after, field) for field in before}
    if any(value < 0 for cohort in (window, after) for value in cohort.values()):
        raise SafetyError("frozen legacy counters precede the transition evidence")
    return [before, window, after]


def _bind_or_validate_legacy_accounting(
    classification: dict[str, Any],
    legacy: dict[str, Any],
    transition: LegacyQualityGateTransition | None,
    *,
    frozen_offset: int,
    quality_gate: bool,
    bind: bool,
) -> dict[str, int]:
    cohorts = classification.get("cohorts")
    if not isinstance(cohorts, list):
        raise SafetyError("legacy audit cohorts are missing")
    expected_layout = (
        [("legacy_prefix", 0, frozen_offset, quality_gate)]
        if transition is None
        else [
            ("before_transition_evidence", 0, transition.before.offset, False),
            (
                "quality_gate_transition_window",
                transition.before.offset,
                transition.after.offset,
                "verified_transition",
            ),
            (
                "after_transition_evidence",
                transition.after.offset,
                frozen_offset,
                quality_gate,
            ),
        ]
    )
    if len(cohorts) != len(expected_layout):
        raise SafetyError("legacy audit cohort layout is invalid")

    numeric_fields = (
        "processed_complete_lines",
        "blank_lines",
        "malformed",
        "invalid",
        "invalid_total",
        "quality_rejected",
        "database_candidates",
        "legacy_seen",
        "legacy_skipped_without_duplicate",
    )
    observed_counters = _legacy_observed_cohort_counters(legacy, transition)
    totals = {field: 0 for field in numeric_fields}
    total_duplicate = 0
    for index, (cohort, layout, observed) in enumerate(
        zip(cohorts, expected_layout, observed_counters, strict=True)
    ):
        if not isinstance(cohort, dict):
            raise SafetyError("legacy audit cohort is invalid")
        name, start_offset, end_offset, cohort_quality_gate = layout
        for field, expected in (
            ("name", name),
            ("start_offset", start_offset),
            ("end_offset", end_offset),
            ("quality_gate", cohort_quality_gate),
        ):
            _require_expected(f"audit cohort {index} {field}", cohort.get(field), expected)
        values = {
            field: _nonnegative_int(cohort, field, f"audit cohort {index}")
            for field in numeric_fields
        }
        if values["invalid_total"] != (
            values["blank_lines"] + values["malformed"] + values["invalid"]
        ):
            raise SafetyError("legacy audit cohort invalid accounting is inconsistent")
        if values["legacy_seen"] != (
            values["processed_complete_lines"]
            - values["blank_lines"]
            - values["malformed"]
        ):
            raise SafetyError("legacy audit cohort seen accounting is inconsistent")
        if values["legacy_skipped_without_duplicate"] != (
            values["malformed"] + values["invalid"] + values["quality_rejected"]
        ):
            raise SafetyError("legacy audit cohort skipped accounting is inconsistent")
        if values["processed_complete_lines"] != (
            values["invalid_total"]
            + values["quality_rejected"]
            + values["database_candidates"]
        ):
            raise SafetyError("legacy audit cohort classification accounting is inconsistent")
        if cohort_quality_gate is False and values["quality_rejected"]:
            raise SafetyError("quality rejection exists before the quality gate was enabled")
        if transition is not None and index == 1:
            _require_expected(
                "transition-window quality count",
                values["quality_rejected"],
                transition.after_quality_skipped,
            )
            _require_expected(
                "transition-window quality reasons",
                cohort.get("quality_skip_reasons"),
                transition.after_quality_reasons,
            )

        duplicate = values["database_candidates"] - observed["inserted"]
        if duplicate < 0:
            raise SafetyError("legacy inserted count exceeds audited database candidates")
        recomputed = {
            "seen": values["legacy_seen"],
            "inserted": observed["inserted"],
            "skipped": values["legacy_skipped_without_duplicate"] + duplicate,
        }
        if recomputed != observed:
            raise SafetyError("legacy checkpoint counters do not match the audited input cohort")
        accounting = {
            "observed": observed,
            "recomputed": recomputed,
            "inferred_duplicate": duplicate,
        }
        if bind:
            cohort["legacy_accounting"] = accounting
        else:
            _require_expected(
                f"audit cohort {index} legacy accounting",
                cohort.get("legacy_accounting"),
                accounting,
            )
        total_duplicate += duplicate
        for field in numeric_fields:
            totals[field] += values[field]

    for field in numeric_fields:
        _require_expected(f"audit classification {field}", classification.get(field), totals[field])
    for field in ("malformed_reasons", "invalid_reasons", "quality_skip_reasons"):
        _require_expected(
            f"audit classification {field}",
            classification.get(field),
            _merge_reason_counts(cohorts, field),
        )

    expected_quality_skipped = totals["quality_rejected"]
    if transition is not None and "quality_skipped" not in legacy:
        raise SafetyError("legacy quality_skipped is required for transition evidence")
    state_quality_skipped = _nonnegative_int(
        {"quality_skipped": legacy.get("quality_skipped", 0)},
        "quality_skipped",
        "legacy checkpoint",
    )
    if state_quality_skipped != expected_quality_skipped:
        raise SafetyError("legacy quality_skipped does not match the audited quality-gated cohort")
    if transition is not None or "quality_skip_reasons" in legacy:
        state_quality_reasons = legacy.get("quality_skip_reasons")
        if not isinstance(state_quality_reasons, dict):
            raise SafetyError("legacy quality_skip_reasons is missing or invalid")
        try:
            normalized_state_reasons = dict(
                sorted((str(key), int(value)) for key, value in state_quality_reasons.items())
            )
        except (TypeError, ValueError) as exc:
            raise SafetyError("legacy quality_skip_reasons is invalid") from exc
        if any(value < 0 for value in normalized_state_reasons.values()):
            raise SafetyError("legacy quality_skip_reasons contains a negative count")
        _require_expected(
            "legacy quality rejection reasons",
            normalized_state_reasons,
            classification["quality_skip_reasons"],
        )

    final_inserted = _nonnegative_int(legacy, "inserted", "legacy checkpoint")
    counters = {
        "seen": totals["processed_complete_lines"],
        "inserted": final_inserted,
        "duplicate": total_duplicate,
        "invalid": totals["invalid_total"],
        "quality_rejected": totals["quality_rejected"],
    }
    if counters["seen"] != sum(value for key, value in counters.items() if key != "seen"):
        raise SafetyError("audited v2 counter invariant is not satisfied")
    legacy_accounting = {
        "observed_final": {
            field: _nonnegative_int(legacy, field, "legacy checkpoint")
            for field in ("seen", "inserted", "skipped")
        },
        "quality_skipped": state_quality_skipped,
    }
    if bind:
        classification["inferred_duplicate"] = total_duplicate
        classification["counters"] = counters
        classification["legacy_accounting"] = legacy_accounting
    else:
        _require_expected(
            "audit inferred duplicate", classification.get("inferred_duplicate"), total_duplicate
        )
        _require_expected("audit v2 counters", classification.get("counters"), counters)
        _require_expected(
            "audit legacy accounting", classification.get("legacy_accounting"), legacy_accounting
        )
    return counters


def _apply_audit_event(
    cohort: dict[str, Any],
    event: dict[str, Any],
    *,
    quality_gate: bool,
) -> None:
    cohort["processed_complete_lines"] += 1
    kind = event["kind"]
    if kind == "blank":
        cohort["blank_lines"] += 1
        return
    if kind == "malformed":
        cohort["malformed_reasons"][event["reason"]] += 1
        return
    classified = event.get("classified")
    if not isinstance(classified, NewsRecordClassification):
        raise SafetyError("legacy audit event classification is invalid")
    if classified.classification == "database":
        cohort["database_candidates"] += 1
    elif classified.classification == "quality_rejected":
        if quality_gate:
            cohort["quality_rejected"] += 1
            cohort["quality_skip_reasons"].update(classified.quality_reasons)
        else:
            cohort["database_candidates"] += 1
    else:
        cohort["invalid_reasons"]["normalization_invalid"] += 1


def _subtract_reason_counts(counter: Counter[str], reasons: Iterable[str]) -> None:
    counter.subtract(reasons)
    for key in tuple(counter):
        if counter[key] < 0:
            raise SafetyError("legacy transition quality reason accounting became negative")
        if counter[key] == 0:
            del counter[key]


def _wall_clock_drift_candidate(
    value: dict[str, Any],
    classified: NewsRecordClassification,
    *,
    audit_quality_now: datetime,
    future_grace_days: int,
    start_offset: int,
    end_offset: int,
    raw: bytes,
) -> dict[str, Any] | None:
    if classified.classification != "database" or classified.normalized is None:
        return None
    from news_date_cleaning import parse_datetime

    published_at = parse_datetime(classified.normalized.get("published_at"))
    fetched_at = parse_datetime(value.get("fetched_at"))
    if published_at is None or fetched_at is None:
        return None
    grace = timedelta(days=future_grace_days)
    if not (
        published_at > fetched_at + grace
        and published_at <= audit_quality_now + grace
    ):
        return None
    return {
        "start_offset": start_offset,
        "end_offset": end_offset,
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
    }


def _wall_clock_candidate_proof(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    payload = json.dumps(candidates, sort_keys=True, separators=(",", ":")).encode()
    return {
        "schema_version": 1,
        "identity": "record_offsets_and_raw_sha256",
        "count": len(candidates),
        "digest": hashlib.sha256(payload).hexdigest(),
        "records": candidates,
    }


def _audit_input_prefix(
    path: Path,
    offset: int,
    source_map: dict[str, Any],
    args: argparse.Namespace,
    *,
    audit_quality_now: datetime | None = None,
    resolved_transition: LegacyQualityGateTransition | None = None,
) -> tuple[InputFingerprint, dict[str, Any], dict[str, Any] | None]:
    audit_quality_now = audit_quality_now or _utc_now()
    audit_quality_now = _parse_audit_quality_now(_audit_quality_now_text(audit_quality_now))
    transition = resolved_transition or _resolve_legacy_quality_gate_transition(args, offset)
    if transition is not None and args.disable_quality_gate:
        raise SafetyError("transition evidence requires the quality gate in the new loader contract")
    descriptor, canonical = _open_regular_nofollow(path)
    if transition is None:
        raw_cohorts = [
            _new_audit_cohort(
                "legacy_prefix",
                0,
                offset,
                quality_gate=not args.disable_quality_gate,
            )
        ]
    else:
        raw_cohorts = [
            _new_audit_cohort(
                "before_transition_evidence",
                0,
                transition.before.offset,
                quality_gate=False,
            ),
            _new_audit_cohort(
                "quality_gate_transition_window",
                transition.before.offset,
                transition.after.offset,
                quality_gate="verified_transition",
            ),
            _new_audit_cohort(
                "after_transition_evidence",
                transition.after.offset,
                offset,
                quality_gate=True,
            ),
        ]
    digest = hashlib.sha256()
    evidence_anchors: dict[str, dict[str, Any]] = (
        {"pre": {"anchor_sha256": digest.hexdigest(), "complete_lines": 0}}
        if transition is not None and transition.before.offset == 0
        else {}
    )
    window_events: list[dict[str, Any]] = []
    wall_clock_candidates: list[dict[str, Any]] = []
    processed = 0
    try:
        before = os.fstat(descriptor)
        if offset > before.st_size:
            raise SafetyError("legacy checkpoint offset is beyond the current input")
        with os.fdopen(os.dup(descriptor), "rb") as handle:
            remaining = offset
            while remaining:
                start_offset = handle.tell()
                raw = handle.readline(remaining)
                if not raw:
                    raise SafetyError("input was truncated during legacy audit")
                if not raw.endswith(b"\n"):
                    raise SafetyError("legacy checkpoint offset is not on a complete record boundary")
                remaining -= len(raw)
                digest.update(raw)
                end_offset = handle.tell()
                processed += 1
                if transition is not None:
                    for label, checkpoint in (
                        ("pre", transition.before),
                        ("post", transition.after),
                    ):
                        if start_offset < checkpoint.offset < end_offset:
                            raise SafetyError(
                                f"legacy transition {label} evidence is not on a complete record boundary"
                            )
                        if end_offset == checkpoint.offset:
                            evidence_anchors[label] = {
                                "anchor_sha256": digest.hexdigest(),
                                "complete_lines": processed,
                            }
                try:
                    decoded = raw.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise SafetyError(
                        "legacy prefix contains invalid UTF-8 that the legacy loader could not persist"
                    ) from exc
                text = decoded.strip()
                if not text:
                    event = {"kind": "blank"}
                else:
                    try:
                        value = json.loads(text)
                    except json.JSONDecodeError:
                        event = {"kind": "malformed", "reason": "invalid_json"}
                    else:
                        if not isinstance(value, dict):
                            raise SafetyError(
                                "legacy prefix contains non-object JSON that the legacy loader could not persist"
                            )
                        evaluate_quality = not args.disable_quality_gate and (
                            transition is None or end_offset > transition.before.offset
                        )
                        try:
                            classified = classify_news_value(
                                value,
                                source_map,
                                disable_quality_gate=not evaluate_quality,
                                min_body_chars=args.min_body_chars,
                                min_published_year=args.min_published_year,
                                future_grace_days=args.future_grace_days,
                                fail_on_exception=True,
                                quality_now=audit_quality_now,
                            )
                        except SafetyError:
                            raise
                        except Exception as exc:
                            raise SafetyError(
                                "legacy normalization raised an unexpected exception"
                            ) from exc
                        event = {
                            "kind": "classified",
                            "classified": classified,
                        }
                event.update(
                    {
                        "start_offset": start_offset,
                        "end_offset": end_offset,
                        "raw": raw,
                    }
                )
                if transition is None:
                    _apply_audit_event(
                        raw_cohorts[0],
                        event,
                        quality_gate=not args.disable_quality_gate,
                    )
                elif end_offset <= transition.before.offset:
                    _apply_audit_event(raw_cohorts[0], event, quality_gate=False)
                elif start_offset >= transition.after.offset:
                    _apply_audit_event(raw_cohorts[2], event, quality_gate=True)
                    if event["kind"] == "classified":
                        candidate = _wall_clock_drift_candidate(
                            value,
                            event["classified"],
                            audit_quality_now=audit_quality_now,
                            future_grace_days=args.future_grace_days,
                            start_offset=start_offset,
                            end_offset=end_offset,
                            raw=raw,
                        )
                        if candidate is not None:
                            wall_clock_candidates.append(candidate)
                else:
                    window_events.append(event)

        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise SafetyError("input identity changed during legacy audit")
        if after.st_size < offset:
            raise SafetyError("input was truncated during legacy audit")
        try:
            path_after = os.stat(canonical, follow_symlinks=False)
        except OSError as exc:
            raise SafetyError("input path changed during legacy audit") from exc
        if (
            not stat.S_ISREG(path_after.st_mode)
            or (path_after.st_dev, path_after.st_ino) != (after.st_dev, after.st_ino)
        ):
            raise SafetyError("input path was replaced during legacy audit")
    finally:
        os.close(descriptor)

    transition_artifact = None
    if transition is not None:
        if set(evidence_anchors) != {"pre", "post"}:
            raise SafetyError("legacy transition evidence anchors were not reached")
        suffix_quality_reasons: Counter[str] = Counter()
        suffix_quality_rows = 0
        for event in window_events:
            classified = event.get("classified")
            if (
                isinstance(classified, NewsRecordClassification)
                and classified.classification == "quality_rejected"
            ):
                suffix_quality_rows += 1
                suffix_quality_reasons.update(classified.quality_reasons)
        potential_quality_rows = suffix_quality_rows
        potential_quality_reasons = dict(sorted(suffix_quality_reasons.items()))

        candidate_indexes: list[int] = []
        for index in range(len(window_events) + 1):
            if (
                suffix_quality_rows == transition.after_quality_skipped
                and dict(sorted(suffix_quality_reasons.items()))
                == transition.after_quality_reasons
            ):
                candidate_indexes.append(index)
            if index == len(window_events):
                break
            classified = window_events[index].get("classified")
            if (
                isinstance(classified, NewsRecordClassification)
                and classified.classification == "quality_rejected"
            ):
                suffix_quality_rows -= 1
                _subtract_reason_counts(suffix_quality_reasons, classified.quality_reasons)

        if not candidate_indexes:
            raise SafetyError("no complete-line transition matches the recorded quality evidence")
        earliest_index = candidate_indexes[0]
        latest_index = candidate_indexes[-1]
        if candidate_indexes != list(range(earliest_index, latest_index + 1)):
            raise SafetyError("matching transition boundaries are not a contiguous invariant range")
        invariant_events = window_events[earliest_index:latest_index]
        for event in invariant_events:
            classified = event.get("classified")
            if (
                isinstance(classified, NewsRecordClassification)
                and classified.classification == "quality_rejected"
            ):
                raise SafetyError("transition candidates are separated by a quality-sensitive record")

        for index, event in enumerate(window_events):
            _apply_audit_event(
                raw_cohorts[1],
                event,
                quality_gate=index >= earliest_index,
            )
        candidate_offsets = [
            transition.before.offset
            if index == 0
            else int(window_events[index - 1]["end_offset"])
            for index in candidate_indexes
        ]
        invariant_payload = b"".join(event["raw"] for event in invariant_events)
        transition_artifact = {
            "source": "recorded_restart_checkpoint_window",
            "evidence_sha256": _legacy_transition_evidence_sha256(transition),
            "provenance": {
                "canonical_path": transition.provenance_path,
                "sha256": transition.provenance_sha256,
            },
            "pre_checkpoint": {
                **asdict(transition.before),
                **evidence_anchors["pre"],
            },
            "post_checkpoint": {
                **asdict(transition.after),
                "quality_skipped": transition.after_quality_skipped,
                "quality_skip_reasons": transition.after_quality_reasons,
                **evidence_anchors["post"],
            },
            "candidate_boundaries": {
                "count": len(candidate_offsets),
                "earliest_offset": candidate_offsets[0],
                "latest_offset": candidate_offsets[-1],
                "canonical_offset": candidate_offsets[0],
                "canonical_policy": "earliest_equivalent_boundary",
                "all_candidates_semantically_equivalent": True,
                "potential_quality_rejected": potential_quality_rows,
                "potential_quality_skip_reasons": potential_quality_reasons,
                "quality_rejected_before_canonical": (
                    potential_quality_rows - transition.after_quality_skipped
                ),
                "quality_rejected_after_canonical": transition.after_quality_skipped,
            },
            "invariant_gap": {
                "start_offset": candidate_offsets[0],
                "end_offset": candidate_offsets[-1],
                "bytes": len(invariant_payload),
                "complete_lines": len(invariant_events),
                "sha256": hashlib.sha256(invariant_payload).hexdigest(),
                "quality_sensitive_records": 0,
            },
        }

    cohorts = [_finish_audit_cohort(cohort) for cohort in raw_cohorts]
    audited_rows = sum(int(cohort["processed_complete_lines"]) for cohort in cohorts)
    if audited_rows != processed:
        raise SafetyError("legacy audit did not assign every complete line to a cohort")
    fingerprint = InputFingerprint(
        canonical_path=str(canonical),
        device=int(after.st_dev),
        inode=int(after.st_ino),
        size=int(after.st_size),
        offset=offset,
        anchor_sha256=digest.hexdigest(),
        rows=audited_rows,
    )
    classification = {
        "cohorts": cohorts,
        "processed_complete_lines": audited_rows,
        "blank_lines": sum(int(cohort["blank_lines"]) for cohort in cohorts),
        "malformed": sum(int(cohort["malformed"]) for cohort in cohorts),
        "invalid": sum(int(cohort["invalid"]) for cohort in cohorts),
        "invalid_total": sum(int(cohort["invalid_total"]) for cohort in cohorts),
        "quality_rejected": sum(int(cohort["quality_rejected"]) for cohort in cohorts),
        "database_candidates": sum(int(cohort["database_candidates"]) for cohort in cohorts),
        "legacy_seen": sum(int(cohort["legacy_seen"]) for cohort in cohorts),
        "legacy_skipped_without_duplicate": sum(
            int(cohort["legacy_skipped_without_duplicate"]) for cohort in cohorts
        ),
        "malformed_reasons": _merge_reason_counts(cohorts, "malformed_reasons"),
        "invalid_reasons": _merge_reason_counts(cohorts, "invalid_reasons"),
        "quality_skip_reasons": _merge_reason_counts(cohorts, "quality_skip_reasons"),
        "wall_clock_drift_candidates": (
            None
            if transition is None
            else _wall_clock_candidate_proof(wall_clock_candidates)
        ),
    }
    return fingerprint, classification, transition_artifact


def audit_legacy(args: argparse.Namespace) -> None:
    from prepare_news_table_rows import load_source_map

    audit_quality_now = _utc_now()
    resolved_code_version, resolved_config_sha256 = _resolve_contract(args)
    initial_identity = _verify_legacy_process(args)
    legacy, initial_state_bytes, offset = _load_bound_legacy_state(args)
    transition = _resolve_legacy_quality_gate_transition(args, offset)
    try:
        source_map = load_source_map(args.source_map)
    except (OSError, KeyError, UnicodeError, ValueError) as exc:
        raise SafetyError("source map is invalid") from exc
    fingerprint, classification, transition_artifact = _audit_input_prefix(
        args.input,
        offset,
        source_map,
        args,
        audit_quality_now=audit_quality_now,
        resolved_transition=transition,
    )
    reconciliation_artifact = _apply_wall_clock_reconciliation(
        classification,
        legacy,
        transition,
        args,
        audit_quality_now,
    )
    _bind_or_validate_legacy_accounting(
        classification,
        legacy,
        transition,
        frozen_offset=offset,
        quality_gate=not args.disable_quality_gate,
        bind=True,
    )
    legacy_seen = _nonnegative_int(legacy, "seen", "legacy checkpoint")
    legacy_inserted = _nonnegative_int(legacy, "inserted", "legacy checkpoint")
    legacy_skipped = _nonnegative_int(legacy, "skipped", "legacy checkpoint")

    legacy_input = str(legacy.get("input") or "").strip()
    if legacy_input:
        try:
            legacy_canonical = str(Path(legacy_input).resolve(strict=True))
        except OSError as exc:
            raise SafetyError("legacy input path is unavailable") from exc
        _require_expected("legacy input path", fingerprint.canonical_path, legacy_canonical)

    final_identity = _verify_legacy_process(args)
    if not identities_match(initial_identity, final_identity):
        raise SafetyError("legacy process identity changed during audit")
    final_state_bytes = args.legacy_state.read_bytes()
    if final_state_bytes != initial_state_bytes:
        raise SafetyError("legacy state changed during audit")
    _verify_transition_provenance(transition)

    audit = {
        "schema_version": LEGACY_AUDIT_SCHEMA_VERSION,
        "kind": "wave1_legacy_prefix_audit",
        "checkpoint_key": args.checkpoint_key,
        "job_id": args.job_id,
        "run_id": args.run_id,
        "audit_quality_now": _audit_quality_now_text(audit_quality_now),
        "input": asdict(fingerprint),
        "legacy_state": {
            "canonical_path": str(args.legacy_state.resolve(strict=True)),
            "sha256": args.expected_state_sha256,
            "offset": offset,
            "seen": legacy_seen,
            "inserted": legacy_inserted,
            "skipped": legacy_skipped,
            "quality_skipped": _nonnegative_int(
                {"quality_skipped": legacy.get("quality_skipped", 0)},
                "quality_skipped",
                "legacy checkpoint",
            ),
            "quality_skip_reasons": legacy.get("quality_skip_reasons", {}),
        },
        "loader_contract": {
            "code_version": resolved_code_version,
            "config_sha256": resolved_config_sha256,
            "source_map": str(args.source_map.resolve(strict=True)),
            "source_map_sha256": _sha256_file(args.source_map.resolve(strict=True)),
            "quality_gate": not args.disable_quality_gate,
            "quality_clock": "wall_clock",
            "min_body_chars": args.min_body_chars,
            "min_published_year": args.min_published_year,
            "future_grace_days": args.future_grace_days,
            "legacy_audit_semantics_version": 4,
        },
        "legacy_quality_gate_transition": transition_artifact,
        "legacy_wall_clock_reconciliation": reconciliation_artifact,
        "classification": classification,
        "legacy_runtime": {
            "pid": args.expected_pid,
            "start_ticks": args.expected_start_ticks,
            "boot_id": args.expected_boot_id,
            "stopped_state_required": bool(args.require_stopped),
        },
    }
    audit["audit_id"] = hashlib.sha256(
        json.dumps(audit, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    payload = (json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    atomic_write_once(args.output, payload)


def _validate_sha256_digest(name: str, value: Any) -> str:
    digest = str(value or "")
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise SafetyError(f"{name} is not a lowercase SHA-256 digest")
    return digest


def _transition_gap_capture_ranges(
    artifact: Any,
    transition: LegacyQualityGateTransition | None,
) -> tuple[tuple[int, int], ...]:
    if transition is None:
        return ()
    if not isinstance(artifact, dict) or not isinstance(artifact.get("invariant_gap"), dict):
        raise SafetyError("legacy transition invariant gap is missing")
    gap = artifact["invariant_gap"]
    start = _nonnegative_int(gap, "start_offset", "audit transition invariant gap")
    end = _nonnegative_int(gap, "end_offset", "audit transition invariant gap")
    if not transition.before.offset <= start <= end <= transition.after.offset:
        raise SafetyError("legacy transition invariant gap is outside the evidence window")
    return ((start, end),)


def _verify_invariant_gap_classifications(
    path: Path,
    bounds: tuple[int, int],
    fingerprint: InputFingerprint,
    source_map: dict[str, Any],
    args: argparse.Namespace,
    audit_quality_now: datetime,
) -> None:
    start, end = bounds
    descriptor, _canonical = _open_regular_nofollow(path)
    try:
        metadata = os.fstat(descriptor)
        if (metadata.st_dev, metadata.st_ino) != (fingerprint.device, fingerprint.inode):
            raise SafetyError("input identity changed before invariant-gap classification")
        if metadata.st_size < end:
            raise SafetyError("input was truncated before invariant-gap classification")
        if start and os.pread(descriptor, 1, start - 1) != b"\n":
            raise SafetyError("invariant gap start is not a complete record boundary")
        if end and os.pread(descriptor, 1, end - 1) != b"\n":
            raise SafetyError("invariant gap end is not a complete record boundary")
        with os.fdopen(os.dup(descriptor), "rb") as handle:
            handle.seek(start)
            remaining = end - start
            while remaining:
                raw = handle.readline(remaining)
                if not raw or not raw.endswith(b"\n"):
                    raise SafetyError("invariant gap contains an incomplete record")
                remaining -= len(raw)
                try:
                    text = raw.decode("utf-8").strip()
                except UnicodeDecodeError as exc:
                    raise SafetyError("invariant gap contains invalid UTF-8") from exc
                if not text:
                    continue
                try:
                    value = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if not isinstance(value, dict):
                    raise SafetyError("invariant gap contains non-object JSON")
                try:
                    classified = classify_news_value(
                        value,
                        source_map,
                        disable_quality_gate=False,
                        min_body_chars=args.min_body_chars,
                        min_published_year=args.min_published_year,
                        future_grace_days=args.future_grace_days,
                        fail_on_exception=True,
                        quality_now=audit_quality_now,
                    )
                except SafetyError:
                    raise
                except Exception as exc:
                    raise SafetyError(
                        "invariant gap classification raised an unexpected exception"
                    ) from exc
                if classified.classification == "quality_rejected":
                    raise SafetyError("invariant gap contains a quality-sensitive record")
        after = os.fstat(descriptor)
        if (after.st_dev, after.st_ino) != (fingerprint.device, fingerprint.inode):
            raise SafetyError("input identity changed during invariant-gap classification")
    finally:
        os.close(descriptor)


def _verify_wall_clock_drift_candidate_records(
    path: Path,
    proof: dict[str, Any],
    fingerprint: InputFingerprint,
    transition: LegacyQualityGateTransition,
    source_map: dict[str, Any],
    args: argparse.Namespace,
    audit_quality_now: datetime,
) -> None:
    records = proof.get("records")
    if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
        raise SafetyError("wall-clock drift candidate records are missing")
    _require_expected(
        "wall-clock drift candidate count",
        _nonnegative_int(proof, "count", "wall-clock drift candidates"),
        len(records),
    )
    _require_expected(
        "wall-clock drift candidate proof",
        proof,
        _wall_clock_candidate_proof(records),
    )
    descriptor, _canonical = _open_regular_nofollow(path)
    previous_end = transition.after.offset
    try:
        metadata = os.fstat(descriptor)
        if (metadata.st_dev, metadata.st_ino) != (fingerprint.device, fingerprint.inode):
            raise SafetyError("input identity changed before wall-clock candidate verification")
        for index, record in enumerate(records):
            start = _nonnegative_int(record, "start_offset", f"wall-clock candidate {index}")
            end = _nonnegative_int(record, "end_offset", f"wall-clock candidate {index}")
            raw_sha256 = _validate_sha256_digest(
                f"wall-clock candidate {index} raw digest",
                record.get("raw_sha256"),
            )
            if not transition.after.offset <= start < end <= fingerprint.offset:
                raise SafetyError("wall-clock drift candidate is outside the gated suffix")
            if start < previous_end:
                raise SafetyError("wall-clock drift candidates overlap or are out of order")
            if start and os.pread(descriptor, 1, start - 1) != b"\n":
                raise SafetyError("wall-clock drift candidate start is not a record boundary")
            raw = os.pread(descriptor, end - start, start)
            if len(raw) != end - start or not raw.endswith(b"\n") or raw.count(b"\n") != 1:
                raise SafetyError("wall-clock drift candidate is not one complete record")
            _require_expected(
                f"wall-clock candidate {index} raw digest",
                hashlib.sha256(raw).hexdigest(),
                raw_sha256,
            )
            try:
                value = json.loads(raw.decode("utf-8").strip())
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SafetyError("wall-clock drift candidate JSON is invalid") from exc
            if not isinstance(value, dict):
                raise SafetyError("wall-clock drift candidate is non-object JSON")
            classified = classify_news_value(
                value,
                source_map,
                disable_quality_gate=False,
                min_body_chars=args.min_body_chars,
                min_published_year=args.min_published_year,
                future_grace_days=args.future_grace_days,
                fail_on_exception=True,
                quality_now=audit_quality_now,
            )
            actual = _wall_clock_drift_candidate(
                value,
                classified,
                audit_quality_now=audit_quality_now,
                future_grace_days=args.future_grace_days,
                start_offset=start,
                end_offset=end,
                raw=raw,
            )
            _require_expected(f"wall-clock drift candidate {index}", actual, record)
            previous_end = end
        after = os.fstat(descriptor)
        if (after.st_dev, after.st_ino) != (fingerprint.device, fingerprint.inode):
            raise SafetyError("input identity changed during wall-clock candidate verification")
    finally:
        os.close(descriptor)


def _validate_legacy_transition_artifact(
    artifact: Any,
    transition: LegacyQualityGateTransition | None,
    classification: dict[str, Any],
    input_anchors: dict[int, dict[str, Any]],
    input_ranges: dict[tuple[int, int], dict[str, Any]],
) -> None:
    if transition is None:
        _require_expected("audit transition evidence", artifact, None)
        return
    if not isinstance(artifact, dict):
        raise SafetyError("legacy transition audit binding is missing")
    _require_expected(
        "audit transition evidence source",
        artifact.get("source"),
        "recorded_restart_checkpoint_window",
    )
    _require_expected(
        "audit transition provenance",
        artifact.get("provenance"),
        {
            "canonical_path": transition.provenance_path,
            "sha256": transition.provenance_sha256,
        },
    )
    _require_expected(
        "audit transition evidence digest",
        artifact.get("evidence_sha256"),
        _legacy_transition_evidence_sha256(transition),
    )
    cohorts = classification.get("cohorts")
    if (
        not isinstance(cohorts, list)
        or len(cohorts) != 3
        or any(not isinstance(cohort, dict) for cohort in cohorts)
    ):
        raise SafetyError("legacy transition audit cohorts are missing")
    for label, expected_checkpoint, expected_lines in (
        (
            "pre",
            transition.before,
            _nonnegative_int(cohorts[0], "processed_complete_lines", "audit pre cohort"),
        ),
        (
            "post",
            transition.after,
            _nonnegative_int(cohorts[0], "processed_complete_lines", "audit pre cohort")
            + _nonnegative_int(
                cohorts[1], "processed_complete_lines", "audit transition cohort"
            ),
        ),
    ):
        value = artifact.get(f"{label}_checkpoint")
        if not isinstance(value, dict):
            raise SafetyError(f"legacy transition {label} checkpoint binding is missing")
        for field, expected in asdict(expected_checkpoint).items():
            _require_expected(f"audit transition {label} {field}", value.get(field), expected)
        _require_expected(
            f"audit transition {label} complete lines",
            value.get("complete_lines"),
            expected_lines,
        )
        anchor_digest = _validate_sha256_digest(
            f"audit transition {label} anchor", value.get("anchor_sha256")
        )
        actual_anchor = input_anchors.get(expected_checkpoint.offset)
        if not isinstance(actual_anchor, dict):
            raise SafetyError(f"input transition {label} anchor was not captured")
        _require_expected(
            f"input transition {label} anchor",
            actual_anchor.get("anchor_sha256"),
            anchor_digest,
        )
        _require_expected(
            f"input transition {label} complete lines",
            actual_anchor.get("complete_lines"),
            expected_lines,
        )
    post = artifact["post_checkpoint"]
    _require_expected(
        "audit transition post quality count",
        post.get("quality_skipped"),
        transition.after_quality_skipped,
    )
    _require_expected(
        "audit transition post quality reasons",
        post.get("quality_skip_reasons"),
        transition.after_quality_reasons,
    )

    candidates = artifact.get("candidate_boundaries")
    gap = artifact.get("invariant_gap")
    if not isinstance(candidates, dict) or not isinstance(gap, dict):
        raise SafetyError("legacy transition candidate proof is missing")
    count = _nonnegative_int(candidates, "count", "audit transition candidates")
    earliest = _nonnegative_int(
        candidates, "earliest_offset", "audit transition candidates"
    )
    latest = _nonnegative_int(candidates, "latest_offset", "audit transition candidates")
    if count < 1 or not transition.before.offset <= earliest <= latest <= transition.after.offset:
        raise SafetyError("legacy transition candidate range is invalid")
    for field, expected in (
        ("canonical_offset", earliest),
        ("canonical_policy", "earliest_equivalent_boundary"),
        ("all_candidates_semantically_equivalent", True),
        ("quality_rejected_after_canonical", transition.after_quality_skipped),
    ):
        _require_expected(f"audit transition candidates {field}", candidates.get(field), expected)
    potential_quality = _nonnegative_int(
        candidates,
        "potential_quality_rejected",
        "audit transition candidates",
    )
    quality_before = _nonnegative_int(
        candidates,
        "quality_rejected_before_canonical",
        "audit transition candidates",
    )
    if potential_quality != quality_before + transition.after_quality_skipped:
        raise SafetyError("audit transition potential quality accounting is inconsistent")
    potential_reasons = candidates.get("potential_quality_skip_reasons")
    if not isinstance(potential_reasons, dict):
        raise SafetyError("audit transition potential quality reasons are invalid")
    for field, expected in (
        ("start_offset", earliest),
        ("end_offset", latest),
        ("bytes", latest - earliest),
        ("complete_lines", count - 1),
        ("quality_sensitive_records", 0),
    ):
        _require_expected(f"audit transition invariant gap {field}", gap.get(field), expected)
    _validate_sha256_digest("audit transition invariant gap", gap.get("sha256"))
    actual_gap = input_ranges.get((earliest, latest))
    if not isinstance(actual_gap, dict):
        raise SafetyError("input transition invariant gap was not captured")
    for field in ("sha256", "bytes", "complete_lines"):
        _require_expected(
            f"input transition invariant gap {field}",
            actual_gap.get(field),
            gap.get(field),
        )


def migrate_legacy(args: argparse.Namespace) -> None:
    resolved_code_version, resolved_config_sha256 = _resolve_contract(args)
    initial_identity = _verify_legacy_process(args)
    legacy, initial_state_bytes, offset = _load_bound_legacy_state(args)
    transition = _resolve_legacy_quality_gate_transition(args, offset)

    validate_private_file(args.audit)
    audit_bytes = args.audit.read_bytes()
    expected_audit_sha256 = _validate_sha256_digest(
        "expected audit digest", getattr(args, "expected_audit_sha256", None)
    )
    _require_expected(
        "audit file digest", hashlib.sha256(audit_bytes).hexdigest(), expected_audit_sha256
    )
    audit = load_json_object(args.audit)
    if (
        audit.get("schema_version") != LEGACY_AUDIT_SCHEMA_VERSION
        or audit.get("kind") != "wave1_legacy_prefix_audit"
    ):
        raise SafetyError("legacy audit schema is invalid")
    audit_quality_now = _parse_audit_quality_now(audit.get("audit_quality_now"))
    supplied_audit_id = audit.get("audit_id")
    audit_without_id = {key: value for key, value in audit.items() if key != "audit_id"}
    calculated_audit_id = hashlib.sha256(
        json.dumps(audit_without_id, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    _require_expected("audit ID", supplied_audit_id, calculated_audit_id)
    for field, expected in (
        ("checkpoint_key", args.checkpoint_key),
        ("job_id", args.job_id),
        ("run_id", args.run_id),
    ):
        _require_expected(f"audit {field}", audit.get(field), expected)
    audit_contract = audit.get("loader_contract")
    if not isinstance(audit_contract, dict):
        raise SafetyError("legacy audit loader contract is missing")
    _require_expected("audit code version", audit_contract.get("code_version"), resolved_code_version)
    _require_expected(
        "audit config digest",
        audit_contract.get("config_sha256"),
        resolved_config_sha256,
    )
    _require_expected(
        "audit semantics version",
        audit_contract.get("legacy_audit_semantics_version"),
        4,
    )
    _require_expected("audit target quality clock", audit_contract.get("quality_clock"), "wall_clock")
    audit_state = audit.get("legacy_state")
    if not isinstance(audit_state, dict):
        raise SafetyError("legacy audit state binding is missing")
    _require_expected(
        "audit state path",
        audit_state.get("canonical_path"),
        str(args.legacy_state.resolve(strict=True)),
    )
    _require_expected("audit state digest", audit_state.get("sha256"), args.expected_state_sha256)
    _require_expected("audit state offset", int(audit_state.get("offset", -1)), offset)
    audit_runtime = audit.get("legacy_runtime")
    if not isinstance(audit_runtime, dict):
        raise SafetyError("legacy audit runtime binding is missing")
    for field, expected in (
        ("pid", args.expected_pid),
        ("start_ticks", args.expected_start_ticks),
        ("boot_id", args.expected_boot_id),
        ("stopped_state_required", bool(args.require_stopped)),
    ):
        _require_expected(f"audit runtime {field}", audit_runtime.get(field), expected)

    anchor_offsets = (
        ()
        if transition is None
        else (transition.before.offset, transition.after.offset)
    )
    audit_transition_artifact = audit.get("legacy_quality_gate_transition")
    capture_ranges = _transition_gap_capture_ranges(
        audit_transition_artifact,
        transition,
    )
    fingerprint, input_anchors, input_ranges = _fingerprint_input_with_anchors(
        args.input,
        offset=offset,
        require_stable_size=False,
        anchor_offsets=anchor_offsets,
        capture_ranges=capture_ranges,
    )
    for field, expected in (
        ("canonical_path", args.expected_input_path),
        ("device", args.expected_input_device),
        ("inode", args.expected_input_inode),
        ("anchor_sha256", args.expected_input_anchor_sha256),
    ):
        _require_expected(f"input {field}", getattr(fingerprint, field), expected)
    if fingerprint.size < args.expected_input_size:
        raise SafetyError("input size is below the explicitly supplied expectation")

    audit_input = audit.get("input")
    if not isinstance(audit_input, dict):
        raise SafetyError("legacy audit input binding is missing")
    for field in ("canonical_path", "device", "inode", "offset", "anchor_sha256", "rows"):
        _require_expected(f"audit input {field}", audit_input.get(field), getattr(fingerprint, field))
    if fingerprint.size < int(audit_input.get("size", -1)):
        raise SafetyError("input was truncated after the legacy audit")

    classification = audit.get("classification")
    if not isinstance(classification, dict):
        raise SafetyError("legacy audit classifications are missing")
    _validate_legacy_transition_artifact(
        audit_transition_artifact,
        transition,
        classification,
        input_anchors,
        input_ranges,
    )
    if capture_ranges:
        from prepare_news_table_rows import load_source_map

        source_map_path = args.source_map.resolve(strict=True)
        _require_expected(
            "audit source map path",
            audit_contract.get("source_map"),
            str(source_map_path),
        )
        _require_expected(
            "audit source map digest",
            audit_contract.get("source_map_sha256"),
            _sha256_file(source_map_path),
        )
        try:
            source_map = load_source_map(args.source_map)
        except (OSError, KeyError, UnicodeError, ValueError) as exc:
            raise SafetyError("source map is invalid during invariant-gap verification") from exc
        _verify_invariant_gap_classifications(
            args.input,
            capture_ranges[0],
            fingerprint,
            source_map,
            args,
            audit_quality_now,
        )
        _require_expected(
            "source map digest after invariant-gap verification",
            _sha256_file(source_map_path),
            audit_contract.get("source_map_sha256"),
        )
        if _resolve_wall_clock_reconciliation_reason(args, transition) is not None:
            candidate_proof = classification.get("wall_clock_drift_candidates")
            if not isinstance(candidate_proof, dict) or transition is None:
                raise SafetyError("wall-clock drift candidate proof is missing")
            _verify_wall_clock_drift_candidate_records(
                args.input,
                candidate_proof,
                fingerprint,
                transition,
                source_map,
                args,
                audit_quality_now,
            )
    _validate_wall_clock_reconciliation(
        audit.get("legacy_wall_clock_reconciliation"),
        classification,
        legacy,
        transition,
        args,
        audit_quality_now,
    )
    counters = _bind_or_validate_legacy_accounting(
        classification,
        legacy,
        transition,
        frozen_offset=offset,
        quality_gate=not args.disable_quality_gate,
        bind=False,
    )
    if counters["seen"] != fingerprint.rows:
        raise SafetyError("seen must equal the number of complete input records before offset")
    for field in ("seen", "inserted", "skipped", "quality_skipped"):
        current_value = _nonnegative_int(
            {field: legacy.get(field, 0 if field == "quality_skipped" else -1)},
            field,
            "legacy checkpoint",
        )
        _require_expected(
            f"audit state {field}",
            _nonnegative_int(audit_state, field, "audit state"),
            current_value,
        )
    _require_expected(
        "audit state quality reasons",
        audit_state.get("quality_skip_reasons"),
        legacy.get("quality_skip_reasons", {}),
    )
    legacy_skipped = _nonnegative_int(legacy, "skipped", "legacy checkpoint")

    final_identity = _verify_legacy_process(args)
    if not identities_match(initial_identity, final_identity):
        raise SafetyError("legacy process identity changed during migration")
    if args.legacy_state.read_bytes() != initial_state_bytes:
        raise SafetyError("legacy state changed during migration")
    _verify_transition_provenance(transition)

    checkpoint = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "checkpoint_key": args.checkpoint_key,
        "job_id": args.job_id,
        "run_id": args.run_id,
        "input": asdict(fingerprint),
        "code_version": resolved_code_version,
        "config_sha256": resolved_config_sha256,
        "counters": counters,
        "legacy_seen": int(legacy.get("seen", -1)),
        "quality_skip_reasons": classification.get("quality_skip_reasons", {}),
        "completed": False,
        "migrated_from": {
            "legacy_state_sha256": args.expected_state_sha256,
            "pid": args.expected_pid,
            "start_ticks": args.expected_start_ticks,
            "boot_id": args.expected_boot_id,
            "legacy_seen": int(legacy.get("seen", -1)),
            "legacy_skipped": legacy_skipped,
            "audit_sha256": hashlib.sha256(audit_bytes).hexdigest(),
            "audit_id": audit.get("audit_id"),
            "legacy_quality_gate_transition": (
                None
                if audit.get("legacy_quality_gate_transition") is None
                else audit["legacy_quality_gate_transition"]["candidate_boundaries"]
            ),
            "legacy_wall_clock_reconciliation": (
                None
                if audit.get("legacy_wall_clock_reconciliation") is None
                else {
                    "mode": audit["legacy_wall_clock_reconciliation"]["mode"],
                    "allowed_reason": audit["legacy_wall_clock_reconciliation"][
                        "allowed_reason"
                    ],
                    "delta": audit["legacy_wall_clock_reconciliation"]["delta"],
                    "state_sha256": audit["legacy_wall_clock_reconciliation"][
                        "state_binding"
                    ]["sha256"],
                }
            ),
        },
        "legacy_runtime_control": {
            "automatic_stop_supported": False,
            "reason": "legacy_process_has_no_authenticated_control_socket",
            "required_action": "wait_for_completion_or_use_an_approved_manual_maintenance_window",
            "pidfd_force_stop_requires_supported_control_python_and_kernel": True,
        },
        "migration_id": hashlib.sha256(
            (
                args.expected_state_sha256
                + fingerprint.anchor_sha256
                + resolved_config_sha256
                + resolved_code_version
                + expected_audit_sha256
                + str(supplied_audit_id)
            ).encode("utf-8")
        ).hexdigest(),
        "updated_at": None,
    }
    state_bytes = (
        json.dumps(checkpoint, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    sql_bytes = render_checkpoint_seed_sql(checkpoint).encode("utf-8")
    atomic_write_once(args.output_state, state_bytes)
    atomic_write_once(args.output_sql, sql_bytes)


def seal_input(args: argparse.Namespace) -> None:
    validate_safe_identifier("job_id", args.job_id)
    validate_safe_identifier("run_id", args.run_id)
    fingerprint = fingerprint_input(args.input, require_stable_size=True)
    for field, expected in (
        ("canonical_path", args.expected_input_path),
        ("device", args.expected_input_device),
        ("inode", args.expected_input_inode),
        ("size", args.expected_final_bytes),
        ("anchor_sha256", args.expected_sha256),
    ):
        if expected is not None:
            _require_expected(f"sealed input {field}", getattr(fingerprint, field), expected)
    if args.expected_rows is not None:
        _require_expected("sealed input rows", fingerprint.rows, args.expected_rows)
    manifest = {
        "schema_version": 1,
        "sealed": True,
        "job_id": args.job_id,
        "run_id": args.run_id,
        "input": {
            "canonical_path": fingerprint.canonical_path,
            "device": fingerprint.device,
            "inode": fingerprint.inode,
            "final_bytes": fingerprint.size,
            "rows": fingerprint.rows,
            "sha256": fingerprint.anchor_sha256,
        },
        "sealed_at": time.time(),
    }
    if args.output.exists() or args.output.is_symlink():
        validate_private_file(args.output)
        existing = load_json_object(args.output)
        immutable_fields_match = all(
            existing.get(key) == manifest.get(key)
            for key in ("schema_version", "sealed", "job_id", "run_id", "input")
        )
        if not immutable_fields_match:
            raise SafetyError("sealed manifest already exists with different immutable content")
        return
    atomic_write_json(args.output, manifest)


def _identity_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--proc-root", type=Path, default=Path("/proc"))


def _loader_contract_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--checkpoint-key", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--source-map", type=Path, required=True)
    parser.add_argument("--code-version", default="")
    parser.add_argument("--disable-quality-gate", action="store_true")
    parser.add_argument("--min-body-chars", type=int, default=120)
    parser.add_argument("--min-published-year", type=int, default=2000)
    parser.add_argument("--future-grace-days", type=int, default=1)


def _legacy_snapshot_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--legacy-state", type=Path, required=True)
    parser.add_argument("--expected-pid", type=int, required=True)
    parser.add_argument("--expected-start-ticks", type=int, required=True)
    parser.add_argument("--expected-boot-id", required=True)
    parser.add_argument("--proc-root", type=Path, default=Path("/proc"))
    parser.add_argument("--expected-state-sha256", required=True)
    parser.add_argument("--require-stopped", action="store_true", required=True)


def _legacy_transition_arguments(parser: argparse.ArgumentParser) -> None:
    for checkpoint in ("pre", "post"):
        parser.add_argument(f"--legacy-transition-{checkpoint}-offset", type=int)
        parser.add_argument(f"--legacy-transition-{checkpoint}-seen", type=int)
        parser.add_argument(f"--legacy-transition-{checkpoint}-inserted", type=int)
        parser.add_argument(f"--legacy-transition-{checkpoint}-skipped", type=int)
    parser.add_argument("--legacy-transition-post-quality-skipped", type=int)
    parser.add_argument(
        "--legacy-transition-post-quality-reasons",
        help="JSON object of cumulative quality-rejection reason counts at the post checkpoint.",
    )
    parser.add_argument("--legacy-transition-provenance-file", type=Path)
    parser.add_argument("--legacy-transition-provenance-sha256")
    parser.add_argument(
        "--legacy-wall-clock-reconcile-reason",
        choices=(LEGACY_WALL_CLOCK_RECONCILIATION_REASON,),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    ddl = commands.add_parser("emit-ddl", help="Write the idempotent checkpoint table DDL.")
    ddl.add_argument("--output", type=Path, required=True)

    secret = commands.add_parser("materialize-secret", help="Materialize a private DB secret file.")
    secret.add_argument("--output", type=Path, required=True)

    validate_secret = commands.add_parser("validate-secret")
    validate_secret.add_argument("--path", type=Path, required=True)

    runtime_meta = commands.add_parser("write-runtime-meta")
    _identity_arguments(runtime_meta)
    runtime_meta.add_argument("--instance-name", required=True)
    runtime_meta.add_argument("--instance-id", required=True)
    runtime_meta.add_argument("--output", type=Path, required=True)
    runtime_meta.add_argument("--expected-exe", type=Path)
    runtime_meta.add_argument("--expected-cwd", type=Path)
    runtime_meta.add_argument("--require-session-leader", action="store_true")
    runtime_meta.add_argument("--previous-meta", type=Path)

    verify = commands.add_parser("verify-runtime")
    verify.add_argument("--meta", type=Path, required=True)
    verify.add_argument("--ready", type=Path)
    verify.add_argument("--require-ready-status")
    verify.add_argument("--proc-root", type=Path, default=Path("/proc"))

    attach = commands.add_parser("attach-runtime-socket")
    attach.add_argument("--meta", type=Path, required=True)
    attach.add_argument("--ready", type=Path, required=True)
    attach.add_argument("--socket", type=Path, required=True)
    attach.add_argument("--proc-root", type=Path, default=Path("/proc"))

    control = commands.add_parser("socket-control")
    control.add_argument("--meta", type=Path, required=True)
    control.add_argument("--socket", type=Path, required=True)
    control.add_argument(
        "--command",
        dest="control_command",
        choices=("status", "stop"),
        required=True,
    )
    control.add_argument("--timeout", type=float, default=2.0)
    control.add_argument("--proc-root", type=Path, default=Path("/proc"))

    alive = commands.add_parser("pid-alive")
    _identity_arguments(alive)

    dead = commands.add_parser("runtime-dead")
    dead.add_argument("--meta", type=Path, required=True)
    dead.add_argument("--proc-root", type=Path, default=Path("/proc"))

    pidfd = commands.add_parser("pidfd-signal")
    pidfd.add_argument("--meta", type=Path, required=True)
    pidfd.add_argument("--signal", choices=("TERM", "KILL"), required=True)
    pidfd.add_argument("--proc-root", type=Path, default=Path("/proc"))

    commands.add_parser("check-pidfd")

    audit = commands.add_parser(
        "audit-legacy",
        help="Read and classify a frozen legacy checkpoint prefix without database access.",
    )
    _loader_contract_arguments(audit)
    _legacy_snapshot_arguments(audit)
    _legacy_transition_arguments(audit)
    audit.add_argument("--output", type=Path, required=True)

    migrate = commands.add_parser("migrate-legacy")
    _loader_contract_arguments(migrate)
    _legacy_snapshot_arguments(migrate)
    _legacy_transition_arguments(migrate)
    migrate.add_argument("--audit", type=Path, required=True)
    migrate.add_argument("--expected-audit-sha256", required=True)
    migrate.add_argument("--output-state", type=Path, required=True)
    migrate.add_argument("--output-sql", type=Path, required=True)
    migrate.add_argument("--expected-input-path", required=True)
    migrate.add_argument("--expected-input-device", type=int, required=True)
    migrate.add_argument("--expected-input-inode", type=int, required=True)
    migrate.add_argument("--expected-input-size", type=int, required=True)
    migrate.add_argument("--expected-input-anchor-sha256", required=True)

    seal = commands.add_parser("seal-input")
    seal.add_argument("--input", type=Path, required=True)
    seal.add_argument("--output", type=Path, required=True)
    seal.add_argument("--job-id", required=True)
    seal.add_argument("--run-id", required=True)
    seal.add_argument("--expected-input-path")
    seal.add_argument("--expected-input-device", type=int)
    seal.add_argument("--expected-input-inode", type=int)
    seal.add_argument("--expected-final-bytes", type=int)
    seal.add_argument("--expected-rows", type=int)
    seal.add_argument("--expected-sha256")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "emit-ddl":
            atomic_write_bytes(args.output, CHECKPOINT_DDL.encode("utf-8"))
        elif args.command == "materialize-secret":
            materialize_database_secret(args.output)
        elif args.command == "validate-secret":
            validate_private_file(args.path)
        elif args.command == "write-runtime-meta":
            identity = capture_process_identity(args.pid, proc_root=args.proc_root)
            if args.previous_meta:
                previous = load_json_object(args.previous_meta).get("identity")
                if not isinstance(previous, dict):
                    raise SafetyError("previous runtime identity is missing")
                for key in ("pid", "start_ticks", "boot_id", "pid_namespace"):
                    if previous.get(key) != identity.get(key):
                        raise SafetyError("runtime core identity changed during startup")
            if args.expected_exe and identity["exe"] != str(args.expected_exe.resolve(strict=True)):
                raise SafetyError("runtime executable is not the expected interpreter")
            if args.expected_cwd and identity["cwd"] != str(args.expected_cwd.resolve(strict=True)):
                raise SafetyError("runtime cwd is not the expected project root")
            if args.require_session_leader and (
                identity["sid"] != args.pid or identity["pgid"] != args.pid
            ):
                raise SafetyError("runtime is not its own session and process-group leader")
            payload = {
                "schema_version": RUNTIME_IDENTITY_SCHEMA_VERSION,
                "instance_name": validate_safe_identifier("instance_name", args.instance_name),
                "instance_id": validate_safe_identifier("instance_id", args.instance_id),
                "identity": identity,
                "created_at": time.time(),
            }
            atomic_write_json(args.output, payload)
        elif args.command == "verify-runtime":
            verified = verify_runtime_identity(args.meta, args.ready, proc_root=args.proc_root)
            if (
                args.require_ready_status
                and verified.get("ready_status") != args.require_ready_status
            ):
                raise SafetyError("readiness status does not match the required state")
        elif args.command == "attach-runtime-socket":
            attach_runtime_socket(
                args.meta,
                args.ready,
                args.socket,
                proc_root=args.proc_root,
            )
        elif args.command == "socket-control":
            socket_control(
                args.meta,
                args.socket,
                args.control_command,
                timeout=args.timeout,
                proc_root=args.proc_root,
            )
        elif args.command == "pid-alive":
            capture_process_identity(args.pid, proc_root=args.proc_root)
        elif args.command == "runtime-dead":
            if not runtime_identity_is_dead(args.meta, proc_root=args.proc_root):
                raise SafetyError("runtime identity is still alive or death is unproven")
        elif args.command == "pidfd-signal":
            number = signal.SIGTERM if args.signal == "TERM" else signal.SIGKILL
            pidfd_send(args.meta, number, proc_root=args.proc_root)
        elif args.command == "check-pidfd":
            check_pidfd_support()
        elif args.command == "audit-legacy":
            audit_legacy(args)
        elif args.command == "migrate-legacy":
            migrate_legacy(args)
        elif args.command == "seal-input":
            seal_input(args)
        else:
            raise SafetyError("unsupported command")
    except SafetyError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
