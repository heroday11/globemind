"""Bounded, read-only projection of the public maintenance event ledger."""

from __future__ import annotations

import json
import math
import os
import re
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

MAINTENANCE_HISTORY_SCHEMA_VERSION = "globemind.maintenance-events.v1"
MAX_SOURCE_BYTES = 64 * 1024
MAX_PUBLIC_EVENTS = 100
MAX_TITLE_CHARS = 120
MAX_SUMMARY_CHARS = 500
MAX_AFFECTED_FEATURES = 20
_CURRENT_LEDGER_AGE = timedelta(hours=24)
_EVENT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,63}$")
_PUBLIC_FEATURES = frozenset({"search", "ground-news", "opinion-analysis"})
_ROOT_KEYS = frozenset({"schema_version", "generated_at", "events"})
_EVENT_KEYS = frozenset(
    {
        "id",
        "type",
        "status",
        "title",
        "summary",
        "started_at",
        "ended_at",
        "affected_features",
    }
)


class MaintenanceHistoryUnavailable(ValueError):
    """Raised internally when the configured ledger cannot be trusted."""


def _governance() -> dict[str, Any]:
    return {
        "retention": {
            "status": "not_approved",
            "published_event_limit": MAX_PUBLIC_EVENTS,
        },
        "subscription": {"status": "not_configured"},
        "owner": {"status": "not_configured"},
        "bounds": {
            "max_source_bytes": MAX_SOURCE_BYTES,
            "max_events": MAX_PUBLIC_EVENTS,
            "max_title_chars": MAX_TITLE_CHARS,
            "max_summary_chars": MAX_SUMMARY_CHARS,
            "max_affected_features": MAX_AFFECTED_FEATURES,
        },
    }


def _empty(status_value: str, reason: str) -> dict[str, Any]:
    return {
        "status": status_value,
        "freshness": "unknown",
        "generated_at": None,
        "events": [],
        "reason": reason,
        **_governance(),
    }


def unconfigured_public_maintenance_history() -> dict[str, Any]:
    return _empty(
        "not_configured",
        "维护事件账本尚未配置；不能据此推断历史无事件。",
    )


