from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data" / "source_curation" / "raw_sources.tsv"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "source_curation" / "curated_sources.csv"
DEFAULT_WHITELIST = PROJECT_ROOT / "data" / "source_curation" / "clean_whitelist.csv"


EXCLUDED_DOMAINS = {
    "dropbox.com",
    "dictionary.cambridge.org",
    "wiktionary.org",
    "wikisource.org",
    "wordreference.com",
    "fc2.com",
    "trendyol.com",
}

EXCLUDED_KEYWORDS = (
    "dictionary",
    "wiktionary",
    "wikisource",
    "forum",
    "blogspot",
    "wordpress",
    "travel",
    "tourism",
    "shopping",
    "sports",
    "sport",
    "entertainment",
    "celebrit",
    "fashion",
    "lifestyle",
    "poem",
    "poesie",
    "encycloped",
    "video",
    "refurbished",
)

OFFICIAL_DOMAIN_HINTS = (
    ".gov.",
    ".gov/",
    ".gob.",
    ".go.",
    ".gc.ca",
    "bundesregierung.de",
    "un.org",
    "who.int",
)

THINK_TANK_HINTS = (
    "brookings",
    "csis",
    "rand.org",
    "wilsoncenter",
    "heritage.org",
    "hoover.org",
    "hudson.org",
    "lowyinstitute",
    "carnegie",
    "stimson.org",
    "sipri.org",
    "brookings.edu",
    "bruegel.org",
    "piie.com",
    "ifri.org",
    "iisd.org",
)

HIGH_VALUE_MEDIA = {
    "aljazeera.net",
    "ansa.it",
    "asahi.com",
    "bbc.com",
    "bbc.co.uk",
    "bernama.com",
    "cbc.ca",
    "cbsnews.com",
    "channelnewsasia.com",
    "csmonitor.com",
    "dawnnews.tv",
    "dpa.com",
    "dw.com",
    "efe.com",
    "elmundo.es",
    "elpais.com",
    "faz.net",
    "foreignaffairs.org",
    "foxnews.com",
    "freemalaysiatoday.com",
    "geo.tv",
    "haaretz.com",
    "hindustantimes.com",
    "hurriyetdailynews.com",
    "interfax.com",
    "inquirer.net",
    "irna.ir",
    "israelhayom.co.il",
    "jpost.com",
    "kyodo.co.jp",
    "lanacion.com.ar",
    "latimes.com",
    "lefigaro.fr",
    "manilatimes.net",
    "nbcnews.com",
    "newsweek.com",
    "nikkei.com",
    "npr.org",
    "nzherald.co.nz",
    "phnompenhpost.com",
    "prothomalo.com",
    "publico.pt",
    "rnz.co.nz",
    "rtve.es",
    "scmp.com",
    "smh.com.au",
    "straitstimes.com",
    "tass.com",
    "theage.com.au",
    "theglobeandmail.com",
    "theguardian.com",
    "thejakartapost.com",
    "thestar.com",
    "thethaiger.com",
    "timesofindia.indiatimes.com",
    "todayonline.com",
    "unb.com.bd",
    "voaindonesia.com",
    "vnexpress.net",
    "wafa.ps",
    "ynetnews.com",
    "yomiuri.co.jp",
    "zaobao.com",
}

NATIONAL_MAJOR_HINTS = {
    "abc.es",
    "abc.net.au",
    "aajtak.intoday.in",
    "amarujala.com",
    "arynews.tv",
    "bangkokbiznews.com",
    "bhaskar.com",
    "bisnis.com",
    "bolnews.com",
    "businesstimes.com.sg",
    "chinatimes.com",
    "dailyexpress.com.my",
    "dailynews.co.th",
    "detik.com",
    "detiknews.com",
    "dn.pt",
    "echoroukonline.com",
    "ekushey-tv.com",
    "el-nacional.com",
    "express.pk",
    "gatra.com",
    "haberler.com",
    "haberturk.com",
    "jagran.com",
    "jugantor.com",
    "kompas.com",
    "liputan6.com",
    "nationthailand.com",
    "okezone.com",
    "posttoday.com",
    "prachachat.net",
    "republika.co.id",
    "sindonews.com",
    "thanhnien.vn",
    "thairath.co.th",
    "thanhnien.vn",
    "theedgemalaysia.com",
    "thestar.com.my",
    "tuoitre.vn",
    "vietnamnet.vn",
    "vov.vn",
}

