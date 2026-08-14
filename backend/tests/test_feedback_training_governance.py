from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from api.features.opinion import (
    EFFECTIVE_STANCE_EXPR,
    FEEDBACK_VISIBLE_EXPR,
    LATEST_FEEDBACK_CTE,
    FeedbackTrainingUseBlocked,
    OpinionFeedbackPayload,
    require_feedback_training_approval,
)
from api.routes import opinion_v2


class _FeedbackResult:
    def __init__(self, row: dict[str, Any]) -> None:
        self._row = row

    def mappings(self) -> _FeedbackResult:
        return self

    def first(self) -> dict[str, Any]:
        return self._row


class _FeedbackSession:
    def __init__(self) -> None:
        self.statement = ""
        self.parameters: dict[str, Any] = {}
        self.commits = 0

    def execute(
        self,
        statement: Any,
        parameters: dict[str, Any] | None = None,
    ) -> _FeedbackResult:
        self.statement = str(statement)
        self.parameters = dict(parameters or {})
        return _FeedbackResult(
            {
                "id": 17,
                "created_at": datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc),
            }
        )

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        return None


class _FailingFeedbackSession(_FeedbackSession):
    def __init__(self) -> None:
        super().__init__()
        self.rollbacks = 0

    def execute(
        self,
        statement: Any,
        parameters: dict[str, Any] | None = None,
    ) -> _FeedbackResult:
        del statement, parameters
        raise SQLAlchemyError("postgresql" + "://private-user:private-password@db.invalid")

    def rollback(self) -> None:
        self.rollbacks += 1


def _governed_payload() -> OpinionFeedbackPayload:
    return OpinionFeedbackPayload(
        news_id=7,
        correction="correct",
        purpose="quality_correction",
        training_consent=False,
        training_opt_out=True,
    )


def _feedback_client(
    monkeypatch: pytest.MonkeyPatch,
    session: _FeedbackSession,
) -> TestClient:
    app = FastAPI()
    app.include_router(opinion_v2.router)
    app.dependency_overrides[opinion_v2.get_current_user_required] = lambda: {
        "id": 22,
        "username": "analyst",
    }
    app.dependency_overrides[opinion_v2.get_opinion_db] = lambda: session
    monkeypatch.setattr(opinion_v2, "_require_opinion_write_schema", lambda _db: None)
    return TestClient(app, raise_server_exceptions=False)


def test_feedback_requires_explicit_non_training_purpose_and_opt_out() -> None:
    with pytest.raises(ValidationError):
        OpinionFeedbackPayload(news_id=7, correction="correct")

    assert _governed_payload().model_dump() == {
        "news_id": 7,
        "correction": "correct",
        "purpose": "quality_correction",
        "training_consent": False,
        "training_opt_out": True,
    }


@pytest.mark.parametrize(
    "override",
    [
        {"purpose": "model_training"},
        {"training_consent": True},
        {"training_opt_out": False},
        {"note": "contact me at analyst@example.test"},
        {"page": "arbitrary-client-page"},
        {"unexpected_approval": "approved"},
        {"news_id": True},
    ],
)
def test_feedback_contract_rejects_training_or_unbounded_content(
    override: dict[str, Any],
) -> None:
    values: dict[str, Any] = {
        "news_id": 7,
        "correction": "correct",
        "purpose": "quality_correction",
        "training_consent": False,
        "training_opt_out": True,
    }
    values.update(override)
    with pytest.raises(ValidationError):
        OpinionFeedbackPayload(**values)


def test_feedback_write_is_minimal_and_returns_fail_closed_governance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FeedbackSession()
    monkeypatch.setattr(opinion_v2, "_require_opinion_write_schema", lambda _db: None)

    response = opinion_v2.submit_opinion_feedback(
        _governed_payload(),
        _user={"id": 22, "username": "analyst"},
        db=session,
    )

    assert session.commits == 1
    assert session.parameters == {"news_id": 7, "correction": "correct"}
    normalized_sql = " ".join(session.statement.lower().split())
    assert "( news_id, correction )" in normalized_sql
    assert all(
        field not in normalized_sql
        for field in ("note", "page", "current_impact_index", "sentiment")
    )

    body = json.loads(response.body)
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert body["governance"] == {
        "schema_version": "opinion-feedback-governance.v1",
        "purpose": "quality_correction",
        "stored_content": "structured_label_only",
        "free_text_accepted": False,
        "training_consent": False,
        "training_opt_out": True,
        "training_use_status": "prohibited_without_approval",
        "training_export_status": "not_configured",
        "deidentification_status": "not_verified",
        "retention_status": "not_approved",
        "retention_period_days": None,
        "review_state": "review_required",
        "eligible_for_training": False,
        "eligible_for_gold": False,
    }


