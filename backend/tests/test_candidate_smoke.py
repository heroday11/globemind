from __future__ import annotations

import hashlib
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

import pytest

from deploy import candidate_smoke

BUILD_ID = "0.10.0-test-build"
AUTH_TOKEN = "candidate-test-bearer-" + "a" * 48
CATALOG_SECRET = "runtime-catalog-secret-must-not-be-persisted"
MACRO_ID = "macro-1"
MICRO_ID = "chain-1"
L1_ID = "cluster-1"

_ORIGINAL_CREATE_CONNECTION = socket.create_connection
_ORIGINAL_GETADDRINFO = socket.getaddrinfo
_ORIGINAL_SOCKET_CONNECT = socket.socket.connect


class _TransportHandler(BaseHTTPRequestHandler):
    paths: list[str] = []

    def do_GET(self) -> None:
        self.paths.append(self.path)
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/ok")
            self.end_headers()
            return
        if self.path == "/large":
            body = b"x" * 64
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


class FakeCandidateClient:
    """In-memory candidate server used without opening a socket."""

    def __init__(
        self,
        *,
        build_id: str = BUILD_ID,
        empty_graph: bool = False,
        leak_secret_on_missing_auth: bool = False,
        feature_down: str | None = None,
        feature_stale: str | None = None,
        feature_omitted: str | None = None,
        catalog_case: str = "current",
        catalog_leak: str | None = None,
        data_catalog_ready: bool = True,
        data_catalog_record_case: str = "current",
        evidence_available: bool = True,
        evidence_matches_body: bool = True,
        evidence_citation_status: str = "available",
        evidence_unavailable_reason: str = "PARAGRAPH_ANCHOR_NOT_FOUND",
        research_storage_available: bool = True,
        model_assurance_ready: bool = True,
        identity_assurance_ready: bool = True,
        identity_audit_leak: bool = False,
        service_level_case: str = "current",
        entity_governance_ready: bool = True,
        entity_catalog_overstates: bool = False,
        financial_triage_case: str = "current",
        privacy_plan_case: str = "current",
    ) -> None:
        self.build_id = build_id
        self.empty_graph = empty_graph
        self.leak_secret_on_missing_auth = leak_secret_on_missing_auth
        self.feature_down = feature_down
        self.feature_stale = feature_stale
        self.feature_omitted = feature_omitted
        self.catalog_case = catalog_case
        self.catalog_leak = catalog_leak
        self.data_catalog_ready = data_catalog_ready
        self.data_catalog_record_case = data_catalog_record_case
        self.evidence_available = evidence_available
        self.evidence_matches_body = evidence_matches_body
        self.evidence_citation_status = evidence_citation_status
        self.evidence_unavailable_reason = evidence_unavailable_reason
        self.research_storage_available = research_storage_available
        self.model_assurance_ready = model_assurance_ready
        self.identity_assurance_ready = identity_assurance_ready
        self.identity_audit_leak = identity_audit_leak
        self.service_level_case = service_level_case
        self.entity_governance_ready = entity_governance_ready
        self.entity_catalog_overstates = entity_catalog_overstates
        self.financial_triage_case = financial_triage_case
        self.privacy_plan_case = privacy_plan_case
        self.requests: list[tuple[str, str]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        max_body_bytes: int = candidate_smoke.DEFAULT_MAX_BODY_BYTES,
    ) -> candidate_smoke.HttpResponse:
        del max_body_bytes
        self.requests.append((method, path))
        status, content_type, body = self._dispatch(
            method,
            path,
            json_body=json_body,
            headers=headers or {},
        )
        if isinstance(body, bytes):
            encoded = body
        else:
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        return candidate_smoke.HttpResponse(
            status=status,
            headers={
                "content-type": content_type,
                "cache-control": "no-store",
                "content-length": str(len(encoded)),
            },
            body=encoded,
            duration_ms=1.25,
        )

    def _dispatch(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None,
        headers: Mapping[str, str],
    ) -> tuple[int, str, Any]:
        release = {
            "version": "0.10.0",
            "build_id": self.build_id,
            "git_sha": "a" * 40,
        }
        if path == "/api/health/live":
            return (
                200,
                "application/json",
                {
                    "status": "healthy",
                    "service": "globemind-api",
                    "check": "process",
                    "release": release,
                },
            )
        if path == "/api/health/ready":
            return (
                200,
                "application/json",
                {
                    "status": "healthy",
                    "ready": True,
                    "release": release,
                    "checks": {"database": {"status": "up", "critical": True}},
                },
            )
        if path == "/api/health/features":
            checks = {
                feature_id: {
                    "feature_id": feature_id,
                    "status": (
                        "down"
                        if feature_id == self.feature_down
                        else "stale"
                        if feature_id == self.feature_stale
                        else "up"
                    ),
                    "latency_ms": 1.0,
                    "dependencies": [f"test:{feature_id}"],
                    "metrics": {},
                }
                for feature_id in candidate_smoke.REQUIRED_FEATURE_HEALTH_IDS
                if feature_id != self.feature_omitted
            }
            ready = self.feature_down is None
            degraded = self.feature_stale is not None
            return (
                200 if ready else 503,
                "application/json",
                {
                    "status": "degraded" if ready and degraded else "healthy" if ready else "unhealthy",
                    "ready": ready,
                    "checks": checks,
                },
            )
        if path == "/":
            return (
                200,
                "text/html; charset=utf-8",
                (
                    b"<!doctype html><html><head><title>GlobeMind \xc2\xb7 \xe5\xa4\x9a\xe8\xaf\xad\xe8\xa8\x80\xe5\x9c\xb0\xe7\xbc\x98\xe6\x83\x85\xe6\x8a\xa5\xe5\xb9\xb3\xe5\x8f\xb0</title>"
                    b'<script type="module" src="/assets/index-test.js?v=1"></script>'
                    b'</head><body><div id="app"></div></body></html>'
                ),
            )
        if path == "/assets/index-test.js?v=1":
            return 200, "application/javascript", b"const candidate = true;"
        if path == "/api/auth/me":
            if self.leak_secret_on_missing_auth and "Authorization" not in headers:
                return (
                    500,
                    "application/json",
                    {
                        "access_token": "must-never-enter-evidence",
                        "secret": "also-must-not-enter-evidence",
                    },
                )
            return 401, "application/json", {"detail": "未登录或 token 无效"}
        if path == "/api/auth/login":
            assert method == "POST"
            assert json_body is not None
            return 401, "application/json", {"detail": "用户名或密码错误"}
        if path == "/api/ops/runtime-catalog":
            if headers.get("Authorization") != f"Bearer {AUTH_TOKEN}":
                return 401, "application/json", {"detail": "未登录或 token 无效"}
            services = [
                {
                    "id": service_id,
                    "name": service_id,
                    "kind": "pipeline",
                    "owner": "test-owner",
                    "criticality": "high",
                    "controller": {
                        "type": "shell-script",
                        "path": f"/root/data/globemind/deploy/{service_id}.sh",
                        "interface": "status",
                        "adoption": "observe-only",
                    },
                    "health_policy": {"mode": "composite", "signals": []},
                    "lifecycle_authorization": {
                        "state": "not-authorized",
                        "authorized_operations": [],
                    },
                    "catalog_status": "current",
                    "catalog_drift": [],
                    "takeover_ready": False,
                    "management_blockers": ["lifecycle-not-authorized"],
                }
                for service_id in sorted(candidate_smoke.REQUIRED_RUNTIME_SERVICE_IDS)
            ]
            control = {"enabled": False, "actions": []}
            if self.catalog_case == "missing-service":
                services.pop()
            elif self.catalog_case == "drifted":
                services[0]["catalog_status"] = "drifted"
                services[0]["catalog_drift"] = [{"code": "test-drift"}]
            elif self.catalog_case == "control-enabled":
                control = {"enabled": True, "actions": ["restart"]}
            elif self.catalog_case == "authorized-operation":
                services[0]["lifecycle_authorization"]["authorized_operations"] = [
                    "restart"
                ]
            elif self.catalog_case == "executable-material":
                services[0]["controller"]["argv"] = ["controller", "restart"]
            if self.catalog_leak == "secret-fields":
                services[0]["secret_refs"] = [
                    {"name": "database", "value": CATALOG_SECRET}
                ]
                services[0]["secret_policy"] = {"value": CATALOG_SECRET}
            elif self.catalog_leak == "credential-path":
                services[0]["diagnostic_path"] = (
                    "/root/data/secrets/globemind/credentials.json"
                )
            elif self.catalog_leak == "service-id":
                services[0]["id"] = CATALOG_SECRET
            return (
                200,
                "application/json",
                {
                    "schema_version": 2,
                    "inventory_version": "1.0.0",
                    "operation": "runtime-catalog",
                    "available": True,
                    "read_only": True,
                    "process_inspection": False,
                    "control": control,
                    "summary": {
                        "service_count": len(services),
                        "catalog_current": sum(
                            service["catalog_status"] == "current" for service in services
                        ),
                        "catalog_drifted": sum(
                            service["catalog_status"] != "current" for service in services
                        ),
                        "lifecycle_authorized": sum(
                            service["lifecycle_authorization"]["state"] == "authorized"
                            for service in services
                        ),
                    },
                    "services": services,
                    **(
                        {"inventory_version": "unexpected"}
                        if self.catalog_case == "wrong-identity"
                        else {}
                    ),
                },
            )
        if path == "/api/research/storage-status":
            if headers.get("Authorization") != f"Bearer {AUTH_TOKEN}":
                return 401, "application/json", {"detail": "未登录或 token 无效"}
            if not self.research_storage_available:
                return (
                    503,
                    "application/json",
                    {
                        "detail": {
                            "schema_version": "research-storage-status-v1",
                            "status": "unavailable",
                            "durability": "unavailable",
                            "fallback": "none",
                        }
                    },
                )
            return (
                200,
                "application/json",
                {
                    "schema_version": "research-storage-status-v1",
                    "status": "available",
                    "backend": "filesystem:workspace-root-isolated-service-store",
                    "durability": "atomic-json-fsync",
                    "fallback": "none",
                    "integrity_check": "sha256-local-consistency",
                    "audit_immutability": "unavailable",
                },
            )
        if path == "/api/model-assurance/status":
            if headers.get("Authorization") != f"Bearer {AUTH_TOKEN}":
                return 401, "application/json", {"detail": "未登录或 token 无效"}
            ready = self.model_assurance_ready
            return (
                200,
                "application/json",
                {
                    "schema_version": "globemind.model-assurance.v1",
                    "generated_at": "2026-08-09T00:00:00Z",
                    "available": ready,
                    "operational_state": "observed" if ready else "not_observed",
                    "release_status": "eligible" if ready else "blocked",
                    "gold_standard_state": "manifest_attested" if ready else "not_observed",
                    "evaluation_count": 2 if ready else 0,
                    "eligible_count": 1 if ready else 0,
                    "latest": (
                        {
                            "evaluation_id": "eval.current",
                            "model_id": "model.test",
                            "model_version": "2",
                            "method_version": "1",
                            "dataset_id": "dataset.test",
                            "dataset_sha256": "a" * 64,
                            "cutoff_at": "2026-08-08T00:00:00Z",
                            "stored_at": "2026-08-09T00:00:00Z",
                            "entry_sha256": "b" * 64,
                            "gate_state": "eligible",
                            "release_eligible": True,
                            "drift_state": "within_threshold",
                            "rollback_action": "proceed",
                            "reason_codes": [],
                        }
                        if ready
                        else None
                    ),
                    "reason_codes": [] if ready else ["NO_EVALUATION_MANIFESTS"],
                },
            )
        if path == "/api/user/security/mfa":
            if headers.get("Authorization") != f"Bearer {AUTH_TOKEN}":
                return 401, "application/json", {"detail": "未登录或 token 无效"}
            ready = self.identity_assurance_ready
            return (
                200,
                "application/json",
                {
                    "schema_version": "identity-mfa-status-v1",
                    "status": "disabled",
                    "enabled": False,
                    "pending_enrollment": False,
                    "pending_expires_at": None,
                    "pending_attempts_remaining": None,
                    "recovery_codes_remaining": 0,
                    "assurance": {
                        "type": "totp-rfc6238",
                        "enrollment_state": "available" if ready else "unavailable",
                        "institutional_sso": "unavailable",
                        "device_attestation": "unavailable",
                        "independent_security_review": "unavailable",
                    },
                    "capabilities": {
                        "totp_enrollment": "available" if ready else "unavailable",
                        "recovery_codes": "available" if ready else "unavailable",
                        "tracked_sessions": "available",
                    },
                    "storage": {
                        "status": "available",
                        "backend": "append-only-filesystem",
                        "writes_on_read": False,
                        "last_seen": "unavailable",
                    },
                },
            )
        if path == "/api/user/security/audit?limit=1":
            if headers.get("Authorization") != f"Bearer {AUTH_TOKEN}":
                return 401, "application/json", {"detail": "未登录或 token 无效"}
            return (
                200,
                "application/json",
                {
                    "schema_version": "identity-security-audit-v1",
                    "events": [],
                    "redaction": {
                        "token": "never_stored",
                        "totp_secret": "not_in_audit",
                        "recovery_code": "never_stored",
                        "reason": (
                            "plain_text"
                            if self.identity_audit_leak
                            else "sha256_and_length_only"
                        ),
                        "body_fields": "none",
                    },
                },
            )
        if path == "/api/user/privacy/deletion-impact-plan":
            if headers.get("Authorization") != f"Bearer {AUTH_TOKEN}":
                return 401, "application/json", {"detail": "未登录或 token 无效"}
            items = [
                {
                    "scope": "identity.account",
                    "disposition": "review_required",
                    "record_count": 1,
                    "count_status": "exact",
                    "ownership_basis": "canonical_database_account_row",
                    "reason_code": "RETENTION_AND_RELATIONAL_POLICY_REQUIRED",
                },
                {
                    "scope": "operations.backups_and_logs",
                    "disposition": "unavailable",
                    "record_count": None,
                    "count_status": "unavailable",
                    "ownership_basis": "external_retention_inventory_required",
                    "reason_code": "BACKUP_AND_LOG_PROVENANCE_REQUIRED",
                },
            ]
            summary = {
                disposition: {
                    "scope_count": 0,
                    "exact_record_count": 0,
                    "unavailable_scope_count": 0,
                }
                for disposition in (
                    "delete",
                    "anonymize",
                    "retain",
                    "review_required",
                    "unavailable",
                )
            }
            summary["review_required"] = {
                "scope_count": 1,
                "exact_record_count": 1,
                "unavailable_scope_count": 0,
            }
            summary["unavailable"] = {
                "scope_count": 1,
                "exact_record_count": 0,
                "unavailable_scope_count": 1,
            }
            if self.privacy_plan_case == "bad-summary":
                summary["review_required"]["scope_count"] = 2
            payload = {
                "schema_version": "account-deletion-impact-plan-v1",
                "generated_at": "2026-08-09T00:00:00+00:00",
                "subject_ref": "user:7",
                "canonical_identity_verified": True,
                "operation_mode": "read_only_preflight",
                "deletion_performed": False,
                "execution_state": "blocked",
                "request_registration_state": "not_checked",
                "scope_complete": False,
                "impact_items": items,
                "disposition_summary": summary,
                "external_blockers": [
                    {
                        "code": "RETENTION_AND_LEGAL_BASIS_REVIEW_REQUIRED",
                        "category": "retention_legal_basis",
                        "status": "open",
                        "required_authority": "privacy_or_legal_owner",
                    },
                    {
                        "code": "DURABLE_CHECKPOINT_AND_RECOVERY_PLAN_REQUIRED",
                        "category": "checkpoint_and_recovery",
                        "status": "open",
                        "required_authority": "operations_owner",
                    },
                    {
                        "code": "SHARED_RESOURCE_AND_RELATIONAL_IMPACT_REVIEW_REQUIRED",
                        "category": "dependency_review",
                        "status": "open",
                        "required_authority": "data_owner",
                    },
                    {
                        "code": "MANUAL_DELETION_AUTHORITY_REQUIRED",
                        "category": "manual_authority",
                        "status": "open",
                        "required_authority": "authorized_human_operator",
                    },
                    {
                        "code": "UNAVAILABLE_SCOPES_MUST_BE_RESOLVED",
                        "category": "scope_completeness",
                        "status": "open",
                        "required_authority": "system_owner",
                    },
                ],
                "execution_contract": {
                    "may_execute": False,
                    "manual_authority_required": True,
                    "request_registration_is_not_execution": True,
                    "retention_and_legal_basis_status": "unverified",
                    "checkpoint_status": "not_proven",
                },
                "limits": {
                    "impact_items": 128,
                    "unavailable_scopes": 96,
                    "source_bytes": 8388608,
                    "response_bytes": 131072,
                },
                "excluded_fields": [
                    "record_bodies",
                    "absolute_paths",
                    "other_subject_identifiers",
                    "credentials_and_tokens",
                ],
            }
            if self.privacy_plan_case == "overstated-execution":
                payload["deletion_performed"] = True
            elif self.privacy_plan_case == "sensitive-field":
                payload["record_bodies"] = ["must-not-be-persisted"]
            return 200, "application/json", payload
        if path in {
            "/api/service-level/status",
            "/api/service-level/summary?window_hours=24",
        }:
            if headers.get("Authorization") != f"Bearer {AUTH_TOKEN}":
                return 401, "application/json", {"detail": "未登录或 token 无效"}
            target = {
                "approval_state": (
                    "approved"
                    if self.service_level_case == "approved-target"
                    else "not_approved"
                ),
                "compliance": (
                    "met"
                    if self.service_level_case == "approved-target"
                    else "not_computable"
                ),
                "targets_configured": self.service_level_case == "approved-target",
                "approver_evidence_state": (
                    "present"
                    if self.service_level_case == "approved-target"
                    else "absent"
                ),
            }
            common = {
                "schema_version": "globemind.service-level.v1",
                "measurement_method_version": (
                    "http-route-template-duration-nearest-rank-v1"
                ),
                "generated_at": "2026-08-09T00:00:00Z",
                "measurement_state": "observed",
                "storage_state": "available",
                "integrity_state": "verified",
                "instrumentation_write_failure_count": 0,
                "instrumentation_write_state": "no_failures_observed",
                "target": target,
            }
            if self.service_level_case == "sensitive-field":
                common["request_id"] = "must-not-be-persisted"
            if path == "/api/service-level/status":
                return (
                    200,
                    "application/json",
                    {**common, "total_observation_count": 2},
                )

            def service_metrics(
                scope: str,
                *,
                success: int = 0,
                error: int = 0,
            ) -> dict[str, Any]:
                count = success + error
                return {
                    "scope": scope,
                    "sample_count": count,
                    "success_count": success,
                    "error_count": error,
                    "timeout_count": 0,
                    "cancelled_count": 0,
                    "error_rate_definition": "all_non_success_outcomes",
                    "percentile_method": "nearest_rank",
                    "success_rate": success / count if count else None,
                    "error_rate": error / count if count else None,
                    "p50_ms": 10 if count else None,
                    "p95_ms": 20 if count else None,
                    "p99_ms": 20 if count else None,
                }

            search_metrics = service_metrics("search", success=1, error=1)
            if self.service_level_case == "bad-rate":
                search_metrics["success_rate"] = 1.0
                search_metrics["error_rate"] = 0.0
            return (
                200,
                "application/json",
                {
                    **common,
                    "window": {
                        "starts_at": "2026-08-08T00:00:00Z",
                        "ends_at": "2026-08-09T00:00:00Z",
                        "hours": 24,
                    },
                    "overall": service_metrics("overall", success=1, error=1),
                    "operations": [
                        search_metrics,
                        service_metrics("export"),
                        service_metrics("report"),
                    ],
                },
            )
        if path == "/api/entity-governance/status":
            if headers.get("Authorization") != f"Bearer {AUTH_TOKEN}":
                return 401, "application/json", {"detail": "未登录或 token 无效"}
            ready = self.entity_governance_ready
            return (
                200,
                "application/json",
                {
                    "schema_version": "entity-governance-status-v2",
                    "storage_status": "available" if ready else "unavailable",
                    "reason": (
                        None
                        if ready
                        else "ENTITY_GOVERNANCE_LEDGER_CONFIGURATION_UNAVAILABLE"
                    ),
                    "root_initialized": False,
                    "event_count": 0 if ready else None,
                    "latest_event_id": None,
                    "integrity_status": "verified" if ready else "unavailable",
                    "mutation_status": "ready" if ready else "blocked",
                    "mutation_blocker": (
                        None
                        if ready
                        else "ENTITY_GOVERNANCE_LEDGER_CONFIGURATION_UNAVAILABLE"
                    ),
                    "chain": "sha256-and-hmac-sha256",
                    "append_semantics": "no-replace-local-filesystem",
                    "hmac_key_id": "unavailable",
                    "hmac_key_rotation": (
                        "offline-controlled-migration-not-implemented"
                    ),
                    "worm_status": "unavailable",
                    "digital_signature_status": "unavailable",
                    "institutional_directory_integration": "unavailable",
                    "accuracy_claim": "not_measured",
                    "seed_review_default": "review_required",
                    "evidence_policy": (
                        "verified-evidence-snapshot-required-for-mutations"
                    ),
                    "review_expiry_policy": "not_configured",
                },
            )
        if path == "/api/entity-governance/catalog":
            if headers.get("Authorization") != f"Bearer {AUTH_TOKEN}":
                return 401, "application/json", {"detail": "未登录或 token 无效"}
            if not self.entity_governance_ready:
                return 503, "application/json", {"detail": "unavailable"}
            review_status = (
                "approved" if self.entity_catalog_overstates else "review_required"
            )
            return (
                200,
                "application/json",
                {
                    "schema_version": "entity-governance-catalog-v2",
                    "accuracy_claim": (
                        "measured" if self.entity_catalog_overstates else "not_measured"
                    ),
                    "projection_policy": "approved-and-not-retracted-only",
                    "review_expiry_policy": "not_configured",
                    "seed_inventory_scope": "bounded-public-search-facade-probes",
                    "approved_entities": [],
                    "review_required_entities": [
                        {
                            "entity_id": "urn:globemind:entity:country:CN",
                            "entity_type": "country",
                            "canonical_names": {"zh": "中国", "en": "China"},
                            "review_status": review_status,
                            "source_catalog_version": "entity-aliases-v2",
                            "source_catalog_review_status": "review_required",
                            "accuracy_claim": "not_measured",
                        }
                    ],
                    "rejected_entity_ids": [],
                    "merge_decisions": [],
                    "split_decisions": [],
                    "event_count": 0,
                    "latest_event_id": None,
                    "assurance": {
                        "worm": "unavailable",
                        "digital_signature": "unavailable",
                        "institutional_directory": "unavailable",
                    },
                },
            )
        if path == "/api/dashboard/stats":
            return (
                200,
                "application/json",
                {
                    "total_news": 100,
                    "language_stats": [{"id": "en", "count": 100}],
                },
            )
        if path == "/api/dashboard/search/options":
            return 200, "application/json", {"language_options": [{"id": "en", "name": "English"}]}
        if path == "/api/financial/dashboard":
            return 200, "application/json", {"mode": "live", "bars": []}
        if path == "/api/financial/alert/data":
            triage = {
                "schema_version": "financial-alert-triage-status-v1",
                "alert_event_id": "alert-1",
                "status": "open",
                "has_audit": False,
                "reviewed": False,
                "transition_count": 0,
                "last_transition_at": None,
                "last_event_id": None,
                "last_event_sha256": None,
                "operational_limitations": {
                    "sla": "unavailable",
                    "notification_delivery": "not_configured",
                    "institutional_incident_system": "not_configured",
                },
                "historical": True,
                "mutations_enabled": False,
            }
            if self.financial_triage_case == "sensitive-field":
                triage["actor_user_id"] = 99
            elif self.financial_triage_case == "contradictory-state":
                triage["mutations_enabled"] = True
            elif self.financial_triage_case == "bad-event-reference":
                triage.update(
                    {
                        "status": "acknowledged",
                        "has_audit": True,
                        "transition_count": 1,
                        "last_transition_at": "not-a-timestampZ",
                        "last_event_id": "fat-20260809T080000000000Z-0000000000000001",
                        "last_event_sha256": "a" * 64,
                    }
                )
            elif self.financial_triage_case == "bad-event-id":
                triage.update(
                    {
                        "status": "acknowledged",
                        "has_audit": True,
                        "transition_count": 1,
                        "last_transition_at": "2026-08-09T08:00:00Z",
                        "last_event_id": "event-1",
                        "last_event_sha256": "a" * 64,
                    }
                )
            elif self.financial_triage_case == "bad-schema":
                triage["schema_version"] = "financial-alert-triage-status-v0"
            history_row = {"id": "alert-1", "triage": triage}
            if self.financial_triage_case == "top-level-sensitive":
                history_row["actor_user_id"] = 99
            return (
                200,
                "application/json",
                {
                    "rules": [],
                    "history": [history_row],
                },
            )
        if path.startswith("/api/story-graph/ground-news/list?"):
            return 200, "application/json", {"stories": [], "total": 0}
        if path == "/api/opinion/quality":
            return (
                200,
                "application/json",
                {
                    "ok": True,
                    "method_version": "test-v1",
                    "status": "healthy",
                    "freshness": {},
                },
            )
        if path == "/api/data-governance/catalog":
            ready = self.data_catalog_ready
            records = [
                {
                    "record_id": "dataset.test-current-news",
                    "kind": "dataset",
                    "status": {
                        "state": "eligible" if ready else "blocked",
                        "release_eligible": ready,
                        "research_ready": ready,
                        "reason_codes": [] if ready else ["OWNER_NOT_NAMED"],
                    },
                }
            ]
            if self.data_catalog_record_case == "duplicate":
                records.append({**records[0], "status": dict(records[0]["status"])})
            elif self.data_catalog_record_case == "kind-mismatch":
                records[0]["record_id"] = "model.test-current-news"
            elif self.data_catalog_record_case == "empty-suffix":
                records[0]["record_id"] = "dataset."
            record_count = len(records)
            return (
                200,
                "application/json",
                {
                    "schema_version": "data-governance-catalog-v1",
                    "contract_version": "1.0.0",
                    "available": True,
                    "catalog_status": "ready" if ready else "incomplete",
                    "registry_sources": {
                        "owner_registry": "verified",
                        "source_catalog": "verified",
                        "references": [
                            "ops/features/registry.json",
                            "data/source_curation/full_source_catalog.csv",
                        ],
                    },
                    "summary": {
                        "record_count": record_count,
                        "dataset_count": record_count,
                        "source_count": 0,
                        "model_count": 0,
                        "eligible_count": record_count if ready else 0,
                        "blocked_count": 0 if ready else record_count,
                        "formal_release_status": "ready" if ready else "blocked",
                    },
                    "records": records,
                    "reason_codes": [] if ready else ["FORMAL_REGISTRATION_INCOMPLETE"],
                },
            )
        if path.startswith("/api/graph/universe?"):
            macros: list[dict[str, Any]] = []
            if not self.empty_graph:
                macros = [
                    {
                        "macro_id": MACRO_ID,
                        "storyline_id": MACRO_ID,
                        "title": "Current hierarchy sample",
                        "article_count": 3,
                        "micro_events": [
                            {
                                "event_id": MICRO_ID,
                                "chain_id": MICRO_ID,
                                "article_count": 3,
                            }
                        ],
                    }
                ]
            return (
                200,
                "application/json",
                {
                    "macros": macros,
                    "unclustered_news": [],
                    "macros_count": len(macros),
                },
            )
        if path == f"/api/graph/macro/{MACRO_ID}":
            return (
                200,
                "application/json",
                {
                    "macro_id": MACRO_ID,
                    "storyline_id": MACRO_ID,
                    "article_count": 3,
                },
            )
        if path == f"/api/graph/macro/{MACRO_ID}/briefing":
            return (
                200,
                "application/json",
                {
                    "storyline_id": MACRO_ID,
                    "macro": {"macro_id": MACRO_ID},
                    "sentiment_distribution": [],
                },
            )
        if path == f"/api/graph/macro/{MACRO_ID}/micros?limit=20&offset=0":
            return (
                200,
                "application/json",
                {
                    "items": [{"event_id": MICRO_ID, "chain_id": MICRO_ID}],
                    "total": 1,
                },
            )
        if path == f"/api/graph/macro/{MACRO_ID}/tree?micro_limit=20":
            return (
                200,
                "application/json",
                {
                    "macro": {"macro_id": MACRO_ID},
                    "micros": [{"event_id": MICRO_ID, "chain_id": MICRO_ID}],
                },
            )
        if path.startswith("/api/graph/macros/search?"):
            return (
                200,
                "application/json",
                {
                    "items": [{"macro_id": MACRO_ID, "storyline_id": MACRO_ID}],
                    "total": 1,
                },
            )
        if path == f"/api/graph/micro/{MICRO_ID}":
            return (
                200,
                "application/json",
                {
                    "event_id": MICRO_ID,
                    "chain_id": MICRO_ID,
                    "article_count": 3,
                },
            )
        if path == f"/api/graph/micro/{MICRO_ID}/news?page=1&page_size=5&brief=true":
            return (
                200,
                "application/json",
                {
                    "event_id": MICRO_ID,
                    "items": [{"id": 1}],
                    "total": 1,
                },
            )
        if path == "/api/graph/micros/news-batch":
            assert json_body == {"event_ids": [MICRO_ID], "limit_per": 5}
            return (
                200,
                "application/json",
                {
                    "by_event": {MICRO_ID: [{"id": 1}]},
                    "total_news": 1,
                },
            )
        if path == "/api/dashboard/search/v11-clusters":
            assert method == "POST"
            return (
                200,
                "application/json",
                {
                    "items": [{"id": MACRO_ID, "level": "macro"}],
                    "total": 1,
                },
            )
        if path == (
            f"/api/dashboard/search/v11-clusters/{MACRO_ID}/children?level=l3&page=1&page_size=20"
        ):
            return (
                200,
                "application/json",
                {
                    "items": [{"id": MICRO_ID, "level": "l2"}],
                    "parent_level": "l3",
                    "child_level": "l2",
                },
            )
        if path == (
            f"/api/dashboard/search/v11-clusters/{MICRO_ID}/children?level=l2&page=1&page_size=20"
        ):
            return (
                200,
                "application/json",
                {
                    "items": [{"id": L1_ID, "level": "l1"}],
                    "parent_level": "l2",
                    "child_level": "l1",
                },
            )
        if path == (
            f"/api/dashboard/search/v11-clusters/{L1_ID}/children?level=l1&page=1&page_size=5"
        ):
            return (
                200,
                "application/json",
                {
                    "items": [{"id": 1, "level": "news"}],
                    "parent_level": "l1",
                    "child_level": "news",
                },
            )
        if path == "/api/article/1/reader":
            evidence_status = "available" if self.evidence_available else "unavailable"
            body = "The sampled article contains source text."
            matched_text = (
                "sampled article"
                if self.evidence_matches_body
                else "Headline-only judgment"
            )
            excerpt = body if self.evidence_matches_body else "Headline-only judgment"
            return (
                200,
                "application/json",
                {
                    "news": {
                        "id": 1,
                        "title": "Headline-only judgment",
                        "body": body,
                    },
                    "analysis": {
                        "evidence_chain": {
                            "schema_version": "article-evidence-v1",
                            "article_id": 1,
                            "paragraph_count": 1,
                            "claims": [
                                {
                                    "id": "article:1:test-judgment",
                                    "claim_type": "judgment",
                                    "text": "The sampled article supports the test judgment.",
                                    "source": "candidate-test-model-v1",
                                    "evidence_status": evidence_status,
                                    "citations": (
                                        [
                                            {
                                                "status": self.evidence_citation_status,
                                                "article_id": 1,
                                                "paragraph_number": 1,
                                                "anchor_id": "article-1-paragraph-1",
                                                "relation": "support",
                                                "matched_text": matched_text,
                                                "excerpt": excerpt,
                                            }
                                        ]
                                        if self.evidence_available
                                        else []
                                    ),
                                    "unavailable_reason": (
                                        None
                                        if self.evidence_available
                                        else self.evidence_unavailable_reason
                                    ),
                                }
                            ],
                            "provenance": {
                                "body_status": "available",
                                "response_body_sha256": hashlib.sha256(
                                    body.encode("utf-8")
                                ).hexdigest(),
                                "hash_scope": "normalized-display-body",
                                "snapshot_status": "unavailable",
                            },
                        }
                    },
                },
            )
        if path in candidate_smoke.LEGACY_OPINION_ENDPOINTS:
            return (
                410,
                "application/json",
                {
                    "ok": False,
                    "code": "endpoint_retired",
                    "status": 410,
                    "endpoint": path,
                    "message": "retired",
                    "retired_in": "v0.10",
                    "alternatives": ["/api/replacement"],
                },
            )
        raise AssertionError(f"unhandled fake candidate request: {method} {path}")


