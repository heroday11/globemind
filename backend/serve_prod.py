"""
生产部署入口 — FastAPI 同时托管 API + 前端静态文件（SPA 一体部署）。
/llm/ 路径代理到本地 vLLM 服务（默认 :8004）。

用法:
    cd /root/data/globemind
    conda activate Globemind_env
    python backend/serve_prod.py

架构:  外层 ASGI 捕获非 /api 请求 → 静态文件 / SPA
        内层 FastAPI 处理 /api 请求
        /llm/ 前缀 → 透明代理到 vLLM OpenAI 兼容服务
"""
import json
import logging
import os
import posixpath
import re
import sys
import uuid
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote, unquote, urlsplit, urlunsplit

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_DIR = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
_cppt_dir = str(_REPO_ROOT / "backend" / "cppt")
if _cppt_dir not in sys.path:
    sys.path.insert(0, _cppt_dir)

import httpx  # noqa: E402
import uvicorn  # noqa: E402

from api.core.environment import string_setting  # noqa: E402
from api.core.runtime_security import is_production  # noqa: E402
from api.services.auth import get_active_user_from_access_token  # noqa: E402
from api.static_cache import (  # noqa: E402
    DATASET_PLAIN_TEXT_EXTS,
    STATIC_EXTS,
    dataset_static_headers,
    no_store_headers,
    resolve_path_under,
    spa_indexing_headers,
    static_cache_headers,
)

# 全局 httpx 客户端（长连接复用）
_vllm_client: Optional[httpx.AsyncClient] = None

VLLM_TARGET = os.getenv("VLLM_TARGET", "http://127.0.0.1:8004")
LLM_PREFIX = "/llm"
GENERATED_ASSET_ROOT = Path(
    string_setting("GLOBEMIND_GENERATED_ASSET_ROOT", "/root/data/web/generated-assets")
).resolve()

# --- Knowledge Graph proxy ---
KG_TARGET = os.getenv("KG_TARGET", "http://192.168.207.170:8088")
KG_PREFIX = "/kg-proxy"
_kg_client: Optional[httpx.AsyncClient] = None

# --- Nav injection for Paper / Bridge ---
NAV_INJECT_FILE = Path(__file__).resolve().parent / "nav_inject.html"
NAV_INJECT_HTML = NAV_INJECT_FILE.read_bytes() if NAV_INJECT_FILE.exists() else b""
if NAV_INJECT_HTML:
    print(f"[deploy] 导航栏注入就绪: {len(NAV_INJECT_HTML)} bytes")
else:
    print("[deploy] 警告: nav_inject.html 未找到，不注入导航栏")

PAPER_BRIDGE_TARGET = os.getenv("PAPER_BRIDGE_TARGET", "http://192.168.207.175:18080")
_backend_client: Optional[httpx.AsyncClient] = None

# Hunyuan3D is a locally hosted Gradio application exposed through the
# production tunnel under a dedicated path.
HUNYUAN3D_TARGET = os.getenv("HUNYUAN3D_TARGET", "http://127.0.0.1:7860")
HUNYUAN3D_PREFIX = "/hunyuan3d"
_hunyuan3d_client: Optional[httpx.AsyncClient] = None

# Pixal3D is a separate local GPU service, exposed only through the dev site.
PIXAL3D_TARGET = os.getenv("PIXAL3D_TARGET", "http://127.0.0.1:7861")
PIXAL3D_PREFIX = "/pixal3d"
_pixal3d_client: Optional[httpx.AsyncClient] = None

_PROXY_BODY_LIMIT = max(1024, int(os.getenv("HTTP_MAX_PROXY_REQUEST_BYTES", str(2 * 1024 * 1024))))
_PIXAL3D_PROXY_BODY_LIMIT = max(1024, int(os.getenv("PIXAL3D_MAX_UPLOAD_BYTES", str(32 * 1024 * 1024))))
_PROXY_HTML_LIMIT = max(1, int(os.getenv("HTTP_MAX_PROXY_HTML_BYTES", str(4 * 1024 * 1024))))
_SENSITIVE_PROXY_HEADERS = {
    "authorization",
    "cf-connecting-ip",
    "cookie",
    "cf-access-jwt-assertion",
    "connection",
    "forwarded",
    "host",
    "keep-alive",
    "content-length",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailer",
    "transfer-encoding",
    "true-client-ip",
    "upgrade",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-real-ip",
}
_SAFE_PROXY_RESPONSE_HEADERS = {
    "accept-ranges",
    "cache-control",
    "content-disposition",
    "content-encoding",
    "content-language",
    "content-length",
    "content-range",
    "content-type",
    "etag",
    "expires",
    "last-modified",
    "retry-after",
    "vary",
}
_HTML_INVALIDATED_HEADERS = {
    "accept-ranges",
    "cache-control",
    "content-encoding",
    "content-length",
    "content-range",
    "etag",
    "expires",
    "last-modified",
}
_PROXY_STATIC_DIRECTORIES = {
    "assets",
    "build",
    "css",
    "dist",
    "fonts",
    "images",
    "img",
    "js",
    "public",
    "static",
}
_PROXY_PUBLIC_FILES = {"favicon.ico", "manifest.json", "robots.txt", "site.webmanifest"}
_PROXY_ALLOWED_METHODS = {"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"}
_proxy_logger = logging.getLogger("globemind.proxy")

