"""Milvus 向量存储基座

封装了：
  - Collection Schema 设计（news_id / embedding / timestamp / cluster_id）
  - IVF_SQ8 索引（标量量化，大幅降低内存消耗）
  - 批量写入 / 检索 / Centroid 集合管理

使用方式：
    from agentic_rag.db.milvus_store import get_milvus_store
    store = get_milvus_store()          # 连接默认 localhost:19530
    store.insert_news([...])            # 写入新闻向量
    store.search_similar(vec, top_k=5)  # 余弦相似度检索
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import List, Optional, Set

import numpy as np

# ─────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────
NEWS_COLLECTION   = "news_vectors"      # 新闻向量主表
CENTROID_COLLECTION = "cluster_centroids"  # 事件簇中心点表

DEFAULT_DIM      = 1024      # BGE-M3 原始向量维度
INDEX_TYPE       = "IVF_FLAT"          # 倒排文件索引（兼容 Milvus Lite / 服务端）
METRIC_TYPE      = "IP"                # 内积 = 余弦相似度（向量已归一化）
NLIST            = 128                 # IVF 聚类桶数
BATCH_SIZE       = 1000                # 批量写入大小（news_vectors）
# 质心单次 insert 过大会触发 gRPC RESOURCE_EXHAUSTED（默认 ~64MB）；可按需调小
CENTROID_UPSERT_CHUNK = int(os.getenv("MILVUS_CENTROID_UPSERT_CHUNK", "1500"))


@dataclass
class SearchResult:
    news_id: int
    score: float
    cluster_id: int
    timestamp: int


@dataclass
class CentroidSearchResult:
    cluster_id: int
    score: float


class MilvusNewsStore:
    """Milvus 新闻向量存储，支持新闻向量 + 事件簇中心点双集合管理。"""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 19530,
        dim: int = DEFAULT_DIM,
        uri: str | None = None,
    ):
        self.host = host
        self.port = port
        self.dim  = dim
        self.uri  = uri
        self._connect()

    # ─────────────────────────────────────────────
    # 连接 & 初始化
    # ─────────────────────────────────────────────
    def _connect(self) -> None:
        from pymilvus import connections
        from pymilvus.exceptions import MilvusException

        # ── URI mode (Milvus Lite local .db / HTTP server) ──
        if self.uri:
            print(f"[Milvus] Connecting via URI: {self.uri}", flush=True)
            connections.connect(alias="default", uri=self.uri, timeout=10)
            print(f"[Milvus] Connected via URI: {self.uri}", flush=True)
            self._ensure_news_collection()
            self._ensure_centroid_collection()
            return

        last_err = None
        host_candidates = [self.host]
        # Support comma-separated MILVUS_HOST values.
        if isinstance(self.host, str) and "," in self.host:
            host_candidates = [h.strip() for h in self.host.split(",") if h.strip()]
        # Common fallbacks for Docker/WSL/IPv4 differences.
        for h in ("127.0.0.1", "localhost", "host.docker.internal"):
            if h not in host_candidates:
                host_candidates.append(h)

        for host in host_candidates:
            for attempt in range(1, 4):
                try:
                    print(
                        f"[Milvus] Connecting {host}:{self.port} (attempt {attempt}/3, timeout=10s)…",
                        flush=True,
                    )
                    connections.connect(
                        alias="default",
                        host=host,
                        port=str(self.port),
                        timeout=10,
                    )
                    self.host = host
                    print(
                        f"[Milvus] Connected to {self.host}:{self.port}",
                        flush=True,
                    )
                    self._ensure_news_collection()
                    self._ensure_centroid_collection()
                    return
                except (MilvusException, TimeoutError, OSError) as e:
                    last_err = e
                    print(f"[Milvus] Connect failed (host={host}, attempt {attempt}/3): {e}")
                    if attempt < 3:
                        time.sleep(3)

        raise RuntimeError(
            "Milvus connection failed. "
            f"hosts tried={host_candidates}, port={self.port}, last_error={last_err}"
        )

    @property
    def news_vectors_collection(self):
        """已加载的 `news_vectors` 集合（供 build_macro_events 等只读使用）。"""
        return self._news_col

    def _ensure_news_collection(self) -> None:
        """创建新闻向量集合（若不存在）。"""
        from pymilvus import (
            Collection, CollectionSchema, FieldSchema, DataType,
            utility,
        )
        if utility.has_collection(NEWS_COLLECTION):
            self._news_col = Collection(NEWS_COLLECTION)
            n0 = self._news_col.num_entities
            print(
                f"[Milvus] Loading '{NEWS_COLLECTION}' into memory "
                f"({n0} entities) — can take several minutes on large sets, please wait…",
                flush=True,
            )
            self._news_col.load()
            print(
                f"[Milvus] Collection '{NEWS_COLLECTION}' loaded (entities={self._news_col.num_entities})",
                flush=True,
            )
            return

        fields = [
            FieldSchema(name="news_id",    dtype=DataType.INT64,
                        is_primary=True, auto_id=False,
                        description="PostgreSQL news.id"),
            FieldSchema(name="embedding",  dtype=DataType.FLOAT_VECTOR,
                        dim=self.dim,
                        description=f"UMAP {self.dim}-dim dense vector"),
            FieldSchema(name="timestamp",  dtype=DataType.INT64,
                        description="Unix epoch seconds of pub_time"),
            FieldSchema(name="cluster_id", dtype=DataType.INT64,
                        description="HDBSCAN cluster label (-1=unassigned)"),
        ]
        schema = CollectionSchema(
            fields=fields,
            description="News dense vectors for incremental event clustering",
        )
        col = Collection(name=NEWS_COLLECTION, schema=schema)
        print(f"[Milvus] Created collection '{NEWS_COLLECTION}'")

        # IVF_SQ8 索引（标量量化 + 倒排文件，极大节省内存）
        col.create_index(
            field_name="embedding",
            index_params={
                "index_type": INDEX_TYPE,
                "metric_type": METRIC_TYPE,
                "params": {"nlist": NLIST},
            },
        )
        print(f"[Milvus] Index created: {INDEX_TYPE} nlist={NLIST} metric={METRIC_TYPE}")
        col.load()
        self._news_col = col

    def _ensure_centroid_collection(self) -> None:
        """创建事件簇中心点集合（若不存在）。"""
        from pymilvus import (
            Collection, CollectionSchema, FieldSchema, DataType,
            utility,
        )
        if utility.has_collection(CENTROID_COLLECTION):
            self._centroid_col = Collection(CENTROID_COLLECTION)
            nc = self._centroid_col.num_entities
            print(
                f"[Milvus] Loading '{CENTROID_COLLECTION}' ({nc} entities)…",
                flush=True,
            )
            self._centroid_col.load()
            print(
                f"[Milvus] Collection '{CENTROID_COLLECTION}' loaded (entities={self._centroid_col.num_entities})",
                flush=True,
            )
            return

        fields = [
            FieldSchema(name="cluster_id",   dtype=DataType.INT64,
                        is_primary=True, auto_id=False,
                        description="Event cluster ID"),
            FieldSchema(name="centroid",      dtype=DataType.FLOAT_VECTOR,
                        dim=self.dim,
                        description=f"Mean vector of cluster, {self.dim}-dim"),
            FieldSchema(name="size",          dtype=DataType.INT64,
                        description="Number of articles in cluster"),
            FieldSchema(name="updated_at",    dtype=DataType.INT64,
                        description="Last update Unix epoch"),
        ]
        schema = CollectionSchema(
            fields=fields,
            description="Event cluster centroids for incremental routing",
        )
        col = Collection(name=CENTROID_COLLECTION, schema=schema)
        print(f"[Milvus] Created collection '{CENTROID_COLLECTION}'")

        col.create_index(
            field_name="centroid",
            index_params={
                "index_type": INDEX_TYPE,
                "metric_type": METRIC_TYPE,
                "params": {"nlist": min(NLIST, 64)},
            },
        )
        col.load()
        self._centroid_col = col

    # ─────────────────────────────────────────────
    # 写入接口
    # ─────────────────────────────────────────────
    def insert_news(
        self,
        news_ids:   List[int],
        embeddings: np.ndarray,
        timestamps: List[int],
        cluster_ids: Optional[List[int]] = None,
        *,
        defer_flush: bool = False,
    ) -> int:
        """
        批量写入新闻向量。

        Args:
            news_ids:    PostgreSQL news.id 列表
            embeddings:  shape (N, dim) float32 已归一化向量
            timestamps:  Unix epoch 列表
            cluster_ids: 聚类标签列表，默认全为 -1（未分配）

        Returns:
            实际写入条数
        """
        n = len(news_ids)
        if cluster_ids is None:
            cluster_ids = [-1] * n

        # 归一化（确保余弦相似度正确）
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        embeddings = embeddings / (norms + 1e-9)

        inserted = 0
        for start in range(0, n, BATCH_SIZE):
            end = start + BATCH_SIZE
            batch_data = [
                news_ids[start:end],
                embeddings[start:end].tolist(),
                timestamps[start:end],
                cluster_ids[start:end],
            ]
            self._news_col.insert(batch_data)
            inserted += len(news_ids[start:end])
            print(f"[Milvus] Inserted {inserted}/{n} news vectors")

        if not defer_flush:
            self._news_col.flush()
            print(f"[Milvus] Flush complete. Total inserted: {inserted}")
        return inserted

    def upsert_centroids(
        self,
        cluster_ids: List[int],
        centroids:   np.ndarray,
        sizes:       List[int],
        *,
        defer_flush: bool = False,
    ) -> int:
        """
        更新/写入事件簇中心点（先删后写以实现 upsert）。

        Args:
            cluster_ids: 事件簇 ID 列表
            centroids:   shape (K, dim) float32
            sizes:       每个簇的文章数量

        Returns:
            写入条数
        """
        # 归一化
        norms = np.linalg.norm(centroids, axis=1, keepdims=True)
        centroids = centroids / (norms + 1e-9)

        now = int(time.time())
        n = len(cluster_ids)
        chunk = max(100, min(CENTROID_UPSERT_CHUNK, n))
        inserted = 0
        for start in range(0, n, chunk):
            end = min(start + chunk, n)
            cids = cluster_ids[start:end]
            cvecs = centroids[start:end]
            szs = sizes[start:end]

            ids_str = ", ".join(str(c) for c in cids)
            expr = f"cluster_id in [{ids_str}]"
            try:
                self._centroid_col.delete(expr)
            except Exception:
                pass

            data = [
                cids,
                cvecs.tolist(),
                szs,
                [now] * len(cids),
            ]
            self._centroid_col.insert(data)
            inserted += len(cids)
            print(f"[Milvus] Upserted centroids {inserted}/{n}")

        if not defer_flush:
            self._centroid_col.flush()
            print(f"[Milvus] Upserted {len(cluster_ids)} centroids (done)")
        return len(cluster_ids)

    # ─────────────────────────────────────────────
    # 检索接口
    # ─────────────────────────────────────────────
    def search_similar_news(
        self,
        query_vec: np.ndarray,
        top_k: int = 10,
        cluster_id_filter: Optional[int] = None,
    ) -> List[SearchResult]:
        """
        在新闻集合中检索最相似向量。

        Args:
            query_vec:         shape (dim,) 已归一化向量
            top_k:             返回 Top-K 结果
            cluster_id_filter: 若指定，只在该簇内检索

        Returns:
            SearchResult 列表，按相似度降序排列
        """
        vec = query_vec / (np.linalg.norm(query_vec) + 1e-9)
        expr = f"cluster_id == {cluster_id_filter}" if cluster_id_filter is not None else ""

        results = self._news_col.search(
            data=[vec.tolist()],
            anns_field="embedding",
            param={"metric_type": METRIC_TYPE, "params": {"nprobe": 300}},
            limit=top_k,
            expr=expr or None,
            output_fields=["news_id", "cluster_id", "timestamp"],
        )
        out = []
        for hits in results:
            for hit in hits:
                out.append(SearchResult(
                    news_id    = hit.entity.get("news_id"),
                    score      = float(hit.score),
                    cluster_id = hit.entity.get("cluster_id"),
                    timestamp  = hit.entity.get("timestamp"),
                ))
        return out

    def search_nearest_centroid(
        self,
        query_vec: np.ndarray,
        top_k: int = 1,
    ) -> List[CentroidSearchResult]:
        """
        在 Centroid 集合中检索最近事件簇。

        Args:
            query_vec: shape (dim,) 已归一化向量
            top_k:     返回前 K 个候选簇

        Returns:
            CentroidSearchResult 列表，按相似度降序排列
        """
        vec = query_vec / (np.linalg.norm(query_vec) + 1e-9)

        results = self._centroid_col.search(
            data=[vec.tolist()],
            anns_field="centroid",
            param={"metric_type": METRIC_TYPE, "params": {"nprobe": 300}},
            limit=top_k,
            output_fields=["cluster_id", "size"],
        )
        out = []
        for hits in results:
            for hit in hits:
                out.append(CentroidSearchResult(
                    cluster_id = hit.entity.get("cluster_id"),
                    score      = float(hit.score),
                ))
        return out

    # ─────────────────────────────────────────────
    # 工具方法
    # ─────────────────────────────────────────────
    def news_ids_present(self, news_ids: List[int], *, chunk_size: int = 512) -> Set[int]:
        """返回已在 `news_vectors` 中出现的 news_id（主键幂等查询）。"""
        if not news_ids:
            return set()
        present: Set[int] = set()
        for i in range(0, len(news_ids), chunk_size):
            part = [int(x) for x in news_ids[i : i + chunk_size]]
            expr = "news_id in [" + ", ".join(str(x) for x in part) + "]"
            try:
                res = self._news_col.query(
                    expr=expr,
                    output_fields=["news_id"],
                    limit=len(part),
                )
            except Exception:
                res = []
            for row in res or []:
                present.add(int(row["news_id"]))
        return present

    def filter_new_news_ids(self, news_ids: List[int]) -> List[int]:
        """去掉 Milvus 已存在的主键，仅返回待插入 ID（顺序保留）。"""
        if not news_ids:
            return []
        have = self.news_ids_present(news_ids)
        return [int(n) for n in news_ids if int(n) not in have]

    def count_news(self) -> int:
        return self._news_col.num_entities

    def count_centroids(self) -> int:
        return self._centroid_col.num_entities

    def get_news_embeddings_by_cluster(
        self,
        cluster_id: int,
        limit: int = 10000,
    ) -> tuple[List[int], np.ndarray]:
        """
        按 cluster_id 批量取出新闻 ID 和向量，用于重算中心点。

        Returns:
            (news_ids, embeddings)  embeddings shape (N, dim)
        """
        expr = f"cluster_id == {cluster_id}"
        res  = self._news_col.query(
            expr=expr,
            output_fields=["news_id", "embedding"],
            limit=limit,
        )
        if not res:
            return [], np.zeros((0, self.dim), dtype=np.float32)
        ids  = [r["news_id"]   for r in res]
        vecs = np.array([r["embedding"] for r in res], dtype=np.float32)
        return ids, vecs

    def update_news_cluster(
        self,
        news_ids:    List[int],
        cluster_ids: List[int],
    ) -> None:
        """
        批量更新新闻的 cluster_id 字段。
        Milvus 不支持原地更新，实现为 delete + re-insert。
        """
        if not news_ids:
            return
        # 先查出原始向量和 timestamp
        ids_str = ", ".join(str(i) for i in news_ids)
        res = self._news_col.query(
            expr=f"news_id in [{ids_str}]",
            output_fields=["news_id", "embedding", "timestamp"],
        )
        if not res:
            return

        id_map  = {r["news_id"]: r for r in res}
        ordered_ids   = [r["news_id"]   for r in res]
        ordered_embs  = np.array([r["embedding"] for r in res], dtype=np.float32)
        ordered_ts    = [r["timestamp"] for r in res]
        new_cid_map   = dict(zip(news_ids, cluster_ids))
        ordered_cids  = [new_cid_map.get(nid, -1) for nid in ordered_ids]

        # 删除旧记录
        self._news_col.delete(f"news_id in [{ids_str}]")
        self._news_col.flush()

        # 重新插入（携带新 cluster_id）
        self.insert_news(ordered_ids, ordered_embs, ordered_ts, ordered_cids)

    def flush_all(self) -> None:
        """路由批末统一刷盘，减少频繁 flush 的 I/O。"""
        self._news_col.flush()
        self._centroid_col.flush()
        print("[Milvus] Flush complete (news_vectors + cluster_centroids).", flush=True)

    def drop_all(self) -> None:
        """删除所有 Milvus 集合（危险操作，仅用于测试重置）。"""
        from pymilvus import utility
        for name in [NEWS_COLLECTION, CENTROID_COLLECTION]:
            if utility.has_collection(name):
                utility.drop_collection(name)
                print(f"[Milvus] Dropped collection '{name}'")

    def get_all_embeddings(self) -> tuple[List[int], np.ndarray, List[int]]:
        """取出全部向量（用于 nightly HDBSCAN 微聚类）。"""
        n = self._news_col.num_entities
        if n == 0:
            return [], np.zeros((0, self.dim), dtype=np.float32), []
        res = self._news_col.query(
            expr="news_id >= 0",
            output_fields=["news_id", "embedding", "cluster_id"],
            limit=min(n + 100, 1000000),
        )
        if not res:
            return [], np.zeros((0, self.dim), dtype=np.float32), []
        ids   = [r["news_id"]   for r in res]
        embs  = np.array([r["embedding"]  for r in res], dtype=np.float32)
        cids  = [r["cluster_id"] for r in res]
        return ids, embs, cids

    def close(self) -> None:
        """Release collections and disconnect Milvus alias."""
        from pymilvus import connections, utility

        # drop_all() 之后集合已不存在；再 release 会触发 Milvus 侧 ERROR 日志（即使被 try 捕获）
        try:
            if utility.has_collection(NEWS_COLLECTION):
                self._news_col.release()
        except Exception:
            pass
        try:
            if utility.has_collection(CENTROID_COLLECTION):
                self._centroid_col.release()
        except Exception:
            pass
        try:
            connections.disconnect("default")
        except Exception:
            pass


# ─────────────────────────────────────────────
# 模块级单例
# ─────────────────────────────────────────────
_store: MilvusNewsStore | None = None


def reset_milvus_store_singleton() -> None:
    """断开 Milvus 后调用，避免单例持有失效连接（可选）。"""
    global _store
    _store = None


def get_milvus_store(
    host: str | None = None,
    port: int | None = None,
    dim: int = DEFAULT_DIM,
    uri: str | None = None,
) -> MilvusNewsStore:
    """返回模块级单例，首次调用时初始化连接。

    连接优先级：
      1. 显式传入的 ``uri`` 参数
      2. ``GLOBEMIND_MILVUS_URI`` 环境变量
      3. ``config.settings.Settings.milvus_uri``（Pydantic .env 回退）
      4. ``MILVUS_HOST`` / ``MILVUS_PORT`` 环境变量 → 客户端-服务器模式
      5. 默认 fallback ``localhost:19530``
    """
    global _store
    if _store is None:
        resolved_uri = uri or os.getenv("GLOBEMIND_MILVUS_URI") or os.getenv("MILVUS_URI")
        if not resolved_uri:
            try:
                from config.settings import get_settings
                resolved_uri = get_settings().milvus_uri
            except Exception:
                pass
        if resolved_uri:
            _store = MilvusNewsStore(uri=resolved_uri, dim=dim)
        else:
            _host = host or os.getenv("MILVUS_HOST", "localhost")
            _port = port or int(os.getenv("MILVUS_PORT", "19530"))
            _store = MilvusNewsStore(host=_host, port=_port, dim=dim)
    return _store


def connect_milvus_collection():
    """返回已加载的 ``news_vectors`` pymilvus Collection（供 data_fetcher 等只读工具用）。

    复用模块级 ``get_milvus_store`` 单例的连接，避免重复初始化。
    """
    return get_milvus_store().news_vectors_collection
