"""Content-minimal contract for authenticated API documentation."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI

from api.core.http_security import RATE_LIMIT_SOURCE_DEFAULTS

API_DOCUMENTATION_SCHEMA_VERSION = (
    "globemind.authenticated-api-documentation.v1"
)
_MAX_OPENAPI_BYTES = 4 * 1024 * 1024
_MAX_OPENAPI_PATHS = 5000
_MAX_OPENAPI_DEPTH = 64
_MAX_OPENAPI_NODES = 250_000
_MAX_OPENAPI_OPERATIONS = 40_000
_MAX_OPERATION_ID_LENGTH = 256
_HTTP_METHODS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
)
_SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")


class ApiDocumentationUnavailable(RuntimeError):
    """The running OpenAPI schema cannot be summarized safely."""


def _has_unsafe_unicode(value: str) -> bool:
    return any(
        (ord(character) < 32 and character not in "\t\n\r")
        or ord(character) == 127
        or 0xD800 <= ord(character) <= 0xDFFF
        for character in value
    )


def _validate_schema_tree(value: Any) -> None:
    pending = [(value, 0)]
    nodes = 0
    while pending:
        current, depth = pending.pop()
        nodes += 1
        if nodes > _MAX_OPENAPI_NODES:
            raise ApiDocumentationUnavailable("OpenAPI schema has too many nodes")
        if depth > _MAX_OPENAPI_DEPTH:
            raise ApiDocumentationUnavailable("OpenAPI schema is too deep")
        if isinstance(current, dict):
            keys: set[str] = set()
            for key, child in current.items():
                if (
                    not isinstance(key, str)
                    or len(key) > _MAX_OPENAPI_BYTES
                    or _has_unsafe_unicode(key)
                    or key in keys
                ):
                    raise ApiDocumentationUnavailable(
                        "OpenAPI schema contains an invalid JSON key"
                    )
                keys.add(key)
                pending.append((child, depth + 1))
        elif isinstance(current, list):
            pending.extend((child, depth + 1) for child in current)
        elif isinstance(current, str):
            if len(current) > _MAX_OPENAPI_BYTES or _has_unsafe_unicode(current):
                raise ApiDocumentationUnavailable(
                    "OpenAPI schema contains invalid text"
                )
        elif current is None or isinstance(current, bool):
            continue
        elif isinstance(current, int):
            if current.bit_length() > 4096:
                raise ApiDocumentationUnavailable(
                    "OpenAPI schema contains an oversized integer"
                )
        elif isinstance(current, float):
            if not math.isfinite(current):
                raise ApiDocumentationUnavailable(
                    "OpenAPI schema contains a non-finite number"
                )
        else:
            raise ApiDocumentationUnavailable(
                "OpenAPI schema contains a non-JSON value"
            )


def _validate_paths(paths: dict[str, Any]) -> tuple[int, int]:
    path_names: set[str] = set()
    operation_ids: set[str] = set()
    path_count = 0
    operation_count = 0
    for path, path_item in paths.items():
        if (
            not isinstance(path, str)
            or not path.startswith("/")
            or len(path) > 2048
            or _has_unsafe_unicode(path)
            or not isinstance(path_item, dict)
            or path in path_names
        ):
            raise ApiDocumentationUnavailable("OpenAPI path inventory is invalid")
        path_names.add(path)
        path_count += 1
        if path_count > _MAX_OPENAPI_PATHS:
            raise ApiDocumentationUnavailable(
                "OpenAPI path inventory exceeds the documentation bound"
            )
        for method, operation in path_item.items():
            if not isinstance(method, str):
                raise ApiDocumentationUnavailable("OpenAPI method key is invalid")
            normalized_method = method.lower()
            if normalized_method not in _HTTP_METHODS:
                continue
            if method != normalized_method or not isinstance(operation, dict):
                raise ApiDocumentationUnavailable("OpenAPI operation is invalid")
            operation_id = operation.get("operationId")
            if (
                not isinstance(operation_id, str)
                or not operation_id
                or len(operation_id) > _MAX_OPERATION_ID_LENGTH
                or _has_unsafe_unicode(operation_id)
                or operation_id in operation_ids
            ):
                raise ApiDocumentationUnavailable(
                    "OpenAPI operationId inventory is invalid"
                )
            operation_ids.add(operation_id)
            operation_count += 1
            if operation_count > _MAX_OPENAPI_OPERATIONS:
                raise ApiDocumentationUnavailable(
                    "OpenAPI operation inventory exceeds the documentation bound"
                )
    return path_count, operation_count


def _canonical_schema(
    app: FastAPI,
) -> tuple[dict[str, Any], bytes, str, int, int]:
    try:
        schema = app.openapi()
    except Exception as exc:
        raise ApiDocumentationUnavailable("OpenAPI generation failed") from exc
    if not isinstance(schema, dict):
        raise ApiDocumentationUnavailable("OpenAPI root is not an object")
    try:
        paths = schema.get("paths")
        if not isinstance(paths, dict):
            raise ApiDocumentationUnavailable("OpenAPI path inventory is invalid")
        openapi_version = schema.get("openapi")
        if not isinstance(openapi_version, str) or not re.fullmatch(
            r"3(?:\.\d+){1,2}", openapi_version
        ):
            raise ApiDocumentationUnavailable("unsupported OpenAPI version")
        _validate_schema_tree(schema)
        path_count, operation_count = _validate_paths(paths)
        encoded = json.dumps(
            schema,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except ApiDocumentationUnavailable:
        raise
    except Exception as exc:
        raise ApiDocumentationUnavailable("OpenAPI schema is not canonical JSON") from exc
    if len(encoded) > _MAX_OPENAPI_BYTES:
        raise ApiDocumentationUnavailable("OpenAPI schema exceeds the documentation bound")
    return schema, encoded, openapi_version, path_count, operation_count


def _generated_at(value: datetime | None) -> str:
    observed = value or datetime.now(timezone.utc)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ApiDocumentationUnavailable("generated_at must be timezone-aware")
    return observed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _application_version(app: FastAPI) -> str:
    try:
        raw_value = getattr(app, "version", "")
    except Exception:
        return "unavailable"
    if not isinstance(raw_value, str):
        return "unavailable"
    value = raw_value.strip()
    return value if _SAFE_VERSION.fullmatch(value) else "unavailable"


def build_bounded_openapi_document(app: FastAPI) -> bytes:
    """Return the exact canonical schema bytes accepted by the contract."""

    _schema, encoded, _openapi_version, _path_count, _operation_count = (
        _canonical_schema(app)
    )
    return encoded


def build_api_documentation_contract(
    app: FastAPI,
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Summarize the running schema without copying descriptions or examples."""

    _schema, encoded, openapi_version, path_count, operation_count = (
        _canonical_schema(app)
    )

    return {
        "schema_version": API_DOCUMENTATION_SCHEMA_VERSION,
        "generated_at": _generated_at(generated_at),
        "access": {
            "catalog_endpoint": "/api/governance/api-contract",
            "openapi_endpoint": "/api/governance/openapi.json",
            "required_role": "administrator",
            "authentication_scheme": "bearer",
            "route_access_policy": "mixed_route_specific",
        },
        "running_schema": {
            "application_version": _application_version(app),
            "openapi_version": openapi_version,
            "path_count": path_count,
            "operation_count": operation_count,
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "hash_scope": "canonical_running_openapi",
        },
        "versioning": {
            "path_versioning": "not_configured",
            "compatibility_policy": "not_approved",
            "deprecation_policy": "not_approved",
            "changelog_binding": "not_configured",
            "stability_claim": "not_established",
        },
        "documentation_ui": {
            "production_public_interactive_docs": "disabled_by_application_policy",
            "authenticated_interactive_ui": "not_configured",
            "running_schema_download": "available_to_administrator",
        },
        "rate_limits": {
            "implementation": "selected_write_routes_in_process",
            "documented_values": "source_defaults_only",
            "effective_runtime_attestation": "not_available",
            "multi_instance_coordination": "not_configured",
            "persistence": "process_memory_only",
            "rejection_status": 429,
            "retry_header": "Retry-After",
            "source_defaults": [
                {
                    "id": item.id,
                    "methods": list(item.methods),
                    "route_matchers": list(item.route_matchers),
                    "default_requests": item.default_requests,
                    "default_window_seconds": item.default_window_seconds,
                    "requests_setting": item.requests_setting,
                    "window_setting": item.window_setting,
                }
                for item in RATE_LIMIT_SOURCE_DEFAULTS
            ],
        },
        "examples": [
            {
                "id": "fetch-running-openapi",
                "method": "GET",
                "path": "/api/governance/openapi.json",
                "authorization": "Bearer <access-token>",
                "required_role": "administrator",
                "request_body": None,
                "status": "documentation_example_only",
            }
        ],
        "limitations": [
            "no_public_production_docs_ui",
            "route_specific_security_is_authoritative_in_openapi",
            "runtime_rate_limit_values_not_attested",
            "no_approved_compatibility_or_deprecation_policy",
            "no_multi_instance_rate_limit_coordination",
            "no_enterprise_api_support_commitment",
        ],
    }


__all__ = [
    "API_DOCUMENTATION_SCHEMA_VERSION",
    "ApiDocumentationUnavailable",
    "build_api_documentation_contract",
    "build_bounded_openapi_document",
]
