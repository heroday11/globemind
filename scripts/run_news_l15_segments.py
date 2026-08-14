#!/usr/bin/env python3
"""Build L1.5 story-angle segments from production L1 clusters.

L1 is a story/routing layer. L1.5 splits each L1 story into tighter
date+angle segments so downstream L2 can build readable evolution chains
without inheriting overly broad story cards as atomic events.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from psycopg2.extras import RealDictCursor, execute_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ensure_news_l1_infra import add_db_args, connect

LOGGER = logging.getLogger("run_news_l15_segments")
RUN_ID = "fast_l15_v1"
DEFAULT_L1_RUN_ID = "fast_l1_v2"


@dataclass(slots=True)
class Article:
    l1_cluster_id: str
    news_id: int
    published_at: datetime | None
    published_date: date | None
    title: str
    event_domain: str | None
    event_family: str | None
    event_action: str | None
    initiator: str | None
    target: str | None
    location: str | None
    tone: str | None
    l1_title: str | None


MARKET_RE = re.compile(
    r"\b(oil|gold|dollar|stocks?|shares?|markets?|nasdaq|dow|s&p|bond|yield|"
    r"rupee|ringgit|yen|yuan|euro|crypto|bitcoin|investors?|prices?|shippers?|freight)\b",
    re.IGNORECASE,
)
PREVIEW_RE = re.compile(
    r"\b(to meet|set to|due to|expected to|plans? to|will meet|will visit|"
    r"ahead of|before talks?|prepares?|preparations?|scheduled|next week|upcoming)\b",
    re.IGNORECASE,
)
MAIN_EVENT_RE = re.compile(
    r"\b(arrives?|meets?|visits?|holds talks?|signs?|agrees?|announces?|launches?|"
    r"strikes?|attacks?|passes?|approves?|imposes?|appoints?|fires?|resigns?|arrests?|executes?)\b",
    re.IGNORECASE,
)
OUTCOME_RE = re.compile(
    r"\b(after|fallout|outcome|ends?|concludes?|returns?|hails?|welcomes?|"
    r"no deal|breakthrough|takeaways?|what next|reaction|responds?|criticizes?)\b",
    re.IGNORECASE,
)
ANALYSIS_RE = re.compile(
    r"\b(analysis|explainer|opinion|factbox|why|how|what it means|"
    r"takeaways?|things to know|profile|background)\b",
    re.IGNORECASE,
)
OFFICIAL_TEMPLATE_RE = re.compile(
    r"\b(nato secretary general|north atlantic council|france diplomatie|"
    r"press conference|doorstep statement|opening remarks|remarks)\b",
    re.IGNORECASE,
)
VIDEO_RE = re.compile(r"\b(watch|video|bloomberg daybreak|closing bell|balance of power|open interest)\b", re.IGNORECASE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build L1.5 story-angle segments from L1 clusters.")
    add_db_args(parser)
    parser.add_argument("--run-id", default=RUN_ID)
    parser.add_argument("--l1-run-id", default=DEFAULT_L1_RUN_ID)
    parser.add_argument("--clear-existing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sample-limit", type=int, default=20)
    return parser.parse_args()


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def normalize_key(value: str | None) -> str:
    text = clean_text(value).lower()
    text = re.sub(r"[^\w\u4e00-\u9fff\u0600-\u06ff\u0400-\u04ff]+", " ", text)
    return " ".join(text.split())


def mode(values: Iterable[str | None], default: str | None = None) -> str | None:
    clean = [value for value in values if value]
    if not clean:
        return default
    return Counter(clean).most_common(1)[0][0]


def stable_id(run_id: str, parts: Iterable[Any]) -> str:
    payload = "|".join(str(part) for part in parts)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return f"{run_id}_{digest}"


def classify_angle(article: Article) -> str:
    title = article.title or ""
    if MARKET_RE.search(title):
        return "market_reaction"
    if ANALYSIS_RE.search(title):
        return "analysis_context"
    if OFFICIAL_TEMPLATE_RE.search(title):
        return "official_update"
    if VIDEO_RE.search(title):
        return "video_clip"
    if PREVIEW_RE.search(title):
        return "preview_planning"
    if OUTCOME_RE.search(title):
        return "outcome_reaction"
    if MAIN_EVENT_RE.search(title):
        return "main_event"
    if article.event_action in {"military_attack", "terror_attack", "protest", "crackdown_arrest"}:
        return "main_event"
    return "context_update"


def segment_date_bucket(article: Article, angle: str) -> str:
    if article.published_date is None:
        return "unknown"
    return article.published_date.isoformat()


def fetch_articles(conn: Any, l1_run_id: str) -> list[Article]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT cluster_id, location, tone, title
            FROM public.event_coref_clusters
            WHERE run_id = %s
            """,
            (l1_run_id,),
        )
        cluster_meta = {
            str(row["cluster_id"]): {
                "location": clean_text(row.get("location")) or None,
                "tone": clean_text(row.get("tone")) or None,
                "title": clean_text(row.get("title")) or None,
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
            ORDER BY m.cluster_id, m.published_at NULLS LAST, m.news_id
            """,
            (l1_run_id,),
        )
        rows = [dict(row) for row in cur.fetchall()]

    articles: list[Article] = []
    for row in rows:
        published_at = row.get("published_at")
        l1_cluster_id = str(row["l1_cluster_id"])
        meta = cluster_meta.get(l1_cluster_id, {})
        articles.append(
            Article(
                l1_cluster_id=l1_cluster_id,
                news_id=int(row["news_id"]),
                published_at=published_at,
                published_date=published_at.date() if isinstance(published_at, datetime) else None,
                title=clean_text(row.get("title")),
                event_domain=clean_text(row.get("event_domain")) or None,
                event_family=clean_text(row.get("event_family")) or None,
                event_action=clean_text(row.get("event_action")) or None,
                initiator=clean_text(row.get("initiator")) or None,
                target=clean_text(row.get("target")) or None,
                location=meta.get("location"),
                tone=meta.get("tone"),
                l1_title=meta.get("title"),
            )
        )
    return articles


def build_segments(articles: list[Article], *, run_id: str) -> dict[str, list[Article]]:
    grouped: dict[tuple[str, str, str], list[Article]] = defaultdict(list)
    for article in articles:
        angle = classify_angle(article)
        date_bucket = segment_date_bucket(article, angle)
        grouped[(article.l1_cluster_id, angle, date_bucket)].append(article)

    segments: dict[str, list[Article]] = {}
    for (l1_cluster_id, angle, date_bucket), members in grouped.items():
        member_ids = sorted(article.news_id for article in members)
        segment_id = stable_id(run_id, [l1_cluster_id, angle, date_bucket, ",".join(map(str, member_ids))])
        segments[segment_id] = members
    return segments


def ensure_l15_infra(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.event_l15_segments (
                segment_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                l1_run_id TEXT NOT NULL,
                l1_cluster_id TEXT NOT NULL,
                article_count INTEGER NOT NULL DEFAULT 0,
                event_domain TEXT,
                event_family TEXT,
                event_action TEXT,
                story_angle TEXT,
                initiator TEXT,
                target TEXT,
                location TEXT,
                tone TEXT,
                start_date DATE,
                end_date DATE,
                title TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.event_l15_members (
                segment_id TEXT NOT NULL REFERENCES public.event_l15_segments(segment_id) ON DELETE CASCADE,
                run_id TEXT NOT NULL,
                l1_run_id TEXT NOT NULL,
                l1_cluster_id TEXT NOT NULL,
                news_id BIGINT NOT NULL,
                story_angle TEXT,
                event_family TEXT,
                event_action TEXT,
                published_at TIMESTAMPTZ,
                membership_score DOUBLE PRECISION NOT NULL DEFAULT 1.0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (segment_id, news_id)
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_event_l15_segments_run ON public.event_l15_segments (run_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_event_l15_segments_l1 ON public.event_l15_segments (l1_run_id, l1_cluster_id)")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_l15_segments_run_l1_angle_articles "
            "ON public.event_l15_segments (run_id, l1_cluster_id, story_angle, article_count DESC, start_date) "
            "INCLUDE (title, segment_id)"
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_event_l15_segments_date ON public.event_l15_segments (start_date, end_date)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_event_l15_segments_family ON public.event_l15_segments (event_family, event_action, story_angle)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_event_l15_members_run ON public.event_l15_members (run_id)")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_l15_members_run_segment_published "
            "ON public.event_l15_members (run_id, segment_id, published_at, news_id)"
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_event_l15_members_news ON public.event_l15_members (news_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_event_coref_members_run_cluster ON public.event_coref_members (run_id, cluster_id, published_at)")
    conn.commit()


def write_segments(
    conn: Any,
    segments: dict[str, list[Article]],
    *,
    run_id: str,
    l1_run_id: str,
    clear_existing: bool,
) -> None:
    with conn.cursor() as cur:
        if clear_existing:
            cur.execute("TRUNCATE public.event_l15_members, public.event_l15_segments CASCADE")
        else:
            cur.execute("DELETE FROM public.event_l15_segments WHERE run_id = %s", (run_id,))
    conn.commit()

    segment_values = []
    member_values = []
    for segment_id, members in sorted(segments.items(), key=lambda item: (-len(item[1]), item[0])):
        dates = [article.published_date for article in members if article.published_date]
        published = [article.published_at for article in members if article.published_at]
        title = mode([article.title for article in members], members[0].l1_title or members[0].title)
        story_angle = classify_angle(members[0])
        segment_values.append(
            (
                segment_id,
                run_id,
                l1_run_id,
                members[0].l1_cluster_id,
                len(members),
                mode([article.event_domain for article in members], "political"),
                mode([article.event_family for article in members], "other"),
                mode([article.event_action for article in members], "other"),
                story_angle,
                mode([article.initiator for article in members], None),
                mode([article.target for article in members], None),
                mode([article.location for article in members], None),
                mode([article.tone for article in members], "neutral"),
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
        return

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


def print_report(segments: dict[str, list[Article]], *, sample_limit: int) -> None:
    sizes = [len(members) for members in segments.values()]
    angle_counts = Counter(classify_angle(members[0]) for members in segments.values() if members)
    print("summary")
    print(f"segments={len(segments)}")
    print(f"members={sum(sizes)}")
    print(f"non_singleton_segments={sum(1 for size in sizes if size > 1)}")
    print(f"max_segment_size={max(sizes) if sizes else 0}")
    print("top_angles", angle_counts.most_common(12))
    lookup = sorted(segments.items(), key=lambda item: (-len(item[1]), item[0]))
    print("sample_segments")
    for segment_id, members in lookup[:sample_limit]:
        dates = [article.published_date for article in members if article.published_date]
        print("-" * 80)
        print(
            f"{segment_id} size={len(members)} angle={classify_angle(members[0])} "
            f"l1={members[0].l1_cluster_id} dates={min(dates) if dates else None}..{max(dates) if dates else None}"
        )
        for article in sorted(members, key=lambda row: (row.published_at or datetime.min, row.news_id))[:8]:
            dt = article.published_date.isoformat() if article.published_date else "?"
            print(f"  {article.news_id} {dt} {article.initiator}->{article.target} | {article.title[:160]}")


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    t0 = time.time()
    conn = connect(args)
    ensure_l15_infra(conn)
    try:
        articles = fetch_articles(conn, args.l1_run_id)
        LOGGER.info("loaded articles=%d in %.1fs", len(articles), time.time() - t0)
        segments = build_segments(articles, run_id=args.run_id)
        print_report(segments, sample_limit=args.sample_limit)
        if not args.dry_run:
            write_segments(
                conn,
                segments,
                run_id=args.run_id,
                l1_run_id=args.l1_run_id,
                clear_existing=args.clear_existing,
            )
            LOGGER.info("wrote L1.5 segments run_id=%s in %.1fs", args.run_id, time.time() - t0)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
