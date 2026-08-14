#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import psycopg2
from psycopg2.extras import Json, execute_values

try:
    from scripts.db_runtime_config import require_database_password
except ModuleNotFoundError:  # Direct execution sets scripts/ as sys.path[0].
    from db_runtime_config import require_database_password


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "analysis" / "ground_news" / "story_source_breakdown.jsonl"

POLITICAL_GROUPS = {
    "left": "left",
    "center_left": "left",
    "center": "center",
    "center_right": "right",
    "right": "right",
    "state_aligned": "state_aligned",
    "unknown": "unknown",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build Ground-News-style source breakdowns for story clusters."
    )
    parser.add_argument(
        "--mapping",
        type=Path,
        help="JSONL or CSV with cluster_id/story_id and article_id/news_id columns.",
    )
    parser.add_argument(
        "--from-l1-run-id",
        help="Load cluster -> news_id mapping directly from public.event_coref_members.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--write-db", action="store_true")
    parser.add_argument("--table", default="story_source_breakdown")
    parser.add_argument("--min-sources-ready", type=int, default=3)
    parser.add_argument("--limit-clusters", type=int)
    parser.add_argument("--host", default="192.168.207.171")
    parser.add_argument("--port", type=int, default=54333)
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--dbname", default="news")
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


def load_mapping(path: Path, limit_clusters: int | None = None) -> dict[str, list[int]]:
    if not path.exists():
        raise FileNotFoundError(path)

    clusters: dict[str, list[int]] = defaultdict(list)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                cluster_id = str(row.get("cluster_id") or row.get("story_id") or "").strip()
                article_id = row.get("article_id") or row.get("news_id")
                if cluster_id and article_id:
                    clusters[cluster_id].append(int(article_id))
                if limit_clusters and len(clusters) > limit_clusters:
                    break
    else:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                cluster_id = str(row.get("cluster_id") or row.get("story_id") or "").strip()
                article_id = row.get("article_id") or row.get("news_id")
                if cluster_id and article_id:
                    clusters[cluster_id].append(int(article_id))
                if limit_clusters and len(clusters) > limit_clusters:
                    break

    if limit_clusters is not None:
        limited: dict[str, list[int]] = {}
        for cluster_id in sorted(clusters):
            limited[cluster_id] = clusters[cluster_id]
            if len(limited) >= limit_clusters:
                break
        return limited
    return dict(clusters)


def load_l1_mapping(args: argparse.Namespace, run_id: str) -> dict[str, list[int]]:
    clusters: dict[str, list[int]] = defaultdict(list)
    conn = connect(args)
    try:
        with conn.cursor() as cur:
            if args.limit_clusters:
                cur.execute(
                    """
                    SELECT m.cluster_id, m.news_id
                    FROM public.event_coref_members AS m
                    JOIN (
                        SELECT cluster_id
                        FROM public.event_coref_clusters
                        WHERE run_id = %s
                        ORDER BY article_count DESC, cluster_id
                        LIMIT %s
                    ) AS limited ON limited.cluster_id = m.cluster_id
                    WHERE m.run_id = %s
                    ORDER BY m.cluster_id, m.news_id
                    """,
                    (run_id, args.limit_clusters, run_id),
                )
            else:
                cur.execute(
                    """
                    SELECT cluster_id, news_id
                    FROM public.event_coref_members
                    WHERE run_id = %s
                    ORDER BY cluster_id, news_id
                    """,
                    (run_id,),
                )
            for cluster_id, news_id in cur.fetchall():
                clusters[str(cluster_id)].append(int(news_id))
    finally:
        conn.close()
    return dict(clusters)


