"""World-state numerical terminal API routes."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path as ApiPath, Query

from api.core.environment import int_setting, string_setting
from api.features.financial import (
    AlertHistoryEventNotFound,
    AlertRuleNotFound,
    AlertRulePayload,
    AlertTriageConflict,
    AlertTriageMutation,
    AlertTriageUnavailable,
    COMPOSITE_METHOD_VERSION,
    FinancialAlertService,
    FinancialAlertTriageService,
    HARD_MINIMUM_SOURCE_COVERAGE,
    JsonStoreError,
    MODEL_VERSION,
    TRUST_SCHEMA_VERSION,
    annotate_alert_history_with_triage,
    composite_method_card,
    dashboard_is_computable,
)
from api.features.financial import (
    clean_severity as _clean_severity,
)
from api.features.financial import (
    clean_trend as _clean_trend,
)
from api.features.financial import (
    enrich_rule as _enrich_rule,
)
from api.features.financial import (
    fsync_directory as _fsync_directory,
)
from api.features.financial import (
    history_message as _history_message,
)
from api.features.financial import (
    iso_timestamp as _iso,
)
from api.features.financial import (
    json_store_lock as _json_store_lock,
)
from api.features.financial import (
    metric_lookup_from_dashboard as _metric_lookup_from_dashboard,
)
from api.features.financial import (
    mutate_json_list as _mutate_json_list,
)
from api.features.financial import (
    read_json_list as _read_json_list,
)
from api.features.financial import (
    read_json_list_unlocked as _read_json_list_unlocked,
)
from api.features.financial import (
    store_lock_path as _store_lock_path,
)
from api.features.financial import (
    utc_now as _utc_now,
)
from api.features.financial import (
    write_json_list as _write_json_list,
)
from api.features.financial import (
    write_json_list_unlocked as _write_json_list_unlocked,
)
from api.services.auth import get_current_admin_user, get_current_user_required
from api.services.financial_terminal import (
    get_dashboard,
    get_indices,
    get_sources,
    get_watchlist,
)

__all__ = [
    "ALERT_HISTORY_COOLDOWN_HOURS",
    "ALERT_HISTORY_STORE",
    "ALERT_RULES_STORE",
    "FINANCIAL_ALERT_TRIAGE_ROOT",
    "AlertRulePayload",
    "AlertTriageMutation",
    "JsonStoreError",
    "_clean_severity",
    "_clean_trend",
    "_create_user_alert_rule",
    "_delete_user_alert_rule",
    "_enrich_rule",
    "_financial_alert_history",
    "_financial_alert_rules",
    "_fsync_directory",
    "_history_message",
    "_iso",
    "_json_store_lock",
    "_metric_lookup_from_dashboard",
    "_mutate_json_list",
    "_read_json_list",
    "_read_json_list_unlocked",
    "_refresh_alert_history_store",
    "_store_lock_path",
    "_update_user_alert_rule",
    "_utc_now",
    "_write_json_list",
    "_write_json_list_unlocked",
    "router",
]

router = APIRouter(prefix="/api/financial", tags=["世界状态数值终端"])

ALERT_RULES_STORE = Path(
    string_setting(
        "FINANCIAL_ALERT_RULES_STORE",
        "/root/data/web/cache/financial_alert_rules.json",
    )
)
ALERT_HISTORY_STORE = Path(
    string_setting(
        "FINANCIAL_ALERT_HISTORY_STORE",
        "/root/data/web/cache/financial_alert_history.json",
    )
)
FINANCIAL_ALERT_TRIAGE_ROOT = Path(
    string_setting(
        "FINANCIAL_ALERT_TRIAGE_ROOT",
        "/root/data/web/financial-alert-triage",
    )
)
ALERT_HISTORY_COOLDOWN_HOURS = int_setting(
    "FINANCIAL_ALERT_HISTORY_COOLDOWN_HOURS",
    6,
    minimum=0,
)


def _alert_service() -> FinancialAlertService:
    return FinancialAlertService(
        rules_path=ALERT_RULES_STORE,
        history_path=ALERT_HISTORY_STORE,
        cooldown_hours=ALERT_HISTORY_COOLDOWN_HOURS,
        dashboard_provider=get_dashboard,
    )


def _alert_triage_service() -> FinancialAlertTriageService:
    return FinancialAlertTriageService(
        ledger_root=FINANCIAL_ALERT_TRIAGE_ROOT,
        alert_history_path=ALERT_HISTORY_STORE,
    )


def _canonical_actor_user_id(user: Dict[str, Any]) -> int:
    value = user.get("user_id")
    if type(value) is not int or value <= 0:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "TRIAGE_ACTOR_USER_ID_INVALID",
                "message": "管理员身份缺少稳定用户 ID。",
            },
        )
    return value


def _triage_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AlertHistoryEventNotFound):
        return HTTPException(
            status_code=404,
            detail={"code": exc.code, "message": "未找到对应的历史告警记录。"},
        )
    if isinstance(exc, AlertTriageConflict):
        return HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": "处置状态或并发前置条件不匹配。"},
        )
    return HTTPException(
        status_code=503,
        detail={
            "code": getattr(exc, "code", "TRIAGE_LEDGER_UNAVAILABLE"),
            "message": "预警处置账本当前不可安全读取。",
        },
    )


def _require_computable_dashboard(dashboard: Dict[str, Any]) -> None:
    if dashboard_is_computable(dashboard):
        return
    trust = dashboard.get("trust") if isinstance(dashboard.get("trust"), dict) else {}
    raise HTTPException(
        status_code=503,
        detail={
            "code": "FINANCIAL_INDEX_NOT_COMPUTABLE",
            "message": "当前数据不满足可信计算门槛，阈值规则评估已暂停。",
            "trust_status": trust.get("trust_status") or "unavailable",
            "freshness_status": trust.get("freshness_status") or "offline",
            "reasons": trust.get("unavailable_reasons") or [],
        },
    )


def _alert_trust_contract(dashboard: Dict[str, Any]) -> Dict[str, Any]:
    raw_trust = dashboard.get("trust")
    trust = dict(raw_trust) if isinstance(raw_trust, dict) else {}
    paused = not dashboard_is_computable(dashboard)
    if paused:
        reasons = trust.get("unavailable_reasons")
        if not isinstance(reasons, list) or not reasons:
            reasons = [
                {
                    "code": "INVALID_TRUST_CONTRACT",
                    "message": (
                        "Alert evaluation is paused because the financial trust "
                        "contract is unavailable or contradictory."
                    ),
                }
            ]
        freshness = trust.get("freshness_status")
        if freshness not in {"delayed", "stale", "offline"}:
            freshness = "offline"
        snapshot_id = trust.get("snapshot_id")
        if not isinstance(snapshot_id, str) or not snapshot_id:
            snapshot_id = "fin-unavailable"
        trust.update(
            {
                "schema_version": TRUST_SCHEMA_VERSION,
                "snapshot_id": snapshot_id,
                "trust_status": "unavailable",
                "freshness_status": freshness,
                "computability": "not_computable",
                "computable": False,
                "data_as_of": trust.get("data_as_of"),
                "coverage_ratio": trust.get("coverage_ratio", 0),
                "minimum_coverage_ratio": trust.get(
                    "minimum_coverage_ratio", HARD_MINIMUM_SOURCE_COVERAGE
                ),
                "usable_sources": trust.get("usable_sources", 0),
                "source_total": trust.get("source_total", 0),
                "usable_source_ids": trust.get("usable_source_ids") or [],
                "unavailable_source_ids": trust.get("unavailable_source_ids") or [],
                "source_status": trust.get("source_status") or {"offline": 1},
                "model_version": trust.get("model_version") or MODEL_VERSION,
                "method_version": trust.get("method_version")
                or COMPOSITE_METHOD_VERSION,
                "composite_method_card": trust.get("composite_method_card")
                or composite_method_card(),
                "unavailable_reasons": reasons,
                "alerts_enabled": False,
            }
        )
    coverage = dict(dashboard.get("coverage") or {})
    coverage.update(
        {
            "coverage_ratio": trust.get("coverage_ratio", 0),
            "minimum_coverage_ratio": trust.get(
                "minimum_coverage_ratio", HARD_MINIMUM_SOURCE_COVERAGE
            ),
            "usable_sources": trust.get("usable_sources", 0),
            "sources_total": trust.get("source_total", 0),
            "source_status": trust.get("source_status") or {"offline": 1},
        }
    )
    return {
        "paused": paused,
        "trust": trust or None,
        "trust_status": trust.get("trust_status") or "unavailable",
        "freshness_status": trust.get("freshness_status") or "offline",
        "computability": trust.get("computability") or "not_computable",
        "computable": trust.get("computable") is True,
        "alerts_enabled": trust.get("alerts_enabled") is True,
        "data_as_of": trust.get("data_as_of"),
        "coverage": coverage,
        "schema_version": trust.get("schema_version"),
        "snapshot_id": trust.get("snapshot_id"),
        "model_version": trust.get("model_version"),
        "method_version": trust.get("method_version"),
        "composite_method_card": trust.get("composite_method_card"),
        "unavailable_reasons": trust.get("unavailable_reasons") or [],
    }


async def _financial_alert_rules(
    refresh: bool = False,
    dashboard: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    return await _alert_service().list_rules(refresh=refresh, dashboard=dashboard)


def _refresh_alert_history_store(
    rules: List[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    return _alert_service().refresh_history_store(rules, now=now)


async def _financial_alert_history(
    limit: int,
    refresh: bool = False,
) -> List[Dict[str, Any]]:
    return await _alert_service().history(limit=limit, refresh=refresh)


def _create_user_alert_rule(row: Dict[str, Any]) -> Dict[str, Any]:
    return _alert_service().create_rule(row)


def _update_user_alert_rule(rule_id: str, changes: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return _alert_service().update_rule(rule_id, changes)
    except AlertRuleNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail="预警规则不存在或不是用户规则",
        ) from exc


def _delete_user_alert_rule(rule_id: str) -> None:
    try:
        _alert_service().delete_rule(rule_id)
    except AlertRuleNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail="预警规则不存在或不是用户规则",
        ) from exc


@router.get("/dashboard")
async def financial_dashboard(
    refresh: bool = Query(False, description="Bypass the short in-memory source cache"),
):
    return await get_dashboard(refresh=refresh)


@router.get("/indices")
async def financial_indices(refresh: bool = Query(False)):
    return await get_indices(refresh=refresh)


@router.get("/watchlist")
async def financial_watchlist(refresh: bool = Query(False)):
    return await get_watchlist(refresh=refresh)


@router.get("/sources")
async def financial_sources(refresh: bool = Query(False)):
    return await get_sources(refresh=refresh)


@router.get("/alert/rules")
async def financial_alert_rules(refresh: bool = Query(False)):
    dashboard = await get_dashboard(refresh=refresh)
    return {
        "rules": await _financial_alert_rules(
            refresh=refresh,
            dashboard=dashboard,
        ),
        **_alert_trust_contract(dashboard),
    }


@router.post("/alert/rules", dependencies=[Depends(get_current_admin_user)])
async def financial_alert_rules_create(body: AlertRulePayload):
    if not str(body.metric or "").strip() or body.threshold is None:
        raise HTTPException(status_code=422, detail="创建预警规则需要 metric 和 threshold")
    dashboard = await get_dashboard(refresh=False)
    _require_computable_dashboard(dashboard)
    row = _create_user_alert_rule(body.model_dump(exclude_none=True))
    return _enrich_rule(row, _metric_lookup_from_dashboard(dashboard))


@router.put("/alert/rules/{rule_id}", dependencies=[Depends(get_current_admin_user)])
async def financial_alert_rules_update(rule_id: str, body: AlertRulePayload):
    dashboard = await get_dashboard(refresh=False)
    _require_computable_dashboard(dashboard)
    row = _update_user_alert_rule(
        rule_id,
        body.model_dump(exclude_unset=True, exclude_none=True),
    )
    return _enrich_rule(row, _metric_lookup_from_dashboard(dashboard))


@router.delete("/alert/rules/{rule_id}", dependencies=[Depends(get_current_admin_user)])
async def financial_alert_rules_delete(rule_id: str):
    _delete_user_alert_rule(rule_id)
    return {"ok": True, "id": rule_id}


@router.get("/alert/history")
async def financial_alert_history(limit: int = Query(50, ge=1, le=200)):
    return await _financial_alert_history(limit=limit)


@router.post("/alert/history/refresh", dependencies=[Depends(get_current_admin_user)])
async def financial_alert_history_refresh(limit: int = Query(50, ge=1, le=200)):
    dashboard = await get_dashboard(refresh=True)
    trust_contract = _alert_trust_contract(dashboard)
    if trust_contract["paused"]:
        history = await _financial_alert_history(limit=limit, refresh=False)
    else:
        rules = await _financial_alert_rules(refresh=True, dashboard=dashboard)
        history = _refresh_alert_history_store(rules)[:limit]
    return {"history": history, **trust_contract}


@router.get("/alert/data")
async def financial_alert_data(refresh: bool = Query(False)):
    dashboard = await get_dashboard(refresh=refresh)
    trust_contract = _alert_trust_contract(dashboard)
    history = await _financial_alert_history(limit=50)
    try:
        history = annotate_alert_history_with_triage(
            history,
            service=_alert_triage_service(),
            historical=trust_contract["paused"],
        )
    except AlertTriageUnavailable as exc:
        raise _triage_http_error(exc) from exc
    return {
        "rules": await _financial_alert_rules(refresh=refresh, dashboard=dashboard),
        "history": history,
        **trust_contract,
    }


@router.get("/alert/triage/{alert_event_id}")
async def financial_alert_triage_detail(
    alert_event_id: str = ApiPath(
        min_length=1,
        max_length=300,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,299}$",
    ),
    _user: Dict[str, Any] = Depends(get_current_user_required),
):
    try:
        detail = _alert_triage_service().detail(
            alert_event_id,
            include_sensitive=False,
        )
    except (AlertHistoryEventNotFound, AlertTriageConflict, AlertTriageUnavailable) as exc:
        raise _triage_http_error(exc) from exc
    trust_contract = _alert_trust_contract(await get_dashboard(refresh=False))
    return {
        **detail,
        "historical": trust_contract["paused"],
        "mutations_enabled": not trust_contract["paused"],
        "trust_status": trust_contract["trust_status"],
        "freshness_status": trust_contract["freshness_status"],
        "snapshot_id": trust_contract["snapshot_id"],
    }


@router.get("/alert/triage/{alert_event_id}/audit")
async def financial_alert_triage_admin_audit(
    alert_event_id: str = ApiPath(
        min_length=1,
        max_length=300,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,299}$",
    ),
    _admin: Dict[str, Any] = Depends(get_current_admin_user),
):
    try:
        detail = _alert_triage_service().detail(
            alert_event_id,
            include_sensitive=True,
        )
    except (AlertHistoryEventNotFound, AlertTriageConflict, AlertTriageUnavailable) as exc:
        raise _triage_http_error(exc) from exc
    trust_contract = _alert_trust_contract(await get_dashboard(refresh=False))
    return {
        **detail,
        "historical": trust_contract["paused"],
        "mutations_enabled": not trust_contract["paused"],
        "trust_status": trust_contract["trust_status"],
        "freshness_status": trust_contract["freshness_status"],
        "snapshot_id": trust_contract["snapshot_id"],
    }


@router.post("/alert/triage/{alert_event_id}/events")
async def financial_alert_triage_mutate(
    body: AlertTriageMutation,
    alert_event_id: str = ApiPath(
        min_length=1,
        max_length=300,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,299}$",
    ),
    admin: Dict[str, Any] = Depends(get_current_admin_user),
):
    # All lifecycle writes use the same financial trust decision as rule and
    # event generation.  A historical alert remains readable while this gate
    # is closed, but no acknowledgement/escalation/resolution/review is added.
    dashboard = await get_dashboard(refresh=False)
    _require_computable_dashboard(dashboard)
    try:
        return _alert_triage_service().mutate(
            alert_event_id,
            body,
            actor_user_id=_canonical_actor_user_id(admin),
        )
    except (AlertHistoryEventNotFound, AlertTriageConflict, AlertTriageUnavailable) as exc:
        raise _triage_http_error(exc) from exc
