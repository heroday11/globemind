#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2

from db_runtime_config import require_database_password


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE_MAP = PROJECT_ROOT / "data" / "source_curation" / "historical_wave1_targets.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "source_curation" / "media_source_profile.csv"

REGION_CODE_MAP = {
    "africa": "AF",
    "asia": "AS",
    "asia_pacific": "AP",
    "europe": "EU",
    "global": "GL",
    "latin_america": "LA",
    "middle_east": "ME",
    "north_america": "NA",
    "south_asia": "SA",
}

PROFILE_COLUMNS = [
    "domain",
    "site_id",
    "source_name",
    "country",
    "region",
    "region_code",
    "source_type",
    "layer",
    "priority_tier",
    "ownership_type",
    "geo_alignment",
    "political_leaning",
    "credibility_tier",
    "label_confidence",
    "evidence_url",
    "evidence_note",
    "review_status",
    "article_count_snapshot",
    "profile_version",
    "updated_at",
]

STRUCTURAL_OWNERSHIP = {
    "executive_government": "government",
    "foreign_ministry": "government",
    "foreign_service": "government",
    "official_government": "government",
    "official_io": "intergovernmental",
    "international_organization": "intergovernmental",
    "international_security_org": "intergovernmental",
    "supranational_executive": "intergovernmental",
    "public_broadcaster": "public",
    "state_media": "state",
    "wire_service": "wire_service",
}

STRUCTURAL_CONFIDENCE = {
    "executive_government": "high",
    "foreign_ministry": "high",
    "foreign_service": "high",
    "official_government": "high",
    "official_io": "high",
    "international_organization": "high",
    "international_security_org": "high",
    "supranational_executive": "high",
    "public_broadcaster": "medium",
    "state_media": "medium",
    "wire_service": "medium",
}

OWNERSHIP_OVERRIDES = {
    "eeas_europa": "intergovernmental",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a seed media_source_profile catalog from current DB domains and source curation CSV."
    )
    parser.add_argument("--source-map", type=Path, default=DEFAULT_SOURCE_MAP)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--host", default="192.168.207.171")
    parser.add_argument("--port", type=int, default=54333)
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--dbname", default="news")
    parser.add_argument("--write-db", action="store_true", help="Create/upsert public.media_source_profile in PostgreSQL.")
    parser.add_argument(
        "--include-source-map-only",
        action="store_true",
        help="Also include source-map domains not currently present in public.media_source.",
    )
    return parser.parse_args()


def normalize_region_code(region: str) -> str:
    key = (region or "").strip().lower()
    if not key:
        return ""
    return REGION_CODE_MAP.get(key, key[:8].upper())