PAPER_PATH = "/data-service/paper"
BRIDGE_PATH = "/data-service/bridge"

_FRONTEND_READ_METHODS = frozenset({"GET", "HEAD"})
_SPA_FALLBACK_ROOTS = frozenset(
    {
        "about-us",
        "academic-data",
        "amazing-globe",
        "country-profiles",
        "data-assistant",
        "data-service",
        "data-statistics",
        "entity-governance",
        "financial-terminal",
        "forgot-password",
        "login",
        "methodology",
        "model-assurance",
        "corrections",
        "privacy",
        "register",
        "research-workspace",
        "reset-password",
        "security",
        "sentiment-analysis",
        "sources",
        "status",
        "terms",
        "user-center",
    }
)
_FORBIDDEN_PUBLIC_SUFFIXES = frozenset(
    {
        ".asp",
        ".aspx",
        ".bak",
        ".cgi",
        ".conf",
        ".crt",
        ".env",
        ".ini",
        ".jsp",
        ".key",
        ".log",
        ".old",
        ".pem",
        ".php",
        ".pl",
        ".py",
        ".rb",
        ".sh",
        ".sql",
        ".toml",
        ".yaml",
        ".yml",
    }
)
_FORBIDDEN_PUBLIC_NAMES = frozenset(
    {
        "credentials.json",
        "docker-compose.yaml",
        "docker-compose.yml",
        "key.json",
        "secrets.json",
        "service-account.json",
        "serviceaccountkey.json",
        "terraform.tfstate",
    }
)

PAPER_API_FIX = b"""<script>
(function(){var p=location.pathname;if(p==='/data-service/paper'||p.startsWith('/data-service/paper/')){window.paperApiUrl=function(path){return '/data-service/paper'+path};}})();
</script>"""

BRIDGE_API_FIX = b"""<script>
(function(){
var p=location.pathname;if(p==='/data-service/bridge'||p.startsWith('/data-service/bridge/')){
var _orig=fetch;window.fetch=function(input,init){
if(typeof input==='string'&&!input.startsWith('http')&&input.startsWith('/api')){
return _orig.call(this,'/data-service/bridge'+input,init);
}
return _orig.call(this,input,init);};}})();
</script>"""


class _ProxyRequestError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class _ProxyResponseTooLarge(Exception):
    pass


def _scope_active_user(scope) -> Optional[dict]:
    for key, value in scope.get("headers", []):
        if key.lower() != b"authorization":
            continue
        scheme, separator, token = value.decode("latin-1").partition(" ")
        if separator and scheme.lower() == "bearer":
            return get_active_user_from_access_token(token.strip())
    return None


def _contains_api_path_segment(path: str) -> bool:
    return any(segment == "api" for segment in path.split("/"))


def _is_public_proxy_read(path: str, public_prefix: str) -> bool:
    if not _path_is_under(path, public_prefix):
        return False
    suffix = path[len(public_prefix):]
    if suffix in {"", "/"}:
        return True
    if _contains_api_path_segment(suffix):
        return False
    relative = suffix.lstrip("/")
    segments = relative.split("/")
    extension = os.path.splitext(relative)[1].lower()
    if extension not in STATIC_EXTS:
        return False
    return (
        relative in _PROXY_PUBLIC_FILES
        or segments[0] in _PROXY_STATIC_DIRECTORIES
        or segments[:2] == ["_next", "static"]
    )


async def _proxy_access_error(scope, send, public_prefix: str) -> bool:
    method = str(scope.get("method") or "GET").upper()
    path = str(scope.get("path") or "")
    if method not in _PROXY_ALLOWED_METHODS:
        body = json.dumps({"detail": "Method not allowed for internal proxy"}).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 405,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"allow", b"GET, HEAD, OPTIONS, POST, PUT, PATCH, DELETE"),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})
        return True
    requires_active_user = method not in {"GET", "HEAD", "OPTIONS"} or (
        method in {"GET", "HEAD"}
        and (
            _contains_api_path_segment(path)
            or not _is_public_proxy_read(path, public_prefix)
        )
    )
    if not requires_active_user:
        return False
    user = _scope_active_user(scope)
    if user is None:
        status, detail = 401, "Active authentication required for internal proxy access"
    elif method in {"PUT", "PATCH", "DELETE"} and user.get("role") != "admin":
        status, detail = 403, "Administrator permission required for proxy mutations"
    else:
        return False
    body = json.dumps({"detail": detail}).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})
    return True


