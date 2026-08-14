"""
微观流水线：两阶段 VRAM 互斥摄取（BGE-M3 与 GLiNER 不同时驻留显存）。

- Phase 1a（BGE Sieve）：仅加载 BGE，补全 china_related_index；未过闸行同时写入空占位，避免反复拉取。
- Phase 1b（GLiNER + 本地情感 GPU）：卸载 BGE 后加载 GLiNER 与 HF 情感 pipeline；涉华候选与主闸 CHINA_GATE_THRESHOLD 对齐，无第二道闸值，无云端 LLM API。
- 合并：--stage 1 = 1a → purge VRAM → 1b。

阶段 2–4（pkl 离线）逻辑不变，仍委托 analysis_service。
"""
from __future__ import annotations

import os
import pickle
import time

from agentic_rag.pipeline_logging import log_stage_timing
from typing import Any, Dict, List, Optional, Tuple

from agentic_rag import analysis_service as svc
from agentic_rag.analysis_service import (
    CHINA_GATE_THRESHOLD,
    SKIPPED_BELOW_GATE_SENTIMENT,
    SKIPPED_BELOW_GATE_TOPIC,
    _build_news_text,
    _compute_china_index_only,
    _passes_llm_gate,
    _write_back_batch,
)
from agentic_rag.gliner_extractor import GLiNEREntityExtractor
from agentic_rag.stage1b_sentiment import (
    load_stage1b_sentiment_pipeline,
    run_stage1b_local_gpu_pipeline,
)
from agentic_rag.db.news_analysis_schema import TABLE_NAME as _NA_TABLE, ensure_news_analysis_table
from agentic_rag.pipeline.sim_time_window import sim_fetch_order_by, sim_pub_time_and
from agentic_rag.pipeline import micro_budget as _micro_budget
from agentic_rag.pipeline.dedupe_lsh import (
    classify_batch,
    dedupe_enabled,
    insert_novels_into_lsh,
    load_lsh_index,
    minhash_for_row,
    save_lsh_index_atomic,
)
from agentic_rag.ingestion.embedder import get_embedder, unload_embedder
from config.settings import FrozenDefaults


def _load_pickle(path: str) -> List[dict]:
    with open(path, "rb") as f:
        return pickle.load(f)


def _save_pickle(path: str, obj: Any) -> None:
    with open(path, "wb") as f:
        pickle.dump(obj, f)


def run_stage0_probe(batch_size: int) -> None:
    """原「阶段①」：仅统计待处理 + 试拉一批（不加载 BGE/GLiNER）。"""
    n = svc.stage_count_unprocessed()
    print(f"[阶段0/探针] 待处理行数(sql_unprocessed): {n}")
    t0 = time.perf_counter()
    rows = svc.stage_fetch_unprocessed(batch_size=batch_size)
    print(f"[阶段0/探针] 拉取 {len(rows)} 条，耗时 {(time.perf_counter() - t0) * 1000:.0f} ms")


def _purge_vram_after_bge() -> None:
    print("[VRAM] Phase 1a 结束：unload_embedder() + gc + cuda.empty_cache", flush=True)
    unload_embedder()


def _purge_vram_after_gliner() -> None:
    import gc

    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
    except Exception:
        pass
    print("[VRAM] Phase 1b 结束：gc + cuda.empty_cache（GLiNER 已在 pipeline 内 unload）", flush=True)


def _fetch_rows_missing_china_index(ex_read, batch_size: int) -> List[dict]:
    """Phase 1a：尚未写入 china_related_index 的新闻（含无 analysis 行）。"""
    lim = max(1, int(batch_size))
    sql = (
        "SELECT n.id, n.title, n.abstract, n.body, n.url FROM news n "
        f"LEFT JOIN {_NA_TABLE} na ON na.news_id = n.id "
        "WHERE na.news_id IS NULL OR na.china_related_index IS NULL "
        f"{sim_pub_time_and('n')}"
        f"{sim_fetch_order_by()} LIMIT {lim}"
    )
    res = ex_read.query(sql)
    if not res.get("ok"):
        raise RuntimeError(f"Phase1a fetch failed: {res.get('error')}")
    return res.get("rows") or []


def _count_rows_missing_china_index(ex_read) -> int:
    sql = (
        "SELECT COUNT(*) AS cnt FROM news n "
        f"LEFT JOIN {_NA_TABLE} na ON na.news_id = n.id "
        "WHERE na.news_id IS NULL OR na.china_related_index IS NULL"
        f"{sim_pub_time_and('n')}"
    )
    res = ex_read.query(sql)
    if not res.get("ok"):
        raise RuntimeError(f"Phase1a count failed: {res.get('error')}")
    rows = res.get("rows") or []
    if not rows:
        return 0
    v = rows[0].get("cnt")
    return int(v) if v is not None else 0