def _run(
    tmp_path: Path,
    client: FakeCandidateClient,
) -> tuple[dict[str, Any], Path]:
    output = tmp_path / "acceptance"
    result = candidate_smoke.CandidateAcceptance(
        base_url="http://127.0.0.1:18091",
        expected_build_id=BUILD_ID,
        output_dir=output,
        auth_token=AUTH_TOKEN,
        client=client,
    ).run()
    return result, output


def test_candidate_smoke_accepts_complete_current_contract(tmp_path: Path) -> None:
    acceptance, output = _run(tmp_path, FakeCandidateClient())

    assert acceptance["status"] == "passed"
    assert acceptance["summary"] == {
        "total": 49,
        "passed": 49,
        "failed": 0,
        "blocked": 0,
        "degraded": 0,
        "skipped": 0,
        "required_passed": 49,
        "required_failed": 0,
    }
    assert (output / "acceptance.json").is_file()
    assert len(list((output / "checks").glob("*.json"))) == 49
    persisted = json.loads((output / "acceptance.json").read_text(encoding="utf-8"))
    assert persisted["candidate"]["expected_build_id"] == BUILD_ID
    assert persisted["policy"]["response_bodies_persisted"] is False
    assert persisted["policy"]["request_headers_persisted"] is False
    assert persisted["policy"]["auth_token_persisted"] is False
    catalog = next(
        item for item in acceptance["checks"] if item["check_id"] == "runtime_catalog"
    )
    assert catalog["outcome"] == "passed"
    assert catalog["actual_status"] == 200
    assert catalog["observations"]["service_count"] == 12
    assert catalog["observations"]["catalog_drifted"] == 0
    assert catalog["observations"]["control_enabled"] is False
    assert AUTH_TOKEN not in (output / "acceptance.json").read_text(encoding="utf-8")


