from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator
from typing import List, Optional, Dict, Any, Tuple, Literal, Union
from datetime import datetime


# 基础字段响应
class FieldResponse(BaseModel):
    id: int
    value: str
    model_config = ConfigDict(from_attributes=True)


class PaginatedFieldResponse(BaseModel):
    data: List[FieldResponse]
    total: int
    page: int
    size: int
    pages: int
    has_next: bool
    has_prev: bool
    model_config = ConfigDict(from_attributes=True)


# 统计响应模型
class StatsResponse(BaseModel):
    total_news: int
    total_languages: int  # 新增：语言总数
    language_stats: List[Dict[str, Any]]


# 搜索请求参数
class SearchRequest(BaseModel):
    _requested_time_field: Optional[str] = PrivateAttr(default=None)
    keyword: Optional[str] = None
    topic: Optional[str] = None
    must_include: Optional[str] = None
    any_include: Optional[str] = None
    need_exclude: Optional[str] = None
    publish_time: Optional[str] = Field(
        default=None,
        max_length=16,
        description="相对时间窗口；字段由 time_field 明确指定，不代表采集时间或更新时间",
    )
    start_time: Optional[str] = Field(
        default=None,
        max_length=64,
        description="ISO 8601 起始时间；新闻按发布日期，事件按起止区间重叠判断",
    )
    end_time: Optional[str] = Field(
        default=None,
        max_length=64,
        description="ISO 8601 结束时间；新闻按发布日期，事件按起止区间重叠判断",
    )
    time_field: Literal["auto", "published_at", "event_time"] = Field(
        default="auto",
        description="news 仅支持 published_at；l1/l2/l3 仅支持 event_time；采集/更新时间尚不可筛选",
    )
    hit_location: Optional[str] = Field(
        default="全文",
        description=(
            '关键词匹配范围："标题"（news.title）、"正文"（news.body）；'
            '兼容值“全文”和“摘要”当前均为标题优先，不代表独立全文/摘要索引'
        ),
    )
    data_source: Optional[str] = None
    country: Optional[str] = Field(
        default=None,
        description=(
            "保留字段；来源国、受众国、事件国和提及国尚未形成可查询维度，"
            "当前搜索会明确拒绝非空值"
        ),
    )
    language: Optional[str] = Field(
        default=None,
        description="显式新闻语言筛选；不会从 country 或 location 推断",
    )
    site: Optional[str] = None
    page: int = 1
    page_size: int = Field(default=10, ge=1, le=100)
    sort_by: Optional[str] = Field(
        default=None,
        max_length=32,
        description=(
            "新闻排序只接受 similarity、published_at 或兼容旧名 pub_time；"
            "歧义名称 time 被拒绝，层级结果不接受调用方排序"
        ),
    )
    sort_order: Optional[str] = Field(default="desc", max_length=8)
    mode: Literal["exact", "fuzzy", "cluster", "event_coref"] = Field(
        default="exact",
        description=(
            "查询语言为有界 boolean-v1：支持大写 AND/OR/NOT、括号、引号短语和隐式 AND；"
            "exact=按 AST 匹配，稳定实体别名仅在叶节点内 OR；"
            "fuzzy=保留 AST 并在叶节点内扩展主题别名，不等同向量相似度；"
            "cluster=簇树展示；event_coref=事件共核簇"
        ),
    )
    search_type: Literal["news", "l1", "l2", "l3"] = Field(
        default="news",
        description="news=新闻表；l1=L1事件聚类；l2=L2走势链；l3=L3大事件",
    )
    cluster_scope: bool = Field(
        default=False,
        description="仅搜索有聚类数据（news_ai_analysis）的文章",
    )
    favorite_scope_topic: Optional[str] = Field(
        default=None,
        description="数据搜索「主题」名称。传入时 is_favorited/is_warned 仅匹配该主题下的收藏或预警；不传则匹配任意主题",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "keyword": "Philippine Independence",
                    "must_include": "Jeddah Consulate",
                    "any_include": "reception flag-raising",
                    "need_exclude": "spam",
                    "hit_location": "全文",
                    "publish_time": "近一月",
                    "language": "en",
                    "data_source": "dfa.gov.ph",
                    "page": 1,
                    "page_size": 10,
                    "sort_by": "published_at",
                    "sort_order": "desc",
                }
            ]
        }
    }


