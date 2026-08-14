"""
将 event coref 聚类结果加载到 PostgreSQL event_coref 表中。

支持两种模式：
  1. 从 JSON checkpoint 文件加载（原流程）
  2. 运行 embedding-enhanced 聚类（在线计算，--use-embeddings）

用法:
    python -m agentic_rag.pipeline.event_coref_loader                          # checkpoint 加载
    python -m agentic_rag.pipeline.event_coref_loader --use-embeddings          # embedding 聚类
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from agentic_rag.db_runtime_config import require_database_password

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

# Import canonical entity normalizer so cluster-level entities are stored
# in canonical form, allowing Layer 2 to match on consistent entity pairs.
try:
    from core_pipeline.event_coref_cluster import _canonical_entity
except ImportError:
    _canonical_entity = None


def _parse_ts(val: Any) -> datetime | None:
    """Parse a datetime/timestamp/string into a timezone-aware datetime."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.replace(tzinfo=timezone.utc) if val.tzinfo is None else val
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return None
        for fmt in (
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%Y/%m/%d",
        ):
            try:
                dt = datetime.strptime(val, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def _mode(values: list[Any]) -> Any:
    """Return the most common value, or None for empty list."""
    if not values:
        return None
    return Counter(v for v in values if v is not None).most_common(1)[0][0]


def _consistent(values: list[Any]) -> bool:
    """Return True if all non-None values are the same (or there's <= 1 unique)."""
    uniq = set(str(v) for v in values if v is not None)
    return len(uniq) <= 1


# ── Shared DB writer (used by both checkpoint loader and embedding clustering) ──


def _build_cluster_records(
    cluster_map: dict[str, list[int]],
    checkpoint: dict[int, dict[str, Any]],
    cluster_titles: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]]]:
    """Build cluster-level and member-level records from raw cluster mapping.

    Args:
        cluster_map: {cluster_id: [article_id, ...]}
        checkpoint: {article_id: event_data_dict}
        cluster_titles: Optional {cluster_id: title} from LLM naming.

    Returns (clusters_data, members_data) suitable for _write_clusters_to_db.
    """
    clusters_data: list[dict[str, Any]] = []
    members_data: list[list[dict[str, Any]]] = []

    for cid, aids in cluster_map.items():
        ev_types: list[str] = []
        initiators: list[str] = []
        targets: list[str] = []
        triggers: list[str] = []
        published_dates: list[datetime] = []
        members: list[dict[str, Any]] = []

        for aid in aids:
            ev = checkpoint.get(aid, {})
            et = ev.get("event_type")
            ini = ev.get("initiator")
            tgt = ev.get("target")
            trig = ev.get("trigger")
            pub = _parse_ts(ev.get("published_at"))

            if et:
                ev_types.append(et)
            if ini:
                initiators.append(ini)
            if tgt:
                targets.append(tgt)
            if trig:
                triggers.append(trig)
            if pub:
                published_dates.append(pub)

            members.append({
                "cluster_id": cid,
                "news_id": aid,
                "event_type": et,
                "initiator": ini,
                "target": tgt,
                "trigger": trig,
                "published_at": pub.isoformat() if pub else None,
            })

        dominant_type = _mode(ev_types)
        dominant_trigger = _mode(triggers)

        same_type = _consistent(ev_types) if ev_types else True
        same_entities = (_consistent(initiators) and _consistent(targets)) if initiators and targets else True
        if not same_type:
            quality = "mixed_types"
        elif not same_entities:
            quality = "mixed_entities"
        else:
            quality = "consistent"

        start_dt = min(published_dates).date() if published_dates else None
        end_dt = max(published_dates).date() if published_dates else None

        canon_initiator = initiators[0] if _consistent(initiators) and initiators else None
        canon_target = targets[0] if _consistent(targets) and targets else None

        # Canonicalize for cluster-level entity consistency.
        # Raw entity strings may differ ("Trump" vs "Donald Trump" vs "US") but
        # normalize to the same canonical form ("united states"). Store canonical
        # values so Layer 2 (micro-story) can match on consistent entity pairs.
        if _canonical_entity is not None and initiators:
            canon_inis = [_canonical_entity(i) for i in initiators if i]
            canon_inis = [c for c in canon_inis if c]
            if canon_inis and _consistent(canon_inis):
                canon_initiator = canon_inis[0]
        if _canonical_entity is not None and targets:
            canon_tgts = [_canonical_entity(t) for t in targets if t]
            canon_tgts = [c for c in canon_tgts if c]
            if canon_tgts and _consistent(canon_tgts):
                canon_target = canon_tgts[0]

        clusters_data.append({
            "cluster_id": cid,
            "article_count": len(aids),
            "event_type": dominant_type,
            "initiator": canon_initiator,
            "target": canon_target,
            "dominant_trigger": dominant_trigger,
            "start_date": str(start_dt) if start_dt else None,
            "end_date": str(end_dt) if end_dt else None,
            "cluster_quality": quality,
            "title": (cluster_titles or {}).get(cid, ""),
        })
        members_data.append(members)

    return clusters_data, members_data