def test_candidate_smoke_requires_durable_research_storage(tmp_path: Path) -> None:
    acceptance, output = _run(
        tmp_path,
        FakeCandidateClient(research_storage_available=False),
    )

    assert acceptance["status"] == "failed"
    check = next(
        item for item in acceptance["checks"] if item["check_id"] == "research_storage"
    )
    assert check["outcome"] == "failed"
    assert check["actual_status"] == 503
    assert "unexpected HTTP status" in check["error"]
    persisted = (output / "acceptance.json").read_text(encoding="utf-8")
    assert AUTH_TOKEN not in persisted


def test_candidate_smoke_requires_eligible_model_assurance(tmp_path: Path) -> None:
    acceptance, output = _run(
        tmp_path,
        FakeCandidateClient(model_assurance_ready=False),
    )

    assert acceptance["status"] == "failed"
    check = next(
        item for item in acceptance["checks"] if item["check_id"] == "model_assurance"
    )
    assert check["outcome"] == "failed"
    assert check["error"] == "model assurance did not satisfy the release gate"
    persisted = (output / "acceptance.json").read_text(encoding="utf-8")
    assert AUTH_TOKEN not in persisted
    assert "dataset.test" not in persisted


def test_candidate_smoke_requires_available_identity_assurance(tmp_path: Path) -> None:
    acceptance, output = _run(
        tmp_path,
        FakeCandidateClient(identity_assurance_ready=False),
    )

    assert acceptance["status"] == "failed"
    check = next(
        item for item in acceptance["checks"] if item["check_id"] == "identity_assurance"
    )
    assert check["outcome"] == "failed"
    assert check["error"] == "identity assurance capability was unavailable"
    persisted = (output / "acceptance.json").read_text(encoding="utf-8")
    assert AUTH_TOKEN not in persisted


