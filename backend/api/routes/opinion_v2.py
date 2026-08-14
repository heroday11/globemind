"""China stance opinion APIs backed by the new Ground News L1/L1.5/L2 tables.

This module intentionally keeps the public `/api/opinion/...` paths used by
the existing sentiment-analysis page, but reads the crawler `news` database
instead of the stale legacy `globemind_news.news_ai_analysis` table.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from api.core.db import SQLALCHEMY_DATABASE_URL, SessionLocal
from api.core.environment import is_test_environment
from api.features.opinion import (
    EFFECTIVE_STANCE_EXPR,
    FEEDBACK_VISIBLE_EXPR,
    LATEST_FEEDBACK_CTE,
    METHOD_VERSION,
    RESPONSE_CACHE_STORAGE,
    VALID_SCORE_EXPR,
    OpinionFeedbackPayload,
    OpinionRefreshPayload,
    apply_opinion_semantic_contract,
    article_decay_weight,
    assure_opinion_overview_claims,
    build_feedback_governance_receipt,
    build_trend_content,
    classify_index_label,
    coerce_date,
    compute_weighted_stance_trend,
    current_db_date,
    dimension_conditions,
    format_signed,
    latest_score_date,
    response_cache_get,
    response_cache_key,
    response_cache_set,
    sanitize_opinion_payload,
    sentiment_matches,
    trend_values_for_rows,
)
from api.services.auth import get_current_admin_user, get_current_user_required

router = APIRouter()

DEFAULT_L1_RUN_ID = "fast_l1_v2"
DEFAULT_L15_RUN_ID = "fast_l15_v1"
DEFAULT_L2_RUN_ID = "fast_l2_v1"

# Compatibility aliases retained for callers and tests that patch route internals.
_RESP_CACHE = RESPONSE_CACHE_STORAGE
_cache_key = response_cache_key
_cache_get = response_cache_get
_cache_set = response_cache_set
_coerce_date = coerce_date
_article_decay_weight = article_decay_weight
_sentiment_match = sentiment_matches
_compute_weighted_stance_trend = compute_weighted_stance_trend
_dimension_conditions = dimension_conditions
_build_trend_content = build_trend_content
_classify_index_label = classify_index_label
_fmt_signed = format_signed
_current_db_date = current_db_date
_latest_score_date = latest_score_date
_trend_for_sql_rows = trend_values_for_rows


def _sanitize_opinion_response(
    content: dict[str, Any],
    db: Session,
    *,
    force_reason_codes: tuple[str, ...] = (),
    include_overview_claims: bool = False,
) -> dict[str, Any]:
    """Re-age cached trust and apply the same fail-closed policy on every read."""

    sanitized = sanitize_opinion_payload(
        content,
        current_date=_current_db_date(db),
        force_reason_codes=force_reason_codes,
    )
    claimed = (
        assure_opinion_overview_claims(sanitized)
        if include_overview_claims
        else sanitized
    )
    return apply_opinion_semantic_contract(claimed)


def _filtered_trust_snapshot(
    db: Session,
    *,
    days: int,
    sentiment_filter: str = "all",
    event_family: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    trend = _build_trend_content(
        db,
        days=max(7, min(3650, int(days))),
        china_min_score=0.4,
        sentiment_filter=sentiment_filter,
        event_family=event_family,
    )
    return trend.get("meta", {}).get("trust", {}), trend.get("meta", {})

CORE_CHINA_PATTERN = (
    r"(\m(china|chinese|chino|chinos|chinas|beijing|pekin|pekín|mainland china|prc)\M|中国|北京|中方|大陆|大陸)"
)
CHINA_PERIPHERY_PATTERN = (
    r"(\m(hong kong|hongkong|taiwan|xinjiang|tibet)\M|香港|台湾|臺灣|新疆|西藏)"
)
CHINA_PATTERN = (
    r"(\m(china|chinese|chino|chinos|chinas|beijing|pekin|pekín|hong kong|hongkong|taiwan|xinjiang|tibet|"
    r"mainland china|prc)\M|中国|北京|香港|台湾|臺灣|新疆|西藏|中方|大陆|大陸)"
)
BRAND_PATTERN = r"(south china morning post|china daily|chinadaily|global times|xinhua|cgtn)"
NEGATIVE_PATTERN = (
    r"\m(criticis(?:e|ed|es|ing)|criticiz(?:e|ed|es|ing)|accus(?:e|ed|es|ing|ation|ations)|"
    r"condemn(?:ed|s|ing)?|warn(?:ed|s|ing)?|concern(?:s|ed|ing)?|sanction(?:s|ed|ing)?|"
    r"tariff(?:s)?|crackdown(?:s)?|spy|spies|spying|espionage|threat(?:s|en(?:ed|s|ing)?|ening)?|"
    r"aggression|violation(?:s)?|human rights|security risk(?:s)?|overcapacity|dumping|coercion|"
    r"ban(?:s|ned|ning)?|restrict(?:s|ed|ing|ion|ions)?|probe(?:s|d)?|"
    r"investigat(?:e|ed|es|ing|ion|ions)|slam(?:s|med|ming)?|blame(?:s|d|ing)?|"
    r"censor(?:s|ed|ship)?|repression|genocide|forced labo(?:u)?r|debt trap|"
    r"military threat(?:s)?|export control(?:s)?|"
    r"cr[ií]tic(?:a|as|o|os|an|ar|ado|ada|aron|ando)?|acus(?:a|an|ar|ado|ada|aron|ando|aci[oó]n|aciones)|"
    r"conden(?:a|an|ar|ado|ada|aron|ando)?|sanci[oó]n(?:es)?|sancion(?:a|an|ar|ado|ada|aron|ando)?|"
    r"arancel(?:es)?|amenaz(?:a|as|an|ar|ado|ada|aron|ando)?|preocup(?:a|an|aci[oó]n|aciones|ado|ada)?|"
    r"restricci[oó]n(?:es)?|restring(?:e|en|ir|ido|ida)?|espionaje|riesgo(?:s)?)\M"
)
POSITIVE_PATTERN = (
    r"\m(prais(?:e|ed|es|ing)|laud(?:ed|s|ing)?|welcom(?:e|ed|es|ing)|"
    r"cooperat(?:e|ed|es|ing|ion)|agreement(?:s)?|partnership(?:s)?|boost(?:s|ed|ing)?|"
    r"growth|recover(?:y|ed|ing)?|innovation|success(?:ful|fully)?|record(?: high| growth)?|"
    r"breakthrough(?:s)?|support(?:s|ed|ing)?|investment(?:s)?|opportunit(?:y|ies)|"
    r"collaboration|consolidat(?:e|ed|es|ing)|expand(?:s|ed|ing)?|deepen(?:s|ed|ing)?|"
    r"strengthen(?:s|ed|ing)?|milestone(?:s)?|"
    r"cooperaci[oó]n|colaboraci[oó]n|convenio(?:s)?|acuerdo(?:s)?|alianza(?:s)?|"
    r"consolid(?:a|an|ar|ado|ada|aron|ando)|fortalec(?:e|en|er|ido|ida|imiento)|"
    r"relaci[oó]n(?:es)?|inversi[oó]n(?:es)?|crecimiento|oportunidad(?:es)?|"
    r"friendship|friendly|ties|mutual|warm welcome)\M"
)
NEGATIVE_CHINA_PROX_PATTERN = (
    rf"({CORE_CHINA_PATTERN}.{{0,160}}{NEGATIVE_PATTERN}|{NEGATIVE_PATTERN}.{{0,160}}{CORE_CHINA_PATTERN})"
)
POSITIVE_CHINA_PROX_PATTERN = (
    rf"({CORE_CHINA_PATTERN}.{{0,160}}{POSITIVE_PATTERN}|{POSITIVE_PATTERN}.{{0,160}}{CORE_CHINA_PATTERN})"
)
SUPPLY_TO_CHINA_PATTERN = (
    rf"(\m(export(?:s|ed|ing)?|shipment(?:s)?|supply|supplies|feed(?:s|ing)?|deliver(?:s|ed|ing)?)\M"
    rf".{{0,120}}{CORE_CHINA_PATTERN}|{CORE_CHINA_PATTERN}.{{0,120}}\m(mill(?:s)?|market|demand|imports?)\M)"
)
CHINA_RESILIENCE_PATTERN = (
    rf"\m(facing|despite|after|amid)\M.{{0,90}}"
    rf"\m(curb(?:s)?|sanction(?:s)?|restriction(?:s)?|export control(?:s)?)\M"
    rf".{{0,120}}{CORE_CHINA_PATTERN}.{{0,120}}"
    rf"\m(launch(?:es|ed|ing)?|build(?:s|ing)?|develop(?:s|ed|ing)?|unveil(?:s|ed|ing)?|boost(?:s|ed|ing)?)\M"
)
DEFENSIVE_CHINA_OPINION_PATTERN = (
    rf"\m(against|toward|towards)\M.{{0,40}}{CORE_CHINA_PATTERN}.{{0,80}}"
    rf"\m(farce|flawed|misguided|wrong|false|overblown)\M"
)
TITLE_RESTRICTION_PATTERN = (
    r"\m(curb(?:s)?|restrict(?:s|ed|ing|ion|ions)?|ban(?:s|ned|ning)?|sanction(?:s|ed|ing)?|"
    r"control(?:s|led|ling)?|block(?:s|ed|ing)?|align with us)\M"
)
INDEX_PAGE_PATTERN = r"(latest news|breaking news|news and updates|latest updates|headlines|live updates|news & analysis)"
SOURCE_SECTION_URL_PATTERN = (
    r"/(section|sections|category|categories|tag|tags|topic|topics|search)(/|$)"
    r"|/[st]-[0-9]+/?$"
    r"|/national/programmes/[A-Za-z0-9-]+/[0-9]{8}/?$"
)
SCMP_ARTICLE_URL_PATTERN = r"/article/[0-9]+"

_LAST_BACKFILL: dict[str, float] = {}
_REFRESH_LOCK = threading.Lock()
_SCHEMA_READY = False
_VALID_FEEDBACK_CORRECTIONS = {"irrelevant", "too_positive", "too_negative", "correct"}
_MAX_FEEDBACK_JSON_BYTES = 4 * 1024
_MAX_FEEDBACK_JSON_DEPTH = 4
_MAX_FEEDBACK_JSON_NODES = 32
_FEEDBACK_NO_STORE_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
}
_OPINION_WRITE_COLUMNS = {
    "china_opinion_article_scores": {
        "news_id",
        "published_at",
        "published_date",
        "language",
        "region",
        "media_source_id",
        "media_domain",
        "source_domain",
        "event_family",
        "event_action",
        "initiator",
        "target",
        "location",
        "tone",
        "china_role",
        "directness",
        "directness_score",
        "stance_score",
        "confidence",
        "relevance_score",
        "article_weight",
        "target_scope",
        "evidence",
        "method_version",
        "scored_at",
        "updated_at",
    },
    "china_opinion_feedback": {
        "id",
        "news_id",
        "correction",
        "page",
        "note",
        "current_impact_index",
        "sentiment",
        "created_at",
    },
}
_OPINION_WRITE_INDEXES = {
    "idx_china_opinion_scores_date": "(published_date)",
    "idx_china_opinion_scores_dims": "(region, language, media_domain, event_family)",
    "idx_china_opinion_scores_direct": "(directness_score, relevance_score)",
    "idx_china_opinion_feedback_news_created": "(news_id, created_at desc, id desc)",
}
_OPINION_COLUMN_TYPES = {
    "china_opinion_article_scores": {
        "news_id": "int8",
        "published_at": "timestamptz",
        "published_date": "date",
        "language": "text",
        "region": "text",
        "media_source_id": "int4",
        "media_domain": "text",
        "source_domain": "text",
        "event_family": "text",
        "event_action": "text",
        "initiator": "text",
        "target": "text",
        "location": "text",
        "tone": "text",
        "china_role": "text",
        "directness": "text",
        "directness_score": "float8",
        "stance_score": "float8",
        "confidence": "float8",
        "relevance_score": "float8",
        "article_weight": "float8",
        "target_scope": "text",
        "evidence": "text",
        "method_version": "text",
        "scored_at": "timestamptz",
        "updated_at": "timestamptz",
    },
    "china_opinion_feedback": {
        "id": "int8",
        "news_id": "int8",
        "correction": "text",
        "page": "text",
        "note": "text",
        "current_impact_index": "float8",
        "sentiment": "float8",
        "created_at": "timestamptz",
    },
}
_OPINION_NOT_NULL_COLUMNS = {
    "china_opinion_article_scores": {
        "news_id",
        "directness_score",
        "stance_score",
        "confidence",
        "relevance_score",
        "article_weight",
        "method_version",
        "scored_at",
        "updated_at",
    },
    "china_opinion_feedback": {"id", "news_id", "correction", "created_at"},
}
_OPINION_DEFAULT_MARKERS = {
    ("china_opinion_article_scores", "directness_score"): "0",
    ("china_opinion_article_scores", "stance_score"): "0",
    ("china_opinion_article_scores", "confidence"): "0",
    ("china_opinion_article_scores", "relevance_score"): "0",
    ("china_opinion_article_scores", "article_weight"): "0",
    ("china_opinion_article_scores", "scored_at"): "now()",
    ("china_opinion_article_scores", "updated_at"): "now()",
    ("china_opinion_feedback", "id"): "nextval(",
    ("china_opinion_feedback", "created_at"): "now()",
}


def _feedback_json_error(*, contract: bool = False) -> HTTPException:
    if contract:
        detail = {
            "code": "OPINION_FEEDBACK_CONTRACT_INVALID",
            "message": "反馈字段不符合结构化非训练用途契约",
        }
    else:
        detail = {
            "code": "OPINION_FEEDBACK_JSON_AMBIGUOUS",
            "message": "反馈正文必须是无重复键、有限且有界的 JSON 对象",
        }
    return HTTPException(
        status_code=422,
        detail=detail,
        headers=dict(_FEEDBACK_NO_STORE_HEADERS),
    )


def _reject_duplicate_feedback_json_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate feedback JSON key")
        result[key] = value
    return result


def _reject_non_finite_feedback_json_number(_value: str) -> None:
    raise ValueError("non-finite feedback JSON number")


def _finite_feedback_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        _reject_non_finite_feedback_json_number(value)
    return parsed


def _validate_feedback_json_shape(value: Any) -> None:
    pending = [(value, 0)]
    node_count = 0
    while pending:
        current, depth = pending.pop()
        node_count += 1
        if node_count > _MAX_FEEDBACK_JSON_NODES:
            raise ValueError("feedback JSON has too many nodes")
        if depth > _MAX_FEEDBACK_JSON_DEPTH:
            raise ValueError("feedback JSON is too deeply nested")
        if isinstance(current, dict):
            pending.extend((child, depth + 1) for child in current.values())
        elif isinstance(current, list):
            pending.extend((child, depth + 1) for child in current)
        elif isinstance(current, float) and not math.isfinite(current):
            raise ValueError("feedback JSON contains a non-finite number")


async def _require_unambiguous_feedback_json(request: Request) -> None:
    content_type = (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    )
    if content_type != "application/json" and not content_type.endswith("+json"):
        raise _feedback_json_error()

    raw_content_length = request.headers.get("content-length")
    if raw_content_length is not None:
        normalized_length = raw_content_length.strip()
        if re.fullmatch(r"[0-9]+", normalized_length) is None:
            raise _feedback_json_error()
        if int(normalized_length, 10) > _MAX_FEEDBACK_JSON_BYTES:
            raise _feedback_json_error()

    buffered = bytearray()
    async for chunk in request.stream():
        if len(buffered) + len(chunk) > _MAX_FEEDBACK_JSON_BYTES:
            raise _feedback_json_error()
        buffered.extend(chunk)
    body = bytes(buffered)
    if not body:
        raise _feedback_json_error()
    # Starlette and FastAPI share this request object; cache only the already
    # bounded bytes so downstream model validation does not consume the stream
    # a second time.
    request._body = body
    try:
        payload = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_feedback_json_keys,
            parse_constant=_reject_non_finite_feedback_json_number,
            parse_float=_finite_feedback_json_float,
        )
        if not isinstance(payload, dict):
            raise ValueError("feedback JSON root must be an object")
        _validate_feedback_json_shape(payload)
    except (TypeError, UnicodeError, ValueError, RecursionError) as exc:
        raise _feedback_json_error() from exc


class _StrictOpinionFeedbackRoute(APIRoute):
    def get_route_handler(self):
        route_handler = super().get_route_handler()

        async def strict_route_handler(request: Request):
            await _require_unambiguous_feedback_json(request)
            try:
                response = await route_handler(request)
            except RequestValidationError as exc:
                raise _feedback_json_error(contract=True) from exc
            except HTTPException as exc:
                headers = dict(exc.headers or {})
                headers.update(_FEEDBACK_NO_STORE_HEADERS)
                exc.headers = headers
                raise
            for name, value in _FEEDBACK_NO_STORE_HEADERS.items():
                response.headers[name] = value
            return response

        return strict_route_handler


_feedback_router = APIRouter(route_class=_StrictOpinionFeedbackRoute)

def _make_news_database_url():
    return SQLALCHEMY_DATABASE_URL


_NEWS_SESSION_LOCAL = SessionLocal


def _opinion_session_factory():
    if is_test_environment():
        raise RuntimeError("Opinion database dependency must be overridden in tests")
    return _NEWS_SESSION_LOCAL


def get_opinion_db():
    db = _opinion_session_factory()()
    try:
        yield db
    finally:
        db.close()


def _json_response(
    content: dict[str, Any],
    *,
    no_store: bool = False,
    status_code: int = 200,
) -> JSONResponse:
    headers = None
    if no_store:
        headers = dict(_FEEDBACK_NO_STORE_HEADERS)
    return JSONResponse(
        content=jsonable_encoder(content),
        media_type="application/json; charset=utf-8",
        headers=headers,
        status_code=status_code,
    )


def _require_opinion_write_schema(db: Session) -> None:
    """Fail closed when owner-applied opinion migrations are incomplete."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    column_rows = db.execute(
        text(
            """
            SELECT table_name, column_name, udt_name, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name IN (
                  'china_opinion_article_scores',
                  'china_opinion_feedback'
              )
            """
        )
    ).mappings().fetchall()
    actual_columns: dict[str, dict[str, dict[str, Any]]] = {}
    for row in column_rows:
        actual_columns.setdefault(str(row["table_name"]), {})[str(row["column_name"])] = dict(row)
    missing_columns = {
        table: sorted(required - set(actual_columns.get(table, {})))
        for table, required in _OPINION_WRITE_COLUMNS.items()
        if required - set(actual_columns.get(table, {}))
    }
    contract_issues: list[str] = []
    for table, expected_columns in _OPINION_COLUMN_TYPES.items():
        for column, expected_type in expected_columns.items():
            actual = actual_columns.get(table, {}).get(column)
            if actual is None:
                continue
            if str(actual.get("udt_name")) != expected_type:
                contract_issues.append(f"{table}.{column}:type")
            if (
                column in _OPINION_NOT_NULL_COLUMNS[table]
                and str(actual.get("is_nullable")) != "NO"
            ):
                contract_issues.append(f"{table}.{column}:nullable")
            marker = _OPINION_DEFAULT_MARKERS.get((table, column))
            if marker and marker not in str(actual.get("column_default") or "").lower():
                contract_issues.append(f"{table}.{column}:default")

    constraint_rows = db.execute(
        text(
            """
            SELECT c.relname AS table_name, con.contype,
                   pg_catalog.pg_get_constraintdef(con.oid) AS definition
            FROM pg_catalog.pg_constraint AS con
            JOIN pg_catalog.pg_class AS c ON c.oid = con.conrelid
            JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relname IN (
                  'china_opinion_article_scores',
                  'china_opinion_feedback'
              )
            """
        )
    ).mappings().fetchall()
    constraint_defs = {
        (str(row["table_name"]), str(row["contype"]), str(row["definition"]).lower())
        for row in constraint_rows
    }
    if not any(
        table == "china_opinion_article_scores" and kind == "p" and "(news_id)" in definition
        for table, kind, definition in constraint_defs
    ):
        contract_issues.append("china_opinion_article_scores:primary-key")
    if not any(
        table == "china_opinion_feedback" and kind == "p" and "(id)" in definition
        for table, kind, definition in constraint_defs
    ):
        contract_issues.append("china_opinion_feedback:primary-key")
    feedback_checks = [
        definition
        for table, kind, definition in constraint_defs
        if table == "china_opinion_feedback" and kind == "c" and "correction" in definition
    ]
    if not feedback_checks or not all(
        value in " ".join(feedback_checks) for value in _VALID_FEEDBACK_CORRECTIONS
    ):
        contract_issues.append("china_opinion_feedback:correction-check")

    index_rows = db.execute(
        text(
            """
            SELECT indexname, indexdef
            FROM pg_catalog.pg_indexes
            WHERE schemaname = 'public'
              AND indexname IN (
                  'idx_china_opinion_scores_date',
                  'idx_china_opinion_scores_dims',
                  'idx_china_opinion_scores_direct',
                  'idx_china_opinion_feedback_news_created'
              )
            """
        )
    ).mappings().fetchall()
    actual_indexes = {str(row["indexname"]): str(row["indexdef"]) for row in index_rows}
    missing_indexes = sorted(set(_OPINION_WRITE_INDEXES) - set(actual_indexes))
    for index_name, expected_columns in _OPINION_WRITE_INDEXES.items():
        definition = re.sub(r"\s+", " ", actual_indexes.get(index_name, "").lower())
        if definition and expected_columns not in definition:
            contract_issues.append(f"{index_name}:definition")

    sequence_row = db.execute(
        text(
            """
            SELECT pg_catalog.pg_get_serial_sequence(
                       'public.china_opinion_feedback', 'id'
                   ) AS sequence_name,
                   pg_catalog.pg_get_userbyid(sequence.relowner) AS sequence_owner
            FROM pg_catalog.pg_class AS sequence
            WHERE sequence.oid = pg_catalog.to_regclass(
                pg_catalog.pg_get_serial_sequence(
                    'public.china_opinion_feedback', 'id'
                )
            )
            """
        )
    ).mappings().first()
    if (
        not sequence_row
        or str(sequence_row.get("sequence_name")) != "public.china_opinion_feedback_id_seq"
        or str(sequence_row.get("sequence_owner")) != "postgres"
    ):
        contract_issues.append("china_opinion_feedback.id:sequence")
    if missing_columns or missing_indexes or contract_issues:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "opinion schema migration is incomplete",
                "missing_columns": missing_columns,
                "missing_indexes": missing_indexes,
                "contract_issues": sorted(set(contract_issues)),
            },
        )
    _SCHEMA_READY = True