def _fetch_canonical_analysis(ex_read, news_ids: List[int]) -> Dict[int, dict]:
    """读取 canonical 的涉华字段（影子稿继承）。"""
    if not news_ids:
        return {}
    ids_str = ",".join(str(int(i)) for i in sorted(set(news_ids)))
    sql = (
        f"SELECT news_id, china_related_index, is_china_related FROM {_NA_TABLE} "
        f"WHERE news_id IN ({ids_str})"
    )
    res = ex_read.query(sql)
    if not res.get("ok"):
        raise RuntimeError(f"canonical fetch failed: {res.get('error')}")
    out: Dict[int, dict] = {}
    for row in res.get("rows") or []:
        nid = int(row["news_id"])
        out[nid] = row
    return out


def _shadow_record_from_duplicate(row: dict, canonical_id: int, canon: dict) -> dict:
    """继承 canonical 的涉华指数；不跑 BGE/GLiNER；占位情感/主题使 1b SQL 不选入。"""
    from agentic_rag.source_credibility import credibility_from_url

    text = _build_news_text(row)
    idx = float(canon.get("china_related_index") or 0.0)
    ic = canon.get("is_china_related")
    if ic is None:
        ic = idx >= CHINA_GATE_THRESHOLD
    return {
        "id": int(row["id"]),
        "title": row.get("title") or "",
        "text": text,
        "url": row.get("url"),
        "is_china_related": bool(ic),
        "china_related_index": max(0.0, min(1.0, idx)),
        "entities": [],
        "sentiment": SKIPPED_BELOW_GATE_SENTIMENT,
        "topic": SKIPPED_BELOW_GATE_TOPIC,
        "sentiment_score": None,
        "source_credibility": credibility_from_url(row.get("url")),
        "bge_embedding": None,
        "duplicate_of": int(canonical_id),
        "dedupe_method": "minhash_lsh",
    }


def _fetch_rows_phase2_llm_pending(ex_read, batch_size: int) -> List[dict]:
    """Phase 1b：涉华候选（is_china_related 或 index 过主闸）且仍缺情感/主题。"""
    lim = max(1, int(batch_size))
    g = float(CHINA_GATE_THRESHOLD)
    sql = (
        "SELECT n.id, n.title, n.abstract, n.body, n.url, na.china_related_index, na.is_china_related "
        "FROM news n "
        f"INNER JOIN {_NA_TABLE} na ON na.news_id = n.id "
        f"WHERE (na.is_china_related IS TRUE OR na.china_related_index >= {g}) "
        "AND na.duplicate_of IS NULL "
        "AND (na.sentiment_analysis IS NULL OR na.topic_classification IS NULL) "
        f"{sim_pub_time_and('n')}"
        f"{sim_fetch_order_by()} LIMIT {lim}"
    )
    res = ex_read.query(sql)
    if not res.get("ok"):
        raise RuntimeError(f"Phase1b fetch failed: {res.get('error')}")
    return res.get("rows") or []


def _count_rows_phase2_llm_pending(ex_read) -> int:
    g = float(CHINA_GATE_THRESHOLD)
    sql = (
        "SELECT COUNT(*) AS cnt FROM news n "
        f"INNER JOIN {_NA_TABLE} na ON na.news_id = n.id "
        f"WHERE (na.is_china_related IS TRUE OR na.china_related_index >= {g}) "
        "AND na.duplicate_of IS NULL "
        "AND (na.sentiment_analysis IS NULL OR na.topic_classification IS NULL)"
        f"{sim_pub_time_and('n')}"
    )
    res = ex_read.query(sql)
    if not res.get("ok"):
        raise RuntimeError(f"Phase1b count failed: {res.get('error')}")
    rows = res.get("rows") or []
    if not rows:
        return 0
    v = rows[0].get("cnt")
    return int(v) if v is not None else 0


def _records_from_rows_phase2(rows: List[dict]) -> List[dict]:
    out: List[dict] = []
    for row in rows:
        text = _build_news_text(row)
        idx = float(row.get("china_related_index") or 0.0)
        ic = row.get("is_china_related")
        if ic is None:
            ic = idx >= CHINA_GATE_THRESHOLD
        out.append({
            "id": int(row["id"]),
            "title": row.get("title") or "",
            "text": text,
            "url": row.get("url"),
            "is_china_related": bool(ic),
            "china_related_index": max(0.0, min(1.0, idx)),
            "entities": [],
            "sentiment": None,
            "topic": None,
            "sentiment_score": None,
            "bge_embedding": None,
        })
    return out


