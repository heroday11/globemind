#!/usr/bin/env python3
"""Browser-level smoke gate for an already-running loopback release candidate.

This tool is deliberately independent from the application runtime. It only
uses the Python standard library until Playwright is loaded inside ``run()``.
It never imports backend code, opens a database connection, or manages a
process. All API calls made by the browser are answered by bounded in-memory
stubs; candidate HTML, JavaScript, CSS, fonts, and local images are still loaded
from the candidate origin.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import sys
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = 2
TOOL_VERSION = "globemind-browser-smoke-v2"
DEFAULT_TIMEOUT_MS = 20_000
DEFAULT_SETTLE_MS = 900
MAX_ERROR_TEXT = 600
OVERFLOW_TOLERANCE_PX = 2

# This value is intentionally public, fixed, and accepted only by browser-side
# API stubs. It must never reach a candidate API or be persisted in evidence.
DUMMY_AUTH_TOKEN = "globemind-browser-smoke-public-dummy-v1"
BROWSER_FIXTURE_SNAPSHOT_ID = "browser-smoke-fixture-20260810-v2"
BROWSER_FIXTURE_GENERATED_AT = "2026-08-10T00:00:00Z"


@dataclass(frozen=True)
class ViewportSpec:
    name: str
    width: int
    height: int
    is_mobile: bool


@dataclass(frozen=True)
class PageSpec:
    check_id: str
    path: str
    selector: str
    authenticated: bool = False
    expected_path: str | None = None
    expected_redirect: str | None = None
    semantic_probe_selectors: tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticAccessibilityExpectation:
    role: str
    aria_live: str | None = None
    aria_atomic: str | None = None
    aria_label_required: bool = False


VIEWPORTS = (
    ViewportSpec("desktop", 1440, 900, False),
    ViewportSpec("mobile", 390, 844, True),
)

PUBLIC_PAGE_SPECS = (
    PageSpec("root", "/", ".home", expected_path="/"),
    PageSpec("login", "/login", ".login-page", expected_path="/login"),
    PageSpec(
        "country-profiles-unavailable",
        "/country-profiles",
        ".country-catalog-page .state-message--error",
        expected_path="/country-profiles",
        semantic_probe_selectors=(".state-message--error",),
    ),
    PageSpec(
        "protected-redirect",
        "/data-service/pipeline-monitor",
        ".login-page",
        expected_path="/login",
        expected_redirect="/data-service/pipeline-monitor",
    ),
)

AUTHENTICATED_PAGE_SPECS = (
    PageSpec(
        "data-search",
        "/data-service/data-search",
        ".search-page",
        authenticated=True,
    ),
    PageSpec(
        "story-graph",
        "/data-service/story-graph",
        ".intel-page",
        authenticated=True,
    ),
    PageSpec(
        "ground-news",
        "/data-service/ground-news",
        ".ground-home",
        authenticated=True,
        semantic_probe_selectors=(".home-hero__metrics", ".home-freshness"),
    ),
    PageSpec("assistant", "/data-assistant", ".yisight-shell", authenticated=True),
    PageSpec(
        "pipeline-monitor",
        "/data-service/pipeline-monitor",
        ".ops-monitor",
        authenticated=True,
        semantic_probe_selectors=(".trend-empty", ".status-ribbon"),
    ),
    PageSpec(
        "sentiment",
        "/sentiment-analysis",
        ".app-root",
        authenticated=True,
    ),
    PageSpec(
        "research-workspace",
        "/research-workspace",
        ".research-workspace",
        authenticated=True,
    ),
    PageSpec(
        "model-assurance",
        "/model-assurance",
        ".assurance-page",
        authenticated=True,
        semantic_probe_selectors=(
            ".status-panel .status-grid",
            ".status-panel .empty-state",
        ),
    ),
    PageSpec(
        "entity-governance",
        "/entity-governance",
        ".governance-page",
        authenticated=True,
        semantic_probe_selectors=(".status-grid", ".state-message--error"),
    ),
)

SEMANTIC_ACCESSIBILITY_EXPECTATIONS: dict[
    str, dict[str, SemanticAccessibilityExpectation]
] = {
    "country-profiles-unavailable": {
        ".state-message--error": SemanticAccessibilityExpectation(
            role="alert",
            aria_live="assertive",
            aria_atomic="true",
        ),
    },
    "ground-news": {
        ".home-hero__metrics": SemanticAccessibilityExpectation(
            role="group",
            aria_label_required=True,
        ),
        ".home-freshness": SemanticAccessibilityExpectation(
            role="status",
            aria_live="polite",
        ),
    },
    "pipeline-monitor": {
        ".trend-empty": SemanticAccessibilityExpectation(
            role="status",
            aria_live="polite",
        ),
        ".status-ribbon": SemanticAccessibilityExpectation(
            role="group",
            aria_label_required=True,
        ),
    },
    "model-assurance": {
        ".status-panel .status-grid": SemanticAccessibilityExpectation(
            role="group",
            aria_label_required=True,
        ),
        ".status-panel .empty-state": SemanticAccessibilityExpectation(
            role="status",
            aria_live="polite",
        ),
    },
    "entity-governance": {
        ".status-grid": SemanticAccessibilityExpectation(
            role="group",
            aria_label_required=True,
        ),
        ".state-message--error": SemanticAccessibilityExpectation(
            role="alert",
            aria_live="assertive",
            aria_atomic="true",
        ),
    },
}

ALLOWED_DOCUMENT_PATHS = frozenset(
    spec.path for spec in (*PUBLIC_PAGE_SPECS, *AUTHENTICATED_PAGE_SPECS)
)
ALLOWED_STATIC_PREFIXES = ("/assets/", "/imgs/")
ALLOWED_STATIC_PATHS = frozenset({"/favicon.ico", "/index.html"})

PAGE_GROUPS = (
    (False, PUBLIC_PAGE_SPECS),
    (True, AUTHENTICATED_PAGE_SPECS),
)


class BrowserSmokeError(RuntimeError):
    """The smoke tool could not execute safely."""


class UnknownApiStub(BrowserSmokeError):
    """A page requested an API route not declared by this candidate gate."""


_SECRET_KEY_RE = re.compile(
    r"(?i)([\"']?(?:access[_-]?token|authorization|password|passwd|secret|"
    r"api[_-]?key)[\"']?\s*[:=]\s*)([\"']?)[^\s,;\"'}]+"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_NETWORK_URL_RE = re.compile(r"(?:https?|wss?)://[^\s<>\"']+", re.IGNORECASE)


def normalize_candidate_base_url(value: str) -> str:
    """Return a canonical literal-loopback origin or fail closed."""

    candidate = str(value).strip()
    if not candidate or "?" in candidate or "#" in candidate:
        raise ValueError("base URL must not contain a query or fragment")
    parsed = urllib.parse.urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base URL must use http or https and include a host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("base URL must not contain credentials")
    if parsed.path not in {"", "/"}:
        raise ValueError("base URL must be an origin without a path prefix")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("base URL port is invalid") from exc
    if port is None:
        raise ValueError("base URL must include an explicit candidate port")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError as exc:
        raise ValueError("base URL host must be a literal loopback IP address") from exc
    if not address.is_loopback:
        raise ValueError("base URL host must be a loopback IP address")
    host = f"[{address.compressed}]" if address.version == 6 else address.compressed
    return f"{parsed.scheme}://{host}:{port}"


def validate_chromium_executable(path: Path | None) -> Path | None:
    if path is None:
        return None
    candidate = path.expanduser()
    if not candidate.is_absolute():
        raise ValueError("Chromium executable path must be absolute")
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise ValueError("Chromium executable must be an executable file")
    return candidate.resolve(strict=True)


def _reject_symlink_components(path: Path) -> None:
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError("evidence path must not contain symbolic links")


def prepare_evidence_dir(output_dir: Path) -> Path:
    """Create a new evidence tree, or consume an existing empty directory."""

    path = output_dir.expanduser()
    if not path.is_absolute():
        raise ValueError("evidence directory must be an absolute path")
    if path == Path(path.anchor):
        raise ValueError("evidence directory must not be a filesystem root")
    _reject_symlink_components(path)
    if path.exists():
        if not path.is_dir():
            raise ValueError("evidence path exists and is not a directory")
        if any(path.iterdir()):
            raise ValueError("evidence directory must be empty")
    else:
        path.mkdir(parents=True, mode=0o750)
    _reject_symlink_components(path)
    os.chmod(path, 0o750)
    screenshots = path / "screenshots"
    screenshots.mkdir(mode=0o750)
    return path


def _url_without_sensitive_parts(raw: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(raw)
    except ValueError:
        return "<redacted-url>"
    hostname = parsed.hostname
    if not parsed.scheme or not parsed.netloc or not hostname:
        return "<redacted-url>"
    try:
        port = parsed.port
    except ValueError:
        return "<redacted-url>"
    host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = f"{host}:{port}" if port is not None else host
    return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _sanitize_network_url(match: re.Match[str]) -> str:
    return _url_without_sensitive_parts(match.group(0))


def redact_text(value: Any, *, limit: int = MAX_ERROR_TEXT) -> str:
    """Remove credentials, query strings, control characters, and long output."""

    text = str(value or "").replace(DUMMY_AUTH_TOKEN, "<redacted>")
    text = _BEARER_RE.sub("Bearer <redacted>", text)
    text = _SECRET_KEY_RE.sub(r"\1<redacted>", text)
    text = _NETWORK_URL_RE.sub(_sanitize_network_url, text)
    text = " ".join(text.replace("\x00", "").split())
    return text[:limit]


def redact_value(value: Any) -> Any:
    """Recursively sanitize the small, tool-controlled evidence structure."""

    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for raw_key, child in value.items():
            key = redact_text(raw_key, limit=120)
            normalized_key = key.lower().replace("-", "_")
            if normalized_key in {
                "access_token",
                "authorization",
                "password",
                "passwd",
                "secret",
                "api_key",
            }:
                sanitized[key] = "<redacted>"
            else:
                sanitized[key] = redact_value(child)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [redact_value(child) for child in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def _safe_url_without_query(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return "<redacted-url>"
    if not parsed.scheme or not parsed.netloc:
        return redact_text(parsed.path or "<redacted-url>")
    return redact_text(_url_without_sensitive_parts(value))


def _decoded_request_path(url: str) -> str:
    """Decode nested URL escapes so encoded API paths cannot bypass stubbing."""

    path = urllib.parse.urlsplit(url).path
    for _ in range(4):
        decoded = urllib.parse.unquote(path, errors="replace")
        if decoded == path:
            break
        path = decoded
    return path


def _is_api_path(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def _is_allowed_candidate_request(path: str, method: str, resource_type: str) -> bool:
    if method.upper() not in {"GET", "HEAD"}:
        return False
    if not path.startswith("/") or "\\" in path:
        return False
    if any(ord(character) < 32 for character in path):
        return False
    if any(part in {".", ".."} for part in path.split("/")):
        return False
    if resource_type == "document":
        return path in ALLOWED_DOCUMENT_PATHS
    return path in ALLOWED_STATIC_PATHS or path.startswith(ALLOWED_STATIC_PREFIXES)


def _redirect_target_status(values: Sequence[str], expected: str | None) -> str:
    if expected is None:
        return "not-applicable"
    if list(values) == [expected]:
        return "matched"
    if not values:
        return "missing"
    return "unexpected"


def _same_origin(url: str, base_url: str) -> bool:
    try:
        requested = urllib.parse.urlsplit(url)
        base = urllib.parse.urlsplit(base_url)
        return (
            requested.scheme,
            requested.hostname,
            requested.port,
        ) == (base.scheme, base.hostname, base.port) and not (
            requested.username or requested.password
        )
    except ValueError:
        return False


def api_stub_payload(path: str, method: str) -> Any:
    """Return deterministic, non-sensitive data for initial page requests."""

    route = urllib.parse.urlsplit(path).path
    verb = method.upper()
    if route == "/api/ops/heartbeat" and verb in {"GET", "POST"}:
        return {"ok": True}
    if route == "/api/auth/me" and verb == "GET":
        return {"id": 900001, "username": "browser-smoke", "roles": ["candidate"]}
    if route == "/api/authoritative-data/country-profiles/catalog" and verb == "GET":
        # This intentionally exercises the public page's fail-closed contract
        # error state. Successful schema rendering remains covered by the Node
        # contract suite and must not be inferred from this browser fixture.
        return {
            "schema_version": "browser-smoke-invalid-country-profile-fixture",
            "fixture_snapshot_id": BROWSER_FIXTURE_SNAPSHOT_ID,
        }
    if route == "/api/research/projects" and verb == "GET":
        return {"projects": []}
    if route == "/api/model-assurance/status" and verb == "GET":
        return {
            "schema_version": "globemind.model-assurance.v1",
            "generated_at": BROWSER_FIXTURE_GENERATED_AT,
            "available": False,
            "operational_state": "not_observed",
            "release_status": "blocked",
            "gold_standard_state": "not_observed",
            "evaluation_count": 0,
            "eligible_count": 0,
            "latest": None,
            "reason_codes": [
                "NO_EVALUATION_MANIFESTS",
                "GOLD_STANDARD_NOT_OBSERVED",
                "RELEASE_BLOCKED",
            ],
        }
    if route == "/api/model-assurance/evaluations" and verb == "GET":
        return []
    if route == "/api/entity-governance/status" and verb == "GET":
        return {
            "schema_version": "entity-governance-status-v2",
            "storage_status": "unavailable",
            "reason": "BROWSER_SMOKE_READ_ONLY_STUB",
            "root_initialized": False,
            "event_count": None,
            "latest_event_id": None,
            "integrity_status": "unavailable",
            "mutation_status": "blocked",
            "mutation_blocker": "BROWSER_SMOKE_READ_ONLY_STUB",
            "chain": "sha256-and-hmac-sha256",
            "append_semantics": "no-replace-local-filesystem",
            "hmac_key_id": "unavailable",
            "hmac_key_rotation": "offline-controlled-migration-not-implemented",
            "worm_status": "unavailable",
            "digital_signature_status": "unavailable",
            "institutional_directory_integration": "unavailable",
            "accuracy_claim": "not_measured",
            "seed_review_default": "review_required",
            "evidence_policy": "verified-evidence-snapshot-required-for-mutations",
            "review_expiry_policy": "not_configured",
        }
    if route == "/api/status" and verb == "GET":
        return {
            "schema_version": "globemind.public-status.v1",
            "generated_at": BROWSER_FIXTURE_GENERATED_AT,
            "status": "historical",
            "research_mode": "historical",
            "ready": True,
            "checks": {
                "search": {
                    "feature_id": "search",
                    "status": "stale",
                    "metrics": {
                        "freshness_status": "stale",
                        "latest_news_at": "2026-08-08T08:00:00+00:00",
                        "freshness_lag_hours": 25,
                        "freshness_sla_hours": 24,
                    },
                },
                "ground-news": {
                    "feature_id": "ground-news",
                    "status": "stale",
                    "metrics": {
                        "freshness_status": "stale",
                        "latest_story_source_at": "2026-08-08T08:00:00+00:00",
                        "freshness_lag_hours": 25,
                        "freshness_sla_hours": 24,
                    },
                },
                "opinion-analysis": {
                    "feature_id": "opinion-analysis",
                    "status": "stale",
                    "metrics": {
                        "freshness_status": "stale",
                        "latest_score_date": "2026-08-08",
                        "freshness_lag_hours": 33,
                        "freshness_sla_hours": 24,
                    },
                },
            },
            "objectives": {
                "freshness": [],
                "workflows": [
                    {
                        "id": identifier,
                        "label": label,
                        "indicator": indicator,
                        "measurement_status": "not_observed",
                        "objective": None,
                        "observed": None,
                        "compliance": "not_computable",
                        "approval_state": "not_approved",
                        "reason": "候选浏览器 stub 不提供服务级观测样本。",
                        "source": "browser-smoke-stub",
                    }
                    for identifier, label, indicator in (
                        (
                            "search-response",
                            "检索响应",
                            "端到端检索成功率与延迟",
                        ),
                        (
                            "export-delivery",
                            "导出交付",
                            "导出成功率与完成时间",
                        ),
                        (
                            "report-generation",
                            "报告生成",
                            "报告成功率与完成时间",
                        ),
                    )
                ],
            },
            "incident_history": {
                "status": "not_available",
                "reason": "浏览器候选 stub 不提供事件历史。",
            },
        }
    if route == "/api/dashboard/news" and verb == "GET":
        return {"data": [], "total": 0, "page": 1, "page_size": 10}
    if route == "/api/dashboard/search/options" and verb == "GET":
        return {"language_options": [], "data_sources": [], "sites": []}
    if route == "/api/dashboard/stats" and verb == "GET":
        return {"total_news": 0, "language_stats": []}
    if route == "/api/user/favorites" and verb == "GET":
        return {"items": [], "news_ids": []}
    if route == "/api/user/search-history" and verb == "GET":
        return {"data": []}
    if route == "/api/story-graph/l3-macro/list" and verb == "GET":
        return {"macros": [], "total": 0, "page": 1, "page_size": 100}
    if route == "/api/story-graph/l2-chain/list" and verb == "GET":
        return {"chains": [], "total": 0, "page": 1, "page_size": 100}
    if route == "/api/story-graph/ground-news/home" and verb == "GET":
        return {
            "lead_story": None,
            "metrics": {
                "total_stories": 0,
                "total_articles": 0,
                "source_profile_coverage": {
                    "total_profiles": 0,
                    "known_bias_profiles": 0,
                },
            },
            "edition": {},
            "sections": [],
            "l2_watchlist": [],
        }
    if route == "/api/ops/pipeline-monitor" and verb == "GET":
        return _pipeline_snapshot_stub()
    if route == "/api/ops/pipeline-monitor/fast" and verb == "GET":
        return {
            "generated_at": "2026-07-11T00:00:00Z",
            "pipeline_updates": [],
            "overview": {},
            "series": {"samples": []},
        }
    if route == "/api/assistant/sessions" and verb == "GET":
        return [
            {
                "id": 900001,
                "title": "候选验收会话",
                "updated_at": "2026-07-11T00:00:00Z",
            }
        ]
    if re.fullmatch(r"/api/assistant/sessions/\d+/messages", route) and verb == "GET":
        return []
    if route == "/api/assistant/schedules" and verb == "GET":
        return {"ok": True, "data": []}
    if route == "/api/workspaces" and verb == "GET":
        return {"ok": True, "data": []}
    if route == "/api/opinion/overview" and verb == "GET":
        return {
            "latest_date": "2026-07-11",
            "summary": {},
            "indices": {},
            "briefs": [],
            "families": [],
        }
    if route == "/api/opinion/china-trend" and verb == "GET":
        return {"dates": [], "values": [], "meta": {"source": "browser-smoke"}}
    if route == "/api/opinion/quality" and verb == "GET":
        return {
            "ok": True,
            "status": "healthy",
            "method_version": "browser-smoke-v1",
            "coverage_by_date": [],
            "pending_feedback": 0,
        }
    raise UnknownApiStub(f"no browser smoke stub for {verb} {route}")


def _pipeline_snapshot_stub() -> dict[str, Any]:
    return {
        "generated_at": "2026-07-11T00:00:00Z",
        "overview": {
            "news_total": 0,
            "status_counts": {},
            "online_active": 0,
            "server_pressure_pct": 0,
            "memory_used_pct": 0,
        },
        "system": {
            "host": "candidate-stub",
            "cpu": {"load1": 0, "count": 1, "pressure_pct": 0},
            "memory": {"used_pct": 0},
            "disk": {"used_pct": 0, "free_bytes": 0},
            "processes": [],
        },
        "db": {},
        "online": {"active": 0, "ttl_sec": 90},
        "pipelines": [],
        "series": {"samples": []},
    }


def assess_page_observation(observation: Mapping[str, Any]) -> list[str]:
    """Produce stable failure codes from browser observations."""

    failures: list[str] = []
    if observation.get("navigation_error"):
        failures.append("navigation-error")
    if observation.get("http_redirects"):
        failures.append("http-redirect")
    if observation.get("external_requests"):
        failures.append("cross-origin-request")
    if observation.get("unexpected_api_requests"):
        failures.append("unexpected-api-request")
    if observation.get("blocked_same_origin_requests"):
        failures.append("unsafe-same-origin-request")
    if observation.get("blocked_web_socket_requests"):
        failures.append("websocket-request")
    if observation.get("resource_errors"):
        failures.append("critical-resource-error")
    expected_path = observation.get("expected_path")
    if expected_path and observation.get("final_path") != expected_path:
        failures.append("unexpected-final-route")
    expected_redirect = observation.get("expected_redirect")
    if expected_redirect and observation.get("redirect_target_status") != "matched":
        failures.append("invalid-auth-redirect")
    if observation.get("authenticated") and observation.get("final_path") == "/login":
        failures.append("authenticated-route-returned-login")
    if not observation.get("selector_visible"):
        failures.append("root-selector-not-visible")
    semantic_probes = observation.get("semantic_probes")
    if isinstance(semantic_probes, Mapping) and any(
        not isinstance(value, Mapping) or value.get("state") != "observed"
        for value in semantic_probes.values()
    ):
        failures.append("semantic-probe-missing")
    page_id = observation.get("page")
    if (
        isinstance(page_id, str)
        and isinstance(semantic_probes, Mapping)
        and _semantic_accessibility_mismatches(page_id, semantic_probes)
    ):
        failures.append("semantic-accessibility-mismatch")
    dimensions = observation.get("dimensions") or {}
    if dimensions.get("visible_text_chars", 0) < 3:
        failures.append("blank-page")
    if dimensions.get("horizontal_overflow_px", 0) > OVERFLOW_TOLERANCE_PX:
        failures.append("horizontal-overflow")
    if dimensions.get("obvious_overlaps"):
        failures.append("obvious-element-overlap")
    if observation.get("console_errors"):
        failures.append("console-error")
    if observation.get("page_errors"):
        failures.append("page-error")
    return failures


def assess_viewport_matrix(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Require every registered page under both viewports and one fixture identity."""

    expected_pages = tuple(
        spec.check_id for spec in (*PUBLIC_PAGE_SPECS, *AUTHENTICATED_PAGE_SPECS)
    )
    observed: dict[str, dict[str, str]] = {page: {} for page in expected_pages}
    semantic_expected = {
        spec.check_id: spec.semantic_probe_selectors
        for spec in (*PUBLIC_PAGE_SPECS, *AUTHENTICATED_PAGE_SPECS)
        if spec.semantic_probe_selectors
    }
    semantic_observed: dict[str, dict[str, Mapping[str, Any]]] = {
        page: {} for page in semantic_expected
    }
    for result in results:
        page = result.get("page")
        viewport = result.get("viewport")
        viewport_name = viewport.get("name") if isinstance(viewport, Mapping) else None
        snapshot_id = result.get("fixture_snapshot_id")
        if (
            page in observed
            and viewport_name in {"desktop", "mobile"}
            and isinstance(snapshot_id, str)
        ):
            observed[page][viewport_name] = snapshot_id
            raw_probes = result.get("semantic_probes")
            if page in semantic_observed and isinstance(raw_probes, Mapping):
                semantic_observed[page][viewport_name] = raw_probes
    missing = [
        f"{viewport}:{page}"
        for page in expected_pages
        for viewport in ("desktop", "mobile")
        if viewport not in observed[page]
    ]
    mismatched = [
        page
        for page in expected_pages
        if set(observed[page].values()) not in (
            set(),
            {BROWSER_FIXTURE_SNAPSHOT_ID},
        )
    ]
    semantic_missing: list[str] = []
    semantic_mismatches: list[str] = []
    semantic_accessibility_mismatches: list[str] = []
    for page, selectors in semantic_expected.items():
        for selector in selectors:
            values: dict[str, str] = {}
            for viewport in ("desktop", "mobile"):
                probe = semantic_observed[page].get(viewport, {}).get(selector)
                if not isinstance(probe, Mapping) or probe.get("state") != "observed":
                    semantic_missing.append(f"{viewport}:{page}:{selector}")
                    continue
                digest = probe.get("normalized_text_sha256")
                if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                    semantic_missing.append(f"{viewport}:{page}:{selector}")
                    continue
                values[viewport] = digest
            if len(values) == 2 and values["desktop"] != values["mobile"]:
                semantic_mismatches.append(f"{page}:{selector}")
        for viewport in ("desktop", "mobile"):
            probes = semantic_observed[page].get(viewport, {})
            semantic_accessibility_mismatches.extend(
                f"{viewport}:{page}:{value}"
                for value in _semantic_accessibility_mismatches(page, probes)
            )
    return {
        "status": (
            "passed"
            if not missing
            and not mismatched
            and not semantic_missing
            and not semantic_mismatches
            and not semantic_accessibility_mismatches
            else "failed"
        ),
        "expected_pages": len(expected_pages),
        "expected_viewport_checks": len(expected_pages) * 2,
        "fixture_snapshot_id": BROWSER_FIXTURE_SNAPSHOT_ID,
        "missing": missing,
        "snapshot_mismatch_pages": mismatched,
        "semantic_probe_missing": semantic_missing,
        "semantic_probe_mismatches": semantic_mismatches,
        "semantic_accessibility_mismatches": semantic_accessibility_mismatches,
        "semantic_probe_comparison": (
            "normalized-visible-text-sha256-and-declared-accessibility-semantics"
        ),
        "claim_scope": (
            "same deterministic API fixture identity, declared semantic-probe text, "
            "and explicit role/live-region/label presence; "
            "all displayed values, candidate APIs, and physical-device behavior are not established"
        ),
    }


