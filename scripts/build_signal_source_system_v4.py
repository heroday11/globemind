from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_PATH = PROJECT_ROOT / "data" / "source_curation" / "political_signal_source_system_v3.csv"
ADDITIONS_PATH = PROJECT_ROOT / "data" / "source_curation" / "strategic_source_additions_v3.csv"
OUTPUT_PATH = PROJECT_ROOT / "data" / "source_curation" / "political_signal_source_system_v4.csv"
REPORT_PATH = PROJECT_ROOT / "docs" / "POLITICAL_SIGNAL_SOURCE_SYSTEM_V4_REPORT.md"


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
        "# Political Signal Source System V4 Report",
        "",
        "日期：2026-06-21",
        "",
        f"- 总条目：`{len(rows)}`",
        f"- 新增/覆盖补充源：`{additions_count}`",
        f"- 输出文件：[political_signal_source_system_v4.csv](/root/data/globemind/data/source_curation/political_signal_source_system_v4.csv)",
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
        "- 补入秘鲁、马来西亚、新西兰官方直出层，完善拉美、东盟和亚太官方覆盖。",
        "- 强化泰国、菲律宾、厄瓜多尔主流媒体层，补齐国家级叙事源。",
        "- 将 `rfi_english` 提升到优先层，增强法语非洲和泛非叙事覆盖。",
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
        "- V4 继续保持分层结构，不追求站点均匀分布，而追求关键政治区域具备足够的官方、事件和叙事三层信号。",
        "- 这一版已经接近数据抓取前的候选源稳定版，后续新增应更偏向抓取可行性和质量验证，而不是继续大幅扩站。",
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
