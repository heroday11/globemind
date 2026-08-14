from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .storage import (
    atomic_write_json,
    exclusive_file_lock,
    read_json_bounded,
    shared_file_lock,
)


@dataclass(frozen=True)
class MonitoringHistoryPolicy:
    max_points: int = 1200
    minimum_interval_seconds: float = 0.25

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_points, bool)
            or not isinstance(self.max_points, int)
            or self.max_points < 1
        ):
            raise ValueError("history max_points must be positive")
        if (
            isinstance(self.minimum_interval_seconds, bool)
            or not isinstance(self.minimum_interval_seconds, (int, float))
            or not math.isfinite(self.minimum_interval_seconds)
            or self.minimum_interval_seconds < 0
        ):
            raise ValueError("history minimum interval must not be negative")


class MonitoringHistoryStore:
    def __init__(
        self,
        *,
        data_path: Path,
        lock_path: Path,
        policy: MonitoringHistoryPolicy,
    ) -> None:
        self._data_path = Path(data_path)
        self._lock_path = Path(lock_path)
        self._policy = policy

    def read(self) -> list[dict[str, Any]]:
        samples, _state = self._read_snapshot()
        return samples

    def _read_snapshot(self) -> tuple[list[dict[str, Any]], str]:
        data_exists = _path_exists(self._data_path)
        lock_exists = _path_exists(self._lock_path)
        if not data_exists and not lock_exists:
            return [], "not_observed"
        if data_exists != lock_exists:
            return [], "unavailable"
        try:
            with shared_file_lock(self._lock_path):
                samples = self._read_unlocked()
        except (OSError, TypeError, UnicodeError, ValueError):
            return [], "unavailable"
        return samples, "observed" if samples else "not_observed"

    def append(self, sample: dict[str, Any]) -> list[dict[str, Any]]:
        timestamp = _number(sample.get("ts"))
        if timestamp is None:
            raise ValueError("history sample requires a numeric ts")
        with exclusive_file_lock(self._lock_path):
            history = self._read_unlocked()
            last_timestamp = _number(history[-1].get("ts")) if history else None
            if last_timestamp is not None and timestamp < last_timestamp:
                raise ValueError("history timestamps must be monotonic")
            if (
                last_timestamp is None
                or timestamp - last_timestamp >= self._policy.minimum_interval_seconds
            ):
                history.append({**sample, "ts": timestamp})
                history = history[-self._policy.max_points :]
                atomic_write_json(self._data_path, history)
            return history

    def payload(
        self,
        samples: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if samples is None:
            history, state = self._read_snapshot()
        else:
            history = samples[-self._policy.max_points :]
            state = "observed" if history else "not_observed"
        return {
            "measurement_state": state,
            "collection_state": "not_configured",
            "sample_count": len(history),
            "max_points": self._policy.max_points,
            "min_interval_sec": self._policy.minimum_interval_seconds,
            "samples": history[-self._policy.max_points :],
        }

    def _read_unlocked(self) -> list[dict[str, Any]]:
        try:
            raw = read_json_bounded(self._data_path)
        except FileNotFoundError:
            return []
        if not isinstance(raw, list):
            raise ValueError("monitoring history contract is invalid")
        if len(raw) > self._policy.max_points:
            raise ValueError("monitoring history bound was exceeded")
        if any(not isinstance(item, dict) for item in raw):
            raise ValueError("monitoring history contract is invalid")
        previous_timestamp: float | None = None
        for item in raw:
            timestamp = _number(item.get("ts"))
            if timestamp is None or (
                previous_timestamp is not None
                and timestamp <= previous_timestamp
            ):
                raise ValueError("monitoring history timestamps are invalid")
            previous_timestamp = timestamp
        return raw


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
