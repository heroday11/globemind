#!/usr/bin/env python3
"""Black-box HTTP acceptance gate for an already-running release candidate.

The gate never imports application code, opens a database connection, or manages
processes.  It records bounded, body-free response summaries so that candidate
evidence can be retained without copying credentials or user data into artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import math
import os
import re
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

SCHEMA_VERSION = 1
TOOL_VERSION = "globemind-candidate-smoke-v1"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_STATIC_BODY_BYTES = 4 * 1024 * 1024
MAX_AUTH_TOKEN_BYTES = 16 * 1024
BUILD_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
SAFE_FILENAME_RE = re.compile(r"[^a-z0-9._-]+")

LEGACY_OPINION_ENDPOINTS = (
    "/api/opinion/micro-story-sub-events",
    "/api/opinion/event-timeseries",
    "/api/opinion/global-attention",
    "/api/opinion/sentiment-polarity",
    "/api/opinion/influence-index",
    "/api/opinion/composite-index",
    "/api/opinion/topic-breakdown",
    "/api/opinion/frame-breakdown",
    "/api/opinion/narrative-dispersion",
)

REQUIRED_FEATURE_HEALTH_IDS = frozenset(
    {
        "assistant",
        "dashboard",
        "financial-alerts",
        "graph-briefing",
        "ground-news",
        "identity",
        "operations",
        "opinion-analysis",
        "search",
        "story-graph",
    }
)

REQUIRED_RUNTIME_SERVICE_IDS = frozenset(
    {
        "daily_ingest",
        "ground_images",
        "ground_refresh",
        "l1_extract",
        "l1_prep",
        "proxy_pool",
        "quality_labels",
        "tunnel",
        "vllm",
        "wave1_extractor",
        "wave1_loader",
        "web",
    }
)

_FORBIDDEN_RUNTIME_CATALOG_KEYS = frozenset(
    {
        "credential",
        "credential_path",
        "credentials",
        "credentials_path",
        "password",
        "password_file",
        "secret",
        "secret_path",
        "secret_policy",
        "secret_refs",
        "secret_transport",
    }
)
_FORBIDDEN_RUNTIME_CONTROL_KEYS = frozenset(
    {
        "args",
        "arguments",
        "argv",
        "cmd",
        "cmdline",
        "command",
        "commands",
        "exec_args",
        "process_args",
        "process_argv",
        "process_command",
    }
)
_FORBIDDEN_RUNTIME_CATALOG_PATH_FRAGMENTS = (
    "/secrets/",
    ".db-secret",
    ".env",
    "credentials.json",
)
_FORBIDDEN_SERVICE_LEVEL_KEYS = frozenset(
    {
        "authorization",
        "body",
        "credential",
        "error_detail",
        "header",
        "headers",
        "path",
        "query",
        "request_id",
        "secret",
        "token",
        "url",
        "user",
        "user_id",
        "username",
    }
)

_GRAPH_DEPENDENT_CHECKS = (
    "graph_macro",
    "graph_macro_briefing",
    "graph_macro_micros",
    "graph_macro_tree",
    "graph_macro_search",
    "graph_micro",
    "graph_micro_news",
    "graph_micro_news_batch",
)


class CheckFailure(RuntimeError):
    """Expected response contract was not satisfied."""


class NetworkFailure(RuntimeError):
    """The candidate could not be reached safely."""


class ResponseTooLarge(RuntimeError):
    """A response exceeded the configured evidence bound."""

    def __init__(
        self,
        *,
        status: int,
        headers: Mapping[str, str],
        duration_ms: float,
        limit: int,
    ) -> None:
        super().__init__(f"response exceeded {limit} bytes")
        self.status = status
        self.headers = dict(headers)
        self.duration_ms = duration_ms
        self.limit = limit


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes
    duration_ms: float


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class HttpClient:
    """Small bounded HTTP client with proxies and redirects disabled."""

    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self.base_url = normalize_base_url(base_url)
        self.timeout_seconds = timeout_seconds
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler(),
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    ) -> HttpResponse:
        if not path.startswith("/"):
            raise ValueError("candidate request paths must be absolute")
        url = f"{self.base_url}{path}"
        parsed = urllib.parse.urlsplit(url)
        base = urllib.parse.urlsplit(self.base_url)
        if (parsed.scheme, parsed.hostname, parsed.port) != (
            base.scheme,
            base.hostname,
            base.port,
        ):
            raise ValueError("candidate request escaped the configured origin")

        request_headers = {
            "Accept": "application/json, text/html;q=0.9, */*;q=0.1",
            "Accept-Encoding": "identity",
            "User-Agent": f"{TOOL_VERSION}/{SCHEMA_VERSION}",
        }
        if headers:
            request_headers.update(headers)
        data = None
        if json_body is not None:
            data = json.dumps(
                json_body,
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url,
            data=data,
            headers=request_headers,
            method=method,
        )
        started = time.monotonic()
        response: Any
        try:
            response = self._opener.open(request, timeout=self.timeout_seconds)
        except urllib.error.HTTPError as exc:
            response = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise NetworkFailure(_safe_error(exc)) from exc

        try:
            status = int(response.getcode())
            response_headers = {key.lower(): value for key, value in response.headers.items()}
            content_length = _parse_content_length(response_headers.get("content-length"))
            if content_length is not None and content_length > max_body_bytes:
                raise ResponseTooLarge(
                    status=status,
                    headers=response_headers,
                    duration_ms=(time.monotonic() - started) * 1000,
                    limit=max_body_bytes,
                )
            body = response.read(max_body_bytes + 1)
            duration_ms = (time.monotonic() - started) * 1000
            if len(body) > max_body_bytes:
                raise ResponseTooLarge(
                    status=status,
                    headers=response_headers,
                    duration_ms=duration_ms,
                    limit=max_body_bytes,
                )
            return HttpResponse(
                status=status,
                headers=response_headers,
                body=body,
                duration_ms=duration_ms,
            )
        except (TimeoutError, OSError) as exc:
            raise NetworkFailure(_safe_error(exc)) from exc
        finally:
            response.close()


class _EntryAssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.module_scripts: list[str] = []
        self.has_app_mount = False
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "script" and values.get("type", "").lower() == "module":
            if values.get("src"):
                self.module_scripts.append(values["src"])
        elif tag.lower() == "div" and values.get("id") == "app":
            self.has_app_mount = True
        elif tag.lower() == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data


Validator = Callable[[Any, HttpResponse], dict[str, Any]]


def _article_body_paragraphs(value: Any) -> list[str]:
    """Mirror the public reader's deterministic display-body segmentation."""
    if not isinstance(value, str):
        return []
    text_value = value.replace("\r\n", "\n").strip()
    if not text_value:
        return []
    lines = [part.strip() for part in re.split(r"\n+", text_value) if part.strip()]
    paragraphs: list[str] = []
    for line in lines:
        if len(line) <= 280:
            paragraphs.append(line)
            continue
        sentences = [
            part.strip()
            for part in re.split(r"(?<=[。！？!?.])\s+", line)
            if part.strip()
        ]
        if len(sentences) <= 1:
            paragraphs.append(line)
            continue
        buffer = ""
        for sentence in sentences:
            if not buffer:
                buffer = sentence
            elif len(f"{buffer} {sentence}") > 240:
                paragraphs.append(buffer)
                buffer = sentence
            else:
                buffer = f"{buffer} {sentence}"
        if buffer:
            paragraphs.append(buffer)
    return paragraphs