def test_candidate_smoke_rejects_identity_audit_redaction_drift(tmp_path: Path) -> None:
    acceptance, _output = _run(
        tmp_path,
        FakeCandidateClient(identity_audit_leak=True),
    )

    assert acceptance["status"] == "failed"
    check = next(
        item
        for item in acceptance["checks"]
        if item["check_id"] == "identity_security_audit"
    )
    assert check["outcome"] == "failed"
    assert check["error"] == "identity security audit redaction was incomplete"


@pytest.mark.parametrize(
    ("privacy_plan_case", "error"),
    (
        (
            "overstated-execution",
            "account deletion impact plan overstated execution capability",
        ),
        (
            "sensitive-field",
            "account deletion impact plan exposed unexpected fields",
        ),
        (
            "bad-summary",
            "account deletion disposition summary was contradictory",
        ),
    ),
)
def test_candidate_smoke_fails_closed_on_deletion_impact_plan_drift(
    tmp_path: Path,
    privacy_plan_case: str,
    error: str,
) -> None:
    acceptance, output = _run(
        tmp_path,
        FakeCandidateClient(privacy_plan_case=privacy_plan_case),
    )

    assert acceptance["status"] == "failed"
    check = next(
        item
        for item in acceptance["checks"]
        if item["check_id"] == "identity_deletion_impact_plan"
    )
    assert check["outcome"] == "failed"
    assert check["error"] == error
    persisted = (output / "acceptance.json").read_text(encoding="utf-8")
    assert "must-not-be-persisted" not in persisted