SOURCE_NAME_OVERRIDES = {
    "abc_es": "ABC",
    "abc_net_au": "ABC News Australia",
    "afp_global": "AFP",
    "africanews": "Africanews",
    "aljazeera_net": "Al Jazeera",
    "ansa_it": "ANSA",
    "ap_world": "AP News",
    "arab_news": "Arab News",
    "arynews_tv": "ARY News",
    "asahi_com": "The Asahi Shimbun",
    "asiaone_com": "AsiaOne",
    "bangkok_post": "Bangkok Post",
    "bangkokbiznews_com": "Bangkok Biz News",
    "bbc_com": "BBC",
    "bisnis_com": "Bisnis",
    "bloomberg_politics": "Bloomberg",
    "bundesregierung_de": "German Federal Government",
    "business_times_sg": "The Business Times",
    "businesstimes_com_sg": "The Business Times",
    "cbsnews_com": "CBS News",
    "channelnewsasia_com": "CNA",
    "chile_minrel": "Chile Foreign Ministry",
    "clarin_mundo": "Clarin",
    "csmonitor_com": "The Christian Science Monitor",
    "daily_star_bd": "The Daily Star Bangladesh",
    "dailymaverick": "Daily Maverick",
    "dawnnews_tv": "Dawn News",
    "detik_com": "Detik",
    "dn_pt": "Diario de Noticias",
    "dpa_com": "dpa",
    "dw_com": "DW",
    "dw_english": "DW",
    "ecowas_news": "ECOWAS",
    "eeas_europa": "European External Action Service",
    "efe_com": "EFE",
    "el_comercio_pe": "El Comercio",
    "el_espectador_politica": "El Espectador",
    "el_tiempo_mundo": "El Tiempo",
    "el_universal_mx": "El Universal",
    "eu_commission_press": "European Commission",
    "express_pk": "The Express Tribune",
    "faz_net": "FAZ",
    "focus_taiwan": "Focus Taiwan",
    "folha_mundo": "Folha de S.Paulo",
    "france24_english": "France 24",
    "france_diplomatie": "France Diplomatie",
    "freemalaysiatoday_com": "Free Malaysia Today",
    "geo_tv": "Geo News",
    "gulf_news_world": "Gulf News",
    "haaretz_com": "Haaretz",
    "haberturk_com": "Haberturk",
    "hindustantimes_com": "Hindustan Times",
    "hurriyetdailynews_com": "Hurriyet Daily News",
    "indian_express_world": "The Indian Express",
    "irna_ir": "IRNA",
    "jpost_com": "The Jerusalem Post",
    "kompas_com": "Kompas",
    "korea_herald": "The Korea Herald",
    "kyiv_independent": "The Kyiv Independent",
    "kyodo_co_jp": "Kyodo News",
    "lanacion_com_ar": "La Nacion",
    "latercera_cl": "La Tercera",
    "lefigaro_fr": "Le Figaro",
    "liputan6_com": "Liputan6",
    "mexico_sre": "Mexico Foreign Ministry",
    "nato_news": "NATO",
    "nationthailand_com": "The Nation Thailand",
    "nbcnews_com": "NBC News",
    "news24_politics": "News24",
    "news_thaipbs_or_th": "Thai PBS",
    "nikkei_com": "Nikkei",
    "nzherald_co_nz": "NZ Herald",
    "nyt_com": "The New York Times",
    "philstar_headlines": "The Philippine Star",
    "phnompenhpost_com": "The Phnom Penh Post",
    "premium_times": "Premium Times",
    "prothomalo_com": "Prothom Alo",
    "publico_pt": "Publico",
    "reuters_world": "Reuters",
    "rnz_co_nz": "RNZ",
    "russia_mfa": "Russia Foreign Ministry",
    "rtve_es": "RTVE",
    "scmp_com": "SCMP",
    "smh_com_au": "The Sydney Morning Herald",
    "straitstimes_com": "The Straits Times",
    "tass_com": "TASS",
    "thanhnien_vn": "Thanh Nien",
    "the_hindu_world": "The Hindu",
    "the_national_uae": "The National",
    "theage_com_au": "The Age",
    "theguardian_com": "The Guardian",
    "theglobeandmail_com": "The Globe and Mail",
    "thejakartapost_com": "The Jakarta Post",
    "thestar_com": "Toronto Star",
    "thestar_com_my": "The Star Malaysia",
    "thenationalnews_com": "The National",
    "timesofindia_indiatimes_com": "Times of India",
    "tuoitre_vn": "Tuoi Tre",
    "ukrinform": "Ukrinform",
    "vietnamnet_vn": "VietNamNet",
    "wafa_ps": "WAFA",
    "who_int": "WHO",
    "yomiuri_co_jp": "The Yomiuri Shimbun",
    "yonhap_english": "Yonhap News Agency",
    "zaobao_com": "Lianhe Zaobao",
}