# 新闻项响应
class NewsResultTimeSemantics(BaseModel):
    """Search-result timestamps whose meanings must never be interchanged."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["search-result-time-semantics-v1"] = (
        "search-result-time-semantics-v1"
    )
    published_at: Optional[datetime] = Field(
        default=None,
        description="新闻来源记录的发布日期；缺失保持 null",
    )
    event_time_start: Optional[datetime] = Field(
        default=None,
        description="事件发生区间起点；新闻列表当前没有该证据，缺失保持 null",
    )
    event_time_end: Optional[datetime] = Field(
        default=None,
        description="事件发生区间终点；新闻列表当前没有该证据，缺失保持 null",
    )
    collected_at: Optional[datetime] = Field(
        default=None,
        description="采集时间；当前搜索 schema 未登记，缺失保持 null",
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        description="新闻记录更新时间；当前搜索 schema 未登记，缺失保持 null",
    )
    legacy_pub_time_status: Literal[
        "legacy_alias_of_published_at_value_unverified"
    ] = (
        "legacy_alias_of_published_at_value_unverified"
    )
    legacy_created_at_status: Literal["legacy_unverified_not_used"] = (
        "legacy_unverified_not_used"
    )

    @model_validator(mode="after")
    def validate_event_interval(self) -> "NewsResultTimeSemantics":
        if self.event_time_start is None or self.event_time_end is None:
            return self
        try:
            inverted = self.event_time_start > self.event_time_end
        except TypeError as exc:
            raise ValueError("event_time bounds must use compatible timezones") from exc
        if inverted:
            raise ValueError("event_time_start must not be after event_time_end")
        return self


class SearchHitSpan(BaseModel):
    """Plain-text offsets into one returned display field."""

    model_config = ConfigDict(extra="forbid")

    field: Literal["title", "abstract"]
    start: int = Field(strict=True, ge=0, le=20000)
    end: int = Field(strict=True, ge=1, le=20000)

    @model_validator(mode="after")
    def validate_non_empty_span(self) -> "SearchHitSpan":
        if self.end <= self.start:
            raise ValueError("search hit span must be non-empty")
        return self


class SearchHitDisclosure(BaseModel):
    """Display-only hit offsets; never a relevance or document-match claim."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["search-hit-display-v1"] = "search-hit-display-v1"
    status: Literal["available", "no_display_span", "unavailable"] = "unavailable"
    offset_encoding: Literal["unicode_code_points"] = "unicode_code_points"
    coverage: Literal["positive_literal_terms_in_returned_display_only"] = (
        "positive_literal_terms_in_returned_display_only"
    )
    effective_search_fields: List[Literal["news.title", "news.body"]] = Field(
        default_factory=list,
        max_length=2,
    )
    alias_span_state: Literal["not_available"] = "not_available"
    relevance_score_state: Literal["not_available"] = "not_available"
    document_match_state: Literal["not_asserted"] = "not_asserted"
    reason_code: Literal[
        "DISPLAY_LITERAL_MATCHES_FOUND",
        "NO_LITERAL_SPAN_IN_RETURNED_DISPLAY_TEXT",
        "NOT_A_SEARCH_RESPONSE",
        "SEARCH_TERMS_NOT_AVAILABLE",
    ] = "NOT_A_SEARCH_RESPONSE"
    spans: List[SearchHitSpan] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_state(self) -> "SearchHitDisclosure":
        if self.status == "available":
            if not self.spans or self.reason_code != "DISPLAY_LITERAL_MATCHES_FOUND":
                raise ValueError("available search hit requires display spans")
        elif self.spans:
            raise ValueError("unavailable search hit cannot contain spans")
        if self.status == "no_display_span" and self.reason_code != (
            "NO_LITERAL_SPAN_IN_RETURNED_DISPLAY_TEXT"
        ):
            raise ValueError("no-display search hit reason mismatch")
        if self.status == "unavailable" and self.reason_code not in {
            "NOT_A_SEARCH_RESPONSE",
            "SEARCH_TERMS_NOT_AVAILABLE",
        }:
            raise ValueError("unavailable search hit reason mismatch")
        return self


