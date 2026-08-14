#!/usr/bin/env python
"""
用 DeepSeek 优化 L2 宏观事件命名（macro_event_coref.title）。

用法:
    python scripts/rename_macro_events.py [--dry-run]

注意:
    - 仅更新 title 字段，不影响其他列
    - 使用 extra_body={"thinking": {"type": "disabled"}} 关闭推理
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

import openai

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "backend"))

from psycopg2.extras import execute_values  # noqa: E402

from agentic_rag.db.connection import get_conn  # noqa: E402

API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
CONCURRENCY = 16

SYSTEM_PROMPT = """你是一名地缘政治事件命名专家。请根据以下事件信息，生成一个简洁、准确、信息量大的中文名称（10-20字）。

要求：
- 名称必须精确反映事件的核心参与者、行为/冲突类型和主要目标/对象
- 使用标准中文新闻术语
- 不同的事件必须有可区分的名称（即使参与者相同，也要体现差异）
- 不要添加事实中没有的信息
- 只输出名称本身，不要任何其他文字

示例：
- initiator=Russia, target=Ukraine, type=军事冲突 → "俄乌战争持续双方公布战损数据"
- initiator=Iran, target=US, type=贸易经济 → "美伊贸易争端升级互相制裁"
- initiator=China, target=US, type=法律政策 → "中美法律政策博弈科技领域受限"
- initiator=Israel, target=Palestinians, type=人权移民 → "巴以人权移民争端国际社会关注"
"""


async def rename_one(client: openai.AsyncOpenAI, event_id: int, title: str, event_type: str | None, initiator: str | None, target: str | None, article_count: int) -> str | None:
    context_parts = []
    if event_type:
        context_parts.append(f"事件类型={event_type}")
    if initiator:
        context_parts.append(f"发起方={initiator}")
    if target:
        context_parts.append(f"目标/对象={target}")
    context_parts.append(f"相关文章数={article_count}")
    context = " | ".join(context_parts)
    text = f"当前名称：{title}\n{context}"

    try:
        resp = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            temperature=0.1,
            max_tokens=30,
            extra_body={"thinking": {"type": "disabled"}},
        )
        raw = (resp.choices[0].message.content or "").strip()
        # 清理：去掉引号和多余空格
        raw = raw.strip('"').strip("'").strip()
        if len(raw) < 4 or len(raw) > 50:
            return None
        return raw
    except Exception as e:
        print(f"  [WARN] 事件 #{event_id} 命名失败: {e}")
        return None


async def main():
    if not API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY is required")
    dry_run = "--dry-run" in sys.argv

    conn = get_conn("globemind_news", autocommit=False, connect_timeout=15)
    cur = conn.cursor()

    cur.execute("""
        SELECT id, title, event_type_family, initiator, target, article_count
        FROM macro_event_coref
        ORDER BY article_count DESC
    """)
    rows = cur.fetchall()
    total = len(rows)
    print(f"共 {total} 个 L2 宏观事件待处理", flush=True)

    client = openai.AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=openai.Timeout(30))
    sem = asyncio.Semaphore(CONCURRENCY)

    async def bounded(eid, title, etype, initiator, target, count):
        async with sem:
            return eid, await rename_one(client, eid, title, etype, initiator, target, count)

    t0 = time.perf_counter()
    results = await asyncio.gather(*[
        bounded(r[0], r[1] or "", r[2], r[3], r[4], r[5]) for r in rows
    ])

    updates = [(eid, name) for eid, name in results if name is not None]
    unchanged = [(eid, name) for eid, name in results if name is None]

    print(f"\nDeepSeek 返回完成 ({time.perf_counter()-t0:.1f}s)")
    print(f"待更新: {len(updates)} 个")
    print(f"保留原名: {len(unchanged)} 个（API 返回无效名称）")

    if dry_run:
        print("\n=== DRY RUN - 拟更新内容 ===")
        for row in rows:
            row_id = row[0]
            old = row[1] or ""
            new = next((n for eid, n in results if eid == row_id), None)
            if new:
                print(f"  #{row_id:>6}: {old:55s} → {new}")
            else:
                print(f"  #{row_id:>6}: {old:55s} → (保留)")
    else:
        if updates:
            execute_values(cur, """
                UPDATE macro_event_coref AS me SET title = u.title
                FROM (VALUES %s) AS u(id, title)
                WHERE me.id = u.id
            """, updates, page_size=500)
            conn.commit()
            print(f"✓ 已更新 {len(updates)} 个 L2 事件名称")

    print("\n=== 最终名称 ===")
    cur.execute("SELECT id, title, event_type_family, article_count FROM macro_event_coref ORDER BY article_count DESC")
    for row in cur.fetchall():
        print(f"  #{row[0]:>6}: {str(row[1]):55s} | {row[2]} | {row[3]}篇")


if __name__ == "__main__":
    asyncio.run(main())