COUNTRY_OVERRIDES = {
    "abc_es": "Spain",
    "abc_net_au": "Australia",
    "afp_global": "France",
    "africanews": "International",
    "aljazeera_net": "Qatar",
    "ansa_it": "Italy",
    "ap_world": "United States",
    "arab_news": "Saudi Arabia",
    "arynews_tv": "Pakistan",
    "asahi_com": "Japan",
    "asiaone_com": "Singapore",
    "bangkok_post": "Thailand",
    "bangkokbiznews_com": "Thailand",
    "bbc_com": "United Kingdom",
    "bisnis_com": "Indonesia",
    "bloomberg_politics": "United States",
    "bundesregierung_de": "Germany",
    "business_times_sg": "Singapore",
    "businesstimes_com_sg": "Singapore",
    "cbsnews_com": "United States",
    "channelnewsasia_com": "Singapore",
    "chile_minrel": "Chile",
    "clarin_mundo": "Argentina",
    "csmonitor_com": "United States",
    "daily_star_bd": "Bangladesh",
    "dailymaverick": "South Africa",
    "dawnnews_tv": "Pakistan",
    "detik_com": "Indonesia",
    "dn_pt": "Portugal",
    "dpa_com": "Germany",
    "dw_com": "Germany",
    "dw_english": "Germany",
    "ecowas_news": "International",
    "eeas_europa": "European Union",
    "efe_com": "Spain",
    "el_comercio_pe": "Peru",
    "el_espectador_politica": "Colombia",
    "el_tiempo_mundo": "Colombia",
    "el_universal_mx": "Mexico",
    "eu_commission_press": "European Union",
    "express_pk": "Pakistan",
    "faz_net": "Germany",
    "focus_taiwan": "Taiwan",
    "folha_mundo": "Brazil",
    "france24_english": "France",
    "france_diplomatie": "France",
    "freemalaysiatoday_com": "Malaysia",
    "geo_tv": "Pakistan",
    "gulf_news_world": "United Arab Emirates",
    "haaretz_com": "Israel",
    "haberturk_com": "Turkey",
    "hindustantimes_com": "India",
    "hurriyetdailynews_com": "Turkey",
    "indian_express_world": "India",
    "irna_ir": "Iran",
    "jpost_com": "Israel",
    "kompas_com": "Indonesia",
    "korea_herald": "South Korea",
    "kyiv_independent": "Ukraine",
    "kyodo_co_jp": "Japan",
    "lanacion_com_ar": "Argentina",
    "latercera_cl": "Chile",
    "lefigaro_fr": "France",
    "liputan6_com": "Indonesia",
    "mexico_sre": "Mexico",
    "nato_news": "International",
    "nationthailand_com": "Thailand",
    "nbcnews_com": "United States",
    "news24_politics": "South Africa",
    "news_thaipbs_or_th": "Thailand",
    "nikkei_com": "Japan",
    "nzherald_co_nz": "New Zealand",
    "nyt_com": "United States",
    "philstar_headlines": "Philippines",
    "phnompenhpost_com": "Cambodia",
    "premium_times": "Nigeria",
    "prothomalo_com": "Bangladesh",
    "publico_pt": "Portugal",
    "reuters_world": "United Kingdom",
    "rnz_co_nz": "New Zealand",
    "russia_mfa": "Russia",
    "rtve_es": "Spain",
    "scmp_com": "Hong Kong",
    "smh_com_au": "Australia",
    "straitstimes_com": "Singapore",
    "tass_com": "Russia",
    "thanhnien_vn": "Vietnam",
    "the_hindu_world": "India",
    "the_national_uae": "United Arab Emirates",
    "theage_com_au": "Australia",
    "theguardian_com": "United Kingdom",
    "theglobeandmail_com": "Canada",
    "thejakartapost_com": "Indonesia",
    "thestar_com": "Canada",
    "thestar_com_my": "Malaysia",
    "thenationalnews_com": "United Arab Emirates",
    "timesofindia_indiatimes_com": "India",
    "tuoitre_vn": "Vietnam",
    "ukrinform": "Ukraine",
    "vietnamnet_vn": "Vietnam",
    "wafa_ps": "Palestine",
    "who_int": "International",
    "yomiuri_co_jp": "Japan",
    "yonhap_english": "South Korea",
    "zaobao_com": "Singapore",
}


def resolve_country(site_id: str) -> str:
    return COUNTRY_OVERRIDES.get((site_id or "").strip().lower(), "")


def humanize_site_id(site_id: str) -> str:
    cleaned = (site_id or "").strip().lower()
    if not cleaned:
        return ""
    if cleaned in SOURCE_NAME_OVERRIDES:
        return SOURCE_NAME_OVERRIDES[cleaned]
    suffixes = [
        "_com_sg",
        "_co_nz",
        "_co_uk",
        "_com_au",
        "_com_ar",
        "_com_br",
        "_com_mx",
        "_com_pk",
        "_com_tr",
        "_com_vn",
        "_com",
        "_net",
        "_org",
        "_gov",
        "_int",
        "_tv",
    ]
    for suffix in suffixes:
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
            break
    return cleaned.replace("_", " ").title()


