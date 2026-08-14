"""Strictly read-only, subject-confined assistant privacy export adapters."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import quote

from api.features.assistant.config import load_assistant_schedule_settings

ASSISTANT_WORKSPACE_EXPORT_SCHEMA_VERSION = "assistant-workspace-export-v1"
ASSISTANT_AUTOMATION_EXPORT_SCHEMA_VERSION = "assistant-automation-export-v1"
MAX_WORKSPACES = 50
MAX_WORKSPACE_FILE_ITEMS = 5000
MAX_SCHEDULES = 500
MAX_REPORT_REFERENCES = 5000
MAX_WORKSPACE_META_BYTES = 64 * 1024
MAX_SCHEDULE_SOURCE_BYTES = 8 * 1024 * 1024
MAX_RELATIVE_PATH_BYTES = 1024
MAX_PATH_DEPTH = 16
MAX_DIRECTORY_SCAN_ENTRIES = 10_000
MAX_EXPORT_ITEM_BYTES = 8 * 1024
MAX_EXPORT_TOTAL_BYTES = 2 * 1024 * 1024
MAX_HASH_FILE_BYTES = 25 * 1024 * 1024
MAX_HASH_TOTAL_BYTES = 128 * 1024 * 1024
_DATA_BUDGET_BYTES = MAX_EXPORT_TOTAL_BYTES - (64 * 1024)
_SAFE_USERNAME = re.compile(r"^[A-Za-z0-9_.@-]{1,96}$")
_SAFE_SCHEDULE_ID = re.compile(r"^[A-Za-z0-9_.@-]{1,128}$")
_FORBIDDEN_RELEASE_ROOT = Path("/root/data/releases/globemind")
_SCHEDULE_FILE = ".assistant_schedules.json"
_WORKSPACE_MARKER = ".workspace.json"
_REPORT_WORKSPACE = "report"
_KNOWN_USER_INTERNAL = frozenset(
    {
        ".assistant-defaults-v1.json",
        ".assistant-defaults.lock",
        ".assistant_schedule_locks",
        ".workspace-locks",
        ".workspace-staging",
        ".workspace-user.lock",
        f"{_SCHEDULE_FILE}.lock",
        "knowledge_base",
    }
)


class AssistantPrivacyExportUnavailable(RuntimeError):
    """Assistant subject data cannot be read without guessing or side effects."""


def _path_has_symlink(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            if current.is_symlink():
                return True
        except OSError as exc:
            raise AssistantPrivacyExportUnavailable("assistant path probe failed") from exc
    return False


def _duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON value")


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AssistantPrivacyExportUnavailable("assistant export value is invalid") from exc


def _read_json(path: Path, *, max_bytes: int) -> Any:
    try:
        before = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size <= 0
            or before.st_size > max_bytes
        ):
            raise AssistantPrivacyExportUnavailable("assistant JSON path is unsafe")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if (
                opened.st_dev != before.st_dev
                or opened.st_ino != before.st_ino
                or opened.st_size != before.st_size
                or opened.st_mtime_ns != before.st_mtime_ns
                or opened.st_nlink != 1
                or not stat.S_ISREG(opened.st_mode)
            ):
                raise AssistantPrivacyExportUnavailable("assistant JSON changed while opening")
            chunks: list[bytes] = []
            remaining = before.st_size
            while remaining:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    raise AssistantPrivacyExportUnavailable("assistant JSON was truncated")
                chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(descriptor, 1):
                raise AssistantPrivacyExportUnavailable("assistant JSON grew while reading")
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
            raise AssistantPrivacyExportUnavailable("assistant JSON changed while reading")
        text = b"".join(chunks).decode("utf-8", errors="strict")
        return json.loads(
            text,
            object_pairs_hook=_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except AssistantPrivacyExportUnavailable:
        raise
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise AssistantPrivacyExportUnavailable("assistant JSON is unreadable") from exc


def _safe_subject_id(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AssistantPrivacyExportUnavailable("canonical subject id is invalid")
    return value


def _safe_username(value: Any) -> str:
    username = str(value or "").strip()
    if username in {".", ".."} or _SAFE_USERNAME.fullmatch(username) is None:
        raise AssistantPrivacyExportUnavailable("canonical subject username is invalid")
    return username


def _bounded_string(value: Any, *, maximum: int, label: str) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise AssistantPrivacyExportUnavailable(f"assistant {label} is invalid")
    return value


def _optional_string(value: Any, *, maximum: int, label: str) -> str | None:
    if value is None:
        return None
    return _bounded_string(value, maximum=maximum, label=label)


def _safe_int(value: Any, *, minimum: int, maximum: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AssistantPrivacyExportUnavailable(f"assistant {label} is invalid")
    if value < minimum or value > maximum:
        raise AssistantPrivacyExportUnavailable(f"assistant {label} is invalid")
    return value


def _relative_path(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise AssistantPrivacyExportUnavailable("assistant path escaped subject root") from exc
    if (
        len(relative.parts) > MAX_PATH_DEPTH
        or len(relative.as_posix().encode("utf-8")) > MAX_RELATIVE_PATH_BYTES
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise AssistantPrivacyExportUnavailable("assistant relative path is invalid")
    return relative.as_posix()


def _strict_entry(path: Path, subject_root: Path) -> os.stat_result:
    _relative_path(path, subject_root)
    if path.is_symlink() or _path_has_symlink(path):
        raise AssistantPrivacyExportUnavailable("assistant subject path contains symlink")
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise AssistantPrivacyExportUnavailable("assistant subject path is unreadable") from exc
    if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
        raise AssistantPrivacyExportUnavailable("assistant subject file is hardlinked")
    if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)):
        raise AssistantPrivacyExportUnavailable("assistant subject entry type is unsafe")
    return metadata


def _download_path(workspace: str, relative: str) -> str:
    return (
        f"/api/workspaces/{quote(workspace, safe='')}/files/"
        f"{quote(relative, safe='/')}/download"
    )


class _HashBudget:
    def __init__(self) -> None:
        self.consumed = 0

    def hash_file(self, path: Path, metadata: os.stat_result) -> tuple[str | None, str]:
        if metadata.st_size > MAX_HASH_FILE_BYTES:
            return None, "unavailable_file_size_limit"
        if self.consumed + metadata.st_size > MAX_HASH_TOTAL_BYTES:
            return None, "unavailable_total_read_limit"
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
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
                    raise AssistantPrivacyExportUnavailable(
                        "assistant file changed while opening"
                    )
                digest = hashlib.sha256()
                remaining = metadata.st_size
                while remaining:
                    chunk = os.read(descriptor, min(1024 * 1024, remaining))
                    if not chunk:
                        raise AssistantPrivacyExportUnavailable(
                            "assistant file changed while hashing"
                        )
                    digest.update(chunk)
                    remaining -= len(chunk)
                if os.read(descriptor, 1):
                    raise AssistantPrivacyExportUnavailable(
                        "assistant file changed while hashing"
                    )
                after_open = os.fstat(descriptor)
            finally:
                os.close(descriptor)
            after_path = path.stat(follow_symlinks=False)
        except AssistantPrivacyExportUnavailable:
            raise
        except OSError as exc:
            raise AssistantPrivacyExportUnavailable("assistant file hash failed") from exc
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
            raise AssistantPrivacyExportUnavailable("assistant file changed while hashing")
        self.consumed += metadata.st_size
        return digest.hexdigest(), "available"


class AssistantPrivacyExportReader:
    """Read assistant ownership metadata without bootstrapping or normalization."""

    def __init__(self, workspace_root: Path) -> None:
        raw = Path(workspace_root)
        if not raw.is_absolute():
            raise AssistantPrivacyExportUnavailable("assistant workspace root must be absolute")
        self.workspace_root = Path(os.path.abspath(os.fspath(raw)))
        if _path_has_symlink(self.workspace_root):
            raise AssistantPrivacyExportUnavailable("assistant workspace root contains symlink")
        try:
            self.workspace_root.relative_to(_FORBIDDEN_RELEASE_ROOT)
        except ValueError:
            pass
        else:
            raise AssistantPrivacyExportUnavailable("assistant workspace root is in releases")

    def _subject_root(self, subject_id: int, username: str) -> tuple[int, str, Path | None]:
        user_id = _safe_subject_id(subject_id)
        owner = _safe_username(username)
        if _path_has_symlink(self.workspace_root):
            raise AssistantPrivacyExportUnavailable("assistant workspace root contains symlink")
        if not self.workspace_root.exists():
            return user_id, owner, None
        try:
            root_metadata = self.workspace_root.stat(follow_symlinks=False)
        except OSError as exc:
            raise AssistantPrivacyExportUnavailable(
                "assistant workspace root is unreadable"
            ) from exc
        if not stat.S_ISDIR(root_metadata.st_mode):
            raise AssistantPrivacyExportUnavailable("assistant workspace root is not a directory")
        subject_root = self.workspace_root / owner
        if subject_root.is_symlink():
            raise AssistantPrivacyExportUnavailable("assistant subject root contains symlink")
        if not subject_root.exists():
            return user_id, owner, None
        if _path_has_symlink(subject_root):
            raise AssistantPrivacyExportUnavailable("assistant subject root contains symlink")
        try:
            if not subject_root.is_dir():
                raise AssistantPrivacyExportUnavailable(
                    "assistant subject root is not a directory"
                )
        except OSError as exc:
            raise AssistantPrivacyExportUnavailable("assistant subject root is unreadable") from exc
        return user_id, owner, subject_root

    @staticmethod
    def _workspace_meta(path: Path, subject_root: Path) -> dict[str, Any]:
        _strict_entry(path.parent, subject_root)
        payload = _read_json(path, max_bytes=MAX_WORKSPACE_META_BYTES)
        if not isinstance(payload, dict):
            raise AssistantPrivacyExportUnavailable("assistant workspace marker is invalid")
        allowed = {"desc", "pinned", "created", "updated", "system_default"}
        unknown_count = sum(key not in allowed for key in payload)
        desc = payload.get("desc", "")
        pinned = payload.get("pinned", False)
        created = payload.get("created", "")
        updated = payload.get("updated", "")
        system_default = payload.get("system_default", False)
        if (
            not isinstance(desc, str)
            or len(desc) > 500
            or not isinstance(pinned, bool)
            or not isinstance(created, str)
            or len(created) > 64
            or not isinstance(updated, str)
            or len(updated) > 64
            or not isinstance(system_default, bool)
        ):
            raise AssistantPrivacyExportUnavailable(
                "assistant workspace marker contract is invalid"
            )
        return {
            "description": desc,
            "pinned": pinned,
            "created_at": created,
            "updated_at": updated,
            "system_default": system_default,
            "marker_extension_status": (
                "excluded_unknown_fields" if unknown_count else "none"
            ),
            "unexported_marker_field_count": unknown_count,
        }

    @staticmethod
    def _iter_directory(path: Path) -> Iterable[Path]:
        try:
            with os.scandir(path) as iterator:
                entries: list[Path] = []
                for entry in iterator:
                    entries.append(Path(entry.path))
                    if len(entries) > MAX_DIRECTORY_SCAN_ENTRIES:
                        raise AssistantPrivacyExportUnavailable(
                            "assistant directory inventory exceeds scan bound"
                        )
        except AssistantPrivacyExportUnavailable:
            raise
        except OSError as exc:
            raise AssistantPrivacyExportUnavailable("assistant directory is unreadable") from exc
        entries.sort(key=lambda item: item.name)
        return entries

    def export_workspaces(self, *, subject_id: int, username: str) -> dict[str, Any]:
        user_id, _owner, subject_root = self._subject_root(subject_id, username)
        truncation: list[str] = []
        unavailable = [
            {
                "scope": "workspace_file_contents",
                "reason": "WORKSPACE_FILE_CONTENT_NOT_INLINED",
            },
            {
                "scope": "workspace_dotfiles_and_internal_entries",
                "reason": "WORKSPACE_INTERNAL_ENTRY_METADATA_EXCLUDED",
            },
            {
                "scope": "knowledge_base_file_contents",
                "reason": "KNOWLEDGE_BASE_SUBJECT_EXPORT_UNAVAILABLE",
            },
        ]
        workspaces: list[dict[str, Any]] = []
        files: list[dict[str, Any]] = []
        hash_budget = _HashBudget()
        hash_unavailable = False
        consumed = 0
        unclassified = False
        if subject_root is not None:
            candidates: list[Path] = []
            for entry in self._iter_directory(subject_root):
                metadata = _strict_entry(entry, subject_root)
                if entry.name == _SCHEDULE_FILE or entry.name in _KNOWN_USER_INTERNAL:
                    continue
                if entry.name.startswith("."):
                    continue
                if not stat.S_ISDIR(metadata.st_mode):
                    unclassified = True
                    continue
                marker = entry / _WORKSPACE_MARKER
                if marker.is_symlink():
                    raise AssistantPrivacyExportUnavailable(
                        "assistant workspace marker contains symlink"
                    )
                if not marker.exists():
                    unclassified = True
                    continue
                candidates.append(entry)
            if len(candidates) > MAX_WORKSPACES:
                truncation.append("workspaces:item_count_limit")
            for workspace in candidates[:MAX_WORKSPACES]:
                name = workspace.name
                if (
                    len(name) > 100
                    or name in {"", ".", ".."}
                    or "/" in name
                    or "\\" in name
                    or any(ord(character) < 32 for character in name)
                ):
                    raise AssistantPrivacyExportUnavailable(
                        "assistant workspace name is invalid"
                    )
                meta = self._workspace_meta(workspace / _WORKSPACE_MARKER, subject_root)
                workspace_record = {"name": name, **meta}
                if name == _REPORT_WORKSPACE:
                    workspace_record["file_inventory_scope"] = (
                        "assistant_schedules_and_generated_reports"
                    )
                    workspaces.append(workspace_record)
                    continue
                workspaces.append(workspace_record)
                stack: list[tuple[Path, int]] = [(workspace, 0)]
                while stack:
                    directory, depth = stack.pop()
                    if depth >= MAX_PATH_DEPTH:
                        truncation.append(f"workspace:{name}:path_depth_limit")
                        continue
                    children = list(self._iter_directory(directory))
                    for child in reversed(children):
                        child_metadata = _strict_entry(child, subject_root)
                        if child.name == _WORKSPACE_MARKER or child.name.startswith("."):
                            continue
                        relative_to_workspace = _relative_path(child, workspace)
                        if len(files) >= MAX_WORKSPACE_FILE_ITEMS:
                            truncation.append("workspace_files:item_count_limit")
                            stack.clear()
                            break
                        if stat.S_ISDIR(child_metadata.st_mode):
                            record = {
                                "workspace": name,
                                "relative_path": relative_to_workspace,
                                "kind": "directory",
                                "extension": "",
                                "size_bytes": 0,
                                "modified_at_ns": child_metadata.st_mtime_ns,
                                "content_status": "not_applicable",
                            }
                            stack.append((child, depth + 1))
                        else:
                            digest, hash_status = hash_budget.hash_file(child, child_metadata)
                            if hash_status != "available":
                                hash_unavailable = True
                            record = {
                                "workspace": name,
                                "relative_path": relative_to_workspace,
                                "kind": "file",
                                "extension": child.suffix.lower()[:32],
                                "size_bytes": child_metadata.st_size,
                                "modified_at_ns": child_metadata.st_mtime_ns,
                                "content_sha256": digest,
                                "hash_status": hash_status,
                                "content_status": "not_inlined_metadata_only",
                                "download_path": _download_path(name, relative_to_workspace),
                            }
                        encoded = _canonical(record)
                        if len(encoded) > MAX_EXPORT_ITEM_BYTES:
                            truncation.append("workspace_files:item_byte_limit")
                            continue
                        if consumed + len(encoded) > _DATA_BUDGET_BYTES:
                            truncation.append("workspace_files:total_byte_limit")
                            stack.clear()
                            break
                        files.append(record)
                        consumed += len(encoded)
        if unclassified:
            unavailable.append(
                {
                    "scope": "unclassified_user_root_entries",
                    "reason": "UNCLASSIFIED_ASSISTANT_ENTRY_EXCLUDED",
                }
            )
        if hash_unavailable:
            unavailable.append(
                {
                    "scope": "workspace_file_hashes_beyond_read_limits",
                    "reason": "WORKSPACE_FILE_HASH_READ_LIMIT_REACHED",
                }
            )
        result = {
            "schema_version": ASSISTANT_WORKSPACE_EXPORT_SCHEMA_VERSION,
            "scope": "assistant_workspace_files",
            "status": "partial",
            "subject_ref": f"user:{user_id}",
            "data": {"workspaces": workspaces, "file_metadata": files},
            "truncated": bool(truncation),
            "truncation_reasons": sorted(set(truncation)),
            "limits": {
                "workspaces": MAX_WORKSPACES,
                "file_metadata_items": MAX_WORKSPACE_FILE_ITEMS,
                "bytes_per_item": MAX_EXPORT_ITEM_BYTES,
                "bytes_total": MAX_EXPORT_TOTAL_BYTES,
                "hash_bytes_per_file": MAX_HASH_FILE_BYTES,
                "hash_bytes_total": MAX_HASH_TOTAL_BYTES,
                "path_depth": MAX_PATH_DEPTH,
                "directory_scan_entries": MAX_DIRECTORY_SCAN_ENTRIES,
            },
            "unavailable_subscopes": unavailable,
        }
        if len(_canonical(result)) > MAX_EXPORT_TOTAL_BYTES:
            raise AssistantPrivacyExportUnavailable("assistant workspace export exceeded bound")
        return result

    @staticmethod
    def _schedule_string(
        schedule: Mapping[str, Any], key: str, maximum: int, *, optional: bool = False
    ) -> str | None:
        value = schedule.get(key)
        if optional:
            return _optional_string(value, maximum=maximum, label=f"schedule {key}")
        return _bounded_string(value, maximum=maximum, label=f"schedule {key}")

    @staticmethod
    def _file_reference(value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise AssistantPrivacyExportUnavailable("assistant report reference is invalid")
        workspace = value.get("workspace")
        filename = value.get("file_name")
        file_path = value.get("file_path")
        if (
            workspace != _REPORT_WORKSPACE
            or not isinstance(filename, str)
            or not filename
            or len(filename.encode("utf-8")) > 255
            or filename != Path(filename).name
            or "/" in filename
            or "\\" in filename
            or not filename.lower().endswith(".md")
            or file_path != f"{_REPORT_WORKSPACE}/{filename}"
        ):
            raise AssistantPrivacyExportUnavailable("assistant report reference escaped scope")
        return filename

    def _read_schedules(
        self, subject_root: Path | None, *, subject_id: int, username: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
        if subject_root is None:
            return [], [], []
        path = subject_root / _SCHEDULE_FILE
        if path.is_symlink():
            raise AssistantPrivacyExportUnavailable(
                "assistant schedule source contains symlink"
            )
        if not path.exists():
            return [], [], []
        payload = _read_json(path, max_bytes=MAX_SCHEDULE_SOURCE_BYTES)
        if (
            not isinstance(payload, dict)
            or payload.get("version") != 1
            or not isinstance(payload.get("items"), list)
        ):
            raise AssistantPrivacyExportUnavailable("assistant schedule source contract is invalid")
        source_items = payload["items"]
        seen_ids: set[str] = set()
        schedules: list[dict[str, Any]] = []
        report_refs: list[dict[str, Any]] = []
        truncation: list[str] = []
        for index, raw in enumerate(source_items):
            if not isinstance(raw, Mapping):
                raise AssistantPrivacyExportUnavailable("assistant schedule item is invalid")
            if raw.get("owner") != username or raw.get("user_id") != subject_id:
                raise AssistantPrivacyExportUnavailable(
                    "assistant schedule canonical owner mismatch"
                )
            schedule_id = raw.get("id")
            if not isinstance(schedule_id, str) or _SAFE_SCHEDULE_ID.fullmatch(schedule_id) is None:
                raise AssistantPrivacyExportUnavailable("assistant schedule id is invalid")
            if schedule_id in seen_ids:
                raise AssistantPrivacyExportUnavailable("assistant schedule id is duplicated")
            seen_ids.add(schedule_id)
            runs = raw.get("recent_runs", [])
            if not isinstance(runs, list):
                raise AssistantPrivacyExportUnavailable("assistant recent runs are invalid")
            if len(runs) > 12:
                truncation.append("recent_runs:item_count_limit")
            safe_runs: list[dict[str, Any]] = []
            for run in runs[:12]:
                if not isinstance(run, Mapping):
                    raise AssistantPrivacyExportUnavailable("assistant run record is invalid")
                run_id = _bounded_string(run.get("id"), maximum=128, label="run id")
                status_value = _bounded_string(
                    run.get("status"), maximum=32, label="run status"
                )
                created_at = _bounded_string(
                    run.get("created_at"), maximum=64, label="run timestamp"
                )
                duration = _safe_int(
                    run.get("duration_ms", 0),
                    minimum=0,
                    maximum=86_400_000,
                    label="run duration",
                )
                filename = self._file_reference(run.get("file"))
                safe_runs.append(
                    {
                        "id": run_id,
                        "status": status_value,
                        "created_at": created_at,
                        "duration_ms": duration,
                        "error_present": bool(run.get("error")),
                        "report_metadata_status": (
                            "referenced" if filename is not None else "unavailable"
                        ),
                    }
                )
                if filename is not None and index < MAX_SCHEDULES:
                    report_refs.append(
                        {
                            "schedule_id": schedule_id,
                            "run_id": run_id,
                            "file_name": filename,
                        }
                    )
            last_filename = self._file_reference(raw.get("last_file"))
            if last_filename is not None and index < MAX_SCHEDULES:
                report_refs.append(
                    {
                        "schedule_id": schedule_id,
                        "run_id": None,
                        "file_name": last_filename,
                    }
                )
            if index >= MAX_SCHEDULES:
                continue
            schedule = {
                "id": schedule_id,
                "title": self._schedule_string(raw, "title", 160),
                "topic": self._schedule_string(raw, "topic", 300),
                "cadence": self._schedule_string(raw, "cadence", 32),
                "timezone": self._schedule_string(raw, "timezone", 80),
                "time_of_day": self._schedule_string(raw, "time_of_day", 5),
                "day_of_week": _safe_int(
                    raw.get("day_of_week", 0), minimum=0, maximum=6, label="day of week"
                ),
                "interval_hours": _safe_int(
                    raw.get("interval_hours", 24),
                    minimum=1,
                    maximum=720,
                    label="interval hours",
                ),
                "enabled": raw.get("enabled"),
                "report_type": self._schedule_string(raw, "report_type", 64),
                "time_range": self._schedule_string(raw, "time_range", 64),
                "perspective": self._schedule_string(raw, "perspective", 100),
                "include_sources": raw.get("include_sources"),
                "include_charts": raw.get("include_charts"),
                "created_at": self._schedule_string(raw, "created_at", 64),
                "updated_at": self._schedule_string(raw, "updated_at", 64),
                "last_run_at": self._schedule_string(raw, "last_run_at", 64, optional=True),
                "next_run_at": self._schedule_string(raw, "next_run_at", 64, optional=True),
                "last_status": self._schedule_string(raw, "last_status", 32),
                "run_count": _safe_int(
                    raw.get("run_count", 0), minimum=0, maximum=10_000_000, label="run count"
                ),
                "pinned_workspace": self._schedule_string(raw, "pinned_workspace", 100),
                "recent_runs": safe_runs,
                "prompt_status": "not_exported_potentially_sensitive",
                "context_status": "not_exported_potentially_sensitive",
                "error_detail_status": "not_exported_potentially_sensitive",
            }
            if not all(
                isinstance(schedule[key], bool)
                for key in ("enabled", "include_sources", "include_charts")
            ):
                raise AssistantPrivacyExportUnavailable("assistant schedule flags are invalid")
            if len(_canonical(schedule)) > MAX_EXPORT_ITEM_BYTES:
                truncation.append("schedules:item_byte_limit")
                schedules.append(
                    {
                        "id": schedule_id,
                        "created_at": schedule["created_at"],
                        "updated_at": schedule["updated_at"],
                        "export_status": "metadata_only_item_byte_limit",
                    }
                )
            else:
                schedules.append(schedule)
        if len(source_items) > MAX_SCHEDULES:
            truncation.append("schedules:item_count_limit")
        if len(report_refs) > MAX_REPORT_REFERENCES:
            truncation.append("generated_reports:reference_count_limit")
        return schedules, report_refs[:MAX_REPORT_REFERENCES], truncation

    def export_schedules_and_reports(
        self, *, subject_id: int, username: str
    ) -> dict[str, Any]:
        user_id, owner, subject_root = self._subject_root(subject_id, username)
        schedules, report_refs, truncation = self._read_schedules(
            subject_root,
            subject_id=user_id,
            username=owner,
        )
        reports: list[dict[str, Any]] = []
        hash_budget = _HashBudget()
        hash_unavailable = False
        referenced_file_missing = False
        seen: set[tuple[str, str]] = set()
        consumed = sum(len(_canonical(item)) for item in schedules)
        if subject_root is not None:
            report_root = subject_root / _REPORT_WORKSPACE
            if report_root.is_symlink():
                raise AssistantPrivacyExportUnavailable(
                    "assistant report workspace contains symlink"
                )
            if report_root.exists():
                _strict_entry(report_root, subject_root)
            for reference in report_refs:
                key = (reference["schedule_id"], reference["file_name"])
                if key in seen:
                    continue
                seen.add(key)
                report_path = report_root / reference["file_name"]
                relative = f"{_REPORT_WORKSPACE}/{reference['file_name']}"
                if report_path.is_symlink():
                    raise AssistantPrivacyExportUnavailable(
                        "assistant report reference contains symlink"
                    )
                if not report_path.exists():
                    referenced_file_missing = True
                    record = {
                        **reference,
                        "relative_path": relative,
                        "storage_status": "unavailable",
                        "content_status": "not_inlined",
                        "content_sha256": None,
                        "hash_status": "unavailable_missing_file",
                        "download_path": None,
                    }
                else:
                    metadata = _strict_entry(report_path, subject_root)
                    if not stat.S_ISREG(metadata.st_mode):
                        raise AssistantPrivacyExportUnavailable(
                            "assistant report reference is not a file"
                        )
                    digest, hash_status = hash_budget.hash_file(report_path, metadata)
                    if hash_status != "available":
                        hash_unavailable = True
                    record = {
                        **reference,
                        "relative_path": relative,
                        "size_bytes": metadata.st_size,
                        "modified_at_ns": metadata.st_mtime_ns,
                        "storage_status": "available",
                        "content_status": "not_inlined_metadata_only",
                        "content_sha256": digest,
                        "hash_status": hash_status,
                        "download_path": _download_path(
                            _REPORT_WORKSPACE, reference["file_name"]
                        ),
                    }
                encoded = _canonical(record)
                if consumed + len(encoded) > _DATA_BUDGET_BYTES:
                    truncation.append("generated_reports:total_byte_limit")
                    break
                reports.append(record)
                consumed += len(encoded)
        unavailable = [
            {
                "scope": "schedule_prompts_and_context",
                "reason": "SCHEDULE_SENSITIVE_BODY_EXCLUDED",
            },
            {
                "scope": "schedule_error_details",
                "reason": "SCHEDULE_ERROR_DETAIL_EXCLUDED",
            },
            {
                "scope": "generated_report_contents",
                "reason": "GENERATED_REPORT_CONTENT_NOT_INLINED",
            },
            {
                "scope": "generated_report_unreferenced_history",
                "reason": "AUTHORITATIVE_REPORT_REFERENCE_REQUIRED",
            },
            {
                "scope": "global_sites_and_members",
                "reason": "GLOBAL_DATA_NOT_SUBJECT_OWNED",
            },
        ]
        if hash_unavailable:
            unavailable.append(
                {
                    "scope": "generated_report_hashes_beyond_read_limits",
                    "reason": "GENERATED_REPORT_HASH_READ_LIMIT_REACHED",
                }
            )
        if referenced_file_missing:
            unavailable.append(
                {
                    "scope": "referenced_generated_report_storage",
                    "reason": "REFERENCED_GENERATED_REPORT_FILE_MISSING",
                }
            )
        result = {
            "schema_version": ASSISTANT_AUTOMATION_EXPORT_SCHEMA_VERSION,
            "scope": "assistant_schedules_and_generated_reports",
            "status": "partial",
            "subject_ref": f"user:{user_id}",
            "data": {"schedules": schedules, "generated_report_metadata": reports},
            "truncated": bool(truncation),
            "truncation_reasons": sorted(set(truncation)),
            "limits": {
                "schedules": MAX_SCHEDULES,
                "report_references": MAX_REPORT_REFERENCES,
                "schedule_source_bytes": MAX_SCHEDULE_SOURCE_BYTES,
                "bytes_per_item": MAX_EXPORT_ITEM_BYTES,
                "bytes_total": MAX_EXPORT_TOTAL_BYTES,
                "hash_bytes_per_file": MAX_HASH_FILE_BYTES,
                "hash_bytes_total": MAX_HASH_TOTAL_BYTES,
            },
            "unavailable_subscopes": unavailable,
        }
        if len(_canonical(result)) > MAX_EXPORT_TOTAL_BYTES:
            raise AssistantPrivacyExportUnavailable("assistant automation export exceeded bound")
        return result


def configured_assistant_privacy_export_reader() -> AssistantPrivacyExportReader:
    return AssistantPrivacyExportReader(load_assistant_schedule_settings().workspace_root)


__all__ = (
    "ASSISTANT_AUTOMATION_EXPORT_SCHEMA_VERSION",
    "ASSISTANT_WORKSPACE_EXPORT_SCHEMA_VERSION",
    "AssistantPrivacyExportReader",
    "AssistantPrivacyExportUnavailable",
    "configured_assistant_privacy_export_reader",
)
