"""
Search route module: POST /api/dashboard/search, /search/clustered, /search/v11-clusters, children.
"""
import time
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from api.core.db import get_db
from api.core.environment import int_setting, string_setting
from api.features.search import (
    ClusterSearchDependencies,
    ClusterSearchSettings,
    DashboardSearchDependencies,
    QUERY_LANGUAGE_VERSION,
    QUERY_LIMITS,
    SearchDependencyUnavailable,
    SearchFilterUnsupported,
    SearchModeError,
    SearchQueryRequired,
    SearchSnapshotConflict,
    SearchSnapshotLedger,
    SearchSnapshotNotFound,
    SearchSnapshotUnavailable,
    SearchSyntaxUnsupported,
    SearchTimeFilterError,
    V11ClusterSearchRequest,
    V11ClusterSearchResponse,
    V11SearchContractError,
    execute_clustered_search,
    execute_dashboard_search,
)
from api.features.search import (
    expand_v11_cluster_children as _expand_v11_cluster_children,
)
from api.features.search import (
    search_v11_clusters as _search_v11_clusters,
)
from api.models.schemas import (
    ClusteredSearchResponse,
    SearchQueryReceipt,
    SearchRequest,
    SearchResponse,
)
from api.services.auth import get_current_user_optional, get_current_user_required
from api.services.helpers import resolve_country_to_language
from api.services.news_search_v2 import SearchDeadlineExceeded, search_dashboard_v2
from api.services.search_service import (
    _build_semantic_query_text,
    _fetch_news_rows_by_ids_ordered,
    _passes_vector_mode_filters,
    _rows_to_news_items,
)
from api.services.search_service import (
    encode_query_bge_m3 as _encode_query_bge_m3,
)
from api.services.search_service import (
    execute_search_exact as _execute_search_exact,
)
from api.services.search_service import (
    vector_fallback_exact_enabled as _vector_fallback_exact_enabled,
)

router = APIRouter(prefix="")


class CaptureSearchSnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    receipt: SearchQueryReceipt
    expected_previous_snapshot_id: str | None = Field(default=None, max_length=80)


def _snapshot_ledger() -> SearchSnapshotLedger:
    root = Path(
        string_setting(
            "SEARCH_SNAPSHOT_ROOT",
            "/root/data/web/search-snapshots",
        )
    )
    return SearchSnapshotLedger(root)


def _snapshot_actor_id(user: dict[str, Any]) -> int:
    raw = user.get("user_id")
    if isinstance(raw, bool):
        raise HTTPException(status_code=403, detail="active user identity is invalid")
    try:
        actor_id = int(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=403, detail="active user identity is invalid") from exc
    if actor_id <= 0:
        raise HTTPException(status_code=403, detail="active user identity is invalid")
    return actor_id


def _raise_snapshot_error(exc: Exception) -> None:
    if isinstance(exc, SearchSnapshotNotFound):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, SearchSnapshotConflict):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, SearchSnapshotUnavailable):
        raise HTTPException(status_code=503, detail="search snapshot ledger is unavailable") from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


# ===================================================================
# POST /api/dashboard/search 三模式搜索
# ===================================================================
@router.post("/api/dashboard/search", response_model=SearchResponse, tags=["仪表盘"])
def search_news(
    params: SearchRequest,
    mode: Optional[str] = Query(
        None,
        description=(
            "可选查询参数，优先级高于 body：exact|fuzzy|cluster|event_coref；"
            "默认 exact（boolean-v1：大写 AND/OR/NOT、括号、引号短语，空格为隐式 AND）"
        ),
    ),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    try:
        execution = execute_dashboard_search(
            params,
            query_mode=mode,
            user=user,
            db=db,
            dependencies=DashboardSearchDependencies(
                provider=search_dashboard_v2,
                clock=time.time,
            ),
        )
        profile = execution.profile
        print(
            "[search_profile] "
            f"mode={profile.mode} search_type={profile.search_type} "
            f"parse={profile.parse_ms:.0f}ms exec={profile.execute_ms:.0f}ms "
            f"total={profile.total_ms:.0f}ms",
            flush=True,
        )
        return execution.result
    except SearchModeError as exc:
        raise HTTPException(
            status_code=422,
            detail="mode 必须是 exact、fuzzy、cluster 或 event_coref",
        ) from exc
    except SearchSyntaxUnsupported as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "unsupported_search_syntax",
                "msg": exc.reason or "查询不符合 boolean-v1 的安全、有界语法",
                "unsupported": list(exc.features),
                "query_language": QUERY_LANGUAGE_VERSION,
                "query_field": exc.query_field,
                "position": exc.position,
                "limits": dict(QUERY_LIMITS),
            },
        ) from exc
    except SearchTimeFilterError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_search_time_semantics",
                "msg": str(exc),
            },
        ) from exc
    except SearchFilterUnsupported as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "unsupported_search_filter",
                "msg": str(exc),
                "unsupported": list(exc.fields),
            },
        ) from exc
    except SearchDeadlineExceeded as exc:
        raise HTTPException(
            status_code=504,
            detail="搜索已达到 6 秒上限，请缩短时间范围或增加限定词",
        ) from exc
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"搜索失败: {str(e)}")


