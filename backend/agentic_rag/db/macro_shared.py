#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从 build_macro_events.py 提取的共享工具函数，供 macro_llm_fill_pending / audit / runner 等使用。"""

from __future__ import annotations

import os
from collections import Counter
from typing import Any, Dict, List, Set, Tuple

from agentic_rag.db_runtime_config import require_database_password

FRAGMENT_DESC_PREFIX = "【零碎线索】"
UNVERIFIED_SOURCE_PREFIX = "[Unverified Source] "


def _pg_write_executor():
    """与 analysis_service._pg_write 一致的写库配置（短连接 + 事务，非连接池）。"""
    from agentic_rag.db.executor import SafePGExecutor
    from agentic_rag.db.security import PGSecurityConfig

    return SafePGExecutor(PGSecurityConfig(
        host=os.getenv("PG_HOST", "127.0.0.1"),
        port=int(os.getenv("PG_PORT", "5432")),
        dbname="postgres",
        user=os.getenv("PG_WRITE_USER", "postgres"),
        password=require_database_password(),
        max_rows=100_000,
        force_limit=False,
    ))


def _table_columns(cur, table: str) -> Set[str]:
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (table,),
    )
    return {r[0] for r in cur.fetchall()}


def _table_column_meta(cur, table: str) -> Dict[str, dict]:
    cur.execute(
        """
        SELECT column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
        """,
        (table,),
    )
    out: Dict[str, dict] = {}
    for name, data_type, is_nullable, column_default in cur.fetchall():
        out[str(name)] = {
            "data_type": str(data_type),
            "is_nullable": str(is_nullable) == "YES",
            "column_default": column_default,
        }
    return out


def _default_value_for_not_null_column(col: str, meta: dict):
    dtype = str(meta.get("data_type") or "").lower()
    if "double precision" in dtype or "real" in dtype or "numeric" in dtype or "decimal" in dtype:
        return 0.0
    if "integer" in dtype or "bigint" in dtype or "smallint" in dtype:
        return 0
    if "boolean" in dtype:
        return False
    if "json" in dtype:
        from psycopg2.extras import Json
        return Json([])
    if "character" in dtype or "text" in dtype:
        return ""
    return None


def pg_connect():
    import psycopg2

    return psycopg2.connect(
        host=os.getenv("PG_HOST", "127.0.0.1"),
        port=int(os.getenv("PG_PORT", "5432")),
        dbname="postgres",
        user=os.getenv("PG_USER", "news_reader"),
        password=require_database_password("PG_PASSWORD", "DB_PASSWORD"),
        connect_timeout=15,
    )


def verify_macro_tables_ready() -> Tuple[int, int, int]:
    """Stage 6 前校验：macro_storylines、micro_events、storyline_micro_map 非空。"""
    n_m = n_e = n_k = 0
    conn = pg_connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM macro_storylines")
        n_m = int(cur.fetchone()[0])
        cur.execute("SELECT COUNT(*) FROM micro_events")
        n_e = int(cur.fetchone()[0])
        cur.execute("SELECT COUNT(*) FROM storyline_micro_map")
        n_k = int(cur.fetchone()[0])
    finally:
        conn.close()
    if n_m < 1 or n_k < 1:
        raise RuntimeError(
            f"宏观表未就绪: macro_storylines={n_m}, storyline_micro_map={n_k}（需先成功运行 Stage 5 含 DB 回写）"
        )
    if n_e < 1:
        raise RuntimeError("micro_events 为空，无法进行 Obsidian 同步（请确认 Stage 5 已写入微事件）")
    return n_m, n_e, n_k


def _intel_label_clean(val: Any) -> str:
    if val is None:
        return ""
    s = str(val).strip()
    if not s or s.upper() == "PARSE_FAILED":
        return ""
    return s


def aggregate_intel_for_cluster(
    news_ids: List[int],
    intel: Dict[int, dict],
    base_entities: Set[str],
) -> dict:
    china_vals: List[float] = []
    sent_c: Counter[str] = Counter()
    topic_c: Counter[str] = Counter()
    ents: Set[str] = set(base_entities)

    for nid in news_ids:
        row = intel.get(int(nid))
        if not row:
            continue
        ci = row.get("china_related_index")
        if ci is not None:
            try:
                china_vals.append(float(ci))
            except (TypeError, ValueError):
                pass
        sv = _intel_label_clean(row.get("sentiment_analysis"))
        if sv:
            sent_c[sv] += 1
        tv = _intel_label_clean(row.get("topic_classification"))
        if tv:
            topic_c[tv] += 1
        el = row.get("entities_list") or []
        ents.update(str(e).strip().lower() for e in el if e)

    out: Dict[str, Any] = {
        "china_index_avg": float(sum(china_vals) / len(china_vals)) if china_vals else None,
        "sentiment_main": sent_c.most_common(1)[0][0] if sent_c else None,
        "topic_main": topic_c.most_common(1)[0][0] if topic_c else None,
        "entities_pool": ents,
    }
    return out


def apply_unverified_source_prefix(
    macro_events: List[dict],
    macro_summaries: Dict[int, str],
) -> None:
    """confidence_score < 0.5 时在宏观综述前加 [Unverified Source] 前缀。"""
    from agentic_rag.macro_llm_naming import PLACEHOLDER_SUMMARY
    from config.settings import get_trust_default_media_score

    default = float(get_trust_default_media_score())
    for macro in macro_events:
        sid = int(macro["macro_id"])
        conf = macro.get("confidence_score")
        try:
            c = float(conf) if conf is not None else default
        except (TypeError, ValueError):
            c = default
        if c >= 0.5:
            continue
        s = macro_summaries.get(sid, "")
        if not s:
            continue
        if s.strip() == PLACEHOLDER_SUMMARY:
            continue
        if not s.startswith(UNVERIFIED_SOURCE_PREFIX):
            macro_summaries[sid] = UNVERIFIED_SOURCE_PREFIX + s


def fetch_news_intel_map(news_ids: List[int]) -> Dict[int, dict]:
    """拉取新闻涉华指数、情感、主题、实体（供微/宏观聚合）。"""
    if not news_ids:
        return {}
    import psycopg2.extras

    out: Dict[int, dict] = {}
    conn = pg_connect()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        nchunks = (len(news_ids) + 4999) // 5000
        for i in range(0, len(news_ids), 5000):
            chunk = [int(x) for x in news_ids[i : i + 5000]]
            ci = i // 5000 + 1
            if i == 0 or ci % 5 == 0 or i + 5000 >= len(news_ids):
                print(
                    f"[PG] news intel chunk {ci}/{nchunks} ({len(chunk)} ids)…",
                    flush=True,
                )
            cur.execute(
                "SELECT n.id, na.china_related_index, na.sentiment_analysis, "
                "na.topic_classification, na.entities "
                "FROM news n "
                "LEFT JOIN news_analysis na ON na.news_id = n.id "
                "WHERE n.id = ANY(%s)",
                (chunk,),
            )
            for r in cur.fetchall():
                nid = int(r["id"])
                ents = r.get("entities")
                elist: List[str] = []
                if isinstance(ents, str):
                    try:
                        ents = json.loads(ents)
                    except Exception:
                        ents = []
                if isinstance(ents, list):
                    elist = [str(x).strip().lower() for x in ents if x]
                out[nid] = {
                    "china_related_index": r.get("china_related_index"),
                    "sentiment_analysis": r.get("sentiment_analysis"),
                    "topic_classification": r.get("topic_classification"),
                    "entities_list": elist,
                }
    finally:
        conn.close()
    return out
