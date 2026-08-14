"""
数据助手 CRUD 路由：知识库、站点、成员、工作区。
工作区基于实际文件系统（/root/data/workspace/{username}/{name}），其余（站点/成员/知识库）基于 JSON 文件持久化。
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Dict, Iterator, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, model_validator
from starlette.background import BackgroundTask

from api.core.environment import int_setting, string_setting
from api.services import file_store
from api.services.assistant_user_defaults import (
    assistant_user_root_path,
    create_assistant_workspace_metadata,
    ensure_assistant_user_defaults,
    read_assistant_workspace_metadata,
    replace_assistant_workspace_metadata,
)
from api.services.auth import get_current_user_required
from api.services.report_export import build_docx_bytes

WORKSPACE_ROOT = Path(string_setting("GLOBEMIND_WORKSPACE_ROOT", "/root/data/workspace"))
SAFE_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,96}$")
UPLOAD_CHUNK_BYTES = 1024 * 1024
MAX_UPLOAD_FILES = int_setting("WORKSPACE_UPLOAD_MAX_FILES", 20, minimum=1)
MAX_UPLOAD_FILE_BYTES = max(
    UPLOAD_CHUNK_BYTES,
    int_setting("WORKSPACE_UPLOAD_MAX_FILE_BYTES", 25 * 1024 * 1024),
)
MAX_UPLOAD_REQUEST_BYTES = max(
    MAX_UPLOAD_FILE_BYTES,
    int_setting("WORKSPACE_UPLOAD_MAX_REQUEST_BYTES", 100 * 1024 * 1024),
)
MAX_WORKSPACE_BYTES = max(
    MAX_UPLOAD_REQUEST_BYTES,
    int_setting("WORKSPACE_MAX_BYTES", 1024 * 1024 * 1024),
)
MAX_USER_BYTES = max(
    MAX_WORKSPACE_BYTES,
    int_setting("WORKSPACE_USER_MAX_BYTES", 5 * 1024 * 1024 * 1024),
)
MAX_WORKSPACES_PER_USER = int_setting(
    "WORKSPACE_MAX_COUNT_PER_USER",
    50,
    minimum=1,
)
MIN_DISK_FREE_BYTES = int_setting(
    "WORKSPACE_DISK_MIN_FREE_BYTES",
    1024 * 1024 * 1024,
    minimum=0,
)
MAX_TEXT_PREVIEW_BYTES = int_setting(
    "WORKSPACE_TEXT_PREVIEW_MAX_BYTES",
    1024 * 1024,
    minimum=1,
)
MAX_ZIP_SELECTION_FILES = 500

router = APIRouter()


def _ok(data: Any = None, msg: str = "") -> JSONResponse:
    return JSONResponse(content={"ok": True, "data": data, "msg": msg})


def _err(msg: str, status: int = 400) -> JSONResponse:
    return JSONResponse(content={"ok": False, "error": msg}, status_code=status)


@router.get("/sites", tags=["assistant_data"])
def assistant_sites(user: Dict[str, Any] = Depends(get_current_user_required)) -> JSONResponse:
    return _ok(file_store.read_json("sites.json"))


@router.get("/members", tags=["assistant_data"])
def assistant_members(user: Dict[str, Any] = Depends(get_current_user_required)) -> JSONResponse:
    return _ok(file_store.read_json("members.json"))


def _current_username(user: Dict[str, Any]) -> str:
    username = str(user.get("username") or "").strip()
    if username in {".", ".."} or not SAFE_USERNAME_RE.fullmatch(username):
        raise HTTPException(status_code=400, detail="当前用户名不能作为安全工作区目录")
    return username


def _safe_join(root: Path, *parts: str) -> Path:
    root_resolved = root.resolve()
    target = root_resolved.joinpath(*[str(part).replace("\\", "/") for part in parts if str(part)]).resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError:
        raise HTTPException(status_code=400, detail="路径越界，已被用户沙箱拦截")
    return target


def _user_root(username: str, *, create: bool = True) -> Path:
    try:
        root = assistant_user_root_path(username, WORKSPACE_ROOT)
        if create:
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            root = assistant_user_root_path(username, WORKSPACE_ROOT)
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail="用户工作区当前不可安全访问",
        ) from exc
    return root


@contextmanager
def _exclusive_file_lock(path: Path) -> Iterator[None]:
    directory_descriptor = -1
    descriptor = -1
    locked = False
    try:
        try:
            parent_metadata = path.parent.lstat()
        except FileNotFoundError:
            path.parent.mkdir(mode=0o700)
            parent_metadata = path.parent.lstat()
        if not stat.S_ISDIR(parent_metadata.st_mode):
            raise OSError("workspace lock parent is not a real directory")

        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            directory_flags |= os.O_NOFOLLOW
        directory_descriptor = os.open(path.parent, directory_flags)
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path.name, flags, 0o600, dir_fd=directory_descriptor)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise OSError("workspace lock is not a single-link regular file")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = True
        after = os.fstat(descriptor)
        current = os.stat(
            path.name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(after.st_mode)
            or after.st_nlink != 1
            or after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or current.st_dev != after.st_dev
            or current.st_ino != after.st_ino
            or current.st_nlink != 1
        ):
            raise OSError("workspace lock changed while acquiring")
        yield
    except OSError as exc:
        raise HTTPException(
            status_code=503,
            detail="工作区写锁当前不可安全获取",
        ) from exc
    finally:
        if descriptor >= 0:
            try:
                if locked:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
        if directory_descriptor >= 0:
            os.close(directory_descriptor)


@contextmanager
def _workspace_mutation_lock(username: str, name: str) -> Iterator[None]:
    """Lock user quota first, then one workspace, consistently in every worker."""
    user_root = _user_root(username)
    digest = hashlib.sha256(str(name).encode("utf-8")).hexdigest()[:24]
    user_lock = user_root / ".workspace-user.lock"
    workspace_lock = user_root / ".workspace-locks" / f"{digest}.lock"
    with _exclusive_file_lock(user_lock):
        with _exclusive_file_lock(workspace_lock):
            yield


def _ensure_private_user_directory(path: Path, *, label: str) -> Path:
    descriptor = -1
    try:
        try:
            before = path.lstat()
        except FileNotFoundError:
            try:
                path.mkdir(mode=0o700)
            except FileExistsError:
                pass
            before = path.lstat()
        if not stat.S_ISDIR(before.st_mode):
            raise OSError(f"{label} is not a real directory")
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        current = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or current.st_dev != opened.st_dev
            or current.st_ino != opened.st_ino
        ):
            raise OSError(f"{label} changed while opening")
        os.fchmod(descriptor, 0o700)
        return path
    except OSError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"{label}当前不可安全使用",
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _validate_workspace_name(name: str) -> Optional[str]:
    clean = str(name or "").strip()
    if not clean:
        return "工作区名称不能为空"
    if clean in (".", "..") or "/" in clean or "\\" in clean:
        return "工作区名称不能包含路径分隔符"
    if not all(c.isalnum() or c in " _-" for c in clean):
        return "工作区名称仅支持字母、数字、空格、下划线和连字符"
    return None


# ==================== 工作区（文件系统） ====================

class WorkspaceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    desc: str = Field(default="", max_length=500)


def _ws_dir(username: str, name: str) -> Path:
    return _safe_join(_user_root(username), name)


def _ws_meta_path(username: str, name: str) -> Path:
    return _ws_dir(username, name) / ".workspace.json"


def _require_workspace(username: str, name: str) -> Path:
    target = _ws_dir(username, name)
    if not target.is_dir() or not (target / ".workspace.json").is_file():
        raise HTTPException(status_code=404, detail="工作区不存在")
    return target


def _workspace_child(username: str, name: str, child: str = "") -> Path:
    target = _require_workspace(username, name)
    return _safe_join(target, child)


def _workspace_size_bytes(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        if path.is_file() and not path.name.startswith(".upload-"):
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


def _user_size_bytes(root: Path) -> int:
    """Count persistent user data without following links or staging artifacts."""
    total = 0
    if not root.is_dir():
        return total
    for dir_path, dir_names, file_names in os.walk(root, followlinks=False):
        current = Path(dir_path)
        dir_names[:] = [
            name
            for name in dir_names
            if name not in {
                ".workspace-locks",
                ".workspace-staging",
                ".assistant_schedule_locks",
            }
            and not (current / name).is_symlink()
        ]
        for name in file_names:
            if name == ".workspace-user.lock" or name.startswith(".upload-"):
                continue
            path = current / name
            if path.is_symlink():
                continue
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


def _workspace_count(username: str) -> int:
    return len(_read_workspaces(username))


def _read_bounded_regular_file(path: Path, *, maximum: int) -> bytes:
    descriptor = -1
    try:
        before = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError("workspace file is not a single-link regular file")
        if before.st_size > maximum:
            raise OverflowError("workspace file exceeds the preview limit")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or opened.st_size != before.st_size
            or opened.st_mtime_ns != before.st_mtime_ns
            or opened.st_nlink != 1
            or not stat.S_ISREG(opened.st_mode)
        ):
            raise ValueError("workspace file changed while opening")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                raise ValueError("workspace file was truncated while reading")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise OverflowError("workspace file grew beyond the preview limit")
        after = os.fstat(descriptor)
        if (
            after.st_dev != opened.st_dev
            or after.st_ino != opened.st_ino
            or after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
            or after.st_nlink != 1
        ):
            raise ValueError("workspace file changed while reading")
        return b"".join(chunks)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _open_single_link_regular_file(path: Path):
    descriptor = -1
    try:
        before = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError("workspace file is not a single-link regular file")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        current = path.stat(follow_symlinks=False)
        if (
            opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or opened.st_size != before.st_size
            or opened.st_mtime_ns != before.st_mtime_ns
            or opened.st_nlink != 1
            or not stat.S_ISREG(opened.st_mode)
            or current.st_dev != opened.st_dev
            or current.st_ino != opened.st_ino
            or current.st_nlink != 1
        ):
            raise ValueError("workspace file changed while opening")
        handle = os.fdopen(descriptor, "rb")
        descriptor = -1
        return handle
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _atomic_replace_workspace_file(path: Path, content: bytes) -> os.stat_result:
    directory_descriptor = -1
    temporary_descriptor = -1
    temporary_created = False
    temporary_name = f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            directory_flags |= os.O_NOFOLLOW
        directory_descriptor = os.open(path.parent, directory_flags)
        before = os.stat(
            path.name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError("workspace file is not a single-link regular file")

        write_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            write_flags |= os.O_NOFOLLOW
        temporary_descriptor = os.open(
            temporary_name,
            write_flags,
            0o600,
            dir_fd=directory_descriptor,
        )
        temporary_created = True
        os.fchmod(temporary_descriptor, 0o600)
        view = memoryview(content)
        while view:
            written = os.write(temporary_descriptor, view)
            if written <= 0:
                raise OSError("workspace file write made no progress")
            view = view[written:]
        os.fsync(temporary_descriptor)
        temporary_metadata = os.fstat(temporary_descriptor)
        if (
            not stat.S_ISREG(temporary_metadata.st_mode)
            or temporary_metadata.st_nlink != 1
            or temporary_metadata.st_size != len(content)
        ):
            raise ValueError("workspace temporary file failed integrity checks")

        current = os.stat(
            path.name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if (
            current.st_dev != before.st_dev
            or current.st_ino != before.st_ino
            or current.st_size != before.st_size
            or current.st_mtime_ns != before.st_mtime_ns
            or current.st_ctime_ns != before.st_ctime_ns
            or current.st_nlink != 1
            or not stat.S_ISREG(current.st_mode)
        ):
            raise ValueError("workspace file changed before replacement")
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
        temporary_created = False
        destination = os.stat(
            path.name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if (
            destination.st_dev != temporary_metadata.st_dev
            or destination.st_ino != temporary_metadata.st_ino
            or destination.st_nlink != 1
            or destination.st_size != len(content)
            or not stat.S_ISREG(destination.st_mode)
        ):
            raise ValueError("workspace file replacement failed integrity checks")
        os.fsync(directory_descriptor)
        return destination
    except FileExistsError as exc:
        raise ValueError("workspace temporary path is unavailable") from exc
    except OSError as exc:
        raise ValueError("workspace file cannot be replaced safely") from exc
    finally:
        if temporary_descriptor >= 0:
            os.close(temporary_descriptor)
        if temporary_created and directory_descriptor >= 0:
            try:
                os.unlink(temporary_name, dir_fd=directory_descriptor)
            except FileNotFoundError:
                pass
        if directory_descriptor >= 0:
            os.close(directory_descriptor)


def _atomic_create_workspace_file(path: Path, content: bytes) -> os.stat_result:
    directory_descriptor = -1
    temporary_descriptor = -1
    temporary_created = False
    temporary_name = f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            directory_flags |= os.O_NOFOLLOW
        directory_descriptor = os.open(path.parent, directory_flags)
        try:
            os.stat(
                path.name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            raise ValueError("workspace destination already exists")

        write_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            write_flags |= os.O_NOFOLLOW
        temporary_descriptor = os.open(
            temporary_name,
            write_flags,
            0o600,
            dir_fd=directory_descriptor,
        )
        temporary_created = True
        os.fchmod(temporary_descriptor, 0o600)
        view = memoryview(content)
        while view:
            written = os.write(temporary_descriptor, view)
            if written <= 0:
                raise OSError("workspace file write made no progress")
            view = view[written:]
        os.fsync(temporary_descriptor)
        temporary_metadata = os.fstat(temporary_descriptor)
        if (
            not stat.S_ISREG(temporary_metadata.st_mode)
            or temporary_metadata.st_nlink != 1
            or temporary_metadata.st_size != len(content)
        ):
            raise ValueError("workspace temporary file failed integrity checks")
        os.link(
            temporary_name,
            path.name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        os.close(temporary_descriptor)
        temporary_descriptor = -1
        os.unlink(temporary_name, dir_fd=directory_descriptor)
        temporary_created = False
        destination = os.stat(
            path.name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if (
            destination.st_dev != temporary_metadata.st_dev
            or destination.st_ino != temporary_metadata.st_ino
            or destination.st_nlink != 1
            or destination.st_size != len(content)
            or not stat.S_ISREG(destination.st_mode)
        ):
            raise ValueError("workspace file creation failed integrity checks")
        os.fsync(directory_descriptor)
        return destination
    except FileExistsError as exc:
        raise ValueError("workspace destination is unavailable") from exc
    except OSError as exc:
        raise ValueError("workspace file cannot be created safely") from exc
    finally:
        if temporary_descriptor >= 0:
            os.close(temporary_descriptor)
        if temporary_created and directory_descriptor >= 0:
            try:
                os.unlink(temporary_name, dir_fd=directory_descriptor)
            except FileNotFoundError:
                pass
        if directory_descriptor >= 0:
            os.close(directory_descriptor)


def _ensure_disk_headroom(path: Path) -> None:
    try:
        free = shutil.disk_usage(path).free
    except OSError as exc:
        raise HTTPException(status_code=507, detail="无法确认工作区磁盘余量") from exc
    if free < MIN_DISK_FREE_BYTES:
        raise HTTPException(
            status_code=507,
            detail=(
                "工作区磁盘可用空间低于安全水位 "
                f"{MIN_DISK_FREE_BYTES // (1024 * 1024)} MB"
            ),
        )


async def _stage_upload(upload: UploadFile, target: Path) -> tuple[Path, int]:
    """Stream one upload to a private staging file and enforce the per-file cap."""
    fd, raw_path = tempfile.mkstemp(prefix=".upload-", dir=target)
    staged_path = Path(raw_path)
    size = 0
    try:
        with os.fdopen(fd, "wb") as output:
            while True:
                chunk = await upload.read(UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_FILE_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"单个文件不能超过 {MAX_UPLOAD_FILE_BYTES // (1024 * 1024)} MB",
                    )
                output.write(chunk)
        return staged_path, size
    except Exception:
        staged_path.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()


def _read_workspaces(username: str) -> list[dict[str, Any]]:
    """从文件系统读取用户的所有 workspace 目录，附带元信息。"""
    user_root = _user_root(username, create=False)
    if not user_root.is_dir():
        return []
    result = []
    try:
        with os.scandir(user_root) as iterator:
            entries = sorted(iterator, key=lambda item: item.name)
    except OSError as exc:
        raise HTTPException(
            status_code=503,
            detail="工作区目录当前不可安全读取",
        ) from exc
    for entry in entries:
        if not entry.is_dir(follow_symlinks=False):
            continue
        name = entry.name
        meta_path = Path(entry.path) / ".workspace.json"
        try:
            meta = read_assistant_workspace_metadata(meta_path)
        except ValueError:
            continue
        desc = meta.get("desc", "")
        pinned = meta.get("pinned", False)
        created = meta.get("created", "")
        updated = meta.get("updated", "")
        if (
            not isinstance(desc, str)
            or len(desc) > 500
            or not isinstance(pinned, bool)
            or not isinstance(created, str)
            or len(created) > 64
            or not isinstance(updated, str)
            or len(updated) > 64
        ):
            continue
        try:
            with os.scandir(entry.path) as children:
                file_count = sum(
                    child.name != ".workspace.json" for child in children
                )
        except OSError as exc:
            raise HTTPException(
                status_code=503,
                detail="工作区目录当前不可安全读取",
            ) from exc
        result.append({
            "name": name,
            "desc": desc,
            "pinned": pinned,
            "created": created,
            "updated": updated,
            "fileCount": file_count,
        })
    # 置顶在前，按 created 降序
    result.sort(key=lambda w: (not w["pinned"], w.get("created", "")), reverse=False)
    return result


@router.get("/workspaces", tags=["assistant_data"])
def list_workspaces(
    user: Dict[str, Any] = Depends(get_current_user_required),
) -> JSONResponse:
    username = _current_username(user)
    ensure_assistant_user_defaults(username, workspace_root=WORKSPACE_ROOT)
    data = _read_workspaces(username)
    return _ok(data)


@router.post("/workspaces", tags=["assistant_data"])
def create_workspace(
    body: WorkspaceCreate,
    user: Dict[str, Any] = Depends(get_current_user_required),
) -> JSONResponse:
    username = _current_username(user)
    name = body.name.strip()
    invalid = _validate_workspace_name(name)
    if invalid:
        return _err(invalid)
    with _workspace_mutation_lock(username, name):
        target = _ws_dir(username, name)
        if target.exists():
            return _err("同名工作区已存在")
        if _workspace_count(username) >= MAX_WORKSPACES_PER_USER:
            return _err(f"每个用户最多创建 {MAX_WORKSPACES_PER_USER} 个工作区", 409)
        target.mkdir(parents=True, exist_ok=False)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        meta = {
            "desc": body.desc,
            "pinned": False,
            "created": now,
            "updated": now,
        }
        try:
            created = create_assistant_workspace_metadata(
                _ws_meta_path(username, name),
                meta,
            )
            if not created:
                raise ValueError("workspace metadata path already exists")
        except ValueError:
            shutil.rmtree(target, ignore_errors=True)
            return _err("工作区元数据路径冲突，未创建", 409)
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise
        return _ok({
            "name": name,
            "desc": body.desc,
            "pinned": False,
            "created": now,
            "updated": now,
            "fileCount": 0,
        })


@router.delete("/workspaces/{name}", tags=["assistant_data"])
def delete_workspace(
    name: str,
    user: Dict[str, Any] = Depends(get_current_user_required),
) -> JSONResponse:
    username = _current_username(user)
    with _workspace_mutation_lock(username, name):
        target = _require_workspace(username, name)
        shutil.rmtree(target)
    return _ok(msg="已删除")


class WorkspaceUpdate(BaseModel):
    name: Optional[str] = None
    desc: Optional[str] = None
    pinned: Optional[bool] = None
    rename: Optional[str] = None  # 新名称（重命名时使用）


@router.put("/workspaces/{name}", tags=["assistant_data"])
def update_workspace(
    name: str,
    body: WorkspaceUpdate,
    user: Dict[str, Any] = Depends(get_current_user_required),
) -> JSONResponse:
    username = _current_username(user)
    with _workspace_mutation_lock(username, name):
        target = _require_workspace(username, name)
        meta_path = _ws_meta_path(username, name)
        try:
            meta = read_assistant_workspace_metadata(meta_path)
        except ValueError:
            return _err("工作区元数据当前不可安全更新", 409)
        changed = False
        if body.desc is not None:
            meta["desc"] = body.desc
            changed = True
        if body.pinned is not None:
            if body.pinned:
                for ws in _read_workspaces(username):
                    other_meta_path = _ws_meta_path(username, ws["name"])
                    if other_meta_path == meta_path or not other_meta_path.is_file():
                        continue
                    try:
                        other_meta = read_assistant_workspace_metadata(
                            other_meta_path
                        )
                    except ValueError:
                        return _err("工作区元数据当前不可安全更新", 409)
                    other_meta["pinned"] = False
                    other_meta["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    try:
                        replace_assistant_workspace_metadata(
                            other_meta_path,
                            other_meta,
                        )
                    except ValueError:
                        return _err("工作区元数据当前不可安全更新", 409)
            meta["pinned"] = body.pinned
            changed = True
        if body.rename is not None:
            new_name = body.rename.strip()
            invalid = _validate_workspace_name(new_name)
            if invalid:
                return _err(invalid.replace("工作区名称", "新名称"))
            new_target = _ws_dir(username, new_name)
            if new_target.exists():
                return _err("目标名称已存在")
            target.rename(new_target)
            target = new_target
            meta_path = _ws_meta_path(username, new_name)
            changed = True
        if changed:
            meta["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            try:
                replace_assistant_workspace_metadata(meta_path, meta)
            except ValueError:
                return _err("工作区元数据当前不可安全更新", 409)
        return _ok({
            "name": body.rename or name,
            "desc": meta.get("desc", ""),
            "pinned": meta.get("pinned", False),
            "created": meta.get("created", ""),
            "updated": meta.get("updated", ""),
        })


# ==================== 工作区文件管理 ====================

@router.get("/workspaces/{name:path}/files", tags=["assistant_data"])
def list_workspace_files(
    name: str,
    subpath: str = Query(default=""),
    user: Dict[str, Any] = Depends(get_current_user_required),
) -> JSONResponse:
    username = _current_username(user)
    target = _workspace_child(username, name, subpath)
    if not target.is_dir():
        return _err("路径不存在", 404)
    files = []
    for entry in sorted(target.iterdir()):
        if entry.name == ".workspace.json":
            continue
        try:
            metadata = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        is_directory = stat.S_ISDIR(metadata.st_mode)
        is_regular = stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1
        if not (is_directory or is_regular):
            continue
        files.append({
            "name": entry.name,
            "is_dir": is_directory,
            "size": metadata.st_size if is_regular else 0,
            "modified": datetime.fromtimestamp(metadata.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        })
    return _ok(files)


class FileCreate(BaseModel):
    filename: str = Field(..., min_length=1, max_length=200)
    content: str = Field(default="", max_length=100000)


class FileDocxExport(BaseModel):
    filename: str = Field(..., min_length=1, max_length=200)
    title: str = Field(default="GlobeMind Report", max_length=200)
    content: str = Field(default="", max_length=500000)


@router.post("/workspaces/{name:path}/files", tags=["assistant_data"])
def create_workspace_file(
    name: str,
    body: FileCreate,
    user: Dict[str, Any] = Depends(get_current_user_required),
) -> JSONResponse:
    username = _current_username(user)
    target = _require_workspace(username, name)
    fname = body.filename.strip()
    if not fname:
        return _err("文件名不能为空")
    # 不允许特殊路径，但允许 "/" 用于子目录中创建文件
    if ".." in fname.split("/") or fname in (".", ".."):
        return _err("文件名不合法")
    file_path = _safe_join(target, fname)
    if file_path.exists():
        return _err("文件已存在")
    file_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        metadata = _atomic_create_workspace_file(
            file_path,
            (body.content or "").encode("utf-8"),
        )
    except ValueError:
        return _err("文件路径冲突或当前不可安全创建", 409)
    return _ok({
        "name": fname,
        "is_dir": False,
        "size": metadata.st_size,
        "modified": datetime.fromtimestamp(metadata.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
    })


@router.post("/workspaces/{name}/files/export-docx", tags=["assistant_data"])
def export_workspace_docx_file(
    name: str,
    body: FileDocxExport,
    user: Dict[str, Any] = Depends(get_current_user_required),
) -> JSONResponse:
    username = _current_username(user)
    target = _require_workspace(username, name)
    fname = body.filename.strip()
    if not fname:
        return _err("文件名不能为空")
    if ".." in fname.split("/") or fname in (".", ".."):
        return _err("文件名不合法")
    if not fname.lower().endswith(".docx"):
        fname = f"{fname}.docx"
    file_path = _safe_join(target, fname)
    if file_path.exists():
        return _err("文件已存在")
    file_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        metadata = _atomic_create_workspace_file(
            file_path,
            build_docx_bytes(
                body.content or "",
                title=body.title.strip() or fname,
            ),
        )
    except ValueError:
        return _err("文件路径冲突或当前不可安全创建", 409)
    return _ok({
        "name": fname,
        "is_dir": False,
        "size": metadata.st_size,
        "modified": datetime.fromtimestamp(metadata.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
    })


@router.put("/workspaces/{name:path}/files/{filename:path}", tags=["assistant_data"])
def update_workspace_file(
    name: str,
    filename: str,
    body: FileCreate,
    user: Dict[str, Any] = Depends(get_current_user_required),
) -> JSONResponse:
    username = _current_username(user)
    target = _require_workspace(username, name)
    file_path = _safe_join(target, filename)
    if not file_path.is_file():
        return _err("文件不存在", 404)
    try:
        metadata = _atomic_replace_workspace_file(
            file_path,
            body.content.encode("utf-8"),
        )
    except ValueError:
        return _err("文件当前不可安全更新", 409)
    return _ok({
        "name": filename,
        "size": metadata.st_size,
        "modified": datetime.fromtimestamp(metadata.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
    })


@router.delete("/workspaces/{name:path}/files/{filename:path}", tags=["assistant_data"])
def delete_workspace_file(
    name: str,
    filename: str,
    user: Dict[str, Any] = Depends(get_current_user_required),
) -> JSONResponse:
    username = _current_username(user)
    target = _require_workspace(username, name)
    file_path = _safe_join(target, filename)
    if not file_path.exists():
        return _err("文件不存在", 404)
    if file_path.is_dir():
        shutil.rmtree(file_path)
    else:
        file_path.unlink()
    return _ok(msg="已删除")


@router.post("/workspaces/{name:path}/upload", tags=["assistant_data"])
async def upload_workspace_files(
    name: str,
    files: List[UploadFile] = File(...),
    user: Dict[str, Any] = Depends(get_current_user_required),
) -> JSONResponse:
    username = _current_username(user)
    target = _require_workspace(username, name)
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(status_code=413, detail=f"单次最多上传 {MAX_UPLOAD_FILES} 个文件")

    _ensure_disk_headroom(target)
    staging_dir = _ensure_private_user_directory(
        _user_root(username) / ".workspace-staging",
        label="工作区上传暂存目录",
    )
    request_size = 0
    staged: list[tuple[Path, str, int]] = []
    names: set[str] = set()
    try:
        for upload in files:
            if not upload.filename:
                continue
            fname = upload.filename.replace("\\", "/").split("/")[-1]
            if not fname or fname in (".", "..") or len(fname.encode("utf-8")) > 255:
                raise HTTPException(status_code=400, detail="上传文件名不合法")
            if fname in names:
                raise HTTPException(status_code=400, detail=f"同一请求中存在重名文件: {fname}")
            names.add(fname)

            staged_path, size = await _stage_upload(upload, staging_dir)
            staged.append((staged_path, fname, size))
            request_size += size
            if request_size > MAX_UPLOAD_REQUEST_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"单次上传总量不能超过 {MAX_UPLOAD_REQUEST_BYTES // (1024 * 1024)} MB",
                )
            _ensure_disk_headroom(target)

        with _workspace_mutation_lock(username, name):
            # Re-resolve after staging: the workspace may have been renamed or removed.
            target = _require_workspace(username, name)
            publish_items = [
                (staged_path, _safe_join(target, fname), size)
                for staged_path, fname, size in staged
            ]
            for _, file_path, _ in publish_items:
                if file_path.exists() and not file_path.is_file():
                    raise HTTPException(status_code=409, detail=f"目标名称不是普通文件: {file_path.name}")

            workspace_size = _workspace_size_bytes(target)
            user_size = _user_size_bytes(_user_root(username))
            replaced_size = sum(path.stat().st_size for _, path, _ in publish_items if path.is_file())
            projected_workspace_size = workspace_size - replaced_size + request_size
            if projected_workspace_size > MAX_WORKSPACE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"工作区总容量不能超过 {MAX_WORKSPACE_BYTES // (1024 * 1024)} MB",
                )
            projected_user_size = user_size - replaced_size + request_size
            if projected_user_size > MAX_USER_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"用户全部工作区总容量不能超过 {MAX_USER_BYTES // (1024 * 1024)} MB",
                )
            _ensure_disk_headroom(target)

            transactions: list[dict[str, Any]] = []
            try:
                for staged_path, file_path, _ in publish_items:
                    had_original = file_path.is_file()
                    backup_path = file_path.with_name(
                        f".upload-backup-{os.getpid()}-{uuid.uuid4().hex}"
                    )
                    entry = {
                        "target": file_path,
                        "backup": backup_path,
                        "had_original": had_original,
                        "published": False,
                    }
                    transactions.append(entry)
                    if had_original:
                        os.replace(file_path, backup_path)
                    os.replace(staged_path, file_path)
                    entry["published"] = True
            except Exception as exc:
                rollback_errors = []
                for entry in reversed(transactions):
                    file_path = entry["target"]
                    backup_path = entry["backup"]
                    try:
                        if entry["published"] and file_path.is_file():
                            file_path.unlink()
                        if entry["had_original"] and backup_path.is_file():
                            os.replace(backup_path, file_path)
                    except OSError as rollback_exc:
                        rollback_errors.append(str(rollback_exc))
                detail = "多文件上传发布失败，原文件已回滚"
                if rollback_errors:
                    detail = "多文件上传发布失败，部分原文件恢复失败"
                raise HTTPException(status_code=500, detail=detail) from exc

            uploaded = []
            for entry in transactions:
                backup_path = entry["backup"]
                try:
                    backup_path.unlink(missing_ok=True)
                except OSError:
                    # The committed target is authoritative; a hidden backup can be
                    # cleaned by maintenance without turning success into an error.
                    pass
                file_path = entry["target"]
                stat = file_path.stat()
                uploaded.append({
                    "name": file_path.name,
                    "is_dir": False,
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                })
            return _ok(uploaded)
    finally:
        for staged_path, _, _ in staged:
            staged_path.unlink(missing_ok=True)


@router.get("/workspaces/{name:path}/files/{filename:path}/download", tags=["assistant_data"])
def download_workspace_file(
    name: str,
    filename: str,
    user: Dict[str, Any] = Depends(get_current_user_required),
) -> StreamingResponse:
    username = _current_username(user)
    target = _require_workspace(username, name)
    file_path = _safe_join(target, filename)
    if not file_path.is_file():
        return _err("文件不存在", 404)
    try:
        handle = _open_single_link_regular_file(file_path)
    except (OSError, ValueError):
        return _err("文件当前不可安全下载", 409)
    # RFC 5987 — 支持中文文件名
    from urllib.parse import quote
    disposition = f'attachment; filename="{quote(filename)}"; filename*=UTF-8\'\'{quote(filename)}'
    return StreamingResponse(
        handle,
        media_type="application/octet-stream",
        headers={"Content-Disposition": disposition},
        background=BackgroundTask(handle.close),
    )


TEXT_EXTENSIONS = {".txt", ".md", ".json", ".yaml", ".yml", ".csv", ".xml",
                    ".log", ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css",
                    ".scss", ".less", ".sql", ".sh", ".env", ".ini", ".cfg",
                    ".conf", ".toml", ".gradle", ".properties", ".cfg"}


@router.get("/workspaces/{name:path}/files/{filename:path}/read", tags=["assistant_data"])
def read_workspace_file(
    name: str,
    filename: str,
    user: Dict[str, Any] = Depends(get_current_user_required),
) -> JSONResponse:
    """读取文本文件内容用于预览。"""
    username = _current_username(user)
    target = _require_workspace(username, name)
    file_path = _safe_join(target, filename)
    if not file_path.is_file():
        return _err("文件不存在", 404)
    ext = file_path.suffix.lower()
    if ext not in TEXT_EXTENSIONS:
        return _err("不支持预览该文件类型", 400)
    try:
        encoded = _read_bounded_regular_file(
            file_path,
            maximum=MAX_TEXT_PREVIEW_BYTES,
        )
        content = encoded.decode("utf-8", errors="strict")
    except OverflowError:
        return _err("文件超过安全预览大小限制", 413)
    except UnicodeDecodeError:
        return _err("文件编码不支持预览", 400)
    except (OSError, ValueError):
        return _err("文件当前不可安全读取", 409)
    return _ok({"name": filename, "content": content, "ext": ext})


ZipSelectionName = Annotated[str, Field(min_length=1, max_length=1024)]


class ZipDownloadRequest(BaseModel):
    filenames: List[ZipSelectionName] = Field(
        default_factory=list,
        max_length=MAX_ZIP_SELECTION_FILES,
        description="为空则打包整个工作区",
    )

    @model_validator(mode="after")
    def require_unique_filenames(self) -> "ZipDownloadRequest":
        normalized = [value.replace("\\", "/") for value in self.filenames]
        if len(normalized) != len(set(normalized)):
            raise ValueError("打包文件列表不能包含重复路径")
        return self


@router.post("/workspaces/{name:path}/download-zip", tags=["assistant_data"])
def download_workspace_zip(
    name: str,
    body: ZipDownloadRequest,
    user: Dict[str, Any] = Depends(get_current_user_required),
) -> StreamingResponse:
    username = _current_username(user)
    target = _require_workspace(username, name)
    names = body.filenames
    if not names:
        names = []
        try:
            with os.scandir(target) as entries:
                for entry in entries:
                    if entry.name == ".workspace.json":
                        continue
                    names.append(entry.name)
                    if len(names) > MAX_ZIP_SELECTION_FILES:
                        return _err("工作区文件数超过单次打包上限", 413)
        except OSError:
            return _err("工作区当前不可安全打包", 409)
        names.sort()
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    try:
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
                for fn in names:
                    fp = _safe_join(target, fn)
                    if not fp.exists():
                        continue
                    arcname = Path(fn).as_posix().lstrip("/")
                    if not arcname or arcname.startswith("../") or "/../" in arcname:
                        continue
                    metadata = fp.stat(follow_symlinks=False)
                    if stat.S_ISDIR(metadata.st_mode):
                        zf.writestr(f"{arcname.rstrip('/')}/", b"")
                        continue
                    with _open_single_link_regular_file(fp) as source:
                        with zf.open(arcname, "w") as destination:
                            shutil.copyfileobj(
                                source,
                                destination,
                                length=UPLOAD_CHUNK_BYTES,
                            )
        except (OSError, ValueError):
            tmp.close()
            return _err("所选文件当前不可安全打包", 409)
        tmp.close()
        from urllib.parse import quote
        disposition = f'attachment; filename="{quote(name)}.zip"; filename*=UTF-8\'\'{quote(name)}.zip'
        archive_handle = open(tmp.name, "rb")
        return StreamingResponse(
            archive_handle,
            media_type="application/zip",
            headers={"Content-Disposition": disposition},
            background=BackgroundTask(archive_handle.close),
        )
    finally:
        os.unlink(tmp.name)


# ==================== 站点 ====================

# ==================== 知识库 v2（基于文件系统） ====================

KB_CATEGORY_DIRS = {
    "geo": ("GEO", "地缘政治", "🌍"),
    "mil": ("MIL", "军事安全", "⚔"),
    "econ": ("ECO", "经济贸易", "📊"),
    "tech": ("TEC", "科技情报", "🔬"),
    "social": ("PUB", "社会舆情", "📢"),
    "law": ("LAW", "法律法规", "⚖"),
}


@router.get("/kb2/categories", tags=["assistant_data"])
def list_kb2_categories(
    user: Dict[str, Any] = Depends(get_current_user_required),
) -> JSONResponse:
    username = _current_username(user)
    ensure_assistant_user_defaults(username, workspace_root=WORKSPACE_ROOT)
    kb_root = _safe_join(_user_root(username), "knowledge_base")
    result = []
    for cat_id, (dirname, name, icon) in KB_CATEGORY_DIRS.items():
        dir_path = kb_root / dirname
        count = 0
        if dir_path.is_dir():
            try:
                count = sum(
                    stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1
                    for entry in dir_path.iterdir()
                    for metadata in (entry.stat(follow_symlinks=False),)
                )
            except OSError as exc:
                raise HTTPException(
                    status_code=503,
                    detail="知识库目录当前不可安全读取",
                ) from exc
        result.append({"id": cat_id, "name": name, "icon": icon, "count": count})
    return _ok(result)


@router.get("/kb2/files", tags=["assistant_data"])
def list_kb2_files(
    category: str = Query(...),
    user: Dict[str, Any] = Depends(get_current_user_required),
) -> JSONResponse:
    username = _current_username(user)
    ensure_assistant_user_defaults(username, workspace_root=WORKSPACE_ROOT)
    dir_info = KB_CATEGORY_DIRS.get(category)
    if not dir_info:
        return _err("无效分类")
    dirname = dir_info[0]
    dir_path = _safe_join(_user_root(username), "knowledge_base", dirname)
    if not dir_path.is_dir():
        return _ok([])
    files = []
    for entry in sorted(dir_path.iterdir()):
        try:
            metadata = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            continue
        ext = entry.suffix.lower()
        files.append({
            "name": entry.name,
            "size": metadata.st_size,
            "modified": datetime.fromtimestamp(metadata.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "ext": ext,
        })
    return _ok(files)


@router.get("/kb2/files/{filename:path}/read", tags=["assistant_data"])
def read_kb2_file(
    filename: str,
    category: str = Query(...),
    user: Dict[str, Any] = Depends(get_current_user_required),
) -> JSONResponse:
    username = _current_username(user)
    dir_info = KB_CATEGORY_DIRS.get(category)
    if not dir_info:
        return _err("无效分类", 400)
    dirname = dir_info[0]
    file_path = _safe_join(_user_root(username), "knowledge_base", dirname, filename)
    if not file_path.is_file():
        return _err("文件不存在", 404)
    ext = file_path.suffix.lower()
    if ext not in TEXT_EXTENSIONS:
        return _err("不支持预览该文件类型", 400)
    try:
        encoded = _read_bounded_regular_file(
            file_path,
            maximum=MAX_TEXT_PREVIEW_BYTES,
        )
        content = encoded.decode("utf-8", errors="strict")
    except OverflowError:
        return _err("文件超过安全预览大小限制", 413)
    except UnicodeDecodeError:
        return _err("文件编码不支持预览", 400)
    except (OSError, ValueError):
        return _err("文件当前不可安全读取", 409)
    return _ok({"name": filename, "content": content, "ext": ext})