@pytest.mark.parametrize(
    ("service_level_case", "check_id", "error"),
    (
        (
            "approved-target",
            "service_level_status",
            "service-level response overstated target approval or compliance",
        ),
        (
            "bad-rate",
            "service_level_summary",
            "service-level aggregate rates were contradictory",
        ),
        (
            "sensitive-field",
            "service_level_status",
            "service-level response exposed a forbidden sensitive field",
        ),
    ),
)
def test_candidate_smoke_fails_closed_on_service_level_contract_drift(
    tmp_path: Path,
    service_level_case: str,
    check_id: str,
    error: str,
) -> None:
    acceptance, output = _run(
        tmp_path,
        FakeCandidateClient(service_level_case=service_level_case),
    )

    assert acceptance["status"] == "failed"
    check = next(item for item in acceptance["checks"] if item["check_id"] == check_id)
    assert check["outcome"] == "failed"
    assert check["error"] == error
    persisted = (output / "acceptance.json").read_text(encoding="utf-8")
    assert "must-not-be-persisted" not in persisted


@pytest.mark.parametrize(
    ("financial_triage_case", "error"),
    (
        (
            "sensitive-field",
            "financial alert triage exposed a forbidden sensitive field",
        ),
        (
            "contradictory-state",
            "financial alert triage state was contradictory",
        ),
        (
            "bad-event-reference",
            "financial alert triage event reference was invalid",
        ),
        (
            "bad-event-id",
            "financial alert triage event reference was invalid",
        ),
        (
            "bad-schema",
            "financial alert triage identity was invalid",
        ),
        (
            "top-level-sensitive",
            "financial alert triage exposed a forbidden sensitive field",
        ),
    ),
)
def test_candidate_smoke_fails_closed_on_financial_triage_contract_drift(
    tmp_path: Path,
    financial_triage_case: str,
    error: str,
) -> None:
    acceptance, output = _run(
        tmp_path,
        FakeCandidateClient(financial_triage_case=financial_triage_case),
    )

    assert acceptance["status"] == "failed"
    check = next(
        item
        for item in acceptance["checks"]
        if item["check_id"] == "financial_alert_triage"
    )
    assert check["outcome"] == "failed"
    assert check["error"] == error
    persisted = (output / "acceptance.json").read_text(encoding="utf-8")
    assert "actor_user_id" not in persisted


