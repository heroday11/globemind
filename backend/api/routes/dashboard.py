"""
Dashboard route module: health, stats, news list, news detail, analysis, aggregations, deploy-version.
"""
import hashlib
import ipaddress
import json
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import desc, func, or_
from sqlalchemy.orm import Session

from api.cache import TTLStore
from api.core.db import get_db
from api.core.environment import raw_setting, string_setting
from api.features import (
    build_feature_health_report,
    build_public_status_report,
    probe_postgres_relations,
)
from api.features.assistant import probe_assistant_health
from api.features.dashboard import (
    NewsTranslateParagraphRequest,
    build_dashboard_readiness,
    probe_dashboard_health,
)
from api.features.dashboard import (
    runtime_release as _runtime_release,
)
from api.features.evidence import build_article_evidence_chain
from api.features.financial import probe_financial_health
from api.features.graph_briefing import probe_graph_briefing_health
from api.features.ground_news import probe_ground_news_health
from api.features.identity import probe_identity_health
from api.features.operations import load_public_maintenance_history, probe_operations_health
from api.features.opinion import probe_opinion_health
from api.features.search import probe_search_health
from api.features.service_level import (
    ServiceLevelService,
    ServiceLevelStore,
    ServiceLevelStoreUnavailable,
)
from api.features.story_graph import (
    STORY_GRAPH_HEALTH_RELATIONS,
    probe_story_graph_health,
)
from api.models.schemas import (
    ArticleReaderResponse,
    NewsBulkByIdsResponse,
    NewsItem,
    NewsListResponse,
    StatsResponse,
)
from api.orm import models
from api.services.assistant_schedule import get_schedule_runner_status
from api.services.auth import get_current_user_optional, get_current_user_required
from api.services.helpers import (
    extract_source_from_url,
    get_language_name,
)
from api.services.news_search_v2 import (
    get_news_analysis_v2,
    get_news_bulk_by_ids_v2,
    get_news_by_id_v2,
    get_news_stats_v2,
    get_search_options_v2,
    list_news_v2,
)

router = APIRouter(prefix="")

_deploy_ts = str(int(time.time()))
_translate_vllm_client: Optional[httpx.Client] = None
_TRANSLATION_PROVIDER_MAX_BYTES = 128 * 1024
_TRANSLATION_PROVIDER_MAX_DEPTH = 64
_TRANSLATION_PROVIDER_MAX_NODES = 20_000
_TRANSLATION_REQUEST_MAX_BYTES = 32 * 1024
_public_cache = TTLStore("dashboard")
_public_service_level = ServiceLevelService(
    ServiceLevelStore(
        Path(string_setting("SERVICE_LEVEL_ROOT", "/root/data/web/service-level"))
    )
)


def _translate_vllm_v1_base() -> str:
    raw = string_setting("VLLM_TRANSLATE_BASE_URL", "http://127.0.0.1:8004")
    if (
        not raw
        or len(raw) > 2048
        or raw != raw.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in raw)
        or "\\" in raw
        or "%" in raw
    ):
        raise HTTPException(
            status_code=503,
            detail={"code": "TRANSLATION_PROVIDER_NOT_APPROVED"},
        )
    base = raw.rstrip("/")
    while base.endswith("/v1/v1"):
        base = base[:-3]
    try:
        parsed = urlsplit(base)
        host = parsed.hostname
        port = parsed.port
        address = ipaddress.ip_address(host or "")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "TRANSLATION_PROVIDER_NOT_APPROVED"},
        ) from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not address.is_loopback
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/", "/v1"}
    ):
        raise HTTPException(
            status_code=503,
            detail={"code": "TRANSLATION_PROVIDER_NOT_APPROVED"},
        )
    host_display = f"[{address.compressed}]" if address.version == 6 else address.compressed
    authority = f"{host_display}:{port}" if port is not None else host_display
    return f"{parsed.scheme}://{authority}/v1"


def _get_translate_vllm_client() -> httpx.Client:
    global _translate_vllm_client
    if _translate_vllm_client is None:
        _translate_vllm_client = httpx.Client(
            timeout=httpx.Timeout(90.0, connect=8.0),
            trust_env=False,
        )
    return _translate_vllm_client


