from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.core.db import Base, get_db
from api.features.identity import (
    PersonalDataExportAdapterBinding,
    PrivacyDeletionRequestStore,
    PrivacyRightsConflict,
    PrivacyRightsNotFound,
    PrivacyRightsUnavailable,
    build_account_deletion_impact_plan,
    build_personal_data_export,
)
from api.features.identity import privacy as privacy_module
from api.orm import models
from api.routes import auth
from api.services.auth import _hash_password, get_current_user_required


@pytest.fixture()
def identity_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    user = models.User(
        id=7,
        username="alice",
        password_hash=_hash_password("secure-pass-123"),
        full_name=None,
        email="alice@example.test",
        phone=None,
        created_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
        is_active=True,
        role="user",
        api_keys=None,
    )
    session.add(user)
    session.flush()
    session.add(
        models.UserSearchHistory(
            user_id=7,
            keyword="private research query",
            created_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
        )
    )
    session.add(
        models.UserFavorite(
            user_id=7,
            news_id=42,
            topic="pilot",
            item_kind="favorite",
            created_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
        )
    )
    chat = models.AssistantChatSession(
        user_id=7,
        title="Research session",
        pinned=False,
        context_summary="bounded context",
        created_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
    )
    session.add(chat)
    session.flush()
    session.add(
        models.AssistantChatMessage(
            session_id=chat.id,
            user_id=7,
            role="user",
            content="my own message",
            extra_json=None,
            created_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
        )
    )
    session.add(
        models.AssistantUserMemory(
            user_id=7,
            memory_summary="my saved preference",
            created_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
            updated_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
        )
    )
    session.commit()
    try:
        yield session, user
    finally:
        session.close()
        engine.dispose()


