"""Read-only service, pipeline, filesystem, port, and health inspection."""

from __future__ import annotations

import glob
import ipaddress
import itertools
import json
import math
import os
import socket
import stat
import struct
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from .constants import SAFE_COMPLETE_VALUES, SCHEMA_VERSION, SEVERITY_ORDER
from .dependency_probes import DependencyProbeRunner
from .json_safety import JSONSafetyError, loads_bounded
from .manifest import InventoryError, ensure_trusted_path, service_dependency_order
from .process_identity import ProcessIdentityInspector, utc_iso
from .redaction import redact_text, sanitize

MAX_STATE_JSON_BYTES = 8 * 1024 * 1024
MAX_STATE_TIMESTAMP = 253_402_300_799.0
MAX_CONTROL_META_BYTES = 64 * 1024
MAX_CONTROL_RESPONSE_BYTES = 8 * 1024

SUPPORTED_SECRET_ENVIRONMENT_POLICIES = {
    "config-path-reference-only",
    "credential-file-only",
    "process-environment-only",
    "secret-file-or-process-environment-only",
}


def _external_dependency_spec(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return {
            "name": value,
            "required": True,
            "verification": "unverified",
            "via_health": None,
        }
    if not isinstance(value, Mapping):
        return {
            "name": "invalid-external-dependency",
            "required": True,
            "verification": "unverified",
            "via_health": None,
        }
    result = {
        "name": value.get("name"),
        "required": bool(value.get("required")),
        "verification": value.get("verification"),
        "via_health": value.get("via_health"),
    }
    if value.get("via_probe") is not None:
        result["via_probe"] = value.get("via_probe")
    if value.get("reason") is not None:
        result["reason"] = value.get("reason")
    return result


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


_LOOPBACK_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def _loopback_http_open(request: urllib.request.Request, *, timeout: float) -> Any:
    return _LOOPBACK_OPENER.open(request, timeout=timeout)


def _parse_timestamp(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            numeric_value = float(value)
        except (OverflowError, ValueError):
            return None
        if math.isfinite(numeric_value) and 0 < numeric_value <= MAX_STATE_TIMESTAMP:
            return numeric_value
    if not isinstance(value, str) or not value.strip():
        return None
    stripped = value.strip()
    try:
        numeric = float(stripped)
    except ValueError:
        numeric = 0.0
    if math.isfinite(numeric) and 0 < numeric <= MAX_STATE_TIMESTAMP:
        return numeric
    try:
        parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00")).timestamp()
        return parsed if math.isfinite(parsed) else None
    except (OverflowError, OSError, ValueError):
        return None


class RuntimeInspector(ProcessIdentityInspector):
    def __init__(
        self,
        inventory: Mapping[str, Any],
        *,
        proc_root: Path = Path("/proc"),
        now: Callable[[], float] = time.time,
        http_open: Callable[..., Any] = _loopback_http_open,
        tcp_connect: Callable[[tuple[str, int], float], Any] = socket.create_connection,
        unix_socket_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.inventory = inventory
        declared_roots = tuple(getattr(inventory, "trusted_roots", ()))
        if declared_roots:
            self.trusted_roots = declared_roots
        elif proc_root != Path("/proc"):
            # Explicit synthetic /proc roots are a programmatic test seam. A
            # loaded manifest always carries its independently validated roots.
            self.trusted_roots = (proc_root.resolve().parent,)
        else:
            self.trusted_roots = (
                Path(__file__).resolve().parents[2],
                Path(__file__).resolve().parents[3],
            )
        self.now = now
        self.http_open = http_open
        self.tcp_connect = tcp_connect
        self.unix_socket_factory = unix_socket_factory or (
            lambda: socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        )
        self._init_process_identity(proc_root=proc_root)

    def identity_is_gone(self, pid: int, starttime_ticks: int) -> bool:
        """Prove that the exact observed PID incarnation is no longer alive."""

        process = self._read_process(pid)
        return process is None or not process.alive or process.starttime_ticks != starttime_ticks

    @staticmethod
    def _issue(severity: str, code: str, message: str, **details: Any) -> dict[str, Any]:
        result: dict[str, Any] = {
            "severity": severity if severity in SEVERITY_ORDER else "error",
            "code": code,
            "message": redact_text(message),
        }
        if details:
            result["details"] = sanitize(details)
        return result

    def _trusted_file(self, value: str, *, allow_glob: bool = False) -> Path:
        return ensure_trusted_path(value, self.trusted_roots, allow_glob=allow_glob)

    def _resolve_port(self, config: Any) -> tuple[int | None, dict[str, Any] | None]:
        if isinstance(config, int) and not isinstance(config, bool):
            return (config if 1 <= config <= 65535 else None), None
        if not isinstance(config, dict):
            return None, self._issue(
                "error", "port-config-invalid", "port number configuration is invalid"
            )
        meta = config.get("pid_meta")
        if isinstance(meta, dict):
            try:
                path = self._trusted_file(str(meta["path"]))
                values = path.read_text(encoding="utf-8").split()
                port = int(values[int(meta.get("token_index", 2))])
                if 1 <= port <= 65535:
                    return port, None
            except (InventoryError, OSError, ValueError, IndexError, KeyError):
                pass
        fallback = config.get("fallback")
        if isinstance(fallback, int) and not isinstance(fallback, bool) and 1 <= fallback <= 65535:
            return fallback, self._issue(
                "warning",
                "port-fallback-used",
                "dynamic port metadata was unavailable; fallback used",
            )
        return None, self._issue("error", "port-unresolved", "port number could not be resolved")

    def _probe_tcp(self, host: str, port: int, timeout: float) -> tuple[bool, float, str | None]:
        started = time.monotonic()
        try:
            if not ipaddress.ip_address(host).is_loopback:
                return False, 0.0, "non-loopback TCP target rejected"
        except ValueError:
            return False, 0.0, "non-loopback TCP target rejected"
        try:
            connection = self.tcp_connect((host, port), timeout)
            close = getattr(connection, "close", None)
            if callable(close):
                close()
        except OSError as exc:
            return False, (time.monotonic() - started) * 1000, redact_text(str(exc))
        return True, (time.monotonic() - started) * 1000, None

    @staticmethod
    def _load_control_meta(path: Path) -> Mapping[str, Any]:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("control metadata must be a non-symlink regular file")
            raw = handle.read(MAX_CONTROL_META_BYTES + 1)
        if len(raw) > MAX_CONTROL_META_BYTES:
            raise ValueError("control metadata exceeds the size limit")
        value = loads_bounded(raw.decode("utf-8"), max_depth=12, max_nodes=512)
        if not isinstance(value, Mapping):
            raise ValueError("control metadata must be a JSON object")
        return value

    @staticmethod
    def _control_socket_identity(path: Path) -> dict[str, Any]:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISSOCK(metadata.st_mode):
            raise ValueError("control path must be a non-symlink Unix socket")
        mode = stat.S_IMODE(metadata.st_mode)
        if mode != 0o600:
            raise ValueError("control socket must have mode 0600")
        return {
            "path": str(path.resolve(strict=True)),
            "device": int(metadata.st_dev),
            "inode": int(metadata.st_ino),
            "owner": int(metadata.st_uid),
            "mode": mode,
        }

    def _inspect_unix_control_status(
        self,
        service: Mapping[str, Any],
        spec: Mapping[str, Any],
        pid_result: Mapping[str, Any],
    ) -> dict[str, Any]:
        item: dict[str, Any] = {
            "type": "unix-control-status",
            "path": spec.get("path"),
            "status": "failing",
            "issues": [],
        }
        severity = "error" if spec.get("required", True) else "warning"

        try:
            if (
                pid_result.get("status") != "running"
                or pid_result.get("identity_strength") != "strong"
                or pid_result.get("control_eligible") is not True
            ):
                raise ValueError("strong process identity is unavailable")
            pid = pid_result.get("pid")
            start_ticks = pid_result.get("starttime_ticks")
            if (
                isinstance(pid, bool)
                or not isinstance(pid, int)
                or isinstance(start_ticks, bool)
                or not isinstance(start_ticks, int)
            ):
                raise ValueError("process identity is incomplete")

            meta_path = self._trusted_file(str(service["pid"]["meta_path"]))
            meta = self._load_control_meta(meta_path)
            identity = meta.get("identity")
            if meta.get("schema_version") != 2 or not isinstance(identity, Mapping):
                raise ValueError("unsupported control metadata schema")
            if identity.get("pid") != pid or identity.get("start_ticks") != start_ticks:
                raise ValueError("control metadata process identity mismatch")
            instance_id = meta.get("instance_id")
            boot_id = identity.get("boot_id")
            if (
                not isinstance(instance_id, str)
                or not instance_id
                or len(instance_id) > 256
                or not isinstance(boot_id, str)
                or not boot_id
                or len(boot_id) > 128
            ):
                raise ValueError("control metadata identity is incomplete")

            socket_path = self._trusted_file(str(spec["path"]))
            before = self._control_socket_identity(socket_path)
            if meta.get("control_socket") != before:
                raise ValueError("declared control socket identity mismatch")

            started = time.monotonic()
            with self.unix_socket_factory() as client:
                client.settimeout(float(spec["timeout_seconds"]))
                client.connect(str(socket_path))
                peer_option = getattr(socket, "SO_PEERCRED", None)
                if peer_option is None:
                    raise ValueError("Unix peer credentials are unavailable")
                peer_pid, peer_uid, _peer_gid = struct.unpack(
                    "3i",
                    client.getsockopt(socket.SOL_SOCKET, peer_option, struct.calcsize("3i")),
                )
                if peer_pid != pid or peer_uid != before["owner"] or peer_uid != os.geteuid():
                    raise ValueError("control socket peer identity mismatch")
                request = {
                    "schema_version": 1,
                    "command": "status",
                    "instance_id": instance_id,
                    "boot_id": boot_id,
                }
                client.sendall(
                    json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8")
                    + b"\n"
                )
                response_bytes = b""
                while b"\n" not in response_bytes:
                    block = client.recv(4096)
                    if not block:
                        break
                    response_bytes += block
                    if len(response_bytes) > MAX_CONTROL_RESPONSE_BYTES:
                        raise ValueError("control response exceeds the size limit")
            if b"\n" not in response_bytes:
                raise ValueError("control response is incomplete")
            after = self._control_socket_identity(socket_path)
            if after != before:
                raise ValueError("control socket identity changed during status check")

            response = loads_bounded(
                response_bytes.split(b"\n", 1)[0].decode("utf-8"),
                max_depth=8,
                max_nodes=128,
            )
            if not isinstance(response, Mapping) or response.get("ok") is not True:
                raise ValueError("control status request was rejected")
            observed_status = response.get("status")
            response_pid = response.get("pid")
            if (
                response.get("instance_id") != instance_id
                or isinstance(response_pid, bool)
                or not isinstance(response_pid, int)
                or response_pid != pid
                or observed_status not in spec["expect_status"]
            ):
                raise ValueError("control status response identity or state mismatch")
        except (
            InventoryError,
            JSONSafetyError,
            KeyError,
            OSError,
            UnicodeError,
            ValueError,
            struct.error,
        ):
            item["issues"].append(
                self._issue(
                    severity,
                    "unix-control-status-failed",
                    "authenticated Unix control status check failed",
                )
            )
            return item

        item.update(
            {
                "status": "passing",
                "observed_status": observed_status,
                "peer_identity_verified": True,
                "latency_ms": round((time.monotonic() - started) * 1000, 2),
            }
        )
        return item

    def _inspect_ports(
        self, service: Mapping[str, Any]
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        results: list[dict[str, Any]] = []
        resolved: dict[str, int] = {}
        for spec in service.get("port") or []:
            port, config_issue = self._resolve_port(spec.get("number"))
            item: dict[str, Any] = {
                "id": spec.get("id"),
                "host": spec.get("host"),
                "port": port,
                "issues": [],
            }
            if config_issue:
                item["issues"].append(config_issue)
            if port is None:
                item["status"] = "unknown"
            else:
                resolved[str(spec.get("id"))] = port
                ok, latency, error = self._probe_tcp(
                    str(spec["host"]), port, float(spec.get("timeout_seconds", 1))
                )
                item.update({"status": "open" if ok else "closed", "latency_ms": round(latency, 2)})
                if not ok and spec.get("required", True):
                    item["issues"].append(
                        self._issue(
                            "error",
                            "required-port-closed",
                            "required TCP port is not accepting connections",
                            error=error,
                        )
                    )
            results.append(item)
        return results, resolved

    def _inspect_health(
        self,
        service: Mapping[str, Any],
        ports: Mapping[str, int],
        pid_result: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for spec in service.get("health") or []:
            check_type = spec.get("type")
            if check_type == "tcp-members":
                members = pid_result.get("members") or []
                checked = 0
                healthy = 0
                failed_count = 0
                for member in members:
                    if member.get("status") != "running" or not isinstance(member.get("port"), int):
                        continue
                    checked += 1
                    ok, _latency, _error = self._probe_tcp(
                        str(spec["host"]), int(member["port"]), float(spec["timeout_seconds"])
                    )
                    member["port_status"] = "open" if ok else "closed"
                    if ok:
                        healthy += 1
                    else:
                        failed_count += 1
                item = {
                    "type": "tcp-members",
                    "status": "passing" if checked and healthy == checked else "failing",
                    "checked_members": checked,
                    "healthy_members": healthy,
                    "issues": [],
                }
                if failed_count or not checked:
                    item["issues"].append(
                        self._issue(
                            "error" if spec.get("required", True) else "warning",
                            "pool-member-health-failed",
                            "one or more declared pool members failed TCP health checks",
                            failed_count=failed_count,
                        )
                    )
                results.append(item)
                continue

            if check_type == "unix-control-status":
                results.append(self._inspect_unix_control_status(service, spec, pid_result))
                continue

            port = ports.get(str(spec.get("port_ref")))
            item = {
                "type": "http",
                "path": spec.get("path", "/"),
                "port_ref": spec.get("port_ref"),
                "status": "unknown",
                "issues": [],
            }
            if port is None:
                item["issues"].append(
                    self._issue(
                        "error", "health-port-unresolved", "HTTP health port is unavailable"
                    )
                )
                results.append(item)
                continue
            port_spec = next(
                (item for item in service["port"] if item["id"] == spec["port_ref"]), None
            )
            if port_spec is None:
                item["issues"].append(
                    self._issue(
                        "error", "health-port-undeclared", "HTTP health port is not declared"
                    )
                )
                results.append(item)
                continue
            host = str(port_spec["host"])
            try:
                loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                loopback = False
            if not loopback:
                item["issues"].append(
                    self._issue(
                        "error",
                        "health-target-rejected",
                        "non-loopback HTTP health target rejected",
                    )
                )
                results.append(item)
                continue
            path = str(spec["path"])
            url_host = f"[{host}]" if ":" in host else host
            url = f"http://{url_host}:{port}{path}"
            request = urllib.request.Request(url, headers={"User-Agent": "globemind-runtime/0.9.3"})
            started = time.monotonic()
            try:
                response = self.http_open(request, timeout=float(spec["timeout_seconds"]))
                response_status = getattr(response, "status", None)
                status_code = int(
                    response_status if response_status is not None else response.getcode()
                )
                close = getattr(response, "close", None)
                if callable(close):
                    close()
                error = None
            except urllib.error.HTTPError as exc:
                # Redirects intentionally arrive here instead of following an
                # arbitrary Location supplied by a local health endpoint.
                status_code = int(exc.code)
                error = redact_text(str(exc))
                exc.close()
            except (OSError, urllib.error.URLError, TimeoutError) as exc:
                status_code = 0
                error = redact_text(str(exc))
            expected = [int(code) for code in spec["expect_status"]]
            passing = status_code in expected
            item.update(
                {
                    "status": "passing" if passing else "failing",
                    "status_code": status_code,
                    "latency_ms": round((time.monotonic() - started) * 1000, 2),
                }
            )
            if not passing:
                item["issues"].append(
                    self._issue(
                        "error" if spec.get("required", True) else "warning",
                        "http-health-failed",
                        "HTTP health check returned an unexpected result",
                        error=error,
                        expected_status=expected,
                        actual_status=status_code,
                    )
                )
            results.append(item)
        return results

    def _select_path(self, spec: Mapping[str, Any]) -> Path | None:
        if spec.get("path"):
            return self._trusted_file(str(spec["path"]))
        pattern = self._trusted_file(str(spec["glob"]), allow_glob=True)
        raw_matches = list(itertools.islice(glob.iglob(str(pattern)), 10_001))
        if len(raw_matches) > 10_000:
            raise InventoryError("filesystem glob exceeded the bounded inspection limit")
        matches: list[Path] = []
        for item in raw_matches:
            try:
                matches.append(self._trusted_file(item))
            except InventoryError as exc:
                raise InventoryError("filesystem glob match escapes trusted roots") from exc
        if not matches:
            return None
        try:
            return max(matches, key=lambda item: item.stat().st_mtime)
        except OSError:
            return sorted(matches)[-1]

    def _inspect_file(self, spec: Mapping[str, Any], category: str) -> dict[str, Any]:
        try:
            path = self._select_path(spec)
        except InventoryError as exc:
            return {
                "configured_path": None,
                "path": None,
                "exists": False,
                "status": "invalid",
                "issues": [self._issue("error", f"{category}-path-untrusted", str(exc))],
            }
        result: dict[str, Any] = {
            "configured_path": spec.get("path") or spec.get("glob"),
            "path": str(path) if path else None,
            "exists": bool(path and path.exists()),
            "authoritative": bool(spec.get("authoritative", False)),
            "issues": [],
        }
        if path is None or not path.exists():
            result["status"] = "missing"
            if spec.get("required", True):
                result["issues"].append(
                    self._issue(
                        "error", f"{category}-missing", f"required {category} file is missing"
                    )
                )
            return result
        try:
            stat_result = path.stat()
        except OSError as exc:
            result["status"] = "unreadable"
            result["issues"].append(
                self._issue("error", f"{category}-unreadable", f"cannot stat {category}: {exc}")
            )
            return result
        result.update(
            {"size_bytes": stat_result.st_size, "modified_at": utc_iso(stat_result.st_mtime)}
        )
        timestamp = stat_result.st_mtime
        timestamp_source = "mtime"
        data: Any = None
        if spec.get("format") == "json":
            if not stat.S_ISREG(stat_result.st_mode):
                result["status"] = "invalid"
                result["issues"].append(
                    self._issue(
                        "error",
                        f"{category}-json-not-regular-file",
                        f"{category} JSON must be a regular file",
                    )
                )
                return result
            if stat_result.st_size > MAX_STATE_JSON_BYTES:
                result["status"] = "invalid"
                result["issues"].append(
                    self._issue(
                        "error",
                        f"{category}-json-too-large",
                        f"{category} JSON exceeds the bounded read limit",
                        maximum_bytes=MAX_STATE_JSON_BYTES,
                    )
                )
                return result
            try:
                with path.open("rb") as handle:
                    raw_data = handle.read(MAX_STATE_JSON_BYTES + 1)
                if len(raw_data) > MAX_STATE_JSON_BYTES:
                    raise JSONSafetyError("JSON grew beyond the bounded read limit")
                data = loads_bounded(raw_data.decode("utf-8"))
            except (OSError, UnicodeError, JSONSafetyError) as exc:
                result["status"] = "invalid"
                result["issues"].append(
                    self._issue(
                        "error",
                        f"{category}-json-invalid",
                        f"{category} JSON cannot be parsed: {exc}",
                    )
                )
                return result
            timestamp_field = spec.get("timestamp_field")
            if timestamp_field:
                parsed = _parse_timestamp(
                    data.get(timestamp_field) if isinstance(data, dict) else None
                )
                if parsed is None:
                    result["issues"].append(
                        self._issue(
                            "error",
                            f"{category}-timestamp-invalid",
                            f"{category} timestamp field is missing or invalid",
                            field=timestamp_field,
                        )
                    )
                else:
                    timestamp = parsed
                    timestamp_source = str(timestamp_field)
            summary_fields = spec.get("summary_fields") or []
            if isinstance(data, dict) and summary_fields:
                result["summary"] = sanitize({field: data.get(field) for field in summary_fields})
            status_field = spec.get("status_field")
            if isinstance(data, dict) and status_field:
                state_value = data.get(status_field)
                normalized_state = (
                    str(state_value).strip().lower() if isinstance(state_value, str) else ""
                )
                declared = {str(item).lower() for item in spec.get("complete_values") or []}
                result["state_value"] = sanitize(state_value)
                result["complete"] = (
                    normalized_state in declared and normalized_state in SAFE_COMPLETE_VALUES
                )

        result["freshness_source"] = timestamp_source
        result["observed_at"] = utc_iso(timestamp)
        age = self.now() - timestamp
        result["age_seconds"] = round(age, 3)
        max_age = spec.get("max_age_seconds")
        if age < -60:
            result["issues"].append(
                self._issue(
                    "warning", f"{category}-clock-skew", f"{category} timestamp is in the future"
                )
            )
        if max_age is not None and age > float(max_age):
            result["issues"].append(
                self._issue(
                    str(spec.get("stale_severity", "warning")),
                    f"{category}-stale",
                    f"{category} has not been refreshed within its declared interval",
                    age_seconds=round(age, 3),
                    max_age_seconds=max_age,
                )
            )
        result["status"] = (
            "stale"
            if any(issue["code"] == f"{category}-stale" for issue in result["issues"])
            else "current"
        )
        return result

    def _inspect_secret_policy(
        self, service: Mapping[str, Any], pid_result: Mapping[str, Any], doctor: bool
    ) -> dict[str, Any]:
        raw_policy = service.get("secret_policy")
        policy = raw_policy if isinstance(raw_policy, Mapping) else {}
        findings = list(pid_result.get("secret_findings") or [])
        issues: list[dict[str, Any]] = []
        policy_known = (
            policy.get("argv") == "forbid-sensitive-values"
            and policy.get("environment") in SUPPORTED_SECRET_ENVIRONMENT_POLICIES
            and policy.get("redact_diagnostics") is True
            and isinstance(policy.get("files"), list)
        )
        if not policy_known:
            issues.append(
                self._issue(
                    "error",
                    "secret-policy-unknown",
                    "secret handling policy is missing or unsupported",
                )
            )
        if findings:
            issues.append(
                self._issue(
                    "error",
                    "secret-in-process-invocation",
                    "process invocation contains a forbidden sensitive option value",
                    options=sorted({finding.get("option", "unknown") for finding in findings}),
                )
            )
        file_checks: list[dict[str, Any]] = []
        if doctor and isinstance(policy.get("files"), list):
            for file_spec in policy.get("files") or []:
                try:
                    path = self._trusted_file(str(file_spec["path"]))
                except InventoryError as exc:
                    item = {
                        "path": None,
                        "exists": False,
                        "issues": [self._issue("error", "secret-file-path-untrusted", str(exc))],
                    }
                else:
                    item = {"path": str(path), "exists": path.is_file(), "issues": []}
                    if not item["exists"]:
                        if file_spec.get("required", True):
                            item["issues"].append(
                                self._issue(
                                    "error",
                                    "secret-file-missing",
                                    "required secret file is missing",
                                )
                            )
                    else:
                        try:
                            mode = path.stat().st_mode & 0o777
                        except OSError as exc:
                            item["issues"].append(
                                self._issue(
                                    "error",
                                    "secret-file-unreadable",
                                    f"cannot stat secret file: {exc}",
                                )
                            )
                        else:
                            item["permissions"] = f"{mode:04o}"
                            allowed_mode = int(str(file_spec["max_permissions"]), 8)
                            if mode & ~allowed_mode:
                                item["issues"].append(
                                    self._issue(
                                        "error",
                                        "secret-file-permissions",
                                        "secret file permissions are broader than policy allows",
                                        actual=f"{mode:04o}",
                                        allowed=f"{allowed_mode:04o}",
                                    )
                                )
                issues.extend(item["issues"])
                file_checks.append(item)
        return {
            "invocation_policy": policy.get("argv"),
            "compliant": not issues,
            "findings": findings,
            "files": file_checks,
            "issues": issues,
        }

    def _inspect_external_dependencies(
        self,
        service: Mapping[str, Any],
        health_results: Sequence[Mapping[str, Any]],
        pid_result: Mapping[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        results: list[dict[str, Any]] = []
        issues: list[dict[str, Any]] = []
        declared_probes = {str(item["id"]): item for item in self.inventory.get("probes") or []}
        probe_results: dict[str, dict[str, Any]] = {}
        probe_runner = DependencyProbeRunner(
            now=self.now,
            http_open=self.http_open,
            tcp_connect=self.tcp_connect,
            listener_ownership_check=self.process_owns_tcp_listener,
        )
        for raw_dependency in service.get("external_dependencies") or []:
            dependency = _external_dependency_spec(raw_dependency)
            observed_status = "unverified"
            via_health = dependency["via_health"]
            if dependency["verification"] == "local-health" and isinstance(via_health, str):
                matching = [
                    check
                    for check in health_results
                    if via_health in {check.get("port_ref"), check.get("type")}
                ]
                if matching:
                    observed_status = (
                        "healthy"
                        if all(check.get("status") == "passing" for check in matching)
                        else "unhealthy"
                    )
            probe_result: dict[str, Any] | None = None
            via_probe = dependency.get("via_probe")
            if dependency["verification"] == "probe" and isinstance(via_probe, str):
                probe = declared_probes.get(via_probe)
                if probe is not None:
                    if via_probe not in probe_results:
                        probe_results[via_probe] = probe_runner.run(probe, pid_result=pid_result)
                    probe_result = probe_results[via_probe]
                    observed_status = str(probe_result["status"])
            item = {**dependency, "observed_status": observed_status}
            if probe_result is not None:
                item["probe"] = probe_result
            results.append(item)
            if observed_status in {"healthy", "external-verified"}:
                continue
            required = bool(dependency["required"])
            if observed_status in {"unhealthy", "unreachable"}:
                severity = "error" if required else "warning"
                code = "external-dependency-unhealthy"
                message = "external dependency health verification is failing"
            elif observed_status == "business-stalled":
                severity = "error" if required else "warning"
                code = "external-dependency-business-stalled"
                message = "local dependency endpoint is reachable but business readiness is stalled"
            else:
                severity = "warning" if required else "info"
                code = "external-dependency-unverified"
                message = (
                    "local reachability is current but does not verify the external dependency"
                    if observed_status == "local-up"
                    else "external dependency has no trusted current verification"
                )
            reason = (
                probe_result.get("reason") if probe_result is not None else dependency.get("reason")
            )
            issues.append(
                self._issue(
                    severity,
                    code,
                    message,
                    dependency=dependency["name"],
                    required=required,
                    verification=dependency["verification"],
                    observed_status=observed_status,
                    reason=reason or "no trusted probe is declared",
                )
            )
        return results, issues

    def _inspect_controller(self, service: Mapping[str, Any], doctor: bool) -> dict[str, Any]:
        controller = service["controller"]
        result = {
            "type": controller.get("type"),
            "path": controller.get("path"),
            "interface": controller.get("interface"),
            "adoption": controller.get("adoption"),
            "issues": [],
        }
        if doctor:
            try:
                path = self._trusted_file(str(controller["path"]))
            except InventoryError as exc:
                result["exists"] = False
                result["issues"].append(self._issue("error", "controller-path-untrusted", str(exc)))
            else:
                result["exists"] = path.is_file()
                if not result["exists"]:
                    result["issues"].append(
                        self._issue(
                            "error",
                            "controller-missing",
                            "declared runtime controller does not exist",
                        )
                    )
        return result

    @staticmethod
    def _collect_issues(result: Mapping[str, Any]) -> list[dict[str, Any]]:
        issues = list(result.get("issues") or [])
        for key in ("controller", "pid", "secret_policy"):
            item = result.get(key)
            if isinstance(item, dict):
                issues.extend(item.get("issues") or [])
        for key in ("port", "log", "health", "state", "output"):
            for item in result.get(key) or []:
                issues.extend(item.get("issues") or [])
        return issues

    @staticmethod
    def _status_for_issues(issues: Sequence[Mapping[str, Any]]) -> str:
        highest = max(
            (SEVERITY_ORDER.get(str(item.get("severity")), 2) for item in issues), default=0
        )
        if highest >= SEVERITY_ORDER["error"]:
            return "unhealthy"
        if highest >= SEVERITY_ORDER["warning"]:
            return "degraded"
        return "healthy"

    def inspect_service(self, service: Mapping[str, Any], *, doctor: bool) -> dict[str, Any]:
        pid_result = self._inspect_pid(service)
        port_results, resolved_ports = self._inspect_ports(service)
        log_results = [self._inspect_file(spec, "log") for spec in service.get("log") or []]
        state_results = [self._inspect_file(spec, "state") for spec in service.get("state") or []]
        output_results = [
            self._inspect_file(spec, "output") for spec in service.get("output") or []
        ]

        if (
            service["pid"].get("expected") == "running-or-complete"
            and pid_result.get("status") != "running"
        ):
            candidates = [
                item
                for item in state_results
                if item.get("authoritative") is True and "complete" in item
            ]
            completion_verified = bool(candidates) and all(
                item.get("complete") is True
                and item.get("status") == "current"
                and not any(
                    issue.get("severity") in {"error", "critical"}
                    for issue in item.get("issues") or []
                )
                for item in candidates
            )
            if completion_verified:
                pid_result["issues"] = [
                    item
                    for item in pid_result.get("issues") or []
                    if item.get("code") not in {"pid-file-missing", "stale-pid"}
                ]
                pid_result["status"] = "complete"

        health_results = self._inspect_health(service, resolved_ports, pid_result)
        external_dependencies, external_issues = self._inspect_external_dependencies(
            service, health_results, pid_result
        )
        result: dict[str, Any] = {
            "id": service["id"],
            "name": service["name"],
            "kind": service.get("kind"),
            "owner": service["owner"],
            "criticality": service["criticality"],
            "check_interval_seconds": service["check_interval_seconds"],
            "dependencies": service.get("dependencies") or [],
            "external_dependencies": external_dependencies,
            "controller": self._inspect_controller(service, doctor),
            "pid": pid_result,
            "port": port_results,
            "log": log_results,
            "health": health_results,
            "state": state_results,
            "output": output_results,
            "secret_policy": self._inspect_secret_policy(service, pid_result, doctor),
            "issues": external_issues,
        }
        # Internal classifications have already been converted to policy
        # findings. Remove them from the process result before serialisation.
        pid_result.pop("secret_findings", None)
        result["issues"] = self._collect_issues(result)
        result["status"] = self._status_for_issues(result["issues"])
        return result

    def inspect(
        self, service_ids: Sequence[str] | None = None, *, doctor: bool = False
    ) -> dict[str, Any]:
        order = service_dependency_order(self.inventory, service_ids or [])
        definitions = {service["id"]: service for service in self.inventory["services"]}
        services = [
            self.inspect_service(definitions[identifier], doctor=doctor) for identifier in order
        ]
        by_id = {service["id"]: service for service in services}
        for service in services:
            for dependency in service.get("dependencies") or []:
                dep_id = dependency["service"]
                dep_status = by_id[dep_id]["status"]
                if dep_status == "healthy":
                    continue
                required = bool(dependency["required"])
                if not required:
                    severity = "info"
                elif dep_status == "unhealthy":
                    severity = "error"
                else:
                    severity = "warning"
                service["issues"].append(
                    self._issue(
                        severity,
                        "dependency-unhealthy",
                        "declared runtime dependency is not healthy",
                        dependency=dep_id,
                        dependency_status=dep_status,
                        required=required,
                    )
                )
            service["status"] = self._status_for_issues(service["issues"])

        counts = {
            status: sum(1 for service in services if service["status"] == status)
            for status in ("healthy", "degraded", "unhealthy")
        }
        overall = (
            "unhealthy"
            if counts["unhealthy"]
            else ("degraded" if counts["degraded"] else "healthy")
        )
        return sanitize(
            {
                "schema_version": SCHEMA_VERSION,
                "inventory_version": self.inventory.get("inventory_version"),
                "generated_at": utc_iso(self.now()),
                "operation": "doctor" if doctor else "status",
                "read_only": True,
                "requested_services": list(service_ids or []),
                "dependency_closure": order,
                "overall_status": overall,
                "summary": {"service_count": len(services), **counts},
                "services": services,
            }
        )


def public_service_definition(service: Mapping[str, Any]) -> dict[str, Any]:
    """Project a manifest entry without process-matching or secret internals."""

    return {
        "id": service["id"],
        "name": service["name"],
        "kind": service["kind"],
        "owner": service["owner"],
        "criticality": service["criticality"],
        "check_interval_seconds": service["check_interval_seconds"],
        "dependencies": service["dependencies"],
        "external_dependencies": [
            {**_external_dependency_spec(item), "observed_status": "unverified"}
            for item in service["external_dependencies"]
        ],
        "controller": {
            "type": service["controller"]["type"],
            "path": service["controller"]["path"],
            "interface": service["controller"]["interface"],
            "adoption": service["controller"]["adoption"],
            "lifecycle_enabled": bool(
                (service["controller"].get("lifecycle") or {}).get("enabled", False)
            ),
        },
        "checks": {
            "ports": len(service["port"]),
            "health": len(service["health"]),
            "logs": len(service["log"]),
            "states": len(service["state"]),
            "outputs": len(service.get("output") or []),
        },
    }


def list_payload(inventory: Mapping[str, Any], service_ids: Sequence[str]) -> dict[str, Any]:
    order = service_dependency_order(inventory, service_ids)
    definitions = {service["id"]: service for service in inventory["services"]}
    return sanitize(
        {
            "schema_version": SCHEMA_VERSION,
            "inventory_version": inventory.get("inventory_version"),
            "operation": "list",
            "read_only": True,
            "requested_services": list(service_ids),
            "dependency_closure": order,
            "control_policy": {
                "mode": inventory["control_policy"]["mode"],
                "read_only": not inventory["control_policy"]["destructive_commands_enabled"],
                "allowed_operations": list(inventory["control_policy"]["allowed_commands"]),
            },
            "services": [
                public_service_definition(definitions[identifier]) for identifier in order
            ],
        }
    )
