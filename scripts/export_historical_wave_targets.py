#!/usr/bin/env python3
from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
INPUT_PATH = PROJECT_ROOT / "data" / "source_curation" / "historical_source_manifest_v1_fast.csv"
WAVE1_PATH = PROJECT_ROOT / "data" / "source_curation" / "historical_wave1_targets.csv"
WAVE2_PATH = PROJECT_ROOT / "data" / "source_curation" / "historical_wave2_targets.csv"
REPORT_PATH = PROJECT_ROOT / "docs" / "HISTORICAL_WAVE_TARGETS_REPORT.md"

WAVE1_STRATEGIES = {"direct_sitemap", "feed_plus_archive"}


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else [])
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    rows = read_rows(INPUT_PATH)
    wave1 = [row for row in rows if row["historical_strategy"] in WAVE1_STRATEGIES]
    wave2 = [row for row in rows if row["historical_strategy"] not in WAVE1_STRATEGIES]

    wave1.sort(key=lambda row: (row["layer"], row["priority_tier"], row["site_id"]))
    wave2.sort(key=lambda row: (row["historical_strategy"], row["layer"], row["site_id"]))

    write_rows(WAVE1_PATH, wave1)
    write_rows(WAVE2_PATH, wave2)

    c1 = Counter(row["layer"] for row in wave1)
    c2 = Counter(row["layer"] for row in wave2)
    lines = [
        "# Historical Wave Targets",
        "",
        f"- Input: [{INPUT_PATH.name}]({INPUT_PATH})",
        f"- Wave 1: [{WAVE1_PATH.name}]({WAVE1_PATH})",
        f"- Wave 2: [{WAVE2_PATH.name}]({WAVE2_PATH})",
        "",
        "## Counts",
        "",
        f"- Wave 1: `{len(wave1)}`",
        f"- Wave 2: `{len(wave2)}`",
        "",
        "## Wave 1 By Layer",
        "",
    ]
    for key, value in sorted(c1.items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Wave 2 By Layer", ""])
    for key, value in sorted(c2.items()):
        lines.append(f"- `{key}`: `{value}`")

    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {len(wave1)} wave1 rows to {WAVE1_PATH}")
    print(f"wrote {len(wave2)} wave2 rows to {WAVE2_PATH}")
    print(f"wrote report to {REPORT_PATH}")


if __name__ == "__main__":
    main()
