from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# ── 冷冻常量：在各流水线模块中作为 fallback 默认值使用 ──────────────────────────
class FrozenDefaults:
    """模块级默认值；环境变量（大写）优先。"""

    # 涉华闸值
    CHINA_GATE_THRESHOLD: float = 0.40
    MILVUS_SYNC_CHINA_ONLY: bool = True

    # 宏观聚类（stage5）
    STAGE5_MIN_SIM: float = 0.55
    STAGE5_MIN_ENTITY_OVERLAP: float = 0.10

    # 增量路由
    ROUTE_SIMILARITY_THRESHOLD: float = 0.65

    # BGE 编码 / 去重
    BGE_ENCODE_BATCH_SIZE: int = 24
    LSH_JACCARD: float = 0.85

    # 3D 拓扑可视化布局常量
    MACRO_SPHERE_R: float = 5.0
    CLUSTER_ARM: float = 2.0
    MACRO_VAL_BASE: int = 8
    MICRO_VAL_BASE: int = 3
    NEWS_JITTER_BASE: float = 1.0
    GHOST_SPHERE_R: float = 4.0
    NEWS_VAL: int = 2


# ── 多原型涉华锚文本 ──────────────────────────────────────────────────────
def get_bge_china_anchor_texts() -> list[str]:
    """返回 6 维涉华语义原型锚文本，每句代表一个涉华维度。

    调用方对每句编码后分别计算余弦相似度，
    再按各维度权重加权得到 ``china_related_index``。

    注意：每个锚文本应尽量具体（含具体实体/事件/数字），
    避免宽泛的话题描述，以提高 BGE-M3 的区分能力。
    """
    return [
        # 1. 中美战略竞争
        "美国对华加征关税并限制高端芯片出口，台海军事部署升级和科技脱钩加剧中美战略对抗",
        # 2. 中国外交与全球治理
        "中国主导一带一路基础设施投资，推动金砖扩员并在联合国提议全球治理体系改革",
        # 3. 中国经济社会
        "中国房地产行业债务危机拖累经济增长，人口老龄化加速与青年失业率攀升构成社会压力",
        # 4. 中国军事安全
        "解放军在南海扩建岛礁并部署反舰导弹，绕台军事演习的频率和规模均创历史新高",
        # 5. 中国人权与法治
        "联合国人权报告关注新疆维吾尔族处境，香港国安法实施后多名民主派人士被捕",
        # 6. 中国文化科技
        "华为突破美国技术封锁推出自主芯片，TikTok 在全球市场面临多国数据安全监管挑战",
    ]


def get_bge_china_negative_anchor() -> str:
    """负向对比锚文本：描述与中国无关的国际事件，用于基线扣除。

    任何新闻对正向锚的相似度若也被此基线捕获（即 cos(news,neg) 也高），
    说明该新闻只是泛泛的国际报道而非真正涉华。
    proto_dim_score = max(0, cos(news, pos_anchor) - cos(news, neg_anchor))
    """
    return (
        "印度与欧盟深化经贸合作并签署自由贸易协定，"
        "日本首相访问东南亚讨论地区安全架构，"
        "欧洲央行连续加息应对通胀压力，"
        "俄罗斯与乌克兰在顿巴斯地区持续交火"
    )


# ── 向后兼容：老代码仍可调用 get_bge_china_anchor_text() ────────────────────
def get_bge_china_anchor_text() -> str:
    """返回单一锚文本（兼容旧版调用方）。

    新版请改用 ``get_bge_china_anchor_texts()`` 获取 6 维原型。
    """
    return "与中国相关的新闻"