def _translate_vllm_model() -> str:
    model = string_setting("VLLM_TRANSLATE_MODEL", "qwen2.5-7b-awq").strip()
    if (
        not model
        or len(model) > 256
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/+:-]{0,255}", model) is None
    ):
        raise HTTPException(
            status_code=503,
            detail={"code": "TRANSLATION_MODEL_NOT_CONFIGURED"},
        )
    return model


def _clean_translation_output(text_value: str) -> str:
    out = str(text_value or "").strip()
    out = re.sub(r"^(译文|翻译|简体中文|中文翻译)\s*[：:]\s*", "", out, flags=re.I)
    out = re.sub(r"^\s*[-*]\s*", "", out, flags=re.M)
    return out.strip()


def _reject_translation_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _parse_translation_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite JSON number")
    return parsed


def _translation_json_object(pairs: list[tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _validate_translation_json_shape(value: Any) -> None:
    pending = [(value, 0)]
    node_count = 0
    while pending:
        current, depth = pending.pop()
        node_count += 1
        if node_count > _TRANSLATION_PROVIDER_MAX_NODES:
            raise ValueError("translation provider response has too many nodes")
        if depth > _TRANSLATION_PROVIDER_MAX_DEPTH:
            raise ValueError("translation provider response is too deep")
        if isinstance(current, dict):
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)
        elif isinstance(current, float) and not math.isfinite(current):
            raise ValueError("translation provider response contains non-finite number")


def _parse_translation_provider_response(response: httpx.Response) -> Dict[str, Any]:
    content_type = str(response.headers.get("content-type", ""))
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type != "application/json" and not media_type.endswith("+json"):
        raise ValueError("translation provider response is not JSON")

    content_length = response.headers.get("content-length")
    if content_length is not None:
        raw_length = str(content_length).strip()
        if re.fullmatch(r"[0-9]+", raw_length) is None:
            raise ValueError("invalid translation provider content length")
        try:
            declared_size = int(raw_length, 10)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid translation provider content length") from exc
        if declared_size < 0 or declared_size > _TRANSLATION_PROVIDER_MAX_BYTES:
            raise ValueError("translation provider response is too large")

    body = bytearray()
    for chunk in response.iter_bytes():
        if not isinstance(chunk, bytes):
            raise ValueError("translation provider response chunk is invalid")
        if len(body) + len(chunk) > _TRANSLATION_PROVIDER_MAX_BYTES:
            raise ValueError("translation provider response is too large")
        body.extend(chunk)
    if not body:
        raise ValueError("translation provider response is empty")

    parsed = json.loads(
        bytes(body).decode("utf-8"),
        object_pairs_hook=_translation_json_object,
        parse_constant=_reject_translation_json_constant,
        parse_float=_parse_translation_json_float,
    )
    _validate_translation_json_shape(parsed)
    if not isinstance(parsed, dict):
        raise ValueError("translation provider response must be an object")
    return parsed


async def _require_unambiguous_translation_json(request: Request) -> None:
    content_type = (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    )
    if content_type != "application/json" and not content_type.endswith("+json"):
        raise HTTPException(
            status_code=422,
            detail={"code": "TRANSLATION_REQUEST_AMBIGUOUS"},
        )
    body = await request.body()
    if not body or len(body) > _TRANSLATION_REQUEST_MAX_BYTES:
        raise HTTPException(
            status_code=422,
            detail={"code": "TRANSLATION_REQUEST_AMBIGUOUS"},
        )
    try:
        parsed = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_translation_json_object,
            parse_constant=_reject_translation_json_constant,
            parse_float=_parse_translation_json_float,
        )
        _validate_translation_json_shape(parsed)
        if not isinstance(parsed, dict):
            raise ValueError("translation request root must be an object")
    except (TypeError, UnicodeError, ValueError, RecursionError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "TRANSLATION_REQUEST_AMBIGUOUS"},
        ) from exc


