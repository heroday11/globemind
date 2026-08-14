"""
Story Tree Builder — 在 L2 Micro-Story 之上构建层级故事树。

核心算法：
  1. 对每条 storyline 中的 L1 簇序列，检测"叙事转折点"（turning point）
  2. 转折点之间的连续簇构成一个 sub-story（子章节）
  3. sub-story 之间有 parent-child 关系（时间顺序+叙事逻辑）
  4. 输出层级树结构，存入 story_hierarchy 表

转折点检测信号（多维度融合）：
  - Embedding trajectory: 连续两个簇的 centroid cosine 突降 → 话题漂移
  - Event type 转换: 某些转换（diplomacy→military）比另一些（diplomacy→trade）更具转折性
  - 时间间隔: 长间隔意味着故事阶段切换
  - Entity set 变化: 实体组的变化标志叙事焦点转移
"""
from __future__ import annotations

import math
import os
import time
from collections import defaultdict
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import psycopg2

from agentic_rag.db.story_hierarchy_schema import ensure_story_hierarchy_tables
from agentic_rag.db_runtime_config import require_database_password
from core_pipeline.event_coref_cluster import _canonical_entity

try:
    from core_pipeline.event_coref_cluster import SYMMETRIC_EVENT_TYPES
except ImportError:
    SYMMETRIC_EVENT_TYPES = frozenset()
from core_pipeline.entity_normalizer import entity_pair_key

# ── 权重配置 ──
_W_EMBEDDING = 0.35   # embedding 轨迹突变权重
_W_TYPE = 0.30        # 事件类型转换权重
_W_TIME = 0.20        # 时间间隔权重
_W_ENTITY = 0.15      # 实体变化权重

# 类型转换转折分 — 值越大表示转折越剧烈
_TYPE_TURN_SCORE: Dict[Tuple[str, str], float] = {
    # 剧烈转折
    ("diplomacy", "military"): 0.9, ("military", "diplomacy"): 0.9,
    ("diplomacy", "protest_repression"): 0.8, ("protest_repression", "diplomacy"): 0.8,
    ("trade_conflict", "military"): 0.8, ("military", "trade_conflict"): 0.8,
    ("diplomacy", "terrorism_espionage"): 0.8,
    # 中等转折
    ("diplomacy", "trade_conflict"): 0.6, ("trade_conflict", "diplomacy"): 0.6,
    ("policy_legal", "military"): 0.7, ("military", "policy_legal"): 0.7,
    ("human_rights_migration", "diplomacy"): 0.6,
    ("aid_disaster", "diplomacy"): 0.5,
    # 相同类型 = 延续，无转折
    ("diplomacy", "diplomacy"): 0.0,
    ("military", "military"): 0.0,
    ("trade_conflict", "trade_conflict"): 0.0,
}
# 未显示的转换默认给中等转折
_DEFAULT_TYPE_TURN = 0.4

_TEMPORAL_HALF_LIFE_DAYS = 14  # 时间衰减半衰期


def _get_conn():
    return psycopg2.connect(
        host=os.getenv("PG_HOST", "127.0.0.1"),
        port=int(os.getenv("PG_PORT", "5432")),
        dbname="globemind_news",
        user=os.getenv("PG_WRITE_USER", os.getenv("PG_USER", "postgres")),
        password=require_database_password(),
    )


