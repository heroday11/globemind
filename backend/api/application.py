# ruff: noqa: E402
"""
Thin entry point: lifespan, CORS, LLM proxy, frontend SPA, route mounting.
Route handlers have been extracted to api/routes/.
Service logic is in api/services/.
"""
import hashlib
import logging
import os
import re
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from api.core.db import DB_HOST, DB_NAME, DB_PORT, SessionLocal, create_tables
from api.core.environment import (
    app_version,
    bool_setting,
    is_test_environment,
    string_setting,
)
from api.core.http_security import RequestBodyLimitMiddleware, RequestRateLimitMiddleware
from api.core.request_observability import (
    REQUEST_ID_HEADER,
    resolve_request_id,
    safe_request_method,
    safe_route_template,
)
from api.core.runtime_security import is_production, validate_runtime_security
from api.features.service_level import (
    ServiceLevelASGIMiddleware,
    ServiceLevelInstrumentationAdapter,
    ServiceLevelService,
    ServiceLevelStore,
)
from api.services.assistant_schedule import start_schedule_runner, stop_schedule_runner
from api.services.auth import get_active_user_from_access_token, get_current_user_required
from api.services.helpers import run_startup_schema_check
from api.static_cache import (
    is_static_asset_path,
    no_store_headers,
    resolve_path_under,
    spa_indexing_headers,
    static_cache_headers,
)

# sys.path setup
_API_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _API_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_runtime_security()
    if is_test_environment():
        yield
        return
    try:
        allow_schema_mutations = bool_setting("ALLOW_RUNTIME_SCHEMA_MUTATIONS")
        if not is_production() or allow_schema_mutations:
            create_tables()
        else:
            print("[startup] production schema mutations disabled; running read-only checks", flush=True)
        print(f"[OK] 数据库连接成功，schema 自检开始 -> {DB_HOST}:{DB_PORT}/{DB_NAME}")
        db = SessionLocal()
        try:
            news_cnt = db.execute(text("SELECT COUNT(*) FROM public.news")).scalar() or 0
            user_cnt = db.execute(text("SELECT COUNT(*) FROM public.app_user")).scalar() or 0
            print(f"[INFO] 当前库概览: news={news_cnt}, app_user={user_cnt}")
            ne_cnt = None
            try:
                ne_cnt = db.execute(
                    text(
                        "SELECT COUNT(*) FROM information_schema.tables "
                        "WHERE table_schema='public' AND table_name='news_embeddings'"
                    )
                ).scalar()
                if int(ne_cnt or 0) == 1:
                    emb_n = db.execute(text("SELECT COUNT(*) FROM public.news_embeddings")).scalar() or 0
                    print(f"[INFO] 聚类/向量: news_embeddings 行数={emb_n}（模糊检索兜底可用）", flush=True)
            except Exception:
                pass
            schema_report = run_startup_schema_check(db)
            if not schema_report.get("ready"):
                raise RuntimeError(f"required database schema unavailable: {schema_report['errors']}")
        finally:
            db.close()
    except Exception as e:
        print(f"[ERROR] 数据库连接失败: {e}")
        if is_production():
            raise
    start_schedule_runner()
    try:
        yield
    finally:
        await stop_schedule_runner()


app = FastAPI(
    title="新闻爬虫数据API",
    description="从dg_dev_crawler数据库的news表读取数据的API服务",
    version=app_version(),
    lifespan=lifespan,
    docs_url=None if is_production() else "/docs",
    redoc_url=None if is_production() else "/redoc",
    openapi_url=None if is_production() else "/openapi.json",
)

