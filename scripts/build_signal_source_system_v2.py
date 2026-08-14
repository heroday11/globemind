from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_PATH = PROJECT_ROOT / "data" / "source_curation" / "political_signal_source_system.csv"
ADDITIONS_PATH = PROJECT_ROOT / "data" / "source_curation" / "strategic_source_additions.csv"
OUTPUT_PATH = PROJECT_ROOT / "data" / "source_curation" / "political_signal_source_system_v2.csv"
REPORT_PATH = PROJECT_ROOT / "docs" / "POLITICAL_SIGNAL_SOURCE_SYSTEM_V2_REPORT.md"


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def merge_rows(base_rows: list[dict[str, str]], additions: list[dict[str, str]]) -> list[dict[str, str]]:
    merged = {row["site_id"]: row for row in base_rows}
    for row in additions:
        merged[row["site_id"]] = row
    return sorted(merged.values(), key=lambda row: (row["layer"], row["priority_tier"], row["region"], row["site_id"]))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_report(path: Path, rows: list[dict[str, str]], additions_count: int) -> None:
    layer_counts = Counter(row["layer"] for row in rows)
    region_counts = Counter(row["region"] for row in rows)
    priority_counts = Counter(row["priority_tier"] for row in rows)
    type_counts = Counter(row["source_type"] for row in rows)
    origin_counts = Counter(row["origin"] for row in rows)

    lines = [
        "# Political Signal Source System V2 Report",
        "",
        "日期：2026-06-21",
        "",
        f"- 总条目：`{len(rows)}`",
        f"- 新增战略补充源：`{additions_count}`",
        f"- 输出文件：[political_signal_source_system_v2.csv](/root/data/globemind/data/source_curation/political_signal_source_system_v2.csv)",
        "",
        "## 层级统计",
        "",
    ]
    for key in ("official_direct", "wire_fast", "media_narrative"):
        lines.append(f"- `{key}`：`{layer_counts.get(key, 0)}`")

    lines.extend([
        "",
        "## 优先级统计",
        "",
    ])
    for key in ("P0", "P1", "P2"):
        lines.append(f"- `{key}`：`{priority_counts.get(key, 0)}`")

    lines.extend([
        "",
        "## 区域统计",
        "",
    ])
    for key, value in sorted(region_counts.items()):
        lines.append(f"- `{key}`：`{value}`")

    lines.extend([
        "",
        "## 来源统计",
        "",
    ])
    for key, value in sorted(origin_counts.items()):
        lines.append(f"- `{key}`：`{value}`")

    lines.extend([
        "",
        "## 重点改进",
        "",
        "- 补入全球通讯社骨干：Reuters、AP、AFP。",
        "- 扩充官方信号层：NATO、OSCE、OAS、ASEAN、澳大利亚、加拿大、墨西哥、阿根廷、沙特、土耳其、印尼、韩国等。",
        "- 增强非洲和拉美媒体覆盖。",
        "- 保留亚洲与中东的任务相关高密度覆盖。",
        "",
        "## 类型统计",
        "",
    ])
    for key, value in sorted(type_counts.items()):
        lines.append(f"- `{key}`：`{value}`")

    lines.extend([
        "",
        "## 说明",
        "",
        "- 这版不再只是主媒体清单，而是面向全球政治动态追踪的分层信号体系。",
        "- `official_direct` 负责正式立场与政策动作。",
        "- `wire_fast` 负责事件基线与高时效更新。",
        "- `media_narrative` 负责跨国家叙事差异与议程设置观察。",
    ])

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    base_rows = load_csv(BASE_PATH)
    additions = load_csv(ADDITIONS_PATH)
    rows = merge_rows(base_rows, additions)
    write_csv(OUTPUT_PATH, rows)
    write_report(REPORT_PATH, rows, len(additions))
    print(f"wrote {len(rows)} rows to {OUTPUT_PATH}")
    print(f"report {REPORT_PATH}")


if __name__ == "__main__":
    main()
