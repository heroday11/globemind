"""Auditable lifecycle handling for persisted financial alert events.

The lifecycle ledger is deliberately independent from ``JsonListStore``.  It
uses immutable entry files, an advisory lock and a verified hash chain.  Its
constructor and every read-only operation are zero-write.
"""

from __future__ import annotations

import fcntl
import hashlib
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
from typing import Any, Callable, Iterator, Mapping, Sequence

from .contracts import AlertTriageMutation

TRIAGE_LEDGER_SCHEMA_VERSION = "financial-alert-triage-ledger-v1"
TRIAGE_STATUS_SCHEMA_VERSION = "financial-alert-triage-status-v1"
MAX_TRIAGE_EVENTS = 20_000
MAX_EVENT_BYTES = 32 * 1024
MAX_HISTORY_BYTES = 8 * 1024 * 1024
MAX_HISTORY_EVENTS = 500
MAX_EVENTS_PER_ALERT = 8
_FORBIDDEN_RELEASE_ROOT = Path("/root/data/releases/globemind")
_ENTRY_FILE = re.compile(
    r"^(?P<sequence>[0-9]{8})-(?P<event>fat-[0-9]{8}T[0-9]{12}Z-[0-9a-f]{16})\.json$"
)
_ALERT_EVENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,299}$")
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")

_STATUSES = frozenset(
    {"open", "acknowledged", "escalated", "false_positive", "resolved"}
)
_ACTIONS = frozenset(
    {"acknowledge", "escalate", "mark_false_positive", "resolve", "postmortem"}
)
_FALSE_POSITIVE_CLASSIFICATIONS = frozenset(
    {
        "data_quality",
        "duplicate_signal",
        "threshold_miscalibration",
        "known_activity",
        "insufficient_context",
    }
)
_ESCALATION_TARGET_ROLES = frozenset(
    {
        "financial_duty_officer",
        "data_quality_reviewer",
        "research_lead",
        "security_duty_officer",
    }
)
_POSTMORTEM_OUTCOMES = frozenset(
    {
        "confirmed_response",
        "process_improvement_identified",
        "no_follow_up_required",
    }
)
_ENTRY_KEYS = frozenset(
    {
        "schema_version",
        "sequence",
        "event_id",
        "alert_event_id",
        "alert_history_sha256",
        "occurred_at",
        "actor_user_id",
        "event_type",
        "action",
        "from_status",
        "to_status",
        "reason",
        "reason_sha256",
        "reason_length",
        "false_positive_classification",
        "escalation_target_role",
        "postmortem_outcome",
        "previous_alert_event_id",
        "previous_alert_event_sha256",
        "previous_record_sha256",
        "record_sha256",
    }
)

TRIAGE_OPERATIONAL_LIMITATIONS: dict[str, str] = {
    "sla": "unavailable",
    "notification_delivery": "not_configured",
    "institutional_incident_system": "not_configured",
}


