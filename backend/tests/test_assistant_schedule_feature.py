from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.core.db import get_db
from api.features.assistant import (
    AssistantScheduleApplication,
    AssistantScheduleConflict,
    AssistantScheduleExecutionFailed,
    AssistantScheduleIdentity,
    AssistantScheduleIdentityError,
    AssistantScheduleNotFound,
    AssistantScheduleOperations,
    AssistantSchedulePayload,
    assistant_schedule_runner_disabled,
    load_assistant_schedule_settings,
)
from api.routes import assistant_schedules
from api.services.auth import get_current_user_required


def _payload(**overrides: Any) -> AssistantSchedulePayload:
    return AssistantSchedulePayload(
        title=overrides.pop("title", "Daily risk brief"),
        topic=overrides.pop("topic", "Supply-chain risk"),
        **overrides,
    )


def _application(
    *,
    deleted: bool = True,
    run_error: Exception | None = None,
    calls: list[tuple[Any, ...]] | None = None,
) -> AssistantScheduleApplication:
    recorded = calls if calls is not None else []

    def list_schedules(username: str, user_id: int) -> list[dict[str, Any]]:
        recorded.append(("list", username, user_id))
        return [{"id": "sched-list"}]

    def upsert_schedule(
        username: str,
        user_id: int,
        payload: dict[str, Any],
        schedule_id: str | None = None,
    ) -> dict[str, Any]:
        recorded.append(("upsert", username, user_id, payload, schedule_id))
        return {"id": schedule_id or "sched-created", **payload}

    def delete_schedule(username: str, schedule_id: str) -> bool:
        recorded.append(("delete", username, schedule_id))
        return deleted

    async def run_schedule(
        username: str,
        user_id: int,
        schedule_id: str,
        db: Any,
        *,
        manual: bool,
    ) -> dict[str, Any]:
        recorded.append(("run", username, user_id, schedule_id, db, manual))
        if run_error is not None:
            raise run_error
        return {"schedule": {"id": schedule_id}, "manual": manual}

    return AssistantScheduleApplication(
        AssistantScheduleOperations(
            list_schedules=list_schedules,
            upsert_schedule=upsert_schedule,
            delete_schedule=delete_schedule,
            run_schedule=run_schedule,
        )
    )


def _route_app(
    monkeypatch: pytest.MonkeyPatch,
    application: AssistantScheduleApplication,
    *,
    claims: dict[str, Any] | None = None,
) -> tuple[FastAPI, object]:
    app = FastAPI()
    app.include_router(assistant_schedules.router)
    database = object()
    monkeypatch.setattr(
        assistant_schedules,
        "build_assistant_schedule_application",
        lambda: application,
    )
    if claims is not None:
        app.dependency_overrides[get_current_user_required] = lambda: claims
    app.dependency_overrides[get_db] = lambda: database
    return app, database


def test_payload_contract_preserves_defaults_and_validation() -> None:
    payload = _payload()

    assert payload.to_schedule_payload()["cadence"] == "daily"
    assert payload.to_schedule_payload()["timezone"] == "Asia/Shanghai"
    assert payload.to_schedule_payload()["include_sources"] is True
    with pytest.raises(ValidationError):
        _payload(cadence="monthly")
    with pytest.raises(ValidationError):
        _payload(interval_hours=0)
    with pytest.raises(ValidationError):
        _payload(time_of_day="8:30")
    with pytest.raises(ValidationError):
        _payload(time_of_day="24:00")


def test_identity_normalizes_claims_and_fails_closed_without_username() -> None:
    identity = AssistantScheduleIdentity.from_claims(
        {"username": " alice ", "user_id": "42"}
    )
    assert identity.username == "alice"
    assert identity.user_id == 42
    with pytest.raises(AssistantScheduleIdentityError, match="缺少 username"):
        AssistantScheduleIdentity.from_claims({"user_id": 42})
    with pytest.raises(AssistantScheduleIdentityError, match="有效 user_id"):
        AssistantScheduleIdentity.from_claims(
            {"username": "alice", "user_id": "not-an-int"}
        )
    with pytest.raises(AssistantScheduleIdentityError, match="username"):
        AssistantScheduleIdentity.from_claims({"username": ".", "user_id": 42})
    with pytest.raises(AssistantScheduleIdentityError, match="有效 user_id"):
        AssistantScheduleIdentity.from_claims({"username": "alice", "user_id": True})


def test_application_facade_owns_crud_identity_and_backend_arguments() -> None:
    calls: list[tuple[Any, ...]] = []
    application = _application(calls=calls)
    claims = {"username": " alice ", "user_id": "7"}

    assert application.list(claims) == [{"id": "sched-list"}]
    assert application.create(claims, _payload())["id"] == "sched-created"
    assert application.update(claims, "sched-7", _payload())["id"] == "sched-7"
    application.delete(claims, "sched-7")

    assert calls[0] == ("list", "alice", 7)
    assert calls[1][1:3] == ("alice", 7)
    assert calls[1][-1] is None
    assert calls[2][1:3] == ("alice", 7)
    assert calls[2][-1] == "sched-7"
    assert calls[3] == ("delete", "alice", "sched-7")


