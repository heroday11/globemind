#!/usr/bin/env python3
"""
重新提取已确定的地缘政治文章（使用新的 7 字段 prompt）。

流程：
  1. 从 checkpoint_v11_240k.jsonl 读取已提取的结果
  2. 过滤出 domain=geopolitical 的文章
  3. 从 DB 加载正文（标题+正文）
  4. 用新 prompt（含 trigger_verb/location/tone）重新提取
  5. 保存到 checkpoint_v12_geopolitical.jsonl

用法：
    python scripts/re_extract_geopolitical.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "backend"))

try:
    from dotenv import load_dotenv
    load_dotenv(_REPO / "backend" / "agentic_rag" / ".env", override=False)
    load_dotenv(_REPO / ".env", override=False)
except ImportError:
    pass

import psycopg2
from core_pipeline.event_extract_v11 import extract_batch, MAX_INPUT_CHARS, USER_TEMPLATE
from scripts.db_runtime_config import require_database_password

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("re_extract")

CHECKPOINT_V11 = _REPO / "data" / "checkpoint_v11_240k.jsonl"
CHECKPOINT_V12 = _REPO / "data" / "checkpoint_v12_geopolitical.jsonl"

DB_HOST = os.getenv("PG_HOST", "192.168.207.171")
DB_PORT = int(os.getenv("PG_PORT", "54333"))
DB_NAME = "globemind_news"
DB_USER = os.getenv("PG_WRITE_USER", "postgres")
DB_PASSWORD = require_database_password()


def load_geopolitical_articles() -> list[dict]:
    articles = []
    with open(CHECKPOINT_V11) as f:
        for line in f:
            d = json.loads(line)
            ev = d.get("event")
            if ev and ev.get("domain") == "geopolitical":
                articles.append({
                    "id": d["article_id"],
                    "published_at": d.get("published_at"),
                })
    logger.info("Loaded %d geopolitical articles from %s", len(articles), CHECKPOINT_V11.name)
    return articles


def load_article_bodies(articles: list[dict]) -> dict[int, str]:
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD, connect_timeout=15,
    )
    cur = conn.cursor()
    bodies: dict[int, str] = {}
    article_ids = [a["id"] for a in articles]
    for start in range(0, len(article_ids), 5000):
        batch = article_ids[start:start + 5000]
        cur.execute(
            "SELECT id, COALESCE(title,'') || ' ' || COALESCE(body,'') FROM news WHERE id = ANY(%s)",
            (batch,),
        )
        for row in cur.fetchall():
            bodies[int(row[0])] = str(row[1] or "")
    cur.close()
    conn.close()
    logger.info("Loaded %d article bodies from DB", len(bodies))
    return bodies


async def main():
    articles = load_geopolitical_articles()
    bodies = load_article_bodies(articles)

    for a in articles:
        body = bodies.get(a["id"], "")
        a["text"] = body[:MAX_INPUT_CHARS]

    logger.info(
        "Starting re-extraction of %d articles with new prompt (max_input=%d, fields: trigger_verb, location, tone)",
        len(articles), MAX_INPUT_CHARS,
    )

    results = await extract_batch(
        articles,
        str(CHECKPOINT_V12),
        text_field="text",
        max_concurrent=80,
    )

    ok = sum(1 for r in results if r.parse_success)
    fail = sum(1 for r in results if not r.parse_success)
    gp = sum(1 for r in results if r.event and r.event.domain == "geopolitical")
    with_trigger = sum(1 for r in results if r.event and r.event.trigger_verb)
    with_location = sum(1 for r in results if r.event and r.event.location)
    with_tone_neutral = sum(1 for r in results if r.event and r.event.tone == "neutral")
    with_tone_positive = sum(1 for r in results if r.event and r.event.tone == "positive")
    with_tone_negative = sum(1 for r in results if r.event and r.event.tone == "negative")

    logger.info("")
    logger.info("=" * 60)
    logger.info("  Re-extraction Complete")
    logger.info("=" * 60)
    logger.info("  Total:        %d", len(results))
    logger.info("  OK:           %d (%.1f%%)", ok, 100 * ok / max(len(results), 1))
    logger.info("  Failed:       %d (%.1f%%)", fail, 100 * fail / max(len(results), 1))
    logger.info("  Geopolitical: %d (%.1f%%)", gp, 100 * gp / max(ok, 1))
    logger.info("  New fields:")
    logger.info("    trigger_verb:  %d/%d (%.1f%%)", with_trigger, ok, 100 * with_trigger / max(ok, 1))
    logger.info("    location:      %d/%d (%.1f%%)", with_location, ok, 100 * with_location / max(ok, 1))
    logger.info("    tone:          neutral=%d, positive=%d, negative=%d",
                with_tone_neutral, with_tone_positive, with_tone_negative)
    logger.info("  Saved to:     %s", CHECKPOINT_V12)


if __name__ == "__main__":
    asyncio.run(main())