def _backfill_score_window(db: Session, start_d: date, end_d: date) -> int:
    _require_opinion_write_schema(db)
    db.execute(
        text(
            """
            DELETE FROM public.china_opinion_article_scores
            WHERE published_date BETWEEN :start_date AND :end_date
            """
        ),
        {"start_date": start_d, "end_date": end_d},
    )
    result = db.execute(
        text(
            """
            WITH raw AS (
                SELECT DISTINCT ON (n.id)
                    n.id AS news_id,
                    n.published_at,
                    (n.published_at AT TIME ZONE 'UTC')::date AS published_date,
                    n.language,
                    n.region,
                    n.media_source_id,
                    ms.domain AS media_domain,
                    p.source_domain,
                    e.event_family,
                    e.event_action,
                    e.initiator,
                    e.target,
                    e.location,
                    lower(coalesce(e.tone, 'neutral')) AS tone,
                    lower(coalesce(e.initiator, '') || ' ' || coalesce(e.canonical_initiator, '')) AS initiator_text,
                    lower(coalesce(e.target, '') || ' ' || coalesce(e.canonical_target, '')) AS target_text,
                    lower(coalesce(e.location, '')) AS location_text,
                    regexp_replace(lower(coalesce(n.title, '')), :brand_pattern, '', 'gi') AS title_text,
                    lower(coalesce(n.title, '') || ' ' || left(coalesce(n.body, ''), 2200)) AS full_text,
                    left(coalesce(n.title, ''), 420) AS evidence_title,
                    e.updated_at AS extraction_updated_at
                FROM public.news AS n
                JOIN public.news_l1_event_extractions AS e ON e.news_id = n.id
                LEFT JOIN public.news_l1_prep AS p ON p.news_id = n.id
                LEFT JOIN public.media_source AS ms ON ms.id = n.media_source_id
                WHERE n.published_at IS NOT NULL
                  AND n.published_at <= now()
                  AND (n.published_at AT TIME ZONE 'UTC')::date BETWEEN :start_date AND :end_date
                  AND COALESCE(e.parse_success, true)
                  AND NOT (
                      coalesce(n.url, '') ~ :source_section_url_pattern
                      OR (
                          coalesce(ms.domain, p.source_domain, '') = 'scmp.com'
                          AND coalesce(n.url, '') !~ :scmp_article_url_pattern
                      )
                  )
                ORDER BY n.id,
                    CASE
                        WHEN lower(coalesce(e.initiator, '') || ' ' || coalesce(e.canonical_initiator, '') || ' ' ||
                                   coalesce(e.target, '') || ' ' || coalesce(e.canonical_target, '')) ~ :china_pattern THEN 0
                        WHEN regexp_replace(lower(coalesce(n.title, '')), :brand_pattern, '', 'gi') ~ :china_pattern THEN 1
                        ELSE 2
                    END,
                    e.updated_at DESC NULLS LAST
            ),
            flags AS (
                SELECT *,
                    (initiator_text ~ :china_pattern) AS china_in_initiator,
                    (target_text ~ :china_pattern) AS china_in_target,
                    (location_text ~ :china_pattern) AS china_in_location,
                    (title_text ~ :china_pattern) AS china_in_title,
                    (full_text ~ :china_pattern) AS china_in_text,
                    (initiator_text ~ :core_china_pattern) AS core_china_in_initiator,
                    (target_text ~ :core_china_pattern) AS core_china_in_target,
                    (location_text ~ :core_china_pattern) AS core_china_in_location,
                    (title_text ~ :core_china_pattern) AS core_china_in_title,
                    (full_text ~ :core_china_pattern) AS core_china_in_text,
                    (initiator_text ~ :china_periphery_pattern) AS periphery_in_initiator,
                    (target_text ~ :china_periphery_pattern) AS periphery_in_target,
                    (location_text ~ :china_periphery_pattern) AS periphery_in_location,
                    (title_text ~ :china_periphery_pattern) AS periphery_in_title,
                    (full_text ~ :negative_pattern) AS has_negative_eval,
                    (full_text ~ :positive_pattern) AS has_positive_eval,
                    (full_text ~ :negative_china_prox_pattern) AS has_china_negative_eval,
                    (full_text ~ :positive_china_prox_pattern) AS has_china_positive_eval,
                    (title_text ~ :negative_pattern OR title_text ~ :positive_pattern) AS title_has_eval,
                    (title_text ~ :supply_to_china_pattern) AS title_supply_to_china,
                    (title_text ~ :china_resilience_pattern) AS title_china_resilience,
                    (title_text ~ :defensive_china_opinion_pattern) AS title_defensive_china_opinion,
                    (title_text ~ :title_restriction_pattern) AS title_has_restriction,
                    (lower(evidence_title) ~ :index_page_pattern AND char_length(evidence_title) < 180) AS likely_index_page
                FROM raw
                WHERE initiator_text ~ :china_pattern
                   OR target_text ~ :china_pattern
                   OR location_text ~ :china_pattern
                   OR title_text ~ :china_pattern
            ),
            scored AS (
                SELECT *,
                    CASE
                        WHEN core_china_in_initiator AND core_china_in_target THEN 'china_as_both'
                        WHEN core_china_in_target THEN 'china_as_target'
                        WHEN core_china_in_initiator THEN 'china_as_initiator'
                        WHEN china_in_initiator OR china_in_target THEN 'china_periphery_related'
                        WHEN core_china_in_location THEN 'china_as_location'
                        WHEN core_china_in_title THEN 'china_in_title'
                        WHEN china_in_location OR china_in_title THEN 'china_periphery_related'
                        ELSE 'china_mention'
                    END AS china_role,
                    CASE
                        WHEN core_china_in_initiator OR core_china_in_target THEN 'direct_evaluation'
                        WHEN core_china_in_title AND (has_china_negative_eval OR has_china_positive_eval OR title_has_eval) THEN 'direct_evaluation'
                        WHEN core_china_in_title AND tone <> 'neutral' THEN 'indirect_related'
                        WHEN (periphery_in_initiator OR periphery_in_target) AND core_china_in_text
                             AND (has_china_negative_eval OR has_china_positive_eval OR title_has_eval) THEN 'indirect_related'
                        WHEN core_china_in_location AND (has_china_negative_eval OR has_china_positive_eval) THEN 'indirect_related'
                        WHEN core_china_in_title OR core_china_in_location OR core_china_in_text THEN 'mention_only'
                        ELSE 'mention_only'
                    END AS directness,
                    CASE
                        WHEN core_china_in_initiator OR core_china_in_target THEN 0.96
                        WHEN core_china_in_title AND (has_china_negative_eval OR has_china_positive_eval OR title_has_eval) THEN 0.78
                        WHEN core_china_in_title AND tone <> 'neutral' THEN 0.62
                        WHEN (periphery_in_initiator OR periphery_in_target) AND core_china_in_text
                             AND (has_china_negative_eval OR has_china_positive_eval OR title_has_eval) THEN 0.52
                        WHEN core_china_in_location AND (has_china_negative_eval OR has_china_positive_eval) THEN 0.58
                        WHEN core_china_in_title OR core_china_in_location OR core_china_in_text THEN 0.42
                        ELSE 0.25
                    END AS directness_score,
                    CASE
                        WHEN title_china_resilience THEN 0.25
                        WHEN title_defensive_china_opinion THEN 0.25
                        WHEN title_supply_to_china AND NOT title_has_restriction THEN 0.10
                        WHEN has_china_negative_eval AND NOT has_china_positive_eval
                             AND (core_china_in_initiator OR core_china_in_target OR core_china_in_title) THEN -0.65
                        WHEN has_china_positive_eval AND NOT has_china_negative_eval
                             AND (core_china_in_initiator OR core_china_in_target OR core_china_in_title) THEN 0.55
                        WHEN core_china_in_target
                             AND event_action IN ('sanction_export_control', 'tariff_trade_dispute', 'export_control') THEN -0.55
                        WHEN core_china_in_target AND tone = 'negative'
                             AND event_action IN ('military_attack', 'crackdown_arrest', 'terror_attack', 'cyber_attack',
                                                  'cyber_espionage', 'territorial_dispute') THEN -0.45
                        WHEN core_china_in_target AND tone = 'negative' THEN -0.35
                        WHEN core_china_in_target AND tone = 'positive' THEN 0.35
                        WHEN core_china_in_initiator AND tone = 'negative'
                             AND event_action IN ('sanction_export_control', 'tariff_trade_dispute', 'military_attack',
                                                  'crackdown_arrest', 'cyber_attack', 'cyber_espionage',
                                                  'territorial_dispute') THEN -0.35
                        WHEN core_china_in_initiator AND tone = 'negative' THEN -0.20
                        WHEN core_china_in_initiator AND tone = 'positive' THEN 0.35
                        WHEN (core_china_in_title OR core_china_in_text) AND has_china_negative_eval AND NOT has_china_positive_eval THEN -0.25
                        WHEN (core_china_in_title OR core_china_in_text) AND has_china_positive_eval AND NOT has_china_negative_eval THEN 0.25
                        WHEN core_china_in_title AND tone = 'negative' THEN -0.22
                        WHEN core_china_in_title AND tone = 'positive' THEN 0.22
                        ELSE 0.0
                    END AS stance_score,
                    CASE
                        WHEN (core_china_in_initiator OR core_china_in_target)
                             AND (has_china_negative_eval OR has_china_positive_eval) THEN 0.86
                        WHEN core_china_in_initiator OR core_china_in_target THEN 0.72
                        WHEN core_china_in_title AND (has_china_negative_eval OR has_china_positive_eval OR title_has_eval) THEN 0.68
                        WHEN core_china_in_title AND tone <> 'neutral' THEN 0.52
                        WHEN (periphery_in_initiator OR periphery_in_target) AND core_china_in_text
                             AND (has_china_negative_eval OR has_china_positive_eval OR title_has_eval) THEN 0.45
                        WHEN core_china_in_title OR core_china_in_text THEN 0.44
                        WHEN core_china_in_location THEN 0.46
                        ELSE 0.35
                    END AS confidence,
                    CASE
                        WHEN full_text ~ '(econom|trade|tariff|export|investment|market|ev|battery|chip|semiconductor)' THEN 'economy_trade'
                        WHEN full_text ~ '(military|navy|army|pla|security|south china sea|defen[cs]e)' THEN 'security_military'
                        WHEN full_text ~ '(taiwan|hong kong|xinjiang|tibet|human rights)' THEN 'sovereignty_rights'
                        WHEN full_text ~ '(technology|innovation|chip|ai|semiconductor|space|satellite)' THEN 'technology'
                        WHEN full_text ~ '(government|beijing|policy|minister|president)' THEN 'government_policy'
                        ELSE 'general_china'
                    END AS target_scope
                FROM flags
            )
            INSERT INTO public.china_opinion_article_scores (
                news_id, published_at, published_date, language, region, media_source_id,
                media_domain, source_domain, event_family, event_action, initiator, target,
                location, tone, china_role, directness, directness_score, stance_score,
                confidence, relevance_score, article_weight, target_scope, evidence,
                method_version, scored_at, updated_at
            )
            SELECT
                news_id, published_at, published_date, language, region, media_source_id,
                media_domain, source_domain, event_family, event_action, initiator, target,
                location, tone, china_role, directness, directness_score, stance_score,
                confidence,
                greatest(0.0, least(1.0, directness_score * confidence)) AS relevance_score,
                greatest(0.05, least(1.0, directness_score * confidence)) AS article_weight,
                target_scope,
                evidence_title,
                :method_version,
                now(),
                now()
            FROM scored
            WHERE directness_score >= 0.55
              AND NOT likely_index_page
            ON CONFLICT (news_id) DO UPDATE SET
                published_at = EXCLUDED.published_at,
                published_date = EXCLUDED.published_date,
                language = EXCLUDED.language,
                region = EXCLUDED.region,
                media_source_id = EXCLUDED.media_source_id,
                media_domain = EXCLUDED.media_domain,
                source_domain = EXCLUDED.source_domain,
                event_family = EXCLUDED.event_family,
                event_action = EXCLUDED.event_action,
                initiator = EXCLUDED.initiator,
                target = EXCLUDED.target,
                location = EXCLUDED.location,
                tone = EXCLUDED.tone,
                china_role = EXCLUDED.china_role,
                directness = EXCLUDED.directness,
                directness_score = EXCLUDED.directness_score,
                stance_score = EXCLUDED.stance_score,
                confidence = EXCLUDED.confidence,
                relevance_score = EXCLUDED.relevance_score,
                article_weight = EXCLUDED.article_weight,
                target_scope = EXCLUDED.target_scope,
                evidence = EXCLUDED.evidence,
                method_version = EXCLUDED.method_version,
                updated_at = now()
            """
        ),
        {
            "start_date": start_d,
            "end_date": end_d,
            "method_version": METHOD_VERSION,
            "china_pattern": CHINA_PATTERN,
            "core_china_pattern": CORE_CHINA_PATTERN,
            "china_periphery_pattern": CHINA_PERIPHERY_PATTERN,
            "brand_pattern": BRAND_PATTERN,
            "negative_pattern": NEGATIVE_PATTERN,
            "positive_pattern": POSITIVE_PATTERN,
            "negative_china_prox_pattern": NEGATIVE_CHINA_PROX_PATTERN,
            "positive_china_prox_pattern": POSITIVE_CHINA_PROX_PATTERN,
            "supply_to_china_pattern": SUPPLY_TO_CHINA_PATTERN,
            "china_resilience_pattern": CHINA_RESILIENCE_PATTERN,
            "defensive_china_opinion_pattern": DEFENSIVE_CHINA_OPINION_PATTERN,
            "title_restriction_pattern": TITLE_RESTRICTION_PATTERN,
            "index_page_pattern": INDEX_PAGE_PATTERN,
            "source_section_url_pattern": SOURCE_SECTION_URL_PATTERN,
            "scmp_article_url_pattern": SCMP_ARTICLE_URL_PATTERN,
        },
    )
    db.execute(
        text(
            """
            DELETE FROM public.china_opinion_article_scores AS s
            USING public.news AS n
            WHERE published_date BETWEEN :start_date AND :end_date
              AND n.id = s.news_id
              AND (
                  (lower(coalesce(evidence, '')) ~ :index_page_pattern
                   AND char_length(coalesce(evidence, '')) < 180)
                  OR coalesce(n.url, '') ~ :source_section_url_pattern
                  OR (
                      coalesce(s.media_domain, s.source_domain, '') = 'scmp.com'
                      AND coalesce(n.url, '') !~ :scmp_article_url_pattern
                  )
              )
            """
        ),
        {
            "start_date": start_d,
            "end_date": end_d,
            "index_page_pattern": INDEX_PAGE_PATTERN,
            "source_section_url_pattern": SOURCE_SECTION_URL_PATTERN,
            "scmp_article_url_pattern": SCMP_ARTICLE_URL_PATTERN,
        },
    )
    db.commit()
    return int(result.rowcount or 0)


