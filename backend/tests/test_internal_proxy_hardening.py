from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from backend import serve_prod


class ChunkStream(httpx.AsyncByteStream):
    def __init__(
        self,
        chunks: list[bytes | Exception],
        before_yield: Callable[[int], None] | None = None,
    ) -> None:
        self.chunks = chunks
        self.before_yield = before_yield
        self.closed = False

    async def __aiter__(self):
        for index, chunk in enumerate(self.chunks):
            if self.before_yield is not None:
                self.before_yield(index)
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class FakeProxyClient:
    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        stream: ChunkStream | None = None,
        send_error: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {"content-type": "application/json"}
        self.stream = stream or ChunkStream([b"{}"])
        self.send_error = send_error
        self.request: httpx.Request | None = None
        self.send_stream_flag: bool | None = None

    def build_request(
        self,
        method: str,
        url: httpx.URL,
        *,
        headers: dict[str, str],
        content: bytes,
    ) -> httpx.Request:
        absolute_url = httpx.URL("http://private-upstream.invalid").join(url)
        self.request = httpx.Request(method, absolute_url, headers=headers, content=content)
        return self.request

    async def send(self, request: httpx.Request, *, stream: bool = False) -> httpx.Response:
        self.request = request
        self.send_stream_flag = stream
        if self.send_error is not None:
            raise self.send_error
        return httpx.Response(
            self.status_code,
            headers=self.headers,
            stream=self.stream,
            request=request,
        )


def _scope(
    path: str,
    *,
    method: str = "GET",
    query_string: bytes = b"",
    authenticated: bool = False,
) -> dict[str, Any]:
    headers: list[tuple[bytes, bytes]] = []
    if authenticated:
        headers.append((b"authorization", b"Bearer active-token"))
    return {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": query_string,
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("globemind.top", 443),
    }


async def _invoke(
    monkeypatch: pytest.MonkeyPatch,
    service: str,
    client: FakeProxyClient,
    scope: dict[str, Any],
    *,
    receive_messages: list[dict[str, Any]] | None = None,
    send_hook: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    if service in {"paper", "bridge"}:
        monkeypatch.setattr(serve_prod, "_backend_client", client)
    else:
        monkeypatch.setattr(serve_prod, "_kg_client", client)
    monkeypatch.setattr(
        serve_prod,
        "get_active_user_from_access_token",
        lambda token: {
            "user_id": 7,
            "username": "active-user",
            "role": "admin" if token == "admin-token" else "user",
        }
        if token in {"active-token", "admin-token"}
        else None,
    )

    queued_messages = list(receive_messages or [])
    receive_calls = 0

    async def receive() -> dict[str, Any]:
        nonlocal receive_calls
        receive_calls += 1
        if not queued_messages:
            raise AssertionError("proxy unexpectedly requested another ASGI message")
        return queued_messages.pop(0)

    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)
        if send_hook is not None:
            send_hook(message)

    if service in {"paper", "bridge"}:
        await serve_prod._proxy_paper_bridge(scope, receive, send, service)
    else:
        await serve_prod._proxy_to_kg(scope, receive, send)
    return sent, receive_calls


def _start_message(messages: list[dict[str, Any]]) -> dict[str, Any]:
    return next(message for message in messages if message["type"] == "http.response.start")


def _response_headers(messages: list[dict[str, Any]]) -> dict[bytes, bytes]:
    return dict(_start_message(messages)["headers"])


def _response_body(messages: list[dict[str, Any]]) -> bytes:
    return b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )


@pytest.mark.parametrize(
    ("service", "path", "expected_path"),
    [
        ("paper", "/data-service/paper/api/search", b"/paper/api/search?q=hello%20world&tag=a%2Fb"),
        ("bridge", "/data-service/bridge/api/search", b"/bridge/api/search?q=hello%20world&tag=a%2Fb"),
        ("kg", "/kg-proxy/api/search", b"/api/search?q=hello%20world&tag=a%2Fb"),
    ],
)
def test_proxy_preserves_query_string(
    monkeypatch: pytest.MonkeyPatch,
    service: str,
    path: str,
    expected_path: bytes,
) -> None:
    client = FakeProxyClient()
    messages, _ = asyncio.run(
        _invoke(
            monkeypatch,
            service,
            client,
            _scope(
                path,
                query_string=b"q=hello%20world&tag=a%2Fb",
                authenticated=True,
            ),
        )
    )

    assert _start_message(messages)["status"] == 200
    assert client.request is not None
    assert client.request.url.raw_path == expected_path
    assert client.send_stream_flag is True


