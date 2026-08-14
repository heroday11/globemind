#!/usr/bin/env python3
"""
全量提取 v3：分类器预筛选 + LLM 精确提取。

流程：
  1. 用 domain 分类器（TF-IDF+LR）筛选所有 241K 文章
  2. 对候选人（~32K）运行 LLM 提取（7 字段含 trigger_verb/location/tone）
  3. 合并已有 v12 数据
  4. 输出 checkpoint_v13_all.jsonl

用法：
  VLLM_BASE_URL=http://127.0.0.1:8004 .env_torch/bin/python3 scripts/extract_all_v12.py
"""
from __future__ import annotations

import asyncio, json, logging, os, sys, time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO)); sys.path.insert(0, str(_REPO / "backend"))
try:
    from dotenv import load_dotenv
    load_dotenv(_REPO / "backend" / "agentic_rag" / ".env", override=False)
except ImportError: pass

import joblib, numpy as np, psycopg2
from core_pipeline.event_extract_v11 import extract_batch, MAX_INPUT_CHARS, USER_TEMPLATE
from scripts.db_runtime_config import require_database_password

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("extract_all_v3")

CHECKPOINT_V11 = _REPO / "data" / "checkpoint_v11_240k.jsonl"
CHECKPOINT_V12 = _REPO / "data" / "checkpoint_v12_geopolitical.jsonl"
CHECKPOINT_V13 = _REPO / "data" / "checkpoint_v13_all.jsonl"

MODEL_DIR = _REPO / "data" / "models"
CLASSIFIER_PATH = MODEL_DIR / "domain_classifier_lr.joblib"
VECTORIZER_PATH = MODEL_DIR / "domain_tfidf_lr.joblib"

CLASSIFIER_THRESHOLD = 0.30  # strict v13: recall≈98.3%, precision≈42.3%; material-pool precision≈77.4%
LLM_CONCURRENT = 200

DB_HOST = os.getenv("PG_HOST", "192.168.207.171")
DB_PORT = int(os.getenv("PG_PORT", "54333"))
DB_NAME = "globemind_news"
DB_USER = os.getenv("PG_WRITE_USER", "postgres")
DB_PASSWORD = require_database_password()

def classify_all_articles() -> set[int]:
    """用 domain 分类器筛选出所有可能是 geopolitical 的文章。"""
    logger.info("加载分类器...")
    model = joblib.load(str(CLASSIFIER_PATH))
    tfidf = joblib.load(str(VECTORIZER_PATH))

    logger.info("加载 v11 检查点...")
    article_texts = {}
    all_ids = []
    with open(CHECKPOINT_V11) as f:
        for line in f:
            d = json.loads(line)
            all_ids.append(d["article_id"])
            # text 会在下一步从 DB 获取
            article_texts[d["article_id"]] = ""

    logger.info("从 DB 加载正文...")
    conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
                            user=DB_USER, password=DB_PASSWORD, connect_timeout=15)
    cur = conn.cursor()
    texts = []
    for start in range(0, len(all_ids), 5000):
        batch = all_ids[start:start+5000]
        cur.execute("SELECT id, COALESCE(title,'') || ' ' || LEFT(COALESCE(body,''),500) FROM news WHERE id = ANY(%s)", (batch,))
        rows = {r[0]: r[1] for r in cur.fetchall()}
        for aid in batch:
            t = rows.get(aid, "")
            article_texts[aid] = t
            texts.append(t)
    conn.close()
    logger.info("DB 加载完成: %d 篇", len(texts))

    # 分类
    t0 = time.time()
    X = tfidf.transform(texts)
    probs = model.predict_proba(X)[:, 1]
    logger.info("分类完成: %.1fs", time.time() - t0)

    candidates = set()
    for i, aid in enumerate(all_ids):
        if probs[i] >= CLASSIFIER_THRESHOLD:
            candidates.add(aid)
    logger.info("筛选结果: %d/%d 候选人 (%.1f%%)",
                len(candidates), len(all_ids), 100*len(candidates)/max(len(all_ids),1))
    return candidates