def get_stop_words_set() -> set[str]:
    """返回中英文停用词集合，用于 fast_entity_tagger 的 FlashText 过滤。"""
    return {
        "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
        "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
        "没有", "看", "好", "自己", "这", "他", "她", "它", "们",
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "in", "on", "at", "to", "for", "of", "with", "by", "from",
        "and", "or", "but", "not", "this", "that", "these", "those",
        "it", "its", "they", "them", "their", "we", "our", "you", "your",
    }


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    pg_host: str = Field(
        default="127.0.0.1",
        validation_alias=AliasChoices("POSTGRES_HOST", "PG_HOST"),
    )
    pg_port: int = Field(default=5432, validation_alias=AliasChoices("POSTGRES_PORT", "PG_PORT"))
    pg_user: str = Field(default="postgres", validation_alias=AliasChoices("POSTGRES_USER", "PG_USER"))
    pg_password: str = Field(
        default="postgres",
        validation_alias=AliasChoices("POSTGRES_PASSWORD", "PG_PASSWORD"),
    )
    pg_database: str = Field(
        default="news",
        validation_alias=AliasChoices("POSTGRES_DB", "PG_DATABASE"),
    )

    milvus_uri: str = Field(
        default="data/milvus_local.db",
        validation_alias=AliasChoices("GLOBEMIND_MILVUS_URI"),
        description="Milvus Lite .db path or server URI; use GLOBEMIND_MILVUS_URI (not MILVUS_URI) to avoid pymilvus env clash.",
    )

    sqlalchemy_pool_size: int = 10
    sqlalchemy_max_overflow: int = 20
    sqlalchemy_pool_timeout: int = 30
    sqlalchemy_pool_recycle: int = 3600

    ruleset_version: str = Field(default="globemind-rules-v0", validation_alias="RULESET_VERSION")
    degradation_mode: str = Field(
        default="normal",
        validation_alias="DEGRADATION_MODE",
        description="normal | degraded | offline",
    )
    fast_track_enabled: bool = Field(default=True, validation_alias="FAST_TRACK_ENABLED")
    slow_track_enrich_paused: bool = Field(default=False, validation_alias="SLOW_TRACK_ENRICH_PAUSED")
    slow_track_llm_paused: bool = Field(default=False, validation_alias="SLOW_TRACK_LLM_PAUSED")
    shadow_milvus_writes: bool = Field(default=False, validation_alias="SHADOW_MILVUS_WRITES")
    prefer_whitelist_sources_only: bool = Field(
        default=False,
        validation_alias="PREFER_WHITELIST_SOURCES_ONLY",
    )

    burst_watch_tokens: str = Field(
        default="taiwan,strait,sanctions,nuclear,鍗楁捣,鍙版咕,娴峰场",
        validation_alias="BURST_WATCH_TOKENS",
    )
    burst_window_minutes: int = Field(default=15, validation_alias="BURST_WINDOW_MINUTES")
    burst_zscore_threshold: float = Field(default=3.0, validation_alias="BURST_ZSCORE_THRESHOLD")
    burst_min_baseline_total: int = Field(default=5, validation_alias="BURST_MIN_BASELINE_TOTAL")

    near_dup_window_minutes: int = Field(default=15, validation_alias="NEAR_DUP_WINDOW_MINUTES")
    near_dup_hamming_max: int = Field(default=3, validation_alias="NEAR_DUP_HAMMING_MAX")
    near_dup_scan_limit: int = Field(default=4000, validation_alias="NEAR_DUP_SCAN_LIMIT")
    near_dup_neighbor_threshold: int = Field(default=8, validation_alias="NEAR_DUP_NEIGHBOR_THRESHOLD")

    cc_brief_enabled: bool = Field(default=False, validation_alias="CC_BRIEF_ENABLED")
    cc_brief_llm_url: str = Field(
        default="http://127.0.0.1:8004/v1/chat/completions",
        validation_alias="CC_BRIEF_LLM_URL",
    )
    cc_brief_model: str = Field(default="gpt-4o-mini", validation_alias="CC_BRIEF_MODEL")

    circuit_failure_threshold: int = Field(default=5, validation_alias="CIRCUIT_FAILURE_THRESHOLD")
    circuit_recovery_seconds: float = Field(default=30.0, validation_alias="CIRCUIT_RECOVERY_SECONDS")

    embedding_dim: int = Field(default=1024, validation_alias="EMBEDDING_DIM")
    phase1_merge_cosine_min: float = Field(default=0.62, validation_alias="PHASE1_MERGE_COSINE_MIN")
    phase1_time_penalty_per_day: float = Field(default=0.003, validation_alias="PHASE1_TIME_PENALTY_PER_DAY")
    phase1_alpha_base: float = Field(default=0.14, validation_alias="PHASE1_ALPHA_BASE")
    phase1_search_topk: int = Field(default=16, validation_alias="PHASE1_SEARCH_TOPK")
    phase1_split_min_members: int = Field(default=48, validation_alias="PHASE1_SPLIT_MIN_MEMBERS")
    phase1_split_dispersion_min: float = Field(default=0.38, validation_alias="PHASE1_SPLIT_DISPERSION_MIN")
    phase1_max_sample_vectors: int = Field(default=32, validation_alias="PHASE1_MAX_SAMPLE_VECTORS")

    m0_jaccard_min: float = Field(
        default=0.12,
        validation_alias="M0_JACCARD_MIN",
        description="M0 entity Jaccard floor; lower values admit more macro edges.",
    )
    m0_activity_half_window_hours: float = Field(
        default=252.0,
        validation_alias="M0_ACTIVITY_HALF_WINDOW_HOURS",
        description="Half-window (h) around last_article_at; 252h = ±10.5d → 21d diameter.",
    )
    m1_shadow_topk: int = Field(default=8, validation_alias="M1_SHADOW_TOPK")
    m1_shadow_weight_min: float = Field(default=0.52, validation_alias="M1_SHADOW_WEIGHT_MIN")
    m1_shadow_long_bridge_days: float = Field(default=90.0, validation_alias="M1_SHADOW_LONG_BRIDGE_DAYS")
    m1_shadow_wormhole_entity_min: float = Field(default=0.20, validation_alias="M1_SHADOW_WORMHOLE_ENTITY_MIN")
    m1_shadow_wormhole_sim_min: float = Field(default=0.70, validation_alias="M1_SHADOW_WORMHOLE_SIM_MIN")
    m1_shadow_secondary_min_shared_entities: int = Field(
        default=2,
        validation_alias="M1_SHADOW_SECONDARY_MIN_SHARED_ENTITIES",
    )
    m1_shadow_secondary_block_tier: int = Field(
        default=2,
        validation_alias="M1_SHADOW_SECONDARY_BLOCK_TIER",
        description="If both clusters are at or above this tier, secondary validation blocks the edge.",
    )
    m1_shadow_secondary_require_for_days: float = Field(
        default=60.0,
        validation_alias="M1_SHADOW_SECONDARY_REQUIRE_FOR_DAYS",
        description="Apply strict secondary validation when candidate time gap exceeds this many days.",
    )

    obsidian_vault_path: str = Field(
        default="data/obsidian_vault",
        validation_alias="OBSIDIAN_VAULT_PATH",
    )
    # Obsidian Watcher (Phase 4)
    obsidian_watcher_enabled: bool = Field(
        default=False,
        validation_alias="OBSIDIAN_WATCHER_ENABLED",
        description="Enable the Obsidian vault file watcher daemon.",
    )
    obsidian_watcher_poll_interval_s: int = Field(
        default=10,
        validation_alias="OBSIDIAN_WATCHER_POLL_INTERVAL_S",
        description="Polling interval in seconds for the vault watcher.",
    )
    obsidian_watcher_auto_create_mnl: bool = Field(
        default=True,
        validation_alias="OBSIDIAN_WATCHER_AUTO_CREATE_MNL",
        description="Auto-create MUST_NOT_LINK when analyst removes a Macro link.",
    )
    gateway_predict_rpm: int = Field(
        default=600,
        validation_alias="GATEWAY_PREDICT_RPM",
        description="Soft cap: max /predict attempts per minute per process (token bucket).",
    )
    dlq_enabled: bool = Field(default=True, validation_alias="DLQ_ENABLED")
    dlq_max_retries: int = Field(default=5, validation_alias="DLQ_MAX_RETRIES")
    embedding_http_url: str = Field(
        default="http://127.0.0.1:8001/v1/embed",
        validation_alias="EMBEDDING_HTTP_URL",
    )
    slow_track_vllm_base_url: str = Field(
        default="http://127.0.0.1:8004",
        validation_alias="SLOW_TRACK_VLLM_BASE_URL",
        description="vLLM OpenAI-compatible host (no path); used for /v1/chat/completions in slow-track enrich.",
    )
    slow_track_translate_model: str = Field(
        default="/root/data/models/Qwen2.5-7B-Instruct-AWQ",
        validation_alias="SLOW_TRACK_TRANSLATE_MODEL",
        description="Model id string passed to vLLM chat/completions (must match served model for prefix caching).",
    )

    shadow_inherit_cluster_id: bool = Field(
        default=False,
        validation_alias="SHADOW_INHERIT_CLUSTER_ID",
        description="When True, duplicate/shadow rows also copy canonical cluster_id from news_ai_analysis.",
    )
    slow_track_handoff_priority_boost: int = Field(
        default=100,
        validation_alias="SLOW_TRACK_HANDOFF_PRIORITY_BOOST",
        description="Higher priority values dequeue first (alert-boosted slow-track work).",
    )

    fast_track_micro_classifier_mode: str = Field(
        default="lexicon",
        validation_alias="FAST_TRACK_MICRO_CLASSIFIER_MODE",
        description="off | lexicon | onnx (onnx falls back to lexicon if ORT/model missing).",
    )
    fast_track_onnx_model_path: str = Field(
        default="",
        validation_alias="FAST_TRACK_ONNX_MODEL_PATH",
        description="Optional ONNX model path for fast-track CPU classification.",
    )
    fast_track_negative_sentiment_threshold: float = Field(
        default=-0.72,
        validation_alias="FAST_TRACK_NEGATIVE_SENTIMENT_THRESHOLD",
        description="At or below this lexicon/onnx sentiment score, emit a micro_classifier RuleHit.",
    )

    infra_circuit_failure_threshold: int = Field(
        default=5,
        validation_alias="INFRA_CIRCUIT_FAILURE_THRESHOLD",
    )
    infra_circuit_recovery_seconds: float = Field(
        default=30.0,
        validation_alias="INFRA_CIRCUIT_RECOVERY_SECONDS",
    )

    sla_handoff_pending_warn: int = Field(default=500, validation_alias="SLA_HANDOFF_PENDING_WARN")
    sla_handoff_pending_critical: int = Field(
        default=2000,
        validation_alias="SLA_HANDOFF_PENDING_CRITICAL",
    )
    sla_gpu_util_pct: float | None = Field(
        default=None,
        validation_alias="GLOBEMIND_GPU_UTIL_PCT",
        description="Optional external probe: GPU utilization 0-100 for SLA JSON only.",
    )
    backpressure_gpu_warn: float = Field(default=90.0, validation_alias="BACKPRESSURE_GPU_WARN")
    backpressure_gpu_critical: float = Field(default=97.0, validation_alias="BACKPRESSURE_GPU_CRITICAL")
    backpressure_dequeue_scale_warn: float = Field(
        default=0.5,
        validation_alias="BACKPRESSURE_DEQUEUE_SCALE_WARN",
        description="When pressure=warn, reduce worker batch/dequeue rate by this multiplier.",
    )
    backpressure_dequeue_scale_critical: float = Field(
        default=0.2,
        validation_alias="BACKPRESSURE_DEQUEUE_SCALE_CRITICAL",
        description="When pressure=critical, reduce worker batch/dequeue rate by this multiplier.",
    )
    backpressure_pause_on_critical: bool = Field(
        default=True,
        validation_alias="BACKPRESSURE_PAUSE_ON_CRITICAL",
        description="When critical pressure is detected, pause slow-track dequeue loop.",
    )
    phase2_queue_capacity: int = Field(
        default=2000,
        validation_alias="PHASE2_QUEUE_CAPACITY",
        description="Bounded in-memory queue capacity for Phase2 enrich tasks.",
    )
    phase2_wfq_weights: str = Field(
        default="high:5,normal:3,low:1",
        validation_alias="PHASE2_WFQ_WEIGHTS",
        description="WFQ weights by class, e.g. high:5,normal:3,low:1",
    )
    china_related_index_version: str = Field(
        default="v1",
        validation_alias="CHINA_RELATED_INDEX_VERSION",
        description="Schema tag for china_index semantics across APIs and docs.",
    )

    @property
    def unified_service_base_url(self) -> str:
        """HTTP origin for unified models (8001); derived from ``embedding_http_url`` host."""
        from urllib.parse import urlparse

        p = urlparse(self.embedding_http_url)
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}"
        return "http://127.0.0.1:8001"

    @property
    def pg_dsn(self) -> str:
        safe_password = quote_plus(self.pg_password)
        return (
            f"postgresql+psycopg2://{self.pg_user}:{safe_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_database}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def obsidian_vault_path() -> Path:
    """兼容 shared.config.settings.obsidian_vault_path 的函数签名，供 agentic_rag import。"""
    return Path(get_settings().obsidian_vault_path)
