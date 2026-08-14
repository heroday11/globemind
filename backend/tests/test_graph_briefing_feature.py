from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from cppt import cc_bridge
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.core.db import get_db
from api.features import graph_briefing
from api.routes import briefing as briefing_route

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LEGACY_RELATIONS = (
    "macro_storylines",
    "micro_events",
    "storyline_micro_map",
    "micro_event_members",
    "news_analysis",
)
MACRO_ID = "fast_l3_v1_macro-a"
CHAIN_ID = "fast_l2_v1_chain-a"


@dataclass
class _Result:
    scalar_value: Any = None
    rows: list[dict[str, Any]] = field(default_factory=list)

    def scalar(self) -> Any:
        return self.scalar_value

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self.rows

    def first(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None


class _Session:
    def __init__(self, *results: _Result):
        self.results = list(results)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(
        self,
        statement: Any,
        parameters: dict[str, Any] | None = None,
    ) -> _Result:
        self.calls.append((str(statement), dict(parameters or {})))
        if not self.results:
            raise AssertionError(f"unexpected SQL: {statement}")
        return self.results.pop(0)


def _macro_row(*, macro_id: str = MACRO_ID) -> dict[str, Any]:
    return {
        "macro_id": macro_id,
        "macro_key": "diplomacy:china",
        "title": "Current macro",
        "summary": "Current hierarchy summary",
        "family_group": "diplomacy",
        "l2_chain_count": 1,
        "l1_cluster_count": 2,
        "segment_count": 3,
        "article_count": 4,
        "start_date": "2026-07-01",
        "end_date": "2026-07-11",
        "actor_counts": {"China": 2},
        "topic_counts": {"trade": 2},
        "quality_score": 0.9,
    }


def _micro_row(
    *,
    chain_id: str = CHAIN_ID,
    macro_id: str = MACRO_ID,
) -> dict[str, Any]:
    return {
        "macro_id": macro_id,
        "chain_id": chain_id,
        "title": "Current chain",
        "start_date": "2026-07-02",
        "end_date": "2026-07-10",
        "article_count": 4,
        "segment_count": 3,
        "family_group": "diplomacy",
        "event_family": "negotiation",
        "event_action": "talks",
        "pair_key": "china-us",
        "initiator": "China",
        "target": "United States",
        "chain_quality": "high",
        "quality_score": 0.8,
        "importance_score": 0.75,
        "role": "main",
        "lane": "diplomatic",
    }


def _batch_news_row() -> dict[str, Any]:
    return {
        "chain_id": CHAIN_ID,
        "news_id": 42,
        "title": "Article",
        "abstract": "Summary",
        "pub_time": "2026-07-11T01:02:03",
        "request_url": "https://example.test/article",
        "language_id": "en",
    }


def _news_row() -> dict[str, Any]:
    row = _batch_news_row()
    row["id"] = row.pop("news_id")
    return row


def _case_results(case: str) -> list[_Result]:
    cases = {
        "search": [_Result(rows=[_macro_row()])],
        "batch": [_Result(rows=[_batch_news_row()])],
        "universe": [
            _Result(rows=[_macro_row()]),
            _Result(rows=[_micro_row()]),
            _Result(scalar_value=10),
            _Result(scalar_value=1),
            _Result(scalar_value=8),
        ],
        "macro": [_Result(rows=[_macro_row()])],
        "briefing": [
            _Result(rows=[_macro_row()]),
            _Result(scalar_value=0.25),
            _Result(rows=[{"label": "positive", "count": 3}]),
            _Result(rows=[{"label": "negotiation", "count": 3}]),
        ],
        "micros": [
            _Result(rows=[_macro_row()]),
            _Result(scalar_value=1),
            _Result(rows=[_micro_row()]),
        ],
        "tree": [_Result(rows=[_macro_row()]), _Result(rows=[_micro_row()])],
        "micro": [_Result(rows=[_micro_row()])],
        "news": [
            _Result(rows=[_micro_row()]),
            _Result(scalar_value=1),
            _Result(rows=[_news_row()]),
        ],
    }
    return cases[case]


def _http_client(session: _Session) -> TestClient:
    app = FastAPI()
    app.include_router(briefing_route.router, prefix="/api/graph")

    def override_get_db() -> _Session:
        return session

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


@pytest.mark.parametrize(
    ("case", "method", "path", "payload"),
    [
        ("search", "GET", "/api/graph/macros/search?q=chip%25_&limit=5", None),
        (
            "batch",
            "POST",
            "/api/graph/micros/news-batch",
            {"event_ids": [CHAIN_ID], "limit_per": 5},
        ),
        (
            "universe",
            "GET",
            "/api/graph/universe?macro_limit=1&micro_per_macro=1&unclustered_limit=0&news_per_micro=0",
            None,
        ),
        ("macro", "GET", f"/api/graph/macro/{MACRO_ID}", None),
        ("briefing", "GET", f"/api/graph/macro/{MACRO_ID}/briefing", None),
        (
            "micros",
            "GET",
            f"/api/graph/macro/{MACRO_ID}/micros?limit=5&offset=2",
            None,
        ),
        ("tree", "GET", f"/api/graph/macro/{MACRO_ID}/tree?micro_limit=5", None),
        ("micro", "GET", f"/api/graph/micro/{CHAIN_ID}", None),
        (
            "news",
            "GET",
            f"/api/graph/micro/{CHAIN_ID}/news?page=2&page_size=5&brief=true",
            None,
        ),
    ],
)
def test_all_nine_graph_endpoints_use_current_hierarchy(
    case: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None,
) -> None:
    session = _Session(*_case_results(case))
    client = _http_client(session)
    kwargs = {"json": payload} if payload is not None else {}

    response = client.request(method, path, **kwargs)

    assert response.status_code == 200, response.text
    assert session.results == []
    assert session.calls
    for sql, _parameters in session.calls:
        assert not any(
            re.search(rf"\b{re.escape(relation)}\b", sql)
            for relation in LEGACY_RELATIONS
        )

    body = response.json()
    if case in {"macro", "briefing", "micros", "tree", "universe"}:
        assert MACRO_ID in response.text
    if case in {"batch", "micros", "tree", "micro", "news", "universe"}:
        assert CHAIN_ID in response.text
    if case == "news":
        assert body["page"] == 2
        assert session.calls[-1][1]["offset"] == 5
    if case == "micros":
        assert session.calls[-1][1]["offset"] == 2
    if case == "search":
        sql, parameters = session.calls[0]
        assert "chip%_" not in sql
        assert parameters["keyword"] == "%chip!%!_%"


@pytest.mark.parametrize(
    ("method", "path", "payload", "expected_status"),
    [
        ("GET", "/api/graph/macro/bad%20id", None, 422),
        ("GET", "/api/graph/micro/-escape", None, 422),
        ("GET", "/api/graph/micro/..%2Fescape", None, 404),
        (
            "POST",
            "/api/graph/micros/news-batch",
            {"event_ids": ["bad id"], "limit_per": 5},
            422,
        ),
        (
            "POST",
            "/api/graph/micros/news-batch",
            {"event_ids": [True], "limit_per": 5},
            422,
        ),
    ],
)
def test_invalid_graph_ids_fail_closed_without_sql(
    method: str,
    path: str,
    payload: dict[str, Any] | None,
    expected_status: int,
) -> None:
    session = _Session()
    client = _http_client(session)
    kwargs = {"json": payload} if payload is not None else {}

    response = client.request(method, path, **kwargs)

    assert response.status_code == expected_status
    assert session.calls == []


def test_batch_accepts_legacy_numeric_id_but_binds_text() -> None:
    session = _Session(_Result(rows=[]))
    response = _http_client(session).post(
        "/api/graph/micros/news-batch",
        json={"event_ids": [123], "limit_per": 7},
    )

    assert response.status_code == 200
    assert response.json()["by_event"] == {"123": []}
    assert session.calls[0][1] == {"chain_ids": ["123"], "limit_per": 7}
    assert "123" not in session.calls[0][0]


def test_valid_unknown_graph_id_returns_404() -> None:
    session = _Session(_Result(rows=[]))

    response = _http_client(session).get("/api/graph/macro/missing-l3")

    assert response.status_code == 404
    assert session.calls[0][1] == {"macro_id": "missing-l3"}


class _LargeUniverseRepository:
    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    def list_macros(self, _limit: int) -> list[dict[str, Any]]:
        return [
            _macro_row(macro_id=f"fast_l3_v1_{macro_index}")
            for macro_index in range(3)
        ]

    def micros_for_macros(
        self,
        _macro_ids: list[str],
        _per_macro: int,
    ) -> list[dict[str, Any]]:
        return [
            _micro_row(
                chain_id=f"fast_l2_v1_{macro_index}_{chain_index}",
                macro_id=f"fast_l3_v1_{macro_index}",
            )
            for macro_index in range(3)
            for chain_index in range(300)
        ]

    def news_for_micros(
        self,
        chain_ids: list[str],
        _limit_per: int,
    ) -> list[dict[str, Any]]:
        self.batch_sizes.append(len(chain_ids))
        return []

    def diagnostics(self) -> dict[str, int]:
        return {"news_total": 0, "macro_total": 3, "linked_news_distinct": 0}


def test_universe_chunks_internal_news_batches_without_weakening_public_limit() -> None:
    repository = _LargeUniverseRepository()
    service = graph_briefing.GraphBriefingService(None, repository=repository)  # type: ignore[arg-type]

    response = service.universe(
        macro_limit=1,
        micro_per_macro=300,
        unclustered_limit=0,
        fill_ambient=False,
        news_per_micro=1,
    )

    assert response["macros_count"] == 1
    assert repository.batch_sizes == [300]


def test_micro_queries_preserve_legacy_stable_ordering() -> None:
    session = _Session(
        _Result(rows=[_macro_row()]),
        _Result(scalar_value=0),
        _Result(rows=[]),
    )

    response = _http_client(session).get(f"/api/graph/macro/{MACRO_ID}/micros")

    assert response.status_code == 200
    listing_sql = session.calls[-1][0]
    assert "article_count" in listing_sql
    assert "DESC" in listing_sql
    assert "COALESCE(chain.article_count, member.article_count) AS article_count" in listing_sql
    assert "COALESCE(chain.segment_count, member.segment_count) AS segment_count" in listing_sql
    assert listing_sql.index("article_count") < listing_sql.index("member.l2_chain_id ASC")


def test_graph_route_and_public_api_keep_repository_internal() -> None:
    route_source = (PROJECT_ROOT / "backend/api/routes/briefing.py").read_text(
        encoding="utf-8"
    )
    feature_source = (
        PROJECT_ROOT / "backend/api/features/graph_briefing/__init__.py"
    ).read_text(encoding="utf-8")

    assert "from api.features.graph_briefing import (" in route_source
    assert "sqlalchemy import text" not in route_source
    assert not any(
        re.search(rf"\b{re.escape(relation)}\b", route_source)
        for relation in LEGACY_RELATIONS
    )
    assert not hasattr(graph_briefing, "GraphBriefingRepository")
    assert "GraphBriefingRepository" not in feature_source


def test_graph_feature_has_no_route_or_core_database_dependency() -> None:
    feature_root = PROJECT_ROOT / "backend/api/features/graph_briefing"
    for source_path in feature_root.glob("*.py"):
        source = source_path.read_text(encoding="utf-8")
        assert "api.routes" not in source, source_path
        assert "api.core.db" not in source, source_path
        assert "fastapi" not in source, source_path


def test_graph_repository_uses_the_current_news_schema_for_abstracts() -> None:
    repository_source = (
        PROJECT_ROOT / "backend/api/features/graph_briefing/repository.py"
    ).read_text(encoding="utf-8")

    assert "article.abstract" not in repository_source
    assert repository_source.count("LEFT(COALESCE(article.body, ''), 1200) AS abstract") == 3
    assert "LEFT(COALESCE(article.body, ''), 4000)" in repository_source


class _BridgeResponse:
    status_code = 200
    text = "{}"

    def __init__(self, payload: dict[str, Any]):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _BridgeClient:
    calls: list[tuple[str, str, dict[str, Any]]]

    def __init__(self, **_kwargs: Any):
        self.calls = []

    async def __aenter__(self) -> _BridgeClient:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def get(self, url: str, **kwargs: Any) -> _BridgeResponse:
        self.calls.append(("GET", url, kwargs))
        return _BridgeResponse({"items": []})

    async def post(self, url: str, **kwargs: Any) -> _BridgeResponse:
        self.calls.append(("POST", url, kwargs))
        return _BridgeResponse({"by_event": {CHAIN_ID: [{"id": 42}]}})


@pytest.mark.parametrize(
    ("args", "method", "suffix"),
    [
        (
            {"action": "search_clusters", "keyword": "trade"},
            "GET",
            "/api/graph/macros/search",
        ),
        (
            {"action": "get_macro", "macro_id": MACRO_ID},
            "GET",
            f"/api/graph/macro/{MACRO_ID}",
        ),
        (
            {"action": "get_micros", "macro_id": MACRO_ID},
            "GET",
            f"/api/graph/macro/{MACRO_ID}/micros",
        ),
        (
            {"action": "get_micro", "micro_id": CHAIN_ID},
            "GET",
            f"/api/graph/micro/{CHAIN_ID}",
        ),
        (
            {"action": "get_news", "micro_id": CHAIN_ID},
            "POST",
            "/api/graph/micros/news-batch",
        ),
    ],
)
def test_cc_bridge_uses_current_text_id_routes(
    monkeypatch: pytest.MonkeyPatch,
    args: dict[str, Any],
    method: str,
    suffix: str,
) -> None:
    clients: list[_BridgeClient] = []

    def client_factory(**kwargs: Any) -> _BridgeClient:
        client = _BridgeClient(**kwargs)
        clients.append(client)
        return client

    monkeypatch.setenv("ASSISTANT_SEARCH_API_BASE", "http://graph.test")
    monkeypatch.setattr(cc_bridge.httpx, "AsyncClient", client_factory)

    response = asyncio.run(cc_bridge._tool_navigate_clusters(args))

    assert response["ok"] is True
    assert len(clients) == 1
    call_method, url, kwargs = clients[0].calls[0]
    assert call_method == method
    assert url.endswith(suffix)
    if args["action"] == "get_news":
        assert kwargs["json"] == {"event_ids": [CHAIN_ID], "limit_per": 10}
        assert response["items_returned"] == 1


def test_cc_bridge_schema_accepts_text_and_integer_hierarchy_ids() -> None:
    properties = cc_bridge.NAVIGATE_CLUSTERS_TOOL["input_schema"]["properties"]

    for name in ("macro_id", "micro_id"):
        assert properties[name]["anyOf"] == [
            {"type": "string"},
            {"type": "integer"},
        ]
