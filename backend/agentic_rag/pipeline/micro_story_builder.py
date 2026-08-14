"""L2 故事线构建 v2: 语义相位分割 + 约束合并 + 代表性标题

取代旧版简单 time-gap 分割，引入多信号相位边界检测：
  1. 加载 L1 簇（entity, date, type, embedding centroid）
  2. 按 entity_set 分组后，用 embedding 轨迹 + 事件类型 + 时间间隙做相位分割
  3. 层级化跨实体集合（同实体对 > 同方向 > 单侧重叠 > 无重叠）
  4. 实体对多样性上限（防止单故事包含过多无关实体对）
  5. 按实体对比例采样事件 → LLM 生成标题
"""
from __future__ import annotations

import math
import os

from agentic_rag.db_runtime_config import require_database_password
import time
from collections import Counter, defaultdict
from datetime import datetime
from typing import Dict, List, Tuple, Set

import psycopg2
import numpy as np

from core_pipeline.event_coref_cluster import _canonical_entity
try:
    from core_pipeline.event_coref_cluster import SYMMETRIC_EVENT_TYPES
except ImportError:
    SYMMETRIC_EVENT_TYPES = frozenset()  # no symmetric types → strict direction matters
from core_pipeline.entity_normalizer import entity_pair_key

# ═══════════════════════════════════════════════════════════════
# 配置常量
# ═══════════════════════════════════════════════════════════════

# ── 边权重（与之前保持一致）──
_ENTITY_WEIGHT = 0.35
_TEMPORAL_WEIGHT = 0.25
_EMBEDDING_WEIGHT = 0.25
_TYPE_WEIGHT = 0.15
_TEMPORAL_DECAY_DAYS = 14

# ── 相位分割权重 ──
_PHASE_EMBEDDING_WEIGHT = 0.35   # embedding 轨迹突变
_PHASE_TYPE_WEIGHT = 0.30       # 事件类型断裂
_PHASE_TIME_WEIGHT = 0.20       # 时间间隙（按密度归一化）
_PHASE_ENTITY_WEIGHT = 0.15     # 复合实体变化
_PHASE_MIN_CLUSTERS = 3          # 最小相位大小

# ── 跨实体集合层级化合并阈值 ──
_MERGE_SAME_PAIR = 0.40          # 同 initiator + target（双向叙事流）
_MERGE_SAME_DIRECTION = 0.45     # 同方向（如 US→Iran 与 Iran→US）
_MERGE_ONE_SIDE = 0.35           # 单侧重叠（如 US→Iran 与 UK→Iran，共享伊朗。跨实体集最高分约0.63，降至此值以下使合并可行）
_MERGE_TANGENTIAL = 0.55         # 切向重叠（如 US→Iran 与 Russia→US）
_MERGE_NONE = 0.70               # 无实体重叠

# ── 实体对多样性上限 ──
_MAX_ENTITY_PAIRS_PER_STORY = 5

# ── 标题生成 ──
_TITLE_MAX_EVENTS = 12
_TITLE_MIN_CHARS = 10
_TITLE_MAX_CHARS = 35

# ── 叙事弧转换得分 ──
_TYPE_TRANSITION_SCORE: Dict[Tuple[str, str], float] = {
    ("diplomacy", "trade_conflict"): 0.8, ("trade_conflict", "diplomacy"): 0.8,
    ("diplomacy", "military"): 0.6, ("military", "diplomacy"): 0.6,
    ("trade_conflict", "military"): 0.7, ("military", "trade_conflict"): 0.7,
    ("diplomacy", "protest_repression"): 0.5, ("protest_repression", "diplomacy"): 0.5,
    ("military", "protest_repression"): 0.4, ("protest_repression", "military"): 0.4,
    ("trade_conflict", "protest_repression"): 0.3,
    ("policy_legal", "diplomacy"): 0.5, ("diplomacy", "policy_legal"): 0.5,
    ("appointment_leadership", "diplomacy"): 0.6,
    ("human_rights_migration", "diplomacy"): 0.5,
}
for t in {"trade_conflict", "diplomacy", "military", "policy_legal",
          "protest_repression", "aid_disaster", "terrorism_espionage",
          "appointment_leadership", "human_rights_migration"}:
    _TYPE_TRANSITION_SCORE[(t, t)] = 0.9

# ── 默认低转换分（代表叙事断裂，意味着相位边界）──
_DEFAULT_TYPE_TRANSITION = 0.2


# ═══════════════════════════════════════════════════════════════
# DB 连接
# ═══════════════════════════════════════════════════════════════

def _get_conn():
    return psycopg2.connect(
        host=os.getenv("PG_HOST", "127.0.0.1"),
        port=int(os.getenv("PG_PORT", "5432")),
        dbname="globemind_news",
        user=os.getenv("PG_WRITE_USER", os.getenv("PG_USER", "postgres")),
        password=require_database_password(),
    )


