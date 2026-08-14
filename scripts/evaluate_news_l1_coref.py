#!/usr/bin/env python3
from __future__ import annotations

import argparse
import itertools
import math
import re
from collections import Counter, defaultdict
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

from db_runtime_config import require_database_password


ROUNDUP_RE = re.compile(
    r"(live updates?|daily brief|news roundup|meet the press|morning bid|market today|the latest|more - bloomberg)",
    re.IGNORECASE,
)
GENERIC_RE = re.compile(
    r"^(video|speech|remarks|opening remarks|press conference|joint press conference|delete)$",
    re.IGNORECASE,
)
NOISE_RE = re.compile(
    r"(exchange rate|foreign exchange|อัตราแลกเปลี่ยน|stocks|nasdaq|s&p|dow jones|markets wrap)",
    re.IGNORECASE,
)
EVENT_SIGNAL_RE = re.compile(
    r"\b("
    r"accuses?|agrees?|announces?|appoints?|approves?|arrests?|attacks?|backs?|"
    r"bans?|blocks?|ceasefire|condemns?|cuts?|deploys?|elections?|extends?|"
    r"hits?|imposes?|kills?|launches?|meets?|meeting|negotiat(?:e|es|ions?)|"
    r"passes?|peace|policy|protests?|rejects?|resigns?|sanctions?|signs?|"
    r"strikes?|summit|supports?|suspends?|tariffs?|talks?|threatens?|urges?|"
    r"visits?|votes?|warns?"
    r")\b",
    re.IGNORECASE,
)
INSTITUTIONAL_TEMPLATE_RE = re.compile(
    r"\b("
    r"meeting of the north atlantic council|"
    r"ministerial meeting of the north atlantic council|"
    r"north atlantic council meeting|"
    r"secretary general'?s press conference|"
    r"doorstep statement by the nato secretary general|"
    r"pre-ministerial press conference of the nato secretary general"
    r")\b",
    re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate L1 coref runs with silver/proxy metrics.")
    parser.add_argument("--host", default="192.168.207.171")
    parser.add_argument("--port", type=int, default=54333)
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--dbname", default="news")
    parser.add_argument("--run-id", action="append", required=True)
    parser.add_argument(
        "--common-news-ids",
        action="store_true",
        help="Evaluate only news_id values present in every requested run_id.",
    )
    return parser.parse_args()


def connect(args: argparse.Namespace) -> Any:
    return psycopg2.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=require_database_password(),
        dbname=args.dbname,
        connect_timeout=20,
    )


def comb2(n: int) -> int:
    return n * (n - 1) // 2


def title_ok(title: str | None) -> bool:
    text = (title or "").strip()
    if len(text) < 35:
        return False
    if GENERIC_RE.search(text) or ROUNDUP_RE.search(text):
        return False
    return True


def title_has_event_signal(title: str | None) -> bool:
    return bool(EVENT_SIGNAL_RE.search(title or ""))


def template_like_title_group(members: list[dict[str, Any]]) -> bool:
    if len(members) < 4:
        return False
    title = members[0].get("title")
    if INSTITUTIONAL_TEMPLATE_RE.search(title or ""):
        return True
    if title_has_event_signal(title):
        return False
    fam_actions = {f"{row.get('event_family') or ''}/{row.get('event_action') or ''}" for row in members}
    pairs = {f"{row.get('initiator') or ''}→{row.get('target') or ''}" for row in members}
    return len(fam_actions) > 2 or len(pairs) > 3