def run_stage1a_bge_sieve(
    batch_size: int = 64,
    max_rows: Optional[int] = None,
    max_batches: Optional[int] = None,
    micro_total_cap: Optional[int] = None,
) -> None:
    """
    Phase 1a：仅 BGE-M3；显存占用期间不加载 GLiNER。
    BGE_ENCODE_BATCH_SIZE 默认取自 FrozenDefaults（24）或环境变量 BGE_ENCODE_BATCH_SIZE。
    """
    svc.ensure_dotenv_loaded()
    from agentic_rag.analysis_quiet import apply_run_analysis_warning_filters

    apply_run_analysis_warning_filters()
    try:
        ensure_news_analysis_table()
    except Exception as e:
        print(f"[Schema] ensure_news_analysis_table: {type(e).__name__}: {e}")

    os.environ["BGE_ENCODE_BATCH_SIZE"] = os.getenv(
        "BGE_ENCODE_BATCH_SIZE", str(FrozenDefaults.BGE_ENCODE_BATCH_SIZE)
    )
    # 全局显存预算（默认 80%）在进入 GPU 编码前应用一次。
    svc.init_cuda_memory_guard_once()

    ex_read = svc._pg_read()
    ex_write = svc._pg_write()

    pending = _count_rows_missing_china_index(ex_read)
    limit_rows, cap_skip = _micro_budget.effective_row_limit(max_rows, micro_total_cap)
    if limit_rows is not None and limit_rows <= 0:
        print(
            f"[阶段1a/BGE] {cap_skip or '本段条数上限为 0'}；跳过 1a"
            f"（状态文件: {_micro_budget.budget_state_path()}）",
            flush=True,
        )
        return
    if micro_total_cap is not None:
        print(
            f"[阶段1a/BGE] micro_total_cap={micro_total_cap} 已累计 {_micro_budget.load_consumed():,}，"
            f"本段上限 {limit_rows if limit_rows is not None else '∞'} 条"
            f"（与单次 --max-rows 取较小值）",
            flush=True,
        )
    print(
        f"[阶段1a/BGE] 待补 china_related_index 行数: {pending:,} "
        f"(batch_size={batch_size}, max_rows={max_rows}, limit_rows={limit_rows}, max_batches={max_batches})"
    )

    total = 0
    batches = 0
    start = time.perf_counter()

    embedder = None
    try:
        embedder = get_embedder()
    except Exception as e:
        print(f"[阶段1a/BGE] Embedder 加载失败: {type(e).__name__}: {e}")
        return

    try:
        while True:
            if max_batches is not None and batches >= max_batches:
                print(f"[阶段1a/BGE] stop: max_batches={max_batches}")
                break
            if limit_rows is not None and total >= limit_rows:
                why = f"limit_rows={limit_rows}"
                if micro_total_cap is not None:
                    why += f"（micro_total_cap 剩余已用尽，已累计 {_micro_budget.load_consumed():,}）"
                print(f"[阶段1a/BGE] stop: {why}")
                break

            remaining = None if limit_rows is None else limit_rows - total
            bs = batch_size if remaining is None else max(1, min(batch_size, remaining))
            rows = _fetch_rows_missing_china_index(ex_read, bs)
            if not rows:
                break

            lsh = None
            real_dups: List[Tuple[dict, int]] = []
            canon_map: Dict[int, dict] = {}
            novel_rows: List[dict] = sorted(rows, key=lambda r: int(r["id"]))
            novel_mhs: List[Any] = []

            if dedupe_enabled():
                lsh = load_lsh_index()
                novel_rows, dup_pairs, _ = classify_batch(lsh, rows)
                canon_map = _fetch_canonical_analysis(
                    ex_read, list({c for _, c in dup_pairs})
                )
                fallback_novel: List[dict] = []
                for row, cid in dup_pairs:
                    if cid in canon_map:
                        real_dups.append((row, cid))
                    else:
                        fallback_novel.append(row)
                novel_rows = sorted(
                    novel_rows + fallback_novel,
                    key=lambda r: int(r["id"]),
                )
                novel_mhs = [minhash_for_row(r) for r in novel_rows]

            records = _compute_china_index_only(
                embedder, novel_rows, show_bge_progress=True
            )
            shadow_recs: List[dict] = []
            if dedupe_enabled():
                for row, cid in real_dups:
                    shadow_recs.append(
                        _shadow_record_from_duplicate(row, cid, canon_map[cid])
                    )

            for rec in records:
                if not _passes_llm_gate(rec):
                    rec["sentiment"] = SKIPPED_BELOW_GATE_SENTIMENT
                    rec["topic"] = SKIPPED_BELOW_GATE_TOPIC
                    rec["entities"] = []

            all_records = records + shadow_recs
            _write_back_batch(ex_write, all_records, sync_milvus=True)

            if dedupe_enabled() and lsh is not None and novel_mhs:
                insert_novels_into_lsh(lsh, novel_rows, novel_mhs)
                save_lsh_index_atomic(lsh)

            n = len(all_records)
            total += n
            if micro_total_cap is not None:
                _micro_budget.add_consumed_micro(n)
            batches += 1
            print(
                f"[阶段1a/BGE] 本批 {n} 条，累计 {total}，批 {batches}，"
                f"用时 {time.perf_counter() - start:.1f}s",
                flush=True,
            )
            if micro_total_cap is not None:
                print(
                    f"[阶段1a/BGE] micro 总预算 已累计 {_micro_budget.load_consumed():,} / {micro_total_cap}",
                    flush=True,
                )
    finally:
        # 先 drain（含 Milvus 缓冲刷盘），再记 1a，避免「1a 秒数」与整段 1 脱节
        try:
            svc.drain_milvus_async_workers()
        except Exception as e:
            print(f"[MilvusSync] drain_milvus_async_workers: {type(e).__name__}: {e}", flush=True)
        log_stage_timing(
            "1a",
            time.perf_counter() - start,
            total_rows=total,
            batches=batches,
            batch_size=batch_size,
            max_rows=max_rows,
            micro_total_cap=micro_total_cap,
            limit_rows=limit_rows,
            llm_backend=os.getenv("LLM_BACKEND", ""),
        )
        if embedder is not None:
            _purge_vram_after_bge()
            del embedder