@pytest.mark.parametrize(
    ("service", "path"),
    [
        ("paper", "/data-service/paper/api/items"),
        ("bridge", "/data-service/bridge/api/items"),
        ("kg", "/kg-proxy/api/items"),
    ],
)
@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_anonymous_proxy_api_reads_require_active_authentication(
    monkeypatch: pytest.MonkeyPatch,
    service: str,
    path: str,
    method: str,
) -> None:
    client = FakeProxyClient()
    messages, _ = asyncio.run(
        _invoke(monkeypatch, service, client, _scope(path, method=method))
    )

    assert _start_message(messages)["status"] == 401
    assert client.request is None


@pytest.mark.parametrize(
    ("service", "path"),
    [
        ("paper", "/data-service/paper"),
        ("bridge", "/data-service/bridge/assets/app.js"),
        ("kg", "/kg-proxy/assets/app.js"),
    ],
)
def test_proxy_page_and_static_reads_remain_public(
    monkeypatch: pytest.MonkeyPatch,
    service: str,
    path: str,
) -> None:
    client = FakeProxyClient(headers={"content-type": "application/javascript"})
    messages, _ = asyncio.run(_invoke(monkeypatch, service, client, _scope(path)))

    assert _start_message(messages)["status"] == 200
    assert client.request is not None


@pytest.mark.parametrize(
    ("service", "path"),
    [
        ("paper", "/data-service/paper/admin/export.json"),
        ("bridge", "/data-service/bridge/reports/latest"),
        ("kg", "/kg-proxy/query"),
    ],
)
def test_anonymous_non_static_proxy_reads_are_not_public(
    monkeypatch: pytest.MonkeyPatch,
    service: str,
    path: str,
) -> None:
    client = FakeProxyClient()
    messages, _ = asyncio.run(_invoke(monkeypatch, service, client, _scope(path)))

    assert _start_message(messages)["status"] == 401
    assert client.request is None


