#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""模拟器 SKIP_MACRO_LLM_NAMING=1 之后：批量补全宏观故事线 LLM 标题与综述。

用法::

    python -m agentic_rag.macro_llm_fill_pending [--dry-run] [--limit N]

会清除进程内 ``SKIP_MACRO_LLM_NAMING``，仅处理 ``description`` / ``topic_main`` 仍为占位符的行。
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, List, Set

from agentic_rag.db.macro_shared import (
    _pg_write_executor,
    _table_columns,
    aggregate_intel_for_cluster,
    apply_unverified_source_prefix,
    fetch_news_intel_map,
)
from agentic_rag.macro_llm_naming import (
    PLACEHOLDER_SUMMARY,
    PLACEHOLDER_TOPIC_MAIN,
    resolve_macro_intel,
)


def _member_news_ids(cur, event_ids: List[int]) -> List[int]:
    if not event_ids:
        return []
    cur.execute(
        "SELECT DISTINCT news_id FROM micro_event_members WHERE event_id = ANY(%s)",
        (event_ids,),
    )
    return [int(r[0]) for r in cur.fetchall()]


def list_pending_storyline_ids(limit: int | None) -> List[int]:
    """占位符尚未补全的 storyline_id 列表（只读）。"""
    ex = _pg_write_executor()
    conn = ex.get_write_conn()
    try:
        cur = conn.cursor()
        lim_sql = ""
        if limit is not None:
            lim_sql = f" LIMIT {max(0, int(limit))}"
        cur.execute(
            f"""
            SELECT storyline_id FROM macro_storylines
            WHERE (description = %s OR topic_main = %s)
              AND (status IS NULL OR status <> 'fragment')
            ORDER BY storyline_id
            {lim_sql}
            """,
            (PLACEHOLDER_SUMMARY, PLACEHOLDER_TOPIC_MAIN),
        )
        return [int(r[0]) for r in cur.fetchall()]
    finally:
        conn.close()


