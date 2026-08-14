"""Local append-only hash/HMAC chain for entity governance decisions."""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import stat
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from .errors import EntityGovernanceConflict, EntityGovernanceUnavailable

EVENT_SCHEMA_VERSION = "entity-governance-event-v1"
HISTORY_SCHEMA_VERSION = "entity-governance-history-v1"
MAX_EVENTS = 10_000
MAX_EVENT_BYTES = 256 * 1024
_FORBIDDEN_RELEASE_ROOT = Path("/root/data/releases/globemind")
_EVENT_ID = re.compile(
    r"^egv-(?P<sequence>[0-9]{10})-"
    r"(?P<stamp>[0-9]{8}T[0-9]{12}Z)-(?P<nonce>[0-9a-f]{16})$"
)
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_EVENT_TYPES = frozenset(
    {
        "entity.decision",
        "alias.review",
        "relation.added",
        "relation.retracted",
        "merge.decision",
        "split.decision",
    }
)
_EVENT_KEYS = frozenset(
    {
        "schema_version",
        "event_id",
        "sequence",
        "occurred_at",
        "actor_ref",
        "event_type",
        "reason",
        "evidence",
        "payload",
        "previous_event_id",
        "previous_record_sha256",
        "previous_chain_hmac_sha256",
        "record_sha256",
        "chain_hmac_sha256",
    }
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise EntityGovernanceConflict("ENTITY_GOVERNANCE_EVENT_NOT_CANONICAL") from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _path_has_symlink(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        os.fsync(descriptor)
    except OSError as exc:
        raise EntityGovernanceUnavailable(
            "ENTITY_GOVERNANCE_DIRECTORY_FSYNC_FAILED"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _ensure_directory(path: Path, mode: int = 0o750) -> None:
    if _path_has_symlink(path):
        raise EntityGovernanceUnavailable("ENTITY_GOVERNANCE_PATH_SYMLINK_REJECTED")
    try:
        path.mkdir(parents=True, exist_ok=True, mode=mode)
        if path.is_symlink() or not path.is_dir():
            raise EntityGovernanceUnavailable(
                "ENTITY_GOVERNANCE_DIRECTORY_UNAVAILABLE"
            )
        os.chmod(path, mode)
    except EntityGovernanceUnavailable:
        raise
    except OSError as exc:
        raise EntityGovernanceUnavailable(
            "ENTITY_GOVERNANCE_DIRECTORY_UNAVAILABLE"
        ) from exc


def _duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EntityGovernanceUnavailable(
                "ENTITY_GOVERNANCE_DUPLICATE_JSON_KEY"
            )
        result[key] = value
    return result


def _reject_non_finite_json_number(_value: str) -> None:
    raise EntityGovernanceUnavailable(
        "ENTITY_GOVERNANCE_NON_FINITE_JSON_NUMBER"
    )


def _finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        _reject_non_finite_json_number(value)
    return parsed


def _read_json(path: Path) -> dict[str, Any]:
    if _path_has_symlink(path):
        raise EntityGovernanceUnavailable("ENTITY_GOVERNANCE_EVENT_SYMLINK_REJECTED")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise EntityGovernanceUnavailable(
                "ENTITY_GOVERNANCE_EVENT_FILE_UNSAFE"
            )
        if metadata.st_size > MAX_EVENT_BYTES:
            raise EntityGovernanceUnavailable("ENTITY_GOVERNANCE_EVENT_TOO_LARGE")
        encoded = b""
        while len(encoded) <= MAX_EVENT_BYTES:
            chunk = os.read(
                descriptor,
                min(65_536, MAX_EVENT_BYTES + 1 - len(encoded)),
            )
            if not chunk:
                break
            encoded += chunk
        if len(encoded) > MAX_EVENT_BYTES:
            raise EntityGovernanceUnavailable("ENTITY_GOVERNANCE_EVENT_TOO_LARGE")
        payload = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_duplicate_keys,
            parse_constant=_reject_non_finite_json_number,
            parse_float=_finite_json_float,
        )
    except EntityGovernanceUnavailable:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise EntityGovernanceUnavailable("ENTITY_GOVERNANCE_EVENT_UNREADABLE") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(payload, dict):
        raise EntityGovernanceUnavailable("ENTITY_GOVERNANCE_EVENT_ROOT_INVALID")
    return payload


def _write_once(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = _canonical_json_bytes(dict(payload))
    if len(encoded) > MAX_EVENT_BYTES:
        raise EntityGovernanceConflict("ENTITY_GOVERNANCE_EVENT_TOO_LARGE")
    if path.exists() or path.is_symlink():
        raise EntityGovernanceConflict("ENTITY_GOVERNANCE_EVENT_ID_COLLISION")
    descriptor = -1
    temporary = ""
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=".entity-governance-",
            dir=path.parent,
        )
        os.fchmod(descriptor, 0o640)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        os.unlink(temporary)
        temporary = ""
        _fsync_directory(path.parent)
    except (EntityGovernanceConflict, EntityGovernanceUnavailable):
        raise
    except OSError as exc:
        raise EntityGovernanceUnavailable(
            "ENTITY_GOVERNANCE_EVENT_COMMIT_FAILED"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


class EntityGovernanceLedger:
    """Append-only filesystem ledger; constructor and reads never initialize it."""

    def __init__(
        self,
        root: Path,
        hmac_key: bytes,
        *,
        clock: Callable[[], datetime] = _utc_now,
        nonce_factory: Callable[[], str] = lambda: secrets.token_hex(8),
    ) -> None:
        raw = Path(root)
        if not raw.is_absolute():
            raise EntityGovernanceUnavailable(
                "ENTITY_GOVERNANCE_ROOT_MUST_BE_ABSOLUTE"
            )
        self.root = Path(os.path.abspath(os.fspath(raw)))
        if _path_has_symlink(self.root):
            raise EntityGovernanceUnavailable(
                "ENTITY_GOVERNANCE_ROOT_SYMLINK_REJECTED"
            )
        try:
            self.root.relative_to(_FORBIDDEN_RELEASE_ROOT)
        except ValueError:
            pass
        else:
            raise EntityGovernanceUnavailable(
                "ENTITY_GOVERNANCE_RELEASE_ROOT_FORBIDDEN"
            )
        if not isinstance(hmac_key, bytes) or not 32 <= len(hmac_key) <= 1024:
            raise EntityGovernanceUnavailable(
                "ENTITY_GOVERNANCE_HMAC_KEY_UNAVAILABLE"
            )
        self._hmac_key = bytes(hmac_key)
        self._clock = clock
        self._nonce_factory = nonce_factory

    @property
    def _event_root(self) -> Path:
        return self.root / "events"

    @property
    def _lock_path(self) -> Path:
        return self.root / ".entity-governance.lock"

    def _validate_container(self) -> None:
        try:
            if _path_has_symlink(self.root):
                raise EntityGovernanceUnavailable(
                    "ENTITY_GOVERNANCE_PATH_SYMLINK_REJECTED"
                )
            if not self.root.exists():
                return
            if self.root.is_symlink() or not self.root.is_dir():
                raise EntityGovernanceUnavailable("ENTITY_GOVERNANCE_ROOT_UNSAFE")
            entries = {entry.name: entry for entry in self.root.iterdir()}
            if set(entries) - {"events", ".entity-governance.lock"}:
                raise EntityGovernanceUnavailable(
                    "ENTITY_GOVERNANCE_ROOT_HAS_UNEXPECTED_ENTRY"
                )
            events = entries.get("events")
            if events is not None and (events.is_symlink() or not events.is_dir()):
                raise EntityGovernanceUnavailable(
                    "ENTITY_GOVERNANCE_EVENT_DIRECTORY_UNSAFE"
                )
            lock = entries.get(".entity-governance.lock")
            if lock is not None:
                metadata = lock.stat(follow_symlinks=False)
                if (
                    lock.is_symlink()
                    or not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_nlink != 1
                ):
                    raise EntityGovernanceUnavailable(
                        "ENTITY_GOVERNANCE_LOCK_FILE_UNSAFE"
                    )
            elif events is not None and any(events.iterdir()):
                raise EntityGovernanceUnavailable(
                    "ENTITY_GOVERNANCE_LOCK_FILE_MISSING"
                )
        except EntityGovernanceUnavailable:
            raise
        except OSError as exc:
            raise EntityGovernanceUnavailable(
                "ENTITY_GOVERNANCE_CONTAINER_UNAVAILABLE"
            ) from exc

    @contextmanager
    def _read_guard(self) -> Iterator[None]:
        self._validate_container()
        if not self.root.exists() or not self._lock_path.exists():
            yield
            return
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = -1
        try:
            descriptor = os.open(self._lock_path, flags)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise EntityGovernanceUnavailable(
                    "ENTITY_GOVERNANCE_LOCK_FILE_UNSAFE"
                )
            fcntl.flock(descriptor, fcntl.LOCK_SH)
            self._validate_container()
            yield
        except EntityGovernanceUnavailable:
            raise
        except OSError as exc:
            raise EntityGovernanceUnavailable(
                "ENTITY_GOVERNANCE_READ_LOCK_UNAVAILABLE"
            ) from exc
        finally:
            if descriptor >= 0:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)

    @contextmanager
    def _write_guard(self) -> Iterator[None]:
        self._validate_container()
        root_existed = self.root.exists()
        _ensure_directory(self.root)
        if not root_existed:
            _fsync_directory(self.root.parent)
        if self._event_root.exists() and any(self._event_root.iterdir()):
            if not self._lock_path.exists():
                raise EntityGovernanceUnavailable(
                    "ENTITY_GOVERNANCE_LOCK_FILE_MISSING"
                )
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = -1
        try:
            descriptor = os.open(self._lock_path, flags, 0o600)
            os.fchmod(descriptor, 0o600)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise EntityGovernanceUnavailable(
                    "ENTITY_GOVERNANCE_LOCK_FILE_UNSAFE"
                )
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            _ensure_directory(self._event_root)
            self._validate_container()
            _fsync_directory(self.root)
            yield
        except EntityGovernanceUnavailable:
            raise
        except OSError as exc:
            raise EntityGovernanceUnavailable(
                "ENTITY_GOVERNANCE_WRITE_LOCK_UNAVAILABLE"
            ) from exc
        finally:
            if descriptor >= 0:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)

    def _event_files(self) -> list[Path]:
        if not self._event_root.exists():
            return []
        entries = list(self._event_root.iterdir())
        if any(
            entry.is_symlink()
            or not entry.is_file()
            or entry.suffix != ".json"
            or _EVENT_ID.fullmatch(entry.stem) is None
            for entry in entries
        ):
            raise EntityGovernanceUnavailable(
                "ENTITY_GOVERNANCE_EVENT_DIRECTORY_INVALID"
            )
        if len(entries) > MAX_EVENTS:
            raise EntityGovernanceUnavailable("ENTITY_GOVERNANCE_EVENT_BOUND_EXCEEDED")
        return sorted(entries)

    def _chain_hmac(self, event: Mapping[str, Any]) -> str:
        material = {
            "schema_version": event["schema_version"],
            "event_id": event["event_id"],
            "sequence": event["sequence"],
            "record_sha256": event["record_sha256"],
            "previous_chain_hmac_sha256": event[
                "previous_chain_hmac_sha256"
            ],
        }
        return hmac.new(
            self._hmac_key,
            _canonical_json_bytes(material),
            hashlib.sha256,
        ).hexdigest()

    def _validate_event(
        self,
        path: Path,
        event: dict[str, Any],
        *,
        sequence: int,
        previous: dict[str, Any] | None,
    ) -> dict[str, Any]:
        event_id = str(event.get("event_id") or "")
        match = _EVENT_ID.fullmatch(event_id)
        raw_time = str(event.get("occurred_at") or "")
        try:
            occurred = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
        except ValueError as exc:
            raise EntityGovernanceUnavailable(
                "ENTITY_GOVERNANCE_EVENT_TIME_INVALID"
            ) from exc
        if occurred.tzinfo is None:
            raise EntityGovernanceUnavailable(
                "ENTITY_GOVERNANCE_EVENT_TIME_INVALID"
            )
        normalized_time = occurred.astimezone(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
        if raw_time != normalized_time:
            raise EntityGovernanceUnavailable(
                "ENTITY_GOVERNANCE_EVENT_TIME_INVALID"
            )
        expected_previous_id = previous["event_id"] if previous else None
        expected_previous_record = previous["record_sha256"] if previous else None
        expected_previous_hmac = previous["chain_hmac_sha256"] if previous else None
        record_material = {
            key: value
            for key, value in event.items()
            if key not in {"record_sha256", "chain_hmac_sha256"}
        }
        claimed_record = event.get("record_sha256")
        claimed_hmac = event.get("chain_hmac_sha256")
        try:
            expected_record = _sha256(record_material)
            expected_hmac = self._chain_hmac(event)
        except (EntityGovernanceConflict, KeyError, TypeError) as exc:
            raise EntityGovernanceUnavailable(
                "ENTITY_GOVERNANCE_EVENT_CHAIN_INVALID"
            ) from exc
        if (
            set(event) != _EVENT_KEYS
            or event.get("schema_version") != EVENT_SCHEMA_VERSION
            or match is None
            or path.name != f"{event_id}.json"
            or int(match.group("sequence")) != sequence
            or isinstance(event.get("sequence"), bool)
            or not isinstance(event.get("sequence"), int)
            or event.get("sequence") != sequence
            or occurred.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            != match.group("stamp")
            or event.get("event_type") not in _EVENT_TYPES
            or re.fullmatch(r"user:[1-9][0-9]*", str(event.get("actor_ref") or ""))
            is None
            or not isinstance(event.get("reason"), str)
            or not 3 <= len(event["reason"]) <= 1000
            or not isinstance(event.get("evidence"), dict)
            or not isinstance(event.get("payload"), dict)
            or event.get("previous_event_id") != expected_previous_id
            or event.get("previous_record_sha256") != expected_previous_record
            or event.get("previous_chain_hmac_sha256") != expected_previous_hmac
            or not isinstance(claimed_record, str)
            or _HEX_64.fullmatch(claimed_record) is None
            or not hmac.compare_digest(claimed_record, expected_record)
            or not isinstance(claimed_hmac, str)
            or _HEX_64.fullmatch(claimed_hmac) is None
            or not hmac.compare_digest(claimed_hmac, expected_hmac)
        ):
            raise EntityGovernanceUnavailable(
                "ENTITY_GOVERNANCE_EVENT_CHAIN_INVALID"
            )
        if previous is not None:
            prior_time = datetime.fromisoformat(
                str(previous["occurred_at"]).replace("Z", "+00:00")
            )
            if occurred <= prior_time:
                raise EntityGovernanceUnavailable(
                    "ENTITY_GOVERNANCE_EVENT_TIME_NOT_MONOTONIC"
                )
        return event

    def _records_unlocked(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        previous: dict[str, Any] | None = None
        for sequence, path in enumerate(self._event_files(), start=1):
            event = self._validate_event(
                path,
                _read_json(path),
                sequence=sequence,
                previous=previous,
            )
            records.append(event)
            previous = event
        return records

    def records(self) -> list[dict[str, Any]]:
        try:
            with self._read_guard():
                return self._records_unlocked()
        except EntityGovernanceUnavailable:
            raise
        except OSError as exc:
            raise EntityGovernanceUnavailable(
                "ENTITY_GOVERNANCE_RECORD_READ_FAILED"
            ) from exc

    def append(
        self,
        *,
        actor_id: int,
        event_type: str,
        reason: str,
        evidence: Mapping[str, Any],
        payload: Mapping[str, Any],
        expected_previous_event_id: str | None,
    ) -> dict[str, Any]:
        if isinstance(actor_id, bool) or not isinstance(actor_id, int) or actor_id <= 0:
            raise EntityGovernanceConflict("ENTITY_GOVERNANCE_ACTOR_ID_INVALID")
        if event_type not in _EVENT_TYPES:
            raise EntityGovernanceConflict("ENTITY_GOVERNANCE_EVENT_TYPE_INVALID")
        if not isinstance(reason, str) or not 3 <= len(reason) <= 1000:
            raise EntityGovernanceConflict("ENTITY_GOVERNANCE_REASON_INVALID")
        if any(ord(character) < 32 or ord(character) == 127 for character in reason):
            raise EntityGovernanceConflict("ENTITY_GOVERNANCE_REASON_INVALID")
        if expected_previous_event_id is not None:
            if (
                not isinstance(expected_previous_event_id, str)
                or _EVENT_ID.fullmatch(expected_previous_event_id) is None
            ):
                raise EntityGovernanceConflict(
                    "ENTITY_GOVERNANCE_EXPECTED_EVENT_INVALID"
                )
        if not isinstance(evidence, Mapping) or not isinstance(payload, Mapping):
            raise EntityGovernanceConflict("ENTITY_GOVERNANCE_EVENT_PAYLOAD_INVALID")
        with self._write_guard():
            records = self._records_unlocked()
            previous = records[-1] if records else None
            actual_previous = previous["event_id"] if previous else None
            if expected_previous_event_id != actual_previous:
                raise EntityGovernanceConflict(
                    "ENTITY_GOVERNANCE_LATEST_EVENT_CHANGED"
                )
            if len(records) >= MAX_EVENTS:
                raise EntityGovernanceUnavailable(
                    "ENTITY_GOVERNANCE_EVENT_BOUND_EXCEEDED"
                )
            now = self._clock()
            if not isinstance(now, datetime) or now.tzinfo is None:
                raise EntityGovernanceConflict("ENTITY_GOVERNANCE_CLOCK_INVALID")
            now = now.astimezone(timezone.utc)
            if previous is not None:
                prior_time = datetime.fromisoformat(
                    str(previous["occurred_at"]).replace("Z", "+00:00")
                )
                if now <= prior_time:
                    raise EntityGovernanceConflict(
                        "ENTITY_GOVERNANCE_EVENT_TIME_NOT_MONOTONIC"
                    )
            sequence = len(records) + 1
            nonce = str(self._nonce_factory())
            if re.fullmatch(r"[0-9a-f]{16}", nonce) is None:
                raise EntityGovernanceConflict("ENTITY_GOVERNANCE_NONCE_INVALID")
            event_id = (
                f"egv-{sequence:010d}-{now.strftime('%Y%m%dT%H%M%S%fZ')}-{nonce}"
            )
            event: dict[str, Any] = {
                "schema_version": EVENT_SCHEMA_VERSION,
                "event_id": event_id,
                "sequence": sequence,
                "occurred_at": now.isoformat().replace("+00:00", "Z"),
                "actor_ref": f"user:{actor_id}",
                "event_type": event_type,
                "reason": reason,
                "evidence": dict(evidence),
                "payload": dict(payload),
                "previous_event_id": actual_previous,
                "previous_record_sha256": (
                    previous["record_sha256"] if previous else None
                ),
                "previous_chain_hmac_sha256": (
                    previous["chain_hmac_sha256"] if previous else None
                ),
            }
            event["record_sha256"] = _sha256(event)
            event["chain_hmac_sha256"] = self._chain_hmac(event)
            path = self._event_root / f"{event_id}.json"
            _write_once(path, event)
            return self._validate_event(
                path,
                _read_json(path),
                sequence=sequence,
                previous=previous,
            )

    def history(self, *, limit: int = 100) -> dict[str, Any]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise EntityGovernanceConflict("ENTITY_GOVERNANCE_HISTORY_LIMIT_INVALID")
        records = self.records()
        return {
            "schema_version": HISTORY_SCHEMA_VERSION,
            "event_count": len(records),
            "items": list(reversed(records[-limit:])),
        }


__all__ = (
    "EVENT_SCHEMA_VERSION",
    "HISTORY_SCHEMA_VERSION",
    "MAX_EVENTS",
    "EntityGovernanceLedger",
)
