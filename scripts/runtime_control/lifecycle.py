"""Fail-closed per-service lifecycle planning and dispatch."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .constants import LIFECYCLE_COMMANDS, SCHEMA_VERSION
from .inspection import RuntimeInspector
from .manifest import Inventory, InventoryError, ensure_trusted_path
from .process_identity import utc_iso
from .redaction import redact_text, sanitize

MAX_EVIDENCE_BYTES = 1024 * 1024
MAX_AUDIT_EVENT_BYTES = 64 * 1024
CONTROL_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
REQUEST_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{7,127}")


class LifecycleError(RuntimeError):
    def __init__(self, code: str, message: str, *, details: list[str] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or []


def _read_attested_file(spec: Mapping[str, Any], roots: tuple[Path, ...]) -> None:
    try:
        path = ensure_trusted_path(str(spec["path"]), roots)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise LifecycleError("evidence-invalid", "adoption evidence is not a regular file")
            if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & 0o022:
                raise LifecycleError(
                    "evidence-invalid",
                    "adoption evidence must be caller-owned and not group/world writable",
                )
            content = handle.read(MAX_EVIDENCE_BYTES + 1)
    except LifecycleError:
        raise
    except (InventoryError, OSError, KeyError, TypeError) as exc:
        raise LifecycleError("evidence-invalid", f"cannot read adoption evidence: {exc}") from exc
    if len(content) > MAX_EVIDENCE_BYTES:
        raise LifecycleError("evidence-invalid", "adoption evidence exceeds the size limit")
    if hashlib.sha256(content).hexdigest() != spec["sha256"]:
        raise LifecycleError("evidence-invalid", "adoption evidence digest does not match")


class AtomicAuditWriter:
    """Write one immutable JSON file per event with an atomic rename commit point."""

    def __init__(self, directory: Path, roots: tuple[Path, ...]) -> None:
        try:
            self.directory = ensure_trusted_path(str(directory), roots)
            metadata = self.directory.lstat()
        except (InventoryError, OSError) as exc:
            raise LifecycleError("audit-unavailable", f"audit directory is unavailable: {exc}") from exc
        if self.directory.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
            raise LifecycleError("audit-unavailable", "audit path must be a non-symlink directory")
        if metadata.st_uid != os.geteuid():
            raise LifecycleError("audit-unavailable", "audit directory must be owned by the caller")
        if stat.S_IMODE(metadata.st_mode) & 0o022:
            raise LifecycleError(
                "audit-unavailable", "audit directory must not be group/world writable"
            )

    def write(self, payload: Mapping[str, Any]) -> str:
        event_id = f"{time.time_ns()}-{secrets.token_hex(8)}"
        event = sanitize({"schema_version": 1, "event_id": event_id, **payload})
        content = (json.dumps(event, ensure_ascii=True, sort_keys=True) + "\n").encode("utf-8")
        if len(content) > MAX_AUDIT_EVENT_BYTES:
            raise LifecycleError("audit-write-failed", "audit event exceeds the size limit")

        directory_flags = (
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
        )
        directory_flags |= getattr(os, "O_NOFOLLOW", 0)
        temporary_name = f".{event_id}.tmp"
        final_name = f"{event_id}.json"
        directory_fd = os.open(self.directory, directory_flags)
        try:
            descriptor = os.open(
                temporary_name,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=directory_fd,
            )
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                    os.fchmod(handle.fileno(), 0o400)
                os.replace(
                    temporary_name,
                    final_name,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                )
                os.fsync(directory_fd)
            except Exception:
                try:
                    os.unlink(temporary_name, dir_fd=directory_fd)
                except OSError:
                    pass
                raise
        except LifecycleError:
            raise
        except OSError as exc:
            raise LifecycleError("audit-write-failed", f"cannot persist audit event: {exc}") from exc
        finally:
            os.close(directory_fd)
        return str(self.directory / final_name)


class LifecycleDispatcher:
    def __init__(
        self,
        inventory: Inventory,
        *,
        inspector: RuntimeInspector | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        now: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.inventory = inventory
        self.inspector = inspector or RuntimeInspector(inventory)
        self.runner = runner
        self.now = now
        self.monotonic = monotonic

    def _service(self, service_id: str) -> Mapping[str, Any]:
        services = {service["id"]: service for service in self.inventory["services"]}
        try:
            return services[service_id]
        except KeyError as exc:
            raise LifecycleError("unknown-service", f"unknown service id: {service_id}") from exc

    def _controller(
        self, service: Mapping[str, Any], operation: str
    ) -> tuple[Path, tuple[str, str], int]:
        if operation not in LIFECYCLE_COMMANDS:
            raise LifecycleError("operation-forbidden", "unsupported lifecycle operation")
        controller = service["controller"]
        lifecycle = controller.get("lifecycle")
        if controller.get("adoption") != "managed" or not isinstance(lifecycle, Mapping):
            raise LifecycleError(
                "lifecycle-not-enabled", "service has not adopted managed lifecycle control"
            )
        if lifecycle.get("enabled") is not True:
            raise LifecycleError("lifecycle-not-enabled", "service lifecycle is not enabled")
        try:
            path = ensure_trusted_path(str(controller["path"]), self.inventory.trusted_roots)
            metadata = path.lstat()
        except (InventoryError, OSError) as exc:
            raise LifecycleError("controller-invalid", f"controller is unavailable: {exc}") from exc
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise LifecycleError("controller-invalid", "controller must be a non-symlink regular file")
        if metadata.st_mode & 0o111 == 0:
            raise LifecycleError("controller-invalid", "controller is not executable")
        if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & 0o022:
            raise LifecycleError(
                "controller-invalid", "controller must be caller-owned and not group/world writable"
            )
        configured_argv = (lifecycle.get("argv") or {}).get(operation)
        fixed_argv = (str(path), operation)
        if not isinstance(configured_argv, list) or tuple(configured_argv) != fixed_argv:
            raise LifecycleError("controller-invalid", "controller argv contract is not fixed")
        return path, fixed_argv, int(lifecycle["timeout_seconds"])

    def _preflight(
        self, service: Mapping[str, Any], operation: str, *, dry_run: bool
    ) -> tuple[Path, tuple[str, str], int, AtomicAuditWriter, tuple[int, int] | None]:
        controller_path, fixed_argv, timeout = self._controller(service, operation)
        lifecycle = service["controller"]["lifecycle"]
        audit = AtomicAuditWriter(Path(lifecycle["audit_directory"]), self.inventory.trusted_roots)
        try:
            for artifact in lifecycle["controller_artifacts"]:
                _read_attested_file(artifact, self.inventory.trusted_roots)
            for name in ("checkpoint", "rollback"):
                _read_attested_file(lifecycle[name], self.inventory.trusted_roots)
        except LifecycleError as exc:
            audit.write(
                {
                    "recorded_at": utc_iso(self.now()),
                    "service_id": service["id"],
                    "operation": operation,
                    "dry_run": dry_run,
                    "outcome": "preflight-denied",
                    "error_code": exc.code,
                    "error": redact_text(str(exc)),
                }
            )
            raise

        inspect_ids = [] if operation in {"restart", "stop"} else [service["id"]]
        inspection = self.inspector.inspect(inspect_ids, doctor=True)
        observed = {item["id"]: item for item in inspection.get("services") or []}
        target = observed.get(service["id"])
        if target is None:
            raise LifecycleError("inspection-incomplete", "target service inspection is missing")

        blockers: list[str] = []
        if operation != "stop":
            for dependency in service.get("dependencies") or []:
                if dependency.get("required") is not True:
                    continue
                item = observed.get(dependency["service"])
                if item is None or item.get("status") != "healthy":
                    blockers.append(f"required dependency {dependency['service']} is not healthy")
            for dependency in target.get("external_dependencies") or []:
                if (
                    dependency.get("required") is True
                    and dependency.get("observed_status") != "healthy"
                ):
                    blockers.append(
                        f"required external dependency {dependency.get('name')} is not verified healthy"
                    )

        if operation in {"restart", "stop"}:
            for candidate in self.inventory["services"]:
                if candidate["id"] == service["id"]:
                    continue
                required_targets = {
                    item["service"]
                    for item in candidate.get("dependencies") or []
                    if item.get("required") is True
                }
                candidate_observation = observed.get(candidate["id"]) or {}
                if (
                    service["id"] in required_targets
                    and (candidate_observation.get("pid") or {}).get("status") == "running"
                ):
                    blockers.append(
                        f"running dependent {candidate['id']} requires this service"
                    )

        secret_policy = target.get("secret_policy") or {}
        if secret_policy.get("compliant") is not True:
            blockers.append("secret policy inspection is not compliant")

        pid = target.get("pid") or {}
        if operation == "start":
            if pid.get("status") != "missing":
                blockers.append("start requires an unambiguous missing PID identity")
        elif pid.get("identity_strength") != "strong" or pid.get("control_eligible") is not True:
            blockers.append("operation requires strong PID and start-ticks identity")

        before_identity: tuple[int, int] | None = None
        if operation != "start" and not blockers:
            observed_pid = pid.get("pid")
            observed_ticks = pid.get("starttime_ticks")
            if (
                isinstance(observed_pid, bool)
                or not isinstance(observed_pid, int)
                or observed_pid <= 0
                or isinstance(observed_ticks, bool)
                or not isinstance(observed_ticks, int)
                or observed_ticks <= 0
            ):
                blockers.append("strong identity observation is missing PID or start ticks")
            else:
                before_identity = (observed_pid, observed_ticks)

        if operation in {"restart", "status"}:
            required_health = [
                item
                for spec, item in zip(service.get("health") or [], target.get("health") or [])
                if spec.get("required") is True
            ]
            if not required_health or any(item.get("status") != "passing" for item in required_health):
                blockers.append("required health checks are not passing")

        if blockers:
            audit.write(
                {
                    "recorded_at": utc_iso(self.now()),
                    "service_id": service["id"],
                    "operation": operation,
                    "dry_run": dry_run,
                    "outcome": "preflight-denied",
                    "error_code": "lifecycle-preflight-failed",
                    "details": blockers,
                }
            )
            raise LifecycleError(
                "lifecycle-preflight-failed",
                "service lifecycle preflight failed",
                details=blockers,
            )
        return controller_path, fixed_argv, timeout, audit, before_identity

    def _postflight(
        self,
        service: Mapping[str, Any],
        operation: str,
        before_identity: tuple[int, int] | None,
    ) -> list[str]:
        inspection = self.inspector.inspect([service["id"]], doctor=True)
        observed = {item["id"]: item for item in inspection.get("services") or []}
        target = observed.get(service["id"])
        if target is None:
            return ["target service postflight inspection is missing"]
        pid = target.get("pid") or {}
        if operation == "stop":
            blockers = [] if pid.get("status") == "missing" else ["stop did not clear PID identity"]
            if before_identity is None or not self.inspector.identity_is_gone(*before_identity):
                blockers.append("stop did not prove the original PID incarnation exited")
            return blockers

        blockers: list[str] = []
        if pid.get("identity_strength") != "strong" or pid.get("control_eligible") is not True:
            blockers.append("postflight PID identity is not strong")
        after_identity = (pid.get("pid"), pid.get("starttime_ticks"))
        if operation == "restart" and before_identity == after_identity:
            blockers.append("restart did not replace the original PID incarnation")
        if operation == "status" and before_identity != after_identity:
            blockers.append("status changed or lost the observed PID incarnation")
        required_health = [
            item
            for spec, item in zip(service.get("health") or [], target.get("health") or [])
            if spec.get("required") is True
        ]
        if not required_health or any(item.get("status") != "passing" for item in required_health):
            blockers.append("postflight required health checks are not passing")
        if (target.get("secret_policy") or {}).get("compliant") is not True:
            blockers.append("postflight secret policy is not compliant")
        return blockers

    def execute(
        self,
        service_id: str,
        operation: str,
        *,
        dry_run: bool = True,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        service = self._service(service_id)
        if request_id is not None and REQUEST_ID_RE.fullmatch(request_id) is None:
            raise LifecycleError("request-id-invalid", "lifecycle request ID has an invalid format")
        if not dry_run and request_id is None:
            raise LifecycleError(
                "request-id-required", "applied lifecycle operations require a request ID"
            )
        effective_request_id = request_id or "unassigned-plan"
        if not self.inventory.manifest_sha256:
            raise LifecycleError("inventory-unattested", "inventory source digest is unavailable")
        controller_path, fixed_argv, timeout, audit, before_identity = self._preflight(
            service, operation, dry_run=dry_run
        )
        base_event = {
            "recorded_at": utc_iso(self.now()),
            "service_id": service_id,
            "operation": operation,
            "controller_path": str(controller_path),
            "dry_run": dry_run,
            "request_id": effective_request_id,
            "actor_uid": os.geteuid(),
            "actor_pid": os.getpid(),
            "inventory_sha256": self.inventory.manifest_sha256,
            "controller_artifact_sha256": [
                item["sha256"] for item in service["controller"]["lifecycle"]["controller_artifacts"]
            ],
            "before_identity": (
                {"pid": before_identity[0], "starttime_ticks": before_identity[1]}
                if before_identity
                else None
            ),
        }
        if dry_run:
            audit_path = audit.write({**base_event, "outcome": "planned"})
            return sanitize(
                {
                    "schema_version": SCHEMA_VERSION,
                    "operation": operation,
                    "service_id": service_id,
                    "mode": "lifecycle",
                    "dry_run": True,
                    "read_only": True,
                    "outcome": "planned",
                    "request_id": effective_request_id,
                    "controller": {"path": str(controller_path), "operation": operation},
                    "audit_path": audit_path,
                }
            )

        started_audit = audit.write({**base_event, "outcome": "dispatch-started"})
        started = self.monotonic()
        try:
            lifecycle = service["controller"]["lifecycle"]
            for artifact in lifecycle["controller_artifacts"]:
                _read_attested_file(artifact, self.inventory.trusted_roots)
            environment = {
                "GLOBEMIND_HOME": str(self.inventory.trusted_roots[0]),
                "HOME": str(self.inventory.trusted_roots[0]),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": CONTROL_PATH,
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            completed = self.runner(
                list(fixed_argv),
                cwd=str(self.inventory.trusted_roots[0]),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
                shell=False,
                env=environment,
            )
        except LifecycleError as exc:
            failed_audit = audit.write(
                {
                    **base_event,
                    "outcome": "dispatch-aborted",
                    "started_audit": started_audit,
                    "duration_ms": round((self.monotonic() - started) * 1000, 3),
                    "error_code": exc.code,
                    "error": redact_text(str(exc)),
                }
            )
            raise LifecycleError(
                "controller-execution-failed",
                f"controller dispatch was aborted; audit={failed_audit}",
            ) from exc
        except (OSError, subprocess.TimeoutExpired) as exc:
            failed_audit = audit.write(
                {
                    **base_event,
                    "outcome": "dispatch-failed",
                    "started_audit": started_audit,
                    "duration_ms": round((self.monotonic() - started) * 1000, 3),
                    "error": redact_text(str(exc)),
                }
            )
            raise LifecycleError(
                "controller-execution-failed",
                f"controller execution failed; audit={failed_audit}",
            ) from exc

        postflight = (
            self._postflight(service, operation, before_identity)
            if completed.returncode == 0
            else []
        )
        outcome = "succeeded" if completed.returncode == 0 and not postflight else "failed"
        completed_audit = audit.write(
            {
                **base_event,
                "outcome": outcome,
                "request_id": effective_request_id,
                "started_audit": started_audit,
                "duration_ms": round((self.monotonic() - started) * 1000, 3),
                "exit_code": completed.returncode,
                "postflight": postflight,
                "output_policy": "discarded",
            }
        )
        return sanitize(
            {
                "schema_version": SCHEMA_VERSION,
                "operation": operation,
                "service_id": service_id,
                "mode": "lifecycle",
                "dry_run": False,
                "read_only": False,
                "outcome": outcome,
                "exit_code": completed.returncode,
                "postflight": postflight,
                "controller": {"path": str(controller_path), "operation": operation},
                "output_policy": "discarded",
                "audit_path": completed_audit,
            }
        )