def fill_one_storyline(sid: int, *, dry_run: bool) -> tuple[str, str]:
    """
    单条连接内补全一条故事线。返回 (status, detail)，
    status in ok, skip, dry_ok, error。
    """
    os.environ.pop("SKIP_MACRO_LLM_NAMING", None)
    use_llm = os.getenv("MACRO_USE_LLM_NAMING", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )
    ex = _pg_write_executor()
    conn = ex.get_write_conn()
    try:
        cur = conn.cursor()
        c_ms = _table_columns(cur, "macro_storylines")
        sel_cols = ["storyline_id", "title", "description", "status"]
        if "confidence_score" in c_ms:
            sel_cols.append("confidence_score")
        cur.execute(
            f"SELECT {', '.join(sel_cols)} FROM macro_storylines WHERE storyline_id = %s",
            (sid,),
        )
        row = cur.fetchone()
        if not row:
            return ("skip", "no row")
        row_d = dict(zip(sel_cols, row))
        conf_val: Any = None
        if "confidence_score" in c_ms and row_d.get("confidence_score") is not None:
            try:
                conf_val = float(row_d["confidence_score"])
            except (TypeError, ValueError):
                conf_val = None

        cur.execute(
            "SELECT event_id FROM storyline_micro_map WHERE storyline_id = %s ORDER BY event_id",
            (sid,),
        )
        eids = [int(r[0]) for r in cur.fetchall()]
        if not eids:
            return ("skip", "no storyline_micro_map")

        cur.execute(
            "SELECT event_id, title FROM micro_events WHERE event_id = ANY(%s)",
            (eids,),
        )
        micro_titles: Dict[int, str] = {}
        for eid, title in cur.fetchall():
            micro_titles[int(eid)] = (title or "").strip() or f"微簇{int(eid)}"

        news_ids = _member_news_ids(cur, eids)
        intel = fetch_news_intel_map(news_ids)
        base_ent: Set[str] = set()
        ag = aggregate_intel_for_cluster(news_ids, intel, base_ent)

        macro_event: Dict[str, Any] = {
            "macro_id": sid,
            "fine_cluster_ids": eids,
            "is_fragment": False,
            "confidence_score": conf_val,
            "china_index_avg": ag.get("china_index_avg"),
            "sentiment_main": ag.get("sentiment_main"),
            "topic_main": ag.get("topic_main"),
        }

        titles_map, summ_map = resolve_macro_intel(
            [macro_event],
            micro_titles,
            use_llm=use_llm,
        )
        macro_summaries = {sid: summ_map.get(sid, "")}
        apply_unverified_source_prefix([macro_event], macro_summaries)

        new_title = (titles_map.get(sid) or "").strip() or f"故事线{sid}"
        new_desc = (macro_summaries.get(sid) or "").strip()
        new_topic = (ag.get("topic_main") or "").strip() or None
        new_sent = (ag.get("sentiment_main") or "").strip() or None
        ci = ag.get("china_index_avg")

        if dry_run:
            print(
                f"[dry-run] storyline_id={sid} title={new_title!r} "
                f"desc_len={len(new_desc)} topic_main={new_topic!r}",
                flush=True,
            )
            return ("dry_ok", "")

        sets = ["title = %s", "description = %s"]
        vals: List[Any] = [new_title, new_desc]
        if "topic_main" in c_ms:
            sets.append("topic_main = %s")
            vals.append(new_topic)
        if "sentiment_main" in c_ms:
            sets.append("sentiment_main = %s")
            vals.append(new_sent)
        if "china_index_avg" in c_ms:
            sets.append("china_index_avg = %s")
            vals.append(ci)
        vals.append(sid)
        sql = f"UPDATE macro_storylines SET {', '.join(sets)} WHERE storyline_id = %s"
        cur.execute(sql, vals)
        conn.commit()
        print(f"[macro_llm_fill_pending] updated storyline_id={sid}", flush=True)
        return ("ok", "")
    except Exception as e:
        conn.rollback()
        return ("error", f"{type(e).__name__}: {e}")
    finally:
        conn.close()


def _fill_pending(*, dry_run: bool, limit: int | None) -> int:
    os.environ.pop("SKIP_MACRO_LLM_NAMING", None)

    sids = list_pending_storyline_ids(limit)
    if not sids:
        print("[macro_llm_fill_pending] 无待补全的宏观故事线（占位符匹配 0 条）", flush=True)
        return 0

    use_llm = os.getenv("MACRO_USE_LLM_NAMING", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )
    print(
        f"[macro_llm_fill_pending] 待处理 {len(sids)} 条，dry_run={dry_run} use_llm={use_llm}",
        flush=True,
    )

    n_ok = 0
    n_skip = 0
    for sid in sids:
        st, _detail = fill_one_storyline(sid, dry_run=dry_run)
        if st == "ok":
            n_ok += 1
        elif st == "dry_ok":
            n_ok += 1
        else:
            n_skip += 1
            if st == "skip":
                print(f"[macro_llm_fill_pending] skip {sid}: {_detail}", flush=True)
            else:
                print(f"[macro_llm_fill_pending] error {sid}: {_detail}", flush=True)

    print(
        f"[macro_llm_fill_pending] 完成：成功 {n_ok}，跳过 {n_skip}（无 map 等）",
        flush=True,
    )
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="补全宏观故事线 LLM 标题/综述（占位符行）")
    p.add_argument("--dry-run", action="store_true", help="只打印将写入的内容，不写库")
    p.add_argument("--limit", type=int, default=None, metavar="N", help="最多处理 N 条")
    args = p.parse_args()
    try:
        rc = _fill_pending(dry_run=bool(args.dry_run), limit=args.limit)
    except KeyboardInterrupt:
        print("\n[macro_llm_fill_pending] 已中断", flush=True)
        rc = 130
    sys.exit(rc)


if __name__ == "__main__":
    main()
