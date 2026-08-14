#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
从 PostgreSQL 将 JSONB 向量灌回 Milvus（灾难恢复 / 离线修复）。

- 先拉 id 列表，再按块并行 SELECT（ThreadPoolExecutor），重叠 DB I/O。
- 向量字段反序列化走 analysis_service._deserialize_bge_embedding_from_db（支持 orjson 路径）。
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from agentic_rag import analysis_service as svc
from agentic_rag.db.news_analysis_schema import TABLE_NAME as _NA
from agentic_rag.db.news_analysis_schema import sql_join_news_embeddings
from agentic_rag.pipeline.sim_time_window import sim_pub_time_and


def _pg_read():
    return svc._pg_read()


def _fetch_id_chunk_worker(
    sql_template: str,
    chunk: List[int],
    *,
    china_only: bool,
) -> List[Dict[str, Any]]:
    ex = _pg_read()
    if not chunk:
        return []
    ids_str = ",".join(str(int(i)) for i in chunk)
    sql = sql_template.format(ids=ids_str)
    res = ex.query(sql)
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "chunk query failed")
    rows = res.get("rows") or []
    out: List[Dict[str, Any]] = []
    for row in rows:
        t = row.get("title") or ""
        ab = row.get("abstract") or ""
        ic = row.get("is_china_related")
        if china_only:
            ic_rel = True
        else:
            ic_rel = bool(ic) if ic is not None else False
        out.append({
            "id": int(row["id"]),
            "title": t,
            "text": f"{t} {ab}".strip(),
            "is_china_related": ic_rel,
            "bge_embedding": svc._deserialize_bge_embedding_from_db(row.get("bge_embedding")),
        })
    return out


def sync_china_news_from_db_disaster_recovery(
    limit: int = 2000,
    *,
    china_only: Optional[bool] = None,
    max_workers: int = 5,
) -> int:
    """
    等价于 sync_china_news_from_db，但 id 分块并行拉取明细行。
    """
    from agentic_rag.db.news_analysis_schema import ensure_news_analysis_table

    try:
        ensure_news_analysis_table()
    except Exception as e:
        print(f"[MilvusDR] news_analysis 表确保: {type(e).__name__}: {e}")
    if not svc._milvus_sync_enabled():
        print("[MilvusDR] MILVUS_SYNC=0，跳过")
        return 0
    if china_only is None:
        china_only = svc._milvus_sync_china_only_from_env()

    ex = _pg_read()
    lim = max(1, min(int(limit), 500_000))
    sim_sql = sim_pub_time_and("n")

    _ne_join = sql_join_news_embeddings("n", "ne", "LEFT")
    if china_only:
        id_sql = (
            f"SELECT n.id FROM news n INNER JOIN {_NA} na ON na.news_id = n.id "
            f"WHERE na.is_china_related IS TRUE AND na.duplicate_of IS NULL "
            f"AND n.title IS NOT NULL AND n.title != '' {sim_sql} ORDER BY n.id DESC LIMIT {lim}"
        )
        detail_sql = (
            "SELECT n.id, n.title, n.abstract, n.pub_time, ne.bge_embedding FROM news n "
            f"INNER JOIN {_NA} na ON na.news_id = n.id "
            f"{_ne_join} "
            "WHERE n.id IN ({ids}) "
            "AND na.is_china_related IS TRUE AND na.duplicate_of IS NULL "
            "AND n.title IS NOT NULL AND n.title != '' "
        )
    else:
        id_sql = (
            f"SELECT n.id FROM news n LEFT JOIN {_NA} na ON na.news_id = n.id "
            f"WHERE n.title IS NOT NULL AND n.title != '' "
            f"AND (na.duplicate_of IS NULL) {sim_sql} ORDER BY n.id DESC LIMIT {lim}"
        )
        detail_sql = (
            "SELECT n.id, n.title, n.abstract, n.pub_time, na.is_china_related, ne.bge_embedding FROM news n "
            f"LEFT JOIN {_NA} na ON na.news_id = n.id "
            f"{_ne_join} "
            "WHERE n.id IN ({ids}) "
            "AND n.title IS NOT NULL AND n.title != '' "
            "AND (na.duplicate_of IS NULL) "
        )

    res = ex.query(id_sql)
    if not res.get("ok"):
        raise RuntimeError(f"MilvusDR id 查询失败: {res.get('error')}")
    id_rows = res.get("rows") or []
    ids = [int(r["id"]) for r in id_rows]
    if not ids:
        print("[MilvusDR] 无候选 id")
        return 0

    chunk_sz = max(1, min(int(os.getenv("MILVUS_DR_CHUNK", "400")), 5000))
    chunks: List[List[int]] = [ids[i : i + chunk_sz] for i in range(0, len(ids), chunk_sz)]
    print(
        f"[MilvusDR] 候选 {len(ids)} 条 id，分 {len(chunks)} 块并行 workers={max_workers}",
        flush=True,
    )

    records: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as pool:
        futs = {
            pool.submit(
                _fetch_id_chunk_worker,
                detail_sql,
                ch,
                china_only=china_only,
            ): ch
            for ch in chunks
        }
        for fut in as_completed(futs):
            records.extend(fut.result())

    records.sort(key=lambda r: int(r["id"]), reverse=True)

    chunk = max(1, min(int(os.getenv("MILVUS_SYNC_CHUNK", "1000")), 10_000))
    n_total = len(records)
    for off in range(0, n_total, chunk):
        part = records[off : off + chunk]
        last = off + chunk >= n_total
        svc.sync_china_news_to_milvus(
            part,
            only_china=china_only,
            defer_store_flush=not last,
        )
    if n_total > chunk:
        print(
            f"[MilvusDR] 已按 MILVUS_SYNC_CHUNK={chunk} 分块路由，末块后统一 flush",
            flush=True,
        )
    return n_total
