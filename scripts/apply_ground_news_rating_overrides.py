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
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "source_curation" / "media_source_profile.csv"

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

# Conservative mapping of Ground News labels into our smaller enum.
# Ground News "Center" -> center; "Lean Left" -> center_left.
# Ground News "High"/"Very High" factuality -> high.
GROUND_NEWS_OVERRIDES = {
    "apnews.com": {
        "political_leaning": "center_left",
        "credibility_tier": "high",
        "ownership_type": "wire_service",
        "geo_alignment": "western",
        "evidence_url": "https://ground.news/interest/associated-press-news",
        "evidence_note": "ground_news_rating_v1: Ground News lists Associated Press News as Lean Left and Very High factuality",
    },
    "bbc.com": {
        "political_leaning": "center",
        "credibility_tier": "high",
        "ownership_type": "public",
        "geo_alignment": "western",
        "evidence_url": "https://ground.news/interest/bbc-news_bf95f4",
        "evidence_note": "ground_news_rating_v1: Ground News lists BBC News as Center and Very High factuality",
    },
    "bloomberg.com": {
        "political_leaning": "center_left",
        "credibility_tier": "high",
        "ownership_type": "private",
        "geo_alignment": "western",
        "evidence_url": "https://ground.news/interest/bloomberg",
        "evidence_note": "ground_news_rating_v1: Ground News lists Bloomberg as Lean Left and Very High factuality",
    },
    "clarin.com": {
        "political_leaning": "center_right",
        "credibility_tier": "high",
        "geo_alignment": "global_south",
        "evidence_url": "https://ground.news/interest/clarin_5fa6ca",
        "evidence_note": "ground_news_rating_v1: Ground News lists Clarin as Lean Right and High factuality",
    },
    "dw.com": {
        "political_leaning": "center",
        "credibility_tier": "high",
        "ownership_type": "public",
        "geo_alignment": "western",
        "evidence_url": "https://ground.news/interest/deutsche-welle",
        "evidence_note": "ground_news_rating_v1: Ground News lists Deutsche Welle as Center and Very High factuality",
    },
    "express.pk": {
        "political_leaning": "center",
        "credibility_tier": "high",
        "ownership_type": "private",
        "geo_alignment": "global_south",
        "evidence_url": "https://ground.news/interest/the-express-tribune",
        "evidence_note": "ground_news_rating_v1: Ground News lists The Express Tribune as Center and High factuality",
    },
    "freemalaysiatoday.com": {
        "political_leaning": "center_right",
        "credibility_tier": "medium",
        "geo_alignment": "global_south",
        "evidence_url": "https://mediabiasfactcheck.com/free-malaysia-today-bias-and-credibility/",
        "evidence_note": "mbfc_rating_v1: Media Bias/Fact Check lists Free Malaysia Today as Right-Center and Mixed factuality",
    },
    "hindustantimes.com": {
        "political_leaning": "center",
        "credibility_tier": "medium",
        "geo_alignment": "global_south",
        "evidence_url": "https://ground.news/interest/hindustan-times",
        "evidence_note": "ground_news_rating_v1: Ground News lists Hindustan Times as Center and Mixed factuality",
    },
    "kyodo.co.jp": {
        "political_leaning": "center",
        "credibility_tier": "high",
        "ownership_type": "wire_service",
        "geo_alignment": "western",
        "evidence_url": "https://ground.news/interest/kyodo-news",
        "evidence_note": "ground_news_rating_v1: Ground News lists Kyodo News+ as Center and High factuality",
    },
    "lanacion.com.ar": {
        "political_leaning": "center_right",
        "credibility_tier": "high",
        "geo_alignment": "global_south",
        "evidence_url": "https://ground.news/interest/la-nacion_7042bc",
        "evidence_note": "ground_news_rating_v1: Ground News lists La Nacion as Lean Right and Very High factuality",
    },
    "rnz.co.nz": {
        "political_leaning": "center",
        "credibility_tier": "high",
        "ownership_type": "public",
        "geo_alignment": "western",
        "evidence_url": "https://ground.news/interest/rnz",
        "evidence_note": "ground_news_rating_v1: Ground News lists RNZ as Center and High factuality",
    },
    "cbsnews.com": {
        "political_leaning": "center",
        "credibility_tier": "high",
        "ownership_type": "private",
        "geo_alignment": "western",
        "evidence_url": "https://ground.news/interest/cbs-news",
        "evidence_note": "ground_news_rating_v1: Ground News lists CBS News as Center and Very High factuality",
    },
    "csmonitor.com": {
        "political_leaning": "center",
        "credibility_tier": "high",
        "geo_alignment": "western",
        "evidence_url": "https://ground.news/interest/the-christian-science-monitor",
        "evidence_note": "ground_news_rating_v1: Ground News lists The Christian Science Monitor as Center and Very High factuality",
    },
    "jpost.com": {
        "political_leaning": "center",
        "credibility_tier": "high",
        "geo_alignment": "middle_east",
        "evidence_url": "https://ground.news/interest/jerusalem-post",
        "evidence_note": "ground_news_rating_v1: Ground News lists Jerusalem Post as Center and High factuality",
    },
    "reuters.com": {
        "political_leaning": "center",
        "credibility_tier": "high",
        "ownership_type": "wire_service",
        "geo_alignment": "western",
        "evidence_url": "https://ground.news/interest/reuters_fa2539",
        "evidence_note": "ground_news_rating_v1: Ground News lists Reuters as Center and Very High factuality",
    },
    "scmp.com": {
        "political_leaning": "center",
        "credibility_tier": "high",
        "geo_alignment": "china",
        "evidence_url": "https://ground.news/interest/south-china-morning-post",
        "evidence_note": "ground_news_rating_v1: Ground News lists South China Morning Post as Center and High factuality",
    },
    "straitstimes.com": {
        "political_leaning": "center_right",
        "credibility_tier": "high",
        "geo_alignment": "global_south",
        "evidence_url": "https://ground.news/interest/the-straits-times_9bb9e6",
        "evidence_note": "ground_news_rating_v1: Ground News lists The Straits Times as Lean Right and High factuality",
    },
    "timesofindia.indiatimes.com": {
        "political_leaning": "center_right",
        "credibility_tier": "medium",
        "geo_alignment": "global_south",
        "evidence_url": "https://ground.news/interest/times-of-india",
        "evidence_note": "ground_news_rating_v1: Ground News lists Times of India as Lean Right and Mixed factuality",
    },
    "abc.es": {
        "political_leaning": "right",
        "credibility_tier": "high",
        "geo_alignment": "western",
        "evidence_url": "https://ground.news/interest/abc_52916d",
        "evidence_note": "ground_news_rating_v1: Ground News lists ABC Spain as Right and High factuality",
    },
    "afp.com": {
        "political_leaning": "center",
        "credibility_tier": "high",
        "ownership_type": "wire_service",
        "geo_alignment": "western",
        "evidence_url": "https://mediabiasfactcheck.com/agence-france-presse-afp/",
        "evidence_note": "mbfc_rating_v1: Media Bias/Fact Check lists AFP as Least Biased and High factuality",
    },
    "ansa.it": {
        "political_leaning": "center",
        "credibility_tier": "high",
        "ownership_type": "wire_service",
        "geo_alignment": "western",
        "evidence_url": "https://ground.news/interest/ansa",
        "evidence_note": "ground_news_rating_v1: Ground News lists ANSA as Center and High factuality",
    },
    "arabnews.com": {
        "political_leaning": "center_right",
        "credibility_tier": "medium",
        "geo_alignment": "middle_east",
        "evidence_url": "https://ground.news/interest/arab-news_02f792",
        "evidence_note": "ground_news_rating_v1: Ground News lists Arab News as Lean Right and Mixed factuality",
    },
    "asahi.com": {
        "political_leaning": "center_left",
        "credibility_tier": "high",
        "geo_alignment": "western",
        "evidence_url": "https://ground.news/interest/asahi",
        "evidence_note": "ground_news_rating_v1: Ground News lists Asahi as Lean Left and High factuality",
    },
    "dailymaverick.co.za": {
        "political_leaning": "center",
        "credibility_tier": "high",
        "geo_alignment": "global_south",
        "evidence_url": "https://ground.news/interest/daily-maverick",
        "evidence_note": "ground_news_rating_v1: Ground News lists Daily Maverick as Center and High factuality",
    },
    "dpa.com": {
        "political_leaning": "center",
        "credibility_tier": "high",
        "ownership_type": "wire_service",
        "geo_alignment": "western",
        "evidence_url": "https://mediabiasfactcheck.com/deutsche-presse-agentur-dpa/",
        "evidence_note": "mbfc_rating_v1: Media Bias/Fact Check lists dpa as Least Biased and High factuality",
    },
    "efe.com": {
        "political_leaning": "center",
        "credibility_tier": "high",
        "ownership_type": "wire_service",
        "geo_alignment": "western",
        "evidence_url": "https://ground.news/interest/efe",
        "evidence_note": "ground_news_rating_v1: Ground News lists EFE as Center and High factuality",
    },
    "en.yna.co.kr": {
        "political_leaning": "center_right",
        "credibility_tier": "high",
        "ownership_type": "wire_service",
        "geo_alignment": "western",
        "evidence_url": "https://ground.news/interest/_ae7d36",
        "evidence_note": "ground_news_rating_v1: Ground News lists Yonhap News Agency as Lean Right and High factuality",
    },
    "faz.net": {
        "political_leaning": "center_right",
        "credibility_tier": "high",
        "geo_alignment": "western",
        "evidence_url": "https://ground.news/interest/frankfurter-allgemeine",
        "evidence_note": "ground_news_rating_v1: Ground News lists Frankfurter Allgemeine Zeitung as Lean Right and High factuality",
    },
    "focustaiwan.tw": {
        "political_leaning": "center",
        "credibility_tier": "high",
        "ownership_type": "wire_service",
        "geo_alignment": "western",
        "evidence_url": "https://ground.news/interest/focus-taiwan",
        "evidence_note": "ground_news_rating_v1: Ground News lists Focus Taiwan as Center and High factuality",
    },
    "france24.com": {
        "political_leaning": "center",
        "credibility_tier": "high",
        "ownership_type": "public",
        "geo_alignment": "western",
        "evidence_url": "https://ground.news/interest/france24",
        "evidence_note": "ground_news_rating_v1: Ground News lists France 24 as Center and High factuality",
    },
    "gulfnews.com": {
        "political_leaning": "center_right",
        "credibility_tier": "medium",
        "geo_alignment": "middle_east",
        "evidence_url": "https://ground.news/interest/gulf-news",
        "evidence_note": "ground_news_rating_v1: Ground News lists Gulf News as Lean Right and Mixed factuality",
    },
    "haaretz.com": {
        "political_leaning": "left",
        "credibility_tier": "high",
        "geo_alignment": "middle_east",
        "evidence_url": "https://ground.news/interest/haaretz",
        "evidence_note": "ground_news_rating_v1: Ground News lists Haaretz as Left and High factuality",
    },
    "hurriyetdailynews.com": {
        "political_leaning": "right",
        "credibility_tier": "medium",
        "geo_alignment": "middle_east",
        "evidence_url": "https://ground.news/interest/hurriyet-daily-news",
        "evidence_note": "ground_news_rating_v1: Ground News lists Hurriyet Daily News as Right and Mixed factuality",
    },
    "koreaherald.com": {
        "political_leaning": "center_right",
        "credibility_tier": "high",
        "geo_alignment": "western",
        "evidence_url": "https://ground.news/interest/the-korea-herald",
        "evidence_note": "ground_news_rating_v1: Ground News lists The Korea Herald as Lean Right and High factuality",
    },
    "kyivindependent.com": {
        "political_leaning": "center_left",
        "credibility_tier": "high",
        "geo_alignment": "western",
        "evidence_url": "https://ground.news/interest/the-kyiv-independent",
        "evidence_note": "ground_news_rating_v1: Ground News lists The Kyiv Independent as Lean Left and High factuality",
    },
    "latercera.com": {
        "political_leaning": "center_right",
        "credibility_tier": "high",
        "geo_alignment": "global_south",
        "evidence_url": "https://ground.news/interest/la-tercera",
        "evidence_note": "ground_news_rating_v1: Ground News lists La Tercera as Lean Right and High factuality",
    },
    "lefigaro.fr": {
        "political_leaning": "center_right",
        "credibility_tier": "high",
        "geo_alignment": "western",
        "evidence_url": "https://ground.news/interest/le-figaro",
        "evidence_note": "ground_news_rating_v1: Ground News lists Le Figaro as Lean Right and High factuality",
    },
    "nbcnews.com": {
        "political_leaning": "center_left",
        "credibility_tier": "high",
        "ownership_type": "private",
        "geo_alignment": "western",
        "evidence_url": "https://ground.news/interest/nbc-news",
        "evidence_note": "ground_news_rating_v1: Ground News lists NBC News as Lean Left and High factuality",
    },
    "news24.com": {
        "political_leaning": "center",
        "credibility_tier": "high",
        "geo_alignment": "global_south",
        "evidence_url": "https://ground.news/interest/news24",
        "evidence_note": "ground_news_rating_v1: Ground News lists News24 as Center and High factuality",
    },
    "nzherald.co.nz": {
        "political_leaning": "center",
        "credibility_tier": "high",
        "geo_alignment": "western",
        "label_confidence": "medium",
        "review_status": "reviewed",
        "replace_evidence_note": True,
        "evidence_url": "https://ground.news/interest/nz-herald",
        "evidence_note": "ground_news_rating_v1: Ground News lists NZ Herald as Center and High factuality; medium confidence due same-name-source ambiguity",
    },
    "premiumtimesng.com": {
        "political_leaning": "center_left",
        "credibility_tier": "high",
        "geo_alignment": "global_south",
        "evidence_url": "https://ground.news/interest/premium-times-nigeria",
        "evidence_note": "ground_news_rating_v1: Ground News lists Premium Times Nigeria as Lean Left and High factuality",
    },
    "rtve.es": {
        "political_leaning": "center_left",
        "credibility_tier": "high",
        "ownership_type": "public",
        "geo_alignment": "western",
        "evidence_url": "https://ground.news/interest/rtve",
        "evidence_note": "ground_news_rating_v1: Ground News lists RTVE as Lean Left and High factuality",
    },
    "smh.com.au": {
        "political_leaning": "center_left",
        "credibility_tier": "high",
        "geo_alignment": "western",
        "evidence_url": "https://ground.news/interest/sydney-morning-herald",
        "evidence_note": "ground_news_rating_v1: Ground News lists The Sydney Morning Herald as Lean Left and High factuality",
    },
    "thenationalnews.com": {
        "political_leaning": "center",
        "credibility_tier": "high",
        "geo_alignment": "middle_east",
        "label_confidence": "medium",
        "review_status": "reviewed",
        "replace_evidence_note": True,
        "evidence_url": "https://ground.news/interest/the-national_7f9810",
        "evidence_note": "ground_news_rating_v1: Ground News page for The National lists Center and High factuality; medium confidence due same-name-source ambiguity",
    },
    "theguardian.com": {
        "political_leaning": "center_left",
        "credibility_tier": "high",
        "geo_alignment": "western",
        "evidence_url": "https://mediabiasfactcheck.com/the-guardian/",
        "evidence_note": "mbfc_rating_v1: Media Bias/Fact Check lists The Guardian as Left-Center and High factuality",
    },
    "thehindu.com": {
        "political_leaning": "center_left",
        "credibility_tier": "high",
        "geo_alignment": "global_south",
        "evidence_url": "https://ground.news/interest/the-hindu",
        "evidence_note": "ground_news_rating_v1: Ground News lists The Hindu as Lean Left and High factuality",
    },
    "thestar.com": {
        "political_leaning": "center_left",
        "credibility_tier": "high",
        "geo_alignment": "western",
        "evidence_url": "https://ground.news/interest/the-toronto-star",
        "evidence_note": "ground_news_rating_v1: Ground News lists Toronto Star as Lean Left and High factuality",
    },
    "ukrinform.net": {
        "political_leaning": "center_right",
        "credibility_tier": "medium",
        "ownership_type": "wire_service",
        "geo_alignment": "western",
        "label_confidence": "medium",
        "review_status": "reviewed",
        "replace_evidence_note": True,
        "evidence_url": "https://mediabiasfactcheck.com/ukrinform-bias-and-credibility/",
        "evidence_note": "mbfc_rating_v1: Media Bias/Fact Check lists Ukrinform as Right-Center and Mixed factuality; medium confidence due government-agency context",
    },
    "yomiuri.co.jp": {
        "political_leaning": "center_right",
        "credibility_tier": "high",
        "geo_alignment": "western",
        "evidence_url": "https://ground.news/interest/_3899b7",
        "evidence_note": "ground_news_rating_v1: Ground News lists Yomiuri Shimbun online as Lean Right and High factuality",
    },
    "africanews.com": {
        "political_leaning": "center_left",
        "credibility_tier": "high",
        "geo_alignment": "global_south",
        "evidence_url": "https://ground.news/interest/africa-news",
        "evidence_note": "ground_news_rating_v1: Ground News lists Africa News as Lean Left and High factuality",
    },
    "aljazeera.net": {
        "political_leaning": "state_aligned",
        "credibility_tier": "medium",
        "ownership_type": "state",
        "geo_alignment": "middle_east",
        "label_confidence": "medium",
        "evidence_url": "https://www.aljazeera.com/about-us",
        "evidence_note": "institutional_override_v1: Al Jazeera says it is funded in part by the Qatari government; state_aligned is institutional, not a left/right rating",
    },
    "bangkokpost.com": {
        "political_leaning": "center_right",
        "credibility_tier": "medium",
        "geo_alignment": "global_south",
        "evidence_url": "https://ground.news/interest/bangkok-post",
        "evidence_note": "ground_news_rating_v1: Ground News lists Bangkok Post as Lean Right and Mixed factuality",
    },
    "channelnewsasia.com": {
        "political_leaning": "center",
        "credibility_tier": "high",
        "geo_alignment": "global_south",
        "evidence_url": "https://ground.news/interest/channel-news-asia",
        "evidence_note": "ground_news_rating_v1: Ground News lists Channel News Asia as Center and Very High factuality",
    },
    "dawnnews.tv": {
        "political_leaning": "center_left",
        "credibility_tier": "medium",
        "geo_alignment": "global_south",
        "label_confidence": "medium",
        "evidence_url": "https://ground.news/interest/dawn_a02aa2",
        "evidence_note": "ground_news_rating_v1: Ground News lists Dawn as Lean Left and High factuality; MBFC lists Dawn mostly factual/medium credibility, so mapped to medium",
    },
    "geo.tv": {
        "political_leaning": "center_right",
        "credibility_tier": "high",
        "geo_alignment": "global_south",
        "evidence_url": "https://ground.news/interest/geo-news",
        "evidence_note": "ground_news_rating_v1: Ground News lists Geo News as Lean Right and High factuality",
    },
    "indianexpress.com": {
        "political_leaning": "center_left",
        "credibility_tier": "medium",
        "geo_alignment": "global_south",
        "label_confidence": "medium",
        "evidence_url": "https://ground.news/interest/indian-express",
        "evidence_note": "ground_news_rating_v1: Ground News lists Indian Express as Lean Left and High factuality; MBFC lists mixed factuality, so mapped conservatively to medium",
    },
    "kompas.com": {
        "political_leaning": "center",
        "credibility_tier": "high",
        "geo_alignment": "global_south",
        "evidence_url": "https://ground.news/interest/kompas",
        "evidence_note": "ground_news_rating_v1: Ground News lists Kompas as Center and High factuality",
    },
    "liputan6.com": {
        "political_leaning": "center_right",
        "credibility_tier": "high",
        "geo_alignment": "global_south",
        "evidence_url": "https://ground.news/interest/liputan6com",
        "evidence_note": "ground_news_rating_v1: Ground News lists liputan6.com as Lean Right and High factuality",
    },
    "nikkei.com": {
        "political_leaning": "center_right",
        "credibility_tier": "high",
        "geo_alignment": "western",
        "evidence_url": "https://mediabiasfactcheck.com/nikkei-asian-review/",
        "evidence_note": "mbfc_rating_v1: Media Bias/Fact Check lists Nikkei Asian Review as Right-Center and High factuality",
    },
    "philstar.com": {
        "political_leaning": "center_left",
        "credibility_tier": "high",
        "geo_alignment": "global_south",
        "evidence_url": "https://ground.news/interest/philstar-global",
        "evidence_note": "ground_news_rating_v1: Ground News lists Philstar Global as Lean Left and Very High factuality",
    },
    "thanhnien.vn": {
        "political_leaning": "state_aligned",
        "credibility_tier": "medium",
        "ownership_type": "party_affiliated",
        "geo_alignment": "global_south",
        "label_confidence": "medium",
        "evidence_url": "https://statemediamonitor.com/2025/07/thanh-nien/",
        "evidence_note": "institutional_override_v1: State Media Monitor describes Thanh Nien as an official mouthpiece tied to Vietnam's party-linked youth organization",
    },
    "theage.com.au": {
        "political_leaning": "center_left",
        "credibility_tier": "high",
        "geo_alignment": "western",
        "evidence_url": "https://ground.news/interest/the-age",
        "evidence_note": "ground_news_rating_v1: Ground News lists The Age as Lean Left and High factuality",
    },
    "theglobeandmail.com": {
        "political_leaning": "center",
        "credibility_tier": "high",
        "geo_alignment": "western",
        "evidence_url": "https://ground.news/interest/the-globe-and-mail",
        "evidence_note": "ground_news_rating_v1: Ground News lists The Globe & Mail as Center and Very High factuality",
    },
    "thejakartapost.com": {
        "political_leaning": "center_left",
        "credibility_tier": "medium",
        "geo_alignment": "global_south",
        "label_confidence": "medium",
        "evidence_url": "https://ground.news/interest/the-jakarta-post",
        "evidence_note": "ground_news_rating_v1: Ground News lists The Jakarta Post as Lean Left and High factuality; MBFC lists mostly factual, so mapped conservatively to medium",
    },
    "thestar.com.my": {
        "political_leaning": "right",
        "credibility_tier": "medium",
        "geo_alignment": "global_south",
        "evidence_url": "https://ground.news/interest/the-star-kuala-lumpur",
        "evidence_note": "ground_news_rating_v1: Ground News lists The Star Kuala Lumpur as Right and Mixed factuality",
    },
    "tuoitre.vn": {
        "political_leaning": "state_aligned",
        "credibility_tier": "medium",
        "ownership_type": "party_affiliated",
        "geo_alignment": "global_south",
        "label_confidence": "medium",
        "evidence_url": "https://statemediamonitor.com/2025/07/tuoi-tre/",
        "evidence_note": "institutional_override_v1: State Media Monitor says Tuoi Tre operates under the Ho Chi Minh City Communist Youth Union",
    },
    "vietnamnet.vn": {
        "political_leaning": "state_aligned",
        "credibility_tier": "medium",
        "ownership_type": "government",
        "geo_alignment": "global_south",
        "label_confidence": "medium",
        "evidence_url": "https://vietnamnet.vn/en/vietnamnet-to-transition-under-the-ministry-of-ethnic-and-religious-affairs-2377357.html",
        "evidence_note": "institutional_override_v1: VietNamNet reports that it was transferred under Vietnam's Ministry of Information and Communications",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply reviewed Ground-News-style media rating overrides.")
    parser.add_argument("--write-db", action="store_true", help="Apply updates. Default is dry-run.")
    parser.add_argument("--export-csv", action="store_true", help="Export media_source_profile.csv from DB after updates.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--host", default="192.168.207.171")
    parser.add_argument("--port", type=int, default=54333)
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--dbname", default="news")
    return parser.parse_args()


def connect(args: argparse.Namespace) -> Any:
    return psycopg2.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=require_database_password(),
        dbname=args.dbname,
        connect_timeout=20,
    )


def fetch_domains(args: argparse.Namespace) -> set[str]:
    conn = connect(args)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT domain FROM public.media_source_profile")
            return {str(row[0]) for row in cur.fetchall()}
    finally:
        conn.close()


def apply_updates(args: argparse.Namespace) -> None:
    conn = connect(args)
    try:
        with conn:
            with conn.cursor() as cur:
                for domain, update in GROUND_NEWS_OVERRIDES.items():
                    cur.execute(
                        """
                        UPDATE public.media_source_profile
                        SET ownership_type = COALESCE(%(ownership_type)s, ownership_type),
                            geo_alignment = %(geo_alignment)s,
                            political_leaning = %(political_leaning)s,
                            credibility_tier = %(credibility_tier)s,
                            label_confidence = %(label_confidence)s,
                            evidence_url = %(evidence_url)s,
                            evidence_note = CASE
                                WHEN %(replace_evidence_note)s THEN %(evidence_note)s
                                WHEN COALESCE(evidence_note, '') = '' THEN %(evidence_note)s
                                WHEN POSITION(%(evidence_note)s IN evidence_note) > 0 THEN evidence_note
                                ELSE evidence_note || '; ' || %(evidence_note)s
                            END,
                            review_status = %(review_status)s,
                            updated_at = %(updated_at)s::timestamptz
                        WHERE domain = %(domain)s
                          AND review_status <> 'locked'
                        """,
                        {
                            "domain": domain,
                            "ownership_type": update.get("ownership_type"),
                            "geo_alignment": update["geo_alignment"],
                            "political_leaning": update["political_leaning"],
                            "credibility_tier": update["credibility_tier"],
                            "label_confidence": update.get("label_confidence", "high"),
                            "review_status": update.get("review_status", "reviewed"),
                            "replace_evidence_note": bool(update.get("replace_evidence_note")),
                            "evidence_url": update["evidence_url"],
                            "evidence_note": update["evidence_note"],
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                    if cur.rowcount != 1:
                        raise RuntimeError(f"domain not updated: {domain}")
    finally:
        conn.close()


def fetch_profile_rows(args: argparse.Namespace) -> list[dict[str, Any]]:
    conn = connect(args)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT domain, site_id, source_name, country, region, region_code,
                       source_type, layer, priority_tier, ownership_type,
                       geo_alignment, political_leaning, credibility_tier,
                       label_confidence, evidence_url, evidence_note, review_status,
                       article_count_snapshot, profile_version, updated_at
                FROM public.media_source_profile
                ORDER BY domain
                """
            )
            columns = [desc[0] for desc in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def export_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PROFILE_COLUMNS)
        writer.writeheader()
        for row in rows:
            out = {column: row.get(column, "") for column in PROFILE_COLUMNS}
            if out.get("updated_at") is not None:
                out["updated_at"] = str(out["updated_at"])
            writer.writerow(out)


def main() -> None:
    args = parse_args()
    domains = fetch_domains(args)
    missing = sorted(set(GROUND_NEWS_OVERRIDES) - domains)
    if missing:
        raise SystemExit(f"missing profile domains: {', '.join(missing)}")

    print(f"External rating overrides: {len(GROUND_NEWS_OVERRIDES)}")
    for domain, update in sorted(GROUND_NEWS_OVERRIDES.items()):
        print(
            domain,
            update["political_leaning"],
            update["credibility_tier"],
            update.get("review_status", "reviewed"),
            update["evidence_url"],
        )
    if args.write_db:
        apply_updates(args)
        print(f"updated DB rows: {len(GROUND_NEWS_OVERRIDES)}")
    else:
        print("dry-run only; pass --write-db to apply")

    if args.export_csv:
        rows = fetch_profile_rows(args)
        export_csv(args.output, rows)
        print(f"exported {len(rows)} rows to {args.output}")


if __name__ == "__main__":
    main()
