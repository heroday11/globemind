from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from api.features.evidence import (
    ClaimType,
    build_article_evidence_chain,
    locate_paragraph_citations,
    normalize_claim_type,
    split_article_paragraphs,
)
from api.routes import dashboard


def _article(**changes):
    values = {
        "id": 42,
        "title": "China policy headline",
        "body": "Opening context without a country.\nBeijing announced the policy in parliament.",
        "request_url": "https://example.test/articles/42",
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_claim_type_is_closed_and_unknown_is_the_safe_fallback() -> None:
    assert normalize_claim_type("information") is ClaimType.INFORMATION
    assert normalize_claim_type("hypothesis") is ClaimType.HYPOTHESIS
    assert normalize_claim_type("judgment") is ClaimType.JUDGMENT
    assert normalize_claim_type("indicator") is ClaimType.INDICATOR
    assert normalize_claim_type("invented") is ClaimType.UNKNOWN


def test_title_is_never_reissued_as_paragraph_evidence() -> None:
    citations, rejected = locate_paragraph_citations(
        article_id=42,
        title="China policy headline",
        body="This paragraph discusses unrelated market activity.",
        evidence_fragments=("China policy headline",),
    )

    assert citations == []
    assert rejected is True

    chain = build_article_evidence_chain(
        _article(body="This paragraph discusses unrelated market activity."),
        {
            "china_analysis": {
                "source": "score-model-v1",
                "is_china_related": True,
                "relevance_score": 0.8,
                "impact_index": -12.3,
                "confidence": 0.7,
                "evidence": "China policy headline",
            }
        },
    )
    assert chain["claims"][0]["evidence_status"] == "unavailable"
    assert chain["claims"][0]["unavailable_reason"] == "TITLE_ONLY_EVIDENCE_REJECTED"
    assert chain["claims"][0]["citations"] == []


def test_real_body_terms_resolve_to_stable_article_paragraph_anchors() -> None:
    article = _article()
    chain = build_article_evidence_chain(
        article,
        {
            "china_analysis": {
                "source": "score-model-v1",
                "is_china_related": True,
                "relevance_score": 0.8,
                "impact_index": -12.3,
                "confidence": 0.7,
                "evidence": "ruleset explanation, not a quote",
            },
            "event_extraction": {
                "initiator": "Beijing",
                "target": "parliament",
                "event_action": "announced",
                "processor_version": "event-v2",
            },
        },
    )

    assert chain["schema_version"] == "article-evidence-v1"
    assert chain["paragraph_count"] == len(split_article_paragraphs(article.body)) == 2
    assert chain["claims"][0]["claim_type"] == "judgment"
    assert chain["claims"][0]["evidence_status"] == "available"
    assert chain["claims"][0]["citations"][0]["anchor_id"] == (
        "article-42-paragraph-2"
    )
    assert "Beijing" in chain["claims"][0]["citations"][0]["excerpt"]
    event_claims = [
        claim for claim in chain["claims"] if claim["claim_type"] == "hypothesis"
    ]
    assert len(event_claims) == 3
    assert all(claim["evidence_status"] == "available" for claim in event_claims)


def test_citation_source_locator_drops_credentials_query_and_fragment() -> None:
    chain = build_article_evidence_chain(
        _article(
            request_url=(
                "https://user:secret@example.test/articles/42"
                "?token=private#fragment"
            )
        ),
        {
            "event_extraction": {
                "initiator": "Beijing",
                "processor_version": "event-v2",
            },
        },
    )

    citation = chain["claims"][0]["citations"][0]
    assert citation["source_url"] == "https://example.test/articles/42"


def test_missing_body_and_provenance_metadata_are_explicitly_unavailable() -> None:
    chain = build_article_evidence_chain(
        _article(body=""),
        {
            "china_analysis": {
                "source": "score-model-v1",
                "is_china_related": True,
                "evidence": "China policy headline",
            }
        },
    )

    assert chain["provenance"] == {
        "body_status": "unavailable",
        "response_body_sha256": None,
        "hash_scope": None,
        "snapshot_status": "unavailable",
        "snapshot_id": None,
        "captured_at": None,
        "parser_version": None,
        "update_status": "unavailable",
        "correction_status": "unavailable",
    }
    assert all(claim["evidence_status"] == "unavailable" for claim in chain["claims"])


def test_article_analysis_route_attaches_the_evidence_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        dashboard,
        "get_news_analysis_v2",
        lambda news_id: {
            "items": [],
            "china_analysis": {
                "source": "score-model-v1",
                "is_china_related": True,
                "evidence": "China policy headline",
            },
        },
    )
    monkeypatch.setattr(dashboard, "get_news_by_id_v2", lambda news_id: _article(id=news_id))

    response = dashboard.get_news_analysis(42, db=None)
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload["evidence_chain"]["article_id"] == 42
    assert payload["evidence_chain"]["claims"][0]["citations"][0][
        "anchor_id"
    ] == "article-42-paragraph-2"


def test_article_reader_contract_includes_the_same_evidence_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    article = _article()
    monkeypatch.setattr(dashboard, "get_news_by_id", lambda news_id, db: article)
    monkeypatch.setattr(
        dashboard,
        "get_news_analysis_v2",
        lambda news_id: {
            "china_analysis": {
                "source": "score-model-v1",
                "is_china_related": True,
                "evidence": "China policy headline",
            }
        },
    )

    response = dashboard.get_article_reader(42, db=None)

    assert response.analysis is not None
    assert response.analysis["evidence_chain"]["article_id"] == 42
    assert response.analysis["evidence_chain"]["claims"][0]["citations"][0][
        "anchor_id"
    ] == "article-42-paragraph-2"


def test_article_reader_keeps_an_unavailable_evidence_contract_without_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    article = _article()
    monkeypatch.setattr(dashboard, "get_news_by_id", lambda news_id, db: article)
    monkeypatch.setattr(dashboard, "get_news_analysis_v2", lambda news_id: None)

    response = dashboard.get_article_reader(42, db=None)

    assert response.analysis is not None
    chain = response.analysis["evidence_chain"]
    assert chain["schema_version"] == "article-evidence-v1"
    assert chain["claims"][0]["claim_type"] == "unknown"
    assert chain["claims"][0]["evidence_status"] == "unavailable"
    assert chain["claims"][0]["unavailable_reason"] == "ANALYSIS_UNAVAILABLE"
