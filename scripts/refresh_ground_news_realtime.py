#!/usr/bin/env python3
"""Refresh Ground News derived story tables for the recent ingestion window.

The upstream prep/extraction workers run continuously. This script closes the
product loop by recomputing recent L1 clusters, L1.5 segments, L2 chains, and
source breakdowns without replacing the entire L1 history.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import Counter
from datetime import date, datetime, time as datetime_time, timedelta
from pathlib import Path
from typing import Any, Iterable

from psycopg2.extras import Json, RealDictCursor, execute_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts import build_story_source_breakdown as source_breakdown
from scripts import run_news_l15_segments as l15
from scripts import run_news_l1_fast_coref as l1_base
from scripts import run_news_l1_fast_coref_experimental as l1_exp
from scripts import run_news_l2_storylines as l2
from scripts.ensure_news_l1_infra import add_db_args, connect, ensure_news_l1_infra

LOGGER = logging.getLogger("refresh_ground_news_realtime")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Incrementally refresh recent Ground News story products."
    )
    add_db_args(parser)
    parser.add_argument("--l1-run-id", default="fast_l1_v2")
    parser.add_argument("--l15-run-id", default="fast_l15_v1")
    parser.add_argument("--l2-run-id", default="fast_l2_v1")
    parser.add_argument("--lookback-days", type=int, default=7)
    parser.add_argument("--future-days", type=int, default=1)
    parser.add_argument("--target-start", help="YYYY-MM-DD override for the refresh window start.")
    parser.add_argument("--target-end", help="YYYY-MM-DD override for the refresh window end.")
    parser.add_argument("--body-chars", type=int, default=300)
    parser.add_argument("--max-rows", type=int)
    parser.add_argument("--max-candidates", type=int, default=650)
    parser.add_argument("--include-general-news", action="store_true")
    parser.add_argument("--disable-exact-title-union", action="store_true")
    parser.add_argument("--min-chain-segments", type=int, default=2)
    parser.add_argument("--min-sources-ready", type=int, default=3)
    parser.add_argument("--source-breakdown-table", default="story_source_breakdown")
    parser.add_argument("--skip-l2", action="store_true")
    parser.add_argument("--skip-source-breakdown", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--lock-key", type=int, default=83004217)
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.fromisoformat(value).date()


def refresh_window(args: argparse.Namespace) -> tuple[date, date, datetime, datetime]:
    today = date.today()
    start = parse_date(args.target_start) or (today - timedelta(days=args.lookback_days))
    end = parse_date(args.target_end) or (today + timedelta(days=args.future_days))
    if end < start:
        raise ValueError(f"target end {end} is before target start {start}")
    start_dt = datetime.combine(start, datetime_time.min)
    end_dt = datetime.combine(end, datetime_time.max)
    return start, end, start_dt, end_dt


def acquire_lock(conn: Any, key: int) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (key,))
        return bool(cur.fetchone()[0])


def release_lock(conn: Any, key: int) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", (key,))
    conn.commit()


def rows_for_ids(conn: Any, sql: str, params: tuple[Any, ...]) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return {str(row[0]) for row in cur.fetchall()}


def existing_l1_cluster_ids(
    conn: Any,
    *,
    run_id: str,
    start_date: date,
    end_date: date,
    record_ids: Iterable[int],
) -> set[str]:
    cluster_ids = rows_for_ids(
        conn,
        """
        SELECT cluster_id
        FROM public.event_coref_clusters
        WHERE run_id = %s
          AND COALESCE(end_date, start_date) >= %s
          AND COALESCE(start_date, end_date) <= %s
        """,
        (run_id, start_date, end_date),
    )
    ids = list(record_ids)
    if ids:
        cluster_ids.update(
            rows_for_ids(
                conn,
                """
                SELECT DISTINCT cluster_id
                FROM public.event_coref_members
                WHERE run_id = %s
                  AND news_id = ANY(%s)
                """,
                (run_id, ids),
            )
        )
    return cluster_ids


def stable_l1_cluster_ids(run_id: str, clusters: dict[str, list[int]]) -> set[str]:
    return {
        l1_base.stable_cluster_id(run_id, sorted(members))
        for members in clusters.values()
        if members
    }


def upsert_l1_clusters(
    conn: Any,
    clusters: dict[str, list[int]],
    records: list[l1_base.Record],
    *,
    run_id: str,
) -> tuple[int, int]:
    lookup = {row.news_id: row for row in records}
    cluster_values: list[tuple[Any, ...]] = []
    member_values: list[tuple[Any, ...]] = []

    for members in sorted(clusters.values(), key=lambda item: (-len(item), min(item))):
        rows = [lookup[news_id] for news_id in sorted(members) if news_id in lookup]
        if not rows:
            continue
        dates = [row.published_date for row in rows if row.published_date]
        cluster_id = l1_base.stable_cluster_id(run_id, [row.news_id for row in rows])
        event_family = l1_base.mode([row.event_family for row in rows], "other")
        event_action = l1_base.mode([row.event_action for row in rows], "other")
        event_domain = l1_base.mode([row.event_domain for row in rows], "political")
        initiator = l1_base.mode([row.initiator for row in rows], None)
        target = l1_base.mode([row.target for row in rows], None)
        location = l1_base.mode([row.location for row in rows], None)
        tone = l1_base.mode([row.tone for row in rows], "neutral")
        quality = "singleton" if len(rows) == 1 else "fast_rule_candidate"
        title = rows[0].title[:200]
        cluster_values.append(
            (
                cluster_id,
                run_id,
                len(rows),
                event_domain,
                event_family,
                event_family,
                event_action,
                initiator,
                target,
                location,
                tone,
                event_action,
                min(dates) if dates else None,
                max(dates) if dates else None,
                quality,
                title,
            )
        )
        for row in rows:
            member_values.append(
                (
                    cluster_id,
                    run_id,
                    row.news_id,
                    row.event_domain,
                    row.event_family,
                    row.event_family,
                    row.event_action,
                    row.initiator,
                    row.target,
                    row.event_action,
                    row.published_at,
                    1.0,
                )
            )

    if not cluster_values:
        return 0, 0

    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO public.event_coref_clusters (
                cluster_id, run_id, article_count, event_domain, event_type,
                event_family, event_action, initiator, target, location, tone,
                dominant_trigger, start_date, end_date, cluster_quality, title
            )
            VALUES %s
            ON CONFLICT (cluster_id) DO UPDATE SET
                article_count = EXCLUDED.article_count,
                event_domain = EXCLUDED.event_domain,
                event_type = EXCLUDED.event_type,
                event_family = EXCLUDED.event_family,
                event_action = EXCLUDED.event_action,
                initiator = EXCLUDED.initiator,
                target = EXCLUDED.target,
                location = EXCLUDED.location,
                tone = EXCLUDED.tone,
                dominant_trigger = EXCLUDED.dominant_trigger,
                start_date = EXCLUDED.start_date,
                end_date = EXCLUDED.end_date,
                cluster_quality = EXCLUDED.cluster_quality,
                title = EXCLUDED.title,
                updated_at = now()
            """,
            cluster_values,
            page_size=1000,
        )
        execute_values(
            cur,
            """
            INSERT INTO public.event_coref_members (
                cluster_id, run_id, news_id, event_domain, event_type,
                event_family, event_action, initiator, target, trigger,
                published_at, membership_score
            )
            VALUES %s
            ON CONFLICT (cluster_id, news_id) DO UPDATE SET
                run_id = EXCLUDED.run_id,
                event_domain = EXCLUDED.event_domain,
                event_type = EXCLUDED.event_type,
                event_family = EXCLUDED.event_family,
                event_action = EXCLUDED.event_action,
                initiator = EXCLUDED.initiator,
                target = EXCLUDED.target,
                trigger = EXCLUDED.trigger,
                published_at = EXCLUDED.published_at,
                membership_score = EXCLUDED.membership_score
            """,
            member_values,
            page_size=3000,
        )
    conn.commit()
    return len(cluster_values), len(member_values)


