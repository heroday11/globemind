"""Security regression tests for expensive and state-changing API routes."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from api.application import app
from api.routes import financial
from api.services.auth import create_access_token
from api.services.auth import AUTH_USER
from api.services import auth as auth_service
from api.core import db as db_module
from backend import serve_prod
from cppt import cc_bridge
import api.application as application


_TEST_USER_ID = 1001
_TEST_ADMIN_ID = 1002
_TEST_USER_PASSWORD_HASH = "test-user-password-hash"
_TEST_ADMIN_PASSWORD_HASH = "test-admin-password-hash"


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(app)
    yield test_client
    test_client.close()


def _auth_headers() -> dict[str, str]:
    token = create_access_token(
        user_id=_TEST_USER_ID,
        username="security-test",
        password_hash=_TEST_USER_PASSWORD_HASH,
    )
    return {"Authorization": f"Bearer {token}"}


def _admin_headers() -> dict[str, str]:
    token = create_access_token(
        user_id=_TEST_ADMIN_ID,
        username=AUTH_USER,
        password_hash=_TEST_ADMIN_PASSWORD_HASH,
    )
    return {"Authorization": f"Bearer {token}"}


class _IdentityLookupQuery:
    def __init__(self, rows: dict[int, object]):
        self._rows = rows
        self._user_id: int | None = None

    def filter(self, *criteria: object, **_kwargs: object) -> "_IdentityLookupQuery":
        for criterion in criteria:
            left = getattr(criterion, "left", None)
            right = getattr(criterion, "right", None)
            if getattr(left, "key", None) == "id":
                value = getattr(right, "value", None)
                if isinstance(value, int):
                    self._user_id = value
        return self

    def first(self) -> object | None:
        return self._rows.get(self._user_id or -1)


class _IdentityLookupSession:
    def __init__(self):
        self._rows = {
            _TEST_USER_ID: SimpleNamespace(
                id=_TEST_USER_ID,
                username="security-test",
                password_hash=_TEST_USER_PASSWORD_HASH,
                is_active=True,
                role="user",
            ),
            _TEST_ADMIN_ID: SimpleNamespace(
                id=_TEST_ADMIN_ID,
                username=AUTH_USER,
                password_hash=_TEST_ADMIN_PASSWORD_HASH,
                is_active=True,
                role="admin",
            ),
        }

    def query(self, *_args: object, **_kwargs: object) -> _IdentityLookupQuery:
        return _IdentityLookupQuery(self._rows)

    def close(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _active_test_identity_directory(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db_module, "SessionLocal", _IdentityLookupSession)


@pytest.fixture
def production_behavior(monkeypatch: pytest.MonkeyPatch):
    """Exercise production-only request behavior while APP_ENV remains isolated."""
    monkeypatch.setattr(application, "is_production", lambda: True)
    monkeypatch.setattr(serve_prod, "is_production", lambda: True)


class _AdminLookupQuery:
    def __init__(self, row: object | None):
        self._row = row

    def filter(self, *_args: object, **_kwargs: object) -> "_AdminLookupQuery":
        return self

    def first(self) -> object | None:
        return self._row


class _AdminLookupSession:
    def __init__(self, row: object | None):
        self._row = row

    def query(self, *_args: object, **_kwargs: object) -> _AdminLookupQuery:
        return _AdminLookupQuery(self._row)

    def close(self) -> None:
        return None


@pytest.mark.parametrize(
    ("row", "token_user", "expected"),
    [
        (SimpleNamespace(username="admin", password_hash="hash", is_active=True, role="admin"), {"user_id": 1, "username": "admin", "auth_version": auth_service._password_auth_version("hash")}, True),
        (SimpleNamespace(username="other", password_hash="hash", is_active=True, role="admin"), {"user_id": 1, "username": "admin", "auth_version": auth_service._password_auth_version("hash")}, False),
        (SimpleNamespace(username="admin", password_hash="hash", is_active=False, role="admin"), {"user_id": 1, "username": "admin", "auth_version": auth_service._password_auth_version("hash")}, False),
        (SimpleNamespace(username="admin", password_hash="hash", is_active=True, role="user"), {"user_id": 1, "username": "admin", "auth_version": auth_service._password_auth_version("hash")}, False),
        (None, {"user_id": 1, "username": "admin", "auth_version": auth_service._password_auth_version("hash")}, False),
    ],
)
def test_admin_authorization_uses_active_database_role(
    monkeypatch: pytest.MonkeyPatch,
    row: object | None,
    token_user: dict[str, Any],
    expected: bool,
) -> None:
    monkeypatch.setattr(db_module, "SessionLocal", lambda: _AdminLookupSession(row))

    assert auth_service.is_admin_user(token_user) is expected


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/llm/v1/models", None),
        ("POST", "/api/ai/analyze", {"text": "test"}),
        ("POST", "/api/ai/analyze/stream", {"text": "test"}),
        ("POST", "/api/assistant/chat", {"message": "test"}),
        ("POST", "/api/financial/alert/rules", {"metric": "test", "threshold": 1}),
        ("PUT", "/api/financial/alert/rules/test", {"threshold": 1}),
        ("DELETE", "/api/financial/alert/rules/test", None),
    ],
)
def test_sensitive_routes_reject_anonymous_requests(
    client: TestClient,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    response = client.request(method, path, json=body)

    assert response.status_code == 401
    assert response.json()["detail"] == "未登录或 token 无效"


def test_sensitive_route_rejects_token_for_nonexistent_user(client: TestClient) -> None:
    token = create_access_token(
        user_id=404404,
        username="missing-user",
        password_hash="missing-user-password-hash",
    )

    response = client.get(
        "/llm/v1/models",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "未登录或 token 无效"


def test_production_wrapper_does_not_bypass_llm_auth() -> None:
    client = TestClient(serve_prod.app)
    try:
        response = client.get("/llm/v1/models")
    finally:
        client.close()

    assert response.status_code == 401


@pytest.mark.parametrize("path", ["/", "/api/health/live", "/cc/chat"])
def test_production_wrapper_applies_security_headers_to_every_surface(
    path: str,
    production_behavior,
) -> None:
    with TestClient(serve_prod.app) as client:
        response = client.post(path, json={"message": "test"}) if path == "/cc/chat" else client.get(path)

    assert response.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"


@pytest.mark.parametrize("path", ["/kg-proxy/api/query", "/data-service/paper/api/query", "/data-service/bridge/api/query"])
def test_internal_proxy_write_requires_authentication(path: str) -> None:
    with TestClient(serve_prod.app) as client:
        response = client.post(path, json={"query": "test"})

    assert response.status_code == 401


def test_internal_proxy_destructive_method_requires_admin() -> None:
    with TestClient(serve_prod.app) as client:
        response = client.delete("/kg-proxy/api/items/1", headers=_auth_headers())

    assert response.status_code == 403


def test_internal_proxy_rejects_oversized_authenticated_body() -> None:
    with TestClient(serve_prod.app) as client:
        response = client.post(
            "/kg-proxy/api/query",
            content=b"x" * (serve_prod._PROXY_BODY_LIMIT + 1),
            headers=_auth_headers(),
        )

    assert response.status_code == 413


@pytest.mark.parametrize("path", ["/cc/chat", "/data-service/cc/chat"])
def test_production_wrapper_enforces_tracked_cc_auth(path: str, production_behavior) -> None:
    client = TestClient(serve_prod.app)
    try:
        response = client.post(path, json={"message": "test"})
    finally:
        client.close()

    assert response.status_code == 404


@pytest.mark.parametrize("path", ["/cc/config", "/data-service/cc/config"])
def test_production_wrapper_hides_cc_configuration(path: str, production_behavior) -> None:
    with TestClient(serve_prod.app) as client:
        response = client.get(path)

    assert response.status_code == 404
    assert "local_sandbox_root" not in response.text


def test_cc_health_remains_public(client: TestClient) -> None:
    response = client.get("/cc/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_cc_auth_does_not_rely_on_ignored_router_dependencies(
    client: TestClient,
    production_behavior,
) -> None:
    route = next(route for route in app.routes if getattr(route, "path", None) == "/cc/chat")
    route_dependencies = list(route.dependencies)
    dependant_dependencies = list(route.dependant.dependencies)
    route.dependencies.clear()
    route.dependant.dependencies.clear()
    try:
        response = client.post("/cc/chat", json={"message": "test"})
    finally:
        route.dependencies[:] = route_dependencies
        route.dependant.dependencies[:] = dependant_dependencies

    assert response.status_code == 404


def test_llm_cors_preflight_does_not_require_a_token(client: TestClient) -> None:
    response = client.options(
        "/llm/v1/models",
        headers={
            "Origin": "https://globemind.top",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://globemind.top"


def test_authenticated_llm_proxy_scrubs_site_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeUpstreamClient:
        forwarded_headers: dict[str, str] = {}

        def build_request(self, method: str, url: str, headers: dict[str, str], content: bytes):
            self.forwarded_headers = {key.lower(): value for key, value in headers.items()}
            return httpx.Request(method, f"http://vllm.test{url}", headers=headers, content=content)

        async def send(self, request: httpx.Request, stream: bool = False) -> httpx.Response:
            assert stream is True
            return httpx.Response(
                200,
                content=json.dumps({"data": []}).encode(),
                headers={"content-type": "application/json"},
                request=request,
            )

    upstream = FakeUpstreamClient()

    async def fake_get_llm_client() -> FakeUpstreamClient:
        return upstream

    monkeypatch.setattr(application, "_get_llm_client", fake_get_llm_client)
    client = TestClient(serve_prod.app)
    try:
        response = client.get(
            "/llm/v1/models",
            headers={**_auth_headers(), "Cookie": "session=private"},
        )
    finally:
        client.close()

    assert response.status_code == 200
    assert response.json() == {"data": []}
    assert "authorization" not in upstream.forwarded_headers
    assert "cookie" not in upstream.forwarded_headers


def test_authenticated_cc_chat_reaches_backend(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_cli(body: cc_bridge.CCChatRequest) -> cc_bridge.CCChatResponse:
        assert body.message == "test"
        return cc_bridge.CCChatResponse(reply="ok", status="ok")

    monkeypatch.setenv("CC_BACKEND", "cli")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr(cc_bridge, "_cc_chat_via_cli", fake_cli)

    response = client.post("/cc/chat", json={"message": "test"}, headers=_auth_headers())

    assert response.status_code == 200
    assert response.json()["reply"] == "ok"


def test_financial_alert_reads_remain_public(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_ids = [f"source-{index}" for index in range(1, 5)]
    trust = {
        "schema_version": "financial-trust-v1",
        "snapshot_id": "fin-auth-test",
        "trust_status": "trusted",
        "freshness_status": "live",
        "computability": "computable",
        "computable": True,
        "data_as_of": "2026-08-09T08:00:00Z",
        "coverage_ratio": 1.0,
        "minimum_coverage_ratio": 0.5,
        "usable_sources": 4,
        "source_total": 4,
        "usable_source_ids": source_ids,
        "unavailable_source_ids": [],
        "source_status": {"live": 4},
        "model_version": "deterministic-ruleset-v0.9.0",
        "method_version": "world-state-composite-v0.9.0",
        "unavailable_reasons": [],
        "alerts_enabled": True,
        "method": {
            "source_inventory_bound": 128,
            "source_weighting": "not_established",
            "contribution_semantics": (
                "availability_gate_only_not_numeric_attribution"
            ),
        },
    }

    async def fake_dashboard(refresh: bool = False) -> dict[str, Any]:
        return {
            **trust,
            "trust": trust,
            "coverage": {
                "coverage_ratio": 1.0,
                "minimum_coverage_ratio": 0.5,
                "usable_sources": 4,
                "sources_total": 4,
                "source_status": {"live": 4},
            },
            "sources": [
                {
                    "id": source_id,
                    "freshness_status": "live",
                    "records": 1,
                    "contribution_state": "usable",
                }
                for source_id in source_ids
            ],
            "alerts_suppressed": False,
        }

    async def fake_rules(
        refresh: bool = False,
        dashboard: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        assert refresh is False
        assert dashboard is not None
        return [{"id": "system-test", "source": "system"}]

    monkeypatch.setattr(financial, "get_dashboard", fake_dashboard)
    monkeypatch.setattr(financial, "_financial_alert_rules", fake_rules)

    response = client.get("/api/financial/alert/rules")

    assert response.status_code == 200
    data = response.json()
    assert data["rules"] == [{"id": "system-test", "source": "system"}]
    assert data["paused"] is True
    assert data["trust_status"] == "unavailable"
    assert data["computability"] == "not_computable"
    assert data["composite_method_card"]["approval_status"] == "not_approved"
    assert data["snapshot_id"] == "fin-auth-test"


def test_non_admin_cannot_mutate_global_financial_alerts(client: TestClient) -> None:
    response = client.post(
        "/api/financial/alert/rules",
        json={"metric": "test metric", "threshold": 10},
        headers=_auth_headers(),
    )

    assert response.status_code == 403


def test_admin_financial_alert_create_is_paused_without_approved_method(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    async def fake_dashboard(refresh: bool = False) -> dict[str, Any]:
        source_ids = [f"source-{index}" for index in range(1, 5)]
        trust = {
            "schema_version": "financial-trust-v1",
            "snapshot_id": "fin-admin-test",
            "trust_status": "trusted",
            "freshness_status": "live",
            "computability": "computable",
            "computable": True,
            "data_as_of": "2026-08-09T08:00:00Z",
            "coverage_ratio": 1.0,
            "minimum_coverage_ratio": 0.5,
            "usable_sources": 4,
            "source_total": 4,
            "usable_source_ids": source_ids,
            "unavailable_source_ids": [],
            "source_status": {"live": 4},
            "model_version": "deterministic-ruleset-v0.9.0",
            "method_version": "world-state-composite-v0.9.0",
            "unavailable_reasons": [],
            "alerts_enabled": True,
            "method": {
                "source_inventory_bound": 128,
                "source_weighting": "not_established",
                "contribution_semantics": (
                    "availability_gate_only_not_numeric_attribution"
                ),
            },
        }
        return {
            "indices": [],
            "watchlist": [],
            "series": [],
            **trust,
            "trust": trust,
            "coverage": {
                "coverage_ratio": 1.0,
                "minimum_coverage_ratio": 0.5,
                "usable_sources": 4,
                "sources_total": 4,
                "source_status": {"live": 4},
            },
            "sources": [
                {
                    "id": source_id,
                    "freshness_status": "live",
                    "records": 1,
                    "contribution_state": "usable",
                }
                for source_id in source_ids
            ],
            "alerts_suppressed": False,
        }

    monkeypatch.setattr(financial, "ALERT_RULES_STORE", tmp_path / "rules.json")
    monkeypatch.setattr(financial, "get_dashboard", fake_dashboard)

    response = client.post(
        "/api/financial/alert/rules",
        json={"metric": "test metric", "threshold": 10},
        headers=_admin_headers(),
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "FINANCIAL_INDEX_NOT_COMPUTABLE"
    assert (tmp_path / "rules.json").exists() is False
