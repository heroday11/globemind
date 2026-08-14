"""Bounded JSON decoding for mutable runtime state files."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any


class JSONSafetyError(ValueError):
    pass


def loads_bounded(text: str, *, max_depth: int = 32, max_nodes: int = 100_000) -> Any:
    def reject_constant(value: str) -> None:
        raise JSONSafetyError(f"non-finite JSON number {value!r}")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise JSONSafetyError(f"duplicate JSON field {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(
            text,
            parse_constant=reject_constant,
            object_pairs_hook=unique_object,
        )
    except JSONSafetyError:
        raise
    except (ValueError, RecursionError) as exc:
        raise JSONSafetyError(str(exc)) from exc

    nodes = 0
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > max_nodes:
            raise JSONSafetyError("JSON node limit exceeded")
        if depth > max_depth:
            raise JSONSafetyError("JSON nesting limit exceeded")
        if isinstance(item, float) and not math.isfinite(item):
            raise JSONSafetyError("non-finite JSON number")
        if isinstance(item, Mapping):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
    return value