def ids_from_any(conn: Any, sql: str, params: tuple[Any, ...]) -> set[str]:
    if any(isinstance(value, list) and not value for value in params):
        return set()
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return {str(row[0]) for row in cur.fetchall()}


def delete_l2_chains(conn: Any, *, run_id: str, chain_ids: set[str]) -> int:
    if not chain_ids:
        return 0
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM public.event_l2_chains
            WHERE run_id = %s
              AND chain_id = ANY(%s)
            """,
            (run_id, list(chain_ids)),
        )
        deleted = cur.rowcount
    conn.commit()
    return deleted


def delete_l15_segments(conn: Any, *, run_id: str, cluster_ids: set[str]) -> int:
    if not cluster_ids:
        return 0
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM public.event_l15_segments
            WHERE run_id = %s
              AND l1_cluster_id = ANY(%s)
            """,
            (run_id, list(cluster_ids)),
        )
        deleted = cur.rowcount
    conn.commit()
    return deleted


def delete_story_assets(conn: Any, *, l1_run_id: str, cluster_ids: set[str]) -> tuple[int, int]:
    if not cluster_ids:
        return 0, 0
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM public.story_source_breakdown
            WHERE story_id = ANY(%s)
            """,
            (list(cluster_ids),),
        )
        breakdown_deleted = cur.rowcount
        cur.execute(
            """
            DELETE FROM public.story_cover_assets
            WHERE run_id = %s
              AND cluster_id = ANY(%s)
            """,
            (l1_run_id, list(cluster_ids)),
        )
        cover_deleted = cur.rowcount
    conn.commit()
    return breakdown_deleted, cover_deleted


def delete_l1_clusters(conn: Any, *, run_id: str, cluster_ids: set[str]) -> int:
    if not cluster_ids:
        return 0
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM public.event_coref_clusters
            WHERE run_id = %s
              AND cluster_id = ANY(%s)
            """,
            (run_id, list(cluster_ids)),
        )
        deleted = cur.rowcount
    conn.commit()
    return deleted