def _unavailable() -> dict[str, Any]:
    return _empty(
        "unavailable",
        "维护事件账本当前不可安全核验；不能据此推断系统正常或历史无事件。",
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise MaintenanceHistoryUnavailable("duplicate JSON key")
        value[key] = item
    return value


def _reject_non_finite(_value: str) -> None:
    raise MaintenanceHistoryUnavailable("non-finite JSON number")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise MaintenanceHistoryUnavailable("non-finite JSON number")
    return parsed


def _safe_text(value: Any, *, maximum: int) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise MaintenanceHistoryUnavailable("text field is invalid")
    if len(value) > maximum or any(
        ord(character) < 32
        or ord(character) == 127
        or 0xD800 <= ord(character) <= 0xDFFF
        for character in value
    ):
        raise MaintenanceHistoryUnavailable("text field is invalid")
    return value


def _timestamp(value: Any) -> datetime:
    raw = _safe_text(value, maximum=64)
    if not re.search(r"(?:Z|[+-]\d{2}:\d{2})$", raw, flags=re.IGNORECASE):
        raise MaintenanceHistoryUnavailable("timestamp is not timezone aware")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MaintenanceHistoryUnavailable("timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise MaintenanceHistoryUnavailable("timestamp is not timezone aware")
    return parsed.astimezone(timezone.utc)


def _validated_path(configured_path: str) -> Path:
    if not isinstance(configured_path, str):
        raise MaintenanceHistoryUnavailable("ledger path is invalid")
    if (
        not configured_path
        or configured_path != configured_path.strip()
        or len(configured_path.encode("utf-8")) > 4096
        or any(ord(character) < 32 or ord(character) == 127 for character in configured_path)
    ):
        raise MaintenanceHistoryUnavailable("ledger path is invalid")
    path = Path(configured_path)
    if not path.is_absolute() or ".." in path.parts or str(path) != configured_path:
        raise MaintenanceHistoryUnavailable("ledger path is invalid")
    return path


def _path_contains_symlink(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            return False
        except OSError:
            return True
        if stat.S_ISLNK(metadata.st_mode):
            return True
    return False


def _read_json_bounded(path: Path) -> Mapping[str, Any]:
    if _path_contains_symlink(path):
        raise MaintenanceHistoryUnavailable("ledger path contains a symbolic link")
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
            or before.st_size > MAX_SOURCE_BYTES
        ):
            raise MaintenanceHistoryUnavailable("ledger metadata is invalid")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise MaintenanceHistoryUnavailable("ledger was truncated")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise MaintenanceHistoryUnavailable("ledger changed while reading")
        after_open = os.fstat(descriptor)
        after_path = path.stat(follow_symlinks=False)
        expected = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
            1,
        )
        if expected != (
            after_open.st_dev,
            after_open.st_ino,
            after_open.st_size,
            after_open.st_mtime_ns,
            after_open.st_ctime_ns,
            after_open.st_nlink,
        ) or expected != (
            after_path.st_dev,
            after_path.st_ino,
            after_path.st_size,
            after_path.st_mtime_ns,
            after_path.st_ctime_ns,
            after_path.st_nlink,
        ):
            raise MaintenanceHistoryUnavailable("ledger changed while reading")
        payload = json.loads(
            b"".join(chunks).decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
            parse_float=_finite_float,
        )
    except MaintenanceHistoryUnavailable:
        raise
    except (OSError, TypeError, UnicodeError, ValueError, RecursionError) as exc:
        raise MaintenanceHistoryUnavailable("ledger is unreadable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(payload, Mapping):
        raise MaintenanceHistoryUnavailable("ledger root is invalid")
    return payload


def _event(value: Any, *, evaluated_at: datetime) -> tuple[dict[str, Any], datetime]:
    if not isinstance(value, Mapping) or set(value) != _EVENT_KEYS:
        raise MaintenanceHistoryUnavailable("event shape is invalid")
    event_id = _safe_text(value.get("id"), maximum=64)
    if _EVENT_ID_PATTERN.fullmatch(event_id) is None:
        raise MaintenanceHistoryUnavailable("event id is invalid")
    event_type = value.get("type")
    event_status = value.get("status")
    if (event_type, event_status) not in {
        ("maintenance", "completed"),
        ("maintenance", "cancelled"),
        ("incident", "resolved"),
    }:
        raise MaintenanceHistoryUnavailable("event state is invalid")
    started_at = _timestamp(value.get("started_at"))
    ended_at = _timestamp(value.get("ended_at"))
    if (
        ended_at < started_at
        or started_at > evaluated_at
        or ended_at > evaluated_at
    ):
        raise MaintenanceHistoryUnavailable("event time is invalid")
    affected = value.get("affected_features")
    if (
        not isinstance(affected, list)
        or not 1 <= len(affected) <= MAX_AFFECTED_FEATURES
        or len(set(affected)) != len(affected)
        or any(item not in _PUBLIC_FEATURES for item in affected)
    ):
        raise MaintenanceHistoryUnavailable("affected features are invalid")
    return (
        {
            "id": event_id,
            "type": event_type,
            "status": event_status,
            "title": _safe_text(value.get("title"), maximum=MAX_TITLE_CHARS),
            "summary": _safe_text(value.get("summary"), maximum=MAX_SUMMARY_CHARS),
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
            "affected_features": list(affected),
        },
        started_at,
    )


def _project(payload: Mapping[str, Any], *, evaluated_at: datetime) -> dict[str, Any]:
    if set(payload) != _ROOT_KEYS or payload.get("schema_version") != MAINTENANCE_HISTORY_SCHEMA_VERSION:
        raise MaintenanceHistoryUnavailable("ledger contract is incompatible")
    generated_at = _timestamp(payload.get("generated_at"))
    if generated_at > evaluated_at:
        raise MaintenanceHistoryUnavailable("ledger time is in the future")
    raw_events = payload.get("events")
    if not isinstance(raw_events, list) or len(raw_events) > MAX_PUBLIC_EVENTS:
        raise MaintenanceHistoryUnavailable("event count is invalid")
    events: list[dict[str, Any]] = []
    ids: set[str] = set()
    previous_started_at: datetime | None = None
    latest_ended_at: datetime | None = None
    for raw_event in raw_events:
        event, started_at = _event(raw_event, evaluated_at=evaluated_at)
        ended_at = _timestamp(event["ended_at"])
        if event["id"] in ids or (
            previous_started_at is not None and started_at >= previous_started_at
        ):
            raise MaintenanceHistoryUnavailable("event ordering is invalid")
        ids.add(event["id"])
        previous_started_at = started_at
        latest_ended_at = max(latest_ended_at or ended_at, ended_at)
        events.append(event)
    if latest_ended_at is not None and latest_ended_at > generated_at:
        raise MaintenanceHistoryUnavailable("ledger predates its events")
    age = evaluated_at - generated_at
    freshness = "current" if age <= _CURRENT_LEDGER_AGE else "stale"
    reason = (
        "维护事件账本已核验；公开记录受数量上限约束，且保留策略、订阅和正式 owner 尚未配置。"
        if events
        else "维护事件账本已核验且暂无已发布记录；这不证明历史无事件。"
    )
    return {
        "status": "available",
        "freshness": freshness,
        "generated_at": generated_at.isoformat(),
        "events": events,
        "reason": reason,
        **_governance(),
    }


def load_public_maintenance_history(
    configured_path: str,
    *,
    evaluated_at: datetime | None = None,
) -> dict[str, Any]:
    """Read a configured ledger without creating, locking, touching, or rewriting it."""
    if configured_path == "":
        return unconfigured_public_maintenance_history()
    try:
        now = evaluated_at or datetime.now(timezone.utc)
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise MaintenanceHistoryUnavailable("evaluation time is invalid")
        now = now.astimezone(timezone.utc)
        path = _validated_path(configured_path)
        return _project(_read_json_bounded(path), evaluated_at=now)
    except (MaintenanceHistoryUnavailable, OSError, TypeError, UnicodeError, ValueError):
        return _unavailable()


__all__ = (
    "MAINTENANCE_HISTORY_SCHEMA_VERSION",
    "MAX_AFFECTED_FEATURES",
    "MAX_PUBLIC_EVENTS",
    "MAX_SOURCE_BYTES",
    "MAX_SUMMARY_CHARS",
    "MAX_TITLE_CHARS",
    "load_public_maintenance_history",
    "unconfigured_public_maintenance_history",
)