def _refresh_scores(db: Session, start_d: date, end_d: date, *, force: bool = False) -> None:
    _require_opinion_write_schema(db)
    if not force:
        recent_start = max(start_d, end_d - timedelta(days=7))
        recent_existing = db.execute(
            text(
                """
                SELECT count(*)
                FROM public.china_opinion_article_scores
                WHERE published_date BETWEEN :recent_start AND :end_date
                  AND method_version = :method_version
                """
            ),
            {"recent_start": recent_start, "end_date": end_d, "method_version": METHOD_VERSION},
        ).scalar() or 0
        if int(recent_existing) > 0:
            start_d = recent_start

    key = f"{start_d.isoformat()}:{end_d.isoformat()}"
    now = time.monotonic()
    if not force and now - _LAST_BACKFILL.get(key, 0) < 300:
        return
    acquired = _REFRESH_LOCK.acquire(blocking=force)
    if not acquired:
        return
    try:
        now = time.monotonic()
        if not force and now - _LAST_BACKFILL.get(key, 0) < 300:
            return
        if not force:
            _LAST_BACKFILL[key] = now
        _backfill_score_window(db, start_d, end_d)
        _LAST_BACKFILL[key] = time.monotonic()
        _RESP_CACHE.clear()
    except Exception:
        if not force:
            _LAST_BACKFILL.pop(key, None)
        db.rollback()
        raise
    finally:
        _REFRESH_LOCK.release()