def impacted_l15_segment_ids(conn: Any, *, run_id: str, cluster_ids: set[str]) -> set[str]:
    if not cluster_ids:
        return set()
    return ids_from_any(
        conn,
        """
        SELECT segment_id
        FROM public.event_l15_segments
        WHERE run_id = %s
          AND l1_cluster_id = ANY(%s)
        """,
        (run_id, list(cluster_ids)),
    )


def impacted_l2_chain_ids(
    conn: Any,
    *,
    run_id: str,
    cluster_ids: set[str],
    segment_ids: set[str],
) -> set[str]:
    chain_ids: set[str] = set()
    if cluster_ids:
        chain_ids.update(
            ids_from_any(
                conn,
                """
                SELECT DISTINCT chain_id
                FROM public.event_l2_chain_segments
                WHERE run_id = %s
                  AND l1_cluster_id = ANY(%s)
                """,
                (run_id, list(cluster_ids)),
            )
        )
    if segment_ids:
        chain_ids.update(
            ids_from_any(
                conn,
                """
                SELECT DISTINCT chain_id
                FROM public.event_l2_chain_segments
                WHERE run_id = %s
                  AND segment_id = ANY(%s)
                """,
                (run_id, list(segment_ids)),
            )
        )
    return chain_ids


def fetch_l15_articles_for_clusters(
    conn: Any,
    *,
    l1_run_id: str,
    cluster_ids: set[str],
) -> list[l15.Article]:
    if not cluster_ids:
        return []
    ids = list(cluster_ids)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT cluster_id, location, tone, title
            FROM public.event_coref_clusters
            WHERE run_id = %s
              AND cluster_id = ANY(%s)
            """,
            (l1_run_id, ids),
        )
        cluster_meta = {
            str(row["cluster_id"]): {
                "location": l15.clean_text(row.get("location")) or None,
                "tone": l15.clean_text(row.get("tone")) or None,
                "title": l15.clean_text(row.get("title")) or None,
            }
            for row in cur.fetchall()
        }
        cur.execute(
            """
            SELECT m.cluster_id AS l1_cluster_id,
                   m.news_id,
                   m.published_at,
                   COALESCE(n.title, '') AS title,
                   m.event_domain,
                   m.event_family,
                   m.event_action,
                   m.initiator,
                   m.target
            FROM public.event_coref_members AS m
            JOIN public.news AS n ON n.id = m.news_id
            WHERE m.run_id = %s
              AND m.cluster_id = ANY(%s)
            ORDER BY m.cluster_id, m.published_at NULLS LAST, m.news_id
            """,
            (l1_run_id, ids),
        )
        rows = [dict(row) for row in cur.fetchall()]

    articles: list[l15.Article] = []
    for row in rows:
        published_at = row.get("published_at")
        l1_cluster_id = str(row["l1_cluster_id"])
        meta = cluster_meta.get(l1_cluster_id, {})
        articles.append(
            l15.Article(
                l1_cluster_id=l1_cluster_id,
                news_id=int(row["news_id"]),
                published_at=published_at,
                published_date=published_at.date() if isinstance(published_at, datetime) else None,
                title=l15.clean_text(row.get("title")),
                event_domain=l15.clean_text(row.get("event_domain")) or None,
                event_family=l15.clean_text(row.get("event_family")) or None,
                event_action=l15.clean_text(row.get("event_action")) or None,
                initiator=l15.clean_text(row.get("initiator")) or None,
                target=l15.clean_text(row.get("target")) or None,
                location=meta.get("location"),
                tone=meta.get("tone"),
                l1_title=meta.get("title"),
            )
        )
    return articles


def upsert_l15_segments(
    conn: Any,
    segments: dict[str, list[l15.Article]],
    *,
    run_id: str,
    l1_run_id: str,
) -> tuple[int, int]:
    segment_values: list[tuple[Any, ...]] = []
    member_values: list[tuple[Any, ...]] = []

    for segment_id, members in sorted(segments.items(), key=lambda item: (-len(item[1]), item[0])):
        dates = [article.published_date for article in members if article.published_date]
        title = l15.mode([article.title for article in members], members[0].l1_title or members[0].title)
        story_angle = l15.classify_angle(members[0])
        segment_values.append(
            (
                segment_id,
                run_id,
                l1_run_id,
                members[0].l1_cluster_id,
                len(members),
                l15.mode([article.event_domain for article in members], "political"),
                l15.mode([article.event_family for article in members], "other"),
                l15.mode([article.event_action for article in members], "other"),
                story_angle,
                l15.mode([article.initiator for article in members], None),
                l15.mode([article.target for article in members], None),
                l15.mode([article.location for article in members], None),
                l15.mode([article.tone for article in members], "neutral"),
                min(dates) if dates else None,
                max(dates) if dates else None,
                title[:240] if title else None,
            )
        )
        for article in members:
            member_values.append(
                (
                    segment_id,
                    run_id,
                    l1_run_id,
                    article.l1_cluster_id,
                    article.news_id,
                    story_angle,
                    article.event_family,
                    article.event_action,
                    article.published_at,
                    1.0,
                )
            )

    if not segment_values:
        return 0, 0

    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO public.event_l15_segments (
                segment_id, run_id, l1_run_id, l1_cluster_id, article_count,
                event_domain, event_family, event_action, story_angle,
                initiator, target, location, tone, start_date, end_date, title
            )
            VALUES %s
            ON CONFLICT (segment_id) DO UPDATE SET
                article_count = EXCLUDED.article_count,
                event_domain = EXCLUDED.event_domain,
                event_family = EXCLUDED.event_family,
                event_action = EXCLUDED.event_action,
                story_angle = EXCLUDED.story_angle,
                initiator = EXCLUDED.initiator,
                target = EXCLUDED.target,
                location = EXCLUDED.location,
                tone = EXCLUDED.tone,
                start_date = EXCLUDED.start_date,
                end_date = EXCLUDED.end_date,
                title = EXCLUDED.title,
                updated_at = now()
            """,
            segment_values,
            page_size=1000,
        )
        execute_values(
            cur,
            """
            INSERT INTO public.event_l15_members (
                segment_id, run_id, l1_run_id, l1_cluster_id, news_id,
                story_angle, event_family, event_action, published_at, membership_score
            )
            VALUES %s
            ON CONFLICT (segment_id, news_id) DO UPDATE SET
                run_id = EXCLUDED.run_id,
                l1_run_id = EXCLUDED.l1_run_id,
                l1_cluster_id = EXCLUDED.l1_cluster_id,
                story_angle = EXCLUDED.story_angle,
                event_family = EXCLUDED.event_family,
                event_action = EXCLUDED.event_action,
                published_at = EXCLUDED.published_at,
                membership_score = EXCLUDED.membership_score
            """,
            member_values,
            page_size=3000,
        )
    conn.commit()
    return len(segment_values), len(member_values)


