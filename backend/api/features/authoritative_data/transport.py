"""Small HTTPS-only JSON transport with hard byte and time limits."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

import httpx

MAX_RESPONSE_BYTES = 1_048_576
DEFAULT_ALLOWED_HOSTS = frozenset(
    {
        "api.worldbank.org",
        "www.imf.org",
        "unstats.un.org",
        "api.crossref.org",
    }
)
_JSON_MEDIA_TYPES = frozenset(
    {"application/json", "text/json", "text/plain"}
)

QueryValue = str | int | float
QueryParams = Mapping[str, QueryValue] | Sequence[tuple[str, QueryValue]]


class UpstreamFailure(RuntimeError):
    """Sanitized upstream failure safe to expose as a reason code."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class _DuplicateKey(ValueError):
    pass


class _InvalidNumber(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise _DuplicateKey(key)
        output[key] = value
    return output


def _reject_non_finite_constant(value: str) -> None:
    raise _InvalidNumber(value)


def _parse_finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise _InvalidNumber(value)
    return parsed


class BoundedJsonClient:
    """Fetch JSON without redirects, ambient proxies, cookies, or unbounded reads."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 8.0,
        maximum_response_bytes: int = MAX_RESPONSE_BYTES,
        allowed_hosts: frozenset[str] = DEFAULT_ALLOWED_HOSTS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if timeout_seconds <= 0 or timeout_seconds > 30:
            raise ValueError("timeout_seconds must be in (0, 30]")
        if maximum_response_bytes <= 0 or maximum_response_bytes > 4_194_304:
            raise ValueError("maximum_response_bytes must be in (0, 4194304]")
        self._timeout_seconds = float(timeout_seconds)
        self._maximum_response_bytes = maximum_response_bytes
        self._allowed_hosts = frozenset(host.lower() for host in allowed_hosts)
        self._transport = transport

    @property
    def network_policy(self) -> dict[str, Any]:
        """Return non-secret settings so tests and inventories can verify the boundary."""

        return {
            "https_only": True,
            "follow_redirects": False,
            "trust_env": False,
            "maximum_response_bytes": self._maximum_response_bytes,
            "timeout_seconds": self._timeout_seconds,
            "allowed_hosts": sorted(self._allowed_hosts),
        }

    def _validate_url(self, url: str) -> None:
        if (
            not isinstance(url, str)
            or not url
            or len(url) > 2_048
            or "\\" in url
            or any(ord(character) < 33 or ord(character) == 127 for character in url)
        ):
            raise UpstreamFailure("UNSAFE_UPSTREAM_URL")
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except (TypeError, ValueError, UnicodeError) as exc:
            raise UpstreamFailure("UNSAFE_UPSTREAM_URL") from exc
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.hostname.lower() not in self._allowed_hosts
            or parsed.username is not None
            or parsed.password is not None
            or port not in (None, 443)
            or parsed.fragment
        ):
            raise UpstreamFailure("UNSAFE_UPSTREAM_URL")

    async def get_json(
        self,
        url: str,
        *,
        params: QueryParams | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any] | list[Any]:
        self._validate_url(url)
        timeout = httpx.Timeout(
            connect=min(3.0, self._timeout_seconds),
            read=self._timeout_seconds,
            write=min(3.0, self._timeout_seconds),
            pool=min(3.0, self._timeout_seconds),
        )
        request_headers = {
            "Accept": "application/json",
            "User-Agent": "GlobeMind-authoritative-data/1.0",
        }
        if headers:
            request_headers.update(headers)

        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                limits=httpx.Limits(
                    max_connections=2,
                    max_keepalive_connections=1,
                ),
                follow_redirects=False,
                trust_env=False,
                transport=self._transport,
            ) as client:
                async with client.stream(
                    "GET",
                    url,
                    params=params,
                    headers=request_headers,
                ) as response:
                    self._validate_response_headers(response)
                    payload = bytearray()
                    async for chunk in response.aiter_bytes():
                        payload.extend(chunk)
                        if len(payload) > self._maximum_response_bytes:
                            raise UpstreamFailure("UPSTREAM_PAYLOAD_TOO_LARGE")
        except UpstreamFailure:
            raise
        except httpx.TimeoutException as exc:
            raise UpstreamFailure("UPSTREAM_TIMEOUT") from exc
        except httpx.RequestError as exc:
            raise UpstreamFailure("UPSTREAM_UNAVAILABLE") from exc

        try:
            decoded = payload.decode("utf-8-sig")
            parsed_payload = json.loads(
                decoded,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite_constant,
                parse_float=_parse_finite_float,
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            _DuplicateKey,
            _InvalidNumber,
            RecursionError,
        ) as exc:
            raise UpstreamFailure("UPSTREAM_INVALID_JSON") from exc
        if not isinstance(parsed_payload, (dict, list)):
            raise UpstreamFailure("UPSTREAM_INVALID_CONTRACT")
        return parsed_payload

    def _validate_response_headers(self, response: httpx.Response) -> None:
        status_code = response.status_code
        if status_code != 200:
            if status_code == 429:
                raise UpstreamFailure("UPSTREAM_RATE_LIMITED")
            if status_code == 404:
                raise UpstreamFailure("UPSTREAM_NOT_FOUND")
            if 300 <= status_code < 400:
                raise UpstreamFailure("UPSTREAM_REDIRECT_REJECTED")
            if 400 <= status_code < 500:
                raise UpstreamFailure("UPSTREAM_REQUEST_REJECTED")
            raise UpstreamFailure("UPSTREAM_UNAVAILABLE")

        content_type = response.headers.get("content-type", "")
        media_type = content_type.split(";", 1)[0].strip().lower()
        if media_type not in _JSON_MEDIA_TYPES:
            raise UpstreamFailure("UPSTREAM_UNEXPECTED_CONTENT_TYPE")

        raw_length = response.headers.get("content-length")
        if raw_length:
            try:
                content_length = int(raw_length)
            except ValueError as exc:
                raise UpstreamFailure("UPSTREAM_INVALID_CONTENT_LENGTH") from exc
            if content_length < 0:
                raise UpstreamFailure("UPSTREAM_INVALID_CONTENT_LENGTH")
            if content_length > self._maximum_response_bytes:
                raise UpstreamFailure("UPSTREAM_PAYLOAD_TOO_LARGE")


__all__ = (
    "BoundedJsonClient",
    "DEFAULT_ALLOWED_HOSTS",
    "MAX_RESPONSE_BYTES",
    "QueryParams",
    "UpstreamFailure",
)
