#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

try:
    from sklearn.neighbors import NearestNeighbors
    _HAS_SKLEARN = True
except ImportError:
    NearestNeighbors = None
    _HAS_SKLEARN = False

from l1_review_utils import (
    ArticleRecord,
    cosine_similarity,
    hydrate_records,
    load_checkpoint_records,
    load_cluster_mapping,
    time_delta_days,
)

NEGATIVE_MIX = {
    "same_entity_same_type_far": 0.35,
    "same_entity_diff_type": 0.25,
    "semantic_neighbor_same_type": 0.25,
    "random_background": 0.15,
}
TIME_WINDOWS = {
    "trade_conflict": 90,
    "diplomacy": 90,
    "military": 90,
    "policy_legal": 60,
    "protest_repression": 90,
    "terrorism_espionage": 90,
    "human_rights_migration": 90,
    "aid_disaster": 60,
    "appointment_leadership": 60,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild event-pair training data with harder negatives.")
    parser.add_argument(
        "--mapping-path",
        default="/root/data/globemind/data/event_coref_mapping_layer1.jsonl",
        help="L1 cluster mapping JSONL",
    )
    parser.add_argument(
        "--output",
        default="/root/data/globemind/data/training_data_event_level_v2.json",
        help="Output dataset JSON path",
    )
    parser.add_argument(
        "--stats-output",
        default="/root/data/globemind/data/training_data_stats_v2.json",
        help="Output stats JSON path",
    )
    parser.add_argument(
        "--max-positives",
        type=int,
        default=16000,
        help="Maximum number of positive pairs to keep",
    )
    parser.add_argument(
        "--max-positives-per-cluster",
        type=int,
        default=24,
        help="Per-cluster positive pair cap before global sampling",
    )
    parser.add_argument(
        "--negative-ratio",
        type=float,
        default=1.0,
        help="Negatives per positive pair",
    )
    parser.add_argument(
        "--semantic-k",
        type=int,
        default=12,
        help="Nearest-neighbor fan-out for semantic hard negatives",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute stats only; do not write files",
    )
    return parser.parse_args()


def effective_time_window(event_type: str) -> int:
    return TIME_WINDOWS.get(event_type, 30)


def record_pair(
    left: ArticleRecord,
    right: ArticleRecord,
    *,
    label: int,
    cosine: float,
    neg_type: Optional[str] = None,
) -> dict:
    delta = time_delta_days(left, right)
    payload = {
        "article_id_1": left.article_id,
        "article_id_2": right.article_id,
        "cluster_id_1": left.cluster_id,
        "cluster_id_2": right.cluster_id,
        "entity_pair_key": left.entity_pair_key,
        "event_type_1": left.event_type,
        "event_type_2": right.event_type,
        "published_at_1": left.published_at,
        "published_at_2": right.published_at,
        "time_delta_days": int(delta if delta is not None else 999),
        "cosine_similarity": round(float(cosine), 6),
        "label": int(label),
    }
    if neg_type:
        payload["neg_type"] = neg_type
    return payload


def pair_key(left: int, right: int) -> tuple[int, int]:
    return (left, right) if left < right else (right, left)


def sorted_pairs_for_cluster(
    cluster_records: Sequence[ArticleRecord],
    max_pairs: int,
) -> list[dict]:
    candidates: list[tuple[tuple[int, float, int, int], dict]] = []
    for left, right in combinations(cluster_records, 2):
        if left.embedding is None or right.embedding is None:
            continue
        delta = time_delta_days(left, right)
        delta_score = delta if delta is not None else 999
        cosine = cosine_similarity(left.embedding, right.embedding)
        score = (delta_score, -cosine, left.article_id, right.article_id)
        candidates.append((score, record_pair(left, right, label=1, cosine=cosine)))
    candidates.sort(key=lambda item: item[0])
    return [payload for _, payload in candidates[:max_pairs]]


def build_positive_pairs(
    records: dict[int, ArticleRecord],
    clusters: dict[str, list[int]],
    *,
    max_per_cluster: int,
    max_total: int,
    rng: random.Random,
) -> list[dict]:
    positives: list[dict] = []
    for cluster_id, article_ids in clusters.items():
        cluster_records = [records[article_id] for article_id in article_ids if article_id in records]
        if len(cluster_records) < 2:
            continue
        positives.extend(sorted_pairs_for_cluster(cluster_records, max_per_cluster))
    if len(positives) <= max_total:
        return positives
    rng.shuffle(positives)
    return positives[:max_total]


def _append_candidate(
    bucket: list[tuple[tuple[int, float, int, int], dict]],
    left: ArticleRecord,
    right: ArticleRecord,
    *,
    neg_type: str,
    score_delta: int,
    cosine: float,
) -> None:
    bucket.append(
        (
            (score_delta, -cosine, min(left.article_id, right.article_id), max(left.article_id, right.article_id)),
            record_pair(left, right, label=0, cosine=cosine, neg_type=neg_type),
        )
    )


def build_same_entity_same_type_far_negatives(
    records: Iterable[ArticleRecord],
    *,
    per_group_cap: int = 16,
) -> list[dict]:
    groups: dict[tuple[str, str], list[ArticleRecord]] = defaultdict(list)
    for record in records:
        groups[(record.entity_pair_key, record.event_type)].append(record)

    negatives: list[dict] = []
    for (_entity_pair, event_type), group in groups.items():
        if len(group) < 2:
            continue
        threshold = max(14, effective_time_window(event_type) // 2)
        candidates: list[tuple[tuple[int, float, int, int], dict]] = []
        for left, right in combinations(group, 2):
            if left.cluster_id == right.cluster_id:
                continue
            if left.embedding is None or right.embedding is None:
                continue
            delta = time_delta_days(left, right)
            if delta is None or delta < threshold:
                continue
            cosine = cosine_similarity(left.embedding, right.embedding)
            if cosine >= 0.93:
                continue
            _append_candidate(
                candidates,
                left,
                right,
                neg_type="same_entity_same_type_far",
                score_delta=delta,
                cosine=cosine,
            )
        candidates.sort(key=lambda item: item[0])
        negatives.extend(payload for _, payload in candidates[:per_group_cap])
    return negatives


def build_same_entity_diff_type_negatives(
    records: Iterable[ArticleRecord],
    *,
    per_group_cap: int = 18,
) -> list[dict]:
    groups: dict[str, list[ArticleRecord]] = defaultdict(list)
    for record in records:
        groups[record.entity_pair_key].append(record)

    negatives: list[dict] = []
    for _entity_pair, group in groups.items():
        if len(group) < 2:
            continue
        candidates: list[tuple[tuple[int, float, int, int], dict]] = []
        for left, right in combinations(group, 2):
            if left.cluster_id == right.cluster_id or left.event_type == right.event_type:
                continue
            if left.embedding is None or right.embedding is None:
                continue
            delta = time_delta_days(left, right)
            if delta is None or delta > 120:
                continue
            cosine = cosine_similarity(left.embedding, right.embedding)
            _append_candidate(
                candidates,
                left,
                right,
                neg_type="same_entity_diff_type",
                score_delta=delta,
                cosine=cosine,
            )
        candidates.sort(key=lambda item: item[0])
        negatives.extend(payload for _, payload in candidates[:per_group_cap])
    return negatives


def build_semantic_neighbor_same_type_negatives(
    records: Sequence[ArticleRecord],
    *,
    k: int,
    max_per_article: int = 2,
) -> list[dict]:
    if not _HAS_SKLEARN or len(records) < 3:
        return []
    usable = [record for record in records if record.embedding is not None]
    if len(usable) < 3:
        return []

    matrix = np.stack([record.embedding for record in usable]).astype(np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9
    matrix = matrix / norms
    nn = NearestNeighbors(
        n_neighbors=min(len(usable), max(3, k + 1)),
        metric="cosine",
        algorithm="brute",
    )
    nn.fit(matrix)
    distances, indices = nn.kneighbors(matrix)

    negatives: list[dict] = []
    seen: set[tuple[int, int]] = set()
    for row_idx, record in enumerate(usable):
        kept = 0
        for dist, nbr_idx in zip(distances[row_idx][1:], indices[row_idx][1:]):
            other = usable[int(nbr_idx)]
            if record.cluster_id == other.cluster_id:
                continue
            if record.event_type != other.event_type:
                continue
            if record.entity_pair_key == other.entity_pair_key:
                continue
            delta = time_delta_days(record, other)
            if delta is None or delta > 14:
                continue
            cosine = float(1.0 - dist)
            if cosine < 0.72 or cosine >= 0.95:
                continue
            key = pair_key(record.article_id, other.article_id)
            if key in seen:
                continue
            seen.add(key)
            negatives.append(
                record_pair(
                    record,
                    other,
                    label=0,
                    cosine=cosine,
                    neg_type="semantic_neighbor_same_type",
                )
            )
            kept += 1
            if kept >= max_per_article:
                break
    negatives.sort(key=lambda row: (row["time_delta_days"], -row["cosine_similarity"]))
    return negatives


def build_random_background_negatives(
    records: Sequence[ArticleRecord],
    *,
    target: int,
    used_pairs: set[tuple[int, int]],
    rng: random.Random,
) -> list[dict]:
    if target <= 0 or len(records) < 2:
        return []

    negatives: list[dict] = []
    attempts = 0
    max_attempts = target * 40
    while len(negatives) < target and attempts < max_attempts:
        attempts += 1
        left, right = rng.sample(records, 2)
        if left.cluster_id == right.cluster_id:
            continue
        if left.embedding is None or right.embedding is None:
            continue
        key = pair_key(left.article_id, right.article_id)
        if key in used_pairs:
            continue
        delta = time_delta_days(left, right)
        if delta is None or delta > 180:
            continue
        if left.entity_pair_key == right.entity_pair_key and left.event_type == right.event_type:
            continue
        cosine = cosine_similarity(left.embedding, right.embedding)
        negatives.append(
            record_pair(
                left,
                right,
                label=0,
                cosine=cosine,
                neg_type="random_background",
            )
        )
        used_pairs.add(key)
    return negatives


def take_from_pool(
    pool: Sequence[dict],
    *,
    target: int,
    used_pairs: set[tuple[int, int]],
) -> list[dict]:
    if target <= 0:
        return []
    selected: list[dict] = []
    for row in pool:
        key = pair_key(int(row["article_id_1"]), int(row["article_id_2"]))
        if key in used_pairs:
            continue
        selected.append(row)
        used_pairs.add(key)
        if len(selected) >= target:
            break
    return selected


def summarize(dataset: Sequence[dict]) -> dict:
    negatives = [row for row in dataset if row["label"] == 0]
    positives = [row for row in dataset if row["label"] == 1]
    negative_types = Counter(row.get("neg_type", "unknown") for row in negatives)
    return {
        "total_pairs": len(dataset),
        "positive_count": len(positives),
        "negative_count": len(negatives),
        "positive_negative_ratio": round(len(positives) / max(len(negatives), 1), 6),
        "positive_same_event_type": sum(1 for row in positives if row["event_type_1"] == row["event_type_2"]),
        "negative_same_event_type": sum(1 for row in negatives if row["event_type_1"] == row["event_type_2"]),
        "positive_same_day": sum(1 for row in positives if row["time_delta_days"] == 0),
        "negative_same_day": sum(1 for row in negatives if row["time_delta_days"] == 0),
        "negative_type_distribution": dict(negative_types),
        "positive_time_delta_distribution": {
            "min": min((row["time_delta_days"] for row in positives), default=None),
            "max": max((row["time_delta_days"] for row in positives), default=None),
            "mean": round(
                float(np.mean([row["time_delta_days"] for row in positives])) if positives else 0.0,
                4,
            ),
        },
        "negative_time_delta_distribution": {
            "min": min((row["time_delta_days"] for row in negatives), default=None),
            "max": max((row["time_delta_days"] for row in negatives), default=None),
            "mean": round(
                float(np.mean([row["time_delta_days"] for row in negatives])) if negatives else 0.0,
                4,
            ),
        },
    }


def main() -> None:
    args = parse_args()
    rng = random.Random(args.seed)

    clusters, article_to_cluster = load_cluster_mapping(args.mapping_path)
    records = load_checkpoint_records(
        set(article_to_cluster.keys()),
        article_to_cluster=article_to_cluster,
    )
    hydrate_records(records, include_embeddings=True, include_news_metadata=False)
    records = {
        article_id: record
        for article_id, record in records.items()
        if record.embedding is not None and record.published_dt is not None
    }

    filtered_clusters: dict[str, list[int]] = {}
    for cluster_id, article_ids in clusters.items():
        kept = [article_id for article_id in article_ids if article_id in records]
        if kept:
            filtered_clusters[cluster_id] = kept

    positives = build_positive_pairs(
        records,
        filtered_clusters,
        max_per_cluster=args.max_positives_per_cluster,
        max_total=args.max_positives,
        rng=rng,
    )
    positive_pairs = {pair_key(row["article_id_1"], row["article_id_2"]) for row in positives}

    all_records = list(records.values())
    same_entity_same_type_pool = build_same_entity_same_type_far_negatives(all_records)
    same_entity_diff_type_pool = build_same_entity_diff_type_negatives(all_records)
    semantic_neighbor_pool = build_semantic_neighbor_same_type_negatives(
        all_records,
        k=args.semantic_k,
    )

    target_negatives = int(round(len(positives) * args.negative_ratio))
    neg_targets = {
        neg_type: int(target_negatives * ratio)
        for neg_type, ratio in NEGATIVE_MIX.items()
    }
    neg_targets["same_entity_same_type_far"] += target_negatives - sum(neg_targets.values())

    used_pairs = set(positive_pairs)
    negatives: list[dict] = []
    negatives.extend(
        take_from_pool(
            same_entity_same_type_pool,
            target=neg_targets["same_entity_same_type_far"],
            used_pairs=used_pairs,
        )
    )
    negatives.extend(
        take_from_pool(
            same_entity_diff_type_pool,
            target=neg_targets["same_entity_diff_type"],
            used_pairs=used_pairs,
        )
    )
    negatives.extend(
        take_from_pool(
            semantic_neighbor_pool,
            target=neg_targets["semantic_neighbor_same_type"],
            used_pairs=used_pairs,
        )
    )
    negatives.extend(
        build_random_background_negatives(
            all_records,
            target=neg_targets["random_background"],
            used_pairs=used_pairs,
            rng=rng,
        )
    )

    if len(negatives) < target_negatives:
        refill_pool = same_entity_same_type_pool + same_entity_diff_type_pool + semantic_neighbor_pool
        negatives.extend(
            take_from_pool(
                refill_pool,
                target=target_negatives - len(negatives),
                used_pairs=used_pairs,
            )
        )

    dataset = positives + negatives[:target_negatives]
    rng.shuffle(dataset)
    stats = summarize(dataset)

    print(json.dumps(stats, indent=2, ensure_ascii=False))

    if args.dry_run:
        return

    output_path = Path(args.output)
    stats_path = Path(args.stats_output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(dataset, handle, ensure_ascii=False, indent=2)
    with stats_path.open("w", encoding="utf-8") as handle:
        json.dump(stats, handle, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
