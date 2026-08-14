"""Bounded personal-data export and append-only deletion-request intake."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from sqlalchemy import asc
from sqlalchemy.orm import Session

from api.features.identity.provider_urls import (
    ProviderBaseUrlError,
    normalize_provider_base_url,
)
from api.orm import models

PRIVACY_EXPORT_SCHEMA_VERSION = "personal-data-export-v1"
PRIVACY_REQUEST_SCHEMA_VERSION = "privacy-rights-request-v1"
PRIVACY_EVENT_SCHEMA_VERSION = "privacy-rights-event-v1"
MAX_EXPORT_ROWS_PER_SECTION = 5000
MAX_EXPORT_ADAPTERS = 8
MAX_EXPORT_ADAPTER_BYTES = 2 * 1024 * 1024
MAX_EXPORT_ADAPTER_TOTAL_BYTES = 4 * 1024 * 1024
MAX_EXPORT_RELATIONAL_FIELD_BYTES = 32 * 1024
MAX_EXPORT_RELATIONAL_ITEM_BYTES = 64 * 1024
MAX_EXPORT_RELATIONAL_TOTAL_BYTES = 4 * 1024 * 1024
MAX_PERSONAL_EXPORT_TOTAL_BYTES = 10 * 1024 * 1024
MAX_REQUESTS_PER_USER = 100
MAX_PRIVACY_EVENT_BYTES = 64 * 1024
_REQUEST_ID = re.compile(r"^privacy-[0-9a-f]{32}$")
_EVENT_ID = re.compile(r"^event-[0-9]{8}T[0-9]{12}Z-[0-9a-f]{16}$")
_FORBIDDEN_RELEASE_ROOT = Path("/root/data/releases/globemind")
_EXPORT_SCOPE = re.compile(r"^[a-z][a-z0-9_]{1,95}$")
_REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,127}$")
_PRIVACY_EVENT_KEYS = {
    "schema_version",
    "event_id",
    "request_id",
    "subject_ref",
    "action",
    "occurred_at",
}
_FORBIDDEN_EXPORT_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "api_keys",
        "authorization",
        "cookie",
        "credentials",
        "password",
        "password_hash",
        "refresh_token",
        "secret",
        "token",
    }
)


class PrivacyRightsUnavailable(RuntimeError):
    """A personal-data operation cannot be completed safely."""


class PrivacyRightsConflict(RuntimeError):
    """The requested state transition is no longer valid."""


class PrivacyRightsNotFound(RuntimeError):
    """A request owned by the current subject does not exist."""


@dataclass(frozen=True)
class PersonalDataExportAdapterBinding:
    """Late-bound cross-feature reader invoked only by an authenticated export."""

    scope: str
    reader: Callable[[], Mapping[str, Any]]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        return str(value)
    current = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat()


def _subject_id(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("subject id must be a positive integer")
    try:
        subject_id = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("subject id must be a positive integer") from exc
    if subject_id <= 0:
        raise ValueError("subject id must be a positive integer")
    return subject_id


def _subject_username(value: Any) -> str:
    username = str(value or "").strip()
    if (
        not username
        or len(username) > 128
        or any(ord(character) < 32 or ord(character) == 127 for character in username)
    ):
        raise ValueError("subject username is invalid")
    return username


def _contains_forbidden_export_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized_key = str(key).strip().lower()
            if normalized_key in _FORBIDDEN_EXPORT_KEYS or normalized_key.endswith(
                ("_api_key", "_password", "_secret", "_token")
            ):
                return True
            if _contains_forbidden_export_key(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden_export_key(item) for item in value)
    return False


def _normalize_account_provider_urls(value: Any, key: str = "") -> Any:
    if isinstance(value, Mapping):
        return {
            str(nested_key): _normalize_account_provider_urls(
                nested,
                str(nested_key).strip().lower(),
            )
            for nested_key, nested in value.items()
        }
    if key == "base_url" or key.endswith("_base_url"):
        if value is None:
            return None
        try:
            return normalize_provider_base_url(value)
        except ProviderBaseUrlError as exc:
            raise PrivacyRightsUnavailable(
                "canonical account metadata contains an unsafe provider URL"
            ) from exc
    return value


def _adapter_unavailable(scope: str, reason: str) -> dict[str, str]:
    return {"scope": scope, "reason": reason}


def _replace_unavailable_scope(
    unavailable_scopes: list[dict[str, str]],
    scope: str,
    reason: str,
) -> None:
    unavailable_scopes[:] = [
        item for item in unavailable_scopes if item.get("scope") != scope
    ]
    unavailable_scopes.append(_adapter_unavailable(scope, reason))


def _integrate_export_adapters(
    *,
    data: dict[str, Any],
    subject_ref: str,
    adapters: tuple[PersonalDataExportAdapterBinding, ...],
    unavailable_scopes: list[dict[str, str]],
    truncated_sections: list[str],
) -> dict[str, dict[str, Any]]:
    if len(adapters) > MAX_EXPORT_ADAPTERS:
        raise PrivacyRightsUnavailable("personal data export adapter bound was exceeded")
    metadata: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    total_bytes = 0
    for binding in adapters:
        scope = str(binding.scope or "").strip()
        if _EXPORT_SCOPE.fullmatch(scope) is None or scope in seen:
            raise PrivacyRightsUnavailable("personal data export adapter scope is invalid")
        seen.add(scope)
        try:
            raw = binding.reader()
            if not isinstance(raw, Mapping):
                raise ValueError("adapter result must be an object")
            result = dict(raw)
            if result.get("scope") != scope:
                raise ValueError("adapter result scope mismatch")
            if result.get("subject_ref") != subject_ref:
                raise ValueError("adapter result subject mismatch")
            status = result.get("status")
            if status not in {"available", "partial", "unavailable"}:
                raise ValueError("adapter status is invalid")
            encoded = json.dumps(
                result,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(encoded) > MAX_EXPORT_ADAPTER_BYTES:
                raise ValueError("adapter result exceeds its byte bound")
            if total_bytes + len(encoded) > MAX_EXPORT_ADAPTER_TOTAL_BYTES:
                raise OverflowError("adapter aggregate exceeds its byte bound")
            if _contains_forbidden_export_key(result):
                raise ValueError("adapter result contains forbidden secret fields")
            if not isinstance(result.get("data"), Mapping):
                raise ValueError("adapter data is invalid")
            if not isinstance(result.get("limits"), Mapping):
                raise ValueError("adapter limits are invalid")
            if not isinstance(result.get("truncation_reasons"), list):
                raise ValueError("adapter truncation metadata is invalid")
            unavailable = result.get("unavailable_subscopes")
            if not isinstance(unavailable, list):
                raise ValueError("adapter unavailable-subscope metadata is invalid")
            for item in unavailable:
                if (
                    not isinstance(item, Mapping)
                    or _EXPORT_SCOPE.fullmatch(str(item.get("scope") or "")) is None
                    or _REASON_CODE.fullmatch(str(item.get("reason") or "")) is None
                ):
                    raise ValueError("adapter unavailable subscope is invalid")
        except OverflowError:
            _replace_unavailable_scope(
                unavailable_scopes,
                scope,
                "ADAPTER_TOTAL_BYTES_EXCEEDED",
            )
            continue
        except Exception:
            # Adapter exceptions and malformed bodies are intentionally not
            # exposed through the privacy response.
            _replace_unavailable_scope(
                unavailable_scopes,
                scope,
                "SUBSYSTEM_EXPORT_ADAPTER_UNAVAILABLE",
            )
            continue

        unavailable_scopes[:] = [
            item for item in unavailable_scopes if item.get("scope") != scope
        ]
        if status == "unavailable":
            reason = str(result.get("reason_code") or "SUBSYSTEM_EXPORT_ADAPTER_UNAVAILABLE")
            if _REASON_CODE.fullmatch(reason) is None:
                reason = "SUBSYSTEM_EXPORT_ADAPTER_UNAVAILABLE"
            _replace_unavailable_scope(unavailable_scopes, scope, reason)
            continue

        total_bytes += len(encoded)
        # JSON round-trip makes the injected section plain finite data rather
        # than retaining arbitrary Mapping implementations.
        data[scope] = json.loads(encoded.decode("utf-8"))["data"]
        was_truncated = bool(result.get("truncated"))
        if was_truncated:
            truncated_sections.append(scope)
        for item in result["unavailable_subscopes"]:
            unavailable_scopes.append(
                _adapter_unavailable(
                    f"{scope}.{item['scope']}",
                    str(item["reason"]),
                )
            )
        metadata[scope] = {
            "schema_version": str(result.get("schema_version") or ""),
            "status": status,
            "truncated": was_truncated,
            "truncation_reasons": list(result["truncation_reasons"]),
            "limits": dict(result["limits"]),
        }
    return metadata


def _bounded_rows(query: Any) -> tuple[list[Any], bool]:
    rows = list(query.limit(MAX_EXPORT_ROWS_PER_SECTION + 1).all())
    return rows[:MAX_EXPORT_ROWS_PER_SECTION], len(rows) > MAX_EXPORT_ROWS_PER_SECTION


def _truncate_utf8(value: str, limit: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value, False
    return encoded[:limit].decode("utf-8", errors="ignore"), True


def _bound_relational_value(
    value: Any,
    *,
    path: str,
    truncated_fields: dict[str, int],
) -> Any:
    if isinstance(value, str):
        bounded, truncated = _truncate_utf8(value, MAX_EXPORT_RELATIONAL_FIELD_BYTES)
        if truncated:
            truncated_fields[path] = truncated_fields.get(path, 0) + 1
        return bounded
    if isinstance(value, Mapping):
        return {
            str(key): _bound_relational_value(
                nested,
                path=f"{path}.{key}",
                truncated_fields=truncated_fields,
            )
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [
            _bound_relational_value(
                nested,
                path=f"{path}[]",
                truncated_fields=truncated_fields,
            )
            for nested in value
        ]
    return value


def _bound_relational_sections(
    sections: dict[str, list[dict[str, Any]]],
    truncation: dict[str, bool],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    result: dict[str, list[dict[str, Any]]] = {}
    truncated_fields: dict[str, int] = {}
    total_bytes = 0
    for section, rows in sections.items():
        output: list[dict[str, Any]] = []
        for row in rows:
            bounded = _bound_relational_value(
                row,
                path=section,
                truncated_fields=truncated_fields,
            )
            encoded = json.dumps(
                bounded,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(encoded) > MAX_EXPORT_RELATIONAL_ITEM_BYTES:
                truncation[section] = True
                bounded = {
                    "id": row.get("id"),
                    "export_status": "metadata_only_item_byte_limit",
                    "content_sha256": hashlib.sha256(encoded).hexdigest(),
                }
                encoded = json.dumps(
                    bounded,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            if total_bytes + len(encoded) > MAX_EXPORT_RELATIONAL_TOTAL_BYTES:
                truncation[section] = True
                break
            output.append(bounded)
            total_bytes += len(encoded)
        result[section] = output
        if any(key == section or key.startswith(f"{section}.") for key in truncated_fields):
            truncation[section] = True
    return result, dict(sorted(truncated_fields.items()))


def build_personal_data_export(
    db: Session,
    *,
    subject_id: int,
    subject_username: str,
    account: Mapping[str, Any],
    adapters: tuple[PersonalDataExportAdapterBinding, ...] = (),
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Export known relational personal data without credential material.

    Cross-feature filesystem and research data is accepted only through
    subject-safe facade adapters. Missing, malformed, or unsafe adapters remain
    explicit unavailable scopes rather than being guessed or treated as empty.
    """

    user_id = _subject_id(subject_id)
    username = _subject_username(subject_username)
    try:
        account_id = _subject_id(account.get("id"))
        account_username = _subject_username(account.get("username"))
    except ValueError as exc:
        raise PrivacyRightsUnavailable("canonical export subject is unavailable") from exc
    if account_id != user_id or account_username != username:
        raise PrivacyRightsUnavailable("canonical export subject does not match account")
    sections: dict[str, list[dict[str, Any]]] = {}
    truncation: dict[str, bool] = {}
    try:
        searches, truncation["search_history"] = _bounded_rows(
            db.query(models.UserSearchHistory)
            .filter(models.UserSearchHistory.user_id == user_id)
            .order_by(asc(models.UserSearchHistory.id))
        )
        sections["search_history"] = [
            {
                "id": int(row.id),
                "keyword": row.keyword,
                "created_at": _iso(row.created_at),
            }
            for row in searches
        ]

        favorites, truncation["favorites"] = _bounded_rows(
            db.query(models.UserFavorite)
            .filter(models.UserFavorite.user_id == user_id)
            .order_by(asc(models.UserFavorite.id))
        )
        sections["favorites"] = [
            {
                "id": int(row.id),
                "news_id": int(row.news_id),
                "topic": row.topic or "",
                "kind": row.item_kind or "favorite",
                "created_at": _iso(row.created_at),
            }
            for row in favorites
        ]

        sessions, truncation["assistant_sessions"] = _bounded_rows(
            db.query(models.AssistantChatSession)
            .filter(models.AssistantChatSession.user_id == user_id)
            .order_by(asc(models.AssistantChatSession.id))
        )
        sections["assistant_sessions"] = [
            {
                "id": int(row.id),
                "title": row.title,
                "pinned": bool(row.pinned),
                "context_summary": row.context_summary,
                "created_at": _iso(row.created_at),
                "updated_at": _iso(row.updated_at),
            }
            for row in sessions
        ]

        messages, truncation["assistant_messages"] = _bounded_rows(
            db.query(models.AssistantChatMessage)
            .filter(models.AssistantChatMessage.user_id == user_id)
            .order_by(asc(models.AssistantChatMessage.id))
        )
        sections["assistant_messages"] = [
            {
                "id": int(row.id),
                "session_id": int(row.session_id),
                "role": row.role,
                "content": row.content,
                "extra_json_status": "not_exported_unverified_sensitive_metadata",
                "created_at": _iso(row.created_at),
            }
            for row in messages
        ]

        memories, truncation["assistant_memory"] = _bounded_rows(
            db.query(models.AssistantUserMemory)
            .filter(models.AssistantUserMemory.user_id == user_id)
            .order_by(asc(models.AssistantUserMemory.id))
        )
        sections["assistant_memory"] = [
            {
                "id": int(row.id),
                "memory_summary": row.memory_summary,
                "created_at": _iso(row.created_at),
                "updated_at": _iso(row.updated_at),
            }
            for row in memories
        ]
    except Exception as exc:
        raise PrivacyRightsUnavailable(
            "personal data relations could not be exported consistently"
        ) from exc

    safe_account = {
        key: account.get(key)
        for key in (
            "id",
            "username",
            "full_name",
            "email",
            "phone",
            "created_at",
            "updated_at",
            "is_active",
            "last_login_at",
            "role",
            "avatar_url",
            "api_key_status",
            "api_config_public",
            "active_provider",
            "default_model",
            "base_url",
        )
    }
    if _contains_forbidden_export_key(safe_account):
        raise PrivacyRightsUnavailable(
            "canonical account metadata contains secret material"
        )
    safe_account = _normalize_account_provider_urls(safe_account)
    for key in ("created_at", "updated_at", "last_login_at"):
        safe_account[key] = _iso(safe_account.get(key))

    data = {"account": safe_account, **sections}
    unavailable_scopes = [
        {
            "scope": "assistant_workspace_files",
            "reason": "SUBSYSTEM_EXPORT_ADAPTER_UNAVAILABLE",
        },
        {
            "scope": "assistant_schedules_and_generated_reports",
            "reason": "PIPELINE_CHECKPOINT_AND_EXPORT_ADAPTER_REQUIRED",
        },
        {
            "scope": "research_workflow_projects",
            "reason": "PROJECT_ACL_EXPORT_ADAPTER_REQUIRED",
        },
        {
            "scope": "assistant_messages.extra_json",
            "reason": "UNVERIFIED_SENSITIVE_METADATA_EXCLUDED",
        },
    ]
    bounded_sections, relational_field_truncation = _bound_relational_sections(
        sections,
        truncation,
    )
    data = {"account": safe_account, **bounded_sections}
    truncated_sections = sorted(key for key, value in truncation.items() if value)
    adapter_status = _integrate_export_adapters(
        data=data,
        subject_ref=f"user:{user_id}",
        adapters=adapters,
        unavailable_scopes=unavailable_scopes,
        truncated_sections=truncated_sections,
    )
    canonical = json.dumps(
        data,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(canonical) > MAX_PERSONAL_EXPORT_TOTAL_BYTES:
        raise PrivacyRightsUnavailable("personal data export exceeded its total byte bound")
    truncated_sections = sorted(set(truncated_sections))
    unavailable_scopes.sort(key=lambda item: (item["scope"], item["reason"]))
    payload = {
        "schema_version": PRIVACY_EXPORT_SCHEMA_VERSION,
        "generated_at": _iso(generated_at or _utcnow()),
        "subject_ref": f"user:{user_id}",
        "complete": not unavailable_scopes and not truncated_sections,
        "data_sha256": hashlib.sha256(canonical).hexdigest(),
        "hash_scope": "canonical-data-section",
        "data": data,
        "adapter_status": adapter_status,
        "relational_field_truncation": relational_field_truncation,
        "truncated_sections": truncated_sections,
        "unavailable_scopes": unavailable_scopes,
        "export_limits": {
            "rows_per_relational_section": MAX_EXPORT_ROWS_PER_SECTION,
            "adapter_count": MAX_EXPORT_ADAPTERS,
            "bytes_per_adapter": MAX_EXPORT_ADAPTER_BYTES,
            "bytes_all_adapters": MAX_EXPORT_ADAPTER_TOTAL_BYTES,
            "bytes_per_relational_field": MAX_EXPORT_RELATIONAL_FIELD_BYTES,
            "bytes_per_relational_item": MAX_EXPORT_RELATIONAL_ITEM_BYTES,
            "bytes_all_relational_sections": MAX_EXPORT_RELATIONAL_TOTAL_BYTES,
            "bytes_total_export": MAX_PERSONAL_EXPORT_TOTAL_BYTES,
        },
        "excluded_secret_material": [
            "password_hash",
            "password_reset_tokens",
            "api_key_values",
            "access_tokens",
        ],
    }
    try:
        encoded_payload = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PrivacyRightsUnavailable("personal data export is not canonical") from exc
    if len(encoded_payload) > MAX_PERSONAL_EXPORT_TOTAL_BYTES:
        raise PrivacyRightsUnavailable("personal data export exceeded its total byte bound")
    return payload


def _path_has_symlink(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _ensure_directory(path: Path) -> None:
    if _path_has_symlink(path):
        raise PrivacyRightsUnavailable("privacy rights path contains a symbolic link")
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o750)
        if path.is_symlink() or not path.is_dir():
            raise PrivacyRightsUnavailable("privacy rights directory is unavailable")
        os.chmod(path, 0o750)
    except PrivacyRightsUnavailable:
        raise
    except OSError as exc:
        raise PrivacyRightsUnavailable("privacy rights directory is unavailable") from exc


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise PrivacyRightsUnavailable("privacy rights event has duplicate keys")
        payload[key] = value
    return payload


def _read_json(path: Path) -> dict[str, Any]:
    descriptor = -1
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > MAX_PRIVACY_EVENT_BYTES
        ):
            raise PrivacyRightsUnavailable("privacy rights event integrity is invalid")
        encoded = b""
        while len(encoded) <= MAX_PRIVACY_EVENT_BYTES:
            chunk = os.read(
                descriptor,
                min(64 * 1024, MAX_PRIVACY_EVENT_BYTES + 1 - len(encoded)),
            )
            if not chunk:
                break
            encoded += chunk
        after = os.fstat(descriptor)
        if (
            after.st_nlink != 1
            or after.st_size != len(encoded)
            or len(encoded) > MAX_PRIVACY_EVENT_BYTES
        ):
            raise PrivacyRightsUnavailable("privacy rights event integrity is invalid")
        payload = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                PrivacyRightsUnavailable("privacy rights event has a non-finite number")
            ),
        )
    except PrivacyRightsUnavailable:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PrivacyRightsUnavailable("privacy rights event is unreadable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(payload, dict):
        raise PrivacyRightsUnavailable("privacy rights event root is invalid")
    return payload


def _append_json(path: Path, payload: dict[str, Any]) -> None:
    _ensure_directory(path.parent)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_PRIVACY_EVENT_BYTES:
        raise PrivacyRightsUnavailable("privacy rights event exceeds its size bound")
    descriptor = -1
    temporary = ""
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=".privacy-", dir=path.parent)
        os.fchmod(descriptor, 0o640)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        os.unlink(temporary)
        temporary = ""
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        raise PrivacyRightsUnavailable("privacy rights event could not be committed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


class PrivacyDeletionRequestStore:
    """Append-only intake/cancellation log; execution stays explicitly manual."""

    def __init__(self, root: Path) -> None:
        raw = Path(root)
        if not raw.is_absolute():
            raise PrivacyRightsUnavailable("privacy rights root must be absolute")
        self.root = Path(os.path.abspath(os.fspath(raw)))
        if _path_has_symlink(self.root):
            raise PrivacyRightsUnavailable("privacy rights root contains a symbolic link")
        try:
            self.root.relative_to(_FORBIDDEN_RELEASE_ROOT)
        except ValueError:
            pass
        else:
            raise PrivacyRightsUnavailable("privacy rights root cannot be inside releases")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        _ensure_directory(self.root)
        lock_path = self.root / ".privacy.lock"
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = -1
        try:
            descriptor = os.open(lock_path, flags, 0o600)
            lock_stat = os.fstat(descriptor)
            if not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_nlink != 1:
                raise PrivacyRightsUnavailable("privacy rights lock integrity is invalid")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            if os.fstat(descriptor).st_nlink != 1:
                raise PrivacyRightsUnavailable("privacy rights lock integrity is invalid")
            yield
        except PrivacyRightsUnavailable:
            raise
        except OSError as exc:
            raise PrivacyRightsUnavailable("privacy rights lock is unavailable") from exc
        finally:
            if descriptor >= 0:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)

    def _user_root(self, subject_id: int) -> Path:
        return self.root / "subjects" / str(_subject_id(subject_id))

    def _request_events(self, subject_id: int, request_id: str) -> list[dict[str, Any]]:
        if _REQUEST_ID.fullmatch(request_id) is None:
            raise ValueError("privacy request id is invalid")
        root = self._user_root(subject_id) / request_id
        if not root.exists():
            raise PrivacyRightsNotFound("privacy request was not found")
        if root.is_symlink() or not root.is_dir():
            raise PrivacyRightsUnavailable("privacy request directory is invalid")
        entries = sorted(root.iterdir())
        if not entries or len(entries) > MAX_REQUESTS_PER_USER:
            raise PrivacyRightsUnavailable("privacy request event bound is invalid")
        events: list[dict[str, Any]] = []
        event_times: list[datetime] = []
        for path in entries:
            event = _read_json(path)
            event_id = str(event.get("event_id") or "")
            try:
                occurred_at = datetime.fromisoformat(
                    str(event.get("occurred_at") or "").replace("Z", "+00:00")
                )
            except ValueError as exc:
                raise PrivacyRightsUnavailable(
                    "privacy request event contract is invalid"
                ) from exc
            if occurred_at.tzinfo is None:
                raise PrivacyRightsUnavailable("privacy request event contract is invalid")
            occurred_at = occurred_at.astimezone(timezone.utc)
            if (
                set(event) != _PRIVACY_EVENT_KEYS
                or path.name != f"{event_id}.json"
                or _EVENT_ID.fullmatch(event_id) is None
                or not event_id.startswith(
                    f"event-{occurred_at.strftime('%Y%m%dT%H%M%S%fZ')}-"
                )
                or event.get("schema_version") != PRIVACY_EVENT_SCHEMA_VERSION
                or event.get("request_id") != request_id
                or event.get("subject_ref") != f"user:{subject_id}"
                or event.get("action") not in {"requested", "cancelled"}
            ):
                raise PrivacyRightsUnavailable("privacy request event contract is invalid")
            events.append(event)
            event_times.append(occurred_at)
        if event_times != sorted(event_times):
            raise PrivacyRightsUnavailable("privacy request event chronology is invalid")
        if events[0].get("action") != "requested" or any(
            event.get("action") == "requested" for event in events[1:]
        ):
            raise PrivacyRightsUnavailable("privacy request transition chain is invalid")
        if sum(event.get("action") == "cancelled" for event in events) > 1:
            raise PrivacyRightsUnavailable("privacy request transition chain is invalid")
        return events

    @staticmethod
    def _public(events: list[dict[str, Any]]) -> dict[str, Any]:
        first = events[0]
        cancelled = next(
            (event for event in reversed(events) if event["action"] == "cancelled"),
            None,
        )
        return {
            "schema_version": PRIVACY_REQUEST_SCHEMA_VERSION,
            "request_id": first["request_id"],
            "request_type": "account_deletion",
            "requested_at": first["occurred_at"],
            "status": "cancelled" if cancelled else "pending_manual_execution",
            "cancelled_at": cancelled["occurred_at"] if cancelled else None,
            "execution_status": "not_executed",
            "execution_blockers": [
                "ACTIVE_PIPELINE_CHECKPOINT_AND_REPLAY_PROOF_REQUIRED",
                "ASSISTANT_WORKSPACE_ERASURE_ADAPTER_REQUIRED",
                "RESEARCH_PROJECT_ACL_AND_RETENTION_REVIEW_REQUIRED",
                "LEGAL_RETENTION_SCHEDULE_APPROVAL_REQUIRED",
            ],
        }

    def list(self, subject_id: int) -> dict[str, Any]:
        user_root = self._user_root(subject_id)
        if not user_root.exists():
            return {
                "schema_version": PRIVACY_REQUEST_SCHEMA_VERSION,
                "items": [],
            }
        if user_root.is_symlink() or not user_root.is_dir():
            raise PrivacyRightsUnavailable("privacy subject directory is invalid")
        entries = sorted(user_root.iterdir())
        if len(entries) > MAX_REQUESTS_PER_USER or any(
            entry.is_symlink()
            or not entry.is_dir()
            or _REQUEST_ID.fullmatch(entry.name) is None
            for entry in entries
        ):
            raise PrivacyRightsUnavailable("privacy subject inventory is invalid")
        items = [self._public(self._request_events(subject_id, entry.name)) for entry in entries]
        return {
            "schema_version": PRIVACY_REQUEST_SCHEMA_VERSION,
            "items": list(reversed(items)),
        }

    def create(self, subject_id: int, *, now: datetime | None = None) -> dict[str, Any]:
        user_id = _subject_id(subject_id)
        current = (now or _utcnow()).astimezone(timezone.utc)
        with self._locked():
            existing = self.list(user_id)["items"]
            if any(item["status"] == "pending_manual_execution" for item in existing):
                raise PrivacyRightsConflict("an account deletion request is already pending")
            if len(existing) >= MAX_REQUESTS_PER_USER:
                raise PrivacyRightsUnavailable("privacy request bound was exceeded")
            request_id = f"privacy-{secrets.token_hex(16)}"
            event_id = (
                f"event-{current.strftime('%Y%m%dT%H%M%S%fZ')}-"
                f"{secrets.token_hex(8)}"
            )
            event = {
                "schema_version": PRIVACY_EVENT_SCHEMA_VERSION,
                "event_id": event_id,
                "request_id": request_id,
                "subject_ref": f"user:{user_id}",
                "action": "requested",
                "occurred_at": current.isoformat(),
            }
            _append_json(self._user_root(user_id) / request_id / f"{event_id}.json", event)
            return self._public([event])

    def cancel(
        self,
        subject_id: int,
        request_id: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        user_id = _subject_id(subject_id)
        current = (now or _utcnow()).astimezone(timezone.utc)
        with self._locked():
            events = self._request_events(user_id, request_id)
            if any(event["action"] == "cancelled" for event in events):
                raise PrivacyRightsConflict("account deletion request is already cancelled")
            previous_time = datetime.fromisoformat(
                str(events[-1]["occurred_at"]).replace("Z", "+00:00")
            ).astimezone(timezone.utc)
            if current <= previous_time:
                raise PrivacyRightsConflict(
                    "account deletion cancellation cannot precede its request"
                )
            event_id = (
                f"event-{current.strftime('%Y%m%dT%H%M%S%fZ')}-"
                f"{secrets.token_hex(8)}"
            )
            event = {
                "schema_version": PRIVACY_EVENT_SCHEMA_VERSION,
                "event_id": event_id,
                "request_id": request_id,
                "subject_ref": f"user:{user_id}",
                "action": "cancelled",
                "occurred_at": current.isoformat(),
            }
            _append_json(self._user_root(user_id) / request_id / f"{event_id}.json", event)
            return self._public([*events, event])


__all__ = (
    "MAX_EXPORT_ADAPTERS",
    "MAX_EXPORT_ADAPTER_BYTES",
    "MAX_EXPORT_ADAPTER_TOTAL_BYTES",
    "MAX_EXPORT_RELATIONAL_FIELD_BYTES",
    "MAX_EXPORT_RELATIONAL_ITEM_BYTES",
    "MAX_EXPORT_RELATIONAL_TOTAL_BYTES",
    "MAX_EXPORT_ROWS_PER_SECTION",
    "MAX_PERSONAL_EXPORT_TOTAL_BYTES",
    "PRIVACY_EVENT_SCHEMA_VERSION",
    "PRIVACY_EXPORT_SCHEMA_VERSION",
    "PRIVACY_REQUEST_SCHEMA_VERSION",
    "PersonalDataExportAdapterBinding",
    "PrivacyDeletionRequestStore",
    "PrivacyRightsConflict",
    "PrivacyRightsNotFound",
    "PrivacyRightsUnavailable",
    "build_personal_data_export",
)
