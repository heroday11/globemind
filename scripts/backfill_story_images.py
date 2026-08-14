#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import psycopg2
from PIL import ImageFile
import requests

from db_runtime_config import require_database_password


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; GlobeMindImageResolver/1.0; "
        "+https://globemind.top)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.8",
}
IMAGE_HEADERS = {
    **HEADERS,
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
}

BAD_IMAGE_HINTS = (
    "logo",
    "favicon",
    "icon",
    "sprite",
    "avatar",
    "profile",
    "placeholder",
    "blank",
    "pixel",
    "tracking",
    "1x1",
    "spacer",
    "loader",
    "loading",
    "preloader",
    "spinner",
    "default",
    "transparent",
)
BAD_EXISTING_IMAGE_RE = r"(loader|loading|preloader|spinner|transparent|blank|pixel|tracking|1x1|spacer)"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EDITORIAL_COVER_DIR = PROJECT_ROOT / "frontend" / "vue_project" / "dist" / "imgs" / "story-covers"


@dataclass
class NewsCandidate:
    cluster_id: str
    news_id: int
    url: str
    domain: str
    source_name: str
    title: str


@dataclass
class ImageCandidate:
    news_id: int
    image_url: str
    image_kind: str
    source_url: str
    credit: str
    width: int | None = None
    height: int | None = None
    status: str = "ok"
    error: str | None = None
    score: float = 0.0


class ImageMetaParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.images: list[tuple[str, str]] = []
        self.json_ld_chunks: list[str] = []
        self._in_json_ld = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {str(k).lower(): (v or "") for k, v in attrs}
        tag = tag.lower()
        if tag == "meta":
            key = (attr.get("property") or attr.get("name") or attr.get("itemprop") or "").lower()
            content = attr.get("content") or ""
            if key in {
                "og:image",
                "og:image:url",
                "og:image:secure_url",
                "twitter:image",
                "twitter:image:src",
                "image",
                "thumbnailurl",
            }:
                self._add(content, key)
        elif tag == "link":
            rel = (attr.get("rel") or "").lower()
            href = attr.get("href") or ""
            if "image_src" in rel or "preload" in rel and (attr.get("as") or "").lower() == "image":
                self._add(href, f"link:{rel}")
        elif tag == "img":
            for key in ("src", "data-src", "data-original", "data-lazy-src"):
                self._add(attr.get(key) or "", f"img:{key}")
            for raw_url in parse_srcset(attr.get("srcset") or attr.get("data-srcset") or ""):
                self._add(raw_url, "img:srcset")
        elif tag == "script":
            script_type = (attr.get("type") or "").lower()
            self._in_json_ld = "ld+json" in script_type

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script":
            self._in_json_ld = False

    def handle_data(self, data: str) -> None:
        if self._in_json_ld and data.strip():
            self.json_ld_chunks.append(data)

    def _add(self, raw_url: str, kind: str) -> None:
        url = normalize_image_url(raw_url, self.base_url)
        if url:
            self.images.append((kind, url))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill news image metadata and L1 story covers.")
    parser.add_argument("--host", default=os.getenv("PG_HOST", "192.168.207.171"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PG_PORT", "54333")))
    parser.add_argument("--user", default=os.getenv("PG_WRITE_USER", "postgres"))
    parser.add_argument("--dbname", default="news")
    parser.add_argument("--l1-run-id", default="fast_l1_v2")
    parser.add_argument("--l15-run-id", default="fast_l15_v1")
    parser.add_argument("--cluster-limit", type=int, default=100)
    parser.add_argument("--news-per-cluster", type=int, default=5)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--timeout", type=float, default=9.0)
    parser.add_argument("--large-cluster-min-articles", type=int, default=2)
    parser.add_argument(
        "--editorial-recent-days",
        type=int,
        default=14,
        help="Also create editorial covers for recent smaller clusters that can appear in live views.",
    )
    parser.add_argument("--editorial-cover-dir", type=Path, default=DEFAULT_EDITORIAL_COVER_DIR)
    parser.add_argument("--editorial-cover-url-prefix", default="/imgs/story-covers")
    parser.add_argument("--disable-editorial-fallback", action="store_true")
    parser.add_argument("--refresh", action="store_true", help="Refetch news even when ok image assets already exist.")
    return parser.parse_args()


def connect(args: argparse.Namespace) -> Any:
    return psycopg2.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=require_database_password(),
        dbname=args.dbname,
        connect_timeout=15,
    )


def ensure_tables(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.news_image_assets (
                news_id BIGINT NOT NULL,
                image_url TEXT NOT NULL,
                image_kind TEXT NOT NULL DEFAULT 'og_image',
                source_url TEXT,
                credit TEXT,
                width INTEGER,
                height INTEGER,
                status TEXT NOT NULL DEFAULT 'ok',
                score DOUBLE PRECISION NOT NULL DEFAULT 0,
                error TEXT,
                fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (news_id, image_url)
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_news_image_assets_news_status
            ON public.news_image_assets (news_id, status)
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.story_cover_assets (
                cluster_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                cover_url TEXT,
                cover_kind TEXT NOT NULL DEFAULT 'remote_image',
                source_news_id BIGINT,
                credit TEXT,
                status TEXT NOT NULL DEFAULT 'ok',
                score DOUBLE PRECISION NOT NULL DEFAULT 0,
                selected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_story_cover_assets_run_status
            ON public.story_cover_assets (run_id, status, score DESC)
            """
        )
    conn.commit()


def demote_bad_existing_assets(conn: Any) -> tuple[int, int]:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE public.news_image_assets
            SET status = 'failed',
                error = 'bad_image_hint',
                updated_at = now()
            WHERE status = 'ok'
              AND lower(image_url) ~ %s
            """,
            (BAD_EXISTING_IMAGE_RE,),
        )
        asset_count = int(cur.rowcount or 0)
        cur.execute(
            """
            UPDATE public.story_cover_assets
            SET status = 'failed',
                updated_at = now()
            WHERE status = 'ok'
              AND lower(cover_url) ~ %s
            """,
            (BAD_EXISTING_IMAGE_RE,),
        )
        cover_count = int(cur.rowcount or 0)
    conn.commit()
    return asset_count, cover_count


def load_candidates(conn: Any, args: argparse.Namespace) -> list[NewsCandidate]:
    with conn.cursor() as cur:
        skip_existing = "" if args.refresh else """
            AND NOT EXISTS (
                SELECT 1
                FROM public.news_image_assets a
                WHERE a.news_id = n.id
                  AND a.status = 'ok'
            )
        """
        cur.execute(
            f"""
            WITH clusters AS MATERIALIZED (
                SELECT c.cluster_id
                FROM public.event_coref_clusters c
                LEFT JOIN public.story_source_breakdown sb ON sb.story_id = c.cluster_id
                LEFT JOIN public.story_cover_assets sc
                  ON sc.cluster_id = c.cluster_id
                 AND sc.run_id = c.run_id
                 AND sc.status = 'ok'
                WHERE c.run_id = %s
                  AND c.article_count >= 2
                  AND (%s OR sc.cluster_id IS NULL)
                ORDER BY c.article_count DESC, COALESCE(sb.source_count, 0) DESC, c.start_date DESC NULLS LAST
                LIMIT %s
            ),
            ranked AS (
                SELECT
                    m.cluster_id,
                    n.id AS news_id,
                    n.url,
                    n.title,
                    COALESCE(msp.source_name, ms.domain, '') AS source_name,
                    COALESCE(ms.domain, '') AS domain,
                    row_number() OVER (
                        PARTITION BY m.cluster_id
                        ORDER BY
                            CASE msp.credibility_tier
                                WHEN 'high' THEN 0
                                WHEN 'medium' THEN 1
                                ELSE 2
                            END,
                            CASE msp.source_type
                                WHEN 'wire_service' THEN 0
                                WHEN 'global_major_media' THEN 1
                                WHEN 'national_major_media' THEN 2
                                ELSE 3
                            END,
                            n.published_at NULLS LAST,
                            n.id
                    ) AS rn
                FROM clusters c
                JOIN public.event_coref_members m
                  ON m.cluster_id = c.cluster_id
                 AND m.run_id = %s
                JOIN public.news n ON n.id = m.news_id
                LEFT JOIN public.media_source ms ON ms.id = n.media_source_id
                LEFT JOIN public.media_source_profile msp ON msp.domain = ms.domain
                WHERE n.url IS NOT NULL
                  AND n.url LIKE 'http%%'
                  {skip_existing}
            )
            SELECT cluster_id, news_id, url, domain, source_name, title
            FROM ranked
            WHERE rn <= %s
            ORDER BY cluster_id, rn
            """,
            (args.l1_run_id, args.refresh, args.cluster_limit, args.l1_run_id, args.news_per_cluster),
        )
        rows = cur.fetchall()
    conn.commit()
    return [
        NewsCandidate(
            cluster_id=str(row[0]),
            news_id=int(row[1]),
            url=str(row[2]),
            domain=str(row[3] or ""),
            source_name=str(row[4] or ""),
            title=str(row[5] or ""),
        )
        for row in rows
    ]


def normalize_image_url(raw_url: str, base_url: str) -> str:
    text = html.unescape(str(raw_url or "")).strip().strip("'\"")
    if not text or text.startswith("data:"):
        return ""
    if "," in text and " " not in text:
        text = text.split(",", 1)[0]
    url = urljoin(base_url, text)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    lowered = url.lower()
    if any(hint in lowered for hint in BAD_IMAGE_HINTS):
        return ""
    if lowered.endswith((".svg", ".gif")):
        return ""
    return url


def parse_srcset(value: str) -> list[str]:
    urls: list[str] = []
    for item in str(value or "").split(","):
        text = item.strip()
        if not text:
            continue
        urls.append(text.split()[0])
    return urls


def extract_json_ld_images(chunks: list[str], base_url: str) -> list[tuple[str, str]]:
    images: list[tuple[str, str]] = []
    for chunk in chunks:
        for match in re.finditer(r'"image"\s*:\s*"([^"]+)"', chunk, re.IGNORECASE):
            url = normalize_image_url(match.group(1), base_url)
            if url:
                images.append(("jsonld:image", url))
        for match in re.finditer(r'"image"\s*:\s*\{[^{}]*"url"\s*:\s*"([^"]+)"', chunk, re.IGNORECASE | re.DOTALL):
            url = normalize_image_url(match.group(1), base_url)
            if url:
                images.append(("jsonld:image.url", url))
        for match in re.finditer(r'"url"\s*:\s*"([^"]+\.(?:jpg|jpeg|png|webp)(?:\?[^"]*)?)"', chunk, re.IGNORECASE):
            url = normalize_image_url(match.group(1), base_url)
            if url:
                images.append(("jsonld:url", url))
    return images


def extract_image_urls(page_html: str, page_url: str) -> list[tuple[str, str]]:
    parser = ImageMetaParser(page_url)
    parser.feed(page_html[:500_000])
    items = parser.images + extract_json_ld_images(parser.json_ld_chunks, page_url)
    seen: set[str] = set()
    deduped: list[tuple[str, str]] = []
    priority = {
        "og:image:secure_url": 0,
        "og:image:url": 1,
        "og:image": 2,
        "twitter:image": 3,
        "twitter:image:src": 4,
        "image": 5,
        "thumbnailurl": 6,
        "img:src": 18,
        "img:srcset": 19,
    }
    for kind, url in sorted(items, key=lambda item: priority.get(item[0], 20)):
        if url in seen:
            continue
        seen.add(url)
        deduped.append((kind, url))
    return deduped[:6]


def new_http_session() -> requests.Session:
    session = requests.Session()
    # The crawler may run in an environment with ALL_PROXY/HTTP_PROXY set to
    # SOCKS endpoints. requests needs an extra dependency for SOCKS, so image
    # backfill should use direct connections unless proxy support is added here.
    session.trust_env = False
    return session


def probe_image(session: requests.Session, url: str, timeout: float) -> tuple[int | None, int | None, str | None]:
    try:
        with session.get(url, headers=IMAGE_HEADERS, timeout=timeout, stream=True, allow_redirects=True) as response:
            status = response.status_code
            content_type = response.headers.get("content-type", "").lower()
            if status >= 400:
                return None, None, f"image_http_{status}"
            if content_type and "image" not in content_type:
                return None, None, f"not_image:{content_type[:40]}"
            parser = ImageFile.Parser()
            total = 0
            for chunk in response.iter_content(chunk_size=16384):
                if not chunk:
                    continue
                total += len(chunk)
                parser.feed(chunk)
                if parser.image:
                    width, height = parser.image.size
                    return int(width), int(height), None
                if total > 524288:
                    break
            return None, None, "image_size_unknown"
    except Exception as exc:
        return None, None, str(exc)[:180]


def image_score(kind: str, width: int | None, height: int | None, source_name: str) -> float:
    width = width or 0
    height = height or 0
    area = width * height
    kind_bonus = 30 if kind.startswith("og:image") else 18 if kind.startswith("twitter") else 10 if kind.startswith("jsonld") else 4
    size_bonus = min(area / 25_000, 70)
    aspect = width / height if width and height else 0
    aspect_bonus = 12 if 1.2 <= aspect <= 2.2 else 4 if 0.8 <= aspect <= 2.6 else -8
    source_bonus = 8 if source_name else 0
    return round(kind_bonus + size_bonus + aspect_bonus + source_bonus, 4)


def fetch_news_image(candidate: NewsCandidate, timeout: float) -> ImageCandidate:
    try:
        with new_http_session() as session:
            response = session.get(candidate.url, headers=HEADERS, timeout=timeout, allow_redirects=True)
            if response.status_code >= 400:
                return ImageCandidate(candidate.news_id, "", "none", candidate.url, candidate.source_name, status="failed", error=f"http_{response.status_code}")
            content_type = response.headers.get("content-type", "").lower()
            if content_type and "html" not in content_type and "xml" not in content_type:
                return ImageCandidate(candidate.news_id, "", "none", candidate.url, candidate.source_name, status="failed", error=f"not_html:{content_type[:40]}")
            image_urls = extract_image_urls(response.text, response.url)
            if not image_urls:
                return ImageCandidate(candidate.news_id, "", "none", candidate.url, candidate.source_name, status="missing", error="no_meta_image")
            failures: list[str] = []
            for kind, image_url in image_urls:
                width, height, error = probe_image(session, image_url, timeout)
                if error:
                    failures.append(error)
                    continue
                if (width or 0) < 320 or (height or 0) < 160:
                    failures.append(f"too_small:{width}x{height}")
                    continue
                score = image_score(kind, width, height, candidate.source_name)
                return ImageCandidate(
                    news_id=candidate.news_id,
                    image_url=image_url,
                    image_kind=kind,
                    source_url=candidate.url,
                    credit=candidate.source_name or candidate.domain,
                    width=width,
                    height=height,
                    status="ok",
                    score=score,
                )
            return ImageCandidate(candidate.news_id, image_urls[0][1], image_urls[0][0], candidate.url, candidate.source_name, status="failed", error=";".join(failures[:3]))
    except Exception as exc:
        return ImageCandidate(candidate.news_id, "", "none", candidate.url, candidate.source_name, status="failed", error=str(exc)[:180])


def save_image_assets(conn: Any, assets: list[ImageCandidate]) -> None:
    with conn.cursor() as cur:
        for asset in assets:
            image_url = asset.image_url or f"missing://{asset.news_id}"
            cur.execute(
                """
                INSERT INTO public.news_image_assets (
                    news_id, image_url, image_kind, source_url, credit,
                    width, height, status, score, error, fetched_at, updated_at
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),now())
                ON CONFLICT (news_id, image_url)
                DO UPDATE SET
                    image_kind = EXCLUDED.image_kind,
                    source_url = EXCLUDED.source_url,
                    credit = EXCLUDED.credit,
                    width = EXCLUDED.width,
                    height = EXCLUDED.height,
                    status = EXCLUDED.status,
                    score = EXCLUDED.score,
                    error = EXCLUDED.error,
                    fetched_at = now(),
                    updated_at = now()
                """,
                (
                    asset.news_id,
                    image_url,
                    asset.image_kind,
                    asset.source_url,
                    asset.credit,
                    asset.width,
                    asset.height,
                    asset.status,
                    asset.score,
                    asset.error,
                ),
            )
    conn.commit()


def build_story_covers(conn: Any, run_id: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH ranked AS (
                SELECT
                    m.cluster_id,
                    m.run_id,
                    a.news_id,
                    a.image_url,
                    a.image_kind,
                    a.credit,
                    a.score,
                    row_number() OVER (
                        PARTITION BY m.cluster_id
                        ORDER BY
                            a.score DESC,
                            COALESCE(a.width, 0) * COALESCE(a.height, 0) DESC,
                            m.published_at NULLS LAST,
                            a.news_id
                    ) AS rn
                FROM public.event_coref_members m
                JOIN public.news_image_assets a
                  ON a.news_id = m.news_id
                 AND a.status = 'ok'
                WHERE m.run_id = %s
                  AND lower(a.image_url) !~ %s
            ),
            picked AS (
                SELECT *
                FROM ranked
                WHERE rn = 1
            )
            INSERT INTO public.story_cover_assets (
                cluster_id, run_id, cover_url, cover_kind, source_news_id,
                credit, status, score, selected_at, updated_at
            )
            SELECT
                cluster_id,
                run_id,
                image_url,
                'remote_image',
                news_id,
                credit,
                'ok',
                score,
                now(),
                now()
            FROM picked
            ON CONFLICT (cluster_id)
            DO UPDATE SET
                run_id = EXCLUDED.run_id,
                cover_url = EXCLUDED.cover_url,
                cover_kind = EXCLUDED.cover_kind,
                source_news_id = EXCLUDED.source_news_id,
                credit = EXCLUDED.credit,
                status = EXCLUDED.status,
                score = EXCLUDED.score,
                selected_at = now(),
                updated_at = now()
            """
            ,
            (run_id, BAD_EXISTING_IMAGE_RE),
        )
        count = cur.rowcount
    conn.commit()
    return int(count or 0)


def theme_for_family(family: str | None) -> tuple[str, str, str]:
    key = (family or "general").lower()
    themes = {
        "diplomacy": ("#17324d", "#2f80ed", "#f2c94c"),
        "conflict": ("#2d1f2f", "#eb5757", "#f2994a"),
        "security": ("#102a43", "#56ccf2", "#27ae60"),
        "public_development": ("#18392b", "#6fcf97", "#f2c94c"),
        "economic_policy": ("#263238", "#00b894", "#74b9ff"),
        "humanitarian": ("#3a2449", "#bb6bd9", "#f2c94c"),
        "disaster": ("#3d2c1f", "#f2994a", "#56ccf2"),
        "law": ("#1f2d3d", "#9b51e0", "#f2f2f2"),
    }
    return themes.get(key, ("#1d2733", "#2d9cdb", "#f2c94c"))


def icon_svg(family: str | None) -> str:
    key = (family or "").lower()
    if key == "diplomacy":
        return """
        <circle cx="330" cy="210" r="62" fill="none" stroke="rgba(255,255,255,.78)" stroke-width="8"/>
        <circle cx="490" cy="210" r="62" fill="none" stroke="rgba(255,255,255,.78)" stroke-width="8"/>
        <path d="M392 210h36" stroke="rgba(255,255,255,.9)" stroke-width="10" stroke-linecap="round"/>
        <path d="M315 292c42 34 100 50 174 48 66-2 121-21 166-55" fill="none" stroke="rgba(255,255,255,.35)" stroke-width="8" stroke-linecap="round"/>
        """
    if key == "public_development":
        return """
        <path d="M245 330h330" stroke="rgba(255,255,255,.75)" stroke-width="10" stroke-linecap="round"/>
        <path d="M285 330V215h70v115M390 330V165h85v165M510 330V245h70v85" fill="none" stroke="rgba(255,255,255,.78)" stroke-width="10" stroke-linejoin="round"/>
        <path d="M260 380c76-44 135-66 208-68 58-2 103 9 158 37" fill="none" stroke="rgba(255,255,255,.28)" stroke-width="14" stroke-linecap="round"/>
        """
    if key in {"conflict", "security"}:
        return """
        <path d="M275 330l86-170 80 116 54-66 72 120" fill="none" stroke="rgba(255,255,255,.78)" stroke-width="12" stroke-linejoin="round"/>
        <circle cx="361" cy="160" r="16" fill="rgba(255,255,255,.85)"/>
        <circle cx="495" cy="210" r="14" fill="rgba(255,255,255,.65)"/>
        """
    return """
    <circle cx="410" cy="245" r="118" fill="none" stroke="rgba(255,255,255,.62)" stroke-width="10"/>
    <path d="M300 245h220M410 135c43 49 64 86 64 110s-21 61-64 110M410 135c-43 49-64 86-64 110s21 61 64 110" fill="none" stroke="rgba(255,255,255,.34)" stroke-width="8"/>
    """


def editorial_svg(cluster: dict[str, Any]) -> str:
    family = str(cluster.get("event_family") or "")
    bg, primary, accent = theme_for_family(family)
    digest = hashlib.sha1(str(cluster.get("cluster_id") or "").encode("utf-8")).hexdigest()
    offset = int(digest[:2], 16) % 80
    title = html.escape(str(cluster.get("title") or family or "Story cover"))
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 820 460" role="img">
  <title>{title}</title>
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{bg}"/>
      <stop offset="0.58" stop-color="{primary}"/>
      <stop offset="1" stop-color="{accent}"/>
    </linearGradient>
    <radialGradient id="r" cx="70%" cy="28%" r="72%">
      <stop offset="0" stop-color="rgba(255,255,255,.35)"/>
      <stop offset="1" stop-color="rgba(255,255,255,0)"/>
    </radialGradient>
  </defs>
  <rect width="820" height="460" fill="url(#g)"/>
  <rect width="820" height="460" fill="url(#r)"/>
  <g opacity=".22" stroke="white" stroke-width="1.4">
    <path d="M{-40 + offset} 95C120 30 200 50 320 98s230 30 548-42"/>
    <path d="M{-65 + offset} 198C105 160 204 168 342 215s238 36 536-30"/>
    <path d="M{-70 + offset} 316C102 270 210 276 348 326s252 42 550-20"/>
  </g>
  <g transform="translate(0,0)">
    {icon_svg(family)}
  </g>
  <g opacity=".26">
    <circle cx="700" cy="92" r="48" fill="white"/>
    <circle cx="105" cy="382" r="72" fill="white"/>
  </g>
</svg>
"""


def build_editorial_story_covers(
    conn: Any,
    *,
    run_id: str,
    min_articles: int,
    recent_days: int,
    cover_dir: Path,
    url_prefix: str,
) -> int:
    cover_dir.mkdir(parents=True, exist_ok=True)
    url_prefix = url_prefix.rstrip("/")
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.cluster_id, c.run_id, c.article_count, c.event_family,
                   c.event_action, c.initiator, c.target, c.location, c.title
            FROM public.event_coref_clusters c
            LEFT JOIN public.story_cover_assets sc
              ON sc.cluster_id = c.cluster_id
             AND sc.run_id = c.run_id
             AND sc.status = 'ok'
            WHERE c.run_id = %s
              AND (
                  c.article_count >= %s
                  OR COALESCE(c.end_date, c.start_date) >= CURRENT_DATE - (%s * INTERVAL '1 day')
              )
              AND sc.cluster_id IS NULL
            ORDER BY c.article_count DESC, c.end_date DESC NULLS LAST, c.cluster_id
            """,
            (run_id, min_articles, recent_days),
        )
        columns = [desc[0] for desc in cur.description]
        clusters = [dict(zip(columns, row)) for row in cur.fetchall()]
    conn.commit()

    if not clusters:
        return 0

    rows: list[tuple[str, str, str, str, None, str, str, float]] = []
    for cluster in clusters:
        cluster_id = str(cluster["cluster_id"])
        filename = f"{cluster_id}.svg"
        (cover_dir / filename).write_text(editorial_svg(cluster), encoding="utf-8")
        rows.append(
            (
                cluster_id,
                str(cluster["run_id"]),
                f"{url_prefix}/{filename}",
                "editorial_svg",
                None,
                "GlobeMind editorial cover",
                "ok",
                42.0 + min(int(cluster.get("article_count") or 0), 20),
            )
        )

    with conn.cursor() as cur:
        for row in rows:
            cur.execute(
                """
                INSERT INTO public.story_cover_assets (
                    cluster_id, run_id, cover_url, cover_kind, source_news_id,
                    credit, status, score, selected_at, updated_at
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now(),now())
                ON CONFLICT (cluster_id)
                DO UPDATE SET
                    run_id = EXCLUDED.run_id,
                    cover_url = EXCLUDED.cover_url,
                    cover_kind = EXCLUDED.cover_kind,
                    source_news_id = EXCLUDED.source_news_id,
                    credit = EXCLUDED.credit,
                    status = EXCLUDED.status,
                    score = EXCLUDED.score,
                    selected_at = now(),
                    updated_at = now()
                """,
                row,
            )
    conn.commit()
    return len(rows)


def build_related_story_covers(conn: Any, run_id: str, min_articles: int) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH missing AS (
                SELECT c.*
                FROM public.event_coref_clusters c
                LEFT JOIN public.story_cover_assets sc
                  ON sc.cluster_id = c.cluster_id
                 AND sc.run_id = c.run_id
                 AND sc.status = 'ok'
                WHERE c.run_id = %s
                  AND c.article_count >= %s
                  AND sc.cluster_id IS NULL
            ),
            donor AS (
                SELECT
                    dc.cluster_id,
                    dc.event_domain,
                    dc.event_family,
                    dc.event_action,
                    dc.initiator,
                    dc.target,
                    dc.location,
                    dc.title,
                    dc.article_count,
                    sc.cover_url,
                    sc.source_news_id,
                    sc.credit,
                    sc.score
                FROM public.event_coref_clusters dc
                JOIN public.story_cover_assets sc
                  ON sc.cluster_id = dc.cluster_id
                 AND sc.run_id = dc.run_id
                 AND sc.status = 'ok'
                WHERE dc.run_id = %s
                  AND sc.cover_url IS NOT NULL
            ),
            ranked AS (
                SELECT
                    m.cluster_id,
                    m.run_id,
                    d.cover_url,
                    d.source_news_id,
                    d.credit,
                    CASE
                        WHEN d.event_family IS NOT DISTINCT FROM m.event_family
                         AND d.event_action IS NOT DISTINCT FROM m.event_action
                        THEN 'related_event_image'
                        ELSE 'family_event_image'
                    END AS cover_kind,
                    (
                        CASE
                            WHEN d.event_family IS NOT DISTINCT FROM m.event_family
                             AND d.event_action IS NOT DISTINCT FROM m.event_action
                            THEN 80 ELSE 35
                        END
                        + CASE
                            WHEN lower(COALESCE(d.initiator, '')) = lower(COALESCE(m.initiator, ''))
                             AND COALESCE(m.initiator, '') <> ''
                            THEN 25 ELSE 0
                          END
                        + CASE
                            WHEN lower(COALESCE(d.target, '')) = lower(COALESCE(m.target, ''))
                             AND COALESCE(m.target, '') <> ''
                            THEN 25 ELSE 0
                          END
                        + CASE
                            WHEN lower(COALESCE(d.location, '')) = lower(COALESCE(m.location, ''))
                             AND COALESCE(m.location, '') <> ''
                            THEN 15 ELSE 0
                          END
                        + LEAST(d.article_count, 20)
                        + d.score * 0.05
                    ) AS match_score,
                    row_number() OVER (
                        PARTITION BY m.cluster_id
                        ORDER BY
                            (
                                CASE
                                    WHEN d.event_family IS NOT DISTINCT FROM m.event_family
                                     AND d.event_action IS NOT DISTINCT FROM m.event_action
                                    THEN 80 ELSE 35
                                END
                                + CASE
                                    WHEN lower(COALESCE(d.initiator, '')) = lower(COALESCE(m.initiator, ''))
                                     AND COALESCE(m.initiator, '') <> ''
                                    THEN 25 ELSE 0
                                  END
                                + CASE
                                    WHEN lower(COALESCE(d.target, '')) = lower(COALESCE(m.target, ''))
                                     AND COALESCE(m.target, '') <> ''
                                    THEN 25 ELSE 0
                                  END
                                + CASE
                                    WHEN lower(COALESCE(d.location, '')) = lower(COALESCE(m.location, ''))
                                     AND COALESCE(m.location, '') <> ''
                                    THEN 15 ELSE 0
                                  END
                                + LEAST(d.article_count, 20)
                                + d.score * 0.05
                            ) DESC,
                            d.article_count DESC,
                            d.cluster_id
                    ) AS rn
                FROM missing m
                JOIN donor d
                  ON d.cluster_id <> m.cluster_id
                 AND d.event_family IS NOT DISTINCT FROM m.event_family
            ),
            picked AS (
                SELECT *
                FROM ranked
                WHERE rn = 1
                  AND match_score >= 55
            )
            INSERT INTO public.story_cover_assets (
                cluster_id, run_id, cover_url, cover_kind, source_news_id,
                credit, status, score, selected_at, updated_at
            )
            SELECT
                cluster_id,
                run_id,
                cover_url,
                cover_kind,
                source_news_id,
                credit,
                'ok',
                match_score,
                now(),
                now()
            FROM picked
            ON CONFLICT (cluster_id)
            DO UPDATE SET
                run_id = EXCLUDED.run_id,
                cover_url = EXCLUDED.cover_url,
                cover_kind = EXCLUDED.cover_kind,
                source_news_id = EXCLUDED.source_news_id,
                credit = EXCLUDED.credit,
                status = EXCLUDED.status,
                score = EXCLUDED.score,
                selected_at = now(),
                updated_at = now()
            """
            ,
            (run_id, min_articles, run_id),
        )
        count = cur.rowcount
    conn.commit()
    return int(count or 0)


def main() -> None:
    args = parse_args()
    started = time.time()
    conn = connect(args)
    try:
        ensure_tables(conn)
        demoted_assets, demoted_covers = demote_bad_existing_assets(conn)
        if demoted_assets or demoted_covers:
            print(f"demoted bad assets: news_assets={demoted_assets} story_covers={demoted_covers}")
        candidates = load_candidates(conn, args)
        print(f"candidate news: {len(candidates)}")
        if candidates:
            assets: list[ImageCandidate] = []
            with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
                futures = [executor.submit(fetch_news_image, item, args.timeout) for item in candidates]
                for idx, future in enumerate(as_completed(futures), start=1):
                    asset = future.result()
                    assets.append(asset)
                    if idx % 25 == 0 or idx == len(futures):
                        ok = sum(1 for item in assets if item.status == "ok")
                        print(f"processed {idx}/{len(futures)} ok={ok}")
            save_image_assets(conn, assets)
        cover_count = build_story_covers(conn, args.l1_run_id)
        related_cover_count = build_related_story_covers(conn, args.l1_run_id, args.large_cluster_min_articles)
        editorial_cover_count = 0
        if not args.disable_editorial_fallback:
            editorial_cover_count = build_editorial_story_covers(
                conn,
                run_id=args.l1_run_id,
                min_articles=args.large_cluster_min_articles,
                recent_days=args.editorial_recent_days,
                cover_dir=args.editorial_cover_dir,
                url_prefix=args.editorial_cover_url_prefix,
            )
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM public.news_image_assets WHERE status = 'ok'")
            ok_assets = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM public.story_cover_assets WHERE run_id = %s AND status = 'ok'", (args.l1_run_id,))
            ok_covers = int(cur.fetchone()[0])
        print(f"story covers upserted: {cover_count}")
        print(f"related story covers upserted: {related_cover_count}")
        print(f"editorial story covers upserted: {editorial_cover_count}")
        print(f"ok image assets total: {ok_assets}")
        print(f"ok story covers total: {ok_covers}")
        print(f"elapsed_sec: {time.time() - started:.1f}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
