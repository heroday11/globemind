from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from news_date_cleaning import clean_published_at, date_from_url, parse_datetime


def test_parse_spanish_month_name():
    dt = parse_datetime("12 de mayo de 2026")
    assert dt is not None
    assert dt.date().isoformat() == "2026-05-12"


def test_reject_ancient_extracted_date_and_use_lastmod():
    result = clean_published_at(
        {
            "url": "https://www.eeas.europa.eu/delegations/paraguay/mujeres-en-ciberseguridad-e-ia-her-cybertracks-2026_en",
            "lastmod": "2026-04-01T22:05:44+02:00",
            "published_at": "0006-04-19T12:00:00+00:00",
            "title": "Mujeres en Ciberseguridad e IA - Her CyberTracks 2026",
            "body": "Plazo de postulación: 19 de abril de 2026",
            "fetched_at": "2026-06-21T04:15:22+00:00",
        }
    )
    assert result.published_at is not None
    assert result.published_at.date().isoformat() == "2026-04-01"
    assert result.source == "lastmod"


def test_use_lastmod_when_extracted_date_is_future_month_swap():
    result = clean_published_at(
        {
            "url": "https://www.elespectador.com/economia/nequi-lanzo-campana-que-premiara-con-hasta-cop-5-millones-a-sus-usuarios/",
            "lastmod": "2026-05-12T17:30:00.533000+00:00",
            "published_at": "2026-12-05T12:30:00-05:00",
            "title": "Nequi lanzó campaña que premiará con hasta COP 5 millones a sus usuarios",
            "body": "La billetera digital explica que se entregarán premios.",
            "fetched_at": "2026-06-21T05:10:37+00:00",
        }
    )
    assert result.published_at is not None
    assert result.published_at.date().isoformat() == "2026-05-12"
    assert result.source == "lastmod"


def test_body_byline_beats_sitemap_lastmod():
    result = clean_published_at(
        {
            "url": "https://www.gob.mx/conade/articulos/rosa-guerrero-logra-lanzamiento-de-bronce-en-los-juegos-paralimpicos-376272",
            "lastmod": "2026-06-20T02:44:37-06:00",
            "published_at": "2026-06-20T00:00:00+00:00",
            "title": "Rosa Guerrero logra lanzamiento de bronce en los Juegos Paralímpicos",
            "body": "Comisión Nacional de Cultura Física y Deporte | 30 de agosto de 2024\nFotos: Jesús Zurita Salazar",
            "fetched_at": "2026-06-20T23:19:58+00:00",
        }
    )
    assert result.published_at is not None
    assert result.published_at.date().isoformat() == "2024-08-30"
    assert result.source == "body_byline"


def test_url_date_beats_bad_future_extracted_date():
    result = clean_published_at(
        {
            "url": "https://www.rtve.es/deportes/20260312/juegos-paralimpicos-invierno-milano-cortina-2026-directo-espana-resumen-resultados-12-marzo/16975508.shtml",
            "lastmod": "2026-03-12T15:19:19+01:00",
            "published_at": "2026-12-03T08:52:33+00:00",
            "title": "Juegos Paralímpicos de Invierno 2026 hoy, en directo | 12 de marzo",
            "body": "Resumen de resultados.",
            "fetched_at": "2026-06-21T05:10:37+00:00",
        }
    )
    assert result.published_at is not None
    assert result.published_at.date().isoformat() == "2026-03-12"
    assert result.source == "url"


def test_invalid_timezone_offset_is_normalized():
    dt = parse_datetime("2026-04-17T00:00:00+20:00")
    assert dt == datetime(2026, 4, 17, 0, 0, tzinfo=timezone.utc)


def test_csmonitor_legacy_url_date_is_preserved():
    dt = date_from_url("https://www.csmonitor.com/1980/0102/010249.html")
    assert dt is not None
    assert dt.date().isoformat() == "1980-01-02"
