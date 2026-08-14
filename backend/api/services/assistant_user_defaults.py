"""Provision the filesystem-backed assistant workspace for one account."""
from __future__ import annotations

import fcntl
import json
import math
import os
import re
import stat
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from api.core.environment import string_setting

SAFE_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,96}$")
BOOTSTRAP_VERSION = 1
BOOTSTRAP_MARKER = ".assistant-defaults-v1.json"
_MAX_BOOTSTRAP_JSON_BYTES = 65_536
_RELEASE_WORKSPACE_ROOT = Path("/root/data/releases/globemind")

DEFAULT_WORKSPACES = (
    {
        "name": "项目",
        "desc": "默认项目工作区，用于集中管理资料、草稿与最终成果",
        "folders": ("资料", "草稿", "成果"),
        "prefer_pinned": True,
    },
    {
        "name": "舆情研判",
        "desc": "沉淀涉华舆情快照、分析过程与研判报告",
        "folders": ("快照", "研判", "报告"),
        "prefer_pinned": False,
    },
    {
        "name": "事件追踪",
        "desc": "跟踪重点事件的时间线、信源与后续动态",
        "folders": ("时间线", "信源", "简报"),
        "prefer_pinned": False,
    },
)

DEFAULT_KNOWLEDGE_BASE_DIRS = ("GEO", "MIL", "ECO", "TEC", "PUB", "LAW")


def default_workspace_root() -> Path:
    return Path(string_setting("GLOBEMIND_WORKSPACE_ROOT", "/root/data/workspace"))


def _absolute_lexical(path: Path) -> Path:
    expanded = path.expanduser()
    return Path(os.path.abspath(os.fspath(expanded)))


def _path_has_symlink_component(path: Path) -> bool:
    absolute = _absolute_lexical(path)
    probe = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        probe = probe / part
        try:
            metadata = probe.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            return True
    return False


def _validate_workspace_path(path: Path, *, label: str) -> Path:
    absolute = _absolute_lexical(path)
    if absolute.is_relative_to(_RELEASE_WORKSPACE_ROOT):
        raise ValueError(f"{label} cannot use a production release path")
    if _path_has_symlink_component(absolute):
        raise ValueError(f"{label} contains a symbolic link")
    return absolute


def _safe_user_root(username: str, workspace_root: Path) -> Path:
    clean = str(username or "").strip()
    if clean in {".", ".."} or not SAFE_USERNAME_RE.fullmatch(clean):
        raise ValueError("username cannot be used as a safe workspace directory")
    root = _validate_workspace_path(workspace_root, label="workspace root")
    target = _validate_workspace_path(root / clean, label="user workspace")
    if not target.is_relative_to(root):
        raise ValueError("workspace path escapes the configured root")
    return target


def assistant_user_root_path(username: str, workspace_root: Path) -> Path:
    """Return a lexical, release-excluded user root without following links."""
    return _safe_user_root(username, workspace_root)


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("workspace metadata contains a duplicate JSON key")
        output[key] = value
    return output


def _reject_non_finite_json_number(value: str) -> None:
    raise ValueError(f"workspace metadata contains a non-finite number: {value}")


def _parse_finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("workspace metadata contains a non-finite number")
    return parsed


def _read_bounded_json(path: Path, *, label: str) -> dict[str, Any]:
    descriptor = -1
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError(f"{label} must be a single-link regular file")
        if before.st_size > _MAX_BOOTSTRAP_JSON_BYTES:
            raise ValueError(f"{label} exceeds the byte limit")
        encoded = b""
        while len(encoded) <= _MAX_BOOTSTRAP_JSON_BYTES:
            chunk = os.read(
                descriptor,
                min(16 * 1024, _MAX_BOOTSTRAP_JSON_BYTES + 1 - len(encoded)),
            )
            if not chunk:
                break
            encoded += chunk
        after = os.fstat(descriptor)
        if (
            after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_nlink != 1
            or after.st_size != len(encoded)
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_ctime_ns != before.st_ctime_ns
            or len(encoded) > _MAX_BOOTSTRAP_JSON_BYTES
        ):
            raise ValueError(f"{label} changed while being read")
        payload = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_non_finite_json_number,
            parse_float=_parse_finite_json_float,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"{label} is not trustworthy JSON") from exc
    except ValueError:
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return payload


