from __future__ import annotations

import math
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from .contracts import HeartbeatPayload
from .storage import (
    atomic_write_json,
    exclusive_file_lock,
    read_json_bounded,
    shared_file_lock,
)

_CLIENT_ID_PATTERN = re.compile(r"[^A-Za-z0-9_.:-]")
_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class HeartbeatPolicy:
    ttl_seconds: int = 90
    max_clients: int = 10_000

    def __post_init__(self) -> None:
        if (
            isinstance(self.ttl_seconds, bool)
            or not isinstance(self.ttl_seconds, int)
            or self.ttl_seconds < 1
        ):
            raise ValueError("heartbeat TTL must be positive")
        if (
            isinstance(self.max_clients, bool)
            or not isinstance(self.max_clients, int)
            or self.max_clients < 1
        ):
            raise ValueError("heartbeat max_clients must be positive")


class HeartbeatRegistry:
    def __init__(
        self,
        *,
        data_path: Path,
        lock_path: Path,
        policy: HeartbeatPolicy,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._data_path = Path(data_path)
        self._lock_path = Path(lock_path)
        self._policy = policy
        self._clock = clock

    def update(
        self,
        payload: HeartbeatPayload,
        *,
        user_agent: str = "",
    ) -> dict[str, Any]:
        # Kept as a source-compatible argument for older adapters. A browser
        # fingerprint is not needed for presence aggregation and is discarded.
        del user_agent
        now = self._clock()
        if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
            raise OSError("heartbeat clock is unavailable")
        client_id = _CLIENT_ID_PATTERN.sub("", payload.client_id)[:128] or "unknown"
        entry = {
            "client_id": client_id,
            "path": normalize_heartbeat_path(payload.path),
            "visibility": payload.visibility,
            "last_seen": now,
            "updated_at": _iso_from_timestamp(now),
        }
        with exclusive_file_lock(self._lock_path):
            data = self._read_unlocked(now)
            data[client_id] = entry
            data = self._prune(data, now)
            if len(data) > self._policy.max_clients:
                newest = sorted(
                    data.items(),
                    key=lambda item: _number(item[1].get("last_seen")) or 0.0,
                    reverse=True,
                )[: self._policy.max_clients]
                data = dict(newest)
            atomic_write_json(self._data_path, data)
        return self._summarize(data)

    def summary(self, *, now: float | None = None) -> dict[str, Any]:
        current = self._clock() if now is None else now
        if (
            isinstance(current, bool)
            or not isinstance(current, (int, float))
            or not math.isfinite(current)
        ):
            return self._unavailable_summary()
        data_exists = _path_exists(self._data_path)
        lock_exists = _path_exists(self._lock_path)
        if not data_exists and not lock_exists:
            return self._summarize({})
        if data_exists != lock_exists:
            return self._unavailable_summary()
        try:
            with shared_file_lock(self._lock_path):
                data = self._read_unlocked(current)
        except (OSError, TypeError, UnicodeError, ValueError):
            return self._unavailable_summary()
        return self._summarize(data)

    def _read_unlocked(self, now: float) -> dict[str, Any]:
        try:
            raw = read_json_bounded(self._data_path)
        except FileNotFoundError:
            return {}
        if not isinstance(raw, dict) or len(raw) > self._policy.max_clients:
            raise ValueError("heartbeat registry contract is invalid")
        return self._prune(raw, now)

    def _prune(self, data: dict[str, Any], now: float) -> dict[str, Any]:
        pruned: dict[str, Any] = {}
        for key, value in data.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                raise ValueError("heartbeat registry entry is invalid")
            client_id = _CLIENT_ID_PATTERN.sub("", key)[:128] or "unknown"
            last_seen = _number(value.get("last_seen"))
            path = value.get("path")
            visibility = value.get("visibility")
            if (
                client_id != key
                or value.get("client_id") != key
                or last_seen is None
                or not isinstance(path, str)
                or normalize_heartbeat_path(path) != path
                or (
                    visibility is not None
                    and visibility not in {"visible", "hidden", "prerender"}
                )
                or not isinstance(value.get("updated_at"), str)
                or set(value)
                - {
                    "client_id",
                    "path",
                    "visibility",
                    "last_seen",
                    "updated_at",
                    "user_agent",
                }
            ):
                raise ValueError("heartbeat registry entry is invalid")
            age = now - last_seen
            if 0 <= age <= self._policy.ttl_seconds:
                pruned[key] = {
                    "client_id": key,
                    "path": path,
                    "visibility": visibility,
                    "last_seen": last_seen,
                    "updated_at": _iso_from_timestamp(last_seen),
                }
        return pruned

    def _summarize(self, data: dict[str, Any]) -> dict[str, Any]:
        paths = Counter(str(item.get("path") or "/") for item in data.values())
        clients = [
            {
                "path": item.get("path"),
                "visibility": item.get("visibility"),
                "updated_at": item.get("updated_at"),
            }
            for item in data.values()
        ]
        return {
            "measurement_state": "available",
            "active": len(data),
            "ttl_sec": self._policy.ttl_seconds,
            "paths": [{"path": path, "count": count} for path, count in paths.most_common(10)],
            "clients": sorted(
                clients,
                key=lambda row: row.get("updated_at") or "",
                reverse=True,
            )[:20],
        }

    def _unavailable_summary(self) -> dict[str, Any]:
        return {
            "measurement_state": "unavailable",
            "active": None,
            "ttl_sec": self._policy.ttl_seconds,
            "paths": [],
            "clients": [],
        }


def normalize_heartbeat_path(value: Any) -> str:
    """Return only a route path, never query parameters or fragments."""

    raw = str(value or "/").strip()
    try:
        path = urlsplit(raw).path
    except ValueError:
        return "/"
    path = _CONTROL_CHARACTER_PATTERN.sub("", path or "/")
    if not path.startswith("/"):
        return "/"
    return path[:256] or "/"


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _path_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _iso_from_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
