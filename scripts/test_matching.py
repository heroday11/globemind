#!/usr/bin/env python3
"""Test the refined matching approach on all ECB+ topics."""
import json
import re
import psycopg2
import psycopg2.extras
from urllib.parse import urlparse
from collections import Counter
from db_runtime_config import require_database_password

conn = psycopg2.connect(
    host='192.168.207.171', port=54333,
    dbname='globemind_news', user='postgres', password=require_database_password()
)
cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

with open('/root/data/globemind/data/ecbplus/ecb_corpus.json') as f:
    docs = json.load(f)['documents']

_STOP = frozenset('a an the and or but in on at to for of with by from is are was were be been has have had do does did will would could should may might can shall not no its his her their our your this that these those i we you they he she it me my myself'.split())

def refine_phrases(raw_text, topic):
    """Extract specific search queries. Returns ordered list (most specific first)."""
    queries = []

    m = re.match(r'(https?://\S+)', raw_text)
    url = m.group(1) if m else ''
    if url:
        path = urlparse(url).path if '://' in url else ''
        segments = [s for s in path.split('/') if s and len(s) > 3]
        for seg in segments[-1:]:
            words = [w for w in re.split(r'[\-_.]', seg)
                     if len(w) > 2 and not re.match(r'^\d+$', w)
                     and w.lower() not in _STOP]
            for i in range(len(words)-1):
                queries.append(f"{words[i]} {words[i+1]}")
            for w in words:
                wl = w.lower()
                if wl not in ('article', 'news', 'story', 'index', 'html', 'php', 'asp', 'video', 'blog', 'page', 'amp', 'www', 'http', 'https', 'com', 'org'):
                    queries.append(w)

    topic_words = topic.split('_')
    for w in topic_words:
        wl = w.lower()
        if len(w) > 4 and wl not in ('article', 'news', 'story', 'index'):
            queries.append(w)

    text_body = raw_text.split(' ', 1)[-1] if ' ' in raw_text else raw_text
    entities = re.findall(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)', text_body)
    for e in entities:
        if 2 <= len(e.split()) <= 4 and len(e) < 50:
            queries.append(e)

    seen = set()
    unique = []
    for q in queries:
        ql = q.lower().strip()
        if ql not in seen:
            seen.add(ql)
            unique.append(q)
    return unique

# Test
print("=== Testing refined queries ===")
seen_topics = set()
tested = 0
matched = 0
details = []

for doc in docs:
    topic = doc['topic']
    if topic in seen_topics:
        continue
    seen_topics.add(topic)

    raw = doc['full_text']
    queries = refine_phrases(raw, topic)
    if not queries:
        continue

    tested += 1
    best_match = None
    best_score = 0.0

    for q in queries[:8]:
        cur.execute(
            "SELECT id, title FROM news WHERE language='en' AND title ILIKE %s LIMIT 10",
            (f'%{q}%',)
        )
        rows = cur.fetchall()

        for r in rows:
            title = r['title'] or ''
            title_lower = title.lower()
            q_words = q.lower().split()
            hits = sum(1 for w in q_words if w in title_lower)
            score = hits / len(q_words) if q_words else 0

            if q.lower() in title_lower:
                score = max(score, 0.6)

            if score > best_score:
                best_score = score
                best_match = (r['id'], title[:60])

        if best_score >= 0.6:
            break

    if best_score >= 0.3:
        matched += 1
        details.append((topic, best_score, best_match[0], queries[0][:40], best_match[1]))
        print(f"  OK [{topic[:30]:30s}] s={best_score:.3f} id={best_match[0]} q=\"{queries[0][:40]}\"")
    else:
        details.append((topic, best_score, None, queries[0][:40] if queries else '', ''))
        print(f"  -- [{topic[:30]:30s}] s={best_score:.3f} q=\"{queries[0][:40] if queries else 'none'}\"")

print(f"\n Matched: {matched}/{tested}")

# Print details for matched
print("\n=== Match details ===")
for topic, score, cid, query, title in details:
    if score >= 0.3:
        print(f"  {topic[:35]:35s} score={score:.3f} id={cid}")
        print(f"    query='{query}'")
        print(f"    title='{title}'")

conn.close()
