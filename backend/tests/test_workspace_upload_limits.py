from __future__ import annotations

import asyncio
import io
import json
import multiprocessing
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from api.routes import assistant_data  # noqa: E402
from api.services.auth import get_current_user_required  # noqa: E402


def _upload(name: str, content: bytes) -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(content))


def _workspace(root: Path, username: str = "tester", name: str = "workspace") -> Path:
    target = root / username / name
    target.mkdir(parents=True, exist_ok=True)
    (target / ".workspace.json").write_text("{}", encoding="utf-8")
    return target


def _configure_limits(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setattr(assistant_data, "WORKSPACE_ROOT", root)
    monkeypatch.setattr(assistant_data, "MAX_UPLOAD_FILE_BYTES", 100)
    monkeypatch.setattr(assistant_data, "MAX_UPLOAD_REQUEST_BYTES", 100)
    monkeypatch.setattr(assistant_data, "MAX_WORKSPACE_BYTES", 100)
    monkeypatch.setattr(assistant_data, "MAX_USER_BYTES", 100)
    monkeypatch.setattr(assistant_data, "MIN_DISK_FREE_BYTES", 0)


def _concurrent_upload_worker(
    root: str,
    filename: str,
    start: multiprocessing.synchronize.Event,
    results: multiprocessing.queues.Queue,
) -> None:
    from api.routes import assistant_data as worker_data

    worker_data.WORKSPACE_ROOT = Path(root)
    worker_data.MAX_UPLOAD_FILE_BYTES = 100
    worker_data.MAX_UPLOAD_REQUEST_BYTES = 100
    worker_data.MAX_WORKSPACE_BYTES = 100
    worker_data.MAX_USER_BYTES = 12
    worker_data.MIN_DISK_FREE_BYTES = 0
    original_size = worker_data._user_size_bytes

    def delayed_size(path: Path) -> int:
        size = original_size(path)
        time.sleep(0.1)
        return size

    worker_data._user_size_bytes = delayed_size
    start.wait(timeout=5)
    try:
        response = asyncio.run(
            worker_data.upload_workspace_files(
                "workspace",
                [_upload(filename, b"123456")],
                user={"username": "tester"},
            )
        )
        results.put(response.status_code)
    except HTTPException as exc:
        results.put(exc.status_code)


def _concurrent_workspace_create_worker(
    root: str,
    name: str,
    start: Any,
    results: Any,
) -> None:
    from api.routes import assistant_data as worker_data

    worker_data.WORKSPACE_ROOT = Path(root)
    worker_data.MAX_WORKSPACES_PER_USER = 1
    original_count = worker_data._workspace_count

    def delayed_count(username: str) -> int:
        count = original_count(username)
        time.sleep(0.1)
        return count

    worker_data._workspace_count = delayed_count
    start.wait(timeout=5)
    response = worker_data.create_workspace(
        worker_data.WorkspaceCreate(name=name),
        user={"username": "tester"},
    )
    results.put(response.status_code)


def test_stage_upload_streams_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(assistant_data, "MAX_UPLOAD_FILE_BYTES", 8)

    path, size = asyncio.run(assistant_data._stage_upload(_upload("ok.txt", b"12345678"), tmp_path))

    assert size == 8
    assert path.read_bytes() == b"12345678"
    path.unlink()


def test_stage_upload_rejects_oversize_and_removes_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(assistant_data, "MAX_UPLOAD_FILE_BYTES", 4)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(assistant_data._stage_upload(_upload("large.bin", b"12345"), tmp_path))

    assert exc.value.status_code == 413
    assert list(tmp_path.iterdir()) == []


def test_upload_request_is_staged_before_files_become_visible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = tmp_path / "workspace-root"
    target = _workspace(root)
    _configure_limits(monkeypatch, root)
    monkeypatch.setattr(assistant_data, "MAX_UPLOAD_REQUEST_BYTES", 5)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            assistant_data.upload_workspace_files(
                "workspace",
                [_upload("first.txt", b"123"), _upload("second.txt", b"456")],
                user={"username": "tester"},
            )
        )

    assert exc.value.status_code == 413
    assert [path.name for path in target.iterdir()] == [".workspace.json"]
    assert not list((root / "tester" / ".workspace-staging").glob(".upload-*"))