_PROTECTED_CC_PATHS = frozenset({"/cc/chat", "/cc/chat/stream", "/cc/config"})
_request_logger = logging.getLogger("globemind.http")


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    request_id = resolve_request_id(request.headers.get(REQUEST_ID_HEADER))
    request.state.request_id = request_id
    started = time.monotonic()
    path = request.url.path
    try:
        if path in _PROTECTED_CC_PATHS:
            if is_production():
                resp = JSONResponse(status_code=404, content={"detail": "Not Found"})
            else:
                scheme, separator, token = request.headers.get("Authorization", "").partition(" ")
                user = (
                    get_active_user_from_access_token(token.strip())
                    if separator and scheme.lower() == "bearer"
                    else None
                )
                if user is None:
                    resp = JSONResponse(status_code=401, content={"detail": "未登录或 token 无效"})
                else:
                    resp = await call_next(request)
        else:
            resp = await call_next(request)
    except Exception:
        _request_logger.error(
            "request_failed request_id=%s method=%s route=%s",
            request_id,
            safe_request_method(request.method),
            safe_route_template(request.scope),
        )
        raise

    if is_static_asset_path(path):
        for key, value in static_cache_headers(path).items():
            resp.headers[key] = value
        resp.headers.pop("Pragma", None)
        resp.headers.pop("Expires", None)
    elif path.startswith(("/api/", "/llm/", "/cc/")) or "text/html" in resp.headers.get("content-type", ""):
        for key, value in no_store_headers().items():
            resp.headers[key] = value
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if is_production():
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    resp.headers[REQUEST_ID_HEADER] = request_id
    _request_logger.info(
        "request_complete request_id=%s method=%s route=%s status=%s duration_ms=%.2f",
        request_id,
        safe_request_method(request.method),
        safe_route_template(request.scope),
        resp.status_code,
        (time.monotonic() - started) * 1000,
    )
    return resp

# CORS
_cors_origins = string_setting("CORS_ORIGINS")
if _cors_origins:
    _allowed_origins = [o.strip() for o in _cors_origins.split(",") if o.strip()]
else:
    _allowed_origins = ["http://localhost:5173", "http://127.0.0.1:5173", "https://globemind.top"]
app.add_middleware(RequestBodyLimitMiddleware)
app.add_middleware(RequestRateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Assistant-Session-Id", REQUEST_ID_HEADER],
)
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)
_service_level_measurement = ServiceLevelService(
    ServiceLevelStore(
        Path(
            string_setting(
                "SERVICE_LEVEL_ROOT",
                "/root/data/web/service-level",
            )
        )
    )
)
app.add_middleware(
    ServiceLevelASGIMiddleware,
    adapter=ServiceLevelInstrumentationAdapter(_service_level_measurement),
    routes={
        ("POST", "/api/dashboard/search"): "search",
        ("GET", "/api/user/privacy/export"): "export",
        (
            "GET",
            "/api/research/projects/{project_id}/exports/{export_version}/artifact",
        ): "report",
    },
)

# vLLM proxy
_llm_client: httpx.AsyncClient | None = None


async def _get_llm_client() -> httpx.AsyncClient:
    global _llm_client
    if _llm_client is None:
        _llm_client = httpx.AsyncClient(
            base_url=string_setting("VLLM_TARGET", "http://127.0.0.1:8004"),
            timeout=300.0,
            trust_env=False,
        )
    return _llm_client


async def llm_proxy(path: str, request: Request):
    client = await _get_llm_client()
    body = await request.body()
    target_url = f"/{path}"
    query = str(request.query_params)
    if query:
        target_url += f"?{query}"
    req = client.build_request(
        method=request.method,
        url=target_url,
        headers={
            k: v
            for k, v in request.headers.items()
            if k.lower() not in ("authorization", "cookie", "host", "content-length")
        },
        content=body,
    )
    resp = await client.send(req, stream=True)
    return StreamingResponse(
        resp.aiter_bytes(),
        status_code=resp.status_code,
        headers={k: v for k, v in resp.headers.items() if k.lower() not in ("transfer-encoding",)},
    )


_LLM_PROXY_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS")
for _llm_proxy_method in _LLM_PROXY_METHODS:
    app.add_api_route(
        "/llm/{path:path}",
        llm_proxy,
        methods=[_llm_proxy_method],
        dependencies=[Depends(get_current_user_required)],
        name=f"llm_proxy_{_llm_proxy_method.lower()}",
        operation_id=f"authenticated_llm_proxy_{_llm_proxy_method.lower()}",
    )

