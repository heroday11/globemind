"""
JSON 文件存储服务：为知识库、站点、成员、工作区提供轻量持久化。
数据存储在 backend/data/ 目录下。
"""
from __future__ import annotations

import json
import math
import os
import re
import stat
from pathlib import Path
from typing import Any, Dict, List

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_MAX_JSON_BYTES = 1024 * 1024
_MAX_RECORDS = 1000
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.json$")
_FORBIDDEN_RELEASE_ROOT = Path("/root/data/releases/globemind")


def _absolute_lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


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


def _validated_data_root() -> Path:
    root = _absolute_lexical(_DATA_DIR)
    if root.is_relative_to(_FORBIDDEN_RELEASE_ROOT):
        raise ValueError("file store cannot use a production release path")
    if _path_has_symlink_component(root):
        raise ValueError("file store path contains a symbolic link")
    return root


def _ensure_dir() -> Path:
    root = _validated_data_root()
    root.mkdir(parents=True, exist_ok=True)
    root = _validated_data_root()
    metadata = root.lstat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("file store root must be a real directory")
    return root


def _data_path(filename: str, *, create_root: bool) -> Path:
    name = str(filename or "")
    if _SAFE_FILENAME.fullmatch(name) is None or name in {".", ".."}:
        raise ValueError("file store filename is invalid")
    root = _ensure_dir() if create_root else _validated_data_root()
    path = root / name
    if not path.is_relative_to(root):
        raise ValueError("file store path escaped its root")
    return path


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("duplicate JSON key")
        output[key] = value
    return output


def _reject_non_finite_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _parse_finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite JSON number")
    return parsed


def read_json(filename: str) -> List[Dict[str, Any]]:
    descriptor = -1
    try:
        path = _data_path(filename, create_root=False)
        try:
            before = path.lstat()
        except FileNotFoundError:
            return []
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size <= 0
            or before.st_size > _MAX_JSON_BYTES
        ):
            return []
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
            or opened.st_ctime_ns != before.st_ctime_ns
            or opened.st_nlink != 1
            or not stat.S_ISREG(opened.st_mode)
        ):
            return []
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                return []
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            return []
        after = os.fstat(descriptor)
        if (
            after.st_dev != opened.st_dev
            or after.st_ino != opened.st_ino
            or after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
            or after.st_ctime_ns != opened.st_ctime_ns
            or after.st_nlink != 1
        ):
            return []
        payload = json.loads(
            b"".join(chunks).decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_constant,
            parse_float=_parse_finite_float,
        )
        if (
            not isinstance(payload, list)
            or len(payload) > _MAX_RECORDS
            or any(not isinstance(item, dict) for item in payload)
        ):
            return []
        return payload
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, RecursionError):
        return []
    finally:
        if descriptor >= 0:
            os.close(descriptor)