def read_assistant_workspace_metadata(path: Path) -> dict[str, Any]:
    """Read one workspace marker through the bootstrap integrity boundary."""
    return _read_bounded_json(path, label="workspace metadata")


def create_assistant_workspace_metadata(
    path: Path,
    payload: dict[str, Any],
) -> bool:
    """Create one private workspace marker without replacing an existing path."""
    return _write_json_if_missing(path, payload)


def replace_assistant_workspace_metadata(path: Path, payload: dict[str, Any]) -> None:
    """Atomically replace one verified workspace marker without following links."""
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("workspace metadata cannot be encoded safely") from exc
    if len(encoded) > _MAX_BOOTSTRAP_JSON_BYTES:
        raise ValueError("workspace metadata exceeds the byte limit")

    parent = _validate_workspace_path(path.parent, label="workspace metadata parent")
    directory_descriptor = -1
    existing_descriptor = -1
    temporary_descriptor = -1
    temporary_created = False
    temporary_name = f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            directory_flags |= os.O_NOFOLLOW
        directory_descriptor = os.open(parent, directory_flags)
        before = os.stat(
            path.name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError("workspace metadata must be a single-link regular file")

        read_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        if hasattr(os, "O_NOFOLLOW"):
            read_flags |= os.O_NOFOLLOW
        existing_descriptor = os.open(
            path.name,
            read_flags,
            dir_fd=directory_descriptor,
        )
        opened = os.fstat(existing_descriptor)
        if (
            opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or opened.st_size != before.st_size
            or opened.st_mtime_ns != before.st_mtime_ns
            or opened.st_ctime_ns != before.st_ctime_ns
            or opened.st_nlink != 1
            or not stat.S_ISREG(opened.st_mode)
        ):
            raise ValueError("workspace metadata changed while opening")
        os.close(existing_descriptor)
        existing_descriptor = -1

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
        view = memoryview(encoded)
        while view:
            written = os.write(temporary_descriptor, view)
            if written <= 0:
                raise OSError("workspace metadata write made no progress")
            view = view[written:]
        os.fsync(temporary_descriptor)
        temporary_metadata = os.fstat(temporary_descriptor)
        if (
            not stat.S_ISREG(temporary_metadata.st_mode)
            or temporary_metadata.st_nlink != 1
            or temporary_metadata.st_size != len(encoded)
        ):
            raise ValueError("workspace metadata temporary file failed integrity checks")

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
            raise ValueError("workspace metadata changed before replacement")
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
            or destination.st_size != len(encoded)
            or not stat.S_ISREG(destination.st_mode)
        ):
            raise ValueError("workspace metadata replacement failed integrity checks")
        os.fsync(directory_descriptor)
    except FileExistsError as exc:
        raise ValueError("workspace metadata temporary path is unavailable") from exc
    except OSError as exc:
        raise ValueError("workspace metadata cannot be replaced safely") from exc
    finally:
        if existing_descriptor >= 0:
            os.close(existing_descriptor)
        if temporary_descriptor >= 0:
            os.close(temporary_descriptor)
        if temporary_created and directory_descriptor >= 0:
            try:
                os.unlink(temporary_name, dir_fd=directory_descriptor)
            except FileNotFoundError:
                pass
        if directory_descriptor >= 0:
            os.close(directory_descriptor)