def test_upload_replaces_file_after_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "workspace-root"
    target = _workspace(root)
    (target / "report.txt").write_bytes(b"old")
    _configure_limits(monkeypatch, root)

    response = asyncio.run(
        assistant_data.upload_workspace_files(
            "workspace",
            [_upload("report.txt", b"new-content")],
            user={"username": "tester"},
        )
    )

    payload = json.loads(response.body)
    assert payload["ok"] is True
    assert payload["data"][0]["size"] == len(b"new-content")
    assert (target / "report.txt").read_bytes() == b"new-content"
    assert not list(target.glob(".upload-*"))


def test_concurrent_process_uploads_cannot_bypass_user_quota(tmp_path: Path):
    root = tmp_path / "workspace-root"
    target = _workspace(root)
    ctx = multiprocessing.get_context("spawn")
    start = ctx.Event()
    results = ctx.Queue()
    processes = [
        ctx.Process(
            target=_concurrent_upload_worker,
            args=(str(root), f"file-{index}.txt", start, results),
        )
        for index in range(2)
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    statuses = sorted(results.get(timeout=2) for _ in processes)
    assert statuses == [200, 413]
    assert sum(path.stat().st_size for path in target.glob("file-*.txt")) == 6


def test_multi_file_publish_rolls_back_every_original_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = tmp_path / "workspace-root"
    target = _workspace(root)
    (target / "first.txt").write_bytes(b"old-first")
    (target / "second.txt").write_bytes(b"old-second")
    _configure_limits(monkeypatch, root)
    monkeypatch.setattr(assistant_data, "MAX_WORKSPACE_BYTES", 1000)
    monkeypatch.setattr(assistant_data, "MAX_USER_BYTES", 1000)
    original_replace = assistant_data.os.replace

    def fail_second_publish(source: str | Path, destination: str | Path) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        if source_path.parent.name == ".workspace-staging" and destination_path.name == "second.txt":
            raise OSError("simulated publish failure")
        original_replace(source, destination)

    monkeypatch.setattr(assistant_data.os, "replace", fail_second_publish)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            assistant_data.upload_workspace_files(
                "workspace",
                [
                    _upload("first.txt", b"new-first"),
                    _upload("second.txt", b"new-second"),
                ],
                user={"username": "tester"},
            )
        )

    assert exc.value.status_code == 500
    assert (target / "first.txt").read_bytes() == b"old-first"
    assert (target / "second.txt").read_bytes() == b"old-second"
    assert not list(target.glob(".upload-backup-*"))
    assert not list((root / "tester" / ".workspace-staging").glob(".upload-*"))


