"""Cross-worker append-only storage for privacy-minimal SLO observations."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from pydantic import ValidationError

from .contracts import (
    FAILURE_SCHEMA_VERSION,
    MEASUREMENT_METHOD_VERSION,
    OBSERVATION_SCHEMA_VERSION,
    STORE_SCHEMA_VERSION,
    ObservationInput,
    Operation,
    StoredObservation,
    StoredWriteFailure,
)

MAX_OBSERVATIONS = 100_000
MAX_WRITE_FAILURES = 10_000
MAX_RECORD_BYTES = 4096
_FORBIDDEN_RELEASE_ROOT = Path("/root/data/releases/globemind")
_ENTRY_FILE = re.compile(r"^(?P<sequence>[0-9]{8})\.json$")
_ALLOWED_ROOT_ENTRIES = frozenset(
    {".service-level.lock", "observations", "write-failures"}
)


class ServiceLevelStoreError(RuntimeError):
    """Base class for bounded service-level storage failures."""


class ServiceLevelStoreUnavailable(ServiceLevelStoreError):
    """The ledger cannot be read or extended without losing integrity."""


def canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(payload),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _path_has_symlink(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ServiceLevelStoreUnavailable(
                "service-level path is unavailable"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode):
            return True
    return False


def _ensure_directory(path: Path, *, mode: int = 0o700) -> None:
    if _path_has_symlink(path):
        raise ServiceLevelStoreUnavailable(
            "service-level path contains a symbolic link"
        )
    try:
        path.mkdir(parents=True, exist_ok=True, mode=mode)
        metadata = path.lstat()
        if not stat.S_ISDIR(metadata.st_mode):
            raise ServiceLevelStoreUnavailable(
                "service-level directory is invalid"
            )
        os.chmod(path, mode)
    except ServiceLevelStoreError:
        raise
    except OSError as exc:
        raise ServiceLevelStoreUnavailable(
            "service-level directory is unavailable"
        ) from exc


def _fsync_directory(path: Path) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        os.fsync(descriptor)
    except OSError as exc:
        raise ServiceLevelStoreUnavailable(
            "service-level directory could not be synchronized"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ServiceLevelStoreUnavailable(
                "service-level record has duplicate JSON keys"
            )
        payload[key] = value
    return payload


def _reject_non_finite(_value: str) -> None:
    raise ServiceLevelStoreUnavailable(
        "service-level record contains a non-finite number"
    )


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
            or metadata.st_size > MAX_RECORD_BYTES
        ):
            raise ServiceLevelStoreUnavailable(
                "service-level record metadata is invalid"
            )
        remaining = metadata.st_size
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise ServiceLevelStoreUnavailable(
                    "service-level record is truncated"
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise ServiceLevelStoreUnavailable(
                "service-level record changed while reading"
            )
        after_open = os.fstat(descriptor)
        after_path = path.stat(follow_symlinks=False)
        if (
            after_open.st_dev != metadata.st_dev
            or after_open.st_ino != metadata.st_ino
            or after_open.st_size != metadata.st_size
            or after_open.st_mtime_ns != metadata.st_mtime_ns
            or after_open.st_nlink != 1
            or after_path.st_dev != after_open.st_dev
            or after_path.st_ino != after_open.st_ino
            or after_path.st_size != after_open.st_size
            or after_path.st_mtime_ns != after_open.st_mtime_ns
            or after_path.st_nlink != 1
        ):
            raise ServiceLevelStoreUnavailable(
                "service-level record changed while reading"
            )
        payload = json.loads(
            b"".join(chunks).decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except ServiceLevelStoreError:
        raise
    except (OSError, TypeError, UnicodeError, ValueError) as exc:
        raise ServiceLevelStoreUnavailable(
            "service-level record is unreadable"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(payload, dict):
        raise ServiceLevelStoreUnavailable(
            "service-level record root is invalid"
        )
    return payload


def _atomic_no_replace(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = json.dumps(
        dict(payload),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if not encoded or len(encoded) > MAX_RECORD_BYTES:
        raise ServiceLevelStoreUnavailable(
            "service-level record exceeds its size bound"
        )
    parent_existed = path.parent.exists()
    _ensure_directory(path.parent)
    if not parent_existed:
        _fsync_directory(path.parent.parent)
    descriptor = -1
    temporary = ""
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=".service-level-",
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
        _fsync_directory(path.parent)
    except (OSError, TypeError, ValueError) as exc:
        raise ServiceLevelStoreUnavailable(
            "service-level record could not be appended"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


class ServiceLevelStore:
    """Two bounded immutable chains protected by a shared filesystem lock."""

    def __init__(self, root: Path) -> None:
        raw = Path(root)
        if not raw.is_absolute():
            raise ServiceLevelStoreUnavailable(
                "service-level root must be absolute"
            )
        self.root = Path(os.path.abspath(os.fspath(raw)))
        try:
            self.root.relative_to(_FORBIDDEN_RELEASE_ROOT)
        except ValueError:
            pass
        else:
            raise ServiceLevelStoreUnavailable(
                "service-level root cannot be inside release evidence"
            )
        if _path_has_symlink(self.root):
            raise ServiceLevelStoreUnavailable(
                "service-level root contains a symbolic link"
            )
        self.observations_root = self.root / "observations"
        self.failures_root = self.root / "write-failures"

    def _validate_root(self) -> None:
        try:
            metadata = self.root.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise ServiceLevelStoreUnavailable(
                "service-level root is unreadable"
            ) from exc
        try:
            entries = list(self.root.iterdir())
        except OSError as exc:
            raise ServiceLevelStoreUnavailable(
                "service-level root is unreadable"
            ) from exc
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_mode & 0o022
            or any(entry.name not in _ALLOWED_ROOT_ENTRIES for entry in entries)
        ):
            raise ServiceLevelStoreUnavailable(
                "service-level root is invalid"
            )

    @contextmanager
    def _write_locked(self) -> Iterator[None]:
        root_existed = self.root.exists()
        _ensure_directory(self.root)
        if not root_existed:
            _fsync_directory(self.root.parent)
        lock_path = self.root / ".service-level.lock"
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = -1
        try:
            descriptor = os.open(lock_path, flags, 0o600)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise ServiceLevelStoreUnavailable(
                    "service-level lock is invalid"
                )
            os.fchmod(descriptor, 0o600)
            _fsync_directory(self.root)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            self._validate_root()
            yield
        except ServiceLevelStoreError:
            raise
        except OSError as exc:
            raise ServiceLevelStoreUnavailable(
                "service-level lock is unavailable"
            ) from exc
        finally:
            if descriptor >= 0:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)

    @contextmanager
    def _read_locked(self) -> Iterator[None]:
        lock_path = self.root / ".service-level.lock"
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = -1
        try:
            descriptor = os.open(lock_path, flags)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise ServiceLevelStoreUnavailable(
                    "service-level lock is invalid"
                )
            fcntl.flock(descriptor, fcntl.LOCK_SH)
            self._validate_root()
            yield
        except ServiceLevelStoreError:
            raise
        except OSError as exc:
            raise ServiceLevelStoreUnavailable(
                "service-level lock is unavailable"
            ) from exc
        finally:
            if descriptor >= 0:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)

    def _record_paths(self, directory: Path, *, maximum: int) -> list[Path]:
        self._validate_root()
        try:
            metadata = directory.lstat()
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise ServiceLevelStoreUnavailable(
                "service-level entries are unreadable"
            ) from exc
        try:
            paths = sorted(directory.iterdir())
        except OSError as exc:
            raise ServiceLevelStoreUnavailable(
                "service-level entries are unreadable"
            ) from exc
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_mode & 0o022:
            raise ServiceLevelStoreUnavailable(
                "service-level entries directory is invalid"
            )
        if len(paths) > maximum:
            raise ServiceLevelStoreUnavailable(
                "service-level record bound was exceeded"
            )
        for path in paths:
            try:
                item_metadata = path.lstat()
            except OSError as exc:
                raise ServiceLevelStoreUnavailable(
                    "service-level entry metadata is unavailable"
                ) from exc
            if (
                not stat.S_ISREG(item_metadata.st_mode)
                or _ENTRY_FILE.fullmatch(path.name) is None
            ):
                raise ServiceLevelStoreUnavailable(
                    "service-level entries contain an invalid file"
                )
        return paths

    def _read_observations_unlocked(self) -> list[StoredObservation]:
        records: list[StoredObservation] = []
        previous_digest: str | None = None
        for expected_sequence, path in enumerate(
            self._record_paths(
                self.observations_root,
                maximum=MAX_OBSERVATIONS,
            ),
            start=1,
        ):
            match = _ENTRY_FILE.fullmatch(path.name)
            try:
                record = StoredObservation.model_validate(_read_json(path))
            except ValidationError as exc:
                raise ServiceLevelStoreUnavailable(
                    "service-level observation contract is invalid"
                ) from exc
            expected_digest = canonical_sha256(
                record.model_dump(mode="json", exclude={"entry_sha256"})
            )
            if (
                match is None
                or int(match.group("sequence")) != expected_sequence
                or record.sequence != expected_sequence
                or record.storage_schema_version != STORE_SCHEMA_VERSION
                or record.observation_schema_version != OBSERVATION_SCHEMA_VERSION
                or record.measurement_method_version != MEASUREMENT_METHOD_VERSION
                or record.previous_entry_sha256 != previous_digest
                or record.entry_sha256 != expected_digest
            ):
                raise ServiceLevelStoreUnavailable(
                    "service-level observation hash chain is invalid"
                )
            records.append(record)
            previous_digest = record.entry_sha256
        return records

    def _read_failures_unlocked(self) -> list[StoredWriteFailure]:
        records: list[StoredWriteFailure] = []
        previous_digest: str | None = None
        for expected_sequence, path in enumerate(
            self._record_paths(
                self.failures_root,
                maximum=MAX_WRITE_FAILURES,
            ),
            start=1,
        ):
            match = _ENTRY_FILE.fullmatch(path.name)
            try:
                record = StoredWriteFailure.model_validate(_read_json(path))
            except ValidationError as exc:
                raise ServiceLevelStoreUnavailable(
                    "service-level write-failure contract is invalid"
                ) from exc
            expected_digest = canonical_sha256(
                record.model_dump(mode="json", exclude={"entry_sha256"})
            )
            if (
                match is None
                or int(match.group("sequence")) != expected_sequence
                or record.sequence != expected_sequence
                or record.failure_schema_version != FAILURE_SCHEMA_VERSION
                or record.measurement_method_version != MEASUREMENT_METHOD_VERSION
                or record.previous_entry_sha256 != previous_digest
                or record.entry_sha256 != expected_digest
            ):
                raise ServiceLevelStoreUnavailable(
                    "service-level write-failure hash chain is invalid"
                )
            records.append(record)
            previous_digest = record.entry_sha256
        return records

    def snapshot(
        self,
    ) -> tuple[list[StoredObservation], list[StoredWriteFailure], bool]:
        """Return fully verified chains without creating or modifying storage."""

        try:
            self.root.lstat()
        except FileNotFoundError:
            return [], [], False
        except OSError as exc:
            raise ServiceLevelStoreUnavailable(
                "service-level root is unreadable"
            ) from exc
        self._validate_root()
        lock_path = self.root / ".service-level.lock"
        try:
            lock_metadata = lock_path.lstat()
        except FileNotFoundError:
            observations = self._read_observations_unlocked()
            failures = self._read_failures_unlocked()
            if observations or failures:
                raise ServiceLevelStoreUnavailable(
                    "service-level entries exist without a lock"
                )
            return [], [], False
        except OSError as exc:
            raise ServiceLevelStoreUnavailable(
                "service-level lock is unavailable"
            ) from exc
        if (
            not stat.S_ISREG(lock_metadata.st_mode)
            or lock_metadata.st_nlink != 1
            or lock_metadata.st_mode & 0o077
        ):
            raise ServiceLevelStoreUnavailable(
                "service-level lock is invalid"
            )
        with self._read_locked():
            return (
                self._read_observations_unlocked(),
                self._read_failures_unlocked(),
                True,
            )

    def append_observation(self, observation: ObservationInput) -> None:
        with self._write_locked():
            records = self._read_observations_unlocked()
            if len(records) >= MAX_OBSERVATIONS:
                raise ServiceLevelStoreUnavailable(
                    "service-level observation bound was exceeded"
                )
            sequence = len(records) + 1
            provisional = StoredObservation(
                sequence=sequence,
                observed_at=observation.observed_at.astimezone(timezone.utc),
                operation=observation.operation,
                outcome=observation.outcome,
                duration_ms=observation.duration_ms,
                previous_entry_sha256=(
                    records[-1].entry_sha256 if records else None
                ),
                entry_sha256="0" * 64,
            )
            record = provisional.model_copy(
                update={
                    "entry_sha256": canonical_sha256(
                        provisional.model_dump(
                            mode="json",
                            exclude={"entry_sha256"},
                        )
                    )
                }
            )
            _atomic_no_replace(
                self.observations_root / f"{sequence:08d}.json",
                record.model_dump(mode="json"),
            )

    def append_write_failure(
        self,
        *,
        operation: Operation,
        failed_at: datetime,
    ) -> None:
        with self._write_locked():
            records = self._read_failures_unlocked()
            if len(records) >= MAX_WRITE_FAILURES:
                raise ServiceLevelStoreUnavailable(
                    "service-level write-failure bound was exceeded"
                )
            sequence = len(records) + 1
            provisional = StoredWriteFailure(
                sequence=sequence,
                failed_at=failed_at.astimezone(timezone.utc),
                operation=operation,
                previous_entry_sha256=(
                    records[-1].entry_sha256 if records else None
                ),
                entry_sha256="0" * 64,
            )
            record = provisional.model_copy(
                update={
                    "entry_sha256": canonical_sha256(
                        provisional.model_dump(
                            mode="json",
                            exclude={"entry_sha256"},
                        )
                    )
                }
            )
            _atomic_no_replace(
                self.failures_root / f"{sequence:08d}.json",
                record.model_dump(mode="json"),
            )


__all__ = (
    "MAX_OBSERVATIONS",
    "MAX_RECORD_BYTES",
    "MAX_WRITE_FAILURES",
    "ServiceLevelStore",
    "ServiceLevelStoreError",
    "ServiceLevelStoreUnavailable",
    "canonical_sha256",
)
