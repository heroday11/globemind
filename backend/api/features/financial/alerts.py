from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from .json_store import JsonListStore
from .trust import dashboard_is_computable

DashboardProvider = Callable[..., Awaitable[dict[str, Any]]]

_EDITABLE_RULE_FIELDS = frozenset(
    {
        "baseline",
        "id",
        "metric",
        "metric_id",
        "severity",
        "threshold",
        "unit",
    }
)


class AlertRuleNotFound(LookupError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_timestamp(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat().replace("+00:00", "Z")


def clean_severity(
    value: str | None,
    *,
    breached: bool,
    current: float,
    threshold: float,
) -> str:
    raw = (value or "").strip().lower()
    if raw in {"high", "medium", "low"}:
        return raw
    if breached:
        return "high"
    if threshold and current >= threshold * 0.82:
        return "medium"
    return "low"


def clean_trend(value: str | None, *, current: float, baseline: float) -> str:
    raw = (value or "").strip().lower()
    if raw in {"up", "down", "flat"}:
        return raw
    if current > baseline:
        return "up"
    if current < baseline:
        return "down"
    return "flat"


def metric_lookup_from_dashboard(
    dashboard: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}

    def add(*keys: Any, row: dict[str, Any]) -> None:
        for key in keys:
            text = str(key or "").strip().lower()
            if text:
                output[text] = row

    for item in dashboard.get("indices") or []:
        if not isinstance(item, dict):
            continue
        row = {
            "current": item.get("value"),
            "unit": "%",
            "metric": item.get("name"),
            "metric_id": item.get("metric_id") or item.get("id"),
        }
        add(item.get("id"), item.get("metric_id"), item.get("name"), row=row)

    for item in dashboard.get("watchlist") or []:
        if not isinstance(item, dict):
            continue
        row = {
            "current": item.get("price"),
            "unit": item.get("unit") or "index",
            "metric": item.get("label") or item.get("symbol"),
            "metric_id": item.get("metric_id") or item.get("symbol"),
        }
        add(item.get("symbol"), item.get("metric_id"), item.get("label"), row=row)

    for item in dashboard.get("series") or []:
        if not isinstance(item, dict):
            continue
        row = {
            "current": item.get("latest"),
            "unit": item.get("unit") or "index",
            "metric": item.get("label") or item.get("id"),
            "metric_id": item.get("id"),
        }
        add(item.get("id"), item.get("label"), row=row)

    return output


def enrich_rule(
    raw: dict[str, Any],
    lookup: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    metric_id = str(raw.get("metric_id") or raw.get("id") or "").strip()
    metric = str(raw.get("metric") or "").strip()
    live = lookup.get(metric_id.lower()) or lookup.get(metric.lower()) or {}
    current = live.get("current") if live.get("current") is not None else raw.get("current")
    current_value = round(_finite_number(current), 2)
    threshold = _finite_number(raw.get("threshold"))
    baseline = _finite_number(raw.get("baseline"), default=current_value)
    breached = current_value >= threshold
    return {
        **raw,
        "id": str(raw.get("id") or f"custom-{uuid.uuid4().hex[:10]}"),
        "metric": metric or str(live.get("metric") or metric_id or "未命名指标"),
        "metric_id": metric_id or str(live.get("metric_id") or ""),
        "unit": str(raw.get("unit") or live.get("unit") or ""),
        "current": current_value,
        "threshold": threshold,
        "baseline": round(baseline, 2),
        "severity": clean_severity(
            raw.get("severity"),
            breached=breached,
            current=current_value,
            threshold=threshold,
        ),
        "breached": breached,
        "trend": clean_trend(None, current=current_value, baseline=baseline),
        "source": raw.get("source") or "user",
    }


def history_message(rule: dict[str, Any]) -> str:
    return (
        f"{rule.get('metric')} 当前值 {float(rule.get('current') or 0):.2f}{rule.get('unit') or ''}，"
        f"已达到/超过阈值 {rule.get('threshold')}{rule.get('unit') or ''}。"
        "建议进入数据助手结合新闻库和事件图谱核查异常原因。"
    )


class FinancialAlertService:
    def __init__(
        self,
        *,
        rules_path: Path,
        history_path: Path,
        cooldown_hours: int,
        dashboard_provider: DashboardProvider,
    ) -> None:
        self._rules = JsonListStore(rules_path)
        self._history = JsonListStore(history_path)
        self._cooldown_hours = max(0, int(cooldown_hours))
        self._dashboard_provider = dashboard_provider

    async def list_rules(
        self,
        *,
        refresh: bool = False,
        dashboard: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if dashboard is None:
            dashboard = await self._dashboard_provider(refresh=refresh)
        if not dashboard_is_computable(dashboard):
            return []
        lookup = metric_lookup_from_dashboard(dashboard)
        default_rules = [
            enrich_rule(
                {
                    **rule,
                    "source": "system",
                    "metric_id": rule.get("metric_id") or rule.get("id"),
                },
                lookup,
            )
            for rule in dashboard.get("alert_rules", [])
            if isinstance(rule, dict)
        ]
        custom_rules = [enrich_rule(rule, lookup) for rule in self._rules.read()]
        seen = {str(rule.get("id")) for rule in custom_rules}
        return custom_rules + [rule for rule in default_rules if str(rule.get("id")) not in seen]

    def create_rule(self, row: dict[str, Any]) -> dict[str, Any]:
        value = {
            **_editable_rule_config(row, include_id=True),
            "id": row.get("id") or f"custom-{uuid.uuid4().hex[:10]}",
            "source": "user",
        }

        def create(
            rows: list[dict[str, Any]],
        ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            next_rows = [item for item in rows if str(item.get("id")) != str(value["id"])]
            next_rows.insert(0, value)
            return next_rows, value

        return self._rules.mutate(create)

    def update_rule(self, rule_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        def update(
            rows: list[dict[str, Any]],
        ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            index = next(
                (index for index, item in enumerate(rows) if str(item.get("id")) == rule_id),
                -1,
            )
            if index < 0:
                raise AlertRuleNotFound(rule_id)
            row = {
                **_editable_rule_config(rows[index], include_id=True),
                **_editable_rule_config(changes, include_id=False),
                "id": rule_id,
                "source": "user",
            }
            rows[index] = row
            return rows, row

        return self._rules.mutate(update)

    def delete_rule(self, rule_id: str) -> None:
        def delete(
            rows: list[dict[str, Any]],
        ) -> tuple[list[dict[str, Any]], None]:
            next_rows = [item for item in rows if str(item.get("id")) != rule_id]
            if len(next_rows) == len(rows):
                raise AlertRuleNotFound(rule_id)
            return next_rows, None

        self._rules.mutate(delete)

    async def history(self, *, limit: int, refresh: bool = False) -> list[dict[str, Any]]:
        if not refresh:
            history = self._history.read()
            history.sort(key=lambda row: str(row.get("triggered_at") or ""), reverse=True)
            return history[:limit]
        rules = await self.list_rules(refresh=True)
        return self.refresh_history_store(rules)[:limit]

    def refresh_history_store(
        self,
        rules: list[dict[str, Any]],
        *,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        current = now or utc_now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        cutoff = current - timedelta(hours=self._cooldown_hours)

        def refresh(
            history: list[dict[str, Any]],
        ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
            existing_recent: set[str] = set()
            for row in history:
                try:
                    timestamp = datetime.fromisoformat(
                        str(row.get("triggered_at", "")).replace("Z", "+00:00")
                    )
                except (TypeError, ValueError):
                    continue
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                if timestamp >= cutoff:
                    existing_recent.add(str(row.get("rule_id") or row.get("id") or ""))

            new_rows: list[dict[str, Any]] = []
            for rule in rules:
                rule_id = str(rule.get("id") or "")
                if not rule_id or not rule.get("breached") or rule_id in existing_recent:
                    continue
                new_rows.append(
                    {
                        "id": f"fin-alert-{rule_id}-{current.strftime('%Y%m%d%H%M%S')}",
                        "rule_id": rule_id,
                        "metric": rule.get("metric") or rule_id,
                        "current": float(rule.get("current") or 0),
                        "threshold": float(rule.get("threshold") or 0),
                        "severity": rule.get("severity") or "high",
                        "triggered_at": iso_timestamp(current),
                        "message": history_message(rule),
                        "eventTags": [
                            str(rule.get("metric") or rule_id),
                            str(rule.get("source") or "system"),
                        ],
                    }
                )
                existing_recent.add(rule_id)
            next_history = (new_rows + history)[:500] if new_rows else history
            return next_history, next_history

        history = self._history.mutate(refresh, write_if_unchanged=False)
        history.sort(key=lambda row: str(row.get("triggered_at") or ""), reverse=True)
        return history


def _editable_rule_config(
    row: dict[str, Any],
    *,
    include_id: bool,
) -> dict[str, Any]:
    allowed = _EDITABLE_RULE_FIELDS if include_id else _EDITABLE_RULE_FIELDS - {"id"}
    return {key: value for key, value in row.items() if key in allowed}


def _finite_number(value: Any, *, default: float = 0.0) -> float:
    try:
        number = float(value) if value is not None else default
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default