class AlertTriageError(RuntimeError):
    """Base class with a stable, non-sensitive API error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class AlertTriageUnavailable(AlertTriageError):
    pass


class AlertTriageConflict(AlertTriageError):
    pass


class AlertHistoryEventNotFound(AlertTriageError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise AlertTriageUnavailable("TRIAGE_CLOCK_REQUIRES_TIMEZONE")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: Any) -> datetime:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise AlertTriageUnavailable("TRIAGE_TIMESTAMP_INVALID")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AlertTriageUnavailable("TRIAGE_TIMESTAMP_INVALID") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AlertTriageUnavailable("TRIAGE_TIMESTAMP_INVALID")
    return parsed.astimezone(timezone.utc)


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AlertTriageUnavailable("TRIAGE_RECORD_NOT_CANONICAL") from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _reason_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _path_has_symlink(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise AlertTriageUnavailable("TRIAGE_PATH_UNAVAILABLE") from exc
        if stat.S_ISLNK(metadata.st_mode):
            return True
    return False


def _validate_directory(path: Path, *, missing_ok: bool) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        if missing_ok:
            return False
        raise AlertTriageUnavailable("TRIAGE_DIRECTORY_MISSING")
    except OSError as exc:
        raise AlertTriageUnavailable("TRIAGE_DIRECTORY_UNAVAILABLE") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_mode & 0o022
    ):
        raise AlertTriageUnavailable("TRIAGE_DIRECTORY_UNSAFE")
    return True


def _ensure_directory(path: Path) -> None:
    if _path_has_symlink(path):
        raise AlertTriageUnavailable("TRIAGE_PATH_SYMLINK_REJECTED")
    if _validate_directory(path, missing_ok=True):
        return
    try:
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(path, 0o700)
    except OSError as exc:
        raise AlertTriageUnavailable("TRIAGE_DIRECTORY_UNAVAILABLE") from exc
    _validate_directory(path, missing_ok=False)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        os.fsync(descriptor)
    except OSError as exc:
        raise AlertTriageUnavailable("TRIAGE_DIRECTORY_FSYNC_FAILED") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise AlertTriageUnavailable("TRIAGE_DUPLICATE_JSON_KEY")
        output[key] = value
    return output


def _reject_non_finite(_value: str) -> None:
    raise AlertTriageUnavailable("TRIAGE_NON_FINITE_JSON_NUMBER")


def _read_bounded_json(path: Path, *, maximum_bytes: int) -> Any:
    if _path_has_symlink(path):
        raise AlertTriageUnavailable("TRIAGE_FILE_SYMLINK_REJECTED")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_mode & 0o022
            or before.st_size <= 0
            or before.st_size > maximum_bytes
        ):
            raise AlertTriageUnavailable("TRIAGE_FILE_METADATA_UNSAFE")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise AlertTriageUnavailable("TRIAGE_FILE_TRUNCATED")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise AlertTriageUnavailable("TRIAGE_FILE_CHANGED_DURING_READ")
        after = os.fstat(descriptor)
        try:
            after_path = path.lstat()
        except OSError as exc:
            raise AlertTriageUnavailable("TRIAGE_FILE_CHANGED_DURING_READ") from exc
        if (
            after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_nlink != 1
            or stat.S_ISLNK(after_path.st_mode)
            or after_path.st_dev != after.st_dev
            or after_path.st_ino != after.st_ino
            or after_path.st_nlink != 1
        ):
            raise AlertTriageUnavailable("TRIAGE_FILE_CHANGED_DURING_READ")
        return json.loads(
            b"".join(chunks).decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except AlertTriageError:
        raise
    except FileNotFoundError:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise AlertTriageUnavailable("TRIAGE_FILE_UNREADABLE") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write_once(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = _canonical_bytes(dict(payload))
    if not encoded or len(encoded) > MAX_EVENT_BYTES:
        raise AlertTriageUnavailable("TRIAGE_EVENT_SIZE_BOUND_EXCEEDED")
    if path.exists() or path.is_symlink():
        raise AlertTriageConflict("TRIAGE_EVENT_ID_COLLISION")
    descriptor = -1
    temporary = ""
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=".financial-triage-", dir=path.parent)
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
    except AlertTriageError:
        raise
    except OSError as exc:
        raise AlertTriageUnavailable("TRIAGE_EVENT_APPEND_FAILED") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _validate_lock_descriptor(descriptor: int, path: Path) -> None:
    try:
        opened = os.fstat(descriptor)
        current = path.lstat()
    except OSError as exc:
        raise AlertTriageUnavailable("TRIAGE_LOCK_UNAVAILABLE") from exc
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_nlink != 1
        or opened.st_mode & 0o077
        or stat.S_ISLNK(current.st_mode)
        or not stat.S_ISREG(current.st_mode)
        or current.st_nlink != 1
        or current.st_dev != opened.st_dev
        or current.st_ino != opened.st_ino
    ):
        raise AlertTriageUnavailable("TRIAGE_LOCK_UNSAFE")


def _validate_alert_event_id(alert_event_id: str) -> str:
    if not isinstance(alert_event_id, str) or _ALERT_EVENT_ID.fullmatch(alert_event_id) is None:
        raise AlertHistoryEventNotFound("ALERT_HISTORY_EVENT_NOT_FOUND")
    return alert_event_id


def _validate_history_row(row: Mapping[str, Any]) -> None:
    row_id = row.get("id")
    rule_id = row.get("rule_id")
    metric = row.get("metric")
    severity = row.get("severity")
    message = row.get("message")
    event_tags = row.get("eventTags")
    if (
        not isinstance(row_id, str)
        or _ALERT_EVENT_ID.fullmatch(row_id) is None
        or not isinstance(rule_id, str)
        or not rule_id
        or len(rule_id) > 128
        or not isinstance(metric, str)
        or not metric
        or len(metric) > 160
        or not isinstance(severity, str)
        or severity not in {"high", "medium", "low"}
        or not isinstance(message, str)
        or len(message) > 4000
        or not isinstance(event_tags, list)
        or len(event_tags) > 32
        or any(
            not isinstance(tag, str) or not tag or len(tag) > 160
            for tag in event_tags
        )
    ):
        raise AlertTriageUnavailable("ALERT_HISTORY_CONTRACT_INVALID")
    for field in ("current", "threshold"):
        value = row.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise AlertTriageUnavailable("ALERT_HISTORY_CONTRACT_INVALID")
        if not math.isfinite(float(value)):
            raise AlertTriageUnavailable("ALERT_HISTORY_CONTRACT_INVALID")
    _parse_iso(row.get("triggered_at"))


def read_alert_history_event(history_path: Path, alert_event_id: str) -> dict[str, Any]:
    """Strictly prove that a lifecycle operation binds to a real history row."""

    event_id = _validate_alert_event_id(alert_event_id)
    path = Path(history_path)
    if not path.is_absolute():
        raise AlertTriageUnavailable("ALERT_HISTORY_PATH_MUST_BE_ABSOLUTE")
    normalized = Path(os.path.abspath(os.fspath(path)))
    try:
        normalized.relative_to(_FORBIDDEN_RELEASE_ROOT)
    except ValueError:
        pass
    else:
        raise AlertTriageUnavailable("ALERT_HISTORY_PATH_INSIDE_RELEASE")
    try:
        payload = _read_bounded_json(normalized, maximum_bytes=MAX_HISTORY_BYTES)
    except FileNotFoundError as exc:
        raise AlertHistoryEventNotFound("ALERT_HISTORY_EVENT_NOT_FOUND") from exc
    if not isinstance(payload, list) or len(payload) > MAX_HISTORY_EVENTS:
        raise AlertTriageUnavailable("ALERT_HISTORY_CONTRACT_INVALID")
    matches: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for row in payload:
        if not isinstance(row, dict):
            raise AlertTriageUnavailable("ALERT_HISTORY_CONTRACT_INVALID")
        _validate_history_row(row)
        row_id = row.get("id")
        assert isinstance(row_id, str)
        if row_id in seen_ids:
            raise AlertTriageUnavailable("ALERT_HISTORY_DUPLICATE_EVENT_ID")
        seen_ids.add(row_id)
        # Canonical encoding rejects nested NaN/Infinity and non-JSON values.
        _canonical_bytes(row)
        if row_id == event_id:
            matches.append(dict(row))
    if len(matches) != 1:
        raise AlertHistoryEventNotFound("ALERT_HISTORY_EVENT_NOT_FOUND")
    return matches[0]


def alert_history_sha256(history_event: Mapping[str, Any]) -> str:
    return _sha256(dict(history_event))


def _require_string(
    row: Mapping[str, Any],
    field: str,
    *,
    allowed: frozenset[str] | None = None,
) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value:
        raise AlertTriageUnavailable("TRIAGE_RECORD_CONTRACT_INVALID")
    if allowed is not None and value not in allowed:
        raise AlertTriageUnavailable("TRIAGE_RECORD_CONTRACT_INVALID")
    return value


def _optional_hash(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _HEX_SHA256.fullmatch(value) is None:
        raise AlertTriageUnavailable("TRIAGE_RECORD_CONTRACT_INVALID")
    return value


def _optional_event_id(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.startswith("fat-") or len(value) > 160:
        raise AlertTriageUnavailable("TRIAGE_RECORD_CONTRACT_INVALID")
    return value


def _transition_target(current: str, action: str) -> str:
    transitions = {
        ("open", "acknowledge"): "acknowledged",
        ("acknowledged", "escalate"): "escalated",
        ("acknowledged", "mark_false_positive"): "false_positive",
        ("acknowledged", "resolve"): "resolved",
        ("escalated", "resolve"): "resolved",
    }
    target = transitions.get((current, action))
    if target is None:
        raise AlertTriageConflict("TRIAGE_STATE_TRANSITION_REJECTED")
    return target


class FinancialAlertTriageLedger:
    """Immutable lifecycle records with global and per-alert chains."""

    def __init__(
        self,
        root: Path,
        *,
        clock: Callable[[], datetime] = _utc_now,
        nonce_factory: Callable[[], str] = lambda: secrets.token_hex(8),
    ) -> None:
        raw = Path(root)
        if not raw.is_absolute():
            raise AlertTriageUnavailable("TRIAGE_ROOT_MUST_BE_ABSOLUTE")
        self.root = Path(os.path.abspath(os.fspath(raw)))
        try:
            self.root.relative_to(_FORBIDDEN_RELEASE_ROOT)
        except ValueError:
            pass
        else:
            raise AlertTriageUnavailable("TRIAGE_ROOT_INSIDE_RELEASE")
        if _path_has_symlink(self.root):
            raise AlertTriageUnavailable("TRIAGE_ROOT_SYMLINK_REJECTED")
        self.events_root = self.root / "events"
        self.lock_path = self.root / ".triage.lock"
        self._clock = clock
        self._nonce_factory = nonce_factory

    @contextmanager
    def _write_locked(self) -> Iterator[None]:
        if _validate_directory(self.root, missing_ok=True):
            try:
                self.lock_path.lstat()
            except FileNotFoundError:
                if self._entry_paths():
                    raise AlertTriageUnavailable("TRIAGE_LOCK_MISSING")
            except OSError as exc:
                raise AlertTriageUnavailable("TRIAGE_LOCK_UNAVAILABLE") from exc
        _ensure_directory(self.root)
        _fsync_directory(self.root.parent)
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = -1
        try:
            descriptor = os.open(self.lock_path, flags, 0o600)
            _validate_lock_descriptor(descriptor, self.lock_path)
            os.fsync(descriptor)
            _fsync_directory(self.root)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            _validate_lock_descriptor(descriptor, self.lock_path)
            yield
            _validate_lock_descriptor(descriptor, self.lock_path)
        except AlertTriageError:
            raise
        except OSError as exc:
            raise AlertTriageUnavailable("TRIAGE_LOCK_UNAVAILABLE") from exc
        finally:
            if descriptor >= 0:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)

    @contextmanager
    def _read_locked(self) -> Iterator[None]:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = -1
        try:
            descriptor = os.open(self.lock_path, flags)
            _validate_lock_descriptor(descriptor, self.lock_path)
            fcntl.flock(descriptor, fcntl.LOCK_SH)
            _validate_lock_descriptor(descriptor, self.lock_path)
            yield
            _validate_lock_descriptor(descriptor, self.lock_path)
        except AlertTriageError:
            raise
        except OSError as exc:
            raise AlertTriageUnavailable("TRIAGE_LOCK_UNAVAILABLE") from exc
        finally:
            if descriptor >= 0:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)

    def _entry_paths(self) -> list[Path]:
        if not _validate_directory(self.root, missing_ok=True):
            return []
        try:
            root_entries = list(self.root.iterdir())
        except OSError as exc:
            raise AlertTriageUnavailable("TRIAGE_ROOT_UNREADABLE") from exc
        allowed = {self.lock_path.name, self.events_root.name}
        if any(item.name not in allowed for item in root_entries):
            raise AlertTriageUnavailable("TRIAGE_ROOT_UNKNOWN_ENTRY")
        if not _validate_directory(self.events_root, missing_ok=True):
            return []
        try:
            paths = sorted(self.events_root.iterdir())
        except OSError as exc:
            raise AlertTriageUnavailable("TRIAGE_EVENTS_UNREADABLE") from exc
        if len(paths) > MAX_TRIAGE_EVENTS:
            raise AlertTriageUnavailable("TRIAGE_EVENT_BOUND_EXCEEDED")
        for path in paths:
            try:
                metadata = path.lstat()
            except OSError as exc:
                raise AlertTriageUnavailable("TRIAGE_EVENT_UNREADABLE") from exc
            if (
                _ENTRY_FILE.fullmatch(path.name) is None
                or stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
            ):
                raise AlertTriageUnavailable("TRIAGE_EVENT_FILE_UNSAFE")
        return paths

    def _validate_record(
        self,
        raw: Any,
        *,
        expected_sequence: int,
        filename_event_id: str,
        previous_record_sha256: str | None,
        alert_latest: dict[str, dict[str, Any]],
        reviewed_alerts: set[str],
    ) -> dict[str, Any]:
        if not isinstance(raw, dict) or frozenset(raw) != _ENTRY_KEYS:
            raise AlertTriageUnavailable("TRIAGE_RECORD_CONTRACT_INVALID")
        if raw.get("schema_version") != TRIAGE_LEDGER_SCHEMA_VERSION:
            raise AlertTriageUnavailable("TRIAGE_RECORD_CONTRACT_INVALID")
        if type(raw.get("sequence")) is not int or raw["sequence"] != expected_sequence:
            raise AlertTriageUnavailable("TRIAGE_RECORD_SEQUENCE_INVALID")
        event_id = _require_string(raw, "event_id")
        if event_id != filename_event_id:
            raise AlertTriageUnavailable("TRIAGE_RECORD_EVENT_ID_INVALID")
        alert_id = _require_string(raw, "alert_event_id")
        _validate_alert_event_id(alert_id)
        occurred_at = _parse_iso(raw.get("occurred_at"))
        if raw.get("occurred_at") != _iso(occurred_at) or not event_id.startswith(
            f"fat-{occurred_at.strftime('%Y%m%dT%H%M%S%fZ')}-"
        ):
            raise AlertTriageUnavailable("TRIAGE_TIMESTAMP_INVALID")
        if type(raw.get("actor_user_id")) is not int or raw["actor_user_id"] <= 0:
            raise AlertTriageUnavailable("TRIAGE_RECORD_ACTOR_INVALID")
        event_type = _require_string(
            raw, "event_type", allowed=frozenset({"transition", "postmortem"})
        )
        action = _require_string(raw, "action", allowed=_ACTIONS)
        from_status = _require_string(raw, "from_status", allowed=_STATUSES)
        to_status = _require_string(raw, "to_status", allowed=_STATUSES)
        reason = _require_string(raw, "reason")
        if len(reason) < 3 or len(reason) > 1000 or any(
            ord(character) < 32 or ord(character) == 127 for character in reason
        ):
            raise AlertTriageUnavailable("TRIAGE_RECORD_REASON_INVALID")
        if (
            raw.get("reason_sha256") != _reason_sha256(reason)
            or type(raw.get("reason_length")) is not int
            or raw["reason_length"] != len(reason)
        ):
            raise AlertTriageUnavailable("TRIAGE_RECORD_REASON_INVALID")
        history_hash = _require_string(raw, "alert_history_sha256")
        if _HEX_SHA256.fullmatch(history_hash) is None:
            raise AlertTriageUnavailable("TRIAGE_RECORD_HISTORY_BINDING_INVALID")

        latest = alert_latest.get(alert_id)
        expected_from = str(latest["to_status"]) if latest is not None else "open"
        expected_previous_id = latest["event_id"] if latest is not None else None
        expected_previous_hash = latest["record_sha256"] if latest is not None else None
        if (
            from_status != expected_from
            or _optional_event_id(raw.get("previous_alert_event_id"))
            != expected_previous_id
            or _optional_hash(raw.get("previous_alert_event_sha256"))
            != expected_previous_hash
        ):
            raise AlertTriageUnavailable("TRIAGE_ALERT_CHAIN_INVALID")
        if latest is not None and latest["alert_history_sha256"] != history_hash:
            raise AlertTriageUnavailable("TRIAGE_RECORD_HISTORY_BINDING_INVALID")

        false_positive = raw.get("false_positive_classification")
        escalation = raw.get("escalation_target_role")
        postmortem = raw.get("postmortem_outcome")
        if event_type == "postmortem":
            postmortem_valid = (
                isinstance(postmortem, str)
                and postmortem in _POSTMORTEM_OUTCOMES
            )
            if (
                action != "postmortem"
                or from_status not in {"resolved", "false_positive"}
                or to_status != from_status
                or alert_id in reviewed_alerts
                or not postmortem_valid
                or false_positive is not None
                or escalation is not None
            ):
                raise AlertTriageUnavailable("TRIAGE_POSTMORTEM_RECORD_INVALID")
            reviewed_alerts.add(alert_id)
        else:
            try:
                expected_target = _transition_target(from_status, action)
            except AlertTriageConflict as exc:
                raise AlertTriageUnavailable(
                    "TRIAGE_TRANSITION_RECORD_INVALID"
                ) from exc
            if action == "postmortem" or to_status != expected_target:
                raise AlertTriageUnavailable("TRIAGE_TRANSITION_RECORD_INVALID")
            false_positive_valid = (
                isinstance(false_positive, str)
                and false_positive in _FALSE_POSITIVE_CLASSIFICATIONS
            )
            escalation_valid = (
                isinstance(escalation, str)
                and escalation in _ESCALATION_TARGET_ROLES
            )
            if action == "mark_false_positive":
                optional_fields_valid = false_positive_valid and escalation is None
            elif action == "escalate":
                optional_fields_valid = escalation_valid and false_positive is None
            else:
                optional_fields_valid = false_positive is None and escalation is None
            if not optional_fields_valid or postmortem is not None:
                raise AlertTriageUnavailable("TRIAGE_TRANSITION_RECORD_INVALID")

        supplied_previous = _optional_hash(raw.get("previous_record_sha256"))
        supplied_hash = _optional_hash(raw.get("record_sha256"))
        expected_hash = _sha256(
            {
                key: value
                for key, value in raw.items()
                if key != "record_sha256"
            }
        )
        if supplied_previous != previous_record_sha256 or supplied_hash != expected_hash:
            raise AlertTriageUnavailable("TRIAGE_HASH_CHAIN_INVALID")
        return dict(raw)

    def _read_unlocked(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        previous_record_sha256: str | None = None
        seen_event_ids: set[str] = set()
        alert_latest: dict[str, dict[str, Any]] = {}
        alert_counts: dict[str, int] = {}
        reviewed_alerts: set[str] = set()
        for expected_sequence, path in enumerate(self._entry_paths(), start=1):
            match = _ENTRY_FILE.fullmatch(path.name)
            if match is None or int(match.group("sequence")) != expected_sequence:
                raise AlertTriageUnavailable("TRIAGE_RECORD_SEQUENCE_INVALID")
            record = self._validate_record(
                _read_bounded_json(path, maximum_bytes=MAX_EVENT_BYTES),
                expected_sequence=expected_sequence,
                filename_event_id=match.group("event"),
                previous_record_sha256=previous_record_sha256,
                alert_latest=alert_latest,
                reviewed_alerts=reviewed_alerts,
            )
            if record["event_id"] in seen_event_ids:
                raise AlertTriageUnavailable("TRIAGE_DUPLICATE_EVENT_ID")
            seen_event_ids.add(record["event_id"])
            alert_id = record["alert_event_id"]
            alert_counts[alert_id] = alert_counts.get(alert_id, 0) + 1
            if alert_counts[alert_id] > MAX_EVENTS_PER_ALERT:
                raise AlertTriageUnavailable("TRIAGE_ALERT_EVENT_BOUND_EXCEEDED")
            records.append(record)
            previous_record_sha256 = record["record_sha256"]
            alert_latest[alert_id] = record
        return records

    def list_events(self, *, alert_event_id: str | None = None) -> list[dict[str, Any]]:
        if alert_event_id is not None:
            _validate_alert_event_id(alert_event_id)
        if _path_has_symlink(self.root):
            raise AlertTriageUnavailable("TRIAGE_ROOT_SYMLINK_REJECTED")
        try:
            self.root.lstat()
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise AlertTriageUnavailable("TRIAGE_ROOT_UNREADABLE") from exc
        try:
            lock_metadata = self.lock_path.lstat()
        except FileNotFoundError:
            records = self._read_unlocked()
            if records:
                raise AlertTriageUnavailable("TRIAGE_LOCK_MISSING")
            return []
        except OSError as exc:
            raise AlertTriageUnavailable("TRIAGE_LOCK_UNAVAILABLE") from exc
        if (
            stat.S_ISLNK(lock_metadata.st_mode)
            or not stat.S_ISREG(lock_metadata.st_mode)
            or lock_metadata.st_nlink != 1
            or lock_metadata.st_mode & 0o077
        ):
            raise AlertTriageUnavailable("TRIAGE_LOCK_UNSAFE")
        with self._read_locked():
            records = self._read_unlocked()
        if alert_event_id is None:
            return records
        return [row for row in records if row["alert_event_id"] == alert_event_id]

    def append(
        self,
        *,
        alert_event_id: str,
        alert_history_sha256: str,
        mutation: AlertTriageMutation,
        actor_user_id: int,
    ) -> dict[str, Any]:
        alert_id = _validate_alert_event_id(alert_event_id)
        if _HEX_SHA256.fullmatch(alert_history_sha256) is None:
            raise AlertTriageUnavailable("TRIAGE_HISTORY_BINDING_INVALID")
        if type(actor_user_id) is not int or actor_user_id <= 0:
            raise AlertTriageConflict("TRIAGE_ACTOR_USER_ID_INVALID")

        with self._write_locked():
            records = self._read_unlocked()
            if len(records) >= MAX_TRIAGE_EVENTS:
                raise AlertTriageUnavailable("TRIAGE_EVENT_BOUND_EXCEEDED")
            alert_records = [row for row in records if row["alert_event_id"] == alert_id]
            if len(alert_records) >= MAX_EVENTS_PER_ALERT:
                raise AlertTriageUnavailable("TRIAGE_ALERT_EVENT_BOUND_EXCEEDED")
            latest = alert_records[-1] if alert_records else None
            current_status = str(latest["to_status"]) if latest else "open"
            expected_id = latest["event_id"] if latest else None
            expected_hash = latest["record_sha256"] if latest else None
            if (
                mutation.expected_previous_event_id != expected_id
                or mutation.expected_previous_event_sha256 != expected_hash
            ):
                raise AlertTriageConflict("TRIAGE_OPTIMISTIC_CONCURRENCY_CONFLICT")
            if latest is not None and latest["alert_history_sha256"] != alert_history_sha256:
                raise AlertTriageConflict("TRIAGE_ALERT_HISTORY_CHANGED")

            is_postmortem = mutation.action == "postmortem"
            if is_postmortem:
                if current_status not in {"resolved", "false_positive"}:
                    raise AlertTriageConflict("TRIAGE_POSTMORTEM_REQUIRES_TERMINAL_STATE")
                if any(row["event_type"] == "postmortem" for row in alert_records):
                    raise AlertTriageConflict("TRIAGE_POSTMORTEM_ALREADY_RECORDED")
                next_status = current_status
            else:
                next_status = _transition_target(current_status, mutation.action)

            sequence = len(records) + 1
            occurred_at = _iso(self._clock())
            nonce = self._nonce_factory()
            if not isinstance(nonce, str) or re.fullmatch(r"[0-9a-f]{16}", nonce) is None:
                raise AlertTriageUnavailable("TRIAGE_NONCE_INVALID")
            event_id = (
                f"fat-{_parse_iso(occurred_at).strftime('%Y%m%dT%H%M%S%fZ')}-{nonce}"
            )
            payload: dict[str, Any] = {
                "schema_version": TRIAGE_LEDGER_SCHEMA_VERSION,
                "sequence": sequence,
                "event_id": event_id,
                "alert_event_id": alert_id,
                "alert_history_sha256": alert_history_sha256,
                "occurred_at": occurred_at,
                "actor_user_id": actor_user_id,
                "event_type": "postmortem" if is_postmortem else "transition",
                "action": mutation.action,
                "from_status": current_status,
                "to_status": next_status,
                "reason": mutation.reason,
                "reason_sha256": _reason_sha256(mutation.reason),
                "reason_length": len(mutation.reason),
                "false_positive_classification": mutation.false_positive_classification,
                "escalation_target_role": mutation.escalation_target_role,
                "postmortem_outcome": mutation.postmortem_outcome,
                "previous_alert_event_id": expected_id,
                "previous_alert_event_sha256": expected_hash,
                "previous_record_sha256": records[-1]["record_sha256"] if records else None,
            }
            payload["record_sha256"] = _sha256(payload)
            _ensure_directory(self.events_root)
            _fsync_directory(self.root)
            target = self.events_root / f"{sequence:08d}-{event_id}.json"
            _write_once(target, payload)
            return dict(payload)


class FinancialAlertTriageService:
    def __init__(
        self,
        *,
        ledger_root: Path,
        alert_history_path: Path,
        clock: Callable[[], datetime] = _utc_now,
        nonce_factory: Callable[[], str] = lambda: secrets.token_hex(8),
    ) -> None:
        self.ledger = FinancialAlertTriageLedger(
            ledger_root,
            clock=clock,
            nonce_factory=nonce_factory,
        )
        self.alert_history_path = Path(alert_history_path)

    @staticmethod
    def _summary(alert_event_id: str, records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        latest = records[-1] if records else None
        transitions = [row for row in records if row["event_type"] == "transition"]
        latest_transition = transitions[-1] if transitions else None
        return {
            "schema_version": TRIAGE_STATUS_SCHEMA_VERSION,
            "alert_event_id": alert_event_id,
            "status": latest["to_status"] if latest else "open",
            "has_audit": bool(records),
            "reviewed": any(row["event_type"] == "postmortem" for row in records),
            "transition_count": len(transitions),
            "last_transition_at": (
                latest_transition["occurred_at"] if latest_transition else None
            ),
            "last_event_id": latest["event_id"] if latest else None,
            "last_event_sha256": latest["record_sha256"] if latest else None,
            "operational_limitations": dict(TRIAGE_OPERATIONAL_LIMITATIONS),
        }

    def summaries(self, alert_event_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        if len(alert_event_ids) > MAX_HISTORY_EVENTS:
            raise AlertTriageUnavailable("TRIAGE_SUMMARY_BOUND_EXCEEDED")
        unique_ids: list[str] = []
        seen: set[str] = set()
        for raw in alert_event_ids:
            try:
                alert_id = _validate_alert_event_id(raw)
            except AlertHistoryEventNotFound as exc:
                raise AlertTriageUnavailable(
                    "ALERT_HISTORY_CONTRACT_INVALID"
                ) from exc
            if alert_id not in seen:
                unique_ids.append(alert_id)
                seen.add(alert_id)
        grouped = {alert_id: [] for alert_id in unique_ids}
        for record in self.ledger.list_events():
            alert_id = record["alert_event_id"]
            if alert_id in grouped:
                grouped[alert_id].append(record)
        return {
            alert_id: self._summary(alert_id, records)
            for alert_id, records in grouped.items()
        }

    def detail(self, alert_event_id: str, *, include_sensitive: bool) -> dict[str, Any]:
        history_event = read_alert_history_event(self.alert_history_path, alert_event_id)
        history_hash = alert_history_sha256(history_event)
        records = self.ledger.list_events(alert_event_id=alert_event_id)
        if records and records[0]["alert_history_sha256"] != history_hash:
            raise AlertTriageUnavailable("TRIAGE_ALERT_HISTORY_BINDING_INVALID")
        summary = self._summary(alert_event_id, records)
        if include_sensitive:
            audit = [dict(record) for record in records]
        else:
            audit = [
                {
                    "event_id": record["event_id"],
                    "occurred_at": record["occurred_at"],
                    "event_type": record["event_type"],
                    "action": record["action"],
                    "from_status": record["from_status"],
                    "to_status": record["to_status"],
                    "reason": {
                        "sha256": record["reason_sha256"],
                        "length": record["reason_length"],
                    },
                    "false_positive_classification": record[
                        "false_positive_classification"
                    ],
                    "escalation_target_role": record["escalation_target_role"],
                    "postmortem_outcome": record["postmortem_outcome"],
                    "previous_event_id": record["previous_alert_event_id"],
                    "previous_event_sha256": record[
                        "previous_alert_event_sha256"
                    ],
                    "event_sha256": record["record_sha256"],
                }
                for record in records
            ]
        return {
            **summary,
            "alert_history_sha256": history_hash,
            "audit": audit,
        }

    def mutate(
        self,
        alert_event_id: str,
        mutation: AlertTriageMutation,
        *,
        actor_user_id: int,
    ) -> dict[str, Any]:
        history_event = read_alert_history_event(self.alert_history_path, alert_event_id)
        self.ledger.append(
            alert_event_id=alert_event_id,
            alert_history_sha256=alert_history_sha256(history_event),
            mutation=mutation,
            actor_user_id=actor_user_id,
        )
        return self.detail(alert_event_id, include_sensitive=True)


def annotate_alert_history_with_triage(
    history: Sequence[Mapping[str, Any]],
    *,
    service: FinancialAlertTriageService,
    historical: bool,
) -> list[dict[str, Any]]:
    ids = [str(row.get("id") or "") for row in history]
    summaries = service.summaries(ids)
    return [
        {
            **dict(row),
            "triage": {
                **summaries[str(row.get("id") or "")],
                "historical": historical,
                "mutations_enabled": not historical,
            },
        }
        for row in history
    ]


__all__ = (
    "AlertHistoryEventNotFound",
    "AlertTriageConflict",
    "AlertTriageError",
    "AlertTriageUnavailable",
    "FinancialAlertTriageLedger",
    "FinancialAlertTriageService",
    "MAX_EVENT_BYTES",
    "MAX_EVENTS_PER_ALERT",
    "MAX_TRIAGE_EVENTS",
    "TRIAGE_LEDGER_SCHEMA_VERSION",
    "TRIAGE_OPERATIONAL_LIMITATIONS",
    "TRIAGE_STATUS_SCHEMA_VERSION",
    "alert_history_sha256",
    "annotate_alert_history_with_triage",
    "read_alert_history_event",
)
