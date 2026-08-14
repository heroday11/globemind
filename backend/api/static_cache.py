"""Shared static-asset cache policy for app and production ASGI wrapper."""
from __future__ import annotations

import os
from pathlib import Path

STATIC_EXTS = {
    ".js", ".css", ".map", ".json",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
    ".woff", ".woff2", ".ttf", ".otf", ".wasm", ".hdr",
}

STATIC_PREFIXES = (
    "/assets/",
    "/imgs/",
    "/fin-terminal/assets/",
    "/amazing-globe/",
    "/datasets/",
)

IMMUTABLE_PREFIXES = ("/assets/", "/fin-terminal/assets/")
IMMUTABLE_EXTS = {".js", ".css", ".map", ".wasm"}
DATASET_PLAIN_TEXT_EXTS = {
    ".cjs",
    ".css",
    ".htm",
    ".html",
    ".js",
    ".mjs",
    ".shtml",
    ".svg",
    ".wasm",
    ".xht",
    ".xhtml",
    ".xml",
    ".xsl",
    ".xslt",
}
DATASET_ATTACHMENT_EXTS = DATASET_PLAIN_TEXT_EXTS | {
    ".mht",
    ".mhtml",
    ".pdf",
    ".swf",
}
PUBLIC_CANONICAL_ORIGIN = "https://globemind.top"
INDEXABLE_SPA_PATHS = frozenset(
    {
        "/",
        "/about-us",
        "/academic-data",
        "/corrections",
        "/data-service/data-search",
        "/data-service/ground-news",
        "/data-service/ground-news-desk",
        "/data-service/help-docs",
        "/data-service/story-graph",
        "/financial-terminal",
        "/methodology",
        "/privacy",
        "/security",
        "/sentiment-analysis",
        "/sources",
        "/status",
        "/terms",
    }
)


def resolve_path_under(root: str | Path, request_path: str) -> Path:
    """Resolve a URL path and reject NUL bytes or paths escaping the static root."""
    if "\x00" in request_path:
        raise ValueError("invalid static path")
    root_path = Path(root).resolve()
    candidate = (root_path / request_path.lstrip("/")).resolve()
    try:
        candidate.relative_to(root_path)
    except ValueError as exc:
        raise ValueError("static path escapes configured root") from exc
    return candidate


def is_static_asset_path(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    return ext in STATIC_EXTS and path.startswith(STATIC_PREFIXES)


def static_cache_headers(path: str) -> dict[str, str]:
    ext = os.path.splitext(path)[1].lower()
    if path.startswith(IMMUTABLE_PREFIXES) and ext in IMMUTABLE_EXTS:
        return {"Cache-Control": "public, max-age=31536000, immutable"}
    if path.startswith("/datasets/"):
        return {"Cache-Control": "public, max-age=3600, stale-while-revalidate=86400"}
    if ext == ".html":
        return {"Cache-Control": "no-cache, no-store, must-revalidate"}
    if ext in STATIC_EXTS:
        return {"Cache-Control": "public, max-age=604800, stale-while-revalidate=86400"}
    return {"Cache-Control": "public, max-age=3600"}


def dataset_static_headers(path: str) -> dict[str, str]:
    """Prevent bundled third-party dataset files from becoming same-origin applications."""
    headers = {
        **static_cache_headers(path),
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "Cross-Origin-Resource-Policy": "same-origin",
    }
    if Path(path).suffix.lower() in DATASET_ATTACHMENT_EXTS:
        headers["Content-Disposition"] = "attachment"
    return headers


def no_store_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }


def spa_indexing_headers(path: str) -> dict[str, str]:
    """Return fail-closed indexing headers for a concrete SPA route."""
    raw = str(path or "").split("?", 1)[0]
    if (
        not raw.startswith("/")
        or "//" in raw
        or "\\" in raw
        or any(ord(character) < 32 or ord(character) == 127 for character in raw)
    ):
        return {"X-Robots-Tag": "noindex, nofollow"}
    normalized = "/" + raw.lstrip("/")
    if normalized != "/":
        normalized = normalized.rstrip("/")
    if normalized in INDEXABLE_SPA_PATHS:
        return {
            "Link": f'<{PUBLIC_CANONICAL_ORIGIN}{normalized}>; rel="canonical"',
            "X-Robots-Tag": "index, follow",
        }
    return {"X-Robots-Tag": "noindex, nofollow"}