@pytest.mark.parametrize(
    ("client", "check_id", "error"),
    (
        (
            FakeCandidateClient(entity_governance_ready=False),
            "entity_governance_status",
            "entity governance event_count was not a non-negative integer",
        ),
        (
            FakeCandidateClient(entity_catalog_overstates=True),
            "entity_governance_catalog",
            "entity governance catalog contract was contradictory",
        ),
    ),
)
def test_candidate_smoke_requires_honest_entity_governance_capability(
    tmp_path: Path,
    client: FakeCandidateClient,
    check_id: str,
    error: str,
) -> None:
    acceptance, _output = _run(tmp_path, client)

    assert acceptance["status"] == "failed"
    check = next(item for item in acceptance["checks"] if item["check_id"] == check_id)
    assert check["outcome"] == "failed"
    assert check["error"] == error


@pytest.mark.parametrize(
    ("client", "check_id", "error"),
    (
        (
            FakeCandidateClient(data_catalog_ready=False),
            "data_governance_catalog",
            "formal data catalog registration was incomplete",
        ),
        (
            FakeCandidateClient(evidence_available=False),
            "article_evidence_chain",
            "sampled article judgment lacked paragraph evidence",
        ),
        (
            FakeCandidateClient(data_catalog_record_case="duplicate"),
            "data_governance_catalog",
            "data catalog contained duplicate record identities",
        ),
        (
            FakeCandidateClient(data_catalog_record_case="kind-mismatch"),
            "data_governance_catalog",
            "data catalog record identity was invalid",
        ),
        (
            FakeCandidateClient(evidence_matches_body=False),
            "article_evidence_chain",
            "article paragraph citation did not match the reader body",
        ),
        (
            FakeCandidateClient(data_catalog_record_case="empty-suffix"),
            "data_governance_catalog",
            "data catalog record identity was invalid",
        ),
        (
            FakeCandidateClient(evidence_citation_status="unknown"),
            "article_evidence_chain",
            "article paragraph citation was invalid",
        ),
        (
            FakeCandidateClient(
                evidence_available=False,
                evidence_unavailable_reason="",
            ),
            "article_evidence_chain",
            "unavailable claim did not explain missing evidence",
        ),
    ),
)
def test_candidate_smoke_fails_closed_on_v1_release_gate_gaps(
    tmp_path: Path,
    client: FakeCandidateClient,
    check_id: str,
    error: str,
) -> None:
    acceptance, _output = _run(tmp_path, client)

    assert acceptance["status"] == "failed"
    check = next(item for item in acceptance["checks"] if item["check_id"] == check_id)
    assert check["outcome"] == "failed"
    assert check["error"] == error


