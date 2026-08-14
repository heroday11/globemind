#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Pipeline v2 编排。

- Stage 1 微观感知：两阶段 BGE→GLiNER+LLM；配合 PIPELINE_V2_DUAL_WRITE 写 PG + Milvus。
- Stage 2 L1 事件共核聚类：BGE-M3 embedding 增强的事件聚类。
- Stage 3 L2 故事线构建：实体规范化 + 跨语言桥接 + 滑动时间窗口 + LLM 命名。
- Stage 4 知识导出：Obsidian / 前端产物。

环境变量（由 runner 在 v2 路径下设置）：
- PIPELINE_V2_DUAL_WRITE=1
- PIPELINE_V2_MILVUS_SYNC_ALL=1 时，Milvus 同步包含非涉华（与 --milvus-sync-all 一致）
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any, List, Optional, Tuple

from pathlib import Path

from agentic_rag import analysis_service as svc
from agentic_rag.pipeline import knowledge_export as kexp
from agentic_rag.pipeline import micro_ingestion as micro
from agentic_rag.pipeline.pipeline_v2_dashboard import print_v2_final_summary, print_v2_footer
from agentic_rag.pipeline_logging import log_pipeline, log_stage_timing

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _topic_from_title(title: str) -> str:
    s = str(title or "").strip()
    if not s:
        return ""
    parts = [p for p in re.split(r"[，,。；;：:、|/\\\-—（）()\[\]【】\s]+", s) if p]
    parts = [p[:16] for p in parts if len(p) >= 2]
    if not parts:
        return (s[:18] + "相关") if len(s) > 18 else (s + "相关")
    uniq: List[str] = []
    for p in parts:
        if p not in uniq:
            uniq.append(p)
        if len(uniq) >= 3:
            break
    return " / ".join(uniq)


def _backfill_topic_main_labels(storyline_ids: Optional[List[int]] = None) -> None:
    """
    Stage3 后兜底：为 topic_main 为空/占位/解析失败的 macro/micro 回填标题主题标签。
    """
    from agentic_rag.db.macro_shared import _pg_write_executor

    ex = _pg_write_executor()
    conn = ex.get_write_conn()
    try:
        cur = conn.cursor()
        sid_where = ""
        sid_params: tuple = ()
        if storyline_ids:
            sid_where = " AND storyline_id = ANY(%s) "
            sid_params = (list({int(x) for x in storyline_ids}),)

        cur.execute(
            f"""
            SELECT storyline_id, title, topic_main
            FROM macro_storylines
            WHERE (status IS NULL OR status <> 'fragment')
              {sid_where}
            """,
            sid_params,
        )
        ms_updates: List[tuple[str, int]] = []
        for sid, title, topic in cur.fetchall():
            t = str(topic or "").strip()
            if t and t.upper() != "PARSE_FAILED" and "未标注" not in t and "解析失败" not in t:
                continue
            nt = _topic_from_title(str(title or ""))
            if nt:
                ms_updates.append((nt, int(sid)))
        if ms_updates:
            cur.executemany(
                "UPDATE macro_storylines SET topic_main = %s WHERE storyline_id = %s",
                ms_updates,
            )

        micro_sid_clause = ""
        micro_params: tuple = ()
        if storyline_ids:
            micro_sid_clause = " WHERE sm.storyline_id = ANY(%s) "
            micro_params = (list({int(x) for x in storyline_ids}),)
        cur.execute(
            f"""
            SELECT me.event_id, me.title, me.topic_main
            FROM micro_events me
            JOIN storyline_micro_map sm ON sm.event_id = me.event_id
            {micro_sid_clause}
            GROUP BY me.event_id, me.title, me.topic_main
            """,
            micro_params,
        )
        me_updates: List[tuple[str, int]] = []
        for eid, title, topic in cur.fetchall():
            t = str(topic or "").strip()
            if t and t.upper() != "PARSE_FAILED" and "未标注" not in t and "解析失败" not in t:
                continue
            nt = _topic_from_title(str(title or ""))
            if nt:
                me_updates.append((nt, int(eid)))
        if me_updates:
            cur.executemany(
                "UPDATE micro_events SET topic_main = %s WHERE event_id = %s",
                me_updates,
            )

        conn.commit()
        print(
            f"[pipeline_v2] topic_main 兜底回填：macro={len(ms_updates)} micro={len(me_updates)}",
            flush=True,
        )
    except Exception as e:
        conn.rollback()
        print(f"[pipeline_v2] topic_main 兜底回填跳过: {type(e).__name__}: {e}", flush=True)
    finally:
        conn.close()


