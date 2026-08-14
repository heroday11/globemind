#!/usr/bin/env python3
"""
LLM-based causal edge detection.
Run AFTER event_evolution_chain.py.
Finds critical turning points (escalation/resolution cross-pair edges),
queries Qwen2.5-7B with context window, relabels as causal_* if confirmed.
"""
import psycopg2, os, json, urllib.request, re, sys
from collections import defaultdict
from db_runtime_config import require_database_password

DB = dict(host=os.getenv("PG_HOST","192.168.207.171"), port=int(os.getenv("PG_PORT","54333")),
          user="postgres", password=require_database_password(), dbname="globemind_news")
LLM_URL = "http://127.0.0.1:8004/v1/chat/completions"
MIN_WEIGHT = 0.75
MAX_CALLS = 100

conn = psycopg2.connect(**DB)

def get_title(cur, cid):
    cur.execute("SELECT n.title FROM event_coref_members m JOIN news n ON m.news_id = n.id WHERE m.cluster_id = %s AND n.title IS NOT NULL LIMIT 1", (cid,))
    r = cur.fetchone()
    return r[0][:120] if r else ""

def llm_query(prompt):
    payload = {"model": "/root/data/models/Qwen2.5-7B-Instruct-AWQ",
               "messages": [{"role": "user", "content": prompt}],
               "max_tokens": 120, "temperature": 0.1}
    req = urllib.request.Request(LLM_URL, data=json.dumps(payload).encode(),
                                  headers={"Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
    text = resp["choices"][0]["message"]["content"].strip()
    # Extract JSON from markdown
    if "```json" in text: text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text: text = text.split("```")[1].split("```")[0].strip()
    return json.loads(text)

# Find all stories with edges
cur = conn.cursor()
cur.execute("""
    SELECT e.story_id, e.edge_type, e.weight, e.from_cluster_id, e.to_cluster_id,
           fc.event_type as ft, fc.initiator as fi, fc.target as ftg,
           tc.event_type as tt, tc.initiator as ti, tc.target as ttg
    FROM story_edges e
    JOIN event_coref_clusters fc ON e.from_cluster_id = fc.cluster_id
    JOIN event_coref_clusters tc ON e.to_cluster_id = tc.cluster_id
    WHERE e.edge_type IN ('escalation','resolution','de-escalation')
    AND e.weight >= %s
    ORDER BY e.weight DESC
""", (MIN_WEIGHT,))
candidates = cur.fetchall()
print(f"候选边: {len(candidates)}")

# Group by story, pick top per story
story_groups = defaultdict(list)
for r in candidates:
    story_groups[r[0]].append(r)

selected = []
for sid, edges in story_groups.items():
    edges.sort(key=lambda x: -x[2])
    # Take top N per story proportional to story size
    n_per_story = max(1, min(5, len(edges) // 3))
    selected.extend(edges[:n_per_story])
selected = selected[:MAX_CALLS]
print(f"选中(每故事1-5条,最多{MAX_CALLS}): {len(selected)}")

import time as _time
relabeled = 0
for idx, r in enumerate(selected):
    sid, etype, weight, fc, tc, ft, fi, ftg, tt, ti, ttg = r

    # Get surrounding events from the same story
    cur.execute("""
        SELECT e.from_cluster_id, e.to_cluster_id, e.edge_type,
               fc.event_type, fc.initiator, fc.target,
               tc.event_type, tc.initiator, tc.target
        FROM story_edges e
        JOIN event_coref_clusters fc ON e.from_cluster_id = fc.cluster_id
        JOIN event_coref_clusters tc ON e.to_cluster_id = tc.cluster_id
        WHERE e.story_id = %s AND e.weight >= 0.3
        ORDER BY fc.start_date, tc.start_date
        LIMIT 10
    """, (sid,))
    context = cur.fetchall()

    # Build a 4-event context window around this edge
    # Find the position of this edge in the context
    ctx_lines = []
    for i, ctx in enumerate(context):
        cfc, ctc, cet, cft, cfi, cftg, ctt, cti, cttg = ctx
        is_key = (cfc == fc and ctc == tc)
        prefix = "**关键边**" if is_key else f"事件{i+1}"
        ctx_lines.append(f"{prefix}: {cet} | {cft} {cfi or '?'}->{cftg or '?'} → {ctt} {cti or '?'}->{cttg or '?'}")
        ctx_lines.append(f"  来源文章: {get_title(cur, cfc)}")
        ctx_lines.append(f"  目标文章: {get_title(cur, ctc)}")

    prompt = "分析以下俄乌战争事件链的因果关系。标为**关键边**的两个事件：后一个事件是因为前一个发生的吗？\n\n" + \
             "\n".join(ctx_lines) + \
             "\n\n回答JSON: {\"is_causal\":true/false,\"causal_type\":\"causal_escalation\"/\"causal_de-escalation\"/null,\"reason\":\"一句话\"}"

    # Add delay between calls to avoid overwhelming vLLM
    if idx > 0 and idx % 5 == 0:
        _time.sleep(1)
        print(f"  进度: {idx}/{len(selected)}...")

    try:
        result = llm_query(prompt)
        if result.get("is_causal") and result.get("causal_type") not in (None, "null", "not_causal"):
            new_type = result["causal_type"]
            cur.execute("UPDATE story_edges SET edge_type = %s, weight = %s WHERE story_id = %s AND from_cluster_id = %s AND to_cluster_id = %s",
                       (new_type, min(0.95, weight+0.05), sid, fc, tc))
            relabeled += 1
            conn.commit()
            print(f"  ✅ {new_type} | {result.get('reason','')[:60]}")
        else:
            print(f"  ➖ 非因果 | {result.get('reason','')[:60]}")
    except Exception as ex:
        print(f"  ❌ LLM失败: {ex}")

print(f"\n共重标注: {relabeled}条边")
conn.close()
