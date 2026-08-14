"""Append-only, hash-chained storage outside immutable release artifacts."""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from pydantic import ValidationError

from .contracts import (
    STORE_SCHEMA_VERSION,
    EvaluationManifest,
    EvaluationResult,
    StoredEvaluation,
)
from .evaluator import canonical_sha256

MAX_ENTRIES = 10_000
MAX_ENTRY_BYTES = 4 * 1024 * 1024
_FORBIDDEN_RELEASE_ROOT = Path("/root/data/releases/globemind")
_ENTRY_FILE = re.compile(
    r"^(?P<sequence>[0-9]{8})-(?P<evaluation>eval\.[a-z0-9][a-z0-9_.-]{1,119})\.json$"
)


class AssuranceStoreError(RuntimeError):
    pass


class AssuranceStoreUnavailable(AssuranceStoreError):
    pass


class AssuranceConflict(AssuranceStoreError):
    pass


class AssuranceNotFound(AssuranceStoreError):
    pass


def _path_has_symlink(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise AssuranceStoreUnavailable("assurance path is unavailable") from exc
        if stat.S_ISLNK(mode):
            return True
    return False


def _ensure_directory(path: Path, *, mode: int = 0o700) -> None:
    if _path_has_symlink(path):
        raise AssuranceStoreUnavailable("assurance path contains a symbolic link")
    try:
        path.mkdir(parents=True, exist_ok=True, mode=mode)
        if path.is_symlink() or not path.is_dir():
            raise AssuranceStoreUnavailable("assurance directory is invalid")
        os.chmod(path, mode)
    except AssuranceStoreUnavailable:
        raise
    except OSError as exc:
        raise AssuranceStoreUnavailable("assurance directory is unavailable") from exc


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise AssuranceStoreUnavailable("assurance record has duplicate keys")
        output[key] = value
    return output


def _reject_non_finite(_value: str) -> None:
    raise AssuranceStoreUnavailable("non-finite assurance number")


def _read_json(path: Path) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_mode & 0o022
            or metadata.st_size <= 0
            or metadata.st_size > MAX_ENTRY_BYTES
        ):
            raise AssuranceStoreUnavailable("assurance record metadata is invalid")
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise AssuranceStoreUnavailable("assurance record is truncated")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise AssuranceStoreUnavailable("assurance record changed while reading")
        payload = json.loads(
            b"".join(chunks).decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except AssuranceStoreError:
        raise
    except (OSError, TypeError, UnicodeError, ValueError) as exc:
        raise AssuranceStoreUnavailable("assurance record is unreadable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(payload, dict):
        raise AssuranceStoreUnavailable("assurance record root is invalid")
    return payload


def _atomic_no_replace(path: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if not encoded or len(encoded) > MAX_ENTRY_BYTES:
        raise AssuranceStoreUnavailable("assurance record exceeds its size bound")
    _ensure_directory(path.parent)
    descriptor = -1
    temporary = ""
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=".model-assurance-",
            dir=path.parent,
        )
        os.fchmod(descriptor, 0o640)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        os.unlink(temporary)
        temporary = ""
        directory_fd = os.open(
            path.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except (OSError, TypeError, ValueError) as exc:
        raise AssuranceStoreUnavailable("assurance record could not be appended") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


class ModelAssuranceStore:
    """Immutable entry files plus a verified previous-entry hash chain."""

    def __init__(self, root: Path) -> None:
        raw = Path(root)
        if not raw.is_absolute():
            raise AssuranceStoreUnavailable("assurance root must be absolute")
        self.root = Path(os.path.abspath(os.fspath(raw)))
        try:
            self.root.relative_to(_FORBIDDEN_RELEASE_ROOT)
        except ValueError:
            pass
        else:
            raise AssuranceStoreUnavailable(
                "assurance root cannot be inside release evidence"
            )
        if _path_has_symlink(self.root):
            raise AssuranceStoreUnavailable("assurance root contains a symbolic link")
        self.entries_root = self.root / "entries"

    @contextmanager
    def _locked(self) -> Iterator[None]:
        _ensure_directory(self.root)
        lock_path = self.root / ".model-assurance.lock"
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = -1
        try:
            descriptor = os.open(lock_path, flags, 0o600)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise AssuranceStoreUnavailable("assurance lock is invalid")
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        except AssuranceStoreError:
            raise
        except OSError as exc:
            raise AssuranceStoreUnavailable("assurance lock is unavailable") from exc
        finally:
            if descriptor >= 0:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)

    @contextmanager
    def _read_locked(self) -> Iterator[None]:
        lock_path = self.root / ".model-assurance.lock"
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_NONBLOCK"):
            flags |= os.O_NONBLOCK
        descriptor = -1
        try:
            descriptor = os.open(lock_path, flags)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise AssuranceStoreUnavailable("assurance lock is invalid")
            fcntl.flock(descriptor, fcntl.LOCK_SH)
            yield
        except AssuranceStoreError:
            raise
        except OSError as exc:
            raise AssuranceStoreUnavailable("assurance lock is unavailable") from exc
        finally:
            if descriptor >= 0:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)

    def _entry_paths(self) -> list[Path]:
        if not self.root.exists():
            return []
        try:
            root_metadata = self.root.lstat()
        except OSError as exc:
            raise AssuranceStoreUnavailable("assurance root is unreadable") from exc
        if (
            not stat.S_ISDIR(root_metadata.st_mode)
            or root_metadata.st_mode & 0o022
        ):
            raise AssuranceStoreUnavailable("assurance root is invalid")
        allowed_root_entries = {".model-assurance.lock", "entries"}
        try:
            root_entries = list(self.root.iterdir())
        except OSError as exc:
            raise AssuranceStoreUnavailable("assurance root is unreadable") from exc
        if any(entry.name not in allowed_root_entries for entry in root_entries):
            raise AssuranceStoreUnavailable("assurance root contains an unknown entry")
        if not self.entries_root.exists():
            return []
        try:
            entries_metadata = self.entries_root.lstat()
        except OSError as exc:
            raise AssuranceStoreUnavailable(
                "assurance entries directory is unreadable"
            ) from exc
        if (
            not stat.S_ISDIR(entries_metadata.st_mode)
            or entries_metadata.st_mode & 0o022
        ):
            raise AssuranceStoreUnavailable("assurance entries directory is invalid")
        try:
            paths = sorted(self.entries_root.iterdir())
        except OSError as exc:
            raise AssuranceStoreUnavailable("assurance entries are unreadable") from exc
        if len(paths) > MAX_ENTRIES:
            raise AssuranceStoreUnavailable("assurance entry bound was exceeded")
        if any(
            path.is_symlink()
            or not path.is_file()
            or _ENTRY_FILE.fullmatch(path.name) is None
            for path in paths
        ):
            raise AssuranceStoreUnavailable("assurance entries contain an invalid file")
        return paths

    def _read_entries_unlocked(self) -> list[StoredEvaluation]:
        entries: list[StoredEvaluation] = []
        previous_digest: str | None = None
        seen_evaluations: set[str] = set()
        for expected_sequence, path in enumerate(self._entry_paths(), start=1):
            match = _ENTRY_FILE.fullmatch(path.name)
            if match is None:
                raise AssuranceStoreUnavailable("assurance entry filename is invalid")
            try:
                entry = StoredEvaluation.model_validate(_read_json(path))
            except ValidationError as exc:
                raise AssuranceStoreUnavailable(
                    "assurance entry contract is invalid"
                ) from exc
            digest_payload = entry.model_dump(
                mode="json",
                exclude={"entry_sha256"},
            )
            expected_digest = canonical_sha256(digest_payload)
            if (
                entry.storage_schema_version != STORE_SCHEMA_VERSION
                or entry.sequence != expected_sequence
                or int(match.group("sequence")) != expected_sequence
                or match.group("evaluation") != entry.manifest.evaluation_id
                or entry.result.evaluation_id != entry.manifest.evaluation_id
                or entry.result.manifest_sha256
                != canonical_sha256(entry.manifest.model_dump(mode="json"))
                or entry.previous_entry_sha256 != previous_digest
                or entry.entry_sha256 != expected_digest
                or entry.manifest.evaluation_id in seen_evaluations
                or entry.stored_at.tzinfo is None
                or entry.result.evaluated_at.tzinfo is None
                or entry.stored_at != entry.result.evaluated_at
            ):
                raise AssuranceStoreUnavailable("assurance hash chain is invalid")
            entries.append(entry)
            previous_digest = entry.entry_sha256
            seen_evaluations.add(entry.manifest.evaluation_id)
        return entries

    def list_entries(self) -> list[StoredEvaluation]:
        if not self.root.exists():
            return []
        lock_path = self.root / ".model-assurance.lock"
        try:
            lock_exists = lock_path.lstat()
        except FileNotFoundError:
            entries = self._read_entries_unlocked()
            if entries:
                raise AssuranceStoreUnavailable(
                    "assurance entries exist without a lock"
                )
            return []
        except OSError as exc:
            raise AssuranceStoreUnavailable("assurance lock is unavailable") from exc
        if not stat.S_ISREG(lock_exists.st_mode) or lock_exists.st_nlink != 1:
            raise AssuranceStoreUnavailable("assurance lock is invalid")
        with self._read_locked():
            return self._read_entries_unlocked()

    def get_entry(self, evaluation_id: str) -> StoredEvaluation:
        entry = next(
            (
                item
                for item in self.list_entries()
                if item.manifest.evaluation_id == evaluation_id
            ),
            None,
        )
        if entry is None:
            raise AssuranceNotFound("model assurance evaluation was not found")
        return entry

    def append(
        self,
        *,
        manifest: EvaluationManifest,
        result: EvaluationResult,
        submitted_by: str,
        stored_at: datetime,
    ) -> StoredEvaluation:
        if stored_at.tzinfo is None or stored_at.utcoffset() is None:
            raise AssuranceStoreUnavailable("stored_at must include a timezone")
        if (
            result.evaluation_id != manifest.evaluation_id
            or result.manifest_sha256
            != canonical_sha256(manifest.model_dump(mode="json"))
            or result.evaluated_at != stored_at
        ):
            raise AssuranceStoreUnavailable(
                "assurance result does not match its manifest or timestamp"
            )
        with self._locked():
            entries = self._read_entries_unlocked()
            if any(
                item.manifest.evaluation_id == manifest.evaluation_id
                for item in entries
            ):
                raise AssuranceConflict("evaluation id is append-only and already exists")
            if len(entries) >= MAX_ENTRIES:
                raise AssuranceStoreUnavailable("assurance entry bound was exceeded")
            sequence = len(entries) + 1
            payload: dict[str, Any] = {
                "storage_schema_version": STORE_SCHEMA_VERSION,
                "sequence": sequence,
                "stored_at": stored_at,
                "submitted_by": submitted_by,
                "previous_entry_sha256": (
                    entries[-1].entry_sha256 if entries else None
                ),
                "manifest": manifest.model_dump(mode="json"),
                "result": result.model_dump(mode="json"),
            }
            provisional = StoredEvaluation.model_validate(
                {**payload, "entry_sha256": "0" * 64}
            )
            digest_payload = provisional.model_dump(
                mode="json",
                exclude={"entry_sha256"},
            )
            entry = provisional.model_copy(
                update={"entry_sha256": canonical_sha256(digest_payload)}
            )
            _ensure_directory(self.entries_root)
            target = self.entries_root / (
                f"{sequence:08d}-{manifest.evaluation_id}.json"
            )
            _atomic_no_replace(target, entry.model_dump(mode="json"))
            return entry


__all__ = (
    "AssuranceConflict",
    "AssuranceNotFound",
    "AssuranceStoreError",
    "AssuranceStoreUnavailable",
    "MAX_ENTRIES",
    "MAX_ENTRY_BYTES",
    "ModelAssuranceStore",
)
