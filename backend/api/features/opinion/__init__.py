"""Public API for opinion contracts, analytics, cache, and trend reads."""

from api.features.opinion.analytics import (
    article_decay_weight,
    classify_index_label,
    coerce_date,
    compute_weighted_stance_trend,
    dimension_conditions,
    finite_float,
    format_signed,
    sentiment_matches,
    trend_values_for_rows,
)
from api.features.opinion.application import OpinionTrendService, build_trend_content
from api.features.opinion.cache import (
    RESPONSE_CACHE_STORAGE,
    InMemoryResponseCache,
    clear_response_cache,
    response_cache_get,
    response_cache_key,
    response_cache_set,
)
from api.features.opinion.claims import (
    OPINION_CLAIM_MAX_CLAIMS,
    OPINION_CLAIM_SCHEMA_VERSION,
    OpinionDerivedClaim,
    assure_opinion_overview_claims,
)
from api.features.opinion.constants import (
    DECAY_ALPHA,
    DECAY_MAX_LAG,
    DECAY_TAU_BASE,
    DECAY_TAU_SCALE,
    METHOD_VERSION,
)
from api.features.opinion.contracts import (
    OpinionFeedbackPayload,
    OpinionRefreshPayload,
    OpinionTrendQuery,
)
from api.features.opinion.feedback_governance import (
    FeedbackTrainingUseBlocked,
    build_feedback_governance_receipt,
    require_feedback_training_approval,
)
from api.features.opinion.health import probe_opinion_health
from api.features.opinion.queries import (
    EFFECTIVE_STANCE_EXPR,
    FEEDBACK_VISIBLE_EXPR,
    LATEST_FEEDBACK_CTE,
    VALID_SCORE_EXPR,
)
from api.features.opinion.repository import (
    OpinionTrendRepository,
    SqlAlchemyOpinionTrendRepository,
    current_db_date,
    latest_score_date,
)
from api.features.opinion.semantics import (
    OPINION_SEMANTIC_CONTRACT_VERSION,
    OPINION_SEMANTIC_SCHEMA_VERSION,
    apply_opinion_semantic_contract,
    build_opinion_semantic_dimensions,
    opinion_semantic_method_card,
)
from api.features.opinion.trust import (
    OPINION_FRESHNESS_MAX_AGE_DAYS,
    OPINION_MIN_ARTICLES,
    OPINION_MIN_SOURCES,
    OPINION_MODEL_VERSION,
    OPINION_SOURCE_ID,
    OPINION_TRUST_SCHEMA_VERSION,
    evaluate_opinion_trust,
    sanitize_opinion_payload,
    suppress_composite_trend,
)

__all__ = (
    "DECAY_ALPHA",
    "DECAY_MAX_LAG",
    "DECAY_TAU_BASE",
    "DECAY_TAU_SCALE",
    "EFFECTIVE_STANCE_EXPR",
    "FEEDBACK_VISIBLE_EXPR",
    "FeedbackTrainingUseBlocked",
    "InMemoryResponseCache",
    "LATEST_FEEDBACK_CTE",
    "METHOD_VERSION",
    "OPINION_CLAIM_MAX_CLAIMS",
    "OPINION_CLAIM_SCHEMA_VERSION",
    "VALID_SCORE_EXPR",
    "OpinionDerivedClaim",
    "OpinionFeedbackPayload",
    "OpinionRefreshPayload",
    "OpinionTrendQuery",
    "OpinionTrendRepository",
    "OpinionTrendService",
    "OPINION_FRESHNESS_MAX_AGE_DAYS",
    "OPINION_MIN_ARTICLES",
    "OPINION_MIN_SOURCES",
    "OPINION_MODEL_VERSION",
    "OPINION_SEMANTIC_CONTRACT_VERSION",
    "OPINION_SEMANTIC_SCHEMA_VERSION",
    "OPINION_SOURCE_ID",
    "OPINION_TRUST_SCHEMA_VERSION",
    "RESPONSE_CACHE_STORAGE",
    "SqlAlchemyOpinionTrendRepository",
    "article_decay_weight",
    "apply_opinion_semantic_contract",
    "assure_opinion_overview_claims",
    "build_trend_content",
    "build_feedback_governance_receipt",
    "build_opinion_semantic_dimensions",
    "classify_index_label",
    "clear_response_cache",
    "coerce_date",
    "compute_weighted_stance_trend",
    "current_db_date",
    "dimension_conditions",
    "evaluate_opinion_trust",
    "finite_float",
    "format_signed",
    "latest_score_date",
    "probe_opinion_health",
    "opinion_semantic_method_card",
    "response_cache_get",
    "response_cache_key",
    "response_cache_set",
    "require_feedback_training_approval",
    "sanitize_opinion_payload",
    "sentiment_matches",
    "suppress_composite_trend",
    "trend_values_for_rows",
)
