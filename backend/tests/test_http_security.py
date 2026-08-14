from __future__ import annotations

import asyncio
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from api.core.http_security import (  # noqa: E402
    RateLimitRule,
    RequestBodyLimitMiddleware,
    RequestRateLimitMiddleware,
)
from api.services.auth import create_access_token  # noqa: E402


def _scope(*, content_length: int | None = None, path: str = "/api/write") -> dict:
    headers = [] if content_length is None else [(b"content-length", str(content_length).encode())]
    return {"type": "http", "method": "POST", "path": path, "headers": headers}


def _run_request(middleware: RequestBodyLimitMiddleware, scope: dict, chunks: list[bytes]) -> list[dict]:
    sent: list[dict] = []
    queue = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ]

    async def receive() -> dict:
        return queue.pop(0) if queue else {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    asyncio.run(middleware(scope, receive, send))
    return sent


def test_rejects_content_length_before_calling_app():
    called = False

    async def app(_scope, _receive, _send):
        nonlocal called
        called = True

    middleware = RequestBodyLimitMiddleware(app)
    middleware.default_limit = 4

    sent = _run_request(middleware, _scope(content_length=5), [b"12345"])

    assert called is False
    assert sent[0]["status"] == 413


def test_rejects_chunked_body_when_running_total_exceeds_limit():
    async def app(_scope, receive, send):
        while True:
            message = await receive()
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = RequestBodyLimitMiddleware(app)
    middleware.default_limit = 4

    sent = _run_request(middleware, _scope(), [b"12", b"345"])

    assert sent[0]["status"] == 413
    assert len([message for message in sent if message["type"] == "http.response.start"]) == 1


def test_allows_body_at_limit():
    async def app(_scope, receive, send):
        message = await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": message["body"]})

    middleware = RequestBodyLimitMiddleware(app)
    middleware.default_limit = 4

    sent = _run_request(middleware, _scope(content_length=4), [b"1234"])

    assert sent[0]["status"] == 200
    assert sent[1]["body"] == b"1234"


def test_uses_stricter_limit_for_ai_routes():
    async def app(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = RequestBodyLimitMiddleware(app)
    middleware.default_limit = 100
    middleware.ai_limit = 4

    sent = _run_request(middleware, _scope(content_length=5, path="/cc/chat"), [b"12345"])

    assert sent[0]["status"] == 413


def test_rate_limiter_rejects_after_rule_capacity():
    async def app(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = RequestRateLimitMiddleware(app)
    middleware.ai_rule = RateLimitRule("ai-test", 2, 60)
    middleware._clock = lambda: 100.0
    scope = _scope(path="/cc/chat")
    scope["client"] = ("203.0.113.10", 1234)

    assert _run_request(middleware, scope, [b""])[0]["status"] == 204
    assert _run_request(middleware, scope, [b""])[0]["status"] == 204
    rejected = _run_request(middleware, scope, [b""])

    assert rejected[0]["status"] == 429
    assert (b"retry-after", b"60") in rejected[0]["headers"]


def test_rate_limiter_separates_authenticated_tokens():
    async def app(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = RequestRateLimitMiddleware(app)
    middleware.ai_rule = RateLimitRule("ai-test", 1, 60)
    middleware._clock = lambda: 100.0
    first = _scope(path="/llm/v1/chat/completions")
    first_token = create_access_token(101, "first")
    first["headers"] = [(b"authorization", f"Bearer {first_token}".encode())]
    first["client"] = ("203.0.113.31", 1)
    second = _scope(path="/llm/v1/chat/completions")
    second_token = create_access_token(202, "second")
    second["headers"] = [(b"authorization", f"Bearer {second_token}".encode())]
    second["client"] = ("203.0.113.32", 1)

    assert _run_request(middleware, first, [b""])[0]["status"] == 204
    assert _run_request(middleware, first, [b""])[0]["status"] == 429
    assert _run_request(middleware, second, [b""])[0]["status"] == 204


def test_invalid_bearer_tokens_share_the_ip_bucket():
    async def app(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = RequestRateLimitMiddleware(app)
    middleware.ai_rule = RateLimitRule("ai-invalid", 1, 60)
    middleware._clock = lambda: 100.0
    first = _scope(path="/llm/v1/chat/completions")
    first["headers"] = [(b"authorization", b"Bearer invalid-one")]
    first["client"] = ("203.0.113.40", 1)
    second = _scope(path="/llm/v1/chat/completions")
    second["headers"] = [(b"authorization", b"Bearer invalid-two")]
    second["client"] = ("203.0.113.40", 2)

    assert _run_request(middleware, first, [b""])[0]["status"] == 204
    assert _run_request(middleware, second, [b""])[0]["status"] == 429


def test_read_routes_are_not_rate_limited():
    calls = 0

    async def app(_scope, _receive, send):
        nonlocal calls
        calls += 1
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = RequestRateLimitMiddleware(app)
    scope = _scope(path="/api/financial/alert/rules")
    scope["method"] = "GET"

    for _ in range(20):
        assert _run_request(middleware, scope, [b""])[0]["status"] == 200
    assert calls == 20


def test_assistant_stream_uses_ai_body_and_rate_limits():
    async def app(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    body = RequestBodyLimitMiddleware(app)
    body.default_limit = 100
    body.ai_limit = 4
    sent = _run_request(
        body,
        _scope(content_length=5, path="/api/assistant/cc/stream"),
        [b"12345"],
    )
    assert sent[0]["status"] == 413

    rate = RequestRateLimitMiddleware(app)
    assert rate.rule_for("POST", "/api/assistant/cc/stream") == rate.ai_rule
    assert rate.rule_for("POST", "/api/assistant/schedules/1/run") == rate.ai_rule


def test_cleanup_keeps_long_window_registration_bucket():
    async def app(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = RequestRateLimitMiddleware(app)
    middleware.registration_rule = RateLimitRule("registration-test", 1, 900)
    middleware.ai_rule = RateLimitRule("ai-test", 10, 60)
    clock = [0.0]
    middleware._clock = lambda: clock[0]
    registration = _scope(path="/api/auth/register")
    registration["client"] = ("203.0.113.20", 1)
    ai = _scope(path="/api/assistant/cc/stream")
    ai["client"] = ("203.0.113.20", 1)

    assert _run_request(middleware, registration, [b""])[0]["status"] == 204
    clock[0] = 301.0
    assert _run_request(middleware, ai, [b""])[0]["status"] == 204
    assert _run_request(middleware, registration, [b""])[0]["status"] == 429