def _log_token_usage(stage: str, *, note: str = "") -> None:
    """将当前 llm_usage 累计写入 pipeline.log 的结构化 TOKEN_USAGE 行。"""
    try:
        from agentic_rag.llm_usage import get_usage_snapshot

        snap = get_usage_snapshot()
        payload = {
            "event": "token_usage",
            "stage": stage,
            "model": os.getenv("CLOUD_API_MODEL", ""),
            "llm_backend": os.getenv("LLM_BACKEND", ""),
            "api_calls": int(snap.get("api_calls", 0) or 0),
            "prompt_tokens": int(snap.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(snap.get("completion_tokens", 0) or 0),
            "total_tokens": int(snap.get("total_tokens", 0) or 0),
        }
        pin = (os.getenv("LLM_PRICE_INPUT_PER_MTOK_CNY") or "").strip()
        pout = (os.getenv("LLM_PRICE_OUTPUT_PER_MTOK_CNY") or "").strip()
        if pin and pout:
            try:
                rin = float(pin)
                rout = float(pout)
                payload["estimated_cost_cny"] = round(
                    (payload["prompt_tokens"] / 1_000_000.0) * rin
                    + (payload["completion_tokens"] / 1_000_000.0) * rout,
                    6,
                )
            except ValueError:
                pass
        if note:
            payload["note"] = note
        log_pipeline("[TOKEN_USAGE] " + json.dumps(payload, ensure_ascii=False))
    except Exception:
        pass


def _resolve_obsidian_output(user_path: str) -> str:
    from config.settings import obsidian_vault_path

    raw = (user_path or "").strip()
    if not raw:
        return str(obsidian_vault_path().resolve())
    p = Path(raw).expanduser()
    if p.is_absolute():
        return str(p.resolve())
    return str((_REPO_ROOT / p).resolve())


def _require_analyzed(min_rows: int) -> int:
    n = svc.count_news_is_china_analyzed()
    if n < min_rows:
        raise SystemExit(
            f"[校验失败] is_china_related 非空仅 {n:,} 条，低于阈值 {min_rows:,}。"
            "请先完成微观分析（Stage 1）。"
        )
    print(f"[校验] is_china_related 已填充: {n:,} 条（要求 >= {min_rows:,}）")
    return n


def _set_v2_dual_write_env(*, milvus_sync_all: bool) -> None:
    os.environ["PIPELINE_V2_DUAL_WRITE"] = "1"
    if milvus_sync_all:
        os.environ["PIPELINE_V2_MILVUS_SYNC_ALL"] = "1"
    else:
        os.environ.pop("PIPELINE_V2_MILVUS_SYNC_ALL", None)


def _clear_v2_dual_write_env() -> None:
    os.environ.pop("PIPELINE_V2_DUAL_WRITE", None)
    os.environ.pop("PIPELINE_V2_MILVUS_SYNC_ALL", None)


def run_stage1_micro_perception(
    *,
    batch_size: int,
    llm_batch_size: Optional[int],
    workers: Optional[int],
    max_rows: Optional[int],
    micro_total_cap: Optional[int],
    milvus_sync_all: bool,
) -> None:
    """原 --stage 1 两阶段微观；v2 默认启用 PG+Milvus 双写（1a 批内向量仍驻留内存时写入 Milvus）。"""
    _set_v2_dual_write_env(milvus_sync_all=milvus_sync_all)
    try:
        micro.run_stage1_two_phase_sequential(
            batch_size=batch_size,
            llm_batch_size=llm_batch_size,
            workers=workers,
            max_rows=max_rows,
            max_batches=None,
            micro_total_cap=micro_total_cap,
        )
    finally:
        _clear_v2_dual_write_env()


def run_stage4_knowledge_export(
    *,
    output_dir: str,
    export_front_artifacts: bool,
    clear_vault: bool,
) -> None:
    """原阶段 6：Obsidian + 可选 _front_export。"""
    from agentic_rag.db.macro_shared import verify_macro_tables_ready

    nm, ne, nk = verify_macro_tables_ready()
    print(
        f"[阶段④/v2] 表校验: macro_storylines={nm}, micro_events={ne}, "
        f"storyline_micro_map={nk}"
    )
    kexp.run_stage6(
        output_dir=_resolve_obsidian_output(output_dir),
        export_front_artifacts=export_front_artifacts,
        clear_vault=clear_vault,
    )


def run_pipeline_all_v2(args: Any) -> None:
    """
    替代原 999：1 → 2 → 3 → 4；不执行「从 PG 全表再灌 Milvus」的 Stage 44。
    微观阶段由 PIPELINE_V2_DUAL_WRITE 在 1a 写回时直写 Milvus。
    """
    out_v = _resolve_obsidian_output(getattr(args, "output_dir", "") or "")
    # v2 默认以“本轮处理数据闭环”为主：涉华+非涉华都双写 Milvus，供后续 Stage2 聚类。
    # 显式 --milvus-china-only 时才退回仅涉华。
    milvus_sync_all = (not bool(getattr(args, "milvus_china_only", False))) or bool(
        getattr(args, "milvus_sync_all", False)
    )
    conc = int(
        getattr(args, "stage3_concurrency", None)
        or os.getenv("STAGE3_LLM_CONCURRENCY", "8")
    )

    summary_rows: List[Tuple[str, str, float]] = []
    footer_done: List[Tuple[str, float]] = []

    print(
        "[pipeline_v2] all: 1 micro (dual-write) → 2 graph (placeholders) → "
        f"3 LLM async (concurrency={conc}) → 4 export",
        flush=True,
    )
    log_pipeline(
        f"PIPELINE_V2_START batch_size={args.batch_size} max_rows={getattr(args, 'max_rows', None)} "
        f"milvus_sync_all={milvus_sync_all} stage3_concurrency={conc} "
        f"stage3_limit={getattr(args, 'stage3_limit', None)!r}"
    )

    print_v2_footer(current="①微观（1a BGE → drain Milvus → 1b GLiNER+LLM）", done=[])
    t1 = time.perf_counter()
    run_stage1_micro_perception(
        batch_size=args.batch_size,
        llm_batch_size=getattr(args, "llm_batch_size", None),
        workers=getattr(args, "workers", None),
        max_rows=getattr(args, "max_rows", None),
        micro_total_cap=getattr(args, "micro_total_cap", None),
        milvus_sync_all=milvus_sync_all,
    )
    dt1 = time.perf_counter() - t1
    summary_rows.append(("v2_1_micro", "①微观感知（含双写 Milvus）", dt1))
    footer_done.append(("①微观", dt1))
    log_stage_timing(
        "v2_1_micro",
        dt1,
        batch_size=args.batch_size,
        max_rows=getattr(args, "max_rows", None),
        milvus_sync_all=milvus_sync_all,
    )
    log_pipeline(f"PIPELINE_V2_STAGE v2_1_micro elapsed_s={dt1:.3f}")

    from agentic_rag.pipeline.runner import _require_analyzed

    _require_analyzed(args.min_analyzed_rows)

    # ── Stage 2: L1 event coreference (stage 7) ──
    print_v2_footer(current="② L1 事件共核聚类", done=footer_done)
    t2 = time.perf_counter()
    from agentic_rag.db.event_coref_schema import ensure_event_coref_tables
    from agentic_rag.pipeline.event_coref_loader import run_event_coref_clustering
    ensure_event_coref_tables()
    nc, nm = run_event_coref_clustering()
    dt2 = time.perf_counter() - t2
    summary_rows.append(("v2_2_l1_coref", "② L1 事件共核聚类", dt2))
    footer_done.append(("②L1", dt2))
    print(f"[pipeline_v2] L1 聚类完成: {nc} 簇, {nm} 成员 ({dt2:.1f}s)", flush=True)
    log_stage_timing("v2_2_l1_coref", dt2, clusters=nc, members=nm)

    # ── Stage 3: L2 storyline building (stage 8) ──
    print_v2_footer(current="③ L2 故事线构建与命名", done=footer_done)
    t3 = time.perf_counter()
    from agentic_rag.db.micro_story_schema import ensure_micro_story_tables
    from agentic_rag.pipeline.micro_story_builder import build_micro_stories
    ensure_micro_story_tables()
    ns, nc2 = build_micro_stories()
    dt3 = time.perf_counter() - t3
    summary_rows.append(("v2_3_l2_story", "③ L2 故事线构建", dt3))
    footer_done.append(("③L2", dt3))
    print(f"[pipeline_v2] L2 故事线完成: {ns} 条, {nc2} 簇 ({dt3:.1f}s)", flush=True)
    log_stage_timing("v2_3_l2_story", dt3, stories=ns, clusters=nc2)

    try:
        from agentic_rag.db.macro_shared import verify_macro_tables_ready

        verify_macro_tables_ready()
    except RuntimeError as e:
        if getattr(args, "smoke_allow_empty_macro", False):
            print(f"[pipeline_v2] 跳过阶段④: {e}", flush=True)
            return
        raise SystemExit(str(e)) from None

    print_v2_footer(current="④Obsidian + 前端产物", done=footer_done)
    t4 = time.perf_counter()
    run_stage4_knowledge_export(
        output_dir=out_v,
        export_front_artifacts=not args.no_front_export,
        clear_vault=bool(args.clear_vault),
    )
    dt4 = time.perf_counter() - t4
    summary_rows.append(("v2_4_export", "④知识导出（Obsidian）", dt4))
    footer_done.append(("④导出", dt4))
    log_stage_timing(
        "v2_4_export",
        dt4,
        output_dir=out_v,
        clear_vault=bool(args.clear_vault),
    )
    log_pipeline(f"PIPELINE_V2_STAGE v2_4_export elapsed_s={dt4:.3f}")

    print_v2_footer(current="（全部完成）", done=footer_done)
    print_v2_final_summary(summary_rows)

    try:
        from agentic_rag.naming_service import print_openai_usage_and_cost_estimate

        print_openai_usage_and_cost_estimate("pipeline_v2 all（微观+宏观LLM）")
    except Exception:
        pass
    _log_token_usage("v2_all_complete", note="final_snapshot")
