"""轻量内存 TTL 缓存（无 Redis 依赖）。"""
from __future__ import annotations

import time
from functools import wraps
from typing import Any, Callable, Dict, Optional, Tuple


TTL_CACHE: Dict[str, Tuple[float, Any]] = {}


def make_cache_key(prefix: str, **kwargs: Any) -> str:
    """Build a stable key for route-level caches."""
    parts = [prefix]
    for key in sorted(kwargs):
        parts.append(f"{key}={kwargs[key]}")
    return "|".join(parts)


class TTLStore:
    """Small per-module TTL cache for dynamic route keys."""

    def __init__(self, namespace: str = ""):
        self.namespace = namespace.strip(":")
        self._items: Dict[str, Tuple[float, Any]] = {}

    def _key(self, key: str) -> str:
        return f"{self.namespace}:{key}" if self.namespace else key

    def get(self, key: str) -> Optional[Any]:
        cache_key = self._key(key)
        hit = self._items.get(cache_key)
        if not hit:
            return None
        expires_at, value = hit
        if time.monotonic() >= expires_at:
            self._items.pop(cache_key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl_seconds: float) -> Any:
        self._items[self._key(key)] = (time.monotonic() + ttl_seconds, value)
        return value

    def clear(self, prefix: Optional[str] = None) -> int:
        if prefix is None:
            n = len(self._items)
            self._items.clear()
            return n
        full_prefix = self._key(prefix)
        keys = [key for key in self._items if key.startswith(full_prefix)]
        for key in keys:
            del self._items[key]
        return len(keys)


def ttl_cache(ttl_seconds: float = 300):
    """TTL 缓存装饰器，适用无副作用的纯函数。"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            key_parts = [func.__name__]
            key_parts.extend(str(a) for a in args)
            key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
            key = ":".join(key_parts)

            now = time.monotonic()
            if key in TTL_CACHE:
                expires, value = TTL_CACHE[key]
                if now < expires:
                    return value

            result = func(*args, **kwargs)
            TTL_CACHE[key] = (now + ttl_seconds, result)
            return result
        return wrapper
    return decorator


def invalidate_cache(pattern: Optional[str] = None) -> int:
    """按 pattern 前缀清理缓存。pattern=None 清理全部。返回清理条数。"""
    global TTL_CACHE
    if pattern is None:
        n = len(TTL_CACHE)
        TTL_CACHE.clear()
        return n
    keys = [k for k in TTL_CACHE if k.startswith(pattern)]
    for k in keys:
        del TTL_CACHE[k]
    return len(keys)
