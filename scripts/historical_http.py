#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from urllib.parse import urlparse


META_MARKER = b"\n__CURLMETA__\t"


@dataclass
class CurlResponse:
    status_code: int | None
    content_type: str
    final_url: str
    body_bytes: bytes
    body_text: str
    ok: bool
    error: str


def _proxy_args() -> list[str]:
    for key in ("ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY"):
        value = os.getenv(key, "").strip()
        if not value:
            continue
        parsed = urlparse(value)
        host = parsed.hostname
        port = parsed.port
        if not host or not port:
            continue
        if parsed.scheme.startswith("socks5"):
            return ["--socks5-hostname", f"{host}:{port}"]
        return ["-x", f"{parsed.scheme}://{host}:{port}"]
    return []


def curl_fetch(url: str, timeout: float, user_agent: str, accept: str) -> CurlResponse:
    cmd = [
        "curl",
        "--location",
        "--silent",
        "--show-error",
        "--compressed",
        "--max-time",
        str(int(timeout)),
        "-A",
        user_agent,
        "-H",
        f"Accept: {accept}",
        *_proxy_args(),
        "-w",
        r"\n__CURLMETA__\t%{http_code}\t%{content_type}\t%{url_effective}",
        url,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, check=False)
    except Exception as exc:
        return CurlResponse(
            status_code=None,
            content_type="",
            final_url=url,
            body_bytes=b"",
            body_text="",
            ok=False,
            error=str(exc),
        )

    stdout = proc.stdout
    marker_index = stdout.rfind(META_MARKER)
    meta_blob = b""
    if marker_index >= 0:
        body_bytes = stdout[:marker_index]
        meta_blob = stdout[marker_index + 1 :]
    else:
        body_bytes = stdout

    status_code: int | None = None
    content_type = ""
    final_url = url
    if meta_blob:
        meta_parts = meta_blob.decode("utf-8", errors="ignore").rstrip("\n").split("\t")
        if len(meta_parts) >= 4:
            try:
                status_code = int(meta_parts[1])
            except ValueError:
                status_code = None
            content_type = meta_parts[2]
            final_url = meta_parts[3]

    error = proc.stderr.decode("utf-8", errors="ignore").strip()
    return CurlResponse(
        status_code=status_code,
        content_type=content_type,
        final_url=final_url,
        body_bytes=body_bytes,
        body_text=body_bytes.decode("utf-8", errors="ignore"),
        ok=proc.returncode == 0 and status_code is not None and status_code < 400,
        error=error,
    )
