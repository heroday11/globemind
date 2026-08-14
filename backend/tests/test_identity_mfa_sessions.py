from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.core import db as db_module
from api.core.identity_security import (
    create_access_token,
    get_user_from_access_token,
    hash_password,
)
from api.features.identity import (
    IdentityAssuranceConflict,
    IdentityAssuranceStore,
    IdentityAssuranceUnavailable,
    IdentityFactorRejected,
    LoginChallengeRejected,
    LoginMfaVerifyRequest,
    LoginRequest,
    configured_identity_assurance_store,
    hash_session_jti,
    totp_code,
    verify_totp,
)
from api.routes.auth import (
    begin_mfa_enrollment,
    complete_mfa_login,
    get_mfa_status,
    list_security_sessions,
    login,
)
from api.services.auth import get_active_user_from_access_token


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


class _Query:
    def __init__(self, row: object | None):
        self.row = row

    def filter(self, *_args: object, **_kwargs: object) -> "_Query":
        return self

    def first(self) -> object | None:
        return self.row


class _Session:
    def __init__(self, row: object | None):
        self.row = row
        self.commit_calls = 0

    def query(self, *_args: object, **_kwargs: object) -> _Query:
        return _Query(self.row)

    def commit(self) -> None:
        self.commit_calls += 1

    def refresh(self, _row: object) -> None:
        return None

    def close(self) -> None:
        return None


def _user() -> SimpleNamespace:
    return SimpleNamespace(
        id=71,
        username="alice",
        password_hash=hash_password("database-password-1"),
        full_name="Alice",
        email="alice@example.test",
        phone="",
        created_at=None,
        updated_at=None,
        is_active=True,
        last_login_at=None,
        role="user",
        avatar_url="",
        api_keys=None,
        active_provider=None,
        default_model=None,
        base_url=None,
    )


def _event_files(store: IdentityAssuranceStore, user_id: int) -> list[Path]:
    root = store.root / "users" / str(user_id) / "events"
    return sorted(root.glob("*.json")) if root.exists() else []


def test_rfc6238_window_is_strict_and_counter_replay_is_rejected() -> None:
    secret = "JBSWY3DPEHPK3PXP"
    at = datetime.fromtimestamp(59, tz=timezone.utc)

    assert totp_code(secret, at=at) == "996554"
    current_counter = int(at.timestamp()) // 30
    previous = totp_code(secret, counter=current_counter - 1)
    future = totp_code(secret, counter=current_counter + 1)
    assert verify_totp(secret, previous, at=at) == current_counter - 1
    assert verify_totp(secret, future, at=at) == current_counter + 1
    assert verify_totp(secret, "12345", at=at) is None
    assert verify_totp(secret, "１２３４５６", at=at) is None
    assert verify_totp(secret, future, at=at, last_counter=current_counter + 1) is None


def test_constructor_and_first_status_audit_reads_do_not_write(tmp_path: Path) -> None:
    root = tmp_path / "identity-assurance-not-created"
    store = IdentityAssuranceStore(root)

    assert not root.exists()
    assert store.availability() == (True, None)
    assert store.status(7)["status"] == "disabled"
    assert store.audit(7)["events"] == []
    assert not root.exists()