@pytest.mark.parametrize("path", ["/kg-proxy/admin.json", "/kg-proxy/export.js"])
def test_root_file_extension_does_not_make_internal_endpoint_public(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    client = FakeProxyClient()
    messages, _ = asyncio.run(_invoke(monkeypatch, "kg", client, _scope(path)))

    assert _start_message(messages)["status"] == 401
    assert client.request is None


@pytest.mark.parametrize("method", ["CONNECT", "TRACE"])
def test_proxy_rejects_unsupported_methods(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    client = FakeProxyClient()
    messages, _ = asyncio.run(
        _invoke(monkeypatch, "kg", client, _scope("/kg-proxy", method=method))
    )

    start = _start_message(messages)
    assert start["status"] == 405
    assert client.request is None


def test_proxy_path_matching_uses_complete_segments() -> None:
    assert serve_prod._path_is_under("/data-service/paper", serve_prod.PAPER_PATH)
    assert serve_prod._path_is_under("/data-service/paper/assets/app.js", serve_prod.PAPER_PATH)
    assert not serve_prod._path_is_under("/data-service/paperwork/api", serve_prod.PAPER_PATH)
    assert serve_prod._contains_api_path_segment("/kg-proxy/v1/api/items")
    assert not serve_prod._contains_api_path_segment("/kg-proxy/apix/items")


def test_proxy_strips_credentials_spoofed_forwarding_and_hop_by_hop_request_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeProxyClient()
    scope = _scope("/kg-proxy/assets/app.js")
    scope["headers"].extend(
        [
            (b"authorization", b"Bearer site-secret"),
            (b"cookie", b"session=site-secret"),
            (b"x-forwarded-for", b"10.0.0.1"),
            (b"connection", b"x-remove-me, keep-alive"),
            (b"x-remove-me", b"private-hop-value"),
            (b"x-safe-client-header", b"kept"),
        ]
    )
    messages, _ = asyncio.run(_invoke(monkeypatch, "kg", client, scope))

    assert _start_message(messages)["status"] == 200
    assert client.request is not None
    forwarded = client.request.headers
    assert "authorization" not in forwarded
    assert "cookie" not in forwarded
    assert "x-forwarded-for" not in forwarded
    assert "connection" not in forwarded
    assert "x-remove-me" not in forwarded
    assert forwarded["x-safe-client-header"] == "kept"


@pytest.mark.parametrize(
    ("service", "path"),
    [
        ("paper", "/data-service/paper/home"),
        ("bridge", "/data-service/bridge/home"),
        ("kg", "/kg-proxy/home"),
    ],
)
@pytest.mark.parametrize("location", ["https://private.internal/login", "//private.internal/login"])
def test_proxy_strips_unsafe_response_headers_and_absolute_locations(
    monkeypatch: pytest.MonkeyPatch,
    service: str,
    path: str,
    location: str,
) -> None:
    client = FakeProxyClient(
        status_code=302,
        headers={
            "content-type": "text/plain",
            "cache-control": "private",
            "location": location,
            "set-cookie": "private_session=secret",
            "server": "private-upstream/1.0",
            "x-internal-route": "database-primary",
        },
        stream=ChunkStream([b"redirect"]),
    )
    messages, _ = asyncio.run(
        _invoke(monkeypatch, service, client, _scope(path, authenticated=True))
    )
    headers = _response_headers(messages)

    assert headers[b"content-type"] == b"text/plain"
    assert headers[b"cache-control"] == b"private"
    assert b"x-request-id" in headers
    assert b"location" not in headers
    assert b"set-cookie" not in headers
    assert b"server" not in headers
    assert b"x-internal-route" not in headers


@pytest.mark.parametrize(
    ("service", "path", "location", "expected"),
    [
        (
            "paper",
            "/data-service/paper/start",
            "/paper/login?next=home#form",
            b"/data-service/paper/login?next=home#form",
        ),
        (
            "bridge",
            "/data-service/bridge/section/start",
            "../login?next=home",
            b"/data-service/bridge/login?next=home",
        ),
        ("kg", "/kg-proxy/start", "/login", b"/kg-proxy/login"),
    ],
)
def test_proxy_rewrites_safe_root_and_relative_locations(
    monkeypatch: pytest.MonkeyPatch,
    service: str,
    path: str,
    location: str,
    expected: bytes,
) -> None:
    client = FakeProxyClient(
        status_code=302,
        headers={"content-type": "text/plain", "location": location},
    )
    messages, _ = asyncio.run(
        _invoke(monkeypatch, service, client, _scope(path, authenticated=True))
    )

    assert _response_headers(messages)[b"location"] == expected


def test_proxy_strips_location_that_escapes_public_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeProxyClient(
        status_code=302,
        headers={
            "content-type": "text/plain",
            "location": "/data-service/paper/../../private",
        },
    )
    messages, _ = asyncio.run(
        _invoke(
            monkeypatch,
            "paper",
            client,
            _scope("/data-service/paper/start", authenticated=True),
        )
    )

    assert b"location" not in _response_headers(messages)


@pytest.mark.parametrize(
    ("service", "path"),
    [
        ("paper", "/data-service/paper/home"),
        ("bridge", "/data-service/bridge/home"),
        ("kg", "/kg-proxy/home"),
    ],
)
def test_proxy_errors_hide_internal_detail_and_emit_request_id(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    service: str,
    path: str,
) -> None:
    internal_detail = "private-db.internal:5432 password=do-not-return"
    client = FakeProxyClient(send_error=RuntimeError(internal_detail))
    with caplog.at_level(logging.ERROR, logger="globemind.proxy"):
        messages, _ = asyncio.run(
            _invoke(monkeypatch, service, client, _scope(path, authenticated=True))
        )

    start = _start_message(messages)
    headers = dict(start["headers"])
    payload = json.loads(_response_body(messages))
    request_id = payload["request_id"]

    assert start["status"] == 502
    assert internal_detail.encode() not in _response_body(messages)
    assert headers[b"x-request-id"].decode() == request_id
    assert internal_detail in caplog.text
    assert request_id in caplog.text


@pytest.mark.parametrize(
    ("event", "expected_status"),
    [
        ({"type": "http.disconnect"}, 499),
        ({"type": "lifespan.startup"}, 400),
    ],
)
@pytest.mark.parametrize(
    ("service", "path"),
    [
        ("paper", "/data-service/paper/write"),
        ("bridge", "/data-service/bridge/write"),
        ("kg", "/kg-proxy/write"),
    ],
)
def test_proxy_request_body_stops_on_disconnect_or_unknown_event(
    monkeypatch: pytest.MonkeyPatch,
    service: str,
    path: str,
    event: dict[str, str],
    expected_status: int,
) -> None:
    client = FakeProxyClient()
    messages, receive_calls = asyncio.run(
        _invoke(
            monkeypatch,
            service,
            client,
            _scope(path, method="POST", authenticated=True),
            receive_messages=[event],
        )
    )

    assert _start_message(messages)["status"] == expected_status
    assert receive_calls == 1
    assert client.request is None


@pytest.mark.parametrize(
    ("service", "path"),
    [
        ("paper", "/data-service/paper/events"),
        ("bridge", "/data-service/bridge/events"),
        ("kg", "/kg-proxy/events"),
    ],
)
def test_non_html_proxy_is_streamed_and_response_is_closed(
    monkeypatch: pytest.MonkeyPatch,
    service: str,
    path: str,
) -> None:
    sent: list[dict[str, Any]] = []

    def before_yield(index: int) -> None:
        if index == 1:
            assert any(
                message["type"] == "http.response.body" and message.get("body") == b"first"
                for message in sent
            )

    stream = ChunkStream([b"first", b"second"], before_yield=before_yield)
    client = FakeProxyClient(headers={"content-type": "application/octet-stream"}, stream=stream)

    def send_hook(message: dict[str, Any]) -> None:
        sent.append(message)

    messages, _ = asyncio.run(
        _invoke(
            monkeypatch,
            service,
            client,
            _scope(path, authenticated=True),
            send_hook=send_hook,
        )
    )

    body_messages = [message for message in messages if message["type"] == "http.response.body"]
    assert [message.get("body") for message in body_messages] == [b"first", b"second", b""]
    assert body_messages[0]["more_body"] is True
    assert body_messages[-1]["more_body"] is False
    assert client.send_stream_flag is True
    assert stream.closed is True


@pytest.mark.parametrize(
    ("service", "path"),
    [
        ("paper", "/data-service/paper/events"),
        ("bridge", "/data-service/bridge/events"),
        ("kg", "/kg-proxy/events"),
    ],
)
def test_stream_failure_after_start_never_sends_a_second_response_start(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    service: str,
    path: str,
) -> None:
    internal_detail = "private upstream stream failed"
    stream = ChunkStream([b"first", RuntimeError(internal_detail)])
    client = FakeProxyClient(headers={"content-type": "application/octet-stream"}, stream=stream)

    with caplog.at_level(logging.ERROR, logger="globemind.proxy"):
        messages, _ = asyncio.run(
            _invoke(monkeypatch, service, client, _scope(path, authenticated=True))
        )

    starts = [message for message in messages if message["type"] == "http.response.start"]
    bodies = [message for message in messages if message["type"] == "http.response.body"]
    assert len(starts) == 1
    assert starts[0]["status"] == 200
    assert [message.get("body") for message in bodies] == [b"first", b""]
    assert bodies[-1]["more_body"] is False
    assert internal_detail.encode() not in _response_body(messages)
    assert internal_detail in caplog.text
    assert stream.closed is True


@pytest.mark.parametrize(
    ("service", "path"),
    [
        ("paper", "/data-service/paper/home"),
        ("bridge", "/data-service/bridge/home"),
        ("kg", "/kg-proxy/home"),
    ],
)
def test_html_proxy_has_strict_buffer_limit_and_closes_response(
    monkeypatch: pytest.MonkeyPatch,
    service: str,
    path: str,
) -> None:
    monkeypatch.setattr(serve_prod, "_PROXY_HTML_LIMIT", 8)
    stream = ChunkStream([b"1234", b"56789"])
    client = FakeProxyClient(headers={"content-type": "text/html; charset=utf-8"}, stream=stream)
    messages, _ = asyncio.run(
        _invoke(monkeypatch, service, client, _scope(path, authenticated=True))
    )

    assert _start_message(messages)["status"] == 502
    assert b"123456789" not in _response_body(messages)
    assert stream.closed is True