# ═══════════════════════════════════════════════════════════════
# 边权重计算（不变）
# ═══════════════════════════════════════════════════════════════

def _edge_weight(c1: dict, c2: dict) -> float:
    """计算两个 L1 簇之间的故事线连接权重。"""
    same_set = c1["entity_set"] == c2["entity_set"]
    entity_score = 1.0 if same_set else 0.0

    gap_days = (c2["start_date"] - c1["end_date"]).days if c2["start_date"] and c1["end_date"] else 999
    gap_days = max(0.1, gap_days)
    temporal_score = math.exp(-gap_days / _TEMPORAL_DECAY_DAYS)

    emb_score = 0.0
    if c1["centroid"] is not None and c2["centroid"] is not None:
        emb_score = float(np.dot(c1["centroid"], c2["centroid"]))

    type_key = (c1["event_type"], c2["event_type"])
    type_score = _TYPE_TRANSITION_SCORE.get(type_key, _DEFAULT_TYPE_TRANSITION)

    return (
        _ENTITY_WEIGHT * entity_score +
        _TEMPORAL_WEIGHT * temporal_score +
        _EMBEDDING_WEIGHT * emb_score +
        _TYPE_WEIGHT * type_score
    )


# ═══════════════════════════════════════════════════════════════
# 1. 相位分割 — 多信号语义边界检测
# ═══════════════════════════════════════════════════════════════

def _phase_boundary_score(
    prev_c: dict,
    curr_c: dict,
    median_gap_days: float,
) -> float:
    """计算 prev → curr 之间的相位边界分数。

    分数越高 → 越可能是两个故事阶段的分界线。

    信号:
      1. Embedding 轨迹突变: 1 - cos(prev_cent, curr_cent)
         相似度陡降 → 叙事方向变化 → 高边界分
      2. 事件类型断裂: 1 - 类型转换得分
         低转换分（如 diplomacy→protest）→ 高边界分
      3. 时间间隙（按密度归一化）:
         相对于该实体组的中位数间隙，偏离越远 → 高边界分
      4. 复合实体变化: 增强实体键是否改变
         "US&israel→iran" → "US→iran" 表示参与方变化
    """
    score = 0.0

    # 1. Embedding 轨迹突变
    emb_score = 0.0
    p_cent = prev_c.get("centroid")
    c_cent = curr_c.get("centroid")
    if p_cent is not None and c_cent is not None:
        sim = float(np.dot(p_cent, c_cent))
        emb_score = max(0.0, 1.0 - sim)
    score += _PHASE_EMBEDDING_WEIGHT * emb_score

    # 2. 事件类型转换
    prev_type = prev_c.get("event_type", "other")
    curr_type = curr_c.get("event_type", "other")
    type_transition = _TYPE_TRANSITION_SCORE.get((prev_type, curr_type), _DEFAULT_TYPE_TRANSITION)
    # 低转换分 → 高边界分（叙事断裂）
    type_boundary = 1.0 - type_transition
    score += _PHASE_TYPE_WEIGHT * type_boundary

    # 3. 时间间隙（按实体组密度归一化）
    gap_days = max(0, (curr_c["start_date"] - prev_c["end_date"]).days) \
        if curr_c["start_date"] and prev_c["end_date"] else 999
    # 相对间隙：相对于中位数间隙的偏离
    # gap / (median + 1) 归一化，防止除零
    rel_gap = gap_days / max(1.0, median_gap_days)
    # sigmoid 映射到 [0, 1)：rel_gap=1 → 0.27, rel_gap=3 → 0.65, rel_gap=5 → 0.84
    time_score = 1.0 - 1.0 / (1.0 + math.exp(-0.5 * (rel_gap - 2.0)))
    score += _PHASE_TIME_WEIGHT * max(0.0, time_score)

    # 4. 复合实体键变化
    prev_key = prev_c.get("enhanced_key", "")
    curr_key = curr_c.get("enhanced_key", "")
    entity_score = 1.0 if (prev_key and curr_key and prev_key != curr_key) else 0.0
    score += _PHASE_ENTITY_WEIGHT * entity_score

    return score


