"""
涉华舆情 API 集成测试。

用 FastAPI TestClient + mock DB session 测试 14 个涉华端点的响应格式。
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from api.application import app
from api.core.db import get_db
from api.features.opinion import evaluate_opinion_trust
from api.routes import opinion_v2
from api.routes.opinion_v2 import get_opinion_db
from api.services.auth import get_current_admin_user

# ── Mock DB session ────────────────────────────────────────────────────


class MockRow:
    """模拟 sqlalchemy Row / Mapping 对象。"""
    def __init__(self, **kwargs):
        self._store = dict(kwargs)
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __getitem__(self, key):
        return self._store[key]

    def get(self, key, default=None):
        return self._store.get(key, default)


class MockScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value

    def scalars(self):
        class ScalarIter:
            def __init__(self, vals):
                self._vals = vals if isinstance(vals, list) else [vals]

            def all(self):
                return self._vals

        return ScalarIter(self._value)

    def fetchall(self):
        return []

    def mappings(self):
        return self

    def one(self):
        return (0, 0.0, 0.0)

    def first(self):
        return None

    @property
    def rowcount(self):
        return 0


class MockMappingResult:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows

    def scalar(self):
        if not self._rows:
            return 0
        keys = list(self._rows[0].keys())
        return self._rows[0][keys[0]] if keys else 0

    def scalars(self):
        class ScalarIter:
            def __init__(self, vals):
                self._vals = vals

            def all(self):
                return self._vals

        return ScalarIter(self._rows)

    def fetchall(self):
        return self._rows

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def one(self):
        if len(self._rows) != 1:
            raise AssertionError(f"expected exactly one row, got {len(self._rows)}")
        return self._rows[0]

    @property
    def rowcount(self):
        return len(self._rows)

    def __iter__(self):
        return iter(self._rows)


class MockDB:
    """
    Mock SQLAlchemy Session — 对任意 SQL 返回空或固定假数据。
    各测试可调用 .set_result(sql_pattern, rows) 覆写指定查询的返回。
    """
    def __init__(self):
        self._overrides: Dict[str, Any] = {}
        self.statements: list[str] = []
        self.commit_calls = 0
        self.rollback_calls = 0

    def set_result(self, sql_pattern: str, rows: Any) -> None:
        self._overrides[sql_pattern] = rows

    def execute(self, stmt, params=None):
        sql_str = str(stmt) if not isinstance(stmt, str) else stmt
        self.statements.append(sql_str)
        if re.search(r"\b(CREATE|ALTER|DROP|TRUNCATE|INSERT|UPDATE|DELETE|MERGE|COPY)\b", sql_str, re.IGNORECASE):
            raise AssertionError(f"read-only mock rejected mutating SQL: {sql_str[:120]}")
        # 找最近匹配的覆写
        for pattern, result in self._overrides.items():
            if pattern in sql_str:
                if isinstance(result, list):
                    return MockMappingResult(result)
                return MockScalarResult(result)
        # 默认空返回
        if "COUNT(*)" in sql_str or "COUNT(DISTINCT" in sql_str:
            return MockScalarResult(0)
        if "EXISTS" in sql_str:
            return MockScalarResult(0)
        if "MAX(" in sql_str:
            return MockScalarResult(None)
        return MockMappingResult([])

    def commit(self):
        self.commit_calls += 1
        raise AssertionError("read-only mock rejected commit")

    def rollback(self):
        self.rollback_calls += 1

    def close(self):
        pass


@pytest.fixture
def mock_db():
    return MockDB()


@pytest.fixture
def client(mock_db):
    """Override both the legacy and V2 opinion database dependencies."""
    def override_get_db():
        yield mock_db
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_opinion_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ── 基础响应测试 ────────────────────────────────────────────────────


class TestChinaTrend:
    def test_returns_json_structure(self, client: TestClient, mock_db: MockDB):
        """china-trend 返回正确的 JSON 结构（即使无数据）。"""
        resp = client.get("/api/opinion/china-trend?days=30")
        assert resp.status_code == 200
        data = resp.json()
        assert "dates" in data
        assert "values" in data
        assert "meta" in data
        assert data["meta"]["total_articles"] == 0
        assert data["meta"]["trust"]["is_computable"] is False
        assert data["meta"]["trust"]["status"] == "unavailable"
        assert data["dates"] == []
        assert data["values"] == []
        assert data["meta"]["avg_impact"] is None
        assert data["meta"]["composite_suppressed"] is True
        assert len(data["dates"]) == len(data["values"])

    def test_with_sentiment_data(self, client: TestClient, mock_db: MockDB):
        """无覆写数据时 china-trend 保持稳定的空结果契约。"""
        resp = client.get("/api/opinion/china-trend?days=30")
        assert resp.status_code == 200
        data = resp.json()
        assert data["meta"]["total_articles"] == 0  # no mock data → empty

    def test_sentiment_filter(self, client: TestClient, mock_db: MockDB):
        """sentiment_filter 参数生效（无数据时返回空，格式正确）。"""
        pos = client.get("/api/opinion/china-trend?days=30&sentiment_filter=positive")
        assert pos.status_code == 200
        data = pos.json()
        assert data["meta"]["total_articles"] == 0

        neg = client.get("/api/opinion/china-trend?days=30&sentiment_filter=negative")
        assert neg.status_code == 200

        neg = client.get("/api/opinion/china-trend?days=30&sentiment_filter=negative")
        assert neg.status_code == 200

    def test_cache_works(self, client: TestClient, mock_db: MockDB):
        """缓存不影响响应结构。"""
        resp1 = client.get("/api/opinion/china-trend?days=365")
        resp2 = client.get("/api/opinion/china-trend?days=365")
        assert resp1.status_code == 200
        assert resp2.status_code == 200

    def test_cache_hit_is_reaged_and_sanitized_before_return(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ):
        trust = evaluate_opinion_trust(
            current_date=date(2026, 8, 9),
            cutoff_date=date(2026, 8, 8),
            article_count=10,
            source_count=3,
        )
        cached = {
            "dates": ["2026-08-08"],
            "values": [28.4],
            "heat": [2.0],
            "meta": {"last_article_date": "2026-08-08", "trust": trust},
        }
        monkeypatch.setattr(opinion_v2, "_cache_get", lambda _key: cached)
        monkeypatch.setattr(opinion_v2, "_current_db_date", lambda _db: date(2026, 8, 12))

        response = client.get("/api/opinion/china-trend?days=31")

        assert response.status_code == 200
        payload = response.json()
        assert payload["values"] == []
        assert payload["meta"]["composite_suppressed"] is True
        assert payload["trust"]["freshness"]["age_days"] == 4
        assert "STALE_DATA" in payload["trust"]["reason_codes"]


class TestOpinionOverviewTrust:
    def test_empty_or_stale_inputs_never_publish_exact_composite(self, client: TestClient):
        resp = client.get("/api/opinion/overview?days=30")
        assert resp.status_code == 200
        data = resp.json()
        assert data["trust"]["is_computable"] is False
        assert data["summary"]["current_index"] is None
        assert data["summary"]["change_24h"] is None
        assert data["summary"]["trend_label"] == "不可计算"
        assert all(item["value"] is None for item in data["target_indices"])
        assert data["metrics"][0]["label"] == "较前一日"
        assert data["metrics"][0]["value"] == "不可计算"
        contract = data["claim_contract"]
        assert contract["schema_version"] == "opinion-derived-claim-contract-v1"
        assert contract["status"] == "complete"
        assert len(contract["claims"]) == 12
        current_claim = next(
            item
            for item in contract["claims"]
            if item["metric"] == "weighted_stance_index"
        )
        assert current_claim["claim_state"] == "explicit_unknown"
        assert current_claim["citation_locator"] is None
        assert (
            current_claim["citation_reason_code"]
            == "SAFE_CITATION_LOCATOR_UNAVAILABLE"
        )
        assert {
            item["metric"]: item["claim_state"]
            for item in contract["claims"]
            if item["metric"] in {
                "target_weighted_stance_index",
                "negative_stance_pressure_index",
                "positive_stance_support_index",
            }
        } == {
            "target_weighted_stance_index": "explicit_unknown",
            "negative_stance_pressure_index": "explicit_unknown",
            "positive_stance_support_index": "explicit_unknown",
        }


class TestV3Stats:
    def test_returns_correct_structure(self, client: TestClient, mock_db: MockDB):
        resp = client.get("/api/opinion/v3-stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "event_coref" in data
        assert "coverage" in data
        assert "china_data" in data

    def test_china_data_with_fallback(self, client: TestClient, mock_db: MockDB):
        """china_data 在 china_relevance_score 为空时回退 prototype_weighted。"""
        mock_db.set_result("china_relevance_score::double precision", [])
        resp = client.get("/api/opinion/v3-stats")
        assert resp.status_code == 200
        assert "relevant_articles" in resp.json()["china_data"]


class TestHealth:
    def test_healthy_response(self, client: TestClient, mock_db: MockDB):
        resp = client.get("/api/opinion/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["status"] in ("healthy", "degraded")
        assert "freshness" in data
        assert "coverage" in data
        assert "alerts" in data

    def test_reports_degraded_on_high_missing(self, client: TestClient, mock_db: MockDB):
        """情感缺失 > 5% 时状态为 degraded。"""
        mock_db.set_result("COUNT(*)", 100)
        mock_db.set_result("china_impact_sentiment IS NULL", 10)
        mock_db.set_result("china_relevance_score IS NULL AND prototype_weighted IS NULL", 5)
        resp = client.get("/api/opinion/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "degraded"


class TestEventsByDate:
    def test_returns_empty_list(self, client: TestClient, mock_db: MockDB):
        """日期无事件时返回空列表。"""
        resp = client.get("/api/opinion/events-by-date?date_str=2024-01-01")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["events"] == []

    def test_with_mock_events(self, client: TestClient, mock_db: MockDB):
        """有事件数据时正确返回。"""
        mock_db.set_result("FROM micro_story_coref ms", [
            {"id": 1, "title": "Test Event", "event_type": "diplomatic",
             "initiator": "US", "target": "CN",
             "start_date": date(2024, 1, 1), "end_date": date(2024, 1, 15),
             "article_count": 10, "cluster_count": 3, "cluster_ids": [1, 2, 3]},
        ])
        mock_db.set_result("na.china_impact_sentiment, COALESCE(na.china_relevance_score, na.prototype_weighted, 0) AS china_index FROM news_ai_analysis na", [
            {"pub_date": date(2024, 1, 5), "china_impact_sentiment": -0.5,
             "china_index": 0.7},
        ])
        resp = client.get("/api/opinion/events-by-date?date_str=2024-01-05")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True


class TestEventNews:
    def test_needs_cluster_id(self, client: TestClient, mock_db: MockDB):
        resp = client.get("/api/opinion/event-news")
        assert resp.status_code == 400

    def test_returns_paginated(self, client: TestClient, mock_db: MockDB):
        mock_db.set_result("china_impact_sentiment IS NOT NULL", [
            {"id": 1, "title": "News 1", "pub_date": date(2024, 1, 5),
             "china_impact_sentiment": -0.5, "china_index": 0.7},
        ])
        mock_db.set_result("COALESCE(na.china_relevance_score, na.prototype_weighted, 0) >= 0.4", 1)
        resp = client.get("/api/opinion/event-news?cluster_id=abc123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

    def test_remaining_mode(self, client: TestClient, mock_db: MockDB):
        mock_db.set_result("DISTINCT ecm.cluster_id", [])
        mock_db.set_result("china_impact_sentiment, COALESCE(na.china_relevance_score, na.prototype_weighted, 0) AS china_index", [])
        resp = client.get(
            "/api/opinion/event-news?remaining=true&date=2024-01-05&micro_story_id=1"
        )
        assert resp.status_code == 400


# ── 所有端点响应格式通用测试 ──────────────────────────────────────

ENDPOINTS = [
    ("GET", "/api/opinion/china-trend?days=30", 200),
    ("GET", "/api/opinion/v3-stats", 200),
    ("GET", "/api/opinion/health", 200),
    ("GET", "/api/opinion/events-by-date?date_str=2024-01-01", 200),
    ("GET", "/api/opinion/micro-story-sub-events?micro_story_id=1&date_str=2024-01-01", 410),
    ("GET", "/api/opinion/event-news?cluster_id=test", 200),
    ("GET", "/api/opinion/event-timeseries?cluster_id=test", 410),
    ("GET", "/api/opinion/global-attention?days=90", 410),
    ("GET", "/api/opinion/sentiment-polarity?days=90", 410),
    ("GET", "/api/opinion/influence-index?days=90", 410),
    ("GET", "/api/opinion/composite-index?days=90", 410),
    ("GET", "/api/opinion/topic-breakdown?days=90", 410),
    ("GET", "/api/opinion/frame-breakdown?days=90", 410),
    ("GET", "/api/opinion/narrative-dispersion?days=90", 410),
]


class TestAllEndpoints:
    @pytest.mark.parametrize("method,path,expected_status", ENDPOINTS)
    def test_status_code(self, client: TestClient, mock_db: MockDB,
                         method: str, path: str, expected_status: int):
        resp = client.request(method, path)
        assert resp.status_code == expected_status, f"{path} 返回 {resp.status_code}"

    @pytest.mark.parametrize("method,path,_", ENDPOINTS)
    def test_json_serializable(self, client: TestClient, mock_db: MockDB,
                               method: str, path: str, _: Any):
        """所有端点返回有效 JSON（不抛序列化错误）。"""
        resp = client.request(method, path)
        assert resp.status_code < 500, f"{path} 5xx 错误"
        try:
            resp.json()
        except json.JSONDecodeError:
            pytest.fail(f"{path} 响应不是合法 JSON: {resp.text[:200]}")


# ── 数据库凭据边界 ───────────────────────────────────────────────

def test_db_credentials_use_central_resolver():
    """Web 数据库引擎只能通过统一、文件型凭据解析器构造连接。"""
    db_source = Path(__file__).resolve().parents[1] / "api" / "core" / "db.py"
    content = db_source.read_text(encoding="utf-8")
    assert "from api.core.database_credentials import" in content
    assert "canonical_database_settings()" in content
    assert "canonical_postgresql_url()" in content
    assert "DB_PASSWORD" not in content
    assert "PG_PASSWORD" not in content


def test_application_has_unique_method_path_routes():
    pairs = [
        (method, route.path)
        for route in app.routes
        for method in getattr(route, "methods", set())
    ]
    duplicates = sorted(pair for pair, count in Counter(pairs).items() if count > 1)

    assert duplicates == []


@pytest.mark.parametrize(
    "path",
    [
        "/api/opinion/china-trend?days=30&refresh=true",
        "/api/opinion/overview?days=30&refresh=true",
        "/api/opinion/v3-stats",
        "/api/opinion/health",
        "/api/opinion/events-by-date?date_str=2024-01-01",
        "/api/opinion/macro-event-clusters?days=30",
        "/api/opinion/event-news?cluster_id=test",
        "/api/opinion/news-by-date?date_str=2024-01-01&refresh=true",
        "/api/opinion/dimensions?days=30",
        "/api/opinion/quality",
        "/api/opinion/top-news?days=30",
    ],
)
def test_opinion_gets_are_read_only(client: TestClient, mock_db: MockDB, path: str):
    response = client.get(path)

    assert response.status_code < 500, response.text
    assert mock_db.commit_calls == 0


def test_opinion_refresh_requires_authentication(client: TestClient):
    response = client.post("/api/opinion/admin/refresh", json={"days": 7})

    assert response.status_code == 401


def test_opinion_refresh_is_an_explicit_admin_write(
    client: TestClient,
    mock_db: MockDB,
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[tuple[date, date, bool]] = []

    def override_admin():
        return {"user_id": 1, "username": "admin", "role": "admin"}

    def fake_refresh(_db, start_d: date, end_d: date, *, force: bool = False):
        calls.append((start_d, end_d, force))

    app.dependency_overrides[get_current_admin_user] = override_admin
    monkeypatch.setattr(opinion_v2, "_refresh_scores", fake_refresh)

    response = client.post(
        "/api/opinion/admin/refresh",
        json={"days": 7, "force": True},
    )

    assert response.status_code == 200
    assert len(calls) == 1
    assert (calls[0][1] - calls[0][0]).days == 6
    assert calls[0][2] is True