def _turning_score(
    prev_c: dict,
    curr_c: dict,
    next_c: Optional[dict] = None,
) -> float:
    """
    计算 curr_c 相对于 prev_c 和 next_c 的转折分数。
    
    使用三个连续点的上下文：如果 prev→curr 的 embedding 方向 
    与 curr→next 的方向差异大，则 curr 是转折点。
    
    如果 next_c 为 None（序列末尾），只用 prev→curr 的绝对变化。
    """
    score = 0.0

    # 1. Embedding trajectory 突变
    emb_score = 0.0
    p_cent = prev_c.get("centroid")
    c_cent = curr_c.get("centroid")
    n_cent = next_c.get("centroid") if next_c else None

    if p_cent is not None and c_cent is not None:
        # prev→curr 的相似度
        sim_pc = float(np.dot(p_cent, c_cent))
        # 相似度越低 → 转折越大
        emb_score = max(0.0, 1.0 - sim_pc)

        if n_cent is not None:
            # curr→next 的相似度
            sim_cn = float(np.dot(c_cent, n_cent))
            # 两个方向的相似度都低 → 更可能是转折点
            emb_score = max(emb_score, max(0.0, 1.0 - sim_cn))

    score += _W_EMBEDDING * emb_score

    # 2. Event type 转换
    prev_type = prev_c.get("event_type", "other")
    curr_type = curr_c.get("event_type", "other")
    type_score = _TYPE_TURN_SCORE.get((prev_type, curr_type), _DEFAULT_TYPE_TURN)
    if next_c:
        next_type = next_c.get("event_type", "other")
        type_score = max(type_score, _TYPE_TURN_SCORE.get((curr_type, next_type), _DEFAULT_TYPE_TURN))
    score += _W_TYPE * type_score

    # 3. 时间间隔
    time_score = 0.0
    p_end = prev_c.get("end_date")
    c_start = curr_c.get("start_date")
    c_end = curr_c.get("end_date")
    n_start = next_c.get("start_date") if next_c else None

    if p_end and c_start:
        gap = max(0, (c_start - p_end).days)
        time_score = 1.0 - math.exp(-gap / _TEMPORAL_HALF_LIFE_DAYS)

    if c_end and n_start and next_c:
        gap2 = max(0, (n_start - c_end).days)
        time_score = max(time_score, 1.0 - math.exp(-gap2 / _TEMPORAL_HALF_LIFE_DAYS))

    score += _W_TIME * time_score

    # 4. Entity set 变化
    entity_score = 0.0
    p_entity = prev_c.get("entity_set")
    c_entity = curr_c.get("entity_set")
    n_entity = next_c.get("entity_set") if next_c else None

    if p_entity != c_entity:
        entity_score = 0.8
    if n_entity and c_entity != n_entity:
        entity_score = max(entity_score, 0.8)

    score += _W_ENTITY * entity_score

    return score


def detect_sub_stories(
    cluster_ids: List[str],
    clusters: Dict[str, dict],
) -> List[Dict[str, Any]]:
    """
    检测一条 storyline 中的子章节（sub-story）。
    
    输入: 按时间排序的 cluster_id 列表
    输出: sub-story 列表，每个包含 {title, cluster_ids, start_date, end_date, level}
    
    算法:
      1. 对每个位置 i，计算 turning_score(ci-1, ci, ci+1)
      2. 如果 turning_score > 阈值 → 标记为转折点
      3. 转折点之间的连续簇 → 一个 sub-story
      4. 合并过小的 sub-story（<2 簇）到相邻的较大 sub-story
    """
    n = len(cluster_ids)
    if n < 2:
        return []

    # 计算每个位置的转折分数
    turning_scores = []
    for i in range(1, n - 1):
        score = _turning_score(
            clusters[cluster_ids[i - 1]],
            clusters[cluster_ids[i]],
            clusters[cluster_ids[i + 1]],
        )
        turning_scores.append((i, score))

    # 自适应阈值：使用 mean + 0.5*std，但至少 0.4
    if turning_scores:
        scores_arr = np.array([s[1] for s in turning_scores])
        threshold = max(0.4, float(np.mean(scores_arr) + 0.5 * np.std(scores_arr)))
    else:
        threshold = 0.6

    # 标记转折点
    split_positions = {0}  # 始终从第一簇开始
    for i, score in turning_scores:
        if score > threshold:
            split_positions.add(i)

    split_positions.add(n)  # 最后一簇结束

    # 生成子章节
    sorted_splits = sorted(split_positions)
    sub_stories = []
    for j in range(len(sorted_splits) - 1):
        start = sorted_splits[j]
        end = sorted_splits[j + 1]
        sub_cids = cluster_ids[start:end]
        if len(sub_cids) < 1:
            continue

        # 计算子章节元信息
        first_c = clusters[sub_cids[0]]
        last_c = clusters[sub_cids[-1]]
        total_articles = sum(clusters[cid]["article_count"] for cid in sub_cids)

        # 主导 event_type
        from collections import Counter
        type_counter = Counter(clusters[cid]["event_type"] for cid in sub_cids)
        dom_type = type_counter.most_common(1)[0][0] if type_counter else "other"

        # 计算该子章节的平均转折分
        avg_turn = 0.0
        if len(sub_cids) > 1:
            scores_in = [
                s for i, s in turning_scores
                if start <= i < end - 1
            ]
            if scores_in:
                avg_turn = float(np.mean(scores_in))

        sub_stories.append({
            "cluster_ids": sub_cids,
            "article_count": total_articles,
            "cluster_count": len(sub_cids),
            "start_date": first_c.get("start_date"),
            "end_date": last_c.get("end_date"),
            "event_type": dom_type,
            "entity_set": first_c.get("entity_set"),
            "turning_score": avg_turn,
            "level": 1,  # sub-story 层级
        })

    # 合并过小的子章节（<2 簇且不是唯一的子章节）
    if len(sub_stories) > 1:
        merged = []
        for ss in sub_stories:
            if ss["cluster_count"] < 2 and merged:
                # 合并到前一个
                prev = merged[-1]
                prev["cluster_ids"].extend(ss["cluster_ids"])
                prev["article_count"] += ss["article_count"]
                prev["cluster_count"] += ss["cluster_count"]
                prev["end_date"] = ss["end_date"]
                prev["turning_score"] = max(prev["turning_score"], ss["turning_score"])
                # 更新主导类型
                from collections import Counter
                all_types = Counter(
                    clusters[cid]["event_type"]
                    for cid in prev["cluster_ids"]
                )
                prev["event_type"] = all_types.most_common(1)[0][0] if all_types else "other"
            else:
                merged.append(ss)
        sub_stories = merged

    return sub_stories