def test_upload_rejects_when_disk_is_below_low_watermark(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = tmp_path / "workspace-root"
    _workspace(root)
    _configure_limits(monkeypatch, root)
    monkeypatch.setattr(assistant_data, "MIN_DISK_FREE_BYTES", 10)
    monkeypatch.setattr(
        assistant_data.shutil,
        "disk_usage",
        lambda _path: type("DiskUsage", (), {"free": 9})(),
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            assistant_data.upload_workspace_files(
                "workspace",
                [_upload("blocked.txt", b"content")],
                user={"username": "tester"},
            )
        )

    assert exc.value.status_code == 507


def test_workspace_count_limit_is_enforced_under_user_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = tmp_path / "workspace-root"
    _configure_limits(monkeypatch, root)
    monkeypatch.setattr(assistant_data, "MAX_WORKSPACES_PER_USER", 1)

    first = assistant_data.create_workspace(
        assistant_data.WorkspaceCreate(name="first"),
        user={"username": "tester"},
    )
    second = assistant_data.create_workspace(
        assistant_data.WorkspaceCreate(name="second"),
        user={"username": "tester"},
    )

    assert first.status_code == 200
    assert second.status_code == 409


def test_concurrent_workspace_creation_cannot_bypass_count_limit(tmp_path: Path):
    root = tmp_path / "workspace-root"
    ctx = multiprocessing.get_context("spawn")
    start = ctx.Event()
    results = ctx.Queue()
    processes = [
        ctx.Process(
            target=_concurrent_workspace_create_worker,
            args=(str(root), name, start, results),
        )
        for name in ("first", "second")
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    assert sorted(results.get(timeout=2) for _ in processes) == [200, 409]
    workspaces = [
        path
        for path in (root / "tester").iterdir()
        if path.is_dir() and (path / ".workspace.json").is_file()
    ]
    assert len(workspaces) == 1


def test_workspace_listing_does_not_follow_linked_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace-root"
    user_root = root / "tester"
    user_root.mkdir(parents=True)
    outside = tmp_path / "outside-workspace"
    outside.mkdir()
    (outside / ".workspace.json").write_text(
        json.dumps({"desc": "outside-canary", "pinned": True}),
        encoding="utf-8",
    )
    (outside / "private.txt").write_text("private", encoding="utf-8")
    (user_root / "linked").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(assistant_data, "WORKSPACE_ROOT", root)

    result = assistant_data._read_workspaces("tester")

    assert result == []
    assert "outside-canary" not in json.dumps(result)


@pytest.mark.parametrize("link_kind", ("symlink", "hardlink"))
def test_workspace_user_lock_rejects_links_without_touching_victim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    link_kind: str,
) -> None:
    root = tmp_path / "workspace-root"
    user_root = root / "tester"
    user_root.mkdir(parents=True)
    victim = tmp_path / "victim.txt"
    victim.write_text("preserve-me", encoding="utf-8")
    victim.chmod(0o644)
    lock = user_root / ".workspace-user.lock"
    if link_kind == "symlink":
        lock.symlink_to(victim)
    else:
        lock.hardlink_to(victim)
    _configure_limits(monkeypatch, root)

    with pytest.raises(HTTPException) as exc:
        assistant_data.create_workspace(
            assistant_data.WorkspaceCreate(name="blocked"),
            user={"username": "tester"},
        )

    assert exc.value.status_code == 503
    assert victim.read_text(encoding="utf-8") == "preserve-me"
    assert victim.stat().st_mode & 0o777 == 0o644
    assert not (user_root / "blocked").exists()


def test_workspace_lock_directory_link_cannot_create_files_outside_user_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace-root"
    user_root = root / "tester"
    user_root.mkdir(parents=True)
    outside = tmp_path / "outside-locks"
    outside.mkdir()
    (user_root / ".workspace-locks").symlink_to(outside, target_is_directory=True)
    _configure_limits(monkeypatch, root)

    with pytest.raises(HTTPException) as exc:
        assistant_data.create_workspace(
            assistant_data.WorkspaceCreate(name="blocked"),
            user={"username": "tester"},
        )

    assert exc.value.status_code == 503
    assert list(outside.iterdir()) == []
    assert not (user_root / "blocked").exists()


def test_dot_username_cannot_write_into_shared_workspace_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace-root"
    root.mkdir()
    _configure_limits(monkeypatch, root)

    with pytest.raises(HTTPException) as exc:
        assistant_data.create_workspace(
            assistant_data.WorkspaceCreate(name="pollution"),
            user={"username": "."},
        )

    assert exc.value.status_code == 400
    assert list(root.iterdir()) == []


def test_linked_user_root_cannot_cross_tenant_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace-root"
    bob_root = root / "bob"
    bob_root.mkdir(parents=True)
    (root / "alice").symlink_to(bob_root, target_is_directory=True)
    _configure_limits(monkeypatch, root)

    with pytest.raises(HTTPException) as exc:
        assistant_data.create_workspace(
            assistant_data.WorkspaceCreate(name="cross-tenant"),
            user={"username": "alice"},
        )

    assert exc.value.status_code in {400, 503}
    assert list(bob_root.iterdir()) == []


def test_workspace_file_listing_skips_linked_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace-root"
    target = _workspace(root)
    normal = target / "normal.txt"
    normal.write_text("normal", encoding="utf-8")
    victim = tmp_path / "outside-secret.txt"
    victim.write_text("outside-canary", encoding="utf-8")
    (target / "symbolic.txt").symlink_to(victim)
    (target / "hard.txt").hardlink_to(victim)
    _configure_limits(monkeypatch, root)

    response = assistant_data.list_workspace_files(
        "workspace",
        subpath="",
        user={"username": "tester"},
    )
    payload = json.loads(response.body)

    assert payload["ok"] is True
    assert [item["name"] for item in payload["data"]] == ["normal.txt"]
    assert "outside-canary" not in response.body.decode("utf-8")


def test_workspace_preview_rejects_hardlinks_and_oversized_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace-root"
    target = _workspace(root)
    victim = tmp_path / "outside-secret.txt"
    victim.write_text("outside-canary", encoding="utf-8")
    (target / "hard.txt").hardlink_to(victim)
    (target / "large.txt").write_text("123456789", encoding="utf-8")
    _configure_limits(monkeypatch, root)
    monkeypatch.setattr(
        assistant_data,
        "MAX_TEXT_PREVIEW_BYTES",
        8,
        raising=False,
    )

    linked = assistant_data.read_workspace_file(
        "workspace",
        "hard.txt",
        user={"username": "tester"},
    )
    oversized = assistant_data.read_workspace_file(
        "workspace",
        "large.txt",
        user={"username": "tester"},
    )

    assert linked.status_code in {404, 409}
    assert oversized.status_code == 413
    assert "outside-canary" not in linked.body.decode("utf-8")
    assert "123456789" not in oversized.body.decode("utf-8")


def test_knowledge_base_listing_and_preview_reject_linked_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace-root"
    directory = root / "tester" / "knowledge_base" / "GEO"
    directory.mkdir(parents=True)
    (directory / "normal.txt").write_text("normal", encoding="utf-8")
    victim = tmp_path / "outside-kb-secret.txt"
    victim.write_text("kb-outside-canary", encoding="utf-8")
    (directory / "symbolic.txt").symlink_to(victim)
    (directory / "hard.txt").hardlink_to(victim)
    (directory / "large.txt").write_text("123456789", encoding="utf-8")
    _configure_limits(monkeypatch, root)
    monkeypatch.setattr(assistant_data, "MAX_TEXT_PREVIEW_BYTES", 8)

    listing = assistant_data.list_kb2_files(
        category="geo",
        user={"username": "tester"},
    )
    linked = assistant_data.read_kb2_file(
        "hard.txt",
        category="geo",
        user={"username": "tester"},
    )
    oversized = assistant_data.read_kb2_file(
        "large.txt",
        category="geo",
        user={"username": "tester"},
    )
    payload = json.loads(listing.body)

    assert [item["name"] for item in payload["data"]] == ["large.txt", "normal.txt"]
    assert linked.status_code in {404, 409}
    assert oversized.status_code == 413
    assert "kb-outside-canary" not in linked.body.decode("utf-8")


def test_download_and_zip_reject_hardlinked_workspace_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace-root"
    target = _workspace(root)
    victim = tmp_path / "outside-download-secret.txt"
    victim.write_text("download-outside-canary", encoding="utf-8")
    (target / "hard.txt").hardlink_to(victim)
    _configure_limits(monkeypatch, root)
    app = FastAPI()
    app.include_router(assistant_data.router)
    app.dependency_overrides[get_current_user_required] = lambda: {
        "username": "tester"
    }

    with TestClient(app) as client:
        download = client.get(
            "/workspaces/workspace/files/hard.txt/download",
        )
        archive = client.post(
            "/workspaces/workspace/download-zip",
            json={"filenames": ["hard.txt"]},
        )

    assert download.status_code == 409
    assert archive.status_code == 409
    assert b"download-outside-canary" not in download.content


def test_download_and_zip_preserve_single_link_regular_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace-root"
    target = _workspace(root)
    (target / "normal.txt").write_text("download-normal", encoding="utf-8")
    _configure_limits(monkeypatch, root)
    app = FastAPI()
    app.include_router(assistant_data.router)
    app.dependency_overrides[get_current_user_required] = lambda: {
        "username": "tester"
    }

    with TestClient(app) as client:
        download = client.get(
            "/workspaces/workspace/files/normal.txt/download",
        )
        archive = client.post(
            "/workspaces/workspace/download-zip",
            json={"filenames": ["normal.txt"]},
        )

    assert download.status_code == 200
    assert download.content == b"download-normal"
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
        assert bundle.namelist() == ["normal.txt"]
        assert bundle.read("normal.txt") == b"download-normal"


def test_zip_selection_is_bounded_before_filesystem_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace-root"
    _workspace(root)
    _configure_limits(monkeypatch, root)
    app = FastAPI()
    app.include_router(assistant_data.router)
    app.dependency_overrides[get_current_user_required] = lambda: {
        "username": "tester"
    }

    with TestClient(app, raise_server_exceptions=False) as client:
        too_many = client.post(
            "/workspaces/workspace/download-zip",
            json={"filenames": [f"missing-{index}.txt" for index in range(501)]},
        )
        too_long = client.post(
            "/workspaces/workspace/download-zip",
            json={"filenames": [f"{'x' * 1025}.txt"]},
        )
        duplicate = client.post(
            "/workspaces/workspace/download-zip",
            json={"filenames": ["same.txt", "same.txt"]},
        )

    assert too_many.status_code == 422
    assert too_long.status_code == 422
    assert duplicate.status_code == 422


def test_whole_workspace_zip_refuses_unbounded_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace-root"
    target = _workspace(root)
    for index in range(501):
        (target / f"item-{index}.txt").write_bytes(b"")
    _configure_limits(monkeypatch, root)
    app = FastAPI()
    app.include_router(assistant_data.router)
    app.dependency_overrides[get_current_user_required] = lambda: {
        "username": "tester"
    }

    with TestClient(app) as client:
        response = client.post(
            "/workspaces/workspace/download-zip",
            json={"filenames": []},
        )

    assert response.status_code == 413


@pytest.mark.parametrize("link_kind", ("symlink", "hardlink"))
def test_workspace_update_does_not_overwrite_linked_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    link_kind: str,
) -> None:
    root = tmp_path / "workspace-root"
    target = root / "tester" / "workspace"
    target.mkdir(parents=True)
    victim = tmp_path / "outside-metadata.json"
    original = json.dumps({"desc": "outside-canary", "pinned": False})
    victim.write_text(original, encoding="utf-8")
    metadata = target / ".workspace.json"
    if link_kind == "symlink":
        metadata.symlink_to(victim)
    else:
        metadata.hardlink_to(victim)
    _configure_limits(monkeypatch, root)

    response = assistant_data.update_workspace(
        "workspace",
        assistant_data.WorkspaceUpdate(desc="overwrite-attempt"),
        user={"username": "tester"},
    )

    assert response.status_code == 409
    assert victim.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    "source",
    (
        '{"desc":"first","desc":"shadow","pinned":false}',
        '{"desc":"unsafe","pinned":false,"value":NaN}',
        '{"desc":"unsafe","pinned":false,"value":1e400}',
    ),
)
def test_workspace_update_rejects_ambiguous_metadata_without_rewriting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
) -> None:
    root = tmp_path / "workspace-root"
    target = root / "tester" / "workspace"
    target.mkdir(parents=True)
    metadata = target / ".workspace.json"
    metadata.write_text(source, encoding="utf-8")
    _configure_limits(monkeypatch, root)

    response = assistant_data.update_workspace(
        "workspace",
        assistant_data.WorkspaceUpdate(desc="overwrite-attempt"),
        user={"username": "tester"},
    )

    assert response.status_code == 409
    assert metadata.read_text(encoding="utf-8") == source


