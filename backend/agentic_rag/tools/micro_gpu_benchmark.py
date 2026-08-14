#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
微观 GPU 微基准：随机 N 条新闻文本，对多组 (BGE batch / GLiNER 并发) 测 BGE encode 与 GLiNER 耗时，
用于在 RTX 3080 10GB 等环境下选「较快且不易 OOM」的默认参数。

用法（仓库根、已激活 venv、已配置 PG/.env）：
  python -m agentic_rag.tools.micro_gpu_benchmark
  python -m agentic_rag.tools.micro_gpu_benchmark --samples 10 --synthetic

无数据库时用 --synthetic 生成占位长文本（仅测本地算力，不连 PG）。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, List

# 在 import torch 前加载 .env
def _load_env() -> None:
    try:
        from pathlib import Path

        from dotenv import load_dotenv

        root = Path(__file__).resolve().parent.parent.parent
        load_dotenv(root / "agentic_rag" / ".env", override=False)
        load_dotenv(root / ".env", override=True)
    except ImportError:
        pass


_load_env()


@dataclass
class Preset:
    name: str
    bge_encode_batch_size: int
    gliner_max_concurrent: int


PRESETS: List[Preset] = [
    Preset("safe_16_g1", 16, 1),
    Preset("balanced_24_g2", 24, 2),
    Preset("fast_32_g2", 32, 2),
]


def _pg_fetch_random_news(n: int) -> List[dict]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    conn = psycopg2.connect(
        host=os.getenv("PG_HOST", "127.0.0.1"),
        port=int(os.getenv("PG_PORT", "5432")),
        dbname=os.getenv("PG_DATABASE", os.getenv("PG_DBNAME", "postgres")),
        user=os.getenv("PG_USER", "news_reader"),
        password=os.getenv("PG_PASSWORD", ""),
        connect_timeout=15,
    )
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """
            SELECT id, title, abstract, body
            FROM news
            WHERE title IS NOT NULL AND trim(title) <> ''
            ORDER BY random()
            LIMIT %s
            """,
            (int(n),),
        )
        rows = cur.fetchall()
        cur.close()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _build_text(row: dict) -> str:
    from agentic_rag.analysis_service import _build_news_text

    return _build_news_text(row)


def _synthetic_texts(n: int) -> List[str]:
    base = (
        "这是一段用于 GPU 微基准的合成新闻正文，包含涉华与英文关键词 China、PRC、Beijing，"
        "用于拉长 tokenizer 输入长度以接近真实新闻。 "
    )
    return [base * 8 + f" 编号{i}。" for i in range(n)]


async def _time_gliner_batch(
    texts: List[str],
    concurrent: int,
) -> float:
    from agentic_rag.analysis_service import GLiNEREntityExtractor

    ext = GLiNEREntityExtractor()
    sem = asyncio.Semaphore(max(1, concurrent))
    t0 = time.perf_counter()

    async def one(t: str) -> None:
        async with sem:
            await asyncio.to_thread(ext.extract, t)

    try:
        await asyncio.gather(*[one(t) for t in texts])
    finally:
        try:
            ext.unload_model()
        except Exception:
            pass
        del ext
    import gc

    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
    except Exception:
        pass
    return time.perf_counter() - t0


def _run_preset(
    preset: Preset,
    texts: List[str],
    *,
    warmup: bool,
) -> tuple[float, float]:
    """返回 (bge_seconds, gliner_seconds)。"""
    os.environ["BGE_ENCODE_BATCH_SIZE"] = str(preset.bge_encode_batch_size)
    os.environ["GLINER_MAX_CONCURRENT"] = str(preset.gliner_max_concurrent)

    from agentic_rag.ingestion.embedder import get_embedder, unload_embedder

    unload_embedder()

    # ---------- BGE ----------
    emb = get_embedder()
    if warmup and texts:
        _ = emb.encode([texts[0]], batch_size=1, show_progress_bar=False)

    t0 = time.perf_counter()
    _ = emb.encode(
        texts,
        batch_size=max(1, min(preset.bge_encode_batch_size, len(texts))),
        show_progress_bar=False,
    )
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.synchronize()
    except Exception:
        pass
    bge_s = time.perf_counter() - t0

    emb.unload_model()
    unload_embedder()

    # ---------- GLiNER ----------
    gliner_s = asyncio.run(_time_gliner_batch(texts, preset.gliner_max_concurrent))
    return bge_s, gliner_s


