from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.requests import Request

from api.features.dashboard import (
    NewsTranslateParagraphRequest,
    build_dashboard_readiness,
)
from api.routes import dashboard as dashboard_route


class _Result:
    def first(self) -> None:
        return None


class _Session:
    def execute(self, _statement: Any) -> _Result:
        return _Result()


def test_dashboard_route_uses_public_contract() -> None:
    assert dashboard_route.NewsTranslateParagraphRequest is NewsTranslateParagraphRequest
    body = NewsTranslateParagraphRequest(text="hello")
    assert body.target_language == "zh-Hans"
    assert body.source_language == "und"
    assert "_user" in inspect.signature(
        dashboard_route.translate_news_paragraph
    ).parameters
    assert "_strict_json" in inspect.signature(
        dashboard_route.translate_news_paragraph
    ).parameters


@pytest.mark.parametrize(
    "payload",
    (
        {"text": "hello", "target_language": "English"},
        {"text": "hello", "source_language": "not a tag"},
        {"text": "hello", "model": "arbitrary-provider-model"},
        {"text": "hello", "unexpected": "field"},
        {"text": "hello\x00world"},
    ),
)
def test_translation_request_rejects_ambiguous_or_unbounded_controls(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        NewsTranslateParagraphRequest.model_validate(payload)


class _TranslationResponse:
    headers = {"content-type": "application/json"}

    def __enter__(self) -> _TranslationResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_bytes(self):
        yield (
            b'{"model":"translation-model-v1",'
            b'"choices":[{"message":{"content":"\xe8\xbf\x99\xe6\x98\xaf\xe8\xaf\x91\xe6\x96\x87"}}]}'
        )


class _TranslationClient:
    def stream(self, *_args: object, **_kwargs: object) -> _TranslationResponse:
        return _TranslationResponse()


def _translation_setting(name: str, default: str | None = None) -> str:
    values = {
        "VLLM_TRANSLATE_BASE_URL": "http://127.0.0.1:8004",
        "VLLM_TRANSLATE_MODEL": "translation-model-v1",
    }
    return values.get(name, default or "")


def test_translation_response_carries_content_minimal_unreviewed_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dashboard_route, "string_setting", _translation_setting)
    monkeypatch.setattr(dashboard_route, "_translate_vllm_client", _TranslationClient())

    result = dashboard_route._translate_paragraph_with_vllm(
        "Source paragraph",
        "zh-Hans",
        "und",
    )

    assert result["schema_version"] == "globemind.news-translation.v1"
    assert result["text"] == "这是译文"
    assert "endpoint" not in result
    assert "source_text" not in result
    assert result["provenance"] == {
        "mode": "machine_translation",
        "backend": "local-vllm-loopback",
        "model_id": "translation-model-v1",
        "source_language": "und",
        "target_language": "zh-Hans",
        "source_text_sha256": hashlib.sha256(
            b"Source paragraph"
        ).hexdigest(),
        "source_text_length": 16,
        "human_review_state": "not_reviewed",
        "quality_state": "not_measured",
        "terminology_version": "not_configured",
        "persistence": "not_persisted_by_endpoint",
        "provider_scope": "loopback_only",
    }


def test_translation_source_binding_preserves_exact_whitespace_and_rejects_surrogates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dashboard_route, "string_setting", _translation_setting)
    monkeypatch.setattr(dashboard_route, "_translate_vllm_client", _TranslationClient())
    source = "  Source paragraph  "

    result = dashboard_route._translate_paragraph_with_vllm(
        source,
        "zh-Hans",
        "und",
    )

    assert result["provenance"]["source_text_sha256"] == hashlib.sha256(
        source.encode("utf-8")
    ).hexdigest()
    assert result["provenance"]["source_text_length"] == len(source)
    with pytest.raises(ValidationError):
        NewsTranslateParagraphRequest.model_validate({"text": "\ud800"})


@pytest.mark.parametrize(
    "unsafe_base",
    (
        "http://127.0.0.1:8004\n",
        "\x00http://127.0.0.1:8004",
        "http://127.0.0.1:8004\t/v1",
        "http://[::1%25lo]:8004/v1",
    ),
    ids=("trailing-newline", "leading-nul", "embedded-tab", "ipv6-zone"),
)
def test_translation_base_rejects_preparse_controls_and_scoped_loopback(
    monkeypatch: pytest.MonkeyPatch,
    unsafe_base: str,
) -> None:
    monkeypatch.setattr(
        dashboard_route,
        "string_setting",
        lambda name, default=None: unsafe_base
        if name == "VLLM_TRANSLATE_BASE_URL"
        else (default or ""),
    )
    with pytest.raises(HTTPException) as failed:
        dashboard_route._translate_vllm_v1_base()
    assert failed.value.status_code == 503
    assert failed.value.detail == {"code": "TRANSLATION_PROVIDER_NOT_APPROVED"}