def _semantic_accessibility_mismatches(
    page: str,
    probes: Mapping[str, Any],
) -> list[str]:
    mismatches: list[str] = []
    for selector, expectation in SEMANTIC_ACCESSIBILITY_EXPECTATIONS.get(page, {}).items():
        probe = probes.get(selector)
        if not isinstance(probe, Mapping) or probe.get("state") != "observed":
            continue
        if probe.get("role") != expectation.role:
            mismatches.append(f"{selector}:role")
        if expectation.aria_live is not None and probe.get("aria_live") != expectation.aria_live:
            mismatches.append(f"{selector}:aria-live")
        if expectation.aria_atomic is not None and probe.get("aria_atomic") != expectation.aria_atomic:
            mismatches.append(f"{selector}:aria-atomic")
        if expectation.aria_label_required and probe.get("aria_label_present") is not True:
            mismatches.append(f"{selector}:aria-label")
    return mismatches


def fingerprint_semantic_probes(values: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Fingerprint text and retain only declared accessibility metadata."""

    output: dict[str, dict[str, Any]] = {}
    for selector, raw_value in values.items():
        if isinstance(raw_value, Mapping):
            raw_text = raw_value.get("text")
            role = raw_value.get("role")
            aria_live = raw_value.get("aria_live")
            aria_atomic = raw_value.get("aria_atomic")
            aria_label_present = raw_value.get("aria_label_present") is True
        else:
            raw_text = raw_value
            role = None
            aria_live = None
            aria_atomic = None
            aria_label_present = False
        normalized = " ".join(str(raw_text or "").split())
        output[str(selector)] = (
            {
                "state": "observed",
                "normalized_text_chars": len(normalized),
                "normalized_text_sha256": hashlib.sha256(
                    normalized.encode("utf-8")
                ).hexdigest(),
                "role": role if isinstance(role, str) and role else None,
                "aria_live": (
                    aria_live if isinstance(aria_live, str) and aria_live else None
                ),
                "aria_atomic": (
                    aria_atomic if isinstance(aria_atomic, str) and aria_atomic else None
                ),
                "aria_label_present": aria_label_present,
            }
            if normalized
            else {
                "state": "missing",
                "normalized_text_chars": 0,
                "normalized_text_sha256": None,
                "role": None,
                "aria_live": None,
                "aria_atomic": None,
                "aria_label_present": False,
            }
        )
    return output


_DOM_SUMMARY_SCRIPT = r"""
(selector) => {
  const root = document.querySelector(selector)
  const viewportWidth = document.documentElement.clientWidth || window.innerWidth
  const viewportHeight = document.documentElement.clientHeight || window.innerHeight
  const docWidth = Math.max(
    document.documentElement.scrollWidth,
    document.body?.scrollWidth || 0,
    root?.scrollWidth || 0,
  )
  const visible = (element) => {
    if (!element) return false
    if (typeof element.checkVisibility === 'function') {
      return element.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })
    }
    const style = getComputedStyle(element)
    const rect = element.getBoundingClientRect()
    return style.display !== 'none' && style.visibility !== 'hidden'
      && Number(style.opacity || 1) > 0 && rect.width > 0 && rect.height > 0
  }
  let visibleTextChars = 0
  if (root) {
    const elements = [root, ...root.querySelectorAll('*')]
    for (const element of elements.slice(0, 5000)) {
      if (!visible(element)) continue
      for (const node of element.childNodes) {
        if (node.nodeType === Node.TEXT_NODE) {
          visibleTextChars += String(node.textContent || '').replace(/\s+/g, '').length
        }
      }
    }
  }
  const label = (element) => {
    const classes = [...element.classList].slice(0, 2)
      .map((value) => String(value).replace(/[^a-zA-Z0-9_-]/g, ''))
      .filter(Boolean)
    return `${element.tagName.toLowerCase()}${classes.map((value) => `.${value}`).join('')}`
  }
  const layoutChildren = root
    ? [...root.children].filter((element) => {
        if (!visible(element)) return false
        const position = getComputedStyle(element).position
        return !['absolute', 'fixed', 'sticky'].includes(position)
      })
    : []
  const overlaps = []
  for (let leftIndex = 0; leftIndex < layoutChildren.length; leftIndex += 1) {
    const left = layoutChildren[leftIndex]
    const a = left.getBoundingClientRect()
    for (let rightIndex = leftIndex + 1; rightIndex < layoutChildren.length; rightIndex += 1) {
      const right = layoutChildren[rightIndex]
      const b = right.getBoundingClientRect()
      const width = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left))
      const height = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top))
      const intersection = width * height
      const smaller = Math.min(a.width * a.height, b.width * b.height)
      if (intersection >= 64 && smaller > 0 && intersection / smaller >= 0.1) {
        overlaps.push({ left: label(left), right: label(right), pixels: Math.round(intersection) })
      }
      if (overlaps.length >= 10) break
    }
    if (overlaps.length >= 10) break
  }
  const rootRect = root?.getBoundingClientRect()
  const overflowElements = [...document.querySelectorAll('body *')]
    .slice(0, 5000)
    .filter((element) => {
      if (!visible(element)) return false
      const rect = element.getBoundingClientRect()
      return rect.left < -2 || rect.right > viewportWidth + 2
    })
    .map((element) => {
      const rect = element.getBoundingClientRect()
      return {
        element: label(element),
        left: Math.round(rect.left),
        right: Math.round(rect.right),
        width: Math.round(rect.width),
      }
    })
    .sort((left, right) => Math.max(right.right - viewportWidth, -right.left)
      - Math.max(left.right - viewportWidth, -left.left))
    .slice(0, 10)
  return {
    viewport: { width: viewportWidth, height: viewportHeight },
    document: {
      scroll_width: docWidth,
      scroll_height: Math.max(
        document.documentElement.scrollHeight,
        document.body?.scrollHeight || 0,
      ),
    },
    root: rootRect ? {
      x: Math.round(rootRect.x),
      y: Math.round(rootRect.y),
      width: Math.round(rootRect.width),
      height: Math.round(rootRect.height),
    } : null,
    visible_text_chars: visibleTextChars,
    horizontal_overflow_px: Math.max(0, Math.round(docWidth - viewportWidth)),
    horizontal_overflow_elements: overflowElements,
    obvious_overlaps: overlaps,
  }
}
"""

_SEMANTIC_PROBE_SCRIPT = r"""
(selectors) => Object.fromEntries(selectors.map((selector) => {
  const element = document.querySelector(selector)
  if (!element) return [selector, { text: '' }]
  const style = getComputedStyle(element)
  const rect = element.getBoundingClientRect()
  const visible = style.display !== 'none' && style.visibility !== 'hidden'
    && Number(style.opacity || 1) > 0 && rect.width > 0 && rect.height > 0
  return [selector, {
    text: visible ? String(element.innerText || element.textContent || '') : '',
    role: String(element.getAttribute('role') || ''),
    aria_live: String(element.getAttribute('aria-live') || ''),
    aria_atomic: String(element.getAttribute('aria-atomic') || ''),
    aria_label_present: Boolean(String(element.getAttribute('aria-label') || '').trim()),
  }]
}))
"""


class BrowserSmoke:
    def __init__(
        self,
        *,
        base_url: str,
        output_dir: Path,
        chromium_executable: Path | None = None,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        settle_ms: int = DEFAULT_SETTLE_MS,
    ) -> None:
        if timeout_ms < 1_000 or timeout_ms > 120_000:
            raise ValueError("timeout must be between 1000 and 120000 milliseconds")
        if settle_ms < 0 or settle_ms > 10_000:
            raise ValueError("settle time must be between 0 and 10000 milliseconds")
        self.base_url = normalize_candidate_base_url(base_url)
        self.output_dir = output_dir.expanduser()
        self.chromium_executable = validate_chromium_executable(chromium_executable)
        self.timeout_ms = timeout_ms
        self.settle_ms = settle_ms
        self.results: list[dict[str, Any]] = []
        self.operational_error = ""
        self.browser_version = ""

    def run(self) -> dict[str, Any]:
        started_at = _utc_now()
        started = time.monotonic()
        evidence_dir = prepare_evidence_dir(self.output_dir)
        try:
            playwright_factory = _load_playwright_factory()
            with playwright_factory() as playwright:
                self._run_browser(playwright)
        except Exception as exc:  # Playwright exposes runtime-specific exception classes.
            self.operational_error = redact_text(f"{type(exc).__name__}: {exc}")

        report = self._build_report(started_at, started)
        _atomic_json_write(evidence_dir / "browser-smoke.json", redact_value(report))
        return report

    def _run_browser(self, playwright: Any) -> None:
        launch_options: dict[str, Any] = {"headless": True}
        if self.chromium_executable is not None:
            launch_options["executable_path"] = str(self.chromium_executable)
        browser = playwright.chromium.launch(**launch_options)
        try:
            self.browser_version = redact_text(browser.version, limit=80)
            for viewport in VIEWPORTS:
                self._run_viewport(browser, viewport)
        finally:
            browser.close()

    def _run_viewport(self, browser: Any, viewport: ViewportSpec) -> None:
        for authenticated, specs in PAGE_GROUPS:
            context = self._create_context(browser, viewport, authenticated=authenticated)
            try:
                for spec in specs:
                    self.results.append(self._check_page(context, viewport, spec))
            finally:
                context.close()

    def _create_context(
        self,
        browser: Any,
        viewport: ViewportSpec,
        *,
        authenticated: bool,
    ) -> Any:
        context = browser.new_context(
            viewport={"width": viewport.width, "height": viewport.height},
            device_scale_factor=1,
            is_mobile=viewport.is_mobile,
            has_touch=viewport.is_mobile,
            locale="zh-CN",
            service_workers="block",
        )
        context.set_default_timeout(self.timeout_ms)
        context.set_default_navigation_timeout(self.timeout_ms)
        context.add_init_script(
            "localStorage.setItem('globemind_new_user_guide_v3', 'completed');"
        )
        if authenticated:
            auth_script = (
                "localStorage.setItem('access_token', "
                f"{json.dumps(DUMMY_AUTH_TOKEN)});"
                "localStorage.setItem('current_user', "
                f"{json.dumps(json.dumps({'id': 900001, 'username': 'browser-smoke'}))});"
            )
            context.add_init_script(auth_script)
        return context

    def _check_page(
        self,
        context: Any,
        viewport: ViewportSpec,
        spec: PageSpec,
    ) -> dict[str, Any]:
        activity: dict[str, list[Any]] = {
            "console_errors": [],
            "page_errors": [],
            "http_redirects": [],
            "external_requests": [],
            "unexpected_api_requests": [],
            "blocked_same_origin_requests": [],
            "blocked_web_socket_requests": [],
            "resource_errors": [],
            "stubbed_api_requests": [],
        }
        page = context.new_page()
        page.on("console", lambda message: self._capture_console(activity, message))
        page.on(
            "pageerror",
            lambda error: activity["page_errors"].append(redact_text(error)),
        )
        page.on("response", lambda response: self._capture_response(activity, response))
        page.route("**/*", lambda route, request: self._route_request(activity, route, request))
        page.route_web_socket(
            "**/*",
            lambda web_socket: self._block_web_socket(activity, web_socket),
        )

        started = time.monotonic()
        navigation_error = ""
        selector_visible = False
        dimensions: dict[str, Any] = {}
        semantic_probes: dict[str, dict[str, Any]] = {}
        final_url = self.base_url
        try:
            page.goto(f"{self.base_url}{spec.path}", wait_until="domcontentloaded")
            page.wait_for_timeout(self.settle_ms)
            page.locator(spec.selector).first.wait_for(state="visible")
            selector_visible = True
            dimensions = page.evaluate(_DOM_SUMMARY_SCRIPT, spec.selector)
            if spec.semantic_probe_selectors:
                semantic_probes = fingerprint_semantic_probes(
                    page.evaluate(
                        _SEMANTIC_PROBE_SCRIPT,
                        list(spec.semantic_probe_selectors),
                    )
                )
            final_url = page.url
        except Exception as exc:
            navigation_error = redact_text(f"{type(exc).__name__}: {exc}")
            final_url = page.url or self.base_url
            try:
                dimensions = page.evaluate(_DOM_SUMMARY_SCRIPT, spec.selector)
            except Exception:
                dimensions = {}

        screenshot_name = f"{viewport.name}-{spec.check_id}.png"
        screenshot_error = ""
        try:
            screenshot = page.screenshot(full_page=False, animations="disabled")
            _atomic_bytes_write(self.output_dir / "screenshots" / screenshot_name, screenshot)
        except Exception as exc:
            screenshot_error = redact_text(f"{type(exc).__name__}: {exc}")
            if not navigation_error:
                navigation_error = f"screenshot failed: {screenshot_error}"

        parsed_final = urllib.parse.urlsplit(final_url)
        query = urllib.parse.parse_qs(parsed_final.query, keep_blank_values=True)
        redirect_values = query.get("redirect", [])
        redirect_target_status = _redirect_target_status(
            redirect_values,
            spec.expected_redirect,
        )
        observation: dict[str, Any] = {
            "check_id": f"{viewport.name}-{spec.check_id}",
            "page": spec.check_id,
            "viewport": {
                "name": viewport.name,
                "width": viewport.width,
                "height": viewport.height,
                "mobile": viewport.is_mobile,
            },
            "requested_path": spec.path,
            "authenticated": spec.authenticated,
            "fixture_snapshot_id": BROWSER_FIXTURE_SNAPSHOT_ID,
            "expected_path": spec.expected_path or spec.path,
            "expected_redirect": spec.expected_redirect,
            "final_url": _safe_url_without_query(final_url),
            "final_path": parsed_final.path or "/",
            "redirect_target_status": redirect_target_status,
            "selector": spec.selector,
            "selector_visible": selector_visible,
            "dimensions": dimensions,
            "semantic_probes": semantic_probes,
            "screenshot": f"screenshots/{screenshot_name}",
            "duration_ms": round((time.monotonic() - started) * 1000, 2),
            "navigation_error": navigation_error,
            "screenshot_error": screenshot_error,
            "console_errors": activity["console_errors"],
            "page_errors": activity["page_errors"],
            "http_redirects": activity["http_redirects"],
            "external_requests": activity["external_requests"],
            "unexpected_api_requests": activity["unexpected_api_requests"],
            "blocked_same_origin_requests": activity["blocked_same_origin_requests"],
            "blocked_web_socket_requests": activity["blocked_web_socket_requests"],
            "resource_errors": activity["resource_errors"],
            "stubbed_api_paths": sorted(set(activity["stubbed_api_requests"])),
        }
        failures = assess_page_observation(observation)
        observation["failures"] = failures
        observation["outcome"] = "passed" if not failures else "failed"
        page.close()
        return redact_value(observation)

    def _route_request(self, activity: dict[str, list[Any]], route: Any, request: Any) -> None:
        url = request.url
        if not _same_origin(url, self.base_url):
            activity["external_requests"].append(
                {"method": request.method, "url": _safe_url_without_query(url)}
            )
            route.abort("blockedbyclient")
            return
        path = _decoded_request_path(url)
        if not _is_api_path(path) and _is_allowed_candidate_request(
            path,
            request.method,
            request.resource_type,
        ):
            route.continue_()
            return
        if not _is_api_path(path):
            activity["blocked_same_origin_requests"].append(
                {
                    "method": request.method,
                    "path": redact_text(path),
                    "resource_type": request.resource_type,
                }
            )
            route.abort("blockedbyclient")
            return
        try:
            payload = api_stub_payload(path, request.method)
        except UnknownApiStub:
            activity["unexpected_api_requests"].append(
                {"method": request.method, "path": path}
            )
            route.fulfill(
                status=501,
                content_type="application/json",
                headers={"Cache-Control": "no-store"},
                body='{"detail":"browser smoke stub missing"}',
            )
            return
        activity["stubbed_api_requests"].append(path)
        route.fulfill(
            status=200,
            content_type="application/json",
            headers={"Cache-Control": "no-store"},
            body=json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
        )

    @staticmethod
    def _block_web_socket(activity: dict[str, list[Any]], web_socket: Any) -> None:
        activity["blocked_web_socket_requests"].append(
            {"url": _safe_url_without_query(web_socket.url)}
        )
        web_socket.close(code=1008, reason="browser smoke blocks WebSocket")

    @staticmethod
    def _capture_console(activity: dict[str, list[Any]], message: Any) -> None:
        if message.type == "error":
            activity["console_errors"].append(redact_text(message.text))

    def _capture_response(self, activity: dict[str, list[Any]], response: Any) -> None:
        status = int(response.status)
        url = response.url
        resource_type = response.request.resource_type
        if 300 <= status < 400:
            activity["http_redirects"].append(
                {"status": status, "url": _safe_url_without_query(url)}
            )
        if status >= 400 and resource_type in {"document", "script", "stylesheet"}:
            activity["resource_errors"].append(
                {
                    "status": status,
                    "resource_type": resource_type,
                    "url": _safe_url_without_query(url),
                }
            )

    def _build_report(self, started_at: str, started: float) -> dict[str, Any]:
        passed = sum(result.get("outcome") == "passed" for result in self.results)
        failed = len(self.results) - passed
        viewport_matrix = assess_viewport_matrix(self.results)
        status = (
            "error"
            if self.operational_error
            else "passed"
            if not failed and viewport_matrix["status"] == "passed"
            else "failed"
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "tool": {"name": TOOL_VERSION, "version": 2},
            "status": status,
            "started_at": started_at,
            "finished_at": _utc_now(),
            "duration_ms": round((time.monotonic() - started) * 1000, 2),
            "candidate": {"base_url": self.base_url},
            "browser": {
                "engine": "chromium",
                "version": self.browser_version,
                "explicit_executable": self.chromium_executable is not None,
                "headless": True,
            },
            "policy": {
                "loopback_only": True,
                "http_redirects": "rejected",
                "cross_origin_requests": "blocked",
                "same_origin_requests": "document-and-static-allowlist",
                "web_socket_requests": "blocked",
                "api_mode": "in-memory-stubs-only",
                "api_fixture_snapshot_id": BROWSER_FIXTURE_SNAPSHOT_ID,
                "viewport_fixture_identity": "exact_shared_snapshot_id",
                "semantic_accessibility_contract": (
                    "explicit-role-live-region-atomic-and-label-presence"
                ),
                "response_bodies_persisted": False,
                "request_headers_persisted": False,
                "dummy_token_persisted": False,
            },
            "limits": {
                "timeout_ms": self.timeout_ms,
                "settle_ms": self.settle_ms,
                "horizontal_overflow_tolerance_px": OVERFLOW_TOLERANCE_PX,
            },
            "summary": {"total": len(self.results), "passed": passed, "failed": failed},
            "viewport_matrix": viewport_matrix,
            "operational_error": self.operational_error,
            "checks": self.results,
        }


def _load_playwright_factory() -> Any:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserSmokeError(
            "Playwright is not installed in the separate browser validation environment"
        ) from exc
    return sync_playwright


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_bytes_write(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _atomic_json_write(path: Path, value: Mapping[str, Any]) -> None:
    payload = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _atomic_bytes_write(path, payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        required=True,
        help="Literal loopback candidate origin, for example http://127.0.0.1:18091",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Absolute path to a new or empty evidence directory",
    )
    parser.add_argument(
        "--chromium-executable",
        type=Path,
        help="Optional absolute path to an executable Chromium binary",
    )
    parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS)
    parser.add_argument("--settle-ms", type=int, default=DEFAULT_SETTLE_MS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        runner = BrowserSmoke(
            base_url=args.base_url,
            output_dir=args.output_dir,
            chromium_executable=args.chromium_executable,
            timeout_ms=args.timeout_ms,
            settle_ms=args.settle_ms,
        )
        report = runner.run()
    except (OSError, ValueError) as exc:
        print(f"browser smoke could not run: {redact_text(exc)}", file=sys.stderr)
        return 2

    summary = report["summary"]
    print(
        f"browser smoke {report['status']}: passed={summary['passed']} "
        f"failed={summary['failed']} evidence={args.output_dir / 'browser-smoke.json'}"
    )
    if report["status"] == "passed":
        return 0
    return 2 if report["status"] == "error" else 1


if __name__ == "__main__":
    raise SystemExit(main())
