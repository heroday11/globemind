#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
舆情流水线 CLI 编排入口（原 run_pipeline_stages 主体）。

用法保持不变：
  python -m agentic_rag.run_pipeline_stages ...
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from agentic_rag import analysis_service as svc
from agentic_rag.pipeline import knowledge_export as kexp
from agentic_rag.pipeline import fast_entity_tagger as fast_ent
from agentic_rag.pipeline import micro_budget
from agentic_rag.pipeline import micro_ingestion as micro
from agentic_rag.pipeline import vector_sync as vsync
from agentic_rag.pipeline_logging import log_pipeline, log_stage_timing
from config.settings import FrozenDefaults, obsidian_vault_path


def _require_analyzed(min_rows: int) -> int:
    n = svc.count_news_is_china_analyzed()
    if n < min_rows:
        raise SystemExit(
            f"[校验失败] is_china_related 非空仅 {n:,} 条，低于阈值 {min_rows:,}。"
            "请先完成微观分析（Stage 2–4 或 run_analysis / --stage 999 前半段）。"
        )
    print(f"[校验] is_china_related 已填充: {n:,} 条（要求 >= {min_rows:,}）")
    return n


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _default_obsidian_root() -> str:
    return str(obsidian_vault_path().resolve())


def _resolve_obsidian_output(user_path: str) -> str:
    raw = (user_path or "").strip()
    if not raw:
        return _default_obsidian_root()
    p = Path(raw).expanduser()
    if p.is_absolute():
        return str(p.resolve())
    return str((_repo_root() / p).resolve())


def _reset_milvus_collections() -> None:
    """清空 news_vectors / cluster_centroids 并重建空集合；断开单例后重连。"""
    from agentic_rag.db.milvus_store import get_milvus_store, reset_milvus_store_singleton

    print(
        "[runner] Milvus reset: dropping news_vectors + cluster_centroids, then recreate empty…",
        flush=True,
    )
    log_pipeline("MILVUS_RESET_START")
    store = get_milvus_store()
    store.drop_all()
    store.close()
    reset_milvus_store_singleton()
    _ = get_milvus_store()
    print("[runner] Milvus reset done (empty collections).", flush=True)
    log_pipeline("MILVUS_RESET_DONE")