class NewsItem(BaseModel):
    id: int
    title: str
    abstract: str = ""
    body: Optional[str] = None
    pub_time: Optional[datetime] = Field(
        default=None,
        description=(
            "legacy 兼容字段；服务端仅把 public.news.published_at 原值复制到此处，"
            "新调用方必须读取 time_semantics.published_at"
        ),
    )
    request_url: Optional[str] = None
    language_id: Optional[str] = None
    created_at: Optional[datetime] = Field(
        default=None,
        description="legacy/unverified 兼容字段；搜索响应不使用它推断采集或更新时间",
    )
    time_semantics: NewsResultTimeSemantics = Field(
        default_factory=NewsResultTimeSemantics,
        description="发布日期、事件时间、采集时间和更新时间的显式分离契约",
    )
    source: Optional[str] = None
    source_country: Optional[str] = Field(
        default=None,
        description="媒体来源画像显式值；未经权威地理验证，且不代表事件国或受众国",
    )
    source_region: Optional[str] = Field(
        default=None,
        description="媒体来源画像显式地区；不推断为事件地点",
    )
    news_region: Optional[str] = Field(
        default=None,
        description="新闻记录显式地区；语义未映射为四维国家字段",
    )
    location: Optional[str] = Field(
        default=None,
        description="仅保留显式 legacy location；缺失时为 null，不从来源、地区或语言推断",
    )
    cluster_title: Optional[str] = None
    cluster_article_count: Optional[int] = None
    value_tag: Optional[str] = None
    is_first_release: bool = False
    is_favorited: bool = False
    is_warned: bool = False
    search_hit: SearchHitDisclosure = Field(default_factory=SearchHitDisclosure)

    # --- 事件共核簇 ---
    event_coref_cluster_id: Optional[str] = None

    # --- 新增翻译相关字段 ---
    has_translation: bool = False
    trans_title: Optional[str] = None
    trans_abstract: Optional[str] = None
    trans_body: Optional[str] = None
    is_translated: Optional[bool] = None

    @model_validator(mode="after")
    def validate_legacy_publication_alias(self) -> "NewsItem":
        explicit = self.time_semantics.published_at
        if self.pub_time is not None:
            if explicit is None:
                raise ValueError(
                    "legacy pub_time cannot supply missing time_semantics.published_at"
                )
            if self.pub_time != explicit:
                raise ValueError("legacy pub_time contradicts time_semantics.published_at")
        return self

    @model_validator(mode="after")
    def validate_search_hit_offsets(self) -> "NewsItem":
        previous: dict[str, int] = {"title": 0, "abstract": 0}
        field_order = {"title": 0, "abstract": 1}
        last_key = (-1, -1)
        for span in self.search_hit.spans:
            text_value = self.title if span.field == "title" else self.abstract
            if span.end > len(text_value):
                raise ValueError("search hit span exceeds returned display text")
            if span.start < previous[span.field]:
                raise ValueError("search hit spans overlap")
            key = (field_order[span.field], span.start)
            if key < last_key:
                raise ValueError("search hit spans must use stable field order")
            if span.field == "title" and "news.title" not in (
                self.search_hit.effective_search_fields
            ):
                raise ValueError("search hit title span contradicts effective fields")
            if span.field == "abstract" and "news.body" not in (
                self.search_hit.effective_search_fields
            ):
                raise ValueError("search hit abstract span contradicts effective fields")
            previous[span.field] = span.end
            last_key = key
        return self

    model_config = ConfigDict(from_attributes=True,
                              json_encoders={datetime: lambda v: v.strftime('%Y-%m-%d %H:%M:%S') if v else None})


# 搜索响应
class ClusterTreeNews(BaseModel):
    id: int
    title: str = ""
    pub_time: Optional[datetime] = Field(
        default=None,
        description="legacy 兼容别名；新调用方读取 time_semantics.published_at",
    )
    time_semantics: NewsResultTimeSemantics = Field(
        default_factory=NewsResultTimeSemantics
    )

    @model_validator(mode="after")
    def validate_legacy_publication_alias(self) -> "ClusterTreeNews":
        explicit = self.time_semantics.published_at
        if self.pub_time is not None:
            if explicit is None:
                raise ValueError(
                    "legacy pub_time cannot supply missing time_semantics.published_at"
                )
            if self.pub_time != explicit:
                raise ValueError("legacy pub_time contradicts time_semantics.published_at")
        return self

