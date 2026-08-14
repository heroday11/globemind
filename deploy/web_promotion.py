#!/usr/bin/env python3
"""Auditable two-phase transaction primitives for GlobeMind Web promotion."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import http.client
import json
import os
import re
import secrets
import stat
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Protocol, Sequence

SCHEMA_VERSION = 1
CREDENTIAL_KIND = "globemind-web-promotion-preflight"
MAX_CREDENTIAL_BYTES = 1024 * 1024
MAX_HEALTH_BYTES = 1024 * 1024
MAX_TOOL_OUTPUT_BYTES = 256 * 1024
MAX_PREFLIGHT_TTL_SECONDS = 300
DEFAULT_PREFLIGHT_TTL_SECONDS = 180
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
BUILD_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[+-][0-9A-Za-z.-]+)?\Z")
GIT_SHA_RE = re.compile(r"[0-9a-f]{40,64}\Z")
REQUEST_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{2,95}\Z")
NONCE_RE = re.compile(r"[0-9a-f]{32}\Z")
JOURNAL_KIND = "globemind-web-promotion-active-transaction"
JOURNAL_PHASES = frozenset(
    {
        "prepared",
        "stopping-old",
        "old-stopped",
        "promoting-links",
        "links-promoted",
        "starting-target",
        "target-started",
        "target-healthy",
        "stopping-target",
        "target-stopped",
        "restoring-links",
        "links-restored",
        "starting-rollback",
        "rollback-healthy",
        "recovery-started",
        "recovery-stopping-target",
        "recovery-target-stopped",
        "recovery-restoring-links",
        "recovery-links-restored",
        "recovery-starting-rollback",
        "recovery-rollback-healthy",
        "recovery-required",
        "recovery-failed",
    }
)
SAFE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
DATABASE_PASSWORD_FILE_MODE = 0o600
PROMOTION_LOCK_FD_ENV = "GLOBEMIND_PROMOTION_LOCK_FD"
_ACTIVE_PROMOTION_LOCK_FD: ContextVar[int | None] = ContextVar(
    "active_promotion_lock_fd", default=None
)


class PromotionError(RuntimeError):
    """Promotion invariant violation."""


class PromotionApplyError(PromotionError):
    """An apply failed, with rollback outcome attached."""

    def __init__(self, message: str, *, rollback_succeeded: bool) -> None:
        super().__init__(message)
        self.rollback_succeeded = rollback_succeeded


class ControllerError(PromotionError):
    """The exact Web controller returned a failure."""

    def __init__(self, message: str, result: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.result = dict(result)


def _utc_now(epoch: float | None = None) -> str:
    value = time.time() if epoch is None else epoch
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PromotionError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise PromotionError(f"non-finite JSON value is forbidden: {value}")


def _decode_json(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PromotionError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise PromotionError(f"{label} must be a JSON object")
    return value


def _read_bounded(path: Path, limit: int, label: str) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise PromotionError(f"cannot stat {label}: {path}") from exc
    if size > limit:
        raise PromotionError(f"{label} exceeds {limit} bytes")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise PromotionError(f"cannot read {label}: {path}") from exc


def _assert_owned_regular(path: Path, *, private: bool, executable: bool = False) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise PromotionError(f"required path is unavailable: {path}") from exc
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise PromotionError(f"path must be a non-symlink regular file: {path}")
    if metadata.st_uid != os.geteuid():
        raise PromotionError(f"path is not owned by the effective user: {path}")
    forbidden = 0o077 if private else 0o022
    if stat.S_IMODE(metadata.st_mode) & forbidden:
        raise PromotionError(f"path permissions are too broad: {path}")
    if executable and not os.access(path, os.X_OK):
        raise PromotionError(f"path is not executable: {path}")


def _assert_database_password_file(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise PromotionError(f"database password file is unavailable: {path}") from exc
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise PromotionError("database password file must be a non-symlink regular file")
    if metadata.st_uid != os.geteuid():
        raise PromotionError("database password file is not owned by the effective user")
    if stat.S_IMODE(metadata.st_mode) != DATABASE_PASSWORD_FILE_MODE:
        raise PromotionError("database password file mode must be exactly 0600")


def _assert_owned_directory(path: Path, *, allow_missing_leaf: bool = False) -> None:
    candidate = path
    if allow_missing_leaf and not candidate.exists():
        if candidate.is_symlink():
            raise PromotionError(f"managed directory leaf must not be a symlink: {candidate}")
        candidate = candidate.parent
    try:
        metadata = candidate.lstat()
    except OSError as exc:
        raise PromotionError(f"managed directory is unavailable: {candidate}") from exc
    if not stat.S_ISDIR(metadata.st_mode) or candidate.is_symlink():
        raise PromotionError(f"managed path must be a non-symlink directory: {candidate}")
    if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & 0o022:
        raise PromotionError(f"managed directory ownership or permissions are unsafe: {candidate}")


def _absolute_without_resolving(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _direct_child(path: Path, root: Path, label: str) -> Path:
    resolved_root = root.resolve(strict=True)
    resolved = path.resolve(strict=True)
    try:
        relative = resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise PromotionError(f"{label} is outside its managed root: {resolved}") from exc
    if len(relative.parts) != 1 or relative.name.startswith("."):
        raise PromotionError(f"{label} must be exactly one directory below its managed root")
    if path.is_symlink() or not resolved.is_dir():
        raise PromotionError(f"{label} must be a real directory: {path}")
    metadata = resolved.stat()
    if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & 0o022:
        raise PromotionError(f"{label} ownership or permissions are unsafe: {resolved}")
    return resolved


@dataclass(frozen=True)
class PromotionConfig:
    request_id: str
    project_dir: Path
    release_root: Path
    runtime_root: Path
    target_release: Path
    current_link: Path
    previous_link: Path
    controller: Path
    verifier: Path
    verify_python: Path
    pid_file: Path
    environment_files: tuple[Path, ...]
    database_password_file: Path
    generated_asset_root: Path
    audit_root: Path
    host: str = "127.0.0.1"
    port: int = 18089
    web_workers: int = 4
    warmup_rounds: int = 12
    db_pool_size: int = 3
    db_max_overflow: int = 2
    db_pool_timeout: int = 30
    stop_timeout_seconds: int = 30
    controller_timeout_seconds: int = 90
    health_timeout_seconds: int = 60
    scheduler_max_heartbeat_age_seconds: int = 180
    credential_ttl_seconds: int = DEFAULT_PREFLIGHT_TTL_SECONDS

    def validate(self) -> None:
        if REQUEST_ID_RE.fullmatch(self.request_id) is None:
            raise PromotionError("request-id has an invalid format")
        if self.host != "127.0.0.1":
            raise PromotionError("health host must be the literal loopback 127.0.0.1")
        for name, value, minimum, maximum in (
            ("port", self.port, 1, 65535),
            ("web_workers", self.web_workers, 1, 32),
            ("warmup_rounds", self.warmup_rounds, 0, 1000),
            ("db_pool_size", self.db_pool_size, 1, 100),
            ("db_max_overflow", self.db_max_overflow, 0, 100),
            ("db_pool_timeout", self.db_pool_timeout, 1, 300),
            ("stop_timeout_seconds", self.stop_timeout_seconds, 1, 300),
            ("controller_timeout_seconds", self.controller_timeout_seconds, 10, 600),
            ("health_timeout_seconds", self.health_timeout_seconds, 1, 300),
            (
                "scheduler_max_heartbeat_age_seconds",
                self.scheduler_max_heartbeat_age_seconds,
                1,
                600,
            ),
            ("credential_ttl_seconds", self.credential_ttl_seconds, 30, 300),
        ):
            if isinstance(value, bool) or not minimum <= value <= maximum:
                raise PromotionError(f"{name} must be between {minimum} and {maximum}")
        if not self.environment_files:
            raise PromotionError("at least one explicit environment file is required")
        if len(set(self.environment_files)) != len(self.environment_files):
            raise PromotionError("environment files must be unique")
        _assert_database_password_file(self.database_password_file)
        all_paths = (
            self.project_dir,
            self.release_root,
            self.runtime_root,
            self.target_release,
            self.current_link,
            self.previous_link,
            self.controller,
            self.verifier,
            self.verify_python,
            self.pid_file,
            *self.environment_files,
            self.database_password_file,
            self.generated_asset_root,
            self.audit_root,
        )
        if any(
            any(ord(character) < 32 or ord(character) == 127 for character in str(path))
            for path in all_paths
        ):
            raise PromotionError("promotion paths must not contain control characters")
        if any(":" in str(path) for path in self.environment_files):
            raise PromotionError("environment file paths must not contain the list delimiter")
        link_parent = self.current_link.parent
        if self.previous_link.parent != link_parent:
            raise PromotionError("current and previous links must share one directory")
        if self.current_link == self.previous_link:
            raise PromotionError("current and previous links must be distinct")
        if self.current_link.name != "current" or self.previous_link.name != "previous":
            raise PromotionError("release link names must be exactly current and previous")
        expected_controller = (self.project_dir / "deploy/start_web_prod.sh").resolve(strict=True)
        expected_verifier = (self.project_dir / "deploy/verify_release.py").resolve(strict=True)
        if self.controller != expected_controller:
            raise PromotionError("controller must be the project Web production controller")
        if self.verifier != expected_verifier:
            raise PromotionError("verifier must be the project production release verifier")
        if self.verify_python != Path("/usr/bin/python3").resolve(strict=True):
            raise PromotionError("verification interpreter must be /usr/bin/python3")
        if link_parent.resolve(strict=True) != self.release_root.resolve(strict=True):
            raise PromotionError("release links must live directly in the release root")

    def request_payload(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "project_dir": str(self.project_dir),
            "release_root": str(self.release_root),
            "runtime_root": str(self.runtime_root),
            "target_release": str(self.target_release),
            "current_link": str(self.current_link),
            "previous_link": str(self.previous_link),
            "controller": str(self.controller),
            "verifier": str(self.verifier),
            "verify_python": str(self.verify_python),
            "pid_file": str(self.pid_file),
            "environment_files": [str(path) for path in self.environment_files],
            "database_password_file": database_password_file_record(
                self.database_password_file
            ),
            "generated_asset_root": str(self.generated_asset_root),
            "audit_root": str(self.audit_root),
            "host": self.host,
            "port": self.port,
            "web_workers": self.web_workers,
            "warmup_rounds": self.warmup_rounds,
            "db_pool_size": self.db_pool_size,
            "db_max_overflow": self.db_max_overflow,
            "db_pool_timeout": self.db_pool_timeout,
            "stop_timeout_seconds": self.stop_timeout_seconds,
            "controller_timeout_seconds": self.controller_timeout_seconds,
            "health_timeout_seconds": self.health_timeout_seconds,
            "scheduler_max_heartbeat_age_seconds": (self.scheduler_max_heartbeat_age_seconds),
            "credential_ttl_seconds": self.credential_ttl_seconds,
        }


@dataclass(frozen=True)
class ReleaseIdentity:
    path: Path
    version: str
    build_id: str
    git_sha: str
    runtime_version: str
    manifest_sha256: str

    def as_dict(self) -> dict[str, str]:
        return {
            "path": str(self.path),
            "version": self.version,
            "build_id": self.build_id,
            "git_sha": self.git_sha,
            "runtime_version": self.runtime_version,
            "manifest_sha256": self.manifest_sha256,
        }


def read_release_identity(release_dir: Path, release_root: Path) -> ReleaseIdentity:
    release = _direct_child(release_dir, release_root, "release")
    manifest_path = release / "release.json"
    raw = _read_bounded(manifest_path, MAX_CREDENTIAL_BYTES, "release manifest")
    manifest = _decode_json(raw, "release manifest")
    if manifest.get("schema_version") != 3:
        raise PromotionError("V1 promotion requires a schema-v3 release")
    version = manifest.get("version")
    build_id = manifest.get("build_id")
    git_sha = manifest.get("git_sha")
    runtime = manifest.get("python_runtime")
    runtime_version = runtime.get("version") if isinstance(runtime, dict) else None
    if not isinstance(version, str) or VERSION_RE.fullmatch(version) is None:
        raise PromotionError("release version is invalid")
    if not isinstance(build_id, str) or BUILD_ID_RE.fullmatch(build_id) is None:
        raise PromotionError("release build-id is invalid")
    if not isinstance(git_sha, str) or GIT_SHA_RE.fullmatch(git_sha) is None:
        raise PromotionError("release git-sha is invalid")
    if runtime_version != version:
        raise PromotionError("release and Python runtime versions must match")
    if runtime.get("role") != "web":
        raise PromotionError("release Python runtime role must be web")
    return ReleaseIdentity(
        path=release,
        version=version,
        build_id=build_id,
        git_sha=git_sha,
        runtime_version=runtime_version,
        manifest_sha256=sha256_bytes(raw),
    )


def runtime_paths(identity: ReleaseIdentity, runtime_root: Path) -> tuple[Path, Path]:
    runtime = _direct_child(
        runtime_root / identity.runtime_version,
        runtime_root,
        "Python runtime",
    )
    manifest = runtime / "inventory" / "runtime.json"
    _assert_owned_regular(manifest, private=False)
    _assert_owned_regular(runtime / "bin" / "python", private=False, executable=True)
    return runtime, manifest


class AtomicLinkManager:
    def __init__(self, config: PromotionConfig) -> None:
        self.config = config

    def inspect(self) -> dict[str, str]:
        self.config.validate()
        current = self._link_target(self.config.current_link, "current")
        previous = self._link_target(self.config.previous_link, "previous")
        return {"current_target": str(current), "previous_target": str(previous)}

    def _link_target(self, link: Path, label: str) -> Path:
        try:
            metadata = link.lstat()
        except OSError as exc:
            raise PromotionError(f"{label} release link is unavailable") from exc
        if not stat.S_ISLNK(metadata.st_mode) or metadata.st_uid != os.geteuid():
            raise PromotionError(f"{label} release link is not a trusted symlink")
        raw_target = Path(os.readlink(link))
        if not raw_target.is_absolute():
            raise PromotionError(f"{label} release link target must be absolute")
        return _direct_child(raw_target, self.config.release_root, f"{label} release")

    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        lock_path = self.config.release_root / ".promotion.lock"
        descriptor = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        token = None
        try:
            inherited_descriptor = fcntl.fcntl(
                descriptor,
                fcntl.F_DUPFD_CLOEXEC,
                20,
            )
            os.close(descriptor)
            descriptor = inherited_descriptor
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) & 0o022
            ):
                raise PromotionError("promotion lock ownership or permissions are unsafe")
            deadline = time.monotonic() + 10
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise PromotionError("another promotion holds the release lock") from exc
                    time.sleep(0.1)
            token = _ACTIVE_PROMOTION_LOCK_FD.set(descriptor)
            yield
        finally:
            if token is not None:
                _ACTIVE_PROMOTION_LOCK_FD.reset(token)
            os.close(descriptor)

    def promote(
        self,
        target: Path,
        expected_current: Path,
        expected_previous: Path,
    ) -> None:
        observed = self.inspect()
        if observed != {
            "current_target": str(expected_current),
            "previous_target": str(expected_previous),
        }:
            raise PromotionError("current or previous release changed before link promotion")
        self._replace(self.config.previous_link, expected_current)
        self._replace(self.config.current_link, target)
        final = self.inspect()
        if final != {
            "current_target": str(target),
            "previous_target": str(expected_current),
        }:
            raise PromotionError("release links did not reach the promoted state")

    def restore(self, old_current: Path, old_previous: Path) -> None:
        self._replace(self.config.current_link, old_current)
        self._replace(self.config.previous_link, old_previous)
        final = self.inspect()
        if final != {
            "current_target": str(old_current),
            "previous_target": str(old_previous),
        }:
            raise PromotionError("release links did not return to the rollback state")

    @staticmethod
    def _replace(link: Path, target: Path) -> None:
        temporary = link.with_name(f".{link.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp")
        try:
            os.symlink(target, temporary)
            os.replace(temporary, link)
            _fsync_directory(link.parent)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


class ReleaseVerifier:
    def __init__(self, config: PromotionConfig) -> None:
        self.config = config

    def verify(self, identity: ReleaseIdentity, runtime: Path, manifest: Path) -> dict[str, str]:
        command = [
            str(self.config.verify_python),
            "-B",
            str(self.config.verifier),
            str(identity.path),
            "--production",
            "--expected-version",
            identity.version,
            "--expected-build-id",
            identity.build_id,
            "--expected-git-sha",
            identity.git_sha,
            "--python-runtime-dir",
            str(runtime),
            "--python-runtime-manifest",
            str(manifest),
            "--python-runtime-root",
            str(self.config.runtime_root),
        ]
        try:
            result = subprocess.run(
                command,
                cwd=self.config.project_dir,
                env={
                    "PATH": SAFE_PATH,
                    "HOME": "/root",
                    "LANG": "C.UTF-8",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "ALLOW_LEGACY_RELEASE": "0",
                },
                capture_output=True,
                timeout=self.config.controller_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise PromotionError("production release verifier timed out") from exc
        except OSError as exc:
            raise PromotionError("production release verifier could not execute") from exc
        if len(result.stdout) > MAX_TOOL_OUTPUT_BYTES or len(result.stderr) > MAX_TOOL_OUTPUT_BYTES:
            raise PromotionError("release verifier output exceeded its safety bound")
        if result.returncode != 0:
            raise PromotionError(
                f"production release verifier failed: stderr_sha256={sha256_bytes(result.stderr)}"
            )
        payload = _decode_json(result.stdout, "release verifier output")
        expected = {
            "status": "verified",
            "version": identity.version,
            "build_id": identity.build_id,
            "git_sha": identity.git_sha,
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            raise PromotionError("release verifier returned an unexpected identity")
        artifact_hash = payload.get("artifact_manifest_sha256")
        if not isinstance(artifact_hash, str) or SHA256_RE.fullmatch(artifact_hash) is None:
            raise PromotionError("release verifier omitted the artifact manifest digest")
        return {**expected, "artifact_manifest_sha256": artifact_hash}


def _proc_stat(proc_root: Path, pid: int) -> dict[str, int]:
    try:
        raw = (proc_root / str(pid) / "stat").read_text(encoding="utf-8")
        fields = raw.rsplit(")", 1)[1].split()
        return {
            "ppid": int(fields[1]),
            "pgid": int(fields[2]),
            "sid": int(fields[3]),
            "start_ticks": int(fields[19]),
        }
    except (OSError, IndexError, ValueError) as exc:
        raise PromotionError(f"cannot read process identity for PID {pid}") from exc


def _proc_cmdline(proc_root: Path, pid: int) -> list[str]:
    try:
        raw = (proc_root / str(pid) / "cmdline").read_bytes()
    except OSError as exc:
        raise PromotionError(f"cannot read process command line for PID {pid}") from exc
    return [part.decode("utf-8", errors="replace") for part in raw.split(b"\0") if part]


class ProcessInspector:
    def __init__(
        self,
        config: PromotionConfig,
        *,
        proc_root: Path = Path("/proc"),
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.proc_root = proc_root
        self.sleep = sleep
        self.monotonic = monotonic

    def inspect(self, release: Path, runtime: Path) -> dict[str, Any]:
        pid_file = self.config.pid_file
        meta_file = pid_file.with_name(f"{pid_file.name}.meta")
        _assert_owned_regular(pid_file, private=False)
        _assert_owned_regular(meta_file, private=False)
        try:
            pid_text = pid_file.read_text(encoding="utf-8")
            meta_text = meta_file.read_text(encoding="utf-8")
            pid = int(pid_text.strip())
            meta_pid, ticks, port, instance = meta_text.split()
        except (OSError, ValueError) as exc:
            raise PromotionError("production PID identity metadata is invalid") from exc
        if (pid, self.config.port, "production") != (int(meta_pid), int(port), instance):
            raise PromotionError("production PID and metadata disagree")
        try:
            boot_id = (
                (self.proc_root / "sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
            )
        except OSError as exc:
            raise PromotionError("cannot read the kernel boot identity") from exc
        if re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", boot_id) is None:
            raise PromotionError("kernel boot identity is invalid")
        master = _proc_stat(self.proc_root, pid)
        if master["start_ticks"] != int(ticks):
            raise PromotionError("production master start ticks changed")
        if master["ppid"] != 1:
            raise PromotionError("production master has not detached under PID 1")
        if master["pgid"] != pid or master["sid"] != pid:
            raise PromotionError("production master is not its own process group/session leader")
        proc_dir = self.proc_root / str(pid)
        if (proc_dir / "exe").resolve(strict=True) != (runtime / "bin/python").resolve(strict=True):
            raise PromotionError("production master executable does not match the release runtime")
        if (proc_dir / "cwd").resolve(strict=True) != release.resolve(strict=True):
            raise PromotionError("production master cwd does not match the release")
        argv = _proc_cmdline(self.proc_root, pid)
        if (
            len(argv) != 2
            or Path(argv[0]).resolve(strict=True) != (runtime / "bin/python").resolve(strict=True)
            or argv[1] != "backend/serve_prod.py"
        ):
            raise PromotionError("production master command line is not the Web entry point")
        if not self._owns_listener(pid):
            raise PromotionError("production master does not own the declared TCP listener")

        workers: list[dict[str, int]] = []
        for candidate in self.proc_root.iterdir():
            if not candidate.name.isdigit():
                continue
            child_pid = int(candidate.name)
            try:
                child = _proc_stat(self.proc_root, child_pid)
                child_argv = _proc_cmdline(self.proc_root, child_pid)
            except PromotionError:
                continue
            is_worker = (
                len(child_argv) >= 5
                and child_argv[1:3] == ["-B", "-c"]
                and child_argv[3].startswith("from multiprocessing.spawn import spawn_main;")
                and child_argv[-1] == "--multiprocessing-fork"
            )
            if child["ppid"] != pid or not is_worker:
                continue
            if child["pgid"] != pid or child["sid"] != pid:
                raise PromotionError("Web worker escaped the production process group/session")
            if (candidate / "exe").resolve(strict=True) != (runtime / "bin/python").resolve(
                strict=True
            ):
                raise PromotionError("Web worker executable does not match the release runtime")
            if (candidate / "cwd").resolve(strict=True) != release.resolve(strict=True):
                raise PromotionError("Web worker cwd does not match the release")
            workers.append({"pid": child_pid, "start_ticks": child["start_ticks"]})
        workers.sort(key=lambda value: value["pid"])
        if len(workers) != self.config.web_workers:
            raise PromotionError(
                f"expected {self.config.web_workers} Web workers, observed {len(workers)}"
            )
        if (
            pid_file.read_text(encoding="utf-8") != pid_text
            or meta_file.read_text(encoding="utf-8") != meta_text
        ):
            raise PromotionError("production PID identity metadata changed during inspection")
        if (self.proc_root / "sys/kernel/random/boot_id").read_text(
            encoding="ascii"
        ).strip() != boot_id:
            raise PromotionError("kernel boot identity changed during inspection")
        if _proc_stat(self.proc_root, pid) != master:
            raise PromotionError("production master identity changed during inspection")
        for worker in workers:
            current = _proc_stat(self.proc_root, worker["pid"])
            if (
                current["start_ticks"] != worker["start_ticks"]
                or current["ppid"] != pid
                or current["pgid"] != pid
                or current["sid"] != pid
            ):
                raise PromotionError("Web worker identity changed during inspection")
        return {
            "pid": pid,
            "boot_id": boot_id,
            "start_ticks": master["start_ticks"],
            "pgid": master["pgid"],
            "sid": master["sid"],
            "port": self.config.port,
            "instance": "production",
            "release": str(release),
            "runtime": str(runtime),
            "workers": workers,
        }

    def _owns_listener(self, pid: int) -> bool:
        listener_inodes = self._listener_inodes()
        if not listener_inodes:
            return False
        try:
            descriptors = {
                os.readlink(path) for path in (self.proc_root / str(pid) / "fd").iterdir()
            }
        except OSError:
            return False
        return any(f"socket:[{inode}]" in descriptors for inode in listener_inodes)

    def _listener_inodes(self, *, exact_host: bool = True) -> set[str]:
        listener_inodes: set[str] = set()
        readable = 0
        for name in ("tcp", "tcp6"):
            path = self.proc_root / "net" / name
            try:
                lines = path.read_text(encoding="utf-8").splitlines()[1:]
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise PromotionError("cannot inspect local TCP listeners") from exc
            readable += 1
            for line in lines:
                parts = line.split()
                if len(parts) < 10:
                    continue
                try:
                    local_address, raw_port = parts[1].rsplit(":", 1)
                    port = int(raw_port, 16)
                except (IndexError, ValueError):
                    continue
                host_matches = name == "tcp" and local_address.upper() == "0100007F"
                if (
                    (host_matches or not exact_host)
                    and parts[3] == "0A"
                    and port == self.config.port
                ):
                    listener_inodes.add(parts[9])
        if readable == 0:
            raise PromotionError("local TCP listener tables are unavailable")
        return listener_inodes

    def wait_dead(self, identity: Mapping[str, Any], timeout: float) -> None:
        deadline = self.monotonic() + timeout
        pid = int(identity["pid"])
        ticks = int(identity["start_ticks"])
        expected_boot_id = identity.get("boot_id")
        while self.monotonic() < deadline:
            if expected_boot_id is not None:
                try:
                    current_boot_id = (
                        (self.proc_root / "sys/kernel/random/boot_id")
                        .read_text(encoding="ascii")
                        .strip()
                    )
                except OSError as exc:
                    raise PromotionError("cannot verify the kernel boot identity") from exc
                if current_boot_id != expected_boot_id:
                    return
            try:
                current = _proc_stat(self.proc_root, pid)["start_ticks"]
            except PromotionError:
                if not (self.proc_root / str(pid) / "stat").exists():
                    return
                raise
            if current != ticks:
                return
            self.sleep(0.2)
        raise PromotionError("old production master did not exit within the stop deadline")

    def wait_running(self, release: Path, runtime: Path, timeout: float) -> dict[str, Any]:
        deadline = self.monotonic() + timeout
        last_error = "production identity did not appear"
        while self.monotonic() < deadline:
            try:
                return self.inspect(release, runtime)
            except PromotionError as exc:
                last_error = str(exc)
            self.sleep(0.25)
        raise PromotionError(f"new production identity did not stabilize: {last_error}")

    def wait_port_free(self, timeout: float) -> None:
        deadline = self.monotonic() + timeout
        while self.monotonic() < deadline:
            if not self._listener_inodes(exact_host=False):
                return
            self.sleep(0.2)
        raise PromotionError("production port remained open after the verified stop")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class HealthGate:
    def __init__(
        self,
        config: PromotionConfig,
        *,
        opener: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirect(),
        )
        self.sleep = sleep
        self.monotonic = monotonic

    def wait(
        self,
        identity: ReleaseIdentity,
        process: Mapping[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        deadline = self.monotonic() + timeout
        last_error = "health endpoint did not respond"
        while self.monotonic() < deadline:
            try:
                return self.check(identity, process)
            except PromotionError as exc:
                last_error = str(exc)
            self.sleep(0.5)
        raise PromotionError(f"production health/scheduler gate failed: {last_error}")

    def check(
        self,
        identity: ReleaseIdentity,
        process: Mapping[str, Any],
    ) -> dict[str, Any]:
        host = f"[{self.config.host}]" if ":" in self.config.host else self.config.host
        url = f"http://{host}:{self.config.port}/api/health/ready"
        request = urllib.request.Request(
            url,
            method="GET",
            headers={"Accept": "application/json", "Accept-Encoding": "identity"},
        )
        response: Any
        try:
            response = self.opener.open(request, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise PromotionError("local readiness request failed") from exc
        try:
            if response.getcode() != 200:
                raise PromotionError(f"readiness returned HTTP {response.getcode()}")
            content_type = str(response.headers.get("content-type") or "").lower()
            if "application/json" not in content_type and "+json" not in content_type:
                raise PromotionError("readiness response content type is not JSON")
            length = response.headers.get("content-length")
            if length is not None and int(length) > MAX_HEALTH_BYTES:
                raise PromotionError("readiness response exceeded its safety bound")
            raw = response.read(MAX_HEALTH_BYTES + 1)
            if len(raw) > MAX_HEALTH_BYTES:
                raise PromotionError("readiness response exceeded its safety bound")
        except (OSError, ValueError, http.client.HTTPException) as exc:
            raise PromotionError("readiness response was malformed") from exc
        finally:
            response.close()
        return self.validate(_decode_json(raw, "readiness response"), identity, process)

    def validate(
        self,
        payload: Mapping[str, Any],
        identity: ReleaseIdentity,
        process: Mapping[str, Any],
    ) -> dict[str, Any]:
        if (
            payload.get("status") != "healthy"
            or payload.get("ready") is not True
            or payload.get("service") != "globemind-api"
        ):
            raise PromotionError("readiness did not report the expected ready service")
        release = payload.get("release")
        expected_release = {
            "version": identity.version,
            "build_id": identity.build_id,
            "git_sha": identity.git_sha,
        }
        if not isinstance(release, dict) or any(
            release.get(key) != value for key, value in expected_release.items()
        ):
            raise PromotionError("readiness release identity does not match the selected artifact")
        checks = payload.get("checks")
        database = checks.get("database") if isinstance(checks, dict) else None
        scheduler = checks.get("assistant_scheduler") if isinstance(checks, dict) else None
        if not isinstance(database, dict) or database.get("status") != "up":
            raise PromotionError("database readiness is not up")
        if not isinstance(scheduler, dict):
            raise PromotionError("assistant scheduler health is missing")
        if (
            scheduler.get("enabled") is not True
            or scheduler.get("healthy") is not True
            or scheduler.get("state") != "running"
        ):
            raise PromotionError("assistant scheduler is not enabled, healthy, and running")
        leader_pid = scheduler.get("leader_pid")
        if isinstance(leader_pid, bool) or not isinstance(leader_pid, int) or leader_pid <= 0:
            raise PromotionError("assistant scheduler leader PID is invalid")
        workers = process.get("workers")
        worker_pids = (
            {item.get("pid") for item in workers if isinstance(item, dict)}
            if isinstance(workers, list)
            else set()
        )
        if leader_pid not in worker_pids:
            raise PromotionError("assistant scheduler leader is not a verified Web worker")
        instance_id = scheduler.get("leader_instance_id")
        if not isinstance(instance_id, str) or not instance_id.startswith(f"{leader_pid}-"):
            raise PromotionError("assistant scheduler leader instance is not PID-bound")
        heartbeat_age = scheduler.get("heartbeat_age_seconds")
        if (
            isinstance(heartbeat_age, bool)
            or not isinstance(heartbeat_age, (int, float))
            or heartbeat_age < 0
            or heartbeat_age > self.config.scheduler_max_heartbeat_age_seconds
        ):
            raise PromotionError("assistant scheduler heartbeat is stale or invalid")
        return {
            "ready": True,
            "database": "up",
            "release": expected_release,
            "scheduler": {
                "enabled": True,
                "healthy": True,
                "state": "running",
                "leader_pid": leader_pid,
                "leader_instance_id": instance_id,
            },
        }


def environment_file_records(paths: Sequence[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        _assert_owned_regular(path, private=True)
        metadata = path.stat()
        records.append(
            {
                "path": str(path),
                "sha256": sha256_file(path),
                "bytes": metadata.st_size,
                "mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
                "uid": metadata.st_uid,
            }
        )
    return records


def database_password_file_record(path: Path) -> dict[str, Any]:
    _assert_database_password_file(path)
    metadata = path.stat()
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "bytes": metadata.st_size,
        "mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
        "uid": metadata.st_uid,
    }


def tool_record(path: Path, *, executable: bool) -> dict[str, Any]:
    _assert_owned_regular(path, private=False, executable=executable)
    metadata = path.stat()
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "bytes": metadata.st_size,
        "mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
        "uid": metadata.st_uid,
    }


def promotion_tool_records(config: PromotionConfig) -> dict[str, Any]:
    return {
        "promotion_library": tool_record(Path(__file__).resolve(), executable=False),
        "promotion_cli": tool_record(
            Path(__file__).with_name("promote_web_release.py").resolve(),
            executable=False,
        ),
        "controller": tool_record(config.controller, executable=True),
        "verifier": tool_record(config.verifier, executable=False),
        "verifier_library": tool_record(
            config.verifier.parent / "release_lib.py",
            executable=False,
        ),
        "verify_python": tool_record(config.verify_python, executable=True),
    }


def controller_environment(
    config: PromotionConfig,
    identity: ReleaseIdentity,
    runtime: Path,
    runtime_manifest: Path,
) -> dict[str, str]:
    return {
        "PATH": f"{runtime / 'bin'}:{SAFE_PATH}",
        "HOME": "/root",
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PROJECT_DIR": str(config.project_dir),
        "INSTANCE": "production",
        "HOST": config.host,
        "PORT": str(config.port),
        "APP_ENV": "production",
        "APP_VERSION": identity.version,
        "BUILD_ID": identity.build_id,
        "GIT_SHA": identity.git_sha,
        "WEB_WORKERS": str(config.web_workers),
        "WARMUP_ROUNDS": str(config.warmup_rounds),
        "DB_POOL_SIZE": str(config.db_pool_size),
        "DB_MAX_OVERFLOW": str(config.db_max_overflow),
        "DB_POOL_TIMEOUT": str(config.db_pool_timeout),
        "PGOPTIONS": "-c max_parallel_workers_per_gather=0",
        "DB_USER": "web_runtime",
        "GLOBEMIND_DB_PASSWORD_FILE": str(config.database_password_file),
        "DB_SSLMODE": "disable",
        "GLOBEMIND_ALLOW_PRIVATE_SCRAM_TRANSPORT": "1",
        "L1_DB_HOST": "",
        "L1_DB_PORT": "",
        "L1_DB_USER": "",
        "L1_DB_NAME": "",
        "OPINION_DB_HOST": "",
        "OPINION_DB_PORT": "",
        "OPINION_DB_USER": "",
        "OPINION_DB_NAME": "",
        "STOP_TIMEOUT_SEC": str(config.stop_timeout_seconds),
        "RELEASE_ROOT": str(config.release_root),
        "FRONTEND_DIST": str(identity.path / "frontend-dist"),
        "PYTHON_RUNTIME_ROOT": str(config.runtime_root),
        "PYTHON_RUNTIME_DIR": str(runtime),
        "PYTHON_RUNTIME_MANIFEST": str(runtime_manifest),
        "VERIFY_PYTHON": str(config.verify_python),
        "GLOBEMIND_ENV_FILE": str(config.environment_files[0]),
        "GLOBEMIND_ENV_FILES": ":".join(str(path) for path in config.environment_files),
        "GLOBEMIND_GENERATED_ASSET_ROOT": str(config.generated_asset_root),
        "ALLOW_RUNTIME_SCHEMA_MUTATIONS": "0",
        "ASSISTANT_SCHEDULE_DISABLE": "0",
        "ALLOW_LEGACY_RELEASE": "0",
    }


class PreflightCapture(Protocol):
    def capture(self, config: PromotionConfig) -> dict[str, Any]: ...

    def assert_bound_inputs(
        self,
        config: PromotionConfig,
        facts: Mapping[str, Any],
    ) -> None: ...

    def validate_recovery(
        self,
        config: PromotionConfig,
        facts: Mapping[str, Any],
        *,
        allow_tool_drift: bool = False,
    ) -> dict[str, str]: ...


class PreflightBuilder:
    def __init__(
        self,
        config: PromotionConfig,
        *,
        links: AtomicLinkManager | None = None,
        verifier: ReleaseVerifier | None = None,
        inspector: ProcessInspector | None = None,
        health: HealthGate | None = None,
    ) -> None:
        self.config = config
        self.links = links or AtomicLinkManager(config)
        self.verifier = verifier or ReleaseVerifier(config)
        self.inspector = inspector or ProcessInspector(config)
        self.health = health or HealthGate(config)

    def capture(self, config: PromotionConfig | None = None) -> dict[str, Any]:
        if config is not None and config != self.config:
            raise PromotionError("preflight builder was used with a different configuration")
        config = self.config
        config.validate()
        release_root = config.release_root.resolve(strict=True)
        project = config.project_dir.resolve(strict=True)
        runtime_root = config.runtime_root.resolve(strict=True)
        if config.project_dir != project or config.release_root != release_root:
            raise PromotionError("project and release roots must be canonical paths")
        if config.runtime_root != runtime_root:
            raise PromotionError("runtime root must be a canonical path")
        for path in (project, release_root, runtime_root):
            _assert_owned_directory(path)
        tools_before = promotion_tool_records(config)
        environment_before = environment_file_records(config.environment_files)
        database_password_before = database_password_file_record(
            config.database_password_file
        )
        links = self.links.inspect()
        target = read_release_identity(config.target_release, release_root)
        current = read_release_identity(Path(links["current_target"]), release_root)
        previous = read_release_identity(Path(links["previous_target"]), release_root)
        if target.path == current.path:
            raise PromotionError("target release must differ from current")

        identities = {item.path: item for item in (target, current, previous)}
        release_facts: dict[str, Any] = {}
        runtime_map: dict[Path, tuple[Path, Path]] = {}
        for path, identity in identities.items():
            runtime, manifest = runtime_paths(identity, runtime_root)
            runtime_map[path] = (runtime, manifest)
            release_facts[str(path)] = {
                **identity.as_dict(),
                "runtime": str(runtime),
                "runtime_manifest": str(manifest),
                "runtime_manifest_sha256": sha256_file(manifest),
                "verification": self.verifier.verify(identity, runtime, manifest),
            }

        current_runtime, current_runtime_manifest = runtime_map[current.path]
        target_runtime, target_runtime_manifest = runtime_map[target.path]
        process = self.inspector.inspect(current.path, current_runtime)
        readiness = self.health.wait(current, process, config.health_timeout_seconds)
        if self.inspector.inspect(current.path, current_runtime) != process:
            raise PromotionError("production process identities changed across the health gate")
        tools_after = promotion_tool_records(config)
        if tools_after != tools_before:
            raise PromotionError("promotion or verifier tools changed during preflight")
        environment_after = environment_file_records(config.environment_files)
        if environment_after != environment_before:
            raise PromotionError("production environment files changed during preflight")
        database_password_after = database_password_file_record(
            config.database_password_file
        )
        if database_password_after != database_password_before:
            raise PromotionError("database password file changed during preflight")

        for path in (config.controller, config.verifier, config.verify_python):
            if path != path.resolve(strict=True):
                raise PromotionError(f"tool path must be canonical: {path}")
        generated = _absolute_without_resolving(config.generated_asset_root)
        try:
            generated.resolve(strict=False).relative_to(release_root)
        except ValueError:
            pass
        else:
            raise PromotionError("generated asset root must not be inside immutable releases")
        _assert_owned_directory(generated, allow_missing_leaf=True)
        audit_root = _absolute_without_resolving(config.audit_root)
        try:
            audit_root.resolve(strict=False).relative_to(release_root)
        except ValueError:
            pass
        else:
            raise PromotionError("promotion audit root must not be inside immutable releases")
        _assert_owned_directory(audit_root, allow_missing_leaf=True)

        return {
            "links": links,
            "target": target.as_dict(),
            "rollback": current.as_dict(),
            "prior_previous": previous.as_dict(),
            "releases": release_facts,
            "environment_files": environment_after,
            "database_password_file": database_password_after,
            "tools": tools_after,
            "current_process": process,
            "current_health": readiness,
            "target_environment": controller_environment(
                config,
                target,
                target_runtime,
                target_runtime_manifest,
            ),
            "rollback_environment": controller_environment(
                config,
                current,
                current_runtime,
                current_runtime_manifest,
            ),
        }

    def validate_recovery(
        self,
        config: PromotionConfig,
        facts: Mapping[str, Any],
        *,
        allow_tool_drift: bool = False,
    ) -> dict[str, str]:
        if config != self.config:
            raise PromotionError("recovery preflight used a different configuration")
        config.validate()
        tools_before = promotion_tool_records(config)
        if tools_before != facts.get("tools") and not allow_tool_drift:
            raise PromotionError("promotion tools differ from the interrupted transaction")
        environment_before = environment_file_records(config.environment_files)
        if environment_before != facts.get("environment_files"):
            raise PromotionError("environment files differ from the interrupted transaction")
        database_password_before = database_password_file_record(
            config.database_password_file
        )
        if database_password_before != facts.get("database_password_file"):
            raise PromotionError(
                "database password file differs from the interrupted transaction"
            )

        expected_identities = (
            _identity_from_facts(facts["target"]),
            _identity_from_facts(facts["rollback"]),
            _identity_from_facts(facts["prior_previous"]),
        )
        runtime_evidence: dict[Path, tuple[Path, Path]] = {}
        for expected in {identity.path: identity for identity in expected_identities}.values():
            actual = read_release_identity(expected.path, config.release_root)
            if actual != expected:
                raise PromotionError("release identity changed after the interrupted transaction")
            runtime, manifest = runtime_paths(actual, config.runtime_root)
            runtime_evidence[actual.path] = (runtime, manifest)
            release_fact = facts["releases"].get(str(actual.path))
            if not isinstance(release_fact, dict):
                raise PromotionError("interrupted release evidence is missing")
            if (
                release_fact.get("runtime") != str(runtime)
                or release_fact.get("runtime_manifest") != str(manifest)
                or release_fact.get("runtime_manifest_sha256") != sha256_file(manifest)
            ):
                raise PromotionError("interrupted Python runtime evidence changed")
            verification = self.verifier.verify(actual, runtime, manifest)
            if verification != release_fact.get("verification"):
                raise PromotionError("production verifier evidence changed during recovery")

        target = expected_identities[0]
        rollback = expected_identities[1]
        target_runtime, target_manifest = runtime_evidence[target.path]
        rollback_runtime, rollback_manifest = runtime_evidence[rollback.path]
        if facts.get("target_environment") != controller_environment(
            config,
            target,
            target_runtime,
            target_manifest,
        ):
            raise PromotionError("target controller environment evidence changed")
        if facts.get("rollback_environment") != controller_environment(
            config,
            rollback,
            rollback_runtime,
            rollback_manifest,
        ):
            raise PromotionError("rollback controller environment evidence changed")

        tools_after = promotion_tool_records(config)
        environment_after = environment_file_records(config.environment_files)
        database_password_after = database_password_file_record(
            config.database_password_file
        )
        if (
            tools_after != tools_before
            or environment_after != environment_before
            or database_password_after != database_password_before
        ):
            raise PromotionError(
                "tools, environment, or database password changed during recovery preflight"
            )
        links = self.links.inspect()
        old_current = str(facts["links"]["current_target"])
        old_previous = str(facts["links"]["previous_target"])
        target = str(facts["target"]["path"])
        allowed = {
            (old_current, old_previous),
            (old_current, old_current),
            (target, old_current),
        }
        observed = (links["current_target"], links["previous_target"])
        if observed not in allowed:
            raise PromotionError("release links are outside all recoverable transaction states")
        return links

    def assert_bound_inputs(
        self,
        config: PromotionConfig,
        facts: Mapping[str, Any],
    ) -> None:
        if config != self.config:
            raise PromotionError("bound-input check used a different configuration")
        if promotion_tool_records(config) != facts.get("tools"):
            raise PromotionError("promotion tools changed before a controller action")
        if environment_file_records(config.environment_files) != facts.get("environment_files"):
            raise PromotionError("environment files changed before a controller action")
        if database_password_file_record(config.database_password_file) != facts.get(
            "database_password_file"
        ):
            raise PromotionError("database password file changed before a controller action")


def create_credential(
    config: PromotionConfig,
    facts: Mapping[str, Any],
    *,
    now: float | None = None,
    nonce: str | None = None,
) -> dict[str, Any]:
    config.validate()
    created = int(time.time() if now is None else now)
    token = nonce or secrets.token_hex(16)
    if NONCE_RE.fullmatch(token) is None:
        raise PromotionError("preflight nonce has an invalid format")
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": CREDENTIAL_KIND,
        "created_at_epoch": created,
        "created_at": _utc_now(created),
        "expires_at_epoch": created + config.credential_ttl_seconds,
        "expires_at": _utc_now(created + config.credential_ttl_seconds),
        "nonce": token,
        "request": config.request_payload(),
        "facts": dict(facts),
        "policy": {
            "default_mode": "dry-run",
            "production_verifier": "required-and-repeated-under-lock",
            "controller": "exact-start-stop-only",
            "direct_signals": "forbidden",
            "rollback": "required-after-any-stop-attempt",
        },
    }


def write_credential(path: Path, credential: Mapping[str, Any]) -> str:
    raw = _canonical_json_bytes(credential)
    if len(raw) > MAX_CREDENTIAL_BYTES:
        raise PromotionError("preflight credential exceeds its safety bound")
    path = _absolute_without_resolving(path)
    _assert_owned_directory(path.parent)
    if path.exists() or path.is_symlink():
        raise PromotionError("preflight credential output already exists")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
        os.chmod(path, 0o600)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return sha256_bytes(raw)


def load_credential(
    path: Path,
    expected_sha256: str,
    config: PromotionConfig,
    *,
    now: float | None = None,
    allow_expired_for_recovery: bool = False,
) -> dict[str, Any]:
    if SHA256_RE.fullmatch(expected_sha256) is None:
        raise PromotionError("preflight credential SHA-256 is invalid")
    _assert_owned_regular(path, private=True)
    raw = _read_bounded(path, MAX_CREDENTIAL_BYTES, "preflight credential")
    if not secrets.compare_digest(sha256_bytes(raw), expected_sha256):
        raise PromotionError("preflight credential content digest does not match")
    credential = _decode_json(raw, "preflight credential")
    required = {
        "schema_version",
        "kind",
        "created_at_epoch",
        "created_at",
        "expires_at_epoch",
        "expires_at",
        "nonce",
        "request",
        "facts",
        "policy",
    }
    if set(credential) != required:
        raise PromotionError("preflight credential fields are incomplete or unknown")
    if (
        credential.get("schema_version") != SCHEMA_VERSION
        or credential.get("kind") != CREDENTIAL_KIND
    ):
        raise PromotionError("preflight credential schema or kind is unsupported")
    if credential.get("request") != config.request_payload():
        raise PromotionError("preflight credential is bound to different promotion inputs")
    nonce = credential.get("nonce")
    if not isinstance(nonce, str) or NONCE_RE.fullmatch(nonce) is None:
        raise PromotionError("preflight credential nonce is invalid")
    created = credential.get("created_at_epoch")
    expires = credential.get("expires_at_epoch")
    if (
        isinstance(created, bool)
        or not isinstance(created, int)
        or isinstance(expires, bool)
        or not isinstance(expires, int)
        or expires - created != config.credential_ttl_seconds
        or expires - created > MAX_PREFLIGHT_TTL_SECONDS
    ):
        raise PromotionError("preflight credential validity window is invalid")
    if credential.get("created_at") != _utc_now(created) or credential.get(
        "expires_at"
    ) != _utc_now(expires):
        raise PromotionError("preflight credential timestamps are inconsistent")
    expected_policy = {
        "default_mode": "dry-run",
        "production_verifier": "required-and-repeated-under-lock",
        "controller": "exact-start-stop-only",
        "direct_signals": "forbidden",
        "rollback": "required-after-any-stop-attempt",
    }
    if credential.get("policy") != expected_policy:
        raise PromotionError("preflight credential policy is invalid")
    current = time.time() if now is None else now
    if current < created - 5 or (not allow_expired_for_recovery and current >= expires):
        raise PromotionError("preflight credential is not currently valid")
    if not isinstance(credential.get("facts"), dict):
        raise PromotionError("preflight credential facts are invalid")
    return credential


class PromotionJournal:
    """Durable same-directory intent record for interruption recovery."""

    def __init__(self, config: PromotionConfig) -> None:
        self.config = config
        self.path = config.release_root / ".promotion-active.json"

    def begin(
        self,
        credential: Mapping[str, Any],
        credential_sha256: str,
        audit_directory: Path,
    ) -> dict[str, Any]:
        self._cleanup_temporaries()
        if self.path.exists() or self.path.is_symlink():
            raise PromotionError(
                "an active promotion journal already exists; recover it before a new apply"
            )
        facts = credential["facts"]
        value = {
            "schema_version": 1,
            "kind": JOURNAL_KIND,
            "request_id": credential["request"]["request_id"],
            "nonce": credential["nonce"],
            "credential_sha256": credential_sha256,
            "facts_sha256": sha256_bytes(_canonical_json_bytes(facts)),
            "audit_directory": str(audit_directory),
            "links": {
                "old_current": facts["links"]["current_target"],
                "old_previous": facts["links"]["previous_target"],
                "target": facts["target"]["path"],
            },
            "phase": "prepared",
            "sequence": 1,
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
        }
        self._create(value)
        return value

    def load_bound(
        self,
        credential: Mapping[str, Any],
        credential_sha256: str,
    ) -> dict[str, Any]:
        self._cleanup_temporaries()
        _assert_owned_regular(self.path, private=True)
        if self.path.stat().st_nlink > 2:
            raise PromotionError("promotion journal must not have hard links")
        value = _decode_json(
            _read_bounded(self.path, MAX_CREDENTIAL_BYTES, "promotion journal"),
            "promotion journal",
        )
        required = {
            "schema_version",
            "kind",
            "request_id",
            "nonce",
            "credential_sha256",
            "facts_sha256",
            "audit_directory",
            "links",
            "phase",
            "sequence",
            "created_at",
            "updated_at",
        }
        if set(value) != required:
            raise PromotionError("promotion journal fields are incomplete or unknown")
        if value.get("schema_version") != 1 or value.get("kind") != JOURNAL_KIND:
            raise PromotionError("promotion journal schema or kind is invalid")
        expected = {
            "request_id": credential["request"]["request_id"],
            "nonce": credential["nonce"],
            "credential_sha256": credential_sha256,
            "facts_sha256": sha256_bytes(_canonical_json_bytes(credential["facts"])),
            "audit_directory": str(
                _absolute_without_resolving(self.config.audit_root)
                / f"{credential['request']['request_id']}-{credential['nonce']}"
            ),
            "links": {
                "old_current": credential["facts"]["links"]["current_target"],
                "old_previous": credential["facts"]["links"]["previous_target"],
                "target": credential["facts"]["target"]["path"],
            },
        }
        if any(value.get(key) != expected_value for key, expected_value in expected.items()):
            raise PromotionError("promotion journal does not match the supplied credential")
        sequence = value.get("sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise PromotionError("promotion journal sequence is invalid")
        if value.get("phase") not in JOURNAL_PHASES:
            raise PromotionError("promotion journal phase is invalid")
        return value

    def _cleanup_temporaries(self) -> None:
        pattern = re.compile(rf"\.{re.escape(self.path.name)}\.[0-9]+\.[0-9a-f]{{12}}\.tmp\Z")
        removed = False
        for path in self.path.parent.iterdir():
            if pattern.fullmatch(path.name) is None:
                continue
            _assert_owned_regular(path, private=True)
            if path.stat().st_nlink > 2:
                raise PromotionError("orphan promotion journal temporary is unsafe")
            path.unlink()
            removed = True
        if removed:
            _fsync_directory(self.path.parent)

    def update(
        self,
        credential: Mapping[str, Any],
        credential_sha256: str,
        phase: str,
    ) -> dict[str, Any]:
        if phase not in JOURNAL_PHASES:
            raise PromotionError("promotion journal phase is invalid")
        value = self.load_bound(credential, credential_sha256)
        value["phase"] = phase
        value["sequence"] = int(value["sequence"]) + 1
        value["updated_at"] = _utc_now()
        self._replace(value)
        return value

    def clear(self, credential: Mapping[str, Any], credential_sha256: str) -> None:
        self.load_bound(credential, credential_sha256)
        self.path.unlink()
        _fsync_directory(self.path.parent)

    def _create(self, value: Mapping[str, Any]) -> None:
        raw = _canonical_json_bytes(value)
        temporary = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
        )
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            _fsync_directory(self.path.parent)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _replace(self, value: Mapping[str, Any]) -> None:
        raw = _canonical_json_bytes(value)
        temporary = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
        )
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            _fsync_directory(self.path.parent)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


class Controller(Protocol):
    def run(self, action: str, environment: Mapping[str, str]) -> dict[str, Any]: ...


class SubprocessController:
    def __init__(self, config: PromotionConfig) -> None:
        self.config = config

    def run(self, action: str, environment: Mapping[str, str]) -> dict[str, Any]:
        if action not in {"start", "stop"}:
            raise PromotionError("controller action must be exactly start or stop")
        if PROMOTION_LOCK_FD_ENV in environment:
            raise PromotionError("controller environment cannot supply the promotion lock lease")
        child_environment = dict(environment)
        lock_descriptor = _ACTIVE_PROMOTION_LOCK_FD.get()
        pass_fds: tuple[int, ...] = ()
        if lock_descriptor is not None:
            os.fstat(lock_descriptor)
            child_environment[PROMOTION_LOCK_FD_ENV] = str(lock_descriptor)
            pass_fds = (lock_descriptor,)
        command = [str(self.config.controller), action]
        try:
            result = subprocess.run(
                command,
                cwd=self.config.project_dir,
                env=child_environment,
                capture_output=True,
                timeout=self.config.controller_timeout_seconds,
                check=False,
                pass_fds=pass_fds,
            )
        except OSError as exc:
            raise ControllerError(
                f"controller {action} could not execute",
                {
                    "action": action,
                    "returncode": None,
                    "timed_out": False,
                    "execution_error": type(exc).__name__,
                },
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ControllerError(
                f"controller {action} timed out",
                {
                    "action": action,
                    "returncode": None,
                    "timed_out": True,
                    "stdout_sha256": sha256_bytes(exc.stdout or b""),
                    "stderr_sha256": sha256_bytes(exc.stderr or b""),
                },
            ) from exc
        if len(result.stdout) > MAX_TOOL_OUTPUT_BYTES or len(result.stderr) > MAX_TOOL_OUTPUT_BYTES:
            raise ControllerError(
                f"controller {action} output exceeded its safety bound",
                {"action": action, "returncode": result.returncode, "oversized": True},
            )
        summary = {
            "action": action,
            "returncode": result.returncode,
            "timed_out": False,
            "stdout_bytes": len(result.stdout),
            "stderr_bytes": len(result.stderr),
            "stdout_sha256": sha256_bytes(result.stdout),
            "stderr_sha256": sha256_bytes(result.stderr),
        }
        if result.returncode != 0:
            raise ControllerError(f"controller {action} failed", summary)
        return summary


class TransactionInspector(Protocol):
    def wait_dead(self, identity: Mapping[str, Any], timeout: float) -> None: ...

    def wait_port_free(self, timeout: float) -> None: ...

    def wait_running(self, release: Path, runtime: Path, timeout: float) -> dict[str, Any]: ...


class TransactionHealth(Protocol):
    def wait(
        self,
        identity: ReleaseIdentity,
        process: Mapping[str, Any],
        timeout: float,
    ) -> dict[str, Any]: ...


class TransactionLinks(Protocol):
    def lock(self) -> contextlib.AbstractContextManager[None]: ...

    def inspect(self) -> dict[str, str]: ...

    def promote(
        self,
        target: Path,
        expected_current: Path,
        expected_previous: Path,
    ) -> None: ...

    def restore(self, old_current: Path, old_previous: Path) -> None: ...


class AuditTrail:
    def __init__(self, root: Path, request_id: str, nonce: str) -> None:
        root = _absolute_without_resolving(root)
        _assert_owned_directory(root.parent)
        root_preexisted = root.exists()
        root.mkdir(mode=0o700, exist_ok=True)
        if not root_preexisted:
            _fsync_directory(root.parent)
        if root.is_symlink():
            raise PromotionError("audit root must not be a symlink")
        metadata = root.stat()
        if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
            raise PromotionError("audit root ownership or permissions are unsafe")
        self.directory = root / f"{request_id}-{nonce}"
        try:
            self.directory.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise PromotionError("preflight credential was already consumed") from exc
        _fsync_directory(root)
        self.sequence = 0

    @classmethod
    def resume(
        cls,
        root: Path,
        request_id: str,
        nonce: str,
        expected_directory: Path,
    ) -> AuditTrail:
        root, directory = cls._validate_location(
            root,
            request_id,
            nonce,
            expected_directory,
        )
        metadata = directory.stat()
        if stat.S_IMODE(metadata.st_mode) != 0o700:
            raise PromotionError("recoverable audit directory must remain mode 0700")
        cls._cleanup_orphan_temporaries(directory)
        if (directory / "SHA256SUMS").exists():
            raise PromotionError("audit directory is already sealed")
        result_path = directory / "result.json"
        if result_path.exists():
            _assert_owned_regular(result_path, private=True)
            checkpoint = _decode_json(
                _read_bounded(result_path, MAX_CREDENTIAL_BYTES, "audit checkpoint"),
                "audit checkpoint",
            )
            if checkpoint.get("status") not in {"rollback_failed", "recovery_failed"}:
                raise PromotionError("unsealed audit result is not a recovery checkpoint")

        sequence = cls._scan_records(directory, allow_readonly=False)
        instance = cls.__new__(cls)
        instance.directory = directory
        instance.sequence = sequence
        _fsync_directory(root)
        return instance

    @classmethod
    def load_sealed_result(
        cls,
        root: Path,
        request_id: str,
        nonce: str,
        expected_directory: Path,
    ) -> tuple[dict[str, Any], bool] | None:
        _root, directory = cls._validate_location(
            root,
            request_id,
            nonce,
            expected_directory,
        )
        cls._cleanup_orphan_temporaries(directory)
        sums_path = directory / "SHA256SUMS"
        result_path = directory / "result.json"
        if not result_path.exists():
            if sums_path.exists():
                raise PromotionError("audit checksums exist without a result")
            return None
        _assert_owned_regular(result_path, private=False)
        result = _decode_json(
            _read_bounded(result_path, MAX_CREDENTIAL_BYTES, "audit result"),
            "audit result",
        )
        status = result.get("status")
        if status in {"rollback_failed", "recovery_failed"}:
            if sums_path.exists():
                raise PromotionError("incomplete recovery checkpoint must not be sealed")
            return None
        if status not in {"promoted", "rolled_back", "recovered"}:
            raise PromotionError("audit result status is invalid")
        cls._scan_records(directory, allow_readonly=True)
        entries = list(directory.iterdir())
        for path in entries:
            _assert_owned_regular(path, private=False)
            metadata = path.stat()
            if metadata.st_nlink > 2 or stat.S_IMODE(metadata.st_mode) not in {
                0o600,
                0o440,
            }:
                raise PromotionError("audit file ownership or permissions are invalid")
        fully_sealed = False
        if sums_path.exists():
            cls._validate_checksums(directory, entries, sums_path)
            fully_sealed = stat.S_IMODE(directory.stat().st_mode) == 0o550 and all(
                stat.S_IMODE(path.stat().st_mode) == 0o440 for path in entries
            )
        elif stat.S_IMODE(directory.stat().st_mode) != 0o700:
            raise PromotionError("result-only audit directory permissions are invalid")
        return result, fully_sealed

    @classmethod
    def finalize_partial_seal(
        cls,
        root: Path,
        request_id: str,
        nonce: str,
        expected_directory: Path,
        result: Mapping[str, Any],
    ) -> None:
        _root, directory = cls._validate_location(
            root,
            request_id,
            nonce,
            expected_directory,
        )
        directory.chmod(0o700)
        for path in directory.iterdir():
            path.chmod(0o600)
        instance = cls.__new__(cls)
        instance.directory = directory
        instance.sequence = cls._scan_records(directory, allow_readonly=False)
        instance.seal(result)

    @staticmethod
    def _validate_checksums(
        directory: Path,
        entries: Sequence[Path],
        sums_path: Path,
    ) -> None:
        raw_sums = _read_bounded(sums_path, MAX_CREDENTIAL_BYTES, "audit checksums")
        try:
            lines = raw_sums.decode("ascii").splitlines()
        except UnicodeError as exc:
            raise PromotionError("audit checksums are not ASCII") from exc
        expected_json = {path.name for path in entries if path.suffix == ".json"}
        observed_json: set[str] = set()
        for line in lines:
            try:
                digest, name = line.split("  ", 1)
            except ValueError as exc:
                raise PromotionError("audit checksum line is invalid") from exc
            if (
                SHA256_RE.fullmatch(digest) is None
                or Path(name).name != name
                or name in observed_json
                or name not in expected_json
            ):
                raise PromotionError("audit checksum entry is invalid")
            if not secrets.compare_digest(sha256_file(directory / name), digest):
                raise PromotionError("sealed audit checksum mismatch")
            observed_json.add(name)
        if observed_json != expected_json or "result.json" not in observed_json:
            raise PromotionError("sealed audit checksum set is incomplete")

    @staticmethod
    def _cleanup_orphan_temporaries(directory: Path) -> None:
        temporary_pattern = re.compile(
            r"\.(?:[0-9]{3}-[a-z][a-z0-9-]{1,63}\.json|result\.json|SHA256SUMS)"
            r"\.[0-9]+\.[0-9a-f]{12}\.tmp\Z"
        )
        removed = False
        for path in directory.iterdir():
            if temporary_pattern.fullmatch(path.name) is None:
                continue
            _assert_owned_regular(path, private=True)
            if path.stat().st_nlink > 2:
                raise PromotionError("orphan audit temporary has unsafe hard links")
            path.unlink()
            removed = True
        if removed:
            _fsync_directory(directory)

    @staticmethod
    def _scan_records(directory: Path, *, allow_readonly: bool) -> int:
        records: list[int] = []
        record_pattern = re.compile(r"([0-9]{3})-([a-z][a-z0-9-]{1,63})\.json\Z")
        for path in directory.iterdir():
            if path.name in {"result.json", "SHA256SUMS"}:
                continue
            match = record_pattern.fullmatch(path.name)
            if match is None:
                raise PromotionError(f"unexpected entry in promotion audit: {path.name}")
            _assert_owned_regular(path, private=not allow_readonly)
            metadata = path.stat()
            allowed_modes = {0o600, 0o440} if allow_readonly else {0o600}
            if metadata.st_nlink > 2 or stat.S_IMODE(metadata.st_mode) not in allowed_modes:
                raise PromotionError("audit record ownership or permissions are invalid")
            sequence = int(match.group(1))
            value = _decode_json(
                _read_bounded(path, MAX_CREDENTIAL_BYTES, "audit record"),
                "audit record",
            )
            if set(value) != {
                "schema_version",
                "sequence",
                "recorded_at",
                "phase",
                "status",
                "details",
            }:
                raise PromotionError("audit record fields are incomplete or unknown")
            if (
                value.get("schema_version") != 1
                or value.get("sequence") != sequence
                or value.get("phase") != match.group(2)
                or not isinstance(value.get("details"), dict)
            ):
                raise PromotionError("audit record content does not match its filename")
            records.append(sequence)
        sequences = sorted(records)
        if sequences != list(range(1, len(sequences) + 1)):
            raise PromotionError("audit record sequence is not contiguous")
        return len(sequences)

    @staticmethod
    def _validate_location(
        root: Path,
        request_id: str,
        nonce: str,
        expected_directory: Path,
    ) -> tuple[Path, Path]:
        root = _absolute_without_resolving(root)
        _assert_owned_directory(root)
        expected = root / f"{request_id}-{nonce}"
        if _absolute_without_resolving(expected_directory) != expected:
            raise PromotionError("promotion journal audit directory is not canonical")
        directory = _direct_child(expected, root, "promotion audit directory")
        if directory != expected.resolve(strict=True):
            raise PromotionError("promotion audit directory identity changed")
        return root, directory

    def record(self, phase: str, status: str, details: Mapping[str, Any]) -> Path:
        sequence = self.sequence + 1
        value = {
            "schema_version": 1,
            "sequence": sequence,
            "recorded_at": _utc_now(),
            "phase": phase,
            "status": status,
            "details": dict(details),
        }
        path = self.directory / f"{sequence:03d}-{phase}.json"
        self._write_new(path, _canonical_json_bytes(value), 0o600)
        _fsync_directory(self.directory)
        self.sequence = sequence
        return path

    def seal(self, result: Mapping[str, Any]) -> None:
        result_path = self.directory / "result.json"
        self._write_atomic(result_path, _canonical_json_bytes(result), 0o600)
        checksums = []
        for path in sorted(self.directory.glob("*.json")):
            checksums.append(f"{sha256_file(path)}  {path.name}\n")
        self._write_atomic(
            self.directory / "SHA256SUMS",
            "".join(checksums).encode("ascii"),
            0o600,
        )
        for path in self.directory.iterdir():
            path.chmod(0o440)
        self.directory.chmod(0o550)
        _fsync_directory(self.directory)
        _fsync_directory(self.directory.parent)

    def checkpoint(self, result: Mapping[str, Any]) -> None:
        if result.get("status") not in {"rollback_failed", "recovery_failed"}:
            raise PromotionError("only an incomplete recovery may be checkpointed")
        self._write_atomic(
            self.directory / "result.json",
            _canonical_json_bytes(result),
            0o600,
        )
        _fsync_directory(self.directory)
        _fsync_directory(self.directory.parent)

    @staticmethod
    def _write_new(path: Path, raw: bytes, mode: int) -> None:
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
            mode,
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            _fsync_directory(path.parent)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _write_atomic(path: Path, raw: bytes, mode: int) -> None:
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
            mode,
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            _fsync_directory(path.parent)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _identity_from_facts(value: Mapping[str, Any]) -> ReleaseIdentity:
    return ReleaseIdentity(
        path=Path(str(value["path"])),
        version=str(value["version"]),
        build_id=str(value["build_id"]),
        git_sha=str(value["git_sha"]),
        runtime_version=str(value["runtime_version"]),
        manifest_sha256=str(value["manifest_sha256"]),
    )


class PromotionTransaction:
    def __init__(
        self,
        config: PromotionConfig,
        *,
        preflight: PreflightCapture,
        controller: Controller,
        inspector: TransactionInspector,
        health: TransactionHealth,
        links: TransactionLinks,
        journal: PromotionJournal | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.preflight = preflight
        self.controller = controller
        self.inspector = inspector
        self.health = health
        self.links = links
        self.journal = journal or PromotionJournal(config)
        self.clock = clock

    def apply(self, credential_path: Path, expected_sha256: str) -> dict[str, Any]:
        credential = load_credential(
            credential_path,
            expected_sha256,
            self.config,
            now=self.clock(),
        )
        nonce = str(credential["nonce"])
        with self.links.lock():
            credential = load_credential(
                credential_path,
                expected_sha256,
                self.config,
                now=self.clock(),
            )
            audit = AuditTrail(self.config.audit_root, self.config.request_id, nonce)
            audit.record(
                "credential",
                "accepted",
                {
                    "credential_path": str(credential_path),
                    "credential_sha256": expected_sha256,
                    "expires_at": credential["expires_at"],
                    "preflight_credential": credential,
                },
            )
            try:
                fresh = self.preflight.capture(self.config)
            except BaseException as exc:
                result = {
                    "schema_version": 1,
                    "status": "rejected",
                    "reason": "preflight revalidation failed",
                    "rollback_attempted": False,
                    "rollback_succeeded": None,
                }
                audit.record(
                    "revalidation",
                    "failed",
                    {"error": f"{type(exc).__name__}: {exc}"[:500]},
                )
                audit.seal(result)
                raise PromotionError("preflight revalidation failed") from exc
            if fresh != credential["facts"]:
                result = {
                    "schema_version": 1,
                    "status": "rejected",
                    "reason": "preflight facts changed before apply",
                    "rollback_attempted": False,
                    "rollback_succeeded": None,
                }
                audit.record("revalidation", "failed", {"facts_match": False})
                audit.seal(result)
                raise PromotionError("preflight facts changed before apply")
            audit.record(
                "revalidation",
                "passed",
                {
                    "facts_match": True,
                    "credential_valid_at_apply_start": True,
                    "completed_after_credential_expiry": self.clock()
                    >= int(credential["expires_at_epoch"]),
                },
            )
            try:
                journal = self.journal.begin(
                    credential,
                    expected_sha256,
                    audit.directory,
                )
            except BaseException as exc:
                result = {
                    "schema_version": 1,
                    "status": "rejected",
                    "reason": "active promotion journal could not be created",
                    "rollback_attempted": False,
                    "rollback_succeeded": None,
                }
                audit.record(
                    "journal",
                    "failed",
                    {"error": f"{type(exc).__name__}: {exc}"[:500]},
                )
                audit.seal(result)
                raise PromotionError("active promotion journal could not be created") from exc
            audit.record("journal", "prepared", journal)

            facts = credential["facts"]
            target = _identity_from_facts(facts["target"])
            rollback = _identity_from_facts(facts["rollback"])
            old_current = Path(facts["links"]["current_target"])
            old_previous = Path(facts["links"]["previous_target"])
            target_runtime = Path(facts["releases"][str(target.path)]["runtime"])
            rollback_runtime = Path(facts["releases"][str(rollback.path)]["runtime"])
            target_environment = facts["target_environment"]
            rollback_environment = facts["rollback_environment"]
            old_process = facts["current_process"]
            stop_attempted = False
            old_stopped_confirmed = False
            target_start_attempted = False
            target_process_verified = False
            target_process: Mapping[str, Any] | None = None
            try:
                self.journal.update(credential, expected_sha256, "stopping-old")
                stop_attempted = True
                self.preflight.assert_bound_inputs(self.config, facts)
                stopped = self.controller.run("stop", rollback_environment)
                self.preflight.assert_bound_inputs(self.config, facts)
                audit.record("stop-old", "passed", stopped)
                self.inspector.wait_dead(old_process, self.config.stop_timeout_seconds)
                self.inspector.wait_port_free(self.config.stop_timeout_seconds)
                old_stopped_confirmed = True
                self.journal.update(credential, expected_sha256, "old-stopped")
                audit.record("old-death", "passed", {"port_free": True})

                self.journal.update(credential, expected_sha256, "promoting-links")
                self.links.promote(target.path, old_current, old_previous)
                self.journal.update(credential, expected_sha256, "links-promoted")
                audit.record(
                    "links",
                    "passed",
                    {"current": str(target.path), "previous": str(old_current)},
                )

                target_start_attempted = True
                self.journal.update(credential, expected_sha256, "starting-target")
                self.preflight.assert_bound_inputs(self.config, facts)
                started = self.controller.run("start", target_environment)
                self.preflight.assert_bound_inputs(self.config, facts)
                self.journal.update(credential, expected_sha256, "target-started")
                audit.record("start-target", "passed", started)
                process = self.inspector.wait_running(
                    target.path,
                    target_runtime,
                    self.config.health_timeout_seconds,
                )
                target_process = process
                target_process_verified = True
                health = self.health.wait(
                    target,
                    process,
                    self.config.health_timeout_seconds,
                )
                stable_process = self.inspector.wait_running(
                    target.path,
                    target_runtime,
                    self.config.health_timeout_seconds,
                )
                if stable_process != process:
                    raise PromotionError("target process identities changed across the health gate")
                self.preflight.assert_bound_inputs(self.config, facts)
                self.journal.update(credential, expected_sha256, "target-healthy")
                audit.record(
                    "target-gate",
                    "passed",
                    {"process": process, "health": health},
                )
                result = {
                    "schema_version": 1,
                    "status": "promoted",
                    "request_id": self.config.request_id,
                    "target": target.as_dict(),
                    "rollback_release": rollback.as_dict(),
                    "rollback_attempted": False,
                    "rollback_succeeded": None,
                    "audit_directory": str(audit.directory),
                }
                audit.seal(result)
                self.journal.clear(credential, expected_sha256)
                return result
            except BaseException as exc:
                rollback_succeeded = False
                recovery_error = ""

                def recovery_record(
                    phase: str,
                    status: str,
                    details: Mapping[str, Any],
                ) -> None:
                    try:
                        audit.record(phase, status, details)
                    except BaseException:
                        pass

                def recovery_journal(phase: str) -> None:
                    try:
                        self.journal.update(credential, expected_sha256, phase)
                    except BaseException:
                        pass

                if stop_attempted:
                    try:
                        if target_start_attempted:
                            try:
                                recovery_journal("stopping-target")
                                self.preflight.assert_bound_inputs(self.config, facts)
                                stopped_target = self.controller.run("stop", target_environment)
                                self.preflight.assert_bound_inputs(self.config, facts)
                                if target_process_verified:
                                    if target_process is None:
                                        raise PromotionError(
                                            "verified target process evidence is missing"
                                        )
                                    self.inspector.wait_dead(
                                        target_process,
                                        self.config.stop_timeout_seconds,
                                    )
                                self.inspector.wait_port_free(
                                    self.config.stop_timeout_seconds,
                                )
                                recovery_journal("target-stopped")
                                recovery_record("stop-target", "passed", stopped_target)
                            except BaseException as stop_exc:
                                details = (
                                    stop_exc.result
                                    if isinstance(stop_exc, ControllerError)
                                    else {"error": f"{type(stop_exc).__name__}: {stop_exc}"[:500]}
                                )
                                recovery_record("stop-target", "failed", details)
                                raise PromotionError(
                                    "target cleanup was not proven; refusing link restoration"
                                ) from stop_exc
                        recovery_journal("restoring-links")
                        self.links.restore(old_current, old_previous)
                        recovery_journal("links-restored")
                        recovery_record(
                            "restore-links",
                            "passed",
                            {"current": str(old_current), "previous": str(old_previous)},
                        )
                        rollback_process: Mapping[str, Any] | None = None
                        rollback_health: Mapping[str, Any] | None = None
                        if not old_stopped_confirmed:
                            try:
                                rollback_process = self.inspector.wait_running(
                                    rollback.path,
                                    rollback_runtime,
                                    self.config.health_timeout_seconds,
                                )
                            except BaseException as running_exc:
                                recovery_record(
                                    "existing-rollback",
                                    "not-running",
                                    {"error": f"{type(running_exc).__name__}: {running_exc}"[:500]},
                                )
                            else:
                                rollback_health = self.health.wait(
                                    rollback,
                                    rollback_process,
                                    self.config.health_timeout_seconds,
                                )
                                stable_rollback_process = self.inspector.wait_running(
                                    rollback.path,
                                    rollback_runtime,
                                    self.config.health_timeout_seconds,
                                )
                                if stable_rollback_process != rollback_process:
                                    raise PromotionError(
                                        "rollback process identities changed across the health gate"
                                    )
                                self.preflight.assert_bound_inputs(self.config, facts)
                                recovery_journal("rollback-healthy")
                                recovery_record(
                                    "rollback-gate",
                                    "passed",
                                    {
                                        "process": rollback_process,
                                        "health": rollback_health,
                                        "already_running": True,
                                    },
                                )
                                rollback_succeeded = True
                        if not rollback_succeeded:
                            recovery_journal("starting-rollback")
                            self.preflight.assert_bound_inputs(self.config, facts)
                            restarted = self.controller.run("start", rollback_environment)
                            self.preflight.assert_bound_inputs(self.config, facts)
                            recovery_record("restart-rollback", "passed", restarted)
                            rollback_process = self.inspector.wait_running(
                                rollback.path,
                                rollback_runtime,
                                self.config.health_timeout_seconds,
                            )
                            rollback_health = self.health.wait(
                                rollback,
                                rollback_process,
                                self.config.health_timeout_seconds,
                            )
                            stable_rollback_process = self.inspector.wait_running(
                                rollback.path,
                                rollback_runtime,
                                self.config.health_timeout_seconds,
                            )
                            if stable_rollback_process != rollback_process:
                                raise PromotionError(
                                    "rollback process identities changed across the health gate"
                                )
                            self.preflight.assert_bound_inputs(self.config, facts)
                            recovery_journal("rollback-healthy")
                            recovery_record(
                                "rollback-gate",
                                "passed",
                                {"process": rollback_process, "health": rollback_health},
                            )
                            rollback_succeeded = True
                    except BaseException as rollback_exc:
                        recovery_error = f"{type(rollback_exc).__name__}: {rollback_exc}"
                        recovery_record(
                            "rollback",
                            "failed",
                            {"error": recovery_error[:500]},
                        )
                result = {
                    "schema_version": 1,
                    "status": "rolled_back" if rollback_succeeded else "rollback_failed",
                    "request_id": self.config.request_id,
                    "target": target.as_dict(),
                    "rollback_release": rollback.as_dict(),
                    "failure": f"{type(exc).__name__}: {exc}"[:500],
                    "rollback_attempted": stop_attempted,
                    "rollback_succeeded": rollback_succeeded,
                    "rollback_error": recovery_error[:500],
                    "audit_directory": str(audit.directory),
                }
                try:
                    if rollback_succeeded:
                        audit.seal(result)
                    else:
                        recovery_journal("recovery-required")
                        audit.checkpoint(result)
                except BaseException as audit_exc:
                    raise PromotionApplyError(
                        "promotion failed; rollback outcome was determined but audit sealing failed: "
                        f"{type(audit_exc).__name__}",
                        rollback_succeeded=rollback_succeeded,
                    ) from exc
                if rollback_succeeded:
                    try:
                        self.journal.clear(credential, expected_sha256)
                    except BaseException as journal_exc:
                        raise PromotionApplyError(
                            "rollback passed but the active promotion journal could not be cleared: "
                            f"{type(journal_exc).__name__}",
                            rollback_succeeded=True,
                        ) from exc
                raise PromotionApplyError(
                    "promotion failed and rollback "
                    + ("succeeded" if rollback_succeeded else "failed"),
                    rollback_succeeded=rollback_succeeded,
                ) from exc

    def recover(self, credential_path: Path, expected_sha256: str) -> dict[str, Any]:
        credential = load_credential(
            credential_path,
            expected_sha256,
            self.config,
            now=self.clock(),
            allow_expired_for_recovery=True,
        )
        nonce = str(credential["nonce"])
        with self.links.lock():
            credential = load_credential(
                credential_path,
                expected_sha256,
                self.config,
                now=self.clock(),
                allow_expired_for_recovery=True,
            )
            journal = self.journal.load_bound(credential, expected_sha256)
            audit_directory = Path(str(journal["audit_directory"]))
            facts = credential["facts"]
            target = _identity_from_facts(facts["target"])
            rollback = _identity_from_facts(facts["rollback"])
            old_current = Path(str(facts["links"]["current_target"]))
            old_previous = Path(str(facts["links"]["previous_target"]))
            target_runtime = Path(str(facts["releases"][str(target.path)]["runtime"]))
            rollback_runtime = Path(str(facts["releases"][str(rollback.path)]["runtime"]))
            target_environment = facts["target_environment"]
            rollback_environment = facts["rollback_environment"]

            completion = AuditTrail.load_sealed_result(
                self.config.audit_root,
                self.config.request_id,
                nonce,
                audit_directory,
            )
            if completion is not None:
                sealed_result, fully_sealed = completion
                return self._finish_sealed_recovery(
                    credential,
                    expected_sha256,
                    sealed_result,
                    audit_directory,
                    fully_sealed,
                    facts,
                    target,
                    rollback,
                    target_runtime,
                    rollback_runtime,
                )

            audit = AuditTrail.resume(
                self.config.audit_root,
                self.config.request_id,
                nonce,
                audit_directory,
            )
            try:
                self.journal.update(credential, expected_sha256, "recovery-started")
                audit.record(
                    "recovery-start",
                    "accepted",
                    {
                        "credential_path": str(credential_path),
                        "credential_sha256": expected_sha256,
                        "credential_expired": self.clock() >= int(credential["expires_at_epoch"]),
                        "interrupted_journal": journal,
                    },
                )
                original_links = {
                    "current_target": str(old_current),
                    "previous_target": str(old_previous),
                }
                observed_before_revalidation = self.links.inspect()
                allow_tool_drift = journal["phase"] in {
                    "prepared",
                    "recovery-failed",
                } and observed_before_revalidation == original_links
                observed = self.preflight.validate_recovery(
                    self.config,
                    facts,
                    allow_tool_drift=allow_tool_drift,
                )
                audit.record(
                    "recovery-revalidation",
                    "passed",
                    {
                        "links": observed,
                        "tool_drift_allowed_before_destructive_phase": allow_tool_drift,
                        "links_before_revalidation": observed_before_revalidation,
                    },
                )

                promoted_links = {
                    "current_target": str(target.path),
                    "previous_target": str(old_current),
                }
                target_was_selected = observed == promoted_links
                old_process_must_start = observed != original_links

                if target_was_selected:
                    target_process: Mapping[str, Any] | None = None
                    try:
                        target_process = self.inspector.wait_running(
                            target.path,
                            target_runtime,
                            min(2.0, float(self.config.health_timeout_seconds)),
                        )
                    except PromotionError:
                        pass
                    self.journal.update(
                        credential,
                        expected_sha256,
                        "recovery-stopping-target",
                    )
                    self.preflight.assert_bound_inputs(self.config, facts)
                    stopped_target = self.controller.run("stop", target_environment)
                    self.preflight.assert_bound_inputs(self.config, facts)
                    if target_process is not None:
                        self.inspector.wait_dead(
                            target_process,
                            self.config.stop_timeout_seconds,
                        )
                    self.inspector.wait_port_free(self.config.stop_timeout_seconds)
                    self.journal.update(
                        credential,
                        expected_sha256,
                        "recovery-target-stopped",
                    )
                    audit.record("recovery-stop-target", "passed", stopped_target)

                self.journal.update(
                    credential,
                    expected_sha256,
                    "recovery-restoring-links",
                )
                self.links.restore(old_current, old_previous)
                if self.links.inspect() != original_links:
                    raise PromotionError("recovery links did not reach the original state")
                self.journal.update(
                    credential,
                    expected_sha256,
                    "recovery-links-restored",
                )
                audit.record("recovery-restore-links", "passed", original_links)

                rollback_process: Mapping[str, Any] | None = None
                if not old_process_must_start:
                    try:
                        rollback_process = self.inspector.wait_running(
                            rollback.path,
                            rollback_runtime,
                            min(2.0, float(self.config.health_timeout_seconds)),
                        )
                    except PromotionError:
                        pass
                if rollback_process is None:
                    self.inspector.wait_port_free(self.config.stop_timeout_seconds)
                    self.journal.update(
                        credential,
                        expected_sha256,
                        "recovery-starting-rollback",
                    )
                    self.preflight.assert_bound_inputs(self.config, facts)
                    restarted = self.controller.run("start", rollback_environment)
                    self.preflight.assert_bound_inputs(self.config, facts)
                    audit.record("recovery-start-rollback", "passed", restarted)
                    rollback_process = self.inspector.wait_running(
                        rollback.path,
                        rollback_runtime,
                        self.config.health_timeout_seconds,
                    )
                rollback_health = self.health.wait(
                    rollback,
                    rollback_process,
                    self.config.health_timeout_seconds,
                )
                stable_process = self.inspector.wait_running(
                    rollback.path,
                    rollback_runtime,
                    self.config.health_timeout_seconds,
                )
                if stable_process != rollback_process:
                    raise PromotionError(
                        "rollback process identities changed across the recovery health gate"
                    )
                if not allow_tool_drift:
                    self.preflight.assert_bound_inputs(self.config, facts)
                self.journal.update(
                    credential,
                    expected_sha256,
                    "recovery-rollback-healthy",
                )
                audit.record(
                    "recovery-rollback-gate",
                    "passed",
                    {"process": rollback_process, "health": rollback_health},
                )
                result = {
                    "schema_version": 1,
                    "status": "recovered",
                    "request_id": self.config.request_id,
                    "target": target.as_dict(),
                    "rollback_release": rollback.as_dict(),
                    "interrupted_phase": journal["phase"],
                    "rollback_attempted": True,
                    "rollback_succeeded": True,
                    "audit_directory": str(audit.directory),
                }
                audit.seal(result)
                self.journal.clear(credential, expected_sha256)
                return result
            except BaseException as exc:
                try:
                    self.journal.update(credential, expected_sha256, "recovery-failed")
                except BaseException:
                    pass
                try:
                    audit.record(
                        "recovery",
                        "failed",
                        {"error": f"{type(exc).__name__}: {exc}"[:500]},
                    )
                    audit.checkpoint(
                        {
                            "schema_version": 1,
                            "status": "recovery_failed",
                            "request_id": self.config.request_id,
                            "target": target.as_dict(),
                            "rollback_release": rollback.as_dict(),
                            "failure": f"{type(exc).__name__}: {exc}"[:500],
                            "rollback_attempted": True,
                            "rollback_succeeded": False,
                            "audit_directory": str(audit.directory),
                        }
                    )
                except BaseException as audit_exc:
                    raise PromotionApplyError(
                        "interrupted promotion recovery failed and audit checkpointing failed: "
                        f"{type(audit_exc).__name__}",
                        rollback_succeeded=False,
                    ) from exc
                raise PromotionApplyError(
                    "interrupted promotion recovery failed; active journal retained",
                    rollback_succeeded=False,
                ) from exc

    def _finish_sealed_recovery(
        self,
        credential: Mapping[str, Any],
        expected_sha256: str,
        result: Mapping[str, Any],
        audit_directory: Path,
        fully_sealed: bool,
        facts: Mapping[str, Any],
        target: ReleaseIdentity,
        rollback: ReleaseIdentity,
        target_runtime: Path,
        rollback_runtime: Path,
    ) -> dict[str, Any]:
        if (
            result.get("schema_version") != 1
            or result.get("request_id") != self.config.request_id
            or result.get("status") not in {"promoted", "rolled_back", "recovered"}
            or result.get("target") != target.as_dict()
            or result.get("rollback_release") != rollback.as_dict()
            or result.get("audit_directory") != str(audit_directory)
        ):
            raise PromotionError(
                "sealed audit is not a completed outcome eligible for journal cleanup"
            )
        observed = self.preflight.validate_recovery(self.config, facts)
        if result["status"] == "promoted":
            expected_links = {
                "current_target": str(target.path),
                "previous_target": str(rollback.path),
            }
            identity = target
            runtime = target_runtime
        else:
            expected_links = {
                "current_target": str(rollback.path),
                "previous_target": str(facts["links"]["previous_target"]),
            }
            identity = rollback
            runtime = rollback_runtime
        if observed != expected_links:
            raise PromotionError("sealed outcome does not match the current release links")
        process = self.inspector.wait_running(
            identity.path,
            runtime,
            self.config.health_timeout_seconds,
        )
        self.health.wait(identity, process, self.config.health_timeout_seconds)
        if (
            self.inspector.wait_running(
                identity.path,
                runtime,
                self.config.health_timeout_seconds,
            )
            != process
        ):
            raise PromotionError("sealed outcome process identity is not stable")
        self.preflight.assert_bound_inputs(self.config, facts)
        if not fully_sealed:
            AuditTrail.finalize_partial_seal(
                self.config.audit_root,
                self.config.request_id,
                str(credential["nonce"]),
                audit_directory,
                result,
            )
        self.journal.clear(credential, expected_sha256)
        recovered = dict(result)
        recovered["journal_recovery"] = "cleared-after-sealed-outcome-revalidation"
        return recovered


def build_default_transaction(
    config: PromotionConfig,
) -> tuple[PreflightBuilder, PromotionTransaction]:
    links = AtomicLinkManager(config)
    inspector = ProcessInspector(config)
    health = HealthGate(config)
    preflight = PreflightBuilder(
        config,
        links=links,
        inspector=inspector,
        health=health,
    )
    transaction = PromotionTransaction(
        config,
        preflight=preflight,
        controller=SubprocessController(config),
        inspector=inspector,
        health=health,
        links=links,
    )
    return preflight, transaction


__all__ = (
    "AtomicLinkManager",
    "AuditTrail",
    "ControllerError",
    "HealthGate",
    "PreflightBuilder",
    "ProcessInspector",
    "PromotionApplyError",
    "PromotionConfig",
    "PromotionError",
    "PromotionTransaction",
    "ReleaseIdentity",
    "ReleaseVerifier",
    "SubprocessController",
    "build_default_transaction",
    "controller_environment",
    "create_credential",
    "database_password_file_record",
    "load_credential",
    "read_release_identity",
    "runtime_paths",
    "sha256_bytes",
    "write_credential",
)