def _validate_reserved_paths(user_root: Path) -> None:
    marker_path = user_root / BOOTSTRAP_MARKER
    try:
        marker_metadata = marker_path.lstat()
    except FileNotFoundError:
        marker_metadata = None
    if marker_metadata is not None and (
        not stat.S_ISREG(marker_metadata.st_mode) or marker_metadata.st_nlink != 1
    ):
        raise ValueError("bootstrap marker must be a single-link regular file")

    lock_path = user_root / ".assistant-defaults.lock"
    try:
        lock_metadata = lock_path.lstat()
    except FileNotFoundError:
        lock_metadata = None
    if lock_metadata is not None and (
        not stat.S_ISREG(lock_metadata.st_mode) or lock_metadata.st_nlink != 1
    ):
        raise ValueError("bootstrap lock must be a single-link regular file")

    reserved_directories = {
        *(str(definition["name"]) for definition in DEFAULT_WORKSPACES),
        "knowledge_base",
    }
    for name in reserved_directories:
        path = user_root / name
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("reserved workspace path must be a real directory")

    for definition in DEFAULT_WORKSPACES:
        workspace = user_root / str(definition["name"])
        for folder in definition["folders"]:
            path = workspace / str(folder)
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISDIR(metadata.st_mode):
                raise ValueError("reserved workspace subdirectory must be a real directory")

    kb_root = user_root / "knowledge_base"
    for dirname in DEFAULT_KNOWLEDGE_BASE_DIRS:
        path = kb_root / dirname
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("reserved workspace subdirectory must be a real directory")

    for entry in os.scandir(user_root):
        if not entry.is_dir(follow_symlinks=False):
            continue
        metadata_path = Path(entry.path) / ".workspace.json"
        try:
            metadata = metadata_path.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError("workspace metadata must be a single-link regular file")


