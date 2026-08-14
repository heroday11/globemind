#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import psycopg2

from db_runtime_config import require_database_password


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MAPPING_CANDIDATES = [
    PROJECT_ROOT / "data" / "historical_news" / "event_coref_mapping_layer1.jsonl",
    PROJECT_ROOT / "data" / "event_coref_mapping_layer1.jsonl",
    PROJECT_ROOT / "data" / "event_coref_article_cluster_v13_100k.jsonl",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check readiness for Ground-News-style story/source analysis."
    )
    parser.add_argument("--format", choices=["text", "json"], default="text")
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


def table_exists(cur: Any, table: str) -> bool:
    cur.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name = %s
        )
        """,
        (table,),
    )
    return bool(cur.fetchone()[0])


def table_count(cur: Any, table: str) -> int | None:
    if not table_exists(cur, table):
        return None
    cur.execute(f"SELECT COUNT(*) FROM public.{table}")
    return int(cur.fetchone()[0])


def file_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False}
    return {
        "path": str(path),
        "exists": True,
        "size_bytes": path.stat().st_size,
        "size_mb": round(path.stat().st_size / 1024 / 1024, 2),
    }


def collect(args: argparse.Namespace) -> dict[str, Any]:
    conn = connect(args)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) AS news_rows,
                       count(distinct media_source_id) AS media_sources,
                       count(*) FILTER (WHERE title IS NOT NULL AND btrim(title) <> '') AS title_filled,
                       count(*) FILTER (WHERE body IS NOT NULL AND btrim(body) <> '') AS body_filled,
                       count(*) FILTER (WHERE published_at IS NOT NULL) AS published_at_filled,
                       count(*) FILTER (WHERE author IS NOT NULL AND btrim(author) <> '') AS author_filled,
                       count(*) FILTER (WHERE language IS NOT NULL AND btrim(language) <> '') AS language_filled,
                       count(*) FILTER (WHERE region IS NOT NULL AND btrim(region) <> '') AS region_filled
                FROM public.news
                """
            )
            news_cols = [desc[0] for desc in cur.description]
            news = dict(zip(news_cols, cur.fetchone()))

            cur.execute(
                """
                SELECT count(*) AS profiles,
                       count(*) FILTER (WHERE coalesce(source_name, '') <> '') AS source_name_filled,
                       count(*) FILTER (WHERE coalesce(country, '') <> '') AS country_filled,
                       count(*) FILTER (WHERE coalesce(source_type, '') <> '' AND source_type <> 'unknown') AS source_type_filled,
                       count(*) FILTER (WHERE ownership_type <> 'unknown') AS ownership_known,
                       count(*) FILTER (WHERE political_leaning <> 'unknown') AS political_known,
                       count(*) FILTER (WHERE credibility_tier <> 'unknown') AS credibility_known,
                       count(*) FILTER (WHERE geo_alignment <> 'unknown') AS geo_known,
                       count(*) FILTER (WHERE review_status IN ('reviewed', 'locked')) AS reviewed
                FROM public.media_source_profile
                """
            )
            profile_cols = [desc[0] for desc in cur.description]
            profile = dict(zip(profile_cols, cur.fetchone()))

            cur.execute(
                """
                SELECT count(*) AS news_rows,
                       count(ms.id) AS media_joined,
                       count(msp.domain) AS profile_joined,
                       count(*) FILTER (WHERE ms.id IS NULL) AS missing_media_source,
                       count(*) FILTER (WHERE msp.domain IS NULL) AS missing_profile
                FROM public.news n
                LEFT JOIN public.media_source ms ON ms.id = n.media_source_id
                LEFT JOIN public.media_source_profile msp ON msp.domain = ms.domain
                """
            )
            join_cols = [desc[0] for desc in cur.description]
            join = dict(zip(join_cols, cur.fetchone()))

            tables = {
                "story_cluster_members": table_count(cur, "story_cluster_members"),
                "story_clusters": table_count(cur, "story_clusters"),
                "event_coref_members": table_count(cur, "event_coref_members"),
                "event_coref_clusters": table_count(cur, "event_coref_clusters"),
                "story_source_breakdown": table_count(cur, "story_source_breakdown"),
            }
    finally:
        conn.close()

    mapping_files = [file_summary(path) for path in DEFAULT_MAPPING_CANDIDATES]

    profiles = int(profile["profiles"] or 0)
    political_known = int(profile["political_known"] or 0)
    credibility_known = int(profile["credibility_known"] or 0)
    reviewed = int(profile["reviewed"] or 0)
    profile_join_missing = int(join["missing_profile"] or 0)
    cluster_member_count = tables.get("story_cluster_members") or tables.get("event_coref_members") or 0
    breakdown_count = tables.get("story_source_breakdown") or 0

    readiness = {
        "news_ingest_ready": int(news["news_rows"] or 0) > 0 and int(news["title_filled"] or 0) == int(news["news_rows"] or 0),
        "source_profile_ready": profiles > 0 and profile_join_missing == 0,
        "source_political_ratings_ready": profiles > 0 and political_known / profiles >= 0.8,
        "source_factuality_ready": profiles > 0 and credibility_known / profiles >= 0.8,
        "source_reviews_ready": profiles > 0 and reviewed / profiles >= 0.8,
        "story_clusters_ready": bool(cluster_member_count),
        "story_source_breakdown_ready": bool(breakdown_count),
    }

    missing = []
    if not readiness["story_clusters_ready"]:
        missing.append("canonical story cluster/member table for current news DB")
    if not readiness["source_political_ratings_ready"]:
        missing.append("reviewed political_leaning ratings for media sources")
    if not readiness["source_factuality_ready"]:
        missing.append("reviewed credibility_tier/factuality ratings for media sources")
    if not readiness["source_reviews_ready"]:
        missing.append("review_status reviewed/locked coverage")
    if not readiness["story_source_breakdown_ready"]:
        missing.append("story_source_breakdown aggregation output")

    return {
        "database": args.dbname,
        "news": news,
        "media_source_profile": profile,
        "news_profile_join": join,
        "tables": tables,
        "mapping_files": mapping_files,
        "readiness": readiness,
        "missing_for_ground_news_style": missing,
    }