def main() -> None:
    svc.ensure_dotenv_loaded()

    p = argparse.ArgumentParser(
        description="Pipeline stages 1–6 与 999 全量一键",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--stage",
        type=str,
        default="999",
        help=(
            "v2 主流程: 1=微观感知(双写Milvus); 2=图演化/宏观; 3=LLM补全宏观命名; 4=Obsidian导出; "
            "7=事件共核簇加载; 8=Micro-Story中层聚类; 9a/9b=Macro-Event顶层聚类（Embedding→HDBSCAN）; "
            "llm-relevance=LLM涉华相关性评分回填(prototype+LLM权重融合); "
            "frame-llm=LLM框架分类回填(覆盖规则映射结果); "
            "all|999=1→2→3→4（不再跑 Stage44 从PG补灌，向量在阶段1直写 Milvus）。"
            "兼容: 0探针; 1a/1b/1c; pkl2|pkl3|pkl4 旧离线pkl链; "
            "44=Milvus灾难恢复同步; 445/4456; 5/6; "
            "--legacy-pipeline-999 恢复旧 999（含 Stage44）"
        ),
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="等价于 --stage all（与 999 默认新行为一致）",
    )
    p.add_argument(
        "--stage3-concurrency",
        type=int,
        default=8,
        metavar="N",
        help="阶段3（宏观 LLM 异步补全）并发上限",
    )
    p.add_argument(
        "--stage3-limit",
        type=int,
        default=None,
        metavar="N",
        help="阶段3：最多补全 N 条待处理宏观故事线；999/all 下默认仅补本轮 Stage2 新增 storylines",
    )
    p.add_argument(
        "--reset-milvus",
        action="store_true",
        help="进程启动时清空 Milvus 的 news_vectors 与 cluster_centroids 并重建空集合（测试前用）",
    )
    p.add_argument(
        "--reset-milvus-exit",
        action="store_true",
        help="仅执行 Milvus 清空后退出，不跑 --stage（需同时指定 --reset-milvus）",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=48,
        help="每批从 DB 拉取条数（1/1a/1b/999）；略小于 64 可减轻单次显存/内存峰值，仍可按机器加大",
    )
    p.add_argument("--pickle", type=str, default="", help="阶段③/④ 输入 pkl")
    p.add_argument("--out", type=str, default="", help="阶段②/③ 输出 pkl")

    p.add_argument(
        "--max-rows",
        type=int,
        default=None,
        help=(
            "999 / 1 / 1a / 1b / 1c: 单次运行内最多处理条数（待处理为 0 则不生效）；"
            "与 --micro-total-cap 同时存在时取较小者。不影响阶段⑤⑥。试跑缩小宏观请用 --max-clusters"
        ),
    )
    p.add_argument(
        "--micro-total-cap",
        type=int,
        default=None,
        help=(
            "微观 1a（或 legacy run_analysis）跨多次运行的累计条数上限，写入 "
            "agentic_rag/data/micro_total_budget.json；便于断点调试时分多轮跑满固定总量。"
            "与 --max-rows 同时存在时本段取 min(单次上限, 剩余总配额)。"
        ),
    )
    p.add_argument(
        "--micro-total-cap-reset",
        action="store_true",
        help="将 micro_total_budget.json 中的累计计数清零（与 --micro-total-cap 配合使用）",
    )
    p.add_argument("--llm-batch-size", type=int, default=None, help="999: 传入 run_analysis（默认随 LLM_BACKEND）")
    p.add_argument("--workers", type=int, default=None, help="999: 传入 run_analysis（默认随 LLM_BACKEND）")

    p.add_argument(
        "--min-analyzed-rows",
        type=int,
        default=100,
        help="阶段5/6/999：要求 is_china_related 非 NULL 的最少条数（试跑少量时可设 1）",
    )
    p.add_argument(
        "--min-sim",
        type=float,
        default=FrozenDefaults.STAGE5_MIN_SIM,
        help="阶段5：Milvus/质心相似度阈值",
    )
    p.add_argument(
        "--min-entity-overlap",
        type=float,
        default=FrozenDefaults.STAGE5_MIN_ENTITY_OVERLAP,
        help="阶段5：>90 天时要求的实体池最小重叠率",
    )
    p.add_argument("--macro-batch", type=int, default=2000, help="阶段5：Milvus 扫描 batch")
    p.add_argument("--topk-news", type=int, default=128, help="阶段5：每簇 Milvus Top-K")
    p.add_argument(
        "--max-clusters",
        type=int,
        default=None,
        help="阶段5/999：仅取前 N 个微簇建图（调试用，显著缩短时间）；与 --max-rows 无关",
    )

    p.add_argument(
        "--output-dir",
        type=str,
        default="",
        help="阶段6/999：Obsidian 库根目录（将创建 MacroEvents, MicroEvents, Articles）",
    )
    p.add_argument(
        "--no-front-export",
        action="store_true",
        help="阶段6/999：不在库下 _front_export 复制 macro_events.json / graph_data.json",
    )
    p.add_argument(
        "--clear-vault",
        action="store_true",
        help="阶段6/999：同步前删除当前 Micro 目录及另一布局下的遗留目录，避免旧占位笔记残留",
    )
    p.add_argument(
        "--clear-macro-db",
        action="store_true",
        help="阶段5/999：回写前清空 storyline_micro_map、macro_storylines，并置空 micro_events.macro_storyline_id",
    )
    p.add_argument(
        "--no-persist-macro-db",
        action="store_true",
        help="阶段5/999：只生成 macro_events.json，不写 PostgreSQL",
    )
    p.add_argument(
        "--milvus-sync-limit",
        type=int,
        default=5000,
        help="阶段44：从 DB 拉取新闻条数上限（DESC id）；是否仅涉华见 --milvus-sync-all",
    )
    p.add_argument(
        "--milvus-sync-all",
        action="store_true",
        help=(
            "阶段44：同步「标题非空」的全部新闻至 Milvus（知识图谱全量）；"
            "v2 的 1/999/all 默认已是本轮涉华+非涉华双写，无需额外指定"
        ),
    )
    p.add_argument(
        "--milvus-china-only",
        action="store_true",
        help=(
            "v2 的 1/999/all 微观双写到 Milvus 时仅写 is_china_related=TRUE；"
            "默认关闭（即本轮处理到的涉华+非涉华都写入并参与后续聚类）"
        ),
    )
    p.add_argument(
        "--legacy-micro-999",
        action="store_true",
        help="阶段999：使用旧版单进程 run_analysis（BGE+GLiNER 同驻显存，易 OOM）；默认已改为两阶段 1a→1b 省显存",
    )
    p.add_argument(
        "--legacy-pipeline-999",
        action="store_true",
        help="999/all：使用旧编排（微观后仍跑 Stage44 从 PG 补灌 Milvus）；默认新编排为双写+跳过44",
    )
    p.add_argument(
        "--smoke-allow-empty-macro",
        action="store_true",
        help="阶段999：阶段5后若宏观表仍为空（试跑条数过少、或无过闸新闻导致 Milvus 无向量），跳过阶段6并以 0 退出；仅冒烟用",
    )

    p.add_argument(
        "--use-embeddings",
        action="store_true",
        help=(
            "阶段⑦：使用 embedding-enhanced 聚类（BGE-M3 语义近邻过滤 + trigger 严格匹配），"
            "代替默认的 checkpoint 文件加载。首次运行会在线计算并保存 checkpoint JSONL。"
        ),
    )

    args = p.parse_args()
    if bool(getattr(args, "all", False)):
        args.stage = "all"

    if getattr(args, "micro_total_cap_reset", False):
        micro_budget.reset_budget()
        print(
            "[runner] 已重置微观累计计数（micro_total_budget.json → consumed_micro=0）",
            flush=True,
        )

    if getattr(args, "reset_milvus_exit", False) and not getattr(args, "reset_milvus", False):
        raise SystemExit("[runner] --reset-milvus-exit 需要同时加 --reset-milvus")

    if getattr(args, "reset_milvus", False):
        _reset_milvus_collections()
    if getattr(args, "reset_milvus_exit", False):
        log_pipeline("RUNNER_EXIT after MILVUS_RESET")
        return

    st = str(args.stage).strip().lower()

    log_pipeline(
        "RUNNER_START "
        f"stage={st!r} max_rows={args.max_rows!r} micro_total_cap={getattr(args, 'micro_total_cap', None)!r} "
        f"batch_size={args.batch_size} "
        f"llm_backend={os.getenv('LLM_BACKEND', '')!r} "
        f"milvus_sync_limit={args.milvus_sync_limit} milvus_sync_all={args.milvus_sync_all} "
        f"milvus_china_only={args.milvus_china_only} "
        f"min_analyzed_rows={args.min_analyzed_rows}"
    )

    if st == "0":
        t0 = time.perf_counter()
        micro.run_stage0_probe(batch_size=args.batch_size)
        log_stage_timing("0", time.perf_counter() - t0, batch_size=args.batch_size)
        return

    if st == "1":
        from agentic_rag.pipeline import pipeline_v2

        v2_milvus_sync_all = (not bool(args.milvus_china_only)) or bool(args.milvus_sync_all)
        pipeline_v2.run_stage1_micro_perception(
            batch_size=args.batch_size,
            llm_batch_size=args.llm_batch_size,
            workers=args.workers,
            max_rows=args.max_rows,
            micro_total_cap=args.micro_total_cap,
            milvus_sync_all=v2_milvus_sync_all,
        )
        return

    if st == "1a":
        micro.run_stage1a_bge_sieve(
            batch_size=args.batch_size,
            max_rows=args.max_rows,
            max_batches=None,
            micro_total_cap=args.micro_total_cap,
        )
        return

    if st == "1b":
        micro.run_stage1b_gliner_llm(
            batch_size=args.batch_size,
            llm_batch_size=args.llm_batch_size,
            workers=args.workers,
            max_rows=args.max_rows,
            max_batches=None,
        )
        return

    if st == "1c":
        t0 = time.perf_counter()
        fast_ent.run_stage1c(
            batch_size=max(1, int(args.batch_size)),
            max_rows=args.max_rows,
            max_batches=None,
        )
        log_stage_timing(
            "1c",
            time.perf_counter() - t0,
            batch_size=args.batch_size,
            max_rows=args.max_rows,
        )
        return

    if st == "2":
        _require_analyzed(args.min_analyzed_rows)
        from agentic_rag.pipeline import pipeline_v2

        pipeline_v2.run_stage2_graph_evolution(
            batch=args.macro_batch,
            topk_news=args.topk_news,
            min_sim=args.min_sim,
            min_entity_overlap=args.min_entity_overlap,
            max_clusters=args.max_clusters,
            persist_to_db=not args.no_persist_macro_db,
            clear_macro_db=bool(args.clear_macro_db),
        )
        return

    if st == "pkl2":
        t0 = time.perf_counter()
        micro.run_stage2(batch_size=args.batch_size, out_path=args.out)
        log_stage_timing(
            "pkl2",
            time.perf_counter() - t0,
            batch_size=args.batch_size,
            out=args.out or "",
        )
        return

    if st == "3":
        from agentic_rag.pipeline import pipeline_v2

        pipeline_v2.run_stage3_agentic_generation(
            dry_run=False,
            limit=getattr(args, "stage3_limit", None),
            concurrency=int(args.stage3_concurrency),
        )
        return

    if st == "pkl3":
        t0 = time.perf_counter()
        micro.run_stage3(
            pickle_path=args.pickle,
            out_path=args.out,
            llm_batch_size=args.llm_batch_size,
            workers=args.workers,
        )
        log_stage_timing(
            "pkl3",
            time.perf_counter() - t0,
            pickle=args.pickle or "",
            out=args.out or "",
        )
        return

    if st == "4":
        _require_analyzed(args.min_analyzed_rows)
        from agentic_rag.pipeline import pipeline_v2

        pipeline_v2.run_stage4_knowledge_export(
            output_dir=args.output_dir or "",
            export_front_artifacts=not args.no_front_export,
            clear_vault=bool(args.clear_vault),
        )
        return

    if st == "pkl4":
        t0 = time.perf_counter()
        micro.run_stage4(pickle_path=args.pickle)
        log_stage_timing("pkl4", time.perf_counter() - t0, pickle=args.pickle or "")
        return

    if st == "44":
        t0 = time.perf_counter()
        vsync.run_stage44(limit=args.milvus_sync_limit, milvus_sync_all=args.milvus_sync_all)
        log_stage_timing(
            "44",
            time.perf_counter() - t0,
            limit=args.milvus_sync_limit,
            milvus_sync_all=args.milvus_sync_all,
        )
        return

    if st == "445":
        _run_stage445_only_body(args)
        return

    if st == "4456":
        _run_stage4456_body(args)
        return

    if st == "5":
        _require_analyzed(args.min_analyzed_rows)
        t0 = time.perf_counter()
        macro.run_stage5(
            batch=args.macro_batch,
            topk_news=args.topk_news,
            min_sim=args.min_sim,
            min_entity_overlap=args.min_entity_overlap,
            max_clusters=args.max_clusters,
            persist_to_db=not args.no_persist_macro_db,
            clear_old_data=True if args.clear_macro_db else None,
        )
        log_stage_timing(
            "5",
            time.perf_counter() - t0,
            min_sim=args.min_sim,
            min_entity_overlap=args.min_entity_overlap,
            macro_batch=args.macro_batch,
            topk_news=args.topk_news,
            max_clusters=args.max_clusters,
            clear_macro_db=args.clear_macro_db,
        )
        return

    if st == "7":
        t0 = time.perf_counter()
        from agentic_rag.db.event_coref_schema import ensure_event_coref_tables

        ensure_event_coref_tables()

        if getattr(args, "use_embeddings", False):
            from agentic_rag.pipeline.event_coref_loader import run_event_coref_clustering

            nc, nm = run_event_coref_clustering()
            print(
                f"[阶段⑦] Embedding-enhanced 共核聚类完成: {nc} clusters, {nm} members "
                f"({time.perf_counter()-t0:.1f}s)",
                flush=True,
            )
            log_stage_timing("7", time.perf_counter() - t0, clusters=nc, members=nm, mode="embedding")
        else:
            from agentic_rag.pipeline.event_coref_loader import load_event_coref_from_checkpoint

            nc, nm = load_event_coref_from_checkpoint()
            print(
                f"[阶段⑦] 事件共核簇加载完成: {nc} clusters, {nm} members "
                f"({time.perf_counter()-t0:.1f}s)",
                flush=True,
            )
            log_stage_timing("7", time.perf_counter() - t0, clusters=nc, members=nm)
        return

    if st == "8":
        t0 = time.perf_counter()
        from agentic_rag.db.micro_story_schema import ensure_micro_story_tables
        from agentic_rag.pipeline.micro_story_builder import build_micro_stories

        ensure_micro_story_tables()
        ns, nc = build_micro_stories()
        print(
            f"[阶段⑧] L2 故事线构建完成: {ns} 个故事, 涵盖 {nc} 个簇 "
            f"({time.perf_counter()-t0:.1f}s)",
            flush=True,
        )
        return

    if st == "llm-relevance":
        t0 = time.perf_counter()
        import importlib.util

        _scripts_dir = str(Path(__file__).resolve().parent.parent.parent / "scripts")
        spec = importlib.util.spec_from_file_location(
            "backfill_llm_china_relevance",
            str(Path(_scripts_dir) / "backfill_llm_china_relevance.py"),
        )
        _mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_mod)

        total = 0
        for dbname in ("globemind", "globemind_news"):
            try:
                total += _mod.backfill(
                    dbname=dbname,
                    dry_run=False,
                    use_llm=True,
                    force=False,
                    threshold=0.4,
                    batch_size=100,
                    max_rows=None,
                )
            except Exception as e:
                print(f"[{dbname}] 跳过: {e}", flush=True)
        print(f"[阶段 llm-relevance] 完成: 更新 {total} 行 ({time.perf_counter()-t0:.1f}s)", flush=True)
        return

    if st == "frame-llm":
        t0 = time.perf_counter()
        import importlib.util

        _scripts_dir = str(Path(__file__).resolve().parent.parent.parent / "scripts")
        spec = importlib.util.spec_from_file_location(
            "backfill_frames",
            str(Path(_scripts_dir) / "backfill_frames.py"),
        )
        _mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_mod)

        from agentic_rag.db.connection import get_db_url

        total = 0
        for dbname, lang in (("globemind", "zh"), ("globemind_news", "en")):
            try:
                url = get_db_url(dbname)
                total += _mod.backfill(url, language=lang, dry_run=False, use_llm=True)
            except Exception as e:
                print(f"[{dbname}] 跳过: {e}", flush=True)
        print(f"[阶段 frame-llm] 完成: 更新 {total} 行 ({time.perf_counter()-t0:.1f}s)", flush=True)
        return

    if st == "6":
        _require_analyzed(args.min_analyzed_rows)
        from agentic_rag.db.macro_shared import verify_macro_tables_ready

        try:
            nm, ne, nk = verify_macro_tables_ready()
        except RuntimeError as e:
            raise SystemExit(str(e)) from None
        print(
            f"[阶段⑥] 表校验通过: macro_storylines={nm}, micro_events={ne}, storyline_micro_map={nk}"
        )
        out_vault = _resolve_obsidian_output(args.output_dir)
        print(f"[阶段⑥] 知识库与前端导出 → {out_vault}")
        log_pipeline(f"stage6 run_sync_v4 → {out_vault!r} clear_vault={args.clear_vault}")
        t0 = time.perf_counter()
        try:
            kexp.run_stage6(
                output_dir=out_vault,
                export_front_artifacts=not args.no_front_export,
                clear_vault=args.clear_vault,
            )
        except Exception as e:
            log_pipeline(f"stage6 ERROR {type(e).__name__}: {e}")
            raise
        log_stage_timing(
            "6",
            time.perf_counter() - t0,
            vault=str(out_vault),
            clear_vault=args.clear_vault,
            no_front_export=args.no_front_export,
        )
        return

    if st not in ("999", "all"):
        raise SystemExit(
            f"[runner] 未知 --stage={args.stage!r}。支持: "
            "0,1,1a,1b,1c,2,3,4,44,445,4456,5,6,7,8,9,9a,9b,"
            "llm-relevance,frame-llm,999,all,pkl2,pkl3,pkl4"
        )

    from agentic_rag.llm_usage import reset_usage_counters
    from agentic_rag.pipeline_file_logging import (
        close_detail_session_log,
        init_detail_session_log,
        log_detail,
        write_token_snapshot,
    )

    reset_usage_counters()
    init_detail_session_log("stage999" if st == "999" else "stage_all")
    try:
        if bool(getattr(args, "legacy_pipeline_999", False)):
            _run_stage999_body(args)
        else:
            from agentic_rag.pipeline import pipeline_v2

            os.environ["STAGE3_LLM_CONCURRENCY"] = str(
                int(getattr(args, "stage3_concurrency", 8) or 8)
            )
            pipeline_v2.run_pipeline_all_v2(args)
            try:
                write_token_snapshot(
                    "pipeline_complete_v2",
                    extra={
                        "stage": st,
                        "max_rows": getattr(args, "max_rows", None),
                        "stage3_concurrency": int(getattr(args, "stage3_concurrency", 8) or 8),
                    },
                )
            except Exception:
                pass
    except KeyboardInterrupt:
        if not bool(getattr(args, "legacy_pipeline_999", False)):
            try:
                write_token_snapshot(
                    "interrupt_v2",
                    extra={"stage": st, "max_rows": getattr(args, "max_rows", None)},
                )
            except Exception:
                pass
        raise
    except Exception as e:
        if not bool(getattr(args, "legacy_pipeline_999", False)):
            try:
                write_token_snapshot(
                    "error_v2",
                    extra={
                        "stage": st,
                        "max_rows": getattr(args, "max_rows", None),
                        "error": str(e),
                    },
                )
            except Exception:
                pass
        raise
    finally:
        close_detail_session_log()
    return


