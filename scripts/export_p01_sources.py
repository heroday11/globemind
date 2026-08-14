from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FULL_CATALOG = PROJECT_ROOT / "data" / "source_curation" / "full_source_catalog.csv"
OUTPUT_PATH = PROJECT_ROOT / "data" / "source_curation" / "p01_master_sources.csv"
REPORT_PATH = PROJECT_ROOT / "docs" / "SOURCE_P01_REPORT.md"


CRAWL_ENTRY_OVERRIDES = {
    "abcnews_go_com": "https://abcnews.go.com/international",
    "csmonitor_com": "https://www.csmonitor.com/World",
    "dpa_com": "https://www.dpa.com/en/international-news",
    "efe_com": "https://efe.com/english/",
    "id_mofcom_gov_cn": "https://fdi.mofcom.gov.cn/EN/come-zonghe-list.html",
    "nbcnews_com": "https://www.nbcnews.com/world",
    "nikkei_com": "https://www.nikkei.com/world/",
    "thestar_com_my": "https://www.thestar.com.my/news/world",
    "todayonline_com": "https://www.todayonline.com/world",
    "tuoitre_vn": "https://tuoitre.vn/the-gioi.htm",
}


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def choose_batch(row: dict[str, str]) -> str:
    source_type = row["source_type"]
    region = row["region"]
    priority = row["priority_tier"]

    if priority == "P0":
        if source_type in {"official_io", "official_government", "state_media"}:
            return "batch_1_official_and_state"
        if region in {"asia", "middle_east"}:
            return "batch_2_asia_middle_east"
        return "batch_3_global_and_western"

    if source_type in {"public_broadcaster", "wire_service"}:
        return "batch_4_public_and_wire"
    if region in {"asia", "middle_east"}:
        return "batch_5_asia_expansion"
    return "batch_6_global_expansion"


def build_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in rows:
        if row["priority_tier"] not in {"P0", "P1"}:
            continue
        crawl_entry = CRAWL_ENTRY_OVERRIDES.get(row["site_id"], row["url"])
        entry = {
            "site_id": row["site_id"],
            "domain": row["domain"],
            "priority_tier": row["priority_tier"],
            "quality_tier": row["quality_tier"],
            "source_type": row["source_type"],
            "region": row["region"],
            "batch": choose_batch(row),
            "preferred_crawl_entry": crawl_entry,
            "original_url": row["url"],
            "notes": row["notes"],
        }
        out.append(entry)
    return out


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_report(path: Path, rows: list[dict[str, str]]) -> None:
    by_priority = Counter(row["priority_tier"] for row in rows)
    by_batch = Counter(row["batch"] for row in rows)
    by_type = Counter(row["source_type"] for row in rows)
    overrides = [row for row in rows if row["preferred_crawl_entry"] != row["original_url"]]

    lines = [
        "# Source P0 P1 Report",
        "",
        "日期：2026-06-21",
        "",
        f"- 主库候选总数：`{len(rows)}`",
        f"- 输出文件：[p01_master_sources.csv](/root/data/globemind/data/source_curation/p01_master_sources.csv)",
        "",
        "## 优先级",
        "",
        f"- `P0`：`{by_priority.get('P0', 0)}`",
        f"- `P1`：`{by_priority.get('P1', 0)}`",
        "",
        "## 批次建议",
        "",
    ]
    for batch, count in sorted(by_batch.items()):
        lines.append(f"- `{batch}`：`{count}`")

    lines.extend([
        "",
        "## 类型分布",
        "",
    ])
    for key, value in sorted(by_type.items()):
        lines.append(f"- `{key}`：`{value}`")

    lines.extend([
        "",
        "## 调整过的抓取入口",
        "",
    ])
    for row in overrides:
        lines.append(
            f"- `{row['site_id']}`：`{row['original_url']}` -> `{row['preferred_crawl_entry']}`"
        )

    lines.extend([
        "",
        "## 说明",
        "",
        "- `preferred_crawl_entry` 是建议优先抓取的入口页。",
        "- `original_url` 保留了你最初给出的原始入口，方便回溯。",
        "- 建议先按 `batch_1` 到 `batch_3` 开抓，再逐步扩展到 `batch_4` 到 `batch_6`。",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    rows = load_rows(FULL_CATALOG)
    out = build_rows(rows)
    write_rows(OUTPUT_PATH, out)
    write_report(REPORT_PATH, out)
    print(f"wrote {len(out)} rows to {OUTPUT_PATH}")
    print(f"report {REPORT_PATH}")


if __name__ == "__main__":
    main()
