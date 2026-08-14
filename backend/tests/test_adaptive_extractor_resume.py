from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from adaptive_global_extractor import load_processed_rows  # noqa: E402


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def test_resume_counts_only_current_input_keys(tmp_path: Path):
    output = tmp_path / "articles.jsonl"
    errors = tmp_path / "errors.jsonl"
    write_jsonl(
        output,
        [
            {"request_url": "https://example.com/in-window", "body": "abc"},
            {"request_url": "https://example.com/old-pruned-out", "body": "xyz"},
        ],
    )
    write_jsonl(errors, [{"request_url": "https://example.com/failed", "error": "http_500", "site_id": "example"}])

    processed, successes, failures, _bytes, body_chars, errors_by_type, errors_by_site = load_processed_rows(
        output,
        errors,
        {"https://example.com/in-window", "https://example.com/failed"},
    )

    assert processed == {"https://example.com/in-window", "https://example.com/failed"}
    assert successes == 1
    assert failures == 1
    assert body_chars == 3
    assert errors_by_type["http_500"] == 1
    assert errors_by_site["example"] == 1


def test_resume_success_wins_over_existing_error(tmp_path: Path):
    output = tmp_path / "articles.jsonl"
    errors = tmp_path / "errors.jsonl"
    write_jsonl(output, [{"request_url": "https://example.com/story", "body": "abc"}])
    write_jsonl(errors, [{"request_url": "https://example.com/story", "error": "timeout", "site_id": "example"}])

    processed, successes, failures, *_rest = load_processed_rows(output, errors, {"https://example.com/story"})

    assert processed == {"https://example.com/story"}
    assert successes == 1
    assert failures == 0