def token_set(text: str | None) -> set[str]:
    value = re.sub(r"[^\w\u4e00-\u9fff]+", " ", (text or "").lower())
    tokens: set[str] = set()
    for part in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", value):
        if re.fullmatch(r"[\u4e00-\u9fff]+", part):
            if len(part) == 1:
                tokens.add(part)
            else:
                tokens.update(part[i : i + 2] for i in range(len(part) - 1))
        elif len(part) >= 3:
            tokens.add(part)
    return tokens


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def load_rows(conn: Any, run_id: str) -> list[dict[str, Any]]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT m.cluster_id,
                   m.news_id,
                   c.article_count,
                   c.event_family,
                   c.event_action,
                   c.initiator,
                   c.target,
                   p.title_hash,
                   n.title
            FROM public.event_coref_members AS m
            JOIN public.event_coref_clusters AS c ON c.cluster_id = m.cluster_id
            JOIN public.news_l1_prep AS p ON p.news_id = m.news_id
            JOIN public.news AS n ON n.id = m.news_id
            WHERE c.run_id = %s
            """,
            (run_id,),
        )
        return [dict(row) for row in cur.fetchall()]


def evaluate_rows(rows: list[dict[str, Any]], run_id: str) -> dict[str, Any]:
    by_cluster: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_title: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in rows:
        by_cluster[str(row["cluster_id"])].append(row)
        if row.get("title_hash") and title_ok(row.get("title")):
            by_title[str(row["title_hash"])].append(row)

    cluster_sizes = [len(v) for v in by_cluster.values()]
    non_singletons = [members for members in by_cluster.values() if len(members) > 1]

    # Silver positive recall: exact non-generic duplicate titles should cluster.
    silver_groups = [members for members in by_title.values() if len(members) >= 2]
    silver_total_pairs = 0
    silver_same_pairs = 0
    silver_rows = 0
    silver_largest_rows = 0
    filtered_silver_total_pairs = 0
    filtered_silver_same_pairs = 0
    filtered_silver_rows = 0
    filtered_silver_largest_rows = 0
    split_groups = 0
    split_rows = 0
    split_same_fields_groups = 0
    filtered_silver_groups = 0
    filtered_split_groups = 0
    filtered_split_rows = 0
    template_silver_groups = 0
    template_silver_rows = 0
    for members in silver_groups:
        silver_rows += len(members)
        counts = Counter(row["cluster_id"] for row in members)
        same_pairs = sum(comb2(count) for count in counts.values())
        total_pairs = comb2(len(members))
        largest_rows = max(counts.values())
        silver_total_pairs += total_pairs
        silver_same_pairs += same_pairs
        silver_largest_rows += largest_rows
        is_template = template_like_title_group(members)
        if is_template:
            template_silver_groups += 1
            template_silver_rows += len(members)
        else:
            filtered_silver_groups += 1
            filtered_silver_rows += len(members)
            filtered_silver_total_pairs += total_pairs
            filtered_silver_same_pairs += same_pairs
            filtered_silver_largest_rows += largest_rows
        if len(counts) > 1:
            split_groups += 1
            split_rows += len(members)
            fam_actions = {f"{row.get('event_family')}/{row.get('event_action')}" for row in members}
            pairs = {f"{row.get('initiator') or ''}→{row.get('target') or ''}" for row in members}
            if len(fam_actions) == 1 and len(pairs) == 1:
                split_same_fields_groups += 1
            if not is_template:
                filtered_split_groups += 1
                filtered_split_rows += len(members)

    # Precision risk proxies.
    low_sim_clusters = 0
    sampled_pair_count = 0
    sampled_low_pair_count = 0
    generic_members = 0
    noise_members = 0
    mixed_field_clusters = 0
    template_title_clusters = 0
    template_title_members = 0
    for members in non_singletons:
        titles = [row.get("title") or "" for row in members]
        generic_members += sum(1 for title in titles if GENERIC_RE.search(title) or ROUNDUP_RE.search(title))
        noise_members += sum(1 for title in titles if NOISE_RE.search(title))
        if len({f"{row.get('event_family')}/{row.get('event_action')}" for row in members}) > 1:
            mixed_field_clusters += 1
        title_counts = Counter(titles)
        dominant_title, dominant_count = title_counts.most_common(1)[0]
        dominant_members = [row for row in members if (row.get("title") or "") == dominant_title]
        if dominant_count >= 6 and template_like_title_group(dominant_members):
            template_title_clusters += 1
            template_title_members += dominant_count
        if len(members) <= 1:
            continue
        tokenized = [token_set(title) for title in titles]
        pairs = list(itertools.combinations(range(len(members)), 2))
        if len(pairs) > 80:
            pairs = pairs[:80]
        low = 0
        for i, j in pairs:
            score = jaccard(tokenized[i], tokenized[j])
            if score < 0.12:
                low += 1
        sampled_pair_count += len(pairs)
        sampled_low_pair_count += low
        if pairs and low / len(pairs) > 0.6 and len(members) >= 4:
            low_sim_clusters += 1

    return {
        "run_id": run_id,
        "members": len(rows),
        "clusters": len(by_cluster),
        "singletons": sum(1 for size in cluster_sizes if size == 1),
        "non_singleton_clusters": len(non_singletons),
        "non_singleton_members": sum(len(members) for members in non_singletons),
        "max_cluster": max(cluster_sizes) if cluster_sizes else 0,
        "silver_title_groups": len(silver_groups),
        "silver_title_rows": silver_rows,
        "silver_pair_recall": silver_same_pairs / silver_total_pairs if silver_total_pairs else 0.0,
        "silver_member_best_cluster_recall": silver_largest_rows / silver_rows if silver_rows else 0.0,
        "filtered_silver_title_groups": filtered_silver_groups,
        "filtered_silver_title_rows": filtered_silver_rows,
        "filtered_silver_pair_recall": filtered_silver_same_pairs / filtered_silver_total_pairs if filtered_silver_total_pairs else 0.0,
        "filtered_silver_member_best_cluster_recall": filtered_silver_largest_rows / filtered_silver_rows if filtered_silver_rows else 0.0,
        "silver_split_groups": split_groups,
        "silver_split_rows": split_rows,
        "silver_split_same_fields_groups": split_same_fields_groups,
        "filtered_silver_split_groups": filtered_split_groups,
        "filtered_silver_split_rows": filtered_split_rows,
        "template_silver_groups_excluded": template_silver_groups,
        "template_silver_rows_excluded": template_silver_rows,
        "generic_or_roundup_member_rate_non_singleton": generic_members / max(1, sum(len(m) for m in non_singletons)),
        "market_noise_member_rate_non_singleton": noise_members / max(1, sum(len(m) for m in non_singletons)),
        "low_title_similarity_pair_rate": sampled_low_pair_count / max(1, sampled_pair_count),
        "low_title_similarity_clusters": low_sim_clusters,
        "mixed_field_clusters": mixed_field_clusters,
        "template_title_clusters": template_title_clusters,
        "template_title_members": template_title_members,
    }


def evaluate_run(conn: Any, run_id: str) -> dict[str, Any]:
    return evaluate_rows(load_rows(conn, run_id), run_id)


def main() -> None:
    args = parse_args()
    conn = connect(args)
    common_count = None
    try:
        if args.common_news_ids:
            loaded = {run_id: load_rows(conn, run_id) for run_id in args.run_id}
            common_ids = set.intersection(
                *(set(row["news_id"] for row in rows) for rows in loaded.values())
            )
            common_count = len(common_ids)
            results = [
                evaluate_rows(
                    [row for row in loaded[run_id] if row["news_id"] in common_ids],
                    run_id,
                )
                for run_id in args.run_id
            ]
        else:
            results = [evaluate_run(conn, run_id) for run_id in args.run_id]
    finally:
        conn.close()

    if common_count is not None:
        print(f"common_news_ids: {common_count}")

    for result in results:
        print(f"== {result['run_id']} ==")
        for key, value in result.items():
            if key == "run_id":
                continue
            if isinstance(value, float):
                print(f"{key}: {value:.6f}")
            else:
                print(f"{key}: {value}")

    if len(results) >= 2:
        base = results[0]
        print("== delta_vs_first ==")
        for result in results[1:]:
            print(f"-- {result['run_id']} vs {base['run_id']} --")
            for key in (
                "non_singleton_members",
                "filtered_silver_pair_recall",
                "filtered_silver_member_best_cluster_recall",
                "generic_or_roundup_member_rate_non_singleton",
                "market_noise_member_rate_non_singleton",
                "low_title_similarity_pair_rate",
                "template_title_clusters",
                "max_cluster",
            ):
                print(f"{key}: {result[key] - base[key]:.6f}" if isinstance(result[key], float) else f"{key}: {result[key] - base[key]}")


if __name__ == "__main__":
    main()
