"""阶段 44：PostgreSQL → Milvus 向量同步（灾难恢复路径，见 milvus_recovery_sync）。"""
from __future__ import annotations

import time

from agentic_rag.milvus_recovery_sync import sync_china_news_from_db_disaster_recovery


def run_stage44(limit: int, milvus_sync_all: bool) -> None:
    scope = "全部有标题新闻" if milvus_sync_all else "仅涉华新闻"
    print(
        f"[阶段44/Milvus] 灾难恢复同步（并行块 + orjson 反序列化）：{scope}，limit={limit}"
    )
    t0 = time.perf_counter()
    n = sync_china_news_from_db_disaster_recovery(
        limit=limit,
        china_only=not milvus_sync_all,
    )
    print(f"[阶段44/Milvus] 完成：自 DB 候选 {n} 条，耗时 {time.perf_counter() - t0:.1f}s")