def test_application_facade_maps_delete_and_run_failures() -> None:
    claims = {"username": "alice", "user_id": 7}

    with pytest.raises(AssistantScheduleNotFound, match="定时任务不存在"):
        _application(deleted=False).delete(claims, "missing")

    async def assert_run_error(
        backend_error: Exception,
        expected: type[Exception],
        message: str,
    ) -> None:
        with pytest.raises(expected, match=message):
            await _application(run_error=backend_error).run(
                claims,
                "sched-7",
                object(),
            )

    import asyncio

    asyncio.run(assert_run_error(KeyError("missing"), AssistantScheduleNotFound, "不存在"))
    asyncio.run(assert_run_error(RuntimeError("正在运行"), AssistantScheduleConflict, "正在运行"))
    asyncio.run(
        assert_run_error(
            ValueError("provider failed"),
            AssistantScheduleExecutionFailed,
            "定时任务执行失败",
        )
    )


def test_schedule_routes_require_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
    app, _ = _route_app(monkeypatch, _application())

    with TestClient(app) as client:
        response = client.get("/api/assistant/schedules")

    assert response.status_code == 401
    assert response.json() == {"detail": "未登录或 token 无效"}


def test_schedule_routes_preserve_paths_and_success_envelopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, ...]] = []
    application = _application(calls=calls)
    app, database = _route_app(
        monkeypatch,
        application,
        claims={"username": "alice", "user_id": 7},
    )
    body = {"title": "Brief", "topic": "Risk"}

    with TestClient(app) as client:
        listed = client.get("/api/assistant/schedules")
        created = client.post("/api/assistant/schedules", json=body)
        updated = client.put("/api/assistant/schedules/sched-7", json=body)
        deleted = client.delete("/api/assistant/schedules/sched-7")
        run = client.post("/api/assistant/schedules/sched-7/run")

    assert listed.status_code == 200
    assert listed.json() == {"ok": True, "data": [{"id": "sched-list"}]}
    assert created.status_code == 200
    assert created.json()["data"]["id"] == "sched-created"
    assert updated.status_code == 200
    assert updated.json()["data"]["id"] == "sched-7"
    assert deleted.status_code == 200
    assert deleted.json() == {"ok": True, "id": "sched-7"}
    assert run.status_code == 200
    assert run.json() == {
        "ok": True,
        "data": {"schedule": {"id": "sched-7"}, "manual": True},
    }
    assert calls[-1] == ("run", "alice", 7, "sched-7", database, True)


@pytest.mark.parametrize(
    ("application", "method", "path", "expected_status", "expected_detail"),
    [
        (
            _application(),
            "get",
            "/api/assistant/schedules",
            400,
            "当前用户缺少 username",
        ),
        (_application(deleted=False), "delete", "/api/assistant/schedules/missing", 404, "定时任务不存在"),
        (_application(run_error=RuntimeError("该定时任务正在运行")), "post", "/api/assistant/schedules/sched-7/run", 409, "该定时任务正在运行"),
        (
            _application(run_error=ValueError("provider failed")),
            "post",
            "/api/assistant/schedules/sched-7/run",
            500,
            "定时任务执行失败",
        ),
    ],
)
def test_schedule_routes_map_application_errors(
    monkeypatch: pytest.MonkeyPatch,
    application: AssistantScheduleApplication,
    method: str,
    path: str,
    expected_status: int,
    expected_detail: str,
) -> None:
    claims = {"user_id": 7} if expected_status == 400 else {"username": "alice", "user_id": 7}
    app, _ = _route_app(monkeypatch, application, claims=claims)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.request(method, path)

    assert response.status_code == expected_status
    assert response.json() == {"detail": expected_detail}


def test_schedule_settings_use_central_typed_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("GLOBEMIND_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    monkeypatch.setenv("ASSISTANT_SCHEDULE_DEFAULT_TZ", "UTC")
    monkeypatch.setenv("ASSISTANT_SCHEDULE_TICK_SEC", "0")
    monkeypatch.setenv("ASSISTANT_SCHEDULE_CONCURRENCY", "3")
    monkeypatch.setenv("ASSISTANT_SCHEDULE_RUN_TIMEOUT_SEC", "90")
    monkeypatch.setenv("ASSISTANT_SCHEDULE_SHUTDOWN_TIMEOUT_SEC", "bad-value")
    monkeypatch.setenv("ASSISTANT_SCHEDULE_STANDBY_TICK_SEC", "4")
    monkeypatch.delenv("ASSISTANT_SCHEDULE_RUNNER_LOCK", raising=False)
    monkeypatch.delenv("ASSISTANT_SCHEDULE_RUNNER_STATUS", raising=False)
    monkeypatch.setenv("ASSISTANT_SCHEDULE_DISABLE", " true ")

    settings = load_assistant_schedule_settings()

    assert settings.workspace_root == tmp_path / "workspace"
    assert settings.default_timezone == "UTC"
    assert settings.runner_interval_seconds == 1
    assert settings.runner_concurrency == 3
    assert settings.run_timeout_seconds == 90
    assert settings.runner_shutdown_timeout_seconds == 30
    assert settings.runner_standby_interval_seconds == 4
    assert settings.runner_lock_path == tmp_path / "workspace/.assistant_schedule_runner.lock"
    assert settings.runner_status_path == tmp_path / "workspace/.assistant_schedule_runner.status.json"
    assert assistant_schedule_runner_disabled() is True

    monkeypatch.setenv("ASSISTANT_SCHEDULE_DISABLE", "false")
    assert assistant_schedule_runner_disabled() is False
