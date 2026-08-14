from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from api.routes import assistant_data
from api.services import assistant_user_defaults as defaults_service
from api.services.assistant_user_defaults import (
    BOOTSTRAP_MARKER,
    DEFAULT_KNOWLEDGE_BASE_DIRS,
    ensure_assistant_user_defaults,
)


def _meta(root: Path, username: str, workspace: str) -> dict:
    path = root / username / workspace / ".workspace.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_provisions_default_workspaces_and_knowledge_base(tmp_path: Path) -> None:
    result = ensure_assistant_user_defaults("new-user", workspace_root=tmp_path)

    user_root = tmp_path / "new-user"
    assert result["created"] is True
    assert set(result["workspaces"]) == {"项目", "舆情研判", "事件追踪"}
    assert _meta(tmp_path, "new-user", "项目")["pinned"] is True
    assert _meta(tmp_path, "new-user", "舆情研判")["pinned"] is False
    assert {path.name for path in (user_root / "项目").iterdir()} >= {
        ".workspace.json",
        "资料",
        "草稿",
        "成果",
    }
    assert {path.name for path in (user_root / "knowledge_base").iterdir()} == set(
        DEFAULT_KNOWLEDGE_BASE_DIRS
    )
    assert (user_root / "knowledge_base" / "ECO").is_dir()
    assert (user_root / BOOTSTRAP_MARKER).is_file()


def test_bootstrap_is_once_only_and_preserves_existing_content(tmp_path: Path) -> None:
    user_root = tmp_path / "existing"
    existing = user_root / "已有项目"
    existing.mkdir(parents=True)
    existing_meta = {
        "desc": "用户自己的项目",
        "pinned": True,
        "created": "2025-01-01 00:00:00",
        "updated": "2025-01-01 00:00:00",
    }
    (existing / ".workspace.json").write_text(
        json.dumps(existing_meta, ensure_ascii=False), encoding="utf-8"
    )

    ensure_assistant_user_defaults("existing", workspace_root=tmp_path)
    assert _meta(tmp_path, "existing", "已有项目") == existing_meta
    assert _meta(tmp_path, "existing", "项目")["pinned"] is False

    (user_root / "事件追踪").rename(user_root / "用户重命名")
    second = ensure_assistant_user_defaults("existing", workspace_root=tmp_path)
    assert second["created"] is False
    assert not (user_root / "事件追踪").exists()


def test_workspace_and_kb_routes_lazy_bootstrap_legacy_account(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(assistant_data, "WORKSPACE_ROOT", tmp_path)

    workspace_response = assistant_data.list_workspaces(user={"username": "legacy"})
    workspace_payload = json.loads(workspace_response.body)
    kb_response = assistant_data.list_kb2_categories(user={"username": "legacy"})
    kb_payload = json.loads(kb_response.body)

    assert workspace_payload["ok"] is True
    assert [item["name"] for item in workspace_payload["data"]][:1] == ["项目"]
    assert sum(bool(item["pinned"]) for item in workspace_payload["data"]) == 1
    assert kb_payload["ok"] is True
    assert [item["id"] for item in kb_payload["data"]] == [
        "geo",
        "mil",
        "econ",
        "tech",
        "social",
        "law",
    ]
    assert (tmp_path / "legacy" / "knowledge_base" / "ECO").is_dir()


def test_bootstrap_rejects_dot_username_without_polluting_workspace_root(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="username"):
        ensure_assistant_user_defaults(".", workspace_root=tmp_path)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("link_kind", ("symlink", "hardlink"))
def test_bootstrap_lock_rejects_links_without_touching_external_file(
    tmp_path: Path,
    link_kind: str,
) -> None:
    workspace_root = tmp_path / "workspace"
    user_root = workspace_root / "alice"
    user_root.mkdir(parents=True)
    victim = tmp_path / "victim.txt"
    victim.write_text("preserve-me", encoding="utf-8")
    victim.chmod(0o644)
    lock = user_root / ".assistant-defaults.lock"
    if link_kind == "symlink":
        lock.symlink_to(victim)
    else:
        lock.hardlink_to(victim)

    with pytest.raises(ValueError, match="lock"):
        ensure_assistant_user_defaults("alice", workspace_root=workspace_root)

    assert victim.read_text(encoding="utf-8") == "preserve-me"
    assert os.stat(victim).st_mode & 0o777 == 0o644
    assert not (user_root / BOOTSTRAP_MARKER).exists()


@pytest.mark.parametrize("reserved_name", ("项目", "knowledge_base"))
def test_bootstrap_rejects_linked_reserved_directories_before_external_writes(
    tmp_path: Path,
    reserved_name: str,
) -> None:
    workspace_root = tmp_path / "workspace"
    user_root = workspace_root / "alice"
    user_root.mkdir(parents=True)
    outside = tmp_path / f"outside-{reserved_name}"
    outside.mkdir()
    (user_root / reserved_name).symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="workspace"):
        ensure_assistant_user_defaults("alice", workspace_root=workspace_root)

    assert list(outside.iterdir()) == []
    assert not (user_root / BOOTSTRAP_MARKER).exists()


