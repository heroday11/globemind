"""Public, research-facing status contract.

The detailed feature health report is an operational diagnostic. This module
derives the smaller public contract needed to decide whether current data is
safe to use without exposing dependency names, relation counts, filesystem
facts, probe latency, or scheduler state.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from api.features.health import FeatureHealthCheck, FeatureHealthReport
from api.features.operations import unconfigured_public_maintenance_history

PUBLIC_STATUS_SCHEMA_VERSION = "globemind.public-status.v1"
PUBLIC_HOME_MODULE_EVIDENCE_SCHEMA_VERSION = "globemind.home-module-evidence.v1"
_PUBLIC_HOME_MODULE_METHOD_ID = "business-freshness-health-projection"
_PUBLIC_HOME_MODULE_METHOD_VERSION = "v1"

_PUBLIC_FEATURES: dict[str, dict[str, str]] = {
    "search": {
        "label": "新闻与事件检索",
        "cutoff_metric": "latest_news_at",
        "module_id": "home-data-search",
        "scope_id": "public-news-event-search",
        "scope_label": "公开新闻与事件检索结果",
    },
    "ground-news": {
        "label": "全球新闻观察",
        "cutoff_metric": "latest_story_source_at",
        "module_id": "home-ground-news",
        "scope_id": "public-ground-news-story-sources",
        "scope_label": "公开事件卡、报道与来源构成",
    },
    "opinion-analysis": {
        "label": "涉华舆情分析",
        "cutoff_metric": "latest_score_date",
        "module_id": "home-opinion-analysis",
        "scope_id": "public-opinion-analysis-scores",
        "scope_label": "公开涉华舆情聚合结果",
    },
}

_WORKFLOW_MEASUREMENT_GAPS = (
    {
        "id": "search-response",
        "label": "检索响应",
        "indicator": "端到端检索成功率与延迟",
        "measurement_status": "unavailable",
        "objective": None,
        "observed": None,
        "compliance": "not_computable",
        "approval_state": "not_approved",
        "reason": "服务级观测证据当前不可用，且尚无经批准目标。",
    },
    {
        "id": "export-delivery",
        "label": "导出交付",
        "indicator": "导出成功率与完成时间",
        "measurement_status": "unavailable",
        "objective": None,
        "observed": None,
        "compliance": "not_computable",
        "approval_state": "not_approved",
        "reason": "服务级观测证据当前不可用，且尚无经批准目标。",
    },
    {
        "id": "report-generation",
        "label": "报告生成",
        "indicator": "报告成功率与完成时间",
        "measurement_status": "unavailable",
        "objective": None,
        "observed": None,
        "compliance": "not_computable",
        "approval_state": "not_approved",
        "reason": "服务级观测证据当前不可用，且尚无经批准目标。",
    },
)

_WORKFLOW_DEFINITIONS = {
    "search": {
        "id": "search-response",
        "label": "检索响应",
        "indicator": "端到端检索成功率与延迟",
    },
    "export": {
        "id": "export-delivery",
        "label": "导出交付",
        "indicator": "导出成功率与完成时间",
    },
    "report": {
        "id": "report-generation",
        "label": "报告生成",
        "indicator": "报告成功率与完成时间",
    },
}

_SERVICE_LEVEL_SCHEMA_VERSION = "globemind.service-level.v1"
_SERVICE_LEVEL_METHOD_VERSION = "http-route-template-duration-nearest-rank-v1"
_MAX_PUBLIC_EVIDENCE_AGE = timedelta(minutes=15)
_FRESHNESS_LAG_TOLERANCE_HOURS = 0.11


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return value


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _rate(value: Any) -> float | None:
    number = _number(value)
    if number is None or number > 1:
        return None
    return float(number)


def _workflow_metrics(value: Any, *, expected_scope: str) -> dict[str, Any] | None:
    if not isinstance(value, Mapping) or value.get("scope") != expected_scope:
        return None
    counts = {
        name: _integer(value.get(name))
        for name in (
            "sample_count",
            "success_count",
            "error_count",
            "timeout_count",
            "cancelled_count",
        )
    }
    if any(item is None for item in counts.values()):
        return None
    sample_count = counts["sample_count"]
    success_count = counts["success_count"]
    assert sample_count is not None and success_count is not None
    if sum(
        counts[name] or 0
        for name in (
            "success_count",
            "error_count",
            "timeout_count",
            "cancelled_count",
        )
    ) != sample_count:
        return None
    success_rate = value.get("success_rate")
    error_rate = value.get("error_rate")
    percentiles = {
        name: value.get(name)
        for name in ("p50_ms", "p95_ms", "p99_ms")
    }
    if sample_count == 0:
        if success_rate is not None or error_rate is not None:
            return None
        if any(item is not None for item in percentiles.values()):
            return None
        normalized_success_rate = None
        normalized_error_rate = None
    else:
        normalized_success_rate = _rate(success_rate)
        normalized_error_rate = _rate(error_rate)
        if normalized_success_rate is None or normalized_error_rate is None:
            return None
        expected_success_rate = success_count / sample_count
        if (
            abs(normalized_success_rate - expected_success_rate) > 1e-12
            or abs(normalized_error_rate - (1 - expected_success_rate)) > 1e-12
        ):
            return None
        normalized_percentiles = {
            name: _integer(item) for name, item in percentiles.items()
        }
        if any(item is None for item in normalized_percentiles.values()):
            return None
        if not (
            normalized_percentiles["p50_ms"]
            <= normalized_percentiles["p95_ms"]
            <= normalized_percentiles["p99_ms"]
        ):
            return None
        percentiles = normalized_percentiles
    if (
        value.get("error_rate_definition") != "all_non_success_outcomes"
        or value.get("percentile_method") != "nearest_rank"
    ):
        return None
    return {
        **counts,
        "success_rate": normalized_success_rate,
        "error_rate": normalized_error_rate,
        "p50_ms": percentiles["p50_ms"],
        "p95_ms": percentiles["p95_ms"],
        "p99_ms": percentiles["p99_ms"],
    }


def _workflow_window_is_current(
    summary: Mapping[str, Any],
    *,
    evaluated_at: datetime,
) -> bool:
    generated_at = _parsed_timestamp(summary.get("generated_at"))
    window = summary.get("window")
    if generated_at is None or not isinstance(window, Mapping):
        return False
    starts_at = _parsed_timestamp(window.get("starts_at"))
    ends_at = _parsed_timestamp(window.get("ends_at"))
    hours = window.get("hours")
    if (
        starts_at is None
        or ends_at is None
        or isinstance(hours, bool)
        or not isinstance(hours, int)
        or hours < 1
        or hours > 24 * 30
        or generated_at > evaluated_at + timedelta(minutes=5)
        or evaluated_at - generated_at > _MAX_PUBLIC_EVIDENCE_AGE
        or abs((ends_at - generated_at).total_seconds()) > 1
        or abs((ends_at - starts_at).total_seconds() - hours * 3600) > 1
    ):
        return False
    return True


def _workflow_objectives(
    summary: Mapping[str, Any] | None,
    *,
    evaluated_at: datetime,
) -> list[dict[str, Any]]:
    if not isinstance(summary, Mapping):
        return [dict(item) for item in _WORKFLOW_MEASUREMENT_GAPS]
    target = summary.get("target")
    operations = summary.get("operations")
    overall = _workflow_metrics(summary.get("overall"), expected_scope="overall")
    failure_count = _integer(summary.get("instrumentation_write_failure_count"))
    if (
        summary.get("schema_version") != _SERVICE_LEVEL_SCHEMA_VERSION
        or summary.get("measurement_method_version") != _SERVICE_LEVEL_METHOD_VERSION
        or not _workflow_window_is_current(summary, evaluated_at=evaluated_at)
        or summary.get("measurement_state") not in {"not_observed", "observed"}
        or summary.get("storage_state") not in {"not_initialized", "available"}
        or summary.get("integrity_state") != "verified"
        or not isinstance(target, Mapping)
        or dict(target)
        != {
            "approval_state": "not_approved",
            "compliance": "not_computable",
            "targets_configured": False,
            "approver_evidence_state": "absent",
        }
        or failure_count is None
        or summary.get("instrumentation_write_state")
        != ("failures_observed" if failure_count else "no_failures_observed")
        or not isinstance(operations, list)
        or len(operations) != len(_WORKFLOW_DEFINITIONS)
        or overall is None
    ):
        return [dict(item) for item in _WORKFLOW_MEASUREMENT_GAPS]

    normalized: dict[str, dict[str, Any]] = {}
    for value in operations:
        if not isinstance(value, Mapping):
            return [dict(item) for item in _WORKFLOW_MEASUREMENT_GAPS]
        scope = value.get("scope")
        if scope not in _WORKFLOW_DEFINITIONS or scope in normalized:
            return [dict(item) for item in _WORKFLOW_MEASUREMENT_GAPS]
        metrics = _workflow_metrics(value, expected_scope=str(scope))
        if metrics is None:
            return [dict(item) for item in _WORKFLOW_MEASUREMENT_GAPS]
        normalized[str(scope)] = metrics
    if set(normalized) != set(_WORKFLOW_DEFINITIONS):
        return [dict(item) for item in _WORKFLOW_MEASUREMENT_GAPS]

    total_samples = sum(item["sample_count"] for item in normalized.values())
    for count_name in (
        "sample_count",
        "success_count",
        "error_count",
        "timeout_count",
        "cancelled_count",
    ):
        if overall[count_name] != sum(
            item[count_name] for item in normalized.values()
        ):
            return [dict(item) for item in _WORKFLOW_MEASUREMENT_GAPS]
    expected_state = "observed" if total_samples else "not_observed"
    if (
        summary.get("measurement_state") != expected_state
        or (
            summary.get("storage_state") == "not_initialized"
            and (total_samples or failure_count)
        )
    ):
        return [dict(item) for item in _WORKFLOW_MEASUREMENT_GAPS]

    items: list[dict[str, Any]] = []
    for scope, definition in _WORKFLOW_DEFINITIONS.items():
        metrics = normalized[scope]
        sample_count = metrics["sample_count"]
        if failure_count:
            measurement_status = "partial" if sample_count else "unavailable"
            reason = (
                "已取得部分持久观测，但记录器报告写入失败；"
                "且尚无经批准目标，不能判定达标。"
            )
        elif sample_count:
            measurement_status = "observed"
            reason = "已取得持久聚合观测；尚无经批准目标，不能判定达标。"
        else:
            measurement_status = "not_observed"
            reason = "观测账本尚无该工作流样本，且尚无经批准目标。"
        items.append(
            {
                **definition,
                "measurement_status": measurement_status,
                "objective": None,
                "observed": metrics if sample_count else None,
                "compliance": "not_computable",
                "approval_state": "not_approved",
                "reason": reason,
                "source": "持久化服务级观测（脱敏聚合）",
            }
        )
    return items


def _parsed_timestamp(
    value: Any,
    *,
    allow_date_only: bool = False,
) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        if allow_date_only and len(raw) == 10:
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            return None
    return parsed.astimezone(timezone.utc)


def _timestamp(
    value: Any,
    *,
    evaluated_at: datetime,
    allow_date_only: bool = False,
) -> tuple[str | None, datetime | None]:
    parsed = _parsed_timestamp(value, allow_date_only=allow_date_only)
    if parsed is None:
        return None, None
    if parsed > evaluated_at + timedelta(minutes=5):
        return None, None
    return str(value).strip(), parsed


def _public_state(check: FeatureHealthCheck | None) -> str:
    if check is None or check.status == "down":
        return "offline"
    raw = str(check.metrics.get("freshness_status") or "").strip().lower()
    if raw == "missing":
        return "offline"
    if raw == "stale" or check.status == "stale":
        return "stale"
    if raw == "delayed" or check.status == "degraded":
        return "delayed"
    if raw in {"current", "live"} and check.status == "up":
        return "live"
    return "offline"


def _public_check(
    feature_id: str,
    check: FeatureHealthCheck | None,
    *,
    evaluated_at: datetime,
) -> dict[str, Any]:
    definition = _PUBLIC_FEATURES[feature_id]
    state = _public_state(check)
    raw_metrics: Mapping[str, Any] = check.metrics if check is not None else {}
    cutoff_metric = definition["cutoff_metric"]
    cutoff, cutoff_timestamp = _timestamp(
        raw_metrics.get(cutoff_metric),
        evaluated_at=evaluated_at,
        allow_date_only=cutoff_metric == "latest_score_date",
    )
    lag_hours = _number(raw_metrics.get("freshness_lag_hours"))
    objective_hours = _number(raw_metrics.get("freshness_sla_hours"))
    if objective_hours == 0:
        objective_hours = None
    if cutoff is None:
        if state != "offline":
            state = "offline"
        # Lag is only meaningful when it can be recomputed from a bounded
        # cutoff. Never publish a free-floating number as freshness evidence.
        lag_hours = None
    elif state != "offline" and (
        lag_hours is None
        or cutoff_timestamp is None
        or abs(
            lag_hours
            - max(
                0.0,
                (evaluated_at - cutoff_timestamp).total_seconds() / 3600,
            )
        )
        > _FRESHNESS_LAG_TOLERANCE_HOURS
    ):
        state = "offline"
        lag_hours = None
    elif state == "live" and (
        objective_hours is None
        or lag_hours > objective_hours
    ):
        state = "offline"
    elif state == "stale" and (
        objective_hours is None or lag_hours <= objective_hours
    ):
        state = "offline"
        lag_hours = None

    capability_status = {
        "live": "up",
        "delayed": "degraded",
        "stale": "stale",
        "offline": "down",
    }[state]
    detail = {
        "live": "数据在当前更新时限内。",
        "delayed": "数据更新延迟，仅应按受限资料使用。",
        "stale": "数据已超过更新时限，仅应按历史资料使用。",
        "offline": (
            "当前能力离线或无法确认数据截止时间，请勿用于当前判断。"
        ),
    }[state]

    metrics: dict[str, Any] = {
        "freshness_status": state,
    }
    if cutoff is not None:
        metrics[cutoff_metric] = cutoff
    if lag_hours is not None:
        metrics["freshness_lag_hours"] = lag_hours
    if objective_hours is not None:
        metrics["freshness_sla_hours"] = objective_hours

    module_evidence = {
        "schema_version": PUBLIC_HOME_MODULE_EVIDENCE_SCHEMA_VERSION,
        "module_id": definition["module_id"],
        "scope": {
            "id": definition["scope_id"],
            "label": definition["scope_label"],
        },
        "cutoff_metric": cutoff_metric,
        "cutoff_status": "available" if cutoff is not None else "unknown",
        "method": {
            "id": _PUBLIC_HOME_MODULE_METHOD_ID,
            "version": _PUBLIC_HOME_MODULE_METHOD_VERSION,
            "status": "configured",
        },
        "evidence_status": (
            "contract_validated"
            if cutoff is not None and state != "offline"
            else "unavailable"
        ),
    }

    return {
        "feature_id": feature_id,
        "label": definition["label"],
        "status": capability_status,
        "research_use": (
            "current"
            if state == "live"
            else "unavailable"
            if state == "offline"
            else "historical"
        ),
        "detail": detail,
        "metrics": metrics,
        "module_evidence": module_evidence,
    }


def _freshness_objective(check: Mapping[str, Any]) -> dict[str, Any]:
    metrics = check["metrics"]
    objective_hours = _number(metrics.get("freshness_sla_hours"))
    lag_hours = _number(metrics.get("freshness_lag_hours"))
    if objective_hours is None or objective_hours <= 0:
        measurement_status = "partial" if lag_hours is not None else "unavailable"
        threshold = None
        threshold_assessment = "unknown"
    else:
        measurement_status = "active" if lag_hours is not None else "partial"
        threshold_assessment = (
            "unknown"
            if lag_hours is None
            else "within"
            if lag_hours <= objective_hours
            else "exceeded"
        )
        threshold = {
            "comparison": "less_than_or_equal",
            "value": objective_hours,
            "unit": "hours",
        }
    return {
        "id": f"{check['feature_id']}-freshness",
        "label": f"{check['label']}数据新鲜度",
        "indicator": "最后有效数据距当前时间",
        "measurement_status": measurement_status,
        "objective": None,
        "threshold": threshold,
        "observed": (
            {"value": lag_hours, "unit": "hours"} if lag_hours is not None else None
        ),
        "threshold_assessment": threshold_assessment,
        "compliance": "not_computable",
        "approval_state": "not_approved",
        "reason": (
            "内部更新时限可用于数据降级；它不是经批准 SLO，不能判定达标。"
            if threshold is not None
            else "尚无可验证更新时限，也没有经批准 SLO。"
        ),
        "source": "运行时业务新鲜度探针",
    }


def _degradation_disclosure(
    checks: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    affected_capability_ids = [
        feature_id
        for feature_id in _PUBLIC_FEATURES
        if checks[feature_id].get("status") == "down"
    ]
    action_required = bool(affected_capability_ids)
    return {
        "status": "action_required" if action_required else "monitoring",
        "trigger": {
            "capability_state": (
                "down_observed" if action_required else "no_down_observed"
            ),
            "affected_capability_ids": affected_capability_ids,
            # Workflow targets remain unapproved, so even an error observation
            # cannot honestly be promoted to an SLA breach.
            "workflow_breach_state": "unknown",
            "affected_workflow_ids": [],
        },
        "incident_owner": {"availability": "unavailable", "value": None},
        "recovery_estimate": {"availability": "unavailable", "value": None},
        "last_status_update": {"availability": "unavailable", "value": None},
        "reason": (
            "已观测到公开能力离线；事件负责人、恢复预计和最近状态更新"
            "均无可验证公开证据。工作流违约状态未知，因为没有经批准目标。"
            if action_required
            else "未观测到公开能力离线；这不证明没有事件。事件处置字段仍无可验证"
            "公开证据，工作流违约状态因目标未批准而保持未知。"
        ),
    }


def build_public_status_report(
    report: FeatureHealthReport,
    *,
    generated_at: datetime | None = None,
    service_level_summary: Mapping[str, Any] | None = None,
    maintenance_history: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a bounded public report from the internal feature report."""
    timestamp = generated_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    timestamp = timestamp.astimezone(timezone.utc)
    checks = {
        feature_id: _public_check(
            feature_id,
            report.checks.get(feature_id),
            evaluated_at=timestamp,
        )
        for feature_id in _PUBLIC_FEATURES
    }
    states = {
        str(check["metrics"].get("freshness_status") or "offline")
        for check in checks.values()
    }
    if "offline" in states:
        public_status = "unavailable"
    elif states == {"live"}:
        public_status = "current"
    else:
        public_status = "historical"

    return {
        "schema_version": PUBLIC_STATUS_SCHEMA_VERSION,
        "generated_at": timestamp.isoformat(),
        "status": public_status,
        "ready": all(check["status"] != "down" for check in checks.values()),
        "research_mode": "current" if public_status == "current" else "historical",
        "checks": checks,
        "objectives": {
            "freshness": [_freshness_objective(check) for check in checks.values()],
            "workflows": _workflow_objectives(
                service_level_summary,
                evaluated_at=timestamp,
            ),
        },
        "degradation_disclosure": _degradation_disclosure(checks),
        "incident_history": dict(
            maintenance_history or unconfigured_public_maintenance_history()
        ),
    }


__all__ = (
    "PUBLIC_HOME_MODULE_EVIDENCE_SCHEMA_VERSION",
    "PUBLIC_STATUS_SCHEMA_VERSION",
    "build_public_status_report",
)