def test_workspace_update_atomically_preserves_normal_pin_and_rename_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace-root"
    user_root = root / "tester"
    first = user_root / "first"
    second = user_root / "second"
    first.mkdir(parents=True)
    second.mkdir()
    for path, pinned in ((first, False), (second, True)):
        (path / ".workspace.json").write_text(
            json.dumps(
                {
                    "desc": path.name,
                    "pinned": pinned,
                    "created": "2026-08-09 00:00:00",
                    "updated": "2026-08-09 00:00:00",
                }
            ),
            encoding="utf-8",
        )
    _configure_limits(monkeypatch, root)

    response = assistant_data.update_workspace(
        "first",
        assistant_data.WorkspaceUpdate(
            desc="updated",
            pinned=True,
            rename="renamed",
        ),
        user={"username": "tester"},
    )

    assert response.status_code == 200
    renamed_metadata = user_root / "renamed" / ".workspace.json"
    assert not first.exists()
    assert json.loads(renamed_metadata.read_text(encoding="utf-8"))["desc"] == "updated"
    assert json.loads(renamed_metadata.read_text(encoding="utf-8"))["pinned"] is True
    assert json.loads(
        (second / ".workspace.json").read_text(encoding="utf-8")
    )["pinned"] is False
    assert renamed_metadata.stat().st_mode & 0o777 == 0o600