async def _read_proxy_body(receive, *, body_limit: int = _PROXY_BODY_LIMIT) -> bytes:
    body = bytearray()
    while True:
        msg = await receive()
        if not isinstance(msg, dict):
            raise _ProxyRequestError(400, "Invalid proxy request event")
        message_type = msg.get("type")
        if message_type == "http.disconnect":
            raise _ProxyRequestError(499, "Client disconnected during proxy request")
        if message_type != "http.request":
            raise _ProxyRequestError(400, "Invalid proxy request event")
        chunk = msg.get("body", b"")
        if not isinstance(chunk, bytes):
            raise _ProxyRequestError(400, "Invalid proxy request body")
        if len(body) + len(chunk) > body_limit:
            raise _ProxyRequestError(413, "Proxy request body too large")
        body.extend(chunk)
        if not msg.get("more_body", False):
            return bytes(body)


def _proxy_request_headers(scope) -> dict[str, str]:
    connection_headers: set[str] = set()
    for key_bytes, value_bytes in scope.get("headers", []):
        if key_bytes.lower() == b"connection":
            connection_headers.update(
                token.strip().lower()
                for token in value_bytes.decode("latin-1").split(",")
                if token.strip()
            )
    headers: dict[str, str] = {}
    for key_bytes, value_bytes in scope.get("headers", []):
        key = key_bytes.decode("latin-1").lower()
        if key not in _SENSITIVE_PROXY_HEADERS and key not in connection_headers:
            headers[key] = value_bytes.decode("latin-1")
    return headers


def _proxy_target_url(target_path: str, scope) -> httpx.URL:
    query_string = scope.get("query_string", b"")
    if not isinstance(query_string, bytes):
        raise _ProxyRequestError(400, "Invalid proxy query string")
    target = httpx.URL(target_path)
    return target.copy_with(query=query_string) if query_string else target


def _path_is_under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def _is_forbidden_public_path(path: str) -> bool:
    segments = tuple(segment for segment in path.split("/") if segment)
    if any(segment.startswith(".") for segment in segments):
        return True
    name = segments[-1].lower() if segments else ""
    return name in _FORBIDDEN_PUBLIC_NAMES or Path(name).suffix in _FORBIDDEN_PUBLIC_SUFFIXES


def _is_spa_navigation_path(path: str) -> bool:
    if path == "/":
        return True
    if _is_forbidden_public_path(path):
        return False
    root = path.lstrip("/").split("/", 1)[0]
    return root in _SPA_FALLBACK_ROOTS


def _rewrite_proxy_location(
    location: str,
    *,
    scope_path: str,
    public_prefix: str,
    upstream_prefix: str,
) -> Optional[str]:
    if not location or "\\" in location or any(ord(char) < 32 or ord(char) == 127 for char in location):
        return None
    try:
        parsed = urlsplit(location)
        decoded_path = unquote(parsed.path)
    except (UnicodeError, ValueError):
        return None
    if parsed.scheme or parsed.netloc or location.startswith("//"):
        return None
    if "\\" in decoded_path or any(ord(char) < 32 or ord(char) == 127 for char in decoded_path):
        return None

    if decoded_path.startswith("/"):
        if _path_is_under(decoded_path, public_prefix):
            rewritten_path = posixpath.normpath(decoded_path)
        else:
            suffix = decoded_path
            if upstream_prefix and _path_is_under(decoded_path, upstream_prefix):
                suffix = decoded_path[len(upstream_prefix):] or "/"
            normalized_suffix = posixpath.normpath("/" + suffix.lstrip("/"))
            rewritten_path = public_prefix if normalized_suffix == "/" else public_prefix + normalized_suffix
    else:
        safe_scope_path = scope_path if _path_is_under(scope_path, public_prefix) else public_prefix
        if not decoded_path:
            rewritten_path = safe_scope_path
        else:
            base_dir = public_prefix if safe_scope_path.rstrip("/") == public_prefix else posixpath.dirname(safe_scope_path)
            rewritten_path = posixpath.normpath(posixpath.join(base_dir, decoded_path))
        if not _path_is_under(rewritten_path, public_prefix):
            return None

    if not _path_is_under(rewritten_path, public_prefix):
        return None
    encoded_path = quote(rewritten_path, safe="/:@-._~!$&'()*+,;=")
    return urlunsplit(("", "", encoded_path, parsed.query, parsed.fragment))