def main() -> int:
    p = argparse.ArgumentParser(description="微观 BGE+GLiNER GPU 微基准")
    p.add_argument("--samples", type=int, default=10, help="随机新闻条数")
    p.add_argument("--synthetic", action="store_true", help="不连库，使用合成文本")
    p.add_argument(
        "--gate-rate",
        type=float,
        default=0.08,
        help="推算十万条时假定过闸比例（仅用于 LLM 条数说明，本地不测云端）",
    )
    args = p.parse_args()

    n = max(1, min(int(args.samples), 64))
    texts: List[str] = []

    if args.synthetic:
        texts = _synthetic_texts(n)
        print(f"[Bench] 使用合成文本 {n} 条（--synthetic）", flush=True)
    else:
        try:
            rows = _pg_fetch_random_news(n)
            if len(rows) < n:
                print(
                    f"[Bench] 库中仅取到 {len(rows)} 条，少于请求的 {n} 条",
                    file=sys.stderr,
                    flush=True,
                )
            if not rows:
                print("[Bench] 无数据，改用 --synthetic 或检查 PG", file=sys.stderr)
                return 1
            texts = [_build_text(r) for r in rows]
            print(f"[Bench] 自 PostgreSQL 随机 {len(texts)} 条新闻", flush=True)
        except Exception as e:
            print(f"[Bench] 读库失败: {type(e).__name__}: {e}，请使用 --synthetic", file=sys.stderr)
            return 1

    try:
        import torch

        print(
            f"[Bench] torch.cuda.is_available()={torch.cuda.is_available()} "
            f"device_count={torch.cuda.device_count()}",
            flush=True,
        )
        if torch.cuda.is_available():
            print(f"[Bench] GPU: {torch.cuda.get_device_name(0)}", flush=True)
    except Exception as e:
        print(f"[Bench] torch 检测: {e}", flush=True)

    print(
        "\n预设组: BGE encode batch × GLiNER 并发（每组先 unload 再测，避免模型并存）\n"
        f"样本数={len(texts)}，过闸率假定={args.gate_rate:.0%}（仅用于推算）\n",
        flush=True,
    )

    results: List[tuple[str, float, float]] = []
    for preset in PRESETS:
        print(f"--- {preset.name}: BGE_BS={preset.bge_encode_batch_size}, GLINER_CONC={preset.gliner_max_concurrent} ---", flush=True)
        bge_s, gl_s = _run_preset(preset, texts, warmup=True)
        results.append((preset.name, bge_s, gl_s))
        print(
            f"    BGE encode: {bge_s:.3f}s  ({len(texts)/max(bge_s,1e-9):.2f} 条/s)",
            flush=True,
        )
        print(
            f"    GLiNER:     {gl_s:.3f}s  ({len(texts)/max(gl_s,1e-9):.2f} 条/s)",
            flush=True,
        )

    # 推算十万条（仅本地 BGE+GLiNER；LLM 为云端另算）
    print("\n=== 十万条粗算（仅本地 BGE 全量 + GLiNER 仅过闸部分）===", flush=True)
    n100k = 100_000
    gate = float(args.gate_rate)
    for name, bge_s, gl_s in results:
        per = len(texts)
        t_bge_100k = (bge_s / per) * n100k
        t_gl_100k = (gl_s / per) * (n100k * gate)
        t_loc = t_bge_100k + t_gl_100k
        print(
            f"  [{name}] BGE≈{t_bge_100k/3600:.2f}h + GLiNER(过闸)≈{t_gl_100k/60:.1f}min "
            f"→ 本地算力合计≈{t_loc/3600:.2f}h",
            flush=True,
        )
    print(
        "\n说明: 未含云端 LLM API 耗时与网络；未含 PG 写回/Milvus/宏观。"
        "\n推荐: 在「不致卡顿」的预设中选 BGE 条/s 较高者；默认已将工程默认改为 balanced_24_g2 附近时可编辑 FrozenDefaults / 环境变量。",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