def _run_stage445_core(args) -> tuple[int, int, int] | None:
    """
    阶段 44→5 + 表校验。返回 (macro_storylines_n, micro_events_n, map_n)；
    --smoke-allow-empty-macro 且表未就绪时返回 None。
    """
    import time

    from agentic_rag.db.macro_shared import verify_macro_tables_ready
    from agentic_rag.pipeline_file_logging import log_detail

    print(
        f"[阶段445] Milvus 补灌: limit={args.milvus_sync_limit}, "
        f"milvus_sync_all={args.milvus_sync_all}",
        flush=True,
    )
    log_pipeline("stage445 stage44 START")
    t44 = time.perf_counter()
    vsync.run_stage44(limit=args.milvus_sync_limit, milvus_sync_all=args.milvus_sync_all)
    log_stage_timing(
        "445_part44",
        time.perf_counter() - t44,
        limit=args.milvus_sync_limit,
        milvus_sync_all=args.milvus_sync_all,
    )

    _require_analyzed(args.min_analyzed_rows)

    print(
        f"[阶段445→5] 宏观: min_sim={args.min_sim}, min_entity_overlap={args.min_entity_overlap}",
        flush=True,
    )
    log_pipeline("stage445 stage5 START")
    log_detail("stage445 stage5 START")
    t5 = time.perf_counter()
    try:
        macro.run_stage5(
            batch=args.macro_batch,
            topk_news=args.topk_news,
            min_sim=args.min_sim,
            min_entity_overlap=args.min_entity_overlap,
            max_clusters=args.max_clusters,
            persist_to_db=not args.no_persist_macro_db,
            clear_old_data=True if args.clear_macro_db else None,
            silent=True,
        )
        log_stage_timing(
            "445_stage5",
            time.perf_counter() - t5,
            min_sim=args.min_sim,
            min_entity_overlap=args.min_entity_overlap,
            macro_batch=args.macro_batch,
            topk_news=args.topk_news,
            max_clusters=args.max_clusters,
            clear_macro_db=args.clear_macro_db,
        )
    except Exception as e:
        log_pipeline(f"stage445 stage5 ERROR {type(e).__name__}: {e}")
        log_detail(f"EXCEPTION stage445 stage5 {type(e).__name__}: {e}")
        raise

    try:
        nm, ne, nk = verify_macro_tables_ready()
    except RuntimeError as e:
        if getattr(args, "smoke_allow_empty_macro", False):
            print(
                f"[阶段445] 宏观表未就绪（--smoke-allow-empty-macro）: {e}",
                flush=True,
            )
            log_pipeline(f"stage445 SKIP (smoke_allow_empty_macro): {e}")
            return None
        raise SystemExit(str(e)) from None
    print(
        f"[阶段445] 表校验: macro_storylines={nm}, micro_events={ne}, storyline_micro_map={nk}",
        flush=True,
    )
    return (nm, ne, nk)


