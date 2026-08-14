#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import faulthandler
import json
import os
import random
import re
import signal
import sqlite3
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import md5
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from aiohttp import ClientSession

from discover_historical_urls import is_blocked_article_url
from news_date_cleaning import clean_published_at

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = PROJECT_ROOT / "data" / "historical_news" / "discovered_urls_sample.jsonl"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "historical_news" / "adaptive_extracted_articles.jsonl"
DEFAULT_ERRORS = PROJECT_ROOT / "data" / "historical_news" / "adaptive_extracted_articles_errors.jsonl"
DEFAULT_STATS = PROJECT_ROOT / "data" / "historical_news" / "adaptive_extractor_stats.json"

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; GlobeMindAdaptiveExtractor/1.0; "
        "+https://example.invalid/globemind)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}

RATE_LIMIT_STATUS = {401, 403, 429, 500, 502, 503, 504}
STOP_REQUESTED = False
RESUME_INDEX_BATCH_SIZE = 50000


@dataclass
class DomainState:
    domain: str
    queue: deque[dict[str, Any]] = field(default_factory=deque)
    in_flight: int = 0
    target_concurrency: int = 1
    next_ready_at: float = 0.0
    cooldown_sec: float = 0.0
    ewma_latency: float = 0.0
    successes: int = 0
    failures: int = 0
    success_streak: int = 0
    fail_streak: int = 0
    total_bytes: int = 0
    proxy_name: str = ""


@dataclass
class ProxyState:
    name: str
    socks_url: str
    region: str = ""
    successes: int = 0
    failures: int = 0
    cooldown_until: float = 0.0
    consecutive_failures: int = 0
    proxy_errors: int = 0
    rate_limit_errors: int = 0
    other_errors: int = 0
    last_error: str = ""
    last_error_at: str = ""
    disabled_count: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Adaptive global extractor with per-domain backoff.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--errors", type=Path, default=DEFAULT_ERRORS)
    parser.add_argument("--stats", type=Path, default=DEFAULT_STATS)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--site-id", action="append", default=[])
    parser.add_argument("--global-concurrency", type=int, default=16)
    parser.add_argument("--max-per-domain", type=int, default=4)
    parser.add_argument("--min-per-domain", type=int, default=1)
    parser.add_argument("--proxy-pool", type=Path)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--base-delay-ms", type=int, default=0)
    parser.add_argument("--jitter-ms", type=int, default=250)
    parser.add_argument("--retry-limit", type=int, default=2)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--resume-index-path",
        type=Path,
        help="SQLite index of processed request URLs for fast --resume restarts.",
    )
    parser.add_argument("--flush-every", type=int, default=25)
    parser.add_argument("--progress-path", type=Path)
    parser.add_argument("--progress-interval-sec", type=float, default=5.0)
    parser.add_argument(
        "--max-runtime-sec",
        type=float,
        default=0.0,
        help="If positive, stop dispatching new work after this many seconds so an external supervisor can restart cleanly.",
    )
    parser.add_argument(
        "--max-idle-sec",
        type=float,
        default=0.0,
        help="If positive, mark remaining queued rows as scheduler_idle_timeout after this many seconds without dispatch or completion.",
    )
    parser.add_argument("--shuffle", action="store_true")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--proxy-failure-threshold", type=int, default=3)
    parser.add_argument("--proxy-base-cooldown-sec", type=float, default=120.0)
    parser.add_argument("--proxy-max-cooldown-sec", type=float, default=1800.0)
    parser.add_argument("--proxy-health-path", type=Path)
    return parser.parse_args()


def _load_connector_types() -> tuple[Any, Any | None]:
    try:
        from aiohttp import TCPConnector
    except ImportError as exc:
        raise RuntimeError("aiohttp is required to run the adaptive extractor") from exc
    try:
        from aiohttp_socks import ProxyConnector
    except ImportError:
        ProxyConnector = None
    return TCPConnector, ProxyConnector


