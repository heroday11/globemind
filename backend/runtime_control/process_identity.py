"""Linux process inspection with explicit identity assurance levels."""

from __future__ import annotations

import glob
import ipaddress
import itertools
import os
import posixpath
import re
import shlex
import stat
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .json_safety import JSONSafetyError, loads_bounded
from .manifest import InventoryError, ensure_trusted_path
from .redaction import dedupe_findings, inspect_argv, redact_text

MAX_PID_META_BYTES = 64 * 1024


def _token_matches(expected: str, actual: str) -> bool:
    if expected == actual:
        return True
    if expected.startswith("-") or actual.startswith("-"):
        return False
    expected_path = posixpath.normpath(expected)
    actual_path = posixpath.normpath(actual)
    if "/" in expected_path:
        if expected_path.startswith("/"):
            return actual_path == expected_path
        return actual_path == expected_path or actual_path.endswith("/" + expected_path)
    return "/" in actual_path and posixpath.basename(actual_path) == expected_path


def _marker_matches(marker: str, argv: tuple[str, ...]) -> bool:
    try:
        expected_tokens = tuple(shlex.split(marker, posix=True))
    except ValueError:
        return False
    if not expected_tokens or len(expected_tokens) > len(argv):
        return False
    width = len(expected_tokens)
    return any(
        all(
            _token_matches(expected, actual)
            for expected, actual in zip(expected_tokens, argv[offset : offset + width])
        )
        for offset in range(len(argv) - width + 1)
    )


def _json_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise ValueError("configured JSON metadata path is missing")
        current = current[part]
    return current


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def utc_iso(timestamp: float | None = None) -> str:
    value = time.time() if timestamp is None else timestamp
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


@dataclass(frozen=True)
class ProcessObservation:
    state: str
    starttime_ticks: int
    start_epoch: float | None
    name: str
    argv: tuple[str, ...]

    @property
    def alive(self) -> bool:
        return self.state != "Z"


