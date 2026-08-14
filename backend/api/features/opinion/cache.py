"""Thread-safe process-local response cache for opinion read models."""

from __future__ import annotations

import hashlib
import math
import threading
import time
from collections.abc import Callable, MutableMapping
from typing import Any

CacheEntry = tuple[float, dict[str, Any]]


class InMemoryResponseCache:
    """Small TTL cache with injectable time for deterministic contract tests."""

    def __init__(
        self,
        storage: MutableMapping[str, CacheEntry] | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.storage = storage if storage is not None else {}
        self._clock = clock
        self._lock = threading.RLock()

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            item = self.storage.get(key)
            if item is None:
                return None
            if self._clock() < item[0]:
                return item[1]
            self.storage.pop(key, None)
            return None

    def set(self, key: str, content: dict[str, Any], ttl: float) -> None:
        safe_ttl = float(ttl)
        if not math.isfinite(safe_ttl):
            safe_ttl = 0.0
        with self._lock:
            self.storage[key] = (self._clock() + max(0.0, safe_ttl), content)

    def clear(self) -> None:
        with self._lock:
            self.storage.clear()


_CACHE = InMemoryResponseCache()
RESPONSE_CACHE_STORAGE = _CACHE.storage


def response_cache_key(func_name: str, **params: Any) -> str:
    raw = func_name + "&".join(f"{key}={value}" for key, value in sorted(params.items()))
    return hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest()


def response_cache_get(key: str) -> dict[str, Any] | None:
    return _CACHE.get(key)


def response_cache_set(key: str, content: dict[str, Any], ttl: float) -> None:
    _CACHE.set(key, content, ttl)


def clear_response_cache() -> None:
    _CACHE.clear()


__all__ = (
    "InMemoryResponseCache",
    "RESPONSE_CACHE_STORAGE",
    "clear_response_cache",
    "response_cache_get",
    "response_cache_key",
    "response_cache_set",
)