def humanize_domain(domain: str, site_id: str = "") -> str:
    from_site_id = humanize_site_id(site_id)
    if from_site_id:
        return from_site_id
    root = domain.split("/")[0].lower().strip()
    parts = root.split(".")
    if len(parts) >= 3 and parts[-2] in {"com", "co", "org", "net", "gov"}:
        label = parts[-3]
    elif len(parts) >= 2:
        label = parts[-2]
    elif parts:
        label = parts[0]
    else:
        label = root
    special = {
        "bbc": "BBC",
        "dw": "DW",
        "apnews": "AP News",
        "reuters": "Reuters",
        "tass": "TASS",
        "irna": "IRNA",
        "scmp": "SCMP",
        "rnz": "RNZ",
    }
    return special.get(label, label.replace("-", " ").replace("_", " ").title())


def load_source_map(path: Path) -> dict[str, dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    by_domain: dict[str, dict[str, str]] = {}
    for row in rows:
        domain = (row.get("domain") or "").strip().lower()
        if domain and domain not in by_domain:
            by_domain[domain] = row
    return by_domain


def fetch_current_sources(args: argparse.Namespace) -> dict[str, dict[str, Any]]:
    conn = psycopg2.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=require_database_password(),
        dbname=args.dbname,
        connect_timeout=15,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ms.domain, ms.region_code, COUNT(n.id)::int AS article_count
                FROM public.media_source ms
                LEFT JOIN public.news n ON n.media_source_id = ms.id
                WHERE ms.domain IS NOT NULL AND btrim(ms.domain) <> ''
                GROUP BY ms.domain, ms.region_code
                ORDER BY ms.domain
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    return {
        str(domain).strip().lower(): {
            "domain": str(domain).strip().lower(),
            "region_code": region_code or "",
            "article_count_snapshot": int(article_count or 0),
        }
        for domain, region_code, article_count in rows
    }


def build_rows(
    current_sources: dict[str, dict[str, Any]],
    source_map: dict[str, dict[str, str]],
    *,
    include_source_map_only: bool,
) -> list[dict[str, Any]]:
    domains = set(current_sources)
    if include_source_map_only:
        domains.update(source_map)

    now = datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []
    for domain in sorted(domains):
        src = source_map.get(domain, {})
        db_row = current_sources.get(domain, {})
        site_id = (src.get("site_id") or "").strip()
        source_type = (src.get("source_type") or "").strip() or "unknown"
        region = (src.get("region") or "").strip()
        region_code = (db_row.get("region_code") or normalize_region_code(region)).strip()
        ownership_type = OWNERSHIP_OVERRIDES.get(site_id, STRUCTURAL_OWNERSHIP.get(source_type, "unknown"))
        confidence = STRUCTURAL_CONFIDENCE.get(source_type, "low")
        evidence_note = "seeded_from_historical_wave1_targets"
        if ownership_type != "unknown":
            evidence_note += f"; ownership inferred structurally from source_type={source_type}"

        rows.append(
            {
                "domain": domain,
                "site_id": site_id,
                "source_name": humanize_domain(domain, site_id),
                "country": resolve_country(site_id),
                "region": region,
                "region_code": region_code,
                "source_type": source_type,
                "layer": (src.get("layer") or "").strip(),
                "priority_tier": (src.get("priority_tier") or "").strip(),
                "ownership_type": ownership_type,
                "geo_alignment": "unknown",
                "political_leaning": "unknown",
                "credibility_tier": "unknown",
                "label_confidence": confidence,
                "evidence_url": (src.get("seed_origin") or src.get("url") or "").strip(),
                "evidence_note": evidence_note,
                "review_status": "seeded",
                "article_count_snapshot": int(db_row.get("article_count_snapshot") or 0),
                "profile_version": "media_profile_seed_v1",
                "updated_at": now,
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=PROFILE_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def ensure_profile_table(cur: Any) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS public.media_source_profile (
            domain TEXT PRIMARY KEY,
            site_id TEXT,
            source_name TEXT,
            country TEXT,
            region TEXT,
            region_code TEXT,
            source_type TEXT,
            layer TEXT,
            priority_tier TEXT,
            ownership_type TEXT,
            geo_alignment TEXT,
            political_leaning TEXT,
            credibility_tier TEXT,
            label_confidence TEXT,
            evidence_url TEXT,
            evidence_note TEXT,
            review_status TEXT,
            article_count_snapshot INTEGER NOT NULL DEFAULT 0,
            profile_version TEXT NOT NULL DEFAULT 'media_profile_seed_v1',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def upsert_db(args: argparse.Namespace, rows: list[dict[str, Any]]) -> None:
    conn = psycopg2.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=require_database_password(),
        dbname=args.dbname,
        connect_timeout=15,
    )
    try:
        with conn:
            with conn.cursor() as cur:
                ensure_profile_table(cur)
                for row in rows:
                    cur.execute(
                        """
                        INSERT INTO public.media_source_profile (
                            domain, site_id, source_name, country, region, region_code,
                            source_type, layer, priority_tier, ownership_type,
                            geo_alignment, political_leaning, credibility_tier,
                            label_confidence, evidence_url, evidence_note, review_status,
                            article_count_snapshot, profile_version, updated_at
                        )
                        VALUES (
                            %(domain)s, %(site_id)s, %(source_name)s, %(country)s, %(region)s, %(region_code)s,
                            %(source_type)s, %(layer)s, %(priority_tier)s, %(ownership_type)s,
                            %(geo_alignment)s, %(political_leaning)s, %(credibility_tier)s,
                            %(label_confidence)s, %(evidence_url)s, %(evidence_note)s, %(review_status)s,
                            %(article_count_snapshot)s, %(profile_version)s, %(updated_at)s
                        )
                        ON CONFLICT (domain) DO UPDATE SET
                            site_id = EXCLUDED.site_id,
                            source_name = CASE
                                WHEN public.media_source_profile.review_status IN ('reviewed', 'locked')
                                THEN public.media_source_profile.source_name
                                ELSE EXCLUDED.source_name
                            END,
                            country = CASE
                                WHEN public.media_source_profile.review_status IN ('reviewed', 'locked')
                                THEN public.media_source_profile.country
                                ELSE EXCLUDED.country
                            END,
                            region = EXCLUDED.region,
                            region_code = EXCLUDED.region_code,
                            source_type = EXCLUDED.source_type,
                            layer = EXCLUDED.layer,
                            priority_tier = EXCLUDED.priority_tier,
                            ownership_type = CASE
                                WHEN public.media_source_profile.review_status IN ('reviewed', 'locked')
                                THEN public.media_source_profile.ownership_type
                                ELSE EXCLUDED.ownership_type
                            END,
                            geo_alignment = CASE
                                WHEN public.media_source_profile.review_status IN ('reviewed', 'locked')
                                THEN public.media_source_profile.geo_alignment
                                ELSE EXCLUDED.geo_alignment
                            END,
                            political_leaning = CASE
                                WHEN public.media_source_profile.review_status IN ('reviewed', 'locked')
                                THEN public.media_source_profile.political_leaning
                                ELSE EXCLUDED.political_leaning
                            END,
                            credibility_tier = CASE
                                WHEN public.media_source_profile.review_status IN ('reviewed', 'locked')
                                THEN public.media_source_profile.credibility_tier
                                ELSE EXCLUDED.credibility_tier
                            END,
                            label_confidence = CASE
                                WHEN public.media_source_profile.review_status IN ('reviewed', 'locked')
                                THEN public.media_source_profile.label_confidence
                                ELSE EXCLUDED.label_confidence
                            END,
                            evidence_url = COALESCE(NULLIF(public.media_source_profile.evidence_url, ''), EXCLUDED.evidence_url),
                            evidence_note = COALESCE(NULLIF(public.media_source_profile.evidence_note, ''), EXCLUDED.evidence_note),
                            review_status = CASE
                                WHEN public.media_source_profile.review_status IN ('reviewed', 'locked')
                                THEN public.media_source_profile.review_status
                                ELSE EXCLUDED.review_status
                            END,
                            article_count_snapshot = EXCLUDED.article_count_snapshot,
                            profile_version = EXCLUDED.profile_version,
                            updated_at = EXCLUDED.updated_at
                        """,
                        row,
                    )
    finally:
        conn.close()


def main() -> None:
    args = parse_args()
    source_map = load_source_map(args.source_map)
    current_sources = fetch_current_sources(args)
    rows = build_rows(
        current_sources,
        source_map,
        include_source_map_only=args.include_source_map_only,
    )
    write_csv(args.output, rows)
    print(f"wrote {len(rows)} rows to {args.output}")
    if args.write_db:
        upsert_db(args, rows)
        print(f"upserted {len(rows)} rows into {args.dbname}.public.media_source_profile")


if __name__ == "__main__":
    main()
