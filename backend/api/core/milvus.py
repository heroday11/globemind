"""Milvus 连接单例（与 agentic_rag.db.milvus_store 对齐，进程内只初始化一次）。"""
from __future__ import annotations

from typing import Any, Optional

_store: Optional[Any] = None


def get_milvus_store_singleton():
    """返回 pymilvus 集合封装实例；首次调用时加载。"""
    global _store
    if _store is None:
        from agentic_rag.db.milvus_store import get_milvus_store

        _store = get_milvus_store()
    return _store