def test_translation_base_and_provider_errors_fail_closed_without_secret_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        dashboard_route,
        "string_setting",
        lambda *_args, **_kwargs: "https://user:secret@example.test/v1?token=canary",
    )
    with pytest.raises(HTTPException) as unsafe:
        dashboard_route._translate_vllm_v1_base()
    assert unsafe.value.status_code == 503
    assert unsafe.value.detail == {"code": "TRANSLATION_PROVIDER_NOT_APPROVED"}
    assert "secret" not in str(unsafe.value.detail)

    class BrokenClient:
        def stream(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("provider-secret-canary")

    monkeypatch.setattr(dashboard_route, "string_setting", _translation_setting)
    monkeypatch.setattr(dashboard_route, "_translate_vllm_client", BrokenClient())
    with pytest.raises(HTTPException) as failed:
        dashboard_route._translate_paragraph_with_vllm("hello", "zh-Hans", "und")
    assert failed.value.status_code == 502
    assert failed.value.detail == {"code": "TRANSLATION_PROVIDER_UNAVAILABLE"}
    assert "canary" not in str(failed.value.detail)


@pytest.mark.parametrize(
    ("body", "content_type"),
    (
        (b'{"text":"first","text":"second"}', "application/json"),
        (b'{"text":"hello","unexpected":NaN}', "application/json"),
        (b'{"text":"hello","unexpected":1e400}', "application/json"),
        (b"{}", "text/plain"),
        (b"x" * (32 * 1024 + 1), "application/json"),
    ),
    ids=("duplicate-key", "nan", "overflow", "wrong-media-type", "oversized"),
)
def test_translation_route_rejects_ambiguous_json_before_provider_use(
    body: bytes,
    content_type: str,
) -> None:
    sent = False

    async def receive() -> dict[str, object]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/dashboard/news/translate-paragraph",
            "headers": [(b"content-type", content_type.encode("ascii"))],
        },
        receive,
    )
    with pytest.raises(HTTPException) as failed:
        asyncio.run(dashboard_route._require_unambiguous_translation_json(request))
    assert failed.value.status_code == 422
    assert failed.value.detail == {"code": "TRANSLATION_REQUEST_AMBIGUOUS"}


def test_translation_fastapi_route_enforces_auth_and_raw_json_before_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_calls: list[tuple[str, str, str]] = []

    def translate(text: str, target: str, source: str) -> dict[str, object]:
        provider_calls.append((text, target, source))
        return {"text": "ok"}

    monkeypatch.setattr(dashboard_route, "_translate_paragraph_with_vllm", translate)
    app = FastAPI()
    app.include_router(dashboard_route.router)
    app.dependency_overrides[dashboard_route.get_current_user_required] = lambda: {
        "id": 1,
        "role": "user",
    }

    deep = {"value": 0}
    for _ in range(dashboard_route._TRANSLATION_PROVIDER_MAX_DEPTH + 1):
        deep = {"value": deep}
    with TestClient(app) as client:
        duplicate = client.post(
            "/api/dashboard/news/translate-paragraph",
            content=b'{"text":"first","text":"second"}',
            headers={"content-type": "application/json"},
        )
        too_deep = client.post(
            "/api/dashboard/news/translate-paragraph",
            content=json.dumps({"text": "hello", "metadata": deep}),
            headers={"content-type": "application/json"},
        )
        valid = client.post(
            "/api/dashboard/news/translate-paragraph",
            json={"text": " exact ", "source_language": "und"},
        )

    assert duplicate.status_code == 422
    assert too_deep.status_code == 422
    assert valid.status_code == 200
    assert provider_calls == [(" exact ", "zh-Hans", "und")]

    unauthenticated = FastAPI()
    unauthenticated.include_router(dashboard_route.router)
    with TestClient(unauthenticated) as client:
        denied = client.post(
            "/api/dashboard/news/translate-paragraph",
            json={"text": "must not reach provider"},
        )
    assert denied.status_code == 401
    assert provider_calls == [(" exact ", "zh-Hans", "und")]