@router.post("/opinion/admin/refresh", tags=["舆情管理"])
def refresh_opinion_scores(
    payload: OpinionRefreshPayload,
    _admin: dict[str, Any] = Depends(get_current_admin_user),
    db: Session = Depends(get_opinion_db),
) -> JSONResponse:
    """Refresh the materialized opinion score window as an explicit admin action."""
    if payload.days < 1 or payload.days > 900:
        raise HTTPException(status_code=422, detail="days must be between 1 and 900")
    current_date = _current_db_date(db)
    end_d = payload.end_date or current_date
    start_d = payload.start_date or (end_d - timedelta(days=payload.days - 1))
    if start_d > end_d:
        raise HTTPException(status_code=422, detail="start_date must not be after end_date")
    if (end_d - start_d).days >= 900:
        raise HTTPException(status_code=422, detail="refresh window must not exceed 900 days")
    if end_d > current_date:
        raise HTTPException(status_code=422, detail="end_date must not be in the future")

    _refresh_scores(db, start_d, end_d, force=payload.force)
    return _json_response(
        {
            "ok": True,
            "start_date": start_d.isoformat(),
            "end_date": end_d.isoformat(),
            "method_version": METHOD_VERSION,
        },
        no_store=True,
    )


@router.get("/opinion/china-trend", tags=["舆情"])
def get_china_opinion_trend(
    days: int = Query(365, ge=7, le=3650),
    china_min_score: float = Query(0.4, ge=0.0, le=1.0),
    sentiment_filter: str = Query("all"),
    region: Optional[str] = Query(None),
    language: Optional[str] = Query(None),
    media_source: Optional[str] = Query(None),
    event_family: Optional[str] = Query(None),
    db: Session = Depends(get_opinion_db),
) -> JSONResponse:
    ck = _cache_key(
        "china_trend_v5",
        days=days,
        china_min_score=china_min_score,
        sentiment_filter=sentiment_filter,
        region=region,
        language=language,
        media_source=media_source,
        event_family=event_family,
    )
    cached = _cache_get(ck)
    if cached is not None:
        return _json_response(_sanitize_opinion_response(cached, db))
    content = _sanitize_opinion_response(
        _build_trend_content(
            db,
            days=days,
            china_min_score=china_min_score,
            sentiment_filter=sentiment_filter,
            region=region,
            language=language,
            media_source=media_source,
            event_family=event_family,
        ),
        db,
    )
    _cache_set(ck, jsonable_encoder(content), ttl=180)
    return _json_response(content)


@router.get("/opinion/overview", tags=["舆情"])
def get_opinion_overview(
    days: int = Query(30, ge=7, le=365),
    db: Session = Depends(get_opinion_db),
) -> JSONResponse:
    ck = _cache_key("overview_v5", method=METHOD_VERSION, days=days)
    cached = _cache_get(ck)
    if cached is not None:
        return _json_response(
            _sanitize_opinion_response(cached, db, include_overview_claims=True)
        )

    trend = _build_trend_content(
        db,
        days=days,
        china_min_score=0.4,
        sentiment_filter="all",
    )
    values = trend["values"]
    current = float(values[-1]) if values else 0.0
    prev = float(values[-2]) if len(values) >= 2 else current
    change = current - prev
    current_date = _current_db_date(db)
    cutoff_date = _coerce_date(
        trend.get("meta", {}).get("trust", {}).get("cutoff_date")
    )
    latest_date = cutoff_date
    query_end = cutoff_date or current_date
    start_d = query_end - timedelta(days=days - 1)
    prev_start = start_d - timedelta(days=days)
    prev_end = start_d - timedelta(days=1)

    summary = db.execute(
        text(
            f"""
            WITH {LATEST_FEEDBACK_CTE},
            scored AS (
                SELECT s.*, {EFFECTIVE_STANCE_EXPR} AS effective_stance
                FROM public.china_opinion_article_scores AS s
                LEFT JOIN latest_feedback AS lf ON lf.news_id = s.news_id
                WHERE {FEEDBACK_VISIBLE_EXPR}
                  AND {VALID_SCORE_EXPR}
            )
            SELECT
                count(*) AS article_count,
                count(DISTINCT coalesce(media_domain, source_domain)) AS source_count,
                count(DISTINCT event_family) AS family_count,
                count(*) FILTER (WHERE effective_stance > 0.15) AS positive_count,
                count(*) FILTER (WHERE effective_stance < -0.15) AS negative_count,
                count(*) FILTER (WHERE effective_stance BETWEEN -0.15 AND 0.15) AS neutral_count
            FROM scored
            WHERE published_date BETWEEN :start_d AND :latest_date
              AND directness_score >= 0.55
              AND relevance_score >= 0.4
            """
        ),
        {
            "start_d": start_d,
            "latest_date": query_end,
            "method_version": METHOD_VERSION,
        },
    ).mappings().first() or {}
    previous_articles = db.execute(
        text(
            f"""
            WITH {LATEST_FEEDBACK_CTE}
            SELECT count(*)
            FROM public.china_opinion_article_scores AS s
            LEFT JOIN latest_feedback AS lf ON lf.news_id = s.news_id
            WHERE s.published_date BETWEEN :prev_start AND :prev_end
              AND s.directness_score >= 0.55
              AND s.relevance_score >= 0.4
              AND {FEEDBACK_VISIBLE_EXPR}
              AND {VALID_SCORE_EXPR}
            """
        ),
        {
            "prev_start": prev_start,
            "prev_end": prev_end,
            "method_version": METHOD_VERSION,
        },
    ).scalar() or 0

    trust = trend.get("meta", {}).get("trust", {})
    article_count = int(summary.get("article_count") or 0)
    source_count = int(summary.get("source_count") or 0)
    positive_count = int(summary.get("positive_count") or 0)
    negative_count = int(summary.get("negative_count") or 0)
    neutral_count = int(summary.get("neutral_count") or 0)
    growth_pct = 0.0 if previous_articles <= 0 else (article_count - previous_articles) * 100.0 / previous_articles

    families = db.execute(
        text(
            f"""
            WITH {LATEST_FEEDBACK_CTE},
            scored AS (
                SELECT s.*, {EFFECTIVE_STANCE_EXPR} AS effective_stance
                FROM public.china_opinion_article_scores AS s
                LEFT JOIN latest_feedback AS lf ON lf.news_id = s.news_id
                WHERE {FEEDBACK_VISIBLE_EXPR}
                  AND {VALID_SCORE_EXPR}
            )
            SELECT coalesce(event_family, 'unknown') AS event_family,
                   count(*) AS article_count,
                   avg(effective_stance) AS avg_stance
            FROM scored
            WHERE published_date BETWEEN :start_d AND :latest_date
              AND directness_score >= 0.55
              AND relevance_score >= 0.4
            GROUP BY 1
            ORDER BY article_count DESC
            LIMIT 8
            """
        ),
        {
            "start_d": start_d,
            "latest_date": query_end,
            "method_version": METHOD_VERSION,
        },
    ).mappings().fetchall()

    briefs = db.execute(
        text(
            f"""
            WITH {LATEST_FEEDBACK_CTE}
            SELECT s.news_id, s.evidence AS title, s.published_at, s.media_domain, s.event_family,
                   {EFFECTIVE_STANCE_EXPR} AS stance_score, s.confidence, s.directness,
                   lf.correction AS feedback_correction
            FROM public.china_opinion_article_scores AS s
            LEFT JOIN latest_feedback AS lf ON lf.news_id = s.news_id
            WHERE s.published_date BETWEEN :start_d AND :latest_date
              AND s.directness_score >= 0.55
              AND s.relevance_score >= 0.4
              AND {FEEDBACK_VISIBLE_EXPR}
              AND {VALID_SCORE_EXPR}
            ORDER BY abs(({EFFECTIVE_STANCE_EXPR}) * s.article_weight) DESC, s.published_at DESC
            LIMIT 6
            """
        ),
        {
            "start_d": start_d,
            "latest_date": query_end,
            "method_version": METHOD_VERSION,
        },
    ).mappings().fetchall()

    latest_events = db.execute(
        text(
            f"""
            WITH {LATEST_FEEDBACK_CTE},
            ranked AS (
                SELECT c.chain_id, c.title, c.event_family, c.article_count,
                       avg({EFFECTIVE_STANCE_EXPR}) AS avg_stance,
                       count(DISTINCT s.news_id) AS china_articles
                FROM public.event_l2_chains AS c
                JOIN public.event_l2_chain_segments AS cs
                  ON cs.run_id = c.run_id AND cs.chain_id = c.chain_id
                JOIN public.event_l15_members AS lm
                  ON lm.run_id = cs.l15_run_id AND lm.segment_id = cs.segment_id
                JOIN public.china_opinion_article_scores AS s
                  ON s.news_id = lm.news_id
                LEFT JOIN latest_feedback AS lf ON lf.news_id = s.news_id
                WHERE c.run_id = :l2_run_id
                  AND s.published_date BETWEEN :start_d AND :latest_date
                  AND s.directness_score >= 0.55
                  AND s.relevance_score >= 0.4
                  AND {FEEDBACK_VISIBLE_EXPR}
                  AND {VALID_SCORE_EXPR}
                GROUP BY c.chain_id, c.title, c.event_family, c.article_count
            )
            SELECT *
            FROM ranked
            ORDER BY abs(avg_stance) * ln(2 + china_articles) DESC
            LIMIT 1
            """
        ),
        {
            "l2_run_id": DEFAULT_L2_RUN_ID,
            "start_d": start_d,
            "latest_date": query_end,
            "method_version": METHOD_VERSION,
        },
    ).mappings().first()

    total_dist = max(1, positive_count + negative_count + neutral_count)
    composite_available = bool(trust.get("is_computable") is True)
    public_meta = {**trend["meta"], "trust": trust}
    content = {
        "ok": True,
        "latest_date": latest_date.isoformat() if latest_date else None,
        "summary": {
            "current_index": round(current, 1) if composite_available else None,
            "change_24h": round(change, 1) if composite_available else None,
            "trend_label": _classify_index_label(current) if composite_available else "不可计算",
            "growth_pct": round(growth_pct, 1),
            "article_count": article_count,
            "source_count": source_count,
            "family_count": int(summary.get("family_count") or 0),
            "positive_pct": round(positive_count * 100 / total_dist, 1),
            "negative_pct": round(negative_count * 100 / total_dist, 1),
            "neutral_pct": round(neutral_count * 100 / total_dist, 1),
        },
        "target_indices": [
            {
                "label": "CN",
                "value": round(current, 1) if composite_available else None,
                "trend_values": values[-14:] if composite_available and values else [],
                "state": (
                    "negative"
                    if composite_available and current < -12
                    else "positive"
                    if composite_available and current > 12
                    else "warning"
                    if composite_available
                    else "unavailable"
                ),
            },
            {
                "label": "NEG",
                "value": round(-negative_count * 100 / total_dist, 1) if composite_available else None,
                "trend_values": [],
                "state": "negative" if composite_available else "unavailable",
            },
            {
                "label": "POS",
                "value": round(positive_count * 100 / total_dist, 1) if composite_available else None,
                "trend_values": [],
                "state": "positive" if composite_available else "unavailable",
            },
        ],
        "top_event": dict(latest_events) if latest_events else None,
        "families": [dict(r) for r in families],
        "briefs": [
            {
                "id": int(r["news_id"]),
                "time": r["published_at"].isoformat() if r["published_at"] else None,
                "title": r["title"] or "无标题",
                "source": r["media_domain"] or "",
                "event_family": r["event_family"] or "",
                "stance_score": round(float(r["stance_score"] or 0.0), 3),
                "confidence": round(float(r["confidence"] or 0.0), 2),
                "severity": "critical" if abs(float(r["stance_score"] or 0.0)) >= 0.7 else "high" if abs(float(r["stance_score"] or 0.0)) >= 0.4 else "info",
            }
            for r in briefs
        ],
        "metrics": [
            {
                "label": "较前一日",
                "value": _fmt_signed(change) if composite_available else "不可计算",
                "display_tone": (
                    "neg"
                    if composite_available and change < 0
                    else "pos"
                    if composite_available and change > 0
                    else "neutral"
                ),
            },
            {"label": "报道量", "value": f"{article_count:,}", "display_tone": "neutral"},
            {"label": "信源数", "value": f"{source_count:,}", "display_tone": "neutral"},
        ],
        "trust": trust,
        "meta": public_meta,
    }
    content = _sanitize_opinion_response(content, db, include_overview_claims=True)
    _cache_set(ck, jsonable_encoder(content), ttl=180)
    return _json_response(content)