# ===================================================================
# Explicit authenticated search snapshots (query contract + IDs only)
# ===================================================================
@router.post("/api/search-snapshots", status_code=201, tags=["search-snapshots"])
def capture_search_snapshot(
    body: CaptureSearchSnapshotRequest,
    user: dict[str, Any] = Depends(get_current_user_required),
):
    try:
        return _snapshot_ledger().capture(
            actor_id=_snapshot_actor_id(user),
            receipt=body.receipt,
            expected_previous_snapshot_id=body.expected_previous_snapshot_id,
        )
    except Exception as exc:
        _raise_snapshot_error(exc)


@router.get("/api/search-snapshots", tags=["search-snapshots"])
def list_search_snapshots(
    limit: int = Query(default=100, ge=1, le=100),
    user: dict[str, Any] = Depends(get_current_user_required),
):
    try:
        return _snapshot_ledger().list(
            _snapshot_actor_id(user),
            limit=limit,
        )
    except Exception as exc:
        _raise_snapshot_error(exc)


@router.get(
    "/api/search-snapshots/{snapshot_id}/replay",
    tags=["search-snapshots"],
)
def replay_search_snapshot(
    snapshot_id: str,
    user: dict[str, Any] = Depends(get_current_user_required),
):
    try:
        return _snapshot_ledger().replay(
            _snapshot_actor_id(user),
            snapshot_id,
        )
    except Exception as exc:
        _raise_snapshot_error(exc)


@router.get("/api/search-snapshots/{snapshot_id}", tags=["search-snapshots"])
def get_search_snapshot(
    snapshot_id: str,
    user: dict[str, Any] = Depends(get_current_user_required),
):
    try:
        return _snapshot_ledger().get(
            _snapshot_actor_id(user),
            snapshot_id,
        )
    except Exception as exc:
        _raise_snapshot_error(exc)


# ===================================================================
# POST /api/dashboard/search/clustered 簇格式搜索
# ===================================================================
@router.post("/api/dashboard/search/clustered", response_model=ClusteredSearchResponse, tags=["仪表盘"])
def search_news_clustered(
    params: SearchRequest,
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """
    按"簇格式"返回 cluster 模式结果（每个簇包含若干条新闻）。
    不改变原有 /api/dashboard/search?mode=cluster 的契约，额外提供一个聚类分组视图。
    """
    try:
        return execute_clustered_search(
            params,
            user=user,
            db=db,
            settings=ClusterSearchSettings(
                centroid_top_k=int_setting(
                    "CLUSTER_CENTROID_TOP_K",
                    8,
                    minimum=1,
                ),
                news_per_cluster=int_setting(
                    "CLUSTER_NEWS_PER_CLUSTER",
                    5,
                    minimum=1,
                ),
            ),
            dependencies=ClusterSearchDependencies(
                build_query=_build_semantic_query_text,
                encode_query=_encode_query_bge_m3,
                get_store=_get_milvus_store,
                resolve_language=resolve_country_to_language,
                fetch_rows=_fetch_news_rows_by_ids_ordered,
                passes_filters=_passes_vector_mode_filters,
                rows_to_items=_rows_to_news_items,
                fallback_enabled=_vector_fallback_exact_enabled,
                execute_exact=_execute_search_exact,
                clock=time.time,
            ),
        )
    except SearchQueryRequired as exc:
        raise HTTPException(status_code=400, detail="clustered 需要非空查询文本") from exc
    except SearchSyntaxUnsupported as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "unsupported_search_syntax",
                "msg": exc.reason or "独立 clustered 向量端点无法忠实执行该查询语法",
                "unsupported": list(exc.features),
                "query_language": QUERY_LANGUAGE_VERSION,
                "query_field": exc.query_field,
                "position": exc.position,
                "limits": dict(QUERY_LIMITS),
            },
        ) from exc
    except SearchTimeFilterError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_search_time_semantics", "msg": str(exc)},
        ) from exc
    except SearchFilterUnsupported as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "unsupported_search_filter",
                "msg": str(exc),
                "unsupported": list(exc.fields),
            },
        ) from exc
    except SearchDependencyUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Milvus 质心或 BGE-M3 不可用: {exc.__cause__ or exc}",
        ) from exc


def _get_milvus_store():
    from agentic_rag.db.milvus_store import get_milvus_store

    return get_milvus_store()


# ===================================================================
# POST /api/dashboard/search/v11-clusters V11 层级簇搜索
# ===================================================================
@router.post("/api/dashboard/search/v11-clusters", response_model=V11ClusterSearchResponse, tags=["仪表盘"])
def search_v11_clusters(
    req: V11ClusterSearchRequest,
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """在 v11 管线表的指定层级按关键词搜索（macro / micro / cluster）。"""
    if not req.keyword.strip():
        return V11ClusterSearchResponse(page=req.page, page_size=req.page_size)
    try:
        return _search_v11_clusters(db, req)
    except V11SearchContractError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ===================================================================
# GET /api/dashboard/search/v11-clusters/{item_id}/children 展开子级
# ===================================================================
@router.get("/api/dashboard/search/v11-clusters/{item_id}/children", tags=["仪表盘"])
def get_v11_cluster_children(
    item_id: str,
    level: str = Query(..., description="parent level: l3|l2|l1|macro|micro|cluster"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """展开指定簇的下一级子项。"""
    try:
        return _expand_v11_cluster_children(db, item_id, level, page, page_size)
    except V11SearchContractError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
