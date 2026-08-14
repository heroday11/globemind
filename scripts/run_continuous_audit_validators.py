#!/usr/bin/env python3
"""Execute the checked-in manual-only audit validators in a bounded subprocess set.

The runner derives every command from validator kind and repository-relative
locator. It does not accept arbitrary commands, inherit the caller environment,
retain validator output bodies, contact services, or configure scheduling.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


_AUDIT_SPEC = importlib.util.spec_from_file_location(
    "globemind_continuous_audit",
    Path(__file__).resolve().with_name("continuous_audit.py"),
)
if _AUDIT_SPEC is None or _AUDIT_SPEC.loader is None:
    raise RuntimeError("continuous audit validator is unavailable")
continuous_audit = importlib.util.module_from_spec(_AUDIT_SPEC)
_AUDIT_SPEC.loader.exec_module(continuous_audit)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PLAN = REPOSITORY_ROOT / "config" / "continuous-audit-validators.json"
LOCKED_RUNTIME = Path("/root/data/python-runtimes/globemind-web/1.0.0/bin/python")
FORBIDDEN_RELEASE_ROOT = Path("/root/data/releases/globemind")
REPORT_JSON_NAME = "continuous-audit-validator-run.json"
REPORT_MARKDOWN_NAME = "continuous-audit-validator-run.md"
REPORT_SCHEMA_VERSION = "globemind-continuous-audit-validator-run-v1"
MAX_CAPTURE_BYTES = 1_048_576
TIMEOUTS = {"python_static": 60, "pytest_contract": 180}
_PYTHON_NAME_RE = re.compile(r"^python(?:3(?:\.11)?)?$")


class ValidatorRunError(RuntimeError):
    """The manual validator execution boundary could not be established."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _path_has_symlink_component(path: Path) -> bool:
    absolute = path if path.is_absolute() else Path.cwd() / path
    probe = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        probe /= part
        if probe.is_symlink():
            return True
    return False


def prepare_output_dir(output_dir: Path, *, repository_root: Path) -> Path:
    path = output_dir.expanduser()
    if not path.is_absolute():
        raise ValidatorRunError("output directory must be an absolute path")
    if path == Path(path.anchor):
        raise ValidatorRunError("output directory must not be a filesystem root")
    normalized = Path(os.path.abspath(os.path.normpath(path)))
    if normalized == FORBIDDEN_RELEASE_ROOT or FORBIDDEN_RELEASE_ROOT in normalized.parents:
        raise ValidatorRunError("output directory cannot be inside a release path")
    if _path_has_symlink_component(normalized):
        raise ValidatorRunError("output directory cannot contain symlink components")
    root = repository_root.resolve(strict=True)
    try:
        normalized.resolve(strict=False).relative_to(root)
    except ValueError:
        pass
    else:
        raise ValidatorRunError("output directory must be outside the repository")
    if normalized.exists():
        if not normalized.is_dir() or any(normalized.iterdir()):
            raise ValidatorRunError("output directory must be absent or empty")
    else:
        normalized.mkdir(parents=True, mode=0o750)
    if _path_has_symlink_component(normalized):
        raise ValidatorRunError("output directory cannot contain symlink components")
    os.chmod(normalized, 0o750)
    return normalized


def _read_plan(plan_path: Path, *, repository_root: Path) -> dict[str, Any]:
    continuous_audit.validate_validator_plan(
        plan_path,
        repository_root=repository_root,
    )
    try:
        payload = json.loads(
            plan_path.read_text(encoding="utf-8"),
            object_pairs_hook=continuous_audit._reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValidatorRunError(f"non-finite JSON number: {value}")
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidatorRunError("cannot read validated plan") from exc
    if not isinstance(payload, dict):
        raise ValidatorRunError("validated plan is not an object")
    if payload.get("runner_locator") != "scripts/run_continuous_audit_validators.py":
        raise ValidatorRunError("validated plan does not name this manual runner")
    return payload


def _safe_locator(locator: str, *, repository_root: Path) -> Path:
    pure = PurePosixPath(locator)
    if pure.is_absolute() or ".." in pure.parts or "\\" in locator:
        raise ValidatorRunError("validator locator is not repository-relative")
    path = repository_root / pure
    if _path_has_symlink_component(path):
        raise ValidatorRunError("validator locator cannot contain symlinks")
    resolved = path.resolve(strict=True)
    if not resolved.is_file() or not resolved.is_relative_to(repository_root.resolve()):
        raise ValidatorRunError("validator locator is not a repository file")
    if resolved.stat().st_nlink != 1:
        raise ValidatorRunError("validator locator cannot be hard-linked")
    return resolved


def build_validator_command(
    validator: Mapping[str, Any],
    *,
    repository_root: Path,
    python_runtime: Path = LOCKED_RUNTIME,
) -> tuple[str, ...]:
    locator = str(validator.get("locator") or "")
    target = _safe_locator(locator, repository_root=repository_root)
    kind = validator.get("kind")
    if kind == "python_static":
        return (str(python_runtime), "-B", str(target))
    if kind == "pytest_contract":
        return (str(python_runtime), "-B", "-m", "pytest", str(target), "-q")
    raise ValidatorRunError("validator kind is unsupported")


def clean_validator_environment(*, repository_root: Path) -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(repository_root / "backend"),
    }


def _capture_digest(value: bytes) -> dict[str, Any]:
    bounded = value[:MAX_CAPTURE_BYTES]
    return {
        "byte_count": len(value),
        "captured_byte_count": len(bounded),
        "truncated": len(value) > len(bounded),
        "sha256": hashlib.sha256(bounded).hexdigest(),
        "body_retained": False,
    }