def replace_l2_chains(
    conn: Any,
    chains: dict[str, list[l2.Segment]],
    *,
    run_id: str,
    l15_run_id: str,
) -> tuple[int, int]:
    chain_values: list[tuple[Any, ...]] = []
    member_values: list[tuple[Any, ...]] = []
    for chain_id, chain in sorted(chains.items(), key=lambda item: (-len(item[1]), item[0])):
        dates = [seg.start_date for seg in chain if seg.start_date] + [
            seg.end_date for seg in chain if seg.end_date
        ]
        quality = l2.chain_quality_metrics(chain)
        chain_values.append(
            (
                chain_id,
                run_id,
                l15_run_id,
                len(chain),
                sum(seg.article_count for seg in chain),
                l2.family_group(chain[0]),
                l2.mode([seg.event_family for seg in chain], None),
                l2.mode([seg.event_action for seg in chain], None),
                l2.actor_pair_key(chain[0]),
                l2.mode([seg.initiator for seg in chain], None),
                l2.mode([seg.target for seg in chain], None),
                min(dates) if dates else None,
                max(dates) if dates else None,
                l2.chain_title(chain)[:240],
                quality["label"],
                quality["score"],
                Json(quality["flags"]),
            )
        )
        previous = None
        for idx, segment in enumerate(chain, start=1):
            metrics = l2.edge_metrics(previous, segment)
            member_values.append(
                (
                    chain_id,
                    run_id,
                    l15_run_id,
                    segment.segment_id,
                    segment.l1_cluster_id,
                    idx,
                    l2.edge_type(previous, segment),
                    segment.event_family,
                    segment.event_action,
                    segment.story_angle,
                    segment.start_date,
                    segment.end_date,
                    segment.article_count,
                    metrics["edge_weight"],
                    metrics["relation_reason"],
                    metrics["title_similarity"],
                    metrics["shared_topic_count"],
                    metrics["gap_days"],
                )
            )
            previous = segment

    with conn.cursor() as cur:
        cur.execute("DELETE FROM public.event_l2_chains WHERE run_id = %s", (run_id,))
        if chain_values:
            execute_values(
                cur,
                """
                INSERT INTO public.event_l2_chains (
                    chain_id, run_id, l15_run_id, segment_count, article_count,
                    family_group, event_family, event_action, pair_key, initiator, target,
                    start_date, end_date, title, chain_quality, quality_score, risk_flags
                )
                VALUES %s
                ON CONFLICT (chain_id) DO UPDATE SET
                    segment_count = EXCLUDED.segment_count,
                    article_count = EXCLUDED.article_count,
                    family_group = EXCLUDED.family_group,
                    event_family = EXCLUDED.event_family,
                    event_action = EXCLUDED.event_action,
                    pair_key = EXCLUDED.pair_key,
                    initiator = EXCLUDED.initiator,
                    target = EXCLUDED.target,
                    start_date = EXCLUDED.start_date,
                    end_date = EXCLUDED.end_date,
                    title = EXCLUDED.title,
                    chain_quality = EXCLUDED.chain_quality,
                    quality_score = EXCLUDED.quality_score,
                    risk_flags = EXCLUDED.risk_flags,
                    updated_at = now()
                """,
                chain_values,
                page_size=1000,
            )
        if member_values:
            execute_values(
                cur,
                """
                INSERT INTO public.event_l2_chain_segments (
                    chain_id, run_id, l15_run_id, segment_id, l1_cluster_id, segment_order,
                    edge_type, event_family, event_action, story_angle, start_date, end_date,
                    article_count, edge_weight, relation_reason, title_similarity,
                    shared_topic_count, gap_days
                )
                VALUES %s
                ON CONFLICT (chain_id, segment_id) DO UPDATE SET
                    run_id = EXCLUDED.run_id,
                    l15_run_id = EXCLUDED.l15_run_id,
                    l1_cluster_id = EXCLUDED.l1_cluster_id,
                    segment_order = EXCLUDED.segment_order,
                    edge_type = EXCLUDED.edge_type,
                    event_family = EXCLUDED.event_family,
                    event_action = EXCLUDED.event_action,
                    story_angle = EXCLUDED.story_angle,
                    start_date = EXCLUDED.start_date,
                    end_date = EXCLUDED.end_date,
                    article_count = EXCLUDED.article_count,
                    edge_weight = EXCLUDED.edge_weight,
                    relation_reason = EXCLUDED.relation_reason,
                    title_similarity = EXCLUDED.title_similarity,
                    shared_topic_count = EXCLUDED.shared_topic_count,
                    gap_days = EXCLUDED.gap_days
                """,
                member_values,
                page_size=3000,
            )
    conn.commit()
    return len(chain_values), len(member_values)


