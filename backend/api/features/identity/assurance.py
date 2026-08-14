"""Durable MFA, login-challenge, session and redacted identity audit records.

The store is append-only. Reads never create directories or update last-seen
timestamps; a missing last-seen signal is reported as unavailable instead of
being invented. TOTP secrets use the existing application Fernet store and
raw recovery codes are returned once but never persisted.
"""

from __future__ import annotations

import base64
import binascii
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import struct
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Literal
from urllib.parse import quote

from api.core.environment import raw_setting, string_setting
from api.core.secrets import (
    SecretDecryptionError,
    SecretStoreConfigurationError,
    decrypt_secret_text,
    encrypt_secret_text,
    is_encrypted_secret_text,
    secret_store_configured,
)

ASSURANCE_SCHEMA_VERSION = "identity-assurance-event-v1"
TOTP_PERIOD_SECONDS = 30
TOTP_DIGITS = 6
TOTP_WINDOW = 1
LOGIN_CHALLENGE_MINUTES = 5
LOGIN_CHALLENGE_MAX_ATTEMPTS = 5
ENROLLMENT_MINUTES = 10
ENROLLMENT_MAX_ATTEMPTS = 5
RECOVERY_CODE_COUNT = 10
MAX_EVENTS_PER_USER = 10_000
MAX_EVENT_BYTES = 256 * 1024

_FORBIDDEN_RELEASE_ROOT = Path("/root/data/releases/globemind")
_EVENT_FILE_RE = re.compile(r"^(?P<sequence>[0-9]{8})-(?P<event_id>[0-9a-f]{32})\.json$")
_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
_TOTP_RE = re.compile(r"^[0-9]{6}$")
_RECOVERY_RE = re.compile(r"^[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}$")
_CHALLENGE_RE = re.compile(
    r"^mfa1\.(?P<user_id>[1-9][0-9]*)\."
    r"(?P<nonce>[A-Za-z0-9_-]{32,128})\.(?P<signature>[0-9a-f]{64})$"
)
_ACTIONS = frozenset(
    {
        "mfa.enrollment_started",
        "mfa.confirm_failed",
        "mfa.enabled",
        "mfa.disabled",
        "login.challenge_created",
        "login.challenge_failed",
        "login.completed",
        "session.issued",
        "session.revoked",
        "sessions.revoked",
    }
)


class IdentityAssuranceError(RuntimeError):
    """Base error safe for translation at the authenticated HTTP boundary."""


class IdentityAssuranceUnavailable(IdentityAssuranceError):
    """Durable assurance state cannot be trusted or written."""


class IdentityAssuranceConflict(IdentityAssuranceError):
    """The requested assurance transition is invalid for current state."""


class IdentityFactorRejected(IdentityAssuranceError):
    """A TOTP or recovery factor did not verify."""


class LoginChallengeRejected(IdentityAssuranceError):
    """A login challenge is invalid, expired, exhausted, or already used."""


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("identity assurance timestamps require timezone information")
    return current.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise IdentityAssuranceUnavailable("IDENTITY_ASSURANCE_TIMESTAMP_INVALID") from exc
    if parsed.tzinfo is None:
        raise IdentityAssuranceUnavailable("IDENTITY_ASSURANCE_TIMESTAMP_INVALID")
    return parsed.astimezone(timezone.utc)


def _canonical(payload: Any) -> bytes:
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise IdentityAssuranceUnavailable("IDENTITY_ASSURANCE_EVENT_INVALID") from exc


def _integrity_key() -> bytes:
    value = raw_setting("JWT_SECRET_KEY", "your-secret-key-change-in-production")
    if not value:
        raise IdentityAssuranceUnavailable("IDENTITY_ASSURANCE_INTEGRITY_KEY_UNAVAILABLE")
    return hmac.new(
        value.encode("utf-8"),
        b"globemind-identity-assurance-v1",
        hashlib.sha256,
    ).digest()


def _event_hmac(payload: dict[str, Any]) -> str:
    return hmac.new(_integrity_key(), _canonical(payload), hashlib.sha256).hexdigest()