def _translate_paragraph_with_vllm(
    text_value: str,
    target_language: str,
    source_language: str,
) -> Dict[str, Any]:
    request = NewsTranslateParagraphRequest(
        text=text_value,
        target_language=target_language,
        source_language=source_language,
    )
    source = request.text
    v1_base = _translate_vllm_v1_base()
    resolved_model = _translate_vllm_model()
    max_tokens = min(2048, max(256, int(len(source) * 1.7) + 160))
    payload = {
        "model": resolved_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是新闻译审助手。将输入忠实翻译为简体中文，保留事实、数字、机构名、地名和人名；"
                    "不要总结，不要扩写，不要输出解释、标签、Markdown 或引号。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"源语言：{request.source_language}\n"
                    f"目标语言：{request.target_language}\n\n"
                    f"待翻译段落：\n{source}"
                ),
            },
        ],
        "temperature": 0.05,
        "top_p": 0.9,
        "max_tokens": max_tokens,
        "stream": False,
    }
    try:
        with _get_translate_vllm_client().stream(
            "POST",
            f"{v1_base}/chat/completions",
            json=payload,
            timeout=90,
        ) as resp:
            resp.raise_for_status()
            data = _parse_translation_provider_response(resp)
        if data.get("model") != resolved_model:
            raise ValueError("translation provider model does not match request")
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ValueError("translation provider response choices are invalid")
        message = choices[0].get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise ValueError("translation provider response content is invalid")
        translated = message["content"]
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": "TRANSLATION_PROVIDER_UNAVAILABLE"},
        ) from exc
    translated = _clean_translation_output(translated)
    if (
        not translated
        or len(translated) > 24_000
        or any(
            (ord(character) < 32 and character not in "\t\n\r")
            or ord(character) == 127
            or 0xD800 <= ord(character) <= 0xDFFF
            for character in translated
        )
    ):
        raise HTTPException(
            status_code=502,
            detail={"code": "TRANSLATION_OUTPUT_INVALID"},
        )
    return {
        "schema_version": "globemind.news-translation.v1",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "text": translated,
        "provenance": {
            "mode": "machine_translation",
            "backend": "local-vllm-loopback",
            "model_id": resolved_model,
            "source_language": request.source_language,
            "target_language": request.target_language,
            "source_text_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "source_text_length": len(source),
            "human_review_state": "not_reviewed",
            "quality_state": "not_measured",
            "terminology_version": "not_configured",
            "persistence": "not_persisted_by_endpoint",
            "provider_scope": "loopback_only",
        },
    }


# ─────────────────────────────────────────────
# GET /api/health, /api/health/live, /api/health/ready
# ─────────────────────────────────────────────
@router.get("/api/health/live", tags=["根目录"])
async def liveness():
    return {
        "status": "healthy",
        "service": "globemind-api",
        "check": "process",
        "release": _runtime_release(),
    }


@router.get("/api/health", tags=["根目录"])
@router.get("/api/health/ready", tags=["根目录"])
def readiness(db: Session = Depends(get_db)):
    scheduler = get_schedule_runner_status()
    status_code, payload = build_dashboard_readiness(db, scheduler)
    return JSONResponse(status_code=status_code, content=payload)


def _build_feature_health_report(db: Session):
    scheduler = get_schedule_runner_status()
    return build_feature_health_report(
        (
            probe_identity_health(db),
            probe_dashboard_health(db),
            probe_assistant_health(scheduler),
            probe_search_health(db),
            probe_financial_health(),
            probe_graph_briefing_health(db),
            probe_story_graph_health(
                lambda: probe_postgres_relations(
                    db,
                    STORY_GRAPH_HEALTH_RELATIONS,
                )
            ),
            probe_ground_news_health(db),
            probe_opinion_health(db),
            probe_operations_health(),
        )
    )


def _build_public_feature_health_report(db: Session):
    """Probe only the three business data capabilities published to visitors."""
    return build_feature_health_report(
        (
            probe_search_health(db),
            probe_ground_news_health(db),
            probe_opinion_health(db),
        )
    )