@pytest.mark.parametrize(
    ("body", "content_type"),
    (
        (
            b'{"audit":{"id":1,"id":2},"choices":[{"message":{"content":"ok"}}]}',
            "application/json",
        ),
        (
            b'{"usage":NaN,"choices":[{"message":{"content":"ok"}}]}',
            "application/json",
        ),
        (
            b'{"usage":1e400,"choices":[{"message":{"content":"ok"}}]}',
            "application/json",
        ),
        (
            (
                b'{"padding":"'
                + b"x" * (128 * 1024)
                + b'","choices":[{"message":{"content":"ok"}}]}'
            ),
            "application/json",
        ),
        (
            b'{"choices":[{"message":{"content":"ok"}}]}',
            "text/html",
        ),
    ),
    ids=("duplicate-key", "nan", "overflow", "oversized", "wrong-media-type"),
)
def test_translation_provider_payload_is_strict_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
    content_type: str,
) -> None:
    class RawResponse:
        headers = {"content-type": content_type}

        def __enter__(self) -> RawResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self):
            yield body

    class RawClient:
        def stream(self, *_args: object, **_kwargs: object) -> RawResponse:
            return RawResponse()

    monkeypatch.setattr(dashboard_route, "string_setting", _translation_setting)
    monkeypatch.setattr(dashboard_route, "_translate_vllm_client", RawClient())

    with pytest.raises(HTTPException) as failed:
        dashboard_route._translate_paragraph_with_vllm("hello", "zh-Hans", "und")
    assert failed.value.status_code == 502
    assert failed.value.detail == {"code": "TRANSLATION_PROVIDER_UNAVAILABLE"}


@pytest.mark.parametrize(
    ("body", "headers"),
    (
        (
            b'{"model":"other-model","choices":[{"message":{"content":"ok"}}]}',
            {"content-type": "application/json"},
        ),
        (
            b'{"model":"translation-model-v1","choices":[{"message":{"content":"\\ud800"}}]}',
            {"content-type": "application/json"},
        ),
        (
            b'{"model":"translation-model-v1","choices":[{"message":{"content":"ok"}}]}',
            {"content-type": "application/json", "content-length": "+81"},
        ),
    ),
    ids=("model-mismatch", "surrogate-output", "ambiguous-content-length"),
)
def test_translation_provider_rejects_model_unicode_and_length_ambiguity(
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
    headers: dict[str, str],
) -> None:
    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self):
            yield body

    response = Response()
    response.headers = headers  # type: ignore[attr-defined]

    class Client:
        def stream(self, *_args: object, **_kwargs: object) -> Response:
            return response

    monkeypatch.setattr(dashboard_route, "string_setting", _translation_setting)
    monkeypatch.setattr(dashboard_route, "_translate_vllm_client", Client())
    with pytest.raises(HTTPException) as failed:
        dashboard_route._translate_paragraph_with_vllm("hello", "zh-Hans", "und")
    assert failed.value.status_code == 502
    assert failed.value.detail in (
        {"code": "TRANSLATION_PROVIDER_UNAVAILABLE"},
        {"code": "TRANSLATION_OUTPUT_INVALID"},
    )


def test_translation_provider_bounds_deep_and_multi_chunk_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nested: object = 0
    for _ in range(dashboard_route._TRANSLATION_PROVIDER_MAX_DEPTH + 1):
        nested = [nested]
    deep_body = json.dumps(
        {
            "model": "translation-model-v1",
            "metadata": nested,
            "choices": [{"message": {"content": "ok"}}],
        }
    ).encode("utf-8")

    class Response:
        headers = {"content-type": "application/json"}

        def __init__(self, chunks: list[bytes]) -> None:
            self.chunks = chunks

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self):
            yield from self.chunks

    responses = iter(
        (
            Response([deep_body]),
            Response([b"x" * 65536, b"x" * 65536, b"x"]),
        )
    )

    class Client:
        def stream(self, *_args: object, **_kwargs: object) -> Response:
            return next(responses)

    monkeypatch.setattr(dashboard_route, "string_setting", _translation_setting)
    monkeypatch.setattr(dashboard_route, "_translate_vllm_client", Client())
    for _ in range(2):
        with pytest.raises(HTTPException) as failed:
            dashboard_route._translate_paragraph_with_vllm("hello", "zh-Hans", "und")
        assert failed.value.status_code == 502
        assert failed.value.detail == {"code": "TRANSLATION_PROVIDER_UNAVAILABLE"}


def test_dashboard_readiness_composes_dashboard_and_identity_repositories() -> None:
    status_code, payload = build_dashboard_readiness(
        _Session(),
        {"enabled": True, "healthy": True, "state": "running"},
    )

    assert status_code == 200
    assert payload["ready"] is True
    assert payload["checks"]["database"]["status"] == "up"
    assert payload["checks"]["assistant_scheduler"]["state"] == "running"
