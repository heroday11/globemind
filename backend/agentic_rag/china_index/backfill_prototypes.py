#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""离线回填：为已有 BGE 向量但缺乏 prototype_scores 的新闻计算 6 维涉华原型分。

使用方式：
    python -m agentic_rag.china_index.backfill_prototypes [--batch-size 500] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import pickle
import time
from typing import Any, Dict, List, Optional

import numpy as np
import psycopg2
import psycopg2.extras

from agentic_rag.db.connection import get_conn as _get_conn_base

_AI_TABLE = "news_ai_analysis"
_NE_TABLE = "news_embeddings"

_DIM_NAMES: List[str] = [
    "中美战略竞争",
    "中国外交与全球治理",
    "中国经济社会",
    "中国军事安全",
    "中国人权与法治",
    "中国文化科技",
]


def _get_conn(*, autocommit: bool = True):
    conn = _get_conn_base(
        os.getenv("PG_DATABASE", "globemind_news"),
        autocommit=autocommit,
        connect_timeout=15,
    )
    return conn


def _compute_lexicon_scores(title: str, abstract: str, body: str = "") -> Dict[str, Any]:
    """快速词典评分，传 body 以覆盖标题可能缺失的中文关键词。"""
    from agentic_rag.china_index.lexicon import score_by_lexicon

    return score_by_lexicon({"title": title or "", "abstract": abstract or "", "body": body or ""})


def _print(msg: str) -> None:
    """带 flush 的 print，确保 nohup 日志实时可见。"""
    import builtins
    builtins.print(msg, flush=True)