class ClusterTreeMicro(BaseModel):
    cluster_id: Union[int, str]
    title: str = ""
    event_type: str = ""
    initiator: str = ""
    target: str = ""
    dominant_trigger: str = ""
    cluster_quality: str = ""
    news_count: int = 0
    news: List[ClusterTreeNews] = []

class ClusterTreeMacro(BaseModel):
    story_id: Union[int, str]
    title: str = ""
    cluster_count: int = 0
    news_count: int = 0
    clusters: List[ClusterTreeMicro] = []


class SearchEntityExpansion(BaseModel):
    query_field: str
    entity_id: str
    entity_type: str
    canonical_names: Dict[str, str]
    matched_alias: str
    expanded_aliases: List[str]
    expanded_alias_details: List[Dict[str, str]] = Field(default_factory=list)
    catalog_version: str
    review_status: Literal["approved", "review_required"] = "review_required"
    review_note: str = ""
    matched_alias_status: Literal["active", "context_dependent", "review_required"] = "active"
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    reviewed_at: Optional[str] = None
    reviewed_by: Optional[str] = None
    review_evidence: Optional[str] = None


class SearchExplainStage(BaseModel):
    stage: Literal["parse", "entity_resolution", "retrieval", "relaxation"]
    status: Literal["executed", "not_run", "not_available"]
    matched_count: Optional[int] = None
    count_semantics: Optional[str] = None
    detail: str


class SearchTimeSemantics(BaseModel):
    requested_field: str
    applied_field: str
    label: str
    predicate: str
    timezone_note: str
    unavailable_fields: List[str]


class SearchQueryExplain(BaseModel):
    version: str = "2.0"
    query_language: str = "boolean-v1"
    raw_query: str
    query_ast: Dict[str, Any]
    expanded_query_ast: Dict[str, Any]
    execution_expression: str
    limits: Dict[str, int]
    normalized_terms: List[str]
    expanded_terms: List[str]
    explicit_phrase: bool
    requested_mode: str
    effective_mode: str
    mode_semantics: str
    search_type: str
    requested_hit_location: str
    effective_search_fields: List[str]
    hit_location_note: str
    alias_catalog_version: str
    entity_expansions: List[SearchEntityExpansion] = Field(default_factory=list)
    applied_filters: List[Dict[str, Any]] = Field(default_factory=list)
    time: SearchTimeSemantics
    stages: List[SearchExplainStage]
    relaxation_suggestions: List[str] = Field(default_factory=list)
    automatic_relaxation: bool = False
    unsupported_syntax: List[str] = Field(default_factory=list)


class SearchResultCoverage(BaseModel):
    status: Literal["available", "partial", "unavailable"]
    scope: Literal["returned_page"] = "returned_page"
    result_time_field: str
    cutoff: Optional[str] = None
    coverage_start: Optional[str] = None
    coverage_end: Optional[str] = None
    timed_result_count: int = Field(ge=0, le=100)
    returned_result_count: int = Field(ge=0, le=100)
    note: str


class SearchQueryReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["search-query-receipt-v1"] = "search-query-receipt-v1"
    receipt_kind: Literal["execution_receipt"] = "execution_receipt"
    method_version: str
    receipt_id: str
    stable_execution_key: str
    receipt_sha256: str
    normalized_contract: Dict[str, Any]
    normalized_contract_sha256: str
    entity_catalog_version: str
    entity_catalog_review_status: Literal["approved", "review_required"]
    time_field: Dict[str, str]
    applied_filters: List[Dict[str, Any]] = Field(default_factory=list)
    result_id_namespace: Literal["news", "l1_event", "l2_trend", "l3_macro", "none"]
    ordered_returned_ids: List[str] = Field(default_factory=list, max_length=100)
    ordered_returned_ids_sha256: str
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    result_coverage: SearchResultCoverage
    snapshot_status: Literal["not_frozen"] = "not_frozen"
    frozen_data_snapshot_id: None = None
    receipt_note: str


