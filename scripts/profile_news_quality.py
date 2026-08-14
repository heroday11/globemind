#!/usr/bin/env python3
"""Create a bounded, content-free JSON profile for a news JSONL sample.

The command is intentionally offline: it does not connect to GlobeMind's
database, services, release artifacts, or external sources.  Output is created
with no-replace semantics so an earlier evidence file cannot be overwritten.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from news_ingest_quality import (
    DEFAULT_MAX_CANDIDATE_PAIR_COMPARISONS,
    DEFAULT_PROFILE_MAX_ROWS,
    MAX_CANDIDATE_PAIR_COMPARISONS,
    MAX_PROFILE_ROWS,
    profile_news_rows,
)


DEFAULT_MAX_INPUT_BYTES = 128 * 1024 * 1024
DEFAULT_MAX_LINE_BYTES = 4 * 1024 * 1024
MAX_INPUT_BYTES = DEFAULT_MAX_INPUT_BYTES
MAX_LINE_BYTES = DEFAULT_MAX_LINE_BYTES
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 100_000
FORBIDDEN_RELEASE_ROOT = Path("/root/data/releases/globemind")


class ProfileInputError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProfileInputError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _finite_json_float(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value):
        raise ProfileInputError("non-finite JSON number")
    return value


def _validate_json_shape(value: Any) -> None:
    stack: list[tuple[Any, int]] = [(value, 0)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES:
            raise ProfileInputError("JSON record exceeds the node limit")
        if depth > MAX_JSON_DEPTH:
            raise ProfileInputError("JSON record exceeds the nesting depth limit")
        if isinstance(current, dict):
            stack.extend((child, depth + 1) for child in current.values())
        elif isinstance(current, list):
            stack.extend((child, depth + 1) for child in current)


def _assert_safe_path(path: Path, *, must_exist: bool) -> Path:
    candidate = path.expanduser()
    resolved = candidate.resolve(strict=must_exist)
    try:
        resolved.relative_to(FORBIDDEN_RELEASE_ROOT)
    except ValueError:
        pass
    else:
        raise ProfileInputError("release artifact paths are forbidden")

    current = candidate if candidate.is_absolute() else Path.cwd() / candidate
    parts = current.parts
    probe = Path(parts[0]) if current.is_absolute() else Path.cwd()
    start = 1 if current.is_absolute() else 0
    for part in parts[start:]:
        probe = probe / part
        if probe.is_symlink():
            raise ProfileInputError("symlink path components are forbidden")
    return resolved


def load_jsonl(
    path: Path,
    *,
    max_rows: int,
    max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES,
    max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
) -> list[dict[str, Any]]:
    if (
        type(max_rows) is not int
        or max_rows <= 0
        or max_rows > MAX_PROFILE_ROWS
    ):
        raise ProfileInputError("max_rows exceeds its hard limit")
    if (
        type(max_input_bytes) is not int
        or max_input_bytes <= 0
        or max_input_bytes > MAX_INPUT_BYTES
    ):
        raise ProfileInputError("input byte limit exceeds its hard limit")
    if (
        type(max_line_bytes) is not int
        or max_line_bytes <= 0
        or max_line_bytes > MAX_LINE_BYTES
    ):
        raise ProfileInputError("line byte limit exceeds its hard limit")
    resolved = _assert_safe_path(path, must_exist=True)
    if not resolved.is_file():
        raise ProfileInputError("input must be a regular file")
    stat = resolved.stat()
    if stat.st_nlink != 1:
        raise ProfileInputError("hard-linked input files are forbidden")
    if stat.st_size > max_input_bytes:
        raise ProfileInputError("input exceeds the configured byte limit")

    rows: list[dict[str, Any]] = []
    with resolved.open("rb") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            if len(raw_line) > max_line_bytes:
                raise ProfileInputError(f"line {line_number} exceeds the byte limit")
            if not raw_line.strip():
                continue
            try:
                value = json.loads(
                    raw_line.decode("utf-8"),
                    object_pairs_hook=_reject_duplicate_keys,
                    parse_float=_finite_json_float,
                    parse_constant=lambda value: (_ for _ in ()).throw(
                        ProfileInputError(f"non-finite JSON number: {value}")
                    ),
                )
            except ProfileInputError:
                raise
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                OverflowError,
                RecursionError,
                ValueError,
            ) as exc:
                raise ProfileInputError(f"line {line_number} is invalid JSON") from exc
            if not isinstance(value, dict):
                raise ProfileInputError(f"line {line_number} must contain an object")
            _validate_json_shape(value)
            rows.append(value)
            if len(rows) > max_rows:
                break
    return rows


def write_json_no_replace(path: Path, payload: dict[str, Any]) -> None:
    resolved = _assert_safe_path(path, must_exist=False)
    parent = resolved.parent
    if not parent.is_dir():
        raise ProfileInputError("output parent must already exist")
    if resolved.exists():
        raise FileExistsError(f"refusing to overwrite existing report: {resolved}")

    encoded = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    temporary_name: str | None = None
    try:
        fd, temporary_name = tempfile.mkstemp(prefix=".quality-profile-", dir=parent)
        os.fchmod(fd, 0o640)
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary_name, resolved)
        directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="bounded JSONL input")
    parser.add_argument("--output", required=True, type=Path, help="new JSON report path")
    parser.add_argument("--max-rows", type=int, default=DEFAULT_PROFILE_MAX_ROWS)
    parser.add_argument(
        "--max-candidate-pairs",
        type=int,
        default=DEFAULT_MAX_CANDIDATE_PAIR_COMPARISONS,
        help="bounded near-duplicate candidate-pair comparison budget",
    )
    parser.add_argument("--max-input-bytes", type=int, default=DEFAULT_MAX_INPUT_BYTES)
    parser.add_argument(
        "--now",
        help="optional ISO-8601 evaluation time for reproducible offline tests",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if (
        type(args.max_rows) is not int
        or args.max_rows <= 0
        or args.max_rows > MAX_PROFILE_ROWS
    ):
        raise ProfileInputError(
            f"--max-rows must be a positive integer no greater than {MAX_PROFILE_ROWS}"
        )
    if (
        type(args.max_input_bytes) is not int
        or args.max_input_bytes <= 0
        or args.max_input_bytes > MAX_INPUT_BYTES
    ):
        raise ProfileInputError(
            "--max-input-bytes must be a positive integer no greater than "
            f"{MAX_INPUT_BYTES}"
        )
    if (
        type(args.max_candidate_pairs) is not int
        or args.max_candidate_pairs <= 0
        or args.max_candidate_pairs > MAX_CANDIDATE_PAIR_COMPARISONS
    ):
        raise ProfileInputError(
            "--max-candidate-pairs must be a positive integer no greater than "
            f"{MAX_CANDIDATE_PAIR_COMPARISONS}"
        )
    now = datetime.fromisoformat(args.now.replace("Z", "+00:00")) if args.now else None
    rows = load_jsonl(
        args.input,
        max_rows=args.max_rows,
        max_input_bytes=args.max_input_bytes,
    )
    profile = profile_news_rows(
        rows,
        now=now,
        max_rows=args.max_rows,
        max_candidate_pair_comparisons=args.max_candidate_pairs,
    )
    write_json_no_replace(args.output, profile)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "evaluated_rows": profile["scope"]["evaluated_rows"],
                "truncated": profile["scope"]["truncated"],
                "near_duplicate_candidate_pairs": profile[
                    "near_duplicate_candidates"
                ]["candidate_pairs_observed"],
                "near_duplicate_observation_overflow": any(
                    profile["near_duplicate_candidates"][key]
                    for key in (
                        "row_evaluation_truncated",
                        "profile_scope_truncated",
                        "comparison_overflow",
                        "candidate_generation_overflow",
                    )
                ),
                "release_decision": profile["assurance"]["release_decision"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