def _safe_proxy_response_headers(
    response_headers: httpx.Headers,
    *,
    request_id: str,
    scope_path: str,
    public_prefix: str,
    upstream_prefix: str,
    html_length: Optional[int] = None,
) -> list[tuple[bytes, bytes]]:
    headers: list[tuple[bytes, bytes]] = []
    is_html = html_length is not None
    for name, value in response_headers.multi_items():
        lower_name = name.lower()
        if lower_name == "location":
            rewritten = _rewrite_proxy_location(
                value,
                scope_path=scope_path,
                public_prefix=public_prefix,
                upstream_prefix=upstream_prefix,
            )
            if rewritten is not None:
                headers.append((b"location", rewritten.encode("latin-1")))
            continue
        if lower_name not in _SAFE_PROXY_RESPONSE_HEADERS:
            continue
        if is_html and lower_name in _HTML_INVALIDATED_HEADERS:
            continue
        headers.append((lower_name.encode("ascii"), value.encode("latin-1")))

    headers.append((b"x-request-id", request_id.encode("ascii")))
    if is_html:
        headers.append((b"content-length", str(html_length).encode("ascii")))
        headers.append((b"cache-control", b"no-cache, no-store, must-revalidate"))
    return headers


async def _send_proxy_json(send, status_code: int, payload: dict[str, str], request_id: str) -> None:
    body = json.dumps(payload).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store"),
                (b"x-request-id", request_id.encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})


async def _proxy_internal_service(
    scope,
    receive,
    send,
    *,
    service: str,
    client: httpx.AsyncClient,
    target_path: str,
    public_prefix: str,
    upstream_prefix: str,
    html_transform: Callable[[bytes], bytes],
    body_limit: int = _PROXY_BODY_LIMIT,
) -> None:
    request_id = uuid.uuid4().hex
    method = str(scope.get("method") or "GET").upper()
    try:
        body = await _read_proxy_body(receive, body_limit=body_limit) if method in {"POST", "PUT", "PATCH", "DELETE"} else b""
        target_url = _proxy_target_url(target_path, scope)
    except _ProxyRequestError as exc:
        await _send_proxy_json(
            send,
            exc.status_code,
            {"detail": exc.detail, "request_id": request_id},
            request_id,
        )
        return

    response: Optional[httpx.Response] = None
    response_started = False
    try:
        request = client.build_request(
            method,
            target_url,
            headers=_proxy_request_headers(scope),
            content=body,
        )
        response = await client.send(request, stream=True)
        content_type = response.headers.get("content-type", "").lower()

        if "text/html" in content_type:
            buffered = bytearray()
            async for chunk in response.aiter_bytes():
                if len(buffered) + len(chunk) > _PROXY_HTML_LIMIT:
                    raise _ProxyResponseTooLarge("upstream HTML response exceeds proxy limit")
                buffered.extend(chunk)
            transformed = html_transform(bytes(buffered))
            if len(transformed) > _PROXY_HTML_LIMIT:
                raise _ProxyResponseTooLarge("transformed HTML response exceeds proxy limit")
            response_headers = _safe_proxy_response_headers(
                response.headers,
                request_id=request_id,
                scope_path=scope.get("path", public_prefix),
                public_prefix=public_prefix,
                upstream_prefix=upstream_prefix,
                html_length=len(transformed),
            )
            response_started = True
            await send(
                {
                    "type": "http.response.start",
                    "status": response.status_code,
                    "headers": response_headers,
                }
            )
            await send({"type": "http.response.body", "body": transformed, "more_body": False})
            return

        response_headers = _safe_proxy_response_headers(
            response.headers,
            request_id=request_id,
            scope_path=scope.get("path", public_prefix),
            public_prefix=public_prefix,
            upstream_prefix=upstream_prefix,
        )
        response_started = True
        await send(
            {
                "type": "http.response.start",
                "status": response.status_code,
                "headers": response_headers,
            }
        )
        async for chunk in response.aiter_raw():
            await send({"type": "http.response.body", "body": chunk, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})
    except Exception:
        _proxy_logger.exception("%s upstream proxy failure request_id=%s", service, request_id)
        if response_started:
            try:
                await send({"type": "http.response.body", "body": b"", "more_body": False})
            except Exception:
                _proxy_logger.exception("%s proxy response finalization failure request_id=%s", service, request_id)
        else:
            await _send_proxy_json(
                send,
                502,
                {"error": f"{service} proxy error", "request_id": request_id},
                request_id,
            )
    finally:
        if response is not None:
            try:
                await response.aclose()
            except Exception:
                _proxy_logger.exception("%s upstream response close failure request_id=%s", service, request_id)


def _get_backend_client() -> httpx.AsyncClient:
    global _backend_client
    if _backend_client is None:
        _backend_client = httpx.AsyncClient(
            base_url=PAPER_BRIDGE_TARGET,
            timeout=httpx.Timeout(60.0, connect=8.0),
            trust_env=False,
        )
    return _backend_client


def _get_hunyuan3d_client() -> httpx.AsyncClient:
    global _hunyuan3d_client
    if _hunyuan3d_client is None:
        _hunyuan3d_client = httpx.AsyncClient(
            base_url=HUNYUAN3D_TARGET,
            timeout=httpx.Timeout(900.0, connect=8.0),
            trust_env=False,
        )
    return _hunyuan3d_client