class CandidateAcceptance:
    def __init__(
        self,
        *,
        base_url: str,
        expected_build_id: str,
        output_dir: Path,
        auth_token: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        client: Any | None = None,
    ) -> None:
        if BUILD_ID_RE.fullmatch(expected_build_id) is None:
            raise ValueError("expected build id has an invalid format")
        if timeout_seconds <= 0 or timeout_seconds > 300:
            raise ValueError("timeout must be greater than zero and at most 300 seconds")
        if (
            not isinstance(auth_token, str)
            or len(auth_token) < 32
            or len(auth_token.encode("utf-8")) > MAX_AUTH_TOKEN_BYTES
            or any(character.isspace() for character in auth_token)
        ):
            raise ValueError("candidate auth token has an invalid format")
        self.base_url = normalize_base_url(base_url)
        self.expected_build_id = expected_build_id
        self.output_dir = output_dir
        self.client = client or HttpClient(self.base_url, timeout_seconds)
        self.timeout_seconds = timeout_seconds
        self._authorization_headers: dict[str, str] | None = {
            "Authorization": f"Bearer {auth_token}"
        }
        self.results: list[dict[str, Any]] = []
        self._entry_asset_path: str | None = None
        self._graph_macro: dict[str, Any] | None = None
        self._graph_micro: dict[str, Any] | None = None
        self._v11_l2_id: str | None = None
        self._v11_l1_id: str | None = None
        self._v11_news_id: int | None = None
        self._entity_governance_event_count: int | None = None

    def run(self) -> dict[str, Any]:
        started_at = _utc_now()
        started_monotonic = time.monotonic()

        self._run_health_checks()
        self._run_frontend_checks()
        self._run_auth_checks()
        self._run_runtime_catalog_check()
        self._run_public_surface_checks()
        self._run_graph_checks()
        self._run_v11_checks()
        self._run_legacy_retirement_checks()

        finished_at = _utc_now()
        summary = self._summarize()
        acceptance = {
            "schema_version": SCHEMA_VERSION,
            "tool": {"name": TOOL_VERSION, "version": 1},
            "status": "passed" if summary["required_failed"] == 0 else "failed",
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_ms": round((time.monotonic() - started_monotonic) * 1000, 2),
            "candidate": {
                "base_url": self.base_url,
                "expected_build_id": self.expected_build_id,
            },
            "limits": {
                "request_timeout_seconds": self.timeout_seconds,
                "default_response_bytes": DEFAULT_MAX_BODY_BYTES,
                "static_response_bytes": MAX_STATIC_BODY_BYTES,
            },
            "policy": {
                "http_redirects": "rejected",
                "response_bodies_persisted": False,
                "request_headers_persisted": False,
                "auth_token_persisted": False,
                "graph_empty": (
                    "fail: linked L3/L2 data is required; dependent checks are blocked and "
                    "do not count as accepted skips"
                ),
                "worker_failover": "out_of_scope: this HTTP gate never signals processes",
            },
            "summary": summary,
            "checks": self.results,
        }
        self._write_evidence(acceptance)
        return acceptance

    def _run_health_checks(self) -> None:
        def validate_live(payload: Any, _response: HttpResponse) -> dict[str, Any]:
            data = _require_object(payload, "liveness response")
            if data.get("status") != "healthy" or data.get("check") != "process":
                raise CheckFailure("liveness process contract was not healthy")
            release = _require_release_identity(data, self.expected_build_id)
            if data.get("service") != "globemind-api":
                raise CheckFailure("liveness service identity did not match globemind-api")
            return {"release": release, "service": "globemind-api"}

        def validate_ready(payload: Any, _response: HttpResponse) -> dict[str, Any]:
            data = _require_object(payload, "readiness response")
            if data.get("ready") is not True:
                raise CheckFailure("readiness did not report ready=true")
            checks = _require_object(data.get("checks"), "readiness checks")
            database = _require_object(checks.get("database"), "database readiness")
            if database.get("status") != "up":
                raise CheckFailure("readiness database check was not up")
            release = _require_release_identity(data, self.expected_build_id)
            return {
                "release": release,
                "service_status": str(data.get("status") or "")[:40],
                "database_status": "up",
            }

        def validate_features(payload: Any, _response: HttpResponse) -> dict[str, Any]:
            data = _require_object(payload, "feature health response")
            if data.get("status") not in {"healthy", "degraded"} or data.get("ready") is not True:
                raise CheckFailure("feature health did not report available and ready=true")
            checks = _require_object(data.get("checks"), "feature health checks")
            observed = set(checks)
            if observed != REQUIRED_FEATURE_HEALTH_IDS:
                raise CheckFailure("feature health check set did not match the V1 contract")
            non_current: list[str] = []
            for feature_id in sorted(REQUIRED_FEATURE_HEALTH_IDS):
                check = _require_object(checks.get(feature_id), f"{feature_id} health check")
                status = check.get("status")
                if status not in {"up", "degraded", "stale"}:
                    raise CheckFailure(f"required feature was unavailable: {feature_id}")
                if status != "up":
                    non_current.append(feature_id)
            return {
                "feature_count": len(checks),
                "service_status": data.get("status"),
                "non_current_count": len(non_current),
            }

        self._json_check("health_live", "identity", "GET", "/api/health/live", validate_live)
        self._json_check("health_ready", "identity", "GET", "/api/health/ready", validate_ready)
        self._json_check(
            "health_features",
            "features",
            "GET",
            "/api/health/features",
            validate_features,
            headers=self._authorization_headers,
        )

    def _run_frontend_checks(self) -> None:
        def validate_root(payload: Any, _response: HttpResponse) -> dict[str, Any]:
            if not isinstance(payload, str):
                raise CheckFailure("root response was not HTML")
            parser = _EntryAssetParser()
            parser.feed(payload)
            if not parser.has_app_mount:
                raise CheckFailure("root HTML did not contain the application mount")
            expected_title = "GlobeMind · 多语言地缘情报平台"
            if parser.title.strip() != expected_title:
                raise CheckFailure("root HTML title did not match GlobeMind")
            if not parser.module_scripts:
                raise CheckFailure("root HTML did not reference a module entry asset")
            asset = _normalize_entry_asset(parser.module_scripts[0])
            self._entry_asset_path = asset
            return {
                "title": expected_title,
                "app_mount": True,
                "entry_asset_fingerprint": _identifier_fingerprint(asset),
            }

        self._html_check("frontend_root", "frontend", "GET", "/", validate_root)
        if self._entry_asset_path is None:
            self._blocked(
                "frontend_entry_asset",
                "frontend",
                "GET",
                "/assets/<entry>.js",
                "root HTML did not yield a trusted entry asset",
            )
            return

        def validate_asset(payload: Any, response: HttpResponse) -> dict[str, Any]:
            if not isinstance(payload, bytes) or not payload:
                raise CheckFailure("frontend entry asset was empty")
            content_type = response.headers.get("content-type", "").lower()
            if "javascript" not in content_type and "ecmascript" not in content_type:
                raise CheckFailure("frontend entry asset content type was not JavaScript")
            return {"nonempty": True, "asset_kind": "module_entry"}

        self._bytes_check(
            "frontend_entry_asset",
            "frontend",
            "GET",
            self._entry_asset_path,
            validate_asset,
            display_endpoint="/assets/<entry>.js",
            max_body_bytes=MAX_STATIC_BODY_BYTES,
        )

    def _run_auth_checks(self) -> None:
        def validate_auth_rejection(payload: Any, _response: HttpResponse) -> dict[str, Any]:
            data = _require_object(payload, "authentication rejection")
            if data.get("detail") != "未登录或 token 无效":
                raise CheckFailure(
                    "protected endpoint did not use the stable unauthenticated contract"
                )
            return {"semantic": "unauthenticated", "detail_redacted": True}

        def validate_login_rejection(payload: Any, _response: HttpResponse) -> dict[str, Any]:
            data = _require_object(payload, "login rejection")
            if data.get("detail") != "用户名或密码错误":
                raise CheckFailure(
                    "invalid login did not use the stable credential rejection contract"
                )
            return {"semantic": "invalid_credentials", "detail_redacted": True}

        self._json_check(
            "auth_missing_credentials",
            "auth",
            "GET",
            "/api/auth/me",
            validate_auth_rejection,
            expected_status=401,
        )
        self._json_check(
            "auth_invalid_bearer",
            "auth",
            "GET",
            "/api/auth/me",
            validate_auth_rejection,
            expected_status=401,
            headers={"Authorization": "Bearer candidate-smoke-invalid-token"},
        )
        self._json_check(
            "auth_invalid_login",
            "auth",
            "POST",
            "/api/auth/login",
            validate_login_rejection,
            expected_status=401,
            json_body={
                "username": "candidate-smoke-user-does-not-exist",
                "password": "candidate-smoke-invalid-password",
            },
        )

    def _run_runtime_catalog_check(self) -> None:
        if self._authorization_headers is None:
            self._blocked(
                "runtime_catalog",
                "operations",
                "GET",
                "/api/ops/runtime-catalog",
                "authenticated candidate session was not established",
            )
            return

        def validate_runtime_catalog(
            payload: Any, _response: HttpResponse
        ) -> dict[str, Any]:
            data = _require_object(payload, "runtime catalog response")
            _reject_runtime_catalog_secrets(data)
            if (
                data.get("schema_version") != 2
                or data.get("inventory_version") != "1.0.0"
                or data.get("operation") != "runtime-catalog"
            ):
                raise CheckFailure("runtime catalog identity did not match the V1 contract")
            if data.get("available") is not True or data.get("read_only") is not True:
                raise CheckFailure("runtime catalog was unavailable or not read-only")
            if data.get("process_inspection") is not False:
                raise CheckFailure("runtime catalog unexpectedly inspected processes")

            control = _require_object(data.get("control"), "runtime catalog control")
            actions = _require_list(control.get("actions"), "runtime catalog control actions")
            if control.get("enabled") is not False or actions:
                raise CheckFailure("runtime catalog exposed executable control actions")

            services = _require_list(data.get("services"), "runtime catalog services")
            service_ids: list[str] = []
            for index, raw_service in enumerate(services):
                service = _require_object(raw_service, f"runtime catalog service {index}")
                service_id = service.get("id")
                if not isinstance(service_id, str) or service_id not in REQUIRED_RUNTIME_SERVICE_IDS:
                    raise CheckFailure("runtime catalog service set did not match the V1 contract")
                service_ids.append(service_id)
                if service.get("catalog_status") != "current":
                    raise CheckFailure(f"runtime catalog service was not current: {service_id}")
                if _require_list(
                    service.get("catalog_drift"),
                    f"runtime catalog drift for {service_id}",
                ):
                    raise CheckFailure(f"runtime catalog service had drift: {service_id}")
                lifecycle = _require_object(
                    service.get("lifecycle_authorization"),
                    f"runtime catalog lifecycle for {service_id}",
                )
                authorized = _require_list(
                    lifecycle.get("authorized_operations"),
                    f"runtime catalog authorized operations for {service_id}",
                )
                if lifecycle.get("state") != "not-authorized" or authorized:
                    raise CheckFailure(
                        f"runtime catalog service exposed authorized operations: {service_id}"
                    )

            observed_ids = frozenset(service_ids)
            if len(service_ids) != len(observed_ids) or observed_ids != REQUIRED_RUNTIME_SERVICE_IDS:
                raise CheckFailure("runtime catalog service set did not match the V1 contract")

            summary = _require_object(data.get("summary"), "runtime catalog summary")
            expected_count = len(REQUIRED_RUNTIME_SERVICE_IDS)
            if (
                summary.get("service_count") != expected_count
                or summary.get("catalog_current") != expected_count
                or summary.get("catalog_drifted") != 0
                or summary.get("lifecycle_authorized") != 0
            ):
                raise CheckFailure("runtime catalog summary did not match the V1 contract")
            service_set_digest = hashlib.sha256(
                "\n".join(sorted(observed_ids)).encode("utf-8")
            ).hexdigest()
            return {
                "service_count": expected_count,
                "catalog_current": expected_count,
                "catalog_drifted": 0,
                "control_enabled": False,
                "authorized_operation_count": 0,
                "service_set_sha256": service_set_digest,
            }

        self._json_check(
            "runtime_catalog",
            "operations",
            "GET",
            "/api/ops/runtime-catalog",
            validate_runtime_catalog,
            headers=self._authorization_headers,
        )
        self._run_model_assurance_check()
        self._run_research_storage_check()
        self._run_identity_assurance_check()
        self._run_service_level_check()
        self._run_entity_governance_check()
        self._authorization_headers = None

    def _run_model_assurance_check(self) -> None:
        if self._authorization_headers is None:
            self._blocked(
                "model_assurance",
                "model_assurance",
                "GET",
                "/api/model-assurance/status",
                "authenticated candidate session was not established",
            )
            return

        def validate_model_assurance(
            payload: Any, _response: HttpResponse
        ) -> dict[str, Any]:
            data = _require_object(payload, "model assurance status")
            evaluation_count = _require_nonnegative_int(
                data.get("evaluation_count"), "model evaluation_count"
            )
            eligible_count = _require_nonnegative_int(
                data.get("eligible_count"), "model eligible_count"
            )
            reasons = _require_list(
                data.get("reason_codes"), "model assurance reason codes"
            )
            if (
                data.get("schema_version") != "globemind.model-assurance.v1"
                or data.get("available") is not True
                or data.get("operational_state") != "observed"
                or data.get("release_status") != "eligible"
                or data.get("gold_standard_state") != "manifest_attested"
                or evaluation_count < 2
                or eligible_count < 1
                or eligible_count > evaluation_count
                or reasons
            ):
                raise CheckFailure("model assurance did not satisfy the release gate")
            latest = _require_object(data.get("latest"), "latest model evaluation")
            latest_reasons = _require_list(
                latest.get("reason_codes"), "latest model evaluation reason codes"
            )
            if (
                latest.get("gate_state") != "eligible"
                or latest.get("release_eligible") is not True
                or latest.get("drift_state") != "within_threshold"
                or latest.get("rollback_action") != "proceed"
                or latest_reasons
            ):
                raise CheckFailure("model assurance did not satisfy the release gate")
            return {
                "evaluation_count": evaluation_count,
                "eligible_count": eligible_count,
                "release_status": "eligible",
                "gold_standard_state": "manifest_attested",
                "latest_drift_state": "within_threshold",
                "latest_rollback_action": "proceed",
            }

        self._json_check(
            "model_assurance",
            "model_assurance",
            "GET",
            "/api/model-assurance/status",
            validate_model_assurance,
            headers=self._authorization_headers,
        )

    def _run_research_storage_check(self) -> None:
        if self._authorization_headers is None:
            self._blocked(
                "research_storage",
                "research_workflow",
                "GET",
                "/api/research/storage-status",
                "authenticated candidate session was not established",
            )
            return

        def validate_research_storage(
            payload: Any, _response: HttpResponse
        ) -> dict[str, Any]:
            data = _require_object(payload, "research storage status")
            if (
                data.get("schema_version") != "research-storage-status-v1"
                or data.get("status") != "available"
                or data.get("durability") != "atomic-json-fsync"
                or data.get("fallback") != "none"
            ):
                raise CheckFailure("research storage was not durably available")
            if data.get("audit_immutability") != "unavailable":
                raise CheckFailure("research storage overstated audit immutability")
            return {
                "status": "available",
                "durability": "atomic-json-fsync",
                "fallback": "none",
                "audit_immutability": "unavailable",
            }

        self._json_check(
            "research_storage",
            "research_workflow",
            "GET",
            "/api/research/storage-status",
            validate_research_storage,
            headers=self._authorization_headers,
        )

    def _run_identity_assurance_check(self) -> None:
        if self._authorization_headers is None:
            for check_id, endpoint in (
                ("identity_assurance", "/api/user/security/mfa"),
                ("identity_security_audit", "/api/user/security/audit?limit=1"),
                (
                    "identity_deletion_impact_plan",
                    "/api/user/privacy/deletion-impact-plan",
                ),
            ):
                self._blocked(
                    check_id,
                    "identity",
                    "GET",
                    endpoint,
                    "authenticated candidate session was not established",
                )
            return

        def validate_identity_assurance(
            payload: Any, _response: HttpResponse
        ) -> dict[str, Any]:
            data = _require_object(payload, "identity assurance status")
            state = data.get("status")
            if state not in {"disabled", "pending", "enabled"}:
                raise CheckFailure("identity assurance status was invalid")
            if (
                data.get("schema_version") != "identity-mfa-status-v1"
                or data.get("enabled") is not (state == "enabled")
                or data.get("pending_enrollment") is not (state == "pending")
            ):
                raise CheckFailure("identity assurance status was contradictory")
            recovery_remaining = _require_nonnegative_int(
                data.get("recovery_codes_remaining"),
                "identity recovery_codes_remaining",
            )
            assurance = _require_object(data.get("assurance"), "identity assurance")
            capabilities = _require_object(
                data.get("capabilities"), "identity assurance capabilities"
            )
            storage = _require_object(data.get("storage"), "identity assurance storage")
            if (
                assurance.get("type") != "totp-rfc6238"
                or assurance.get("enrollment_state") != "available"
                or assurance.get("institutional_sso") != "unavailable"
                or assurance.get("device_attestation") != "unavailable"
                or assurance.get("independent_security_review") != "unavailable"
                or capabilities.get("totp_enrollment") != "available"
                or capabilities.get("recovery_codes") != "available"
                or capabilities.get("tracked_sessions") != "available"
                or storage.get("status") != "available"
                or storage.get("backend") != "append-only-filesystem"
                or storage.get("writes_on_read") is not False
                or storage.get("last_seen") != "unavailable"
            ):
                raise CheckFailure("identity assurance capability was unavailable")
            if state == "pending":
                attempts = data.get("pending_attempts_remaining")
                if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts <= 0:
                    raise CheckFailure("identity MFA enrollment had no remaining attempts")
            elif data.get("pending_attempts_remaining") is not None:
                raise CheckFailure("identity assurance pending attempt state was contradictory")
            return {
                "status": state,
                "enrollment_state": "available",
                "tracked_sessions": "available",
                "recovery_codes_remaining": recovery_remaining,
                "writes_on_read": False,
            }

        def validate_identity_audit(
            payload: Any, _response: HttpResponse
        ) -> dict[str, Any]:
            data = _require_object(payload, "identity security audit")
            events = _require_list(data.get("events"), "identity security audit events")
            redaction = _require_object(
                data.get("redaction"), "identity security audit redaction"
            )
            if data.get("schema_version") != "identity-security-audit-v1" or len(events) > 1:
                raise CheckFailure("identity security audit contract was invalid")
            if redaction != {
                "token": "never_stored",
                "totp_secret": "not_in_audit",
                "recovery_code": "never_stored",
                "reason": "sha256_and_length_only",
                "body_fields": "none",
            }:
                raise CheckFailure("identity security audit redaction was incomplete")
            for raw_event in events:
                event = _require_object(raw_event, "identity security audit event")
                if set(event) != {
                    "event_id",
                    "sequence",
                    "timestamp",
                    "action",
                    "reason_sha256",
                    "reason_length",
                    "changed_fields",
                }:
                    raise CheckFailure("identity security audit event exposed unexpected fields")
                _require_nonnegative_int(event.get("reason_length"), "audit reason_length")
                _require_list(event.get("changed_fields"), "audit changed_fields")
            return {
                "event_count": len(events),
                "token_storage": "never_stored",
                "reason_storage": "sha256_and_length_only",
            }

        def validate_deletion_impact_plan(
            payload: Any, _response: HttpResponse
        ) -> dict[str, Any]:
            data = _require_object(payload, "account deletion impact plan")
            if set(data) != {
                "schema_version",
                "generated_at",
                "subject_ref",
                "canonical_identity_verified",
                "operation_mode",
                "deletion_performed",
                "execution_state",
                "request_registration_state",
                "scope_complete",
                "impact_items",
                "disposition_summary",
                "external_blockers",
                "execution_contract",
                "limits",
                "excluded_fields",
            }:
                raise CheckFailure(
                    "account deletion impact plan exposed unexpected fields"
                )
            if (
                data.get("schema_version") != "account-deletion-impact-plan-v1"
                or not _valid_timezone_timestamp(data.get("generated_at"))
                or re.fullmatch(r"user:[1-9][0-9]*", str(data.get("subject_ref") or ""))
                is None
                or data.get("canonical_identity_verified") is not True
                or data.get("operation_mode") != "read_only_preflight"
                or data.get("deletion_performed") is not False
                or data.get("execution_state") != "blocked"
                or data.get("request_registration_state") != "not_checked"
                or type(data.get("scope_complete")) is not bool
            ):
                raise CheckFailure(
                    "account deletion impact plan overstated execution capability"
                )
            items = _require_list(
                data.get("impact_items"), "account deletion impact items"
            )
            if not items or len(items) > 128:
                raise CheckFailure("account deletion impact item bound was invalid")
            dispositions = {
                "delete",
                "anonymize",
                "retain",
                "review_required",
                "unavailable",
            }
            observed: dict[str, dict[str, int]] = {
                disposition: {
                    "scope_count": 0,
                    "exact_record_count": 0,
                    "unavailable_scope_count": 0,
                }
                for disposition in dispositions
            }
            scopes: set[str] = set()
            for raw_item in items:
                item = _require_object(raw_item, "account deletion impact item")
                if set(item) != {
                    "scope",
                    "disposition",
                    "record_count",
                    "count_status",
                    "ownership_basis",
                    "reason_code",
                }:
                    raise CheckFailure(
                        "account deletion impact item exposed unexpected fields"
                    )
                scope = item.get("scope")
                disposition = item.get("disposition")
                count_status = item.get("count_status")
                if (
                    not isinstance(scope, str)
                    or re.fullmatch(
                        r"[a-z][a-z0-9_]{1,95}(?:\.[a-z][a-z0-9_]{1,95})*",
                        scope,
                    )
                    is None
                    or scope in scopes
                    or disposition not in dispositions
                    or count_status not in {"exact", "lower_bound", "unavailable"}
                    or not isinstance(item.get("ownership_basis"), str)
                    or not item["ownership_basis"]
                    or re.fullmatch(
                        r"[A-Z][A-Z0-9_]{2,127}",
                        str(item.get("reason_code") or ""),
                    )
                    is None
                ):
                    raise CheckFailure("account deletion impact item was invalid")
                scopes.add(scope)
                count = item.get("record_count")
                if count_status == "unavailable":
                    if count is not None or disposition != "unavailable":
                        raise CheckFailure(
                            "account deletion impact count was contradictory"
                        )
                else:
                    count = _require_nonnegative_int(
                        count, "account deletion impact record_count"
                    )
                    if disposition == "unavailable":
                        raise CheckFailure(
                            "account deletion impact count was contradictory"
                        )
                bucket = observed[str(disposition)]
                bucket["scope_count"] += 1
                if count_status == "exact":
                    bucket["exact_record_count"] += int(count or 0)
                else:
                    bucket["unavailable_scope_count"] += 1
            summary = _require_object(
                data.get("disposition_summary"), "account deletion disposition summary"
            )
            if set(summary) != dispositions:
                raise CheckFailure("account deletion disposition summary was incomplete")
            for disposition in dispositions:
                bucket = _require_object(
                    summary.get(disposition),
                    f"account deletion {disposition} summary",
                )
                if bucket != observed[disposition]:
                    raise CheckFailure(
                        "account deletion disposition summary was contradictory"
                    )
            if data.get("scope_complete") is not (
                observed["unavailable"]["scope_count"] == 0
            ):
                raise CheckFailure("account deletion scope completeness was contradictory")
            blockers = _require_list(
                data.get("external_blockers"), "account deletion blockers"
            )
            required_categories = {
                "retention_legal_basis",
                "checkpoint_and_recovery",
                "dependency_review",
                "manual_authority",
                "scope_completeness",
            }
            categories: set[str] = set()
            for raw_blocker in blockers:
                blocker = _require_object(raw_blocker, "account deletion blocker")
                category = blocker.get("category")
                if (
                    set(blocker)
                    != {"code", "category", "status", "required_authority"}
                    or category not in required_categories
                    or category in categories
                    or blocker.get("status") != "open"
                    or not isinstance(blocker.get("required_authority"), str)
                    or not blocker["required_authority"]
                    or re.fullmatch(
                        r"[A-Z][A-Z0-9_]{2,127}",
                        str(blocker.get("code") or ""),
                    )
                    is None
                ):
                    raise CheckFailure("account deletion blocker contract was invalid")
                categories.add(str(category))
            if categories != required_categories:
                raise CheckFailure("account deletion blocker set was incomplete")
            if data.get("execution_contract") != {
                "may_execute": False,
                "manual_authority_required": True,
                "request_registration_is_not_execution": True,
                "retention_and_legal_basis_status": "unverified",
                "checkpoint_status": "not_proven",
            }:
                raise CheckFailure("account deletion execution contract was invalid")
            limits = _require_object(data.get("limits"), "account deletion plan limits")
            if set(limits) != {
                "impact_items",
                "unavailable_scopes",
                "source_bytes",
                "response_bytes",
            } or any(
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
                for value in limits.values()
            ):
                raise CheckFailure("account deletion plan limits were invalid")
            excluded = _require_list(
                data.get("excluded_fields"), "account deletion excluded fields"
            )
            if set(excluded) != {
                "record_bodies",
                "absolute_paths",
                "other_subject_identifiers",
                "credentials_and_tokens",
            }:
                raise CheckFailure("account deletion exclusions were incomplete")
            return {
                "execution_state": "blocked",
                "deletion_performed": False,
                "impact_scope_count": len(items),
                "unavailable_scope_count": observed["unavailable"]["scope_count"],
                "external_blocker_count": len(blockers),
                "writes_exercised": False,
            }

        self._json_check(
            "identity_assurance",
            "identity",
            "GET",
            "/api/user/security/mfa",
            validate_identity_assurance,
            headers=self._authorization_headers,
        )
        self._json_check(
            "identity_security_audit",
            "identity",
            "GET",
            "/api/user/security/audit?limit=1",
            validate_identity_audit,
            headers=self._authorization_headers,
        )
        self._json_check(
            "identity_deletion_impact_plan",
            "identity",
            "GET",
            "/api/user/privacy/deletion-impact-plan",
            validate_deletion_impact_plan,
            headers=self._authorization_headers,
        )

    def _run_service_level_check(self) -> None:
        if self._authorization_headers is None:
            for check_id, endpoint in (
                ("service_level_status", "/api/service-level/status"),
                ("service_level_summary", "/api/service-level/summary?window_hours=24"),
            ):
                self._blocked(
                    check_id,
                    "service_level",
                    "GET",
                    endpoint,
                    "authenticated candidate session was not established",
                )
            return

        def validate_target(value: Any) -> None:
            target = _require_object(value, "service-level target")
            if target != {
                "approval_state": "not_approved",
                "compliance": "not_computable",
                "targets_configured": False,
                "approver_evidence_state": "absent",
            }:
                raise CheckFailure(
                    "service-level response overstated target approval or compliance"
                )

        def validate_common(data: dict[str, Any]) -> tuple[int, int]:
            _reject_service_level_sensitive_fields(data)
            if (
                data.get("schema_version") != "globemind.service-level.v1"
                or data.get("measurement_method_version")
                != "http-route-template-duration-nearest-rank-v1"
                or data.get("measurement_state") not in {"not_observed", "observed"}
                or data.get("storage_state") not in {"not_initialized", "available"}
                or data.get("integrity_state") != "verified"
            ):
                raise CheckFailure("service-level identity or integrity was unavailable")
            failures = _require_nonnegative_int(
                data.get("instrumentation_write_failure_count"),
                "service-level instrumentation_write_failure_count",
            )
            if data.get("instrumentation_write_state") != (
                "failures_observed" if failures else "no_failures_observed"
            ):
                raise CheckFailure("service-level write-failure state was contradictory")
            validate_target(data.get("target"))
            return failures, 1 if data.get("storage_state") == "available" else 0

        def validate_status(payload: Any, _response: HttpResponse) -> dict[str, Any]:
            data = _require_object(payload, "service-level status")
            failures, initialized = validate_common(data)
            observations = _require_nonnegative_int(
                data.get("total_observation_count"),
                "service-level total_observation_count",
            )
            if data.get("measurement_state") != (
                "observed" if observations else "not_observed"
            ):
                raise CheckFailure("service-level measurement state was contradictory")
            if not initialized and (observations or failures):
                raise CheckFailure("uninitialized service-level storage contained records")
            return {
                "measurement_state": data["measurement_state"],
                "storage_state": data["storage_state"],
                "total_observation_count": observations,
                "write_failure_count": failures,
                "target_approval_state": "not_approved",
                "compliance": "not_computable",
            }

        def validate_metrics(value: Any, expected_scope: str) -> dict[str, Any]:
            metrics = _require_object(value, f"service-level {expected_scope} metrics")
            if (
                metrics.get("scope") != expected_scope
                or metrics.get("error_rate_definition")
                != "all_non_success_outcomes"
                or metrics.get("percentile_method") != "nearest_rank"
            ):
                raise CheckFailure("service-level aggregate method was invalid")
            counts = {
                name: _require_nonnegative_int(
                    metrics.get(name), f"service-level {expected_scope} {name}"
                )
                for name in (
                    "sample_count",
                    "success_count",
                    "error_count",
                    "timeout_count",
                    "cancelled_count",
                )
            }
            if sum(
                counts[name]
                for name in (
                    "success_count",
                    "error_count",
                    "timeout_count",
                    "cancelled_count",
                )
            ) != counts["sample_count"]:
                raise CheckFailure("service-level aggregate counters were contradictory")
            if counts["sample_count"]:
                success_rate = metrics.get("success_rate")
                error_rate = metrics.get("error_rate")
                if (
                    isinstance(success_rate, bool)
                    or not isinstance(success_rate, (int, float))
                    or isinstance(error_rate, bool)
                    or not isinstance(error_rate, (int, float))
                    or not math.isfinite(float(success_rate))
                    or not math.isfinite(float(error_rate))
                    or abs(
                        float(success_rate)
                        - counts["success_count"] / counts["sample_count"]
                    )
                    > 1e-12
                    or abs(float(error_rate) - (1 - float(success_rate))) > 1e-12
                ):
                    raise CheckFailure("service-level aggregate rates were contradictory")
                percentiles = [
                    _require_nonnegative_int(
                        metrics.get(name), f"service-level {expected_scope} {name}"
                    )
                    for name in ("p50_ms", "p95_ms", "p99_ms")
                ]
                if percentiles != sorted(percentiles):
                    raise CheckFailure("service-level percentiles were contradictory")
            elif any(
                metrics.get(name) is not None
                for name in ("success_rate", "error_rate", "p50_ms", "p95_ms", "p99_ms")
            ):
                raise CheckFailure("empty service-level aggregate exposed numeric results")
            return counts

        def validate_summary(payload: Any, _response: HttpResponse) -> dict[str, Any]:
            data = _require_object(payload, "service-level summary")
            failures, initialized = validate_common(data)
            operations = _require_list(data.get("operations"), "service-level operations")
            if len(operations) != 3:
                raise CheckFailure("service-level operation set was incomplete")
            by_scope: dict[str, dict[str, Any]] = {}
            for raw in operations:
                item = _require_object(raw, "service-level operation")
                scope = item.get("scope")
                if scope not in {"search", "export", "report"} or scope in by_scope:
                    raise CheckFailure("service-level operation set was invalid")
                by_scope[str(scope)] = validate_metrics(item, str(scope))
            if set(by_scope) != {"search", "export", "report"}:
                raise CheckFailure("service-level operation set was incomplete")
            overall = validate_metrics(data.get("overall"), "overall")
            for name in overall:
                if overall[name] != sum(item[name] for item in by_scope.values()):
                    raise CheckFailure("service-level overall aggregate was contradictory")
            samples = overall["sample_count"]
            if data.get("measurement_state") != (
                "observed" if samples else "not_observed"
            ):
                raise CheckFailure("service-level summary state was contradictory")
            if not initialized and (samples or failures):
                raise CheckFailure("uninitialized service-level storage contained records")
            return {
                "measurement_state": data["measurement_state"],
                "storage_state": data["storage_state"],
                "sample_count": samples,
                "write_failure_count": failures,
                "operation_count": len(by_scope),
                "target_approval_state": "not_approved",
                "compliance": "not_computable",
            }

        self._json_check(
            "service_level_status",
            "service_level",
            "GET",
            "/api/service-level/status",
            validate_status,
            headers=self._authorization_headers,
        )
        self._json_check(
            "service_level_summary",
            "service_level",
            "GET",
            "/api/service-level/summary?window_hours=24",
            validate_summary,
            headers=self._authorization_headers,
        )

    def _run_entity_governance_check(self) -> None:
        if self._authorization_headers is None:
            for check_id, endpoint in (
                ("entity_governance_status", "/api/entity-governance/status"),
                ("entity_governance_catalog", "/api/entity-governance/catalog"),
            ):
                self._blocked(
                    check_id,
                    "entity_governance",
                    "GET",
                    endpoint,
                    "authenticated candidate session was not established",
                )
            return

        def validate_status(payload: Any, _response: HttpResponse) -> dict[str, Any]:
            data = _require_object(payload, "entity governance status")
            event_count = _require_nonnegative_int(
                data.get("event_count"), "entity governance event_count"
            )
            latest_event_id = data.get("latest_event_id")
            if (
                data.get("schema_version") != "entity-governance-status-v2"
                or data.get("storage_status") != "available"
                or data.get("reason") is not None
                or data.get("integrity_status") != "verified"
                or data.get("mutation_status") != "ready"
                or data.get("mutation_blocker") is not None
                or data.get("chain") != "sha256-and-hmac-sha256"
                or data.get("append_semantics") != "no-replace-local-filesystem"
                or data.get("hmac_key_id") != "unavailable"
                or data.get("hmac_key_rotation")
                != "offline-controlled-migration-not-implemented"
                or data.get("worm_status") != "unavailable"
                or data.get("digital_signature_status") != "unavailable"
                or data.get("institutional_directory_integration") != "unavailable"
                or data.get("accuracy_claim") != "not_measured"
                or data.get("seed_review_default") != "review_required"
                or data.get("evidence_policy")
                != "verified-evidence-snapshot-required-for-mutations"
                or data.get("review_expiry_policy") != "not_configured"
                or not isinstance(data.get("root_initialized"), bool)
                or (event_count == 0 and latest_event_id is not None)
                or (event_count > 0 and not _valid_public_id(latest_event_id))
                or (event_count > 0 and data.get("root_initialized") is not True)
            ):
                raise CheckFailure("entity governance status was unavailable or overstated")
            self._entity_governance_event_count = event_count
            return {
                "storage_status": "available",
                "integrity_status": "verified",
                "mutation_status": "ready",
                "event_count": event_count,
                "root_initialized": data["root_initialized"],
                "accuracy_claim": "not_measured",
                "worm_status": "unavailable",
            }

        def validate_catalog(payload: Any, _response: HttpResponse) -> dict[str, Any]:
            data = _require_object(payload, "entity governance catalog")
            approved = _require_list(
                data.get("approved_entities"), "approved governed entities"
            )
            review_required = _require_list(
                data.get("review_required_entities"), "entity review queue"
            )
            rejected = _require_list(
                data.get("rejected_entity_ids"), "rejected governed entities"
            )
            merges = _require_list(data.get("merge_decisions"), "entity merges")
            splits = _require_list(data.get("split_decisions"), "entity splits")
            event_count = _require_nonnegative_int(
                data.get("event_count"), "entity catalog event_count"
            )
            assurance = _require_object(data.get("assurance"), "entity assurance")
            if (
                data.get("schema_version") != "entity-governance-catalog-v2"
                or data.get("accuracy_claim") != "not_measured"
                or data.get("projection_policy") != "approved-and-not-retracted-only"
                or data.get("review_expiry_policy") != "not_configured"
                or data.get("seed_inventory_scope")
                != "bounded-public-search-facade-probes"
                or assurance
                != {
                    "worm": "unavailable",
                    "digital_signature": "unavailable",
                    "institutional_directory": "unavailable",
                }
                or self._entity_governance_event_count is None
                or event_count != self._entity_governance_event_count
            ):
                raise CheckFailure("entity governance catalog contract was contradictory")
            entity_ids: set[str] = set()
            for raw in approved:
                item = _require_object(raw, "approved governed entity")
                entity_id = item.get("entity_id")
                if (
                    not isinstance(entity_id, str)
                    or not entity_id.startswith("urn:globemind:entity:")
                    or item.get("review_status") != "approved"
                    or entity_id in entity_ids
                ):
                    raise CheckFailure("approved entity projection was invalid")
                entity_ids.add(entity_id)
            for raw in review_required:
                item = _require_object(raw, "entity review queue item")
                entity_id = item.get("entity_id")
                if (
                    not isinstance(entity_id, str)
                    or not entity_id.startswith("urn:globemind:entity:")
                    or item.get("review_status") != "review_required"
                    or item.get("accuracy_claim") != "not_measured"
                    or entity_id in entity_ids
                ):
                    raise CheckFailure("entity review queue was invalid")
                entity_ids.add(entity_id)
            for entity_id in rejected:
                if (
                    not isinstance(entity_id, str)
                    or not entity_id.startswith("urn:globemind:entity:")
                    or entity_id in entity_ids
                ):
                    raise CheckFailure("rejected entity projection was invalid")
                entity_ids.add(entity_id)
            return {
                "approved_count": len(approved),
                "review_required_count": len(review_required),
                "rejected_count": len(rejected),
                "merge_count": len(merges),
                "split_count": len(splits),
                "event_count": event_count,
                "accuracy_claim": "not_measured",
            }

        self._json_check(
            "entity_governance_status",
            "entity_governance",
            "GET",
            "/api/entity-governance/status",
            validate_status,
            headers=self._authorization_headers,
        )
        self._json_check(
            "entity_governance_catalog",
            "entity_governance",
            "GET",
            "/api/entity-governance/catalog",
            validate_catalog,
            headers=self._authorization_headers,
        )

    def _run_public_surface_checks(self) -> None:
        def validate_stats(payload: Any, _response: HttpResponse) -> dict[str, Any]:
            data = _require_object(payload, "dashboard stats")
            total = _require_nonnegative_int(data.get("total_news"), "total_news")
            languages = _require_list(data.get("language_stats"), "language_stats")
            return {"total_news": total, "language_count": len(languages)}

        def validate_options(payload: Any, _response: HttpResponse) -> dict[str, Any]:
            data = _require_object(payload, "search options")
            languages = _require_list(data.get("language_options"), "language_options")
            return {"language_option_count": len(languages)}

        def validate_financial(payload: Any, _response: HttpResponse) -> dict[str, Any]:
            data = _require_object(payload, "financial dashboard")
            bars = _require_list(data.get("bars"), "financial bars")
            if data.get("mode") not in {"live", "artlist"}:
                raise CheckFailure("financial dashboard mode was not recognized")
            return {"mode": data["mode"], "bar_count": len(bars)}

        def validate_financial_triage(
            payload: Any, _response: HttpResponse
        ) -> dict[str, Any]:
            data = _require_object(payload, "financial alert data")
            _require_list(data.get("rules"), "financial alert rules")
            history = _require_list(data.get("history"), "financial alert history")
            if len(history) > 50:
                raise CheckFailure("financial alert history exceeded its public bound")
            statuses = {
                "open",
                "acknowledged",
                "escalated",
                "false_positive",
                "resolved",
            }
            expected_keys = {
                "schema_version",
                "alert_event_id",
                "status",
                "has_audit",
                "reviewed",
                "transition_count",
                "last_transition_at",
                "last_event_id",
                "last_event_sha256",
                "operational_limitations",
                "historical",
                "mutations_enabled",
            }
            status_counts = {status: 0 for status in statuses}
            for index, raw in enumerate(history):
                row = _require_object(raw, f"financial alert history row {index}")
                stack: list[tuple[Any, int]] = [(row, 0)]
                nodes = 0
                while stack:
                    current, depth = stack.pop()
                    nodes += 1
                    if nodes > 2_000 or depth > 12:
                        raise CheckFailure(
                            "financial alert triage exceeded structural safety limits"
                        )
                    if isinstance(current, Mapping):
                        for raw_key, child in current.items():
                            key = re.sub(
                                r"(?<=[a-z0-9])(?=[A-Z])", "_", str(raw_key)
                            )
                            normalized = key.strip().lower().replace("-", "_")
                            if normalized in {
                                "actor",
                                "actor_user_id",
                                "audit",
                                "audit_events",
                                "escalation_target_role",
                                "false_positive_classification",
                                "postmortem_outcome",
                                "reason",
                                "reason_length",
                                "reason_sha256",
                                "user",
                                "user_id",
                                "username",
                            }:
                                raise CheckFailure(
                                    "financial alert triage exposed a forbidden sensitive field"
                                )
                            stack.append((child, depth + 1))
                    elif isinstance(current, list):
                        stack.extend((child, depth + 1) for child in current)
                triage = _require_object(
                    row.get("triage"), f"financial alert triage row {index}"
                )
                if set(triage) != expected_keys:
                    raise CheckFailure(
                        "financial alert triage exposed unexpected fields"
                    )
                alert_id = triage.get("alert_event_id")
                if (
                    triage.get("schema_version")
                    != "financial-alert-triage-status-v1"
                    or not isinstance(alert_id, str)
                    or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,299}", alert_id)
                    is None
                    or str(row.get("id") or "") != alert_id
                ):
                    raise CheckFailure("financial alert triage identity was invalid")
                status = triage.get("status")
                if status not in statuses:
                    raise CheckFailure("financial alert triage status was invalid")
                has_audit = triage.get("has_audit")
                reviewed = triage.get("reviewed")
                historical = triage.get("historical")
                mutations_enabled = triage.get("mutations_enabled")
                if any(
                    type(value) is not bool
                    for value in (has_audit, reviewed, historical, mutations_enabled)
                ) or historical == mutations_enabled:
                    raise CheckFailure("financial alert triage state was contradictory")
                transitions = _require_nonnegative_int(
                    triage.get("transition_count"),
                    "financial alert triage transition_count",
                )
                if transitions > 8:
                    raise CheckFailure("financial alert triage transition bound was exceeded")
                last_at = triage.get("last_transition_at")
                last_id = triage.get("last_event_id")
                last_sha = triage.get("last_event_sha256")
                last_values_are_null = (
                    last_at is None and last_id is None and last_sha is None
                )
                last_values_are_valid = (
                    isinstance(last_at, str)
                    and _valid_utc_timestamp(last_at)
                    and isinstance(last_id, str)
                    and re.fullmatch(
                        r"fat-[0-9]{8}T[0-9]{12}Z-[0-9a-f]{16}", last_id
                    )
                    is not None
                    and isinstance(last_sha, str)
                    and re.fullmatch(r"[0-9a-f]{64}", last_sha) is not None
                )
                if not (last_values_are_null or last_values_are_valid):
                    raise CheckFailure("financial alert triage event reference was invalid")
                if (
                    (not has_audit and (status != "open" or transitions or reviewed))
                    or (not has_audit and not last_values_are_null)
                    or (has_audit and (status == "open" or transitions < 1))
                    or (has_audit and not last_values_are_valid)
                    or (reviewed and status not in {"resolved", "false_positive"})
                ):
                    raise CheckFailure("financial alert triage state was contradictory")
                if triage.get("operational_limitations") != {
                    "sla": "unavailable",
                    "notification_delivery": "not_configured",
                    "institutional_incident_system": "not_configured",
                }:
                    raise CheckFailure(
                        "financial alert triage overstated operational capability"
                    )
                status_counts[str(status)] += 1
            return {
                "history_count": len(history),
                "triage_state": "observed" if history else "not_observed",
                "status_counts": status_counts,
                "mutations_exercised": False,
                "sla_state": "unavailable",
            }

        def validate_ground_news(payload: Any, _response: HttpResponse) -> dict[str, Any]:
            data = _require_object(payload, "Ground News list")
            stories = _require_list(data.get("stories"), "Ground News stories")
            return {
                "story_count": len(stories),
                "total": _optional_nonnegative_int(data.get("total")),
            }

        def validate_opinion_quality(payload: Any, _response: HttpResponse) -> dict[str, Any]:
            data = _require_object(payload, "opinion quality")
            if data.get("ok") is not True or not isinstance(data.get("method_version"), str):
                raise CheckFailure("opinion quality contract was incomplete")
            _require_object(data.get("freshness"), "opinion quality freshness")
            if data.get("status") not in {"healthy", "degraded"}:
                raise CheckFailure("opinion quality status was not recognized")
            return {"ok": True, "business_status": data["status"]}

        def validate_data_catalog(payload: Any, _response: HttpResponse) -> dict[str, Any]:
            data = _require_object(payload, "data governance catalog")
            if (
                data.get("schema_version") != "data-governance-catalog-v1"
                or data.get("contract_version") != "1.0.0"
            ):
                raise CheckFailure("data catalog identity did not match the V1 contract")
            if data.get("available") is not True:
                raise CheckFailure("data catalog was unavailable")
            if data.get("catalog_status") != "ready":
                raise CheckFailure("formal data catalog registration was incomplete")

            registry_sources = _require_object(
                data.get("registry_sources"), "data catalog registry sources"
            )
            if (
                registry_sources.get("owner_registry") != "verified"
                or registry_sources.get("source_catalog") != "verified"
            ):
                raise CheckFailure("data catalog registry evidence was unavailable")

            summary = _require_object(data.get("summary"), "data catalog summary")
            records = _require_list(data.get("records"), "data catalog records")
            record_count = _require_nonnegative_int(
                summary.get("record_count"), "data catalog record_count"
            )
            eligible_count = _require_nonnegative_int(
                summary.get("eligible_count"), "data catalog eligible_count"
            )
            blocked_count = _require_nonnegative_int(
                summary.get("blocked_count"), "data catalog blocked_count"
            )
            if (
                record_count == 0
                or len(records) != record_count
                or eligible_count != record_count
                or blocked_count != 0
                or summary.get("formal_release_status") != "ready"
            ):
                raise CheckFailure("data catalog did not satisfy the V1 release gate")

            kinds: set[str] = set()
            record_ids: set[str] = set()
            observed_kind_counts = {"dataset": 0, "source": 0, "model": 0}
            for index, raw_record in enumerate(records):
                record = _require_object(raw_record, f"data catalog record {index}")
                record_id = record.get("record_id")
                kind = record.get("kind")
                if (
                    not isinstance(record_id, str)
                    or not isinstance(kind, str)
                    or kind not in {"dataset", "source", "model"}
                    or re.fullmatch(
                        rf"{kind}\.[a-z0-9][a-z0-9_.-]*",
                        record_id,
                    ) is None
                ):
                    raise CheckFailure("data catalog record identity was invalid")
                if record_id in record_ids:
                    raise CheckFailure("data catalog contained duplicate record identities")
                record_ids.add(record_id)
                status = _require_object(
                    record.get("status"), f"data catalog record status {index}"
                )
                if (
                    status.get("state") != "eligible"
                    or status.get("release_eligible") is not True
                    or status.get("research_ready") is not True
                    or _require_list(
                        status.get("reason_codes"),
                        f"data catalog record reason codes {index}",
                    )
                ):
                    raise CheckFailure("data catalog contained a blocked formal record")
                kinds.add(str(kind))
                observed_kind_counts[str(kind)] += 1
            if any(
                summary.get(f"{kind}_count") != count
                for kind, count in observed_kind_counts.items()
            ):
                raise CheckFailure("data catalog kind counts did not match its records")
            return {
                "record_count": record_count,
                "eligible_count": eligible_count,
                "blocked_count": blocked_count,
                "record_kind_count": len(kinds),
                "formal_release_status": "ready",
            }

        self._json_check(
            "dashboard_stats", "public_api", "GET", "/api/dashboard/stats", validate_stats
        )
        self._json_check(
            "search_options",
            "public_api",
            "GET",
            "/api/dashboard/search/options",
            validate_options,
        )
        self._json_check(
            "financial_dashboard",
            "public_api",
            "GET",
            "/api/financial/dashboard",
            validate_financial,
        )
        self._json_check(
            "financial_alert_triage",
            "public_api",
            "GET",
            "/api/financial/alert/data",
            validate_financial_triage,
        )
        self._json_check(
            "ground_news_list",
            "public_api",
            "GET",
            (
                "/api/story-graph/ground-news/list?page=1&page_size=1&quality=all"
                "&date_days=0&min_sources=1&min_articles=1"
            ),
            validate_ground_news,
            display_endpoint="/api/story-graph/ground-news/list",
        )
        self._json_check(
            "opinion_quality",
            "public_api",
            "GET",
            "/api/opinion/quality",
            validate_opinion_quality,
        )
        self._json_check(
            "data_governance_catalog",
            "data_governance",
            "GET",
            "/api/data-governance/catalog",
            validate_data_catalog,
        )

    def _run_graph_checks(self) -> None:
        def validate_universe(payload: Any, _response: HttpResponse) -> dict[str, Any]:
            data = _require_object(payload, "graph universe")
            macros = _require_list(data.get("macros"), "graph macros")
            _require_list(data.get("unclustered_news"), "graph unclustered_news")
            self._graph_macro = None
            self._graph_micro = None
            for raw_macro in macros:
                if not isinstance(raw_macro, dict):
                    continue
                micros = raw_macro.get("micro_events")
                if not isinstance(micros, list) or not micros:
                    continue
                macro_id = raw_macro.get("macro_id") or raw_macro.get("storyline_id")
                for raw_micro in micros:
                    if not isinstance(raw_micro, dict):
                        continue
                    micro_id = raw_micro.get("event_id") or raw_micro.get("chain_id")
                    if _valid_public_id(macro_id) and _valid_public_id(micro_id):
                        self._graph_macro = raw_macro
                        self._graph_micro = raw_micro
                        break
                if self._graph_macro is not None:
                    break
            return {
                "macro_count": len(macros),
                "linked_sample_available": self._graph_macro is not None,
            }

        self._json_check(
            "graph_universe",
            "graph",
            "GET",
            (
                "/api/graph/universe?macro_limit=100&micro_per_macro=5"
                "&unclustered_limit=0&fill_ambient=false&news_per_micro=0"
            ),
            validate_universe,
            display_endpoint="/api/graph/universe",
        )

        if self._graph_macro is None or self._graph_micro is None:
            self._failed_synthetic(
                "graph_sample_availability",
                "graph",
                "GET",
                "/api/graph/universe",
                "current hierarchy had no linked L3/L2 sample; production acceptance requires data",
            )
            for check_id in _GRAPH_DEPENDENT_CHECKS:
                self._blocked(
                    check_id,
                    "graph",
                    _graph_method(check_id),
                    _graph_endpoint_template(check_id),
                    "linked graph sample was unavailable",
                )
            return

        macro_id = str(self._graph_macro.get("macro_id") or self._graph_macro["storyline_id"])
        micro_id = str(self._graph_micro.get("event_id") or self._graph_micro["chain_id"])
        title = str(self._graph_macro.get("title") or "").strip()
        if not title:
            title = str(self._graph_macro.get("macro_key") or "").strip()
        if not title:
            title = macro_id
        query = title[:160]
        encoded_macro = urllib.parse.quote(macro_id, safe="")
        encoded_micro = urllib.parse.quote(micro_id, safe="")
        encoded_query = urllib.parse.quote(query, safe="")
        self._passed_synthetic(
            "graph_sample_availability",
            "graph",
            "GET",
            "/api/graph/universe",
            {
                "macro_id_fingerprint": _identifier_fingerprint(macro_id),
                "micro_id_fingerprint": _identifier_fingerprint(micro_id),
            },
        )

        def validate_macro(payload: Any, _response: HttpResponse) -> dict[str, Any]:
            data = _require_object(payload, "graph macro")
            if str(data.get("macro_id") or data.get("storyline_id") or "") != macro_id:
                raise CheckFailure("graph macro identity did not match the sampled universe item")
            return {
                "identity_match": True,
                "article_count": _optional_nonnegative_int(data.get("article_count")),
            }

        def validate_briefing(payload: Any, _response: HttpResponse) -> dict[str, Any]:
            data = _require_object(payload, "graph briefing")
            if str(data.get("storyline_id") or "") != macro_id:
                raise CheckFailure("graph briefing identity did not match the sampled macro")
            _require_object(data.get("macro"), "graph briefing macro")
            return {
                "identity_match": True,
                "has_sentiment_distribution": isinstance(data.get("sentiment_distribution"), list),
            }

        def validate_micros(payload: Any, _response: HttpResponse) -> dict[str, Any]:
            data = _require_object(payload, "graph macro micros")
            items = _require_list(data.get("items"), "graph macro micro items")
            found = any(
                isinstance(item, dict)
                and str(item.get("event_id") or item.get("chain_id") or "") == micro_id
                for item in items
            )
            if not found:
                raise CheckFailure("sampled L2 chain was absent from its L3 macro listing")
            return {"item_count": len(items), "sample_identity_match": True}

        def validate_tree(payload: Any, _response: HttpResponse) -> dict[str, Any]:
            data = _require_object(payload, "graph macro tree")
            _require_object(data.get("macro"), "graph tree macro")
            micros = _require_list(data.get("micros"), "graph tree micros")
            if not any(
                isinstance(item, dict)
                and str(item.get("event_id") or item.get("chain_id") or "") == micro_id
                for item in micros
            ):
                raise CheckFailure("sampled L2 chain was absent from the L3 tree")
            return {"micro_count": len(micros), "sample_identity_match": True}

        def validate_search(payload: Any, _response: HttpResponse) -> dict[str, Any]:
            data = _require_object(payload, "graph macro search")
            items = _require_list(data.get("items"), "graph macro search items")
            if not any(
                isinstance(item, dict)
                and str(item.get("macro_id") or item.get("storyline_id") or "") == macro_id
                for item in items
            ):
                raise CheckFailure("graph search did not find the sampled L3 macro")
            return {"result_count": len(items), "sample_identity_match": True}

        def validate_micro(payload: Any, _response: HttpResponse) -> dict[str, Any]:
            data = _require_object(payload, "graph micro")
            if str(data.get("event_id") or data.get("chain_id") or "") != micro_id:
                raise CheckFailure("graph micro identity did not match the sampled universe item")
            return {
                "identity_match": True,
                "article_count": _optional_nonnegative_int(data.get("article_count")),
            }

        def validate_news(payload: Any, _response: HttpResponse) -> dict[str, Any]:
            data = _require_object(payload, "graph micro news")
            if str(data.get("event_id") or "") != micro_id:
                raise CheckFailure("graph micro news identity did not match the sampled L2 chain")
            items = _require_list(data.get("items"), "graph micro news items")
            return {
                "item_count": len(items),
                "total": _require_nonnegative_int(data.get("total"), "graph news total"),
            }

        def validate_batch(payload: Any, _response: HttpResponse) -> dict[str, Any]:
            data = _require_object(payload, "graph news batch")
            by_event = _require_object(data.get("by_event"), "graph news batch by_event")
            if micro_id not in by_event or not isinstance(by_event[micro_id], list):
                raise CheckFailure("graph news batch omitted the sampled L2 chain")
            return {"event_count": len(by_event), "sample_item_count": len(by_event[micro_id])}

        self._json_check(
            "graph_macro",
            "graph",
            "GET",
            f"/api/graph/macro/{encoded_macro}",
            validate_macro,
            display_endpoint="/api/graph/macro/{macro_id}",
        )
        self._json_check(
            "graph_macro_briefing",
            "graph",
            "GET",
            f"/api/graph/macro/{encoded_macro}/briefing",
            validate_briefing,
            display_endpoint="/api/graph/macro/{macro_id}/briefing",
        )
        self._json_check(
            "graph_macro_micros",
            "graph",
            "GET",
            f"/api/graph/macro/{encoded_macro}/micros?limit=20&offset=0",
            validate_micros,
            display_endpoint="/api/graph/macro/{macro_id}/micros",
        )
        self._json_check(
            "graph_macro_tree",
            "graph",
            "GET",
            f"/api/graph/macro/{encoded_macro}/tree?micro_limit=20",
            validate_tree,
            display_endpoint="/api/graph/macro/{macro_id}/tree",
        )
        self._json_check(
            "graph_macro_search",
            "graph",
            "GET",
            f"/api/graph/macros/search?q={encoded_query}&limit=80",
            validate_search,
            display_endpoint="/api/graph/macros/search",
        )
        self._json_check(
            "graph_micro",
            "graph",
            "GET",
            f"/api/graph/micro/{encoded_micro}",
            validate_micro,
            display_endpoint="/api/graph/micro/{micro_id}",
        )
        self._json_check(
            "graph_micro_news",
            "graph",
            "GET",
            f"/api/graph/micro/{encoded_micro}/news?page=1&page_size=5&brief=true",
            validate_news,
            display_endpoint="/api/graph/micro/{micro_id}/news",
        )
        self._json_check(
            "graph_micro_news_batch",
            "graph",
            "POST",
            "/api/graph/micros/news-batch",
            validate_batch,
            json_body={"event_ids": [micro_id], "limit_per": 5},
        )

    def _run_v11_checks(self) -> None:
        if self._graph_macro is None or self._graph_micro is None:
            for check_id, endpoint in (
                ("v11_search_current", "/api/dashboard/search/v11-clusters"),
                ("v11_l3_children", "/api/dashboard/search/v11-clusters/{item_id}/children"),
                ("v11_l2_children", "/api/dashboard/search/v11-clusters/{item_id}/children"),
                ("v11_l1_children", "/api/dashboard/search/v11-clusters/{item_id}/children"),
                ("article_evidence_chain", "/api/article/{news_id}/reader"),
            ):
                self._blocked(
                    check_id,
                    "v11",
                    "POST" if "search" in check_id else "GET",
                    endpoint,
                    "linked graph sample was unavailable",
                )
            return

        macro_id = str(self._graph_macro.get("macro_id") or self._graph_macro["storyline_id"])
        query = str(
            self._graph_macro.get("title") or self._graph_macro.get("macro_key") or macro_id
        ).strip()[:160]
        encoded_macro = urllib.parse.quote(macro_id, safe="")

        def validate_search(payload: Any, _response: HttpResponse) -> dict[str, Any]:
            data = _require_object(payload, "V11 search")
            items = _require_list(data.get("items"), "V11 search items")
            if not any(
                isinstance(item, dict) and str(item.get("id") or "") == macro_id for item in items
            ):
                raise CheckFailure("V11 current-table search did not find the sampled L3 macro")
            return {"result_count": len(items), "sample_identity_match": True, "level": "macro"}

        def validate_l3(payload: Any, _response: HttpResponse) -> dict[str, Any]:
            data = _require_object(payload, "V11 L3 children")
            items = _require_list(data.get("items"), "V11 L3 child items")
            if data.get("parent_level") != "l3" or data.get("child_level") != "l2":
                raise CheckFailure("V11 L3 response did not use current hierarchy labels")
            first = next(
                (
                    item
                    for item in items
                    if isinstance(item, dict) and _valid_public_id(item.get("id"))
                ),
                None,
            )
            if first is None:
                raise CheckFailure("V11 L3 response had no usable L2 child")
            self._v11_l2_id = str(first["id"])
            return {
                "item_count": len(items),
                "parent_level": "l3",
                "child_level": "l2",
                "sampled_l2_fingerprint": _identifier_fingerprint(self._v11_l2_id),
            }

        def validate_l2(payload: Any, _response: HttpResponse) -> dict[str, Any]:
            data = _require_object(payload, "V11 L2 children")
            items = _require_list(data.get("items"), "V11 L2 child items")
            if data.get("parent_level") != "l2" or data.get("child_level") != "l1":
                raise CheckFailure("V11 L2 response did not use current hierarchy labels")
            if (
                not items
                or not isinstance(items[0], dict)
                or not _valid_public_id(items[0].get("id"))
            ):
                raise CheckFailure("V11 L2 response had no usable L1 child")
            self._v11_l1_id = str(items[0]["id"])
            return {"item_count": len(items), "parent_level": "l2", "child_level": "l1"}

        def validate_l1(payload: Any, _response: HttpResponse) -> dict[str, Any]:
            data = _require_object(payload, "V11 L1 children")
            items = _require_list(data.get("items"), "V11 L1 child items")
            if data.get("parent_level") != "l1" or data.get("child_level") != "news":
                raise CheckFailure("V11 L1 response did not use current hierarchy labels")
            if not items:
                raise CheckFailure("V11 L1 response had no linked news")
            first = next(
                (
                    item
                    for item in items
                    if isinstance(item, dict)
                    and isinstance(item.get("id"), int)
                    and not isinstance(item.get("id"), bool)
                    and item["id"] > 0
                ),
                None,
            )
            if first is None:
                raise CheckFailure("V11 L1 response had no usable news identifier")
            self._v11_news_id = int(first["id"])
            return {"item_count": len(items), "parent_level": "l1", "child_level": "news"}

        def validate_evidence_chain(payload: Any, _response: HttpResponse) -> dict[str, Any]:
            if self._v11_news_id is None:
                raise CheckFailure("evidence sample identity was unavailable")
            data = _require_object(payload, "article reader")
            news = _require_object(data.get("news"), "article reader news")
            if news.get("id") != self._v11_news_id:
                raise CheckFailure("article reader returned a different news identity")
            body_paragraphs = _article_body_paragraphs(news.get("body"))
            if not body_paragraphs:
                raise CheckFailure("article reader did not return a verifiable body")
            analysis = _require_object(data.get("analysis"), "article reader analysis")
            chain = _require_object(
                analysis.get("evidence_chain"), "article evidence chain"
            )
            if (
                chain.get("schema_version") != "article-evidence-v1"
                or chain.get("article_id") != self._v11_news_id
            ):
                raise CheckFailure("article evidence identity did not match the V1 contract")
            paragraph_count = _require_nonnegative_int(
                chain.get("paragraph_count"), "article evidence paragraph_count"
            )
            if paragraph_count != len(body_paragraphs):
                raise CheckFailure("article evidence paragraph count did not match the body")
            claims = _require_list(chain.get("claims"), "article evidence claims")
            if not claims:
                raise CheckFailure("article evidence chain had no claims")

            judgment_count = 0
            cited_claim_count = 0
            citation_count = 0
            for index, raw_claim in enumerate(claims):
                claim = _require_object(raw_claim, f"article evidence claim {index}")
                claim_type = claim.get("claim_type")
                if not isinstance(claim_type, str) or claim_type not in {
                    "information",
                    "hypothesis",
                    "judgment",
                    "unknown",
                    "indicator",
                }:
                    raise CheckFailure("article evidence claim type was not recognized")
                if not all(
                    isinstance(claim.get(field), str) and claim[field].strip()
                    for field in ("id", "text", "source")
                ):
                    raise CheckFailure("article evidence claim metadata was incomplete")
                evidence_status = claim.get("evidence_status")
                citations = _require_list(
                    claim.get("citations"), f"article evidence citations {index}"
                )
                if evidence_status == "available":
                    if not citations:
                        raise CheckFailure("available claim had no paragraph citation")
                    cited_claim_count += 1
                    for citation_index, raw_citation in enumerate(citations):
                        citation = _require_object(
                            raw_citation,
                            f"article evidence citation {index}:{citation_index}",
                        )
                        paragraph_number = citation.get("paragraph_number")
                        expected_anchor = (
                            f"article-{self._v11_news_id}-paragraph-{paragraph_number}"
                        )
                        matched_text = citation.get("matched_text")
                        excerpt = citation.get("excerpt")
                        relation = citation.get("relation")
                        relation_is_valid = isinstance(relation, str) and relation in {
                            "input",
                            "support",
                            "oppose",
                            "background",
                        }
                        if (
                            citation.get("article_id") != self._v11_news_id
                            or citation.get("status") != "available"
                            or not isinstance(paragraph_number, int)
                            or isinstance(paragraph_number, bool)
                            or paragraph_number < 1
                            or paragraph_number > paragraph_count
                            or citation.get("anchor_id") != expected_anchor
                            or not relation_is_valid
                            or not isinstance(matched_text, str)
                            or not matched_text.strip()
                            or not isinstance(excerpt, str)
                            or not excerpt.strip()
                        ):
                            raise CheckFailure("article paragraph citation was invalid")
                        paragraph = body_paragraphs[paragraph_number - 1]
                        excerpt_body = excerpt.removeprefix("…").removesuffix("…")
                        if (
                            matched_text not in paragraph
                            or not excerpt_body
                            or excerpt_body not in paragraph
                        ):
                            raise CheckFailure(
                                "article paragraph citation did not match the reader body"
                            )
                        citation_count += 1
                elif evidence_status == "unavailable":
                    unavailable_reason = claim.get("unavailable_reason")
                    if (
                        citations
                        or not isinstance(unavailable_reason, str)
                        or not unavailable_reason.strip()
                    ):
                        raise CheckFailure("unavailable claim did not explain missing evidence")
                else:
                    raise CheckFailure("article evidence status was not recognized")
                if claim_type == "judgment":
                    judgment_count += 1
                    if evidence_status != "available":
                        raise CheckFailure("sampled article judgment lacked paragraph evidence")

            provenance = _require_object(
                chain.get("provenance"), "article evidence provenance"
            )
            body_hash = provenance.get("response_body_sha256")
            expected_body_hash = hashlib.sha256(
                "\n\n".join(body_paragraphs).encode("utf-8")
            ).hexdigest()
            if (
                provenance.get("body_status") != "available"
                or provenance.get("hash_scope") != "normalized-display-body"
                or not isinstance(body_hash, str)
                or re.fullmatch(r"[0-9a-f]{64}", body_hash) is None
                or body_hash != expected_body_hash
            ):
                raise CheckFailure("article evidence body provenance was incomplete")
            if judgment_count == 0 or cited_claim_count == 0:
                raise CheckFailure("sampled article had no paragraph-backed judgment")
            return {
                "paragraph_count": paragraph_count,
                "claim_count": len(claims),
                "judgment_count": judgment_count,
                "cited_claim_count": cited_claim_count,
                "citation_count": citation_count,
                "body_hash_present": True,
            }

        self._json_check(
            "v11_search_current",
            "v11",
            "POST",
            "/api/dashboard/search/v11-clusters",
            validate_search,
            json_body={"keyword": query, "level": "macro", "page": 1, "page_size": 100},
        )
        self._json_check(
            "v11_l3_children",
            "v11",
            "GET",
            f"/api/dashboard/search/v11-clusters/{encoded_macro}/children?level=l3&page=1&page_size=20",
            validate_l3,
            display_endpoint="/api/dashboard/search/v11-clusters/{item_id}/children?level=l3",
        )
        if self._v11_l2_id is None:
            self._blocked(
                "v11_l2_children",
                "v11",
                "GET",
                "/api/dashboard/search/v11-clusters/{item_id}/children?level=l2",
                "V11 L3 response did not yield a usable L2 identifier",
            )
            self._blocked(
                "v11_l1_children",
                "v11",
                "GET",
                "/api/dashboard/search/v11-clusters/{item_id}/children?level=l1",
                "V11 L2 request was blocked",
            )
            self._blocked(
                "article_evidence_chain",
                "evidence",
                "GET",
                "/api/article/{news_id}/reader",
                "V11 L1 request was blocked",
            )
            return
        encoded_v11_l2 = urllib.parse.quote(self._v11_l2_id, safe="")
        self._json_check(
            "v11_l2_children",
            "v11",
            "GET",
            f"/api/dashboard/search/v11-clusters/{encoded_v11_l2}/children?level=l2&page=1&page_size=20",
            validate_l2,
            display_endpoint="/api/dashboard/search/v11-clusters/{item_id}/children?level=l2",
        )
        if self._v11_l1_id is None:
            self._blocked(
                "v11_l1_children",
                "v11",
                "GET",
                "/api/dashboard/search/v11-clusters/{item_id}/children?level=l1",
                "V11 L2 response did not yield a usable L1 identifier",
            )
            self._blocked(
                "article_evidence_chain",
                "evidence",
                "GET",
                "/api/article/{news_id}/reader",
                "V11 L1 request was blocked",
            )
            return
        encoded_l1 = urllib.parse.quote(self._v11_l1_id, safe="")
        self._json_check(
            "v11_l1_children",
            "v11",
            "GET",
            f"/api/dashboard/search/v11-clusters/{encoded_l1}/children?level=l1&page=1&page_size=5",
            validate_l1,
            display_endpoint="/api/dashboard/search/v11-clusters/{item_id}/children?level=l1",
        )
        if self._v11_news_id is None:
            self._blocked(
                "article_evidence_chain",
                "evidence",
                "GET",
                "/api/article/{news_id}/reader",
                "V11 L1 response did not yield a usable news identifier",
            )
            return
        self._json_check(
            "article_evidence_chain",
            "evidence",
            "GET",
            f"/api/article/{self._v11_news_id}/reader",
            validate_evidence_chain,
            display_endpoint="/api/article/{news_id}/reader",
        )

    def _run_legacy_retirement_checks(self) -> None:
        for endpoint in LEGACY_OPINION_ENDPOINTS:
            check_id = "legacy_" + endpoint.rsplit("/", 1)[-1].replace("-", "_")

            def validate_retired(
                payload: Any,
                _response: HttpResponse,
                expected_endpoint: str = endpoint,
            ) -> dict[str, Any]:
                data = _require_object(payload, "retired endpoint response")
                if (
                    data.get("ok") is not False
                    or data.get("code") != "endpoint_retired"
                    or data.get("status") != 410
                    or data.get("endpoint") != expected_endpoint
                    or data.get("retired_in") != "v0.10"
                ):
                    raise CheckFailure(
                        "legacy endpoint did not use the stable V0.10 retirement contract"
                    )
                _require_list(data.get("alternatives"), "retirement alternatives")
                return {"retired": True, "retired_in": "v0.10", "message_redacted": True}

            self._json_check(
                check_id,
                "legacy_retirement",
                "GET",
                endpoint,
                validate_retired,
                expected_status=410,
            )

    def _json_check(
        self,
        check_id: str,
        category: str,
        method: str,
        path: str,
        validator: Validator,
        *,
        expected_status: int = 200,
        json_body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        display_endpoint: str | None = None,
    ) -> None:
        self._http_check(
            check_id,
            category,
            method,
            path,
            expected_status,
            "json",
            validator,
            json_body=json_body,
            headers=headers,
            display_endpoint=display_endpoint,
        )

    def _html_check(
        self,
        check_id: str,
        category: str,
        method: str,
        path: str,
        validator: Validator,
    ) -> None:
        self._http_check(check_id, category, method, path, 200, "html", validator)

    def _bytes_check(
        self,
        check_id: str,
        category: str,
        method: str,
        path: str,
        validator: Validator,
        *,
        display_endpoint: str,
        max_body_bytes: int,
    ) -> None:
        self._http_check(
            check_id,
            category,
            method,
            path,
            200,
            "bytes",
            validator,
            display_endpoint=display_endpoint,
            max_body_bytes=max_body_bytes,
        )

    def _http_check(
        self,
        check_id: str,
        category: str,
        method: str,
        path: str,
        expected_status: int,
        payload_kind: str,
        validator: Validator,
        *,
        json_body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        display_endpoint: str | None = None,
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    ) -> None:
        endpoint = display_endpoint or _display_endpoint(path)
        base_result = {
            "check_id": check_id,
            "category": category,
            "required": True,
            "method": method,
            "endpoint": endpoint,
            "expected_status": expected_status,
        }
        try:
            response = self.client.request(
                method,
                path,
                json_body=json_body,
                headers=headers,
                max_body_bytes=max_body_bytes,
            )
        except ResponseTooLarge as exc:
            self.results.append(
                {
                    **base_result,
                    "outcome": "failed",
                    "actual_status": exc.status,
                    "duration_ms": round(exc.duration_ms, 2),
                    "response": _response_metadata(exc.headers, None),
                    "error": f"response exceeded the {exc.limit}-byte evidence limit",
                }
            )
            return
        except (NetworkFailure, ValueError) as exc:
            self.results.append(
                {
                    **base_result,
                    "outcome": "failed",
                    "actual_status": None,
                    "duration_ms": None,
                    "response": None,
                    "error": _safe_error(exc),
                }
            )
            return

        result = {
            **base_result,
            "actual_status": response.status,
            "duration_ms": round(response.duration_ms, 2),
            "response": _response_metadata(response.headers, response.body),
        }
        if response.status != expected_status:
            self.results.append(
                {
                    **result,
                    "outcome": "failed",
                    "error": "unexpected HTTP status",
                }
            )
            return

        try:
            payload = _decode_payload(payload_kind, response)
            observations = validator(payload, response)
        except (CheckFailure, UnicodeError, json.JSONDecodeError) as exc:
            self.results.append(
                {
                    **result,
                    "outcome": "failed",
                    "error": _safe_error(exc),
                }
            )
            return
        self.results.append(
            {
                **result,
                "outcome": "passed",
                "observations": observations,
            }
        )

    def _passed_synthetic(
        self,
        check_id: str,
        category: str,
        method: str,
        endpoint: str,
        observations: Mapping[str, Any],
    ) -> None:
        self.results.append(
            {
                "check_id": check_id,
                "category": category,
                "required": True,
                "method": method,
                "endpoint": endpoint,
                "expected_status": None,
                "actual_status": None,
                "duration_ms": 0.0,
                "response": None,
                "outcome": "passed",
                "observations": dict(observations),
            }
        )

    def _failed_synthetic(
        self,
        check_id: str,
        category: str,
        method: str,
        endpoint: str,
        error: str,
    ) -> None:
        self.results.append(
            {
                "check_id": check_id,
                "category": category,
                "required": True,
                "method": method,
                "endpoint": endpoint,
                "expected_status": None,
                "actual_status": None,
                "duration_ms": 0.0,
                "response": None,
                "outcome": "failed",
                "error": error,
            }
        )

    def _blocked(
        self,
        check_id: str,
        category: str,
        method: str,
        endpoint: str,
        reason: str,
    ) -> None:
        self.results.append(
            {
                "check_id": check_id,
                "category": category,
                "required": True,
                "method": method,
                "endpoint": endpoint,
                "expected_status": None,
                "actual_status": None,
                "duration_ms": None,
                "response": None,
                "outcome": "blocked",
                "error": reason,
            }
        )

    def _summarize(self) -> dict[str, int]:
        outcomes = {"passed": 0, "failed": 0, "blocked": 0, "degraded": 0, "skipped": 0}
        for result in self.results:
            outcome = str(result["outcome"])
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
        required_failed = sum(
            1
            for result in self.results
            if result.get("required") and result.get("outcome") != "passed"
        )
        return {
            "total": len(self.results),
            **outcomes,
            "required_passed": len(self.results) - required_failed,
            "required_failed": required_failed,
        }

    def _write_evidence(self, acceptance: Mapping[str, Any]) -> None:
        output_dir = self.output_dir
        if output_dir.is_symlink():
            raise ValueError("output directory must not be a symbolic link")
        if output_dir.exists():
            if not output_dir.is_dir():
                raise ValueError("output path exists and is not a directory")
            if any(output_dir.iterdir()):
                raise ValueError("output directory must be empty to preserve immutable evidence")
        else:
            output_dir.mkdir(parents=True, mode=0o750)
        os.chmod(output_dir, 0o750)
        checks_dir = output_dir / "checks"
        checks_dir.mkdir(mode=0o750)

        for index, result in enumerate(self.results, start=1):
            slug = SAFE_FILENAME_RE.sub("-", str(result["check_id"]).lower()).strip("-")
            _atomic_json_write(checks_dir / f"{index:03d}-{slug}.json", result)
        _atomic_json_write(output_dir / "acceptance.json", acceptance)


