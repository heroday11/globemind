#!/usr/bin/env python
"""将 checkpoint_remaining_57201.jsonl 的结果写入 news_ai_analysis 表（INSERT）。"""
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from backend.agentic_rag.db.connection import get_conn
import psycopg2.extras

CHECKPOINT = _REPO / "data" / "checkpoint_remaining_57201.jsonl"


def _exec_batch(cur, rows):
    psycopg2.extras.execute_values(
        cur,
        """
        INSERT INTO news_ai_analysis (news_id, event_domain, event_type, event_initiator, event_target, analyzed_at)
        VALUES %s
        ON CONFLICT (news_id) DO UPDATE SET
            event_domain = EXCLUDED.event_domain,
            event_type = EXCLUDED.event_type,
            event_initiator = EXCLUDED.event_initiator,
            event_target = EXCLUDED.event_target,
            analyzed_at = NOW()
        """,
        [(r[0], r[1], r[2], r[3], r[4]) for r in rows],
        template="(%s, %s, %s, %s, %s, NOW())",
    )


conn = get_conn("globemind_news", autocommit=False, connect_timeout=15)
cur = conn.cursor()

n_ok = 0
n_err = 0
n_total = 0
BATCH = 500
batch_rows = []

with open(CHECKPOINT) as f:
    for line in f:
        d = json.loads(line)
        n_total += 1
        aid = d["article_id"]
        ev = d.get("event")

        if ev and d.get("parse_success"):
            domain = ev.get("domain")
            event_type = ev.get("event_type")
            initiator = ev.get("initiator")
            target = ev.get("target")
            n_ok += 1
        else:
            domain = None
            event_type = None
            initiator = None
            target = None
            n_err += 1

        batch_rows.append((aid, domain, event_type, initiator, target))
        if len(batch_rows) >= BATCH:
            _exec_batch(cur, batch_rows)
            conn.commit()
            batch_rows.clear()

if batch_rows:
    _exec_batch(cur, batch_rows)
    conn.commit()

cur.close()
conn.close()
print(f"写入完成: 总计={n_total}, 成功={n_ok}, 失败={n_err}")