def test_workspace_creation_does_not_follow_raced_metadata_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace-root"
    user_root = root / "tester"
    user_root.mkdir(parents=True)
    target = user_root / "new-workspace"
    victim = tmp_path / "outside-create-metadata.json"
    victim.write_text("preserve-me", encoding="utf-8")
    original_mkdir = Path.mkdir

    def injecting_mkdir(path: Path, *args, **kwargs):
        result = original_mkdir(path, *args, **kwargs)
        if path == target:
            (path / ".workspace.json").symlink_to(victim)
        return result

    monkeypatch.setattr(Path, "mkdir", injecting_mkdir)
    _configure_limits(monkeypatch, root)

    response = assistant_data.create_workspace(
        assistant_data.WorkspaceCreate(name="new-workspace"),
        user={"username": "tester"},
    )

    assert response.status_code == 409
    assert victim.read_text(encoding="utf-8") == "preserve-me"
    assert not target.exists()


def test_upload_rejects_linked_staging_directory_without_external_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace-root"
    target = _workspace(root)
    user_root = root / "tester"
    outside = tmp_path / "outside-staging"
    outside.mkdir(mode=0o755)
    outside_mode = outside.stat().st_mode & 0o777
    canary = outside / "preserve.txt"
    canary.write_text("preserve-me", encoding="utf-8")
    (user_root / ".workspace-staging").symlink_to(
        outside,
        target_is_directory=True,
    )
    _configure_limits(monkeypatch, root)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            assistant_data.upload_workspace_files(
                "workspace",
                [_upload("blocked.txt", b"blocked")],
                user={"username": "tester"},
            )
        )

    assert exc.value.status_code == 503
    assert canary.read_text(encoding="utf-8") == "preserve-me"
    assert outside.stat().st_mode & 0o777 == outside_mode
    assert not (target / "blocked.txt").exists()