@router.get("/opinion/v3-stats", tags=["舆情"])
def get_v3_aggregate_stats(db: Session = Depends(get_opinion_db)) -> JSONResponse:
    cluster_count = db.execute(text("SELECT count(*) FROM public.event_coref_clusters WHERE run_id = :run_id"), {"run_id": DEFAULT_L1_RUN_ID}).scalar() or 0
    segment_count = db.execute(text("SELECT count(*) FROM public.event_l15_segments WHERE run_id = :run_id"), {"run_id": DEFAULT_L15_RUN_ID}).scalar() or 0
    chain_count = db.execute(text("SELECT count(*) FROM public.event_l2_chains WHERE run_id = :run_id"), {"run_id": DEFAULT_L2_RUN_ID}).scalar() or 0
    total_news = db.execute(text("SELECT count(*) FROM public.news WHERE published_at <= now()")).scalar() or 0
    scored = db.execute(text("SELECT count(*) FROM public.china_opinion_article_scores WHERE relevance_score >= 0.4")).scalar() or 0
    direct = db.execute(text("SELECT count(*) FROM public.china_opinion_article_scores WHERE directness = 'direct_evaluation' AND relevance_score >= 0.4")).scalar() or 0
    latest = _latest_score_date(db)
    content = {
        "ok": True,
        "event_coref": {
            "clusters": {"total": int(cluster_count)},
            "l15_segments": int(segment_count),
            "l2_chains": int(chain_count),
        },
        "coverage": {
            "total_news": int(total_news),
            "scored_china_related": int(scored),
            "direct_evaluation": int(direct),
            "coverage_pct": round(scored * 100 / total_news, 3) if total_news else 0.0,
        },
        "china_data": {
            "relevant_articles": int(scored),
            "direct_evaluation_articles": int(direct),
            "latest_score_date": latest.isoformat() if latest else None,
            "method_version": METHOD_VERSION,
        },
    }
    return JSONResponse(content=jsonable_encoder(content), media_type="application/json; charset=utf-8")


@router.get("/opinion/health", tags=["舆情"])
def get_opinion_health(db: Session = Depends(get_opinion_db)) -> JSONResponse:
    current_date = _current_db_date(db)
    latest_news = db.execute(text("SELECT max(published_at) FROM public.news WHERE published_at <= now()")).scalar()
    latest_score = db.execute(text("SELECT max(updated_at) FROM public.china_opinion_article_scores")).scalar()
    latest_score_date = _latest_score_date(db)
    total_recent = db.execute(
        text("SELECT count(*) FROM public.news_l1_event_extractions e JOIN public.news n ON n.id = e.news_id WHERE n.published_at <= now() AND (n.published_at AT TIME ZONE 'UTC')::date >= :start_d"),
        {"start_d": current_date - timedelta(days=30)},
    ).scalar() or 0
    scored_recent = db.execute(
        text("SELECT count(*) FROM public.china_opinion_article_scores WHERE published_date >= :start_d AND relevance_score >= 0.4"),
        {"start_d": current_date - timedelta(days=30)},
    ).scalar() or 0
    lag_hours = None
    if latest_news:
        ln = latest_news if latest_news.tzinfo else latest_news.replace(tzinfo=timezone.utc)
        lag_hours = round((datetime.now(timezone.utc) - ln).total_seconds() / 3600, 1)
    alerts = []
    status = "healthy"
    if latest_score_date and (current_date - latest_score_date).days > 2:
        alerts.append(f"score lag {(current_date - latest_score_date).days}d")
        status = "degraded"
    if scored_recent == 0:
        alerts.append("no recent China stance scores")
        status = "degraded"
    content = {
        "ok": True,
        "status": status,
        "alerts": alerts,
        "freshness": {
            "latest_news": latest_news.isoformat() if latest_news else None,
            "latest_score": latest_score.isoformat() if latest_score else None,
            "latest_score_date": latest_score_date.isoformat() if latest_score_date else None,
            "lag_hours": lag_hours,
        },
        "coverage": {
            "recent_l1_extractions": int(total_recent),
            "recent_scored_china_articles": int(scored_recent),
            "method_version": METHOD_VERSION,
        },
    }
    return JSONResponse(content=jsonable_encoder(content), media_type="application/json; charset=utf-8")


@router.get("/opinion/events-by-date", tags=["舆情"])
def get_events_by_date(
    date_str: str = Query(...),
    sentiment_filter: str = Query("all"),
    db: Session = Depends(get_opinion_db),
) -> JSONResponse:
    target_date = date.fromisoformat(date_str)
    sentiment_clause = ""
    if sentiment_filter == "positive":
        sentiment_clause = "AND s.stance_score > 0.15"
    elif sentiment_filter == "negative":
        sentiment_clause = "AND s.stance_score < -0.15"

    chain_rows = db.execute(
        text(
            f"""
            WITH {LATEST_FEEDBACK_CTE},
            chain_articles AS (
                SELECT c.chain_id, c.title, c.event_family, c.event_action, c.initiator, c.target,
                       c.start_date, c.end_date, c.article_count, c.segment_count,
                       cs.l1_cluster_id, s.news_id,
                       {EFFECTIVE_STANCE_EXPR} AS stance_score, s.article_weight,
                       s.relevance_score, s.confidence
                FROM public.event_l2_chains AS c
                JOIN public.event_l2_chain_segments AS cs
                  ON cs.run_id = c.run_id AND cs.chain_id = c.chain_id
                JOIN public.event_l15_members AS lm
                  ON lm.run_id = cs.l15_run_id AND lm.segment_id = cs.segment_id
                JOIN public.china_opinion_article_scores AS s
                  ON s.news_id = lm.news_id
                LEFT JOIN latest_feedback AS lf ON lf.news_id = s.news_id
                WHERE c.run_id = :l2_run_id
                  AND s.published_date = :target_date
                  AND s.directness_score >= 0.55
                  AND s.relevance_score >= 0.4
                  AND {FEEDBACK_VISIBLE_EXPR}
                  AND {VALID_SCORE_EXPR}
                  {sentiment_clause}
            )
            SELECT chain_id, title, event_family, event_action, initiator, target,
                   start_date, end_date, max(article_count) AS article_count,
                   max(segment_count) AS segment_count,
                   count(DISTINCT l1_cluster_id) AS cluster_count,
                   count(DISTINCT news_id) AS china_news_count,
                   100.0 * sum(stance_score * article_weight) / nullif(sum(article_weight), 0) AS impact_index,
                   sum(article_weight) AS heat
            FROM chain_articles
            GROUP BY chain_id, title, event_family, event_action, initiator, target, start_date, end_date
            ORDER BY abs(100.0 * sum(stance_score * article_weight) / nullif(sum(article_weight), 0))
                     * ln(2 + count(DISTINCT news_id)) DESC
            LIMIT 40
            """
        ),
        {
            "l2_run_id": DEFAULT_L2_RUN_ID,
            "target_date": target_date,
            "method_version": METHOD_VERSION,
        },
    ).mappings().fetchall()

    events: list[dict[str, Any]] = []
    trend_start = target_date - timedelta(days=30)
    for row in chain_rows:
        trend_rows = db.execute(
            text(
                f"""
                WITH {LATEST_FEEDBACK_CTE}
                SELECT s.published_date,
                       {EFFECTIVE_STANCE_EXPR} AS stance_score,
                       s.confidence, s.relevance_score
                FROM public.event_l2_chain_segments AS cs
                JOIN public.event_l15_members AS lm
                  ON lm.run_id = cs.l15_run_id AND lm.segment_id = cs.segment_id
                JOIN public.china_opinion_article_scores AS s ON s.news_id = lm.news_id
                LEFT JOIN latest_feedback AS lf ON lf.news_id = s.news_id
                WHERE cs.run_id = :l2_run_id
                  AND cs.chain_id = :chain_id
                  AND s.published_date BETWEEN :trend_start AND :target_date
                  AND s.directness_score >= 0.55
                  AND s.relevance_score >= 0.4
                  AND {FEEDBACK_VISIBLE_EXPR}
                  AND {VALID_SCORE_EXPR}
                  {sentiment_clause}
                """
            ),
            {
                "l2_run_id": DEFAULT_L2_RUN_ID,
                "chain_id": row["chain_id"],
                "trend_start": trend_start,
                "target_date": target_date,
                "method_version": METHOD_VERSION,
            },
        ).mappings().fetchall()
        trend_values = _trend_for_sql_rows(trend_start, target_date, trend_rows)
        impact = round(float(row["impact_index"] or 0.0), 2)
        events.append(
            {
                "macro_id": row["chain_id"],
                "macro_event_id": row["chain_id"],
                "label": row["title"] or row["chain_id"],
                "event_type": row["event_family"],
                "event_action": row["event_action"],
                "initiator": row["initiator"],
                "target": row["target"],
                "start_date": row["start_date"].isoformat() if row["start_date"] else None,
                "end_date": row["end_date"].isoformat() if row["end_date"] else None,
                "member_count": int(row["article_count"] or 0),
                "cluster_count": int(row["cluster_count"] or 0),
                "china_news_count": int(row["china_news_count"] or 0),
                "weighted_stance_index": impact,
                "stance_attention_rank": round(abs(impact) * math.log(2 + int(row["china_news_count"] or 0)), 2),
                "impact_index": impact,
                "daily_impact": impact,
                "china_importance": round(abs(impact) * math.log(2 + int(row["china_news_count"] or 0)), 2),
                "trend_dates": [(trend_start + timedelta(days=i)).isoformat() for i in range(len(trend_values))],
                "trend_values": trend_values,
                "level": "l2",
            }
        )

    if not events:
        fallback_rows = db.execute(
            text(
                f"""
                WITH {LATEST_FEEDBACK_CTE}
                SELECT ec.cluster_id, ec.title, ec.event_family, ec.event_action, ec.initiator, ec.target,
                       ec.article_count,
                       count(DISTINCT s.news_id) AS china_news_count,
                       100.0 * sum(s.stance_score * s.article_weight) / nullif(sum(s.article_weight), 0) AS impact_index
                FROM public.event_coref_members AS ecm
                JOIN public.event_coref_clusters AS ec
                  ON ec.run_id = ecm.run_id AND ec.cluster_id = ecm.cluster_id
                JOIN public.china_opinion_article_scores AS s ON s.news_id = ecm.news_id
                LEFT JOIN latest_feedback AS lf ON lf.news_id = s.news_id
                WHERE ecm.run_id = :l1_run_id
                  AND s.published_date = :target_date
                  AND s.directness_score >= 0.55
                  AND s.relevance_score >= 0.4
                  AND {FEEDBACK_VISIBLE_EXPR}
                  AND {VALID_SCORE_EXPR}
                  {sentiment_clause}
                GROUP BY ec.cluster_id, ec.title, ec.event_family, ec.event_action, ec.initiator, ec.target, ec.article_count
                ORDER BY abs(100.0 * sum(s.stance_score * s.article_weight) / nullif(sum(s.article_weight), 0))
                         * ln(2 + count(DISTINCT s.news_id)) DESC
                LIMIT 40
                """
            ),
            {
                "l1_run_id": DEFAULT_L1_RUN_ID,
                "target_date": target_date,
                "method_version": METHOD_VERSION,
            },
        ).mappings().fetchall()
        for row in fallback_rows:
            impact = round(float(row["impact_index"] or 0.0), 2)
            events.append(
                {
                    "macro_id": row["cluster_id"],
                    "cluster_id": row["cluster_id"],
                    "label": row["title"] or row["cluster_id"],
                    "event_type": row["event_family"],
                    "event_action": row["event_action"],
                    "initiator": row["initiator"],
                    "target": row["target"],
                    "member_count": int(row["article_count"] or 0),
                    "cluster_count": 1,
                    "china_news_count": int(row["china_news_count"] or 0),
                    "weighted_stance_index": impact,
                    "impact_index": impact,
                    "daily_impact": impact,
                    "trend_values": [impact],
                    "level": "l1",
                }
            )

    total_raw_daily = round(sum(float(e.get("daily_impact") or 0.0) for e in events), 2)
    trust, meta = _filtered_trust_snapshot(
        db,
        days=7,
        sentiment_filter=sentiment_filter,
    )
    content = _sanitize_opinion_response(
        jsonable_encoder(
            {
                "ok": True,
                "events": events,
                "total_raw_daily": total_raw_daily,
                "trust": trust,
                "meta": meta,
            }
        ),
        db,
    )
    return JSONResponse(content=content, media_type="application/json; charset=utf-8")