def batch(items: list[int], size: int) -> Iterable[list[int]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def fetch_article_rows(args: argparse.Namespace, article_ids: set[int]) -> dict[int, dict[str, Any]]:
    if not article_ids:
        return {}

    rows: dict[int, dict[str, Any]] = {}
    conn = connect(args)
    try:
        with conn.cursor() as cur:
            ids = sorted(article_ids)
            for part in batch(ids, 5000):
                cur.execute(
                    """
                    SELECT n.id,
                           COALESCE(n.title, '') AS title,
                           n.published_at,
                           ms.domain,
                           COALESCE(msp.source_name, ms.domain) AS source_name,
                           COALESCE(msp.country, '') AS country,
                           COALESCE(msp.source_type, 'unknown') AS source_type,
                           COALESCE(msp.ownership_type, 'unknown') AS ownership_type,
                           COALESCE(msp.political_leaning, 'unknown') AS political_leaning,
                           COALESCE(msp.credibility_tier, 'unknown') AS credibility_tier,
                           COALESCE(msp.review_status, 'seeded') AS review_status
                    FROM public.news n
                    LEFT JOIN public.media_source ms ON ms.id = n.media_source_id
                    LEFT JOIN public.media_source_profile msp ON msp.domain = ms.domain
                    WHERE n.id = ANY(%s)
                    """,
                    (part,),
                )
                columns = [desc[0] for desc in cur.description]
                for row in cur.fetchall():
                    rows[int(row[0])] = dict(zip(columns, row))
    finally:
        conn.close()
    return rows


def pct(counter: Counter[str], denominator: int) -> dict[str, float]:
    if denominator <= 0:
        return {}
    return {key: round(value * 100.0 / denominator, 2) for key, value in sorted(counter.items())}


def group_political(value: str) -> str:
    return POLITICAL_GROUPS.get(value or "unknown", "unknown")


def choose_representative_title(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    sorted_rows = sorted(
        rows,
        key=lambda row: (
            row.get("published_at") is None,
            row.get("published_at") or datetime.max.replace(tzinfo=timezone.utc),
            -len(str(row.get("title") or "")),
        ),
    )
    return str(sorted_rows[0].get("title") or "")


def build_breakdowns(
    clusters: dict[str, list[int]],
    article_rows: dict[int, dict[str, Any]],
    *,
    min_sources_ready: int,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    built_at = datetime.now(timezone.utc).isoformat()
    for story_id, article_ids in sorted(clusters.items()):
        rows = [article_rows[article_id] for article_id in article_ids if article_id in article_rows]
        if not rows:
            output.append(
                {
                    "story_id": story_id,
                    "article_count": len(article_ids),
                    "matched_article_count": 0,
                    "source_count": 0,
                    "source_domains": [],
                    "representative_title": "",
                    "first_published_at": None,
                    "last_published_at": None,
                    "source_type_counts": {},
                    "source_type_pct_articles": {},
                    "country_counts": {},
                    "ownership_type_counts": {},
                    "credibility_tier_counts": {},
                    "review_status_counts": {},
                    "political_leaning_counts_sources": {},
                    "political_group_counts_sources": {},
                    "political_group_pct_all_sources": {},
                    "political_group_pct_reviewed_known_sources": {},
                    "reviewed_known_political_source_count": 0,
                    "unknown_political_source_count": 0,
                    "analysis_status": "missing_articles",
                    "built_at": built_at,
                }
            )
            continue

        source_domains = sorted({str(row.get("domain") or "") for row in rows if row.get("domain")})
        source_count = len(source_domains)
        source_type_counts = Counter(str(row.get("source_type") or "unknown") for row in rows)
        country_counts = Counter(str(row.get("country") or "unknown") for row in rows)
        ownership_counts = Counter(str(row.get("ownership_type") or "unknown") for row in rows)
        credibility_counts = Counter(str(row.get("credibility_tier") or "unknown") for row in rows)
        review_status_counts = Counter(str(row.get("review_status") or "seeded") for row in rows)

        by_domain: dict[str, dict[str, Any]] = {}
        for row in rows:
            domain = str(row.get("domain") or "")
            if domain and domain not in by_domain:
                by_domain[domain] = row

        source_political = Counter(
            str(row.get("political_leaning") or "unknown") for row in by_domain.values()
        )
        source_political_group = Counter(
            group_political(str(row.get("political_leaning") or "unknown"))
            for row in by_domain.values()
        )
        reviewed_known_sources = [
            row
            for row in by_domain.values()
            if row.get("review_status") in {"reviewed", "locked"}
            and row.get("political_leaning") not in {None, "", "unknown"}
        ]
        known_group_counts = Counter(
            group_political(str(row.get("political_leaning") or "unknown"))
            for row in reviewed_known_sources
        )

        published_values = [row.get("published_at") for row in rows if row.get("published_at")]
        if source_count < 2:
            analysis_status = "single_source"
        elif not reviewed_known_sources:
            analysis_status = "missing_political_ratings"
        elif source_count < min_sources_ready:
            analysis_status = "low_source_count"
        else:
            analysis_status = "ready"

        output.append(
            {
                "story_id": story_id,
                "article_count": len(article_ids),
                "matched_article_count": len(rows),
                "source_count": source_count,
                "source_domains": source_domains,
                "representative_title": choose_representative_title(rows),
                "first_published_at": min(published_values).isoformat() if published_values else None,
                "last_published_at": max(published_values).isoformat() if published_values else None,
                "source_type_counts": dict(sorted(source_type_counts.items())),
                "source_type_pct_articles": pct(source_type_counts, len(rows)),
                "country_counts": dict(sorted(country_counts.items())),
                "ownership_type_counts": dict(sorted(ownership_counts.items())),
                "credibility_tier_counts": dict(sorted(credibility_counts.items())),
                "review_status_counts": dict(sorted(review_status_counts.items())),
                "political_leaning_counts_sources": dict(sorted(source_political.items())),
                "political_group_counts_sources": dict(sorted(source_political_group.items())),
                "political_group_pct_all_sources": pct(source_political_group, source_count),
                "political_group_pct_reviewed_known_sources": pct(
                    known_group_counts, len(reviewed_known_sources)
                ),
                "reviewed_known_political_source_count": len(reviewed_known_sources),
                "unknown_political_source_count": source_political.get("unknown", 0),
                "analysis_status": analysis_status,
                "built_at": built_at,
            }
        )
    return output


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def ensure_table(cur: Any, table: str) -> None:
    if not table.replace("_", "").isalnum():
        raise ValueError(f"unsafe table name: {table!r}")
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS public.{table} (
            story_id TEXT PRIMARY KEY,
            article_count INTEGER NOT NULL,
            matched_article_count INTEGER NOT NULL,
            source_count INTEGER NOT NULL DEFAULT 0,
            source_domains JSONB NOT NULL DEFAULT '[]'::jsonb,
            representative_title TEXT,
            first_published_at TIMESTAMPTZ,
            last_published_at TIMESTAMPTZ,
            source_type_counts JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            source_type_pct_articles JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            country_counts JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            ownership_type_counts JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            credibility_tier_counts JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            review_status_counts JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            political_leaning_counts_sources JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            political_group_counts_sources JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            political_group_pct_all_sources JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            political_group_pct_reviewed_known_sources JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            reviewed_known_political_source_count INTEGER NOT NULL DEFAULT 0,
            unknown_political_source_count INTEGER NOT NULL DEFAULT 0,
            analysis_status TEXT NOT NULL,
            built_at TIMESTAMPTZ NOT NULL
        )
        """
    )
    cur.execute(
        f"""
        ALTER TABLE public.{table}
        ADD COLUMN IF NOT EXISTS source_type_pct_articles JSONB NOT NULL DEFAULT '{{}}'::jsonb
        """
    )


def write_db(args: argparse.Namespace, rows: list[dict[str, Any]]) -> None:
    conn = connect(args)
    try:
        with conn:
            with conn.cursor() as cur:
                ensure_table(cur, args.table)
                values = [
                    (
                        row["story_id"],
                        row["article_count"],
                        row["matched_article_count"],
                        row["source_count"],
                        Json(row.get("source_domains", [])),
                        row.get("representative_title"),
                        row.get("first_published_at"),
                        row.get("last_published_at"),
                        Json(row.get("source_type_counts", {})),
                        Json(row.get("source_type_pct_articles", {})),
                        Json(row.get("country_counts", {})),
                        Json(row.get("ownership_type_counts", {})),
                        Json(row.get("credibility_tier_counts", {})),
                        Json(row.get("review_status_counts", {})),
                        Json(row.get("political_leaning_counts_sources", {})),
                        Json(row.get("political_group_counts_sources", {})),
                        Json(row.get("political_group_pct_all_sources", {})),
                        Json(row.get("political_group_pct_reviewed_known_sources", {})),
                        row.get("reviewed_known_political_source_count", 0),
                        row.get("unknown_political_source_count", 0),
                        row["analysis_status"],
                        row["built_at"],
                    )
                    for row in rows
                ]
                execute_values(
                    cur,
                    f"""
                    INSERT INTO public.{args.table} (
                        story_id, article_count, matched_article_count, source_count,
                        source_domains, representative_title, first_published_at, last_published_at,
                        source_type_counts, source_type_pct_articles, country_counts, ownership_type_counts,
                        credibility_tier_counts, review_status_counts,
                        political_leaning_counts_sources, political_group_counts_sources,
                        political_group_pct_all_sources, political_group_pct_reviewed_known_sources,
                        reviewed_known_political_source_count, unknown_political_source_count,
                        analysis_status, built_at
                    )
                    VALUES %s
                    ON CONFLICT (story_id) DO UPDATE SET
                        article_count = EXCLUDED.article_count,
                        matched_article_count = EXCLUDED.matched_article_count,
                        source_count = EXCLUDED.source_count,
                        source_domains = EXCLUDED.source_domains,
                        representative_title = EXCLUDED.representative_title,
                        first_published_at = EXCLUDED.first_published_at,
                        last_published_at = EXCLUDED.last_published_at,
                        source_type_counts = EXCLUDED.source_type_counts,
                        source_type_pct_articles = EXCLUDED.source_type_pct_articles,
                        country_counts = EXCLUDED.country_counts,
                        ownership_type_counts = EXCLUDED.ownership_type_counts,
                        credibility_tier_counts = EXCLUDED.credibility_tier_counts,
                        review_status_counts = EXCLUDED.review_status_counts,
                        political_leaning_counts_sources = EXCLUDED.political_leaning_counts_sources,
                        political_group_counts_sources = EXCLUDED.political_group_counts_sources,
                        political_group_pct_all_sources = EXCLUDED.political_group_pct_all_sources,
                        political_group_pct_reviewed_known_sources = EXCLUDED.political_group_pct_reviewed_known_sources,
                        reviewed_known_political_source_count = EXCLUDED.reviewed_known_political_source_count,
                        unknown_political_source_count = EXCLUDED.unknown_political_source_count,
                        analysis_status = EXCLUDED.analysis_status,
                        built_at = EXCLUDED.built_at
                    """,
                    values,
                    page_size=1000,
                )
    finally:
        conn.close()


def main() -> None:
    args = parse_args()
    if args.from_l1_run_id:
        clusters = load_l1_mapping(args, args.from_l1_run_id)
    elif args.mapping:
        clusters = load_mapping(args.mapping, args.limit_clusters)
    else:
        raise SystemExit("Provide --mapping or --from-l1-run-id")
    article_ids = {article_id for ids in clusters.values() for article_id in ids}
    article_rows = fetch_article_rows(args, article_ids)
    rows = build_breakdowns(clusters, article_rows, min_sources_ready=args.min_sources_ready)
    write_jsonl(args.output, rows)
    if args.write_db:
        write_db(args, rows)

    status_counts = Counter(row.get("analysis_status", "unknown") for row in rows)
    print(f"loaded clusters: {len(clusters)}")
    print(f"unique article ids: {len(article_ids)}")
    print(f"matched article ids: {len(article_rows)}")
    print(f"wrote breakdowns: {len(rows)} to {args.output}")
    if args.write_db:
        print(f"upserted breakdowns into {args.dbname}.public.{args.table}")
    print("analysis_status:", dict(sorted(status_counts.items())))


if __name__ == "__main__":
    main()
