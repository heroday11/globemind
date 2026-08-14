#!/bin/bash
# Agent 1: 全量28K话题先验 + 分类器L1聚类
export PATH="/root/data/globemind/.env_torch/bin:$PATH"
export PG_HOST=192.168.207.171
export DB_HOST=192.168.207.171
cd /root/data/globemind

python3 << 'PYEOF'
import sys, os, json, time, psycopg2, numpy as np
from collections import defaultdict, Counter
sys.path.insert(0, '.'); sys.path.insert(0, 'backend')
from core_pipeline.topic_clustering import cluster_topics, validate_document_format
from core_pipeline.event_extract_v11 import Event, ExtractionResult
from core_pipeline.event_coref_cluster import build_event_coreference_with_embeddings, load_entity_aliases
from core_pipeline.document_classifier import DocumentPairClassifier
from scripts.db_runtime_config import require_database_password

os.environ['PG_HOST'] = '192.168.207.171'; os.environ['DB_HOST'] = '192.168.207.171'

print('[1/5] Loading 28K checkpoint...')
results = []
with open('data/checkpoint_v11_240k.jsonl') as f:
    for line in f:
        d = json.loads(line)
        ev = d.get('event')
        if not ev or ev.get('domain') != 'geopolitical': continue
        results.append(ExtractionResult(article_id=d['article_id'], published_at=d.get('published_at'),
            event=Event(**ev), raw_response='', parse_success=True))
print(f'  {len(results)} articles')

print('[2/5] Loading bodies + embeddings...')
conn = psycopg2.connect(host='192.168.207.171', port=54333, dbname='globemind_news', user='postgres', password=require_database_password())
cur = conn.cursor()
aids = [r.article_id for r in results]
batch_size = 5000
bodies = {}
for i in range(0, len(aids), batch_size):
    batch = aids[i:i+batch_size]
    cur.execute("SELECT id, COALESCE(title,'')||' '||COALESCE(body,'') FROM news WHERE id = ANY(%s)", (batch,))
    for r in cur.fetchall(): bodies[r[0]] = r[1]
cur.execute("SELECT news_id, embedding FROM news_embeddings WHERE model IN ('bge-m3','BAAI/bge-m3')")
embs = {}
for nid, raw in cur.fetchall():
    if isinstance(raw, memoryview): raw = bytes(raw)
    if isinstance(raw, bytes): raw = json.loads(raw.decode())
    if isinstance(raw, str): raw = json.loads(raw)
    embs[int(nid)] = np.array(raw, dtype='float32')
print(f'  {len(bodies)} bodies, {len(embs)} embeddings')

print('[3/5] Topic clustering...')
t0 = time.time()
valid = validate_document_format(bodies)
assignments = cluster_topics(valid, top_k=20, resolution=1.0)
topic_map = {}
for tid, aids in assignments.items():
    for aid in aids: topic_map[aid] = tid
for aid in aids:
    if aid not in topic_map: topic_map[aid] = 'default'
print(f'  {len(assignments)} topics in {time.time()-t0:.0f}s')

print('[4/5] L1 clustering with classifier...')
clf = DocumentPairClassifier(); clf.load()
alias_path = 'data/entity_alias.json'
if os.path.exists(alias_path): load_entity_aliases(str(alias_path))

topic_groups = defaultdict(list)
for r in results: topic_groups[topic_map.get(r.article_id, 'default')].append(r)

all_clusters = {}
total_non_sing = 0
for tid, group in sorted(topic_groups.items(), key=lambda x: -len(x[1])):
    if len(group) < 3: continue
    t1 = time.time()
    group_aids = {r.article_id for r in group}
    topic_embs = {aid: embs[aid] for aid in group_aids if aid in embs}
    if len(topic_embs) < 3: continue
    clusters = build_event_coreference_with_embeddings(group, embeddings=topic_embs, use_classifier=True)
    for cid, aids in clusters.items():
        new_cid = f'{tid[:6]}_{cid}' if tid != 'default' else cid
        all_clusters[new_cid] = aids
    n_multi = sum(1 for v in clusters.values() if len(v) >= 2)
    total_non_sing += n_multi
    print(f'  topic {tid[:6]:6s}: {len(clusters):5d} cls ({n_multi:3d} non-sing) in {time.time()-t1:.0f}s')

print(f'\n[5/5] Writing to DB...')
cur.execute('TRUNCATE event_coref_members CASCADE')
cur.execute('TRUNCATE event_coref_clusters CASCADE')
conn.commit()
n_written = 0
for cid, aids in all_clusters.items():
    r0 = next((r for r in results if r.article_id == aids[0]), None)
    cur.execute("INSERT INTO event_coref_clusters (cluster_id, article_count, event_type, initiator, target) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (cid, len(aids), r0.event.event_type if r0 and r0.event else 'other', r0.event.initiator if r0 else None, r0.event.target if r0 else None))
    for aid in aids:
        cur.execute("INSERT INTO event_coref_members (cluster_id, news_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (cid, aid))
    n_written += 1
    if n_written % 500 == 0: conn.commit()
conn.commit()
cur.close(); conn.close()

with open('data/event_coref_mapping_layer1.jsonl', 'w') as f:
    for cid, aids in all_clusters.items():
        for aid in aids:
            f.write(json.dumps({'cluster_id': cid, 'article_id': aid}) + '\n')

nons = sum(1 for v in all_clusters.values() if len(v) >= 2)
sings = len(all_clusters) - nons
print(f'\n=== RESULT ===')
print(f'Total clusters: {len(all_clusters)}')
print(f'Non-singleton: {nons} ({100*nons//max(len(all_clusters),1)}%)')
print(f'Singletons: {sings}')
print(f'DONE')
PYEOF
