from __future__ import annotations

import json
import math
import os
import re
import shutil
import socket
import subprocess
import time
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text

from api.core.db import SessionLocal
from api.core.environment import float_setting, int_setting, string_setting
from api.core.redaction import redact_text, sanitize_diagnostic
from api.features.operations import (
    HeartbeatPayload,
    HeartbeatPolicy,
    HeartbeatRegistry,
    MonitoringHistoryPolicy,
    MonitoringHistoryStore,
    RuntimeCatalogUnavailable,
    attach_catalog_management,
    load_runtime_catalog,
    unavailable_runtime_catalog,
)
from api.services.auth import get_current_user_required

PROJECT_ROOT = Path(string_setting("GLOBEMIND_ROOT", "/root/data/globemind"))
DATA_ROOT = PROJECT_ROOT / "data"
LOG_ROOT = PROJECT_ROOT / "logs"
WEB_PID_FILE = Path("/root/data/web/pids/globemind_web_prod.pid")
RUNTIME_ROOT = DATA_ROOT / "runtime"
PLATFORM_RUNTIME_ROOT = Path(
    string_setting("GLOBEMIND_PLATFORM_RUNTIME_ROOT", "/root/data/runtime/globemind")
)
HEARTBEAT_FILE = RUNTIME_ROOT / "ops_heartbeats.json"
HEARTBEAT_LOCK = RUNTIME_ROOT / "ops_heartbeats.lock"
HISTORY_FILE = RUNTIME_ROOT / "ops_monitor_history.json"
HISTORY_LOCK = RUNTIME_ROOT / "ops_monitor_history.lock"
HEARTBEAT_TTL_SEC = int_setting("OPS_HEARTBEAT_TTL_SEC", 90, minimum=1)
HEARTBEAT_MAX_CLIENTS = int_setting("OPS_HEARTBEAT_MAX_CLIENTS", 10000, minimum=100)
SNAPSHOT_CACHE_TTL_SEC = int_setting("OPS_MONITOR_CACHE_TTL_SEC", 10, minimum=0)
HISTORY_MAX_POINTS = int_setting("OPS_MONITOR_HISTORY_MAX_POINTS", 1200, minimum=1)
HISTORY_MIN_INTERVAL_SEC = float_setting(
    "OPS_MONITOR_HISTORY_MIN_INTERVAL_SEC", 0.25, minimum=0.0
)
GPU_CACHE_TTL_SEC = float_setting("OPS_MONITOR_GPU_CACHE_TTL_SEC", 8.0, minimum=0.0)
WEB_PORT = int_setting("PORT", 8088, minimum=1)
WEB_WORKERS = int_setting("WEB_WORKERS", 4, minimum=1)

router = APIRouter(prefix="/api/ops", tags=["ops-monitor"])

_SNAPSHOT_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}
_GPU_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}


def _now_ts() -> float:
    return time.time()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_iso_from_ts(ts: float | int | None) -> Optional[str]:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except Exception:
        return None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            stat = path.stat()
            data["_path"] = str(path)
            data["_mtime"] = _safe_iso_from_ts(stat.st_mtime)
            data["_age_sec"] = max(0.0, _now_ts() - stat.st_mtime)
            return data
    except FileNotFoundError:
        return {"_path": str(path), "_missing": True}
    except Exception as exc:
        return {"_path": str(path), "_error": str(exc)}
    return {"_path": str(path), "_error": "json root is not an object"}


def _latest_dir(root: Path, pattern: str) -> Optional[Path]:
    try:
        dirs = [p for p in root.glob(pattern) if p.is_dir()]
    except Exception:
        return None
    if not dirs:
        return None
    return max(dirs, key=lambda p: p.stat().st_mtime)


def _read_pid(path: Path) -> Optional[int]:
    try:
        raw = path.read_text("utf-8").strip()
        return int(raw)
    except Exception:
        return None


def _pid_running(pid: Optional[int]) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _run_cmd(args: list[str], timeout: float = 2.0) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except Exception as exc:
        return 1, "", str(exc)


def _ps_info(pid: Optional[int]) -> dict[str, Any]:
    if not pid:
        return {}
    rc, out, _err = _run_cmd(
        ["ps", "-p", str(pid), "-o", "pid=,ppid=,pcpu=,pmem=,etime=,stat=,comm="],
        timeout=1.5,
    )
    if rc != 0 or not out:
        return {}
    parts = out.strip().split()
    if len(parts) != 7:
        return {}
    return {
        "pid": int(parts[0]),
        "ppid": int(parts[1]),
        "cpu_pct": _to_float(parts[2]),
        "mem_pct": _to_float(parts[3]),
        "etime": parts[4],
        "stat": parts[5],
        "name": Path(parts[6]).name[:80],
    }


def _process_from_pid_file(path: Path) -> dict[str, Any]:
    pid = _read_pid(path)
    running = _pid_running(pid)
    info = _ps_info(pid) if running else {}
    return {
        "pid_file": str(path),
        "pid": pid,
        "running": running,
        "evidence_quality": "heuristic",
        "evidence_source": "legacy-pid-file",
        "authoritative_for_management": False,
        **info,
    }


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _read_tail(path: Path, lines: int = 8) -> list[str]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return [redact_text(line.rstrip("\n")) for line in deque(handle, maxlen=lines)]
    except Exception:
        return []


def _to_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or isinstance(value, bool):
            return default
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(value)
    except Exception:
        return default


def _to_optional_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _pct(value: Any) -> Optional[float]:
    number = _to_float(value)
    if number is None:
        return None
    if number <= 1:
        number *= 100
    return round(max(0.0, min(100.0, number)), 2)


def _eta_seconds(remaining: Any, per_sec: Any) -> Optional[int]:
    remain = _to_float(remaining)
    rate = _to_float(per_sec)
    if remain is None or rate is None or rate <= 0:
        return None
    return int(remain / rate)


def _bounded_number(value: Any, default: Optional[float] = None) -> Optional[float]:
    number = _to_float(value, default)
    if number is None:
        return None
    return round(float(number), 4)