def _run_stage445_only_body(args) -> None:
    """仅 44→5，不导出 Obsidian（供历史模拟按块提速）。"""
    import time

    t_all = time.perf_counter()
    counts = _run_stage445_core(args)
    if counts is None:
        return
    total_s = time.perf_counter() - t_all
    print(f"[阶段445] 完成，总用时 {total_s:.1f}s（仅 44+5，未导出 Obsidian）", flush=True)
    log_pipeline(f"stage445 COMPLETE total_elapsed_s={total_s:.1f}")
    if os.getenv("PIPELINE_TIMING_JSON", "1").strip().lower() not in ("0", "false", "no"):
        print(
            "[PIPELINE_JSON] "
            + json.dumps(
                {"event": "stage445_complete", "elapsed_s": round(total_s, 3)},
                ensure_ascii=False,
            ),
            flush=True,
        )


def _run_stage4456_body(args) -> None:
    """阶段 44→5→6：无微观；全量一键或显式需要每块导出时使用。"""
    import time

    from agentic_rag.pipeline_file_logging import log_detail

    t_all = time.perf_counter()
    counts = _run_stage445_core(args)
    if counts is None:
        return
    nm, ne, nk = counts
    out_vault = _resolve_obsidian_output(args.output_dir)
    print(
        f"[阶段4456→6] 表就绪: macro_storylines={nm}, micro_events={ne}, storyline_micro_map={nk}",
        flush=True,
    )
    print(f"[阶段4456→6] Obsidian / 导出 → {out_vault}", flush=True)
    log_pipeline(f"stage4456 stage6 run_sync_v4 → {out_vault!r}")
    log_detail(f"stage4456 stage6 → {out_vault}")
    t6 = time.perf_counter()
    try:
        kexp.run_stage6(
            output_dir=out_vault,
            export_front_artifacts=not args.no_front_export,
            clear_vault=args.clear_vault,
            silent=True,
        )
    except Exception as e:
        log_pipeline(f"stage4456 stage6 ERROR {type(e).__name__}: {e}")
        raise
    log_stage_timing(
        "4456_stage6",
        time.perf_counter() - t6,
        vault=str(out_vault),
        clear_vault=args.clear_vault,
    )
    total_s = time.perf_counter() - t_all
    print(f"[阶段4456] 完成，总用时 {total_s:.1f}s（44+5+6 合计）", flush=True)
    log_pipeline(f"stage4456 COMPLETE total_elapsed_s={total_s:.1f}")
    if os.getenv("PIPELINE_TIMING_JSON", "1").strip().lower() not in ("0", "false", "no"):
        print(
            "[PIPELINE_JSON] "
            + json.dumps(
                {"event": "stage4456_complete", "elapsed_s": round(total_s, 3)},
                ensure_ascii=False,
            ),
            flush=True,
        )


