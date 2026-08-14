from __future__ import annotations

import json
import math
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from api.core.environment import int_setting
from api.core.identity_security import get_user_from_access_token

ASGIApp = Callable[[dict[str, Any], Callable[..., Awaitable[dict[str, Any]]], Callable[..., Awaitable[None]]], Awaitable[None]]


class RequestBodyTooLarge(Exception):
    pass


def _positive_int_env(name: str, default: int) -> int:
    return max(1024, int_setting(name, default))


def _rate_int_env(name: str, default: int) -> int:
    return max(1, int_setting(name, default))


class RequestBodyLimitMiddleware:
    """Reject oversized write requests before an endpoint buffers their body."""

    WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.default_limit = _positive_int_env("HTTP_MAX_REQUEST_BYTES", 8 * 1024 * 1024)
        self.ai_limit = _positive_int_env("HTTP_MAX_AI_REQUEST_BYTES", 2 * 1024 * 1024)
        upload_payload_limit = _positive_int_env(
            "WORKSPACE_UPLOAD_MAX_REQUEST_BYTES",
            100 * 1024 * 1024,
        )
        self.upload_limit = _positive_int_env(
            "HTTP_MAX_UPLOAD_REQUEST_BYTES",
            upload_payload_limit + 2 * 1024 * 1024,
        )

    def limit_for_path(self, path: str) -> int:
        if path.startswith("/api/workspaces/") and path.endswith("/upload"):
            return self.upload_limit
        if path.startswith(("/llm/", "/cc/", "/api/ai/", "/api/assistant/")):
            return self.ai_limit
        return self.default_limit

    @staticmethod
    async def _reject(send: Callable[..., Awaitable[None]], status: int, detail: str) -> None:
        body = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})

    async def __call__(self, scope: dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http" or str(scope.get("method", "GET")).upper() not in self.WRITE_METHODS:
            await self.app(scope, receive, send)
            return

        limit = self.limit_for_path(str(scope.get("path") or ""))
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        raw_content_length = headers.get(b"content-length")
        if raw_content_length:
            try:
                content_length = int(raw_content_length)
            except (TypeError, ValueError):
                await self._reject(send, 400, "Invalid Content-Length header")
                return
            if content_length < 0:
                await self._reject(send, 400, "Invalid Content-Length header")
                return
            if content_length > limit:
                await self._reject(send, 413, f"Request body exceeds the {limit}-byte limit")
                return

        received = 0
        response_started = False

        async def limited_receive() -> dict[str, Any]:
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body") or b"")
                if received > limit:
                    raise RequestBodyTooLarge
            return message

        async def tracked_send(message: dict[str, Any]) -> None:
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except RequestBodyTooLarge:
            if response_started:
                raise
            await self._reject(send, 413, f"Request body exceeds the {limit}-byte limit")


@dataclass(frozen=True)
class RateLimitRule:
    name: str
    requests: int
    window_seconds: int


@dataclass(frozen=True)
class RateLimitSourceDefault:
    id: str
    requests_setting: str
    default_requests: int
    window_setting: str
    default_window_seconds: int
    methods: tuple[str, ...]
    route_matchers: tuple[str, ...]


RATE_LIMIT_SOURCE_DEFAULTS = (
    RateLimitSourceDefault(
        id="auth",
        requests_setting="AUTH_RATE_LIMIT_REQUESTS",
        default_requests=10,
        window_setting="AUTH_RATE_LIMIT_WINDOW_SECONDS",
        default_window_seconds=60,
        methods=("POST",),
        route_matchers=("exact:/api/auth/login",),
    ),
    RateLimitSourceDefault(
        id="registration",
        requests_setting="REGISTRATION_RATE_LIMIT_REQUESTS",
        default_requests=5,
        window_setting="REGISTRATION_RATE_LIMIT_WINDOW_SECONDS",
        default_window_seconds=900,
        methods=("POST",),
        route_matchers=(
            "exact:/api/auth/register",
            "exact:/api/auth/forgot-password",
        ),
    ),
    RateLimitSourceDefault(
        id="ai",
        requests_setting="AI_RATE_LIMIT_REQUESTS",
        default_requests=30,
        window_setting="AI_RATE_LIMIT_WINDOW_SECONDS",
        default_window_seconds=60,
        methods=("POST",),
        route_matchers=(
            "prefix:/llm/",
            "prefix:/api/ai/",
            "prefix:/api/assistant/",
            "exact:/cc/chat",
            "exact:/cc/chat/stream",
        ),
    ),
    RateLimitSourceDefault(
        id="upload",
        requests_setting="UPLOAD_RATE_LIMIT_REQUESTS",
        default_requests=10,
        window_setting="UPLOAD_RATE_LIMIT_WINDOW_SECONDS",
        default_window_seconds=60,
        methods=("POST",),
        route_matchers=("pattern:/api/workspaces/{workspace}/upload",),
    ),
    RateLimitSourceDefault(
        id="heartbeat",
        requests_setting="HEARTBEAT_RATE_LIMIT_REQUESTS",
        default_requests=30,
        window_setting="HEARTBEAT_RATE_LIMIT_WINDOW_SECONDS",
        default_window_seconds=60,
        methods=("POST",),
        route_matchers=("exact:/api/ops/heartbeat",),
    ),
    RateLimitSourceDefault(
        id="mutation",
        requests_setting="MUTATION_RATE_LIMIT_REQUESTS",
        default_requests=60,
        window_setting="MUTATION_RATE_LIMIT_WINDOW_SECONDS",
        default_window_seconds=60,
        methods=("POST", "PUT", "PATCH", "DELETE"),
        route_matchers=("prefix:/api/financial/alert/",),
    ),
)
_RATE_LIMIT_SOURCE_DEFAULT_BY_ID = {
    item.id: item for item in RATE_LIMIT_SOURCE_DEFAULTS
}


def _configured_rate_limit_rule(rule_id: str) -> RateLimitRule:
    source = _RATE_LIMIT_SOURCE_DEFAULT_BY_ID[rule_id]
    return RateLimitRule(
        source.id,
        _rate_int_env(source.requests_setting, source.default_requests),
        _rate_int_env(source.window_setting, source.default_window_seconds),
    )


class RequestRateLimitMiddleware:
    """Small in-process guardrail until the unified runtime provides Redis."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._lock = threading.Lock()
        self._buckets: dict[tuple[str, str], tuple[deque[float], int]] = {}
        self._clock = time.monotonic
        self._last_cleanup = 0.0
        self.auth_rule = _configured_rate_limit_rule("auth")
        self.registration_rule = _configured_rate_limit_rule("registration")
        self.ai_rule = _configured_rate_limit_rule("ai")
        self.upload_rule = _configured_rate_limit_rule("upload")
        self.heartbeat_rule = _configured_rate_limit_rule("heartbeat")
        self.mutation_rule = _configured_rate_limit_rule("mutation")

    def rule_for(self, method: str, path: str) -> RateLimitRule | None:
        if method != "POST" and not (method in {"PUT", "PATCH", "DELETE"} and path.startswith("/api/financial/alert/")):
            return None
        if path == "/api/auth/register" or path == "/api/auth/forgot-password":
            return self.registration_rule
        if path == "/api/auth/login":
            return self.auth_rule
        if path == "/api/ops/heartbeat":
            return self.heartbeat_rule
        if path.startswith(("/llm/", "/api/ai/")) or path.startswith("/api/assistant/") or path in {
            "/cc/chat",
            "/cc/chat/stream",
            "/api/assistant/chat",
        }:
            return self.ai_rule
        if path.startswith("/api/workspaces/") and path.endswith("/upload"):
            return self.upload_rule
        if path.startswith("/api/financial/alert/"):
            return self.mutation_rule
        return None

    @staticmethod
    def _header_map(scope: dict[str, Any]) -> dict[bytes, bytes]:
        return {key.lower(): value for key, value in scope.get("headers", [])}

    @staticmethod
    def _ip_key(scope: dict[str, Any], headers: dict[bytes, bytes]) -> str:
        cf_ip = headers.get(b"cf-connecting-ip", b"").decode("latin-1").strip()
        if cf_ip:
            return "ip:" + cf_ip[:64]
        client = scope.get("client") or ("unknown", 0)
        return "ip:" + str(client[0])[:64]

    def client_keys(self, scope: dict[str, Any]) -> tuple[str, ...]:
        headers = self._header_map(scope)
        ip_key = self._ip_key(scope, headers)
        authorization = headers.get(b"authorization", b"")
        if authorization.lower().startswith(b"bearer "):
            token = authorization[7:].strip().decode("latin-1")
            try:
                user = get_user_from_access_token(token)
            except Exception:
                user = None
            if user and user.get("user_id") is not None:
                return ("user:" + str(user["user_id"]), ip_key)
        return (ip_key,)

    def _retry_after(self, rule: RateLimitRule, key: str, now: float) -> int | None:
        bucket_key = (rule.name, key)
        cutoff = now - rule.window_seconds
        with self._lock:
            bucket, _window = self._buckets.setdefault(bucket_key, (deque(), rule.window_seconds))
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= rule.requests:
                return max(1, math.ceil(rule.window_seconds - (now - bucket[0])))
            bucket.append(now)
            if now - self._last_cleanup >= 300:
                self._last_cleanup = now
                stale = [
                    stored_key
                    for stored_key, (values, window) in self._buckets.items()
                    if not values or values[-1] <= now - window
                ]
                for stale_key in stale:
                    self._buckets.pop(stale_key, None)
        return None

    @staticmethod
    async def _reject(send: Callable[..., Awaitable[None]], retry_after: int) -> None:
        body = json.dumps({"detail": "请求过于频繁，请稍后重试"}, ensure_ascii=False).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                    (b"retry-after", str(retry_after).encode("ascii")),
                    (b"x-content-type-options", b"nosniff"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})

    async def __call__(self, scope: dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        rule = self.rule_for(str(scope.get("method") or "GET").upper(), str(scope.get("path") or ""))
        if rule is None:
            await self.app(scope, receive, send)
            return
        now = self._clock()
        for client_key in self.client_keys(scope):
            retry_after = self._retry_after(rule, client_key, now)
            if retry_after is not None:
                await self._reject(send, retry_after)
                return
        await self.app(scope, receive, send)
