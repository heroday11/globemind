"""Explicit append-only snapshots of search contracts and returned IDs only."""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import stat
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from api.features.search.receipts import (
    QueryReceiptIntegrityError,
    canonical_json_bytes,
    canonical_sha256,
    verify_query_receipt,
)
from api.models.schemas import SearchQueryReceipt

SEARCH_SNAPSHOT_SCHEMA_VERSION = "search-snapshot-v1"
SEARCH_SNAPSHOT_LIST_SCHEMA_VERSION = "search-snapshot-list-v1"
SEARCH_SNAPSHOT_REPLAY_SCHEMA_VERSION = "search-snapshot-replay-v1"
MAX_SNAPSHOTS_PER_USER = 10_000
MAX_RECORD_BYTES = 1024 * 1024
_SNAPSHOT_ID = re.compile(
    r"^search-snap-(?P<stamp>[0-9]{8}T[0-9]{12}Z)-(?P<nonce>[0-9a-f]{16})$"
)
_ACTOR_REF = re.compile(r"^user:(?P<actor_id>[1-9][0-9]*)$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_RELEASE_ROOT = Path("/root/data/releases/globemind")
_RECORD_KEYS = frozenset(
    {
        "schema_version",
        "snapshot_id",
        "snapshot_scope",
        "previous_snapshot_id",
        "previous_integrity_sha256",
        "captured_at",
        "actor_ref",
        "receipt",
        "receipt_sha256",
        "normalized_contract_sha256",
        "ordered_returned_ids_sha256",
        "corpus_snapshot_status",
        "body_persistence",
        "integrity_sha256",
    }
)


class SearchSnapshotError(RuntimeError):
    """Base class for bounded public snapshot failures."""


class SearchSnapshotUnavailable(SearchSnapshotError):
    """Snapshot storage or a stored record is unsafe or inconsistent."""


class SearchSnapshotConflict(SearchSnapshotError):
    """Optimistic identity or append-only uniqueness no longer matches."""


class SearchSnapshotNotFound(SearchSnapshotError):
    """The authenticated user's requested snapshot does not exist."""


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("snapshot timestamps require timezone information")
    return current.astimezone(timezone.utc)


def _snapshot_id(value: datetime) -> str:
    return f"search-snap-{value.strftime('%Y%m%dT%H%M%S%fZ')}-{secrets.token_hex(8)}"


def _path_has_symlink(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.exists() and current.is_symlink():
            return True
    return False


def _ensure_directory(path: Path, *, mode: int = 0o750) -> None:
    if _path_has_symlink(path):
        raise SearchSnapshotUnavailable("snapshot path contains a symbolic link")
    try:
        path.mkdir(parents=True, exist_ok=True, mode=mode)
        if path.is_symlink() or not path.is_dir():
            raise SearchSnapshotUnavailable("snapshot directory is unavailable")
        os.chmod(path, mode)
    except SearchSnapshotUnavailable:
        raise
    except OSError as exc:
        raise SearchSnapshotUnavailable("snapshot directory is unavailable") from exc


def _duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise SearchSnapshotUnavailable("snapshot record has a duplicate JSON key")
        payload[key] = value
    return payload


def _read_json(path: Path) -> dict[str, Any]:
    if _path_has_symlink(path):
        raise SearchSnapshotUnavailable("snapshot record path contains a symbolic link")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise SearchSnapshotUnavailable("snapshot record has an unsafe file type or link count")
            if metadata.st_size > MAX_RECORD_BYTES:
                raise SearchSnapshotUnavailable("snapshot record exceeds its size bound")
            encoded = b""
            while len(encoded) <= MAX_RECORD_BYTES:
                chunk = os.read(descriptor, min(65536, MAX_RECORD_BYTES + 1 - len(encoded)))
                if not chunk:
                    break
                encoded += chunk
            if len(encoded) > MAX_RECORD_BYTES:
                raise SearchSnapshotUnavailable("snapshot record exceeds its size bound")
        finally:
            os.close(descriptor)
        payload = json.loads(encoded.decode("utf-8"), object_pairs_hook=_duplicate_keys)
    except SearchSnapshotError:
        raise
    except FileNotFoundError as exc:
        raise SearchSnapshotNotFound("search snapshot was not found") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SearchSnapshotUnavailable("snapshot record is unreadable") from exc
    if not isinstance(payload, dict):
        raise SearchSnapshotUnavailable("snapshot record root is invalid")
    return payload


def _write_once(path: Path, payload: Mapping[str, Any]) -> None:
    _ensure_directory(path.parent)
    if path.exists() or path.is_symlink():
        raise SearchSnapshotConflict("snapshot identifier already exists")
    encoded = canonical_json_bytes(dict(payload))
    if len(encoded) > MAX_RECORD_BYTES:
        raise SearchSnapshotUnavailable("snapshot record exceeds its size bound")
    descriptor = -1
    temporary = ""
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=".search-snapshot-", dir=path.parent)
        os.fchmod(descriptor, 0o640)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        os.unlink(temporary)
        temporary = ""
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except SearchSnapshotError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise SearchSnapshotUnavailable("snapshot record could not be committed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _record_hash_material(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "integrity_sha256"}


class SearchSnapshotLedger:
    """Per-user append-only ledger; reads never create its durable root."""

    def __init__(self, root: Path) -> None:
        raw = Path(root)
        if not raw.is_absolute():
            raise SearchSnapshotUnavailable("snapshot root must be absolute")
        self.root = Path(os.path.abspath(os.fspath(raw)))
        if _path_has_symlink(self.root):
            raise SearchSnapshotUnavailable("snapshot root contains a symbolic link")
        try:
            self.root.relative_to(_FORBIDDEN_RELEASE_ROOT)
        except ValueError:
            pass
        else:
            raise SearchSnapshotUnavailable("snapshot root cannot be inside release evidence")

    @staticmethod
    def _actor_id(actor_id: int) -> int:
        if isinstance(actor_id, bool) or not isinstance(actor_id, int) or actor_id <= 0:
            raise ValueError("actor id must be a positive integer")
        return actor_id

    def _record_root(self, actor_id: int) -> Path:
        return self.root / "users" / str(self._actor_id(actor_id)) / "records"

    @contextmanager
    def _locked(self) -> Iterator[None]:
        _ensure_directory(self.root)
        lock_path = self.root / ".search-snapshots.lock"
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = -1
        try:
            descriptor = os.open(lock_path, flags, 0o600)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise SearchSnapshotUnavailable("snapshot lock has an unsafe file type or link count")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        except SearchSnapshotError:
            raise
        except OSError as exc:
            raise SearchSnapshotUnavailable("snapshot lock is unavailable") from exc
        finally:
            if descriptor >= 0:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)

    def _record_files(self, actor_id: int) -> list[Path]:
        record_root = self._record_root(actor_id)
        if _path_has_symlink(record_root):
            raise SearchSnapshotUnavailable("snapshot record path contains a symbolic link")
        if not record_root.exists():
            return []
        if not record_root.is_dir():
            raise SearchSnapshotUnavailable("snapshot record directory is invalid")
        entries = list(record_root.iterdir())
        if any(
            entry.is_symlink()
            or not entry.is_file()
            or entry.suffix != ".json"
            or _SNAPSHOT_ID.fullmatch(entry.stem) is None
            for entry in entries
        ):
            raise SearchSnapshotUnavailable("snapshot record directory contains an invalid entry")
        if len(entries) > MAX_SNAPSHOTS_PER_USER:
            raise SearchSnapshotUnavailable("snapshot record bound was exceeded")
        return sorted(entries)

    def _validate_record(
        self,
        path: Path,
        payload: dict[str, Any],
        *,
        actor_id: int,
        expected_previous: str | None,
        expected_previous_integrity: str | None,
    ) -> dict[str, Any]:
        snapshot_id = str(payload.get("snapshot_id") or "")
        match = _SNAPSHOT_ID.fullmatch(snapshot_id)
        actor_ref = str(payload.get("actor_ref") or "")
        actor_match = _ACTOR_REF.fullmatch(actor_ref)
        try:
            captured = datetime.fromisoformat(str(payload.get("captured_at") or "").replace("Z", "+00:00"))
        except ValueError as exc:
            raise SearchSnapshotUnavailable("snapshot capture time is invalid") from exc
        if captured.tzinfo is None:
            raise SearchSnapshotUnavailable("snapshot capture time is invalid")
        if (
            set(payload) != _RECORD_KEYS
            or payload.get("schema_version") != SEARCH_SNAPSHOT_SCHEMA_VERSION
            or match is None
            or path.name != f"{snapshot_id}.json"
            or captured.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") != match.group("stamp")
            or actor_match is None
            or int(actor_match.group("actor_id")) != actor_id
            or payload.get("previous_snapshot_id") != expected_previous
            or payload.get("previous_integrity_sha256") != expected_previous_integrity
            or payload.get("snapshot_scope") != "query-contract-and-ordered-result-ids"
            or payload.get("corpus_snapshot_status") != "not_frozen"
            or payload.get("body_persistence") != "forbidden"
        ):
            raise SearchSnapshotUnavailable("snapshot record contract is invalid")
        try:
            receipt = verify_query_receipt(payload.get("receipt") or {})
        except QueryReceiptIntegrityError as exc:
            raise SearchSnapshotUnavailable("stored query receipt failed verification") from exc
        integrity = str(payload.get("integrity_sha256") or "")
        if (
            payload.get("receipt_sha256") != receipt["receipt_sha256"]
            or payload.get("normalized_contract_sha256")
            != receipt["normalized_contract_sha256"]
            or payload.get("ordered_returned_ids_sha256")
            != receipt["ordered_returned_ids_sha256"]
            or _HEX_64.fullmatch(integrity) is None
            or integrity != canonical_sha256(_record_hash_material(payload))
        ):
            raise SearchSnapshotUnavailable("snapshot record integrity check failed")
        return payload

    def _records(self, actor_id: int) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        previous: str | None = None
        previous_integrity: str | None = None
        for path in self._record_files(actor_id):
            record = self._validate_record(
                path,
                _read_json(path),
                actor_id=actor_id,
                expected_previous=previous,
                expected_previous_integrity=previous_integrity,
            )
            records.append(record)
            previous = record["snapshot_id"]
            previous_integrity = record["integrity_sha256"]
        return records

    def capture(
        self,
        *,
        actor_id: int,
        receipt: SearchQueryReceipt | Mapping[str, Any],
        expected_previous_snapshot_id: str | None,
        captured_at: datetime | None = None,
    ) -> dict[str, Any]:
        actor = self._actor_id(actor_id)
        if (
            expected_previous_snapshot_id is not None
            and _SNAPSHOT_ID.fullmatch(expected_previous_snapshot_id) is None
        ):
            raise ValueError("expected previous snapshot id has an invalid format")
        verified_receipt = verify_query_receipt(receipt)
        now = _utc(captured_at)
        with self._locked():
            records = self._records(actor)
            previous = records[-1] if records else None
            previous_id = str(previous["snapshot_id"]) if previous else None
            if expected_previous_snapshot_id != previous_id:
                raise SearchSnapshotConflict("latest search snapshot changed")
            if previous is not None:
                previous_time = datetime.fromisoformat(
                    str(previous["captured_at"]).replace("Z", "+00:00")
                ).astimezone(timezone.utc)
                if now <= previous_time:
                    raise ValueError("captured_at must be later than the previous snapshot")
            if previous and previous["receipt_sha256"] == verified_receipt["receipt_sha256"]:
                raise SearchSnapshotConflict("this exact query receipt is already the latest snapshot")
            if len(records) >= MAX_SNAPSHOTS_PER_USER:
                raise SearchSnapshotUnavailable("snapshot record bound was exceeded")
            snapshot_id = _snapshot_id(now)
            record = {
                "schema_version": SEARCH_SNAPSHOT_SCHEMA_VERSION,
                "snapshot_id": snapshot_id,
                "snapshot_scope": "query-contract-and-ordered-result-ids",
                "previous_snapshot_id": previous_id,
                "previous_integrity_sha256": (
                    str(previous["integrity_sha256"]) if previous else None
                ),
                "captured_at": now.isoformat(),
                "actor_ref": f"user:{actor}",
                "receipt": verified_receipt,
                "receipt_sha256": verified_receipt["receipt_sha256"],
                "normalized_contract_sha256": verified_receipt["normalized_contract_sha256"],
                "ordered_returned_ids_sha256": verified_receipt["ordered_returned_ids_sha256"],
                "corpus_snapshot_status": "not_frozen",
                "body_persistence": "forbidden",
            }
            record["integrity_sha256"] = canonical_sha256(record)
            path = self._record_root(actor) / f"{snapshot_id}.json"
            _write_once(path, record)
            return self._validate_record(
                path,
                _read_json(path),
                actor_id=actor,
                expected_previous=previous_id,
                expected_previous_integrity=(
                    str(previous["integrity_sha256"]) if previous else None
                ),
            )

    def list(self, actor_id: int, *, limit: int = 100) -> dict[str, Any]:
        actor = self._actor_id(actor_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("snapshot list limit must be between 1 and 100")
        records = self._records(actor)
        return {
            "schema_version": SEARCH_SNAPSHOT_LIST_SCHEMA_VERSION,
            "snapshot_count": len(records),
            "items": list(reversed(records[-limit:])),
        }

    def get(self, actor_id: int, snapshot_id: str) -> dict[str, Any]:
        actor = self._actor_id(actor_id)
        if _SNAPSHOT_ID.fullmatch(str(snapshot_id or "")) is None:
            raise ValueError("snapshot id has an invalid format")
        records = self._records(actor)
        record = next(
            (item for item in records if item["snapshot_id"] == snapshot_id),
            None,
        )
        if record is None:
            raise SearchSnapshotNotFound("search snapshot was not found")
        return record

    def replay(self, actor_id: int, snapshot_id: str) -> dict[str, Any]:
        record = self.get(actor_id, snapshot_id)
        receipt = record["receipt"]
        return {
            "schema_version": SEARCH_SNAPSHOT_REPLAY_SCHEMA_VERSION,
            "snapshot_id": record["snapshot_id"],
            "replay_mode": "frozen_ids_only",
            "normalized_contract": receipt["normalized_contract"],
            "result_id_namespace": receipt["result_id_namespace"],
            "frozen_ordered_result_ids": receipt["ordered_returned_ids"],
            "frozen_ordered_result_ids_sha256": receipt["ordered_returned_ids_sha256"],
            "frozen_page": receipt["page"],
            "frozen_total": receipt["total"],
            "frozen_result_coverage": receipt["result_coverage"],
            "current_query_executed": False,
            "difference_status": "not_compared",
            "difference_hints": [
                "These are the captured ordered IDs, not current search results.",
                "Run the normalized contract again and compare its query receipt to identify additions, removals, order changes, total changes, and cutoff changes.",
                "Article bodies and the underlying corpus were not frozen by this snapshot.",
            ],
        }


__all__ = (
    "MAX_SNAPSHOTS_PER_USER",
    "SEARCH_SNAPSHOT_LIST_SCHEMA_VERSION",
    "SEARCH_SNAPSHOT_REPLAY_SCHEMA_VERSION",
    "SEARCH_SNAPSHOT_SCHEMA_VERSION",
    "SearchSnapshotConflict",
    "SearchSnapshotError",
    "SearchSnapshotLedger",
    "SearchSnapshotNotFound",
    "SearchSnapshotUnavailable",
)