@pytest.mark.parametrize(
    ("catalog_case", "error"),
    (
        ("missing-service", "runtime catalog service set did not match the V1 contract"),
        ("drifted", "runtime catalog service was not current"),
        ("control-enabled", "runtime catalog exposed executable control actions"),
        (
            "authorized-operation",
            "runtime catalog service exposed authorized operations",
        ),
        ("executable-material", "runtime catalog exposed executable control material"),
        ("wrong-identity", "runtime catalog identity did not match the V1 contract"),
    ),
)
def test_runtime_catalog_contract_failures_block_candidate(
    tmp_path: Path,
    catalog_case: str,
    error: str,
) -> None:
    acceptance, _output = _run(
        tmp_path,
        FakeCandidateClient(catalog_case=catalog_case),
    )

    assert acceptance["status"] == "failed"
    check = next(
        item for item in acceptance["checks"] if item["check_id"] == "runtime_catalog"
    )
    assert check["outcome"] == "failed"
    assert error in check["error"]


@pytest.mark.parametrize("leak", ("secret-fields", "credential-path", "service-id"))
def test_runtime_catalog_rejects_sensitive_fields_without_persisting_values(
    tmp_path: Path,
    leak: str,
) -> None:
    acceptance, output = _run(
        tmp_path,
        FakeCandidateClient(catalog_leak=leak),
    )

    assert acceptance["status"] == "failed"
    check = next(
        item for item in acceptance["checks"] if item["check_id"] == "runtime_catalog"
    )
    assert check["outcome"] == "failed"
    persisted = (output / "acceptance.json").read_text(encoding="utf-8")
    assert CATALOG_SECRET not in persisted
    assert AUTH_TOKEN not in persisted
    assert "/root/data/secrets/" not in persisted


def test_candidate_smoke_fails_when_story_graph_capability_probe_is_down(
    tmp_path: Path,
) -> None:
    acceptance, _output = _run(
        tmp_path,
        FakeCandidateClient(feature_down="story-graph"),
    )

    assert acceptance["status"] == "failed"
    check = next(item for item in acceptance["checks"] if item["check_id"] == "health_features")
    assert check["outcome"] == "failed"
    assert check["actual_status"] == 503


def test_candidate_smoke_accepts_stale_but_available_business_data(tmp_path: Path) -> None:
    acceptance, _output = _run(
        tmp_path,
        FakeCandidateClient(feature_stale="ground-news"),
    )

    assert acceptance["status"] == "passed"
    check = next(item for item in acceptance["checks"] if item["check_id"] == "health_features")
    assert check["outcome"] == "passed"
    assert check["observations"]["service_status"] == "degraded"
    assert check["observations"]["non_current_count"] == 1


def test_candidate_smoke_rejects_missing_story_graph_health_check(tmp_path: Path) -> None:
    acceptance, _output = _run(
        tmp_path,
        FakeCandidateClient(feature_omitted="story-graph"),
    )

    assert acceptance["status"] == "failed"
    check = next(item for item in acceptance["checks"] if item["check_id"] == "health_features")
    assert check["outcome"] == "failed"
    assert check["actual_status"] == 200
    assert check["error"] == "feature health check set did not match the V1 contract"


def test_candidate_smoke_fails_closed_on_release_identity_mismatch(tmp_path: Path) -> None:
    acceptance, _output = _run(
        tmp_path,
        FakeCandidateClient(build_id="0.10.0-wrong-build"),
    )

    assert acceptance["status"] == "failed"
    failed = {item["check_id"] for item in acceptance["checks"] if item["outcome"] == "failed"}
    assert {"health_live", "health_ready"} <= failed


def test_empty_graph_is_auditable_failure_not_an_accepted_skip(tmp_path: Path) -> None:
    client = FakeCandidateClient(empty_graph=True)
    acceptance, _output = _run(tmp_path, client)

    assert acceptance["status"] == "failed"
    graph_availability = next(
        item for item in acceptance["checks"] if item["check_id"] == "graph_sample_availability"
    )
    assert graph_availability["outcome"] == "failed"
    blocked = {item["check_id"] for item in acceptance["checks"] if item["outcome"] == "blocked"}
    assert set(candidate_smoke._GRAPH_DEPENDENT_CHECKS) <= blocked
    assert {
        "v11_search_current",
        "v11_l3_children",
        "v11_l2_children",
        "v11_l1_children",
        "article_evidence_chain",
    } <= blocked
    assert acceptance["summary"]["skipped"] == 0
    assert not any("/api/graph/macro/" in path for _method, path in client.requests)


