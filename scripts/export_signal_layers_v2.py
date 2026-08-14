from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = PROJECT_ROOT / "data" / "source_curation" / "political_signal_source_system_v2.csv"
OUTPUT_DIR = PROJECT_ROOT / "data" / "source_curation"
REPORT_PATH = PROJECT_ROOT / "docs" / "POLITICAL_SIGNAL_LAYER_EXPORT_REPORT.md"


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    rows = load_rows(SOURCE_PATH)
    official = [row for row in rows if row["layer"] == "official_direct"]
    wire = [row for row in rows if row["layer"] == "wire_fast"]
    media = [row for row in rows if row["layer"] == "media_narrative"]
    priority = [row for row in rows if row["priority_tier"] in {"P0", "P1"}]

    write_rows(OUTPUT_DIR / "official_direct_v2.csv", official)
    write_rows(OUTPUT_DIR / "wire_fast_v2.csv", wire)
    write_rows(OUTPUT_DIR / "media_narrative_v2.csv", media)
    write_rows(OUTPUT_DIR / "political_signal_priority_v2.csv", priority)

    layer_counts = Counter(row["layer"] for row in rows)
    priority_counts = Counter(row["priority_tier"] for row in priority)

    lines = [
        "# Political Signal Layer Export Report",
        "",
        "日期：2026-06-21",
        "",
        "- 输入文件：[political_signal_source_system_v2.csv](/root/data/globemind/data/source_curation/political_signal_source_system_v2.csv)",
        "",
        "## 输出",
        "",
        "- [official_direct_v2.csv](/root/data/globemind/data/source_curation/official_direct_v2.csv)",
        "- [wire_fast_v2.csv](/root/data/globemind/data/source_curation/wire_fast_v2.csv)",
        "- [media_narrative_v2.csv](/root/data/globemind/data/source_curation/media_narrative_v2.csv)",
        "- [political_signal_priority_v2.csv](/root/data/globemind/data/source_curation/political_signal_priority_v2.csv)",
        "",
        "## 统计",
        "",
        f"- `official_direct`：`{layer_counts.get('official_direct', 0)}`",
        f"- `wire_fast`：`{layer_counts.get('wire_fast', 0)}`",
        f"- `media_narrative`：`{layer_counts.get('media_narrative', 0)}`",
        f"- `P0/P1 priority`：`{len(priority)}`",
        f"- `P0`：`{priority_counts.get('P0', 0)}`",
        f"- `P1`：`{priority_counts.get('P1', 0)}`",
    ]
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("exports written")


if __name__ == "__main__":
    main()