LOCAL_NEWS_HINTS = (
    "gazete",
    "gazetesi",
    "haber",
    "postasi",
    "ekspres",
    "olay",
    "gundem",
    "yerel",
    "tribune",
    "times",
    "daily",
    "post",
)


@dataclass
class SourceRecord:
    site_id: str
    url: str
    domain: str
    decision: str
    tier: str
    source_type: str
    reason: str


def normalize_domain(url: str) -> str:
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower().strip()
    if host.startswith("www."):
        host = host[4:]
    return host


def contains_any(text: str, values: Iterable[str]) -> bool:
    return any(value in text for value in values)


def classify(site_id: str, url: str) -> SourceRecord:
    domain = normalize_domain(url)
    raw = f"{site_id.lower()} {domain} {url.lower()}"

    if not domain:
        return SourceRecord(site_id, url, domain, "drop", "D", "invalid", "missing domain")

    if domain in EXCLUDED_DOMAINS or contains_any(raw, EXCLUDED_KEYWORDS):
        return SourceRecord(site_id, url, domain, "drop", "D", "noise", "non-news, utility, forum, lifestyle, or commerce source")

    if contains_any(raw, OFFICIAL_DOMAIN_HINTS):
        return SourceRecord(site_id, url, domain, "keep", "A", "official", "official government or international organization source")

    if domain in HIGH_VALUE_MEDIA:
        return SourceRecord(site_id, url, domain, "keep", "A", "major_media", "high-value global or major national news source")

    if contains_any(raw, THINK_TANK_HINTS):
        return SourceRecord(site_id, url, domain, "review", "C", "think_tank", "analysis source; useful for context, not raw news backbone")

    if domain in NATIONAL_MAJOR_HINTS:
        return SourceRecord(site_id, url, domain, "keep", "B", "national_media", "useful national mainstream media source")

    if contains_any(raw, LOCAL_NEWS_HINTS):
        return SourceRecord(site_id, url, domain, "review", "C", "regional_media", "likely local or regional publisher; keep only if language or geography coverage is needed")

    return SourceRecord(site_id, url, domain, "review", "C", "unknown", "unclear source value; manual review recommended")


def read_rows(path: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t")
        reader = csv.DictReader(handle, dialect=dialect)
        for row in reader:
            site_id = (row.get("site_id") or "").strip()
            url = (row.get("url") or "").strip()
            if not site_id or not url:
                continue
            rows.append((site_id, url))
    return rows


def write_curated(path: Path, rows: list[SourceRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["site_id", "url", "domain", "decision", "tier", "source_type", "reason"])
        for row in rows:
            writer.writerow(
                [row.site_id, row.url, row.domain, row.decision, row.tier, row.source_type, row.reason]
            )


def write_whitelist(path: Path, rows: list[SourceRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["site_id", "url", "domain", "tier", "source_type"])
        for row in rows:
            if row.decision != "keep":
                continue
            writer.writerow([row.site_id, row.url, row.domain, row.tier, row.source_type])


def main() -> None:
    parser = argparse.ArgumentParser(description="Curate mixed source lists into keep/review/drop buckets.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--whitelist-output", type=Path, default=DEFAULT_WHITELIST)
    args = parser.parse_args()

    rows = [classify(site_id, url) for site_id, url in read_rows(args.input)]
    write_curated(args.output, rows)
    write_whitelist(args.whitelist_output, rows)

    counts: dict[str, int] = {"A": 0, "B": 0, "C": 0, "D": 0}
    for row in rows:
        counts[row.tier] = counts.get(row.tier, 0) + 1

    print(f"input={args.input}")
    print(f"curated={args.output}")
    print(f"whitelist={args.whitelist_output}")
    print(f"counts={counts}")


if __name__ == "__main__":
    main()
