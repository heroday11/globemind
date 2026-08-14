from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from api.features.assistant import privacy_export as assistant_privacy_module
from api.features.assistant import (
    AssistantPrivacyExportReader,
    AssistantPrivacyExportUnavailable,
)
from api.features.research_workflow import (
    MemberChangeRequest,
    ProjectCreateRequest,
    QuestionCreateRequest,
    ResearchSubjectExportUnavailable,
    ResearchWorkflowService,
    SavedSearchCreateRequest,
    WorkspaceResearchRepository,
    build_research_subject_export,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _workspace(root: Path, username: str, name: str) -> Path:
    workspace = root / username / name
    _write_json(
        workspace / ".workspace.json",
        {
            "desc": f"{name} workspace",
            "pinned": False,
            "created": "2026-08-09 08:00:00",
            "updated": "2026-08-09 08:00:00",
        },
    )
    return workspace


def _schedule(
    *,
    subject_id: int,
    username: str,
    report_name: str,
    canary: str,
) -> dict[str, object]:
    file_info = {
        "workspace": "report",
        "file_name": report_name,
        "file_path": f"report/{report_name}",
        "size": 100,
    }
    return {
        "id": "sched-safe-1",
        "user_id": subject_id,
        "owner": username,
        "title": "Daily brief",
        "topic": "Subject-owned topic",
        "prompt": f"private prompt {canary}",
        "cadence": "daily",
        "timezone": "Asia/Shanghai",
        "time_of_day": "08:30",
        "day_of_week": 0,
        "interval_hours": 24,
        "enabled": True,
        "report_type": "brief",
        "time_range": "24h",
        "perspective": "综合研判",
        "include_sources": True,
        "include_charts": False,
        "created_at": "2026-08-09T08:00:00+00:00",
        "updated_at": "2026-08-09T09:00:00+00:00",
        "last_run_at": "2026-08-09T09:00:00+00:00",
        "next_run_at": "2026-08-10T09:00:00+00:00",
        "last_status": "done",
        "last_error": f"provider error {canary}",
        "last_file": file_info,
        "run_count": 1,
        "recent_runs": [
            {
                "id": "run-safe-1",
                "status": "done",
                "created_at": "2026-08-09T09:00:00+00:00",
                "file": file_info,
                "error": f"hidden error {canary}",
                "duration_ms": 1234,
            }
        ],
        "favorite_context": {"authorization": canary},
        "knowledge_context": {"api_key": canary},
        "pinned_workspace": "notes",
    }


def test_assistant_privacy_reader_exports_only_metadata_hashes_and_download_locators(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    notes = _workspace(root, "alice", "notes")
    marker = json.loads((notes / ".workspace.json").read_text(encoding="utf-8"))
    marker["secret_storage_location_canary"] = "marker-secret-value-canary"
    _write_json(notes / ".workspace.json", marker)
    note_body = "workspace-content-secret-canary"
    note = notes / "private.md"
    note.write_text(note_body, encoding="utf-8")
    (notes / ".env").write_text("API_KEY=dotfile-secret-canary", encoding="utf-8")

    reports = _workspace(root, "alice", "report")
    report_name = "2026-08-09T09-00-00-report.md"
    report_body = "generated-report-body-secret-canary"
    report = reports / report_name
    report.write_text(report_body, encoding="utf-8")
    schedule_canary = "schedule-sensitive-secret-canary"
    schedule_path = root / "alice" / ".assistant_schedules.json"
    _write_json(
        schedule_path,
        {
            "version": 1,
            "updated_at": "2026-08-09T09:00:00+00:00",
            "items": [
                _schedule(
                    subject_id=7,
                    username="alice",
                    report_name=report_name,
                    canary=schedule_canary,
                )
            ],
        },
    )
    bob = _workspace(root, "bob", "private")
    (bob / "bob-only.txt").write_text("cross-user-canary", encoding="utf-8")

    before_schedule = schedule_path.read_bytes()
    before_inventory = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
    reader = AssistantPrivacyExportReader(root)
    workspace_export = reader.export_workspaces(subject_id=7, username="alice")
    automation_export = reader.export_schedules_and_reports(
        subject_id=7,
        username="alice",
    )
    after_inventory = sorted(str(path.relative_to(root)) for path in root.rglob("*"))

    serialized = json.dumps(
        {"workspace": workspace_export, "automation": automation_export},
        ensure_ascii=False,
    )
    assert note_body not in serialized
    assert report_body not in serialized
    assert schedule_canary not in serialized
    assert "secret_storage_location_canary" not in serialized
    assert "marker-secret-value-canary" not in serialized
    assert "dotfile-secret-canary" not in serialized
    assert "cross-user-canary" not in serialized
    assert "bob-only.txt" not in serialized
    assert before_schedule == schedule_path.read_bytes()
    assert before_inventory == after_inventory

    note_metadata = workspace_export["data"]["file_metadata"][0]
    assert note_metadata["relative_path"] == "private.md"
    assert note_metadata["content_sha256"] == hashlib.sha256(
        note_body.encode("utf-8")
    ).hexdigest()
    assert note_metadata["download_path"] == (
        "/api/workspaces/notes/files/private.md/download"
    )
    assert all(
        item["relative_path"] != ".env"
        for item in workspace_export["data"]["file_metadata"]
    )
    notes_metadata = next(
        item for item in workspace_export["data"]["workspaces"] if item["name"] == "notes"
    )
    assert notes_metadata["unexported_marker_field_count"] == 1
    assert notes_metadata["marker_extension_status"] == "excluded_unknown_fields"

    assert automation_export["data"]["schedules"][0]["prompt_status"].startswith(
        "not_exported"
    )
    report_metadata = automation_export["data"]["generated_report_metadata"][0]
    assert report_metadata["content_sha256"] == hashlib.sha256(
        report_body.encode("utf-8")
    ).hexdigest()
    assert report_metadata["download_path"].endswith(
        f"/report/files/{report_name}/download"
    )
    assert automation_export["status"] == "partial"
    assert workspace_export["status"] == "partial"


def test_assistant_privacy_reader_missing_root_is_zero_write_and_unsafe_data_fails_closed(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing-workspace"
    reader = AssistantPrivacyExportReader(missing)
    assert reader.export_workspaces(subject_id=7, username="alice")["data"] == {
        "workspaces": [],
        "file_metadata": [],
    }
    assert reader.export_schedules_and_reports(
        subject_id=7, username="alice"
    )["data"] == {"schedules": [], "generated_report_metadata": []}
    assert not missing.exists()
    with pytest.raises(AssistantPrivacyExportUnavailable):
        reader.export_workspaces(subject_id=7, username="..")

    target = tmp_path / "target"
    target.mkdir()
    unsafe_root = tmp_path / "unsafe-root"
    unsafe_root.mkdir()
    (unsafe_root / "alice").symlink_to(target, target_is_directory=True)
    with pytest.raises(AssistantPrivacyExportUnavailable):
        AssistantPrivacyExportReader(unsafe_root).export_workspaces(
            subject_id=7,
            username="alice",
        )

    hardlink_root = tmp_path / "hardlink-root"
    workspace = _workspace(hardlink_root, "alice", "notes")
    source = workspace / "file.txt"
    source.write_text("safe", encoding="utf-8")
    os.link(source, tmp_path / "file-second-link")
    with pytest.raises(AssistantPrivacyExportUnavailable):
        AssistantPrivacyExportReader(hardlink_root).export_workspaces(
            subject_id=7,
            username="alice",
        )

    broken_subject_root = tmp_path / "broken-subject-root"
    broken_subject_root.mkdir()
    (broken_subject_root / "alice").symlink_to(
        tmp_path / "missing-subject-target",
        target_is_directory=True,
    )
    with pytest.raises(AssistantPrivacyExportUnavailable):
        AssistantPrivacyExportReader(broken_subject_root).export_workspaces(
            subject_id=7,
            username="alice",
        )

    broken_marker_root = tmp_path / "broken-marker-root"
    workspace = broken_marker_root / "alice" / "notes"
    workspace.mkdir(parents=True)
    (workspace / ".workspace.json").symlink_to(tmp_path / "missing-marker")
    with pytest.raises(AssistantPrivacyExportUnavailable):
        AssistantPrivacyExportReader(broken_marker_root).export_workspaces(
            subject_id=7,
            username="alice",
        )


@pytest.mark.parametrize(
    "mutation",
    ["duplicate", "corrupt", "owner_mismatch", "report_escape"],
)
def test_assistant_schedule_json_and_report_references_fail_closed(
    tmp_path: Path,
    mutation: str,
) -> None:
    root = tmp_path / mutation
    _workspace(root, "alice", "report")
    path = root / "alice" / ".assistant_schedules.json"
    item = _schedule(
        subject_id=7,
        username="alice",
        report_name="safe.md",
        canary="canary",
    )
    if mutation == "owner_mismatch":
        item["owner"] = "bob"
    if mutation == "report_escape":
        item["last_file"] = {
            "workspace": "report",
            "file_name": "../bob.md",
            "file_path": "report/../bob.md",
        }
    _write_json(path, {"version": 1, "items": [item]})
    if mutation == "duplicate":
        path.write_text(
            '{"version":1,"version":1,"items":[]}',
            encoding="utf-8",
        )
    elif mutation == "corrupt":
        path.write_text("{broken", encoding="utf-8")

    with pytest.raises(AssistantPrivacyExportUnavailable):
        AssistantPrivacyExportReader(root).export_schedules_and_reports(
            subject_id=7,
            username="alice",
        )


def test_assistant_schedule_truncation_does_not_link_reports_from_omitted_schedules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "schedule-limit"
    reports = _workspace(root, "alice", "report")
    first_name = "first.md"
    second_name = "omitted.md"
    (reports / first_name).write_text("first", encoding="utf-8")
    (reports / second_name).write_text("omitted-report-canary", encoding="utf-8")
    first = _schedule(
        subject_id=7,
        username="alice",
        report_name=first_name,
        canary="first-canary",
    )
    second = _schedule(
        subject_id=7,
        username="alice",
        report_name=second_name,
        canary="second-canary",
    )
    second["id"] = "sched-safe-2"
    _write_json(
        root / "alice" / ".assistant_schedules.json",
        {"version": 1, "items": [first, second]},
    )
    monkeypatch.setattr(assistant_privacy_module, "MAX_SCHEDULES", 1)

    payload = AssistantPrivacyExportReader(root).export_schedules_and_reports(
        subject_id=7,
        username="alice",
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    assert [item["id"] for item in payload["data"]["schedules"]] == ["sched-safe-1"]
    assert [
        item["file_name"] for item in payload["data"]["generated_report_metadata"]
    ] == [first_name]
    assert "omitted-report-canary" not in serialized
    assert "schedules:item_count_limit" in payload["truncation_reasons"]


def _identity(username: str, user_id: int) -> dict[str, object]:
    return {"username": username, "user_id": user_id, "role": "user"}


def test_research_privacy_export_contains_only_subject_membership_and_authored_content(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    repository = WorkspaceResearchRepository(workspace)
    service = ResearchWorkflowService(repository)
    alice = _identity("alice", 7)
    bob = _identity("bob", 8)
    project = service.create_project(
        ProjectCreateRequest(
            title="Shared project",
            description="ACL-visible metadata",
            reason="alice creates the project",
        ),
        alice,
    )
    project = service.set_member(
        project["id"],
        "bob",
        MemberChangeRequest(
            expected_version=project["version"],
            role="reviewer",
            reason="alice adds bob",
        ),
        alice,
    )
    project = service.add_question(
        project["id"],
        QuestionCreateRequest(
            expected_version=project["version"],
            question="Alice private authored question?",
            reason="alice records own question",
        ),
        alice,
    )
    bob_project = service.create_project(
        ProjectCreateRequest(
            title="Bob shared project",
            description="Metadata visible to Alice",
            reason="bob creates another project",
        ),
        bob,
    )
    bob_project = service.set_member(
        bob_project["id"],
        "alice",
        MemberChangeRequest(
            expected_version=bob_project["version"],
            role="reviewer",
            reason="bob adds alice",
        ),
        bob,
    )
    bob_project = service.add_question(
        bob_project["id"],
        QuestionCreateRequest(
            expected_version=bob_project["version"],
            question="Bob private authored question?",
            reason="bob records own question",
        ),
        bob,
    )

    alice_export = build_research_subject_export(
        repository,
        subject_id=7,
        username="alice",
    )
    alice_text = json.dumps(alice_export, ensure_ascii=False)
    assert "Alice private authored question?" in alice_text
    assert "Bob private authored question?" not in alice_text
    assert '"bob"' not in alice_text
    assert {item["subject_membership"]["role"] for item in alice_export["data"]["projects"]} == {
        "owner",
        "reviewer",
    }
    assert alice_export["status"] == "partial"

    bob_export = build_research_subject_export(
        repository,
        subject_id=8,
        username="bob",
    )
    bob_text = json.dumps(bob_export, ensure_ascii=False)
    assert "Bob private authored question?" in bob_text
    assert "Alice private authored question?" not in bob_text
    assert '"alice"' not in bob_text
    assert {item["subject_membership"]["role"] for item in bob_export["data"]["projects"]} == {
        "owner",
        "reviewer",
    }

    outsider = build_research_subject_export(
        repository,
        subject_id=9,
        username="mallory",
    )
    assert outsider["data"]["projects"] == []
    assert project["id"] not in json.dumps(outsider)


def test_research_privacy_reader_is_zero_write_and_rejects_hardlink_and_duplicate_json(
    tmp_path: Path,
) -> None:
    empty_root = tmp_path / "empty-workspace"
    empty_repository = WorkspaceResearchRepository(empty_root)
    result = build_research_subject_export(
        empty_repository,
        subject_id=7,
        username="alice",
    )
    assert result["data"]["projects"] == []
    assert not empty_root.exists()

    workspace = tmp_path / "hardlinked-workspace"
    repository = WorkspaceResearchRepository(workspace)
    project = ResearchWorkflowService(repository).create_project(
        ProjectCreateRequest(title="Project", reason="create project"),
        _identity("alice", 7),
    )
    state = (
        workspace
        / ".research-workflow-v1+store"
        / project["id"]
        / "state.json"
    )
    second_link = tmp_path / "state-second-link.json"
    os.link(state, second_link)
    with pytest.raises(ResearchSubjectExportUnavailable):
        build_research_subject_export(repository, subject_id=7, username="alice")
    second_link.unlink()

    original = state.read_text(encoding="utf-8")
    state.write_text(
        original.replace('"title":', '"title":"duplicate","title":', 1),
        encoding="utf-8",
    )
    with pytest.raises(ResearchSubjectExportUnavailable):
        build_research_subject_export(repository, subject_id=7, username="alice")

    state.write_text(
        original.replace('"version": 1', '"version": NaN', 1),
        encoding="utf-8",
    )
    with pytest.raises(ResearchSubjectExportUnavailable):
        build_research_subject_export(repository, subject_id=7, username="alice")

    outside_state = tmp_path / "outside-state.json"
    outside_state.write_text(original, encoding="utf-8")
    state.unlink()
    state.symlink_to(outside_state)
    with pytest.raises(ResearchSubjectExportUnavailable):
        build_research_subject_export(repository, subject_id=7, username="alice")
    assert outside_state.read_text(encoding="utf-8") == original


def test_research_privacy_export_redacts_normalized_secret_field_names(
    tmp_path: Path,
) -> None:
    repository = WorkspaceResearchRepository(tmp_path / "workspace")
    service = ResearchWorkflowService(repository)
    alice = _identity("alice", 7)
    project = service.create_project(
        ProjectCreateRequest(title="Secret redaction", reason="create redaction test"),
        alice,
    )
    service.add_saved_search(
        project["id"],
        SavedSearchCreateRequest(
            expected_version=project["version"],
            name="Private connector search",
            query="bounded query",
            filters={
                "client_secret": "client-secret-canary",
                "nested": {
                    "ApiKey": "api-key-canary",
                    "clientSecret": "camel-secret-canary",
                    "privateKey": "private-key-canary",
                    "service-access-token": "access-token-canary",
                    "safe_label": "retained-value",
                },
            },
            reason="capture subject-authored search without exporting credentials",
        ),
        alice,
    )

    payload = build_research_subject_export(
        repository,
        subject_id=7,
        username="alice",
    )
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "client-secret-canary" not in serialized
    assert "api-key-canary" not in serialized
    assert "camel-secret-canary" not in serialized
    assert "private-key-canary" not in serialized
    assert "access-token-canary" not in serialized
    assert "retained-value" in serialized
    saved_search = payload["data"]["projects"][0]["subject_authored"][
        "saved_searches"
    ][0]
    assert saved_search["redacted_fields"] == [
        "filters.client_secret",
        "filters.nested.ApiKey",
        "filters.nested.clientSecret",
        "filters.nested.privateKey",
        "filters.nested.service-access-token",
    ]
