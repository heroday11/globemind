from __future__ import annotations

import csv
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
P01_PATH = PROJECT_ROOT / "data" / "source_curation" / "p01_master_sources.csv"
OFFICIAL_PATH = PROJECT_ROOT / "data" / "source_curation" / "official_signal_sources.csv"
OUTPUT_PATH = PROJECT_ROOT / "data" / "source_curation" / "political_signal_source_system.csv"
REPORT_PATH = PROJECT_ROOT / "docs" / "POLITICAL_SIGNAL_SOURCE_SYSTEM_REPORT.md"


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def map_media_row(row: dict[str, str]) -> dict[str, str]:
    source_type = row["source_type"]
    if source_type in {"official_io", "official_government"}:
        layer = "official_direct"
        role = "official_position"
    elif source_type in {"wire_service", "state_media"}:
        layer = "wire_fast"
        role = "event_baseline"
    else:
        layer = "media_narrative"
        role = "narrative_and_agenda"

    return {
        "site_id": row["site_id"],
        "domain": row["domain"],
        "layer": layer,
        "priority_tier": row["priority_tier"],
        "region": row["region"],
        "source_type": row["source_type"],
        "preferred_entry": row["preferred_crawl_entry"],
        "signal_role": role,
        "origin": "media_core",
        "notes": row["notes"],
    }


def map_official_row(row: dict[str, str]) -> dict[str, str]:
    return {
        "site_id": row["site_id"],
        "domain": row["domain"],
        "layer": "official_direct",
        "priority_tier": row["priority_tier"],
        "region": row["region"],
        "source_type": row["institution_type"],
        "preferred_entry": row["preferred_crawl_entry"],
        "signal_role": row["role"],
        "origin": "official_expansion",
        "notes": row["notes"],
    }


def build_rows() -> list[dict[str, str]]:
    rows = [map_media_row(row) for row in load_csv(P01_PATH)]
    rows.extend(map_official_row(row) for row in load_csv(OFFICIAL_PATH))
    return rows


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_report(path: Path, rows: list[dict[str, str]]) -> None:
    from collections import Counter

    layer_counts = Counter(row["layer"] for row in rows)
    origin_counts = Counter(row["origin"] for row in rows)
    region_counts = Counter(row["region"] for row in rows)

    lines = [
        "# Political Signal Source System Report",
        "",
        "日期：2026-06-21",
        "",
        f"- 总条目：`{len(rows)}`",
        f"- 系统文件：[political_signal_source_system.csv](/root/data/globemind/data/source_curation/political_signal_source_system.csv)",
        "",
        "## 按层统计",
        "",
    ]
    for key in ("official_direct", "wire_fast", "media_narrative"):
        lines.append(f"- `{key}`：`{layer_counts.get(key, 0)}`")

    lines.extend([
        "",
        "## 来源统计",
        "",
    ])
    for key, value in sorted(origin_counts.items()):
        lines.append(f"- `{key}`：`{value}`")

    lines.extend([
        "",
        "## 区域统计",
        "",
    ])
    for key, value in sorted(region_counts.items()):
        lines.append(f"- `{key}`：`{value}`")

    lines.extend([
        "",
        "## 说明",
        "",
        "- `official_direct` 用于跟踪正式立场与政策动作。",
        "- `wire_fast` 用于建立高时效事件基线。",
        "- `media_narrative` 用于观察国家叙事和议程设置差异。",
    ])

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    rows = build_rows()
    write_rows(OUTPUT_PATH, rows)
    write_report(REPORT_PATH, rows)
    print(f"wrote {len(rows)} rows to {OUTPUT_PATH}")
    print(f"report {REPORT_PATH}")


if __name__ == "__main__":
    main()