def _write_clusters_to_db(
    clusters_data: list[dict[str, Any]],
    members_data: list[list[dict[str, Any]]],
    batch_size: int = 5000,
) -> tuple[int, int]:
    """Batch INSERT cluster + member records into PostgreSQL.

    Clears existing data first (idempotent).

    Returns (n_clusters, n_members) written.
    """
    import psycopg2

    host = os.getenv("PG_HOST", "127.0.0.1")
    port = int(os.getenv("PG_PORT", "5432"))
    dbname = "globemind_news"
    user = os.getenv("PG_WRITE_USER", "postgres")
    password = require_database_password()

    conn = psycopg2.connect(
        host=host, port=port, dbname=dbname, user=user, password=password, connect_timeout=15,
    )
    conn.autocommit = False
    n_clusters = 0
    n_members = 0

    try:
        cur = conn.cursor()

        cur.execute("DELETE FROM event_coref_members")
        cur.execute("DELETE FROM event_coref_clusters")

        cluster_sql = """
            INSERT INTO event_coref_clusters
                (cluster_id, article_count, event_type, initiator, target,
                 dominant_trigger, start_date, end_date, cluster_quality, title)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        for i in range(0, len(clusters_data), batch_size):
            batch = clusters_data[i : i + batch_size]
            rows = [(
                c["cluster_id"], c["article_count"],
                c["event_type"], c["initiator"], c["target"],
                c["dominant_trigger"], c["start_date"], c["end_date"],
                c["cluster_quality"], c.get("title", ""),
            ) for c in batch]
            cur.executemany(cluster_sql, rows)
            n_clusters += len(rows)
            conn.commit()

        member_sql = """
            INSERT INTO event_coref_members
                (cluster_id, news_id, event_type, initiator, target, trigger, published_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        for batch in members_data:
            for i in range(0, len(batch), batch_size):
                part = batch[i : i + batch_size]
                rows = [(
                    m["cluster_id"], m["news_id"],
                    m["event_type"], m["initiator"], m["target"],
                    m["trigger"], m["published_at"],
                ) for m in part]
                cur.executemany(member_sql, rows)
                n_members += len(rows)
                conn.commit()

        cur.close()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return n_clusters, n_members


# ── Mode 1: Load from checkpoint files ──


def load_event_coref_from_checkpoint(
    mapping_path: str = "/root/data/globemind/data/event_coref_article_cluster_v13_100k.jsonl",
    checkpoint_path: str = "/root/data/globemind/data/event_extract_v11_checkpoint.jsonl",
    batch_size: int = 5000,
) -> tuple[int, int]:
    """Load article→cluster mapping + event data from JSON checkpoint files.

    Returns (n_clusters, n_members) written.
    """
    from agentic_rag.db.event_coref_schema import ensure_event_coref_tables

    ensure_event_coref_tables()

    # Step 1: Load checkpoint (article_id → event data)
    t0 = time.perf_counter()
    checkpoint: dict[int, dict[str, Any]] = {}
    with open(checkpoint_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            aid = int(d["article_id"])
            ev = d.get("event") or {}
            published_at = d.get("published_at")
            checkpoint[aid] = {
                "event_type": ev.get("event_type"),
                "initiator": ev.get("initiator"),
                "target": ev.get("target"),
                "trigger": ev.get("trigger"),
                "published_at": published_at,
            }
    t1 = time.perf_counter()
    print(f"[Loader] 加载提取 checkpoint: {len(checkpoint)} 条 ({t1-t0:.1f}s)", flush=True)

    # Step 2: Load mapping, group by cluster_id
    cluster_map: dict[str, list[int]] = {}
    with open(mapping_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            cid = d["cluster_id"]
            aid = int(d["article_id"])
            cluster_map.setdefault(cid, []).append(aid)
    t2 = time.perf_counter()
    print(
        f"[Loader] 加载映射文件: {sum(len(v) for v in cluster_map.values())} 映射, "
        f"{len(cluster_map)} 簇 ({t2-t1:.1f}s)",
        flush=True,
    )

    # Step 3: Build cluster records
    clusters_data, members_data = _build_cluster_records(cluster_map, checkpoint)
    t3 = time.perf_counter()
    print(f"[Loader] 构建簇元数据: {len(clusters_data)} 簇 ({t3-t2:.1f}s)", flush=True)

    # Step 4: Batch INSERT
    n_clusters, n_members = _write_clusters_to_db(clusters_data, members_data, batch_size)
    t4 = time.perf_counter()
    print(
        f"[Loader] 写入 DB 完成: {n_clusters} 簇, {n_members} 成员 ({t4-t3:.1f}s)",
        flush=True,
    )
    return (n_clusters, n_members)


# ── Mode 2: Run embedding-enhanced clustering ──


def run_event_coref_clustering(
    checkpoint_path: str = "/root/data/globemind/data/event_extract_v11_checkpoint.jsonl",
    mapping_output_path: str = "/root/data/globemind/data/event_coref_article_cluster_v13_100k.jsonl",
    batch_size: int = 5000,
) -> tuple[int, int]:
    """Run embedding-enhanced event coref clustering from DB data.

    Loads event extraction data (from checkpoint or DB) + BGE-M3 embeddings
    (from DB), computes semantic nearest-neighbor index, runs
    ``build_event_coreference_with_embeddings()``, writes results to PostgreSQL
    **and** saves cluster mapping JSONL for future runs.

    Returns (n_clusters, n_members) written.
    """
    from core_pipeline.event_extract_v11 import DOMAIN_MAP, Event, ExtractionResult
    from core_pipeline.event_coref_cluster import (
        build_event_coreference_with_embeddings,
    )

    from agentic_rag.db.event_coref_schema import ensure_event_coref_tables

    ensure_event_coref_tables()

    t0 = time.perf_counter()

    # ── Step 1: Load event data (checkpoint file or fallback to DB) ──
    import psycopg2

    host = os.getenv("PG_HOST", "127.0.0.1")
    port = int(os.getenv("PG_PORT", "5432"))
    dbname = "globemind_news"
    user = os.getenv("PG_WRITE_USER", "postgres")
    password = require_database_password()

    checkpoint: dict[int, dict[str, Any]] = {}
    if os.path.isfile(checkpoint_path):
        with open(checkpoint_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                aid = int(d["article_id"])
                ev = d.get("event") or {}
                published_at = d.get("published_at")
                checkpoint[aid] = {
                    "event_type": ev.get("event_type"),
                    "initiator": ev.get("initiator"),
                    "target": ev.get("target"),
                    "trigger": ev.get("trigger"),
                    "published_at": published_at,
                }
        t1 = time.perf_counter()
        print(f"[EmbedCluster] 加载提取 checkpoint: {len(checkpoint)} 条 ({t1-t0:.1f}s)", flush=True)
    else:
        # Fallback: load from event_coref_members (already populated)
        conn = psycopg2.connect(host=host, port=port, dbname=dbname, user=user, password=password, connect_timeout=15)
        cur = conn.cursor()
        cur.execute("""
            SELECT ecm.news_id, ecm.event_type, ecm.initiator, ecm.target,
                   ecm.trigger, ecm.published_at::text
            FROM event_coref_members ecm
            ORDER BY ecm.news_id
        """)
        for row in cur.fetchall():
            aid, et, ini, tgt, trig, pub_at = row
            checkpoint[aid] = {
                "event_type": et,
                "initiator": ini,
                "target": tgt,
                "trigger": trig,
                "published_at": pub_at,
            }
        cur.close()
        conn.close()
        t1 = time.perf_counter()
        print(f"[EmbedCluster] 从 event_coref_members 加载: {len(checkpoint)} 条 ({t1-t0:.1f}s)", flush=True)

    # ── Step 2: Load body/title from DB for quality filtering ──
    conn = psycopg2.connect(host=host, port=port, dbname=dbname, user=user, password=password, connect_timeout=15)
    cur = conn.cursor()

    article_ids = list(checkpoint.keys())
    print(f"[EmbedCluster] 加载 {len(article_ids)} 篇文章的正文和标题 ...", flush=True)

    bodies: dict[int, str] = {}
    for i in range(0, len(article_ids), 5000):
        batch_ids = article_ids[i : i + 5000]
        placeholders = ",".join("%s" for _ in batch_ids)
        cur.execute(
            f"SELECT id, body, title FROM news WHERE id IN ({placeholders})",
            batch_ids,
        )
        for row in cur.fetchall():
            nid, body, title = row
            bodies[nid] = (body or "") + "\n" + (title or "")

    t2 = time.perf_counter()
    print(f"[EmbedCluster] 正文加载完成: {len(bodies)} 篇 ({t2-t1:.1f}s)", flush=True)

    # Build ExtractionResult list
    results: list[ExtractionResult] = []
    for aid in article_ids:
        c = checkpoint[aid]
        et = c.get("event_type") or "other"
        event = Event(
            domain=DOMAIN_MAP.get(et, "geopolitical"),
            event_type=et,
            initiator=c.get("initiator"),
            target=c.get("target"),
        )
        results.append(ExtractionResult(
            article_id=aid,
            published_at=c.get("published_at"),
            event=event,
            raw_response="",
            parse_success=True,
        ))

    # ── Step 3: Load embeddings for these article IDs ──
    import numpy as np

    t3 = time.perf_counter()
    article_id_set = set(article_ids)
    embeddings: dict[int, Any] = {}
    for i in range(0, len(article_ids), 5000):
        batch_ids = article_ids[i : i + 5000]
        placeholders = ",".join("%s" for _ in batch_ids)
        cur.execute(
            f"SELECT news_id, embedding FROM news_embeddings WHERE news_id IN ({placeholders})",
            batch_ids,
        )
        for row in cur.fetchall():
            nid, emb_raw = row
            nid = int(nid)
            if nid not in article_id_set:
                continue
            if isinstance(emb_raw, memoryview):
                emb_raw = bytes(emb_raw)
            if isinstance(emb_raw, bytes):
                emb_raw = json.loads(emb_raw.decode())
            embeddings[nid] = np.array(emb_raw, dtype=np.float32)
    cur.close()
    conn.close()
    print(f"[EmbedCluster] 嵌入加载: {len(embeddings)} 条 ({time.perf_counter()-t3:.1f}s)", flush=True)

    # ── Step 3b: Load entity alias map (pre-computed from embedding analysis) ──
    alias_path = Path(__file__).resolve().parent / "entity_alias.json"
    if alias_path.exists():
        from core_pipeline.event_coref_cluster import load_entity_aliases
        load_entity_aliases(str(alias_path))

    # ── Step 4: Run clustering (FAISS NN index built internally) ──
    t4 = time.perf_counter()
    clusters = build_event_coreference_with_embeddings(
        results, article_bodies=bodies, embeddings=embeddings,
    )
    print(f"[EmbedCluster] 聚类完成: {len(clusters)} 簇 ({time.perf_counter()-t4:.1f}s)", flush=True)

    # ── Step 6: Save cluster mapping JSONL ──
    mapping_lines: list[str] = []
    for cid, aids in clusters.items():
        for aid in aids:
            mapping_lines.append(json.dumps({"cluster_id": cid, "article_id": aid}))
    with open(mapping_output_path, "w") as f:
        f.write("\n".join(mapping_lines))
    os.chmod(mapping_output_path, 0o644)
    print(f"[EmbedCluster] 映射文件已保存: {mapping_output_path} ({len(mapping_lines)} 条)", flush=True)

    # ── Step 7: Build records + write to DB ──
    clusters_data, members_data = _build_cluster_records(clusters, checkpoint)
    t5 = time.perf_counter()
    print(f"[EmbedCluster] 构建簇元数据: {len(clusters_data)} 簇 ({t5-t4:.1f}s)", flush=True)

    n_clusters, n_members = _write_clusters_to_db(clusters_data, members_data, batch_size)
    t6 = time.perf_counter()
    print(
        f"[EmbedCluster] 写入 DB 完成: {n_clusters} 簇, {n_members} 成员 ({t6-t5:.1f}s)",
        flush=True,
    )
    print(
        f"[EmbedCluster] 总耗时: {t6-t0:.1f}s",
        flush=True,
    )
    return (n_clusters, n_members)


if __name__ == "__main__":
    try:
        from pathlib import Path
        from dotenv import load_dotenv

        _agentic = Path(__file__).resolve().parent.parent
        load_dotenv(_agentic / ".env", override=False)
        load_dotenv(_agentic.parent / ".env", override=False)
    except ImportError:
        pass

    use_embeddings = "--use-embeddings" in sys.argv

    t0 = time.perf_counter()
    if use_embeddings:
        nc, nm = run_event_coref_clustering()
    else:
        nc, nm = load_event_coref_from_checkpoint()
    print(f"[event_coref_loader] 完成: {nc} clusters, {nm} members ({time.perf_counter()-t0:.1f}s)", flush=True)