@contextmanager
def _bootstrap_lock(user_root: Path) -> Iterator[None]:
    directory_descriptor = -1
    descriptor = -1
    try:
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            directory_flags |= os.O_NOFOLLOW
        directory_descriptor = os.open(user_root, directory_flags)
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(
            ".assistant-defaults.lock",
            flags,
            0o600,
            dir_fd=directory_descriptor,
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError("bootstrap lock must be a single-link regular file")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        after_lock = os.fstat(descriptor)
        if not stat.S_ISREG(after_lock.st_mode) or after_lock.st_nlink != 1:
            raise ValueError("bootstrap lock integrity changed")
        yield
    except OSError as exc:
        raise ValueError("bootstrap lock is unavailable") from exc
    finally:
        if descriptor >= 0:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
        if directory_descriptor >= 0:
            os.close(directory_descriptor)


def _write_json_if_missing(path: Path, payload: dict[str, Any]) -> bool:
    """Create JSON atomically without replacing a user-owned file."""
    encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    if len(encoded) > _MAX_BOOTSTRAP_JSON_BYTES:
        raise ValueError("workspace JSON exceeds the byte limit")

    parent = _validate_workspace_path(path.parent, label="workspace JSON parent")
    directory_descriptor = -1
    temporary_descriptor = -1
    temporary_created = False
    temporary_name = f".{path.name}.{uuid.uuid4().hex}.tmp"
    temporary_metadata: os.stat_result | None = None
    try:
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            directory_flags |= os.O_NOFOLLOW
        directory_descriptor = os.open(parent, directory_flags)
        directory_metadata = os.fstat(directory_descriptor)
        if not stat.S_ISDIR(directory_metadata.st_mode):
            raise ValueError("workspace JSON parent must be a real directory")

        try:
            os.stat(path.name, dir_fd=directory_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            return False

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        temporary_descriptor = os.open(
            temporary_name,
            flags,
            0o600,
            dir_fd=directory_descriptor,
        )
        temporary_created = True
        os.fchmod(temporary_descriptor, 0o600)
        view = memoryview(encoded)
        while view:
            written = os.write(temporary_descriptor, view)
            if written <= 0:
                raise OSError("workspace JSON write made no progress")
            view = view[written:]
        os.fsync(temporary_descriptor)
        temporary_metadata = os.fstat(temporary_descriptor)
        if (
            not stat.S_ISREG(temporary_metadata.st_mode)
            or temporary_metadata.st_nlink != 1
            or temporary_metadata.st_size != len(encoded)
        ):
            raise ValueError("workspace JSON temporary file failed integrity checks")

        try:
            os.link(
                temporary_name,
                path.name,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError:
            return False

        os.close(temporary_descriptor)
        temporary_descriptor = -1
        os.unlink(temporary_name, dir_fd=directory_descriptor)
        temporary_created = False

        destination_metadata = os.stat(
            path.name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if (
            temporary_metadata is None
            or destination_metadata.st_dev != temporary_metadata.st_dev
            or destination_metadata.st_ino != temporary_metadata.st_ino
            or not stat.S_ISREG(destination_metadata.st_mode)
            or destination_metadata.st_nlink != 1
            or destination_metadata.st_size != len(encoded)
        ):
            raise ValueError("workspace JSON destination failed integrity checks")
        os.fsync(directory_descriptor)
        return True
    except FileExistsError as exc:
        raise ValueError("workspace JSON temporary path is unavailable") from exc
    except OSError as exc:
        raise ValueError("workspace JSON could not be created safely") from exc
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


def _has_pinned_workspace(user_root: Path) -> bool:
    for entry in os.scandir(user_root):
        if not entry.is_dir(follow_symlinks=False):
            continue
        meta_path = Path(entry.path) / ".workspace.json"
        if not meta_path.exists():
            continue
        payload = _read_bounded_json(meta_path, label="workspace metadata")
        if payload.get("pinned") is True:
            return True
    return False


def ensure_assistant_user_defaults(
    username: str,
    *,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    """Create the account's defaults once, preserving every existing path/file."""
    root = Path(workspace_root) if workspace_root is not None else default_workspace_root()
    user_root = _safe_user_root(username, root)
    user_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    user_root = _validate_workspace_path(user_root, label="user workspace")
    user_metadata = user_root.lstat()
    if not stat.S_ISDIR(user_metadata.st_mode):
        raise ValueError("user workspace must be a real directory")
    _validate_reserved_paths(user_root)
    marker_path = user_root / BOOTSTRAP_MARKER

    with _bootstrap_lock(user_root):
        _validate_reserved_paths(user_root)
        if marker_path.exists():
            marker = _read_bounded_json(marker_path, label="bootstrap marker")
            marker_version = marker.get("version")
            if (
                type(marker_version) is not int
                or marker_version != BOOTSTRAP_VERSION
            ):
                raise ValueError("bootstrap marker version is unsupported")
            return {"created": False, "user_root": user_root}

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        has_pinned = _has_pinned_workspace(user_root)
        created_workspaces: list[str] = []

        for definition in DEFAULT_WORKSPACES:
            workspace = user_root / str(definition["name"])
            meta_path = workspace / ".workspace.json"
            workspace_preexisted = workspace.exists()
            if workspace_preexisted and not workspace.is_dir():
                continue
            workspace.mkdir(parents=True, exist_ok=True)

            should_pin = bool(definition["prefer_pinned"] and not has_pinned)
            meta_created = _write_json_if_missing(
                meta_path,
                {
                    "desc": definition["desc"],
                    "pinned": should_pin,
                    "created": now,
                    "updated": now,
                    "system_default": True,
                },
            )
            if not meta_created:
                continue
            created_workspaces.append(str(definition["name"]))
            has_pinned = has_pinned or should_pin
            for folder in definition["folders"]:
                (workspace / str(folder)).mkdir(exist_ok=True)

        kb_root = user_root / "knowledge_base"
        kb_root.mkdir(parents=True, exist_ok=True)
        for dirname in DEFAULT_KNOWLEDGE_BASE_DIRS:
            (kb_root / dirname).mkdir(exist_ok=True)

        _write_json_if_missing(
            marker_path,
            {
                "version": BOOTSTRAP_VERSION,
                "created": now,
                "workspaces": created_workspaces,
                "knowledge_base": list(DEFAULT_KNOWLEDGE_BASE_DIRS),
            },
        )
        return {
            "created": True,
            "user_root": user_root,
            "workspaces": created_workspaces,
        }