def current_l1_mapping(conn: Any, *, run_id: str, cluster_ids: set[str]) -> dict[str, list[int]]:
    if not cluster_ids:
        return {}
    clusters: dict[str, list[int]] = {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT cluster_id, news_id
            FROM public.event_coref_members
            WHERE run_id = %s
              AND cluster_id = ANY(%s)
            ORDER BY cluster_id, news_id
            """,
            (run_id, list(cluster_ids)),
        )
        for cluster_id, news_id in cur.fetchall():
            clusters.setdefault(str(cluster_id), []).append(int(news_id))
    return clusters


def rebuild_source_breakdown(
    conn: Any,
    args: argparse.Namespace,
    *,
    cluster_ids: set[str],
) -> int:
    clusters = current_l1_mapping(conn, run_id=args.l1_run_id, cluster_ids=cluster_ids)
    if not clusters:
        return 0
    article_ids = {news_id for ids in clusters.values() for news_id in ids}
    article_rows = source_breakdown.fetch_article_rows(args, article_ids)
    rows = source_breakdown.build_breakdowns(
        clusters,
        article_rows,
        min_sources_ready=args.min_sources_ready,
    )
    if args.dry_run:
        LOGGER.info("dry-run source breakdown rows=%d", len(rows))
        return len(rows)
    source_breakdown.write_db(args, rows)
    return len(rows)


def summarize_clusters(clusters: dict[str, list[int]]) -> dict[str, Any]:
    sizes = [len(members) for members in clusters.values()]
    return {
        "clusters": len(sizes),
        "members": sum(sizes),
        "non_singleton": sum(1 for size in sizes if size > 1),
        "max_cluster": max(sizes) if sizes else 0,
    }


def main() -> None:
    args = parse_args()
    args.table = args.source_breakdown_table
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    start_date, end_date, start_dt, end_dt = refresh_window(args)
    t0 = time.time()
    conn = connect(args)
    locked = False
    try:
        locked = acquire_lock(conn, args.lock_key)
        if not locked:
            LOGGER.warning("another refresh is active; exiting")
            return

        ensure_news_l1_infra(conn)
        l15.ensure_l15_infra(conn)
        l2.ensure_l2_infra(conn)

        l1_args = argparse.Namespace(
            body_chars=args.body_chars,
            include_general_news=args.include_general_news,
            target_start=start_dt.isoformat(sep=" "),
            target_end=end_dt.isoformat(sep=" "),
            max_rows=args.max_rows,
        )
        records = l1_exp.fetch_records(conn, l1_args)
        LOGGER.info(
            "loaded L1 records=%d window=%s..%s include_general=%s",
            len(records),
            start_date,
            end_date,
            args.include_general_news,
        )
        clusters = l1_exp.build_clusters(
            records,
            max_candidates=args.max_candidates,
            exact_title_union=not args.disable_exact_title_union,
        )
        cluster_summary = summarize_clusters(clusters)
        LOGGER.info("built L1 clusters %s", cluster_summary)

        record_ids = [row.news_id for row in records]
        old_cluster_ids = existing_l1_cluster_ids(
            conn,
            run_id=args.l1_run_id,
            start_date=start_date,
            end_date=end_date,
            record_ids=record_ids,
        )
        new_cluster_ids = stable_l1_cluster_ids(args.l1_run_id, clusters)
        all_impacted_ids = old_cluster_ids | new_cluster_ids
        LOGGER.info(
            "impacted L1 old=%d new=%d total=%d",
            len(old_cluster_ids),
            len(new_cluster_ids),
            len(all_impacted_ids),
        )

        old_segment_ids = impacted_l15_segment_ids(
            conn, run_id=args.l15_run_id, cluster_ids=all_impacted_ids
        )
        old_chain_ids = impacted_l2_chain_ids(
            conn,
            run_id=args.l2_run_id,
            cluster_ids=all_impacted_ids,
            segment_ids=old_segment_ids,
        )

        if args.dry_run:
            LOGGER.info(
                "dry-run would delete l2_chains=%d l15_segments=%d l1_clusters=%d",
                len(old_chain_ids),
                len(old_segment_ids),
                len(old_cluster_ids),
            )
            return

        deleted_l2 = delete_l2_chains(conn, run_id=args.l2_run_id, chain_ids=old_chain_ids)
        deleted_l15 = delete_l15_segments(conn, run_id=args.l15_run_id, cluster_ids=all_impacted_ids)
        deleted_breakdown, deleted_covers = delete_story_assets(
            conn, l1_run_id=args.l1_run_id, cluster_ids=all_impacted_ids
        )
        deleted_l1 = delete_l1_clusters(conn, run_id=args.l1_run_id, cluster_ids=old_cluster_ids)
        LOGGER.info(
            "deleted old derived l2=%d l15=%d breakdown=%d covers=%d l1=%d",
            deleted_l2,
            deleted_l15,
            deleted_breakdown,
            deleted_covers,
            deleted_l1,
        )

        l1_clusters_written, l1_members_written = upsert_l1_clusters(
            conn,
            clusters,
            records,
            run_id=args.l1_run_id,
        )
        LOGGER.info("upserted L1 clusters=%d members=%d", l1_clusters_written, l1_members_written)

        l15_articles = fetch_l15_articles_for_clusters(
            conn,
            l1_run_id=args.l1_run_id,
            cluster_ids=new_cluster_ids,
        )
        l15_segments = l15.build_segments(l15_articles, run_id=args.l15_run_id)
        l15_segments_written, l15_members_written = upsert_l15_segments(
            conn,
            l15_segments,
            run_id=args.l15_run_id,
            l1_run_id=args.l1_run_id,
        )
        angle_counts = Counter(
            l15.classify_angle(members[0]) for members in l15_segments.values() if members
        )
        LOGGER.info(
            "upserted L1.5 segments=%d members=%d angles=%s",
            l15_segments_written,
            l15_members_written,
            dict(angle_counts.most_common(8)),
        )

        if not args.skip_l2:
            all_l15 = l2.fetch_segments(conn, args.l15_run_id)
            chains = l2.build_chains(
                all_l15,
                run_id=args.l2_run_id,
                min_chain_segments=args.min_chain_segments,
            )
            l2_chains_written, l2_members_written = replace_l2_chains(
                conn,
                chains,
                run_id=args.l2_run_id,
                l15_run_id=args.l15_run_id,
            )
            LOGGER.info(
                "rebuilt L2 chains=%d chain_segments=%d from l15_segments=%d",
                l2_chains_written,
                l2_members_written,
                len(all_l15),
            )

        if not args.skip_source_breakdown:
            breakdown_rows = rebuild_source_breakdown(conn, args, cluster_ids=new_cluster_ids)
            LOGGER.info("rebuilt source breakdown rows=%d", breakdown_rows)

        LOGGER.info("refresh finished in %.1fs", time.time() - t0)
    finally:
        if locked:
            release_lock(conn, args.lock_key)
        conn.close()


if __name__ == "__main__":
    main()