def _reason_metadata(reason: str) -> dict[str, Any]:
    normalized = str(reason or "").strip()
    return {
        "reason_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "reason_length": len(normalized),
    }


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise IdentityAssuranceUnavailable("IDENTITY_ASSURANCE_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _path_has_symlink(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.exists() and current.is_symlink():
            return True
    return False


def _user_id(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("user id must be a positive integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("user id must be a positive integer") from exc
    if normalized <= 0:
        raise ValueError("user id must be a positive integer")
    return normalized


def hash_session_jti(jti: str) -> str:
    normalized = str(jti or "").strip()
    if len(normalized) < 20 or len(normalized) > 256:
        raise ValueError("session jti is invalid")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def hash_auth_version(auth_version: str) -> str:
    normalized = str(auth_version or "").strip()
    if not normalized:
        raise ValueError("auth version is required")
    return hashlib.sha256(normalized.encode("ascii")).hexdigest()


def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _secret_bytes(secret: str) -> bytes:
    normalized = str(secret or "").strip().upper()
    if not normalized or re.fullmatch(r"[A-Z2-7]{16,128}", normalized) is None:
        raise IdentityAssuranceUnavailable("TOTP_SECRET_INVALID")
    padded = normalized + ("=" * ((8 - len(normalized) % 8) % 8))
    try:
        return base64.b32decode(padded, casefold=False)
    except (ValueError, binascii.Error) as exc:
        raise IdentityAssuranceUnavailable("TOTP_SECRET_INVALID") from exc


def totp_code(secret: str, *, at: datetime | None = None, counter: int | None = None) -> str:
    if counter is None:
        counter = int(_utc(at).timestamp()) // TOTP_PERIOD_SECONDS
    if isinstance(counter, bool) or counter < 0:
        raise ValueError("TOTP counter must be non-negative")
    digest = hmac.new(
        _secret_bytes(secret),
        struct.pack(">Q", counter),
        hashlib.sha1,
    ).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{binary % (10**TOTP_DIGITS):0{TOTP_DIGITS}d}"


def verify_totp(
    secret: str,
    code: str,
    *,
    at: datetime | None = None,
    last_counter: int = -1,
) -> int | None:
    candidate = str(code or "")
    if _TOTP_RE.fullmatch(candidate) is None:
        return None
    current = int(_utc(at).timestamp()) // TOTP_PERIOD_SECONDS
    matched: int | None = None
    for counter in range(current - TOTP_WINDOW, current + TOTP_WINDOW + 1):
        if counter < 0:
            continue
        if hmac.compare_digest(totp_code(secret, counter=counter), candidate):
            matched = counter
    if matched is None or matched <= last_counter:
        return None
    return matched


def _recovery_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(12))
    return "-".join((raw[:4], raw[4:8], raw[8:]))


def _recovery_digest(code: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        code.encode("ascii"),
        salt,
        200_000,
        dklen=32,
    ).hex()


def _new_recovery_codes() -> tuple[list[str], list[dict[str, str]]]:
    codes: list[str] = []
    records: list[dict[str, str]] = []
    while len(codes) < RECOVERY_CODE_COUNT:
        code = _recovery_code()
        if code in codes:
            continue
        salt = secrets.token_bytes(16)
        codes.append(code)
        records.append({"salt": salt.hex(), "digest": _recovery_digest(code, salt)})
    return codes, records


def _normalize_recovery(value: str) -> str | None:
    normalized = str(value or "").strip().upper()
    return normalized if _RECOVERY_RE.fullmatch(normalized) else None


class IdentityAssuranceStore:
    """Append-only filesystem implementation with authenticated event chains."""

    def __init__(
        self,
        root: Path,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        raw = Path(root)
        if not raw.is_absolute():
            raise IdentityAssuranceUnavailable("IDENTITY_ASSURANCE_ROOT_NOT_ABSOLUTE")
        self.root = Path(os.path.abspath(os.fspath(raw)))
        try:
            self.root.relative_to(_FORBIDDEN_RELEASE_ROOT)
        except ValueError:
            pass
        else:
            raise IdentityAssuranceUnavailable("IDENTITY_ASSURANCE_ROOT_IN_RELEASE")
        try:
            if _path_has_symlink(self.root):
                raise IdentityAssuranceUnavailable(
                    "IDENTITY_ASSURANCE_ROOT_SYMLINK_REJECTED"
                )
        except IdentityAssuranceUnavailable:
            raise
        except OSError as exc:
            raise IdentityAssuranceUnavailable(
                "IDENTITY_ASSURANCE_ROOT_PROBE_FAILED"
            ) from exc
        self.clock = clock

    @property
    def users_root(self) -> Path:
        return self.root / "users"

    @property
    def lock_root(self) -> Path:
        return self.root / ".locks"

    @property
    def temporary_root(self) -> Path:
        return self.root / ".temporary"

    def availability(self) -> tuple[bool, str | None]:
        try:
            if _path_has_symlink(self.root):
                return False, "IDENTITY_ASSURANCE_ROOT_SYMLINK_REJECTED"
            if self.root.exists():
                if self.root.is_symlink() or not self.root.is_dir():
                    return False, "IDENTITY_ASSURANCE_ROOT_UNSAFE"
                if not os.access(self.root, os.R_OK | os.W_OK | os.X_OK):
                    return False, "IDENTITY_ASSURANCE_ROOT_NOT_READ_WRITE"
            else:
                parent = self.root.parent
                if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK):
                    return False, "IDENTITY_ASSURANCE_PARENT_NOT_WRITABLE"
        except OSError:
            return False, "IDENTITY_ASSURANCE_ROOT_PROBE_FAILED"
        return True, None

    def _event_root(self, user_id: int) -> Path:
        return self.users_root / str(_user_id(user_id)) / "events"

    def _ensure_write_root(self, user_id: int) -> None:
        available, reason = self.availability()
        if not available:
            raise IdentityAssuranceUnavailable(reason or "IDENTITY_ASSURANCE_UNAVAILABLE")
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.users_root.mkdir(exist_ok=True, mode=0o700)
            self.lock_root.mkdir(exist_ok=True, mode=0o700)
            self.temporary_root.mkdir(exist_ok=True, mode=0o700)
            event_root = self._event_root(user_id)
            event_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            for directory in (
                self.root,
                self.users_root,
                self.lock_root,
                self.temporary_root,
                event_root.parent,
                event_root,
            ):
                if directory.is_symlink() or not directory.is_dir():
                    raise IdentityAssuranceUnavailable("IDENTITY_ASSURANCE_DIRECTORY_UNSAFE")
                os.chmod(directory, 0o700)
        except IdentityAssuranceUnavailable:
            raise
        except OSError as exc:
            raise IdentityAssuranceUnavailable("IDENTITY_ASSURANCE_INITIALIZATION_FAILED") from exc

    @contextmanager
    def _locked(self, user_id: int) -> Iterator[None]:
        normalized = _user_id(user_id)
        self._ensure_write_root(normalized)
        lock_path = self.lock_root / f"{normalized}.lock"
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        locked = False
        try:
            descriptor = os.open(lock_path, flags, 0o600)
            lock_stat = os.fstat(descriptor)
            if (
                not stat.S_ISREG(lock_stat.st_mode)
                or lock_stat.st_nlink != 1
                or lock_stat.st_uid != os.geteuid()
            ):
                raise IdentityAssuranceUnavailable("IDENTITY_ASSURANCE_LOCK_UNSAFE")
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            locked = True
            yield
        except IdentityAssuranceError:
            raise
        except OSError as exc:
            raise IdentityAssuranceUnavailable("IDENTITY_ASSURANCE_LOCK_FAILED") from exc
        finally:
            if "descriptor" in locals():
                try:
                    if locked:
                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)

    def _load_events(self, user_id: int) -> list[dict[str, Any]]:
        normalized = _user_id(user_id)
        now = _utc(self.clock())
        available, reason = self.availability()
        if not available:
            raise IdentityAssuranceUnavailable(reason or "IDENTITY_ASSURANCE_UNAVAILABLE")
        event_root = self._event_root(normalized)
        if not event_root.exists():
            return []
        if event_root.is_symlink() or not event_root.is_dir() or _path_has_symlink(event_root):
            raise IdentityAssuranceUnavailable("IDENTITY_ASSURANCE_EVENT_ROOT_UNSAFE")
        try:
            paths = sorted(event_root.iterdir())
        except OSError as exc:
            raise IdentityAssuranceUnavailable("IDENTITY_ASSURANCE_EVENTS_UNREADABLE") from exc
        if len(paths) > MAX_EVENTS_PER_USER:
            raise IdentityAssuranceUnavailable("IDENTITY_ASSURANCE_EVENT_LIMIT_EXCEEDED")
        events: list[dict[str, Any]] = []
        previous_hmac: str | None = None
        previous_timestamp: datetime | None = None
        for expected_sequence, path in enumerate(paths, start=1):
            match = _EVENT_FILE_RE.fullmatch(path.name)
            try:
                stat = path.stat(follow_symlinks=False)
            except OSError as exc:
                raise IdentityAssuranceUnavailable("IDENTITY_ASSURANCE_EVENT_UNREADABLE") from exc
            if (
                match is None
                or path.is_symlink()
                or not path.is_file()
                or stat.st_nlink != 1
                or stat.st_size > MAX_EVENT_BYTES
                or int(match.group("sequence")) != expected_sequence
            ):
                raise IdentityAssuranceUnavailable("IDENTITY_ASSURANCE_EVENT_PATH_INVALID")
            try:
                event = json.loads(
                    path.read_text(encoding="utf-8"),
                    object_pairs_hook=_reject_duplicate_keys,
                    parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
                )
            except IdentityAssuranceError:
                raise
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
                raise IdentityAssuranceUnavailable("IDENTITY_ASSURANCE_EVENT_INVALID") from exc
            if not isinstance(event, dict):
                raise IdentityAssuranceUnavailable("IDENTITY_ASSURANCE_EVENT_INVALID")
            claimed_hmac = str(event.get("event_hmac_sha256") or "")
            unsigned = dict(event)
            unsigned.pop("event_hmac_sha256", None)
            if (
                event.get("schema_version") != ASSURANCE_SCHEMA_VERSION
                or event.get("user_id") != normalized
                or event.get("sequence") != expected_sequence
                or event.get("event_id") != match.group("event_id")
                or event.get("action") not in _ACTIONS
                or event.get("previous_event_hmac") != previous_hmac
                or _HEX_64_RE.fullmatch(str(event.get("reason_sha256") or "")) is None
                or not isinstance(event.get("reason_length"), int)
                or not isinstance(event.get("changed_fields"), list)
                or not isinstance(event.get("details"), dict)
                or _HEX_64_RE.fullmatch(claimed_hmac) is None
                or not hmac.compare_digest(claimed_hmac, _event_hmac(unsigned))
            ):
                raise IdentityAssuranceUnavailable("IDENTITY_ASSURANCE_EVENT_INTEGRITY_INVALID")
            event_timestamp = _parse_timestamp(event.get("timestamp"))
            if event_timestamp > now:
                raise IdentityAssuranceUnavailable(
                    "IDENTITY_ASSURANCE_CLOCK_ROLLBACK"
                )
            if (
                previous_timestamp is not None
                and event_timestamp < previous_timestamp
            ):
                raise IdentityAssuranceUnavailable(
                    "IDENTITY_ASSURANCE_CLOCK_ROLLBACK"
                )
            previous_hmac = claimed_hmac
            previous_timestamp = event_timestamp
            events.append(event)
        return events

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _append_locked(
        self,
        user_id: int,
        events: list[dict[str, Any]],
        *,
        action: str,
        changed_fields: list[str],
        reason: str,
        details: dict[str, Any],
        at: datetime,
    ) -> dict[str, Any]:
        if action not in _ACTIONS:
            raise ValueError("identity assurance action is invalid")
        if len(events) >= MAX_EVENTS_PER_USER:
            raise IdentityAssuranceUnavailable("IDENTITY_ASSURANCE_EVENT_LIMIT_EXCEEDED")
        event_timestamp = _utc(at)
        if event_timestamp > _utc(self.clock()):
            raise IdentityAssuranceUnavailable(
                "IDENTITY_ASSURANCE_FUTURE_TIMESTAMP"
            )
        if events and event_timestamp < _parse_timestamp(events[-1].get("timestamp")):
            raise IdentityAssuranceUnavailable("IDENTITY_ASSURANCE_CLOCK_ROLLBACK")
        sequence = len(events) + 1
        event_id = uuid.uuid4().hex
        event = {
            "schema_version": ASSURANCE_SCHEMA_VERSION,
            "event_id": event_id,
            "user_id": _user_id(user_id),
            "sequence": sequence,
            "timestamp": _timestamp(event_timestamp),
            "action": action,
            "previous_event_hmac": events[-1]["event_hmac_sha256"] if events else None,
            **_reason_metadata(reason),
            "changed_fields": list(changed_fields),
            "details": details,
        }
        event["event_hmac_sha256"] = _event_hmac(event)
        encoded = _canonical(event)
        if len(encoded) > MAX_EVENT_BYTES:
            raise IdentityAssuranceUnavailable("IDENTITY_ASSURANCE_EVENT_TOO_LARGE")
        root = self._event_root(user_id)
        destination = root / f"{sequence:08d}-{event_id}.json"
        descriptor = -1
        temporary = ""
        try:
            descriptor, temporary = tempfile.mkstemp(
                prefix=".identity-", dir=self.temporary_root
            )
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=True) as handle:
                descriptor = -1
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary, destination, follow_symlinks=False)
            os.unlink(temporary)
            temporary = ""
            self._fsync_directory(root)
        except OSError as exc:
            raise IdentityAssuranceUnavailable("IDENTITY_ASSURANCE_EVENT_WRITE_FAILED") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
        events.append(event)
        return event

    @staticmethod
    def _mfa_state(events: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
        state: dict[str, Any] = {"status": "disabled"}
        for event in events:
            action = event["action"]
            if action == "mfa.enrollment_started":
                state = {"status": "pending", **event["details"]}
            elif action == "mfa.enabled":
                state = {"status": "enabled", **event["details"]}
            elif action == "mfa.disabled":
                state = {"status": "disabled"}
        if state["status"] == "pending" and _parse_timestamp(state.get("expires_at")) <= now:
            return {"status": "disabled"}
        return state

    @staticmethod
    def _used_factors(events: list[dict[str, Any]]) -> tuple[int, set[int]]:
        last_counter = -1
        used_recovery: set[int] = set()
        for event in events:
            if event["action"] == "mfa.enabled":
                last_counter = max(last_counter, int(event["details"].get("totp_counter", -1)))
                used_recovery = set()
            elif event["action"] == "mfa.disabled":
                factor = event["details"].get("factor")
                if factor == "totp":
                    last_counter = max(last_counter, int(event["details"].get("counter", -1)))
                elif factor == "recovery_code":
                    used_recovery.add(int(event["details"].get("recovery_index", -1)))
            elif event["action"] == "login.completed":
                factor = event["details"].get("factor")
                if factor == "totp":
                    last_counter = max(last_counter, int(event["details"].get("counter", -1)))
                elif factor == "recovery_code":
                    used_recovery.add(int(event["details"].get("recovery_index", -1)))
        return last_counter, used_recovery

    @staticmethod
    def _pending_confirmation_failures(events: list[dict[str, Any]]) -> int:
        latest_start = max(
            (
                event["sequence"]
                for event in events
                if event["action"] == "mfa.enrollment_started"
            ),
            default=0,
        )
        return sum(
            1
            for event in events
            if event["sequence"] > latest_start
            and event["action"] == "mfa.confirm_failed"
        )

    @staticmethod
    def _secret(state: dict[str, Any]) -> str:
        encrypted = state.get("encrypted_secret")
        if not isinstance(encrypted, str) or not is_encrypted_secret_text(encrypted):
            raise IdentityAssuranceUnavailable("MFA_SECRET_UNAVAILABLE")
        try:
            secret = decrypt_secret_text(encrypted)
        except (SecretStoreConfigurationError, SecretDecryptionError) as exc:
            raise IdentityAssuranceUnavailable("MFA_SECRET_DECRYPTION_FAILED") from exc
        if not secret:
            raise IdentityAssuranceUnavailable("MFA_SECRET_UNAVAILABLE")
        return secret

    @staticmethod
    def _verify_recovery(
        state: dict[str, Any],
        recovery_code: str,
        used: set[int],
    ) -> int | None:
        candidate = _normalize_recovery(recovery_code)
        records = state.get("recovery_codes")
        if candidate is None or not isinstance(records, list):
            return None
        matched: int | None = None
        for index, record in enumerate(records):
            try:
                salt = bytes.fromhex(str(record["salt"]))
                expected = str(record["digest"])
                actual = _recovery_digest(candidate, salt)
            except (KeyError, TypeError, ValueError):
                raise IdentityAssuranceUnavailable("MFA_RECOVERY_RECORD_INVALID")
            if hmac.compare_digest(actual, expected) and index not in used:
                matched = index
        return matched

    def status(self, user_id: int) -> dict[str, Any]:
        now = _utc(self.clock())
        events = self._load_events(user_id)
        storage_available, storage_reason = self.availability()
        if not storage_available:
            raise IdentityAssuranceUnavailable(
                storage_reason or "IDENTITY_ASSURANCE_UNAVAILABLE"
            )
        state = self._mfa_state(events, now)
        _last_counter, used_recovery = self._used_factors(events)
        recovery_total = len(state.get("recovery_codes", [])) if state["status"] == "enabled" else 0
        pending_failures = self._pending_confirmation_failures(events)
        secret_storage_available = secret_store_configured()
        return {
            "schema_version": "identity-mfa-status-v1",
            "status": state["status"],
            "enabled": state["status"] == "enabled",
            "pending_enrollment": state["status"] == "pending",
            "pending_expires_at": state.get("expires_at") if state["status"] == "pending" else None,
            "pending_attempts_remaining": (
                max(0, ENROLLMENT_MAX_ATTEMPTS - pending_failures)
                if state["status"] == "pending"
                else None
            ),
            "recovery_codes_remaining": max(0, recovery_total - len(used_recovery)),
            "assurance": {
                "type": "totp-rfc6238",
                "enrollment_state": (
                    "available" if secret_storage_available else "unavailable"
                ),
                "institutional_sso": "unavailable",
                "device_attestation": "unavailable",
                "independent_security_review": "unavailable",
            },
            "capabilities": {
                "totp_enrollment": (
                    "available" if secret_storage_available else "unavailable"
                ),
                "recovery_codes": (
                    "available" if secret_storage_available else "unavailable"
                ),
                "tracked_sessions": "available",
            },
            "storage": {
                "status": "available",
                "backend": "append-only-filesystem",
                "writes_on_read": False,
                "last_seen": "unavailable",
            },
            "capability_inventory": {
                "schema_version": "identity-security-capabilities-v1",
                "evidence_scope": "repository_source_and_local_ledger_only",
                "totp": (
                    "available" if secret_storage_available else "unavailable"
                ),
                "recovery_codes": (
                    "available" if secret_storage_available else "unavailable"
                ),
                "tracked_web_sessions": "available",
                "institutional_sso": "not_configured",
                "security_keys": "not_configured",
                "trusted_devices": "not_configured",
                "device_attestation": "not_configured",
                "runtime_idp_attestation": "not_available",
                "independent_security_review": "not_provided",
            },
        }

    def begin_enrollment(self, user_id: int, *, account_label: str) -> dict[str, Any]:
        if not secret_store_configured():
            raise IdentityAssuranceUnavailable("MFA_SECRET_STORE_NOT_CONFIGURED")
        normalized = _user_id(user_id)
        now = _utc(self.clock())
        secret = generate_totp_secret()
        try:
            encrypted = encrypt_secret_text(secret)
        except SecretStoreConfigurationError as exc:
            raise IdentityAssuranceUnavailable("MFA_SECRET_STORE_NOT_CONFIGURED") from exc
        if not encrypted:
            raise IdentityAssuranceUnavailable("MFA_SECRET_ENCRYPTION_FAILED")
        expires_at = now + timedelta(minutes=ENROLLMENT_MINUTES)
        with self._locked(normalized):
            events = self._load_events(normalized)
            current_status = self._mfa_state(events, now)["status"]
            if current_status == "enabled":
                raise IdentityAssuranceConflict("MFA_ALREADY_ENABLED")
            if current_status == "pending":
                raise IdentityAssuranceConflict("MFA_ENROLLMENT_ALREADY_PENDING")
            self._append_locked(
                normalized,
                events,
                action="mfa.enrollment_started",
                changed_fields=["mfa.status", "mfa.encrypted_secret"],
                reason="MFA_ENROLLMENT_REQUESTED",
                details={"encrypted_secret": encrypted, "expires_at": _timestamp(expires_at)},
                at=now,
            )
        issuer = "GlobeMind"
        label = quote(f"{issuer}:{str(account_label or '').strip()}", safe="")
        uri = (
            f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
            f"&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_PERIOD_SECONDS}"
        )
        return {
            "schema_version": "identity-mfa-enrollment-v1",
            "status": "pending",
            "secret": secret,
            "otpauth_uri": uri,
            "expires_at": _timestamp(expires_at),
            "secret_display": "one_time",
            "max_confirmation_attempts": ENROLLMENT_MAX_ATTEMPTS,
        }

    def confirm_enrollment(self, user_id: int, code: str) -> dict[str, Any]:
        normalized = _user_id(user_id)
        now = _utc(self.clock())
        with self._locked(normalized):
            events = self._load_events(normalized)
            state = self._mfa_state(events, now)
            if state["status"] != "pending":
                raise IdentityAssuranceConflict("MFA_PENDING_ENROLLMENT_REQUIRED")
            if self._pending_confirmation_failures(events) >= ENROLLMENT_MAX_ATTEMPTS:
                raise IdentityFactorRejected("MFA_ENROLLMENT_ATTEMPTS_EXHAUSTED")
            secret = self._secret(state)
            counter = verify_totp(secret, code, at=now)
            if counter is None:
                self._append_locked(
                    normalized,
                    events,
                    action="mfa.confirm_failed",
                    changed_fields=["mfa.confirm_attempt"],
                    reason="MFA_CODE_REJECTED",
                    details={},
                    at=now,
                )
                raise IdentityFactorRejected("MFA_CODE_REJECTED")
            codes, recovery_records = _new_recovery_codes()
            self._append_locked(
                normalized,
                events,
                action="mfa.enabled",
                changed_fields=["mfa.status", "mfa.recovery_codes", "mfa.totp_counter"],
                reason="MFA_ENROLLMENT_CONFIRMED",
                details={
                    "encrypted_secret": state["encrypted_secret"],
                    "recovery_codes": recovery_records,
                    "totp_counter": counter,
                },
                at=now,
            )
        return {
            "schema_version": "identity-mfa-confirmation-v1",
            "status": "enabled",
            "recovery_codes": codes,
            "recovery_codes_display": "one_time",
        }

    def create_login_challenge(
        self, user_id: int, *, auth_version: str
    ) -> dict[str, Any]:
        normalized = _user_id(user_id)
        now = _utc(self.clock())
        unsigned_token = f"mfa1.{normalized}.{secrets.token_urlsafe(32)}"
        signature = hmac.new(
            _integrity_key(),
            unsigned_token.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        token = f"{unsigned_token}.{signature}"
        challenge_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        expires_at = now + timedelta(minutes=LOGIN_CHALLENGE_MINUTES)
        with self._locked(normalized):
            events = self._load_events(normalized)
            if self._mfa_state(events, now)["status"] != "enabled":
                raise IdentityAssuranceConflict("MFA_NOT_ENABLED")
            self._append_locked(
                normalized,
                events,
                action="login.challenge_created",
                changed_fields=["login_challenge"],
                reason="PASSWORD_VERIFIED_MFA_REQUIRED",
                details={
                    "challenge_sha256": challenge_hash,
                    "expires_at": _timestamp(expires_at),
                    "max_attempts": LOGIN_CHALLENGE_MAX_ATTEMPTS,
                    "auth_version_sha256": hash_auth_version(auth_version),
                },
                at=now,
            )
        return {
            "schema_version": "identity-login-challenge-v1",
            "mfa_required": True,
            "challenge": token,
            "expires_in": LOGIN_CHALLENGE_MINUTES * 60,
            "methods": ["totp", "recovery_code"],
        }

    @staticmethod
    def challenge_user_id(challenge: str) -> int:
        match = _CHALLENGE_RE.fullmatch(str(challenge or ""))
        if match is None:
            raise LoginChallengeRejected("LOGIN_CHALLENGE_REJECTED")
        unsigned_token = str(challenge).rsplit(".", 1)[0]
        expected_signature = hmac.new(
            _integrity_key(),
            unsigned_token.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(match.group("signature"), expected_signature):
            raise LoginChallengeRejected("LOGIN_CHALLENGE_REJECTED")
        return _user_id(match.group("user_id"))

    def complete_login_challenge(
        self,
        challenge: str,
        *,
        code: str | None,
        recovery_code: str | None,
        jti: str,
        issued_at: datetime,
        expires_at: datetime,
        auth_version: str,
    ) -> int:
        user_id = self.challenge_user_id(challenge)
        challenge_hash = hashlib.sha256(challenge.encode("ascii")).hexdigest()
        now = _utc(self.clock())
        with self._locked(user_id):
            events = self._load_events(user_id)
            created = next(
                (
                    event
                    for event in reversed(events)
                    if event["action"] == "login.challenge_created"
                    and event["details"].get("challenge_sha256") == challenge_hash
                ),
                None,
            )
            completed = any(
                event["action"] == "login.completed"
                and event["details"].get("challenge_sha256") == challenge_hash
                for event in events
            )
            failures = sum(
                1
                for event in events
                if event["action"] == "login.challenge_failed"
                and event["details"].get("challenge_sha256") == challenge_hash
            )
            if (
                created is None
                or completed
                or _parse_timestamp(created["details"].get("expires_at")) <= now
                or failures >= int(created["details"].get("max_attempts", 0))
                or not hmac.compare_digest(
                    str(created["details"].get("auth_version_sha256") or ""),
                    hash_auth_version(auth_version),
                )
            ):
                raise LoginChallengeRejected("LOGIN_CHALLENGE_REJECTED")
            state = self._mfa_state(events, now)
            if state["status"] != "enabled":
                raise LoginChallengeRejected("LOGIN_CHALLENGE_REJECTED")
            last_counter, used_recovery = self._used_factors(events)
            factor_details: dict[str, Any]
            if code is not None:
                counter = verify_totp(self._secret(state), code, at=now, last_counter=last_counter)
                if counter is None:
                    factor_details = {}
                else:
                    factor_details = {"factor": "totp", "counter": counter}
            else:
                recovery_index = self._verify_recovery(state, recovery_code or "", used_recovery)
                factor_details = (
                    {}
                    if recovery_index is None
                    else {"factor": "recovery_code", "recovery_index": recovery_index}
                )
            if not factor_details:
                self._append_locked(
                    user_id,
                    events,
                    action="login.challenge_failed",
                    changed_fields=["login_challenge.attempts"],
                    reason="MFA_FACTOR_REJECTED",
                    details={"challenge_sha256": challenge_hash},
                    at=now,
                )
                raise IdentityFactorRejected("MFA_FACTOR_REJECTED")
            session = self._session_details(
                jti=jti,
                issued_at=issued_at,
                expires_at=expires_at,
                auth_version=auth_version,
            )
            self._append_locked(
                user_id,
                events,
                action="login.completed",
                changed_fields=["login_challenge.status", "mfa.factor", "sessions[]"],
                reason="MFA_LOGIN_COMPLETED",
                details={
                    "challenge_sha256": challenge_hash,
                    **factor_details,
                    "session": session,
                },
                at=now,
            )
        return user_id

    @staticmethod
    def _session_details(
        *,
        jti: str,
        issued_at: datetime,
        expires_at: datetime,
        auth_version: str,
    ) -> dict[str, Any]:
        issued = _utc(issued_at)
        expires = _utc(expires_at)
        if expires <= issued:
            raise ValueError("session expiry must follow issuance")
        return {
            "jti_sha256": hash_session_jti(jti),
            "auth_version_sha256": hash_auth_version(auth_version),
            "issued_at": _timestamp(issued),
            "expires_at": _timestamp(expires),
            "last_seen_status": "unavailable",
        }

    def issue_session(
        self,
        user_id: int,
        *,
        jti: str,
        issued_at: datetime,
        expires_at: datetime,
        auth_version: str,
    ) -> dict[str, Any]:
        normalized = _user_id(user_id)
        details = self._session_details(
            jti=jti,
            issued_at=issued_at,
            expires_at=expires_at,
            auth_version=auth_version,
        )
        with self._locked(normalized):
            events = self._load_events(normalized)
            if any(
                session["jti_sha256"] == details["jti_sha256"]
                for session in self._sessions(events).values()
            ):
                raise IdentityAssuranceConflict("SESSION_JTI_COLLISION")
            self._append_locked(
                normalized,
                events,
                action="session.issued",
                changed_fields=["sessions[]"],
                reason="PASSWORD_LOGIN_COMPLETED",
                details={"session": details},
                at=_utc(issued_at),
            )
        return details

    @staticmethod
    def _sessions(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        sessions: dict[str, dict[str, Any]] = {}
        revoked: set[str] = set()
        for event in events:
            if event["action"] in {"session.issued", "login.completed"}:
                raw = event["details"].get("session")
                if not isinstance(raw, dict):
                    raise IdentityAssuranceUnavailable("IDENTITY_SESSION_EVENT_INVALID")
                session_id = str(raw.get("jti_sha256") or "")
                if _HEX_64_RE.fullmatch(session_id) is None or session_id in sessions:
                    raise IdentityAssuranceUnavailable("IDENTITY_SESSION_EVENT_INVALID")
                sessions[session_id] = dict(raw)
            elif event["action"] == "session.revoked":
                revoked.add(str(event["details"].get("session_id") or ""))
            elif event["action"] == "sessions.revoked":
                targets = event["details"].get("session_ids")
                if not isinstance(targets, list):
                    raise IdentityAssuranceUnavailable("IDENTITY_SESSION_EVENT_INVALID")
                revoked.update(str(item) for item in targets)
        for session_id, session in sessions.items():
            session["revoked"] = session_id in revoked
        return sessions

    def session_valid(self, user_id: int, *, jti: str, auth_version: str) -> bool:
        now = _utc(self.clock())
        session_id = hash_session_jti(jti)
        sessions = self._sessions(self._load_events(user_id))
        session = sessions.get(session_id)
        if session is None or session.get("revoked") is True:
            return False
        try:
            version_matches = hmac.compare_digest(
                str(session["auth_version_sha256"]),
                hash_auth_version(auth_version),
            )
            not_expired = _parse_timestamp(session["expires_at"]) > now
        except (KeyError, TypeError, ValueError) as exc:
            raise IdentityAssuranceUnavailable("IDENTITY_SESSION_EVENT_INVALID") from exc
        return version_matches and not_expired

    def list_sessions(
        self,
        user_id: int,
        *,
        current_jti: str,
        auth_version: str,
    ) -> dict[str, Any]:
        now = _utc(self.clock())
        current_id = hash_session_jti(current_jti)
        version_hash = hash_auth_version(auth_version)
        sessions = self._sessions(self._load_events(user_id))
        items: list[dict[str, Any]] = []
        for session_id, session in sessions.items():
            if session.get("revoked") is True:
                status: Literal["active", "revoked", "expired", "password_changed"] = "revoked"
            elif _parse_timestamp(session.get("expires_at")) <= now:
                status = "expired"
            elif not hmac.compare_digest(
                str(session.get("auth_version_sha256") or ""), version_hash
            ):
                status = "password_changed"
            else:
                status = "active"
            items.append(
                {
                    "session_id": session_id,
                    "issued_at": session["issued_at"],
                    "expires_at": session["expires_at"],
                    "status": status,
                    "current": session_id == current_id,
                    "last_seen_at": None,
                    "last_seen_status": "unavailable",
                }
            )
        items.sort(key=lambda item: (item["issued_at"], item["session_id"]), reverse=True)
        return {
            "schema_version": "identity-session-list-v1",
            "tracking": "login-issued-tokens-only",
            "untracked_tokens": "not_listed_and_not_claimed_revocable",
            "items": items,
        }

    def revoke_session(self, user_id: int, *, session_id: str, reason: str) -> None:
        normalized = _user_id(user_id)
        if _HEX_64_RE.fullmatch(str(session_id or "")) is None:
            raise IdentityAssuranceConflict("SESSION_NOT_FOUND")
        now = _utc(self.clock())
        with self._locked(normalized):
            events = self._load_events(normalized)
            sessions = self._sessions(events)
            if session_id not in sessions:
                raise IdentityAssuranceConflict("SESSION_NOT_FOUND")
            if sessions[session_id].get("revoked") is not True:
                self._append_locked(
                    normalized,
                    events,
                    action="session.revoked",
                    changed_fields=["sessions.status"],
                    reason=reason,
                    details={"session_id": session_id},
                    at=now,
                )

    def revoke_other_sessions(
        self,
        user_id: int,
        *,
        current_jti: str,
        reason: str,
    ) -> int:
        normalized = _user_id(user_id)
        current_id = hash_session_jti(current_jti)
        now = _utc(self.clock())
        with self._locked(normalized):
            events = self._load_events(normalized)
            sessions = self._sessions(events)
            targets = sorted(
                session_id
                for session_id, session in sessions.items()
                if session_id != current_id
                and session.get("revoked") is not True
                and _parse_timestamp(session.get("expires_at")) > now
            )
            if targets:
                self._append_locked(
                    normalized,
                    events,
                    action="sessions.revoked",
                    changed_fields=["sessions.status"],
                    reason=reason,
                    details={"session_ids": targets, "except_session_id": current_id},
                    at=now,
                )
        return len(targets)

    def revoke_all_sessions(self, user_id: int, *, reason: str) -> int:
        normalized = _user_id(user_id)
        now = _utc(self.clock())
        with self._locked(normalized):
            events = self._load_events(normalized)
            sessions = self._sessions(events)
            targets = sorted(
                session_id
                for session_id, session in sessions.items()
                if session.get("revoked") is not True
            )
            if targets:
                self._append_locked(
                    normalized,
                    events,
                    action="sessions.revoked",
                    changed_fields=["sessions.status"],
                    reason=reason,
                    details={"session_ids": targets, "except_session_id": None},
                    at=now,
                )
        return len(targets)

    def disable_mfa(
        self,
        user_id: int,
        *,
        code: str | None,
        recovery_code: str | None,
    ) -> None:
        normalized = _user_id(user_id)
        now = _utc(self.clock())
        with self._locked(normalized):
            events = self._load_events(normalized)
            state = self._mfa_state(events, now)
            if state["status"] != "enabled":
                raise IdentityAssuranceConflict("MFA_NOT_ENABLED")
            last_counter, used_recovery = self._used_factors(events)
            if code is not None:
                counter = verify_totp(self._secret(state), code, at=now, last_counter=last_counter)
                factor = None if counter is None else {"factor": "totp", "counter": counter}
            else:
                index = self._verify_recovery(state, recovery_code or "", used_recovery)
                factor = (
                    None
                    if index is None
                    else {"factor": "recovery_code", "recovery_index": index}
                )
            if factor is None:
                raise IdentityFactorRejected("MFA_FACTOR_REJECTED")
            self._append_locked(
                normalized,
                events,
                action="mfa.disabled",
                changed_fields=["mfa.status"],
                reason="MFA_DISABLED_BY_USER",
                details=factor,
                at=now,
            )

    def audit(self, user_id: int, *, limit: int = 100) -> dict[str, Any]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 200:
            raise ValueError("audit limit must be between 1 and 200")
        events = self._load_events(user_id)
        public = [
            {
                "event_id": event["event_id"],
                "sequence": event["sequence"],
                "timestamp": event["timestamp"],
                "action": event["action"],
                "reason_sha256": event["reason_sha256"],
                "reason_length": event["reason_length"],
                "changed_fields": event["changed_fields"],
            }
            for event in reversed(events[-limit:])
        ]
        return {
            "schema_version": "identity-security-audit-v1",
            "events": public,
            "redaction": {
                "token": "never_stored",
                "totp_secret": "not_in_audit",
                "recovery_code": "never_stored",
                "reason": "sha256_and_length_only",
                "body_fields": "none",
            },
        }


def configured_identity_assurance_store() -> IdentityAssuranceStore:
    return IdentityAssuranceStore(
        Path(
            string_setting(
                "IDENTITY_ASSURANCE_ROOT",
                "/root/data/web/identity-assurance",
            )
        )
    )


__all__ = (
    "ASSURANCE_SCHEMA_VERSION",
    "IdentityAssuranceConflict",
    "IdentityAssuranceError",
    "IdentityAssuranceStore",
    "IdentityAssuranceUnavailable",
    "IdentityFactorRejected",
    "LoginChallengeRejected",
    "configured_identity_assurance_store",
    "generate_totp_secret",
    "hash_auth_version",
    "hash_session_jti",
    "totp_code",
    "verify_totp",
)
