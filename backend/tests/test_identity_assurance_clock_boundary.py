from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from api.features.identity import (
    IdentityAssuranceStore,
    IdentityAssuranceUnavailable,
)
from api.routes.auth import (
    _StrictIdentitySecurityRoute,
    _require_unambiguous_identity_security_json,
    router as auth_router,
)
from api.services.auth import get_current_user_required


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 8, 9, 22, 20, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now


def _event_count(root: Path, user_id: int) -> int:
    event_root = root / "users" / str(user_id) / "events"
    return len(list(event_root.glob("*.json"))) if event_root.exists() else 0


def _request(
    body: bytes,
    *,
    content_type: str = "application/json",
    content_length: str | None = None,
) -> Request:
    sent = False

    async def receive() -> dict[str, object]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    headers = []
    if content_type:
        headers.append((b"content-type", content_type.encode("ascii")))
    headers.append(
        (
            b"content-length",
            (content_length if content_length is not None else str(len(body))).encode(
                "ascii"
            ),
        )
    )
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/user/security/mfa/confirm",
            "headers": headers,
        },
        receive,
    )


def test_read_and_append_reject_clock_rollback_without_advancing_ledger(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    root = tmp_path / "identity-assurance"
    store = IdentityAssuranceStore(root, clock=clock)
    store.begin_enrollment(7, account_label="alice")
    assert _event_count(root, 7) == 1

    clock.now -= timedelta(seconds=1)
    with pytest.raises(IdentityAssuranceUnavailable, match="CLOCK_ROLLBACK"):
        store.status(7)
    assert _event_count(root, 7) == 1


def test_future_session_timestamp_is_rejected_before_event_append(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    root = tmp_path / "identity-assurance"
    store = IdentityAssuranceStore(root, clock=clock)

    with pytest.raises(IdentityAssuranceUnavailable, match="FUTURE_TIMESTAMP"):
        store.issue_session(
            8,
            jti="j" * 43,
            issued_at=clock.now + timedelta(minutes=5),
            expires_at=clock.now + timedelta(hours=1),
            auth_version="auth-version-1",
        )
    assert _event_count(root, 8) == 0


def test_status_exposes_bounded_unconfigured_enterprise_capabilities(
    tmp_path: Path,
) -> None:
    status = IdentityAssuranceStore(tmp_path / "identity-assurance").status(9)

    assert status["capability_inventory"] == {
        "schema_version": "identity-security-capabilities-v1",
        "evidence_scope": "repository_source_and_local_ledger_only",
        "totp": "available",
        "recovery_codes": "available",
        "tracked_web_sessions": "available",
        "institutional_sso": "not_configured",
        "security_keys": "not_configured",
        "trusted_devices": "not_configured",
        "device_attestation": "not_configured",
        "runtime_idp_attestation": "not_available",
        "independent_security_review": "not_provided",
    }


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        (b'{"code":"123456","code":"654321"}', "application/json"),
        (b'{"code":NaN}', "application/json"),
        (b'{"code":1e400}', "application/json"),
        (b'{"a":{"b":{"c":{"d":{"e":1}}}}}', "application/json"),
        (b'["123456"]', "application/json"),
        (b'{"code":"123456"}', "text/plain"),
        (b'{"reason":"\\ud800"}', "application/json"),
        (b'{"reason":"line\\u0000break"}', "application/json"),
        (b"{" + b'"padding":"' + (b"x" * 5000) + b'"}', "application/json"),
    ],
)
def test_identity_security_mutation_json_is_strict_and_bounded(
    body: bytes,
    content_type: str,
) -> None:
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(
            _require_unambiguous_identity_security_json(
                _request(body, content_type=content_type)
            )
        )
    assert rejected.value.status_code == 422
    assert rejected.value.detail == {
        "code": "IDENTITY_SECURITY_JSON_INVALID",
        "message": "身份安全写入必须是受界、无歧义的 JSON 对象",
    }
    assert rejected.value.headers == {
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }


def test_identity_security_routes_use_strict_wrapper_and_enrollment_allows_no_body() -> None:
    paths = {
        "/api/user/security/mfa/enroll",
        "/api/user/security/mfa/confirm",
        "/api/user/security/mfa/disable",
        "/api/user/security/sessions/{session_id}/revoke",
        "/api/user/security/sessions/revoke-others",
    }
    matched = [route for route in auth_router.routes if getattr(route, "path", "") in paths]
    assert {route.path for route in matched} == paths
    assert all(isinstance(route, _StrictIdentitySecurityRoute) for route in matched)
    asyncio.run(
        _require_unambiguous_identity_security_json(
            _request(b"", content_type=""),
            allow_empty=True,
        )
    )


@pytest.mark.parametrize("content_length", ["invalid", "1", "9999"])
def test_identity_security_json_rejects_ambiguous_content_length(
    content_length: str,
) -> None:
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(
            _require_unambiguous_identity_security_json(
                _request(b'{"code":"123456"}', content_length=content_length)
            )
        )
    assert rejected.value.detail["code"] == "IDENTITY_SECURITY_JSON_INVALID"


def test_identity_security_http_wrapper_is_no_store_and_rejects_before_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IDENTITY_ASSURANCE_ROOT", str(tmp_path / "assurance"))
    app = FastAPI()
    app.include_router(auth_router)
    app.dependency_overrides[get_current_user_required] = lambda: {
        "user_id": 7,
        "username": "alice",
        "session_tracking": "tracked",
        "jti": "j" * 43,
        "auth_version": "auth-version-1",
    }
    client = TestClient(app)

    status = client.get("/api/user/security/mfa")
    assert status.status_code == 200
    assert status.headers["cache-control"] == "private, no-store"
    assert status.headers["x-content-type-options"] == "nosniff"
    assert not (tmp_path / "assurance").exists()

    rejected = client.post(
        "/api/user/security/mfa/confirm",
        content=b'{"code":"123456","code":"654321"}',
        headers={"Content-Type": "application/json"},
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "IDENTITY_SECURITY_JSON_INVALID"
    assert rejected.headers["cache-control"] == "private, no-store"
    assert rejected.headers["x-content-type-options"] == "nosniff"

    invalid_contract = client.post(
        "/api/user/security/mfa/confirm",
        json={"code": "secret-invalid-code"},
    )
    assert invalid_contract.status_code == 422
    assert invalid_contract.json() == {
        "detail": {
            "code": "IDENTITY_SECURITY_REQUEST_INVALID",
            "message": "身份安全请求字段无效",
        }
    }
    assert "secret-invalid-code" not in invalid_contract.text
    assert invalid_contract.headers["cache-control"] == "private, no-store"
    assert invalid_contract.headers["x-content-type-options"] == "nosniff"