@router.get("/opinion/macro-event-clusters", tags=["舆情"])
def get_macro_event_clusters(
    macro_event_id: str = Query(...),
    date_str: str = Query(...),
    sentiment_filter: str = Query("all"),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_opinion_db),
) -> JSONResponse:
    target_date = date.fromisoformat(date_str)
    sentiment_clause = ""
    if sentiment_filter == "positive":
        sentiment_clause = "AND s.stance_score > 0.15"
    elif sentiment_filter == "negative":
        sentiment_clause = "AND s.stance_score < -0.15"
    offset = (page - 1) * page_size
    rows = db.execute(
        text(
            f"""
            WITH {LATEST_FEEDBACK_CTE},
            cluster_articles AS (
                SELECT cs.l1_cluster_id, seg.title AS segment_title, ec.title AS cluster_title,
                       coalesce(seg.event_family, ec.event_family) AS event_family,
                       coalesce(seg.event_action, ec.event_action) AS event_action,
                       coalesce(seg.initiator, ec.initiator) AS initiator,
                       coalesce(seg.target, ec.target) AS target,
                       ec.article_count, s.news_id,
                       {EFFECTIVE_STANCE_EXPR} AS stance_score, s.article_weight
                FROM public.event_l2_chain_segments AS cs
                JOIN public.event_l15_segments AS seg
                  ON seg.run_id = cs.l15_run_id AND seg.segment_id = cs.segment_id
                LEFT JOIN public.event_coref_clusters AS ec
                  ON ec.run_id = seg.l1_run_id AND ec.cluster_id = seg.l1_cluster_id
                JOIN public.event_l15_members AS lm
                  ON lm.run_id = cs.l15_run_id AND lm.segment_id = cs.segment_id
                JOIN public.china_opinion_article_scores AS s ON s.news_id = lm.news_id
                LEFT JOIN latest_feedback AS lf ON lf.news_id = s.news_id
                WHERE cs.run_id = :l2_run_id
                  AND cs.chain_id = :chain_id
                  AND s.published_date BETWEEN :trend_start AND :target_date
                  AND s.directness_score >= 0.55
                  AND s.relevance_score >= 0.4
                  AND {FEEDBACK_VISIBLE_EXPR}
                  AND {VALID_SCORE_EXPR}
                  {sentiment_clause}
            ),
            ranked AS (
                SELECT l1_cluster_id, max(segment_title) AS segment_title, max(cluster_title) AS cluster_title,
                       max(event_family) AS event_family, max(event_action) AS event_action,
                       max(initiator) AS initiator, max(target) AS target, max(article_count) AS article_count,
                       count(DISTINCT news_id) AS china_news_count,
                       100.0 * sum(stance_score * article_weight) FILTER (WHERE true)
                          / nullif(sum(article_weight) FILTER (WHERE true), 0) AS impact_index
                FROM cluster_articles
                WHERE true
                GROUP BY l1_cluster_id
            )
            SELECT *, count(*) OVER() AS total_count
            FROM ranked
            ORDER BY abs(coalesce(impact_index, 0)) * ln(2 + china_news_count) DESC
            LIMIT :limit OFFSET :offset
            """
        ),
        {
            "l2_run_id": DEFAULT_L2_RUN_ID,
            "chain_id": macro_event_id,
            "target_date": target_date,
            "trend_start": target_date - timedelta(days=30),
            "limit": page_size,
            "offset": offset,
            "method_version": METHOD_VERSION,
        },
    ).mappings().fetchall()

    sub_events = []
    trend_start = target_date - timedelta(days=30)
    for row in rows:
        cluster_id = row["l1_cluster_id"]
        trend_rows = db.execute(
            text(
                f"""
                WITH {LATEST_FEEDBACK_CTE}
                SELECT s.published_date,
                       {EFFECTIVE_STANCE_EXPR} AS stance_score,
                       s.confidence, s.relevance_score
                FROM public.event_coref_members AS ecm
                JOIN public.china_opinion_article_scores AS s ON s.news_id = ecm.news_id
                LEFT JOIN latest_feedback AS lf ON lf.news_id = s.news_id
                WHERE ecm.run_id = :l1_run_id
                  AND ecm.cluster_id = :cluster_id
                  AND s.published_date BETWEEN :trend_start AND :target_date
                  AND s.directness_score >= 0.55
                  AND s.relevance_score >= 0.4
                  AND {FEEDBACK_VISIBLE_EXPR}
                  AND {VALID_SCORE_EXPR}
                  {sentiment_clause}
                """
            ),
            {
                "l1_run_id": DEFAULT_L1_RUN_ID,
                "cluster_id": cluster_id,
                "trend_start": trend_start,
                "target_date": target_date,
                "method_version": METHOD_VERSION,
            },
        ).mappings().fetchall()
        trend_values = _trend_for_sql_rows(trend_start, target_date, trend_rows)
        impact = round(float(row["impact_index"] or 0.0), 2)
        sub_events.append(
            {
                "cluster_id": cluster_id,
                "macro_id": cluster_id,
                "label": row["segment_title"] or row["cluster_title"] or cluster_id,
                "event_type": row["event_family"],
                "event_action": row["event_action"],
                "initiator": row["initiator"],
                "target": row["target"],
                "member_count": int(row["article_count"] or 0),
                "china_news_count": int(row["china_news_count"] or 0),
                "weighted_stance_index": impact,
                "stance_attention_rank": round(abs(impact) * math.log(2 + int(row["china_news_count"] or 0)), 2),
                "impact_index": impact,
                "daily_impact": impact,
                "china_importance": round(abs(impact) * math.log(2 + int(row["china_news_count"] or 0)), 2),
                "trend_dates": [(trend_start + timedelta(days=i)).isoformat() for i in range(len(trend_values))],
                "trend_values": trend_values,
                "level": "l1",
            }
        )

    total = int(rows[0]["total_count"]) if rows else 0
    trust, meta = _filtered_trust_snapshot(
        db,
        days=31,
        sentiment_filter=sentiment_filter,
    )
    content = _sanitize_opinion_response(
        jsonable_encoder(
            {
                "ok": True,
                "sub_events": sub_events,
                "macro_total_clusters": total,
                "china_clusters": total,
                "l1_total_impact": round(sum(float(e["daily_impact"]) for e in sub_events), 2),
                "page": page,
                "page_size": page_size,
                "total": total,
                "has_more": (offset + page_size) < total,
                "trust": trust,
                "meta": meta,
            }
        ),
        db,
    )
    return JSONResponse(content=content, media_type="application/json; charset=utf-8")


@router.get("/opinion/event-news", tags=["舆情"])
def get_event_news(
    cluster_id: Optional[str] = Query(None),
    macro_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_opinion_db),
) -> JSONResponse:
    cid = cluster_id or macro_id
    if not cid:
        return JSONResponse(content={"ok": False, "error": "必须指定 cluster_id 或 macro_id"}, status_code=400)
    offset = (page - 1) * page_size
    trust, meta = _filtered_trust_snapshot(db, days=30)
    total = db.execute(
        text(
            f"""
            WITH {LATEST_FEEDBACK_CTE}
            SELECT count(DISTINCT s.news_id)
            FROM public.event_coref_members AS ecm
            JOIN public.china_opinion_article_scores AS s ON s.news_id = ecm.news_id
            LEFT JOIN latest_feedback AS lf ON lf.news_id = s.news_id
            WHERE ecm.run_id = :l1_run_id
              AND ecm.cluster_id = :cluster_id
              AND s.directness_score >= 0.55
              AND s.relevance_score >= 0.4
              AND {FEEDBACK_VISIBLE_EXPR}
              AND {VALID_SCORE_EXPR}
            """
        ),
        {
            "l1_run_id": DEFAULT_L1_RUN_ID,
            "cluster_id": cid,
            "method_version": METHOD_VERSION,
        },
    ).scalar() or 0
    rows = db.execute(
        text(
            f"""
            WITH {LATEST_FEEDBACK_CTE}
            SELECT DISTINCT ON (n.id)
                   n.id, n.title, (n.published_at AT TIME ZONE 'UTC')::date AS pub_date,
                   {EFFECTIVE_STANCE_EXPR} AS stance_score,
                   s.relevance_score, s.confidence, s.media_domain, s.evidence
            FROM public.event_coref_members AS ecm
            JOIN public.news AS n ON n.id = ecm.news_id
            JOIN public.china_opinion_article_scores AS s ON s.news_id = ecm.news_id
            LEFT JOIN latest_feedback AS lf ON lf.news_id = s.news_id
            WHERE ecm.run_id = :l1_run_id
              AND ecm.cluster_id = :cluster_id
              AND s.directness_score >= 0.55
              AND s.relevance_score >= 0.4
              AND {FEEDBACK_VISIBLE_EXPR}
              AND {VALID_SCORE_EXPR}
            ORDER BY n.id, abs(s.stance_score) * s.article_weight DESC, n.published_at DESC
            LIMIT :limit OFFSET :offset
            """
        ),
        {
            "l1_run_id": DEFAULT_L1_RUN_ID,
            "cluster_id": cid,
            "limit": page_size,
            "offset": offset,
            "method_version": METHOD_VERSION,
        },
    ).mappings().fetchall()
    news_list = [
        {
            "id": int(r["id"]),
            "title": r["title"] or r["evidence"] or "无标题",
            "pub_date": r["pub_date"].isoformat() if r["pub_date"] else None,
            "stance_score": round(float(r["stance_score"] or 0.0), 3),
            "china_index": round(float(r["relevance_score"] or 0.0), 3),
            "confidence": round(float(r["confidence"] or 0.0), 2),
            "source": r["media_domain"] or "",
        }
        for r in rows
    ]
    content = _sanitize_opinion_response(
        jsonable_encoder(
            {
                "ok": True,
                "total": int(total),
                "page": page,
                "page_size": page_size,
                "news": news_list,
                "trust": trust,
                "meta": meta,
            }
        ),
        db,
    )
    return JSONResponse(content=content, media_type="application/json; charset=utf-8")