@router.get("/api/status", tags=["根目录"])
def public_status(db: Session = Depends(get_db)):
    """Return the bounded, research-facing data status contract."""
    evaluated_at = datetime.now(timezone.utc)
    report = _build_public_feature_health_report(db)
    try:
        service_level_summary = _public_service_level.summary().model_dump(mode="json")
    except ServiceLevelStoreUnavailable:
        service_level_summary = None
    return JSONResponse(
        status_code=200,
        headers={"Cache-Control": "no-store"},
        content=build_public_status_report(
            report,
            generated_at=evaluated_at,
            service_level_summary=service_level_summary,
            maintenance_history=load_public_maintenance_history(
                raw_setting("MAINTENANCE_EVENT_LEDGER_PATH", ""),
                evaluated_at=evaluated_at,
            ),
        ),
    )


@router.get("/api/health/features", tags=["根目录"])
def feature_health(
    db: Session = Depends(get_db),
    _user: dict[str, Any] = Depends(get_current_user_required),
):
    """Return detailed capability probes to authenticated operators and gates."""
    report = _build_feature_health_report(db)
    return JSONResponse(
        status_code=200 if report.ready else 503,
        content=jsonable_encoder(report),
    )


# ─────────────────────────────────────────────
# GET /api/dashboard/stats
# ─────────────────────────────────────────────
@router.get("/api/dashboard/stats", response_model=StatsResponse, tags=["仪表盘"])
def get_stats(db: Session = Depends(get_db)):
    try:
        cached = _public_cache.get("stats:v2")
        if cached is not None:
            return cached
        return _public_cache.set("stats:v2", get_news_stats_v2(), ttl_seconds=300)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"统计查询失败: {str(e)}")


# ─────────────────────────────────────────────
# GET /api/dashboard/search/options
# ─────────────────────────────────────────────
@router.get("/api/dashboard/search/options", tags=["仪表盘"])
def get_search_options(db: Session = Depends(get_db)):
    try:
        cached = _public_cache.get("search-options:v2")
        if cached is not None:
            return cached
        return _public_cache.set("search-options:v2", get_search_options_v2(), ttl_seconds=600)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"搜索选项查询失败: {str(e)}")