def build_connector() -> Any:
    TCPConnector, ProxyConnector = _load_connector_types()
    proxy = os.getenv("ALL_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
    if proxy:
        if ProxyConnector is None:
            raise RuntimeError("aiohttp_socks is required when a proxy is configured")
        return ProxyConnector.from_url(normalize_proxy_url(proxy), limit=0, ttl_dns_cache=300)
    return TCPConnector(limit=0, ttl_dns_cache=300, ssl=False)


def build_proxy_connector(socks_url: str | None) -> Any:
    TCPConnector, ProxyConnector = _load_connector_types()
    if socks_url:
        if ProxyConnector is None:
            raise RuntimeError("aiohttp_socks is required when --proxy-pool is used")
        return ProxyConnector.from_url(normalize_proxy_url(socks_url), limit=0, ttl_dns_cache=300)
    return TCPConnector(limit=0, ttl_dns_cache=300, ssl=False)


def normalize_proxy_url(proxy_url: str) -> str:
    if proxy_url.startswith("socks5h://"):
        return "socks5://" + proxy_url[len("socks5h://") :]
    return proxy_url


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_input_jsonl(path: Path) -> list[dict[str, Any]]:
    from extract_historical_articles import read_jsonl

    return read_jsonl(path)


def extract_article_payload(text: str, url: str) -> dict[str, Any]:
    from extract_historical_articles import extract_article

    return extract_article(text, url)


def append_jsonl_row(handle: Any, row: dict[str, Any]) -> None:
    handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def row_key(row: dict[str, Any]) -> str:
    return str(row.get("request_url") or row.get("url") or "")


_JSON_STRING_FIELD_PATTERNS: dict[str, re.Pattern[bytes]] = {}


def extract_json_string_field(raw_line: bytes, field: str) -> str:
    pattern = _JSON_STRING_FIELD_PATTERNS.get(field)
    if pattern is None:
        pattern = re.compile(rb'"' + re.escape(field.encode("utf-8")) + rb'"\s*:\s*"((?:\\.|[^"\\])*)"')
        _JSON_STRING_FIELD_PATTERNS[field] = pattern
    match = pattern.search(raw_line)
    if not match:
        return ""
    try:
        value = json.loads(b'"' + match.group(1) + b'"')
    except Exception:
        return match.group(1).decode("utf-8", errors="ignore")
    return str(value or "")


def extract_processed_row_fields(raw_line: bytes, is_error: bool) -> tuple[str, str, str, int, int]:
    key = extract_json_string_field(raw_line, "request_url") or extract_json_string_field(raw_line, "url")
    site_id = extract_json_string_field(raw_line, "site_id")
    error_text = extract_json_string_field(raw_line, "error") if is_error else ""
    if key:
        if is_error:
            return key, site_id, error_text, 0, 0
        body = extract_json_string_field(raw_line, "body")
        return key, site_id, "", len(body), len(body.encode("utf-8", errors="ignore"))

    row = json.loads(raw_line)
    key = row_key(row)
    site_id = str(row.get("site_id") or "")
    if is_error:
        return key, site_id, str(row.get("error") or ""), 0, 0
    body = row.get("body")
    if isinstance(body, str):
        return key, site_id, "", len(body), len(body.encode("utf-8", errors="ignore"))
    return key, site_id, "", len(str(row.get("body") or "")), 0


def load_processed_rows(
    output_path: Path,
    error_path: Path,
    allowed_keys: set[str] | None = None,
    rebuild_index_conn: sqlite3.Connection | None = None,
) -> tuple[set[str], int, int, int, int, Counter[str], Counter[str]]:
    processed: set[str] = set()
    success_keys: set[str] = set()
    error_keys: set[str] = set()
    success_count = 0
    failure_count = 0
    total_bytes = 0
    total_body_chars = 0
    error_counter: Counter[str] = Counter()
    site_error_counter: Counter[str] = Counter()
    index_rows: list[tuple[str, str, str, str, int, int]] = []
    last_index_log = time.monotonic()

    for path, is_error in ((output_path, False), (error_path, True)):
        if not path.exists():
            continue
        with path.open("rb") as fh:
            for raw_line in fh:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                fast_key = extract_json_string_field(raw_line, "request_url") or extract_json_string_field(raw_line, "url")
                if fast_key and allowed_keys is not None and fast_key not in allowed_keys:
                    continue
                try:
                    key, site_id, error_text, body_chars, body_bytes = extract_processed_row_fields(raw_line, is_error)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if is_error:
                    if not key or (allowed_keys is not None and key not in allowed_keys):
                        continue
                    if key in success_keys or key in error_keys:
                        continue
                    error_keys.add(key)
                    processed.add(key)
                    failure_count += 1
                    error_counter[error_text] += 1
                    site_error_counter[site_id] += 1
                    if rebuild_index_conn is not None:
                        index_rows.append((key, "error", site_id, error_text, 0, 0))
                else:
                    if not key or (allowed_keys is not None and key not in allowed_keys):
                        continue
                    if key in success_keys:
                        continue
                    success_keys.add(key)
                    processed.add(key)
                    success_count += 1
                    total_body_chars += body_chars
                    total_bytes += body_bytes
                    if rebuild_index_conn is not None:
                        index_rows.append((key, "success", site_id, "", body_chars, body_bytes))
                if rebuild_index_conn is not None and len(index_rows) >= RESUME_INDEX_BATCH_SIZE:
                    rebuild_index_conn.executemany(
                        """
                        INSERT OR IGNORE INTO processed_keys
                            (key, status, site_id, error, body_chars, body_bytes)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        index_rows,
                    )
                    rebuild_index_conn.commit()
                    index_rows.clear()
                if rebuild_index_conn is not None and time.monotonic() - last_index_log >= 30:
                    print(
                        "[resume-index] "
                        f"scanning={path.name} successes={success_count} "
                        f"failures={failure_count} processed_keys={len(processed)}",
                        flush=True,
                    )
                    last_index_log = time.monotonic()
    if rebuild_index_conn is not None and index_rows:
        rebuild_index_conn.executemany(
            """
            INSERT OR IGNORE INTO processed_keys
                (key, status, site_id, error, body_chars, body_bytes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            index_rows,
        )
    return processed, success_count, failure_count, total_bytes, total_body_chars, error_counter, site_error_counter


def default_resume_index_path(progress_path: Path) -> Path:
    cache_root = Path(os.getenv("GLOBEMIND_RESUME_INDEX_DIR", "/root/.cache/globemind/resume_indexes"))
    digest = md5(str(progress_path).encode("utf-8")).hexdigest()[:12]
    return cache_root / f"{progress_path.stem}_{digest}.sqlite3"


def file_signature(path: Path) -> dict[str, int]:
    if not path.exists():
        return {"size": 0, "mtime_ns": 0}
    stat = path.stat()
    return {"size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}


def open_resume_index(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS processed_keys (
            key TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            site_id TEXT,
            error TEXT,
            body_chars INTEGER NOT NULL DEFAULT 0,
            body_bytes INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_processed_status ON processed_keys(status)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS source_meta (
            name TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def read_resume_meta(conn: sqlite3.Connection) -> dict[str, str]:
    return {name: value for name, value in conn.execute("SELECT name, value FROM source_meta")}


def write_resume_meta(conn: sqlite3.Connection, output_path: Path, error_path: Path) -> None:
    output_sig = file_signature(output_path)
    error_sig = file_signature(error_path)
    rows = [
        ("version", "1"),
        ("output_size", str(output_sig["size"])),
        ("output_mtime_ns", str(output_sig["mtime_ns"])),
        ("error_size", str(error_sig["size"])),
        ("error_mtime_ns", str(error_sig["mtime_ns"])),
        ("updated_at", datetime.now(timezone.utc).isoformat()),
    ]
    conn.executemany(
        """
        INSERT INTO source_meta (name, value)
        VALUES (?, ?)
        ON CONFLICT(name) DO UPDATE SET value=excluded.value
        """,
        rows,
    )


def resume_index_matches(conn: sqlite3.Connection, output_path: Path, error_path: Path) -> bool:
    meta = read_resume_meta(conn)
    output_sig = file_signature(output_path)
    error_sig = file_signature(error_path)
    return (
        meta.get("version") == "1"
        and meta.get("output_size") == str(output_sig["size"])
        and meta.get("output_mtime_ns") == str(output_sig["mtime_ns"])
        and meta.get("error_size") == str(error_sig["size"])
        and meta.get("error_mtime_ns") == str(error_sig["mtime_ns"])
    )


def load_processed_rows_from_index(
    conn: sqlite3.Connection,
    allowed_keys: set[str] | None = None,
) -> tuple[set[str], int, int, int, int, Counter[str], Counter[str]]:
    processed: set[str] = set()
    success_count = 0
    failure_count = 0
    total_bytes = 0
    total_body_chars = 0
    error_counter: Counter[str] = Counter()
    site_error_counter: Counter[str] = Counter()
    query = "SELECT key, status, site_id, error, body_chars, body_bytes FROM processed_keys"
    for key, status, site_id, error, body_chars, body_bytes in conn.execute(query):
        if not key or (allowed_keys is not None and key not in allowed_keys):
            continue
        processed.add(str(key))
        if status == "success":
            success_count += 1
            total_body_chars += int(body_chars or 0)
            total_bytes += int(body_bytes or 0)
        else:
            failure_count += 1
            error_counter[str(error or "")] += 1
            site_error_counter[str(site_id or "")] += 1
    return processed, success_count, failure_count, total_bytes, total_body_chars, error_counter, site_error_counter


def rebuild_resume_index(
    conn: sqlite3.Connection,
    output_path: Path,
    error_path: Path,
    allowed_keys: set[str] | None = None,
) -> tuple[set[str], int, int, int, int, Counter[str], Counter[str]]:
    conn.execute("DELETE FROM processed_keys")
    conn.execute("DELETE FROM source_meta")
    result = load_processed_rows(output_path, error_path, allowed_keys, rebuild_index_conn=conn)
    write_resume_meta(conn, output_path, error_path)
    conn.commit()
    return result


def add_resume_index_row(
    conn: sqlite3.Connection | None,
    row: dict[str, Any],
    status: str,
    error_text: str = "",
    body_chars: int = 0,
    body_bytes: int = 0,
) -> None:
    if conn is None:
        return
    key = row_key(row)
    if not key:
        return
    conn.execute(
        """
        INSERT OR IGNORE INTO processed_keys
            (key, status, site_id, error, body_chars, body_bytes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            key,
            status,
            str(row.get("site_id") or ""),
            error_text,
            int(body_chars or 0),
            int(body_bytes or 0),
        ),
    )


def commit_resume_index(
    conn: sqlite3.Connection | None,
    output_path: Path,
    error_path: Path,
) -> None:
    if conn is None:
        return
    write_resume_meta(conn, output_path, error_path)
    conn.commit()


def load_previous_progress(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_stats_snapshot(path: Path, stats: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def filter_input_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, Counter[str]]:
    kept: list[dict[str, Any]] = []
    filtered = 0
    filtered_by_site: Counter[str] = Counter()
    for row in rows:
        site_id = str(row.get("site_id") or "")
        url = str(row.get("url") or "")
        if not url or is_blocked_article_url(site_id, url):
            filtered += 1
            filtered_by_site[site_id or "__missing_site__"] += 1
            continue
        kept.append(row)
    return kept, filtered, filtered_by_site


def install_signal_handlers() -> None:
    def _handle(_signum: int, _frame: Any) -> None:
        global STOP_REQUESTED
        STOP_REQUESTED = True

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)


def build_states(rows: list[dict[str, Any]], shuffle: bool, seed: int) -> list[DomainState]:
    if shuffle:
        rng = random.Random(seed)
        rows = rows[:]
        rng.shuffle(rows)
    by_domain: dict[str, DomainState] = {}
    for row in rows:
        domain = str(row.get("domain") or urlparse(str(row["url"])).netloc).lower()
        state = by_domain.setdefault(domain, DomainState(domain=domain))
        state.queue.append(row)
    return list(by_domain.values())


def load_proxy_states(path: Path | None) -> list[ProxyState]:
    if path is None:
        proxy = os.getenv("ALL_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or ""
        return [ProxyState(name="default", socks_url=proxy)]
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [
        ProxyState(
            name=row.get("name") or f"proxy_{idx}",
            socks_url=row["socks_url"],
            region=row.get("region", ""),
        )
        for idx, row in enumerate(rows)
        if row.get("socks_url")
    ]


def choose_proxy_for_domain(domain: str, proxies: list[ProxyState]) -> ProxyState:
    available = [proxy for proxy in proxies if proxy.cooldown_until <= time.monotonic()]
    pool = available or proxies
    ordered = sorted(
        pool,
        key=lambda proxy: (
            proxy.consecutive_failures,
            proxy.proxy_errors,
            proxy.failures,
            -proxy.successes,
            proxy.name,
        ),
    )
    idx = abs(hash(domain)) % len(ordered)
    return ordered[idx]


def rotate_proxy(current_name: str, proxies: list[ProxyState]) -> str:
    available = [proxy for proxy in proxies if proxy.cooldown_until <= time.monotonic()]
    pool = available or proxies
    ordered = sorted(
        pool,
        key=lambda proxy: (
            proxy.consecutive_failures,
            proxy.proxy_errors,
            proxy.failures,
            -proxy.successes,
            proxy.name,
        ),
    )
    for proxy in ordered:
        if proxy.name != current_name:
            return proxy.name
    return current_name


def is_proxy_error(error_text: str, status: Any) -> bool:
    if status is None:
        lowered = error_text.lower()
        proxy_markers = (
            "socks",
            "proxy",
            "connector",
            "connection reset",
            "server disconnected",
            "cannot connect",
            "timed out",
            "timeout",
            "ssl",
        )
        return any(marker in lowered for marker in proxy_markers)
    return False


def compute_proxy_cooldown(proxy: ProxyState, args: argparse.Namespace) -> float:
    multiplier = max(1, proxy.consecutive_failures - max(0, args.proxy_failure_threshold - 1))
    return min(args.proxy_max_cooldown_sec, args.proxy_base_cooldown_sec * multiplier)


async def fetch_one(
    session: ClientSession,
    row: dict[str, Any],
    timeout_sec: float,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any]]:
    from aiohttp import ClientTimeout

    url = str(row["url"])
    started = time.perf_counter()
    status_code: int | None = None
    body_bytes_len = 0
    try:
        timeout = ClientTimeout(total=timeout_sec)
        async with session.get(url, headers=HTTP_HEADERS, timeout=timeout, allow_redirects=True) as resp:
            status_code = resp.status
            body_bytes = await resp.read()
            body_bytes_len = len(body_bytes)
            text = body_bytes.decode("utf-8", errors="ignore")
            if resp.status >= 400:
                raise RuntimeError(f"http_{resp.status}")
            payload = await asyncio.to_thread(extract_article_payload, text, url)
            if not payload["title"] or not payload["body"]:
                raise RuntimeError("missing_core_fields")
            fetched_at = datetime.now(timezone.utc).isoformat()
            date_result = clean_published_at(
                {
                    **row,
                    "request_url": url,
                    "response_url": str(resp.url),
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
                    "response_url": str(resp.url),
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
                    "fetch_status": resp.status,
                    "fetched_at": fetched_at,
                    "extraction_method": payload["extraction_method"],
                }
            )
            metrics = {
                "ok": True,
                "elapsed_sec": time.perf_counter() - started,
                "download_bytes": body_bytes_len,
                "body_chars": len(payload["body"]),
                "fetch_status": resp.status,
                "proxy_name": str(row.get("_proxy_name", "")),
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
                "fetch_status": status_code,
            }
        )
        metrics = {
            "ok": False,
            "elapsed_sec": time.perf_counter() - started,
            "download_bytes": body_bytes_len,
            "body_chars": 0,
            "fetch_status": status_code,
            "proxy_name": str(row.get("_proxy_name", "")),
            "error": str(exc),
        }
        return None, error, metrics


def next_delay_sec(state: DomainState, base_delay_ms: int, jitter_ms: int) -> float:
    base = max(0.0, base_delay_ms / 1000)
    jitter = random.uniform(0, max(0, jitter_ms) / 1000)
    latency_component = 0.0
    if state.ewma_latency > 0:
        latency_component = state.ewma_latency / max(1, state.target_concurrency) * 0.15
    return base + jitter + latency_component + state.cooldown_sec


def adjust_state_on_result(
    state: DomainState,
    metrics: dict[str, Any],
    proxies_by_name: dict[str, ProxyState],
    args: argparse.Namespace,
    max_per_domain: int,
    min_per_domain: int,
    base_delay_ms: int,
    jitter_ms: int,
) -> None:
    state.in_flight = max(0, state.in_flight - 1)
    elapsed = float(metrics["elapsed_sec"])
    proxy = proxies_by_name.get(state.proxy_name)
    if state.ewma_latency <= 0:
        state.ewma_latency = elapsed
    else:
        state.ewma_latency = state.ewma_latency * 0.8 + elapsed * 0.2

    if metrics["ok"]:
        state.successes += 1
        state.success_streak += 1
        state.fail_streak = 0
        state.total_bytes += int(metrics["download_bytes"])
        if proxy is not None:
            proxy.successes += 1
            proxy.consecutive_failures = 0
            proxy.cooldown_until = 0.0
            proxy.last_error = ""
        state.cooldown_sec = max(0.0, state.cooldown_sec * 0.5)
        if state.success_streak >= max(2, state.target_concurrency * 2) and state.target_concurrency < max_per_domain:
            state.target_concurrency += 1
            state.success_streak = 0
        state.next_ready_at = time.monotonic() + next_delay_sec(state, base_delay_ms, jitter_ms)
        return

    state.failures += 1
    state.fail_streak += 1
    state.success_streak = 0
    status = metrics.get("fetch_status")
    error_text = str(metrics["error"])
    proxy_error = is_proxy_error(error_text, status)
    hard_backoff = status in RATE_LIMIT_STATUS or "timeout" in error_text.lower() or proxy_error
    if proxy is not None:
        proxy.failures += 1
        proxy.last_error = error_text[:300]
        proxy.last_error_at = datetime.now(timezone.utc).isoformat()
        if proxy_error:
            proxy.proxy_errors += 1
            proxy.consecutive_failures += 1
            if proxy.consecutive_failures >= max(1, args.proxy_failure_threshold):
                proxy.disabled_count += 1
                proxy.cooldown_until = time.monotonic() + compute_proxy_cooldown(proxy, args)
                state.proxy_name = rotate_proxy(state.proxy_name, list(proxies_by_name.values()))
        elif status in RATE_LIMIT_STATUS:
            proxy.rate_limit_errors += 1
            proxy.consecutive_failures = 0
        else:
            proxy.other_errors += 1
            proxy.consecutive_failures = 0
    if hard_backoff:
        state.target_concurrency = max(min_per_domain, max(1, state.target_concurrency // 2))
        state.cooldown_sec = min(60.0, max(1.0, state.cooldown_sec * 2 if state.cooldown_sec else 2.0))
    else:
        state.cooldown_sec = min(10.0, max(0.5, state.cooldown_sec))
    state.next_ready_at = time.monotonic() + next_delay_sec(state, base_delay_ms, jitter_ms)


async def run_scheduler(args: argparse.Namespace) -> dict[str, Any]:
    from aiohttp import ClientSession, ClientTimeout

    global STOP_REQUESTED
    STOP_REQUESTED = False
    original_rows = read_input_jsonl(args.input)
    if args.site_id:
        keep = set(args.site_id)
        original_rows = [row for row in original_rows if row.get("site_id") in keep]
    raw_input_rows = len(original_rows)
    original_rows, filtered_input_rows, filtered_input_sites = filter_input_rows(original_rows)
    if args.shuffle:
        rng = random.Random(args.seed)
        original_rows = original_rows[:]
        rng.shuffle(original_rows)
    if args.limit > 0:
        original_rows = original_rows[: args.limit]
    input_keys = {row_key(row) for row in original_rows if row_key(row)}

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.errors.parent.mkdir(parents=True, exist_ok=True)
    progress_path = args.progress_path or args.stats
    proxy_health_path = args.proxy_health_path or progress_path.with_name(progress_path.stem + "_proxy_health.json")
    resume_index_path = args.resume_index_path or default_resume_index_path(progress_path)

    processed_keys: set[str] = set()
    previous_successes = 0
    previous_failures = 0
    previous_download_bytes = 0
    previous_body_chars = 0
    error_counter: Counter[str] = Counter()
    site_error_counter: Counter[str] = Counter()
    previous_progress = load_previous_progress(progress_path if args.resume else None)
    previous_elapsed_sec = float(previous_progress.get("elapsed_sec") or 0.0)
    previous_created_at = str(previous_progress.get("created_at") or "")
    resume_index_conn: sqlite3.Connection | None = None
    resume_index_loaded = False
    resume_index_rebuilt = False
    resume_scan_sec = 0.0
    if args.resume:
        resume_index_conn = open_resume_index(resume_index_path)
        resume_t0 = time.perf_counter()
        if resume_index_matches(resume_index_conn, args.output, args.errors):
            print(f"[resume-index] loading existing index: {resume_index_path}", flush=True)
            (
                processed_keys,
                previous_successes,
                previous_failures,
                previous_download_bytes,
                previous_body_chars,
                error_counter,
                site_error_counter,
            ) = load_processed_rows_from_index(resume_index_conn, input_keys)
            resume_index_loaded = True
        else:
            print(f"[resume-index] rebuilding index: {resume_index_path}", flush=True)
            (
                processed_keys,
                previous_successes,
                previous_failures,
                previous_download_bytes,
                previous_body_chars,
                error_counter,
                site_error_counter,
            ) = rebuild_resume_index(resume_index_conn, args.output, args.errors, input_keys)
            resume_index_rebuilt = True
        resume_scan_sec = time.perf_counter() - resume_t0
        previous_avg_body_chars = float(previous_progress.get("avg_body_chars") or 0.0)
        estimated_body_chars = int(previous_avg_body_chars * previous_successes)
        if estimated_body_chars > previous_body_chars:
            previous_body_chars = estimated_body_chars
        previous_download_mib = float(previous_progress.get("download_mib") or 0.0)
        estimated_download_bytes = int(previous_download_mib * 1024 * 1024)
        if estimated_download_bytes > previous_download_bytes:
            previous_download_bytes = estimated_download_bytes
        print(
            "[resume-index] ready "
            f"loaded={resume_index_loaded} rebuilt={resume_index_rebuilt} "
            f"processed_keys={len(processed_keys)} successes={previous_successes} "
            f"failures={previous_failures} scan_sec={resume_scan_sec:.1f}",
            flush=True,
        )

    rows = [row for row in original_rows if row_key(row) not in processed_keys]
    states = build_states(rows, False, args.seed)
    proxies = load_proxy_states(args.proxy_pool)
    proxies_by_name = {proxy.name: proxy for proxy in proxies}
    for state in states:
        state.proxy_name = choose_proxy_for_domain(state.domain, proxies).name

    success_count = previous_successes
    failure_count = previous_failures
    download_bytes_total = previous_download_bytes
    body_chars_total = previous_body_chars
    processed_this_run = 0

    timeout = ClientTimeout(total=args.timeout)
    active: dict[asyncio.Task[Any], DomainState] = {}
    rr_index = 0
    t0 = time.perf_counter()
    last_progress_write = 0.0
    last_activity = time.monotonic()
    stop_reason = ""

    output_mode = "a" if args.resume else "w"
    error_mode = "a" if args.resume else "w"
    output_handle = args.output.open(output_mode, encoding="utf-8", buffering=1)
    error_handle = args.errors.open(error_mode, encoding="utf-8", buffering=1)

    def current_stats(running: bool) -> dict[str, Any]:
        elapsed = previous_elapsed_sec + (time.perf_counter() - t0)
        total_processed = success_count + failure_count
        total_rows = len(original_rows)
        remaining_rows = max(0, total_rows - total_processed)
        avg_body_chars = round(body_chars_total / success_count, 1) if success_count else 0.0
        return {
            "created_at": previous_created_at or datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "input": str(args.input),
            "input_rows_raw": raw_input_rows,
            "global_concurrency": args.global_concurrency,
            "max_per_domain": args.max_per_domain,
            "proxy_pool_size": len(proxies),
            "rows": total_rows,
            "rows_remaining": remaining_rows,
            "rows_filtered_pre_extract": filtered_input_rows,
            "rows_skipped_existing": len(processed_keys),
            "rows_this_run": len(rows),
            "resume_index_path": str(resume_index_path) if args.resume else "",
            "resume_index_loaded": resume_index_loaded,
            "resume_index_rebuilt": resume_index_rebuilt,
            "resume_scan_sec": round(resume_scan_sec, 3),
            "domains": len({str(row.get("domain") or urlparse(str(row["url"])).netloc).lower() for row in original_rows}),
            "elapsed_sec": round(elapsed, 3),
            "successes": success_count,
            "failures": failure_count,
            "processed": total_processed,
            "success_rate": round(success_count / total_processed, 4) if total_processed else 0.0,
            "completion_rate": round(total_processed / total_rows, 4) if total_rows else 1.0,
            "successes_per_sec": round(success_count / elapsed, 3) if elapsed else 0.0,
            "successes_per_min": round((success_count / elapsed) * 60, 1) if elapsed else 0.0,
            "download_mib": round(download_bytes_total / 1024 / 1024, 2),
            "avg_body_chars": avg_body_chars,
            "top_errors": error_counter.most_common(10),
            "top_error_sites": site_error_counter.most_common(10),
            "top_filtered_input_sites": filtered_input_sites.most_common(10),
            "running": running,
            "active_tasks": len(active),
            "stop_reason": stop_reason,
            "proxy_stats": [
                {
                    "name": proxy.name,
                    "region": proxy.region,
                    "successes": proxy.successes,
                    "failures": proxy.failures,
                    "consecutive_failures": proxy.consecutive_failures,
                    "proxy_errors": proxy.proxy_errors,
                    "rate_limit_errors": proxy.rate_limit_errors,
                    "other_errors": proxy.other_errors,
                    "cooldown_remaining_sec": round(max(0.0, proxy.cooldown_until - time.monotonic()), 1),
                    "disabled_count": proxy.disabled_count,
                    "last_error": proxy.last_error,
                    "last_error_at": proxy.last_error_at,
                }
                for proxy in proxies
            ],
        }

    def write_proxy_health_snapshot() -> None:
        payload = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "proxy_failure_threshold": args.proxy_failure_threshold,
            "proxy_base_cooldown_sec": args.proxy_base_cooldown_sec,
            "proxy_max_cooldown_sec": args.proxy_max_cooldown_sec,
            "proxies": [
                {
                    "name": proxy.name,
                    "region": proxy.region,
                    "socks_url": proxy.socks_url,
                    "successes": proxy.successes,
                    "failures": proxy.failures,
                    "consecutive_failures": proxy.consecutive_failures,
                    "proxy_errors": proxy.proxy_errors,
                    "rate_limit_errors": proxy.rate_limit_errors,
                    "other_errors": proxy.other_errors,
                    "cooldown_remaining_sec": round(max(0.0, proxy.cooldown_until - time.monotonic()), 1),
                    "disabled_count": proxy.disabled_count,
                    "last_error": proxy.last_error,
                    "last_error_at": proxy.last_error_at,
                }
                for proxy in sorted(
                    proxies,
                    key=lambda p: (
                        max(0.0, p.cooldown_until - time.monotonic()) > 0,
                        p.consecutive_failures,
                        p.proxy_errors,
                        p.failures,
                    ),
                    reverse=True,
                )
            ],
        }
        write_stats_snapshot(proxy_health_path, payload)

    def mark_remaining_failed(reason: str) -> None:
        nonlocal failure_count, processed_this_run
        remaining_rows: list[dict[str, Any]] = []
        for state in states:
            while state.queue:
                remaining_rows.append(state.queue.popleft())
        for row in remaining_rows:
            error = dict(row)
            error.update(
                {
                    "request_url": row_key(row),
                    "error": reason,
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                    "fetch_status": None,
                }
            )
            append_jsonl_row(error_handle, error)
            add_resume_index_row(resume_index_conn, error, "error", error_text=reason)
            failure_count += 1
            processed_this_run += 1
            error_counter[reason] += 1
            site_error_counter[str(error.get("site_id") or "")] += 1

    sessions: dict[str, ClientSession] = {}
    for proxy in proxies:
        connector = build_proxy_connector(proxy.socks_url)
        sessions[proxy.name] = ClientSession(connector=connector, timeout=timeout, trust_env=False)

    try:
        while True:
            now = time.monotonic()
            if args.max_runtime_sec > 0 and time.perf_counter() - t0 >= args.max_runtime_sec:
                STOP_REQUESTED = True
                stop_reason = "max_runtime_sec"
            dispatched = False
            if states and len(active) < args.global_concurrency:
                for offset in range(len(states)):
                    if STOP_REQUESTED:
                        break
                    idx = (rr_index + offset) % len(states)
                    state = states[idx]
                    if not state.queue:
                        continue
                    if state.in_flight >= state.target_concurrency:
                        continue
                    if now < state.next_ready_at:
                        continue
                    row = state.queue.popleft()
                    row["_proxy_name"] = state.proxy_name
                    state.in_flight += 1
                    session = sessions[state.proxy_name]
                    task = asyncio.create_task(fetch_one(session, row, args.timeout))
                    active[task] = state
                    rr_index = idx + 1
                    dispatched = True
                    last_activity = time.monotonic()
                    if len(active) >= args.global_concurrency:
                        break

            if active:
                done, _pending = await asyncio.wait(
                    active.keys(),
                    timeout=0.05 if dispatched else 0.5,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in done:
                    last_activity = time.monotonic()
                    state = active.pop(task)
                    article, error, metrics = await task
                    adjust_state_on_result(
                        state,
                        metrics,
                        proxies_by_name=proxies_by_name,
                        args=args,
                        max_per_domain=args.max_per_domain,
                        min_per_domain=args.min_per_domain,
                        base_delay_ms=args.base_delay_ms,
                        jitter_ms=args.jitter_ms,
                    )
                    if article is not None:
                        append_jsonl_row(output_handle, article)
                        output_handle.flush()
                        add_resume_index_row(
                            resume_index_conn,
                            article,
                            "success",
                            body_chars=int(metrics["body_chars"]),
                            body_bytes=int(metrics["download_bytes"]),
                        )
                        success_count += 1
                        processed_this_run += 1
                        download_bytes_total += int(metrics["download_bytes"])
                        body_chars_total += int(metrics["body_chars"])
                    if error is not None:
                        attempts = int(error.get("_attempt", 0))
                        if attempts < args.retry_limit and (
                            metrics.get("fetch_status") in RATE_LIMIT_STATUS
                            or is_proxy_error(str(metrics.get("error") or ""), metrics.get("fetch_status"))
                        ):
                            retry_row = dict(error)
                            retry_row["_attempt"] = attempts + 1
                            state.proxy_name = rotate_proxy(state.proxy_name, proxies)
                            state.queue.appendleft(retry_row)
                        else:
                            append_jsonl_row(error_handle, error)
                            error_handle.flush()
                            add_resume_index_row(
                                resume_index_conn,
                                error,
                                "error",
                                error_text=str(error["error"]),
                            )
                            failure_count += 1
                            processed_this_run += 1
                            error_counter[str(error["error"])] += 1
                            site_error_counter[str(error.get("site_id") or "")] += 1

                    if processed_this_run > 0 and processed_this_run % max(1, args.flush_every) == 0:
                        output_handle.flush()
                        error_handle.flush()
                        commit_resume_index(resume_index_conn, args.output, args.errors)
                    if time.monotonic() - last_progress_write >= max(1.0, args.progress_interval_sec):
                        output_handle.flush()
                        error_handle.flush()
                        commit_resume_index(resume_index_conn, args.output, args.errors)
                        write_stats_snapshot(progress_path, current_stats(running=True))
                        write_proxy_health_snapshot()
                        last_progress_write = time.monotonic()
            else:
                if all(not state.queue and state.in_flight == 0 for state in states):
                    break
                if args.max_idle_sec > 0 and time.monotonic() - last_activity >= args.max_idle_sec:
                    mark_remaining_failed("scheduler_idle_timeout")
                    break
                await asyncio.sleep(0.1)

            states = [state for state in states if state.queue or state.in_flight > 0]
            if STOP_REQUESTED and not active:
                break
            if not states and not active:
                break
    finally:
        output_handle.flush()
        error_handle.flush()
        commit_resume_index(resume_index_conn, args.output, args.errors)
        output_handle.close()
        error_handle.close()
        for session in sessions.values():
            await session.close()

    stats = current_stats(running=False)
    write_stats_snapshot(progress_path, stats)
    write_stats_snapshot(args.stats, stats)
    write_proxy_health_snapshot()
    if resume_index_conn is not None:
        resume_index_conn.close()
    return stats


def main() -> None:
    try:
        faulthandler.enable(all_threads=True)
    except Exception:
        pass
    install_signal_handlers()
    args = parse_args()
    stats = asyncio.run(run_scheduler(args))
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