async def main():
    # 1. 分类器筛选
    candidates = classify_all_articles()

    # 2. 合并已有 v12 数据
    already_done = set()
    if CHECKPOINT_V12.exists():
        with open(CHECKPOINT_V12) as f:
            for line in f:
                already_done.add(json.loads(line)["article_id"])
        logger.info("已有 v12 数据: %d 篇", len(already_done))

    # 3. 需要 LLM 提取的：候选人中未完成的
    to_extract_ids = candidates - already_done
    logger.info("需要 LLM 提取: %d 篇 (候选人 %d - 已有 %d)",
                len(to_extract_ids), len(candidates), len(already_done))

    # 4. 加载正文
    logger.info("加载正文...")
    conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
                            user=DB_USER, password=DB_PASSWORD, connect_timeout=15)
    cur = conn.cursor()
    to_extract_ids_list = list(to_extract_ids)
    articles_for_llm = []
    for start in range(0, len(to_extract_ids_list), 5000):
        batch = to_extract_ids_list[start:start+5000]
        cur.execute("SELECT id, published_at, COALESCE(title,'') || ' ' || COALESCE(body,'') FROM news WHERE id = ANY(%s)", (batch,))
        rows = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        for aid in batch:
            if aid in rows:
                articles_for_llm.append({
                    "id": aid,
                    "published_at": str(rows[aid][0]) if rows[aid][0] else None,
                    "text": (rows[aid][1] or "")[:MAX_INPUT_CHARS],
                })
    conn.close()
    logger.info("正文加载完成: %d 篇", len(articles_for_llm))

    # 5. LLM 提取（写入临时文件）
    if articles_for_llm:
        temp_ckpt = CHECKPOINT_V13.with_suffix(".tmp.jsonl")
        logger.info("开始 LLM 提取 %d 篇...", len(articles_for_llm))
        await extract_batch(articles_for_llm, str(temp_ckpt), text_field="text", max_concurrent=LLM_CONCURRENT)
    else:
        logger.info("没有需要 LLM 提取的文章")

    # 6. 合并 → v13
    logger.info("合并数据...")
    written = set()
    with open(CHECKPOINT_V13, "w") as out:
        # 先写 v12 已有数据
        if CHECKPOINT_V12.exists():
            with open(CHECKPOINT_V12) as f:
                for line in f:
                    d = json.loads(line)
                    out.write(line)
                    written.add(d["article_id"])
        # 再写新提取数据
        temp_ckpt = CHECKPOINT_V13.with_suffix(".tmp.jsonl")
        if temp_ckpt.exists():
            with open(temp_ckpt) as f:
                for line in f:
                    d = json.loads(line)
                    if d["article_id"] not in written:
                        out.write(line)
                    written.add(d["article_id"])
            temp_ckpt.unlink()
        # 最后写 v11 的 general_news（无新字段，但完整）
        with open(CHECKPOINT_V11) as f:
            for line in f:
                d = json.loads(line)
                if d["article_id"] not in written:
                    out.write(line)
                    written.add(d["article_id"])

    # 统计
    gp, tv, loc = 0, 0, 0
    with open(CHECKPOINT_V13) as f:
        total = sum(1 for _ in f)
    with open(CHECKPOINT_V13) as f:
        for line in f:
            d = json.loads(line); ev = d.get("event")
            if ev:
                if ev.get("domain") == "geopolitical": gp += 1
                if ev.get("trigger_verb"): tv += 1
                if ev.get("location"): loc += 1

    logger.info("")
    logger.info("=" * 60)
    logger.info("  全量提取完成")
    logger.info("=" * 60)
    logger.info("  总文章:     %d", total)
    logger.info("  Geopolitical: %d (%.1f%%)", gp, 100*gp/max(total,1))
    logger.info("  trigger_verb: %d", tv)
    logger.info("  location:     %d", loc)

if __name__ == "__main__":
    asyncio.run(main())