def backfill(
    *,
    proto_pkl: str = "/tmp/proto_vecs.pkl",
    batch_size: int = 1000,
    dry_run: bool = False,
    max_rows: Optional[int] = None,
) -> None:
    t0 = time.perf_counter()

    # 1. 加载预计算的原型向量（由 ONNX 离线编码）和学习模型
    _print(f"[Step 1/4] 加载原型向量从 {proto_pkl} …")
    with open(proto_pkl, "rb") as f:
        proto_vecs = pickle.load(f)
    _print(f"  原型向量已就绪（{len(proto_vecs)} × {proto_vecs[0].shape[0]} 维）")

    from agentic_rag.china_index.learned_model import predict_proba_batch, unload_model

    # 2. 查询待处理新闻（用服务端游标避免一次性加载全部向量）
    _print("[Step 2/4] 查询待处理新闻…")

    # 先统计总数（autocommit 连接，简单查询）
    count_conn = _get_conn(autocommit=True)
    ccur = count_conn.cursor()
    ccur.execute(
        f"SELECT COUNT(*) FROM {_AI_TABLE} na "
        f"JOIN {_NE_TABLE} ne ON ne.news_id = na.news_id "
        f"WHERE na.prototype_scores IS NULL AND ne.embedding IS NOT NULL"
    )
    total = ccur.fetchone()[0]
    ccur.close()
    count_conn.close()
    _print(f"  待处理: {total:,} 条新闻")

    if total == 0:
        _print("  无需处理。")
        return
    if dry_run:
        _print(f"[dry-run] 将处理 {total:,} 条（未实际写入）。")
        return

    # 服务端游标需非 autocommit 连接（transaction 内）
    read_conn = _get_conn(autocommit=False)
    read_cur = read_conn.cursor(name="backfill_cursor")
    limit_clause = f"LIMIT {int(max_rows)}" if max_rows else ""

    read_cur.execute(
        f"""
        SELECT na.news_id, n.title, n.abstract, n.body,
               ne.embedding
        FROM {_AI_TABLE} na
        JOIN news n ON n.id = na.news_id
        JOIN {_NE_TABLE} ne ON ne.news_id = na.news_id
        WHERE na.prototype_scores IS NULL
          AND ne.embedding IS NOT NULL
        ORDER BY na.news_id
        {limit_clause}
        """
    )

    # 3. 批量计算（服务端游标逐批读取）
    _print(f"[Step 3/4] 批量计算原型分（batch_size={batch_size}）…")
    write_conn = _get_conn(autocommit=False)
    write_conn.autocommit = False
    write_cur = write_conn.cursor()

    processed = 0

    # 预编译 UPDATE
    update_sql = (
        f"UPDATE {_AI_TABLE} SET "
        f"prototype_scores = %s::jsonb, "
        f"prototype_weighted = %s, "
        f"lexicon_score = %s, "
        f"lexicon_matches = %s::jsonb, "
        f"china_index_version = 'v2' "
        f"WHERE news_id = %s"
    )

    while True:
        batch = read_cur.fetchmany(batch_size)
        if not batch:
            break
        batch_params: List[tuple] = []

        # 收集本批向量做批量预测
        batch_vecs = []
        batch_meta = []
        for row in batch:
            news_id, title, abstract, body, embedding_json = row
            if embedding_json is None:
                continue
            vec = np.asarray(embedding_json, dtype=np.float32)
            batch_vecs.append(vec)
            batch_meta.append((news_id, title, abstract, body, vec))

        if not batch_vecs:
            continue

        vecs_arr = np.stack(batch_vecs, axis=0)

        # 批量学习模型预测（prototype_weighted 存储此值）
        learned_probas = predict_proba_batch(vecs_arr)

        # 6 维余弦相似度（仅用于 prototype_scores 可解释性，不参与加权）
        proto_dots = np.stack([np.dot(vecs_arr, pv) for pv in proto_vecs], axis=1)

        for i, (news_id, title, abstract, body, vec) in enumerate(batch_meta):
            raw_scores = [max(0.0, min(1.0, float(proto_dots[i, j]))) for j in range(6)]
            weighted = float(learned_probas[i])

            # 词典分（title + abstract + body）
            lex = _compute_lexicon_scores(title or "", abstract or "", body or "")
            lex_score = lex["score"]
            lex_matches = lex.get("matches", {})

            proto_json = json.dumps(
                {name: round(s, 4) for name, s in zip(_DIM_NAMES, raw_scores)},
                ensure_ascii=False,
            )
            lex_json = json.dumps(lex_matches, ensure_ascii=False)

            batch_params.append(
                (proto_json, round(weighted, 4), round(lex_score, 4), lex_json, int(news_id))
            )

        # 批量写回
        try:
            psycopg2.extras.execute_batch(write_cur, update_sql, batch_params, page_size=500)
            write_conn.commit()
        except Exception as e:
            write_conn.rollback()
            _print(f"  [错误] batch offset {processed}: {e}")
            raise

        processed += len(batch)
        elapsed = time.perf_counter() - t0
        rate = processed / elapsed if elapsed > 0 else 0
        _print(
            f"  进度: {processed:,}/{total:,} ({100.0 * processed / total:.1f}%) "
            f"速率: {rate:.0f} 条/s"
        )

    read_cur.close()
    read_conn.close()
    write_cur.close()
    write_conn.close()

    # 4. 验证
    _print("[Step 4/4] 验证…")
    verify_conn = _get_conn(autocommit=True)
    vcur = verify_conn.cursor()
    vcur.execute(
        f"SELECT COUNT(*) FROM {_AI_TABLE} WHERE prototype_scores IS NOT NULL"
    )
    filled = vcur.fetchone()[0]
    vcur.execute(
        f"SELECT COUNT(*) FROM {_AI_TABLE} WHERE prototype_weighted IS NOT NULL"
    )
    filled_w = vcur.fetchone()[0]
    vcur.execute(
        f"SELECT COUNT(*) FROM {_AI_TABLE} WHERE china_index_version = 'v2'"
    )
    v2_count = vcur.fetchone()[0]
    verify_conn.close()

    total_elapsed = time.perf_counter() - t0
    _print(f"\n[完成] 耗时 {total_elapsed:.1f}s")
    _print(f"  prototype_scores 已填充: {filled:,} 条")
    _print(f"  prototype_weighted 已填充: {filled_w:,} 条")
    _print(f"  china_index_version=v2: {v2_count:,} 条")


def main() -> None:
    parser = argparse.ArgumentParser(description="离线回填 6 维涉华原型分")
    parser.add_argument("--batch-size", type=int, default=500, help="每批处理条数")
    parser.add_argument("--max-rows", type=int, default=None, help="最多处理条数（调试用）")
    parser.add_argument("--dry-run", action="store_true", help="仅预览不写入")
    args = parser.parse_args()

    backfill(
        batch_size=args.batch_size,
        dry_run=args.dry_run,
        max_rows=args.max_rows,
    )


if __name__ == "__main__":
    main()