def _run_stage999_body(args) -> None:
    """stage 999 主流程（外层 try/finally 负责关闭详细日志文件句柄）。"""
    import time

    from agentic_rag.pipeline_file_logging import log_detail, write_token_snapshot

    pending0 = svc.stage_count_unprocessed()
    analyzed0 = svc.count_news_is_china_analyzed()
    print(f"[阶段999] 预检（类阶段①）待处理={pending0:,} | is_china_related 已填={analyzed0:,}")
    if pending0 == 0:
        print(
            "[阶段999] 说明: 待处理=0 时，--max-rows 不会处理任何新闻（微观阶段跳过）；"
            "阶段⑤仍默认按 Milvus 全量微簇建图，除非设置 --max-clusters。"
        )
    legacy = getattr(args, "legacy_micro_999", False)
    mcap = getattr(args, "micro_total_cap", None)
    print(
        f"[阶段999] 微观: {'legacy run_analysis（同驻显存）' if legacy else '两阶段 1a(BGE)→1b(GLiNER+LLM) 省显存'}, "
        f"batch_size={args.batch_size}, max_rows={args.max_rows}, micro_total_cap={mcap}, "
        f"llm_batch_size={args.llm_batch_size}, workers={args.workers}"
    )
    if mcap is not None:
        print(
            f"[阶段999] 微观累计预算: 已用 {micro_budget.load_consumed():,} / {mcap} "
            f"（{micro_budget.budget_state_path()}）",
            flush=True,
        )
    log_detail(
        f"预检 pending={pending0} analyzed={analyzed0} max_rows={args.max_rows} micro_total_cap={mcap} "
        f"batch_size={args.batch_size} min_analyzed_rows={args.min_analyzed_rows} "
        f"legacy_micro_999={legacy}"
    )
    t_all = time.perf_counter()
    log_pipeline(
        f"stage999 micro begin legacy={legacy} max_rows={args.max_rows} micro_total_cap={mcap} "
        f"batch_size={args.batch_size} output_dir={_resolve_obsidian_output(args.output_dir)!r}"
    )
    try:
        if legacy:
            svc.run_analysis(
                batch_size=args.batch_size,
                llm_batch_size=args.llm_batch_size,
                workers=args.workers,
                max_batches=None,
                max_rows=args.max_rows,
                max_seconds=None,
                per_chunk_timeout_seconds=None,
                micro_total_cap=args.micro_total_cap,
            )
        else:
            micro.run_stage1_two_phase_sequential(
                batch_size=args.batch_size,
                llm_batch_size=args.llm_batch_size,
                workers=args.workers,
                max_rows=args.max_rows,
                max_batches=None,
                micro_total_cap=args.micro_total_cap,
            )
            # 1b 写回为省显存未逐批同步 Milvus；此处用阶段44 补灌（与 --milvus-sync-limit 对齐）
            lim44 = int(args.milvus_sync_limit)
            if args.max_rows is not None:
                lim44 = max(lim44, int(args.max_rows) * 2)
            lim44 = min(max(lim44, 1), 500_000)
            print(
                f"[阶段999→4.5] 两阶段模式：补灌 Milvus（limit={lim44}，与 --milvus-sync-all 一致）…"
            )
            vsync.run_stage44(limit=lim44, milvus_sync_all=args.milvus_sync_all)
        write_token_snapshot(
            "after_micro_analysis",
            extra={
                "max_rows": args.max_rows,
                "elapsed_s_stage": time.perf_counter() - t_all,
                "legacy_micro_999": legacy,
            },
        )
    except KeyboardInterrupt:
        log_pipeline(
            "stage999 微观 KeyboardInterrupt — 可重新执行本命令从未完成行继续（已提交批不会回滚）；"
            "--max-rows 为单次上限；若使用 --micro-total-cap，已计入条数保存在 micro_total_budget.json"
        )
        log_detail("KeyboardInterrupt during stage999 micro")
        try:
            write_token_snapshot("interrupt_after_micro", extra={})
        except Exception:
            pass
        raise
    except Exception as e:
        log_pipeline(f"stage999 微观 EXCEPTION {type(e).__name__}: {e}")
        log_detail(f"EXCEPTION stage999 micro {type(e).__name__}: {e}")
        try:
            write_token_snapshot("error_after_micro", extra={"error": str(e)})
        except Exception:
            pass
        raise
    t_after_micro = time.perf_counter()
    print(f"[阶段999] 微观分析阶段结束，已用 {t_after_micro - t_all:.1f}s")
    log_pipeline(f"stage999 micro finished in {t_after_micro - t_all:.1f}s")
    log_stage_timing(
        "999_part_micro_44",
        t_after_micro - t_all,
        max_rows=args.max_rows,
        batch_size=args.batch_size,
        legacy_micro_999=legacy,
        milvus_sync_limit=args.milvus_sync_limit,
    )
    if legacy:
        print(
            "[阶段999→4.5] 旧版 run_analysis：默认不在此阶段写 Milvus（微观只写 PG）；"
            "下方将执行补灌。若要在每批写回时同步 Milvus，设 MILVUS_SYNC_DURING_MICRO=1；"
            "完全关闭向量侧请设 MILVUS_SYNC=0。"
        )
    else:
        print(
            "[阶段999→4.5] 两阶段模式：默认微观不写 Milvus（省 I/O）；下方将执行补灌 stage 44。"
            "若需恢复 1a 逐批同步，设 MILVUS_SYNC_DURING_MICRO=1；"
            "可与 MILVUS_SYNC_ASYNC=1 叠加以重叠 BGE。"
        )

    _require_analyzed(args.min_analyzed_rows)

    print(
        f"[阶段999→5] 宏观事件: min_sim={args.min_sim}, "
        f"min_entity_overlap={args.min_entity_overlap}"
    )
    log_pipeline("stage999 stage5 run_build_macro_events START")
    log_detail("stage5 macro run_build_macro_events START")
    t5 = time.perf_counter()
    try:
        macro.run_stage5(
            batch=args.macro_batch,
            topk_news=args.topk_news,
            min_sim=args.min_sim,
            min_entity_overlap=args.min_entity_overlap,
            max_clusters=args.max_clusters,
            persist_to_db=not args.no_persist_macro_db,
            clear_old_data=True if args.clear_macro_db else None,
            silent=True,
        )
        log_stage_timing(
            "999_stage5",
            time.perf_counter() - t5,
            min_sim=args.min_sim,
            min_entity_overlap=args.min_entity_overlap,
            macro_batch=args.macro_batch,
            topk_news=args.topk_news,
            max_clusters=args.max_clusters,
        )
        write_token_snapshot(
            "after_stage5_macro",
            extra={"elapsed_s_total": time.perf_counter() - t_all},
        )
    except Exception as e:
        log_pipeline(f"stage999 stage5 ERROR {type(e).__name__}: {e}")
        log_detail(f"EXCEPTION stage5 {type(e).__name__}: {e}")
        try:
            write_token_snapshot("error_after_stage5", extra={"error": str(e)})
        except Exception:
            pass
        raise
    log_pipeline("stage999 stage5 OK")
    log_detail("stage5 OK")

    from agentic_rag.db.macro_shared import verify_macro_tables_ready

    try:
        nm, ne, nk = verify_macro_tables_ready()
    except RuntimeError as e:
        if getattr(args, "smoke_allow_empty_macro", False):
            print(
                f"[阶段999] 宏观表未就绪，跳过阶段6（--smoke-allow-empty-macro）: {e}",
                flush=True,
            )
            log_pipeline(f"stage999 SKIP stage6 (smoke_allow_empty_macro): {e}")
            log_detail(f"SKIP stage6 {e!r}")
            print(
                "[阶段999] 提示: 试跑条数过少或本批均无 china_related_index≥闸门 时，"
                "Milvus 可能无涉华向量，宏观为空属正常；生产请用足够 max_rows。",
                flush=True,
            )
            return
        raise SystemExit(str(e)) from None
    print(f"[阶段999→6] 表校验: macro_storylines={nm}, micro_events={ne}, storyline_micro_map={nk}")
    out_vault = _resolve_obsidian_output(args.output_dir)
    print(f"[阶段999→6] Obsidian / 导出 → {out_vault}")
    log_pipeline(f"stage999 stage6 run_sync_v4 → {out_vault!r} clear_vault={args.clear_vault}")
    log_detail(f"stage6 run_sync_v4 → {out_vault}")
    t6 = time.perf_counter()
    try:
        kexp.run_stage6(
            output_dir=out_vault,
            export_front_artifacts=not args.no_front_export,
            clear_vault=args.clear_vault,
            silent=True,
        )
    except Exception as e:
        log_pipeline(f"stage999 stage6 ERROR {type(e).__name__}: {e}")
        raise
    log_stage_timing(
        "999_stage6",
        time.perf_counter() - t6,
        vault=str(out_vault),
        clear_vault=args.clear_vault,
    )
    print(f"[阶段999] 全链路完成，总用时 {time.perf_counter() - t_all:.1f}s")
    log_pipeline(f"stage999 COMPLETE total_elapsed_s={time.perf_counter() - t_all:.1f}")
    log_stage_timing(
        "999_total",
        time.perf_counter() - t_all,
        max_rows=args.max_rows,
        output_dir=str(out_vault),
    )
    log_detail(f"COMPLETE total_elapsed_s={time.perf_counter() - t_all:.1f}")
    try:
        from agentic_rag.naming_service import print_openai_usage_and_cost_estimate

        print_openai_usage_and_cost_estimate("全流程(微观LLM+宏观命名，同一计数器累计)")
        write_token_snapshot(
            "pipeline_complete",
            extra={
                "total_elapsed_s": time.perf_counter() - t_all,
                "output_dir": out_vault,
            },
        )
    except Exception:
        pass


def run_pipeline_stages_with_argv(argv: list[str]) -> int:
    """
    进程内执行流水线，等价于在同一解释器里运行
    ``python agentic_rag/run_pipeline_stages.py`` + ``argv``。

    用于历史模拟器等场景，避免每块 ``subprocess`` 导致的重复冷启动
    （重复 import、BGE/Milvus 重复初始化）。

    调用方需自行设置 ``os.environ``（如 ``SIM_START`` / ``SIM_END``）。

    返回进程退出码（0 表示成功）。`SystemExit` 会被捕获并转为整数码。
    """
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parent.parent / "run_pipeline_stages.py"
    old = sys.argv[:]
    try:
        sys.argv = [str(script), *argv]
        main()
        return 0
    except SystemExit as e:
        code = e.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        try:
            return int(code)
        except Exception:
            return 1
    finally:
        sys.argv = old


if __name__ == "__main__":
    main()