def test_workspace_file_update_rejects_hardlink_without_touching_victim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace-root"
    target = _workspace(root)
    victim = tmp_path / "outside-update-secret.txt"
    victim.write_text("preserve-me", encoding="utf-8")
    (target / "hard.txt").hardlink_to(victim)
    _configure_limits(monkeypatch, root)

    response = assistant_data.update_workspace_file(
        "workspace",
        "hard.txt",
        assistant_data.FileCreate(filename="hard.txt", content="overwrite"),
        user={"username": "tester"},
    )

    assert response.status_code == 409
    assert victim.read_text(encoding="utf-8") == "preserve-me"


def test_workspace_file_update_is_atomic_and_private(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace-root"
    target = _workspace(root)
    path = target / "normal.txt"
    path.write_text("old", encoding="utf-8")
    path.chmod(0o644)
    _configure_limits(monkeypatch, root)

    response = assistant_data.update_workspace_file(
        "workspace",
        "normal.txt",
        assistant_data.FileCreate(filename="normal.txt", content="new"),
        user={"username": "tester"},
    )

    assert response.status_code == 200
    assert path.read_text(encoding="utf-8") == "new"
    assert path.stat().st_mode & 0o777 == 0o600


def test_workspace_file_creation_does_not_follow_raced_destination_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace-root"
    target = _workspace(root)
    destination = target / "new.txt"
    victim = tmp_path / "outside-created-file.txt"
    victim.write_text("preserve-me", encoding="utf-8")
    original_mkdir = Path.mkdir

    def injecting_mkdir(path: Path, *args, **kwargs):
        result = original_mkdir(path, *args, **kwargs)
        if path == target and not destination.exists():
            destination.symlink_to(victim)
        return result

    monkeypatch.setattr(Path, "mkdir", injecting_mkdir)
    _configure_limits(monkeypatch, root)

    response = assistant_data.create_workspace_file(
        "workspace",
        assistant_data.FileCreate(filename="new.txt", content="overwrite"),
        user={"username": "tester"},
    )

    assert response.status_code == 409
    assert victim.read_text(encoding="utf-8") == "preserve-me"
