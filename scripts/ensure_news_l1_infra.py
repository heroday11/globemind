#!/usr/bin/env python3
from __future__ import annotations

import argparse
from typing import Any

import psycopg2

try:
    from scripts.db_runtime_config import require_database_password
except ModuleNotFoundError:  # Direct execution sets scripts/ as sys.path[0].
    from db_runtime_config import require_database_password


def add_db_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default="192.168.207.171")
    parser.add_argument("--port", type=int, default=54333)
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--dbname", default="news")


def connect(args: argparse.Namespace) -> Any:
    return psycopg2.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=require_database_password(),
        dbname=args.dbname,
        connect_timeout=20,
    )


def ensure_news_l1_infra(conn: Any) -> None:
    """Create DB objects needed after L1 event extraction."""
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.news_embeddings (
                news_id BIGINT PRIMARY KEY,
                model TEXT NOT NULL DEFAULT 'bge-m3',
                dim INTEGER NOT NULL DEFAULT 1024,
                embedding REAL[] NOT NULL,
                embedding_text_hash TEXT,
                embedding_text_chars INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_news_embeddings_model "
            "ON public.news_embeddings (model)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_news_embeddings_text_hash "
            "ON public.news_embeddings (embedding_text_hash)"
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.event_coref_clusters (
                cluster_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL DEFAULT 'default',
                article_count INTEGER NOT NULL DEFAULT 0,
                event_domain TEXT,
                event_type TEXT,
                event_family TEXT,
                event_action TEXT,
                initiator TEXT,
                target TEXT,
                location TEXT,
                tone TEXT,
                dominant_trigger TEXT,
                start_date DATE,
                end_date DATE,
                cluster_quality TEXT,
                title TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        for statement in (
            "ALTER TABLE public.event_coref_clusters ADD COLUMN IF NOT EXISTS run_id TEXT NOT NULL DEFAULT 'default'",
            "ALTER TABLE public.event_coref_clusters ADD COLUMN IF NOT EXISTS event_domain TEXT",
            "ALTER TABLE public.event_coref_clusters ADD COLUMN IF NOT EXISTS event_family TEXT",
            "ALTER TABLE public.event_coref_clusters ADD COLUMN IF NOT EXISTS event_action TEXT",
            "ALTER TABLE public.event_coref_clusters ADD COLUMN IF NOT EXISTS location TEXT",
            "ALTER TABLE public.event_coref_clusters ADD COLUMN IF NOT EXISTS tone TEXT",
            "ALTER TABLE public.event_coref_clusters ADD COLUMN IF NOT EXISTS title TEXT",
        ):
            cur.execute(statement)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_coref_clusters_run_id "
            "ON public.event_coref_clusters (run_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_coref_clusters_run_articles_start "
            "ON public.event_coref_clusters (run_id, article_count DESC, start_date DESC, cluster_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_coref_clusters_family_action "
            "ON public.event_coref_clusters (event_family, event_action)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_coref_clusters_date "
            "ON public.event_coref_clusters (start_date, end_date)"
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.event_coref_members (
                cluster_id TEXT NOT NULL REFERENCES public.event_coref_clusters(cluster_id) ON DELETE CASCADE,
                run_id TEXT NOT NULL DEFAULT 'default',
                news_id BIGINT NOT NULL,
                event_domain TEXT,
                event_type TEXT,
                event_family TEXT,
                event_action TEXT,
                initiator TEXT,
                target TEXT,
                trigger TEXT,
                published_at TIMESTAMPTZ,
                membership_score DOUBLE PRECISION NOT NULL DEFAULT 1.0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (cluster_id, news_id)
            )
            """
        )
        for statement in (
            "ALTER TABLE public.event_coref_members ADD COLUMN IF NOT EXISTS run_id TEXT NOT NULL DEFAULT 'default'",
            "ALTER TABLE public.event_coref_members ADD COLUMN IF NOT EXISTS event_domain TEXT",
            "ALTER TABLE public.event_coref_members ADD COLUMN IF NOT EXISTS event_family TEXT",
            "ALTER TABLE public.event_coref_members ADD COLUMN IF NOT EXISTS event_action TEXT",
            "ALTER TABLE public.event_coref_members ADD COLUMN IF NOT EXISTS membership_score DOUBLE PRECISION NOT NULL DEFAULT 1.0",
        ):
            cur.execute(statement)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_coref_members_run_id "
            "ON public.event_coref_members (run_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_coref_members_news_id "
            "ON public.event_coref_members (news_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_coref_members_family_action "
            "ON public.event_coref_members (event_family, event_action)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_coref_members_published_at "
            "ON public.event_coref_members (published_at)"
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_news_id ON public.news (id)")
    conn.commit()


def reset_missing_extraction_status(conn: Any) -> int:
    """Return prep rows marked extracted but missing extraction rows to pending."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE public.news_l1_prep AS prep
            SET processing_status = 'pending_event',
                updated_at = now()
            WHERE prep.processing_status = 'event_extracted'
              AND NOT EXISTS (
                  SELECT 1
                  FROM public.news_l1_event_extractions AS e
                  WHERE e.news_id = prep.news_id
              )
            """
        )
        count = int(cur.rowcount)
    conn.commit()
    return count


def table_counts(conn: Any) -> dict[str, int]:
    tables = (
        "news_l1_prep",
        "news_l1_event_extractions",
        "news_embeddings",
        "event_coref_clusters",
        "event_coref_members",
    )
    counts: dict[str, int] = {}
    with conn.cursor() as cur:
        for table in tables:
            cur.execute(f"SELECT count(*) FROM public.{table}")
            counts[table] = int(cur.fetchone()[0])
    return counts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ensure news DB infra for L1 clustering.")
    add_db_args(parser)
    parser.add_argument(
        "--fix-missing-extractions",
        action="store_true",
        help="Reset prep rows from event_extracted to pending_event when the extraction row is missing.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    conn = connect(args)
    try:
        ensure_news_l1_infra(conn)
        fixed = reset_missing_extraction_status(conn) if args.fix_missing_extractions else 0
        counts = table_counts(conn)
    finally:
        conn.close()

    print("ensured news L1 infra")
    if args.fix_missing_extractions:
        print(f"reset_missing_extraction_status={fixed}")
    for table, count in counts.items():
        print(f"{table}={count}")


if __name__ == "__main__":
    main()
