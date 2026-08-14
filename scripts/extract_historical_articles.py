#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from hashlib import md5
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from historical_http import curl_fetch
from news_date_cleaning import clean_published_at, parse_datetime
import trafilatura


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = PROJECT_ROOT / "data" / "historical_news" / "discovered_urls_3y.jsonl"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "historical_news" / "extracted_articles_3y.jsonl"
DEFAULT_ERROR_OUTPUT = PROJECT_ROOT / "data" / "historical_news" / "extracted_articles_3y_errors.jsonl"

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; GlobeMindArticleExtractor/1.0; "
        "+https://example.invalid/globemind)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
ARTICLE_TYPE_NAMES = {
    "article",
    "newsarticle",
    "reportagenewsarticle",
    "liveblogposting",
    "blogposting",
}
CONTAINER_HINTS = ("article", "story", "content", "main", "post", "entry")
AUTHOR_NOISE = {
    "updated",
    "read",
    "associated press",
    "the associated press",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch historical article URLs and extract title/body/pub time.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--errors", type=Path, default=DEFAULT_ERROR_OUTPUT)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--timeout", type=float, default=18.0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--site-id", action="append", default=[])
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def normalize_dt(value: str | None) -> str:
    dt = parse_datetime(value)
    return dt.isoformat() if dt else ""


def normalize_author(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


def clean_author(author: str) -> str:
    parts = [part.strip() for part in author.replace("|", ",").replace(";", ",").split(",")]
    kept: list[str] = []
    for part in parts:
        if not part:
            continue
        if part.lower() in AUTHOR_NOISE:
            continue
        kept.append(part)
    return ", ".join(kept)


def text_content(node: Any) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return " ".join(node.split())
    return " ".join(node.get_text(" ", strip=True).split())


def find_meta(soup: BeautifulSoup, keys: list[tuple[str, str]]) -> str:
    for attr_name, attr_value in keys:
        tag = soup.find("meta", attrs={attr_name: attr_value})
        if tag and tag.get("content"):
            return str(tag.get("content")).strip()
    return ""


def iter_json_ld_objects(raw_text: str) -> list[dict[str, Any]]:
    raw_text = raw_text.strip()
    if not raw_text:
        return []
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        return []

    output: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            output.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(parsed)
    return output


def pick_json_ld_article(soup: BeautifulSoup) -> dict[str, Any]:
    for script_tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw_text = script_tag.string or script_tag.get_text()
        for obj in iter_json_ld_objects(raw_text):
            raw_type = obj.get("@type")
            types = [raw_type] if isinstance(raw_type, str) else raw_type or []
            normalized_types = {str(item).lower() for item in types}
            if normalized_types & ARTICLE_TYPE_NAMES:
                return obj
    return {}


def body_from_json_ld(article_obj: dict[str, Any]) -> str:
    body = article_obj.get("articleBody")
    if isinstance(body, str):
        return " ".join(body.split())
    if isinstance(body, list):
        return "\n".join(" ".join(str(item).split()) for item in body if item)
    return ""


def score_container(tag: Any) -> tuple[int, int]:
    paragraphs = tag.find_all("p")
    paragraph_texts = [text_content(p) for p in paragraphs]
    paragraph_texts = [text for text in paragraph_texts if len(text) >= 40]
    total_len = sum(len(text) for text in paragraph_texts)
    return len(paragraph_texts), total_len


def best_body_container(soup: BeautifulSoup) -> Any:
    article_tag = soup.find("article")
    if article_tag:
        return article_tag

    candidates = []
    candidates.extend(soup.find_all(["main", "section", "div"]))
    best = None
    best_score = (-1, -1)
    for candidate in candidates:
        attrs = " ".join(
            filter(
                None,
                [
                    candidate.get("id", ""),
                    " ".join(candidate.get("class", [])),
                ],
            )
        ).lower()
        if attrs and not any(hint in attrs for hint in CONTAINER_HINTS):
            continue
        score = score_container(candidate)
        if score > best_score:
            best = candidate
            best_score = score
    return best


def clean_soup(soup: BeautifulSoup) -> None:
    for selector in [
        "script",
        "style",
        "noscript",
        "svg",
        "form",
        "header",
        "footer",
        "nav",
        "aside",
        "figure",
        "figcaption",
    ]:
        for tag in soup.select(selector):
            tag.decompose()


def body_from_dom(soup: BeautifulSoup) -> str:
    clean_soup(soup)
    container = best_body_container(soup)
    if container is None:
        return ""
    paragraphs = [text_content(tag) for tag in container.find_all("p")]
    paragraphs = [paragraph for paragraph in paragraphs if len(paragraph) >= 40]
    if not paragraphs:
        paragraphs = [text_content(container)]
    return "\n".join(paragraphs[:200]).strip()


def extract_trafilatura_metadata(html: str, url: str) -> dict[str, str]:
    try:
        extracted = trafilatura.extract(
            html,
            url=url,
            output_format="json",
            with_metadata=True,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
        )
    except Exception:
        return {}
    if not extracted:
        return {}
    try:
        payload = json.loads(extracted)
    except json.JSONDecodeError:
        return {}
    return {
        "title": str(payload.get("title") or "").strip(),
        "author": clean_author(normalize_author(payload.get("author"))),
        "published_at": normalize_dt(str(payload.get("date") or "").strip()),
        "language": str(payload.get("language") or "").strip(),
        "body": str(payload.get("text") or "").strip(),
    }


def extract_article(html: str, url: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    article_obj = pick_json_ld_article(soup)
    trafilatura_meta = extract_trafilatura_metadata(html, url)

    title = ""
    for candidate in [
        article_obj.get("headline") if article_obj else "",
        find_meta(soup, [("property", "og:title"), ("name", "twitter:title")]),
        trafilatura_meta.get("title", ""),
        text_content(soup.title),
    ]:
        if candidate:
            title = " ".join(str(candidate).split())
            break

    abstract = find_meta(
        soup,
        [
            ("name", "description"),
            ("property", "og:description"),
            ("name", "twitter:description"),
        ],
    )

    published_at = normalize_dt(
        article_obj.get("datePublished") if article_obj else ""
        or find_meta(
            soup,
            [
                ("property", "article:published_time"),
                ("name", "pubdate"),
                ("name", "publishdate"),
                ("itemprop", "datePublished"),
                ("name", "date"),
            ],
        )
    )
    if not published_at:
        time_tag = soup.find("time")
        if time_tag and time_tag.get("datetime"):
            published_at = normalize_dt(str(time_tag.get("datetime")))
    if not published_at:
        published_at = trafilatura_meta.get("published_at", "")

    language = ""
    if soup.html and soup.html.get("lang"):
        language = str(soup.html.get("lang")).strip()
    if not language:
        language = find_meta(soup, [("property", "og:locale"), ("http-equiv", "content-language")])
    if not language:
        language = trafilatura_meta.get("language", "")

    body = body_from_json_ld(article_obj) if article_obj else ""
    extraction_method = "jsonld"
    if not body:
        body = body_from_dom(soup)
        extraction_method = "dom"
    if len(body) < 400 and trafilatura_meta.get("body") and len(trafilatura_meta["body"]) > len(body):
        body = trafilatura_meta["body"]
        extraction_method = "trafilatura_fallback"

    author = ""
    if article_obj:
        author_obj = article_obj.get("author")
        if isinstance(author_obj, dict):
            author = str(author_obj.get("name") or "").strip()
        elif isinstance(author_obj, list):
            names = []
            for item in author_obj:
                if isinstance(item, dict) and item.get("name"):
                    names.append(str(item.get("name")).strip())
                elif item:
                    names.append(str(item).strip())
            author = ", ".join(name for name in names if name)
        elif author_obj:
            author = str(author_obj).strip()
    if not author:
        author = trafilatura_meta.get("author", "")
    author = clean_author(author)

    return {
        "title": title,
        "abstract": abstract,
        "body": body,
        "published_at": published_at,
        "language": language,
        "author": author,
        "extraction_method": extraction_method,
        "domain": urlparse(url).netloc,
    }


def fetch_and_extract_with_metrics(
    row: dict[str, Any], timeout: float
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any]]:
    url = str(row["url"])
    started = perf_counter()
    try:
        resp = curl_fetch(
            url=url,
            timeout=timeout,
            user_agent=HTTP_HEADERS["User-Agent"],
            accept=HTTP_HEADERS["Accept"],
        )
        if not resp.ok:
            raise RuntimeError(resp.error or f"http_{resp.status_code}")
        payload = extract_article(resp.body_text, url)
        if not payload["title"] or not payload["body"]:
            raise RuntimeError("missing_core_fields")
        fetched_at = datetime.now(timezone.utc).isoformat()
        date_result = clean_published_at(
            {
                **row,
                "request_url": url,
                "response_url": resp.final_url,
                "title": payload["title"],
                "body": payload["body"],
                "published_at": payload["published_at"],
                "fetched_at": fetched_at,
            }
        )

        article = dict(row)
        article.update(
            {
                "request_url": url,
                "response_url": resp.final_url,
                "title": payload["title"],
                "abstract": payload["abstract"],
                "body": payload["body"],
                "published_at": date_result.isoformat(),
                "published_at_raw": payload["published_at"] or str(row.get("lastmod", "")),
                "published_at_source": date_result.source,
                "published_at_confidence": date_result.confidence,
                "language": payload["language"],
                "author": payload["author"],
                "content_md5": md5(payload["body"].encode("utf-8")).hexdigest(),
                "fetch_status": resp.status_code,
                "fetched_at": fetched_at,
                "extraction_method": payload["extraction_method"],
            }
        )
        metrics = {
            "ok": True,
            "elapsed_sec": perf_counter() - started,
            "download_bytes": len(resp.body_bytes),
            "body_chars": len(payload["body"]),
            "fetch_status": resp.status_code,
            "extraction_method": payload["extraction_method"],
            "error": "",
        }
        return article, None, metrics
    except Exception as exc:
        error = dict(row)
        error.update(
            {
                "request_url": url,
                "error": str(exc),
                "failed_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        metrics = {
            "ok": False,
            "elapsed_sec": perf_counter() - started,
            "download_bytes": 0,
            "body_chars": 0,
            "fetch_status": None,
            "extraction_method": "",
            "error": str(exc),
        }
        return None, error, metrics


def fetch_and_extract(row: dict[str, Any], timeout: float) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    article, error, _metrics = fetch_and_extract_with_metrics(row, timeout)
    return article, error


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


def main() -> None:
    args = parse_args()
    rows = read_jsonl(args.input)
    if args.site_id:
        keep = set(args.site_id)
        rows = [row for row in rows if row.get("site_id") in keep]
    if args.limit > 0:
        rows = rows[: args.limit]

    articles: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        future_map = {
            executor.submit(fetch_and_extract, row, args.timeout): str(row.get("url", ""))
            for row in rows
        }
        for future in as_completed(future_map):
            article, error = future.result()
            if article is not None:
                articles.append(article)
            if error is not None:
                errors.append(error)

    articles.sort(key=lambda row: (str(row.get("site_id", "")), str(row.get("request_url", ""))))
    errors.sort(key=lambda row: (str(row.get("site_id", "")), str(row.get("request_url", ""))))
    ok_count = write_jsonl(args.output, articles)
    err_count = write_jsonl(args.errors, errors)
    print(f"wrote {ok_count} extracted articles to {args.output}")
    print(f"wrote {err_count} extraction errors to {args.errors}")


if __name__ == "__main__":
    main()