def test_training_status_cannot_be_enabled_by_request_fields() -> None:
    payload = _governed_payload()
    assert not hasattr(payload, "training_use_status")
    assert not hasattr(payload, "review_state")
    assert not hasattr(payload, "retention_status")


def test_unreviewed_feedback_is_not_projected_and_self_assertion_cannot_approve_it() -> None:
    assert "FROM public.china_opinion_feedback" not in LATEST_FEEDBACK_CTE
    assert "WHERE FALSE" in LATEST_FEEDBACK_CTE
    assert EFFECTIVE_STANCE_EXPR == "s.stance_score"
    assert FEEDBACK_VISIBLE_EXPR == "TRUE"

    with pytest.raises(
        FeedbackTrainingUseBlocked,
        match="FEEDBACK_TRAINING_GOVERNANCE_NOT_CONFIGURED",
    ):
        require_feedback_training_approval(
            {
                "legal": "approved",
                "privacy": "approved",
                "model_owner": "approved",
                "human_review": "approved",
            }
        )


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        (
            b'{"news_id":7,"correction":"correct","purpose":"quality_correction",'
            b'"training_consent":true,"training_consent":false,"training_opt_out":true}',
            "application/json",
        ),
        (
            b'{"news_id":7,"correction":"correct","purpose":"quality_correction",'
            b'"training_consent":false,"training_opt_out":true,"extra":NaN}',
            "application/json",
        ),
        (
            b'{"news_id":7,"correction":"correct","purpose":"quality_correction",'
            b'"training_consent":false,"training_opt_out":true,"extra":1e400}',
            "application/json",
        ),
        (
            json.dumps(
                {
                    "news_id": 7,
                    "correction": "correct",
                    "purpose": "quality_correction",
                    "training_consent": False,
                    "training_opt_out": True,
                    "extra": [[[[[["too-deep"]]]]]],
                }
            ).encode("utf-8"),
            "application/json",
        ),
        (
            b'{"news_id":7,"correction":"correct","purpose":"quality_correction",'
            b'"training_consent":false,"training_opt_out":true}' + b" " * 4096,
            "application/json",
        ),
        (b"[]", "application/json"),
        (b"news_id=7", "application/x-www-form-urlencoded"),
    ],
    ids=("duplicate", "nan", "overflow", "depth", "size", "root", "media-type"),
)
def test_feedback_route_rejects_ambiguous_json_before_storage(
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
    content_type: str,
) -> None:
    session = _FeedbackSession()
    client = _feedback_client(monkeypatch, session)

    response = client.post(
        "/opinion/feedback",
        content=body,
        headers={"content-type": content_type},
    )

    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.json() == {
        "detail": {
            "code": "OPINION_FEEDBACK_JSON_AMBIGUOUS",
            "message": "反馈正文必须是无重复键、有限且有界的 JSON 对象",
        }
    }
    assert session.statement == ""
    assert session.commits == 0


def test_feedback_contract_error_is_redacted_and_no_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _feedback_client(monkeypatch, _FeedbackSession())

    response = client.post(
        "/opinion/feedback",
        content=b'{"news_id":7,"correction":"correct"}',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.json() == {
        "detail": {
            "code": "OPINION_FEEDBACK_CONTRACT_INVALID",
            "message": "反馈字段不符合结构化非训练用途契约",
        }
    }


def test_feedback_storage_error_never_exposes_exception_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FailingFeedbackSession()
    client = _feedback_client(monkeypatch, session)

    response = client.post(
        "/opinion/feedback",
        json={
            "news_id": 7,
            "correction": "correct",
            "purpose": "quality_correction",
            "training_consent": False,
            "training_opt_out": True,
        },
    )

    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.json() == {
        "ok": False,
        "error": {
            "code": "OPINION_FEEDBACK_STORE_UNAVAILABLE",
            "message": "反馈当前无法安全记录",
        },
    }
    assert "private-password" not in response.text
    assert session.rollbacks == 1
