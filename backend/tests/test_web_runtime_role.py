from __future__ import annotations

import builtins
import json
import sys
import types
from typing import Any

import numpy as np
import pytest
from fastapi import HTTPException

from api.models.schemas import SearchRequest
from api.routes import search as search_route
from api.services import search_service

_LOCAL_MODEL_ROOTS = ("agentic_rag.ingestion.embedder", "sentence_transformers", "torch")


def _deny_local_model_imports(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    original_import = builtins.__import__
    attempts: list[str] = []

    def guarded_import(
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        if name.startswith(_LOCAL_MODEL_ROOTS):
            attempts.append(name)
            raise AssertionError(f"Web role attempted local model import: {name}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    return attempts


class _EmbeddingResponse:
    def __enter__(self) -> "_EmbeddingResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps({"data": [{"embedding": [3.0, 4.0]}]}).encode()


def test_remote_bge_embedding_never_imports_local_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BGE_M3_BASE_URL", "http://127.0.0.1:8001")
    monkeypatch.setenv("BGE_LOCAL_FALLBACK_ENABLED", "0")
    monkeypatch.setattr(search_service.urllib.request, "urlopen", lambda *_a, **_k: _EmbeddingResponse())
    attempts = _deny_local_model_imports(monkeypatch)

    vector = search_service.encode_query_bge_m3("test query")

    assert attempts == []
    assert vector.dtype == np.float32
    assert np.allclose(vector, np.asarray([0.6, 0.8], dtype=np.float32))


def test_remote_bge_failure_does_not_activate_local_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BGE_LOCAL_FALLBACK_ENABLED", "0")

    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise OSError("embedding service unavailable")

    monkeypatch.setattr(search_service.urllib.request, "urlopen", unavailable)
    attempts = _deny_local_model_imports(monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        search_service.encode_query_bge_m3("test query")

    assert exc_info.value.status_code == 503
    assert "local model fallback is disabled" in str(exc_info.value.detail)
    assert attempts == []


def test_local_bge_fallback_remains_enabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BGE_LOCAL_FALLBACK_ENABLED", raising=False)

    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise OSError("embedding service unavailable")

    class FakeEmbedder:
        def encode(self, texts: list[str]) -> list[list[float]]:
            assert texts == ["test query"]
            return [[0.0, 5.0]]

    module = types.ModuleType("agentic_rag.ingestion.embedder")
    module.get_embedder = lambda: FakeEmbedder()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agentic_rag.ingestion.embedder", module)
    monkeypatch.setattr(search_service.urllib.request, "urlopen", unavailable)

    vector = search_service.encode_query_bge_m3("test query")

    assert np.allclose(vector, np.asarray([0.0, 1.0], dtype=np.float32))


@pytest.mark.parametrize("mode", ["fuzzy", "cluster"])
def test_dashboard_search_modes_do_not_load_local_embedding_model(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    attempts = _deny_local_model_imports(monkeypatch)
    observed: dict[str, str] = {}

    def fake_search(params: SearchRequest, **_kwargs: object) -> dict[str, object]:
        observed["mode"] = params.mode
        return {
            "data": [],
            "total": 0,
            "page": 1,
            "page_size": 20,
            "total_pages": 0,
            "has_next": False,
            "has_prev": False,
            "query_time_ms": 0.0,
            "cluster_tree": [],
            "event_coref_clusters": [],
            "micro_story_items": [],
            "macro_event_items": [],
        }

    monkeypatch.setattr(search_route, "search_dashboard_v2", fake_search)
    params = SearchRequest(keyword="test", mode=mode)

    result = search_route.search_news(params=params, mode=mode, user=None, db=object())

    assert observed == {"mode": mode}
    assert result["total"] == 0
    assert attempts == []
