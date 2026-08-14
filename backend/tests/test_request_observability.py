from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from api.application import app
from api.core.request_observability import (
    resolve_request_id,
    safe_request_method,
    safe_route_template,
)


class _Route:
    path = "/api/news/{news_id}"


def test_request_id_accepts_only_bounded_opaque_values() -> None:
    fixed = uuid.UUID("12345678-1234-5678-1234-567812345678")

    assert resolve_request_id("candidate-acceptance-001", factory=lambda: fixed) == (
        "candidate-acceptance-001"
    )
    assert resolve_request_id("bad value\nAuthorization: secret", factory=lambda: fixed) == fixed.hex
    assert resolve_request_id("x" * 129, factory=lambda: fixed) == fixed.hex


def test_observability_uses_route_templates_and_never_raw_paths() -> None:
    assert safe_route_template({"route": _Route(), "path": "/api/news/private-user-value"}) == (
        "/api/news/{news_id}"
    )
    assert safe_route_template({"path": "/api/news/private-user-value"}) == "unmatched"
    assert safe_route_template({"route": type("Bad", (), {"path": "/bad?secret=value"})()}) == (
        "unmatched"
    )
    assert safe_route_template({"route": type("Long", (), {"path": "/" + "x" * 256})()}) == (
        "unmatched"
    )


def test_observability_rejects_log_injection_in_request_methods() -> None:
    assert safe_request_method("get") == "GET"
    assert safe_request_method("POST\nsecret=value") == "UNKNOWN"
    assert safe_request_method("X" * 17) == "UNKNOWN"


def test_application_emits_request_correlation_without_logging_query_values(caplog) -> None:
    caplog.set_level("INFO", logger="globemind.http")

    with TestClient(app) as client:
        response = client.get(
            "/api/health/live?private-marker=must-not-be-logged",
            headers={"X-Request-ID": "candidate-observation-001"},
        )

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "candidate-observation-001"
    assert "candidate-observation-001" in caplog.text
    assert "route=/api/health/live" in caplog.text
    assert "must-not-be-logged" not in caplog.text
    assert "authorization" not in caplog.text.lower()