def build_story_tree() -> Tuple[int, int]:
    """
    主入口：从现有 micro_story_coref 读取故事线，
    检测子章节，写入 story_hierarchy 表。
    
    返回: (处理的故事数, 子章节总数)
    """
    t0 = time.time()
    ensure_story_hierarchy_tables()

    conn = _get_conn()
    cur = conn.cursor()

    # 1. 加载所有非单例故事线
    cur.execute("""
        SELECT id, title, event_type, start_date, end_date,
               article_count, cluster_count
        FROM micro_story_coref
        WHERE cluster_count >= 2
        ORDER BY id
    """)
    stories = cur.fetchall()
    print(f"[StoryTree] {len(stories)} stories to process", flush=True)

    if not stories:
        cur.close()
        conn.close()
        return 0, 0

    # 2. 加载每个故事线的簇成员
    story_id_to_cids: Dict[int, List[str]] = {}
    story_id_to_order: Dict[int, int] = {}
    for s in stories:
        sid = s[0]
        cur.execute("""
            SELECT DISTINCT cluster_id
            FROM micro_story_coref_members
            WHERE micro_story_id = %s
        """, (sid,))
        cids = [r[0] for r in cur.fetchall()]
        story_id_to_cids[sid] = cids
        story_id_to_order[sid] = 0

    # 3. 加载所有涉及的 L1 簇元信息
    all_cids = set()
    for cids in story_id_to_cids.values():
        all_cids.update(cids)

    cluster_meta: Dict[str, dict] = {}
    if all_cids:
        batch = list(all_cids)
        cur.execute("""
            SELECT cluster_id, event_type, initiator, target,
                   article_count, start_date, end_date, title
            FROM event_coref_clusters
            WHERE cluster_id = ANY(%s)
        """, (batch,))
        for r in cur.fetchall():
            cid = r[0]
            init_canon = _canonical_entity(r[2]) or "?" if r[2] else "?"
            tgt_canon = _canonical_entity(r[3]) or "?" if r[3] else "?"
            etype = r[1] or "other"
            if etype in SYMMETRIC_EVENT_TYPES:
                entity_set = (min(init_canon, tgt_canon), max(init_canon, tgt_canon))
            else:
                entity_set = (init_canon, tgt_canon)
            # Enhanced entity key for better cross-set matching
            init_raw = r[2] or ""
            tgt_raw = r[3] or ""
            cluster_meta[cid] = {
                "enhanced_key": entity_pair_key(init_raw, tgt_raw),
                "event_type": etype,
                "entity_set": entity_set,
                "article_count": r[4],
                "start_date": r[5],
                "end_date": r[6],
                "title": r[7] or "",
                "centroid": None,
            }

    # 4. 加载 embedding centroid（用于轨迹分析）
    cur.execute("""
        SELECT ne.news_id, ne.embedding
        FROM news_embeddings ne
        WHERE ne.model IN ('bge-m3', 'BAAI/bge-m3')
    """)
    news_embeddings: Dict[int, np.ndarray] = {}
    for nid, emb_raw in cur.fetchall():
        if isinstance(emb_raw, memoryview):
            emb_raw = bytes(emb_raw)
        if isinstance(emb_raw, bytes):
            import json
            emb_raw = json.loads(emb_raw.decode())
        news_embeddings[nid] = np.array(emb_raw, dtype=np.float32)

    # 为每个 L1 簇计算 centroid
    cid_news: Dict[str, List[int]] = defaultdict(list)
    for sid, cids in story_id_to_cids.items():
        for cid in cids:
            cur.execute("""
                SELECT news_id FROM micro_story_coref_members
                WHERE micro_story_id = %s AND cluster_id = %s
                LIMIT 100
            """, (sid, cid))
            for r in cur.fetchall():
                cid_news[cid].append(r[0])

    for cid, members in cid_news.items():
        if cid in cluster_meta:
            vecs = [news_embeddings[nid] for nid in members if nid in news_embeddings]
            if vecs:
                centroid = np.mean(vecs, axis=0).astype(np.float32)
                norm = np.linalg.norm(centroid)
                if norm > 0:
                    centroid = centroid / norm
                cluster_meta[cid]["centroid"] = centroid

    cur.close()
    conn.close()

    # 5. 为每条故事线检测子章节
    total_sub_stories = 0
    stories_processed = 0

    conn2 = _get_conn()
    cur2 = conn2.cursor()

    # 清空旧数据
    cur2.execute("TRUNCATE TABLE story_hierarchy RESTART IDENTITY CASCADE")
    conn2.commit()

    for story in stories:
        sid = story[0]
        cids = story_id_to_cids.get(sid, [])
        if len(cids) < 2:
            continue

        # 按 start_date 排序
        sorted_cids = sorted(
            cids,
            key=lambda c: (cluster_meta.get(c, {}).get("start_date") or date(2020, 1, 1),
                          cluster_meta.get(c, {}).get("end_date") or date(2020, 1, 1))
        )

        # 只保留有元信息的簇
        valid_cids = [c for c in sorted_cids if c in cluster_meta]
        if len(valid_cids) < 2:
            continue

        # 检测子章节
        sub_stories = detect_sub_stories(valid_cids, cluster_meta)

        if not sub_stories:
            # 如果没有检测到子章节，整条故事线作为一个子章节
            first_c = cluster_meta.get(valid_cids[0], {})
            last_c = cluster_meta.get(valid_cids[-1], {})
            total_articles = sum(cluster_meta.get(c, {}).get("article_count", 0) for c in valid_cids)
            from collections import Counter
            type_counter = Counter(cluster_meta.get(c, {}).get("event_type", "other") for c in valid_cids)
            sub_stories = [{
                "cluster_ids": valid_cids,
                "article_count": total_articles,
                "cluster_count": len(valid_cids),
                "start_date": first_c.get("start_date"),
                "end_date": last_c.get("end_date"),
                "event_type": type_counter.most_common(1)[0][0] if type_counter else "other",
                "entity_set": first_c.get("entity_set"),
                "turning_score": 0.0,
                "level": 1,
            }]

        # 写入 story_hierarchy
        # Root node (level 0)
        cur2.execute("""
            INSERT INTO story_hierarchy (story_id, parent_id, level, title,
                cluster_ids, article_count, cluster_count, start_date, end_date,
                event_type, entity_set, turning_score)
            VALUES (%s, NULL, 0, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            sid,
            story[1],  # story title
            valid_cids,
            story[5],  # article_count
            story[6],  # cluster_count
            story[3],  # start_date
            story[4],  # end_date
            story[2],  # event_type
            str(cluster_meta.get(valid_cids[0], {}).get("entity_set", "")),
            0.0,
        ))
        root_id = cur2.fetchone()[0]

        # Sub-story nodes (level 1)
        for ss in sub_stories:
            # 生成子章节标题描述
            first_c = cluster_meta.get(ss["cluster_ids"][0], {})
            last_c = cluster_meta.get(ss["cluster_ids"][-1], {})

            cur2.execute("""
                INSERT INTO story_hierarchy (story_id, parent_id, level, title,
                    cluster_ids, article_count, cluster_count, start_date, end_date,
                    event_type, entity_set, turning_score)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                sid,
                root_id,
                ss["level"],
                f"{ss['event_type']} · {ss['cluster_count']}事件 · {ss['article_count']}篇",
                ss["cluster_ids"],
                ss["article_count"],
                ss["cluster_count"],
                ss["start_date"],
                ss["end_date"],
                ss["event_type"],
                str(ss.get("entity_set", "")),
                ss["turning_score"],
            ))
            total_sub_stories += 1

        stories_processed += 1
        if stories_processed % 20 == 0:
            conn2.commit()

    conn2.commit()
    cur2.close()
    conn2.close()

    elapsed = time.time() - t0
    print(f"[StoryTree] Done: {stories_processed} stories → {total_sub_stories} sub-stories ({elapsed:.1f}s)", flush=True)
    return stories_processed, total_sub_stories


if __name__ == "__main__":
    build_story_tree()
