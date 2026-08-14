from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.features import (
    FeatureHealthCheck,
    build_feature_health_report,
    build_public_status_report,
    probe_postgres_relations,
    run_feature_probe,
)
from api.features.assistant import probe_assistant_health
from api.features.dashboard import probe_dashboard_health
from api.features.financial import probe_financial_health
from api.features.freshness import apply_freshness
from api.features.graph_briefing import probe_graph_briefing_health
from api.features.ground_news import probe_ground_news_health
from api.features.identity import probe_identity_health
from api.features.search import probe_search_health
from api.features.story_graph import (
    STORY_GRAPH_HEALTH_RELATIONS,
    probe_story_graph_health,
)
from api.routes import dashboard

EXPECTED_STORY_GRAPH_RELATIONS = {
    "public.event_l2_chains": ("run_id", "chain_id", "title"),
    "public.event_l2_chain_segments": (
        "run_id",
        "chain_id",
        "segment_id",
        "l15_run_id",
    ),
    "public.event_l15_segments": ("segment_id", "l1_cluster_id", "title"),
    "public.event_l15_members": ("run_id", "segment_id", "news_id"),
    "public.event_l3_macro_events": ("run_id", "macro_id", "title"),
    "public.event_l3_macro_members": (
        "run_id",
        "macro_id",
        "l2_run_id",
        "l2_chain_id",
    ),
    "public.event_l3_macro_edges": (
        "run_id",
        "macro_id",
        "from_chain_id",
        "to_chain_id",
    ),
    "public.event_coref_clusters": ("cluster_id", "title"),
    "public.event_coref_members": ("cluster_id", "news_id"),
    "public.news": ("id", "title", "published_at", "url"),
}


class _Result:
    def first(self) -> None:
        return None

    def scalar(self):
        return datetime.now(timezone.utc)


class _Session:
    def __init__(self, *, fail_on: str | None = None) -> None:
        self.fail_on = fail_on
        self.statements: list[str] = []

    def execute(self, statement: Any) -> _Result:
        sql = str(statement)
        self.statements.append(sql)
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("sensitive database failure")
        return _Result()


def _check(feature_id: str, status: str = "up") -> FeatureHealthCheck:
    return FeatureHealthCheck(
        feature_id=feature_id,
        status=status,
        latency_ms=0,
        dependencies=[f"test:{feature_id}"],
        detail="capability probe failed" if status == "down" else None,
    )


def test_story_graph_health_relation_contract_is_exact() -> None:
    assert STORY_GRAPH_HEALTH_RELATIONS == EXPECTED_STORY_GRAPH_RELATIONS


def test_database_feature_probes_execute_real_relation_reads() -> None:
    db = _Session()

    checks = (
        probe_identity_health(db),
        probe_dashboard_health(db),
        probe_search_health(db),
        probe_graph_briefing_health(db),
        probe_story_graph_health(
            lambda: probe_postgres_relations(db, STORY_GRAPH_HEALTH_RELATIONS)
        ),
        probe_ground_news_health(db),
    )

    assert all(check.status == "up" for check in checks)
    assert len(db.statements) == 32
    assert all(statement.startswith("SELECT ") for statement in db.statements)
    relation_reads = [statement for statement in db.statements if "MAX(published_at)" not in statement]
    assert all(statement.endswith(" LIMIT 1") for statement in relation_reads)
    assert (
        "SELECT run_id, macro_id, from_chain_id, to_chain_id "
        "FROM public.event_l3_macro_edges LIMIT 1"
    ) in db.statements
    assert "SELECT run_id, chain_id, title FROM public.event_l2_chains LIMIT 1" in db.statements
    assert "SELECT id, title, published_at, url FROM public.news LIMIT 1" in db.statements


def test_story_graph_probe_fails_closed_when_current_graph_relation_cannot_be_read() -> None:
    db = _Session(fail_on="public.event_l3_macro_edges")

    check = probe_story_graph_health(
        lambda: probe_postgres_relations(db, STORY_GRAPH_HEALTH_RELATIONS)
    )

    assert check.status == "down"
    assert check.detail == "capability probe failed"
    assert check.metrics == {}
    assert any("FROM public.event_l3_macro_edges" in statement for statement in db.statements)


def test_database_probe_failure_is_isolated_and_redacted() -> None:
    check = probe_search_health(_Session(fail_on="event_l2_chains"))

    assert check.status == "down"
    assert check.detail == "capability probe failed"
    assert "sensitive" not in json.dumps(check.model_dump())


