#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Sequence

from l1_review_utils import (
    ArticleRecord,
    cosine_similarity,
    hydrate_records,
    load_checkpoint_records,
    load_cluster_mapping,
    time_delta_days,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export L1 cluster audit samples.")
    parser.add_argument(
        "--mapping-path",
        default="/root/data/globemind/data/event_coref_mapping_layer1.jsonl",
        help="L1 cluster mapping JSONL",
    )
    parser.add_argument(
        "--output-dir",
        default="/root/data/globemind/data/l1_audit",
        help="Audit output directory",
    )
    parser.add_argument(
        "--cluster-sample-size",
        type=int,
        default=40,
        help="Number of non-singleton clusters to export for purity review",
    )
    parser.add_argument(
        "--merge-sample-size",
        type=int,
        default=80,
        help="Number of cross-cluster candidate pairs to export for recall review",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed",
    )
    return parser.parse_args()


def summarize_cluster(cluster_id: str, members: Sequence[ArticleRecord]) -> dict:
    dates = [member.published_at for member in members if member.published_at]
    event_types = Counter(member.event_type for member in members)
    entity_pairs = Counter(member.entity_pair_key for member in members)
    return {
        "cluster_id": cluster_id,
        "size": len(members),
        "dominant_event_type": event_types.most_common(1)[0][0] if event_types else "other",
        "event_type_distribution": dict(event_types),
        "entity_pair_distribution": dict(entity_pairs),
        "date_range": {
            "start": min(dates) if dates else None,
            "end": max(dates) if dates else None,
        },
        "review_label": None,
        "notes": "",
        "members": [
            {
                "article_id": member.article_id,
                "published_at": member.published_at,
                "event_type": member.event_type,
                "initiator": member.initiator,
                "target": member.target,
                "entity_pair_key": member.entity_pair_key,
                "title": member.title,
                "source": member.source,
                "abstract": member.abstract,
            }
            for member in sorted(members, key=lambda row: (row.published_at or "", row.article_id))
        ],
    }


def sample_clusters(
    clusters: dict[str, list[int]],
    records: dict[int, ArticleRecord],
    *,
    sample_size: int,
    rng: random.Random,
) -> list[dict]:
    non_singletons = []
    for cluster_id, article_ids in clusters.items():
        members = [records[article_id] for article_id in article_ids if article_id in records]
        if len(members) >= 2:
            non_singletons.append((cluster_id, members))
    non_singletons.sort(key=lambda item: (-len(item[1]), item[0]))

    if len(non_singletons) <= sample_size:
        chosen = non_singletons
    else:
        head = non_singletons[: max(5, sample_size // 4)]
        tail = non_singletons[max(5, sample_size // 4) :]
        rng.shuffle(tail)
        chosen = head + tail[: max(0, sample_size - len(head))]

    return [summarize_cluster(cluster_id, members) for cluster_id, members in chosen]


def build_merge_candidates(
    records: Sequence[ArticleRecord],
    *,
    sample_size: int,
) -> list[dict]:
    by_entity_type: dict[tuple[str, str], list[ArticleRecord]] = defaultdict(list)
    for record in records:
        by_entity_type[(record.entity_pair_key, record.event_type)].append(record)

    candidates: list[tuple[tuple[int, float, int, int], dict]] = []
    for (entity_pair, event_type), group in by_entity_type.items():
        if len(group) < 2:
            continue
        for left, right in combinations(group, 2):
            if left.cluster_id == right.cluster_id:
                continue
            if left.embedding is None or right.embedding is None:
                continue
            delta = time_delta_days(left, right)
            if delta is None or delta > 7:
                continue
            cosine = cosine_similarity(left.embedding, right.embedding)
            if cosine < 0.82:
                continue
            candidates.append(
                (
                    (delta, -cosine, min(left.article_id, right.article_id), max(left.article_id, right.article_id)),
                    {
                        "candidate_type": "same_entity_same_type_close_time",
                        "review_label": None,
                        "notes": "",
                        "entity_pair_key": entity_pair,
                        "event_type": event_type,
                        "time_delta_days": delta,
                        "cosine_similarity": round(cosine, 6),
                        "left": {
                            "article_id": left.article_id,
                            "cluster_id": left.cluster_id,
                            "published_at": left.published_at,
                            "title": left.title,
                            "source": left.source,
                            "initiator": left.initiator,
                            "target": left.target,
                            "abstract": left.abstract,
                        },
                        "right": {
                            "article_id": right.article_id,
                            "cluster_id": right.cluster_id,
                            "published_at": right.published_at,
                            "title": right.title,
                            "source": right.source,
                            "initiator": right.initiator,
                            "target": right.target,
                            "abstract": right.abstract,
                        },
                    },
                )
            )

    candidates.sort(key=lambda item: item[0])
    deduped: list[dict] = []
    seen: set[tuple[int, int]] = set()
    for _, candidate in candidates:
        key = tuple(sorted((candidate["left"]["article_id"], candidate["right"]["article_id"])))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
        if len(deduped) >= sample_size:
            break
    return deduped


def write_jsonl(path: Path, rows: Sequence[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    args = parse_args()
    rng = random.Random(args.seed)

    clusters, article_to_cluster = load_cluster_mapping(args.mapping_path)
    records = load_checkpoint_records(
        set(article_to_cluster.keys()),
        article_to_cluster=article_to_cluster,
    )
    hydrate_records(records, include_embeddings=True, include_news_metadata=True)
    records = {
        article_id: record
        for article_id, record in records.items()
        if record.embedding is not None and record.published_dt is not None
    }

    cluster_rows = sample_clusters(
        clusters,
        records,
        sample_size=args.cluster_sample_size,
        rng=rng,
    )
    merge_rows = build_merge_candidates(
        list(records.values()),
        sample_size=args.merge_sample_size,
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cluster_path = output_dir / "cluster_purity_sample.jsonl"
    merge_path = output_dir / "missed_merge_candidates.jsonl"
    summary_path = output_dir / "audit_summary.json"

    write_jsonl(cluster_path, cluster_rows)
    write_jsonl(merge_path, merge_rows)
    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "cluster_sample_size": len(cluster_rows),
                "merge_candidate_size": len(merge_rows),
                "cluster_sample_path": str(cluster_path),
                "merge_candidate_path": str(merge_path),
            },
            handle,
            ensure_ascii=False,
            indent=2,
        )

    print(
        json.dumps(
            {
                "cluster_sample_size": len(cluster_rows),
                "merge_candidate_size": len(merge_rows),
                "output_dir": str(output_dir),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
