#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from goose3 import Goose
from newspaper import Article
import trafilatura

from extract_historical_articles import extract_article
from historical_http import curl_fetch


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "historical_news" / "extractor_comparison.jsonl"

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; GlobeMindExtractorCompare/1.0; "
        "+https://example.invalid/globemind)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare article extraction libraries on real pages.")
    parser.add_argument("--url", action="append", default=[])
    parser.add_argument("--url-file", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=float, default=20.0)
    return parser.parse_args()


def load_urls(args: argparse.Namespace) -> list[str]:
    urls = list(args.url)
    if args.url_file:
        with args.url_file.open("r", encoding="utf-8") as fh:
            for line in fh:
                url = line.strip()
                if url:
                    urls.append(url)
    deduped: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url not in seen:
            seen.add(url)
            deduped.append(url)
    return deduped


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


def summarize_result(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = normalize_text(payload.get("body", ""))
    return {
        "extractor": name,
        "title": normalize_text(payload.get("title", "")),
        "authors": normalize_text(payload.get("authors", "")),
        "published_at": normalize_text(payload.get("published_at", "")),
        "language": normalize_text(payload.get("language", "")),
        "body_len": len(body),
        "body_preview": body[:300],
    }


def run_custom(url: str, html: str) -> dict[str, Any]:
    payload = extract_article(html, url)
    return {
        "title": payload.get("title", ""),
        "authors": "",
        "published_at": payload.get("published_at", ""),
        "language": payload.get("language", ""),
        "body": payload.get("body", ""),
    }


def run_trafilatura(url: str, html: str) -> dict[str, Any]:
    extracted = trafilatura.extract(
        html,
        url=url,
        output_format="json",
        with_metadata=True,
        include_comments=False,
        include_tables=False,
        favor_precision=True,
    )
    if not extracted:
        return {}
    payload = json.loads(extracted)
    return {
        "title": payload.get("title", ""),
        "authors": payload.get("author", ""),
        "published_at": payload.get("date", ""),
        "language": payload.get("language", ""),
        "body": payload.get("text", ""),
    }


def run_goose3(url: str, html: str) -> dict[str, Any]:
    with Goose() as goose:
        article = goose.extract(raw_html=html)
    publish_date = article.publish_date
    if hasattr(publish_date, "isoformat"):
        publish_date = publish_date.isoformat()
    return {
        "title": article.title or "",
        "authors": article.authors or [],
        "published_at": publish_date or "",
        "language": article.meta_lang or "",
        "body": article.cleaned_text or "",
    }


def run_newspaper3k(url: str, html: str) -> dict[str, Any]:
    article = Article(url=url, language="en")
    article.set_html(html)
    article.parse()
    return {
        "title": article.title or "",
        "authors": article.authors or [],
        "published_at": article.publish_date.isoformat() if article.publish_date else "",
        "language": article.meta_lang or "",
        "body": article.text or "",
    }


def compare_one(url: str, timeout: float) -> dict[str, Any]:
    response = curl_fetch(
        url=url,
        timeout=timeout,
        user_agent=HTTP_HEADERS["User-Agent"],
        accept=HTTP_HEADERS["Accept"],
    )
    if not response.ok:
        return {
            "url": url,
            "fetch_status": response.status_code,
            "fetch_error": response.error,
            "results": [],
        }

    html = response.body_text
    results = []
    for name, fn in [
        ("custom", run_custom),
        ("trafilatura", run_trafilatura),
        ("goose3", run_goose3),
        ("newspaper3k", run_newspaper3k),
    ]:
        try:
            payload = fn(url, html)
            results.append(summarize_result(name, payload))
        except Exception as exc:
            results.append(
                {
                    "extractor": name,
                    "error": str(exc),
                }
            )

    return {
        "url": url,
        "fetch_status": response.status_code,
        "final_url": response.final_url,
        "results": results,
    }


def main() -> None:
    args = parse_args()
    urls = load_urls(args)
    rows = [compare_one(url, args.timeout) for url in urls]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} comparison rows to {args.output}")


if __name__ == "__main__":
    main()
