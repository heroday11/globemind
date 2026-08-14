from __future__ import annotations

import json
from types import SimpleNamespace

from starlette.requests import Request

from api.core.http_security import RequestRateLimitMiddleware
from api.routes import ops_monitor


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/ops/heartbeat",
            "headers": [(b"user-agent", b"must-not-be-read")],
            "client": ("127.0.0.1", 1234),
        }
    )


def test_heartbeat_store_is_capped_and_atomically_published(tmp_path, monkeypatch) -> None:
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(ops_monitor, "RUNTIME_ROOT", runtime)
    monkeypatch.setattr(ops_monitor, "HEARTBEAT_FILE", runtime / "heartbeats.json")
    monkeypatch.setattr(ops_monitor, "HEARTBEAT_LOCK", runtime / "heartbeats.lock")
    monkeypatch.setattr(ops_monitor, "HEARTBEAT_MAX_CLIENTS", 100)
    monkeypatch.setattr(ops_monitor, "HEARTBEAT_TTL_SEC", 3600)

    for index in range(125):
        ops_monitor._heartbeat_update(
            ops_monitor.HeartbeatPayload(client_id=f"client-{index:04d}", path="/test"),
        )

    stored = json.loads(ops_monitor.HEARTBEAT_FILE.read_text(encoding="utf-8"))
    assert len(stored) == 100
    assert "client-0124" in stored
    assert "client-0000" not in stored
    assert ops_monitor.HEARTBEAT_FILE.stat().st_mode & 0o777 == 0o600
    assert list(runtime.glob(".heartbeats.json.*.tmp")) == []


def test_heartbeat_has_a_dedicated_rate_limit_rule(monkeypatch) -> None:
    monkeypatch.setenv("HEARTBEAT_RATE_LIMIT_REQUESTS", "7")
    middleware = RequestRateLimitMiddleware(SimpleNamespace())

    rule = middleware.rule_for("POST", "/api/ops/heartbeat")

    assert rule is not None
    assert rule.name == "heartbeat"
    assert rule.requests == 7


def test_public_heartbeat_response_does_not_disclose_online_clients(monkeypatch) -> None:
    observed = {}
    monkeypatch.setattr(
        ops_monitor,
        "_heartbeat_update",
        lambda payload, _request: observed.update(
            path=payload.path,
        )
        or {
            "active": 2,
            "paths": [{"path": "/reset-password", "count": 1}],
            "clients": [{"path": "/private"}],
        },
    )

    response = ops_monitor.heartbeat(
        ops_monitor.HeartbeatPayload(
            client_id="client-public",
            path="/reset-password?token=secret",
        ),
        _request(),
    )

    assert response == {"ok": True}
    assert observed["path"] == "/reset-password?token=secret"
