#!/usr/bin/env python3
"""
事件级聚类管线（v2 — 严格阈值 + 话题先验）

架构:
  1. 加载 checkpoint → 文章正文 → BGE-M3 embeddings
  2. 关键词图 Louvain → 话题分割
  3. 逐话题运行 L1 事件共指聚类
     - 阈值: 同实体对 0.85 / 部分实体 0.90 / 无实体 0.92
     - 时间窗口: 外交/贸易 7天 / 军事/抗议 3天
  4. 写入 DB + 保存映射文件

用法:
    PYTHONDONTWRITEBYTECODE=1 python -B -m scripts.run_event_level_pipeline
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import psycopg2

from core_pipeline.entity_normalizer import entity_pair_key
from core_pipeline.event_coref_cluster import (
    build_event_coreference_with_embeddings,
    load_entity_aliases,
)
from core_pipeline.event_extract_v11 import Event, ExtractionResult
from core_pipeline.topic_clustering import (
    cluster_topics,
    validate_document_format,
)
from scripts.db_runtime_config import require_database_password

_REPO = Path(__file__).resolve().parent.parent

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("event_level_pipeline")

CHECKPOINT_V13 = _REPO / "data" / "checkpoint_v13_all.jsonl"
CHECKPOINT_V12 = _REPO / "data" / "checkpoint_v12_geopolitical.jsonl"
CHECKPOINT_V11 = _REPO / "data" / "checkpoint_v11_240k.jsonl"
# 优先使用 v13（完整数据集），其次 v12（仅地缘），最后 v11（旧版）
CHECKPOINT = CHECKPOINT_V13 if CHECKPOINT_V13.exists() else (CHECKPOINT_V12 if CHECKPOINT_V12.exists() else CHECKPOINT_V11)
MAPPING_OUT = _REPO / "data" / "event_coref_mapping_layer1.jsonl"
ALIAS_PATH = _REPO / "backend" / "agentic_rag" / "pipeline" / "entity_alias.json"

def _connect_database():
    """Resolve database credentials only for an explicitly executed database operation."""
    try:
        from dotenv import load_dotenv

        load_dotenv(_REPO / "backend" / "agentic_rag" / ".env", override=False)
        load_dotenv(_REPO / ".env", override=False)
    except ImportError:
        pass

    write_user = os.getenv("PG_WRITE_USER", "").strip()
    if not write_user:
        raise RuntimeError("PG_WRITE_USER is required for the event-level pipeline")
    return psycopg2.connect(
        host=os.getenv("PG_HOST", "127.0.0.1"),
        port=int(os.getenv("PG_PORT", "5432")),
        dbname=os.getenv("PG_DATABASE", "globemind_news"),
        user=write_user,
        password=require_database_password(),
        connect_timeout=15,
    )


@dataclass
class _ClusterSummary:
    cluster_id: str
    article_ids: list[int]
    topic_id: str
    size: int
    event_type: str
    entity_pair: str
    centroid: np.ndarray
    start_dt: datetime | None
    end_dt: datetime | None


def _parse_dt(value: object) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        try:
            return datetime.fromisoformat(text.replace(" ", "T"))
        except ValueError:
            return None


def _cluster_topic_id(cluster_id: str) -> str:
    if "_" not in cluster_id:
        return "default"
    return cluster_id.split("_", 1)[0]


def _cluster_sort_key(summary: _ClusterSummary) -> tuple[int, int, str]:
    return (-summary.size, min(summary.article_ids), summary.cluster_id)


def _cosine(vec1: np.ndarray, vec2: np.ndarray) -> float:
    denom = float(np.linalg.norm(vec1) * np.linalg.norm(vec2))
    if denom <= 1e-9:
        return 0.0
    return float(np.dot(vec1, vec2) / denom)


def _days_between(left: _ClusterSummary, right: _ClusterSummary) -> int:
    if left.start_dt is None or left.end_dt is None or right.start_dt is None or right.end_dt is None:
        return 999
    if left.end_dt < right.start_dt:
        return (right.start_dt - left.end_dt).days
    if right.end_dt < left.start_dt:
        return (left.start_dt - right.end_dt).days
    return 0


def _span_days(summary: _ClusterSummary) -> int:
    if summary.start_dt is None or summary.end_dt is None:
        return 999
    return (summary.end_dt - summary.start_dt).days


def _build_cluster_summaries(
    clusters: dict[str, list[int]],
    results: list[ExtractionResult],
    embeddings: dict[int, np.ndarray],
    article_titles: dict[int, str],
) -> list[_ClusterSummary]:
    lookup = {r.article_id: r for r in results}
    summaries: list[_ClusterSummary] = []
    for cluster_id, article_ids in clusters.items():
        vectors = [embeddings[aid] for aid in article_ids if aid in embeddings]
        if not vectors:
            continue
        result_rows = [lookup[aid] for aid in article_ids if aid in lookup]
        event_types = Counter(
            r.event.event_type for r in result_rows if r.event and r.event.event_type
        )
        entity_pairs = Counter(
            entity_pair_key(r.event.initiator or "", r.event.target or "")
            for r in result_rows if r.event
        )
        dts = [_parse_dt(r.published_at) for r in result_rows if r.published_at]
        dts = [dt for dt in dts if dt is not None]
        centroid = np.mean(np.stack(vectors).astype(np.float32), axis=0)
        summaries.append(
            _ClusterSummary(
                cluster_id=cluster_id,
                article_ids=list(article_ids),
                topic_id=_cluster_topic_id(cluster_id),
                size=len(article_ids),
                event_type=event_types.most_common(1)[0][0] if event_types else "other",
                entity_pair=entity_pairs.most_common(1)[0][0] if entity_pairs else "→",
                centroid=centroid,
                start_dt=min(dts) if dts else None,
                end_dt=max(dts) if dts else None,
            )
        )
    return summaries


def rescue_cross_topic_splits(
    clusters: dict[str, list[int]],
    results: list[ExtractionResult],
    embeddings: dict[int, np.ndarray],
    article_titles: dict[int, str] | None = None,
    *,
    max_cluster_size: int = 1,
    max_gap_days: int = 0,
    min_cosine: float = 0.94,
    max_target_span_days: int = 1,
    max_merged_span_days: int = 1,
) -> tuple[dict[str, list[int]], int]:
    """Merge only duplicate-like tiny clusters split by topic pre-partitioning."""
    summaries = _build_cluster_summaries(clusters, results, embeddings, article_titles or {})
    if not summaries:
        return clusters, 0

    buckets: dict[tuple[str, str], list[_ClusterSummary]] = defaultdict(list)
    for summary in summaries:
        if summary.entity_pair == "→":
            continue
        buckets[(summary.entity_pair, summary.event_type)].append(summary)

    active = {summary.cluster_id: list(summary.article_ids) for summary in summaries}
    merge_count = 0

    for bucket in buckets.values():
        bucket.sort(key=_cluster_sort_key)
        for summary in bucket:
            if summary.cluster_id not in active or len(active[summary.cluster_id]) > max_cluster_size:
                continue

            best_target: _ClusterSummary | None = None
            best_score = min_cosine
            for candidate in bucket:
                if candidate.cluster_id == summary.cluster_id or candidate.cluster_id not in active:
                    continue
                if candidate.topic_id == summary.topic_id:
                    continue
                if len(active[candidate.cluster_id]) < len(active[summary.cluster_id]):
                    continue
                if _span_days(candidate) > max_target_span_days:
                    continue
                gap_days = _days_between(summary, candidate)
                if gap_days > max_gap_days:
                    continue
                if (
                    summary.start_dt is None or summary.end_dt is None
                    or candidate.start_dt is None or candidate.end_dt is None
                ):
                    continue
                merged_span_days = (
                    max(summary.end_dt, candidate.end_dt) - min(summary.start_dt, candidate.start_dt)
                ).days
                if merged_span_days > max_merged_span_days:
                    continue
                score = _cosine(summary.centroid, candidate.centroid)
                if score < best_score:
                    continue
                best_score = score
                best_target = candidate

            if best_target is None:
                continue

            target_ids = active.get(best_target.cluster_id)
            source_ids = active.pop(summary.cluster_id, None)
            if not target_ids or not source_ids:
                continue
            target_ids.extend(source_ids)
            active[best_target.cluster_id] = sorted(set(target_ids))
            merge_count += 1

    return active, merge_count


def load_checkpoint(path: str) -> list[ExtractionResult]:
    seen: dict[int, ExtractionResult] = {}
    with open(path) as f:
        for line in f:
            d = json.loads(line)
            ev = d.get("event")
            if not ev or ev.get("domain") != "geopolitical":
                continue
            seen[d["article_id"]] = ExtractionResult(
                article_id=d["article_id"],
                published_at=d.get("published_at"),
                event=Event(**ev) if ev else None,
                raw_response="",
                parse_success=True,
            )
    return list(seen.values())


def load_article_content(results: list[ExtractionResult]) -> tuple[dict[int, str], dict[int, str]]:
    conn = _connect_database()
    cur = conn.cursor()
    article_ids = [r.article_id for r in results]
    bodies: dict[int, str] = {}
    titles: dict[int, str] = {}
    for start in range(0, len(article_ids), 5000):
        batch = article_ids[start:start + 5000]
        cur.execute(
            "SELECT id, COALESCE(title,''), COALESCE(body,'') FROM news WHERE id = ANY(%s)",
            (batch,),
        )
        for row in cur.fetchall():
            article_id = int(row[0])
            title = str(row[1] or "")
            body = str(row[2] or "")
            titles[article_id] = title
            bodies[article_id] = f"{title} {body}".strip()
    cur.close()
    conn.close()
    return bodies, titles


def load_embeddings(article_ids: set[int]) -> dict[int, np.ndarray]:
    conn = _connect_database()
    cur = conn.cursor()
    cur.execute("SELECT news_id, embedding FROM news_embeddings WHERE model IN ('bge-m3','BAAI/bge-m3')")
    embs: dict[int, np.ndarray] = {}
    for nid, raw in cur.fetchall():
        if nid not in article_ids:
            continue
        if isinstance(raw, memoryview):
            raw = bytes(raw)
        if isinstance(raw, bytes):
            raw = json.loads(raw.decode())
        if isinstance(raw, str):
            raw = json.loads(raw)
        embs[int(nid)] = np.array(raw, dtype=np.float32)
    cur.close()
    conn.close()
    return embs


def write_to_db(clusters: dict[str, list[int]], results: list[ExtractionResult]) -> None:
    lookup = {r.article_id: r for r in results}
    conn = _connect_database()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("TRUNCATE event_coref_members CASCADE")
    cur.execute("TRUNCATE event_coref_clusters CASCADE")
    for cid, aids in clusters.items():
        ev_types, inits, tgts, dates = [], [], [], []
        for aid in aids:
            r = lookup.get(aid)
            if r and r.event:
                ev_types.append(r.event.event_type)
                inits.append(r.event.initiator or "?")
                tgts.append(r.event.target or "?")
                if r.published_at:
                    try:
                        from datetime import datetime
                        dt = datetime.fromisoformat(str(r.published_at).replace("Z","+00:00").replace(" ","T"))
                        dates.append(dt.date())
                    except ValueError:
                        pass
        et = Counter(ev_types).most_common(1)[0][0] if ev_types else "other"
        sd = min(dates) if dates else None
        ed = max(dates) if dates else None
        cur.execute("INSERT INTO event_coref_clusters(cluster_id,article_count,event_type,initiator,target,start_date,end_date) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (cid, len(aids), et, Counter(inits).most_common(1)[0][0] if inits else "?", Counter(tgts).most_common(1)[0][0] if tgts else "?", sd, ed))
        for aid in aids:
            cur.execute("INSERT INTO event_coref_members(cluster_id,news_id) VALUES(%s,%s) ON CONFLICT DO NOTHING", (cid, aid))
    cur.close()
    conn.close()


def main() -> None:
    t0 = time.time()
    logger.info("Step 1/7: Loading checkpoint...")
    results = load_checkpoint(str(CHECKPOINT))
    logger.info("  %d geopolitical articles", len(results))

    logger.info("Step 2/7: Loading article bodies...")
    bodies, titles = load_article_content(results)
    logger.info("  %d bodies loaded", len(bodies))

    logger.info("Step 3/7: Loading BGE-M3 embeddings...")
    all_ids = {r.article_id for r in results}
    embeddings = load_embeddings(all_ids)
    logger.info("  %d embeddings loaded", len(embeddings))

    logger.info("Step 4/7: Topic clustering...")
    topic_docs = {aid: bodies.get(aid, "") for aid in all_ids if len(bodies.get(aid, "")) > 50}
    valid_docs = validate_document_format(topic_docs)
    topic_assignments = cluster_topics(valid_docs, top_k=20, resolution=1.0)
    article_topic: dict[int, str] = {}
    for tid, aids in topic_assignments.items():
        for aid in aids:
            article_topic[aid] = tid
    for r in results:
        if r.article_id not in article_topic:
            article_topic[r.article_id] = "default"
    topic_counts = Counter(article_topic.values())
    logger.info("  %d topics", len(topic_counts))
    for tid, cnt in topic_counts.most_common(5):
        logger.info("    topic %s: %d articles", tid, cnt)

    if ALIAS_PATH.exists():
        load_entity_aliases(str(ALIAS_PATH))
        logger.info("  Entity aliases loaded")

    logger.info("Step 5/7: Per-topic L1 clustering...")
    geo = [r for r in results if r.article_id in embeddings]
    topic_groups: dict[str, list[ExtractionResult]] = defaultdict(list)
    for r in geo:
        topic_groups[article_topic.get(r.article_id, "default")].append(r)

    all_clusters: dict[str, list[int]] = {}
    t_l1 = time.time()
    for tid, group in sorted(topic_groups.items(), key=lambda x: -len(x[1])):
        if len(group) < 2:
            for r in group:
                all_clusters[f"{tid}_s_{r.article_id}"] = [r.article_id]
            continue
        g_ids = {r.article_id for r in group}
        g_embs = {aid: embeddings[aid] for aid in g_ids if aid in embeddings}
        g_bodies = {aid: bodies[aid] for aid in g_ids if aid in bodies}
        clusters = build_event_coreference_with_embeddings(
            group,
            article_bodies=g_bodies,
            article_titles=titles,
            embeddings=g_embs,
        )
        for cid, aids in clusters.items():
            all_clusters[f"{tid}_{cid}"] = aids

    all_clusters, rescue_merges = rescue_cross_topic_splits(
        all_clusters,
        geo,
        embeddings,
        titles,
    )
    elapsed_l1 = time.time() - t_l1

    sizes = [len(v) for v in all_clusters.values()]
    n_sing = sum(1 for s in sizes if s == 1)
    n_nons = len(all_clusters) - n_sing
    logger.info("")
    logger.info("=" * 60)
    logger.info("  Pipeline Complete")
    logger.info("=" * 60)
    logger.info("  Total clusters:      %7d", len(all_clusters))
    logger.info("  Non-singleton:       %7d (%.1f%%)", n_nons, 100 * n_nons / max(len(all_clusters), 1))
    logger.info("  Singleton:           %7d (%.1f%%)", n_sing, 100 * n_sing / max(len(all_clusters), 1))
    logger.info("  Articles:            %7d", sum(len(v) for v in all_clusters.values()))
    logger.info("  Max cluster:         %7d", max(sizes) if sizes else 0)
    logger.info("  Rescue merges:       %7d", rescue_merges)
    logger.info("  L1 time:             %7.1fs", elapsed_l1)
    logger.info("  Total time:          %7.1fs", time.time() - t0)

    logger.info("Step 6/7: Saving mapping...")
    with open(MAPPING_OUT, "w") as f:
        for cid, aids in all_clusters.items():
            for aid in aids:
                f.write(json.dumps({"cluster_id": cid, "article_id": aid}) + "\n")
    logger.info("  Saved to %s", MAPPING_OUT)

    logger.info("Step 7/7: Writing to DB...")
    write_to_db(all_clusters, results)
    logger.info("  DB write complete")

    # ── Step 8: L2 Event Evolution Chain ──
    logger.info("Step 8/8: Building L2 story graphs...")
    try:
        from core_pipeline.event_evolution_chain import build_storylines
        t_l2 = time.time()
        result = build_storylines(min_article_count=2, clear_existing=True, max_gap_days=30)
        logger.info("  L2 complete: %d graphs, %d edges (%.1fs)",
                     result.get("graphs", 0), result.get("edges", 0), time.time() - t_l2)
    except Exception as e:
        logger.warning("  L2 story graph build skipped: %s", e)

    # ── Step 9: LLM Naming (L1 clusters + L2 stories) ──
    logger.info("Step 9/9: Generating event & story titles...")
    try:
        import asyncio

        import aiohttp
        vllm_url = os.environ.get("VLLM_BASE_URL", "http://127.0.0.1:8004").rstrip("/") + "/v1/chat/completions"
        logger.info("  Using vLLM at %s", vllm_url)
        model = os.environ.get("VLLM_MODEL_NAME", "Qwen/Qwen2.5-7B-Instruct")
        sem = asyncio.Semaphore(20)

        async def _name_batch(items, prompt_fn, max_tokens=30):
            async def _name_one(item):
                async with sem:
                    prompt = prompt_fn(item) if callable(prompt_fn) else prompt_fn.format(text=item)
                    payload = {
                        "model": model, "temperature": 0.1, "max_tokens": max_tokens,
                        "messages": [
                            {"role": "system", "content": "用10个字以内的中文概括新闻事件，只输出标题，不要多余文字。"},
                            {"role": "user", "content": prompt},
                        ],
                    }
                    async with aiohttp.ClientSession() as s:
                        async with s.post(vllm_url, json=payload) as resp:
                            try:
                                d = await resp.json()
                                return d["choices"][0]["message"]["content"].strip().strip('"').strip("'")
                            except (KeyError, TypeError, ValueError):
                                return ""
            return await asyncio.gather(*[_name_one(item) for item in items])

        conn_n = _connect_database()
        cur_n = conn_n.cursor()

        # L1 clusters (with L2 story context)
        try:
            cur_n.execute("DROP TABLE IF EXISTS _tmp_l1_story")
            cur_n.execute("""
                CREATE TEMP TABLE _tmp_l1_story AS
                SELECT DISTINCT se.from_cluster_id as cid, COALESCE(st.title, '') as story_title
                FROM story_edges se
                LEFT JOIN story_trees st ON se.story_id = st.id
            """)
        except Exception:
            pass  # temp table might fail, fallback without story context

        cur_n.execute("""
            SELECT ec.cluster_id, ec.event_type, ec.initiator, ec.target,
                   MAX(LEFT(COALESCE(n.title, ''), 50)) as news_title,
                   COALESCE(s.story_title, '') as story_title
            FROM event_coref_clusters ec
            LEFT JOIN event_coref_members ecm ON ec.cluster_id = ecm.cluster_id
            LEFT JOIN news n ON ecm.news_id = n.id
            LEFT JOIN _tmp_l1_story s ON ec.cluster_id = s.cid
            WHERE (ec.title IS NULL OR ec.title = '') AND ec.article_count >= 2
            GROUP BY ec.cluster_id, ec.event_type, ec.initiator, ec.target, s.story_title
            LIMIT 500
        """)
        to_name = cur_n.fetchall()
        if to_name:
            def _l1_prompt(r):
                ctx = f"，故事:{r[5][:20]}" if r[5] else ""
                return f"事件:{r[2] or '?'}对{r[3] or '?'}，类型:{r[1]}{ctx}。中文标题（10字内）："
            titles = asyncio.run(_name_batch(to_name, _l1_prompt, 20))
            for (cid, et, init, tgt, _, st), title in zip(to_name, titles):
                if title:
                    title = title.strip().strip('"').strip("'").strip('"')
                    cur_n.execute("UPDATE event_coref_clusters SET title = %s WHERE cluster_id = %s", (title[:120], cid))
            conn_n.commit()
            logger.info("  Named %d L1 clusters (with L2 story context)", len(to_name))

        # L2 story graphs (from story_edges data)
        cur_n.execute("SELECT story_id, COUNT(*) as edges FROM story_edges GROUP BY story_id HAVING COUNT(*) >= 3 ORDER BY edges DESC LIMIT 100")
        story_rows = cur_n.fetchall()
        if story_rows:
            # Ensure story_trees has entries
            for sid, _ in story_rows:
                cur_n.execute("INSERT INTO story_trees (id, title, node_count) VALUES (%s, '', 0) ON CONFLICT (id) DO NOTHING", (sid,))
            conn_n.commit()

            def _l2_prompt(sid):
                cur_n2 = conn_n.cursor()
                cur_n2.execute("""
                    SELECT DISTINCT ecc.event_type, ecc.initiator, ecc.target FROM story_edges se
                    JOIN event_coref_clusters ecc ON se.from_cluster_id = ecc.cluster_id
                    WHERE se.story_id = %s LIMIT 5
                """, (sid,))
                events = [f"{r[1] or '?'}对{r[2] or '?'}({r[0]})" for r in cur_n2.fetchall()]
                return f"事件序列: {'; '.join(events)}。中文标题（10字内）："
            sids = [r[0] for r in story_rows]
            titles = asyncio.run(_name_batch(sids, _l2_prompt, 30))
            for sid, title in zip(sids, titles):
                if title:
                    cur_n.execute("UPDATE story_trees SET title = %s WHERE id = %s", (title[:200], sid))
            conn_n.commit()
            logger.info("  Named %d L2 story graphs", len(sids))

        conn_n.close()
        logger.info("  Naming complete")
    except Exception as e:
        logger.warning("  Naming skipped: %s", e)


if __name__ == "__main__":
    main()