def pct(part: int, total: int) -> str:
    if total <= 0:
        return "0.00%"
    return f"{part * 100.0 / total:.2f}%"


def print_text(report: dict[str, Any]) -> None:
    news = report["news"]
    profile = report["media_source_profile"]
    join = report["news_profile_join"]
    readiness = report["readiness"]

    print("Ground-News-style readiness")
    print(f"database: {report['database']}")
    print("")
    print("News")
    print(f"- rows: {news['news_rows']}")
    print(f"- media sources: {news['media_sources']}")
    print(f"- title coverage: {pct(news['title_filled'], news['news_rows'])}")
    print(f"- body coverage: {pct(news['body_filled'], news['news_rows'])}")
    print(f"- published_at coverage: {pct(news['published_at_filled'], news['news_rows'])}")
    print("")
    print("Media source profile")
    print(f"- profiles: {profile['profiles']}")
    print(f"- source_name: {pct(profile['source_name_filled'], profile['profiles'])}")
    print(f"- country: {pct(profile['country_filled'], profile['profiles'])}")
    print(f"- source_type: {pct(profile['source_type_filled'], profile['profiles'])}")
    print(f"- ownership known: {pct(profile['ownership_known'], profile['profiles'])}")
    print(f"- political known: {pct(profile['political_known'], profile['profiles'])}")
    print(f"- credibility known: {pct(profile['credibility_known'], profile['profiles'])}")
    print(f"- reviewed/locked: {pct(profile['reviewed'], profile['profiles'])}")
    print("")
    print("Join")
    print(f"- news rows: {join['news_rows']}")
    print(f"- missing media_source: {join['missing_media_source']}")
    print(f"- missing profile: {join['missing_profile']}")
    print("")
    print("Tables")
    for table, count in report["tables"].items():
        print(f"- {table}: {'missing' if count is None else count}")
    print("")
    print("Mapping files")
    for item in report["mapping_files"]:
        if item["exists"]:
            print(f"- {item['path']}: {item['size_mb']} MB")
        else:
            print(f"- {item['path']}: missing")
    print("")
    print("Readiness")
    for key, value in readiness.items():
        print(f"- {key}: {'yes' if value else 'no'}")
    print("")
    print("Missing")
    for item in report["missing_for_ground_news_style"]:
        print(f"- {item}")


def main() -> None:
    args = parse_args()
    report = collect(args)
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print_text(report)


if __name__ == "__main__":
    main()
