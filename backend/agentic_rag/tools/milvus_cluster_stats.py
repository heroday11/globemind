#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""统计 Milvus news_vectors 中已分配簇 vs 未分配(-1) 的数量与占比。"""
from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

root = Path(__file__).resolve().parent.parent.parent
os.chdir(root)
sys.path.insert(0, str(root))

try:
    from dotenv import load_dotenv

    load_dotenv(root / "agentic_rag" / ".env", override=False)
    load_dotenv(root / ".env", override=True)
except ImportError:
    pass


def main() -> int:
    from agentic_rag.pipeline.incremental_router import _iter_news_rows
    from agentic_rag.db.milvus_store import get_milvus_store

    store = get_milvus_store()
    assigned = 0
    unassigned = 0
    cids: Counter[int] = Counter()

    for r in _iter_news_rows(store._news_col, batch=3000):
        cid = int(r.get("cluster_id", -1))
        cids[cid] += 1
        if cid < 0:
            unassigned += 1
        else:
            assigned += 1

    total = assigned + unassigned
    pct = (100.0 * assigned / total) if total else 0.0
    sizes_pos = [cids[k] for k in cids if k >= 0]
    n_clusters = len(sizes_pos)
    singleton = sum(1 for s in sizes_pos if s == 1)
    multi = sum(1 for s in sizes_pos if s >= 2)

    print("news_vectors 实体总数:", total)
    print("已分配 cluster_id>=0:", assigned, f"({pct:.2f}%)")
    print("未分配 cluster_id=-1:", unassigned, f"({100.0 - pct:.2f}%)")
    print("不同簇数量(含孤立):", n_clusters)
    cent_n = store.count_centroids()
    print("cluster_centroids 质心条数:", cent_n)

    # 分布：单条簇 vs 多条簇
    print("簇规模: 仅 1 条新闻的簇数:", singleton)
    print("簇规模: >=2 条新闻的簇数:", multi)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
