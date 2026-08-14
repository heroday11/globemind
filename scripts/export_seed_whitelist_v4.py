from __future__ import annotations

import csv
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = PROJECT_ROOT / "data" / "source_curation" / "political_signal_priority_v4.csv"
OUTPUT_DIR = PROJECT_ROOT / "data" / "source_curation"
REPORT_PATH = PROJECT_ROOT / "docs" / "SEED_WHITELIST_V4_REPORT.md"


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def map_seed_tier(row: dict[str, str]) -> str:
    if row["layer"] in {"official_direct", "wire_fast"}:
        return "A"
    if row["priority_tier"] == "P0":
        return "A"
    return "B"


def map_seed_type(row: dict[str, str]) -> str:
    if row["layer"] == "official_direct":
        return "official"
    if row["layer"] == "wire_fast":
        return "wire"
    if row["source_type"] in {"global_major_media", "public_broadcaster"}:
        return "major_media"
    if row["source_type"] in {"regional_major_media"}:
        return "regional_media"
    return "national_media"


def to_seed_row(row: dict[str, str]) -> dict[str, str]:
    return {
        "site_id": row["site_id"],
        "url": row["preferred_entry"],
        "domain": row["domain"],
        "seed_tier": map_seed_tier(row),
        "seed_type": map_seed_type(row),
        "layer": row["layer"],
        "priority_tier": row["priority_tier"],
        "region": row["region"],
        "source_type": row["source_type"],
        "signal_role": row["signal_role"],
        "notes": row["notes"],
    }


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    rows = [to_seed_row(row) for row in load_rows(SOURCE_PATH)]
    rows.sort(key=lambda row: (row["seed_tier"], row["layer"], row["region"], row["site_id"]))

    a_rows = [row for row in rows if row["seed_tier"] == "A"]
    b_rows = [row for row in rows if row["seed_tier"] == "B"]

    write_rows(OUTPUT_DIR / "seed_whitelist_priority_v4.csv", rows)
    write_rows(OUTPUT_DIR / "seed_whitelist_priority_v4_a.csv", a_rows)
    write_rows(OUTPUT_DIR / "seed_whitelist_priority_v4_b.csv", b_rows)

    lines = [
        "# Seed Whitelist V4 Report",
        "",
        "日期：2026-06-21",
        "",
        "- 输入文件：[political_signal_priority_v4.csv](/root/data/globemind/data/source_curation/political_signal_priority_v4.csv)",
        "",
        "## 输出",
        "",
        "- [seed_whitelist_priority_v4.csv](/root/data/globemind/data/source_curation/seed_whitelist_priority_v4.csv)",
        "- [seed_whitelist_priority_v4_a.csv](/root/data/globemind/data/source_curation/seed_whitelist_priority_v4_a.csv)",
        "- [seed_whitelist_priority_v4_b.csv](/root/data/globemind/data/source_curation/seed_whitelist_priority_v4_b.csv)",
        "",
        "## 统计",
        "",
        f"- 全部种子：`{len(rows)}`",
        f"- `A` 级种子：`{len(a_rows)}`",
        f"- `B` 级种子：`{len(b_rows)}`",
        "",
        "## 规则",
        "",
        "- `A`：全部 `official_direct`、全部 `wire_fast`、以及 `P0` 主媒体源。",
        "- `B`：其余 `P1` 主流媒体叙事源。",
        "- 建议抓取顺序：先 `A` 后 `B`。",
    ]
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"seed_all={len(rows)}")
    print(f"seed_a={len(a_rows)}")
    print(f"seed_b={len(b_rows)}")


if __name__ == "__main__":
    main()