def _detect_phase_boundaries(
    group_cids: List[str],
    clusters: Dict[str, dict],
) -> List[List[str]]:
    """检测实体组内的相位边界位置。

    Args:
        group_cids: 按时间排序的 cluster_id 列表（同一 entity_set）
        clusters: 所有簇的元数据

    Returns:
        相位列表，每个相位是一个 cluster_id 列表
        如果没有检测到边界，返回空列表
    """
    if len(group_cids) < _PHASE_MIN_CLUSTERS + 1:
        return []

    # 计算该组的中位数时间间隙（用于归一化）
    gaps = []
    for i in range(len(group_cids) - 1):
        prev_c = clusters[group_cids[i]]
        curr_c = clusters[group_cids[i + 1]]
        gap = max(0, (curr_c["start_date"] - prev_c["end_date"]).days) \
            if curr_c["start_date"] and prev_c["end_date"] else 999
        gaps.append(gap)
    median_gap = float(np.median(gaps)) if gaps else 7.0

    # 对每个相邻对计算相位边界分
    boundary_scores = []
    for i in range(len(group_cids) - 1):
        score = _phase_boundary_score(
            clusters[group_cids[i]],
            clusters[group_cids[i + 1]],
            median_gap,
        )
        boundary_scores.append((i, i + 1, score))

    if not boundary_scores:
        return []

    # 自适应阈值：mean + 0.5*std，且不低于 0.35
    scores_arr = np.array([s[2] for s in boundary_scores])
    threshold = max(0.35, float(np.mean(scores_arr) + 0.5 * np.std(scores_arr)))

    # 高于阈值的标记为相位边界
    split_positions = {0}
    for i, j, score in boundary_scores:
        if score > threshold:
            split_positions.add(j)  # 在位置 j 分割
    split_positions.add(len(group_cids))

    # 生成相位
    sorted_splits = sorted(split_positions)
    phases = []
    for k in range(len(sorted_splits) - 1):
        start = sorted_splits[k]
        end = sorted_splits[k + 1]
        phase_cids = group_cids[start:end]
        if len(phase_cids) >= 1:
            phases.append(phase_cids)

    # 合并过小相位（<_PHASE_MIN_CLUSTERS 且非唯一相位）
    if len(phases) > 1:
        merged = [phases[0]]
        for phase in phases[1:]:
            if len(phase) < _PHASE_MIN_CLUSTERS and merged:
                # 并入前一个相位
                merged[-1].extend(phase)
            else:
                merged.append(phase)
        # 重新检查：如果第一个相位太小且后面有，往后合并
        if len(merged) > 1 and len(merged[0]) < _PHASE_MIN_CLUSTERS:
            merged[1] = merged[0] + merged[1]
            merged.pop(0)
        phases = merged

    # 检查是否有单个超长相位需要进一步分割（>12 簇）
    final_phases = []
    for phase in phases:
        if len(phase) > 12:
            # 对超长相位再做时间间隙分割
            sub_phases = _split_overlong_phase(phase, clusters)
            final_phases.extend(sub_phases)
        else:
            final_phases.append(phase)

    return final_phases


def _split_overlong_phase(
    phase_cids: List[str],
    clusters: Dict[str, dict],
) -> List[List[str]]:
    """对超长相位（>12 簇）按时间间隙做二次分割。"""
    # 计算该相位的间隙分布
    gaps = []
    for i in range(len(phase_cids) - 1):
        prev_c = clusters[phase_cids[i]]
        curr_c = clusters[phase_cids[i + 1]]
        gap = max(0, (curr_c["start_date"] - prev_c["end_date"]).days) \
            if curr_c["start_date"] and prev_c["end_date"] else 999
        gaps.append(gap)

    if not gaps:
        return [phase_cids]

    median_gap = float(np.median(gaps))
    # 超过中位数 3 倍的间隙作为硬分割点
    hard_threshold = max(14, median_gap * 3.0)

    sub_phases = []
    current = [phase_cids[0]]
    for i in range(1, len(phase_cids)):
        prev_c = clusters[phase_cids[i - 1]]
        curr_c = clusters[phase_cids[i]]
        gap = max(0, (curr_c["start_date"] - prev_c["end_date"]).days) \
            if curr_c["start_date"] and prev_c["end_date"] else 999
        if gap > hard_threshold:
            if len(current) >= 2:
                sub_phases.append(current)
            current = [phase_cids[i]]
        else:
            current.append(phase_cids[i])
    if current:
        sub_phases.append(current)

    return sub_phases


# ═══════════════════════════════════════════════════════════════
# 2. 层级化跨实体集合并阈值
# ═══════════════════════════════════════════════════════════════

def _get_entity_overlap_level(
    entity_set1: tuple,
    entity_set2: tuple,
) -> int:
    """返回两个实体集的实体重叠深度级别。

    级别:
      3 = 同 initiator AND 同 target（完整匹配）
      2 = 同 initiator + 同 target 但方向互换（bidirectional）
      1 = 只有一边重叠（同 initiator 或同 target）
      0 = 切向重叠（init1 匹配 tgt2 或反之）
     -1 = 无重叠
    """
    init1, tgt1 = entity_set1
    init2, tgt2 = entity_set2

    if init1 == init2 and tgt1 == tgt2:
        return 3  # 完整匹配
    if init1 == tgt2 and tgt1 == init2:
        return 2  # 双向交换
    if init1 == init2 or tgt1 == tgt2:
        return 1  # 单侧重叠
    if init1 == tgt2 or tgt1 == init2:
        return 0  # 切向重叠
    return -1  # 无重叠


