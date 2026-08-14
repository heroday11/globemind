from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

_MutationResult = TypeVar("_MutationResult")


class JsonStoreError(RuntimeError):
    """An existing store cannot be mutated without risking data loss."""


class JsonListStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @property
    def lock_path(self) -> Path:
        return self.path.with_name(f".{self.path.name}.lock")

    @contextmanager
    def lock(self, *, exclusive: bool, create: bool = True) -> Iterator[None]:
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = (os.O_RDWR if create or exclusive else os.O_RDONLY) | getattr(
            os,
            "O_CLOEXEC",
            0,
        )
        if create:
            flags |= os.O_CREAT
        lock_fd = os.open(self.lock_path, flags, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            yield
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)

    def read_unlocked(self, *, strict: bool) -> list[dict[str, Any]]:
        try:
            raw = json.loads(
                self.path.read_text(encoding="utf-8"),
                parse_constant=_reject_non_finite_constant,
            )
        except FileNotFoundError:
            return []
        except (OSError, UnicodeError, ValueError) as exc:
            if strict:
                raise JsonStoreError(f"cannot safely read JSON store: {self.path}") from exc
            return []
        if not isinstance(raw, list) or any(not isinstance(row, dict) for row in raw):
            if strict:
                raise JsonStoreError(f"JSON store must contain an object array: {self.path}")
            return []
        return raw

    def read(self) -> list[dict[str, Any]]:
        try:
            with self.lock(exclusive=False, create=False):
                return self.read_unlocked(strict=False)
        except FileNotFoundError:
            return self.read_unlocked(strict=False)

    def write_unlocked(self, rows: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = -1
        temporary: Path | None = None
        try:
            descriptor, raw_temporary = tempfile.mkstemp(
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
            )
            temporary = Path(raw_temporary)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = -1
                json.dump(
                    rows,
                    handle,
                    ensure_ascii=False,
                    indent=2,
                    allow_nan=False,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            temporary = None
            _fsync_directory(self.path.parent)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary is not None:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass

    def write(self, rows: list[dict[str, Any]]) -> None:
        with self.lock(exclusive=True):
            self.write_unlocked(rows)

    def mutate(
        self,
        mutation: Callable[
            [list[dict[str, Any]]],
            tuple[list[dict[str, Any]], _MutationResult],
        ],
        *,
        write_if_unchanged: bool = True,
    ) -> _MutationResult:
        with self.lock(exclusive=True):
            rows = self.read_unlocked(strict=True)
            next_rows, result = mutation(rows)
            if write_if_unchanged or next_rows != rows or not self.path.exists():
                self.write_unlocked(next_rows)
            return result


def store_lock_path(path: Path) -> Path:
    return JsonListStore(path).lock_path


@contextmanager
def json_store_lock(path: Path, *, exclusive: bool, create: bool = True) -> Iterator[None]:
    with JsonListStore(path).lock(exclusive=exclusive, create=create):
        yield


def read_json_list_unlocked(path: Path, *, strict: bool) -> list[dict[str, Any]]:
    return JsonListStore(path).read_unlocked(strict=strict)


def read_json_list(path: Path) -> list[dict[str, Any]]:
    return JsonListStore(path).read()


def write_json_list_unlocked(path: Path, rows: list[dict[str, Any]]) -> None:
    JsonListStore(path).write_unlocked(rows)


def write_json_list(path: Path, rows: list[dict[str, Any]]) -> None:
    JsonListStore(path).write(rows)


def mutate_json_list(
    path: Path,
    mutation: Callable[
        [list[dict[str, Any]]],
        tuple[list[dict[str, Any]], _MutationResult],
    ],
) -> _MutationResult:
    return JsonListStore(path).mutate(mutation)


def fsync_directory(path: Path) -> None:
    _fsync_directory(path)


def _fsync_directory(path: Path) -> None:
    directory_fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _reject_non_finite_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")
