#!/usr/bin/env python3
"""Offline verifier for an externally produced browser-smoke v2 evidence set."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from deploy import browser_smoke

RECEIPT_SCHEMA_VERSION = "globemind-browser-smoke-evidence-receipt-v1"
MAX_REPORT_BYTES = 8 * 1024 * 1024
MAX_SCREENSHOT_BYTES = 20 * 1024 * 1024
FORBIDDEN_RELEASE_ROOT = Path("/root/data/releases/globemind")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class BrowserSmokeEvidenceError(RuntimeError):
    pass


def _read_single_link_file(path: Path, *, maximum_bytes: int) -> bytes:
    if not path.is_absolute():
        raise BrowserSmokeEvidenceError("evidence path must be absolute")
    candidate = Path(os.path.abspath(os.path.normpath(path)))
    if (
        candidate == FORBIDDEN_RELEASE_ROOT
        or FORBIDDEN_RELEASE_ROOT in candidate.parents
    ):
        raise BrowserSmokeEvidenceError("evidence path cannot use a production release")
    current = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise BrowserSmokeEvidenceError("evidence path must not contain symlinks")
    try:
        descriptor = os.open(
            candidate,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise BrowserSmokeEvidenceError("evidence artifact is unavailable") from exc
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise BrowserSmokeEvidenceError(
                    "evidence must be a single-link regular file"
                )
            if before.st_size <= 0 or before.st_size > maximum_bytes:
                raise BrowserSmokeEvidenceError("evidence artifact size is invalid")
            raw = handle.read(maximum_bytes + 1)
            after = os.fstat(descriptor)
        try:
            path_after = candidate.stat()
        except OSError as exc:
            raise BrowserSmokeEvidenceError(
                "evidence artifact changed during read"
            ) from exc
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        path_identity = (
            path_after.st_dev,
            path_after.st_ino,
            path_after.st_size,
            path_after.st_mtime_ns,
        )
        if before_identity != after_identity or after_identity != path_identity:
            raise BrowserSmokeEvidenceError("evidence artifact changed during read")
        if len(raw) != before.st_size or len(raw) > maximum_bytes:
            raise BrowserSmokeEvidenceError("evidence artifact changed during read")
        return raw
    except OSError as exc:
        raise BrowserSmokeEvidenceError("evidence artifact is unavailable") from exc
    finally:
        os.close(descriptor)


def _strict_json(raw: bytes) -> Mapping[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise BrowserSmokeEvidenceError("report contains duplicate JSON keys")
            value[key] = item
        return value

    try:
        payload = json.loads(
            raw,
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda value: (_ for _ in ()).throw(
                BrowserSmokeEvidenceError(
                    f"report contains non-finite JSON number: {value}"
                )
            ),
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BrowserSmokeEvidenceError("report is not strict JSON") from exc
    if not isinstance(payload, Mapping):
        raise BrowserSmokeEvidenceError("report root must be an object")
    return payload


def _parse_time(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise BrowserSmokeEvidenceError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BrowserSmokeEvidenceError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BrowserSmokeEvidenceError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def verify_browser_smoke_evidence(
    report_path: Path,
    *,
    expected_report_sha256: str,
    evaluated_at: datetime,
) -> dict[str, Any]:
    """Recheck report invariants and hash screenshots without opening a browser."""

    if _SHA256_RE.fullmatch(expected_report_sha256) is None:
        raise BrowserSmokeEvidenceError("expected report SHA-256 is invalid")
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise BrowserSmokeEvidenceError("evaluated_at must include a timezone")
    raw = _read_single_link_file(report_path, maximum_bytes=MAX_REPORT_BYTES)
    report_sha = hashlib.sha256(raw).hexdigest()
    if report_sha != expected_report_sha256:
        raise BrowserSmokeEvidenceError("report SHA-256 mismatch")
    report = _strict_json(raw)
    if report.get("schema_version") != browser_smoke.SCHEMA_VERSION:
        raise BrowserSmokeEvidenceError("browser-smoke schema version mismatch")
    if report.get("tool") != {
        "name": browser_smoke.TOOL_VERSION,
        "version": browser_smoke.SCHEMA_VERSION,
    }:
        raise BrowserSmokeEvidenceError("browser-smoke tool identity mismatch")
    if report.get("status") != "passed" or report.get("operational_error") not in {
        None,
        "",
    }:
        raise BrowserSmokeEvidenceError("browser-smoke report did not pass")
    started_at = _parse_time(report.get("started_at"), field="started_at")
    finished_at = _parse_time(report.get("finished_at"), field="finished_at")
    evaluated_utc = evaluated_at.astimezone(timezone.utc)
    if finished_at < started_at or finished_at > evaluated_utc:
        raise BrowserSmokeEvidenceError("browser-smoke time bounds are invalid")
    candidate = report.get("candidate")
    if not isinstance(candidate, Mapping):
        raise BrowserSmokeEvidenceError("candidate identity is missing")
    base_url = candidate.get("base_url")
    if not isinstance(base_url, str):
        raise BrowserSmokeEvidenceError("candidate base URL is missing")
    if browser_smoke.normalize_candidate_base_url(base_url) != base_url:
        raise BrowserSmokeEvidenceError("candidate base URL is not normalized loopback")
    policy = report.get("policy")
    required_policy = {
        "loopback_only": True,
        "cross_origin_requests": "blocked",
        "web_socket_requests": "blocked",
        "api_mode": "in-memory-stubs-only",
        "api_fixture_snapshot_id": browser_smoke.BROWSER_FIXTURE_SNAPSHOT_ID,
        "response_bodies_persisted": False,
        "request_headers_persisted": False,
        "dummy_token_persisted": False,
    }
    if not isinstance(policy, Mapping) or any(
        policy.get(key) != value for key, value in required_policy.items()
    ):
        raise BrowserSmokeEvidenceError("browser-smoke safety policy mismatch")

    checks = report.get("checks")
    if not isinstance(checks, list) or not all(
        isinstance(check, Mapping) for check in checks
    ):
        raise BrowserSmokeEvidenceError("browser-smoke checks are invalid")
    expected_check_ids = {
        f"{viewport.name}-{spec.check_id}"
        for viewport in browser_smoke.VIEWPORTS
        for spec in (
            *browser_smoke.PUBLIC_PAGE_SPECS,
            *browser_smoke.AUTHENTICATED_PAGE_SPECS,
        )
    }
    check_ids = [check.get("check_id") for check in checks]
    if len(check_ids) != len(set(check_ids)) or set(check_ids) != expected_check_ids:
        raise BrowserSmokeEvidenceError("browser-smoke check scope is not exact")
    for check in checks:
        failures = browser_smoke.assess_page_observation(check)
        if failures or check.get("failures") != [] or check.get("outcome") != "passed":
            raise BrowserSmokeEvidenceError("browser-smoke page outcome is inconsistent")
    recomputed_matrix = browser_smoke.assess_viewport_matrix(checks)
    if recomputed_matrix.get("status") != "passed":
        raise BrowserSmokeEvidenceError("browser-smoke viewport matrix did not pass")
    if report.get("viewport_matrix") != recomputed_matrix:
        raise BrowserSmokeEvidenceError("stored viewport matrix does not recompute")
    summary = report.get("summary")
    expected_summary = {
        "total": len(checks),
        "passed": len(checks),
        "failed": 0,
    }
    if summary != expected_summary:
        raise BrowserSmokeEvidenceError("browser-smoke summary is inconsistent")

    evidence_root = report_path.parent.resolve(strict=True)
    screenshots: list[dict[str, Any]] = []
    screenshot_locators: list[str] = []
    for check in checks:
        locator = check.get("screenshot")
        if not isinstance(locator, str):
            raise BrowserSmokeEvidenceError("screenshot locator is missing")
        posix = PurePosixPath(locator)
        if (
            posix.is_absolute()
            or len(posix.parts) != 2
            or posix.parts[0] != "screenshots"
            or posix.suffix != ".png"
            or any(part in {"", ".", ".."} for part in posix.parts)
            or "\\" in locator
        ):
            raise BrowserSmokeEvidenceError("screenshot locator is unsafe")
        screenshot_locators.append(locator)
        screenshot_path = evidence_root.joinpath(*posix.parts)
        try:
            screenshot_path.resolve(strict=True).relative_to(evidence_root)
        except (OSError, ValueError) as exc:
            raise BrowserSmokeEvidenceError("screenshot escapes evidence root") from exc
        screenshot_raw = _read_single_link_file(
            screenshot_path,
            maximum_bytes=MAX_SCREENSHOT_BYTES,
        )
        if not screenshot_raw.startswith(b"\x89PNG\r\n\x1a\n"):
            raise BrowserSmokeEvidenceError("screenshot is not a PNG artifact")
        screenshots.append(
            {
                "check_id": check["check_id"],
                "artifact_locator": locator,
                "artifact_sha256": hashlib.sha256(screenshot_raw).hexdigest(),
                "artifact_bytes": len(screenshot_raw),
            }
        )
    if len(screenshot_locators) != len(set(screenshot_locators)):
        raise BrowserSmokeEvidenceError("screenshot locators must be unique")

    semantic_pages = browser_smoke.SEMANTIC_ACCESSIBILITY_EXPECTATIONS
    semantic_selectors = sum(len(value) for value in semantic_pages.values())
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "evaluated_at": evaluated_utc.isoformat().replace("+00:00", "Z"),
        "candidate_base_url": base_url,
        "report_sha256": report_sha,
        "report_bytes": len(raw),
        "browser_evidence_verification": "passed",
        "candidate_acceptance": "not_established_in_memory_stubs_only",
        "check_scope": "exact_registered_pages_and_viewports",
        "page_count": len(browser_smoke.PUBLIC_PAGE_SPECS)
        + len(browser_smoke.AUTHENTICATED_PAGE_SPECS),
        "viewport_count": len(browser_smoke.VIEWPORTS),
        "check_count": len(checks),
        "business_semantic_page_count": len(semantic_pages),
        "semantic_selector_count": semantic_selectors,
        "semantic_probe_observation_count": semantic_selectors
        * len(browser_smoke.VIEWPORTS),
        "screenshot_artifacts": screenshots,
        "screenshot_bodies_retained_in_receipt": False,
        "response_bodies_retained_in_receipt": False,
        "candidate_api_consistency": "not_established_in_memory_stubs_only",
        "physical_device_behavior": "not_established",
        "release_decision": "not_computable",
    }


__all__ = (
    "BrowserSmokeEvidenceError",
    "RECEIPT_SCHEMA_VERSION",
    "verify_browser_smoke_evidence",
)
