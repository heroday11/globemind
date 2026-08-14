#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段 3（v2）：asyncio 并发补全宏观故事线 LLM（线程池承载同步 OpenAI/命名逻辑）。"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Optional, Sequence

from agentic_rag.macro_llm_fill_pending import (
    fill_one_storyline,
    list_pending_storyline_ids,
)
from agentic_rag.pipeline_logging import log_pipeline_progress


def _tqdm_enabled() -> bool:
    return os.getenv("PIPELINE_NO_TQDM", "").strip().lower() not in (
        "1",
        "true",
        "yes",
    )


async def run_fill_pending_async(
    *,
    dry_run: bool,
    limit: Optional[int],
    max_concurrency: int,
    storyline_ids: Optional[Sequence[int]] = None,
) -> None:
    os.environ.pop("SKIP_MACRO_LLM_NAMING", None)
    if storyline_ids is not None:
        sids = [int(x) for x in storyline_ids]
    else:
        sids = list_pending_storyline_ids(limit)
    if not sids:
        print("[macro_llm_fill_async] 无待补全的宏观故事线", flush=True)
        return

    use_llm = os.getenv("MACRO_USE_LLM_NAMING", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )
    print(
        f"[macro_llm_fill_async] asyncio 并发={max_concurrency} 条数={len(sids)} "
        f"dry_run={dry_run} use_llm={use_llm}",
        flush=True,
    )
    sem = asyncio.Semaphore(max(1, int(max_concurrency)))
    t0 = time.perf_counter()
    n_total = len(sids)

    async def one(sid: int) -> tuple[int, str, str]:
        async with sem:
            st, detail = await asyncio.to_thread(
                fill_one_storyline,
                sid,
                dry_run=dry_run,
            )
            return (sid, st, detail)

    tasks = [asyncio.create_task(one(s)) for s in sids]
    pbar = None
    if _tqdm_enabled():
        try:
            from tqdm import tqdm

            pbar = tqdm(
                total=n_total,
                desc="macro_llm",
                unit="sl",
                ascii=True,
                mininterval=0.5,
            )
        except Exception:
            pbar = None

    n_ok = 0
    n_bad = 0
    n_done = 0
    log_every = max(1, n_total // 20)

    for fut in asyncio.as_completed(tasks):
        try:
            r = await fut
        except BaseException as ex:
            print(f"[macro_llm_fill_async] task 异常: {ex!r}", flush=True)
            n_bad += 1
            n_done += 1
            if pbar:
                pbar.update(1)
            continue
        if isinstance(r, BaseException):
            print(f"[macro_llm_fill_async] gather 异常: {r!r}", flush=True)
            n_bad += 1
        else:
            _sid, st, detail = r
            if st in ("ok", "dry_ok"):
                n_ok += 1
            else:
                n_bad += 1
                print(f"[macro_llm_fill_async] sid={_sid} {st}: {detail}", flush=True)
        n_done += 1
        if pbar:
            pbar.update(1)
        elapsed = time.perf_counter() - t0
        if n_done % log_every == 0 or n_done == n_total:
            eta_s = None
            if n_done > 0 and n_done < n_total:
                eta_s = (n_total - n_done) * (elapsed / n_done)
            log_pipeline_progress(
                "v2_3_llm",
                done=n_done,
                total=n_total,
                elapsed_s=elapsed,
                eta_s=eta_s,
            )

    if pbar:
        pbar.close()

    print(
        f"[macro_llm_fill_async] 完成 成功≈{n_ok} 失败/跳过≈{n_bad} "
        f"用时 {time.perf_counter() - t0:.1f}s",
        flush=True,
    )
