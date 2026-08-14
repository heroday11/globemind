"""Live data aggregation for the GlobeMind world-state terminal."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import math
import os
import secrets
import stat
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx
from sqlalchemy import text

from api.core.db import SQLALCHEMY_DATABASE_URL, engine
from api.core.environment import float_setting, int_setting, string_setting
from api.features.financial import (
    apply_dashboard_trust_gate,
    assess_dashboard_trust,
    calculate_extracted_wsi,
    dashboard_is_computable,
)

CACHE_TTL_SECONDS = int_setting("FINANCIAL_TERMINAL_CACHE_TTL", 900)
HTTP_TIMEOUT = float_setting("FINANCIAL_TERMINAL_HTTP_TIMEOUT", 6.0)
SOURCE_TIMEOUT_SECONDS = float_setting("FINANCIAL_TERMINAL_SOURCE_TIMEOUT", 5.0)
GDELT_TIMEOUT_SECONDS = float_setting("FINANCIAL_TERMINAL_GDELT_TIMEOUT", 10.0)
NVD_TIMEOUT_SECONDS = float_setting("FINANCIAL_TERMINAL_NVD_TIMEOUT", 10.0)
XML_SOURCE_TIMEOUT_SECONDS = float_setting("FINANCIAL_TERMINAL_XML_SOURCE_TIMEOUT", 18.0)
WORLD_BANK_TIMEOUT_SECONDS = float_setting("FINANCIAL_TERMINAL_WORLD_BANK_TIMEOUT", 8.0)
LOCAL_STORY_TIMEOUT_SECONDS = float_setting("FINANCIAL_TERMINAL_LOCAL_STORY_TIMEOUT", 4.0)
HISTORY_POINTS = int_setting("FINANCIAL_TERMINAL_HISTORY_POINTS", 96)
CORE_WORLD_BANK_KEYS = {
    "gdp",
    "inflation",
    "unemployment",
    "trade",
    "military",
    "electricity",
    "manufacturing",
    "fdi",
    "usa_gdp",
    "chn_gdp",
    "euu_gdp",
    "ind_gdp",
    "jpn_gdp",
    "deu_gdp",
    "usa_cpi",
    "chn_cpi",
    "ind_cpi",
    "jpn_cpi",
    "deu_cpi",
}
CORE_OPENALEX_KEYS = {"tech", "cyber", "climate", "quantum", "space"}
SHARED_DASHBOARD_CACHE = Path(
    string_setting(
        "FINANCIAL_TERMINAL_SHARED_CACHE",
        "/root/data/web/cache/financial_dashboard.json",
    )
)
_MAX_SHARED_DASHBOARD_CACHE_BYTES = 4_194_304
_MAX_SHARED_DASHBOARD_CACHE_TTL_SECONDS = 86_400
_SHARED_DASHBOARD_CACHE_CLOCK_SKEW_SECONDS = 5
_RELEASE_CACHE_ROOT = Path("/root/data/releases/globemind")

_DASHBOARD_CACHE: Tuple[float, Dict[str, Any]] | None = None
_DASHBOARD_BUILD_TASK: asyncio.Task | None = None
_DASHBOARD_REFRESH_TASK: asyncio.Task | None = None
_SOURCE_CACHE: Dict[str, Tuple[float, Any]] = {}
_METRIC_HISTORY: Dict[str, List[Dict[str, Any]]] = {}
_L1_ENGINE = engine


def _make_l1_database_url():
    return SQLALCHEMY_DATABASE_URL


def _get_l1_engine():
    return _L1_ENGINE


def _cache_path_has_symlink(path: Path) -> bool:
    absolute = path if path.is_absolute() else Path.cwd() / path
    probe = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        probe = probe / part
        try:
            metadata = probe.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            return True
    return False


def _cache_path_is_allowed(path: Path) -> bool:
    absolute = path if path.is_absolute() else Path.cwd() / path
    return not absolute.is_relative_to(_RELEASE_CACHE_ROOT) and not _cache_path_has_symlink(
        absolute
    )


def _reject_duplicate_cache_keys(pairs: list[tuple[str, Any]]) -> Dict[str, Any]:
    output: Dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("duplicate shared dashboard cache key")
        output[key] = value
    return output


def _reject_non_finite_cache_constant(value: str) -> None:
    raise ValueError(f"non-finite shared dashboard cache number: {value}")


def _parse_finite_cache_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite shared dashboard cache number")
    return parsed


def _shared_dashboard_cache_ttl_seconds() -> int:
    return max(
        1,
        min(int(CACHE_TTL_SECONDS), _MAX_SHARED_DASHBOARD_CACHE_TTL_SECONDS),
    )


def _read_shared_dashboard_cache() -> Tuple[float, Dict[str, Any]] | None:
    directory_descriptor = -1
    descriptor = -1
    try:
        path = SHARED_DASHBOARD_CACHE
        if not _cache_path_is_allowed(path):
            return None
        absolute = path if path.is_absolute() else Path.cwd() / path
        parent_before = os.stat(absolute.parent, follow_symlinks=False)
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            directory_flags |= os.O_NOFOLLOW
        directory_descriptor = os.open(absolute.parent, directory_flags)
        parent_opened = os.fstat(directory_descriptor)
        if (
            not stat.S_ISDIR(parent_opened.st_mode)
            or parent_opened.st_dev != parent_before.st_dev
            or parent_opened.st_ino != parent_before.st_ino
        ):
            return None
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(absolute.name, flags, dir_fd=directory_descriptor)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > _MAX_SHARED_DASHBOARD_CACHE_BYTES
        ):
            return None
        encoded = b""
        while len(encoded) <= _MAX_SHARED_DASHBOARD_CACHE_BYTES:
            chunk = os.read(
                descriptor,
                min(
                    64 * 1024,
                    _MAX_SHARED_DASHBOARD_CACHE_BYTES + 1 - len(encoded),
                ),
            )
            if not chunk:
                break
            encoded += chunk
        after = os.fstat(descriptor)
        if (
            after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_nlink != 1
            or after.st_size != len(encoded)
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_ctime_ns != before.st_ctime_ns
            or len(encoded) > _MAX_SHARED_DASHBOARD_CACHE_BYTES
        ):
            return None
        raw = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_cache_keys,
            parse_constant=_reject_non_finite_cache_constant,
            parse_float=_parse_finite_cache_float,
        )
        if not isinstance(raw, dict):
            return None
        expires_raw = raw.get("expires_at")
        if isinstance(expires_raw, bool) or not isinstance(expires_raw, (int, float)):
            return None
        expires_at = float(expires_raw)
        now = time.time()
        if (
            not math.isfinite(expires_at)
            or now >= expires_at
            or expires_at
            > now
            + _shared_dashboard_cache_ttl_seconds()
            + _SHARED_DASHBOARD_CACHE_CLOCK_SKEW_SECONDS
        ):
            return None
        payload = raw.get("payload")
        if not isinstance(payload, dict):
            return None
        return max(1.0, expires_at - now), payload
    except Exception:
        return None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if directory_descriptor >= 0:
            os.close(directory_descriptor)


def _write_shared_dashboard_cache(payload: Dict[str, Any]) -> None:
    directory_descriptor = -1
    temporary_descriptor = -1
    temporary_name = ""
    try:
        path = SHARED_DASHBOARD_CACHE
        absolute = path if path.is_absolute() else Path.cwd() / path
        if absolute.is_relative_to(_RELEASE_CACHE_ROOT):
            return
        if _cache_path_has_symlink(absolute.parent):
            return
        absolute.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not _cache_path_is_allowed(absolute):
            return
        encoded = json.dumps(
            {
                "expires_at": time.time() + _shared_dashboard_cache_ttl_seconds(),
                "payload": payload,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(encoded) > _MAX_SHARED_DASHBOARD_CACHE_BYTES:
            return
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            directory_flags |= os.O_NOFOLLOW
        parent_before = os.stat(absolute.parent, follow_symlinks=False)
        directory_descriptor = os.open(absolute.parent, directory_flags)
        parent_opened = os.fstat(directory_descriptor)
        if (
            not stat.S_ISDIR(parent_opened.st_mode)
            or parent_opened.st_dev != parent_before.st_dev
            or parent_opened.st_ino != parent_before.st_ino
        ):
            return
        try:
            existing = os.stat(
                absolute.name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            existing = None
        if existing is not None and (
            not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1
        ):
            return
        temporary_name = f".{absolute.name}.{secrets.token_hex(8)}.tmp"
        temporary_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            temporary_flags |= os.O_NOFOLLOW
        temporary_descriptor = os.open(
            temporary_name,
            temporary_flags,
            0o600,
            dir_fd=directory_descriptor,
        )
        written = 0
        while written < len(encoded):
            count = os.write(temporary_descriptor, encoded[written:])
            if count <= 0:
                raise OSError("short shared dashboard cache write")
            written += count
        os.fsync(temporary_descriptor)
        temporary_metadata = os.fstat(temporary_descriptor)
        if (
            not stat.S_ISREG(temporary_metadata.st_mode)
            or temporary_metadata.st_nlink != 1
            or temporary_metadata.st_size != len(encoded)
        ):
            return
        os.close(temporary_descriptor)
        temporary_descriptor = -1
        os.replace(
            temporary_name,
            absolute.name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
        temporary_name = ""
        os.fsync(directory_descriptor)
    except Exception:
        pass
    finally:
        if temporary_descriptor >= 0:
            os.close(temporary_descriptor)
        if temporary_name and directory_descriptor >= 0:
            try:
                os.unlink(temporary_name, dir_fd=directory_descriptor)
            except OSError:
                pass
        if directory_descriptor >= 0:
            os.close(directory_descriptor)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _apply_cached_dashboard(payload: Dict[str, Any], *, cache_state: str) -> Dict[str, Any]:
    if dashboard_is_computable(payload):
        return apply_dashboard_trust_gate(payload, cache_state=cache_state)
    return apply_dashboard_trust_gate(payload, cache_state="invalid")


def _iso(dt: datetime | None = None) -> str:
    return (dt or _utc_now()).isoformat().replace("+00:00", "Z")


WORLD_BANK_SPECS: List[Dict[str, str]] = [
    {"key": "gdp", "country": "WLD", "indicator": "NY.GDP.MKTP.KD.ZG", "name": "世界银行：全球 GDP 增速", "metric_id": "WB-GDP", "label": "全球 GDP 增速基线", "unit": "%", "category": "economy", "region": "全球", "description": "世界银行公布的全球 GDP 实际增速。"},
    {"key": "inflation", "country": "WLD", "indicator": "NY.GDP.DEFL.KD.ZG", "name": "世界银行：全球价格压力", "metric_id": "WB-CPI", "label": "全球通胀压力基线", "unit": "%", "category": "economy", "region": "全球", "description": "世界银行 GDP deflator 近年变化。"},
    {"key": "unemployment", "country": "WLD", "indicator": "SL.UEM.TOTL.ZS", "name": "世界银行：全球失业率", "metric_id": "WB-UNEMP", "label": "全球失业率", "unit": "%", "category": "economy", "region": "全球", "description": "世界银行全球失业率年度序列。"},
    {"key": "trade", "country": "WLD", "indicator": "NE.TRD.GNFS.ZS", "name": "世界银行：全球贸易开放度", "metric_id": "WB-TRADE", "label": "全球贸易开放度", "unit": "%GDP", "category": "logistics", "region": "全球", "description": "商品和服务进出口总额占 GDP 比重。"},
    {"key": "military", "country": "WLD", "indicator": "MS.MIL.XPND.GD.ZS", "name": "世界银行：全球军费占比", "metric_id": "WB-MIL", "label": "全球军费占 GDP", "unit": "%GDP", "category": "security", "region": "全球", "description": "全球军费支出占 GDP 比重。"},
    {"key": "internet", "country": "WLD", "indicator": "IT.NET.USER.ZS", "name": "世界银行：互联网普及率", "metric_id": "WB-INTERNET", "label": "全球互联网普及率", "unit": "%", "category": "science", "region": "全球", "description": "使用互联网的人口占比。"},
    {"key": "popgrowth", "country": "WLD", "indicator": "SP.POP.GROW", "name": "世界银行：全球人口增速", "metric_id": "WB-POP", "label": "全球人口增速", "unit": "%", "category": "society", "region": "全球", "description": "世界银行全球人口年增长率。"},
    {"key": "hitech", "country": "WLD", "indicator": "TX.VAL.TECH.MF.ZS", "name": "世界银行：高技术出口占比", "metric_id": "WB-HTECH", "label": "高技术出口占比", "unit": "%", "category": "science", "region": "全球", "description": "高技术出口占制造业出口比重。"},
    {"key": "electricity", "country": "WLD", "indicator": "EG.USE.ELEC.KH.PC", "name": "世界银行：人均用电量", "metric_id": "WB-ELECTRIC", "label": "全球人均用电量", "unit": "kWh", "category": "energy", "region": "全球", "description": "全球人均电力消费年度序列。"},
    {"key": "manufacturing", "country": "WLD", "indicator": "NV.IND.MANF.ZS", "name": "世界银行：制造业占比", "metric_id": "WB-MFG", "label": "全球制造业增加值占比", "unit": "%GDP", "category": "economy", "region": "全球", "description": "制造业增加值占 GDP 比重。"},
    {"key": "fdi", "country": "WLD", "indicator": "BX.KLT.DINV.WD.GD.ZS", "name": "世界银行：FDI 净流入", "metric_id": "WB-FDI", "label": "全球 FDI 净流入占比", "unit": "%GDP", "category": "economy", "region": "全球", "description": "外商直接投资净流入占 GDP 比重。"},
    {"key": "usa_gdp", "country": "USA", "indicator": "NY.GDP.MKTP.KD.ZG", "name": "世界银行：美国 GDP 增速", "metric_id": "WB-USA-GDP", "label": "美国 GDP 增速", "unit": "%", "category": "economy", "region": "美国", "description": "美国实际 GDP 年增长率。"},
    {"key": "chn_gdp", "country": "CHN", "indicator": "NY.GDP.MKTP.KD.ZG", "name": "世界银行：中国 GDP 增速", "metric_id": "WB-CHN-GDP", "label": "中国 GDP 增速", "unit": "%", "category": "economy", "region": "中国", "description": "中国实际 GDP 年增长率。"},
    {"key": "euu_gdp", "country": "EUU", "indicator": "NY.GDP.MKTP.KD.ZG", "name": "世界银行：欧盟 GDP 增速", "metric_id": "WB-EUU-GDP", "label": "欧盟 GDP 增速", "unit": "%", "category": "economy", "region": "欧盟", "description": "欧盟实际 GDP 年增长率。"},
    {"key": "ind_gdp", "country": "IND", "indicator": "NY.GDP.MKTP.KD.ZG", "name": "世界银行：印度 GDP 增速", "metric_id": "WB-IND-GDP", "label": "印度 GDP 增速", "unit": "%", "category": "economy", "region": "印度", "description": "印度实际 GDP 年增长率。"},
    {"key": "jpn_gdp", "country": "JPN", "indicator": "NY.GDP.MKTP.KD.ZG", "name": "世界银行：日本 GDP 增速", "metric_id": "WB-JPN-GDP", "label": "日本 GDP 增速", "unit": "%", "category": "economy", "region": "日本", "description": "日本实际 GDP 年增长率。"},
    {"key": "deu_gdp", "country": "DEU", "indicator": "NY.GDP.MKTP.KD.ZG", "name": "世界银行：德国 GDP 增速", "metric_id": "WB-DEU-GDP", "label": "德国 GDP 增速", "unit": "%", "category": "economy", "region": "德国", "description": "德国实际 GDP 年增长率。"},
    {"key": "usa_cpi", "country": "USA", "indicator": "FP.CPI.TOTL.ZG", "name": "世界银行：美国 CPI", "metric_id": "WB-USA-CPI", "label": "美国 CPI 通胀", "unit": "%", "category": "economy", "region": "美国", "description": "美国消费者价格指数年涨幅。"},
    {"key": "chn_cpi", "country": "CHN", "indicator": "FP.CPI.TOTL.ZG", "name": "世界银行：中国 CPI", "metric_id": "WB-CHN-CPI", "label": "中国 CPI 通胀", "unit": "%", "category": "economy", "region": "中国", "description": "中国消费者价格指数年涨幅。"},
    {"key": "ind_cpi", "country": "IND", "indicator": "FP.CPI.TOTL.ZG", "name": "世界银行：印度 CPI", "metric_id": "WB-IND-CPI", "label": "印度 CPI 通胀", "unit": "%", "category": "economy", "region": "印度", "description": "印度消费者价格指数年涨幅。"},
    {"key": "jpn_cpi", "country": "JPN", "indicator": "FP.CPI.TOTL.ZG", "name": "世界银行：日本 CPI", "metric_id": "WB-JPN-CPI", "label": "日本 CPI 通胀", "unit": "%", "category": "economy", "region": "日本", "description": "日本消费者价格指数年涨幅。"},
    {"key": "deu_cpi", "country": "DEU", "indicator": "FP.CPI.TOTL.ZG", "name": "世界银行：德国 CPI", "metric_id": "WB-DEU-CPI", "label": "德国 CPI 通胀", "unit": "%", "category": "economy", "region": "德国", "description": "德国消费者价格指数年涨幅。"},
]

WORLD_BANK_MATRIX_COUNTRIES: List[Tuple[str, str, str]] = [
    ("usa", "USA", "美国"),
    ("chn", "CHN", "中国"),
    ("euu", "EUU", "欧盟"),
    ("ind", "IND", "印度"),
    ("jpn", "JPN", "日本"),
    ("deu", "DEU", "德国"),
    ("fra", "FRA", "法国"),
    ("gbr", "GBR", "英国"),
    ("bra", "BRA", "巴西"),
    ("rus", "RUS", "俄罗斯"),
    ("kor", "KOR", "韩国"),
    ("zaf", "ZAF", "南非"),
]

WORLD_BANK_MATRIX_INDICATORS: List[Dict[str, str]] = [
    {"key": "gdp", "indicator": "NY.GDP.MKTP.KD.ZG", "label": "GDP 增速", "unit": "%", "category": "economy", "description": "实际 GDP 年增长率。"},
    {"key": "cpi", "indicator": "FP.CPI.TOTL.ZG", "label": "CPI 通胀", "unit": "%", "category": "economy", "description": "消费者价格指数年涨幅。"},
    {"key": "unemp", "indicator": "SL.UEM.TOTL.ZS", "label": "失业率", "unit": "%", "category": "economy", "description": "总失业率占劳动力比例。"},
    {"key": "trade", "indicator": "NE.TRD.GNFS.ZS", "label": "贸易开放度", "unit": "%GDP", "category": "logistics", "description": "商品和服务进出口总额占 GDP 比重。"},
    {"key": "mil", "indicator": "MS.MIL.XPND.GD.ZS", "label": "军费占 GDP", "unit": "%GDP", "category": "security", "description": "军费支出占 GDP 比重。"},
    {"key": "internet", "indicator": "IT.NET.USER.ZS", "label": "互联网普及率", "unit": "%", "category": "science", "description": "使用互联网的人口占比。"},
    {"key": "mfg", "indicator": "NV.IND.MANF.ZS", "label": "制造业占比", "unit": "%GDP", "category": "economy", "description": "制造业增加值占 GDP 比重。"},
    {"key": "pop", "indicator": "SP.POP.GROW", "label": "人口增速", "unit": "%", "category": "society", "description": "人口年增长率。"},
    {"key": "elec", "indicator": "EG.USE.ELEC.KH.PC", "label": "人均用电量", "unit": "kWh", "category": "energy", "description": "人均电力消费量。"},
    {"key": "energyuse", "indicator": "EG.USE.PCAP.KG.OE", "label": "人均能源使用", "unit": "kg油当量", "category": "energy", "description": "人均能源使用量，按千克油当量计。"},
    {"key": "elec_access", "indicator": "EG.ELC.ACCS.ZS", "label": "通电率", "unit": "%", "category": "energy", "description": "可获得电力的人口占比。"},
    {"key": "clean_cook", "indicator": "EG.CFT.ACCS.ZS", "label": "清洁烹饪可及率", "unit": "%", "category": "energy", "description": "可获得清洁烹饪燃料和技术的人口占比。"},
    {"key": "renew", "indicator": "EG.FEC.RNEW.ZS", "label": "可再生能源占比", "unit": "%", "category": "energy", "description": "可再生能源在最终能源消费中的占比。"},
    {"key": "renew_elec", "indicator": "EG.ELC.RNEW.ZS", "label": "可再生发电占比", "unit": "%", "category": "energy", "description": "可再生能源发电占总发电量比例。"},
    {"key": "fossil_elec", "indicator": "EG.ELC.FOSL.ZS", "label": "化石发电占比", "unit": "%", "category": "energy", "description": "油气煤发电占总发电量比例。"},
    {"key": "grid_loss", "indicator": "EG.ELC.LOSS.ZS", "label": "电网输配损耗", "unit": "%", "category": "energy", "description": "电力输配损耗占发电量比例。"},
]

_existing_world_bank_metric_ids = {spec["metric_id"] for spec in WORLD_BANK_SPECS}
for country_key, country_code, region_label in WORLD_BANK_MATRIX_COUNTRIES:
    for indicator_spec in WORLD_BANK_MATRIX_INDICATORS:
        metric_id = f"WB-{country_code}-{indicator_spec['key'].upper()}"
        if metric_id in _existing_world_bank_metric_ids:
            continue
        WORLD_BANK_SPECS.append({
            "key": f"{country_key}_{indicator_spec['key']}",
            "country": country_code,
            "indicator": indicator_spec["indicator"],
            "name": f"世界银行：{region_label} {indicator_spec['label']}",
            "metric_id": metric_id,
            "label": f"{region_label} {indicator_spec['label']}",
            "unit": indicator_spec["unit"],
            "category": indicator_spec["category"],
            "region": region_label,
            "description": f"{region_label}{indicator_spec['description']}",
        })
        _existing_world_bank_metric_ids.add(metric_id)

OPENALEX_SPECS: List[Dict[str, str]] = [
    {"key": "tech", "query": "artificial intelligence semiconductor", "source_id": "openalex-tech", "name": "OpenAlex：AI 与半导体", "metric_id": "OA-TECH", "label": "AI/半导体论文产出热度", "category": "science", "description": "近 30 天 AI 与半导体方向论文数量。"},
    {"key": "quantum", "query": "quantum computing", "source_id": "openalex-quantum", "name": "OpenAlex：量子计算", "metric_id": "OA-QUANTUM", "label": "量子计算论文热度", "category": "science", "description": "近 30 天量子计算方向论文数量。"},
    {"key": "bio", "query": "biotechnology synthetic biology", "source_id": "openalex-bio", "name": "OpenAlex：生物技术", "metric_id": "OA-BIO", "label": "生物技术论文热度", "category": "science", "description": "近 30 天生物技术与合成生物学论文数量。"},
    {"key": "climate", "query": "climate change energy transition", "source_id": "openalex-climate", "name": "OpenAlex：气候能源", "metric_id": "OA-CLIMATE", "label": "气候能源论文热度", "category": "energy", "description": "近 30 天气候变化与能源转型方向论文数量。"},
    {"key": "cyber", "query": "cybersecurity vulnerability malware", "source_id": "openalex-cyber", "name": "OpenAlex：网络安全", "metric_id": "OA-CYBER", "label": "网络安全论文热度", "category": "science", "description": "近 30 天网络安全、漏洞与恶意软件方向论文数量。"},
    {"key": "robotics", "query": "robotics autonomous systems", "source_id": "openalex-robotics", "name": "OpenAlex：机器人", "metric_id": "OA-ROBOTICS", "label": "机器人论文热度", "category": "science", "description": "近 30 天机器人与自主系统方向论文数量。"},
    {"key": "space", "query": "space technology satellite remote sensing", "source_id": "openalex-space", "name": "OpenAlex：空间技术", "metric_id": "OA-SPACE", "label": "空间技术论文热度", "category": "science", "description": "近 30 天空间技术、卫星与遥感方向论文数量。"},
    {"key": "battery", "query": "battery energy storage lithium", "source_id": "openalex-battery", "name": "OpenAlex：储能电池", "metric_id": "OA-BATTERY", "label": "储能电池论文热度", "category": "energy", "description": "近 30 天电池、储能与锂相关论文数量。"},
    {"key": "nuclear", "query": "nuclear energy fusion reactor", "source_id": "openalex-nuclear", "name": "OpenAlex：核能聚变", "metric_id": "OA-NUCLEAR", "label": "核能聚变论文热度", "category": "energy", "description": "近 30 天核能、聚变与反应堆方向论文数量。"},
    {"key": "water", "query": "water scarcity drought hydrology", "source_id": "openalex-water", "name": "OpenAlex：水资源", "metric_id": "OA-WATER", "label": "水资源论文热度", "category": "society", "description": "近 30 天水资源、干旱与水文学方向论文数量。"},
    {"key": "food", "query": "food security agriculture crop yield", "source_id": "openalex-food", "name": "OpenAlex：粮食安全", "metric_id": "OA-FOOD", "label": "粮食安全论文热度", "category": "society", "description": "近 30 天粮食安全、农业与作物产量方向论文数量。"},
    {"key": "health", "query": "public health infectious disease surveillance", "source_id": "openalex-health", "name": "OpenAlex：公共卫生", "metric_id": "OA-HEALTH", "label": "公共卫生论文热度", "category": "society", "description": "近 30 天公共卫生、传染病与监测方向论文数量。"},
]


def _source(
    source_id: str,
    name: str,
    status: str,
    *,
    records: int = 0,
    detail: str = "",
    cadence: str = "",
    url: str = "",
    latency_ms: Optional[int] = None,
    last_updated: Optional[str] = None,
) -> Dict[str, Any]:
    checked_at = _iso()
    return {
        "id": source_id,
        "name": name,
        "status": status,
        "records": records,
        "detail": detail,
        "cadence": cadence,
        "url": url,
        "latency_ms": latency_ms,
        "last_updated": last_updated or checked_at,
        "checked_at": checked_at,
    }


def _source_cache_ttl(cadence: str) -> int:
    raw = (cadence or "").strip().lower()
    if "annual" in raw:
        return 86400
    if "daily" in raw or "day" in raw:
        return 3600
    if "15m" in raw:
        return 900
    return 300


def _source_cache_key(source_id: str, url: str, params: Optional[Dict[str, Any]]) -> str:
    if not params:
        return f"{source_id}|{url}"
    param_key = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return f"{source_id}|{url}|{param_key}"


async def _get_json(
    client: httpx.AsyncClient,
    source_id: str,
    name: str,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    cadence: str = "",
    timeout: Optional[float] = None,
) -> Tuple[Optional[Any], Dict[str, Any]]:
    cache_key = _source_cache_key(source_id, url, params)
    cached = _SOURCE_CACHE.get(cache_key)
    now_mono = time.monotonic()
    if cached and now_mono < cached[0]:
        return cached[1], _source(
            source_id,
            name,
            "live",
            detail="cached",
            cadence=cadence,
            url=url,
            latency_ms=0,
        )

    started = time.perf_counter()
    try:
        request_kwargs: Dict[str, Any] = {"params": params, "headers": headers}
        if timeout is not None:
            request_kwargs["timeout"] = timeout
        response = await client.get(url, **request_kwargs)
        latency_ms = int((time.perf_counter() - started) * 1000)
        response.raise_for_status()
        try:
            data = response.json()
        except Exception as exc:
            preview = response.text[:180].replace("\n", " ")
            raise RuntimeError(f"invalid JSON: {preview}") from exc
        _SOURCE_CACHE[cache_key] = (now_mono + _source_cache_ttl(cadence), data)
        return data, _source(
            source_id,
            name,
            "live",
            detail="connected",
            cadence=cadence,
            url=str(response.url),
            latency_ms=latency_ms,
        )
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        detail = str(exc) or exc.__class__.__name__
        if cached:
            return cached[1], _source(
                source_id,
                name,
                "degraded",
                detail=f"stale cache after error: {detail}"[:220],
                cadence=cadence,
                url=url,
                latency_ms=latency_ms,
            )
        return None, _source(
            source_id,
            name,
            "degraded",
            detail=detail[:220],
            cadence=cadence,
            url=url,
            latency_ms=latency_ms,
        )


def _score_from_count(count: float, *, base: float = 40, scale: float = 20, cap: float = 96) -> float:
    if count <= 0:
        return max(0.0, base * 0.65)
    return round(min(cap, base + math.log1p(count) * scale), 2)


def _series_point(dt: datetime, value: float, *, samples: Optional[int] = None) -> Dict[str, Any]:
    point = {
        "time": int(dt.astimezone(timezone.utc).timestamp()),
        "value": round(float(value), 4),
    }
    if samples is not None:
        point["samples"] = int(samples)
    return point


def _latest_value(points: List[Dict[str, Any]], default: float = 0.0) -> float:
    if not points:
        return round(default, 2)
    try:
        return round(float(points[-1]["value"]), 2)
    except Exception:
        return round(default, 2)


def _series_change_pct(points: List[Dict[str, Any]]) -> float:
    if len(points) < 2:
        return 0.0
    first = float(points[0].get("value") or 0.0)
    last = float(points[-1].get("value") or 0.0)
    if not first:
        return 0.0
    return round(((last - first) / abs(first)) * 100, 2)


def _recent_change_pct(points: List[Dict[str, Any]]) -> float:
    if len(points) < 2:
        return 0.0
    previous = float(points[-2].get("value") or 0.0)
    current = float(points[-1].get("value") or 0.0)
    if not previous:
        return 0.0
    return round(((current - previous) / abs(previous)) * 100, 2)


def _spark_from_points(points: List[Dict[str, Any]], *, count: int = 24) -> List[float]:
    values = [round(float(point.get("value") or 0.0), 3) for point in points[-count:]]
    return values if values else [0.0]


def _history_points(metric_id: str) -> List[Dict[str, Any]]:
    return list(_METRIC_HISTORY.get(metric_id, []))


def _append_history_point(metric_id: str, value: float, *, at: Optional[datetime] = None) -> None:
    ts = int((at or _utc_now()).timestamp())
    history = _METRIC_HISTORY.setdefault(metric_id, [])
    if history and history[-1]["time"] == ts:
        history[-1]["value"] = round(float(value), 4)
    else:
        history.append({"time": ts, "value": round(float(value), 4)})
    if len(history) > HISTORY_POINTS:
        del history[:-HISTORY_POINTS]


def _prefer_points(metric_id: str, native_points: List[Dict[str, Any]], fallback_value: float) -> List[Dict[str, Any]]:
    points = list(native_points)
    if len(points) < 2:
        points = _history_points(metric_id)
    if points:
        return points
    return [_series_point(_utc_now(), fallback_value)]


def _resample_values(points: List[Dict[str, Any]], target_length: int, fallback_value: float = 0.0) -> List[float]:
    values = [float(point.get("value") or 0.0) for point in points]
    if not values:
        return [round(float(fallback_value), 4)] * max(target_length, 1)
    if len(values) == target_length:
        return [round(value, 4) for value in values]
    if len(values) == 1:
        return [round(values[0], 4)] * max(target_length, 1)
    out: List[float] = []
    for index in range(max(target_length, 1)):
        pos = index * (len(values) - 1) / max(target_length - 1, 1)
        lo = int(math.floor(pos))
        hi = int(math.ceil(pos))
        if lo == hi:
            out.append(round(values[lo], 4))
            continue
        mix = pos - lo
        out.append(round(values[lo] * (1 - mix) + values[hi] * mix, 4))
    return out


def _pick_anchor(*series: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    candidates = [list(points) for points in series if points]
    if not candidates:
        return [_series_point(_utc_now(), 0)]
    candidates.sort(key=len, reverse=True)
    return candidates[0]


def _combine_on_anchor(
    anchor: List[Dict[str, Any]],
    series_values: List[List[Dict[str, Any]]],
    builder,
) -> List[Dict[str, Any]]:
    if not anchor:
        return []
    aligned = [_resample_values(points, len(anchor)) for points in series_values]
    out: List[Dict[str, Any]] = []
    for index, base in enumerate(anchor):
        values = [values_list[index] for values_list in aligned]
        out.append({
            "time": base["time"],
            "value": round(float(builder(*values)), 4),
        })
    return out


def _parse_iso_dt(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
        return datetime.fromisoformat(text).astimezone(timezone.utc)
    except Exception:
        return None


def _parse_gdelt_dt(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y%m%d%H%M%S", "%Y%m%d%H%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _parse_rss_dt(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return _parse_iso_dt(text)


def _xml_child_text(element: Optional[ET.Element], child_name: str) -> str:
    if element is None:
        return ""
    for child in list(element):
        local_name = str(child.tag).rsplit("}", 1)[-1]
        if local_name == child_name:
            return str(child.text or "").strip()
    return ""


def _bucket_samples(
    samples: List[Tuple[datetime, float]],
    *,
    bucket_count: int,
    bucket_size: timedelta,
    aggregation: str,
) -> List[Dict[str, Any]]:
    if bucket_count <= 0:
        return []
    now = _utc_now()
    start = now - bucket_size * bucket_count
    points: List[Dict[str, Any]] = []
    for index in range(bucket_count):
        bucket_start = start + bucket_size * index
        bucket_end = bucket_start + bucket_size
        values = [value for dt, value in samples if bucket_start <= dt < bucket_end]
        if aggregation == "count":
            metric_value = float(len(values))
        elif aggregation == "max":
            metric_value = max(values) if values else 0.0
        elif aggregation == "avg":
            metric_value = sum(values) / len(values) if values else 0.0
        else:
            metric_value = sum(values)
        points.append(_series_point(bucket_end, metric_value, samples=len(values)))
    return points


def _bars_from_series(points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not points:
        return []
    bars: List[Dict[str, Any]] = []
    previous = float(points[0].get("value") or 0.0)
    for point in points:
        close = float(point.get("value") or 0.0)
        open_v = previous
        spread = max(0.18, abs(close - open_v) * 0.45)
        high = max(open_v, close) + spread
        low = max(0.0, min(open_v, close) - spread)
        bars.append({
            "time": int(point["time"]),
            "open": round(open_v, 4),
            "high": round(high, 4),
            "low": round(low, 4),
            "close": round(close, 4),
            "volume": int(point.get("samples") or max(1, abs(close - open_v) * 1000)),
        })
        previous = close
    return bars


def _latest_point_age_days(points: List[Dict[str, Any]]) -> Optional[float]:
    if not points:
        return None
    try:
        latest_ts = max(int(point.get("time") or 0) for point in points)
    except Exception:
        return None
    if latest_ts <= 0:
        return None
    return max(0.0, (_utc_now().timestamp() - latest_ts) / 86400)


def _fresh_chart_points(
    primary: List[Dict[str, Any]],
    fallbacks: List[List[Dict[str, Any]]],
    *,
    max_age_days: int = 45,
    max_points: int = 240,
) -> List[Dict[str, Any]]:
    for points in [primary, *fallbacks]:
        if len(points) < 2:
            continue
        age = _latest_point_age_days(points)
        if age is not None and age <= max_age_days:
            return points[-max_points:]
    return primary[-max_points:] if primary else []


def _should_record_metric_history(definition: Dict[str, Any]) -> bool:
    status = str(definition.get("status") or "live").lower()
    points = definition.get("points") or []
    if status in {"disabled", "degraded"} and not points:
        return False
    return True


def _stable_spark(value: float, key: str, *, points: int = 24, amplitude: float = 0.055) -> List[float]:
    seed = sum(ord(c) for c in key) % 37
    out: List[float] = []
    for i in range(points):
        phase = (i + seed) * 0.63
        wobble = math.sin(phase) * amplitude + math.cos(phase * 0.43) * amplitude * 0.45
        trend = (i - points + 1) / max(points, 1) * amplitude * 0.65
        out.append(round(max(0.01, value * (1 + wobble + trend)), 3))
    return out


def _change_pct(series: Iterable[float], current: float) -> float:
    values = list(series)
    if not values or not values[0]:
        return 0.0
    return round(((current - values[0]) / values[0]) * 100, 2)


def _rolling_ma(bars: List[Dict[str, Any]], period: int) -> List[Dict[str, float]]:
    if len(bars) < period:
        return []
    out: List[Dict[str, float]] = []
    for i in range(period - 1, len(bars)):
        value = sum(float(bars[i - j]["close"]) for j in range(period)) / period
        out.append({"time": bars[i]["time"], "value": round(value, 4)})
    return out


def _make_bars(world_value: float, components: List[float], *, count: int = 220) -> List[Dict[str, Any]]:
    now = int(time.time())
    step = 3600
    component_pressure = sum(components) / max(len(components), 1)
    start_value = world_value * 0.92 + component_pressure * 0.08
    bars: List[Dict[str, Any]] = []
    prev = start_value
    for i in range(count):
        pulse = math.sin(i * 0.19) * 0.34 + math.cos(i * 0.071) * 0.22
        event = 0.0
        if i % 41 == 0:
            event += min(1.25, component_pressure / 78)
        if i % 67 == 0:
            event -= 0.72
        target_pull = (world_value - prev) * 0.012
        open_v = prev
        close_v = max(15.0, min(99.0, open_v + pulse + event + target_pull))
        spread = 0.34 + abs(event) * 0.38 + (component_pressure / 100) * 0.22
        high = max(open_v, close_v) + spread
        low = min(open_v, close_v) - spread
        volume = int(80000 + component_pressure * 2500 + abs(event) * 85000 + (math.sin(i * 0.37) + 1) * 16000)
        bars.append({
            "time": now - (count - i) * step,
            "open": round(open_v, 4),
            "high": round(high, 4),
            "low": round(max(0.0, low), 4),
            "close": round(close_v, 4),
            "volume": volume,
        })
        prev = close_v
    return bars


def _article_text(article: Dict[str, Any]) -> str:
    return " ".join(str(article.get(k) or "") for k in ("title", "seendate", "domain", "sourcecountry")).lower()


def _count_keywords(articles: List[Dict[str, Any]], keywords: Iterable[str]) -> int:
    keys = [k.lower() for k in keywords]
    return sum(1 for article in articles if any(k in _article_text(article) for k in keys))


async def _fetch_gdelt(client: httpx.AsyncClient) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    params = {
        "query": '(conflict OR protest OR sanctions OR "supply chain" OR semiconductor OR energy OR migration OR cyber)',
        "mode": "artlist",
        "format": "json",
        "timespan": "24h",
        "maxrecords": 150,
    }
    data, status = await _get_json(
        client,
        "gdelt",
        "GDELT 2.1 DOC",
        "https://api.gdeltproject.org/api/v2/doc/doc",
        params=params,
        cadence="15m",
        timeout=GDELT_TIMEOUT_SECONDS,
    )
    articles = data.get("articles", []) if isinstance(data, dict) else []
    if status["status"] == "live":
        status["records"] = len(articles)
        status["detail"] = "latest global news/event articles"
        observed = [
            timestamp
            for timestamp in (_parse_gdelt_dt(article.get("seendate")) for article in articles)
            if timestamp is not None
        ]
        if observed:
            status["last_updated"] = _iso(max(observed))
    counts = {
        "politics": _count_keywords(articles, ["diplomacy", "minister", "election", "sanction", "summit", "united nations", "g7"]),
        "security": _count_keywords(articles, ["conflict", "war", "attack", "military", "missile", "strike", "protest"]),
        "energy": _count_keywords(articles, ["oil", "gas", "energy", "power", "electricity", "opec"]),
        "logistics": _count_keywords(articles, ["shipping", "port", "supply chain", "semiconductor", "red sea", "trade"]),
        "science": _count_keywords(articles, ["ai", "artificial intelligence", "semiconductor", "chip", "technology", "cyber"]),
        "society": _count_keywords(articles, ["migration", "refugee", "food", "health", "climate", "protest"]),
    }
    return {"articles": articles, "counts": counts}, status


def _latest_non_null_observation(data: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(data, list) or len(data) < 2 or not isinstance(data[1], list):
        return None
    for row in data[1]:
        if row.get("value") is not None:
            return row
    return None


async def _fetch_worldbank_indicator(
    client: httpx.AsyncClient,
    indicator: str,
    name: str,
    *,
    country: str = "WLD",
    source_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    resolved_source_id = source_id or f"worldbank-{country.lower()}-{indicator.lower().replace('.', '-')}"
    data, status = await _get_json(
        client,
        resolved_source_id,
        name,
        f"https://api.worldbank.org/v2/country/{country}/indicator/{indicator}",
        params={"format": "json", "per_page": 24},
        cadence="annual",
    )
    observations = data[1] if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list) else []
    points: List[Dict[str, Any]] = []
    latest: Optional[float] = None
    latest_year = ""
    for row in reversed(observations):
        if row.get("value") is None:
            continue
        try:
            dt = datetime(int(row["date"]), 1, 1, tzinfo=timezone.utc)
            value = float(row["value"])
        except Exception:
            continue
        points.append(_series_point(dt, value))
        latest = value
        latest_year = str(row.get("date") or latest_year)
    if latest is not None:
        status["records"] = len(points)
        status["detail"] = f"{latest_year}: {latest}"
        status["last_updated"] = _iso(
            datetime(int(latest_year), 12, 31, tzinfo=timezone.utc)
        )
    return {"latest": latest, "points": points}, status


async def _fetch_worldbank_indicator_batch(
    client: httpx.AsyncClient,
    indicator: str,
    specs: List[Dict[str, str]],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    countries = sorted({spec["country"] for spec in specs})
    source_name = f"世界银行：{specs[0]['label']}等"
    data, batch_status = await _get_json(
        client,
        f"worldbank-batch-{indicator.lower().replace('.', '-')}",
        source_name,
        f"https://api.worldbank.org/v2/country/{';'.join(countries)}/indicator/{indicator}",
        params={"format": "json", "per_page": max(1200, len(countries) * 48)},
        cadence="annual",
    )
    observations = data[1] if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list) else []
    grouped: Dict[str, List[Dict[str, Any]]] = {country: [] for country in countries}
    for row in observations:
        country_code = str(row.get("countryiso3code") or row.get("country", {}).get("id") or "").upper()
        if country_code in grouped:
            grouped[country_code].append(row)

    payloads: Dict[str, Dict[str, Any]] = {}
    statuses: Dict[str, Dict[str, Any]] = {}
    for spec in specs:
        rows = grouped.get(spec["country"], [])
        points: List[Dict[str, Any]] = []
        latest: Optional[float] = None
        latest_year = ""
        for row in reversed(rows):
            if row.get("value") is None:
                continue
            try:
                dt = datetime(int(row["date"]), 1, 1, tzinfo=timezone.utc)
                value = float(row["value"])
            except Exception:
                continue
            points.append(_series_point(dt, value))
            latest = value
            latest_year = str(row.get("date") or latest_year)

        status = dict(batch_status)
        status["id"] = f"worldbank-{spec['key']}"
        status["name"] = spec["name"]
        status["records"] = len(points)
        if batch_status["status"] == "live" and latest is not None:
            status["detail"] = f"{latest_year}: {latest}"
            status["last_updated"] = _iso(
                datetime(int(latest_year), 12, 31, tzinfo=timezone.utc)
            )
        elif batch_status["status"] == "live":
            status["status"] = "degraded"
            status["detail"] = "no recent observation"
        payloads[spec["key"]] = {"latest": latest, "points": points}
        statuses[spec["key"]] = status
    return payloads, statuses


async def _fetch_openalex_count(client: httpx.AsyncClient, query: str, source_id: str, name: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    since = (_utc_now() - timedelta(days=30)).date().isoformat()
    params = {
        "search": query,
        "filter": f"from_publication_date:{since}",
        "per-page": 1,
    }
    mailto = string_setting("OPENALEX_MAILTO")
    if mailto:
        params["mailto"] = mailto
    data, status = await _get_json(
        client,
        source_id,
        name,
        "https://api.openalex.org/works",
        params=params,
        cadence="daily",
    )
    count = 0
    if isinstance(data, dict):
        count = int(data.get("meta", {}).get("count") or 0)
    if status["status"] == "live":
        status["records"] = count
        status["detail"] = f"30d works matching {query}"
    return {"latest": count, "points": []}, status


async def _fetch_opensky(client: httpx.AsyncClient) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    data, status = await _get_json(
        client,
        "opensky",
        "OpenSky Network",
        "https://opensky-network.org/api/states/all",
        cadence="near-real-time",
    )
    states = data.get("states", []) if isinstance(data, dict) else []
    airborne = [row for row in states if isinstance(row, list) and len(row) > 8 and row[8] is False]
    countries: Dict[str, int] = {}
    for row in states:
        if isinstance(row, list) and len(row) > 2 and row[2]:
            countries[str(row[2])] = countries.get(str(row[2]), 0) + 1
    if status["status"] == "live":
        status["records"] = len(states)
        status["detail"] = f"{len(airborne)} airborne states"
    return {
        "states": len(states),
        "airborne": len(airborne),
        "rows": states,
        "top_countries": sorted(countries.items(), key=lambda item: item[1], reverse=True)[:5],
    }, status


async def _fetch_usgs(client: httpx.AsyncClient) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    data, status = await _get_json(
        client,
        "usgs-earthquake",
        "USGS Earthquake",
        "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson",
        cadence="5m",
    )
    features = data.get("features", []) if isinstance(data, dict) else []
    mags = [
        float(item.get("properties", {}).get("mag") or 0)
        for item in features
        if isinstance(item, dict)
    ]
    if status["status"] == "live":
        status["records"] = len(features)
        status["detail"] = f"max magnitude {max(mags) if mags else 0:.1f}"
    return {"count": len(features), "max_mag": max(mags) if mags else 0.0, "features": features}, status


async def _fetch_nvd(client: httpx.AsyncClient) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    end = _utc_now()
    start = end - timedelta(days=7)
    data, status = await _get_json(
        client,
        "nvd",
        "NVD CVE",
        "https://services.nvd.nist.gov/rest/json/cves/2.0",
        params={
            "pubStartDate": start.strftime("%Y-%m-%dT%H:%M:%S.000"),
            "pubEndDate": end.strftime("%Y-%m-%dT%H:%M:%S.000"),
            "resultsPerPage": 2000,
        },
        cadence="continuous",
        timeout=NVD_TIMEOUT_SECONDS,
    )
    count = int(data.get("totalResults") or 0) if isinstance(data, dict) else 0
    vulnerabilities = data.get("vulnerabilities", []) if isinstance(data, dict) else []
    if status["status"] == "live":
        status["records"] = count
        status["detail"] = "7d published CVEs"
    return {"recent": count, "vulnerabilities": vulnerabilities}, status


async def _fetch_cisa_kev(client: httpx.AsyncClient) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    data, status = await _get_json(
        client,
        "cisa-kev",
        "CISA KEV",
        "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json",
        cadence="daily",
    )
    vulnerabilities = data.get("vulnerabilities", []) if isinstance(data, dict) else []
    cutoff = _utc_now().date() - timedelta(days=30)
    recent = 0
    for item in vulnerabilities:
        date_s = str(item.get("dateAdded") or "")
        try:
            if datetime.fromisoformat(date_s).date() >= cutoff:
                recent += 1
        except ValueError:
            continue
    if status["status"] == "live":
        status["records"] = len(vulnerabilities)
        status["detail"] = f"{recent} added in 30d"
    return {"recent": recent, "vulnerabilities": vulnerabilities}, status


async def _fetch_eia(client: httpx.AsyncClient) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    api_key = string_setting("EIA_API_KEY")
    if not api_key:
        return {"latest": None, "rows": []}, _source("eia", "EIA Open Data", "disabled", detail="missing EIA_API_KEY", cadence="daily")
    data, status = await _get_json(
        client,
        "eia",
        "EIA Open Data",
        "https://api.eia.gov/v2/petroleum/pri/spt/data/",
        params={
            "api_key": api_key,
            "frequency": "daily",
            "data[0]": "value",
            "facets[product][]": "EPCBRENT",
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
            "length": 32,
        },
        cadence="daily",
    )
    rows = data.get("response", {}).get("data", []) if isinstance(data, dict) else []
    value = None
    if rows:
        try:
            value = float(rows[0]["value"])
        except Exception:
            value = None
    if status["status"] == "live":
        status["records"] = len(rows)
        status["detail"] = f"Brent spot {value}" if value is not None else "connected"
    return {"latest": value, "rows": rows}, status


async def _fetch_openaq(client: httpx.AsyncClient) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    api_key = string_setting("OPENAQ_API_KEY")
    if not api_key:
        return {"latest": None, "results": []}, _source("openaq", "OpenAQ", "disabled", detail="missing OPENAQ_API_KEY", cadence="near-real-time")
    data, status = await _get_json(
        client,
        "openaq",
        "OpenAQ",
        "https://api.openaq.org/v3/latest",
        params={"limit": 100},
        headers={"X-API-Key": api_key},
        cadence="near-real-time",
    )
    results = data.get("results", []) if isinstance(data, dict) else []
    pm25_values: List[float] = []
    for row in results:
        for measurement in row.get("measurements", []) or []:
            if str(measurement.get("parameter", "")).lower() == "pm25":
                try:
                    pm25_values.append(float(measurement.get("value")))
                except Exception:
                    pass
    avg = sum(pm25_values) / len(pm25_values) if pm25_values else None
    if status["status"] == "live":
        status["records"] = len(results)
        status["detail"] = f"avg pm2.5 {avg:.1f}" if avg is not None else "connected"
    return {"latest": avg, "results": results}, status


async def _fetch_firms(client: httpx.AsyncClient) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    map_key = string_setting("NASA_FIRMS_MAP_KEY")
    if not map_key:
        return {"latest": None, "rows": []}, _source("nasa-firms", "NASA FIRMS", "disabled", detail="missing NASA_FIRMS_MAP_KEY", cadence="near-real-time")
    url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{map_key}/VIIRS_SNPP_NRT/-180,-90,180,90/1"
    started = time.perf_counter()
    try:
        response = await client.get(url)
        latency_ms = int((time.perf_counter() - started) * 1000)
        response.raise_for_status()
        rows = list(csv.DictReader(io.StringIO(response.text)))
        count = len(rows)
        return {"latest": count, "rows": rows}, _source("nasa-firms", "NASA FIRMS", "live", records=count, detail="global VIIRS fires 24h", cadence="near-real-time", url=url, latency_ms=latency_ms)
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return {"latest": None, "rows": []}, _source("nasa-firms", "NASA FIRMS", "degraded", detail=str(exc)[:220], cadence="near-real-time", url=url, latency_ms=latency_ms)


async def _fetch_noaa_kp(client: httpx.AsyncClient) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    data, status = await _get_json(
        client,
        "noaa-kp",
        "NOAA SWPC Kp",
        "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json",
        cadence="3h",
    )
    rows = data if isinstance(data, list) else []
    latest = None
    points: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        dt = _parse_iso_dt(row.get("time_tag"))
        try:
            value = float(row.get("Kp"))
        except Exception:
            continue
        if dt is None:
            continue
        points.append(_series_point(dt, value, samples=int(row.get("station_count") or 0)))
        latest = value
    if status["status"] == "live":
        status["records"] = len(points)
        status["detail"] = f"Kp {latest:.2f}" if latest is not None else "connected"
    return {"latest": latest, "points": points}, status


async def _fetch_eonet(client: httpx.AsyncClient) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    data, status = await _get_json(
        client,
        "nasa-eonet",
        "NASA EONET",
        "https://eonet.gsfc.nasa.gov/api/v3/events",
        params={"status": "open", "limit": 250},
        cadence="daily",
    )
    events = data.get("events", []) if isinstance(data, dict) else []
    category_counts: Dict[str, int] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        for category in event.get("categories", []) or []:
            category_id = str(category.get("id") or "")
            if category_id:
                category_counts[category_id] = category_counts.get(category_id, 0) + 1
    if status["status"] == "live":
        status["records"] = len(events)
        status["detail"] = f"{len(events)} open natural events"
    return {"latest": len(events), "events": events, "category_counts": category_counts}, status


async def _fetch_gdacs(client: httpx.AsyncClient) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    url = "https://www.gdacs.org/xml/rss.xml"
    started = time.perf_counter()
    try:
        response = await client.get(
            url,
            headers={"Accept": "application/rss+xml,application/xml,text/xml,*/*"},
            timeout=XML_SOURCE_TIMEOUT_SECONDS,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        channel = root.find("channel")
        feed_dt = _parse_rss_dt(_xml_child_text(channel, "pubDate"))
        alerts: List[Dict[str, Any]] = []
        samples: List[Tuple[datetime, float]] = []
        high_samples: List[Tuple[datetime, float]] = []
        level_counts: Dict[str, int] = {}
        for item in channel.findall("item") if channel is not None else []:
            title = _xml_child_text(item, "title")
            description = _xml_child_text(item, "description")
            event_type = _xml_child_text(item, "eventtype")
            alert_level = _xml_child_text(item, "alertlevel").lower()
            if not alert_level:
                haystack = f"{title} {description}".lower()
                for candidate in ("red", "orange", "green"):
                    if candidate in haystack:
                        alert_level = candidate
                        break
            dt = _parse_rss_dt(_xml_child_text(item, "pubDate")) or feed_dt
            if alert_level:
                level_counts[alert_level] = level_counts.get(alert_level, 0) + 1
            if dt is not None:
                samples.append((dt, 1.0))
                if alert_level in {"orange", "red"}:
                    high_samples.append((dt, 1.0))
            alerts.append({
                "title": title,
                "description": description,
                "event_type": event_type,
                "alert_level": alert_level,
                "published_at": dt.isoformat().replace("+00:00", "Z") if dt else "",
            })

        cutoff = _utc_now() - timedelta(hours=24)
        recent_24h = sum(1 for alert in alerts if _parse_iso_dt(alert.get("published_at")) and _parse_iso_dt(alert.get("published_at")) >= cutoff)
        high_24h = sum(
            1
            for alert in alerts
            if alert.get("alert_level") in {"orange", "red"}
            and _parse_iso_dt(alert.get("published_at"))
            and _parse_iso_dt(alert.get("published_at")) >= cutoff
        )
        if not recent_24h and alerts:
            recent_24h = len(alerts)
            high_24h = sum(1 for alert in alerts if alert.get("alert_level") in {"orange", "red"})
        detail = f"{recent_24h} alerts/24h, {high_24h} high impact"
        if feed_dt is not None:
            detail += f", latest {feed_dt.date().isoformat()}"
        return (
            {
                "recent_24h": recent_24h,
                "high_24h": high_24h,
                "alerts": alerts,
                "level_counts": level_counts,
                "points": _bucket_samples(samples, bucket_count=12, bucket_size=timedelta(hours=2), aggregation="count"),
                "high_points": _bucket_samples(high_samples, bucket_count=12, bucket_size=timedelta(hours=2), aggregation="count"),
            },
            _source(
                "gdacs",
                "GDACS Disaster Alerts",
                "live",
                records=len(alerts),
                detail=detail,
                cadence="15m",
                url=str(response.url),
                latency_ms=latency_ms,
                last_updated=feed_dt.isoformat().replace("+00:00", "Z") if feed_dt else None,
            ),
        )
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return (
            {"recent_24h": 0, "high_24h": 0, "alerts": [], "level_counts": {}, "points": [], "high_points": []},
            _source("gdacs", "GDACS Disaster Alerts", "degraded", detail=str(exc)[:220], cadence="15m", url=url, latency_ms=latency_ms),
        )


async def _fetch_epss(client: httpx.AsyncClient) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    top_task = _get_json(
        client,
        "first-epss",
        "FIRST EPSS",
        "https://api.first.org/data/v1/epss",
        params={"limit": 10, "order": "!epss"},
        cadence="daily",
    )
    high_95_task = _get_json(
        client,
        "first-epss-high95",
        "FIRST EPSS >=0.95",
        "https://api.first.org/data/v1/epss",
        params={"epss-gt": 0.95, "limit": 0},
        cadence="daily",
    )
    high_99_task = _get_json(
        client,
        "first-epss-high99",
        "FIRST EPSS >=0.99",
        "https://api.first.org/data/v1/epss",
        params={"epss-gt": 0.99, "limit": 0},
        cadence="daily",
    )
    (data, status), (high_95_data, high_95_status), (high_99_data, high_99_status) = await asyncio.gather(
        top_task,
        high_95_task,
        high_99_task,
    )
    rows = data.get("data", []) if isinstance(data, dict) else []
    scores: List[float] = []
    for row in rows:
        try:
            scores.append(float(row.get("epss") or 0))
        except Exception:
            continue
    high_95 = int(high_95_data.get("total") or 0) if isinstance(high_95_data, dict) else 0
    high_99 = int(high_99_data.get("total") or 0) if isinstance(high_99_data, dict) else 0
    max_epss = max(scores) if scores else 0.0
    latest_date = str(rows[0].get("date") or "") if rows else ""
    if status["status"] == "live":
        status["records"] = high_95
        status["detail"] = f"{high_95} CVEs >=0.95 EPSS, {high_99} >=0.99"
        if latest_date:
            status["detail"] += f", latest {latest_date}"
        if high_95_status["status"] != "live" or high_99_status["status"] != "live":
            status["status"] = "degraded"
            status["detail"] += "; threshold query degraded"
    return {
        "rows": rows,
        "high_95": high_95,
        "high_99": high_99,
        "max_epss": max_epss,
        "latest_date": latest_date,
    }, status


async def _fetch_un_sanctions(client: httpx.AsyncClient) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    url = "https://scsanctions.un.org/resources/xml/en/consolidated.xml"
    started = time.perf_counter()
    try:
        response = await client.get(
            url,
            headers={"Accept": "application/xml,text/xml,*/*"},
            timeout=XML_SOURCE_TIMEOUT_SECONDS,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        generated_at = _parse_iso_dt(root.attrib.get("dateGenerated"))
        individuals = root.findall(".//INDIVIDUAL")
        entities = root.findall(".//ENTITY")
        cutoff = _utc_now().date() - timedelta(days=30)
        updated_30d = 0
        list_counts: Dict[str, int] = {}

        for node in [*individuals, *entities]:
            list_type = _xml_child_text(node, "UN_LIST_TYPE") or "UNKNOWN"
            list_counts[list_type] = list_counts.get(list_type, 0) + 1
            recent_node_update = False
            listed_on = _parse_iso_dt(_xml_child_text(node, "LISTED_ON"))
            if listed_on and listed_on.date() >= cutoff:
                recent_node_update = True
            for container in node.findall(".//LAST_DAY_UPDATED"):
                for value_node in list(container):
                    update_dt = _parse_iso_dt(str(value_node.text or "").strip())
                    if update_dt and update_dt.date() >= cutoff:
                        recent_node_update = True
                        break
                if recent_node_update:
                    break
            if recent_node_update:
                updated_30d += 1

        total = len(individuals) + len(entities)
        detail = f"{total} entries, {updated_30d} updated 30d"
        if generated_at is not None:
            detail += f", generated {generated_at.date().isoformat()}"
        return (
            {
                "total": total,
                "individuals": len(individuals),
                "entities": len(entities),
                "updated_30d": updated_30d,
                "list_counts": list_counts,
                "generated_at": generated_at.isoformat().replace("+00:00", "Z") if generated_at else "",
            },
            _source(
                "un-sanctions",
                "UN Security Council Sanctions",
                "live",
                records=total,
                detail=detail,
                cadence="daily",
                url=str(response.url),
                latency_ms=latency_ms,
                last_updated=generated_at.isoformat().replace("+00:00", "Z") if generated_at else None,
            ),
        )
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return (
            {"total": 0, "individuals": 0, "entities": 0, "updated_30d": 0, "list_counts": {}, "generated_at": ""},
            _source("un-sanctions", "UN Security Council Sanctions", "degraded", detail=str(exc)[:220], cadence="daily", url=url, latency_ms=latency_ms),
        )


_GROUND_NEWS_FAMILY_GROUPS: Dict[str, Tuple[str, ...]] = {
    "politics": ("diplomacy", "domestic_politics", "law_policy"),
    "security": ("military_security", "civil_unrest", "security_crime"),
    "economy": ("economic_trade",),
    "energy": ("economic_trade", "disaster_environment"),
    "logistics": ("economic_trade", "technology_industry"),
    "science": ("technology_industry",),
    "society": ("civil_unrest", "human_rights_migration", "public_development"),
}


_RAW_NEWS_PATTERNS: Dict[str, str] = {
    "politics": r"(election|minister|president|parliament|government|diplomac|summit|sanction|treaty|cabinet|united nations|nato|brics|g7|g20)",
    "security": r"(war|conflict|attack|military|missile|strike|protest|riot|terror|border|ceasefire|hostage|drone)",
    "energy": r"(oil|gas|energy|power|electricity|opec|pipeline|lng|nuclear|renewable)",
    "logistics": r"(shipping|port|supply chain|semiconductor|trade route|red sea|freight|aviation)",
    "science": r"(cyber|artificial intelligence|semiconductor|chip|technology|satellite|quantum|vulnerability)",
    "society": r"(migration|refugee|food security|health|climate|disaster|earthquake|wildfire|flood|drought)",
}


def _ground_news_counts_for(payload: Dict[str, Any], families: Iterable[str], key: str) -> float:
    family_map = payload.get("families", {}) if isinstance(payload, dict) else {}
    total = 0.0
    for family in families:
        total += float((family_map.get(family) or {}).get(key) or 0)
    return total


def _ground_news_series_for(payload: Dict[str, Any], families: Iterable[str], key: str = "stories") -> List[Dict[str, Any]]:
    rows = payload.get("daily", []) if isinstance(payload, dict) else []
    bucket: Dict[int, float] = {}
    family_set = set(families)
    for row in rows:
        if row.get("event_family") not in family_set:
            continue
        day = row.get("day")
        if not day:
            continue
        if isinstance(day, datetime):
            dt = day.replace(tzinfo=timezone.utc)
        else:
            try:
                dt = datetime.fromisoformat(str(day)[:10]).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        ts = int(dt.timestamp())
        bucket[ts] = bucket.get(ts, 0.0) + float(row.get(key) or 0)
    return [{"time": ts, "value": round(value, 4)} for ts, value in sorted(bucket.items())]


def _raw_news_series_for(payload: Dict[str, Any], category: str) -> List[Dict[str, Any]]:
    rows = payload.get("raw_hourly", []) if isinstance(payload, dict) else []
    points: List[Dict[str, Any]] = []
    for row in rows:
        if row.get("category") != category:
            continue
        bucket = row.get("bucket")
        if isinstance(bucket, datetime):
            dt = bucket.astimezone(timezone.utc)
        else:
            dt = _parse_iso_dt(bucket)
        if dt is not None:
            points.append(_series_point(dt, float(row.get("count") or 0)))
    return points


def _rolling_sum_series(points: List[Dict[str, Any]], *, window: int = 7) -> List[Dict[str, Any]]:
    ordered = sorted(points, key=lambda point: int(point.get("time") or 0))
    values = [float(point.get("value") or 0) for point in ordered]
    rolled: List[Dict[str, Any]] = []
    for index, point in enumerate(ordered):
        start = max(0, index - window + 1)
        rolled.append({"time": point["time"], "value": round(sum(values[start:index + 1]), 4)})
    return rolled


def _with_current_tail(points: List[Dict[str, Any]], current_value: float) -> List[Dict[str, Any]]:
    tail = _series_point(_utc_now(), float(current_value))
    cleaned = [point for point in points if int(point.get("time") or 0) < tail["time"]]
    return [*cleaned, tail]


def _fetch_ground_news_signals_sync() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    run_id = string_setting("FINANCIAL_TERMINAL_GROUND_NEWS_RUN_ID", "fast_l1_v2")
    started = time.perf_counter()
    family_sql = text(
        """
        WITH base AS (
            SELECT
                c.event_family,
                c.article_count,
                COALESCE(sb.source_count, 0) AS source_count,
                COALESCE(sb.analysis_status, 'not_built') AS analysis_status,
                COALESCE(c.end_date, c.start_date) AS story_date
            FROM public.event_coref_clusters AS c
            LEFT JOIN public.story_source_breakdown AS sb
              ON sb.story_id = c.cluster_id
            WHERE c.run_id = :run_id
              AND COALESCE(c.end_date, c.start_date) IS NOT NULL
              AND COALESCE(c.end_date, c.start_date) <= CURRENT_DATE
              AND COALESCE(c.end_date, c.start_date) >= CURRENT_DATE - INTERVAL '14 days'
        )
        SELECT
            event_family,
            COUNT(*) FILTER (WHERE story_date >= CURRENT_DATE - INTERVAL '1 day') AS stories_24h,
            COALESCE(SUM(article_count) FILTER (WHERE story_date >= CURRENT_DATE - INTERVAL '1 day'), 0) AS articles_24h,
            COUNT(*) FILTER (WHERE story_date >= CURRENT_DATE - INTERVAL '7 days') AS stories_7d,
            COALESCE(SUM(article_count) FILTER (WHERE story_date >= CURRENT_DATE - INTERVAL '7 days'), 0) AS articles_7d,
            COUNT(*) FILTER (
                WHERE story_date >= CURRENT_DATE - INTERVAL '7 days'
                  AND source_count >= 2
            ) AS usable_7d,
            COUNT(*) FILTER (
                WHERE story_date >= CURRENT_DATE - INTERVAL '7 days'
                  AND analysis_status = 'ready'
            ) AS ready_7d,
            MAX(story_date) AS latest_story_date
        FROM base
        GROUP BY event_family
        """
    )
    daily_sql = text(
        """
        SELECT
            COALESCE(c.end_date, c.start_date) AS day,
            c.event_family,
            COUNT(*) AS stories,
            COALESCE(SUM(c.article_count), 0) AS articles,
            COUNT(*) FILTER (WHERE COALESCE(sb.source_count, 0) >= 2) AS usable,
            COUNT(*) FILTER (WHERE COALESCE(sb.analysis_status, 'not_built') = 'ready') AS ready
        FROM public.event_coref_clusters AS c
        LEFT JOIN public.story_source_breakdown AS sb
          ON sb.story_id = c.cluster_id
        WHERE c.run_id = :run_id
          AND COALESCE(c.end_date, c.start_date) IS NOT NULL
          AND COALESCE(c.end_date, c.start_date) <= CURRENT_DATE
          AND COALESCE(c.end_date, c.start_date) >= CURRENT_DATE - INTERVAL '14 days'
        GROUP BY day, c.event_family
        ORDER BY day, c.event_family
        """
    )
    raw_case = " ".join(
        f"WHEN text_blob ~* '{pattern}' THEN '{category}'"
        for category, pattern in _RAW_NEWS_PATTERNS.items()
    )
    raw_sql = text(
        f"""
        WITH recent_news AS (
            SELECT
                date_trunc('hour', published_at) AS bucket,
                concat_ws(' ', title, left(COALESCE(body, ''), 600)) AS text_blob
            FROM public.news
            WHERE published_at >= now() - INTERVAL '24 hours'
              AND published_at <= now() + INTERVAL '1 day'
        ),
        tagged AS (
            SELECT
                bucket,
                CASE {raw_case} ELSE NULL END AS category
            FROM recent_news
        )
        SELECT category, bucket, COUNT(*) AS count
        FROM tagged
        WHERE category IS NOT NULL
        GROUP BY category, bucket
        ORDER BY bucket, category
        """
    )
    with _get_l1_engine().connect() as conn:
        family_rows = [dict(row) for row in conn.execute(family_sql, {"run_id": run_id}).mappings()]
        daily_rows = [dict(row) for row in conn.execute(daily_sql, {"run_id": run_id}).mappings()]
        raw_rows = [dict(row) for row in conn.execute(raw_sql).mappings()]

    family_map = {str(row.get("event_family") or "unknown"): row for row in family_rows}
    raw_counts: Dict[str, int] = {}
    for row in raw_rows:
        category = str(row.get("category") or "")
        raw_counts[category] = raw_counts.get(category, 0) + int(row.get("count") or 0)

    total_7d = sum(int(row.get("stories_7d") or 0) for row in family_rows)
    raw_total_24h = sum(raw_counts.values())
    latest_dates = [row.get("latest_story_date") for row in family_rows if row.get("latest_story_date")]
    latest_story_date = max(latest_dates).isoformat() if latest_dates else ""
    latest_raw_dates = [
        parsed
        for parsed in (_parse_iso_dt(row.get("bucket")) for row in raw_rows)
        if parsed is not None
    ]
    observed_dates = [
        parsed
        for parsed in [_parse_iso_dt(latest_story_date), *latest_raw_dates]
        if parsed is not None
    ]
    last_updated = _iso(max(observed_dates)) if observed_dates else None
    latency_ms = int((time.perf_counter() - started) * 1000)
    status = "live" if total_7d or raw_total_24h else "degraded"
    detail = f"{total_7d} clusters/7d, {raw_total_24h} raw articles/24h"
    if latest_story_date:
        detail += f", latest {latest_story_date}"
    return (
        {
            "families": family_map,
            "daily": daily_rows,
            "raw_counts": raw_counts,
            "raw_hourly": raw_rows,
            "total_7d": total_7d,
            "raw_total_24h": raw_total_24h,
            "latest_story_date": latest_story_date,
        },
        _source(
            "ground-news-local",
            "Ground News 本地事件图谱",
            status,
            records=total_7d + raw_total_24h,
            detail=detail,
            cadence="15m-daily",
            url="local://news/event_coref_clusters",
            latency_ms=latency_ms,
            last_updated=last_updated,
        ),
    )


async def _fetch_ground_news_signals() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    try:
        return await asyncio.to_thread(_fetch_ground_news_signals_sync)
    except Exception as exc:
        return (
            {"families": {}, "daily": [], "raw_counts": {}, "raw_hourly": [], "total_7d": 0, "raw_total_24h": 0},
            _source("ground-news-local", "Ground News 本地事件图谱", "degraded", detail=str(exc)[:220], cadence="15m-daily"),
        )


_GDELT_SERIES_KEYWORDS: Dict[str, List[str]] = {
    "politics": ["diplomacy", "minister", "election", "sanction", "summit", "united nations", "g7"],
    "security": ["conflict", "war", "attack", "military", "missile", "strike", "protest"],
    "energy": ["oil", "gas", "energy", "power", "electricity", "opec"],
    "logistics": ["shipping", "port", "supply chain", "semiconductor", "red sea", "trade"],
    "science": ["ai", "artificial intelligence", "semiconductor", "chip", "technology", "cyber"],
    "society": ["migration", "refugee", "food", "health", "climate", "protest"],
}


def _gdelt_series(articles: List[Dict[str, Any]], category: str) -> List[Dict[str, Any]]:
    keywords = _GDELT_SERIES_KEYWORDS.get(category, [])
    samples: List[Tuple[datetime, float]] = []
    for article in articles:
        if not any(keyword in _article_text(article) for keyword in keywords):
            continue
        dt = _parse_gdelt_dt(article.get("seendate"))
        if dt is not None:
            samples.append((dt, 1.0))
    return _bucket_samples(samples, bucket_count=12, bucket_size=timedelta(hours=2), aggregation="count")


def _opensky_activity_series(rows: List[Any], *, airborne_only: bool) -> List[Dict[str, Any]]:
    samples: List[Tuple[datetime, float]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) <= 8:
            continue
        if airborne_only and row[8] is not False:
            continue
        dt = _parse_iso_dt(row[4] or row[3])
        if dt is not None:
            samples.append((dt, 1.0))
    return _bucket_samples(samples, bucket_count=12, bucket_size=timedelta(minutes=30), aggregation="count")


def _usgs_metric_series(features: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    event_samples: List[Tuple[datetime, float]] = []
    mag_samples: List[Tuple[datetime, float]] = []
    for feature in features:
        props = feature.get("properties", {}) if isinstance(feature, dict) else {}
        dt = _parse_iso_dt((props.get("time") or 0) / 1000 if props.get("time") else None)
        if dt is None:
            continue
        mag = float(props.get("mag") or 0.0)
        event_samples.append((dt, 1.0))
        mag_samples.append((dt, mag))
    return (
        _bucket_samples(event_samples, bucket_count=12, bucket_size=timedelta(hours=2), aggregation="count"),
        _bucket_samples(mag_samples, bucket_count=12, bucket_size=timedelta(hours=2), aggregation="max"),
    )


def _nvd_series(vulnerabilities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    samples: List[Tuple[datetime, float]] = []
    for row in vulnerabilities:
        cve = row.get("cve", {}) if isinstance(row, dict) else {}
        dt = _parse_iso_dt(cve.get("published"))
        if dt is not None:
            samples.append((dt, 1.0))
    return _bucket_samples(samples, bucket_count=7, bucket_size=timedelta(days=1), aggregation="count")


def _cisa_series(vulnerabilities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    samples: List[Tuple[datetime, float]] = []
    for row in vulnerabilities:
        dt = _parse_iso_dt(row.get("dateAdded"))
        if dt is not None:
            samples.append((dt, 1.0))
    return _bucket_samples(samples, bucket_count=10, bucket_size=timedelta(days=3), aggregation="count")


def _eia_series(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    points: List[Dict[str, Any]] = []
    for row in reversed(rows):
        try:
            dt = datetime.strptime(str(row.get("period")), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            value = float(row.get("value"))
        except Exception:
            continue
        points.append(_series_point(dt, value))
    return points


def _openaq_series(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    samples: List[Tuple[datetime, float]] = []
    for row in results:
        for measurement in row.get("measurements", []) or []:
            if str(measurement.get("parameter", "")).lower() != "pm25":
                continue
            dt = (
                _parse_iso_dt(measurement.get("lastUpdated"))
                or _parse_iso_dt(measurement.get("datetime"))
                or _parse_iso_dt((measurement.get("period") or {}).get("datetimeFrom", {}).get("utc"))
                or _parse_iso_dt((row.get("datetimeLast") or {}).get("utc"))
            )
            try:
                value = float(measurement.get("value"))
            except Exception:
                continue
            if dt is not None:
                samples.append((dt, value))
    return _bucket_samples(samples, bucket_count=12, bucket_size=timedelta(hours=2), aggregation="avg")


def _firms_series(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    samples: List[Tuple[datetime, float]] = []
    for row in rows:
        date_s = str(row.get("acq_date") or "").strip()
        time_s = str(row.get("acq_time") or "").strip().zfill(4)
        if not date_s:
            continue
        try:
            dt = datetime.strptime(f"{date_s} {time_s[:2]}:{time_s[2:]}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        samples.append((dt, 1.0))
    return _bucket_samples(samples, bucket_count=12, bucket_size=timedelta(hours=2), aggregation="count")


def _eonet_series(events: List[Dict[str, Any]], category_id: Optional[str] = None) -> List[Dict[str, Any]]:
    samples: List[Tuple[datetime, float]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        if category_id:
            ids = {str(category.get("id") or "") for category in event.get("categories", []) or []}
            if category_id not in ids:
                continue
        geometries = event.get("geometry", []) or []
        dates = [_parse_iso_dt(geometry.get("date")) for geometry in geometries if isinstance(geometry, dict)]
        dates = [dt for dt in dates if dt is not None]
        if dates:
            samples.append((max(dates), 1.0))
    return _bucket_samples(samples, bucket_count=14, bucket_size=timedelta(hours=12), aggregation="count")


def _finalize_metric(definition: Dict[str, Any]) -> Dict[str, Any]:
    points = _prefer_points(definition["id"], definition.get("points", []), definition.get("current_value", 0.0))
    latest = _latest_value(points, float(definition.get("current_value", 0.0)))
    change_pct = definition.get("change_pct")
    if change_pct is None:
        change_pct = _series_change_pct(points)
    return {
        "id": definition["id"],
        "kind": definition.get("kind", "metric"),
        "label": definition["label"],
        "unit": definition.get("unit", ""),
        "source": definition.get("source", ""),
        "cadence": definition.get("cadence", ""),
        "status": definition.get("status", "live"),
        "category": definition.get("category"),
        "region": definition.get("region"),
        "description": definition.get("description", ""),
        "points": points,
        "latest": latest,
        "change_pct": round(float(change_pct or 0.0), 2),
    }


def _index_card_from_metric(card_id: str, name: str, metric: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": card_id,
        "name": name,
        "value": round(float(metric.get("latest") or 0.0), 2),
        "change_pct": round(float(metric.get("change_pct") or 0.0), 2),
        "spark": _spark_from_points(metric.get("points", [])),
        "source": metric.get("source"),
        "metric_id": metric["id"],
    }


def _watch_row_from_metric(metric: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "symbol": metric["id"],
        "metric_id": metric["id"],
        "label": metric["label"],
        "price": round(float(metric.get("latest") or 0.0), 2),
        "change_pct": round(float(metric.get("change_pct") or 0.0), 2),
        "category": metric.get("category"),
        "region": metric.get("region"),
        "source": metric.get("source"),
        "cadence": metric.get("cadence"),
        "status": metric.get("status"),
        "unit": metric.get("unit"),
        "description": metric.get("description"),
    }


async def build_dashboard(refresh: bool = False) -> Dict[str, Any]:
    global _DASHBOARD_CACHE, _DASHBOARD_BUILD_TASK, _DASHBOARD_REFRESH_TASK
    now_mono = time.monotonic()
    if not refresh and _DASHBOARD_CACHE:
        if now_mono < _DASHBOARD_CACHE[0]:
            return _apply_cached_dashboard(_DASHBOARD_CACHE[1], cache_state="hit")
        if _DASHBOARD_REFRESH_TASK is None or _DASHBOARD_REFRESH_TASK.done():
            _DASHBOARD_REFRESH_TASK = asyncio.create_task(build_dashboard(refresh=True))
        return _apply_cached_dashboard(_DASHBOARD_CACHE[1], cache_state="stale")

    if not refresh:
        shared_hit = _read_shared_dashboard_cache()
        if shared_hit is not None:
            ttl_remaining, payload = shared_hit
            _DASHBOARD_CACHE = (now_mono + ttl_remaining, payload)
            return _apply_cached_dashboard(payload, cache_state="shared")

    if not refresh:
        if _DASHBOARD_BUILD_TASK is None or _DASHBOARD_BUILD_TASK.done():
            _DASHBOARD_BUILD_TASK = asyncio.create_task(build_dashboard(refresh=True))
        task = _DASHBOARD_BUILD_TASK
        try:
            payload = await task
            return _apply_cached_dashboard(payload, cache_state="coalesced")
        finally:
            if _DASHBOARD_BUILD_TASK is task and task.done():
                _DASHBOARD_BUILD_TASK = None

    headers = {
        "User-Agent": "GlobeMind/1.0 (+https://globemind.top; world-state terminal)",
        "Accept": "application/json,text/plain,*/*",
    }
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers=headers, follow_redirects=True, trust_env=False) as client:
        gdelt_task = _fetch_gdelt(client)
        local_story_task = _fetch_ground_news_signals()
        active_worldbank_specs = [spec for spec in WORLD_BANK_SPECS if spec["key"] in CORE_WORLD_BANK_KEYS]
        active_openalex_specs = [spec for spec in OPENALEX_SPECS if spec["key"] in CORE_OPENALEX_KEYS]
        worldbank_groups: Dict[str, List[Dict[str, str]]] = {}
        for spec in active_worldbank_specs:
            worldbank_groups.setdefault(spec["indicator"], []).append(spec)
        worldbank_tasks = [
            _fetch_worldbank_indicator_batch(client, indicator, specs)
            for indicator, specs in worldbank_groups.items()
        ]
        openalex_tasks = [
            _fetch_openalex_count(client, spec["query"], spec["source_id"], spec["name"])
            for spec in active_openalex_specs
        ]
        other_tasks = [
            (_fetch_opensky(client), SOURCE_TIMEOUT_SECONDS),
            (_fetch_usgs(client), SOURCE_TIMEOUT_SECONDS),
            (_fetch_nvd(client), NVD_TIMEOUT_SECONDS),
            (_fetch_cisa_kev(client), SOURCE_TIMEOUT_SECONDS),
            (_fetch_noaa_kp(client), SOURCE_TIMEOUT_SECONDS),
            (_fetch_eonet(client), SOURCE_TIMEOUT_SECONDS),
            (_fetch_gdacs(client), XML_SOURCE_TIMEOUT_SECONDS),
            (_fetch_epss(client), SOURCE_TIMEOUT_SECONDS),
            (_fetch_un_sanctions(client), XML_SOURCE_TIMEOUT_SECONDS),
            (_fetch_eia(client), SOURCE_TIMEOUT_SECONDS),
            (_fetch_openaq(client), SOURCE_TIMEOUT_SECONDS),
            (_fetch_firms(client), SOURCE_TIMEOUT_SECONDS),
        ]
        async def _bounded(coro, timeout_seconds: float = SOURCE_TIMEOUT_SECONDS):
            return await asyncio.wait_for(coro, timeout=timeout_seconds)

        scheduled_tasks: List[Tuple[Any, float]] = [
            (gdelt_task, GDELT_TIMEOUT_SECONDS),
            (local_story_task, LOCAL_STORY_TIMEOUT_SECONDS),
        ]
        scheduled_tasks.extend((task, WORLD_BANK_TIMEOUT_SECONDS) for task in worldbank_tasks)
        scheduled_tasks.extend((task, SOURCE_TIMEOUT_SECONDS) for task in openalex_tasks)
        scheduled_tasks.extend(other_tasks)
        results = await asyncio.gather(
            *[_bounded(coro, timeout_seconds) for coro, timeout_seconds in scheduled_tasks],
            return_exceptions=True,
        )

    sources: List[Dict[str, Any]] = []

    def _unwrap(index: int, default: Any) -> Any:
        result = results[index]
        if isinstance(result, Exception):
            return default
        return result

    def _result_error(index: int, fallback: str = "failed") -> str:
        result = results[index]
        if isinstance(result, Exception):
            detail = str(result).strip()
            return detail[:220] if detail else result.__class__.__name__
        return fallback

    gdelt_payload, gdelt_status = _unwrap(0, ({"counts": {}, "articles": []}, _source("gdelt", "GDELT 2.1 DOC", "degraded", detail=_result_error(0))))
    sources.append(gdelt_status)

    local_story_payload, local_story_status = _unwrap(
        1,
        (
            {"families": {}, "daily": [], "raw_counts": {}, "raw_hourly": [], "total_7d": 0, "raw_total_24h": 0},
            _source("ground-news-local", "Ground News 本地事件图谱", "degraded", detail=_result_error(1), cadence="15m-daily"),
        ),
    )
    sources.append(local_story_status)

    cursor = 2
    worldbank_payloads: Dict[str, Dict[str, Any]] = {}
    worldbank_statuses: Dict[str, Dict[str, Any]] = {}
    for indicator, specs in worldbank_groups.items():
        payload_map, status_map = _unwrap(
            cursor,
            ({}, {}),
        )
        for spec in specs:
            payload = payload_map.get(spec["key"], {"latest": None, "points": []}) if isinstance(payload_map, dict) else {"latest": None, "points": []}
            status = status_map.get(spec["key"]) if isinstance(status_map, dict) else None
            if not status:
                status = _source(f"worldbank-{spec['key']}", spec["name"], "degraded", detail=_result_error(cursor, f"{indicator} batch failed"))
            worldbank_payloads[spec["key"]] = payload
            worldbank_statuses[spec["key"]] = status
            sources.append(status)
        cursor += 1

    openalex_payloads: Dict[str, Dict[str, Any]] = {}
    openalex_statuses: Dict[str, Dict[str, Any]] = {}
    for spec in active_openalex_specs:
        payload, status = _unwrap(
            cursor,
            ({"latest": 0, "points": []}, _source(spec["source_id"], spec["name"], "degraded")),
        )
        openalex_payloads[spec["key"]] = payload
        openalex_statuses[spec["key"]] = status
        sources.append(status)
        cursor += 1

    opensky_payload, opensky_status = _unwrap(cursor, ({"states": 0, "airborne": 0, "rows": []}, _source("opensky", "OpenSky Network", "degraded")))
    cursor += 1
    usgs_payload, usgs_status = _unwrap(cursor, ({"count": 0, "max_mag": 0.0, "features": []}, _source("usgs-earthquake", "USGS Earthquake", "degraded")))
    cursor += 1
    nvd_payload, nvd_status = _unwrap(cursor, ({"recent": 0, "vulnerabilities": []}, _source("nvd", "NVD CVE", "degraded")))
    cursor += 1
    cisa_payload, cisa_status = _unwrap(cursor, ({"recent": 0, "vulnerabilities": []}, _source("cisa-kev", "CISA KEV", "degraded")))
    cursor += 1
    noaa_payload, noaa_status = _unwrap(cursor, ({"latest": None, "points": []}, _source("noaa-kp", "NOAA SWPC Kp", "degraded")))
    cursor += 1
    eonet_payload, eonet_status = _unwrap(cursor, ({"latest": 0, "events": [], "category_counts": {}}, _source("nasa-eonet", "NASA EONET", "degraded")))
    cursor += 1
    gdacs_payload, gdacs_status = _unwrap(cursor, ({"recent_24h": 0, "high_24h": 0, "alerts": [], "level_counts": {}, "points": [], "high_points": []}, _source("gdacs", "GDACS Disaster Alerts", "degraded", detail=_result_error(cursor), cadence="15m")))
    cursor += 1
    epss_payload, epss_status = _unwrap(cursor, ({"rows": [], "high_95": 0, "high_99": 0, "max_epss": 0.0, "latest_date": ""}, _source("first-epss", "FIRST EPSS", "degraded", detail=_result_error(cursor), cadence="daily")))
    cursor += 1
    un_sanctions_payload, un_sanctions_status = _unwrap(cursor, ({"total": 0, "individuals": 0, "entities": 0, "updated_30d": 0, "list_counts": {}, "generated_at": ""}, _source("un-sanctions", "UN Security Council Sanctions", "degraded", detail=_result_error(cursor), cadence="daily")))
    cursor += 1
    eia_payload, eia_status = _unwrap(cursor, ({"latest": None, "rows": []}, _source("eia", "EIA Open Data", "disabled")))
    cursor += 1
    openaq_payload, openaq_status = _unwrap(cursor, ({"latest": None, "results": []}, _source("openaq", "OpenAQ", "disabled")))
    cursor += 1
    firms_payload, firms_status = _unwrap(cursor, ({"latest": None, "rows": []}, _source("nasa-firms", "NASA FIRMS", "disabled")))
    sources.extend([opensky_status, usgs_status, nvd_status, cisa_status, noaa_status, eonet_status, gdacs_status, epss_status, un_sanctions_status, eia_status, openaq_status, firms_status])

    gdelt_counts = gdelt_payload.get("counts", {}) if isinstance(gdelt_payload, dict) else {}
    gdelt_articles = gdelt_payload.get("articles", []) if isinstance(gdelt_payload, dict) else []
    local_raw_counts = local_story_payload.get("raw_counts", {}) if isinstance(local_story_payload, dict) else {}
    local_politics_24h = _ground_news_counts_for(local_story_payload, _GROUND_NEWS_FAMILY_GROUPS["politics"], "stories_24h")
    local_security_24h = _ground_news_counts_for(local_story_payload, _GROUND_NEWS_FAMILY_GROUPS["security"], "stories_24h")
    local_energy_24h = _ground_news_counts_for(local_story_payload, _GROUND_NEWS_FAMILY_GROUPS["energy"], "stories_24h")
    local_logistics_24h = _ground_news_counts_for(local_story_payload, _GROUND_NEWS_FAMILY_GROUPS["logistics"], "stories_24h")
    local_science_24h = _ground_news_counts_for(local_story_payload, _GROUND_NEWS_FAMILY_GROUPS["science"], "stories_24h")
    local_society_24h = _ground_news_counts_for(local_story_payload, _GROUND_NEWS_FAMILY_GROUPS["society"], "stories_24h")
    local_politics_7d = _ground_news_counts_for(local_story_payload, _GROUND_NEWS_FAMILY_GROUPS["politics"], "stories_7d")
    local_security_7d = _ground_news_counts_for(local_story_payload, _GROUND_NEWS_FAMILY_GROUPS["security"], "stories_7d")
    local_economy_7d = _ground_news_counts_for(local_story_payload, _GROUND_NEWS_FAMILY_GROUPS["economy"], "stories_7d")
    local_energy_7d = _ground_news_counts_for(local_story_payload, _GROUND_NEWS_FAMILY_GROUPS["energy"], "stories_7d")
    local_logistics_7d = _ground_news_counts_for(local_story_payload, _GROUND_NEWS_FAMILY_GROUPS["logistics"], "stories_7d")
    local_science_7d = _ground_news_counts_for(local_story_payload, _GROUND_NEWS_FAMILY_GROUPS["science"], "stories_7d")
    local_society_7d = _ground_news_counts_for(local_story_payload, _GROUND_NEWS_FAMILY_GROUPS["society"], "stories_7d")
    gdp_value = worldbank_payloads.get("gdp", {}).get("latest")
    inflation_value = worldbank_payloads.get("inflation", {}).get("latest")
    trade_value = float(worldbank_payloads.get("trade", {}).get("latest") or 0)
    military_value = float(worldbank_payloads.get("military", {}).get("latest") or 0)
    electric_value = float(worldbank_payloads.get("electricity", {}).get("latest") or 0)
    openalex_count = float(openalex_payloads.get("tech", {}).get("latest") or 0)
    openalex_total = sum(float(payload.get("latest") or 0) for payload in openalex_payloads.values())
    nvd_count = float(nvd_payload.get("recent") or 0)
    cisa_recent = float(cisa_payload.get("recent") or 0)
    kp_value = float(noaa_payload.get("latest") or 0)
    eonet_total = float(eonet_payload.get("latest") or 0)
    gdacs_alerts = float(gdacs_payload.get("recent_24h") or 0)
    gdacs_high = float(gdacs_payload.get("high_24h") or 0)
    epss_high_95 = float(epss_payload.get("high_95") or 0)
    epss_high_99 = float(epss_payload.get("high_99") or 0)
    epss_max = float(epss_payload.get("max_epss") or 0)
    un_sanctions_updated = float(un_sanctions_payload.get("updated_30d") or 0)
    eia_price = eia_payload.get("latest")
    openaq_pm25 = openaq_payload.get("latest")
    firms_count = firms_payload.get("latest")

    politics_signal = (
        float(gdelt_counts.get("politics", 0))
        + float(local_raw_counts.get("politics", 0))
        + local_politics_24h
        + local_politics_7d / 12
        + un_sanctions_updated / 4
    )
    security_signal = (
        float(gdelt_counts.get("security", 0))
        + float(local_raw_counts.get("security", 0))
        + local_security_24h
        + local_security_7d / 12
        + un_sanctions_updated / 4
        + usgs_payload.get("max_mag", 0) * 2
        + military_value * 2
    )
    energy_signal = (
        float(gdelt_counts.get("energy", 0))
        + float(local_raw_counts.get("energy", 0))
        + local_energy_24h
        + local_energy_7d / 18
        + (eia_price or 0) / 18
        + electric_value / 1200
    )
    logistics_signal = (
        float(gdelt_counts.get("logistics", 0))
        + float(local_raw_counts.get("logistics", 0))
        + local_logistics_24h
        + local_logistics_7d / 18
        + opensky_payload.get("states", 0) / 900
        + trade_value / 18
    )
    science_signal = (
        float(gdelt_counts.get("science", 0))
        + float(local_raw_counts.get("science", 0))
        + local_science_24h
        + local_science_7d / 18
        + openalex_total / 38000
        + nvd_count / 25
        + cisa_recent
        + epss_high_95 / 80
        + epss_high_99 / 60
        + kp_value / 2
    )
    society_signal = (
        float(gdelt_counts.get("society", 0))
        + float(local_raw_counts.get("society", 0))
        + local_society_24h
        + local_society_7d / 18
        + (openaq_pm25 or 0) / 6
        + (firms_count or 0) / 500
        + usgs_payload.get("count", 0) / 30
        + eonet_total / 40
        + gdacs_alerts / 5
        + gdacs_high * 2
    )

    politics = _score_from_count(politics_signal, base=34, scale=13)
    security = _score_from_count(security_signal, base=38, scale=12)
    energy = _score_from_count(energy_signal, base=36, scale=12)
    logistics = _score_from_count(logistics_signal, base=35, scale=13)
    science = _score_from_count(science_signal, base=34, scale=12)
    society = _score_from_count(society_signal, base=34, scale=12)
    macro = 48.0
    if gdp_value is not None:
        macro += max(-10, min(10, float(gdp_value) * 2.2))
    if inflation_value is not None:
        macro += max(0, min(15, float(inflation_value) * 1.1))
    macro = round(max(15, min(95, macro)), 2)
    politics_series = _gdelt_series(gdelt_articles, "politics")
    security_news_series = _gdelt_series(gdelt_articles, "security")
    energy_news_series = _gdelt_series(gdelt_articles, "energy")
    logistics_news_series = _gdelt_series(gdelt_articles, "logistics")
    science_news_series = _gdelt_series(gdelt_articles, "science")
    society_news_series = _gdelt_series(gdelt_articles, "society")
    ground_politics_series = _ground_news_series_for(local_story_payload, _GROUND_NEWS_FAMILY_GROUPS["politics"])
    ground_security_series = _ground_news_series_for(local_story_payload, _GROUND_NEWS_FAMILY_GROUPS["security"])
    ground_economy_series = _ground_news_series_for(local_story_payload, _GROUND_NEWS_FAMILY_GROUPS["economy"])
    ground_energy_series = _ground_news_series_for(local_story_payload, _GROUND_NEWS_FAMILY_GROUPS["energy"])
    ground_logistics_series = _ground_news_series_for(local_story_payload, _GROUND_NEWS_FAMILY_GROUPS["logistics"])
    ground_science_series = _ground_news_series_for(local_story_payload, _GROUND_NEWS_FAMILY_GROUPS["science"])
    ground_society_series = _ground_news_series_for(local_story_payload, _GROUND_NEWS_FAMILY_GROUPS["society"])
    raw_politics_series = _raw_news_series_for(local_story_payload, "politics")
    raw_security_series = _raw_news_series_for(local_story_payload, "security")
    raw_energy_series = _raw_news_series_for(local_story_payload, "energy")
    raw_logistics_series = _raw_news_series_for(local_story_payload, "logistics")
    raw_science_series = _raw_news_series_for(local_story_payload, "science")
    raw_society_series = _raw_news_series_for(local_story_payload, "society")
    ground_politics_7d_series = _with_current_tail(_rolling_sum_series(ground_politics_series), local_politics_7d)
    ground_security_7d_series = _with_current_tail(_rolling_sum_series(ground_security_series), local_security_7d)
    ground_economy_7d_series = _with_current_tail(_rolling_sum_series(ground_economy_series), local_economy_7d)
    ground_energy_7d_series = _with_current_tail(_rolling_sum_series(ground_energy_series), local_energy_7d)
    ground_logistics_7d_series = _with_current_tail(_rolling_sum_series(ground_logistics_series), local_logistics_7d)
    ground_science_7d_series = _with_current_tail(_rolling_sum_series(ground_science_series), local_science_7d)
    ground_society_7d_series = _with_current_tail(_rolling_sum_series(ground_society_series), local_society_7d)
    politics_signal_series = _combine_on_anchor(
        _pick_anchor(politics_series, raw_politics_series, ground_politics_series),
        [politics_series, raw_politics_series, ground_politics_series],
        lambda gdelt, raw, local_story: gdelt + raw + local_story / 12,
    )
    security_signal_series = _combine_on_anchor(
        _pick_anchor(security_news_series, raw_security_series, ground_security_series),
        [security_news_series, raw_security_series, ground_security_series],
        lambda gdelt, raw, local_story: gdelt + raw + local_story / 12,
    )
    energy_signal_series = _combine_on_anchor(
        _pick_anchor(energy_news_series, raw_energy_series, ground_energy_series),
        [energy_news_series, raw_energy_series, ground_energy_series],
        lambda gdelt, raw, local_story: gdelt + raw + local_story / 18,
    )
    logistics_signal_series = _combine_on_anchor(
        _pick_anchor(logistics_news_series, raw_logistics_series, ground_logistics_series),
        [logistics_news_series, raw_logistics_series, ground_logistics_series],
        lambda gdelt, raw, local_story: gdelt + raw + local_story / 18,
    )
    science_signal_series = _combine_on_anchor(
        _pick_anchor(science_news_series, raw_science_series, ground_science_series),
        [science_news_series, raw_science_series, ground_science_series],
        lambda gdelt, raw, local_story: gdelt + raw + local_story / 18,
    )
    society_signal_series = _combine_on_anchor(
        _pick_anchor(society_news_series, raw_society_series, ground_society_series),
        [society_news_series, raw_society_series, ground_society_series],
        lambda gdelt, raw, local_story: gdelt + raw + local_story / 18,
    )
    opensky_states_series = _opensky_activity_series(opensky_payload.get("rows", []), airborne_only=False)
    opensky_airborne_series = _opensky_activity_series(opensky_payload.get("rows", []), airborne_only=True)
    usgs_count_series, usgs_mag_series = _usgs_metric_series(usgs_payload.get("features", []))
    nvd_history_series = _nvd_series(nvd_payload.get("vulnerabilities", []))
    cisa_history_series = _cisa_series(cisa_payload.get("vulnerabilities", []))
    eia_history_series = _eia_series(eia_payload.get("rows", []))
    openaq_history_series = _openaq_series(openaq_payload.get("results", []))
    firms_history_series = _firms_series(firms_payload.get("rows", []))
    eonet_all_series = _eonet_series(eonet_payload.get("events", []))
    gdacs_alerts_series = _with_current_tail(gdacs_payload.get("points", []), gdacs_alerts)
    gdacs_high_series = _with_current_tail(gdacs_payload.get("high_points", []), gdacs_high)
    epss_high_series = _with_current_tail(_history_points("EPSS-HIGH"), epss_high_95)
    epss_top_series = _with_current_tail(_history_points("EPSS-TOP"), epss_max * 100)
    un_sanctions_update_series = _with_current_tail(_history_points("UNSAN-UPD30"), un_sanctions_updated)
    un_sanctions_total_series = _with_current_tail(_history_points("UNSAN-TOTAL"), float(un_sanctions_payload.get("total") or 0))
    noaa_kp_series = noaa_payload.get("points", [])
    gdp_series = worldbank_payloads.get("gdp", {}).get("points", [])
    inflation_series = worldbank_payloads.get("inflation", {}).get("points", [])
    military_series = worldbank_payloads.get("military", {}).get("points", [])
    trade_series = worldbank_payloads.get("trade", {}).get("points", [])
    electricity_series = worldbank_payloads.get("electricity", {}).get("points", [])

    politics_index_series = _combine_on_anchor(
        _pick_anchor(politics_signal_series, un_sanctions_update_series),
        [politics_signal_series, un_sanctions_update_series],
        lambda news, sanctions_updates: _score_from_count(news + sanctions_updates / 4, base=34, scale=13),
    )
    security_index_series = _combine_on_anchor(
        _pick_anchor(security_signal_series, usgs_mag_series, un_sanctions_update_series),
        [security_signal_series, usgs_mag_series, military_series, un_sanctions_update_series],
        lambda news, max_mag, military_share, sanctions_updates: _score_from_count(news + max_mag * 2 + military_share * 2 + sanctions_updates / 4, base=38, scale=12),
    )
    energy_index_series = _combine_on_anchor(
        _pick_anchor(eia_history_series, energy_signal_series),
        [eia_history_series, energy_signal_series, electricity_series],
        lambda brent, news, electricity: _score_from_count(news + brent / 18 + electricity / 1200, base=36, scale=12),
    )
    logistics_index_series = _combine_on_anchor(
        _pick_anchor(opensky_states_series, logistics_signal_series),
        [opensky_states_series, logistics_signal_series, trade_series],
        lambda states_count, news, trade_share: _score_from_count(news + states_count / 900 + trade_share / 18, base=35, scale=13),
    )
    science_index_series = _combine_on_anchor(
        _pick_anchor(nvd_history_series, cisa_history_series, science_signal_series, noaa_kp_series, epss_high_series),
        [nvd_history_series, cisa_history_series, science_signal_series, noaa_kp_series, epss_high_series],
        lambda nvd_value, cisa_value, news, kp, epss_high: _score_from_count(news + openalex_total / 38000 + nvd_value / 25 + cisa_value + epss_high / 80 + kp / 2, base=34, scale=12),
    )
    society_index_series = _combine_on_anchor(
        _pick_anchor(usgs_count_series, openaq_history_series, firms_history_series, society_signal_series, eonet_all_series, gdacs_alerts_series, gdacs_high_series),
        [usgs_count_series, openaq_history_series, firms_history_series, society_signal_series, eonet_all_series, gdacs_alerts_series, gdacs_high_series],
        lambda eq_count, pm25_value, fire_count, news, natural_events, gdacs_events, gdacs_impact: _score_from_count(news + pm25_value / 6 + fire_count / 500 + eq_count / 30 + natural_events / 10 + gdacs_events / 5 + gdacs_impact * 2, base=34, scale=12),
    )
    macro_index_series = _combine_on_anchor(
        _pick_anchor(gdp_series, inflation_series),
        [gdp_series, inflation_series],
        lambda gdp_growth, inflation_growth: max(15, min(95, 48 + max(-10, min(10, gdp_growth * 2.2)) + max(0, min(15, inflation_growth * 1.1)))),
    )
    world_index_series = _combine_on_anchor(
        _pick_anchor(politics_index_series, security_index_series, energy_index_series, logistics_index_series, science_index_series, society_index_series),
        [politics_index_series, security_index_series, energy_index_series, logistics_index_series, science_index_series, society_index_series, macro_index_series],
        lambda politics_v, security_v, energy_v, logistics_v, science_v, society_v, macro_v: calculate_extracted_wsi(
            [
                politics_v,
                security_v,
                energy_v,
                logistics_v,
                science_v,
                society_v,
                macro_v,
            ]
        ),
    )
    world = round(
        _latest_value(
            world_index_series,
            round(
                calculate_extracted_wsi(
                    [politics, security, energy, logistics, science, society, macro]
                ),
                2,
            ),
        ),
        2,
    )

    metric_definitions = [
        {"id": "IDX-WSI", "kind": "index", "label": "世界状态综合指数", "current_value": world, "points": world_index_series, "unit": "指数", "source": "Ground News/GDELT/OpenSky/USGS/EIA/World Bank/NOAA/EONET/GDACS/EPSS/UN Sanctions", "cadence": "15m-annual", "status": "live", "category": "politics", "region": "全球", "description": "综合本地事件图谱、外交、安全、能源、物流、科技、社会、自然事件、空间天气、制裁和宏观数据的派生指数。"},
        {"id": "IDX-MACRO", "kind": "index", "label": "宏观经济压力", "current_value": macro, "points": macro_index_series, "unit": "指数", "source": "World Bank", "cadence": "annual", "status": worldbank_statuses.get("gdp", {}).get("status", "live"), "category": "economy", "region": "全球", "description": "全球 GDP、价格压力和主要经济体年度指标折算。"},
        {"id": "IDX-DIPLOMACY", "kind": "index", "label": "外交温度", "current_value": politics, "points": politics_index_series, "unit": "指数", "source": "Ground News/GDELT", "cadence": "15m-daily", "status": "live" if local_story_status["status"] == "live" or gdelt_status["status"] == "live" else "degraded", "category": "politics", "region": "全球", "description": "按本地事件图谱、原始新闻和 GDELT 近 24 小时政治外交密度折算。"},
        {"id": "IDX-SECURITY", "kind": "index", "label": "冲突安全压力", "current_value": security, "points": security_index_series, "unit": "指数", "source": "Ground News/GDELT/USGS/World Bank/UN Sanctions", "cadence": "15m-annual", "status": "live" if local_story_status["status"] == "live" or gdelt_status["status"] == "live" or usgs_status["status"] == "live" or un_sanctions_status["status"] == "live" else "degraded", "category": "security", "region": "全球", "description": "按本地冲突事件、GDELT 安全新闻、地震扰动、制裁更新和军费基线共同折算。"},
        {"id": "IDX-ENERGY", "kind": "index", "label": "能源压力", "current_value": energy, "points": energy_index_series, "unit": "指数", "source": "Ground News/EIA/GDELT", "cadence": "daily", "status": "live" if local_story_status["status"] == "live" or eia_status["status"] == "live" or gdelt_status["status"] == "live" else "degraded", "category": "energy", "region": "全球", "description": "Brent 现货、能源新闻与本地事件图谱压力共同折算。"},
        {"id": "IDX-SUPPLY", "kind": "index", "label": "供应链扰动", "current_value": logistics, "points": logistics_index_series, "unit": "指数", "source": "Ground News/OpenSky/GDELT", "cadence": "30m", "status": "live" if local_story_status["status"] == "live" or opensky_status["status"] == "live" or gdelt_status["status"] == "live" else "degraded", "category": "logistics", "region": "全球", "description": "航空活跃度、供应链新闻与本地事件图谱波动共同折算。"},
        {"id": "IDX-TECH", "kind": "index", "label": "科技政策波动", "current_value": science, "points": science_index_series, "unit": "指数", "source": "Ground News/OpenAlex/NVD/CISA/EPSS/GDELT", "cadence": "daily", "status": "live" if local_story_status["status"] == "live" or nvd_status["status"] == "live" or cisa_status["status"] == "live" or epss_status["status"] == "live" else "degraded", "category": "science", "region": "全球", "description": "文献、漏洞、利用概率、科技新闻与本地事件图谱共同折算。"},
        {"id": "IDX-SOCIETY", "kind": "index", "label": "社会舆论热度", "current_value": society, "points": society_index_series, "unit": "指数", "source": "Ground News/USGS/OpenAQ/NASA FIRMS/GDELT/GDACS", "cadence": "2h", "status": "live" if local_story_status["status"] == "live" or usgs_status["status"] == "live" or gdelt_status["status"] == "live" or gdacs_status["status"] == "live" else "degraded", "category": "society", "region": "全球", "description": "地震、空气、火点、GDACS 灾害告警、社会议题新闻与本地事件图谱共同折算。"},
        {"id": "GDELT-POL", "label": "全球政治外交新闻事件量", "current_value": float(gdelt_counts.get("politics", 0)), "points": politics_series, "unit": "条/24h", "source": "GDELT 24h", "cadence": "2h", "status": gdelt_status["status"], "category": "politics", "region": "全球", "description": "按近 24 小时 GDELT 文章桶统计的政治外交相关事件量。"},
        {"id": "GDELT-SEC", "label": "冲突与抗议新闻事件量", "current_value": float(gdelt_counts.get("security", 0)), "points": security_news_series, "unit": "条/24h", "source": "GDELT 24h", "cadence": "2h", "status": gdelt_status["status"], "category": "security", "region": "全球", "description": "冲突、攻击、军事、抗议相关事件桶统计。"},
        {"id": "GN-POL-7D", "label": "本地政治外交事件簇", "current_value": float(local_politics_7d), "points": ground_politics_7d_series, "unit": "簇/7d", "source": "Ground News 本地事件图谱", "cadence": "daily", "status": local_story_status["status"], "category": "politics", "region": "全球", "description": "近 7 天外交、国内政治和政策法规事件簇数量。", "change_pct": _recent_change_pct(ground_politics_7d_series)},
        {"id": "GN-POL-24H", "label": "本地政治事件 24h", "current_value": float(local_politics_24h + float(local_raw_counts.get("politics", 0))), "points": _with_current_tail(politics_signal_series, local_politics_24h + float(local_raw_counts.get("politics", 0))), "unit": "条/24h", "source": "Ground News 本地事件图谱", "cadence": "15m-daily", "status": local_story_status["status"], "category": "politics", "region": "全球", "description": "本地事件簇与近 24 小时原始新闻关键词共同形成的政治热度。", "change_pct": 0.0},
        {"id": "GN-SEC-7D", "label": "本地安全冲突事件簇", "current_value": float(local_security_7d), "points": ground_security_7d_series, "unit": "簇/7d", "source": "Ground News 本地事件图谱", "cadence": "daily", "status": local_story_status["status"], "category": "security", "region": "全球", "description": "近 7 天军事安全、抗议动荡和安全犯罪事件簇数量。", "change_pct": _recent_change_pct(ground_security_7d_series)},
        {"id": "GN-SEC-24H", "label": "本地安全冲突 24h", "current_value": float(local_security_24h + float(local_raw_counts.get("security", 0))), "points": _with_current_tail(security_signal_series, local_security_24h + float(local_raw_counts.get("security", 0))), "unit": "条/24h", "source": "Ground News 本地事件图谱", "cadence": "15m-daily", "status": local_story_status["status"], "category": "security", "region": "全球", "description": "本地事件簇与近 24 小时原始新闻关键词共同形成的安全冲突热度。", "change_pct": 0.0},
        {"id": "UNSAN-UPD30", "label": "联合国制裁近 30 日更新", "current_value": un_sanctions_updated, "points": un_sanctions_update_series, "unit": "项", "source": "UN Security Council Sanctions", "cadence": "daily", "status": un_sanctions_status["status"], "category": "security", "region": "全球", "description": "联合国安理会综合制裁清单中近 30 日新增或更新的对象数量。", "change_pct": _recent_change_pct(un_sanctions_update_series)},
        {"id": "UNSAN-TOTAL", "label": "联合国制裁清单对象", "current_value": float(un_sanctions_payload.get("total") or 0), "points": un_sanctions_total_series, "unit": "项", "source": "UN Security Council Sanctions", "cadence": "daily", "status": un_sanctions_status["status"], "category": "security", "region": "全球", "description": "联合国安理会综合制裁清单中的个人与实体总量。", "change_pct": _recent_change_pct(un_sanctions_total_series)},
        {"id": "GN-ECON-7D", "label": "本地经贸事件簇", "current_value": float(local_economy_7d), "points": ground_economy_7d_series, "unit": "簇/7d", "source": "Ground News 本地事件图谱", "cadence": "daily", "status": local_story_status["status"], "category": "economy", "region": "全球", "description": "近 7 天经济与贸易事件簇数量。", "change_pct": _recent_change_pct(ground_economy_7d_series)},
        {"id": "GN-ENERGY-7D", "label": "本地能源资源事件簇", "current_value": float(local_energy_7d), "points": ground_energy_7d_series, "unit": "簇/7d", "source": "Ground News 本地事件图谱", "cadence": "daily", "status": local_story_status["status"], "category": "energy", "region": "全球", "description": "近 7 天能源、经贸和灾害环境相关事件簇数量。", "change_pct": _recent_change_pct(ground_energy_7d_series)},
        {"id": "GN-LOG-7D", "label": "本地供应链事件簇", "current_value": float(local_logistics_7d), "points": ground_logistics_7d_series, "unit": "簇/7d", "source": "Ground News 本地事件图谱", "cadence": "daily", "status": local_story_status["status"], "category": "logistics", "region": "全球", "description": "近 7 天经贸、科技产业和供应链相关事件簇数量。", "change_pct": _recent_change_pct(ground_logistics_7d_series)},
        {"id": "GN-TECH-7D", "label": "本地科技产业事件簇", "current_value": float(local_science_7d), "points": ground_science_7d_series, "unit": "簇/7d", "source": "Ground News 本地事件图谱", "cadence": "daily", "status": local_story_status["status"], "category": "science", "region": "全球", "description": "近 7 天科技产业事件簇数量。", "change_pct": _recent_change_pct(ground_science_7d_series)},
        {"id": "GN-SOC-7D", "label": "本地社会民生事件簇", "current_value": float(local_society_7d), "points": ground_society_7d_series, "unit": "簇/7d", "source": "Ground News 本地事件图谱", "cadence": "daily", "status": local_story_status["status"], "category": "society", "region": "全球", "description": "近 7 天抗议、人权迁移和公共发展事件簇数量。", "change_pct": _recent_change_pct(ground_society_7d_series)},
        {"id": "WB-GDP", "label": "全球 GDP 增速基线", "current_value": float(gdp_value if gdp_value is not None else 0), "points": gdp_series, "unit": "%", "source": "World Bank", "cadence": "annual", "status": worldbank_statuses.get("gdp", {}).get("status", "degraded"), "category": "economy", "region": "全球", "description": "世界银行公布的全球 GDP 实际增速。"},
        {"id": "WB-CPI", "label": "全球通胀压力基线", "current_value": float(inflation_value if inflation_value is not None else 0), "points": inflation_series, "unit": "%", "source": "World Bank", "cadence": "annual", "status": worldbank_statuses.get("inflation", {}).get("status", "degraded"), "category": "economy", "region": "全球", "description": "世界银行 GDP deflator 近年变化。"},
        {"id": "OS-AIR", "label": "全球 ADS-B 航空状态量", "current_value": float(opensky_payload.get("states", 0)), "points": opensky_states_series, "unit": "架", "source": "OpenSky", "cadence": "30m", "status": opensky_status["status"], "category": "logistics", "region": "全球", "description": "按最近被捕获时间回溯的航空状态分布。"},
        {"id": "OS-FLY", "label": "空中飞行器数量", "current_value": float(opensky_payload.get("airborne", 0)), "points": opensky_airborne_series, "unit": "架", "source": "OpenSky", "cadence": "30m", "status": opensky_status["status"], "category": "logistics", "region": "全球", "description": "当前空中飞行器及近 6 小时活跃分布。"},
        {"id": "OA-TECH", "label": "AI/半导体论文产出热度", "current_value": float(openalex_count), "points": openalex_payloads.get("tech", {}).get("points", []), "unit": "篇/30d", "source": "OpenAlex 30d", "cadence": "daily", "status": openalex_statuses.get("tech", {}).get("status", "degraded"), "category": "science", "region": "全球", "description": "当前接口返回 30 天累计量，历史样本由系统持续累积。"},
        {"id": "NVD-CVE", "label": "近 7 日 CVE 发布量", "current_value": float(nvd_count), "points": nvd_history_series, "unit": "条", "source": "NVD", "cadence": "daily", "status": nvd_status["status"], "category": "science", "region": "全球", "description": "NVD 近 7 日公开 CVE 数量，按日分桶。"},
        {"id": "CISA-KEV", "label": "近 30 日已利用漏洞新增", "current_value": float(cisa_recent), "points": cisa_history_series, "unit": "条", "source": "CISA KEV", "cadence": "3d", "status": cisa_status["status"], "category": "science", "region": "美国/全球", "description": "CISA 已知被利用漏洞清单新增项，按 3 日分桶。"},
        {"id": "EPSS-HIGH", "label": "高利用概率漏洞总量", "current_value": epss_high_95, "points": epss_high_series, "unit": "个", "source": "FIRST EPSS", "cadence": "daily", "status": epss_status["status"], "category": "science", "region": "全球", "description": "FIRST EPSS 全库中利用概率不低于 0.95 的 CVE 数量。", "change_pct": _recent_change_pct(epss_high_series)},
        {"id": "EPSS-TOP", "label": "最高漏洞利用概率", "current_value": epss_max * 100, "points": epss_top_series, "unit": "%", "source": "FIRST EPSS", "cadence": "daily", "status": epss_status["status"], "category": "science", "region": "全球", "description": "FIRST EPSS 当前样本中的最高漏洞利用概率。", "change_pct": _recent_change_pct(epss_top_series)},
        {"id": "NOAA-KP", "label": "全球地磁活动 Kp 指数", "current_value": float(kp_value), "points": noaa_kp_series, "unit": "Kp", "source": "NOAA SWPC", "cadence": "3h", "status": noaa_status["status"], "category": "science", "region": "全球", "description": "NOAA SWPC 行星 K 指数，反映近地空间天气扰动。"},
        {"id": "USGS-EQ", "label": "近 24 小时地震事件量", "current_value": float(usgs_payload.get("count", 0)), "points": usgs_count_series, "unit": "次", "source": "USGS", "cadence": "2h", "status": usgs_status["status"], "category": "society", "region": "全球", "description": "USGS all_day feed 地震事件按 2 小时分桶。"},
        {"id": "USGS-MAG", "label": "近 24 小时最高震级", "current_value": float(usgs_payload.get("max_mag", 0)), "points": usgs_mag_series, "unit": "Mw", "source": "USGS", "cadence": "2h", "status": usgs_status["status"], "category": "society", "region": "全球", "description": "各时间桶内最高震级。"},
        {"id": "EONET-ACTIVE", "label": "全球开放自然事件", "current_value": float(eonet_payload.get("latest") or 0), "points": [], "unit": "个", "source": "NASA EONET", "cadence": "daily", "status": eonet_status["status"], "category": "society", "region": "全球", "description": "NASA EONET 当前开放自然事件数。"},
        {"id": "EONET-STORM", "label": "开放风暴事件", "current_value": float(eonet_payload.get("category_counts", {}).get("severeStorms", 0)), "points": [], "unit": "个", "source": "NASA EONET", "cadence": "daily", "status": eonet_status["status"], "category": "society", "region": "全球", "description": "EONET severeStorms 类别当前开放事件。"},
        {"id": "EONET-FIRE", "label": "开放野火事件", "current_value": float(eonet_payload.get("category_counts", {}).get("wildfires", 0)), "points": [], "unit": "个", "source": "NASA EONET", "cadence": "daily", "status": eonet_status["status"], "category": "society", "region": "全球", "description": "EONET wildfires 类别当前开放事件。"},
        {"id": "EONET-VOLCANO", "label": "开放火山事件", "current_value": float(eonet_payload.get("category_counts", {}).get("volcanoes", 0)), "points": [], "unit": "个", "source": "NASA EONET", "cadence": "daily", "status": eonet_status["status"], "category": "society", "region": "全球", "description": "EONET volcanoes 类别当前开放事件。"},
        {"id": "GDACS-ALERT", "label": "GDACS 24h 灾害告警", "current_value": gdacs_alerts, "points": gdacs_alerts_series, "unit": "个", "source": "GDACS", "cadence": "15m", "status": gdacs_status["status"], "category": "society", "region": "全球", "description": "GDACS 近实时灾害告警按 2 小时分桶。"},
        {"id": "GDACS-HIGH", "label": "GDACS 高影响告警", "current_value": gdacs_high, "points": gdacs_high_series, "unit": "个", "source": "GDACS", "cadence": "15m", "status": gdacs_status["status"], "category": "society", "region": "全球", "description": "GDACS Orange/Red 等级高影响灾害告警。"},
        {"id": "EIA-BRENT", "label": "Brent 原油现货", "current_value": float(eia_price if eia_price is not None else 0), "points": eia_history_series, "unit": "USD/bbl", "source": "EIA", "cadence": "daily", "status": eia_status["status"], "category": "energy", "region": "全球", "description": "EIA 公布的 Brent 现货日度价格。"},
        {"id": "OAQ-PM25", "label": "空气质量 PM2.5 样本均值", "current_value": float(openaq_pm25 if openaq_pm25 is not None else 0), "points": openaq_history_series, "unit": "ug/m3", "source": "OpenAQ", "cadence": "2h", "status": openaq_status["status"], "category": "society", "region": "全球", "description": "OpenAQ 最新样本中的 PM2.5 平均值，按时间分桶。"},
        {"id": "FIRMS-FIRE", "label": "全球卫星火点数量", "current_value": float(firms_count if firms_count is not None else 0), "points": firms_history_series, "unit": "个", "source": "NASA FIRMS", "cadence": "2h", "status": firms_status["status"], "category": "society", "region": "全球", "description": "NASA FIRMS 全球火点按 2 小时分桶。"},
    ]

    existing_metric_ids = {definition["id"] for definition in metric_definitions}
    for spec in active_worldbank_specs:
        metric_id = spec.get("metric_id")
        if not metric_id or metric_id in existing_metric_ids:
            continue
        payload = worldbank_payloads.get(spec["key"], {})
        metric_definitions.append({
            "id": metric_id,
            "label": spec.get("label") or spec["name"],
            "current_value": float(payload.get("latest") if payload.get("latest") is not None else 0),
            "points": payload.get("points", []),
            "unit": spec.get("unit", ""),
            "source": "World Bank",
            "cadence": "annual",
            "status": worldbank_statuses.get(spec["key"], {}).get("status", "degraded"),
            "category": spec.get("category"),
            "region": spec.get("region", "全球"),
            "description": spec.get("description", ""),
        })
        existing_metric_ids.add(metric_id)

    for spec in active_openalex_specs:
        metric_id = spec.get("metric_id")
        if not metric_id or metric_id in existing_metric_ids:
            continue
        payload = openalex_payloads.get(spec["key"], {})
        metric_definitions.append({
            "id": metric_id,
            "label": spec.get("label") or spec["name"],
            "current_value": float(payload.get("latest") or 0),
            "points": payload.get("points", []),
            "unit": "篇/30d",
            "source": "OpenAlex 30d",
            "cadence": "daily",
            "status": openalex_statuses.get(spec["key"], {}).get("status", "degraded"),
            "category": spec.get("category"),
            "region": "全球",
            "description": spec.get("description", ""),
        })
        existing_metric_ids.add(metric_id)

    build_evaluated_at = _utc_now()
    build_last_updated = _iso(build_evaluated_at)
    preflight_trust = assess_dashboard_trust(
        {"last_updated": build_last_updated, "sources": sources},
        cache_state="miss",
        now=build_evaluated_at,
    )
    for definition in metric_definitions:
        is_composite = str(definition.get("id") or "").startswith("IDX-")
        if _should_record_metric_history(definition) and (
            not is_composite or preflight_trust["computable"]
        ):
            _append_history_point(definition["id"], float(definition.get("current_value") or 0.0))

    series = [_finalize_metric(definition) for definition in metric_definitions]
    series_map = {item["id"]: item for item in series}
    indices = [
        _index_card_from_metric("wsi", "世界状态综合", series_map["IDX-WSI"]),
        _index_card_from_metric("macro", "宏观经济压力", series_map["IDX-MACRO"]),
        _index_card_from_metric("diplomacy", "外交温度", series_map["IDX-DIPLOMACY"]),
        _index_card_from_metric("security", "冲突安全压力", series_map["IDX-SECURITY"]),
        _index_card_from_metric("energy", "能源压力", series_map["IDX-ENERGY"]),
        _index_card_from_metric("supply", "供应链扰动", series_map["IDX-SUPPLY"]),
        _index_card_from_metric("tech", "科技政策波动", series_map["IDX-TECH"]),
        _index_card_from_metric("society", "社会舆论热度", series_map["IDX-SOCIETY"]),
    ]
    watchlist = [
        _watch_row_from_metric(metric)
        for metric in series
        if metric.get("kind") != "index"
    ]

    wsi_chart_points = _fresh_chart_points(
        series_map["IDX-WSI"]["points"],
        [
            series_map["IDX-DIPLOMACY"]["points"],
            series_map["IDX-SECURITY"]["points"],
            series_map["GN-POL-24H"]["points"],
            series_map["GN-SEC-24H"]["points"],
        ],
    )
    bars = _bars_from_series(wsi_chart_points)
    coverage = {
        "series_total": len(series),
        "watchlist_total": len(watchlist),
        "sources_total": len(sources),
        "live_sources": sum(1 for item in sources if item.get("status") == "live"),
        "degraded_sources": sum(1 for item in sources if item.get("status") == "degraded"),
        "disabled_sources": sum(1 for item in sources if item.get("status") == "disabled"),
        "politics_security_series": sum(1 for item in series if item.get("category") in {"politics", "security"}),
        "near_realtime_series": sum(
            1
            for item in series
            if any(token in str(item.get("cadence") or "").lower() for token in ("15m", "30m", "2h", "3h", "5m", "near-real-time"))
        ),
        "ground_news_records": int(local_story_payload.get("total_7d") or 0) + int(local_story_payload.get("raw_total_24h") or 0),
        "ground_news_latest_story_date": local_story_payload.get("latest_story_date") or "",
        "chart_latest_age_days": _latest_point_age_days(wsi_chart_points),
        "chart_points": len(wsi_chart_points),
    }

    payload = {
        "mode": "live",
        "cache": "miss",
        "last_updated": build_last_updated,
        "bars": bars,
        "ma20": _rolling_ma(bars, 20),
        "ma50": _rolling_ma(bars, 50),
        "ma200": _rolling_ma(bars, 200),
        "indices": indices,
        "watchlist": watchlist,
        "series": series,
        "default_metric_id": "IDX-WSI",
        "sources": sources,
        "alert_rules": [],
        "coverage": coverage,
    }
    trusted_payload = apply_dashboard_trust_gate(
        payload,
        cache_state="miss",
        now=build_evaluated_at,
    )
    if dashboard_is_computable(trusted_payload):
        trusted_payload["alert_rules"] = build_alert_rules_from_dashboard(
            trusted_payload["indices"],
            trusted_payload["watchlist"],
        )
    _DASHBOARD_CACHE = (now_mono + CACHE_TTL_SECONDS, trusted_payload)
    _write_shared_dashboard_cache(trusted_payload)
    return trusted_payload


def build_alert_rules_from_dashboard(indices: List[Dict[str, Any]], watchlist: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    index_map = {item["id"]: item for item in indices}
    row_map = {item["symbol"]: item for item in watchlist}
    candidates = [
        ("world-state", "世界状态综合指数", "%", index_map.get("wsi", {}).get("value", 0), 72, 58, "high"),
        ("security", "冲突与安全压力", "%", index_map.get("security", {}).get("value", 0), 65, 45, "medium"),
        ("logistics", "全球物流活跃异常", "index", row_map.get("OS-AIR", {}).get("price", 0), 9000, 7200, "medium"),
        ("tech", "科技与网络安全热度", "index", index_map.get("tech", {}).get("value", 0), 68, 48, "medium"),
        ("earthquake", "地震事件活跃度", "events", row_map.get("USGS-EQ", {}).get("price", 0), 120, 55, "low"),
        ("energy", "能源压力指数", "%", index_map.get("energy", {}).get("value", 0), 70, 52, "medium"),
    ]
    rules: List[Dict[str, Any]] = []
    for rule_id, metric, unit, current, threshold, baseline, default_severity in candidates:
        current_f = float(current or 0)
        breached = current_f >= threshold
        severity = "high" if breached and default_severity in {"high", "medium"} else default_severity
        if not breached and current_f >= threshold * 0.82:
            severity = "medium"
        elif not breached:
            severity = "low"
        rules.append({
            "id": rule_id,
            "metric": metric,
            "unit": unit,
            "current": round(current_f, 2),
            "threshold": threshold,
            "baseline": baseline,
            "severity": severity,
            "breached": breached,
            "trend": "up" if current_f >= baseline else "down",
        })
    return rules


async def get_dashboard(refresh: bool = False) -> Dict[str, Any]:
    return await build_dashboard(refresh=refresh)


async def get_indices(refresh: bool = False) -> List[Dict[str, Any]]:
    return (await get_dashboard(refresh=refresh))["indices"]


async def get_watchlist(refresh: bool = False) -> List[Dict[str, Any]]:
    return (await get_dashboard(refresh=refresh))["watchlist"]


async def get_alert_rules(refresh: bool = False) -> List[Dict[str, Any]]:
    return (await get_dashboard(refresh=refresh))["alert_rules"]


async def get_sources(refresh: bool = False) -> List[Dict[str, Any]]:
    return (await get_dashboard(refresh=refresh))["sources"]