def _get_pixal3d_client() -> httpx.AsyncClient:
    global _pixal3d_client
    if _pixal3d_client is None:
        _pixal3d_client = httpx.AsyncClient(
            base_url=PIXAL3D_TARGET,
            timeout=httpx.Timeout(900.0, connect=8.0),
            trust_env=False,
        )
    return _pixal3d_client


async def _proxy_to_hunyuan3d(scope, receive, send):
    """Proxy the public /hunyuan3d path to the local Gradio application."""
    path = scope["path"]
    target_path = path[len(HUNYUAN3D_PREFIX):] or "/"
    if not target_path.startswith("/"):
        target_path = "/" + target_path
    await _proxy_internal_service(
        scope,
        receive,
        send,
        service="Hunyuan3D",
        client=_get_hunyuan3d_client(),
        target_path=target_path,
        public_prefix=HUNYUAN3D_PREFIX,
        upstream_prefix="",
        html_transform=lambda html: html,
    )


async def _proxy_to_pixal3d(scope, receive, send):
    """Proxy the public /pixal3d path to the local Pixal3D application."""
    path = scope["path"]
    target_path = path[len(PIXAL3D_PREFIX):] or "/"
    if not target_path.startswith("/"):
        target_path = "/" + target_path
    await _proxy_internal_service(
        scope,
        receive,
        send,
        service="Pixal3D",
        client=_get_pixal3d_client(),
        target_path=target_path,
        public_prefix=PIXAL3D_PREFIX,
        upstream_prefix="",
        html_transform=lambda html: html,
        body_limit=_PIXAL3D_PROXY_BODY_LIMIT,
    )


def _inject_nav_and_fix(html: bytes, service: str) -> bytes:
    """Inject nav bar + fix internal paths + add API fix."""
    html = html.replace(b'href="/paper/', b'href="/data-service/paper/')
    html = html.replace(b'href="/bridge/', b'href="/data-service/bridge/')
    if NAV_INJECT_HTML:
        _idx = html.find(b'<body')
        if _idx >= 0:
            _end = html.find(b'>', _idx)
            if _end >= 0:
                html = html[:_end+1] + NAV_INJECT_HTML + html[_end+1:]
    if service == "paper":
        html = html.replace(b"</body>", PAPER_API_FIX + b"</body>")
    elif service == "bridge":
        html = html.replace(b"</body>", BRIDGE_API_FIX + b"</body>")
    return html


async def _proxy_paper_bridge(scope, receive, send, service: str):
    """Reverse-proxy to Paper/Bridge backend with HTML injection."""
    prefix = f"/data-service/{service}"
    if await _proxy_access_error(scope, send, prefix):
        return
    target_path = scope["path"][len(prefix):] or "/"
    if not target_path.startswith("/"):
        target_path = "/" + target_path
    if service in ("paper", "bridge"):
        target_path = f"/{service}" + target_path
    await _proxy_internal_service(
        scope,
        receive,
        send,
        service=service,
        client=_get_backend_client(),
        target_path=target_path,
        public_prefix=prefix,
        upstream_prefix=f"/{service}",
        html_transform=lambda html: _inject_nav_and_fix(html, service),
    )


def _get_vllm_client() -> httpx.AsyncClient:
    global _vllm_client
    if _vllm_client is None:
        _vllm_client = httpx.AsyncClient(
            base_url=VLLM_TARGET,
            timeout=httpx.Timeout(180.0, connect=8.0),
            trust_env=False,
        )
    return _vllm_client


def _get_kg_client() -> httpx.AsyncClient:
    global _kg_client
    if _kg_client is None:
        _kg_client = httpx.AsyncClient(
            base_url=KG_TARGET,
            timeout=httpx.Timeout(60.0, connect=8.0),
            trust_env=False,
        )
    return _kg_client


async def _proxy_to_kg(scope, receive, send):
    """将 /kg-proxy/... 请求透明代理到知识图谱后端。"""
    if await _proxy_access_error(scope, send, KG_PREFIX):
        return
    path = scope["path"]
    target_path = path[len(KG_PREFIX):] if path.startswith(KG_PREFIX) else path
    if not target_path.startswith("/"):
        target_path = "/" + target_path

    def rewrite_kg_html(html: bytes) -> bytes:
        html = html.replace(b'src="/assets/', b'src="/kg-proxy/assets/')
        html = html.replace(b'href="/assets/', b'href="/kg-proxy/assets/')
        return html.replace(b'href="/favicon.ico"', b'href="/kg-proxy/favicon.ico"')

    await _proxy_internal_service(
        scope,
        receive,
        send,
        service="KG",
        client=_get_kg_client(),
        target_path=target_path,
        public_prefix=KG_PREFIX,
        upstream_prefix="",
        html_transform=rewrite_kg_html,
    )