def normalize_base_url(value: str) -> str:
    candidate = value.strip()
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
    if isinstance(address, ipaddress.IPv4Address):
        is_loopback = address in ipaddress.IPv4Network("127.0.0.0/8")
    else:
        is_loopback = address == ipaddress.IPv6Address("::1")
    if not is_loopback:
        raise ValueError("base URL host must be a loopback IP address")
    host = f"[{address.compressed}]" if address.version == 6 else address.compressed
    return f"{parsed.scheme}://{host}:{port}"


def _decode_payload(kind: str, response: HttpResponse) -> Any:
    content_type = response.headers.get("content-type", "").lower()
    if kind == "json":
        if "application/json" not in content_type and "+json" not in content_type:
            raise CheckFailure("response content type was not JSON")
        return json.loads(response.body.decode("utf-8"))
    if kind == "html":
        if "text/html" not in content_type:
            raise CheckFailure("response content type was not HTML")
        return response.body.decode("utf-8")
    if kind == "bytes":
        return response.body
    raise ValueError(f"unsupported payload kind: {kind}")


def _response_metadata(headers: Mapping[str, str], body: bytes | None) -> dict[str, Any]:
    raw_content_type = str(headers.get("content-type") or "")
    media_type = raw_content_type.split(";", 1)[0].strip().lower()
    if re.fullmatch(r"[a-z0-9!#$&^_.+\-]+/[a-z0-9!#$&^_.+\-]+", media_type) is None:
        media_type = "invalid"
    cache_control = str(headers.get("cache-control") or "").lower()
    metadata: dict[str, Any] = {
        "content_type": media_type,
        "cache_policy": {
            "no_cache": "no-cache" in cache_control,
            "no_store": "no-store" in cache_control,
            "private": "private" in cache_control,
        },
    }
    if body is not None:
        metadata.update(
            {
                "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        )
    else:
        metadata.update({"bytes": None, "sha256": None})
    return metadata


def _require_release_identity(data: Mapping[str, Any], expected_build_id: str) -> dict[str, str]:
    release = _require_object(data.get("release"), "release identity")
    build_id = release.get("build_id")
    if build_id != expected_build_id:
        raise CheckFailure("release.build_id did not match the expected candidate build")
    version = release.get("version")
    git_sha = release.get("git_sha")
    if (
        not isinstance(version, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+\-]{0,63}", version) is None
        or not isinstance(git_sha, str)
        or re.fullmatch(r"[0-9a-f]{40,64}", git_sha) is None
    ):
        raise CheckFailure("release identity was incomplete")
    return {
        "version": version,
        "build_id": build_id,
        "git_sha": git_sha,
    }


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CheckFailure(f"{label} was not an object")
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise CheckFailure(f"{label} was not a list")
    return value


def _reject_runtime_catalog_secrets(value: Mapping[str, Any]) -> None:
    stack: list[tuple[Any, int]] = [(value, 0)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > 50_000 or depth > 32:
            raise CheckFailure("runtime catalog exceeded structural safety limits")
        if isinstance(current, Mapping):
            for raw_key, child in current.items():
                key = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(raw_key))
                normalized = key.strip().lower().replace("-", "_")
                if normalized in _FORBIDDEN_RUNTIME_CONTROL_KEYS:
                    raise CheckFailure("runtime catalog exposed executable control material")
                if normalized in _FORBIDDEN_RUNTIME_CATALOG_KEYS or normalized.endswith(
                    (
                        "_credential",
                        "_credential_path",
                        "_credentials",
                        "_credentials_path",
                        "_password",
                        "_password_path",
                        "_secret",
                        "_secret_path",
                        "_token",
                        "_token_path",
                    )
                ):
                    raise CheckFailure("runtime catalog exposed a forbidden sensitive field")
                stack.append((child, depth + 1))
        elif isinstance(current, list):
            stack.extend((child, depth + 1) for child in current)
        elif isinstance(current, str):
            lowered = current.lower()
            if any(
                fragment in lowered
                for fragment in _FORBIDDEN_RUNTIME_CATALOG_PATH_FRAGMENTS
            ):
                raise CheckFailure("runtime catalog exposed a forbidden credential path")


def _reject_service_level_sensitive_fields(value: Mapping[str, Any]) -> None:
    stack: list[tuple[Any, int]] = [(value, 0)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > 5_000 or depth > 16:
            raise CheckFailure("service-level response exceeded structural safety limits")
        if isinstance(current, Mapping):
            for raw_key, child in current.items():
                key = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(raw_key))
                normalized = key.strip().lower().replace("-", "_")
                if normalized in _FORBIDDEN_SERVICE_LEVEL_KEYS or normalized.endswith(
                    ("_password", "_secret", "_token", "_credential")
                ):
                    raise CheckFailure(
                        "service-level response exposed a forbidden sensitive field"
                    )
                stack.append((child, depth + 1))
        elif isinstance(current, list):
            stack.extend((child, depth + 1) for child in current)


def read_auth_token_file(path: Path) -> str:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("candidate auth token file must be an absolute canonical path")
    try:
        before = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError("candidate auth token file is unavailable or unsafe") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ValueError("candidate auth token file is unavailable or unsafe")
    if resolved != path:
        raise ValueError("candidate auth token file must be an absolute canonical path")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or (metadata.st_dev, metadata.st_ino) != (before.st_dev, before.st_ino)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or metadata.st_size < 32
            or metadata.st_size > MAX_AUTH_TOKEN_BYTES
        ):
            raise ValueError("candidate auth token file failed ownership or mode checks")
        chunks: list[bytes] = []
        remaining = MAX_AUTH_TOKEN_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        after = os.fstat(descriptor)
        if (
            (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            != (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
            )
            or len(content) != metadata.st_size
        ):
            raise ValueError("candidate auth token file changed while reading")
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError("candidate auth token file is unavailable or unsafe") from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    if len(content) > MAX_AUTH_TOKEN_BYTES:
        raise ValueError("candidate auth token file is too large")
    try:
        token = content.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError("candidate auth token file is not UTF-8") from exc
    if len(token) < 32 or any(character.isspace() for character in token):
        raise ValueError("candidate auth token file has an invalid token")
    return token


def _require_nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CheckFailure(f"{label} was not a non-negative integer")
    return value


def _optional_nonnegative_int(value: Any) -> int | None:
    if value is None:
        return None
    return _require_nonnegative_int(value, "optional count")


def _normalize_entry_asset(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        raise CheckFailure("frontend entry asset was not same-origin")
    if not parsed.path.startswith(("/assets/", "/v2/assets/")) or not parsed.path.endswith(".js"):
        raise CheckFailure("frontend entry asset path was outside the release asset roots")
    if any(part in {"", ".", ".."} for part in parsed.path.split("/")[1:]):
        raise CheckFailure("frontend entry asset path was not canonical")
    return urllib.parse.urlunsplit(("", "", parsed.path, parsed.query, ""))


def _display_endpoint(path: str) -> str:
    return urllib.parse.urlsplit(path).path


def _valid_public_id(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return False
    normalized = str(value).strip()
    return bool(
        normalized
        and len(normalized) <= 256
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:@+\-]*", normalized)
    )


def _valid_utc_timestamp(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value.endswith("Z")
        and _valid_timezone_timestamp(value)
    )


def _valid_timezone_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or len(value) > 64:
        return False
    try:
        normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _identifier_fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _parse_content_length(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _safe_error(exc: BaseException) -> str:
    if isinstance(exc, CheckFailure):
        value = str(exc)
    elif isinstance(exc, json.JSONDecodeError):
        value = f"invalid JSON at byte {exc.pos}"
    else:
        value = f"{type(exc).__name__}: {exc}"
    value = re.sub(
        r"(?i)(authorization|password|secret|token)(\s*[=:]\s*)[^\s,;]+",
        r"\1\2<redacted>",
        value,
    )
    value = " ".join(value.replace("\x00", "").split())
    return value[:240]


def _graph_method(check_id: str) -> str:
    return "POST" if check_id == "graph_micro_news_batch" else "GET"


def _graph_endpoint_template(check_id: str) -> str:
    return {
        "graph_macro": "/api/graph/macro/{macro_id}",
        "graph_macro_briefing": "/api/graph/macro/{macro_id}/briefing",
        "graph_macro_micros": "/api/graph/macro/{macro_id}/micros",
        "graph_macro_tree": "/api/graph/macro/{macro_id}/tree",
        "graph_macro_search": "/api/graph/macros/search",
        "graph_micro": "/api/graph/micro/{micro_id}",
        "graph_micro_news": "/api/graph/micro/{micro_id}/news",
        "graph_micro_news_batch": "/api/graph/micros/news-batch",
    }[check_id]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_json_write(path: Path, value: Mapping[str, Any]) -> None:
    payload = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(temporary, flags, 0o640)
    try:
        with os.fdopen(fd, "wb") as handle:
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url", required=True, help="Candidate origin, for example http://127.0.0.1:18091"
    )
    parser.add_argument(
        "--expected-build-id", required=True, help="Exact schema-v3 release build_id"
    )
    parser.add_argument(
        "--output-dir", required=True, type=Path, help="New or empty immutable evidence directory"
    )
    parser.add_argument(
        "--auth-token-file",
        required=True,
        type=Path,
        help="Mode-0600 file containing a short-lived candidate Bearer token",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Per-request timeout in seconds (default: 30)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        auth_token = read_auth_token_file(args.auth_token_file)
        runner = CandidateAcceptance(
            base_url=args.base_url,
            expected_build_id=args.expected_build_id,
            output_dir=args.output_dir,
            auth_token=auth_token,
            timeout_seconds=args.timeout,
        )
        del auth_token
        acceptance = runner.run()
    except (OSError, ValueError) as exc:
        print(f"candidate acceptance could not run: {_safe_error(exc)}", file=sys.stderr)
        return 2
    summary = acceptance["summary"]
    print(
        f"candidate acceptance {acceptance['status']}: "
        f"passed={summary['passed']} failed={summary['failed']} "
        f"blocked={summary['blocked']} evidence={args.output_dir / 'acceptance.json'}"
    )
    return 0 if acceptance["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
