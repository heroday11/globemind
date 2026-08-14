from __future__ import annotations

import fcntl
import json
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, TextIO

_FORBIDDEN_RELEASE_ROOT = Path("/root/data/releases/globemind")
MAX_MUTABLE_JSON_BYTES = 16 * 1024 * 1024


def _validate_path(path: Path) -> Path:
    normalized = Path(os.path.abspath(os.fspath(path)))
    if not Path(path).is_absolute():
        raise OSError("operations storage path must be absolute")
    try:
        normalized.relative_to(_FORBIDDEN_RELEASE_ROOT)
    except ValueError:
        pass
    else:
        raise OSError("operations storage cannot use release evidence")
    current = Path(normalized.anchor)
    for part in normalized.parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            raise OSError("operations storage path contains a symbolic link")
    return normalized


def _validate_open_file(path: Path, descriptor: int, *, private: bool) -> os.stat_result:
    metadata = os.fstat(descriptor)
    path_metadata = path.stat(follow_symlinks=False)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or path_metadata.st_dev != metadata.st_dev
        or path_metadata.st_ino != metadata.st_ino
        or path_metadata.st_nlink != 1
        or (private and metadata.st_mode & 0o077)
    ):
        raise OSError("operations storage file metadata is invalid")
    return metadata


@contextmanager
def exclusive_file_lock(path: Path) -> Iterator[TextIO]:
    lock_path = _validate_path(Path(path))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    _validate_path(lock_path.parent)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        _validate_open_file(lock_path, descriptor, private=True)
        os.fchmod(descriptor, 0o600)
        lock = os.fdopen(descriptor, "a+", encoding="utf-8", closefd=False)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield lock
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()
    finally:
        os.close(descriptor)


@contextmanager
def shared_file_lock(path: Path) -> Iterator[None]:
    """Open an existing private lock without creating any filesystem state."""

    lock_path = _validate_path(Path(path))
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags)
    try:
        _validate_open_file(lock_path, descriptor, private=True)
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        try:
            _validate_open_file(lock_path, descriptor, private=True)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError("operations storage JSON contains duplicate keys")
        payload[key] = value
    return payload


def _reject_non_finite(_value: str) -> None:
    raise ValueError("operations storage JSON contains a non-finite number")


def read_json_bounded(path: Path, *, maximum_bytes: int = MAX_MUTABLE_JSON_BYTES) -> Any:
    target = _validate_path(Path(path))
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(target, flags)
    try:
        metadata = _validate_open_file(target, descriptor, private=True)
        if metadata.st_size <= 0 or metadata.st_size > maximum_bytes:
            raise OSError("operations storage JSON size is invalid")
        remaining = metadata.st_size
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise OSError("operations storage JSON is truncated")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise OSError("operations storage JSON changed while reading")
        after = _validate_open_file(target, descriptor, private=True)
        if after.st_size != metadata.st_size or after.st_mtime_ns != metadata.st_mtime_ns:
            raise OSError("operations storage JSON changed while reading")
        return json.loads(
            b"".join(chunks).decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    finally:
        os.close(descriptor)


def atomic_write_json(path: Path, payload: Any) -> None:
    target = _validate_path(Path(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    _validate_path(target.parent)
    try:
        existing = target.lstat()
    except FileNotFoundError:
        existing = None
    if existing is not None and (
        not stat.S_ISREG(existing.st_mode)
        or existing.st_nlink != 1
        or existing.st_mode & 0o077
    ):
        raise OSError("operations storage target metadata is invalid")
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    if len(encoded) > MAX_MUTABLE_JSON_BYTES:
        raise OSError("operations storage JSON exceeds its size bound")
    fd, temporary = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        directory_fd = os.open(
            target.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


__all__ = (
    "MAX_MUTABLE_JSON_BYTES",
    "atomic_write_json",
    "exclusive_file_lock",
    "read_json_bounded",
    "shared_file_lock",
)