def _metric(label: str, value: Any, unit: str = "") -> dict[str, Any]:
    return {"label": label, "value": value, "unit": unit}


def _status_from_process(proc: dict[str, Any], *, expected: bool = True) -> str:
    if proc.get("running"):
        return "running"
    return "failed" if expected else "idle"


def _progress_pipeline(
    *,
    pipeline_id: str,
    group: str,
    name: str,
    progress: dict[str, Any],
    proc: dict[str, Any],
    expected: bool = True,
    extra_metrics: Optional[list[dict[str, Any]]] = None,
    alerts: Optional[list[str]] = None,
) -> dict[str, Any]:
    top_errors = progress.get("top_errors") if isinstance(progress.get("top_errors"), list) else []
    failure_rate = 0.0
    processed = _to_float(progress.get("processed"), 0.0) or 0.0
    failures = _to_float(progress.get("failures"), 0.0) or 0.0
    if processed > 0:
        failure_rate = failures / processed

    status = _status_from_process(proc, expected=expected)
    final_alerts = list(alerts or [])
    if progress.get("_missing"):
        status = "unknown"
        final_alerts.append("未找到进度文件")
    elif status == "running" and failure_rate >= 0.20:
        status = "warning"
        final_alerts.append("失败率偏高")
    elif status == "running" and _to_float(progress.get("_age_sec"), 0.0) and float(progress["_age_sec"]) > 1800:
        status = "warning"
        final_alerts.append("进度文件超过 30 分钟未更新")

    if top_errors:
        first_error = top_errors[0]
        if isinstance(first_error, list) and len(first_error) >= 2 and _to_int(first_error[1]) > 1000:
            final_alerts.append(f"主要错误：{first_error[0] or 'unknown'} x{first_error[1]}")

    completion = _pct(progress.get("completion_rate"))
    rate_per_min = _to_float(progress.get("successes_per_min"))
    eta = _eta_seconds(progress.get("rows_remaining"), progress.get("successes_per_sec"))
    metrics = [
        _metric("成功", _to_int(progress.get("successes"))),
        _metric("失败", _to_int(progress.get("failures"))),
        _metric("剩余", _to_int(progress.get("rows_remaining"))),
        _metric("速率", round(rate_per_min or 0, 1), "/min"),
    ]
    if extra_metrics:
        metrics.extend(extra_metrics)
    return {
        "id": pipeline_id,
        "group": group,
        "name": name,
        "status": status,
        "pid": proc.get("pid"),
        "process": proc,
        "updated_at": progress.get("updated_at") or progress.get("_mtime"),
        "progress_pct": completion,
        "rate_per_min": rate_per_min,
        "eta_sec": eta,
        "metrics": metrics,
        "alerts": final_alerts[:5],
        "top_errors": top_errors[:5],
        "top_error_sites": (progress.get("top_error_sites") or [])[:5],
        "details": {
            "rows": progress.get("rows") or progress.get("input_rows_raw"),
            "processed": progress.get("processed"),
            "active_tasks": progress.get("active_tasks"),
            "global_concurrency": progress.get("global_concurrency"),
            "max_per_domain": progress.get("max_per_domain"),
            "progress_path": progress.get("_path"),
        },
    }


def _loader_pipeline(db: dict[str, Any]) -> dict[str, Any]:
    state = _read_json(
        DATA_ROOT / "historical_news/jobs/wave1_1y_prod_20260621/news_loader_state.json"
    )
    heartbeat = _read_json(
        PLATFORM_RUNTIME_ROOT / "wave1_loader/wave1_loader.pid.heartbeat"
    )
    status = "unknown"
    heartbeat_age = _to_float(heartbeat.get("_age_sec"))
    heartbeat_status = str(heartbeat.get("status") or "").lower()
    if not heartbeat.get("_missing") and heartbeat_age is not None:
        if heartbeat_status == "running" and heartbeat_age <= 180:
            status = "running"
        elif heartbeat_status in {"complete", "completed", "succeeded", "success"}:
            status = "idle"
        else:
            status = "warning"
    inserted = _to_int(state.get("inserted"))
    seen = _to_int(state.get("seen"))
    progress_pct = round(inserted / seen * 100, 2) if seen else None
    alerts: list[str] = []
    if state.get("_missing"):
        status = "unknown"
        alerts.append("未找到 loader state")
    if heartbeat.get("_missing"):
        alerts.append("未找到 loader 权威心跳")
    elif heartbeat_age is not None and heartbeat_age > 180:
        alerts.append("loader 权威心跳超过 180 秒未更新")
    if state.get("quality_skipped"):
        alerts.append(f"质量门控跳过 {state.get('quality_skipped')} 条")
    return {
        "id": "wave1_loader",
        "group": "数据获取",
        "name": "Wave1 入库加载",
        "status": status,
        "pid": None,
        "process": {
            "pid": None,
            "running": None,
            "evidence_quality": "not-inspected",
            "evidence_source": "runtime-catalog-identity-contract",
            "authoritative_for_management": False,
        },
        "updated_at": state.get("_mtime") or _safe_iso_from_ts(state.get("updated_at")),
        "progress_pct": progress_pct,
        "rate_per_min": None,
        "eta_sec": None,
        "metrics": [
            _metric("已读", seen),
            _metric("入库", inserted),
            _metric("跳过", _to_int(state.get("skipped"))),
            _metric("库内新闻", db.get("news", {}).get("total", 0)),
        ],
        "alerts": alerts[:5],
        "top_errors": [],
        "top_error_sites": [],
        "details": {
            "state_path": state.get("_path"),
            "heartbeat": {
                key: heartbeat.get(key)
                for key in (
                    "heartbeat_at",
                    "last_progress_at",
                    "status",
                    "checkpoint_key",
                    "offset",
                    "seen",
                )
                if heartbeat.get(key) is not None
            },
            "quality_skip_reasons": state.get("quality_skip_reasons") or {},
        },
        "telemetry_evidence": {
            "quality": "authoritative-state",
            "source": "catalog-declared-heartbeat-and-loader-state",
            "authoritative_for_management": False,
            "process_inspection": False,
        },
    }


