#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import numpy as np
import psycopg2
from db_runtime_config import require_database_password

_REPO = Path(__file__).resolve().parent.parent
_DATA_DIR = _REPO / "data"
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
_CHECKPOINT_V13 = _DATA_DIR / "checkpoint_v13_all.jsonl"
_CHECKPOINT_V12 = _DATA_DIR / "checkpoint_v12_geopolitical.jsonl"
_CHECKPOINT_V11 = _DATA_DIR / "checkpoint_v11_240k.jsonl"
DEFAULT_MAPPING_PATH = _DATA_DIR / "event_coref_mapping_layer1.jsonl"


@dataclass
class ArticleRecord:
    article_id: int
    cluster_id: str
    event_type: str
    initiator: str
    target: str
    entity_pair_key: str
    published_at: Optional[str]
    published_dt: Optional[datetime]
    embedding: Optional[np.ndarray] = None
    title: str = ""
    source: str = ""
    abstract: str = ""


def get_db_config() -> Dict[str, Any]:
    return {
        "host": os.getenv("PG_HOST", "192.168.207.171"),
        "port": int(os.getenv("PG_PORT", "54333")),
        "dbname": "globemind_news",
        "user": os.getenv("PG_WRITE_USER", "postgres"),
        "password": require_database_password(),
        "connect_timeout": 15,
    }


def select_checkpoint_path(path: Optional[str] = None) -> Path:
    if path:
        return Path(path)
    for candidate in (_CHECKPOINT_V13, _CHECKPOINT_V12, _CHECKPOINT_V11):
        if candidate.exists():
            return candidate
    raise FileNotFoundError("No checkpoint file found")


def parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = datetime.fromisoformat(text.replace(" ", "T"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def load_cluster_mapping(path: str | Path = DEFAULT_MAPPING_PATH) -> tuple[dict[str, list[int]], dict[int, str]]:
    clusters: dict[str, list[int]] = defaultdict(list)
    article_to_cluster: dict[int, str] = {}
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            cluster_id = str(row["cluster_id"])
            article_id = int(row["article_id"])
            clusters[cluster_id].append(article_id)
            article_to_cluster[article_id] = cluster_id
    return dict(clusters), article_to_cluster


def load_checkpoint_records(
    article_ids: set[int],
    *,
    article_to_cluster: dict[int, str],
    checkpoint_path: str | Path | None = None,
) -> dict[int, ArticleRecord]:
    from core_pipeline.entity_normalizer import entity_pair_key

    path = select_checkpoint_path(str(checkpoint_path) if checkpoint_path else None)
    records: dict[int, ArticleRecord] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            article_id = int(row.get("article_id") or 0)
            if article_id not in article_ids:
                continue
            event = row.get("event") or {}
            if event.get("domain") != "geopolitical":
                continue
            initiator = str(event.get("initiator") or "")
            target = str(event.get("target") or "")
            published_at = row.get("published_at")
            records[article_id] = ArticleRecord(
                article_id=article_id,
                cluster_id=article_to_cluster[article_id],
                event_type=str(event.get("event_type") or "other"),
                initiator=initiator,
                target=target,
                entity_pair_key=entity_pair_key(initiator, target),
                published_at=str(published_at) if published_at is not None else None,
                published_dt=parse_dt(published_at),
            )
    return records


def load_embeddings(
    article_ids: Iterable[int],
    *,
    db_config: Optional[dict[str, Any]] = None,
) -> dict[int, np.ndarray]:
    db_conf = db_config or get_db_config()
    article_set = {int(article_id) for article_id in article_ids}
    if not article_set:
        return {}

    embeddings: dict[int, np.ndarray] = {}
    conn = psycopg2.connect(**db_conf)
    try:
        cur = conn.cursor()
        ids = list(article_set)
        for start in range(0, len(ids), 5000):
            batch = ids[start : start + 5000]
            cur.execute(
                """
                SELECT news_id, embedding
                FROM news_embeddings
                WHERE news_id = ANY(%s)
                  AND model IN ('bge-m3', 'BAAI/bge-m3')
                """,
                (batch,),
            )
            for news_id, raw in cur.fetchall():
                if isinstance(raw, memoryview):
                    raw = bytes(raw)
                if isinstance(raw, bytes):
                    raw = raw.decode()
                if isinstance(raw, str):
                    raw = json.loads(raw)
                embeddings[int(news_id)] = np.asarray(raw, dtype=np.float32)
    finally:
        conn.close()
    return embeddings


def load_news_metadata(
    article_ids: Iterable[int],
    *,
    db_config: Optional[dict[str, Any]] = None,
) -> dict[int, dict[str, str]]:
    db_conf = db_config or get_db_config()
    article_set = {int(article_id) for article_id in article_ids}
    if not article_set:
        return {}

    rows: dict[int, dict[str, str]] = {}
    conn = psycopg2.connect(**db_conf)
    try:
        cur = conn.cursor()
        ids = list(article_set)
        for start in range(0, len(ids), 5000):
            batch = ids[start : start + 5000]
            cur.execute(
                """
                SELECT id,
                       COALESCE(title, ''),
                       COALESCE(media_source_name, media_source_domain, source_dataset_name, ''),
                       COALESCE(abstract, '')
                FROM news
                WHERE id = ANY(%s)
                """,
                (batch,),
            )
            for article_id, title, source, abstract in cur.fetchall():
                rows[int(article_id)] = {
                    "title": str(title or ""),
                    "source": str(source or ""),
                    "abstract": str(abstract or ""),
                }
    finally:
        conn.close()
    return rows


def hydrate_records(
    records: dict[int, ArticleRecord],
    *,
    include_embeddings: bool = True,
    include_news_metadata: bool = False,
    db_config: Optional[dict[str, Any]] = None,
) -> dict[int, ArticleRecord]:
    if include_embeddings:
        embeddings = load_embeddings(records.keys(), db_config=db_config)
        for article_id, embedding in embeddings.items():
            if article_id in records:
                records[article_id].embedding = embedding
    if include_news_metadata:
        metadata_map = load_news_metadata(records.keys(), db_config=db_config)
        for article_id, metadata in metadata_map.items():
            if article_id not in records:
                continue
            records[article_id].title = metadata["title"]
            records[article_id].source = metadata["source"]
            records[article_id].abstract = metadata["abstract"]
    return records


def cosine_similarity(vec1: np.ndarray, vec2: np.ndarray) -> float:
    denom = float(np.linalg.norm(vec1) * np.linalg.norm(vec2))
    if denom <= 1e-12:
        return 0.0
    return float(np.dot(vec1, vec2) / denom)


def time_delta_days(left: ArticleRecord, right: ArticleRecord) -> Optional[int]:
    if left.published_dt is None or right.published_dt is None:
        return None
    return abs((left.published_dt - right.published_dt).days)
