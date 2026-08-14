#!/usr/bin/env python3
"""Backfill deterministic article quality labels for search and event pipelines.

The label table is intentionally separate from public.news so the raw crawl
archive remains untouched. Downstream jobs can join `news_quality_labels` and
only accept `is_good = true`.
"""

from __future__ import annotations

import argparse
import os
import time
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text

from db_runtime_config import require_database_password


LABEL_VERSION = "quality_v1_20260629"


def env(name: str, fallback: str) -> str:
    value = os.getenv(name)
    return value if value not in (None, "") else fallback


def database_url() -> str:
    user = env("L1_DB_USER", env("PG_USER", env("DB_USER", "postgres")))
    password = require_database_password()
    host = env("L1_DB_HOST", env("PG_HOST", env("DB_HOST", "192.168.207.171")))
    port = env("L1_DB_PORT", env("PG_PORT", env("DB_PORT", "54333")))
    name = env("L1_DB_NAME", "news")
    return f"postgresql+psycopg2://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{name}"


DDL = """
CREATE TABLE IF NOT EXISTS public.news_quality_labels (
    news_id BIGINT PRIMARY KEY,
    is_good BOOLEAN NOT NULL,
    quality_label TEXT NOT NULL,
    reasons TEXT[] NOT NULL DEFAULT '{}',
    label_version TEXT NOT NULL,
    source_domain TEXT,
    published_at TIMESTAMPTZ,
    content_fingerprint TEXT,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_news_quality_labels_good_id ON public.news_quality_labels (news_id) WHERE is_good",
    "CREATE INDEX IF NOT EXISTS idx_news_quality_labels_version_good ON public.news_quality_labels (label_version, is_good, news_id)",
    "CREATE INDEX IF NOT EXISTS idx_news_quality_labels_checked_at ON public.news_quality_labels (checked_at)",
]


BATCH_SQL = """
WITH selected AS (
    SELECT n.id
    FROM public.news n
    WHERE n.id > :last_id
      AND (:mode != 'missing' OR NOT EXISTS (
          SELECT 1 FROM public.news_quality_labels q
          WHERE q.news_id = n.id AND q.label_version = :label_version
      ))
      AND (:mode != 'recent' OR n.published_at >= now() - (:recent_hours * interval '1 hour'))
      AND (:mode != 'bad_dates' OR (
          n.published_at IS NULL
          OR n.published_at < TIMESTAMPTZ '2000-01-01 00:00:00+00'
          OR n.published_at > now() + interval '1 day'
      ))
    ORDER BY n.id
    LIMIT :batch_size
),
src AS (
    SELECT
        n.id AS news_id,
        btrim(COALESCE(n.title, '')) AS title_text,
        LEFT(COALESCE(n.body, ''), 1400) AS body_head,
        LENGTH(COALESCE(n.body, '')) AS body_len,
        n.url,
        n.published_at,
        LOWER(COALESCE(ms.domain, '')) AS source_domain
    FROM selected s
    JOIN public.news n ON n.id = s.id
    LEFT JOIN public.media_source ms ON ms.id = n.media_source_id
),
classified AS (
    SELECT
        news_id,
        source_domain,
        published_at,
        md5(title_text || E'\\n' || body_head) AS content_fingerprint,
        ARRAY_REMOVE(ARRAY[
            CASE
                WHEN (source_domain LIKE '%dw.com%' OR COALESCE(url, '') ILIKE '%://%dw.com/%')
                     AND COALESCE(url, '') NOT LIKE '%/a-%'
                THEN 'dw_non_article'
            END,
            CASE WHEN title_text = '' THEN 'empty_title' END,
            CASE WHEN published_at IS NULL THEN 'missing_published_at' END,
            CASE WHEN published_at < TIMESTAMPTZ '2000-01-01 00:00:00+00' THEN 'published_before_min_year' END,
            CASE WHEN published_at > now() + interval '1 day' THEN 'published_future_too_far' END,
            CASE WHEN body_len < 120 THEN 'body_too_short' END,
            CASE
                WHEN lower(title_text) IN (
                    'news', 'world', 'international', 'politics', 'business',
                    'economy', 'markets', 'sport', 'sports', 'football',
                    'latest news', 'breaking news', 'editorial standards',
                    'privacy policy', 'terms of use', 'terms and conditions',
                    'contact us', 'about us', 'sitemap', 'rss feed'
                )
                OR title_text ~* '(editorial standards|privacy policy|terms (of use|and conditions)|contact us|about us|sitemap|rss feed|newsletters?|subscribe|fixtures?|standings?|score(s|board)?|results?|weather forecast|tv schedule|programmes?)'
                THEN 'page_like_title'
            END,
            CASE
                WHEN COALESCE(url, '') ~* '/(tag|tags|topic|topics|category|categories|section|sections|author|authors|search|privacy|terms|about|contact|newsletter|subscribe|sitemap|weather|scores|fixtures|standings|results|programmes|schedule|live-tv)(/|[?]|#|$)'
                THEN 'page_like_url'
            END,
            CASE
                WHEN body_len < 80
                     AND (
                         title_text = ''
                         OR char_length(title_text) < 12
                         OR lower(title_text) IN (
                             'news', 'world', 'politics', 'business', 'economy',
                             'opinion', 'china', '中国', '中國', 'international',
                             'breaking news'
                         )
                         OR title_text ~ '(新闻|新聞|每日新闻|每日新聞|头条新闻|頭條新聞|深度报道|深度報導|最新ニュース|最新情報|ニュース 経済)'
                     )
                THEN 'thin_or_generic'
            END,
            CASE
                WHEN lower(body_head) LIKE 'to view this video please enable javascript%'
                     OR lower(body_head) LIKE '%enable javascript%'
                     OR lower(body_head) LIKE '%please enable javascript%'
                     OR lower(body_head) LIKE '%请启用javascript%'
                THEN 'javascript_placeholder'
            END,
            CASE WHEN body_head LIKE '%読売新聞の購読者%' AND body_head LIKE '%限定%' THEN 'paywall' END,
            CASE
                WHEN body_head ~ '(广告|廣告).{0,240}(跳转至下一栏|跳轉至下一欄|头条新闻|頭條新聞|每日新闻|每日新聞|深度报道|深度報導)'
                     OR body_head ~ '(跳转至下一栏|跳轉至下一欄).{0,240}(头条新闻|頭條新聞|每日新闻|每日新聞|深度报道|深度報導)'
                THEN 'boilerplate_navigation'
            END,
            CASE WHEN title_text ~ '(跳转至下一栏|跳轉至下一欄|广告|廣告)' THEN 'boilerplate_title' END,
            CASE WHEN title_text ~ '(最新ニュース・特集|最新新闻.*专题)' THEN 'section_page' END,
            CASE
                WHEN title_text ~ '(中国地方|中国地銀|中国銀行)'
                     OR LEFT(body_head, 260) ~ '(中国地方|中国地銀|中国銀行)'
                THEN 'regional_false_positive'
            END
        ]::TEXT[], NULL) AS reasons
    FROM src
),
upserted AS (
    INSERT INTO public.news_quality_labels (
        news_id,
        is_good,
        quality_label,
        reasons,
        label_version,
        source_domain,
        published_at,
        content_fingerprint,
        checked_at
    )
    SELECT
        news_id,
        COALESCE(cardinality(reasons), 0) = 0 AS is_good,
        CASE WHEN COALESCE(cardinality(reasons), 0) = 0 THEN 'good' ELSE 'bad' END AS quality_label,
        COALESCE(reasons, '{}'::TEXT[]) AS reasons,
        :label_version AS label_version,
        source_domain,
        published_at,
        content_fingerprint,
        now()
    FROM classified
    ON CONFLICT (news_id) DO UPDATE SET
        is_good = EXCLUDED.is_good,
        quality_label = EXCLUDED.quality_label,
        reasons = EXCLUDED.reasons,
        label_version = EXCLUDED.label_version,
        source_domain = EXCLUDED.source_domain,
        published_at = EXCLUDED.published_at,
        content_fingerprint = EXCLUDED.content_fingerprint,
        checked_at = now()
    RETURNING news_id, is_good
)
SELECT
    COUNT(*) AS processed,
    COALESCE(MAX(news_id), :last_id) AS max_id,
    COUNT(*) FILTER (WHERE is_good) AS good_count,
    COUNT(*) FILTER (WHERE NOT is_good) AS bad_count