def _daily_pipeline_context() -> tuple[Optional[Path], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    run_dir = _latest_dir(DATA_ROOT / "historical_news/daily", "daily_*")
    if not run_dir:
        return None, {}, {}, {}, {}
    return (
        run_dir,
        _read_json(run_dir / "extract_progress.json"),
        _read_json(run_dir / "prune_stats.json"),
        _read_json(run_dir / "db_prefilter_stats.json"),
        _read_json(run_dir / "load_state.json"),
    )


def _daily_pipeline() -> dict[str, Any]:
    run_dir, progress, prune, prefilter, load_state = _daily_pipeline_context()
    proc = _process_from_pid_file(LOG_ROOT / "daily_news_ingest_loop.pid")
    child_running = _processes_matching(["daily_"], limit=1)
    alerts: list[str] = []
    if run_dir:
        alerts.append(f"当前批次：{run_dir.name}")
    if prune.get("removed_pct") is not None:
        alerts.append(f"URL 预剪枝移除 {prune.get('removed_pct')}%")
    if prefilter.get("skipped_existing_db"):
        alerts.append(f"DB 去重 {prefilter.get('skipped_existing_db')} 条")
    if child_running:
        proc = {**proc, "child": child_running[0]}
    pipeline = _progress_pipeline(
        pipeline_id="daily_ingest",
        group="数据获取",
        name="每日新闻更新",
        progress=progress or {"_missing": True},
        proc=proc,
        extra_metrics=[
            _metric("发现", prune.get("kept", 0)),
            _metric("去重后", prefilter.get("kept", 0)),
        ],
        alerts=alerts,
    )
    pipeline["details"].update(
        {
            "run_dir": str(run_dir) if run_dir else None,
            "prune": prune,
            "prefilter": prefilter,
            "load_state": load_state,
        }
    )
    return pipeline


def _simple_loop_pipeline(
    *,
    pipeline_id: str,
    group: str,
    name: str,
    pid_file: Path,
    log_file: Optional[Path] = None,
    metrics: Optional[list[dict[str, Any]]] = None,
    alerts: Optional[list[str]] = None,
    expected: bool = True,
    status_override: Optional[str] = None,
) -> dict[str, Any]:
    proc = _process_from_pid_file(pid_file)
    status = status_override or _status_from_process(proc, expected=expected)
    last_log = _read_tail(log_file, 6) if log_file else []
    return {
        "id": pipeline_id,
        "group": group,
        "name": name,
        "status": status,
        "pid": proc.get("pid"),
        "process": proc,
        "updated_at": None,
        "progress_pct": None,
        "rate_per_min": None,
        "eta_sec": None,
        "metrics": metrics or [],
        "alerts": (alerts or [])[:5],
        "top_errors": [],
        "top_error_sites": [],
        "details": {
            "pid_file": str(pid_file),
            "log_file": str(log_file) if log_file else None,
            "last_log": last_log,
        },
    }


def _heartbeat_update(
    payload: HeartbeatPayload,
    _request: Request | None = None,
) -> dict[str, Any]:
    return _heartbeat_registry().update(payload)


def _heartbeat_registry() -> HeartbeatRegistry:
    return HeartbeatRegistry(
        data_path=HEARTBEAT_FILE,
        lock_path=HEARTBEAT_LOCK,
        policy=HeartbeatPolicy(
            ttl_seconds=HEARTBEAT_TTL_SEC,
            max_clients=HEARTBEAT_MAX_CLIENTS,
        ),
        clock=_now_ts,
    )


def _online_summary(now: Optional[float] = None) -> dict[str, Any]:
    return _heartbeat_registry().summary(now=now)


def _safe_table_name(table: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
        raise ValueError(f"unsafe table name: {table}")
    return table


def _table_exists(db: Any, table: str) -> bool:
    rel = f"public.{_safe_table_name(table)}"
    return bool(db.execute(text("SELECT to_regclass(:rel) IS NOT NULL"), {"rel": rel}).scalar())


def _scalar(db: Any, sql: str, params: Optional[dict[str, Any]] = None, default: Any = 0) -> Any:
    try:
        value = db.execute(text(sql), params or {}).scalar()
        return default if value is None else value
    except Exception:
        return default


def _rows(db: Any, sql: str, params: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    try:
        result = db.execute(text(sql), params or {})
        return [dict(row._mapping) for row in result]
    except Exception:
        return []


def _table_count(db: Any, table: str) -> Optional[int]:
    if not _table_exists(db, table):
        return None
    return _to_int(_scalar(db, f"SELECT COUNT(*) FROM public.{_safe_table_name(table)}", default=0))


def _db_snapshot() -> dict[str, Any]:
    data: dict[str, Any] = {"available": False, "errors": [], "tables": {}}
    db = SessionLocal()
    try:
        db.execute(text("SET LOCAL statement_timeout = '5000ms'"))
        data["available"] = True

        watched_tables = [
            "news",
            "news_quality_labels",
            "news_l1_prep",
            "event_coref_clusters",
            "event_coref_members",
            "event_l15_segments",
            "event_l15_members",
            "event_l2_chains",
            "event_l2_chain_segments",
            "story_source_breakdown",
            "news_image_assets",
            "story_cover_assets",
            "news_embeddings",
            "lxy_translated",
        ]
        table_exists = {name: _table_exists(db, name) for name in watched_tables}
        data["tables"] = table_exists

        if table_exists["news"]:
            total = _to_int(_scalar(db, "SELECT COUNT(*) FROM public.news", default=0))
            raw_latest = _scalar(db, "SELECT MAX(published_at) FROM public.news", default=None)
            raw_last_24h = _to_int(
                _scalar(
                    db,
                    "SELECT COUNT(*) FROM public.news WHERE published_at >= now() - interval '24 hours'",
                    default=0,
                )
            )
            data["news"] = {
                "total": total,
                "raw_latest_published_at": raw_latest,
                "raw_last_24h": raw_last_24h,
            }
        else:
            data["news"] = {"total": 0}

        if table_exists["news_quality_labels"]:
            quality = _rows(
                db,
                """
                SELECT
                  COUNT(*)::bigint AS total,
                  COUNT(*) FILTER (WHERE is_good)::bigint AS good,
                  COUNT(*) FILTER (WHERE NOT is_good)::bigint AS bad,
                  MAX(checked_at) AS latest_checked_at
                FROM public.news_quality_labels
                """,
            )
            labels = quality[0] if quality else {}
            label_total = _to_int(labels.get("total"))
            data["quality"] = {
                "labels_total": label_total,
                "good": _to_int(labels.get("good")),
                "bad": _to_int(labels.get("bad")),
                "missing_estimate": max(_to_int(data.get("news", {}).get("total")) - label_total, 0),
                "latest_checked_at": labels.get("latest_checked_at"),
            }
            if table_exists["news"]:
                data["quality"]["latest_good_published_at"] = _scalar(
                    db,
                    """
                    SELECT MAX(n.published_at)
                    FROM public.news n
                    JOIN public.news_quality_labels q ON q.news_id = n.id AND q.is_good
                    WHERE n.published_at >= timestamp '2000-01-01'
                      AND n.published_at <= now() + interval '1 day'
                    """,
                    default=None,
                )
                data["quality"]["good_last_24h"] = _to_int(
                    _scalar(
                        db,
                        """
                        SELECT COUNT(*)
                        FROM public.news n
                        JOIN public.news_quality_labels q ON q.news_id = n.id AND q.is_good
                        WHERE n.published_at >= now() - interval '24 hours'
                          AND n.published_at <= now() + interval '1 day'
                        """,
                        default=0,
                    )
                )
        else:
            data["quality"] = {"labels_total": 0, "good": 0, "bad": 0, "missing_estimate": 0}

        if table_exists["news_l1_prep"]:
            status_rows = _rows(
                db,
                """
                SELECT processing_status, COUNT(*)::bigint AS count
                FROM public.news_l1_prep
                GROUP BY processing_status
                ORDER BY processing_status
                """,
            )
            data["l1_prep"] = {
                "status_counts": {str(row["processing_status"]): _to_int(row["count"]) for row in status_rows},
                "total": sum(_to_int(row["count"]) for row in status_rows),
            }
        else:
            data["l1_prep"] = {"status_counts": {}, "total": 0}

        for table in (
            "event_coref_clusters",
            "event_coref_members",
            "event_l15_segments",
            "event_l15_members",
            "event_l2_chains",
            "event_l2_chain_segments",
            "story_source_breakdown",
            "news_image_assets",
            "story_cover_assets",
            "news_embeddings",
            "lxy_translated",
        ):
            data[table] = {"count": _table_count(db, table) if table_exists.get(table) else 0}

        if table_exists["event_coref_clusters"]:
            top_run = _rows(
                db,
                """
                SELECT run_id, COUNT(*)::bigint AS clusters, COALESCE(SUM(article_count), 0)::bigint AS articles
                FROM public.event_coref_clusters
                GROUP BY run_id
                ORDER BY clusters DESC
                LIMIT 1
                """,
            )
            data["event_coref_clusters"]["top_run"] = top_run[0] if top_run else None

        if table_exists["event_coref_members"] and table_exists["news"] and table_exists["news_quality_labels"]:
            data["derived_quality"] = {
                "l1_bad_or_bad_date": _to_int(
                    _scalar(
                        db,
                        """
                        SELECT COUNT(*)
                        FROM public.event_coref_members m
                        JOIN public.news n ON n.id = m.news_id
                        LEFT JOIN public.news_quality_labels q ON q.news_id = n.id
                        WHERE q.is_good IS DISTINCT FROM TRUE
                           OR n.published_at < timestamp '2000-01-01'
                           OR n.published_at > now() + interval '1 day'
                        """,
                        default=0,
                    )
                )
            }
        else:
            data["derived_quality"] = {"l1_bad_or_bad_date": 0}

        if table_exists["event_l15_members"] and table_exists["news"] and table_exists["news_quality_labels"]:
            data["derived_quality"]["l15_bad_or_bad_date"] = _to_int(
                _scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM public.event_l15_members m
                    JOIN public.news n ON n.id = m.news_id
                    LEFT JOIN public.news_quality_labels q ON q.news_id = n.id
                    WHERE q.is_good IS DISTINCT FROM TRUE
                       OR n.published_at < timestamp '2000-01-01'
                       OR n.published_at > now() + interval '1 day'
                    """,
                    default=0,
                )
            )

        if table_exists["event_coref_clusters"] and table_exists["story_cover_assets"]:
            run_id = None
            top_run = data.get("event_coref_clusters", {}).get("top_run")
            if isinstance(top_run, dict):
                run_id = top_run.get("run_id")
            run_filter = "WHERE c.run_id = :run_id" if run_id else ""
            params = {"run_id": run_id} if run_id else {}
            image_rows = _rows(
                db,
                f"""
                WITH clusters AS (
                  SELECT c.cluster_id, c.run_id, c.article_count
                  FROM public.event_coref_clusters c
                  {run_filter}
                )
                SELECT
                  COUNT(*) FILTER (WHERE article_count >= 5)::bigint AS large_5,
                  COUNT(sc.cluster_id) FILTER (WHERE article_count >= 5)::bigint AS large_5_with_cover,
                  COUNT(*) FILTER (WHERE article_count >= 10)::bigint AS large_10,
                  COUNT(sc.cluster_id) FILTER (WHERE article_count >= 10)::bigint AS large_10_with_cover,
                  COUNT(*) FILTER (WHERE article_count >= 20)::bigint AS large_20,
                  COUNT(sc.cluster_id) FILTER (WHERE article_count >= 20)::bigint AS large_20_with_cover
                FROM clusters c
                LEFT JOIN public.story_cover_assets sc
                  ON sc.cluster_id = c.cluster_id
                 AND sc.run_id = c.run_id
                 AND sc.status = 'ok'
                """,
                params,
            )
            data["image_coverage"] = image_rows[0] if image_rows else {}
            data["image_coverage"]["run_id"] = run_id
        else:
            data["image_coverage"] = {}

        if table_exists["story_cover_assets"]:
            cover_kinds = _rows(
                db,
                """
                SELECT cover_kind, COUNT(*)::bigint AS count
                FROM public.story_cover_assets
                WHERE status = 'ok'
                GROUP BY cover_kind
                ORDER BY count DESC
                LIMIT 8
                """,
            )
            data["story_cover_assets"]["cover_kinds"] = cover_kinds
    except Exception as exc:
        data["errors"].append(str(exc))
    finally:
        db.close()
    return data


def _system_snapshot() -> dict[str, Any]:
    cpu = _cpu_snapshot()
    mem = _meminfo()
    disk = _disk_snapshot()
    uptime_sec = None
    try:
        uptime_sec = float(Path("/proc/uptime").read_text("utf-8").split()[0])
    except Exception:
        pass

    return {
        "host": socket.gethostname(),
        "cpu": cpu,
        "memory": mem,
        "disk": disk,
        "uptime_sec": uptime_sec,
        "gpus": _gpu_snapshot(),
        "processes": _pipeline_processes(),
    }


def _fast_system_snapshot() -> dict[str, Any]:
    cpu = _cpu_snapshot()
    mem = _meminfo()
    gpus = _gpu_snapshot()
    return {
        "host": socket.gethostname(),
        "cpu": cpu,
        "memory": mem,
        "gpus": gpus,
    }


def _cpu_snapshot() -> dict[str, Any]:
    raw_count = os.cpu_count()
    cpu_count = (
        raw_count
        if isinstance(raw_count, int)
        and not isinstance(raw_count, bool)
        and raw_count > 0
        else None
    )
    try:
        load1, load5, load15 = os.getloadavg()
    except OSError:
        load1 = load5 = load15 = None
    loads = [_to_float(value) for value in (load1, load5, load15)]
    pressure = (
        round(loads[0] / cpu_count * 100, 2)
        if cpu_count is not None and loads[0] is not None
        else None
    )
    return {
        "count": cpu_count,
        "load1": round(loads[0], 2) if loads[0] is not None else None,
        "load5": round(loads[1], 2) if loads[1] is not None else None,
        "load15": round(loads[2], 2) if loads[2] is not None else None,
        "pressure_pct": pressure,
    }


def _meminfo() -> dict[str, Any]:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text("utf-8").splitlines():
            parts = line.split()
            if len(parts) >= 2:
                values[parts[0].rstrip(":")] = int(parts[1]) * 1024
    except Exception:
        pass
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    valid = (
        isinstance(total, int)
        and not isinstance(total, bool)
        and total > 0
        and isinstance(available, int)
        and not isinstance(available, bool)
        and 0 <= available <= total
    )
    used = total - available if valid else None
    return {
        "total_bytes": total if valid else None,
        "available_bytes": available if valid else None,
        "used_bytes": used,
        "used_pct": round(used / total * 100, 2) if valid else None,
    }


def _disk_snapshot() -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(str(PROJECT_ROOT))
    except OSError:
        usage = None
    valid = usage is not None and usage.total > 0
    return {
        "path": str(PROJECT_ROOT),
        "total_bytes": usage.total if valid else None,
        "used_bytes": usage.used if valid else None,
        "free_bytes": usage.free if valid else None,
        "used_pct": round(usage.used / usage.total * 100, 2) if valid else None,
    }


def _gpu_snapshot() -> list[dict[str, Any]]:
    now = _now_ts()
    cached = _GPU_CACHE.get("data")
    if cached is not None and now - float(_GPU_CACHE.get("ts") or 0) < GPU_CACHE_TTL_SEC:
        return cached
    rc, out, err = _run_cmd(
        [
            "nvidia-smi",
            "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu",
            "--format=csv,noheader,nounits",
        ],
        timeout=2.0,
    )
    if rc != 0 or not out:
        data = [{"available": False, "error": err or "nvidia-smi unavailable"}]
        _GPU_CACHE["ts"] = now
        _GPU_CACHE["data"] = data
        return data
    gpus = []
    for line in out.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 6:
            continue
        used = _to_float(parts[3], 0.0) or 0.0
        total = _to_float(parts[4], 0.0) or 0.0
        gpus.append(
            {
                "available": True,
                "index": _to_int(parts[0]),
                "name": parts[1],
                "utilization_pct": _to_float(parts[2], 0.0),
                "memory_used_mib": used,
                "memory_total_mib": total,
                "memory_used_pct": round(used / total * 100, 2) if total else 0.0,
                "temperature_c": _to_float(parts[5], 0.0),
            }
        )
    _GPU_CACHE["ts"] = now
    _GPU_CACHE["data"] = gpus
    return gpus


def _pipeline_processes() -> list[dict[str, Any]]:
    keywords = [
        "wave1",
        "daily_news",
        "adaptive_global_extractor.py",
        "stream_load_news_to_postgres.py",
        "news_quality_labels",
        "stream_l1_event_features.py",
        "ground_news",
        "vllm",
        "serve_prod.py",
    ]
    return _processes_matching(keywords, limit=24)


def _processes_matching(keywords: Iterable[str], limit: int = 20) -> list[dict[str, Any]]:
    rc, out, _err = _run_cmd(
        [
            "ps",
            "-eo",
            "pid=,ppid=,pcpu=,pmem=,etime=,stat=,comm=,args=",
            "--sort=-pcpu",
        ],
        timeout=2.0,
    )
    if rc != 0 or not out:
        return []
    lowered_keywords = [kw.lower() for kw in keywords]
    rows: list[dict[str, Any]] = []
    for line in out.splitlines():
        lowered = line.lower()
        if not any(kw in lowered for kw in lowered_keywords):
            continue
        if "ops_monitor.py" in lowered or "ps -eo" in lowered:
            continue
        parts = line.strip().split(None, 7)
        if len(parts) < 8:
            continue
        matched_label = next(
            (keyword for keyword in lowered_keywords if keyword in lowered),
            "process",
        )
        rows.append(
            {
                "pid": _to_int(parts[0]),
                "ppid": _to_int(parts[1]),
                "cpu_pct": _to_float(parts[2], 0.0),
                "mem_pct": _to_float(parts[3], 0.0),
                "etime": parts[4],
                "stat": parts[5],
                "name": Path(parts[6]).name[:80],
                "label": matched_label[:80],
                "evidence_quality": "heuristic",
                "evidence_source": "process-name-match",
                "authoritative_for_management": False,
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _build_pipelines(db: dict[str, Any]) -> list[dict[str, Any]]:
    pipelines: list[dict[str, Any]] = []

    wave_progress = _read_json(
        DATA_ROOT / "historical_news/jobs/wave1_1y_prod_20260621/wave1_articles_merged_progress.json"
    )
    pipelines.append(
        _progress_pipeline(
            pipeline_id="wave1_extract",
            group="数据获取",
            name="Wave1 历史新闻抽取",
            progress=wave_progress,
            proc=_process_from_pid_file(LOG_ROOT / "wave1_remaining_extract.pid"),
        )
    )
    pipelines.append(_loader_pipeline(db))
    pipelines.append(_daily_pipeline())

    quality = db.get("quality", {})
    missing = _to_int(quality.get("missing_estimate"))
    quality_alerts = []
    if missing > 5000:
        quality_alerts.append(f"待补质量标签约 {missing} 条")
    pipelines.append(
        _simple_loop_pipeline(
            pipeline_id="quality_labels",
            group="质量与清洗",
            name="日期与质量标签清洗",
            pid_file=LOG_ROOT / "news_quality_labels_loop.pid",
            log_file=LOG_ROOT / "news_quality_labels_loop.log",
            metrics=[
                _metric("已标记", quality.get("labels_total", 0)),
                _metric("好新闻", quality.get("good", 0)),
                _metric("坏/旧页", quality.get("bad", 0)),
                _metric("待补", missing),
            ],
            alerts=quality_alerts,
        )
    )

    l1 = db.get("l1_prep", {})
    status_counts = l1.get("status_counts") or {}
    pending = _to_int(status_counts.get("pending_event"))
    event_extracted = _to_int(status_counts.get("event_extracted"))
    skipped_low_quality = _to_int(status_counts.get("skipped_low_quality"))
    pipelines.append(
        _simple_loop_pipeline(
            pipeline_id="l1_prep",
            group="事件处理",
            name="L1 事件准备",
            pid_file=LOG_ROOT / "l1_prep_worker.pid",
            log_file=LOG_ROOT / "l1_prep_worker.log",
            metrics=[
                _metric("总量", l1.get("total", 0)),
                _metric("待抽取", pending),
                _metric("低质跳过", skipped_low_quality),
            ],
        )
    )
    pipelines.append(
        _simple_loop_pipeline(
            pipeline_id="l1_extract",
            group="事件处理",
            name="L1 事件抽取",
            pid_file=LOG_ROOT / "l1_extract_worker.pid",
            log_file=LOG_ROOT / "l1_extract_worker.log",
            metrics=[
                _metric("已抽取", event_extracted),
                _metric("待抽取", pending),
                _metric("L1 成员", db.get("event_coref_members", {}).get("count", 0)),
                _metric("L1 簇", db.get("event_coref_clusters", {}).get("count", 0)),
            ],
            alerts=[f"待抽取 {pending} 条"] if pending > 10000 else [],
        )
    )

    derived = db.get("derived_quality", {})
    pipelines.append(
        _simple_loop_pipeline(
            pipeline_id="ground_realtime",
            group="前端内容",
            name="实时聚类与前端索引",
            pid_file=LOG_ROOT / "ground_news_realtime_refresh_loop.pid",
            log_file=LOG_ROOT / "ground_news_realtime_refresh_loop.log",
            metrics=[
                _metric("L1 成员", db.get("event_coref_members", {}).get("count", 0)),
                _metric("L1.5 片段", db.get("event_l15_segments", {}).get("count", 0)),
                _metric("L2 链", db.get("event_l2_chains", {}).get("count", 0)),
                _metric("源分析", db.get("story_source_breakdown", {}).get("count", 0)),
            ],
            alerts=[
                f"派生层异常日期/低质成员 {derived.get('l1_bad_or_bad_date')} 条"
            ]
            if _to_int(derived.get("l1_bad_or_bad_date")) > 0
            else [],
        )
    )

    coverage = db.get("image_coverage", {})
    large_5 = _to_int(coverage.get("large_5"))
    large_5_cover = _to_int(coverage.get("large_5_with_cover"))
    cover_pct = round(large_5_cover / large_5 * 100, 2) if large_5 else None
    image_alerts = []
    image_status = None
    if cover_pct is not None and cover_pct < 95:
        image_status = "warning"
        image_alerts.append(f"5+ 大簇图片覆盖 {cover_pct}%")
    pipelines.append(
        _simple_loop_pipeline(
            pipeline_id="story_images",
            group="前端内容",
            name="聚类图片覆盖",
            pid_file=LOG_ROOT / "ground_news_image_backfill_loop.pid",
            log_file=LOG_ROOT / "ground_news_image_backfill_loop.log",
            metrics=[
                _metric("文章图", db.get("news_image_assets", {}).get("count", 0)),
                _metric("簇封面", db.get("story_cover_assets", {}).get("count", 0)),
                _metric("5+覆盖", cover_pct if cover_pct is not None else "—", "%"),
                _metric("20+覆盖", _coverage_pct(coverage, "large_20"), "%"),
            ],
            alerts=image_alerts,
            status_override=image_status,
        )
    )

    embedding_count = _to_int(db.get("news_embeddings", {}).get("count"))
    pipelines.append(
        _simple_loop_pipeline(
            pipeline_id="embeddings",
            group="智能计算",
            name="向量嵌入 news_embeddings",
            pid_file=LOG_ROOT / "l1_embeddings_full.pid",
            log_file=LOG_ROOT / "l1_embeddings_full.log",
            metrics=[
                _metric("向量行", embedding_count),
                _metric("好新闻", quality.get("good", 0)),
            ],
            alerts=["向量表为空，语义检索/向量聚类还没有开始"] if embedding_count == 0 else [],
            expected=False,
            status_override="not_started" if embedding_count == 0 else None,
        )
    )

    translated_count = _to_int(db.get("lxy_translated", {}).get("count"))
    pipelines.append(
        _simple_loop_pipeline(
            pipeline_id="translation",
            group="智能计算",
            name="LLM 翻译 lxy_translated",
            pid_file=LOG_ROOT / "llm_translation.pid",
            metrics=[
                _metric("已翻译", translated_count),
                _metric("新闻总量", db.get("news", {}).get("total", 0)),
            ],
            alerts=["翻译表为空，新闻翻译实时管线还没有启动"] if translated_count == 0 else [],
            expected=False,
            status_override="not_started" if translated_count == 0 else None,
        )
    )

    vllm_proc = _process_from_pid_file(LOG_ROOT / "vllm_service_supervisor.pid")
    vllm_running = vllm_proc.get("running") and _port_open("127.0.0.1", 8004)
    pipelines.append(
        _simple_loop_pipeline(
            pipeline_id="vllm",
            group="服务",
            name="vLLM 推理服务",
            pid_file=LOG_ROOT / "vllm_service_supervisor.pid",
            log_file=LOG_ROOT / "vllm_service_supervisor.log",
            metrics=[
                _metric("端口", "open" if _port_open("127.0.0.1", 8004) else "closed"),
                _metric("GPU", len([g for g in _gpu_snapshot() if g.get("available")])),
            ],
            alerts=[] if vllm_running else ["vLLM 端口 8004 未连通"],
            status_override="running" if vllm_running else "warning",
        )
    )

    web_proc = _process_from_pid_file(WEB_PID_FILE)
    web_running = web_proc.get("running") and _port_open("127.0.0.1", WEB_PORT)
    pipelines.append(
        _simple_loop_pipeline(
            pipeline_id="web",
            group="服务",
            name="前端与 API 服务",
            pid_file=WEB_PID_FILE,
            log_file=Path("/root/data/web/logs/globemind_web_prod.log"),
            metrics=[
                _metric("端口", "open" if web_running else "closed"),
                _metric("在线", _online_summary().get("active", 0)),
                _metric("Workers", WEB_WORKERS),
            ],
            alerts=[] if web_running else ["生产 Web/API 端口未连通"],
            status_override="running" if web_running else "failed",
        )
    )

    return pipelines


def _coverage_pct(coverage: dict[str, Any], key_prefix: str) -> Any:
    total = _to_int(coverage.get(key_prefix))
    covered = _to_int(coverage.get(f"{key_prefix}_with_cover"))
    if not total:
        return "—"
    return round(covered / total * 100, 2)


def _first_available_gpu(gpus: list[dict[str, Any]]) -> dict[str, Any]:
    for gpu in gpus:
        if gpu.get("available"):
            return gpu
    return {}


def _cached_overview() -> dict[str, Any]:
    cached = _SNAPSHOT_CACHE.get("data")
    if isinstance(cached, dict) and isinstance(cached.get("overview"), dict):
        return dict(cached["overview"])
    return {}


def _fast_progress_update(pipeline_id: str, progress: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": pipeline_id,
        "updated_at": progress.get("updated_at") or progress.get("_mtime"),
        "progress_pct": _pct(progress.get("completion_rate")),
        "rate_per_min": _to_float(progress.get("successes_per_min")),
        "eta_sec": _eta_seconds(progress.get("rows_remaining"), progress.get("successes_per_sec")),
        "successes": _to_optional_int(progress.get("successes")),
        "failures": _to_optional_int(progress.get("failures")),
        "remaining": _to_optional_int(progress.get("rows_remaining")),
        "active_tasks": _to_optional_int(progress.get("active_tasks")),
    }


def _fast_snapshot() -> dict[str, Any]:
    wave_progress = _read_json(
        DATA_ROOT / "historical_news/jobs/wave1_1y_prod_20260621/wave1_articles_merged_progress.json"
    )
    run_dir, daily_progress, _prune, _prefilter, _load_state = _daily_pipeline_context()
    system = _fast_system_snapshot()
    online = _online_summary()
    gpus = system.get("gpus", [])
    gpu = _first_available_gpu(gpus)
    cached_overview = _cached_overview()
    overview = {
        **cached_overview,
        "wave1_progress_pct": _pct(wave_progress.get("completion_rate")),
        "daily_progress_pct": _pct(daily_progress.get("completion_rate")),
        "online_active": online.get("active", 0),
        "server_pressure_pct": system.get("cpu", {}).get("pressure_pct"),
        "memory_used_pct": system.get("memory", {}).get("used_pct"),
        "gpu_count": len([item for item in gpus if item.get("available")]),
        "gpu_utilization_pct": gpu.get("utilization_pct"),
        "gpu_memory_used_pct": gpu.get("memory_used_pct"),
    }
    data = {
        "ok": True,
        "generated_at": _now_iso(),
        "overview": overview,
        "online": online,
        "system": system,
        "pipeline_updates": [
            _fast_progress_update("wave1_extract", wave_progress),
            _fast_progress_update("daily_ingest", daily_progress or {"_missing": True}),
        ],
        "daily_run": run_dir.name if run_dir else None,
    }
    data["series"] = _history_payload()
    return data


def _history_sample(data: dict[str, Any]) -> dict[str, Any]:
    overview = data.get("overview") or {}
    updates = {
        update.get("id"): update
        for update in data.get("pipeline_updates", [])
        if isinstance(update, dict) and update.get("id")
    }
    wave = updates.get("wave1_extract", {})
    daily = updates.get("daily_ingest", {})
    return {
        "ts": _now_ts(),
        "time": data.get("generated_at") or _now_iso(),
        "news_total": _to_optional_int(overview.get("news_total")),
        "online_active": _to_optional_int(overview.get("online_active")),
        "cpu_pressure_pct": _bounded_number(overview.get("server_pressure_pct")),
        "memory_used_pct": _bounded_number(overview.get("memory_used_pct")),
        "gpu_utilization_pct": _bounded_number(overview.get("gpu_utilization_pct")),
        "gpu_memory_used_pct": _bounded_number(overview.get("gpu_memory_used_pct")),
        "wave_progress_pct": _bounded_number(overview.get("wave1_progress_pct")),
        "daily_progress_pct": _bounded_number(overview.get("daily_progress_pct")),
        "wave_rate_per_min": _bounded_number(wave.get("rate_per_min") or _pipeline_rate_from_snapshot(data, "wave1_extract")),
        "daily_rate_per_min": _bounded_number(daily.get("rate_per_min") or _pipeline_rate_from_snapshot(data, "daily_ingest")),
        "wave_remaining": _to_optional_int(wave.get("remaining")),
        "daily_remaining": _to_optional_int(daily.get("remaining")),
    }


def _pipeline_rate_from_snapshot(data: dict[str, Any], pipeline_id: str) -> Optional[float]:
    for pipeline in data.get("pipelines", []) or []:
        if isinstance(pipeline, dict) and pipeline.get("id") == pipeline_id:
            return _to_float(pipeline.get("rate_per_min"))
    return None


def _read_history_unlocked() -> list[dict[str, Any]]:
    return _history_store().read()


def _append_history_sample(sample: dict[str, Any]) -> list[dict[str, Any]]:
    return _history_store().append(sample)


def _history_payload(samples: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    return _history_store().payload(samples)


def _history_store() -> MonitoringHistoryStore:
    return MonitoringHistoryStore(
        data_path=HISTORY_FILE,
        lock_path=HISTORY_LOCK,
        policy=MonitoringHistoryPolicy(
            max_points=HISTORY_MAX_POINTS,
            minimum_interval_seconds=HISTORY_MIN_INTERVAL_SEC,
        ),
    )


def _overview(db: dict[str, Any], pipelines: list[dict[str, Any]], system: dict[str, Any], online: dict[str, Any]) -> dict[str, Any]:
    status_counts = Counter(pipeline.get("status") for pipeline in pipelines)
    wave = next((p for p in pipelines if p.get("id") == "wave1_extract"), {})
    daily = next((p for p in pipelines if p.get("id") == "daily_ingest"), {})
    gpu = _first_available_gpu(system.get("gpus", []))
    return {
        "status_counts": dict(status_counts),
        "news_total": db.get("news", {}).get("total"),
        "latest_good_published_at": db.get("quality", {}).get("latest_good_published_at"),
        "good_last_24h": db.get("quality", {}).get("good_last_24h"),
        "wave1_progress_pct": wave.get("progress_pct"),
        "daily_progress_pct": daily.get("progress_pct"),
        "online_active": online.get("active"),
        "server_pressure_pct": system.get("cpu", {}).get("pressure_pct"),
        "memory_used_pct": system.get("memory", {}).get("used_pct"),
        "gpu_count": len([g for g in system.get("gpus", []) if g.get("available")]),
        "gpu_utilization_pct": gpu.get("utilization_pct"),
        "gpu_memory_used_pct": gpu.get("memory_used_pct"),
    }


def _snapshot() -> dict[str, Any]:
    db = _db_snapshot()
    system = _system_snapshot()
    online = _online_summary()
    catalog = _runtime_catalog_snapshot()
    pipelines = attach_catalog_management(_build_pipelines(db), catalog)
    data = {
        "ok": True,
        "generated_at": _now_iso(),
        "overview": _overview(db, pipelines, system, online),
        "pipelines": pipelines,
        "db": db,
        "system": system,
        "online": online,
        "runtime_catalog": catalog,
    }
    data["series"] = _history_payload()
    return data


@router.post("/heartbeat")
def heartbeat(payload: HeartbeatPayload, request: Request) -> dict[str, Any]:
    _heartbeat_update(payload, request)
    return {"ok": True}


def _runtime_catalog_snapshot() -> dict[str, Any]:
    try:
        return load_runtime_catalog()
    except RuntimeCatalogUnavailable:
        return unavailable_runtime_catalog()


@router.get("/runtime-catalog")
def runtime_catalog(
    _user: Dict[str, Any] = Depends(get_current_user_required),
) -> dict[str, Any]:
    try:
        return load_runtime_catalog()
    except RuntimeCatalogUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "runtime-catalog-unavailable"},
        ) from exc


@router.get("/pipeline-monitor")
def pipeline_monitor(
    fresh: bool = False,
    _user: Dict[str, Any] = Depends(get_current_user_required),
) -> dict[str, Any]:
    now = _now_ts()
    if not fresh and _SNAPSHOT_CACHE.get("data") and now - float(_SNAPSHOT_CACHE.get("ts") or 0) < SNAPSHOT_CACHE_TTL_SEC:
        cached = dict(_SNAPSHOT_CACHE["data"])
        cached["cached"] = True
        cached["online"] = _online_summary()
        cached["overview"] = {
            **cached.get("overview", {}),
            "online_active": cached["online"].get("active", 0),
        }
        cached["series"] = _history_payload()
        return cached
    data = sanitize_diagnostic(_snapshot())
    _SNAPSHOT_CACHE["ts"] = now
    _SNAPSHOT_CACHE["data"] = data
    return data


@router.get("/pipeline-monitor/fast")
def pipeline_monitor_fast(
    _user: Dict[str, Any] = Depends(get_current_user_required),
) -> dict[str, Any]:
    return _fast_snapshot()