def test_assistant_probe_checks_storage_and_scheduler(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("GLOBEMIND_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    monkeypatch.delenv("ASSISTANT_SCHEDULE_RUNNER_LOCK", raising=False)
    monkeypatch.delenv("ASSISTANT_SCHEDULE_RUNNER_STATUS", raising=False)

    healthy = probe_assistant_health({"enabled": False, "healthy": True, "state": "disabled"})
    unhealthy = probe_assistant_health({"enabled": True, "healthy": False, "state": "unavailable"})

    assert healthy.status == "up"
    assert healthy.metrics["paths_checked"] == 3
    assert healthy.metrics["scheduler_state"] == "disabled"
    assert unhealthy.status == "down"


def test_financial_probe_rejects_corrupt_store(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    rules = tmp_path / "rules.json"
    history = tmp_path / "history.json"
    rules.write_text("[]\n", encoding="utf-8")
    history.write_text("not-json\n", encoding="utf-8")
    monkeypatch.setenv("FINANCIAL_ALERT_RULES_STORE", str(rules))
    monkeypatch.setenv("FINANCIAL_ALERT_HISTORY_STORE", str(history))

    assert probe_financial_health().status == "down"

    history.write_text("[]\n", encoding="utf-8")
    healthy = probe_financial_health()
    assert healthy.status == "up"
    assert healthy.metrics["stores_parsed"] == 2


def test_feature_health_report_rejects_duplicate_feature_ids() -> None:
    with pytest.raises(ValueError, match="duplicate feature health check"):
        build_feature_health_report((_check("search"), _check("search")))


def test_probe_runner_redacts_exception_detail() -> None:
    def fail() -> None:
        raise RuntimeError("database password appeared here")

    check = run_feature_probe("test", ("test:dependency",), fail)

    assert check.status == "down"
    assert check.detail == "capability probe failed"
    assert "password" not in json.dumps(check.model_dump())


def test_freshness_marks_old_business_data_stale_with_auditable_metrics() -> None:
    now = datetime(2026, 7, 31, tzinfo=timezone.utc)
    check = apply_freshness(
        _check("search"),
        now - timedelta(hours=72),
        sla_hours=48,
        metric_name="latest_news_at",
        now=now,
    )

    assert check.status == "stale"
    assert check.detail == "business data freshness threshold exceeded"
    assert check.metrics["freshness_status"] == "stale"
    assert check.metrics["freshness_lag_hours"] == 72.0
    assert check.metrics["freshness_threshold_approval_state"] == "not_approved"


def test_date_only_freshness_uses_conservative_utc_day_start() -> None:
    now = datetime(2026, 8, 9, 9, tzinfo=timezone.utc)
    check = apply_freshness(
        _check("opinion-analysis"),
        now.date(),
        sla_hours=24,
        metric_name="latest_score_date",
        now=now,
    )

    assert check.metrics["latest_score_date"] == "2026-08-09T00:00:00+00:00"
    assert check.metrics["freshness_lag_hours"] == 9.0


def test_feature_health_report_keeps_stale_capabilities_available() -> None:
    report = build_feature_health_report((_check("search", "stale"), _check("identity")))

    assert report.status == "degraded"
    assert report.ready is True


def test_public_status_exposes_only_research_freshness_and_honest_slo_gaps() -> None:
    current = FeatureHealthCheck(
        feature_id="search",
        status="up",
        latency_ms=91.2,
        dependencies=["postgres:private-search-relation"],
        metrics={
            "latest_news_at": "2026-08-09T08:00:00+00:00",
            "freshness_lag_hours": 1.0,
            "freshness_sla_hours": 48,
            "freshness_status": "current",
            "relations_checked": 4,
        },
    )
    stale = FeatureHealthCheck(
        feature_id="ground-news",
        status="stale",
        latency_ms=12,
        dependencies=["filesystem:/private/path"],
        metrics={
            "latest_story_source_at": "2026-07-22T10:30:00+00:00",
            "freshness_lag_hours": 430.5,
            "freshness_sla_hours": 48,
            "freshness_status": "stale",
            "paths_checked": 3,
        },
    )
    missing = FeatureHealthCheck(
        feature_id="opinion-analysis",
        status="stale",
        latency_ms=4,
        dependencies=["postgres:private-opinion-relation"],
        metrics={"freshness_status": "missing", "freshness_sla_hours": 72},
    )
    internal = build_feature_health_report((current, stale, missing, _check("identity")))

    payload = build_public_status_report(
        internal,
        generated_at=datetime(2026, 8, 9, 9, tzinfo=timezone.utc),
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["schema_version"] == "globemind.public-status.v1"
    assert payload["status"] == "unavailable"
    assert payload["research_mode"] == "historical"
    assert set(payload["checks"]) == {"search", "ground-news", "opinion-analysis"}
    assert payload["checks"]["search"]["research_use"] == "current"
    assert payload["checks"]["ground-news"]["research_use"] == "historical"
    assert payload["checks"]["search"]["module_evidence"] == {
        "schema_version": "globemind.home-module-evidence.v1",
        "module_id": "home-data-search",
        "scope": {
            "id": "public-news-event-search",
            "label": "公开新闻与事件检索结果",
        },
        "cutoff_metric": "latest_news_at",
        "cutoff_status": "available",
        "method": {
            "id": "business-freshness-health-projection",
            "version": "v1",
            "status": "configured",
        },
        "evidence_status": "contract_validated",
    }
    assert payload["checks"]["opinion-analysis"]["module_evidence"][
        "cutoff_status"
    ] == "unknown"
    assert payload["checks"]["opinion-analysis"]["module_evidence"][
        "evidence_status"
    ] == "unavailable"
    assert {
        item["metrics"]["freshness_status"] for item in payload["checks"].values()
    } <= {"live", "delayed", "stale", "offline"}
    freshness = payload["objectives"]["freshness"]
    assert freshness[0]["threshold_assessment"] == "within"
    assert freshness[1]["threshold_assessment"] == "exceeded"
    assert all(item["objective"] is None for item in freshness)
    assert all(item["compliance"] == "not_computable" for item in freshness)
    assert all(item["approval_state"] == "not_approved" for item in freshness)
    assert all(item["objective"] is None for item in payload["objectives"]["workflows"])
    assert "private" not in serialized
    assert "dependencies" not in serialized
    assert "latency_ms" not in serialized
    assert "relations_checked" not in serialized
    assert "paths_checked" not in serialized


def test_public_status_fails_closed_on_invalid_live_freshness_metadata() -> None:
    checks = (
        FeatureHealthCheck(
            feature_id="search",
            status="up",
            latency_ms=1,
            dependencies=["test"],
            metrics={
                "freshness_status": "live",
                "freshness_lag_hours": 1,
                "freshness_sla_hours": 48,
            },
        ),
        FeatureHealthCheck(
            feature_id="ground-news",
            status="up",
            latency_ms=1,
            dependencies=["test"],
            metrics={
                "freshness_status": "live",
                "latest_story_source_at": "2026-08-10T12:00:00+00:00",
                "freshness_lag_hours": float("nan"),
                "freshness_sla_hours": 48,
            },
        ),
        FeatureHealthCheck(
            feature_id="opinion-analysis",
            status="up",
            latency_ms=1,
            dependencies=["test"],
            metrics={
                "freshness_status": "live",
                "latest_score_date": "2026-08-09",
                "freshness_lag_hours": 1,
                "freshness_sla_hours": float("inf"),
            },
        ),
    )

    payload = build_public_status_report(
        build_feature_health_report(checks),
        generated_at=datetime(2026, 8, 9, 9, tzinfo=timezone.utc),
    )
    serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False)

    assert payload["status"] == "unavailable"
    assert payload["ready"] is False
    assert all(
        check["metrics"]["freshness_status"] == "offline"
        for check in payload["checks"].values()
    )
    assert "NaN" not in serialized
    assert "Infinity" not in serialized


def test_public_status_rejects_cutoff_lag_mismatch() -> None:
    inconsistent = FeatureHealthCheck(
        feature_id="search",
        status="up",
        latency_ms=1,
        dependencies=["test"],
        metrics={
            "freshness_status": "live",
            "latest_news_at": "2026-07-01T00:00:00Z",
            "freshness_lag_hours": 1,
            "freshness_sla_hours": 48,
        },
    )

    payload = build_public_status_report(
        build_feature_health_report((inconsistent,)),
        generated_at=datetime(2026, 8, 9, 9, tzinfo=timezone.utc),
    )

    assert payload["checks"]["search"]["status"] == "down"
    assert payload["checks"]["search"]["research_use"] == "unavailable"
    assert payload["checks"]["search"]["metrics"]["freshness_status"] == "offline"
    assert payload["checks"]["search"]["module_evidence"]["cutoff_status"] == (
        "available"
    )
    assert payload["checks"]["search"]["module_evidence"]["evidence_status"] == (
        "unavailable"
    )


def test_public_status_does_not_publish_lag_without_a_cutoff_anchor() -> None:
    offline = FeatureHealthCheck(
        feature_id="search",
        status="down",
        latency_ms=1,
        dependencies=["test"],
        metrics={
            "freshness_status": "offline",
            "freshness_lag_hours": 1,
            "freshness_sla_hours": 48,
        },
    )

    payload = build_public_status_report(
        build_feature_health_report((offline,)),
        generated_at=datetime(2026, 8, 9, 9, tzinfo=timezone.utc),
    )

    metrics = payload["checks"]["search"]["metrics"]
    assert metrics["freshness_status"] == "offline"
    assert "latest_news_at" not in metrics
    assert "freshness_lag_hours" not in metrics
    objective = payload["objectives"]["freshness"][0]
    assert objective["observed"] is None
    assert objective["threshold_assessment"] == "unknown"


def test_public_status_discloses_unknown_incident_response_for_offline_capability() -> None:
    checks = (
        FeatureHealthCheck(
            feature_id="search",
            status="down",
            latency_ms=1,
            dependencies=["test"],
            metrics={"freshness_status": "offline", "freshness_sla_hours": 48},
        ),
        FeatureHealthCheck(
            feature_id="ground-news",
            status="up",
            latency_ms=1,
            dependencies=["test"],
            metrics={
                "freshness_status": "live",
                "latest_story_source_at": "2026-08-09T08:00:00Z",
                "freshness_lag_hours": 1,
                "freshness_sla_hours": 48,
            },
        ),
        FeatureHealthCheck(
            feature_id="opinion-analysis",
            status="up",
            latency_ms=1,
            dependencies=["test"],
            metrics={
                "freshness_status": "live",
                "latest_score_date": "2026-08-09T08:00:00Z",
                "freshness_lag_hours": 1,
                "freshness_sla_hours": 48,
            },
        ),
    )
    generated_at = datetime(2026, 8, 9, 9, tzinfo=timezone.utc)

    payload = build_public_status_report(
        build_feature_health_report(checks),
        generated_at=generated_at,
    )

    assert payload["degradation_disclosure"] == {
        "status": "action_required",
        "trigger": {
            "capability_state": "down_observed",
            "affected_capability_ids": ["search"],
            "workflow_breach_state": "unknown",
            "affected_workflow_ids": [],
        },
        "incident_owner": {"availability": "unavailable", "value": None},
        "recovery_estimate": {"availability": "unavailable", "value": None},
        "last_status_update": {"availability": "unavailable", "value": None},
        "reason": (
            "已观测到公开能力离线；事件负责人、恢复预计和最近状态更新"
            "均无可验证公开证据。工作流违约状态未知，因为没有经批准目标。"
        ),
    }
    assert payload["degradation_disclosure"]["recovery_estimate"]["value"] is None
    assert payload["degradation_disclosure"]["last_status_update"]["value"] is None
    assert payload["objectives"]["freshness"][0]["approval_state"] == "not_approved"
    assert payload["objectives"]["freshness"][0]["compliance"] == "not_computable"


def _service_level_summary() -> dict[str, object]:
    def metrics(
        scope: str,
        *,
        success: int = 0,
        error: int = 0,
    ) -> dict[str, object]:
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
            "p50_ms": 12 if count else None,
            "p95_ms": 25 if count else None,
            "p99_ms": 25 if count else None,
        }

    return {
        "schema_version": "globemind.service-level.v1",
        "measurement_method_version": (
            "http-route-template-duration-nearest-rank-v1"
        ),
        "generated_at": "2026-08-09T09:00:00Z",
        "measurement_state": "observed",
        "storage_state": "available",
        "integrity_state": "verified",
        "window": {
            "starts_at": "2026-08-08T09:00:00Z",
            "ends_at": "2026-08-09T09:00:00Z",
            "hours": 24,
        },
        "overall": metrics("overall", success=1, error=1),
        "operations": [
            metrics("search", success=1, error=1),
            metrics("export"),
            metrics("report"),
        ],
        "instrumentation_write_failure_count": 0,
        "instrumentation_write_state": "no_failures_observed",
        "target": {
            "approval_state": "not_approved",
            "compliance": "not_computable",
            "targets_configured": False,
            "approver_evidence_state": "absent",
        },
    }


def test_public_status_projects_bounded_service_measurement_without_slo_claim() -> None:
    payload = build_public_status_report(
        build_feature_health_report((_check("identity"),)),
        generated_at=datetime(2026, 8, 9, 9, tzinfo=timezone.utc),
        service_level_summary=_service_level_summary(),
    )
    workflows = payload["objectives"]["workflows"]

    assert [item["id"] for item in workflows] == [
        "search-response",
        "export-delivery",
        "report-generation",
    ]
    assert workflows[0]["measurement_status"] == "observed"
    assert workflows[0]["observed"] == {
        "sample_count": 2,
        "success_count": 1,
        "error_count": 1,
        "timeout_count": 0,
        "cancelled_count": 0,
        "success_rate": 0.5,
        "error_rate": 0.5,
        "p50_ms": 12,
        "p95_ms": 25,
        "p99_ms": 25,
    }
    assert workflows[1]["measurement_status"] == "not_observed"
    assert workflows[1]["observed"] is None
    assert all(item["objective"] is None for item in workflows)
    assert all(item["compliance"] == "not_computable" for item in workflows)
    assert all(item["approval_state"] == "not_approved" for item in workflows)


def test_public_status_rejects_contradictory_service_measurement() -> None:
    summary = _service_level_summary()
    summary["operations"][0]["success_rate"] = 1.0

    payload = build_public_status_report(
        build_feature_health_report((_check("identity"),)),
        generated_at=datetime(2026, 8, 9, 9, tzinfo=timezone.utc),
        service_level_summary=summary,
    )

    assert all(
        item["measurement_status"] == "unavailable"
        and item["observed"] is None
        and item["compliance"] == "not_computable"
        for item in payload["objectives"]["workflows"]
    )

    stale = _service_level_summary()
    stale["generated_at"] = "2026-08-09T08:00:00Z"
    stale["window"]["ends_at"] = "2026-08-09T08:00:00Z"
    stale["window"]["starts_at"] = "2026-08-08T08:00:00Z"
    payload = build_public_status_report(
        build_feature_health_report((_check("identity"),)),
        generated_at=datetime(2026, 8, 9, 9, tzinfo=timezone.utc),
        service_level_summary=stale,
    )
    assert all(
        item["measurement_status"] == "unavailable"
        and item["observed"] is None
        for item in payload["objectives"]["workflows"]
    )


def test_feature_health_http_contract_returns_503_when_one_feature_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        dashboard,
        "get_schedule_runner_status",
        lambda: {"enabled": True, "healthy": True, "state": "running"},
    )
    monkeypatch.setattr(dashboard, "probe_identity_health", lambda _db: _check("identity"))
    monkeypatch.setattr(dashboard, "probe_dashboard_health", lambda _db: _check("dashboard"))
    monkeypatch.setattr(
        dashboard,
        "probe_assistant_health",
        lambda _status: _check("assistant"),
    )
    monkeypatch.setattr(dashboard, "probe_search_health", lambda _db: _check("search", "down"))
    monkeypatch.setattr(dashboard, "probe_financial_health", lambda: _check("financial-alerts"))
    monkeypatch.setattr(
        dashboard,
        "probe_graph_briefing_health",
        lambda _db: _check("graph-briefing"),
    )
    monkeypatch.setattr(
        dashboard,
        "probe_story_graph_health",
        lambda _probe: _check("story-graph"),
    )
    monkeypatch.setattr(
        dashboard,
        "probe_ground_news_health",
        lambda _db: _check("ground-news"),
    )
    monkeypatch.setattr(
        dashboard,
        "probe_opinion_health",
        lambda _db: _check("opinion-analysis"),
    )
    monkeypatch.setattr(
        dashboard,
        "probe_operations_health",
        lambda: _check("operations"),
    )

    app = FastAPI()
    app.include_router(dashboard.router)

    def database_override():
        yield object()

    app.dependency_overrides[dashboard.get_db] = database_override
    with TestClient(app) as client:
        unauthenticated = client.get("/api/health/features")
        public_response = client.get("/api/status")

    assert unauthenticated.status_code == 401
    assert public_response.status_code == 200
    assert set(public_response.json()["checks"]) == {
        "search",
        "ground-news",
        "opinion-analysis",
    }

    app.dependency_overrides[dashboard.get_current_user_required] = lambda: {
        "user_id": 1,
        "username": "candidate",
        "role": "user",
    }
    with TestClient(app) as client:
        response = client.get("/api/health/features")
    payload = response.json()

    assert response.status_code == 503
    assert payload["status"] == "unhealthy"
    assert payload["ready"] is False
    assert set(payload["checks"]) == {
        "identity",
        "dashboard",
        "assistant",
        "search",
        "financial-alerts",
        "graph-briefing",
        "story-graph",
        "ground-news",
        "opinion-analysis",
        "operations",
    }

    monkeypatch.setattr(
        dashboard,
        "probe_search_health",
        lambda _db: _check("search"),
    )
    with TestClient(app) as client:
        healthy_response = client.get("/api/health/features")

    assert healthy_response.status_code == 200
    assert healthy_response.json()["ready"] is True