class SearchResponse(BaseModel):
    data: List[NewsItem]
    total: int
    page: int
    page_size: int
    total_pages: int
    has_next: bool
    has_prev: bool
    query_time_ms: float
    cluster_tree: Optional[List[ClusterTreeMacro]] = None
    event_coref_clusters: Optional[List["EventCorefClusterInfo"]] = None
    micro_story_items: Optional[List["MicroStoryItem"]] = None
    macro_event_items: Optional[List["MacroEventItem"]] = None
    query_explain: Optional[SearchQueryExplain] = None
    query_receipt: Optional[SearchQueryReceipt] = None
    model_config = ConfigDict(from_attributes=True)


class EventCorefClusterInfo(BaseModel):
    cluster_id: str
    article_count: int
    event_type: Optional[str] = ""
    initiator: Optional[str] = ""
    target: Optional[str] = ""
    dominant_trigger: Optional[str] = ""
    cluster_quality: Optional[str] = ""
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    articles: List[NewsItem] = []


class MicroStoryItem(BaseModel):
    '''L1 事件直接搜索结果项。'''
    id: Union[int, str]
    title: str = ""
    event_type: Optional[str] = None
    initiator: Optional[str] = None
    target: Optional[str] = None
    article_count: int = 0
    cluster_count: int = 0
    model_config = ConfigDict(from_attributes=True)


class MacroEventItem(BaseModel):
    '''L2走势链 / L3大事件直接搜索结果项。'''
    id: Union[int, str]
    title: str = ""
    initiator: Optional[str] = None
    target: Optional[str] = None
    article_count: int = 0
    story_count: int = 0
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    chain_quality: Optional[str] = None
    quality_score: Optional[float] = None
    summary: Optional[str] = None
    level: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


# 按簇返回（cluster 语义检索的"簇格式"结果）
class ClusterGroup(BaseModel):
    cluster_id: int
    score: float
    items: List[NewsItem]


class ClusteredSearchResponse(BaseModel):
    query: str
    clusters: List[ClusterGroup]
    query_time_ms: float
    effective_strategy: Literal["clustered_vector", "exact_fallback"] = "clustered_vector"
    fallback_applied: bool = False
    model_config = ConfigDict(from_attributes=True)


# ================== V11 层级簇检索（三层次：macro → micro → cluster → news）==================

class V11ClusterItem(BaseModel):
    """层级簇搜索结果中的一个条目（macro / micro / cluster 共用）。
    内嵌 children 实现 macro → micro → cluster → news 的树形结构。
    cluster 层级以下为 news（不嵌套 V11ClusterItem，由 {id}/children 懒加载）。"""
    id: Union[int, str]
    title: str = ""
    level: str = "macro"      # "macro" | "micro" | "cluster"
    article_count: int = 0
    children_count: int = 0   # story_count / cluster_count 等
    children: List["V11ClusterItem"] = []
    initiator: Optional[str] = None
    target: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    event_type: Optional[str] = None
    dominant_trigger: Optional[str] = None


class V11ClusterSearchRequest(BaseModel):
    keyword: str = ""
    level: Literal["macro", "micro", "cluster"] = "macro"
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class V11ClusterSearchResponse(BaseModel):
    items: List[V11ClusterItem] = []
    total: int = 0
    page: int = 1
    page_size: int = 20
    total_pages: int = 0
    has_next: bool = False
    has_prev: bool = False


class V11ClusterChildrenResponse(BaseModel):
    """展开某簇后的子级列表。"""
    items: List[Union[V11ClusterItem, Dict[str, Any]]] = []
    total: int = 0
    page: int = 1
    page_size: int = 20
    parent_level: str = ""
    child_level: str = ""


# 分页列表响应（与 search 的列表部分对齐，无 query_time_ms）
class NewsListResponse(BaseModel):
    data: List[NewsItem]
    total: int
    page: int
    page_size: int
    total_pages: int
    has_next: bool
    has_prev: bool
    model_config = ConfigDict(from_attributes=True)


class NewsBulkByIdsResponse(BaseModel):
    """按 id 列表拉取新闻（用于报告中心/收藏补全，不受分页列表前 N 条限制）。"""

    data: List[NewsItem]
    model_config = ConfigDict(from_attributes=True)


class ArticleReaderResponse(BaseModel):
    """单条新闻阅读面板：正文 + 可选 NLP 分析行（news_analysis）。"""

    news: NewsItem
    analysis: Optional[Dict[str, Any]] = None