async def _proxy_to_vllm(scope, receive, send):
    """将 /llm/... 请求透明代理到 vLLM 后端（去掉 /llm 前缀）。"""
    path = scope["path"]
    # /llm/v1/chat/completions → /v1/chat/completions
    target_path = path[len(LLM_PREFIX):] if path.startswith(LLM_PREFIX) else path
    method = scope.get("method", "GET")

    # 提取并清洗请求头
    headers = {}
    for k, v in scope.get("headers", []):
        key = k.decode("latin-1").lower()
        if key not in ("host", "content-length", "transfer-encoding"):
            headers[key] = v.decode("latin-1")

    # 读取完整请求体
    body = b""
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        more = True
        while more:
            msg = await receive()
            if msg["type"] == "http.request":
                chunk = msg.get("body", b"")
                if chunk:
                    body += chunk
                more = msg.get("more_body", False)

    client = _get_vllm_client()
    try:
        resp = await client.request(method, target_path, headers=headers, content=body)

        # 透传响应头（排除 hop-by-hop 头）
        resp_headers = []
        hop_by_hop = {"transfer-encoding", "content-encoding", "content-length", "connection"}
        for k, v in resp.headers.items():
            if k.lower() not in hop_by_hop:
                resp_headers.append((k.encode("latin-1"), v.encode("latin-1")))

        await send({
            "type": "http.response.start",
            "status": resp.status_code,
            "headers": resp_headers,
        })
        async for chunk in resp.aiter_bytes():
            await send({
                "type": "http.response.body",
                "body": chunk,
                "more_body": True,
            })
        await send({
            "type": "http.response.body",
            "body": b"",
            "more_body": False,
        })
    except Exception as e:
        err_body = json.dumps({"error": "vLLM proxy error", "detail": str(e)}).encode()
        await send({
            "type": "http.response.start",
            "status": 502,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({
            "type": "http.response.body",
            "body": err_body,
            "more_body": False,
        })


def make_app():
    """构建 ASGI 应用：前端静态文件 / SPA + FastAPI（/api）+ vLLM 代理（/llm）。"""
    from api.application import app as api_app

    frontend_env = os.getenv("FRONTEND_DIST")
    if frontend_env:
        FRONTEND_DIST = Path(frontend_env)
    else:
        FRONTEND_DIST = _REPO_ROOT / "frontend" / "vue_project" / "dist"
        if not (FRONTEND_DIST / "index.html").is_file():
            FRONTEND_DIST = _REPO_ROOT / "frontend" / "vue_project" / "dist" / "v2"
            if not (FRONTEND_DIST / "index.html").is_file():
                FRONTEND_DIST = _REPO_ROOT / "frontend" / "vue_project" / "dist"
    index_html = FRONTEND_DIST / "index.html"
    HAS_FRONTEND = FRONTEND_DIST.is_dir() and index_html.is_file()

    if not HAS_FRONTEND:
        print("[deploy] 警告: 前端构建产物不存在，仅 API 可用")
        return api_app
    try:
        GENERATED_ASSET_ROOT.relative_to(FRONTEND_DIST.resolve())
    except ValueError:
        pass
    else:
        raise RuntimeError("generated asset root must be outside the immutable frontend release")

    print(f"[deploy] 前端静态文件目录: {FRONTEND_DIST}")
    print("[deploy] 一体部署就绪：API → 静态文件 → SPA fallback")
    print(f"[deploy] vLLM 代理：{LLM_PREFIX}/... → {VLLM_TARGET}/...")

    dist_str = str(FRONTEND_DIST)
    # 缓存 index.html，但在前端重新构建后按 mtime/size 自动刷新。
    _index_sig = None
    _index_html_bust = b""
    _asset_url_re = re.compile(rb'(src|href)=(["\'])(/[^"\']*?\.(?:js|css))(["\'"])')
    _retired_frontend_routes = {
        "/data-service/alert-center": "/financial-terminal",
        "/data-service/open-computing": "/data-service/data-search",
        "/data-service/algorithm-analysis": "/data-service/data-search",
    }

    def _index_asset_exists(asset_path: bytes) -> bool:
        path_text = asset_path.decode("utf-8", errors="ignore").split("?", 1)[0]
        if path_text.startswith("/v2/assets/"):
            candidates = [
                FRONTEND_DIST / path_text.lstrip("/"),
                FRONTEND_DIST / "assets" / path_text.rsplit("/", 1)[-1],
                _REPO_ROOT / "frontend" / "vue_project" / "dist" / path_text.lstrip("/"),
            ]
        elif path_text.startswith("/assets/"):
            asset_name = path_text[len("/assets/"):]
            candidates = [
                FRONTEND_DIST / "assets" / asset_name,
                _REPO_ROOT / "frontend" / "vue_project" / "dist" / "assets" / asset_name,
            ]
        else:
            candidates = [FRONTEND_DIST / path_text.lstrip("/")]
        return any(path.is_file() for path in candidates)

    def _load_index_html_bust() -> bytes:
        nonlocal _index_sig, _index_html_bust
        try:
            stat = index_html.stat()
        except OSError:
            return _index_html_bust

        sig = (stat.st_mtime_ns, stat.st_size)
        if sig == _index_sig:
            return _index_html_bust

        raw = index_html.read_bytes()
        missing_assets = [
            m.group(3).decode("utf-8", errors="ignore")
            for m in _asset_url_re.finditer(raw)
            if not _index_asset_exists(m.group(3))
        ]
        if missing_assets and _index_html_bust:
            print(
                "[deploy] index.html references missing assets; keeping previous SPA entry: "
                + ", ".join(missing_assets[:5]),
                flush=True,
            )
            return _index_html_bust

        version = str(stat.st_mtime_ns).encode()
        _index_html_bust = _asset_url_re.sub(
            lambda m: m.group(1) + b"=" + m.group(2) + m.group(3) + b"?v=" + version + m.group(4),
            raw,
        )
        _index_sig = sig
        return _index_html_bust

    _load_index_html_bust()

    async def app(scope, receive, send):
        """顶层 ASGI 应用"""
        if scope["type"] == "lifespan":
            await api_app(scope, receive, send)
            return

        raw_send = send

        async def send_with_security_headers(message):
            if message.get("type") == "http.response.start":
                managed = {
                    b"x-content-type-options",
                    b"x-frame-options",
                    b"referrer-policy",
                    b"permissions-policy",
                    b"strict-transport-security",
                }
                headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() not in managed
                ]
                headers.extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"SAMEORIGIN"),
                        (b"referrer-policy", b"strict-origin-when-cross-origin"),
                        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                    ]
                )
                if is_production():
                    headers.append(
                        (b"strict-transport-security", b"max-age=31536000; includeSubDomains")
                    )
                message = {**message, "headers": headers}
            await raw_send(message)

        send = send_with_security_headers

        path = scope["path"]

        method = str(scope.get("method") or "GET").upper()

        if path in _retired_frontend_routes and method in _FRONTEND_READ_METHODS:
            from starlette.responses import RedirectResponse
            await RedirectResponse(_retired_frontend_routes[path], status_code=308)(scope, receive, send)
            return
        if path.startswith("/data-service/model-test/") and method in _FRONTEND_READ_METHODS:
            from starlette.responses import RedirectResponse
            await RedirectResponse("/data-service/data-search", status_code=308)(scope, receive, send)
            return

        # Paper / Bridge 反向代理 + 导航栏注入
        if _path_is_under(path, PAPER_PATH):
            await _proxy_paper_bridge(scope, receive, send, "paper")
            return
        if _path_is_under(path, BRIDGE_PATH):
            await _proxy_paper_bridge(scope, receive, send, "bridge")
            return

        if _path_is_under(path, HUNYUAN3D_PREFIX):
            await _proxy_to_hunyuan3d(scope, receive, send)
            return

        if _path_is_under(path, PIXAL3D_PREFIX):
            await _proxy_to_pixal3d(scope, receive, send)
            return

        # 跳过 /data-service 前缀（Cloudflare Tunnel 透传完整路径，如 globemind.top/data-service/docs/）
        if path.startswith("/data-service"):
            sub_path = path[len("/data-service"):] or "/"
            if sub_path.startswith("/api") or sub_path.startswith("/cc") or sub_path.rstrip("/") in ("/openapi.json", "/docs", "/redoc"):
                scope["path"] = sub_path
                await api_app(scope, receive, send)
                return
            # data-service 下非 API/doc/cc 路径 → 继续走静态文件/SPA

        # /cc 路径 → FastAPI（CC 路由 /cc/health, /cc/chat, …）
        if path.startswith("/cc"):
            await api_app(scope, receive, send)
            return

        # API 及文档路径 → 交给 FastAPI
        if path.startswith("/api") or path.rstrip("/") in ("/openapi.json", "/docs", "/redoc"):
            await api_app(scope, receive, send)
            return

        # /llm 路径 → FastAPI 的受保护 vLLM 代理（统一鉴权与请求头清洗）
        if path.startswith(LLM_PREFIX):
            await api_app(scope, receive, send)
            return

        # RFC 9116 is the only reviewed public dot-path exception. It is
        # generated by the API instead of being served from the static tree,
        # so the general dotfile deny rule below remains intact.
        if path == "/.well-known/security.txt":
            await api_app(scope, receive, send)
            return

        # /kg-proxy 路径 → 代理到知识图谱
        if _path_is_under(path, KG_PREFIX):
            await _proxy_to_kg(scope, receive, send)
            return

        # Static content and SPA navigation are read-only. Unknown writes must
        # not look successful merely because the frontend index exists.
        if method not in _FRONTEND_READ_METHODS:
            from starlette.responses import Response

            await Response(
                content=b"Method not allowed for frontend content",
                status_code=405,
                media_type="text/plain",
                headers={**no_store_headers(), "Allow": "GET, HEAD"},
            )(scope, receive, send)
            return

        # Defense in depth: never serve dotfiles or common secret/config
        # filenames even if they are accidentally copied into the web root.
        if _is_forbidden_public_path(path):
            from starlette.responses import Response

            await Response(status_code=404, headers=no_store_headers())(scope, receive, send)
            return

        # 非 /api /llm 路径 → 先尝试返回静态文件
        if path.startswith("/imgs/hermes-generated/"):
            try:
                generated_path = resolve_path_under(GENERATED_ASSET_ROOT, path)
            except ValueError:
                from starlette.responses import Response
                await Response(status_code=404, headers=no_store_headers())(scope, receive, send)
                return
            if generated_path.is_file():
                from starlette.responses import FileResponse
                await FileResponse(generated_path, headers=static_cache_headers(path))(
                    scope, receive, send
                )
                return
            from starlette.responses import Response
            await Response(status_code=404, headers=no_store_headers())(scope, receive, send)
            return

        try:
            file_path = resolve_path_under(dist_str, path)
        except ValueError:
            from starlette.responses import Response
            await Response(status_code=404, headers=no_store_headers())(scope, receive, send)
            return
        if file_path.is_file():
            from starlette.responses import FileResponse
            if path.startswith("/datasets/"):
                media_type = (
                    "text/plain; charset=utf-8"
                    if file_path.suffix.lower() in DATASET_PLAIN_TEXT_EXTS
                    else None
                )
                resp = FileResponse(
                    file_path,
                    headers=dataset_static_headers(path),
                    media_type=media_type,
                )
            else:
                resp = FileResponse(file_path, headers=static_cache_headers(path))
            await resp(scope, receive, send)
            return

        # Static asset requests must not fall through to the SPA index.
        # Browsers enforce module MIME types; returning text/html for a missing
        # JS chunk causes "Expected a JavaScript module" errors.
        if (
            path.startswith(("/assets/", "/imgs/", "/amazing-globe/", "/datasets/", "/fin-terminal/assets/"))
            or path == "/favicon.ico"
            or os.path.splitext(path)[1].lower() in STATIC_EXTS
        ):
            from starlette.responses import Response
            resp = Response(
                content=b"Static asset not found",
                status_code=404,
                media_type="text/plain",
                headers=no_store_headers(),
            )
            await resp(scope, receive, send)
            return

        # 目录路径 → 尝试 index.html
        if path.endswith("/"):
            dir_idx = resolve_path_under(dist_str, path.lstrip("/") + "/index.html")
            if dir_idx.is_file():
                from starlette.responses import FileResponse
                resp = FileResponse(dir_idx, headers=no_store_headers())
                await resp(scope, receive, send)
                return

        # /fin-terminal/ → 独立 iframe 应用
        if path.startswith("/fin-terminal"):
            ft_dist = FRONTEND_DIST / "fin-terminal"
            try:
                ft_file = resolve_path_under(ft_dist, path[len("/fin-terminal"):])
            except ValueError:
                from starlette.responses import Response
                await Response(status_code=404, headers=no_store_headers())(scope, receive, send)
                return
            if ft_file.is_file():
                from starlette.responses import FileResponse
                await FileResponse(ft_file, headers=static_cache_headers(path))(scope, receive, send)
                return
            ft_idx = resolve_path_under(ft_dist, "index.html")
            if ft_idx.is_file():
                from starlette.responses import FileResponse
                await FileResponse(ft_idx, headers=no_store_headers())(scope, receive, send)
                return

        # Only declared top-level application routes may enter the SPA. This
        # keeps scanner probes and misspelled endpoints from returning 200.
        from starlette.responses import Response

        if not _is_spa_navigation_path(path):
            await Response(status_code=404, headers=no_store_headers())(scope, receive, send)
            return

        resp = Response(
            content=_load_index_html_bust(),
            media_type="text/html",
            headers={**no_store_headers(), **spa_indexing_headers(path)},
        )
        await resp(scope, receive, send)

    return app


app = make_app()

# === 启动 ===
if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8088"))
    workers = max(1, int(os.getenv("WEB_WORKERS", "1")))
    print(f"[deploy] 生产服务器启动 → http://{host}:{port} workers={workers}")
    if workers > 1:
        uvicorn.run(
            "backend.serve_prod:app",
            host=host,
            port=port,
            workers=workers,
            backlog=int(os.getenv("WEB_BACKLOG", "2048")),
        )
    else:
        uvicorn.run(
            app,
            host=host,
            port=port,
            backlog=int(os.getenv("WEB_BACKLOG", "2048")),
        )