# Frontend static assets
_frontend_dist = string_setting(
    "FRONTEND_DIST",
    "/root/data/globemind/frontend/vue_project/dist",
)
_v2_dist = os.path.join(_frontend_dist, "v2")
_frontend_index = os.path.join(_v2_dist, "index.html") if os.path.isdir(_v2_dist) else (
    os.path.join(_frontend_dist, "index.html") if os.path.isdir(_frontend_dist) else None
)
if _frontend_index:
    print(f"[frontend] 前端 SPA 已就绪: {_frontend_index}", flush=True)
    if os.path.isdir(_v2_dist):
        print(f"[frontend] v2 dist 已就绪: {_v2_dist}", flush=True)
    _assets_dir = (
        os.path.join(_v2_dist, "assets")
        if os.path.isdir(os.path.join(_v2_dist, "assets"))
        else os.path.join(_frontend_dist, "assets")
    )
    app.mount("/assets", StaticFiles(directory=_assets_dir), name="frontend_assets")
    imgs_dir = os.path.join(_frontend_dist, "imgs")
    if os.path.isdir(imgs_dir):
        app.mount("/imgs", StaticFiles(directory=imgs_dir), name="frontend_images")
        print(f"[frontend] 已挂载 /imgs -> {imgs_dir}", flush=True)
else:
    print(f"[frontend] 前端目录不存在: {_frontend_dist}", flush=True)

# Router modules contain individual endpoint handlers
from cppt.cc_bridge import cc_router

from api.routes.assistant import router as assistant_router
from api.routes.assistant_data import router as assistant_data_router
from api.routes.assistant_schedules import router as assistant_schedules_router
from api.routes.auth import router as auth_router
from api.routes.authoritative_data import router as authoritative_data_router
from api.routes.briefing import router as briefing_router
from api.routes.dashboard import router as dashboard_router
from api.routes.data_governance import router as data_governance_router
from api.routes.entity_governance import router as entity_governance_router
from api.routes.evidence_ledger import router as evidence_ledger_router
from api.routes.financial import router as financial_router
from api.routes.governance_inventory import router as governance_inventory_router
from api.routes.model_assurance import router as model_assurance_router
from api.routes.opinion import router as opinion_router
from api.routes.opinion_v2 import router as opinion_v2_router
from api.routes.ops_monitor import router as ops_monitor_router
from api.routes.research_workflow import router as research_workflow_router
from api.routes.search import router as search_router
from api.routes.service_level import router as service_level_router
from api.routes.story_graph import router as story_graph_router

app.include_router(cc_router)
app.include_router(dashboard_router)
app.include_router(search_router)
app.include_router(auth_router)
app.include_router(authoritative_data_router)
app.include_router(assistant_router)
app.include_router(assistant_schedules_router)
app.include_router(briefing_router, prefix="/api/graph", tags=["知识图谱层级"])
app.include_router(opinion_v2_router, prefix="/api")
app.include_router(opinion_router, prefix="/api")
app.include_router(assistant_data_router, prefix="/api")
app.include_router(story_graph_router)
app.include_router(financial_router)
app.include_router(ops_monitor_router)
app.include_router(data_governance_router)
app.include_router(entity_governance_router)
app.include_router(evidence_ledger_router)
app.include_router(governance_inventory_router)
app.include_router(research_workflow_router)
app.include_router(model_assurance_router)
app.include_router(service_level_router)


