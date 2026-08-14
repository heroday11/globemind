from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_PATH = PROJECT_ROOT / "data" / "source_curation" / "political_signal_source_system_v2.csv"
ADDITIONS_PATH = PROJECT_ROOT / "data" / "source_curation" / "strategic_source_additions_v2.csv"
OUTPUT_PATH = PROJECT_ROOT / "data" / "source_curation" / "political_signal_source_system_v3.csv"
REPORT_PATH = PROJECT_ROOT / "docs" / "POLITICAL_SIGNAL_SOURCE_SYSTEM_V3_REPORT.md"


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
        "# Political Signal Source System V3 Report",
        "",
        "日期：2026-06-21",
        "",
        f"- 总条目：`{len(rows)}`",
        f"- 新增/覆盖补充源：`{additions_count}`",
        f"- 输出文件：[political_signal_source_system_v3.csv](/root/data/globemind/data/source_curation/political_signal_source_system_v3.csv)",
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
        "## 本轮改进",
        "",
        "- 补入俄罗斯、乌克兰、以色列、肯尼亚、埃塞俄比亚、哥伦比亚、智利、巴基斯坦、孟加拉国、斯里兰卡、新加坡、台湾等官方直出层。",
        "- 强化非洲、拉美、南亚和中东的国家级主流媒体层。",
        "- 将尼日利亚官方源和东非区域媒体提升为优先层，改善非洲的结构性偏薄问题。",
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
        "- `official_direct` 负责正式政策立场与外交动作。",
        "- `wire_fast` 负责事件基线和高时效首发。",
        "- `media_narrative` 负责主流叙事、国内政治动态和跨国议程差异。",
        "- V3 目标不是平均分配站点数量，而是让主要政治地区具备更自然的多层政治信号结构。",
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