def run_stage1b_gliner_llm(
    batch_size: int = 64,
    llm_batch_size: Optional[int] = None,
    workers: Optional[int] = None,
    max_rows: Optional[int] = None,
    max_batches: Optional[int] = None,
) -> None:
    """
    Phase 1b：仅本地 GPU — GLiNER + HuggingFace 情感 pipeline；已移除云端/本地 vLLM LLM API。
    涉华候选与 CHINA_GATE_THRESHOLD / is_china_related 对齐，全部跑 GLiNER+情感（无 STAGE1B 第二闸）。
    llm_batch_size/workers 参数保留兼容调用方，用于本地子批大小（默认同 batch_size）。
    """
    unload_embedder()

    svc.ensure_dotenv_loaded()
    from agentic_rag.analysis_quiet import apply_run_analysis_warning_filters

    apply_run_analysis_warning_filters()
    try:
        ensure_news_analysis_table()
    except Exception as e:
        print(f"[Schema] ensure_news_analysis_table: {type(e).__name__}: {e}")

    os.environ.setdefault("GLINER_MAX_CONCURRENT", "1")
    # 在 GLiNER / 情感模型加载前应用统一显存预算。
    svc.init_cuda_memory_guard_once()

    local_chunk = int(llm_batch_size) if llm_batch_size is not None else int(batch_size)
    local_chunk = max(1, local_chunk)

    print(
        f"[阶段1b/本地GPU] SQL 候选：(is_china_related OR china_related_index>={CHINA_GATE_THRESHOLD})；"
        f"DB 子批 chunk_size={local_chunk}；"
        f"GLiNER inference batch={os.getenv('STAGE1B_GLINER_INFER_BATCH_SIZE', '16')}；"
        f"情感 HF batch={os.getenv('STAGE1B_SENTIMENT_BATCH_SIZE', '16')}；"
        f"GLINER_MAX_CONCURRENT={os.getenv('GLINER_MAX_CONCURRENT', '1')}",
        flush=True,
    )

    ex_read = svc._pg_read()
    ex_write = svc._pg_write()

    pending = _count_rows_phase2_llm_pending(ex_read)
    print(
        f"[阶段1b] 待处理（SQL：缺情感/主题，且涉华候选 OR index>={CHINA_GATE_THRESHOLD}）: {pending:,} 条"
    )

    total = 0
    batches = 0
    start = time.perf_counter()
    shared_gliner: Optional[GLiNEREntityExtractor] = None
    sentiment_pipe: Any = None

    try:
        shared_gliner = GLiNEREntityExtractor()
        sentiment_pipe = load_stage1b_sentiment_pipeline()
        while True:
            if max_batches is not None and batches >= max_batches:
                print(f"[阶段1b] stop: max_batches={max_batches}")
                break
            if max_rows is not None and total >= max_rows:
                print(f"[阶段1b] stop: max_rows={max_rows}")
                break

            remaining = None if max_rows is None else max_rows - total
            bs = batch_size if remaining is None else max(1, min(batch_size, remaining))
            rows = _fetch_rows_phase2_llm_pending(ex_read, bs)
            if not rows:
                break

            records = _records_from_rows_phase2(rows)
            run_stage1b_local_gpu_pipeline(
                records,
                shared_gliner,
                sentiment_pipe,
                chunk_size=local_chunk,
            )
            _write_back_batch(ex_write, records, sync_milvus=False)

            n = len(records)
            total += n
            batches += 1
            print(
                f"[阶段1b] 本批 {n} 条，累计 {total}，批 {batches}，"
                f"用时 {time.perf_counter() - start:.1f}s",
                flush=True,
            )
    finally:
        if shared_gliner is not None:
            try:
                shared_gliner.unload_model()
            except Exception:
                pass
        sentiment_pipe = None
        log_stage_timing(
            "1b",
            time.perf_counter() - start,
            total_rows=total,
            batches=batches,
            batch_size=batch_size,
            max_rows=max_rows,
            llm_batch_size=local_chunk,
            workers=workers,
            llm_backend="local_gpu",
        )
        _purge_vram_after_gliner()


