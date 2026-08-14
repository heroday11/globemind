from __future__ import annotations

import csv
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILE = PROJECT_ROOT / "data" / "source_curation" / "seed_whitelist_high_value.csv"
OUTPUT_DIR = PROJECT_ROOT / "data" / "source_curation"

ASIA_PRIORITY_DOMAINS = {
    "aljazeera.net",
    "asahi.com",
    "bernama.com",
    "channelnewsasia.com",
    "dawnnews.tv",
    "freemalaysiatoday.com",
    "geo.tv",
    "hindustantimes.com",
    "inquirer.net",
    "irna.ir",
    "jpost.com",
    "kyodo.co.jp",
    "manilatimes.net",
    "nikkei.com",
    "phnompenhpost.com",
    "prothomalo.com",
    "scmp.com",
    "straitstimes.com",
    "thejakartapost.com",
    "timesofindia.indiatimes.com",
    "todayonline.com",
    "unb.com.bd",
    "vnexpress.net",
    "wafa.ps",
    "ynetnews.com",
    "yomiuri.co.jp",
    "zaobao.com",
}


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    rows = load_rows(SOURCE_FILE)

    official = [row for row in rows if row["source_type"] == "official"]
    global_major = [row for row in rows if row["source_type"] in {"major_media", "national_media"}]
    asia_priority = [row for row in global_major if row["domain"] in ASIA_PRIORITY_DOMAINS]

    write_rows(OUTPUT_DIR / "official_sources.csv", official)
    write_rows(OUTPUT_DIR / "global_major_media.csv", global_major)
    write_rows(OUTPUT_DIR / "asia_priority_media.csv", asia_priority)

    print(f"official={len(official)}")
    print(f"global_major={len(global_major)}")
    print(f"asia_priority={len(asia_priority)}")


if __name__ == "__main__":
    main()