def test_response_secrets_and_bodies_are_never_persisted(tmp_path: Path) -> None:
    acceptance, output = _run(
        tmp_path,
        FakeCandidateClient(leak_secret_on_missing_auth=True),
    )

    assert acceptance["status"] == "failed"
    serialized = (output / "acceptance.json").read_text(encoding="utf-8")
    assert "must-never-enter-evidence" not in serialized
    assert "also-must-not-enter-evidence" not in serialized
    auth = next(
        item for item in acceptance["checks"] if item["check_id"] == "auth_missing_credentials"
    )
    assert auth["actual_status"] == 500
    assert set(auth["response"]) == {"bytes", "cache_policy", "content_type", "sha256"}


def _write_auth_token(path: Path, value: str = AUTH_TOKEN, mode: int = 0o600) -> Path:
    path.write_text(value, encoding="utf-8")
    path.chmod(mode)
    return path


def test_auth_token_file_accepts_only_owned_mode_0600_regular_file(
    tmp_path: Path,
) -> None:
    token_file = _write_auth_token(tmp_path / "candidate.token")

    assert candidate_smoke.read_auth_token_file(token_file) == AUTH_TOKEN


def test_auth_token_file_rejects_symlink(tmp_path: Path) -> None:
    target = _write_auth_token(tmp_path / "target.token")
    link = tmp_path / "candidate.token"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="unavailable or unsafe"):
        candidate_smoke.read_auth_token_file(link)


def test_auth_token_file_rejects_noncanonical_parent_symlink(tmp_path: Path) -> None:
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    token_file = _write_auth_token(real_parent / "candidate.token")
    alias_parent = tmp_path / "alias"
    alias_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(ValueError, match="absolute canonical path"):
        candidate_smoke.read_auth_token_file(alias_parent / token_file.name)


def test_auth_token_file_rejects_non_regular_file(tmp_path: Path) -> None:
    directory = tmp_path / "candidate.token"
    directory.mkdir(mode=0o700)

    with pytest.raises(ValueError):
        candidate_smoke.read_auth_token_file(directory)


def test_auth_token_file_rejects_wrong_owner(tmp_path: Path, monkeypatch) -> None:
    token_file = _write_auth_token(tmp_path / "candidate.token")
    monkeypatch.setattr(candidate_smoke.os, "geteuid", lambda: token_file.stat().st_uid + 1)

    with pytest.raises(ValueError, match="ownership or mode"):
        candidate_smoke.read_auth_token_file(token_file)


@pytest.mark.parametrize("mode", (0o400, 0o640, 0o644, 0o660))
def test_auth_token_file_rejects_non_0600_mode(tmp_path: Path, mode: int) -> None:
    token_file = _write_auth_token(tmp_path / "candidate.token", mode=mode)

    with pytest.raises(ValueError, match="ownership or mode"):
        candidate_smoke.read_auth_token_file(token_file)


def test_auth_token_file_rejects_identity_change_while_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_file = _write_auth_token(tmp_path / "candidate.token")
    real_fstat = candidate_smoke.os.fstat
    calls = 0

    def changed_fstat(descriptor: int):
        nonlocal calls
        calls += 1
        metadata = real_fstat(descriptor)
        if calls != 2:
            return metadata
        values = list(metadata)
        values[8] = metadata.st_mtime + 1
        return candidate_smoke.os.stat_result(values)

    monkeypatch.setattr(candidate_smoke.os, "fstat", changed_fstat)

    with pytest.raises(ValueError, match="changed while reading"):
        candidate_smoke.read_auth_token_file(token_file)


@pytest.mark.parametrize(
    "value",
    (
        "short",
        AUTH_TOKEN + "\n",
        "x" * (candidate_smoke.MAX_AUTH_TOKEN_BYTES + 1),
    ),
)
def test_auth_token_file_rejects_invalid_size_or_whitespace(
    tmp_path: Path,
    value: str,
) -> None:
    token_file = _write_auth_token(tmp_path / "candidate.token", value=value)

    with pytest.raises(ValueError):
        candidate_smoke.read_auth_token_file(token_file)


def test_candidate_cli_accepts_token_file_path_not_token_value(tmp_path: Path) -> None:
    token_file = tmp_path / "candidate.token"
    args = candidate_smoke.build_parser().parse_args(
        [
            "--base-url",
            "http://127.0.0.1:18091",
            "--expected-build-id",
            BUILD_ID,
            "--output-dir",
            str(tmp_path / "evidence"),
            "--auth-token-file",
            str(token_file),
        ]
    )

    assert args.auth_token_file == token_file
    help_text = candidate_smoke.build_parser().format_help()
    assert "--auth-token-file" in help_text
    assert "--auth-token " not in help_text


def test_http_client_uses_bounded_local_transport_and_rejects_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "create_connection", _ORIGINAL_CREATE_CONNECTION)
    monkeypatch.setattr(socket, "getaddrinfo", _ORIGINAL_GETADDRINFO)
    monkeypatch.setattr(socket.socket, "connect", _ORIGINAL_SOCKET_CONNECT)
    _TransportHandler.paths = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _TransportHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = candidate_smoke.HttpClient(
            f"http://127.0.0.1:{server.server_port}",
            timeout_seconds=2,
        )
        response = client.request("GET", "/ok")
        assert response.status == 200
        assert json.loads(response.body) == {"ok": True}

        redirect = client.request("GET", "/redirect")
        assert redirect.status == 302
        assert _TransportHandler.paths == ["/ok", "/redirect"]

        with pytest.raises(candidate_smoke.ResponseTooLarge):
            client.request("GET", "/large", max_body_bytes=16)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        ("http://127.0.0.1:18091", "http://127.0.0.1:18091"),
        ("http://127.42.0.8:8080/", "http://127.42.0.8:8080"),
        ("https://[::1]:18443", "https://[::1]:18443"),
        ("http://[0:0:0:0:0:0:0:1]:8080/", "http://[::1]:8080"),
    ),
)
def test_candidate_base_url_accepts_literal_loopback_origins(
    value: str,
    expected: str,
) -> None:
    assert candidate_smoke.normalize_base_url(value) == expected


@pytest.mark.parametrize(
    "value",
    (
        "ftp://127.0.0.1:18091",
        "http://127.0.0.1",
        "http://localhost:18091",
        "https://globemind.top:443",
        "http://0.0.0.0:18091",
        "http://126.255.255.255:18091",
        "http://128.0.0.0:18091",
        "http://192.168.1.10:18091",
        "http://[::]:18091",
        "http://[::ffff:127.0.0.1]:18091",
        "http://[::1%25lo]:18091",
        "http://user:password@127.0.0.1:18091",
        "http://127.0.0.1:18091/candidate",
        "http://127.0.0.1:18091?token=value",
        "http://127.0.0.1:18091?",
        "http://127.0.0.1:18091#fragment",
        "http://127.0.0.1:18091#",
        "http://127.0.0.1:not-a-port",
        "http://127.0.0.1:65536",
    ),
)
def test_candidate_base_url_rejects_unsafe_targets(value: str) -> None:
    with pytest.raises(ValueError):
        candidate_smoke.normalize_base_url(value)