def test_bootstrap_rejects_linked_marker_and_workspace_metadata(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    user_root = workspace_root / "alice"
    existing_workspace = user_root / "existing"
    existing_workspace.mkdir(parents=True)
    outside_marker = tmp_path / "outside-marker.json"
    outside_marker.write_text('{"version":1}', encoding="utf-8")
    marker = user_root / BOOTSTRAP_MARKER
    marker.symlink_to(outside_marker)

    with pytest.raises(ValueError, match="marker"):
        ensure_assistant_user_defaults("alice", workspace_root=workspace_root)

    marker.unlink()
    outside_metadata = tmp_path / "outside-metadata.json"
    outside_metadata.write_text('{"pinned":true}', encoding="utf-8")
    (existing_workspace / ".workspace.json").symlink_to(outside_metadata)

    with pytest.raises(ValueError, match="metadata"):
        ensure_assistant_user_defaults("alice", workspace_root=workspace_root)


@pytest.mark.parametrize(
    "relative_path",
    (
        Path("项目") / "资料",
        Path("knowledge_base") / "GEO",
    ),
)
def test_bootstrap_rejects_linked_default_subdirectories(
    tmp_path: Path,
    relative_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    user_root = workspace_root / "alice"
    linked = user_root / relative_path
    linked.parent.mkdir(parents=True)
    outside = tmp_path / "outside-subdirectory"
    outside.mkdir()
    linked.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="workspace"):
        ensure_assistant_user_defaults("alice", workspace_root=workspace_root)

    assert not (user_root / BOOTSTRAP_MARKER).exists()


def test_bootstrap_json_files_are_private(tmp_path: Path) -> None:
    ensure_assistant_user_defaults("alice", workspace_root=tmp_path)

    user_root = tmp_path / "alice"
    assert (user_root / BOOTSTRAP_MARKER).stat().st_mode & 0o777 == 0o600
    for definition in defaults_service.DEFAULT_WORKSPACES:
        metadata = user_root / str(definition["name"]) / ".workspace.json"
        assert metadata.stat().st_mode & 0o777 == 0o600


def test_bootstrap_rejects_precreated_temporary_link_without_touching_victim(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace = workspace_root / "alice" / "项目"
    workspace.mkdir(parents=True)
    victim = tmp_path / "victim.json"
    victim.write_text("preserve-me", encoding="utf-8")
    temporary = workspace / "..workspace.json.predictable.tmp"
    temporary.symlink_to(victim)
    monkeypatch.setattr(
        defaults_service.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="predictable"),
    )

    with pytest.raises(ValueError, match="workspace"):
        ensure_assistant_user_defaults("alice", workspace_root=workspace_root)

    assert victim.read_text(encoding="utf-8") == "preserve-me"
    assert temporary.is_symlink()
    assert not (workspace_root / "alice" / BOOTSTRAP_MARKER).exists()


@pytest.mark.parametrize("ambiguous_version", (True, 1.0))
def test_bootstrap_marker_requires_an_exact_integer_version(
    tmp_path: Path,
    ambiguous_version: object,
) -> None:
    user_root = tmp_path / "alice"
    user_root.mkdir()
    (user_root / BOOTSTRAP_MARKER).write_text(
        json.dumps({"version": ambiguous_version}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="version"):
        ensure_assistant_user_defaults("alice", workspace_root=tmp_path)

    assert not (user_root / "项目").exists()


@pytest.mark.parametrize(
    "source",
    (
        '{"version":1,"version":1}',
        '{"version":1,"value":NaN}',
        '{"version":1,"value":1e400}',
        '{"version":1,"nested":' + "[" * 1100 + "0" + "]" * 1100 + "}",
        '{"version":1,"padding":"' + "x" * 65_536 + '"}',
    ),
)
def test_bootstrap_rejects_untrustworthy_marker_json(
    tmp_path: Path,
    source: str,
) -> None:
    user_root = tmp_path / "alice"
    user_root.mkdir()
    (user_root / BOOTSTRAP_MARKER).write_text(source, encoding="utf-8")

    with pytest.raises(ValueError):
        ensure_assistant_user_defaults("alice", workspace_root=tmp_path)

    assert not (user_root / "项目").exists()


def test_bootstrap_rejects_hardlinked_marker_and_workspace_metadata(
    tmp_path: Path,
) -> None:
    user_root = tmp_path / "alice"
    existing_workspace = user_root / "existing"
    existing_workspace.mkdir(parents=True)
    victim = tmp_path / "victim.json"
    victim.write_text('{"version":1,"pinned":true}', encoding="utf-8")
    marker = user_root / BOOTSTRAP_MARKER
    marker.hardlink_to(victim)

    with pytest.raises(ValueError, match="marker"):
        ensure_assistant_user_defaults("alice", workspace_root=tmp_path)

    marker.unlink()
    (existing_workspace / ".workspace.json").hardlink_to(victim)
    with pytest.raises(ValueError, match="metadata"):
        ensure_assistant_user_defaults("alice", workspace_root=tmp_path)

    assert victim.read_text(encoding="utf-8") == '{"version":1,"pinned":true}'