def run_stage1_two_phase_sequential(
    batch_size: int = 64,
    llm_batch_size: Optional[int] = None,
    workers: Optional[int] = None,
    max_rows: Optional[int] = None,
    max_batches: Optional[int] = None,
    micro_total_cap: Optional[int] = None,
) -> None:
    """--stage 1：1a → 显存清理 → 1b。"""
    print("[阶段1/两阶段] 1a BGE Sieve → purge VRAM → 1b GLiNER+本地情感", flush=True)
    t_all = time.perf_counter()
    run_stage1a_bge_sieve(
        batch_size=batch_size,
        max_rows=max_rows,
        max_batches=max_batches,
        micro_total_cap=micro_total_cap,
    )
    run_stage1b_gliner_llm(
        batch_size=batch_size,
        llm_batch_size=llm_batch_size,
        workers=workers,
        max_rows=max_rows,
        max_batches=max_batches,
    )
    log_stage_timing(
        "1",
        time.perf_counter() - t_all,
        batch_size=batch_size,
        max_rows=max_rows,
        micro_total_cap=micro_total_cap,
        llm_batch_size=llm_batch_size,
        workers=workers,
        llm_backend=os.getenv("LLM_BACKEND", ""),
    )


def run_stage2(batch_size: int, out_path: str) -> None:
    emb = svc.make_embedder_only()
    rows = svc.stage_fetch_unprocessed(batch_size=batch_size)
    t0 = time.perf_counter()
    records = svc.stage_embed_china_only(rows, embedder=emb)
    dt = time.perf_counter() - t0
    print(
        f"[阶段②] BGE-M3 {len(records)} 条，耗时 {dt:.1f}s "
        f"（约 {dt / max(1, len(records)):.2f}s/条）"
    )
    out = out_path or "records_stage2.pkl"
    _save_pickle(out, records)
    print(f"[阶段②] 已保存 {out}")


def run_stage3(
    pickle_path: str,
    out_path: str,
    llm_batch_size: int | None,
    workers: int | None,
) -> None:
    if not pickle_path:
        raise SystemExit("阶段③需要 --pickle records_stage2.pkl")
    records = _load_pickle(pickle_path)
    t0 = time.perf_counter()
    svc.stage_llm_only(records, llm_batch_size=llm_batch_size, workers=workers)
    print(f"[阶段③] GLiNER ∥ LLM 耗时 {time.perf_counter() - t0:.1f}s")
    out = out_path or "records_stage3.pkl"
    _save_pickle(out, records)
    print(f"[阶段③] 已保存 {out}")


def run_stage4(pickle_path: str) -> None:
    if not pickle_path:
        raise SystemExit("阶段④需要 --pickle records_stage3.pkl")
    records = _load_pickle(pickle_path)
    t0 = time.perf_counter()
    svc.stage_write_back(records)
    print(f"[阶段④] 写回 {len(records)} 条，耗时 {time.perf_counter() - t0:.1f}s")
