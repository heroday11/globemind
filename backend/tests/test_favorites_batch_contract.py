from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.core.db import get_db
from api.features.identity import contracts
from api.orm import models
from api.routes import auth
from api.services.auth import get_current_user_required


class _RowsQuery:
    def __init__(self, db: "_FavoriteDb") -> None:
        self._db = db

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def with_for_update(self, *_args, **_kwargs):
        return self

    def all(self):
        return list(self._db.rows)

    def first(self):
        return self._db.rows[0] if self._db.rows else None


class _FavoriteDb:
    def __init__(self, rows=None) -> None:
        self.rows = list(rows or [])
        self.commit_count = 0
        self.rollback_count = 0

    def query(self, *_args, **_kwargs):
        return _RowsQuery(self)

    def add(self, row) -> None:
        self.rows.append(row)

    def delete(self, row) -> None:
        self.rows.remove(row)

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1


def _row(
    news_id: int,
    *,
    topic: str = "policy",
    kind: str = "favorite",
) -> models.UserFavorite:
    return models.UserFavorite(
        id=news_id,
        user_id=7,
        news_id=news_id,
        topic=topic,
        item_kind=kind,
        created_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize(
    "request_type,payload",
    [
        (contracts.FavoriteToggleRequest, {"news_id": "1"}),
        (contracts.FavoriteToggleRequest, {"news_id": True}),
        (contracts.FavoriteToggleRequest, {"news_id": 0}),
        (contracts.FavoriteRemoveRequest, {"news_id": -1}),
        (contracts.FavoriteRemoveRequest, {"news_id": 1, "extra": "ignored"}),
        (contracts.FavoriteRemoveRequest, {"news_id": 1, "topic": "bad\u0000tag"}),
        (contracts.FavoriteRemoveRequest, {"news_id": 1, "kind": "other"}),
    ],
)
def test_single_favorite_contracts_reject_ambiguous_values(request_type, payload):
    with pytest.raises(ValidationError):
        request_type.model_validate(payload)


def test_list_contract_excludes_warning_ids_and_reports_honest_counts():
    db = _FavoriteDb([_row(10), _row(11, kind="warning"), _row(10, topic="second")])

    payload = auth.get_user_favorites(user={"user_id": 7}, db=db)

    assert payload["schema_version"] == "user-favorites-v2"
    assert payload["news_ids"] == [10]
    assert payload["counts"] == {
        "favorite_records": 2,
        "warning_records": 1,
        "invalid_records": 0,
        "distinct_favorite_news": 1,
    }


def test_unbound_identity_is_rejected_instead_of_becoming_an_empty_collection():
    with pytest.raises(HTTPException) as exc_info:
        auth.get_user_favorites(user={"user_id": 0}, db=_FavoriteDb())
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "FAVORITES_ACCOUNT_UNAVAILABLE"

    with pytest.raises(HTTPException) as malformed:
        auth.get_user_favorites(user={"user_id": "not-an-id"}, db=_FavoriteDb())
    assert malformed.value.status_code == 403


def test_batch_contract_has_a_hard_limit_and_rejects_duplicate_keys():
    request_type = getattr(contracts, "FavoriteBatchRequest", None)
    assert request_type is not None

    duplicate = {
        "operations": [
            {"news_id": 1, "topic": "policy", "kind": "favorite", "favorited": True},
            {"news_id": 1, "topic": "policy", "kind": "favorite", "favorited": False},
        ]
    }
    with pytest.raises(ValidationError):
        request_type.model_validate(duplicate)

    too_many = {
        "operations": [
            {"news_id": index + 1, "favorited": True}
            for index in range(101)
        ]
    }
    with pytest.raises(ValidationError):
        request_type.model_validate(too_many)


def test_batch_set_is_atomic_optimistic_and_idempotent_on_replay():
    request_type = getattr(contracts, "FavoriteBatchRequest", None)
    assert request_type is not None
    db = _FavoriteDb()
    body = request_type.model_validate(
        {
            "operations": [
                {
                    "news_id": 42,
                    "topic": "policy",
                    "kind": "favorite",
                    "favorited": True,
                    "expected_favorited": False,
                }
            ]
        }
    )

    first = auth.batch_set_user_favorites(body=body, user={"user_id": 7}, db=db)
    assert first["schema_version"] == "favorite-batch-v1"
    assert first["applied"] == 1
    assert first["unchanged"] == 0
    assert len(db.rows) == 1

    replay = request_type.model_validate(
        {
            "operations": [
                {
                    "news_id": 42,
                    "topic": "policy",
                    "kind": "favorite",
                    "favorited": True,
                }
            ]
        }
    )
    second = auth.batch_set_user_favorites(body=replay, user={"user_id": 7}, db=db)
    assert second["applied"] == 0
    assert second["unchanged"] == 1
    assert len(db.rows) == 1
    assert db.commit_count == 1

    stale = request_type.model_validate(
        {
            "operations": [
                {
                    "news_id": 42,
                    "topic": "policy",
                    "kind": "favorite",
                    "favorited": False,
                    "expected_favorited": False,
                }
            ]
        }
    )
    with pytest.raises(HTTPException) as exc_info:
        auth.batch_set_user_favorites(body=stale, user={"user_id": 7}, db=db)
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "FAVORITES_STATE_CONFLICT"
    assert len(db.rows) == 1


def _favorites_app(db: _FavoriteDb) -> FastAPI:
    app = FastAPI()
    app.include_router(auth.router)
    app.dependency_overrides[get_current_user_required] = lambda: {
        "user_id": 7,
        "username": "researcher",
    }
    app.dependency_overrides[get_db] = lambda: db
    return app


@pytest.mark.parametrize(
    "body,content_type",
    [
        (
            b'{"operations":[{"news_id":1,"news_id":2,"favorited":true}]}',
            "application/json",
        ),
        (b'{"operations":[{"news_id":NaN,"favorited":true}]}', "application/json"),
        (b'{"operations":[{"news_id":1e400,"favorited":true}]}', "application/json"),
        (
            json.dumps(
                {"operations": [{"news_id": 1, "favorited": True, "extra": [[[[[[{}]]]]]]}]}
            ).encode("utf-8"),
            "application/json",
        ),
        (b'{"operations":[]}', "text/plain"),
        (b" " * (64 * 1024 + 1), "application/json"),
    ],
    ids=[
        "duplicate-key",
        "nan",
        "overflow",
        "too-deep",
        "wrong-media-type",
        "oversized",
    ],
)
def test_batch_http_boundary_rejects_ambiguous_json_before_mutation(body, content_type):
    db = _FavoriteDb()
    response = TestClient(_favorites_app(db)).post(
        "/api/user/favorites/batch",
        content=body,
        headers={"content-type": content_type},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "FAVORITES_JSON_AMBIGUOUS"
    assert db.rows == []
    assert db.commit_count == 0


def test_favorites_routes_require_authentication():
    app = FastAPI()
    app.include_router(auth.router)
    client = TestClient(app)

    assert client.get("/api/user/favorites").status_code == 401
    assert (
        client.post(
            "/api/user/favorites/batch",
            json={"operations": [{"news_id": 1, "favorited": True}]},
        ).status_code
        == 401
    )