@router.get("/opinion/news-by-date", tags=["舆情"])
def get_news_by_date(
    date_str: str = Query(..., description="日期 YYYY-MM-DD"),
    sentiment_filter: str = Query("all", description="all / positive / negative"),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_opinion_db),
) -> JSONResponse:
    """Return influential China-stance articles for a clicked timeline date."""
    target_date = date.fromisoformat(date_str)
    trust, meta = _filtered_trust_snapshot(
        db,
        days=7,
        sentiment_filter=sentiment_filter,
    )

    ck = _cache_key(
        "news_by_date_v4",
        method=METHOD_VERSION,
        snapshot_id=trust.get("snapshot_id") or "missing",
        date=target_date.isoformat(),
        sentiment_filter=sentiment_filter,
        page=page,
        page_size=page_size,
    )
    cached = _cache_get(ck)
    if cached is not None:
        content = _sanitize_opinion_response(cached, db)
        return JSONResponse(content=content, media_type="application/json; charset=utf-8")

    sentiment_clause = ""
    if sentiment_filter == "positive":
        sentiment_clause = f"AND {EFFECTIVE_STANCE_EXPR} > 0.15"
    elif sentiment_filter == "negative":
        sentiment_clause = f"AND {EFFECTIVE_STANCE_EXPR} < -0.15"

    params = {
        "target_date": target_date,
        "limit": page_size,
        "offset": (page - 1) * page_size,
        "method_version": METHOD_VERSION,
    }
    total = db.execute(
        text(
            f"""
            WITH {LATEST_FEEDBACK_CTE}
            SELECT count(*)
            FROM public.china_opinion_article_scores AS s
            JOIN public.news AS n ON n.id = s.news_id
            LEFT JOIN latest_feedback AS lf ON lf.news_id = s.news_id
            WHERE s.published_date = :target_date
              AND s.directness_score >= 0.55
              AND s.relevance_score >= 0.4
              AND {FEEDBACK_VISIBLE_EXPR}
              AND {VALID_SCORE_EXPR}
              AND NOT (
                  coalesce(n.url, '') ~ :source_section_url_pattern
                  OR (
                      coalesce(s.media_domain, s.source_domain, '') = 'scmp.com'
                      AND coalesce(n.url, '') !~ :scmp_article_url_pattern
                  )
              )
              {sentiment_clause}
            """
        ),
        {
            **params,
            "source_section_url_pattern": SOURCE_SECTION_URL_PATTERN,
            "scmp_article_url_pattern": SCMP_ARTICLE_URL_PATTERN,
        },
    ).scalar() or 0

    rows = db.execute(
        text(
            f"""
            WITH {LATEST_FEEDBACK_CTE}
            SELECT
                s.news_id,
                n.title,
                n.url,
                s.published_at,
                s.published_date,
                s.media_domain,
                s.source_domain,
                s.language,
                s.region,
                s.event_family,
                s.event_action,
                s.initiator,
                s.target,
                s.china_role,
                s.directness,
                {EFFECTIVE_STANCE_EXPR} AS stance_score,
                lf.correction AS feedback_correction,
                s.confidence,
                s.relevance_score,
                s.article_weight,
                s.evidence,
                ecm.cluster_id
            FROM public.china_opinion_article_scores AS s
            JOIN public.news AS n ON n.id = s.news_id
            LEFT JOIN latest_feedback AS lf ON lf.news_id = s.news_id
            LEFT JOIN public.event_coref_members AS ecm
              ON ecm.news_id = s.news_id
             AND ecm.run_id = :l1_run_id
            WHERE s.published_date = :target_date
              AND s.directness_score >= 0.55
              AND s.relevance_score >= 0.4
              AND {FEEDBACK_VISIBLE_EXPR}
              AND {VALID_SCORE_EXPR}
              AND NOT (
                  coalesce(n.url, '') ~ :source_section_url_pattern
                  OR (
                      coalesce(s.media_domain, s.source_domain, '') = 'scmp.com'
                      AND coalesce(n.url, '') !~ :scmp_article_url_pattern
                  )
              )
              {sentiment_clause}
            ORDER BY abs(({EFFECTIVE_STANCE_EXPR}) * s.article_weight) DESC,
                     s.confidence DESC,
                     s.published_at DESC NULLS LAST
            LIMIT :limit OFFSET :offset
            """
        ),
        {
            **params,
            "l1_run_id": DEFAULT_L1_RUN_ID,
            "source_section_url_pattern": SOURCE_SECTION_URL_PATTERN,
            "scmp_article_url_pattern": SCMP_ARTICLE_URL_PATTERN,
        },
    ).mappings().fetchall()

    news = []
    for row in rows:
        impact_index = round(float(row["stance_score"] or 0.0) * float(row["article_weight"] or 0.0) * 100.0, 1)
        news.append(
            {
                "id": int(row["news_id"]),
                "title": row["title"] or row["evidence"] or "无标题",
                "url": row["url"],
                "pub_date": row["published_date"].isoformat() if row["published_date"] else None,
                "pub_time": row["published_at"].isoformat() if row["published_at"] else None,
                "source": row["media_domain"] or row["source_domain"] or "",
                "language": row["language"],
                "region": row["region"],
                "event_family": row["event_family"],
                "event_action": row["event_action"],
                "initiator": row["initiator"],
                "target": row["target"],
                "china_role": row["china_role"],
                "directness": row["directness"],
                "stance_score": round(float(row["stance_score"] or 0.0), 3),
                "weighted_stance_contribution": impact_index,
                "weighted_stance_contribution_abs": min(100.0, abs(impact_index)),
                "impact_index": impact_index,
                "impact_abs": min(100.0, abs(impact_index)),
                "polarity": "positive" if impact_index > 0 else "negative" if impact_index < 0 else "neutral",
                "china_index": round(float(row["relevance_score"] or 0.0), 3),
                "confidence": round(float(row["confidence"] or 0.0), 2),
                "evidence": row["evidence"],
                "cluster_id": row["cluster_id"],
                "feedback": row["feedback_correction"],
            }
        )

    pos = db.execute(
        text(
            f"""
            WITH {LATEST_FEEDBACK_CTE}
            SELECT
                count(*) FILTER (WHERE {EFFECTIVE_STANCE_EXPR} > 0.15) AS positive_count,
                count(*) FILTER (WHERE {EFFECTIVE_STANCE_EXPR} < -0.15) AS negative_count,
                count(*) FILTER (WHERE {EFFECTIVE_STANCE_EXPR} BETWEEN -0.15 AND 0.15) AS neutral_count,
                count(DISTINCT coalesce(s.media_domain, s.source_domain)) AS source_count
            FROM public.china_opinion_article_scores AS s
            JOIN public.news AS n ON n.id = s.news_id
            LEFT JOIN latest_feedback AS lf ON lf.news_id = s.news_id
            WHERE s.published_date = :target_date
              AND s.directness_score >= 0.55
              AND s.relevance_score >= 0.4
              AND {FEEDBACK_VISIBLE_EXPR}
              AND {VALID_SCORE_EXPR}
              AND NOT (
                  coalesce(n.url, '') ~ :source_section_url_pattern
                  OR (
                      coalesce(s.media_domain, s.source_domain, '') = 'scmp.com'
                      AND coalesce(n.url, '') !~ :scmp_article_url_pattern
                  )
              )
            """
        ),
        {
            "target_date": target_date,
            "source_section_url_pattern": SOURCE_SECTION_URL_PATTERN,
            "scmp_article_url_pattern": SCMP_ARTICLE_URL_PATTERN,
            "method_version": METHOD_VERSION,
        },
    ).mappings().first() or {}

    content = jsonable_encoder(
        {
            "ok": True,
            "date": target_date.isoformat(),
            "total": int(total),
            "page": page,
            "page_size": page_size,
            "news": news,
            "summary": {
                "positive_count": int(pos.get("positive_count") or 0),
                "negative_count": int(pos.get("negative_count") or 0),
                "neutral_count": int(pos.get("neutral_count") or 0),
                "source_count": int(pos.get("source_count") or 0),
            },
            "trust": trust,
            "meta": meta,
        }
    )
    content = _sanitize_opinion_response(content, db)
    _cache_set(ck, content, ttl=180)
    return JSONResponse(content=content, media_type="application/json; charset=utf-8")


@_feedback_router.post("/opinion/feedback", tags=["舆情"])
def submit_opinion_feedback(
    payload: OpinionFeedbackPayload,
    _user: dict[str, Any] = Depends(get_current_user_required),
    db: Session = Depends(get_opinion_db),
) -> JSONResponse:
    """Store a minimal correction in a non-training, review-required queue."""
    correction = str(payload.correction or "").strip().lower()
    if correction not in _VALID_FEEDBACK_CORRECTIONS:
        return _json_response(
            content={
                "ok": False,
                "error": "correction must be one of irrelevant, too_positive, too_negative, correct",
            },
            status_code=400,
            no_store=True,
        )

    try:
        _require_opinion_write_schema(db)
        row = db.execute(
            text(
                """
                INSERT INTO public.china_opinion_feedback (
                    news_id, correction
                )
                VALUES (
                    :news_id, :correction
                )
                RETURNING id, created_at
                """
            ),
            {
                "news_id": int(payload.news_id),
                "correction": correction,
            },
        ).mappings().first()
        db.commit()
    except SQLAlchemyError:
        try:
            db.rollback()
        except SQLAlchemyError:
            pass
        return _json_response(
            {
                "ok": False,
                "error": {
                    "code": "OPINION_FEEDBACK_STORE_UNAVAILABLE",
                    "message": "反馈当前无法安全记录",
                },
            },
            no_store=True,
            status_code=503,
        )
    _RESP_CACHE.clear()
    content = {
        "ok": True,
        "id": int(row["id"]) if row else None,
        "news_id": int(payload.news_id),
        "correction": correction,
        "created_at": row["created_at"].isoformat() if row and row["created_at"] else None,
        "governance": build_feedback_governance_receipt(),
    }
    return _json_response(content, no_store=True)


router.include_router(_feedback_router)


def _dimension_group_rows(
    db: Session,
    *,
    group_expr: str,
    start_d: date,
    latest_date: date,
    limit: int,
) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            f"""
            WITH {LATEST_FEEDBACK_CTE},
            scored AS (
                SELECT
                    {group_expr} AS dimension_key,
                    coalesce(s.media_domain, s.source_domain) AS source_key,
                    {EFFECTIVE_STANCE_EXPR} AS effective_stance,
                    s.article_weight,
                    s.relevance_score
                FROM public.china_opinion_article_scores AS s
                LEFT JOIN latest_feedback AS lf ON lf.news_id = s.news_id
                WHERE s.published_date BETWEEN :start_d AND :latest_date
                  AND s.directness_score >= 0.55
                  AND s.relevance_score >= 0.4
                  AND {FEEDBACK_VISIBLE_EXPR}
                  AND {VALID_SCORE_EXPR}
            )
            SELECT
                coalesce(nullif(dimension_key, ''), 'unknown') AS key,
                count(*) AS article_count,
                count(DISTINCT source_key) AS source_count,
                100.0 * sum(effective_stance * article_weight) / nullif(sum(article_weight), 0) AS impact_index,
                count(*) FILTER (WHERE effective_stance > 0.15) AS positive_count,
                count(*) FILTER (WHERE effective_stance < -0.15) AS negative_count,
                count(*) FILTER (WHERE effective_stance BETWEEN -0.15 AND 0.15) AS neutral_count
            FROM scored
            GROUP BY 1
            HAVING count(*) > 0
            ORDER BY article_count DESC, abs(coalesce(100.0 * sum(effective_stance * article_weight) / nullif(sum(article_weight), 0), 0)) DESC
            LIMIT :limit
            """
        ),
        {
            "start_d": start_d,
            "latest_date": latest_date,
            "limit": limit,
            "method_version": METHOD_VERSION,
        },
    ).mappings().fetchall()
    return [
        {
            "key": r["key"],
            "article_count": int(r["article_count"] or 0),
            "source_count": int(r["source_count"] or 0),
            "weighted_stance_index": round(float(r["impact_index"] or 0.0), 1),
            "impact_index": round(float(r["impact_index"] or 0.0), 1),
            "positive_count": int(r["positive_count"] or 0),
            "negative_count": int(r["negative_count"] or 0),
            "neutral_count": int(r["neutral_count"] or 0),
        }
        for r in rows
    ]