def test_status_reports_enrollment_unavailable_without_fernet_and_stays_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "identity-assurance-no-fernet"
    monkeypatch.delenv("USER_API_KEYS_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("USER_API_KEYS_DECRYPTION_KEYS", raising=False)

    status = IdentityAssuranceStore(root).status(7)

    assert status["assurance"]["enrollment_state"] == "unavailable"
    assert status["capabilities"]["totp_enrollment"] == "unavailable"
    assert status["capabilities"]["recovery_codes"] == "unavailable"
    assert status["storage"]["status"] == "available"
    assert not root.exists()


def test_enrollment_challenge_recovery_sessions_and_redacted_audit_are_durable(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    store = IdentityAssuranceStore(tmp_path / "identity-assurance", clock=clock)
    enrollment = store.begin_enrollment(7, account_label="alice")
    assert enrollment["status"] == "pending"
    assert enrollment["secret_display"] == "one_time"
    assert enrollment["secret"] in enrollment["otpauth_uri"]

    confirmation = store.confirm_enrollment(
        7,
        totp_code(enrollment["secret"], at=clock.now),
    )
    recovery_codes = confirmation["recovery_codes"]
    assert len(recovery_codes) == 10
    assert len(set(recovery_codes)) == 10
    assert store.status(7)["enabled"] is True

    serialized_events = b"\n".join(path.read_bytes() for path in _event_files(store, 7))
    assert enrollment["secret"].encode() not in serialized_events
    for recovery_code in recovery_codes:
        assert recovery_code.encode() not in serialized_events

    # The enrollment-confirmation counter is consumed; the same code cannot
    # immediately authorize a login challenge.
    challenge = store.create_login_challenge(7, auth_version="auth-version-1")
    with pytest.raises(IdentityFactorRejected):
        store.complete_login_challenge(
            challenge["challenge"],
            code=totp_code(enrollment["secret"], at=clock.now),
            recovery_code=None,
            jti="j" * 43,
            issued_at=clock.now,
            expires_at=clock.now + timedelta(hours=24),
            auth_version="auth-version-1",
        )

    clock.advance(30)
    next_challenge = store.create_login_challenge(7, auth_version="auth-version-1")
    jti = "k" * 43
    assert (
        store.complete_login_challenge(
            next_challenge["challenge"],
            code=totp_code(enrollment["secret"], at=clock.now),
            recovery_code=None,
            jti=jti,
            issued_at=clock.now,
            expires_at=clock.now + timedelta(hours=24),
            auth_version="auth-version-1",
        )
        == 7
    )
    with pytest.raises(LoginChallengeRejected):
        store.complete_login_challenge(
            next_challenge["challenge"],
            code=totp_code(enrollment["secret"], at=clock.now),
            recovery_code=None,
            jti="m" * 43,
            issued_at=clock.now,
            expires_at=clock.now + timedelta(hours=24),
            auth_version="auth-version-1",
        )

    assert store.session_valid(7, jti=jti, auth_version="auth-version-1") is True
    sessions = store.list_sessions(
        7,
        current_jti=jti,
        auth_version="auth-version-1",
    )
    assert sessions["items"] == [
        {
            "session_id": hash_session_jti(jti),
            "issued_at": "2026-08-09T08:00:30Z",
            "expires_at": "2026-08-10T08:00:30Z",
            "status": "active",
            "current": True,
            "last_seen_at": None,
            "last_seen_status": "unavailable",
        }
    ]
    store.revoke_session(7, session_id=hash_session_jti(jti), reason="user revoked device")
    assert store.session_valid(7, jti=jti, auth_version="auth-version-1") is False

    recovery_challenge = store.create_login_challenge(7, auth_version="auth-version-1")
    recovery_jti = "r" * 43
    store.complete_login_challenge(
        recovery_challenge["challenge"],
        code=None,
        recovery_code=recovery_codes[0],
        jti=recovery_jti,
        issued_at=clock.now,
        expires_at=clock.now + timedelta(hours=24),
        auth_version="auth-version-1",
    )
    another_challenge = store.create_login_challenge(7, auth_version="auth-version-1")
    with pytest.raises(IdentityFactorRejected):
        store.complete_login_challenge(
            another_challenge["challenge"],
            code=None,
            recovery_code=recovery_codes[0],
            jti="s" * 43,
            issued_at=clock.now,
            expires_at=clock.now + timedelta(hours=24),
            auth_version="auth-version-1",
        )

    audit = store.audit(7)
    audit_text = json.dumps(audit)
    assert enrollment["secret"] not in audit_text
    assert recovery_codes[0] not in audit_text
    assert next_challenge["challenge"] not in audit_text
    assert jti not in audit_text
    assert audit["redaction"]["token"] == "never_stored"

    reloaded = IdentityAssuranceStore(store.root, clock=clock)
    assert reloaded.status(7)["enabled"] is True
    assert reloaded.session_valid(
        7,
        jti=recovery_jti,
        auth_version="auth-version-1",
    ) is True


def test_challenge_attempt_limit_expiry_auth_version_and_disable_factor(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    store = IdentityAssuranceStore(tmp_path / "identity-assurance", clock=clock)
    enrollment = store.begin_enrollment(9, account_label="alice")
    recovery_codes = store.confirm_enrollment(
        9,
        totp_code(enrollment["secret"], at=clock.now),
    )["recovery_codes"]
    challenge = store.create_login_challenge(9, auth_version="auth-version-1")
    tampered_challenge = challenge["challenge"].replace("mfa1.9.", "mfa1.99.", 1)
    with pytest.raises(LoginChallengeRejected):
        store.challenge_user_id(tampered_challenge)
    for _attempt in range(5):
        with pytest.raises(IdentityFactorRejected):
            store.complete_login_challenge(
                challenge["challenge"],
                code="000000",
                recovery_code=None,
                jti="x" * 43,
                issued_at=clock.now,
                expires_at=clock.now + timedelta(hours=24),
                auth_version="auth-version-1",
            )
    with pytest.raises(LoginChallengeRejected):
        store.complete_login_challenge(
            challenge["challenge"],
            code=None,
            recovery_code=recovery_codes[1],
            jti="y" * 43,
            issued_at=clock.now,
            expires_at=clock.now + timedelta(hours=24),
            auth_version="auth-version-1",
        )

    version_bound = store.create_login_challenge(9, auth_version="auth-version-1")
    with pytest.raises(LoginChallengeRejected):
        store.complete_login_challenge(
            version_bound["challenge"],
            code=None,
            recovery_code=recovery_codes[1],
            jti="z" * 43,
            issued_at=clock.now,
            expires_at=clock.now + timedelta(hours=24),
            auth_version="auth-version-2",
        )

    expired = store.create_login_challenge(9, auth_version="auth-version-1")
    clock.advance(301)
    with pytest.raises(LoginChallengeRejected):
        store.complete_login_challenge(
            expired["challenge"],
            code=None,
            recovery_code=recovery_codes[1],
            jti="q" * 43,
            issued_at=clock.now,
            expires_at=clock.now + timedelta(hours=24),
            auth_version="auth-version-1",
        )

    store.disable_mfa(9, code=None, recovery_code=recovery_codes[1])
    assert store.status(9)["status"] == "disabled"
    with pytest.raises(IdentityAssuranceConflict):
        store.create_login_challenge(9, auth_version="auth-version-1")


def test_pending_enrollment_has_finite_attempts_and_cannot_be_restarted_early(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    store = IdentityAssuranceStore(tmp_path / "identity-assurance", clock=clock)
    enrollment = store.begin_enrollment(12, account_label="alice")
    assert store.status(12)["pending_attempts_remaining"] == 5
    with pytest.raises(IdentityAssuranceConflict, match="MFA_ENROLLMENT_ALREADY_PENDING"):
        store.begin_enrollment(12, account_label="alice")

    valid_code = totp_code(enrollment["secret"], at=clock.now)
    invalid_code = "000000" if valid_code != "000000" else "111111"
    for remaining in range(4, -1, -1):
        with pytest.raises(IdentityFactorRejected):
            store.confirm_enrollment(12, invalid_code)
        assert store.status(12)["pending_attempts_remaining"] == remaining
    with pytest.raises(IdentityFactorRejected, match="MFA_ENROLLMENT_ATTEMPTS_EXHAUSTED"):
        store.confirm_enrollment(
            12,
            valid_code,
        )
    assert len(_event_files(store, 12)) == 6

    clock.advance(601)
    replacement = store.begin_enrollment(12, account_label="alice")
    assert replacement["secret"] != enrollment["secret"]
    assert store.status(12)["pending_attempts_remaining"] == 5


def test_unsafe_tampered_duplicate_and_hardlinked_storage_fails_closed(
    tmp_path: Path,
) -> None:
    with pytest.raises(IdentityAssuranceUnavailable):
        IdentityAssuranceStore(Path("relative/identity"))
    with pytest.raises(IdentityAssuranceUnavailable):
        IdentityAssuranceStore(Path("~/identity"))
    with pytest.raises(IdentityAssuranceUnavailable):
        IdentityAssuranceStore(Path("/root/data/releases/globemind/current/identity"))

    target = tmp_path / "target"
    target.mkdir()
    symlink = tmp_path / "linked"
    symlink.symlink_to(target, target_is_directory=True)
    with pytest.raises(IdentityAssuranceUnavailable):
        IdentityAssuranceStore(symlink)

    safe_parent = tmp_path / "safe-parent"
    safe_parent.mkdir()
    race_store = IdentityAssuranceStore(safe_parent / "store")
    safe_parent.rmdir()
    safe_parent.symlink_to(target, target_is_directory=True)
    with pytest.raises(IdentityAssuranceUnavailable):
        race_store.status(2)

    clock = _Clock()
    tampered = IdentityAssuranceStore(tmp_path / "tampered", clock=clock)
    tampered.begin_enrollment(3, account_label="alice")
    event = _event_files(tampered, 3)[0]
    payload = json.loads(event.read_text(encoding="utf-8"))
    payload["action"] = "mfa.enabled"
    event.chmod(0o600)
    event.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(IdentityAssuranceUnavailable):
        tampered.status(3)

    duplicate = IdentityAssuranceStore(tmp_path / "duplicate", clock=clock)
    duplicate.begin_enrollment(4, account_label="alice")
    duplicate_event = _event_files(duplicate, 4)[0]
    duplicate_event.chmod(0o600)
    duplicate_event.write_text(
        duplicate_event.read_text(encoding="utf-8").replace(
            '"action":', '"action":"mfa.enrollment_started","action":'
        ),
        encoding="utf-8",
    )
    with pytest.raises(IdentityAssuranceUnavailable):
        duplicate.status(4)

    hardlinked = IdentityAssuranceStore(tmp_path / "hardlinked", clock=clock)
    hardlinked.begin_enrollment(5, account_label="alice")
    hardlink_event = _event_files(hardlinked, 5)[0]
    os.link(hardlink_event, tmp_path / "second-link")
    with pytest.raises(IdentityAssuranceUnavailable):
        hardlinked.status(5)

    hardlinked_lock = IdentityAssuranceStore(tmp_path / "hardlinked-lock", clock=clock)
    hardlinked_lock.begin_enrollment(6, account_label="alice")
    lock_file = hardlinked_lock.lock_root / "6.lock"
    os.link(lock_file, tmp_path / "second-lock-link")
    with pytest.raises(IdentityAssuranceUnavailable):
        hardlinked_lock.confirm_enrollment(6, "000000")


def test_real_login_issues_tracked_session_and_mfa_never_returns_early_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assurance_root = tmp_path / "identity-assurance"
    monkeypatch.setenv("IDENTITY_ASSURANCE_ROOT", str(assurance_root))
    row = _user()
    db = _Session(row)

    first = login(LoginRequest(username="alice", password="database-password-1"), db)
    identity = get_user_from_access_token(first["access_token"])
    assert identity is not None
    assert identity["session_tracking"] == "tracked"
    assert configured_identity_assurance_store().session_valid(
        row.id,
        jti=identity["jti"],
        auth_version=identity["auth_version"],
    ) is True

    store = configured_identity_assurance_store()
    enrollment = store.begin_enrollment(row.id, account_label=row.username)
    recovery_codes = store.confirm_enrollment(
        row.id,
        totp_code(enrollment["secret"]),
    )["recovery_codes"]

    challenge = login(LoginRequest(username="alice", password="database-password-1"), db)
    assert challenge["mfa_required"] is True
    assert "access_token" not in challenge
    completed = complete_mfa_login(
        LoginMfaVerifyRequest(
            challenge=challenge["challenge"],
            recovery_code=recovery_codes[0],
        ),
        db,
    )
    completed_identity = get_user_from_access_token(completed["access_token"])
    assert completed_identity is not None
    assert completed_identity["session_tracking"] == "tracked"
    with pytest.raises(HTTPException) as replay:
        complete_mfa_login(
            LoginMfaVerifyRequest(
                challenge=challenge["challenge"],
                recovery_code=recovery_codes[1],
            ),
            db,
        )
    assert replay.value.status_code == 401

    monkeypatch.setattr(db_module, "SessionLocal", lambda: _Session(row))
    assert get_active_user_from_access_token(completed["access_token"]) is not None
    store.revoke_session(
        row.id,
        session_id=hash_session_jti(completed_identity["jti"]),
        reason="test revocation",
    )
    assert get_active_user_from_access_token(completed["access_token"]) is None


def test_invalid_password_does_not_disclose_mfa_or_write_assurance_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "identity-assurance"
    monkeypatch.setenv("IDENTITY_ASSURANCE_ROOT", str(root))
    with pytest.raises(HTTPException) as rejected:
        login(LoginRequest(username="alice", password="wrong-password"), _Session(_user()))
    assert rejected.value.status_code == 401
    assert rejected.value.detail == "用户名或密码错误"
    assert not root.exists()


def test_untracked_candidate_token_can_read_mfa_capability_without_creating_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "candidate-read-only"
    monkeypatch.setenv("IDENTITY_ASSURANCE_ROOT", str(root))
    row = _user()
    monkeypatch.setattr(db_module, "SessionLocal", lambda: _Session(row))
    token = create_access_token(
        row.id,
        row.username,
        password_hash=row.password_hash,
    )
    identity = get_active_user_from_access_token(token)
    assert identity is not None
    assert identity["session_tracking"] == "untracked"

    status = get_mfa_status(identity)
    assert status == {
        "schema_version": "identity-mfa-status-v1",
        "status": "disabled",
        "enabled": False,
        "pending_enrollment": False,
        "pending_expires_at": None,
        "pending_attempts_remaining": None,
        "recovery_codes_remaining": 0,
        "assurance": {
            "type": "totp-rfc6238",
            "enrollment_state": "available",
            "institutional_sso": "unavailable",
            "device_attestation": "unavailable",
            "independent_security_review": "unavailable",
        },
        "capabilities": {
            "totp_enrollment": "available",
            "recovery_codes": "available",
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
            "totp": "available",
            "recovery_codes": "available",
            "tracked_web_sessions": "available",
            "institutional_sso": "not_configured",
            "security_keys": "not_configured",
            "trusted_devices": "not_configured",
            "device_attestation": "not_configured",
            "runtime_idp_attestation": "not_available",
            "independent_security_review": "not_provided",
        },
    }
    assert not root.exists()
    with pytest.raises(HTTPException) as untracked:
        list_security_sessions(identity)
    assert untracked.value.status_code == 409
    with pytest.raises(HTTPException) as enrollment:
        begin_mfa_enrollment(identity)
    assert enrollment.value.status_code == 409
    assert not root.exists()