def execute_validator(
    validator: Mapping[str, Any],
    *,
    repository_root: Path,
    python_runtime: Path = LOCKED_RUNTIME,
) -> dict[str, Any]:
    command = build_validator_command(
        validator,
        repository_root=repository_root,
        python_runtime=python_runtime,
    )
    kind = str(validator["kind"])
    started = time.monotonic()
    timed_out = False
    try:
        completed = subprocess.run(
            command,
            cwd=repository_root,
            env=clean_validator_environment(repository_root=repository_root),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=TIMEOUTS[kind],
            check=False,
        )
        return_code: int | None = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        return_code = None
        stdout = bytes(exc.stdout or b"")
        stderr = bytes(exc.stderr or b"")
    if timed_out:
        status = "failed"
    elif return_code == 0:
        status = "passed"
    elif kind == "python_static" and return_code == 1:
        status = "finding"
    else:
        status = "failed"
    return {
        "id": validator["id"],
        "domain": validator["domain"],
        "kind": kind,
        "locator": validator["locator"],
        "status": status,
        "return_code": return_code,
        "timed_out": timed_out,
        "timeout_seconds": TIMEOUTS[kind],
        "duration_ms": round((time.monotonic() - started) * 1000, 2),
        "stdout": _capture_digest(stdout),
        "stderr": _capture_digest(stderr),
    }


def build_report(
    plan: Mapping[str, Any],
    results: Sequence[Mapping[str, Any]],
    *,
    started_at: str,
) -> dict[str, Any]:
    counts = {
        status: sum(result.get("status") == status for result in results)
        for status in ("passed", "finding", "failed")
    }
    status = (
        "failed"
        if counts["failed"]
        else "completed_with_findings"
        if counts["finding"]
        else "passed"
    )
    plan_hash = hashlib.sha256(
        json.dumps(plan, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": status,
        "started_at": started_at,
        "finished_at": _utc_now(),
        "plan_sha256": plan_hash,
        "plan_state": plan["plan_state"],
        "execution_mode": "bounded_offline_plan",
        "summary": {"total": len(results), **counts},
        "results": list(results),
        "safety_boundaries": {
            "commands_from_config": False,
            "commands_derived_from_kind_and_locator": True,
            "caller_environment_inherited": False,
            "validator_output_bodies_retained": False,
            "network_calls_initiated_by_runner": False,
            "database_calls_initiated_by_runner": False,
            "service_control_initiated_by_runner": False,
            "release_access_initiated_by_runner": False,
            "scheduler_declared_in_plan": plan.get("scheduler_state")
            == "configured_daily_repository_workflow",
            "artifact_retention_declared_in_plan": plan.get(
                "artifact_retention_state"
            )
            == "configured_30_day_repository_workflow",
            "scheduler_control_initiated_by_runner": False,
            "artifact_retention_control_initiated_by_runner": False,
            "issue_integration_configured": False,
        },
    }


def _write_new(path: Path, body: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def write_report(output_dir: Path, report: Mapping[str, Any]) -> None:
    _write_new(
        output_dir / REPORT_JSON_NAME,
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    summary = report["summary"]
    lines = [
        "# GlobeMind manual validator run",
        "",
        f"- Status: `{report['status']}`",
        f"- Total: `{summary['total']}`",
        f"- Passed: `{summary['passed']}`",
        f"- Findings: `{summary['finding']}`",
        f"- Failed: `{summary['failed']}`",
        "- Validator stdout/stderr bodies retained: `false`",
        "- Scheduler / retention / issue integration configured: `false`",
        "",
    ]
    _write_new(output_dir / REPORT_MARKDOWN_NAME, "\n".join(lines))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--python-runtime", type=Path, default=LOCKED_RUNTIME)
    return parser.parse_args(argv)


def validate_python_runtime(path: Path) -> Path:
    if not path.is_absolute():
        raise ValidatorRunError("Python runtime must be an absolute path")
    normalized = Path(os.path.abspath(os.path.normpath(path)))
    if normalized == FORBIDDEN_RELEASE_ROOT or FORBIDDEN_RELEASE_ROOT in normalized.parents:
        raise ValidatorRunError("Python runtime cannot use a production release")
    try:
        resolved = normalized.resolve(strict=True)
    except OSError as exc:
        raise ValidatorRunError("Python runtime is unavailable") from exc
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise ValidatorRunError("Python runtime must be an executable file")
    if _PYTHON_NAME_RE.fullmatch(normalized.name) is None:
        raise ValidatorRunError("Python runtime basename is not approved")
    return resolved


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    python_runtime = validate_python_runtime(args.python_runtime)
    plan = _read_plan(DEFAULT_PLAN, repository_root=REPOSITORY_ROOT)
    output_dir = prepare_output_dir(args.output_dir, repository_root=REPOSITORY_ROOT)
    started_at = _utc_now()
    results = [
        execute_validator(
            item,
            repository_root=REPOSITORY_ROOT,
            python_runtime=python_runtime,
        )
        for item in plan["validators"]
    ]
    report = build_report(plan, results, started_at=started_at)
    write_report(output_dir, report)
    print(
        json.dumps(
            {
                "status": report["status"],
                **report["summary"],
                "json": REPORT_JSON_NAME,
                "markdown": REPORT_MARKDOWN_NAME,
            },
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "passed" else 1 if report["status"] == "completed_with_findings" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValidatorRunError, continuous_audit.ContinuousAuditError) as exc:
        print(f"validator run refused: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
