"""
从 Milvus / PostgreSQL / macro_events.json 拉取导出所需原始数据。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pymilvus import Collection

from agentic_rag.db.executor import SafePGExecutor
from agentic_rag.db.security import PGSecurityConfig
from config.settings import macro_events_json_path


def fetch_news_clusters(col: Collection, batch_size: int = 2000) -> Dict[int, int]:
    """返回 {news_id: cluster_id}"""
    mapping: Dict[int, int] = {}
    last_id = -1
    while True:
        rows = col.query(
            expr=f"news_id > {last_id}",
            output_fields=["news_id", "cluster_id"],
            limit=batch_size,
        )
        if not rows:
            break
        rows_sorted = sorted(rows, key=lambda r: r["news_id"])
        for row in rows_sorted:
            mapping[int(row["news_id"])] = int(row.get("cluster_id", -1))
        last_id = int(rows_sorted[-1]["news_id"])
        print(f"[Milvus] fetched={len(mapping)}")
        if len(rows_sorted) < batch_size:
            break
    return mapping


def fetch_news_embeddings(col: Collection, batch_size: int = 2000) -> tuple:
    """返回 (news_ids_list, embeddings_np, cluster_ids_list)"""
    import numpy as np

    news_ids, embeddings, cluster_ids = [], [], []
    last_id = -1
    while True:
        rows = col.query(
            expr=f"news_id > {last_id}",
            output_fields=["news_id", "embedding", "cluster_id"],
            limit=batch_size,
        )
        if not rows:
            break
        rows_sorted = sorted(rows, key=lambda r: r["news_id"])
        for row in rows_sorted:
            news_ids.append(int(row["news_id"]))
            embeddings.append(row["embedding"])
            cluster_ids.append(int(row.get("cluster_id", -1)))
        last_id = news_ids[-1]
        if len(news_ids) % 10000 == 0:
            print(f"[Milvus] fetched={len(news_ids)}")
        if len(rows_sorted) < batch_size:
            break
    print(f"[Milvus] Total fetched vectors: {len(news_ids)}")
    return news_ids, np.asarray(embeddings, dtype=np.float32), cluster_ids


def fetch_titles(news_ids: List[int], chunk_size: int = 1000) -> Dict[int, str]:
    cfg = PGSecurityConfig(max_rows=chunk_size + 100, force_limit=True)
    executor = SafePGExecutor(cfg)
    title_map: Dict[int, str] = {}
    for start in range(0, len(news_ids), chunk_size):
        chunk = news_ids[start : start + chunk_size]
        ids_sql = ",".join(str(i) for i in chunk)
        sql = "SELECT id, title FROM news " f"WHERE id IN ({ids_sql}) LIMIT {len(chunk)}"
        result = executor.query(sql)
        if not result["ok"]:
            print(f"[PG] Warning: {result.get('error')}")
            continue
        for row in result["rows"]:
            title_map[int(row["id"])] = (row.get("title") or "").strip()
        print(f"[PG] titles fetched={min(start + chunk_size, len(news_ids))}/{len(news_ids)}")
    return title_map


def fetch_cluster_meta() -> Dict[int, dict]:
    cfg = PGSecurityConfig(max_rows=10000, force_limit=False)
    executor = SafePGExecutor(cfg)
    result = executor.query("SELECT cluster_id, article_count FROM cluster_meta LIMIT 10000")
    meta: Dict[int, dict] = {}
    if result["ok"]:
        for row in result["rows"]:
            meta[int(row["cluster_id"])] = {
                "article_count": int(row.get("article_count") or 0),
            }
    print(f"[PG] cluster_meta rows={len(meta)}")
    return meta


def fetch_pg_macro_and_micro_titles() -> Tuple[Dict[int, str], Dict[int, str]]:
    """从 macro_storylines / micro_events 补全标题（JSON 无标题时）。"""
    macro_titles: Dict[int, str] = {}
    micro_titles: Dict[int, str] = {}
    cfg = PGSecurityConfig(max_rows=250000, force_limit=False)
    executor = SafePGExecutor(cfg)
    try:
        r = executor.query("SELECT storyline_id, title FROM macro_storylines LIMIT 50000")
        if r.get("ok") and r.get("rows"):
            for row in r["rows"]:
                try:
                    sid = int(row["storyline_id"])
                    t = (row.get("title") or "").strip()
                    if t:
                        macro_titles[sid] = t
                except (TypeError, ValueError, KeyError):
                    continue
            print(f"[PG] macro_storylines titles={len(macro_titles)}")
    except Exception as e:
        print(f"[PG] macro_storylines: {e}")
    try:
        r = executor.query("SELECT event_id, title FROM micro_events LIMIT 200000")
        if r.get("ok") and r.get("rows"):
            for row in r["rows"]:
                try:
                    eid = int(row["event_id"])
                    t = (row.get("title") or "").strip()
                    if t:
                        micro_titles[eid] = t
                except (TypeError, ValueError, KeyError):
                    continue
            print(f"[PG] micro_events titles={len(micro_titles)}")
    except Exception as e:
        print(f"[PG] micro_events: {e}")
    return macro_titles, micro_titles


def load_macro_events_bundle() -> Tuple[Dict[int, dict], Dict[int, int]]:
    """
    从 macro_events.json 加载宏观事件；权威 ID 为 storyline_id（缺省同 macro_id）。
    返回 (macro_events 字典, fine_cluster_id -> storyline_id)。
    """
    macro_file = macro_events_json_path()
    macro: Dict[int, dict] = {}
    fine_to_macro: Dict[int, int] = {}
    if not macro_file.exists():
        print(f"[Macro] {macro_file} not found, run build_macro_events.py first")
        return macro, fine_to_macro
    try:
        data = json.loads(macro_file.read_text(encoding="utf-8"))
        for k, v in data.get("fine_to_macro", {}).items():
            try:
                fine_to_macro[int(k)] = int(v)
            except (TypeError, ValueError):
                continue
        for ev in data.get("macro_events", []):
            sid = int(ev.get("storyline_id", ev["macro_id"]))
            macro[sid] = {
                "article_count": int(ev.get("article_count", 0)),
                "window": str(ev.get("window", "")),
                "timeline_start": ev.get("timeline_start", ""),
                "timeline_end": ev.get("timeline_end", ""),
                "fine_cluster_count": int(ev.get("fine_cluster_count", 0)),
                "fine_cluster_ids": [int(x) for x in ev.get("fine_cluster_ids", [])],
                "centroid": ev.get("centroid", []),
                "storyline_id": sid,
                "macro_id": sid,
                "macro_title": (
                    (ev.get("macro_title") or ev.get("title") or ev.get("storyline_title") or "").strip()
                ),
            }
        if not fine_to_macro and macro:
            for sid, mev in macro.items():
                for fid in mev.get("fine_cluster_ids", []):
                    fine_to_macro[int(fid)] = int(sid)
        print(f"[Macro] Loaded {len(macro)} macro events, fine_to_macro entries={len(fine_to_macro)}")
    except Exception as e:
        print(f"[Macro] load error: {e}")
    return macro, fine_to_macro
