from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

REDACTED = "[REDACTED]"
FORBIDDEN_DIAGNOSTIC_KEYS = frozenset({"argv", "cmd", "cmdline", "command"})
_SENSITIVE_NAME = re.compile(
    r"(?i)(?:^|[_-])(?:api[_-]?key|auth[_-]?token|authorization|client[_-]?secret|"
    r"credential|database[_-]?password|password|passwd|pgpassword|pwd|refresh[_-]?token|"
    r"secret(?:[_-]?access[_-]?key)?|token)(?:$|[_-])"
)
_DATABASE_URL = re.compile(
    r"(?i)(\b(?:postgres(?:ql)?(?:\+[a-z0-9_.-]+)?|mysql|mariadb|"
    r"mongodb(?:\+srv)?|redis)://[^\s/:@]+:)([^\s/@]+)(@)"
)
_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:api[_-]?key|auth[_-]?token|authorization|aws[_-]?secret[_-]?access[_-]?key|"
    r"client[_-]?secret|credential|database[_-]?password|password|passwd|pgpassword|pwd|"
    r"refresh[_-]?token|secret|token)\b\s*[=:]\s*)([^\s,;]+)"
)
_FLAG_VALUE = re.compile(
    r"(?i)((?:--?)(?:api[_-]?key|auth[_-]?token|authorization|client[_-]?secret|"
    r"credential|database[_-]?password|password|passwd|pgpassword|pwd|refresh[_-]?token|"
    r"secret|token)(?:=|\s+))([^\s]+)"
)
_BEARER = re.compile(r"(?i)(\bBearer\s+)[^\s,;]+")


def redact_text(value: str) -> str:
    text = str(value)
    text = _DATABASE_URL.sub(rf"\1{REDACTED}\3", text)
    text = _BEARER.sub(rf"\1{REDACTED}", text)
    text = _ASSIGNMENT.sub(rf"\1{REDACTED}", text)
    return _FLAG_VALUE.sub(rf"\1{REDACTED}", text)


def sanitize_diagnostic(value: Any, *, key: str = "") -> Any:
    normalized_key = key.strip().lower().replace("-", "_")
    if normalized_key in FORBIDDEN_DIAGNOSTIC_KEYS:
        return None
    if key and _SENSITIVE_NAME.search(key):
        return REDACTED
    if isinstance(value, Mapping):
        return {
            str(item_key): sanitize_diagnostic(item_value, key=str(item_key))
            for item_key, item_value in value.items()
            if str(item_key).strip().lower().replace("-", "_")
            not in FORBIDDEN_DIAGNOSTIC_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_diagnostic(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