# ─────────────────────────────────────────────
# GET /api/dashboard/news 分页列表
# ─────────────────────────────────────────────
@router.get("/api/dashboard/news", response_model=NewsListResponse, tags=["仪表盘"])
def get_news_list(
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    sort_by: Literal["published_at", "pub_time"] = Query("published_at"),
    sort_order: Literal["asc", "desc"] = Query("desc"),
    favorite_scope_topic: Optional[str] = Query(
        None,
        description="与搜索页主题联动：传入时 is_favorited/is_warned 仅判断该主题下记录",
    ),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """分页获取新闻列表，与 POST /search 的列表字段一致，不含 body 可减体积。"""
    try:
        return list_news_v2(
            page=page,
            size=size,
            sort_by=sort_by,
            sort_order=sort_order,
            user=user,
            app_db=db,
            favorite_scope_topic=favorite_scope_topic,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"列表查询失败: {str(e)}")


# ─────────────────────────────────────────────
# GET /api/dashboard/news/by-ids 批量取新闻
# ─────────────────────────────────────────────
@router.get("/api/dashboard/news/by-ids", response_model=NewsBulkByIdsResponse, tags=["仪表盘"])
def get_news_bulk_by_ids(
    ids: str = Query(..., min_length=1, max_length=12000, description="逗号分隔的新闻 id，最多 500 条"),
    favorite_scope_topic: Optional[str] = Query(
        None,
        description="若传入，则 is_favorited/is_warned 仅统计该主题",
    ),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    id_list: List[int] = []
    for p in (ids or "").split(","):
        p = p.strip()
        if not p:
            continue
        try:
            id_list.append(int(p))
        except ValueError:
            continue
    id_list = list(dict.fromkeys(id_list))[:500]
    if not id_list:
        return NewsBulkByIdsResponse(data=[])
    data_list = get_news_bulk_by_ids_v2(id_list, user, db, favorite_scope_topic)
    return NewsBulkByIdsResponse(data=data_list)


# ─────────────────────────────────────────────
# POST /api/dashboard/news/translate-paragraph 单段翻译
# ─────────────────────────────────────────────
@router.post("/api/dashboard/news/translate-paragraph", tags=["仪表盘"])
def translate_news_paragraph(
    body: NewsTranslateParagraphRequest,
    _user: Dict[str, Any] = Depends(get_current_user_required),
    _strict_json: None = Depends(_require_unambiguous_translation_json),
):
    """新闻详情翻译面板：由后端调用本机 vLLM，前端逐段请求。"""
    return _translate_paragraph_with_vllm(
        body.text,
        body.target_language,
        body.source_language,
    )


# ─────────────────────────────────────────────
# GET /api/dashboard/news/{news_id} 单条新闻详情
# ─────────────────────────────────────────────
@router.get("/api/dashboard/news/{news_id}", response_model=NewsItem, tags=["仪表盘"])
def get_news_by_id(news_id: int, db: Session = Depends(get_db)):
    """根据 id 获取单条新闻详情，用于详情页展示。"""
    item = get_news_by_id_v2(news_id)
    if not item:
        raise HTTPException(status_code=404, detail="新闻不存在")
    return item


# ─────────────────────────────────────────────
# GET /api/article/{news_id} 兼容旧路由
# ─────────────────────────────────────────────
@router.get("/api/article/{news_id}", response_model=NewsItem, tags=["仪表盘"])
def get_article_by_id_compat(news_id: int, db: Session = Depends(get_db)):
    """兼容 Vue 仪表盘与旧 search_server：GET /api/article/{id}（同 GET /api/dashboard/news/{id}）。
    【注意】此接口为向前兼容保留，新代码请使用 /api/dashboard/news/{news_id}。"""
    return get_news_by_id(news_id, db)


# ─────────────────────────────────────────────
# GET /api/article/{news_id}/reader 阅读器
# ─────────────────────────────────────────────
@router.get("/api/article/{news_id}/reader", response_model=ArticleReaderResponse, tags=["仪表盘"])
def get_article_reader(news_id: int, db: Session = Depends(get_db)):
    """
    阅读器专用：新闻全文 + 可选 `news_analysis` 行（存在则返回，便于前端展示情感/实体等）。
    """
    news_item = get_news_by_id(news_id, db)
    raw_analysis: Optional[Dict[str, Any]] = get_news_analysis_v2(news_id)
    analysis = dict(raw_analysis) if isinstance(raw_analysis, dict) else {}
    analysis["evidence_chain"] = build_article_evidence_chain(news_item, analysis)
    return ArticleReaderResponse(news=news_item, analysis=analysis)


# ─────────────────────────────────────────────
# GET /api/dashboard/news/{news_id}/analysis 分析面板
# ─────────────────────────────────────────────
@router.get("/api/dashboard/news/{news_id}/analysis", tags=["仪表盘"])
def get_news_analysis(news_id: int, db: Session = Depends(get_db)):
    """新闻详情页分析面板：元数据 + 计算字段 + L1 聚类 + 舆情趋势。"""
    try:
        analysis = get_news_analysis_v2(news_id)
        article = get_news_by_id_v2(news_id)
        if article is not None:
            analysis["evidence_chain"] = build_article_evidence_chain(article, analysis)
        return JSONResponse(content=jsonable_encoder(analysis))
    except Exception:
        return JSONResponse(content={"items": [], "l1_clusters": [], "trend": []}, status_code=500)


# ─────────────────────────────────────────────
# GET /api/dashboard/news-rank
# ─────────────────────────────────────────────
@router.get("/api/dashboard/news-rank", tags=["仪表盘"])
def get_news_rank(
    type: Optional[str] = Query("day"),
    db: Session = Depends(get_db),
):
    """新闻排行榜：按发布时间取最近 N 条，返回 title、hotValue、trend。"""
    try:
        limit = 10
        query = (
            db.query(models.News.id, models.News.title, models.News.pub_time)
            .order_by(desc(models.News.pub_time))
            .limit(limit * 3)
        )
        rows = query.all()
        out = []
        for i, row in enumerate(rows[:limit]):
            out.append({
                "title": row.title or "无标题",
                "hotValue": f"{(len(rows) - i) * 0.1:.1f}w",
                "trend": "up" if i % 2 == 0 else "down",
            })
        return out
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────
# GET /api/dashboard/language-ratio
# ─────────────────────────────────────────────
@router.get("/api/dashboard/language-ratio", tags=["仪表盘"])
def get_language_ratio(db: Session = Depends(get_db)):
    """多语言占比：基于 language_stats 计算各语言占比，总和 100。"""
    try:
        total_news = db.query(func.count(models.News.id)).scalar() or 0
        if total_news == 0:
            return []
        lang_counts = (
            db.query(models.News.language_id, func.count(models.News.id).label("cnt"))
            .group_by(models.News.language_id)
            .all()
        )
        total = sum(c for _, c in lang_counts)
        out = []
        for language_value, cnt in lang_counts:
            name = get_language_name(language_value)
            pct = round((cnt / total) * 100) if total else 0
            out.append({"name": name, "value": pct})
        remainder = 100 - sum(x["value"] for x in out)
        if out and remainder != 0:
            out[0]["value"] = out[0]["value"] + remainder
        return out
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────
# GET /api/dashboard/media-source
# ─────────────────────────────────────────────
@router.get("/api/dashboard/media-source", tags=["仪表盘"])
def get_media_source(db: Session = Depends(get_db), limit: int = Query(7, le=20)):
    """主要媒体：从 request_url 解析域名并聚合，返回 name、score。"""
    try:
        rows = (
            db.query(models.News.request_url, func.count(models.News.id).label("cnt"))
            .filter(models.News.request_url.isnot(None), models.News.request_url != "")
            .group_by(models.News.request_url)
            .order_by(desc(func.count(models.News.id)))
            .limit(limit * 3)
            .all()
        )
        by_domain: Dict[str, int] = {}
        for url, cnt in rows:
            name = extract_source_from_url(url or "")
            if name:
                by_domain[name] = by_domain.get(name, 0) + cnt
        sorted_domains = sorted(by_domain.items(), key=lambda x: -x[1])[:limit]
        max_cnt = max((c for _, c in sorted_domains), default=1)
        return [{"name": n, "score": round(80 + (c / max_cnt) * 10, 1)} for n, c in sorted_domains]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────
# GET /api/dashboard/source-type
# ─────────────────────────────────────────────
@router.get("/api/dashboard/source-type", tags=["仪表盘"])
def get_source_type(db: Session = Depends(get_db)):
    """信源类型分布：按 request_url 是否含常见域名打标签，返回 name、value 占比。"""
    try:
        total_news = db.query(func.count(models.News.id)).scalar() or 0
        if total_news == 0:
            return [
                {"name": "官方媒体", "value": 32},
                {"name": "商业媒体", "value": 28},
                {"name": "独立媒体", "value": 12},
                {"name": "国际组织", "value": 15},
                {"name": "其他", "value": 13},
            ]
        official = (
            db.query(func.count(models.News.id))
            .filter(
                models.News.request_url.isnot(None),
                models.News.request_url != "",
                or_(
                    models.News.request_url.contains(".gov"),
                    models.News.request_url.contains("reuters"),
                    models.News.request_url.contains("xinhua"),
                ),
            )
            .scalar()
            or 0
        )
        pct_official = round((official / total_news) * 100)
        pct_other = 100 - pct_official
        return [
            {"name": "官方/主流媒体", "value": pct_official},
            {"name": "其他", "value": pct_other},
        ]
    except Exception:
        return [
            {"name": "官方媒体", "value": 32},
            {"name": "商业媒体", "value": 28},
            {"name": "独立媒体", "value": 12},
            {"name": "国际组织", "value": 15},
            {"name": "其他", "value": 15},
        ]


# ─────────────────────────────────────────────
# GET /api/deploy-version
# ─────────────────────────────────────────────
@router.get("/api/deploy-version", tags=["系统"])
def get_deploy_version():
    return {
        "deploy_timestamp": _deploy_ts,
        "server_time": datetime.now().isoformat(),
        **_runtime_release(),
    }
