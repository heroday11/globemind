"""Secret detection and output-boundary sanitization."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .constants import REDACTED

SENSITIVE_NAMES = frozenset(
    {
        "api-key",
        "apikey",
        "auth-token",
        "authorization",
        "aws-access-key-id",
        "aws-secret-access-key",
        "client-secret",
        "credential",
        "credentials",
        "database-url",
        "dsn",
        "password",
        "passwd",
        "pgpassword",
        "pwd",
        "refresh-token",
        "secret",
        "token",
    }
)
SENSITIVE_SUFFIXES = (
    "-api-key",
    "-auth-token",
    "-client-secret",
    "-credential",
    "-credentials",
    "-password",
    "-refresh-token",
    "-secret",
    "-token",
)

# Process invocation data is inspection-only. These keys may never cross the
# JSON/text response boundary, even when supplied by an untrusted manifest.
FORBIDDEN_RESPONSE_KEYS = frozenset(
    {
        "args",
        "arguments",
        "argv",
        "argv-raw",
        "cmd",
        "cmdline",
        "command",
        "command-line",
        "commands",
        "exec-args",
        "invocation-args",
        "process-args",
        "process-arguments",
        "process-argv",
        "process-command",
        "raw-argv",
        "raw-command",
    }
)


def normalized_name(value: str) -> str:
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", value)
    return separated.lower().lstrip("-").replace("_", "-")


def is_sensitive_name(value: str) -> bool:
    name = normalized_name(value)
    return name in SENSITIVE_NAMES or name.endswith(SENSITIVE_SUFFIXES)


def is_forbidden_response_key(value: str) -> bool:
    name = normalized_name(value)
    parts = set(name.split("-"))
    return (
        name in FORBIDDEN_RESPONSE_KEYS
        or bool({"argv", "cmdline", "command", "commands"} & parts)
        or name.startswith("argv-")
        or name.startswith("commands-")
        or name.startswith("cmdline-")
        or name.startswith("command-")
        or name.startswith("process-args-")
        or name.startswith("process-argv-")
        or name.startswith("process-command-")
        or name.startswith("raw-argv-")
        or name.startswith("raw-command-")
    )


def redact_text(value: str) -> str:
    """Redact common credential forms without returning the matched value."""

    text = str(value)
    # User-info in DSNs, including dialect suffixes such as postgresql+psycopg.
    text = re.sub(
        r"(?i)(\b[a-z][a-z0-9+.-]*://[^\s:/@?#]*:)([^\s/@?#]+)(@)",
        rf"\1{REDACTED}\3",
        text,
    )
    quoted_or_token = r"""(?:"[^"]*"|'[^']*'|[^\s,;&]+)"""
    text = re.sub(
        r"(?i)(\bauthorization\b\s*[:=]\s*)[^\r\n,;]+",
        rf"\1{REDACTED}",
        text,
    )
    text = re.sub(r"(?i)(\bBearer\s+)[^\s,;]+", rf"\1{REDACTED}", text)
    # Assignment and URL query forms: PGPASSWORD=x, token=x, ?password=x.
    generic_suffix = r"(?:[a-z0-9]+[_-])*(?:api[_-]?key|password|token|secret)"
    sensitive = (
        rf"{generic_suffix}|auth[_-]?token|authorization|aws[_-]?(?:access[_-]?key[_-]?id|"
        r"secret[_-]?access[_-]?key)|client[_-]?secret|credential(?:s)?|database[_-]?url|"
        r"dsn|password|passwd|pgpassword|pwd|refresh[_-]?token|secret|token"
    )
    text = re.sub(
        rf"(?i)(\b(?:{sensitive})\b\s*[=:]\s*)({quoted_or_token})",
        rf"\1{REDACTED}",
        text,
    )
    text = re.sub(
        rf"(?i)([?&](?:{sensitive})=)([^&#\s]+)",
        rf"\1{REDACTED}",
        text,
    )
    text = re.sub(
        rf"(?i)((?:--?)(?:{sensitive})(?:\s+|=))({quoted_or_token})",
        rf"\1{REDACTED}",
        text,
    )
    return text


def redact_argv(argv: Sequence[str]) -> tuple[list[str], list[dict[str, str]]]:
    """Compatibility helper; raw argv is never included in inspector output."""

    safe: list[str] = []
    findings: list[dict[str, str]] = []
    redact_next_for: str | None = None
    authorization_pending = False
    for raw_token in argv:
        token = str(raw_token)
        if redact_next_for is not None:
            safe.append(REDACTED)
            findings.append({"type": "sensitive-argv-value", "option": redact_next_for})
            redact_next_for = None
            continue
        if authorization_pending:
            safe.append(REDACTED)
            findings.append({"type": "sensitive-argv-value", "option": "authorization"})
            if token.strip().lower() in {"basic", "bearer"}:
                redact_next_for = "authorization"
            authorization_pending = False
            continue
        if token.strip().lower().rstrip(":=") == "authorization":
            safe.append(token)
            authorization_pending = True
            continue

        if token.startswith("-"):
            option, separator, option_value = token.partition("=")
            if is_sensitive_name(option):
                if separator:
                    safe.append(f"{option}={REDACTED}")
                    findings.append({"type": "sensitive-argv-value", "option": option})
                    if option_value.strip().lower() in {"basic", "bearer"}:
                        redact_next_for = option
                else:
                    safe.append(option)
                    redact_next_for = option
                continue

        key, separator, assigned_value = token.partition("=")
        if separator and is_sensitive_name(key):
            safe.append(f"{key}={REDACTED}")
            findings.append({"type": "sensitive-argv-value", "option": key})
            if assigned_value.strip().lower() in {"basic", "bearer"}:
                redact_next_for = key
            continue

        redacted = redact_text(token)
        if redacted != token:
            findings.append({"type": "sensitive-argv-value", "option": "embedded"})
        safe.append(redacted)
    return safe, dedupe_findings(findings)


def inspect_argv(argv: Sequence[str]) -> list[dict[str, str]]:
    """Return secret classifications only, never process arguments."""

    _safe, findings = redact_argv(argv)
    return [
        {"type": "sensitive-process-value", "option": finding.get("option", "unknown")}
        for finding in findings
    ]


def dedupe_findings(findings: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, str]] = []
    for finding in findings:
        identity = (finding.get("type", ""), finding.get("option", ""))
        if identity in seen:
            continue
        seen.add(identity)
        result.append(finding)
    return result


def sanitize(value: Any, key: str = "") -> Any:
    """Recursively redact secrets and drop invocation-bearing response keys."""

    if key and is_sensitive_name(key):
        return REDACTED
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for item_key, item_value in value.items():
            string_key = str(item_key)
            if is_forbidden_response_key(string_key):
                continue
            safe_key = redact_text(string_key)
            result[safe_key] = sanitize(item_value, string_key)
        return result
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
