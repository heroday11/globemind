"""Durable repository for research projects inside the configured workspace root.

The existing assistant file endpoints deliberately keep their path and locking
helpers private.  This repository therefore shares the same configured storage
root, while isolating workflow state in a service-owned directory.  It never
falls back to process memory.
"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import hmac
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol, TypeVar

from api.core.environment import int_setting, string_setting

from .contracts import ResearchProject

PROJECT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
# ``+`` is intentionally outside the assistant account-name allowlist.  The
# schedule scanner enumerates workspace-root children as candidate usernames;
# this service directory must never be mistaken for an account sandbox.
STORE_DIRECTORY = ".research-workflow-v1+store"
STATE_FILENAME = "state.json"
MAX_REPOSITORY_PROJECT_ENTRIES = 1000
_FORBIDDEN_RELEASE_ROOT = Path("/root/data/releases/globemind")
_MutationResult = TypeVar("_MutationResult")


def _path_has_symlink(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("research project JSON contains duplicate keys")
        result[key] = value
    return result


def _canonical_timestamp(value: Any) -> tuple[str, datetime]:
    raw = value if isinstance(value, str) else ""
    normalized = raw.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("project history timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError("project history timestamp requires a timezone")
    utc_value = parsed.astimezone(timezone.utc)
    canonical = utc_value.isoformat().replace("+00:00", "Z")
    if raw != canonical:
        raise ValueError("project history timestamp is not canonical UTC")
    return canonical, utc_value


class ResearchRepositoryError(RuntimeError):
    """Base class for safe repository failures."""


class ResearchRepositoryUnavailable(ResearchRepositoryError):
    """The configured durable store cannot be safely used."""


class ResearchProjectNotFound(ResearchRepositoryError):
    """No safely readable project exists for the identifier."""


class ResearchVersionConflict(ResearchRepositoryError):
    def __init__(self, *, expected: int, actual: int) -> None:
        super().__init__(
            f"project version conflict: expected {expected}, current {actual}"
        )
        self.expected = expected
        self.actual = actual


class ResearchRepositoryCapacityExceeded(ResearchRepositoryError):
    """A project reached the configured durable-state size ceiling."""


class ResearchProjectRepository(Protocol):
    """Persistence boundary; implementations must be durable or unavailable."""

    def availability(self) -> tuple[bool, str | None]: ...

    def create(self, project: dict[str, Any]) -> dict[str, Any]: ...

    def get(self, project_id: str) -> dict[str, Any]: ...

    def list_projects(self) -> list[dict[str, Any]]: ...

    def mutate(
        self,
        project_id: str,
        *,
        expected_version: int,
        mutation: Callable[
            [dict[str, Any]],
            tuple[dict[str, Any], _MutationResult],
        ],
    ) -> _MutationResult: ...


def configured_research_repository() -> "WorkspaceResearchRepository":
    return WorkspaceResearchRepository(
        Path(string_setting("GLOBEMIND_WORKSPACE_ROOT", "/root/data/workspace")),
        max_state_bytes=int_setting(
            "RESEARCH_PROJECT_MAX_STATE_BYTES",
            32 * 1024 * 1024,
            minimum=1024 * 1024,
        ),
        max_projects_per_owner=int_setting(
            "RESEARCH_MAX_PROJECTS_PER_OWNER",
            50,
            minimum=1,
        ),
    )


class WorkspaceResearchRepository:
    """Atomic JSON repository in an isolated directory under workspace storage."""

    def __init__(
        self,
        workspace_root: Path,
        *,
        max_state_bytes: int = 32 * 1024 * 1024,
        max_projects_per_owner: int = 50,
    ) -> None:
        raw_root = Path(workspace_root)
        if not raw_root.is_absolute():
            raise ResearchRepositoryUnavailable("WORKSPACE_ROOT_NOT_ABSOLUTE")
        self.workspace_root = Path(os.path.abspath(os.fspath(raw_root)))
        if _path_has_symlink(self.workspace_root):
            raise ResearchRepositoryUnavailable("WORKSPACE_ROOT_SYMLINK_REJECTED")
        try:
            self.workspace_root.relative_to(_FORBIDDEN_RELEASE_ROOT)
        except ValueError:
            pass
        else:
            raise ResearchRepositoryUnavailable("WORKSPACE_ROOT_IN_RELEASE")
        self.root = self.workspace_root / STORE_DIRECTORY
        self.max_state_bytes = max(1024, int(max_state_bytes))
        self.max_projects_per_owner = max(1, int(max_projects_per_owner))

    @property
    def lock_root(self) -> Path:
        return self.root / ".locks"

    def availability(self) -> tuple[bool, str | None]:
        try:
            if _path_has_symlink(self.workspace_root):
                return False, "WORKSPACE_ROOT_SYMLINK_REJECTED"
            if self.workspace_root.exists():
                if not self.workspace_root.is_dir():
                    return False, "WORKSPACE_ROOT_NOT_DIRECTORY"
                if self.workspace_root.is_symlink():
                    return False, "WORKSPACE_ROOT_SYMLINK_REJECTED"
                if not os.access(self.workspace_root, os.R_OK | os.W_OK | os.X_OK):
                    return False, "WORKSPACE_ROOT_NOT_READ_WRITE"
            else:
                parent = self.workspace_root.parent
                if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK):
                    return False, "WORKSPACE_ROOT_PARENT_NOT_WRITABLE"
            try:
                root_metadata = self.root.stat(follow_symlinks=False)
            except FileNotFoundError:
                root_metadata = None
            if root_metadata is not None and not stat.S_ISDIR(root_metadata.st_mode):
                return False, "RESEARCH_STORE_UNSAFE"
            try:
                lock_metadata = self.lock_root.stat(follow_symlinks=False)
            except FileNotFoundError:
                lock_metadata = None
            if lock_metadata is not None and not stat.S_ISDIR(lock_metadata.st_mode):
                return False, "RESEARCH_STORE_LOCK_ROOT_UNSAFE"
        except OSError:
            return False, "RESEARCH_STORE_PROBE_FAILED"
        return True, None

    def _read_availability(self) -> tuple[bool, str | None]:
        """Probe only the guarantees needed by GET/list; never require writes."""

        try:
            if _path_has_symlink(self.workspace_root):
                return False, "WORKSPACE_ROOT_SYMLINK_REJECTED"
            try:
                workspace_metadata = self.workspace_root.stat(follow_symlinks=False)
            except FileNotFoundError:
                return True, None
            if not stat.S_ISDIR(workspace_metadata.st_mode):
                return False, "WORKSPACE_ROOT_NOT_DIRECTORY"
            if not os.access(self.workspace_root, os.R_OK | os.X_OK):
                return False, "WORKSPACE_ROOT_NOT_READABLE"
            try:
                root_metadata = self.root.stat(follow_symlinks=False)
            except FileNotFoundError:
                return True, None
            if not stat.S_ISDIR(root_metadata.st_mode) or self.root.is_symlink():
                return False, "RESEARCH_STORE_UNSAFE"
            if not os.access(self.root, os.R_OK | os.X_OK):
                return False, "RESEARCH_STORE_NOT_READABLE"
        except OSError:
            return False, "RESEARCH_STORE_PROBE_FAILED"
        return True, None

    def _ensure_root(self) -> None:
        available, reason = self.availability()
        if not available:
            raise ResearchRepositoryUnavailable(reason or "RESEARCH_STORE_UNAVAILABLE")
        try:
            self.workspace_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.root.mkdir(exist_ok=True, mode=0o700)
            self.lock_root.mkdir(exist_ok=True, mode=0o700)
            for directory in (self.workspace_root, self.root, self.lock_root):
                metadata = directory.stat(follow_symlinks=False)
                if not stat.S_ISDIR(metadata.st_mode) or directory.is_symlink():
                    raise ResearchRepositoryUnavailable(
                        "RESEARCH_STORE_LOCK_ROOT_UNSAFE"
                        if directory == self.lock_root
                        else "RESEARCH_STORE_UNSAFE"
                    )
            os.chmod(self.root, 0o700)
            os.chmod(self.lock_root, 0o700)
        except ResearchRepositoryUnavailable:
            raise
        except OSError as exc:
            raise ResearchRepositoryUnavailable(
                "RESEARCH_STORE_INITIALIZATION_FAILED"
            ) from exc

    def _project_dir(self, project_id: str) -> Path:
        clean = str(project_id or "").strip().lower()
        if not PROJECT_ID_RE.fullmatch(clean):
            raise ResearchProjectNotFound("research project not found")
        return self.root / clean

    def _state_path(self, project_id: str) -> Path:
        return self._project_dir(project_id) / STATE_FILENAME

    @contextmanager
    def _lock(self, name: str) -> Iterator[None]:
        self._ensure_root()
        lock_path = self.lock_root / f"{name}.lock"
        try:
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(lock_path, flags, 0o600)
        except OSError as exc:
            raise ResearchRepositoryUnavailable("RESEARCH_STORE_LOCK_FAILED") from exc
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise ResearchRepositoryUnavailable("RESEARCH_STORE_LOCK_UNSAFE")
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            locked = os.fstat(descriptor)
            path_metadata = lock_path.stat(follow_symlinks=False)
            if (
                not stat.S_ISREG(locked.st_mode)
                or locked.st_nlink != 1
                or locked.st_dev != opened.st_dev
                or locked.st_ino != opened.st_ino
                or path_metadata.st_dev != locked.st_dev
                or path_metadata.st_ino != locked.st_ino
                or path_metadata.st_nlink != 1
            ):
                raise ResearchRepositoryUnavailable("RESEARCH_STORE_LOCK_UNSAFE")
            yield
        except OSError as exc:
            raise ResearchRepositoryUnavailable("RESEARCH_STORE_LOCK_FAILED") from exc
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    @staticmethod
    def _reject_non_finite(value: str) -> None:
        raise ValueError(f"non-finite number is forbidden: {value}")

    @staticmethod
    def _canonical_sha256(payload: Any) -> str:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def _validate_project_integrity(cls, project: dict[str, Any]) -> None:
        state_payload = dict(project)
        claimed_state_sha256 = state_payload.pop("state_integrity_sha256")
        if not hmac.compare_digest(
            claimed_state_sha256,
            cls._canonical_sha256(state_payload),
        ):
            raise ValueError("project state integrity digest does not match")
        version = int(project["version"])
        changes = project["change_history"]
        audit_events = project["audit_events"]
        if len(changes) != version or len(audit_events) != version:
            raise ValueError("project history length does not match project version")
        if len({item["change_id"] for item in changes}) != len(changes):
            raise ValueError("project change identifiers are not unique")
        if len({item["event_id"] for item in audit_events}) != len(audit_events):
            raise ValueError("project audit identifiers are not unique")
        previous_change_sha256: str | None = None
        previous_event_sha256: str | None = None
        previous_timestamp: datetime | None = None
        for index, (change, event) in enumerate(
            zip(changes, audit_events, strict=True), start=1
        ):
            previous = None if index == 1 else index - 1
            if change["version"] != index or change["previous_version"] != previous:
                raise ValueError("project change history is not contiguous")
            if event["version"] != index or event["previous_version"] != previous:
                raise ValueError("project audit history is not contiguous")
            if event["project_id"] != project["id"]:
                raise ValueError("audit event belongs to another project")
            _canonical, timestamp = _canonical_timestamp(change["timestamp"])
            if previous_timestamp is not None and timestamp < previous_timestamp:
                raise ValueError("project history timestamps are not monotonic")
            previous_timestamp = timestamp
            change_payload = dict(change)
            claimed_change_sha256 = change_payload.pop("change_sha256")
            valid_change_link = (
                change_payload["previous_change_sha256"] == previous_change_sha256
            )
            valid_change_digest = hmac.compare_digest(
                claimed_change_sha256, cls._canonical_sha256(change_payload)
            )
            if not valid_change_link or not valid_change_digest:
                raise ValueError("project change hash chain is invalid")
            previous_change_sha256 = claimed_change_sha256
            event_payload = dict(event)
            claimed_event_sha256 = event_payload.pop("event_sha256")
            valid_event_link = (
                event_payload["previous_event_sha256"] == previous_event_sha256
            )
            valid_event_digest = hmac.compare_digest(
                claimed_event_sha256, cls._canonical_sha256(event_payload)
            )
            if not valid_event_link or not valid_event_digest:
                raise ValueError("project audit hash chain is invalid")
            previous_event_sha256 = claimed_event_sha256
            for key in ("actor", "timestamp", "action", "resource_type", "resource_id"):
                if event[key] != change[key]:
                    raise ValueError("audit and change history records diverge")
            reason = change["reason"]
            if event["reason_length"] != len(reason) or not hmac.compare_digest(
                event["reason_sha256"], hashlib.sha256(reason.encode("utf-8")).hexdigest()
            ):
                raise ValueError("audit reason digest does not match change history")
        if project["created_at"] != changes[0]["timestamp"]:
            raise ValueError("project creation timestamp does not match history")
        if project["updated_at"] != changes[-1]["timestamp"]:
            raise ValueError("project update timestamp does not match history")

        owner_members = [
            member
            for member in project["members"]
            if member["username"] == project["owner"] and member["role"] == "owner"
        ]
        member_names = [member["username"] for member in project["members"]]
        if len(owner_members) != 1 or len(member_names) != len(set(member_names)):
            raise ValueError("project membership integrity is invalid")
        if any(
            member["role"] == "owner" and member["username"] != project["owner"]
            for member in project["members"]
        ):
            raise ValueError("project has an unauthorized additional owner")

        identifiers: list[str] = []
        for collection in (
            "research_questions",
            "saved_searches",
            "evidence_items",
            "information_gaps",
            "alternative_hypotheses",
            "judgments",
            "human_decisions",
            "reviews",
        ):
            identifiers.extend(str(item["id"]) for item in project[collection])
        identifiers.extend(
            str(item["manifest_id"]) for item in project["export_manifests"]
        )
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("project resource identifiers are not unique")

        for expected_export_version, manifest in enumerate(
            project["export_manifests"], start=1
        ):
            if manifest["project_id"] != project["id"]:
                raise ValueError("export manifest belongs to another project")
            if manifest["export_version"] != expected_export_version:
                raise ValueError("export manifest versions are not contiguous")
            integrity_payload = dict(manifest)
            claimed_digest = integrity_payload.pop("integrity_sha256")
            if not hmac.compare_digest(
                claimed_digest, cls._canonical_sha256(integrity_payload)
            ):
                raise ValueError("export manifest integrity digest does not match")
            matching_change = next(
                (
                    change
                    for change in changes
                    if change["action"] == "export_manifest.created"
                    and change["resource_id"] == manifest["manifest_id"]
                ),
                None,
            )
            if (
                matching_change is None
                or matching_change["version"] != manifest["project_version"]
                or matching_change["previous_version"]
                != manifest["previous_project_version"]
            ):
                raise ValueError("export manifest version lineage is invalid")

    def _read_state_path(self, path: Path) -> dict[str, Any]:
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ResearchRepositoryUnavailable("RESEARCH_PROJECT_PATH_ESCAPE") from exc
        if _path_has_symlink(path):
            raise ResearchRepositoryUnavailable("RESEARCH_PROJECT_SYMLINK_REJECTED")
        try:
            metadata = path.stat(follow_symlinks=False)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_size <= 0
            ):
                raise ResearchRepositoryUnavailable(
                    "RESEARCH_PROJECT_STATE_PATH_INVALID"
                )
            if metadata.st_size > self.max_state_bytes:
                raise ResearchRepositoryCapacityExceeded(
                    "RESEARCH_PROJECT_STATE_CAPACITY_EXCEEDED"
                )
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(path, flags)
            try:
                opened = os.fstat(descriptor)
                if (
                    opened.st_dev != metadata.st_dev
                    or opened.st_ino != metadata.st_ino
                    or opened.st_size != metadata.st_size
                    or opened.st_mtime_ns != metadata.st_mtime_ns
                    or opened.st_nlink != 1
                    or not stat.S_ISREG(opened.st_mode)
                ):
                    raise ResearchRepositoryUnavailable(
                        "RESEARCH_PROJECT_STATE_CHANGED"
                    )
                chunks: list[bytes] = []
                remaining = metadata.st_size
                while remaining:
                    chunk = os.read(descriptor, min(64 * 1024, remaining))
                    if not chunk:
                        raise ResearchRepositoryUnavailable(
                            "RESEARCH_PROJECT_STATE_CHANGED"
                        )
                    chunks.append(chunk)
                    remaining -= len(chunk)
                if os.read(descriptor, 1):
                    raise ResearchRepositoryUnavailable(
                        "RESEARCH_PROJECT_STATE_CHANGED"
                    )
                after_open = os.fstat(descriptor)
            finally:
                os.close(descriptor)
            after_path = path.stat(follow_symlinks=False)
            if (
                after_open.st_dev != opened.st_dev
                or after_open.st_ino != opened.st_ino
                or after_open.st_size != opened.st_size
                or after_open.st_mtime_ns != opened.st_mtime_ns
                or after_open.st_dev != after_path.st_dev
                or after_open.st_ino != after_path.st_ino
                or after_open.st_size != after_path.st_size
                or after_open.st_mtime_ns != after_path.st_mtime_ns
                or after_path.st_nlink != 1
            ):
                raise ResearchRepositoryUnavailable(
                    "RESEARCH_PROJECT_STATE_CHANGED"
                )
            raw = json.loads(
                b"".join(chunks).decode("utf-8", errors="strict"),
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=self._reject_non_finite,
            )
            project = ResearchProject.model_validate(raw)
        except ResearchRepositoryCapacityExceeded:
            raise
        except FileNotFoundError as exc:
            raise ResearchProjectNotFound("research project not found") from exc
        except (OSError, UnicodeError, ValueError) as exc:
            raise ResearchRepositoryUnavailable(
                "RESEARCH_PROJECT_STATE_INVALID"
            ) from exc
        serialized = project.model_dump(mode="json")
        try:
            if serialized["id"] != path.parent.name:
                raise ValueError("project identifier does not match its storage directory")
            self._validate_project_integrity(serialized)
        except ValueError as exc:
            raise ResearchRepositoryUnavailable(
                "RESEARCH_PROJECT_INTEGRITY_INVALID"
            ) from exc
        return serialized

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _write_state(self, path: Path, project: dict[str, Any]) -> None:
        validated = ResearchProject.model_validate(project).model_dump(mode="json")
        self._validate_project_integrity(validated)
        serialized = json.dumps(
            validated,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ) + "\n"
        if len(serialized.encode("utf-8")) > self.max_state_bytes:
            raise ResearchRepositoryCapacityExceeded(
                "RESEARCH_PROJECT_STATE_CAPACITY_EXCEEDED"
            )
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = -1
        temporary: Path | None = None
        try:
            descriptor, raw_temporary = tempfile.mkstemp(
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
            )
            temporary = Path(raw_temporary)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = -1
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
            temporary = None
            self._fsync_directory(path.parent)
        except OSError as exc:
            raise ResearchRepositoryUnavailable("RESEARCH_PROJECT_WRITE_FAILED") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def create(self, project: dict[str, Any]) -> dict[str, Any]:
        validated = ResearchProject.model_validate(project).model_dump(mode="json")
        project_id = validated["id"]
        project_dir = self._project_dir(project_id)
        with self._lock("repository"):
            if project_dir.exists():
                raise ResearchRepositoryUnavailable("RESEARCH_PROJECT_ID_COLLISION")
            owned_projects = 0
            for state_path in sorted(self.root.glob(f"*/{STATE_FILENAME}")):
                if not PROJECT_ID_RE.fullmatch(state_path.parent.name):
                    continue
                if self._read_state_path(state_path)["owner"] == validated["owner"]:
                    owned_projects += 1
            if owned_projects >= self.max_projects_per_owner:
                raise ResearchRepositoryCapacityExceeded(
                    "RESEARCH_PROJECT_OWNER_CAPACITY_EXCEEDED"
                )
            staging = self.root / f".{project_id}.{uuid.uuid4().hex}.tmp"
            try:
                staging.mkdir(mode=0o700)
                self._write_state(staging / STATE_FILENAME, validated)
                os.replace(staging, project_dir)
                self._fsync_directory(self.root)
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                raise
        return copy.deepcopy(validated)

    def get(self, project_id: str) -> dict[str, Any]:
        available, reason = self._read_availability()
        if not available:
            raise ResearchRepositoryUnavailable(reason or "RESEARCH_STORE_UNAVAILABLE")
        if not self.root.exists():
            raise ResearchProjectNotFound("research project not found")
        return self._read_state_path(self._state_path(project_id))

    def list_projects(self) -> list[dict[str, Any]]:
        available, reason = self._read_availability()
        if not available:
            raise ResearchRepositoryUnavailable(reason or "RESEARCH_STORE_UNAVAILABLE")
        if not self.root.exists():
            return []
        try:
            entries: list[Path] = []
            with os.scandir(self.root) as iterator:
                for entry in iterator:
                    entries.append(Path(entry.path))
                    if len(entries) > MAX_REPOSITORY_PROJECT_ENTRIES + 1:
                        raise ResearchRepositoryUnavailable(
                            "RESEARCH_STORE_INVENTORY_LIMIT_EXCEEDED"
                        )
            entries.sort(key=lambda path: path.name)
        except ResearchRepositoryUnavailable:
            raise
        except OSError as exc:
            raise ResearchRepositoryUnavailable("RESEARCH_STORE_LIST_FAILED") from exc
        projects: list[dict[str, Any]] = []
        for entry in entries:
            if entry.name == ".locks":
                if entry.is_symlink() or not entry.is_dir():
                    raise ResearchRepositoryUnavailable("RESEARCH_STORE_LOCK_ROOT_UNSAFE")
                continue
            if (
                PROJECT_ID_RE.fullmatch(entry.name) is None
                or entry.is_symlink()
                or not entry.is_dir()
            ):
                raise ResearchRepositoryUnavailable("RESEARCH_STORE_INVENTORY_INVALID")
            try:
                children: list[Path] = []
                with os.scandir(entry) as iterator:
                    for child in iterator:
                        children.append(Path(child.path))
                        if len(children) > 1:
                            break
                children.sort(key=lambda path: path.name)
            except OSError as exc:
                raise ResearchRepositoryUnavailable(
                    "RESEARCH_PROJECT_INVENTORY_UNREADABLE"
                ) from exc
            if len(children) != 1 or children[0].name != STATE_FILENAME:
                raise ResearchRepositoryUnavailable(
                    "RESEARCH_PROJECT_INVENTORY_INVALID"
                )
            projects.append(self._read_state_path(children[0]))
            if len(projects) > MAX_REPOSITORY_PROJECT_ENTRIES:
                raise ResearchRepositoryUnavailable(
                    "RESEARCH_STORE_INVENTORY_LIMIT_EXCEEDED"
                )
        return projects

    def mutate(
        self,
        project_id: str,
        *,
        expected_version: int,
        mutation: Callable[
            [dict[str, Any]],
            tuple[dict[str, Any], _MutationResult],
        ],
    ) -> _MutationResult:
        self._project_dir(project_id)
        with self._lock(project_id):
            path = self._state_path(project_id)
            current = self._read_state_path(path)
            actual = int(current["version"])
            if actual != expected_version:
                raise ResearchVersionConflict(expected=expected_version, actual=actual)
            next_project, result = mutation(copy.deepcopy(current))
            validated = ResearchProject.model_validate(next_project).model_dump(
                mode="json"
            )
            if int(validated["version"]) != actual + 1:
                raise ResearchRepositoryUnavailable("RESEARCH_VERSION_CHAIN_INVALID")
            self._write_state(path, validated)
            return copy.deepcopy(result)


__all__ = (
    "MAX_REPOSITORY_PROJECT_ENTRIES",
    "ResearchProjectNotFound",
    "ResearchProjectRepository",
    "ResearchRepositoryCapacityExceeded",
    "ResearchRepositoryUnavailable",
    "ResearchVersionConflict",
    "WorkspaceResearchRepository",
    "configured_research_repository",
)