def test_personal_export_contains_known_subject_data_but_no_credentials(identity_db) -> None:
    db, user = identity_db
    account = auth._serialize_user_profile(user)
    account["api_key_status"] = {"openai": True}

    payload = build_personal_data_export(
        db,
        subject_id=7,
        subject_username="alice",
        account=account,
        generated_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["schema_version"] == "personal-data-export-v1"
    assert payload["complete"] is False
    assert payload["data"]["search_history"][0]["keyword"] == "private research query"
    assert payload["data"]["favorites"][0]["news_id"] == 42
    assert payload["data"]["assistant_messages"][0]["content"] == "my own message"
    assert "extra_json" not in payload["data"]["assistant_messages"][0]
    assert payload["data"]["assistant_memory"][0]["memory_summary"] == "my saved preference"
    assert "password_hash" not in payload["data"]["account"]
    assert "api_keys" not in payload["data"]["account"]
    assert "secure-pass-123" not in serialized
    assert {item["scope"] for item in payload["unavailable_scopes"]} == {
        "assistant_workspace_files",
        "assistant_schedules_and_generated_reports",
        "research_workflow_projects",
        "assistant_messages.extra_json",
    }


def test_personal_export_rejects_secret_material_in_public_account_config(
    identity_db,
) -> None:
    db, user = identity_db
    account = auth._serialize_user_profile(user)
    account["api_config_public"] = {
        "image": {"api_key": "provider-secret-must-not-be-exported"}
    }

    with pytest.raises(PrivacyRightsUnavailable, match="account metadata"):
        build_personal_data_export(
            db,
            subject_id=7,
            subject_username="alice",
            account=account,
        )


def test_personal_export_rejects_credentialed_legacy_provider_url(identity_db) -> None:
    db, user = identity_db
    account = auth._serialize_user_profile(user)
    account["base_url"] = "https://alice:provider-secret@example.test/v1"

    with pytest.raises(PrivacyRightsUnavailable, match="unsafe provider URL"):
        build_personal_data_export(
            db,
            subject_id=7,
            subject_username="alice",
            account=account,
        )


def test_personal_export_bounds_relational_text_and_excludes_message_extra_json(
    identity_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, user = identity_db
    canary = "provider-token-must-never-be-exported"
    message = db.query(models.AssistantChatMessage).filter_by(user_id=7).one()
    message.content = "x" * 100
    message.extra_json = json.dumps({"token": canary})
    db.commit()
    monkeypatch.setattr(privacy_module, "MAX_EXPORT_RELATIONAL_FIELD_BYTES", 16)

    payload = build_personal_data_export(
        db,
        subject_id=7,
        subject_username="alice",
        account=auth._serialize_user_profile(user),
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["data"]["assistant_messages"][0]["content"] == "x" * 16
    assert payload["data"]["assistant_messages"][0]["extra_json_status"].startswith(
        "not_exported"
    )
    assert payload["relational_field_truncation"]["assistant_messages.content"] == 1
    assert "assistant_messages" in payload["truncated_sections"]
    assert canary not in serialized


def test_personal_export_rejects_mismatched_or_sensitive_adapter_fragments(
    identity_db,
) -> None:
    db, user = identity_db

    def fragment(scope: str, *, subject_ref: str, data: dict) -> dict:
        return {
            "schema_version": "test-fragment-v1",
            "scope": scope,
            "status": "available",
            "subject_ref": subject_ref,
            "data": data,
            "truncated": False,
            "truncation_reasons": [],
            "limits": {"items": 1},
            "unavailable_subscopes": [],
        }

    payload = build_personal_data_export(
        db,
        subject_id=7,
        subject_username="alice",
        account=auth._serialize_user_profile(user),
        adapters=(
            PersonalDataExportAdapterBinding(
                scope="assistant_workspace_files",
                reader=lambda: fragment(
                    "assistant_workspace_files",
                    subject_ref="user:8",
                    data={"workspaces": []},
                ),
            ),
            PersonalDataExportAdapterBinding(
                scope="research_workflow_projects",
                reader=lambda: fragment(
                    "research_workflow_projects",
                    subject_ref="user:7",
                    data={"access_token": "adapter-secret-canary"},
                ),
            ),
        ),
    )

    assert "assistant_workspace_files" not in payload["data"]
    assert "research_workflow_projects" not in payload["data"]
    unavailable = {item["scope"]: item["reason"] for item in payload["unavailable_scopes"]}
    assert unavailable["assistant_workspace_files"] == (
        "SUBSYSTEM_EXPORT_ADAPTER_UNAVAILABLE"
    )
    assert unavailable["research_workflow_projects"] == (
        "SUBSYSTEM_EXPORT_ADAPTER_UNAVAILABLE"
    )
    assert "adapter-secret-canary" not in json.dumps(payload)


def test_personal_export_marks_relational_total_byte_truncation(
    identity_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, user = identity_db
    monkeypatch.setattr(privacy_module, "MAX_EXPORT_RELATIONAL_TOTAL_BYTES", 1)

    payload = build_personal_data_export(
        db,
        subject_id=7,
        subject_username="alice",
        account=auth._serialize_user_profile(user),
    )

    assert payload["data"]["search_history"] == []
    assert {
        "assistant_memory",
        "assistant_messages",
        "assistant_sessions",
        "favorites",
        "search_history",
    }.issubset(payload["truncated_sections"])
    assert payload["export_limits"]["bytes_all_relational_sections"] == 1


def test_personal_export_fails_closed_when_final_response_exceeds_total_bound(
    identity_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, user = identity_db
    monkeypatch.setattr(privacy_module, "MAX_PERSONAL_EXPORT_TOTAL_BYTES", 256)

    with pytest.raises(PrivacyRightsUnavailable, match="total byte bound"):
        build_personal_data_export(
            db,
            subject_id=7,
            subject_username="alice",
            account=auth._serialize_user_profile(user),
        )


def test_deletion_impact_plan_is_count_only_blocked_and_non_executable(identity_db) -> None:
    db, user = identity_db
    body_canary = "private-body-must-not-leak"
    absolute_path_canary = "/root/private/alice/report.md"
    other_identity_canary = "bob-private-identity"

    def fragment(scope: str, data: dict, unavailable: list[dict] | None = None) -> dict:
        return {
            "schema_version": f"{scope}-v1",
            "scope": scope,
            "status": "partial",
            "subject_ref": "user:7",
            "data": data,
            "truncated": False,
            "truncation_reasons": [],
            "limits": {"items": 5000},
            "unavailable_subscopes": unavailable or [],
        }

    personal_export = build_personal_data_export(
        db,
        subject_id=7,
        subject_username="alice",
        account=auth._serialize_user_profile(user),
        adapters=(
            PersonalDataExportAdapterBinding(
                scope="assistant_workspace_files",
                reader=lambda: fragment(
                    "assistant_workspace_files",
                    {
                        "workspaces": [{"name": body_canary}],
                        "file_metadata": [
                            {
                                "relative_path": absolute_path_canary,
                                "content_sha256": "a" * 64,
                            }
                        ],
                    },
                    [
                        {
                            "scope": "workspace_file_contents",
                            "reason": "WORKSPACE_FILE_CONTENT_NOT_INLINED",
                        }
                    ],
                ),
            ),
            PersonalDataExportAdapterBinding(
                scope="assistant_schedules_and_generated_reports",
                reader=lambda: fragment(
                    "assistant_schedules_and_generated_reports",
                    {
                        "schedules": [{"title": body_canary}],
                        "generated_report_metadata": [
                            {"relative_path": absolute_path_canary}
                        ],
                    },
                ),
            ),
            PersonalDataExportAdapterBinding(
                scope="research_workflow_projects",
                reader=lambda: fragment(
                    "research_workflow_projects",
                    {
                        "projects": [
                            {
                                "project": {"title": body_canary},
                                "subject_membership": {"role": "owner"},
                                "subject_authored": {
                                    "research_questions": [{"body": body_canary}],
                                    "author_events": [
                                        {"other_subject": other_identity_canary}
                                    ],
                                },
                            }
                        ]
                    },
                ),
            ),
        ),
    )

    plan = build_account_deletion_impact_plan(
        subject_id=7,
        subject_username="alice",
        account=auth._serialize_user_profile(user),
        personal_export=personal_export,
        generated_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
    )
    serialized = json.dumps(plan, ensure_ascii=False)
    by_scope = {item["scope"]: item for item in plan["impact_items"]}

    assert plan["schema_version"] == "account-deletion-impact-plan-v1"
    assert plan["operation_mode"] == "read_only_preflight"
    assert plan["deletion_performed"] is False
    assert plan["execution_state"] == "blocked"
    assert plan["request_registration_state"] == "not_checked"
    assert by_scope["assistant.workspaces"]["record_count"] == 1
    assert by_scope["research.project_memberships"]["disposition"] == "delete"
    assert by_scope["research.subject_authored_records"] == {
        "scope": "research.subject_authored_records",
        "disposition": "anonymize",
        "record_count": 2,
        "count_status": "exact",
        "ownership_basis": "canonical_subject_author_marker",
        "reason_code": "SHARED_RESEARCH_AUTHORSHIP_REQUIRES_ANONYMIZATION",
    }
    assert by_scope["research.shared_project_containers"]["disposition"] == "retain"
    assert by_scope["identity.account"]["disposition"] == "review_required"
    assert by_scope["assistant_workspace_files.workspace_file_contents"][
        "disposition"
    ] == "unavailable"
    assert {item["category"] for item in plan["external_blockers"]} >= {
        "retention_legal_basis",
        "checkpoint_and_recovery",
        "manual_authority",
    }
    for canary in (
        body_canary,
        absolute_path_canary,
        other_identity_canary,
        "private research query",
        "my own message",
        "alice@example.test",
    ):
        assert canary not in serialized


def test_deletion_impact_plan_marks_truncated_counts_unavailable_and_rejects_mismatch(
    identity_db,
) -> None:
    db, user = identity_db
    account = auth._serialize_user_profile(user)
    personal_export = build_personal_data_export(
        db,
        subject_id=7,
        subject_username="alice",
        account=account,
    )
    personal_export["truncated_sections"].append("assistant_messages")

    plan = build_account_deletion_impact_plan(
        subject_id=7,
        subject_username="alice",
        account=account,
        personal_export=personal_export,
    )
    messages = next(
        item for item in plan["impact_items"] if item["scope"] == "assistant.chat_messages"
    )
    assert messages["disposition"] == "unavailable"
    assert messages["record_count"] is None
    assert messages["count_status"] == "unavailable"

    with pytest.raises(PrivacyRightsUnavailable, match="subject"):
        build_account_deletion_impact_plan(
            subject_id=7,
            subject_username="renamed-subject",
            account=account,
            personal_export=personal_export,
        )


def test_deletion_request_is_append_only_reversible_and_never_claims_execution(
    tmp_path: Path,
) -> None:
    root = tmp_path / "privacy"
    store = PrivacyDeletionRequestStore(root)

    assert store.list(7)["items"] == []
    assert not root.exists()

    created = store.create(7, now=datetime(2026, 8, 9, tzinfo=timezone.utc))
    assert created["status"] == "pending_manual_execution"
    assert created["execution_status"] == "not_executed"
    assert created["execution_blockers"]
    with pytest.raises(PrivacyRightsConflict, match="already pending"):
        store.create(7)

    cancelled = store.cancel(
        7,
        created["request_id"],
        now=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    assert cancelled["status"] == "cancelled"
    assert cancelled["execution_status"] == "not_executed"
    with pytest.raises(PrivacyRightsConflict, match="already cancelled"):
        store.cancel(7, created["request_id"])
    with pytest.raises(PrivacyRightsNotFound):
        store.cancel(8, created["request_id"])

    persisted = "\n".join(path.read_text() for path in root.rglob("*.json"))
    assert "password" not in persisted
    assert "alice" not in persisted


def test_deletion_store_rejects_symlink_and_release_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(PrivacyRightsUnavailable, match="symbolic link"):
        PrivacyDeletionRequestStore(link)

    release_root = tmp_path / "releases"
    monkeypatch.setattr(privacy_module, "_FORBIDDEN_RELEASE_ROOT", release_root)
    with pytest.raises(PrivacyRightsUnavailable, match="inside releases"):
        PrivacyDeletionRequestStore(release_root / "current" / "privacy")
    assert not release_root.exists()


def test_deletion_store_rejects_hardlinked_lock_file(tmp_path: Path) -> None:
    root = tmp_path / "privacy"
    root.mkdir()
    external_lock = tmp_path / "external-lock"
    external_lock.write_text("", encoding="utf-8")
    (root / ".privacy.lock").hardlink_to(external_lock)
    store = PrivacyDeletionRequestStore(root)

    with pytest.raises(PrivacyRightsUnavailable, match="lock"):
        store.create(7, now=datetime(2026, 8, 9, tzinfo=timezone.utc))

    assert not (root / "subjects").exists()


def test_deletion_cancellation_rejects_clock_regression_without_appending(
    tmp_path: Path,
) -> None:
    store = PrivacyDeletionRequestStore(tmp_path / "privacy")
    created = store.create(7, now=datetime(2026, 8, 9, tzinfo=timezone.utc))

    with pytest.raises(PrivacyRightsConflict, match="precede"):
        store.cancel(
            7,
            created["request_id"],
            now=datetime(2026, 8, 8, tzinfo=timezone.utc),
        )

    assert store.list(7)["items"][0]["status"] == "pending_manual_execution"
    assert len(list((tmp_path / "privacy").rglob("*.json"))) == 1


@pytest.mark.parametrize("mutation", ["unknown_key", "invalid_timestamp"])
def test_deletion_store_rejects_noncanonical_event_contract(
    tmp_path: Path,
    mutation: str,
) -> None:
    store = PrivacyDeletionRequestStore(tmp_path / "privacy")
    store.create(7, now=datetime(2026, 8, 9, tzinfo=timezone.utc))
    event_path = next((tmp_path / "privacy").rglob("*.json"))
    event = json.loads(event_path.read_text(encoding="utf-8"))
    if mutation == "unknown_key":
        event["unexpected"] = "must-not-be-accepted"
    else:
        event["occurred_at"] = "not-a-timestamp"
    event_path.write_text(json.dumps(event), encoding="utf-8")

    with pytest.raises(PrivacyRightsUnavailable, match="event contract"):
        store.list(7)


def _privacy_app(db, *, authenticated: bool) -> FastAPI:
    app = FastAPI()
    app.include_router(auth.router)
    app.dependency_overrides[get_db] = lambda: db
    if authenticated:
        app.dependency_overrides[get_current_user_required] = lambda: {
            "user_id": 7,
            "username": "alice",
            "role": "user",
        }
    return app


@pytest.mark.parametrize(
    ("claims", "expected_status"),
    [
        ({"user_id": True, "username": "alice", "role": "user"}, 400),
        ({"user_id": "7", "username": "alice", "role": "user"}, 400),
        ({"user_id": 7, "role": "user"}, 403),
    ],
)
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/user/privacy/export", None),
        ("GET", "/api/user/privacy/deletion-impact-plan", None),
        ("GET", "/api/user/privacy/deletion-requests", None),
        (
            "POST",
            "/api/user/privacy/deletion-requests",
            {
                "password": "secure-pass-123",
                "acknowledgement": "REQUEST ACCOUNT DELETION",
            },
        ),
        (
            "POST",
            "/api/user/privacy/deletion-requests/privacy-00000000000000000000000000000000/cancel",
            None,
        ),
    ],
)
def test_all_privacy_routes_reject_noncanonical_claim_aliases_before_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    identity_db,
    claims: dict,
    expected_status: int,
    method: str,
    path: str,
    body: dict | None,
) -> None:
    db, _user = identity_db
    privacy_root = tmp_path / "privacy"
    workspace_root = tmp_path / "workspace"
    monkeypatch.setenv("PRIVACY_RIGHTS_ROOT", str(privacy_root))
    monkeypatch.setenv("GLOBEMIND_WORKSPACE_ROOT", str(workspace_root))
    app = _privacy_app(db, authenticated=True)
    app.dependency_overrides[get_current_user_required] = lambda: claims

    response = TestClient(app).request(method, path, json=body)

    assert response.status_code == expected_status
    assert not privacy_root.exists()
    assert not workspace_root.exists()


def test_privacy_routes_require_auth_password_and_explicit_acknowledgement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    identity_db,
) -> None:
    db, _user = identity_db
    monkeypatch.setenv("PRIVACY_RIGHTS_ROOT", str(tmp_path / "privacy"))
    workspace_root = tmp_path / "workspace"
    monkeypatch.setenv("GLOBEMIND_WORKSPACE_ROOT", str(workspace_root))

    anonymous = TestClient(_privacy_app(db, authenticated=False))
    assert anonymous.get("/api/user/privacy/export").status_code == 401
    assert anonymous.get("/api/user/privacy/deletion-impact-plan").status_code == 401

    mismatched_app = _privacy_app(db, authenticated=True)
    mismatched_app.dependency_overrides[get_current_user_required] = lambda: {
        "user_id": 7,
        "username": "renamed-or-forged-subject",
        "role": "user",
    }
    assert TestClient(mismatched_app).get("/api/user/privacy/export").status_code == 403
    assert (
        TestClient(mismatched_app)
        .get("/api/user/privacy/deletion-impact-plan")
        .status_code
        == 403
    )
    assert not workspace_root.exists()

    client = TestClient(_privacy_app(db, authenticated=True))
    exported = client.get("/api/user/privacy/export")
    assert exported.status_code == 200
    assert exported.json()["complete"] is False
    assert set(exported.json()["adapter_status"]) == {
        "assistant_schedules_and_generated_reports",
        "assistant_workspace_files",
        "research_workflow_projects",
    }
    assert not workspace_root.exists()

    privacy_root = tmp_path / "privacy"
    planned = client.get("/api/user/privacy/deletion-impact-plan")
    assert planned.status_code == 200
    plan = planned.json()
    assert plan["deletion_performed"] is False
    assert plan["execution_state"] == "blocked"
    assert plan["canonical_identity_verified"] is True
    assert not workspace_root.exists()
    assert not privacy_root.exists()
    serialized_plan = json.dumps(plan, ensure_ascii=False)
    assert "private research query" not in serialized_plan
    assert "my own message" not in serialized_plan
    assert "/root/" not in serialized_plan

    missing_ack = client.post(
        "/api/user/privacy/deletion-requests",
        json={"password": "secure-pass-123"},
    )
    assert missing_ack.status_code == 422
    wrong_password = client.post(
        "/api/user/privacy/deletion-requests",
        json={
            "password": "wrong-password",
            "acknowledgement": "REQUEST ACCOUNT DELETION",
        },
    )
    assert wrong_password.status_code == 403

    created = client.post(
        "/api/user/privacy/deletion-requests",
        json={
            "password": "secure-pass-123",
            "acknowledgement": "REQUEST ACCOUNT DELETION",
        },
    )
    assert created.status_code == 201
    request_id = created.json()["request_id"]
    assert created.json()["status"] == "pending_manual_execution"
    assert "secure-pass-123" not in "\n".join(
        path.read_text() for path in (tmp_path / "privacy").rglob("*.json")
    )

    cancelled = client.post(
        f"/api/user/privacy/deletion-requests/{request_id}/cancel"
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
