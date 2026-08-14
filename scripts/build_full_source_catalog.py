from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

from curate_source_catalog import (
    HIGH_VALUE_MEDIA,
    NATIONAL_MAJOR_HINTS,
    OFFICIAL_DOMAIN_HINTS,
    THINK_TANK_HINTS,
    classify,
    contains_any,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_PATH = PROJECT_ROOT / "data" / "source_curation" / "raw_sources.tsv"
RECOMMENDED_PATH = PROJECT_ROOT / "data" / "source_curation" / "recommended_sources_catalog.csv"
OUTPUT_PATH = PROJECT_ROOT / "data" / "source_curation" / "full_source_catalog.csv"
SUMMARY_PATH = PROJECT_ROOT / "docs" / "SOURCE_FULL_CLASSIFICATION_REPORT.md"


PUBLIC_BROADCASTER_HINTS = (
    "bbc",
    "cbc",
    "npr",
    "rtve",
    "rnz",
    "vov",
    "thaipbs",
    "voa",
    "abc.net.au",
)

WIRE_HINTS = (
    "afp",
    "apnews",
    "aapnews",
    "ansa",
    "dpa",
    "efe",
    "interfax",
    "kyodo",
    "bernama",
    "unb.com.bd",
)

STATE_MEDIA_HINTS = (
    "irna",
    "tass",
    "wafa",
    "sana",
    "xinhua",
    "cri.cn",
    "china.com",
    "cctv",
    "avn.info.ve",
    "portalangop.co.ao",
)

BUSINESS_MEDIA_HINTS = (
    "nikkei",
    "businesstimes",
    "theedgemalaysia",
    "bisnis.com",
    "fortune.com",
    "forbes.com",
    "ilsole24ore",
)

REGIONAL_NETWORK_HINTS = (
    "asianews.network",
    "allafrica.com",
)

MANUAL_OVERRIDES = {
    "abcnews_go_com": {
        "priority_tier": "P1",
        "quality_tier": "B",
        "source_type": "global_major_media",
        "region": "north_america",
        "action": "secondary_crawl",
        "notes": "major US broadcaster; use international desk rather than climate alerts page",
    },
    "bbc_co_uk": {
        "priority_tier": "P2",
        "quality_tier": "C",
        "source_type": "public_broadcaster",
        "region": "asia",
        "action": "selective_crawl",
        "notes": "BBC Burmese service is useful for Myanmar coverage but not core main crawl",
    },
    "dpa_com": {
        "priority_tier": "P1",
        "quality_tier": "B",
        "source_type": "wire_service",
        "region": "europe",
        "action": "secondary_crawl",
        "notes": "major wire; use English international news service rather than corporate pages",
    },
    "esdm_go_id": {
        "priority_tier": "P2",
        "quality_tier": "C",
        "source_type": "official_government",
        "region": "asia",
        "action": "selective_crawl",
        "notes": "official ministry site with narrow policy scope",
    },
    "id_mofcom_gov_cn": {
        "priority_tier": "P1",
        "quality_tier": "B",
        "source_type": "official_government",
        "region": "asia",
        "action": "secondary_crawl",
        "notes": "official commerce and investment news source useful for China policy framing",
    },
    "io_gov_mo": {
        "priority_tier": "P2",
        "quality_tier": "C",
        "source_type": "official_government",
        "region": "asia",
        "action": "selective_crawl",
        "notes": "official Macau public administration source with limited newsroom relevance",
    },
    "kemenkopmk_go_id": {
        "priority_tier": "P2",
        "quality_tier": "C",
        "source_type": "official_government",
        "region": "asia",
        "action": "selective_crawl",
        "notes": "official ministry site; useful selectively rather than in the main crawl",
    },
}

NOISE_KEYWORDS = (
    "dictionary",
    "wiki",
    "forum",
    "travel",
    "tourism",
    "poetry",
    "medical reference",
    "sports site",
    "video platform",
    "brand site",
    "e-commerce site",
    "blog platform",
    "lifestyle",
    "celebrity",
)


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def choose_source_type(site_id: str, domain: str, reason: str, tier: str) -> str:
    raw = f"{site_id.lower()} {domain.lower()} {reason.lower()}"
    if tier == "D" or contains_any(raw, NOISE_KEYWORDS):
        return "noise"
    if contains_any(raw, OFFICIAL_DOMAIN_HINTS):
        if "un.org" in raw or "who.int" in raw:
            return "official_io"
        return "official_government"
    if contains_any(raw, THINK_TANK_HINTS):
        return "think_tank_context"
    if contains_any(raw, PUBLIC_BROADCASTER_HINTS):
        return "public_broadcaster"
    if contains_any(raw, WIRE_HINTS):
        return "wire_service"
    if contains_any(raw, STATE_MEDIA_HINTS):
        return "state_media"
    if contains_any(raw, BUSINESS_MEDIA_HINTS):
        return "business_media"
    if contains_any(raw, REGIONAL_NETWORK_HINTS):
        return "regional_network"
    if domain in HIGH_VALUE_MEDIA:
        return "global_major_media"
    if domain in NATIONAL_MAJOR_HINTS:
        return "national_major_media"
    if "asia" in raw or "africa" in raw:
        return "regional_major_media"
    if tier == "C":
        return "regional_media_candidate"
    return "national_major_media"


def choose_region(site_id: str, domain: str) -> str:
    raw = f"{site_id.lower()} {domain.lower()}"
    if any(token in raw for token in ("japan", "jp", "korea", "china", "taiwan", "vietnam", "thai", "indonesia", "malaysia", "singapore", "philipp", "lao", "myanmar", "bangla", "pakistan", "india", "sri", "cambodia")):
        return "asia"
    if any(token in raw for token in ("israel", "iran", "turk", "arab", "saudi", "syria", "yemen", "qatar", "uae", "palest")):
        return "middle_east"
    if any(token in raw for token in ("fr", "es", "it", "de", "uk", "eu", "pt", "germany", "france", "spain", "italy", "europe")):
        return "europe"
    if any(token in raw for token in ("au", "nz", "canada", "cbc", "globeandmail", "smh", "theage", "abc.net.au", "rnz")):
        return "asia_pacific"
    if any(token in raw for token in ("latimes", "cbs", "nbc", "npr", "foxnews", "newsweek", "newyorker", "us", "america")):
        return "north_america"
    if any(token in raw for token in ("argentina", "ve", "cu", "pe", "cl", "br", "mx", "lahora", "lanacion", "perfil")):
        return "latin_america"
    if any(token in raw for token in ("africa", "sudan", "somalia", "mauritania", "algeria")):
        return "africa"
    return "global"


def fallback_row(site_id: str, url: str) -> dict[str, str]:
    base = classify(site_id, url)
    source_type = choose_source_type(site_id, base.domain, base.reason, base.tier)

    if base.tier == "D":
        priority = "Drop"
        action = "drop"
    elif source_type == "think_tank_context":
        priority = "P3"
        action = "context_only"
    elif base.tier == "A":
        priority = "P1"
        action = "secondary_crawl"
    else:
        priority = "P2"
        action = "selective_crawl"

    notes = base.reason
    if source_type == "regional_media_candidate":
        notes = "possible news publisher; needs manual review before large-scale crawl"

    row = {
        "site_id": site_id,
        "url": url,
        "domain": base.domain,
        "priority_tier": priority,
        "quality_tier": base.tier,
        "source_type": source_type,
        "region": choose_region(site_id, base.domain),
        "action": action,
        "notes": notes,
        "classification_basis": "heuristic",
    }
    if site_id in MANUAL_OVERRIDES:
        row.update(MANUAL_OVERRIDES[site_id])
        row["classification_basis"] = "manual_override"
    return row


def build_full_catalog() -> list[dict[str, str]]:
    raw_rows = read_tsv(RAW_PATH)
    recommended_rows = read_csv(RECOMMENDED_PATH)
    recommended_by_site = {row["site_id"]: row for row in recommended_rows}

    full_rows: list[dict[str, str]] = []
    for row in raw_rows:
        site_id = row["site_id"]
        url = row["url"]
        if site_id in recommended_by_site:
            merged = dict(recommended_by_site[site_id])
            merged["classification_basis"] = "manual_or_curated"
            full_rows.append(merged)
        else:
            full_rows.append(fallback_row(site_id, url))
    return full_rows


def write_full_catalog(rows: list[dict[str, str]]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_summary(rows: list[dict[str, str]]) -> None:
    total = len(rows)
    priority_counts = Counter(row["priority_tier"] for row in rows)
    quality_counts = Counter(row["quality_tier"] for row in rows)
    type_counts = Counter(row["source_type"] for row in rows)
    basis_counts = Counter(row["classification_basis"] for row in rows)
    action_counts = Counter(row["action"] for row in rows)

    lines = [
        "# Source Full Classification Report",
        "",
        "日期：2026-06-21",
        "",
        "## 输入",
        "",
        f"- 原始站点总数：`{total}`",
        f"- 原始清单：[raw_sources.tsv](/root/data/globemind/data/source_curation/raw_sources.tsv)",
        f"- 全量分类结果：[full_source_catalog.csv](/root/data/globemind/data/source_curation/full_source_catalog.csv)",
        "",
        "## 分类基础",
        "",
        f"- 人工/已整理覆盖：`{basis_counts.get('manual_or_curated', 0)}`",
        f"- 规则补齐：`{basis_counts.get('heuristic', 0)}`",
        "",
        "## 优先级统计",
        "",
    ]

    for key in ("P0", "P1", "P2", "P3", "Drop"):
        lines.append(f"- `{key}`：`{priority_counts.get(key, 0)}`")

    lines.extend([
        "",
        "## 质量统计",
        "",
    ])
    for key in ("A", "B", "C", "D"):
        lines.append(f"- `{key}`：`{quality_counts.get(key, 0)}`")

    lines.extend([
        "",
        "## 动作统计",
        "",
    ])
    for key in ("primary_crawl", "secondary_crawl", "selective_crawl", "context_only", "drop"):
        lines.append(f"- `{key}`：`{action_counts.get(key, 0)}`")

    lines.extend([
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
        "- `P0/P1` 可视为新闻主库候选。",
        "- `P2` 主要用于区域、语种、国家视角补充。",
        "- `P3` 不进入新闻主库，只作分析参考。",
        "- `Drop` 为明确不适合进入新闻正文库的站点。",
    ])

    SUMMARY_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    rows = build_full_catalog()
    write_full_catalog(rows)
    write_summary(rows)
    print(f"wrote {len(rows)} rows to {OUTPUT_PATH}")
    print(f"summary {SUMMARY_PATH}")


if __name__ == "__main__":
    main()
