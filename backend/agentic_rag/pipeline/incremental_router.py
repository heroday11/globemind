"""
incremental_router.py  —  V2 实时增量路由 + 冷启动孤儿晋升

核心职责：
  1) route_news_batch()：优先匹配已有 centroid，剩余样本进入孤儿池
  2) _promote_orphans_greedy()：对孤儿做单遍贪心建簇（冷启动兜底）
  3) compute_and_store_centroids()：重算并写回 centroid 集合
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from agentic_rag.pipeline.joint_distance import compute_joint_distance

try:
    from config.settings import FrozenDefaults

    _ROUTE_TH_DEFAULT = str(FrozenDefaults.ROUTE_SIMILARITY_THRESHOLD)
except Exception:
    _ROUTE_TH_DEFAULT = "0.85"
SIMILARITY_THRESHOLD = float(os.getenv("ROUTE_THRESHOLD", _ROUTE_TH_DEFAULT))
UNASSIGNED_DB = Path(os.getenv("UNASSIGNED_DB", "./data/unassigned_pool.db"))


@dataclass
class RouteResult:
    news_id: int
    decision: str
    cluster_id: int
    score: float
    title: str = ""


@dataclass
class MicroClusterResult:
    n_input: int
    n_new_clusters: int
    n_noise: int
    new_cluster_ids: List[int] = field(default_factory=list)
    elapsed_s: float = 0.0


class UnassignedPool:
    def __init__(self, db_path: Path = UNASSIGNED_DB):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._init_table()

    def _init_table(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS unassigned (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                news_id INTEGER NOT NULL UNIQUE,
                embedding TEXT NOT NULL,
                title TEXT DEFAULT '',
                created_at INTEGER NOT NULL
            )
            """
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_created ON unassigned(created_at)")
        self._conn.commit()

    def add_batch(self, news_ids: List[int], embeddings: np.ndarray, titles: Optional[List[str]] = None) -> int:
        if titles is None:
            titles = [""] * len(news_ids)
        now = int(time.time())
        rows = [(nid, json.dumps(emb.tolist()), t, now) for nid, emb, t in zip(news_ids, embeddings, titles)]
        cur = self._conn.executemany(
            "INSERT OR IGNORE INTO unassigned(news_id, embedding, title, created_at) VALUES (?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()
        return cur.rowcount

    def fetch_all(self) -> Tuple[List[int], np.ndarray, List[str]]:
        rows = self._conn.execute("SELECT news_id, embedding, title FROM unassigned ORDER BY id").fetchall()
        if not rows:
            return [], np.zeros((0,), dtype=np.float32), []
        ids = [r[0] for r in rows]
        embs = np.array([json.loads(r[1]) for r in rows], dtype=np.float32)
        titles = [r[2] for r in rows]
        return ids, embs, titles

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM unassigned").fetchone()
        return int(row[0]) if row else 0

    def delete_by_ids(self, news_ids: List[int]) -> None:
        if not news_ids:
            return
        q = ",".join("?" * len(news_ids))
        self._conn.execute(f"DELETE FROM unassigned WHERE news_id IN ({q})", news_ids)
        self._conn.commit()


def _normalize(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    if n <= 1e-9:
        return v.astype(np.float32)
    return (v / n).astype(np.float32)


def _iter_news_rows(col, batch: int = 2000):
    # Milvus can report "collection not loaded" after reconnect/reset;
    # force load here to make iteration robust.
    try:
        col.load()
    except Exception:
        pass

    last_id = -1
    while True:
        try:
            rows = col.query(
                expr=f"news_id > {last_id}",
                output_fields=["news_id", "embedding", "timestamp", "cluster_id"],
                limit=batch,
            )
        except Exception as e:
            if "collection not loaded" in str(e).lower():
                col.load()
                rows = col.query(
                    expr=f"news_id > {last_id}",
                    output_fields=["news_id", "embedding", "timestamp", "cluster_id"],
                    limit=batch,
                )
            else:
                raise

        if not rows:
            break
        rows = sorted(rows, key=lambda r: r["news_id"])
        for r in rows:
            yield r
        last_id = int(rows[-1]["news_id"])
        if len(rows) < batch:
            break


def _next_cluster_id_from_milvus(store, base_offset: int = 10000) -> int:
    max_cid = -1
    for r in _iter_news_rows(store._news_col, batch=2000):
        cid = int(r.get("cluster_id", -1))
        if cid > max_cid:
            max_cid = cid
    return max(base_offset, max_cid + 1)


def compute_and_store_centroids(cluster_ids: Optional[List[int]] = None, batch_size: int = 2000) -> int:
    from agentic_rag.db.milvus_store import get_milvus_store

    store = get_milvus_store()
    sums: Dict[int, np.ndarray] = {}
    cnts: Dict[int, int] = {}

    allow = set(cluster_ids) if cluster_ids else None

    for r in _iter_news_rows(store._news_col, batch=batch_size):
        cid = int(r.get("cluster_id", -1))
        if cid < 0:
            continue
        if allow is not None and cid not in allow:
            continue
        emb = np.array(r["embedding"], dtype=np.float32)
        if cid not in sums:
            sums[cid] = emb.copy()
            cnts[cid] = 1
        else:
            sums[cid] += emb
            cnts[cid] += 1

    if not sums:
        return 0

    cids = sorted(sums.keys())
    centroids = np.stack([_normalize(sums[c] / cnts[c]) for c in cids], axis=0)
    sizes = [cnts[c] for c in cids]
    return store.upsert_centroids(cids, centroids, sizes)


def _promote_orphans_greedy(
    news_ids: List[int],
    embeddings: np.ndarray,
    titles: List[str],
    timestamps: List[int],
    max_joint_dist: float = 0.35,
) -> List[RouteResult]:
    """冷启动孤儿晋升：单遍贪心建簇 + 写入 Milvus + 写入 centroid + 清理孤儿池。"""
    if len(news_ids) == 0:
        return []

    from agentic_rag.db.milvus_store import get_milvus_store

    clusters: List[dict] = []
    for idx, (emb, ts) in enumerate(zip(embeddings, timestamps)):
        emb = _normalize(np.array(emb, dtype=np.float32))
        ts_i = int(ts) if ts else int(time.time())

        if not clusters:
            clusters.append({"center": emb, "ts_center": ts_i, "idx": [idx]})
            continue

        best_k = -1
        best_d = 1e9
        for k, c in enumerate(clusters):
            d = float(compute_joint_distance(emb, c["center"], ts_i, c["ts_center"]))
            if d < best_d:
                best_d = d
                best_k = k

        if best_k >= 0 and best_d <= max_joint_dist:
            c = clusters[best_k]
            c["idx"].append(idx)
            member_vecs = np.stack([embeddings[i] for i in c["idx"]], axis=0)
            c["center"] = _normalize(member_vecs.mean(axis=0))
            c["ts_center"] = int(sum(int(timestamps[i]) for i in c["idx"]) / len(c["idx"]))
        else:
            clusters.append({"center": emb, "ts_center": ts_i, "idx": [idx]})

    store = get_milvus_store()
    base_cid = _next_cluster_id_from_milvus(store)

    insert_ids: List[int] = []
    insert_ts: List[int] = []
    insert_embs: List[np.ndarray] = []
    insert_cids: List[int] = []

    centroid_ids: List[int] = []
    centroid_vecs: List[np.ndarray] = []
    centroid_sizes: List[int] = []

    promoted: List[RouteResult] = []

    for i, c in enumerate(clusters):
        cid = base_cid + i
        idxs = c["idx"]

        centroid_ids.append(cid)
        centroid_vecs.append(c["center"])
        centroid_sizes.append(len(idxs))

        for j in idxs:
            nid = int(news_ids[j])
            ts = int(timestamps[j]) if timestamps[j] else int(time.time())
            emb = _normalize(np.array(embeddings[j], dtype=np.float32))
            insert_ids.append(nid)
            insert_ts.append(ts)
            insert_embs.append(emb)
            insert_cids.append(cid)

            d = float(compute_joint_distance(emb, c["center"], ts, c["ts_center"]))
            promoted.append(RouteResult(
                news_id=nid,
                decision="assigned",
                cluster_id=cid,
                score=max(0.0, 1.0 - d),
                title=titles[j] if j < len(titles) else "",
            ))

    store.insert_news(
        news_ids=insert_ids,
        embeddings=np.stack(insert_embs),
        timestamps=insert_ts,
        cluster_ids=insert_cids,
        defer_flush=True,
    )
    store.upsert_centroids(
        cluster_ids=centroid_ids,
        centroids=np.stack(centroid_vecs),
        sizes=centroid_sizes,
        defer_flush=True,
    )

    pool = UnassignedPool()
    pool.delete_by_ids([r.news_id for r in promoted])

    print(f"[Promotion] Promoted {len(promoted)} orphan news into {len(clusters)} new clusters")
    return promoted


def route_news_batch(
    news_ids: List[int],
    embeddings: np.ndarray,
    titles: Optional[List[str]] = None,
    timestamps: Optional[List[int]] = None,
    threshold: float = SIMILARITY_THRESHOLD,
    enable_orphan_promotion: bool = True,
    promotion_max_joint_dist: float = 0.35,
    defer_store_flush: bool = False,
) -> List[RouteResult]:
    from agentic_rag.db.milvus_store import get_milvus_store

    store = get_milvus_store()
    pool = UnassignedPool()

    if titles is None:
        titles = [""] * len(news_ids)
    now = int(time.time())
    if timestamps is None:
        timestamps = [now] * len(news_ids)

    results: List[RouteResult] = []
    assigned_ids: List[int] = []
    assigned_embs: List[np.ndarray] = []
    assigned_cids: List[int] = []
    assigned_ts: List[int] = []

    unassigned_ids: List[int] = []
    unassigned_embs: List[np.ndarray] = []
    unassigned_titles: List[str] = []
    unassigned_ts: List[int] = []

    n_route = len(news_ids)
    step = max(500, n_route // 20)
    for idx, (nid, emb, title, ts) in enumerate(
        zip(news_ids, embeddings, titles, timestamps)
    ):
        if n_route > 800 and idx > 0 and idx % step == 0:
            print(f"[Router] 质心匹配进度 {idx}/{n_route} …", flush=True)
        emb = _normalize(np.array(emb, dtype=np.float32))
        ts_i = int(ts) if ts else now

        hits = store.search_nearest_centroid(emb, top_k=1)
        if hits and hits[0].score >= threshold:
            hit = hits[0]
            results.append(RouteResult(nid, "assigned", hit.cluster_id, float(hit.score), title))
            assigned_ids.append(int(nid))
            assigned_embs.append(emb)
            assigned_cids.append(int(hit.cluster_id))
            assigned_ts.append(ts_i)
        else:
            score = float(hits[0].score) if hits else 0.0
            results.append(RouteResult(int(nid), "unassigned", -1, score, title))
            unassigned_ids.append(int(nid))
            unassigned_embs.append(emb)
            unassigned_titles.append(title)
            unassigned_ts.append(ts_i)

    if assigned_ids:
        store.insert_news(
            assigned_ids,
            np.stack(assigned_embs),
            assigned_ts,
            assigned_cids,
            defer_flush=True,
        )
        print(f"[Router] Assigned {len(assigned_ids)} news to existing clusters")

    if unassigned_ids:
        pool.add_batch(unassigned_ids, np.stack(unassigned_embs), unassigned_titles)
        print(f"[Router] {len(unassigned_ids)} news → Unassigned Pool (total pool size={pool.count()})")

        if enable_orphan_promotion:
            promoted = _promote_orphans_greedy(
                news_ids=unassigned_ids,
                embeddings=np.stack(unassigned_embs),
                titles=unassigned_titles,
                timestamps=unassigned_ts,
                max_joint_dist=promotion_max_joint_dist,
            )
            promoted_map = {x.news_id: x for x in promoted}
            for i, r in enumerate(results):
                if r.news_id in promoted_map:
                    p = promoted_map[r.news_id]
                    results[i] = p

    if not defer_store_flush:
        store.flush_all()

    return results


def route_single_news(news_id: int, embedding: np.ndarray, title: str = "", threshold: float = SIMILARITY_THRESHOLD) -> RouteResult:
    return route_news_batch([news_id], embedding.reshape(1, -1), [title], threshold=threshold)[0]


def nightly_micro_cluster(
    min_cluster_size: int = 5,
    min_samples: int = 3,
    min_pool_size: int = 20,
    update_centroids: bool = True,
) -> MicroClusterResult:
    t0 = time.perf_counter()
    pool = UnassignedPool()
    n = pool.count()
    if n < min_pool_size:
        return MicroClusterResult(n_input=n, n_new_clusters=0, n_noise=n, elapsed_s=time.perf_counter() - t0)

    ids, embs, titles = pool.fetch_all()
    ts = [int(time.time())] * len(ids)
    promoted = _promote_orphans_greedy(ids, embs, titles, ts)
    new_ids = sorted({p.cluster_id for p in promoted})

    if update_centroids and new_ids:
        compute_and_store_centroids(cluster_ids=new_ids)

    return MicroClusterResult(
        n_input=len(ids),
        n_new_clusters=len(new_ids),
        n_noise=0,
        new_cluster_ids=new_ids,
        elapsed_s=time.perf_counter() - t0,
    )
