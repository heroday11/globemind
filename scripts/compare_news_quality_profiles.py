#!/usr/bin/env python3
"""Compare two trusted-format offline news quality profiles.

The command reads bounded, content-free profile JSON files and creates one new
comparison artifact with no-replace semantics. It never supplies a release
decision: expected volume, cadence, and thresholds require explicit approval.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from news_ingest_quality import compare_news_quality_profiles
from profile_news_quality import (
    ProfileInputError,
    _assert_safe_path,
    _finite_json_float,
    _reject_duplicate_keys,
    _validate_json_shape,
    write_json_no_replace,
)


DEFAULT_MAX_PROFILE_BYTES = 16 * 1024 * 1024
MAX_PROFILE_BYTES = DEFAULT_MAX_PROFILE_BYTES


def load_profile_json(
    path: Path, *, max_profile_bytes: int = DEFAULT_MAX_PROFILE_BYTES
) -> dict[str, Any]:
    if (
        type(max_profile_bytes) is not int
        or max_profile_bytes <= 0
        or max_profile_bytes > MAX_PROFILE_BYTES
    ):
        raise ProfileInputError("profile byte limit exceeds its hard limit")
    resolved = _assert_safe_path(path, must_exist=True)
    if not resolved.is_file():
        raise ProfileInputError("profile input must be a regular file")
    stat = resolved.stat()
    if stat.st_nlink != 1:
        raise ProfileInputError("hard-linked profile inputs are forbidden")
    if stat.st_size > max_profile_bytes:
        raise ProfileInputError("profile input exceeds the configured byte limit")
    try:
        payload = json.loads(
            resolved.read_bytes().decode("utf-8"),
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
        raise ProfileInputError("profile input is invalid JSON") from exc
    _validate_json_shape(payload)
    if not isinstance(payload, dict):
        raise ProfileInputError("profile input must contain an object")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--current", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--max-profile-bytes", type=int, default=DEFAULT_MAX_PROFILE_BYTES
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    baseline = load_profile_json(
        args.baseline, max_profile_bytes=args.max_profile_bytes
    )
    current = load_profile_json(
        args.current, max_profile_bytes=args.max_profile_bytes
    )
    comparison = compare_news_quality_profiles(baseline, current)
    write_json_no_replace(args.output, comparison)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "comparison_state": comparison["comparison_state"],
                "release_decision": comparison["release_decision"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