def _get_merge_threshold(
    overlap_level: int,
    edge_weight: float,
) -> float:
    """根据实体重叠深度获取合适的合并阈值。

    同实体对（完整匹配）→ 低阈值，容易合并
    单侧重叠 → 高阈值，需要很强的叙事连接才能合并
    无重叠 → 几乎不应合并
    """
    if overlap_level == 3:
        return _MERGE_SAME_PAIR
    elif overlap_level == 2:
        return _MERGE_SAME_DIRECTION
    elif overlap_level == 1:
        return _MERGE_ONE_SIDE
    elif overlap_level == 0:
        return _MERGE_TANGENTIAL
    else:
        return _MERGE_NONE


def _count_entity_pairs_in_story(
    story: List[str],
    clusters: Dict[str, dict],
) -> Set[tuple]:
    """统计一个故事中出现的不同实体对数量。"""
    pairs = set()
    for cid in story:
        if cid in clusters:
            es = clusters[cid].get("entity_set")
            if es:
                pairs.add(es)
    return pairs


# ═══════════════════════════════════════════════════════════════
# 3. 标题生成 — 按实体对比例采样
# ═══════════════════════════════════════════════════════════════

def _sample_events_for_title(
    story: List[str],
    clusters: Dict[str, dict],
    max_events: int = _TITLE_MAX_EVENTS,
) -> str:
    """从故事中按实体对比例采样事件，用于 LLM 标题生成。

    确保每个实体对在标题提示中都有体现，
    避免标题偏向事件多的单实体对。
    """
    # 按 entity_set 分组事件
    entity_events: Dict[tuple, List[str]] = defaultdict(list)
    for cid in story:
        c = clusters[cid]
        es = c.get("entity_set", ("?", "?"))
        entity_events[es].append(
            f"{c['start_date']} [{c['event_type']}] {c['title']}"
        )

    if not entity_events:
        return ""

    # 按比例分配每组的采样数
    n_groups = len(entity_events)
    base_per_group = max(1, max_events // n_groups)
    remaining = max_events - base_per_group * n_groups

    sampled = []
    # 先给每组分配 base_per_group 个
    for es, events in entity_events.items():
        events_sorted = sorted(events)  # 按时间排序
        sampled.extend(events_sorted[:base_per_group])

    # 剩余名额给最大的组
    if remaining > 0:
        largest_es = max(entity_events.items(), key=lambda x: len(x[1]))[0]
        remaining_events = entity_events[largest_es][base_per_group:base_per_group + remaining]
        sampled.extend(remaining_events)

    # 最后按时间排序
    sampled.sort(key=lambda s: s.split(" [")[0] if " [" in s else "")

    return "\n".join(sampled)


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def build_micro_stories() -> Tuple[int, int]:
    """Build L2 storylines with semantic phase segmentation + constrained merging."""
    t0 = time.time()

    # ═══════════════════════════════════════════
    # 1. 加载 L1 簇
    # ═══════════════════════════════════════════
    conn = _get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT cc.cluster_id, cc.article_count, cc.event_type, cc.initiator, cc.target,
               cc.start_date, cc.end_date, cc.title
        FROM event_coref_clusters cc
        WHERE cc.article_count >= 2
    """)
    cluster_rows = cur.fetchall()
    print(f"[L2] {len(cluster_rows)} non-singleton L1 clusters", flush=True)

    if not cluster_rows:
        print("[L2] No non-singleton clusters to process", flush=True)
        cur.close()
        conn.close()
        return 0, 0

    # 加载成员 news_ids
    all_cids = [r[0] for r in cluster_rows]
    cur.execute("""
        SELECT cluster_id, news_id FROM event_coref_members
        WHERE cluster_id = ANY(%s)
    """, (all_cids,))
    member_rows = cur.fetchall()
    cid_members: Dict[str, List[int]] = defaultdict(list)
    for cid, nid in member_rows:
        cid_members[cid].append(nid)

    # 加载 embedding centroids
    cur.execute("""
        SELECT ne.news_id, ne.embedding
        FROM news_embeddings ne
        WHERE ne.model IN ('bge-m3', 'BAAI/bge-m3')
    """)
    emb_rows = cur.fetchall()
    import json
    news_embeddings: Dict[int, np.ndarray] = {}
    for nid, emb_raw in emb_rows:
        if isinstance(emb_raw, memoryview):
            emb_raw = bytes(emb_raw)
        if isinstance(emb_raw, bytes):
            emb_raw = json.loads(emb_raw.decode())
        news_embeddings[nid] = np.array(emb_raw, dtype=np.float32)

    cur.close()
    conn.close()

    # 构建簇元数据
    clusters: Dict[str, dict] = {}
    for row in cluster_rows:
        cid = row[0]
        members = cid_members.get(cid, [])
        vecs = [news_embeddings[nid] for nid in members if nid in news_embeddings]
        centroid = None
        if vecs:
            centroid = np.mean(vecs, axis=0).astype(np.float32)
            norm_v = np.linalg.norm(centroid)
            if norm_v > 0:
                centroid = centroid / norm_v

        init_canon = _canonical_entity(row[3]) or "?"
        tgt_canon = _canonical_entity(row[4]) or "?"
        etype = row[2] or "other"

        if etype in SYMMETRIC_EVENT_TYPES:
            entity_set = (min(init_canon, tgt_canon), max(init_canon, tgt_canon))
        else:
            entity_set = (init_canon, tgt_canon)

        init_raw = row[3] or ""
        tgt_raw = row[4] or ""
        enhanced_key = entity_pair_key(init_raw, tgt_raw)

        clusters[cid] = {
            "article_count": row[1],
            "event_type": etype,
            "entity_set": entity_set,
            "enhanced_key": enhanced_key,
            "start_date": row[5] if row[5] else None,
            "end_date": row[6] if row[6] else None,
            "title": row[7] or "",
            "centroid": centroid,
            "members": members,
        }

    # 筛选有日期和 embedding 的簇
    valid_cids = [cid for cid, c in clusters.items()
                  if c["start_date"] and c["end_date"]]
    print(f"[L2] {len(valid_cids)} clusters with dates", flush=True)

    if len(valid_cids) < 2:
        print("[L2] Too few clusters for graph construction", flush=True)
        return 0, 0

    # ═══════════════════════════════════════════
    # 2. 构建事件图 + 相位分割
    # ═══════════════════════════════════════════

    cid_list = sorted(
        valid_cids,
        key=lambda c: (clusters[c]["entity_set"], clusters[c]["start_date"])
    )

    from itertools import groupby
    entity_groups = {}
    for key, group in groupby(cid_list, key=lambda c: clusters[c]["entity_set"]):
        entity_groups[key] = sorted(group, key=lambda c: clusters[c]["start_date"])

    print(f"[L2] {len(entity_groups)} entity groups", flush=True)

    # ── 2a. 构建实体组内边（不变）──
    edges: List[Tuple[str, str, float]] = []
    for entity_set, group_cids in entity_groups.items():
        for i in range(len(group_cids)):
            for j in range(i + 1, min(i + 6, len(group_cids))):
                c1, c2 = clusters[group_cids[i]], clusters[group_cids[j]]
                w = _edge_weight(c1, c2)
                if w >= 0.35:
                    edges.append((group_cids[i], group_cids[j], w))

    # ── 2b. 跨实体集边（不变）──
    cross_edges_added = 0
    enhanced_groups: Dict[str, List[str]] = {}
    for cid in valid_cids:
        key = clusters[cid].get("enhanced_key", "")
        if key:
            enhanced_groups.setdefault(key, []).append(cid)
    for k in enhanced_groups:
        enhanced_groups[k].sort(key=lambda c: clusters[c].get("start_date") or datetime(2020, 1, 1))

    enhanced_keys = list(enhanced_groups.keys())
    for i in range(len(enhanced_keys)):
        for j in range(i + 1, len(enhanced_keys)):
            k1, k2 = enhanced_keys[i], enhanced_keys[j]
            parts1 = k1.split("→")
            parts2 = k2.split("→")
            if len(parts1) != 2 or len(parts2) != 2:
                continue
            init_set1 = set(parts1[0].split("&")) if parts1[0] else set()
            tgt_set1 = set(parts1[1].split("&")) if parts1[1] else set()
            init_set2 = set(parts2[0].split("&")) if parts2[0] else set()
            tgt_set2 = set(parts2[1].split("&")) if parts2[1] else set()
            entity_overlap = (
                bool(init_set1 & init_set2) or bool(tgt_set1 & tgt_set2) or
                bool(init_set1 & tgt_set2) or bool(tgt_set1 & init_set2)
            )
            if not entity_overlap:
                continue
            cids1, cids2 = enhanced_groups[k1], enhanced_groups[k2]
            for c1 in cids1[-3:]:
                for c2 in cids2[:3]:
                    w = _edge_weight(clusters[c1], clusters[c2])
                    if w >= 0.30:
                        edges.append((c1, c2, w))
                        cross_edges_added += 1

    if cross_edges_added:
        print(f"[L2] {cross_edges_added} cross-entity-set edges (enhanced)", flush=True)

    print(f"[L2] {len(edges)} edges in event graph", flush=True)

    if len(edges) < 2:
        print("[L2] Too few edges for community detection", flush=True)
        return 0, 0

    # ═══════════════════════════════════════════
    # 3. 相位分割 + 故事线构建（核心改进）
    # ═══════════════════════════════════════════

    storylines: List[List[str]] = []

    for entity_set, group_cids in entity_groups.items():
        if len(group_cids) < 2:
            continue

        # 使用语义相位分割检测故事阶段
        phases = _detect_phase_boundaries(group_cids, clusters)

        if phases:
            # 用检测到的相位作为故事线
            for phase in phases:
                if len(phase) >= 2:
                    storylines.append(phase)
        else:
            # 相位检测未找到分割点 → 使用传统 time-gap 分割作为回退
            # （小实体组通常不需要分割）
            if len(group_cids) > 10:
                current = [group_cids[0]]
                for cid in group_cids[1:]:
                    prev_c = clusters[current[-1]]
                    curr_c = clusters[cid]
                    gap = (curr_c["start_date"] - prev_c["end_date"]).days \
                        if curr_c["start_date"] and prev_c["end_date"] else 999
                    type_changed = prev_c["event_type"] != curr_c["event_type"]

                    if len(current) >= 8 or gap > 30 or (type_changed and gap > 7):
                        if len(current) >= 2:
                            storylines.append(current)
                        current = [cid]
                    else:
                        current.append(cid)
                if len(current) >= 2:
                    storylines.append(current)
            else:
                current = [group_cids[0]]
                for cid in group_cids[1:]:
                    prev_c = clusters[current[-1]]
                    curr_c = clusters[cid]
                    gap = (curr_c["start_date"] - prev_c["end_date"]).days \
                        if curr_c["start_date"] and prev_c["end_date"] else 999
                    has_bridge = any(
                        _edge_weight(clusters[ec], curr_c) > 0.55
                        for ec in current[-3:]
                    )
                    if len(current) >= 10 or gap > 60 or (gap > 21 and not has_bridge):
                        if len(current) >= 2:
                            storylines.append(current)
                        current = [cid]
                    else:
                        current.append(cid)
                if len(current) >= 2:
                    storylines.append(current)

    # ═══════════════════════════════════════════
    # 4. 约束跨实体集合（核心改进）
    # ═══════════════════════════════════════════

    if edges and storylines:
        cid_to_story: Dict[str, int] = {}
        cid_to_entity: Dict[str, tuple] = {}
        for si, story in enumerate(storylines):
            for cid in story:
                cid_to_story[cid] = si
                cid_to_entity[cid] = clusters[cid]["entity_set"]

        changed = True
        iterations = 0
        while changed and iterations < 50:
            changed = False
            iterations += 1
            for e in edges:
                c1, c2, w = e
                s1 = cid_to_story.get(c1)
                s2 = cid_to_story.get(c2)
                if s1 is None or s2 is None or s1 == s2:
                    continue

                # 只合并不同实体集的故事线
                if cid_to_entity.get(c1) == cid_to_entity.get(c2):
                    continue

                # 获取实体重叠深度
                es1 = clusters[c1]["entity_set"]
                es2 = clusters[c2]["entity_set"]
                overlap_level = _get_entity_overlap_level(es1, es2)

                # 层级化阈值
                merge_threshold = _get_merge_threshold(overlap_level, w)

                if w <= merge_threshold:
                    continue

                # 检查实体对多样性上限
                merged_story = storylines[s1] + storylines[s2]
                entity_pairs = _count_entity_pairs_in_story(merged_story, clusters)
                if len(entity_pairs) > _MAX_ENTITY_PAIRS_PER_STORY:
                    # 尝试只合并到最近的 _MAX_ENTITY_PAIRS_PER_STORY 个实体对
                    continue

                # 执行合并
                storylines[s1].extend(storylines[s2])
                storylines[s2] = []
                for c in storylines[s1]:
                    cid_to_story[c] = s1
                changed = True

        storylines = [s for s in storylines if len(s) >= 2]

    # Debug: 故事线大小分布
    _size_counts = Counter(len(s) for s in storylines)
    print(f"[L2] Storyline size distribution: largest={max(_size_counts.keys()) if _size_counts else 0}, "
          f"total={sum(len(s) for s in storylines)}", flush=True)
    for _es, _gc in sorted(entity_groups.items(), key=lambda x: -len(x[1]))[:3]:
        _n_in = sum(1 for s in storylines for c in s if c in clusters and clusters[c]["entity_set"] == _es)
        _n_out = len(_gc) - _n_in
        print(f"[L2]   Entity {_es}: {len(_gc)} cls → {_n_in} in storylines, {_n_out} not covered", flush=True)
    print(f"[L2] {len(storylines)} storylines after phase segmentation + constrained merge", flush=True)

    # 调试：显示合并后的故事线实体对多样性统计
    diverse_stories = sum(
        1 for s in storylines
        if len(_count_entity_pairs_in_story(s, clusters)) > 1
    )
    print(f"[L2] {diverse_stories} storylines with multiple entity pairs (max {_MAX_ENTITY_PAIRS_PER_STORY})", flush=True)

    # ═══════════════════════════════════════════
    # 5. 孤簇救援（保持不变）
    # ═══════════════════════════════════════════

    covered_cids = set()
    for story in storylines:
        for cid in story:
            covered_cids.add(cid)

    rescued = 0
    for cid in valid_cids:
        if cid in covered_cids:
            continue
        c = clusters[cid]
        best_score = 0.0
        best_si = -1
        for si, story in enumerate(storylines):
            for scid in story[-3:]:
                w = _edge_weight(clusters[scid], c)
                if w > best_score:
                    best_score = w
                    best_si = si
        if best_score >= 0.45 and best_si >= 0:
            # 救援时也检查实体对多样性上限
            new_story = storylines[best_si] + [cid]
            entity_pairs = _count_entity_pairs_in_story(new_story, clusters)
            if len(entity_pairs) <= _MAX_ENTITY_PAIRS_PER_STORY:
                storylines[best_si].append(cid)
                covered_cids.add(cid)
                rescued += 1

    if rescued:
        print(f"[L2] Rescue pass: {rescued} isolated clusters attached to storylines", flush=True)

    if not storylines:
        print("[L2] No multi-cluster storylines found", flush=True)
        return 0, 0

    # ═══════════════════════════════════════════
    # 6. 标题生成（按比例采样改进）
    # ═══════════════════════════════════════════

    from agentic_rag.naming_service import generate_titles_batch

    storyline_prompts = []
    for story in storylines:
        # 使用按实体对比例采样
        event_text = _sample_events_for_title(story, clusters, _TITLE_MAX_EVENTS)
        if not event_text:
            continue

        prompt = (
            "以下是按时间顺序排列的相关事件，它们共同构成一个完整的故事线。\n\n"
            + event_text
            + "\n\n请用一句简洁的中文概括这条故事线（15-35字）："
        )
        storyline_prompts.append(prompt)

    print(f"[L2] Generating {len(storyline_prompts)} storyline titles...", flush=True)
    storyline_titles = generate_titles_batch(storyline_prompts, max_tokens=48)
    print(f"[L2] LLM naming done in {time.time()-t0:.1f}s", flush=True)

    # 如果标题数量不匹配（LLM 错误），填充占位符
    while len(storyline_titles) < len(storylines):
        storyline_titles.append("")

    # ═══════════════════════════════════════════
    # 7. 写入数据库
    # ═══════════════════════════════════════════

    conn2 = _get_conn()
    cur2 = conn2.cursor()

    cur2.execute("TRUNCATE TABLE story_edges, micro_story_coref_members, micro_story_coref RESTART IDENTITY CASCADE")
    conn2.commit()

    n_stories = 0
    n_clusters_covered = 0
    for story, title in zip(storylines, storyline_titles):
        total_articles = sum(clusters[cid]["article_count"] for cid in story)
        total_clusters = len(story)
        first_cid = min(story, key=lambda c: clusters[c]["start_date"] or datetime(2020, 1, 1))
        last_cid = max(story, key=lambda c: clusters[c]["end_date"] or datetime(2026, 12, 31))
        first_date = clusters[first_cid]["start_date"]
        last_date = clusters[last_cid]["end_date"]
        dominant_types = Counter(clusters[cid]["event_type"] for cid in story)
        dom_type = dominant_types.most_common(1)[0][0] if dominant_types else "other"
        entity_sets = set(clusters[cid]["entity_set"] for cid in story)
        dom_init = list(entity_sets)[0][0] if entity_sets else None
        dom_tgt = list(entity_sets)[0][1] if entity_sets else None

        # 如果标题为空或无效，使用实体对+事件类型作为标题
        if not title or len(title) < _TITLE_MIN_CHARS:
            if dom_init and dom_tgt:
                title = f"{dom_init}←→{dom_tgt} · {dom_type}"
            else:
                title = f"{dom_type} · {total_clusters}事件"

        cur2.execute("""
            INSERT INTO micro_story_coref (id, title, event_type, initiator, target,
                start_date, end_date, article_count, cluster_count, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (id) DO UPDATE SET
                title = EXCLUDED.title, article_count = EXCLUDED.article_count
        """, (n_stories + 1, title, dom_type, dom_init, dom_tgt,
              first_date, last_date, total_articles, total_clusters))

        # 写入成员
        for cid in story:
            for nid in clusters[cid].get("members", [])[:3]:
                cur2.execute("""
                    INSERT INTO micro_story_coref_members (micro_story_id, cluster_id, news_id)
                    VALUES (%s, %s, %s)
                """, (n_stories + 1, cid, nid))

        # 写入边
        sorted_story = sorted(story, key=lambda c: clusters[c].get("start_date") or datetime(2020, 1, 1))
        for i in range(len(sorted_story) - 1):
            c_from = sorted_story[i]
            c_to = sorted_story[i + 1]
            t1 = clusters[c_from]["event_type"]
            t2 = clusters[c_to]["event_type"]
            if t1 == t2:
                etype = "continued"
            elif (t1, t2) in _TYPE_TRANSITION_SCORE:
                s = _TYPE_TRANSITION_SCORE[(t1, t2)]
                etype = "escalation" if s >= 0.7 else "response" if s >= 0.5 else "transition"
            else:
                etype = "transition"
            cur2.execute("""
                INSERT INTO story_edges (story_id, from_cluster_id, to_cluster_id, edge_type, weight)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (story_id, from_cluster_id, to_cluster_id) DO NOTHING
            """, (n_stories + 1, c_from, c_to, etype, _edge_weight(clusters[c_from], clusters[c_to])))

        n_stories += 1
        n_clusters_covered += len(story)

    # ── 8. 连通性修复：确保每个故事是无向连通的 ──
    _repair_count = 0
    cur2.execute("""
        SELECT story_id, from_cluster_id, to_cluster_id
        FROM story_edges
        ORDER BY story_id
    """)
    edge_rows = cur2.fetchall()
    story_adj: Dict[int, Dict[str, List[str]]] = {}
    for sid, frm, to in edge_rows:
        story_adj.setdefault(sid, {}).setdefault(frm, []).append(to)
        story_adj[sid].setdefault(to, []).append(frm)

    for sid, adj in story_adj.items():
        all_nodes = list(adj.keys())
        if not all_nodes:
            continue
        visited: Set[str] = set()
        components: List[List[str]] = []
        for nid in all_nodes:
            if nid in visited:
                continue
            stack = [nid]
            comp: List[str] = []
            while stack:
                cur = stack.pop()
                if cur in visited:
                    continue
                visited.add(cur)
                comp.append(cur)
                for nb in adj.get(cur, []):
                    if nb not in visited:
                        stack.append(nb)
            components.append(comp)

        if len(components) <= 1:
            continue

        # 找到跨分量的时间最邻近对并加桥接边
        comp_dates: List[tuple[str, datetime, int]] = []
        for ci, comp in enumerate(components):
            for cid in comp:
                c = clusters.get(cid, {})
                d = c.get("start_date") or c.get("end_date")
                if d:
                    comp_dates.append((cid, d, ci))
        comp_dates.sort(key=lambda x: x[1])

        for i in range(len(comp_dates) - 1):
            cid_a, d_a, ci_a = comp_dates[i]
            cid_b, d_b, ci_b = comp_dates[i + 1]
            if ci_a == ci_b:
                continue
            t1 = clusters.get(cid_a, {}).get("event_type", "other")
            t2 = clusters.get(cid_b, {}).get("event_type", "other")
            if t1 == t2:
                etype = "continued"
            elif (t1, t2) in _TYPE_TRANSITION_SCORE:
                s = _TYPE_TRANSITION_SCORE[(t1, t2)]
                etype = "escalation" if s >= 0.7 else "response" if s >= 0.5 else "transition"
            else:
                etype = "transition"
            w = _edge_weight(clusters.get(cid_a, {}), clusters.get(cid_b, {}))
            cur2.execute("""
                INSERT INTO story_edges (story_id, from_cluster_id, to_cluster_id, edge_type, weight)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (story_id, from_cluster_id, to_cluster_id) DO NOTHING
            """, (sid, cid_a, cid_b, etype, w))
            _repair_count += 1
            # 合并分量标记
            for j in range(len(comp_dates)):
                if comp_dates[j][2] == ci_b:
                    comp_dates[j] = (comp_dates[j][0], comp_dates[j][1], ci_a)

    if _repair_count:
        conn2.commit()
        print(f"[L2] 连通性修复: {_repair_count} 条桥接边已添加", flush=True)

    conn2.commit()
    cur2.close()
    conn2.close()

    elapsed = time.time() - t0
    print(f"[L2] Done: {n_stories} stories, {n_clusters_covered} clusters ({elapsed:.1f}s)", flush=True)

    # 输出关键优化指标
    if n_stories > 0:
        avg_clusters_per_story = n_clusters_covered / n_stories
        print(f"[L2] Metrics: avg {avg_clusters_per_story:.1f} cls/story, "
              f"{n_clusters_covered}/{len(valid_cids)} cls covered ({(100*n_clusters_covered/max(len(valid_cids),1)):.0f}%)", flush=True)

    return n_stories, n_clusters_covered


if __name__ == "__main__":
    build_micro_stories()