@router.get("/opinion/dimensions", tags=["舆情"])
def get_opinion_dimensions(
    days: int = Query(30, ge=7, le=365),
    limit: int = Query(8, ge=3, le=20),
    db: Session = Depends(get_opinion_db),
) -> JSONResponse:
    """Return compact dimension breakdowns for analyst drill-down."""
    current_date = _current_db_date(db)
    trend = _build_trend_content(
        db,
        days=days,
        china_min_score=0.4,
        sentiment_filter="all",
    )
    trust = trend.get("meta", {}).get("trust", {})
    latest_date = _coerce_date(trust.get("cutoff_date"))
    query_end = latest_date or current_date
    ck = _cache_key(
        "dimensions_v4",
        method=METHOD_VERSION,
        days=days,
        limit=limit,
        snapshot_id=trust.get("snapshot_id") or "missing",
    )
    cached = _cache_get(ck)
    if cached is not None:
        content = _sanitize_opinion_response(cached, db)
        return JSONResponse(content=content, media_type="application/json; charset=utf-8")
    start_d = query_end - timedelta(days=days - 1)
    content = jsonable_encoder({
        "ok": True,
        "start_date": start_d.isoformat(),
        "latest_date": latest_date.isoformat() if latest_date else None,
        "days": days,
        "dimensions": {
            "regions": _dimension_group_rows(db, group_expr="s.region", start_d=start_d, latest_date=query_end, limit=limit),
            "languages": _dimension_group_rows(db, group_expr="s.language", start_d=start_d, latest_date=query_end, limit=limit),
            "sources": _dimension_group_rows(db, group_expr="coalesce(s.media_domain, s.source_domain)", start_d=start_d, latest_date=query_end, limit=limit),
            "families": _dimension_group_rows(db, group_expr="s.event_family", start_d=start_d, latest_date=query_end, limit=limit),
        },
        "trust": trust,
        "meta": trend.get("meta", {}),
    })
    content = _sanitize_opinion_response(content, db)
    _cache_set(ck, content, ttl=300)
    return JSONResponse(content=content, media_type="application/json; charset=utf-8")


@router.get("/opinion/quality", tags=["舆情"])
def get_opinion_quality(db: Session = Depends(get_opinion_db)) -> JSONResponse:
    """Return score coverage and analyst feedback status for the UI quality panel."""
    current_date = _current_db_date(db)
    trust, meta = _filtered_trust_snapshot(db, days=14)
    latest_score_date = _coerce_date(trust.get("cutoff_date"))
    ck = _cache_key(
        "quality_v5",
        method=METHOD_VERSION,
        current_date=current_date.isoformat(),
        snapshot_id=trust.get("snapshot_id") or "missing",
    )
    cached = _cache_get(ck)
    if cached is not None:
        content = _sanitize_opinion_response(cached, db)
        return JSONResponse(content=content, media_type="application/json; charset=utf-8")
    coverage_degraded = False
    try:
        db.execute(text("SET LOCAL max_parallel_workers_per_gather = 0"))
        coverage_rows = db.execute(
            text(
                """
                WITH dates AS (
                    SELECT (published_at AT TIME ZONE 'UTC')::date AS d, count(*) AS news_count
                    FROM public.news
                    WHERE published_at <= now()
                      AND (published_at AT TIME ZONE 'UTC')::date >= :start_d
                    GROUP BY 1
                ),
                l1 AS (
                    SELECT (n.published_at AT TIME ZONE 'UTC')::date AS d, count(DISTINCT n.id) AS l1_count
                    FROM public.news AS n
                    JOIN public.news_l1_event_extractions AS e ON e.news_id = n.id
                    WHERE n.published_at <= now()
                      AND (n.published_at AT TIME ZONE 'UTC')::date >= :start_d
                    GROUP BY 1
                ),
                scored AS (
                    SELECT published_date AS d,
                           count(*) AS scored_count,
                           count(*) FILTER (WHERE relevance_score >= 0.4 AND directness_score >= 0.55) AS scored_relevant
                    FROM public.china_opinion_article_scores
                    WHERE published_date >= :start_d
                      AND method_version = :method_version
                      AND stance_score BETWEEN -1.0 AND 1.0
                      AND confidence BETWEEN 0.0 AND 1.0
                      AND relevance_score BETWEEN 0.0 AND 1.0
                      AND article_weight > 0.0
                      AND article_weight <= 1.0
                    GROUP BY 1
                )
                SELECT dates.d,
                       dates.news_count,
                       coalesce(l1.l1_count, 0) AS l1_count,
                       coalesce(scored.scored_count, 0) AS scored_count,
                       coalesce(scored.scored_relevant, 0) AS scored_relevant
                FROM dates
                LEFT JOIN l1 USING (d)
                LEFT JOIN scored USING (d)
                ORDER BY dates.d DESC
                LIMIT 14
                """
            ),
            {
                "start_d": current_date - timedelta(days=14),
                "method_version": METHOD_VERSION,
            },
        ).mappings().fetchall()
    except SQLAlchemyError:
        db.rollback()
        coverage_degraded = True
        coverage_rows = []
    feedback_rows = db.execute(
        text(
            """
            SELECT correction, count(*) AS n
            FROM public.china_opinion_feedback
            WHERE created_at >= now() - interval '30 days'
            GROUP BY correction
            """
        )
    ).mappings().fetchall()
    pending_feedback = db.execute(
        text(
            """
            SELECT count(*)
            FROM public.china_opinion_feedback
            WHERE created_at >= now() - interval '30 days'
            """
        )
    ).scalar() or 0
    content = {
        "ok": True,
        "method_version": METHOD_VERSION,
        "status": (
            "degraded"
            if coverage_degraded
            or not latest_score_date
            or (current_date - latest_score_date).days > 2
            else "healthy"
        ),
        "freshness": {
            "current_date": current_date.isoformat(),
            "latest_score_date": latest_score_date.isoformat() if latest_score_date else None,
        },
        "coverage_by_date": [
            {
                "date": r["d"].isoformat() if r["d"] else None,
                "news_count": int(r["news_count"] or 0),
                "l1_count": int(r["l1_count"] or 0),
                "scored_count": int(r["scored_count"] or 0),
                "scored_relevant": int(r["scored_relevant"] or 0),
            }
            for r in coverage_rows
        ],
        "feedback_30d": {r["correction"]: int(r["n"] or 0) for r in feedback_rows},
        "pending_feedback_30d": int(pending_feedback),
        "feedback_governance": build_feedback_governance_receipt(),
        "trust": trust,
        "meta": meta,
    }
    content = jsonable_encoder(content)
    content = _sanitize_opinion_response(content, db)
    _cache_set(ck, content, ttl=300)
    return JSONResponse(content=content, media_type="application/json; charset=utf-8")


@router.get("/opinion/top-news", tags=["舆情"])
def get_top_opinion_news(
    days: int = Query(30, ge=1, le=365),
    sentiment_filter: str = Query("all", description="all / positive / negative"),
    event_family: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=50),
    db: Session = Depends(get_opinion_db),
) -> JSONResponse:
    """Return recent high-impact China-stance news for compact insight popovers."""
    current_date = _current_db_date(db)
    trend = _build_trend_content(
        db,
        days=days,
        china_min_score=0.4,
        sentiment_filter=sentiment_filter,
        event_family=event_family,
    )
    trust = trend.get("meta", {}).get("trust", {})
    latest_date = _coerce_date(trust.get("cutoff_date"))
    query_end = latest_date or current_date
    start_d = query_end - timedelta(days=days - 1)

    clauses = [
        "s.published_date BETWEEN :start_d AND :latest_date",
        "s.directness_score >= 0.55",
        "s.relevance_score >= 0.4",
        """NOT (
            coalesce(n.url, '') ~ :source_section_url_pattern
            OR (
                coalesce(s.media_domain, s.source_domain, '') = 'scmp.com'
                AND coalesce(n.url, '') !~ :scmp_article_url_pattern
            )
        )""",
    ]
    params: dict[str, Any] = {
        "start_d": start_d,
        "latest_date": query_end,
        "method_version": METHOD_VERSION,
        "limit": page_size,
        "offset": (page - 1) * page_size,
        "source_section_url_pattern": SOURCE_SECTION_URL_PATTERN,
        "scmp_article_url_pattern": SCMP_ARTICLE_URL_PATTERN,
    }
    if sentiment_filter == "positive":
        clauses.append(f"{EFFECTIVE_STANCE_EXPR} > 0.15")
    elif sentiment_filter == "negative":
        clauses.append(f"{EFFECTIVE_STANCE_EXPR} < -0.15")
    if event_family:
        clauses.append("s.event_family = :event_family")
        params["event_family"] = event_family

    where_sql = " AND ".join(clauses)
    ck = _cache_key(
        "top_news_v4",
        method=METHOD_VERSION,
        days=days,
        snapshot_id=trust.get("snapshot_id") or "missing",
        sentiment_filter=sentiment_filter,
        event_family=event_family or "",
        page=page,
        page_size=page_size,
    )
    cached = _cache_get(ck)
    if cached is not None:
        content = _sanitize_opinion_response(cached, db)
        return JSONResponse(content=content, media_type="application/json; charset=utf-8")

    total = db.execute(
        text(
            f"""
            WITH {LATEST_FEEDBACK_CTE}
            SELECT count(*)
            FROM public.china_opinion_article_scores AS s
            JOIN public.news AS n ON n.id = s.news_id
            LEFT JOIN latest_feedback AS lf ON lf.news_id = s.news_id
            WHERE {where_sql}
              AND {FEEDBACK_VISIBLE_EXPR}
              AND {VALID_SCORE_EXPR}
            """
        ),
        params,
    ).scalar() or 0

    rows = db.execute(
        text(
            f"""
            WITH {LATEST_FEEDBACK_CTE}
            SELECT
                s.news_id,
                n.title,
                n.url,
                s.published_at,
                s.published_date,
                s.media_domain,
                s.source_domain,
                s.language,
                s.region,
                s.event_family,
                s.event_action,
                s.initiator,
                s.target,
                s.china_role,
                s.directness,
                {EFFECTIVE_STANCE_EXPR} AS stance_score,
                lf.correction AS feedback_correction,
                s.confidence,
                s.relevance_score,
                s.article_weight,
                s.evidence
            FROM public.china_opinion_article_scores AS s
            JOIN public.news AS n ON n.id = s.news_id
            LEFT JOIN latest_feedback AS lf ON lf.news_id = s.news_id
            WHERE {where_sql}
              AND {FEEDBACK_VISIBLE_EXPR}
              AND {VALID_SCORE_EXPR}
            ORDER BY abs(({EFFECTIVE_STANCE_EXPR}) * s.article_weight) DESC,
                     s.confidence DESC,
                     s.published_at DESC NULLS LAST
            LIMIT :limit OFFSET :offset
            """
        ),
        params,
    ).mappings().fetchall()

    news = []
    for row in rows:
        impact_index = round(float(row["stance_score"] or 0.0) * float(row["article_weight"] or 0.0) * 100.0, 1)
        news.append(
            {
                "id": int(row["news_id"]),
                "title": row["title"] or row["evidence"] or "无标题",
                "url": row["url"],
                "pub_date": row["published_date"].isoformat() if row["published_date"] else None,
                "pub_time": row["published_at"].isoformat() if row["published_at"] else None,
                "source": row["media_domain"] or row["source_domain"] or "",
                "language": row["language"],
                "region": row["region"],
                "event_family": row["event_family"],
                "event_action": row["event_action"],
                "initiator": row["initiator"],
                "target": row["target"],
                "china_role": row["china_role"],
                "directness": row["directness"],
                "stance_score": round(float(row["stance_score"] or 0.0), 3),
                "weighted_stance_contribution": impact_index,
                "weighted_stance_contribution_abs": min(100.0, abs(impact_index)),
                "impact_index": impact_index,
                "impact_abs": min(100.0, abs(impact_index)),
                "polarity": "positive" if impact_index > 0 else "negative" if impact_index < 0 else "neutral",
                "china_index": round(float(row["relevance_score"] or 0.0), 3),
                "confidence": round(float(row["confidence"] or 0.0), 2),
                "evidence": row["evidence"],
                "feedback": row["feedback_correction"],
            }
        )

    content = jsonable_encoder(
        {
            "ok": True,
            "total": int(total),
            "page": page,
            "page_size": page_size,
            "start_date": start_d.isoformat(),
            "latest_date": latest_date.isoformat() if latest_date else None,
            "filters": {
                "days": days,
                "sentiment_filter": sentiment_filter,
                "event_family": event_family,
            },
            "news": news,
            "trust": trust,
            "meta": trend.get("meta", {}),
        }
    )
    content = _sanitize_opinion_response(content, db)
    _cache_set(ck, content, ttl=180)
    return JSONResponse(content=content, media_type="application/json; charset=utf-8")