class ProcessIdentityInspector:
    """Mixin used by RuntimeInspector; raw argv remains private to this module."""

    proc_root: Path
    clock_ticks: int
    boot_time: float | None
    trusted_roots: tuple[Path, ...]

    def _init_process_identity(
        self,
        *,
        proc_root: Path,
        clock_ticks: int | None = None,
    ) -> None:
        self.proc_root = proc_root
        self.clock_ticks = clock_ticks or int(os.sysconf("SC_CLK_TCK"))
        self.boot_time = self._read_boot_time()

    def _read_boot_time(self) -> float | None:
        try:
            for line in (self.proc_root / "stat").read_text(encoding="utf-8").splitlines():
                if line.startswith("btime "):
                    return float(line.split()[1])
        except (OSError, ValueError, IndexError):
            return None
        return None

    def _read_process(self, pid: int) -> ProcessObservation | None:
        process_dir = self.proc_root / str(pid)
        try:
            stat_line = (process_dir / "stat").read_text(encoding="utf-8").strip()
            right_paren = stat_line.rfind(")")
            if right_paren < 0:
                return None
            process_name = stat_line[stat_line.find("(") + 1 : right_paren]
            fields = stat_line[right_paren + 2 :].split()
            if len(fields) <= 19:
                return None
            state = fields[0]
            starttime_ticks = int(fields[19])
            raw_cmdline = (process_dir / "cmdline").read_bytes()
        except (OSError, ValueError):
            return None
        argv = tuple(
            part.decode("utf-8", errors="replace") for part in raw_cmdline.split(b"\0") if part
        )
        try:
            comm = (process_dir / "comm").read_text(encoding="utf-8").strip()
        except OSError:
            comm = process_name
        start_epoch = None
        if self.boot_time is not None:
            start_epoch = self.boot_time + (starttime_ticks / self.clock_ticks)
        return ProcessObservation(
            state=state,
            starttime_ticks=starttime_ticks,
            start_epoch=start_epoch,
            name=redact_text(comm)[:128],
            argv=argv,
        )

    @staticmethod
    def _decode_proc_net_address(
        encoded: str,
        *,
        version: int,
    ) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
        raw = bytes.fromhex(encoded)
        if version == 4:
            if len(raw) != 4:
                raise ValueError("invalid IPv4 listener address")
            packed = raw[::-1]
        else:
            if len(raw) != 16:
                raise ValueError("invalid IPv6 listener address")
            packed = b"".join(raw[index : index + 4][::-1] for index in range(0, 16, 4))
        return ipaddress.ip_address(packed)

    def process_owns_tcp_listener(
        self,
        pid: int,
        starttime_ticks: int,
        host: str,
        port: int,
    ) -> bool:
        """Prove that one PID incarnation owns the exact declared TCP listener."""
        try:
            expected_host = ipaddress.ip_address(host)
        except ValueError:
            return False
        if not expected_host.is_loopback or not 1 <= port <= 65535:
            return False

        before = self._read_process(pid)
        if before is None or not before.alive or before.starttime_ticks != starttime_ticks:
            return False

        table_name = "tcp" if expected_host.version == 4 else "tcp6"
        listener_inodes: set[str] = set()
        try:
            lines = (self.proc_root / "net" / table_name).read_text(encoding="ascii").splitlines()
        except OSError:
            return False
        for line in lines[1:]:
            fields = line.split()
            if len(fields) < 10 or fields[3] != "0A":
                continue
            try:
                encoded_host, encoded_port = fields[1].split(":", 1)
                observed_host = self._decode_proc_net_address(
                    encoded_host,
                    version=expected_host.version,
                )
                observed_port = int(encoded_port, 16)
            except (ValueError, IndexError):
                continue
            if observed_host == expected_host and observed_port == port:
                listener_inodes.add(fields[9])
        if not listener_inodes:
            return False

        owned = False
        try:
            for descriptor in (self.proc_root / str(pid) / "fd").iterdir():
                try:
                    target = os.readlink(descriptor)
                except OSError:
                    continue
                if target.startswith("socket:[") and target.endswith("]"):
                    if target[8:-1] in listener_inodes:
                        owned = True
                        break
        except OSError:
            return False
        if not owned:
            return False

        after = self._read_process(pid)
        return bool(
            after is not None
            and after.alive
            and after.starttime_ticks == starttime_ticks
        )

    def _trusted_path(self, value: str, *, allow_glob: bool = False) -> Path:
        return ensure_trusted_path(value, self.trusted_roots, allow_glob=allow_glob)

    @staticmethod
    def _read_pid_metadata(meta_path: Path, spec: Mapping[str, Any]) -> tuple[int, int]:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(meta_path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("PID identity metadata must be a non-symlink regular file")
            raw = handle.read(MAX_PID_META_BYTES + 1)
        if len(raw) > MAX_PID_META_BYTES:
            raise ValueError("PID identity metadata exceeds the size limit")
        text = raw.decode("utf-8")
        meta_spec = spec.get("meta") or {}
        if meta_spec.get("format", "tokens") == "json":
            payload = loads_bounded(text, max_depth=12, max_nodes=512)
            if not isinstance(payload, Mapping):
                raise ValueError("PID identity metadata must be a JSON object")
            if payload.get("schema_version") != meta_spec["schema_version"]:
                raise ValueError("PID identity metadata schema mismatch")
            pid = _positive_int(_json_path(payload, str(meta_spec["pid_path"])), "PID")
            ticks = _positive_int(
                _json_path(payload, str(meta_spec["starttime_ticks_path"])), "start ticks"
            )
            return pid, ticks

        values = text.split()
        pid = int(values[int(meta_spec.get("pid_index", 0))])
        ticks = int(values[int(meta_spec.get("starttime_ticks_index", 1))])
        return _positive_int(pid, "PID"), _positive_int(ticks, "start ticks")

    def _inspect_pid_file(self, spec: Mapping[str, Any], path: Path) -> dict[str, Any]:
        try:
            trusted_path = self._trusted_path(str(path))
        except InventoryError as exc:
            return {
                "path": None,
                "exists": False,
                "status": "invalid",
                "identity_verified": False,
                "identity_strength": "none",
                "control_eligible": False,
                "issues": [self._issue("error", "pid-path-untrusted", str(exc))],
                "secret_findings": [],
            }

        result: dict[str, Any] = {
            "path": str(trusted_path),
            "exists": trusted_path.is_file(),
            "status": "missing",
            "identity_verified": False,
            "identity_strength": "none",
            "control_eligible": False,
            "issues": [],
            "secret_findings": [],
        }
        if not result["exists"]:
            result["issues"].append(
                self._issue("error", "pid-file-missing", "required PID file is missing")
            )
            return result
        try:
            raw_pid = trusted_path.read_text(encoding="utf-8").strip()
            if not re.fullmatch(r"[1-9][0-9]*", raw_pid):
                raise ValueError("PID is not a positive integer")
            pid = int(raw_pid)
        except (OSError, ValueError) as exc:
            result["status"] = "stale"
            result["issues"].append(
                self._issue("error", "pid-file-invalid", f"PID file is invalid: {exc}")
            )
            return result
        result["pid"] = pid

        process = self._read_process(pid)
        if process is None or not process.alive:
            result["status"] = "stale"
            result["alive"] = False
            result["issues"].append(
                self._issue("error", "stale-pid", "PID does not identify a live process")
            )
            return result
        result.update(
            {
                "alive": True,
                "process_name": process.name,
                "process_state": process.state,
                "starttime_ticks": process.starttime_ticks,
            }
        )
        if process.start_epoch is not None:
            result["started_at"] = utc_iso(process.start_epoch)
        result["secret_findings"] = inspect_argv(process.argv)

        identity_ok = True
        missing_markers = [
            str(marker)
            for marker in spec.get("cmdline_contains") or []
            if not _marker_matches(str(marker), process.argv)
        ]
        if missing_markers:
            identity_ok = False
            result["issues"].append(
                self._issue(
                    "error",
                    "pid-cmdline-mismatch",
                    "live PID invocation does not match the declared service",
                    missing_marker_count=len(missing_markers),
                )
            )

        try:
            pidfile_mtime = trusted_path.stat().st_mtime
        except OSError:
            pidfile_mtime = None
        if (
            pidfile_mtime is not None
            and process.start_epoch is not None
            and process.start_epoch
            > pidfile_mtime + float(spec.get("pidfile_start_tolerance_seconds", 5))
        ):
            identity_ok = False
            result["issues"].append(
                self._issue(
                    "error",
                    "pid-starttime-mismatch",
                    "PID file predates the current process start time",
                )
            )

        metadata_verified = False
        meta_path_value = spec.get("meta_path")
        if meta_path_value:
            try:
                meta_path = self._trusted_path(str(meta_path_value))
                result["meta_path"] = str(meta_path)
                meta_pid, meta_ticks = self._read_pid_metadata(meta_path, spec)
            except (
                InventoryError,
                JSONSafetyError,
                IndexError,
                KeyError,
                OSError,
                TypeError,
                UnicodeError,
                ValueError,
            ) as exc:
                identity_ok = False
                result["issues"].append(
                    self._issue(
                        "error", "pid-meta-invalid", f"PID identity metadata is invalid: {exc}"
                    )
                )
            else:
                result["recorded_starttime_ticks"] = meta_ticks
                metadata_verified = meta_pid == pid and meta_ticks == process.starttime_ticks
                if not metadata_verified:
                    identity_ok = False
                    result["issues"].append(
                        self._issue(
                            "error",
                            "pid-meta-mismatch",
                            "PID or process start time differs from identity metadata",
                        )
                    )

        result["identity_verified"] = identity_ok
        result["identity_strength"] = (
            "strong" if identity_ok and metadata_verified else ("weak" if identity_ok else "none")
        )
        # Lifecycle operations remain disabled. This flag describes identity
        # fitness only and is false unless immutable start-ticks metadata agrees.
        result["control_eligible"] = bool(identity_ok and metadata_verified)
        result["status"] = "running" if identity_ok else "stale"
        return result

    def _inspect_pid(self, service: Mapping[str, Any]) -> dict[str, Any]:
        spec = service["pid"]
        if spec["kind"] == "single":
            return self._inspect_pid_file(spec, Path(spec["path"]))

        try:
            pattern = self._trusted_path(str(spec["glob"]), allow_glob=True)
        except InventoryError as exc:
            return {
                "kind": "directory",
                "status": "invalid",
                "identity_strength": "none",
                "control_eligible": False,
                "running_members": 0,
                "stale_members": 0,
                "member_count": 0,
                "minimum_running": int(spec.get("minimum_running", 1)),
                "members": [],
                "secret_findings": [],
                "issues": [self._issue("error", "pid-glob-untrusted", str(exc))],
            }
        raw_matches = list(itertools.islice(glob.iglob(str(pattern)), 10_001))
        too_many_matches = len(raw_matches) > 10_000
        paths: list[Path] = []
        unsafe_matches = 0
        for item in sorted(raw_matches[:10_000]):
            try:
                paths.append(self._trusted_path(item))
            except InventoryError:
                # A symlinked match escaping the trust roots is excluded and
                # represented as a finding without publishing the target path.
                unsafe_matches += 1
                continue
        members: list[dict[str, Any]] = []
        findings: list[dict[str, str]] = []
        for path in paths:
            member = self._inspect_pid_file(spec, path)
            if spec.get("port_from_filename"):
                try:
                    member["port"] = int(path.stem)
                except ValueError:
                    member["issues"].append(
                        self._issue(
                            "error",
                            "member-port-invalid",
                            "PID filename does not contain a valid port",
                        )
                    )
                    member["status"] = "stale"
            findings.extend(member.pop("secret_findings", []))
            members.append(member)
        running = sum(1 for member in members if member.get("status") == "running")
        stale = sum(1 for member in members if member.get("status") == "stale")
        minimum = int(spec.get("minimum_running", 1))
        issues: list[dict[str, Any]] = []
        if too_many_matches:
            issues.append(
                self._issue(
                    "error",
                    "pid-glob-limit-exceeded",
                    "PID glob exceeded the bounded inspection limit",
                    maximum=10_000,
                )
            )
        if unsafe_matches:
            issues.append(
                self._issue(
                    "error",
                    "pid-glob-match-untrusted",
                    "one or more PID glob matches escaped the trusted roots",
                    count=unsafe_matches,
                )
            )
        if running < minimum:
            issues.append(
                self._issue(
                    "error",
                    "pool-below-minimum",
                    "running pool members are below the declared minimum",
                    running=running,
                    minimum=minimum,
                )
            )
        if stale:
            issues.append(
                self._issue(
                    "warning",
                    "stale-pid-files",
                    "pool contains stale or mismatched PID files",
                    count=stale,
                )
            )
        strengths = {
            member.get("identity_strength")
            for member in members
            if member.get("status") == "running"
        }
        return {
            "kind": "directory",
            "configured_pattern": str(pattern),
            "status": "running" if running >= minimum else "stale",
            "identity_strength": "strong"
            if strengths == {"strong"} and running
            else ("weak" if running else "none"),
            "control_eligible": bool(
                running
                and all(
                    member.get("control_eligible")
                    for member in members
                    if member.get("status") == "running"
                )
            ),
            "running_members": running,
            "stale_members": stale,
            "member_count": len(members),
            "minimum_running": minimum,
            "members": members,
            "secret_findings": dedupe_findings(findings),
            "issues": issues,
        }

    # RuntimeInspector supplies the concrete sanitising issue factory.
    def _issue(self, severity: str, code: str, message: str, **details: Any) -> dict[str, Any]:
        raise NotImplementedError