FROM upserted;
"""


def ensure_schema(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(DDL))
        for stmt in INDEXES:
            conn.execute(text(stmt))


def backfill(engine, args) -> None:
    last_id = int(args.start_id or 0)
    processed_total = 0
    good_total = 0
    bad_total = 0
    started = time.time()
    while True:
        with engine.begin() as conn:
            row = conn.execute(
                text(BATCH_SQL),
                {
                    "last_id": last_id,
                    "batch_size": args.batch_size,
                    "mode": args.mode,
                    "recent_hours": args.recent_hours,
                    "label_version": LABEL_VERSION,
                },
            ).mappings().first()
        processed = int(row["processed"] or 0)
        if processed == 0:
            break
        last_id = int(row["max_id"] or last_id)
        good = int(row["good_count"] or 0)
        bad = int(row["bad_count"] or 0)
        processed_total += processed
        good_total += good
        bad_total += bad
        elapsed = time.time() - started
        print(
            f"processed={processed_total} last_id={last_id} "
            f"good={good_total} bad={bad_total} elapsed={elapsed:.1f}s",
            flush=True,
        )
        if args.limit and processed_total >= args.limit:
            break


def print_summary(engine) -> None:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE is_good) AS good,
                    COUNT(*) FILTER (WHERE NOT is_good) AS bad,
                    MAX(checked_at) AS last_checked
                FROM public.news_quality_labels
                WHERE label_version = :label_version
                """
            ),
            {"label_version": LABEL_VERSION},
        ).mappings().first()
        print(
            f"summary total={row['total']} good={row['good']} "
            f"bad={row['bad']} last_checked={row['last_checked']}",
            flush=True,
        )
        for reason in conn.execute(
            text(
                """
                SELECT reason, COUNT(*) AS count
                FROM public.news_quality_labels q
                CROSS JOIN LATERAL unnest(q.reasons) AS reason
                WHERE q.label_version = :label_version
                GROUP BY reason
                ORDER BY count DESC, reason
                LIMIT 20
                """
            ),
            {"label_version": LABEL_VERSION},
        ).mappings():
            print(f"reason {reason['reason']} {reason['count']}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill public.news_quality_labels")
    parser.add_argument("--mode", choices=["full", "missing", "recent", "bad_dates"], default="missing")
    parser.add_argument("--batch-size", type=int, default=50000)
    parser.add_argument("--start-id", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--recent-hours", type=int, default=72)
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()

    engine = create_engine(database_url(), pool_pre_ping=True)
    ensure_schema(engine)
    if not args.summary_only:
        backfill(engine, args)
    print_summary(engine)


if __name__ == "__main__":
    main()