@app.get("/.well-known/security.txt", include_in_schema=False)
def security_contact_document() -> PlainTextResponse:
    """Expose the single reviewed dot-path exception from RFC 9116."""
    return PlainTextResponse(
        "\n".join(
            (
                "Contact: mailto:contact@globemind.top",
                "Expires: 2027-02-09T00:00:00Z",
                "Preferred-Languages: zh, en",
                "Canonical: https://globemind.top/.well-known/security.txt",
                "Policy: https://globemind.top/security",
                "",
            )
        ),
        headers={
            "Cache-Control": "public, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )

# SPA catch-all (must be registered last, after all routes)
if _frontend_index:
    _index_sig = None
    _index_html_bytes = b""
    _etag = ""
    _deploy_ts = ""
    _asset_url_re = re.compile(r'(src|href)="((?:/v2)?/assets/[^"]+\.(?:js|css))"')
    _retired_frontend_routes = {
        "data-service/alert-center": "/financial-terminal",
        "data-service/open-computing": "/data-service/data-search",
        "data-service/algorithm-analysis": "/data-service/data-search",
    }

    def _index_asset_exists(asset_url: str) -> bool:
        asset_path = asset_url.split("?", 1)[0]
        if asset_path.startswith("/v2/assets/"):
            candidates = [
                os.path.join(_frontend_dist, asset_path.lstrip("/")),
                os.path.join(_v2_dist, "assets", asset_path.rsplit("/", 1)[-1]),
            ]
        elif asset_path.startswith("/assets/"):
            asset_name = asset_path[len("/assets/"):]
            candidates = [
                os.path.join(_assets_dir, asset_name),
                os.path.join(_frontend_dist, "assets", asset_name),
            ]
        else:
            candidates = [os.path.join(_frontend_dist, asset_path.lstrip("/"))]
        return any(os.path.isfile(path) for path in candidates)

    def _load_frontend_index():
        global _index_sig, _index_html_bytes, _etag, _deploy_ts
        try:
            stat = os.stat(_frontend_index)
        except OSError:
            return _index_html_bytes, _etag, _deploy_ts

        sig = (stat.st_mtime_ns, stat.st_size)
        if sig == _index_sig:
            return _index_html_bytes, _etag, _deploy_ts

        with open(_frontend_index, "rb") as f:
            raw_index_html = f.read().decode("utf-8")

        asset_urls = [m.group(2) for m in _asset_url_re.finditer(raw_index_html)]
        missing_assets = [url for url in asset_urls if not _index_asset_exists(url)]
        if missing_assets and _index_html_bytes:
            print(
                "[frontend] index.html references missing assets; keeping previous SPA entry: "
                + ", ".join(missing_assets[:5]),
                flush=True,
            )
            return _index_html_bytes, _etag, _deploy_ts

        _deploy_ts = str(stat.st_mtime_ns)
        index_html = _asset_url_re.sub(
            lambda m: f'{m.group(1)}="{m.group(2)}?v={_deploy_ts}"',
            raw_index_html,
        )
        _index_html_bytes = index_html.encode("utf-8")
        _etag = hashlib.md5(_index_html_bytes).hexdigest()
        _index_sig = sig
        return _index_html_bytes, _etag, _deploy_ts

    _load_frontend_index()

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str, request: Request):
        if full_path.startswith("api/") or full_path.startswith("llm/") or full_path.startswith("cc/"):
            raise HTTPException(404)
        normalized_path = full_path.strip("/")
        if is_production() and normalized_path in {"docs", "redoc", "openapi.json"}:
            raise HTTPException(404)
        if normalized_path in _retired_frontend_routes:
            return RedirectResponse(_retired_frontend_routes[normalized_path], status_code=308)
        if normalized_path.startswith("data-service/model-test/"):
            return RedirectResponse("/data-service/data-search", status_code=308)
        try:
            static_candidate = resolve_path_under(_frontend_dist, full_path)
        except ValueError:
            raise HTTPException(404)
        if static_candidate.is_file():
            ext = os.path.splitext(full_path)[1].lower()
            if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico"):
                cache_max_age = "86400"
            elif ext == ".html":
                cache_max_age = "0, no-store, must-revalidate"  # HTML不缓存，确保实时更新
            else:
                cache_max_age = "3600"
            return FileResponse(
                static_candidate,
                headers={
                    "Cache-Control": f"public, max-age={cache_max_age}",
                    "X-Content-Type-Options": "nosniff",
                    "Referrer-Policy": "strict-origin-when-cross-origin",
                },
            )
        static_exts = {
            ".js", ".css", ".map", ".json",
            ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
            ".woff", ".woff2", ".ttf", ".otf",
        }
        request_path = "/" + full_path.lstrip("/")
        if (
            request_path.startswith(("/assets/", "/imgs/", "/amazing-globe/"))
            or request_path == "/favicon.ico"
            or os.path.splitext(request_path)[1].lower() in static_exts
        ):
            return Response(
                content=b"Static asset not found",
                status_code=404,
                media_type="text/plain",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "X-Content-Type-Options": "nosniff",
                },
            )
        index_html_bytes, etag, deploy_ts = _load_frontend_index()
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304)
        return Response(
            content=index_html_bytes,
            media_type="text/html",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "ETag": etag,
                "Pragma": "no-cache",
                "X-Deploy-Timestamp": deploy_ts,
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "strict-origin-when-cross-origin",
                **spa_indexing_headers(request_path),
            },
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8088)
