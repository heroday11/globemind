"""
媒体域名信任分：PostgreSQL media_metadata → media_trust.json → ontology 默认分；
未知域名可异步占位记录（后续接 LLM 画像）。
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Dict

from urllib.parse import urlparse

from config.settings import get_media_trust_json_path, get_trust_default_media_score

logger = logging.getLogger(__name__)

_MEDIA_TRUST: Dict[str, float] | None = None
# 进程内解析结果缓存（同域名不重复查库、不重复触发占位异步任务）
_RESOLVED_HOST_TRUST: Dict[str, float] = {}
_trust_read_executor = None


def _normalize_host(netloc: str) -> str:
    h = (netloc or "").strip().lower()
    if h.startswith("www."):
        h = h[4:]
    return h


def _get_trust_read_executor():
    """复用只读 SafePGExecutor 单例（与 pg_client 一致；每次 query 仍短连接）。"""
    global _trust_read_executor
    if _trust_read_executor is None:
        from agentic_rag.db.pg_client import get_read_executor

        _trust_read_executor = get_read_executor(max_rows=10, force_limit=True)
    return _trust_read_executor


def _load_media_trust_table() -> Dict[str, float]:
    global _MEDIA_TRUST
    if _MEDIA_TRUST is not None:
        return _MEDIA_TRUST
    path = get_media_trust_json_path()
    out: Dict[str, float] = {}
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k, v in raw.items():
                    try:
                        key = _normalize_host(str(k))
                        if key:
                            out[key] = float(v)
                    except (TypeError, ValueError):
                        continue
        except (json.JSONDecodeError, OSError):
            pass
    _MEDIA_TRUST = out
    return out


def _lookup_trust_in_db(host: str) -> float | None:
    if not host:
        return None
    sql = "SELECT trust_score FROM media_metadata WHERE domain = %s LIMIT 1"
    try:
        ex = _get_trust_read_executor()
        r = ex.query(sql, (host,))
    except Exception as e:
        logger.debug("media_metadata 查询失败，回退 JSON: %s", e)
        return None
    if not r.get("ok") or r.get("error"):
        return None
    rows = r.get("rows") or []
    if not rows:
        return None
    ts = rows[0].get("trust_score")
    if ts is None:
        return None
    try:
        return float(ts)
    except (TypeError, ValueError):
        return None


def _schedule_domain_profiling_placeholder(domain: str, source_url: str) -> None:
    """占位：后续可改为 LLM 根据文章调性写入 media_metadata。"""

    def _run() -> None:
        u = (source_url or "").strip()
        logger.info(
            "[media_trust] async profile placeholder domain=%s url=%s",
            domain,
            u[:240] if u else "",
        )

    t = threading.Thread(target=_run, daemon=True)
    t.start()


def get_media_trust(url: str) -> float:
    """
    多级查找：
      1) media_metadata（域名）
      2) media_trust.json
      3) ontology default_media_score（默认 0.4），并对未知域名触发异步占位日志（后续 LLM 画像）

    同进程内对已解析域名做缓存，避免重复查库与重复调度。
    """
    default = get_trust_default_media_score()
    if not url or not str(url).strip():
        return default
    try:
        raw_u = str(url).strip()
        if "://" not in raw_u:
            raw_u = "https://" + raw_u
        p = urlparse(raw_u)
        host = _normalize_host(p.netloc)
    except Exception:
        return default
    if not host:
        return default

    if host in _RESOLVED_HOST_TRUST:
        return float(_RESOLVED_HOST_TRUST[host])

    db_val = _lookup_trust_in_db(host)
    if db_val is not None:
        _RESOLVED_HOST_TRUST[host] = db_val
        return db_val

    table = _load_media_trust_table()
    if host in table:
        v = float(table[host])
        _RESOLVED_HOST_TRUST[host] = v
        return v

    _schedule_domain_profiling_placeholder(host, url)
    _RESOLVED_HOST_TRUST[host] = default
    return default
