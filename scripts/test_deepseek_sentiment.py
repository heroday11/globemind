#!/usr/bin/env python
"""
DeepSeek 情感标注测试（10 条）。

用法:
    python scripts/test_deepseek_sentiment.py
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import time

import openai

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "backend"))

from agentic_rag.db.connection import get_conn  # noqa: E402

API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-v4-flash"
CONCURRENCY = 100

SYSTEM_PROMPT = """你是一名地缘政治新闻情感分析专家。请分析以下新闻的情感倾向。

评分标准（-10 ~ +10）：
-10 ~ -7: 极其负面（战争、大规模冲突、人道灾难、严重偏见抹黑性报道）
-6 ~ -3: 较负面（制裁、外交紧张、争议事件、带有偏见的报道）
-2 ~ +2: 中性 / 客观报道（事实陈述、无明显情感倾向）
+3 ~ +6: 较正面（合作协议、外交突破、积极进展）
+7 ~ +10: 极其正面（和平条约、重大合作成果、历史性突破）

特别注意：
- 识别新闻中是否包含偏见抹黑（bias/smear）、双标（double standards）或煽动性语言
- 即使新闻表面上在报道"事实"，如果语言带有明显的倾向性、选择性负面强调，应给予相应的负面分数
- 例如：无端指责、以偏概全、使用情感化负面词汇的描述，都应被判为负面

请只输出一个整数分数，不要任何其他文字。"""


async def analyze_one(client: openai.AsyncOpenAI, title: str, content: str, idx: int) -> dict:
    text = f"标题：{title}\n正文：{content[:200]}"
    try:
        resp = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            temperature=0.01,
            max_tokens=5,
            extra_body={"thinking": {"type": "disabled"}},
        )
        raw = (resp.choices[0].message.content or "").strip()
        nums = re.findall(r"-?\d+", raw)
        score = int(nums[0]) if nums else 0
        score = max(-10, min(10, score))
        return {"idx": idx, "score": score, "raw": raw, "error": None}
    except Exception as e:
        return {"idx": idx, "score": 0, "raw": "", "error": str(e)}


async def main():
    if not API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY is required")
    conn = get_conn("globemind_news", autocommit=True, connect_timeout=15)
    cur = conn.cursor()

    cur.execute("""
        SELECT DISTINCT n.id, n.title,
               LEFT(COALESCE(n.body, n.abstract, ''), 200) AS content
        FROM event_coref_members ec
        JOIN news n ON n.id = ec.news_id
        JOIN news_ai_analysis na ON na.news_id = n.id
        WHERE na.event_domain = 'geopolitical'
        LIMIT 10
    """)
    rows = cur.fetchall()
    print(f"取到 {len(rows)} 条新闻\n")

    client = openai.AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL)
    sem = asyncio.Semaphore(CONCURRENCY)

    async def bounded_analyze(title, content, idx):
        async with sem:
            return await analyze_one(client, title, content, idx)

    t0 = time.perf_counter()
    tasks = [bounded_analyze(r[1] or "", r[2] or "", i) for i, r in enumerate(rows)]
    results = await asyncio.gather(*tasks)
    elapsed = time.perf_counter() - t0

    print(f"{'ID':>10} {'分数':>4} {'标题'}")
    print("-" * 90)
    for r, row in zip(results, rows):
        score_str = f"{r['score']:+d}" if r['error'] is None else f"ERR({r['error'][:30]})"
        print(f"{row[0]:>10} {score_str:>4}  {row[1][:60]}")
    print(f"\n耗时: {elapsed:.1f}s ({len(rows)} 条, 并发{CONCURRENCY})")
    print(f"平均分: {sum(r['score'] for r in results)/len(results):.1f}")


if __name__ == "__main__":
    asyncio.run(main())
