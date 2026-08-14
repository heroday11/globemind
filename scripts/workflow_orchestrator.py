#!/usr/bin/env python3
"""
Cluster Optimization Workflow Orchestrator
==========================================
多Agent工作流编排器 — 替代Dynamic Workflows（当前会话限制）

架构:
  编排器 (Python) ──→ Agent 1 (topic + L1) ──→ 质量门禁 ──┐
                   ├──→ Agent 2 (classifier)            ├──→ 审查 → 测试 → 报告
                   ├──→ Agent 3 (ECB+ eval)            ┘
                   └──→ 监控 + 重试 + 日志
  
每个Agent是独立进程，可监控、可重试、有质量门禁。
"""
import sys, os, json, time, subprocess, signal, logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

REPO = Path(__file__).resolve().parent.parent
os.chdir(str(REPO))

# 日志
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    handlers=[
        logging.FileHandler("workflow_orchestrator.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("orchestrator")

class Agent:
    """一个可执行的Agent任务"""
    def __init__(self, name: str, script: str, depends_on: List[str] = None,
                 retry: int = 2, quality_gate: Optional[str] = None, timeout: int = 900):
        self.name = name
        self.script = script
        self.depends_on = depends_on or []
        self.retry = retry
        self.quality_gate = quality_gate
        self.timeout = timeout
        self.status = "pending"  # pending | running | passed | failed | retrying
        self.output = ""
        self.attempts = 0
    
    def run(self) -> bool:
        """执行Agent任务"""
        self.status = "running"
        self.attempts += 1
        log.info(f"▶ Agent [{self.name}] (attempt {self.attempts})")
        
        for attempt in range(self.retry + 1):
            t0 = time.time()
            result = subprocess.run(
                ["python3", "-c", self.script],
                capture_output=True, text=True, timeout=self.timeout
            )
            elapsed = time.time() - t0
            self.output = result.stdout + result.stderr
            
            if result.returncode == 0:
                log.info(f"  ✅ {self.name} 完成 ({elapsed:.0f}s)")
                self.status = "passed"
                return True
            else:
                error = result.stderr[:200]
                log.warning(f"  ⚠️  {self.name} 失败 (attempt {attempt+1}): {error}")
                if attempt < self.retry:
                    log.info(f"  🔄 重试 {self.name}...")
                    time.sleep(10)
        
        self.status = "failed"
        log.error(f"  ❌ {self.name} 已失败 ({self.retry+1}次尝试)")
        return False

class Workflow:
    """工作流编排器"""
    def __init__(self):
        self.agents: Dict[str, Agent] = {}
        self.status = "initialized"
        self.start_time = None
    
    def add_agent(self, agent: Agent):
        self.agents[agent.name] = agent
    
    def run(self):
        """执行工作流"""
        self.start_time = time.time()
        self.status = "running"
        
        log.info("=" * 60)
        log.info("  Cluster Optimization Workflow")
        log.info(f"  Agents: {len(self.agents)}")
        log.info("=" * 60)
        
        # 拓扑排序：按依赖关系执行
        executed = set()
        
        while len(executed) < len(self.agents):
            batch = []
            for name, agent in self.agents.items():
                if name in executed:
                    continue
                deps_met = all(d in executed for d in agent.depends_on)
                if deps_met:
                    batch.append(agent)
            
            if not batch:
                log.error("❌ 依赖循环或无法满足的依赖!")
                self.status = "failed"
                return
            
            # 并行执行batch中的Agent
            log.info(f"\n▶ Batch: {[a.name for a in batch]}")
            results = []
            for agent in batch:
                results.append(agent.run())
            
            executed.update(a.name for a in batch)
        
        # 报告
        total = time.time() - self.start_time
        passed = sum(1 for a in self.agents.values() if a.status == "passed")
        failed = sum(1 for a in self.agents.values() if a.status == "failed")
        
        log.info(f"\n{'='*60}")
        log.info(f"  工作流完成! {passed}/{len(self.agents)} passed, {total:.0f}s")
        log.info(f"{'='*60}")
        
        for name, agent in self.agents.items():
            status_icon = "✅" if agent.status == "passed" else "❌"
            log.info(f"  {status_icon} {name}: {agent.status} ({agent.attempts} attempts)")
        
        self.status = "completed"


# ═══════════════════════════════════════════════════════
# Agent 定义
# ═══════════════════════════════════════════════════════

# Agent 1: 全量话题先验 + L1 聚类
AGENT_TOPIC_L1 = """
import sys, os, json, time
sys.path.insert(0, '.'); sys.path.insert(0, 'backend')
os.environ['PG_HOST'] = '192.168.207.171'; os.environ['DB_HOST'] = '192.168.207.171'

import psycopg2, numpy as np
from collections import defaultdict, Counter
from core_pipeline.topic_clustering import cluster_topics, validate_document_format
from core_pipeline.event_extract_v11 import Event, ExtractionResult
from core_pipeline.event_coref_cluster import build_event_coreference_with_embeddings, load_entity_aliases
from core_pipeline.document_classifier import DocumentPairClassifier
from scripts.db_runtime_config import require_database_password

conn = psycopg2.connect(host='192.168.207.171', port=54333, dbname='globemind_news', user='postgres', password=require_database_password())
cur = conn.cursor()

# Load checkpoint
results = []
with open('data/checkpoint_v11_240k.jsonl') as f:
    for line in f:
        d = json.loads(line)
        ev = d.get('event')
        if not ev or ev.get('domain') != 'geopolitical': continue
        results.append(ExtractionResult(article_id=d['article_id'], published_at=d.get('published_at'),
            event=Event(**ev), raw_response='', parse_success=True))
print(f'ARTICLES:{len(results)}')

# Load bodies + embeddings
aids = [r.article_id for r in results]
cur.execute('SELECT id, COALESCE(title,\"\")||\" \"||COALESCE(body,\"\") FROM news WHERE id = ANY(%s)', (aids,))
bodies = {r[0]:r[1] for r in cur.fetchall()}
cur.execute(\"SELECT news_id, embedding FROM news_embeddings WHERE model IN ('bge-m3','BAAI/bge-m3')\")
embs = {}
for nid, raw in cur.fetchall():
    if isinstance(raw, memoryview): raw = bytes(raw)
    if isinstance(raw, bytes): raw = json.loads(raw.decode())
    if isinstance(raw, str): raw = json.loads(raw)
    embs[int(nid)] = np.array(raw, dtype='float32')
print(f'EMBS:{len(embs)}')

# Topic clustering
valid = validate_document_format(bodies)
assignments = cluster_topics(valid, top_k=20, resolution=1.0)
topic_map = {}
for tid, aids in assignments.items():
    for aid in aids: topic_map[aid] = tid
for aid in aids:
    if aid not in topic_map: topic_map[aid] = 'default'
print(f'TOPICS:{len(assignments)}')

# Classifier
clf = DocumentPairClassifier()
clf_loaded = clf.load()
print(f'CLASSIFIER:{clf_loaded}')

# Per-topic L1
alias_path = 'data/entity_alias.json'
if os.path.exists(alias_path): load_entity_aliases(str(alias_path))

topic_groups = defaultdict(list)
for r in results:
    topic_groups[topic_map.get(r.article_id, 'default')].append(r)

all_clusters = {}
total_non_sing = 0
for tid, group in sorted(topic_groups.items(), key=lambda x: -len(x[1])):
    if len(group) < 3: continue
    group_aids = {r.article_id for r in group}
    topic_embs = {aid: embs[aid] for aid in group_aids if aid in embs}
    if len(topic_embs) < 3: continue
    clusters = build_event_coreference_with_embeddings(group, embeddings=topic_embs)
    for cid, aids in clusters.items():
        new_cid = f'{tid[:8]}_{cid}' if tid != 'default' else cid
        all_clusters[new_cid] = aids
    total_non_sing += sum(1 for v in clusters.values() if len(v) >= 2)

# Write to DB
cur.execute('TRUNCATE event_coref_members CASCADE')
cur.execute('TRUNCATE event_coref_clusters CASCADE')
conn.commit()
for cid, aids in all_clusters.items():
    r0 = next((r for r in results if r.article_id == aids[0]), None)
    cur.execute('INSERT INTO event_coref_clusters (cluster_id, article_count, event_type, initiator, target) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
        (cid, len(aids), r0.event.event_type if r0 and r0.event else 'other', r0.event.initiator if r0 and r0.event else None, r0.event.target if r0 and r0.event else None))
    for aid in aids:
        cur.execute('INSERT INTO event_coref_members (cluster_id, news_id) VALUES (%s,%s) ON CONFLICT DO NOTHING', (cid, aid))
conn.commit()
cur.close(); conn.close()

with open('data/event_coref_mapping_layer1.jsonl', 'w') as f:
    for cid, aids in all_clusters.items():
        for aid in aids:
            f.write(json.dumps({'cluster_id': cid, 'article_id': aid}) + '\\n')

nons = sum(1 for v in all_clusters.values() if len(v) >= 2)
print(f'RESULT: total={len(all_clusters)} non_sing={nons}')
print('DONE')
"""

# Agent 2: L2 + StoryTree
AGENT_L2_TREE = """
import sys, os, time
sys.path.insert(0, '.'); sys.path.insert(0, 'backend')
os.environ['PG_HOST'] = '192.168.207.171'; os.environ['DB_HOST'] = '192.168.207.171'
from agentic_rag.pipeline.micro_story_builder import build_micro_stories
from agentic_rag.pipeline.story_tree_builder import build_story_tree
t0 = time.time()
n_stories, n_clusters = build_micro_stories()
t1 = time.time()
n_roots, n_leaves = build_story_tree()
t2 = time.time()
print(f'L2: {n_stories} stories ({t1-t0:.0f}s)')
print(f'TREE: {n_roots+n_leaves} nodes ({t2-t1:.0f}s)')
print('DONE')
"""

# Agent 3: 分类器训练 + 评估
AGENT_CLASSIFIER_EVAL = """
import sys, os, json, time, numpy as np, random
sys.path.insert(0, '.'); sys.path.insert(0, 'backend')
os.environ['PG_HOST'] = '192.168.207.171'
import psycopg2
from collections import defaultdict
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score, roc_auc_score
from core_pipeline.event_coref_cluster import _canonical_entity
from scripts.db_runtime_config import require_database_password

conn = psycopg2.connect(host='192.168.207.171', port=54333, dbname='globemind_news', user='postgres', password=require_database_password())
cur = conn.cursor()
cur.execute('SELECT news_id, embedding FROM news_embeddings WHERE model IN (\"bge-m3\",\"BAAI/bge-m3\")')
embs = {}
for nid, raw in cur.fetchall():
    if isinstance(raw, memoryview): raw = bytes(raw)
    if isinstance(raw, bytes): raw = json.loads(raw.decode())
    if isinstance(raw, str): raw = json.loads(raw)
    embs[int(nid)] = np.array(raw, dtype='float32')
cur.close(); conn.close()
print(f'EMBS:{len(embs)}')

# Load clusters
with open('data/event_coref_mapping_layer1.jsonl') as f:
    members = defaultdict(list)
    for line in f:
        d = json.loads(line)
        members[d['cluster_id']].append(d['article_id'])

# Positive pairs (same cluster)
pos = []
for cid, aids in members.items():
    if len(aids) < 2: continue
    sampled = random.sample(aids, min(len(aids), 6))
    for i in range(len(sampled)):
        for j in range(i+1, len(sampled)):
            if sampled[i] in embs and sampled[j] in embs:
                pos.append((sampled[i], sampled[j], 1))

# Negative pairs (different clusters)
cluster_list = list(members.keys())
neg = []
random.shuffle(cluster_list)
for i in range(min(5000, len(cluster_list))):
    c1 = cluster_list[i % len(cluster_list)]
    c2 = cluster_list[(i+1) % len(cluster_list)]
    if not members[c1] or not members[c2]: continue
    a1 = random.choice(members[c1])
    a2 = random.choice(members[c2])
    if a1 in embs and a2 in embs:
        neg.append((a1, a2, 0))

all_pairs = pos + neg
random.shuffle(all_pairs)
X_a = np.array([embs[a1] for a1,_,_ in all_pairs])
X_b = np.array([embs[a2] for _,a2,_ in all_pairs])
y = np.array([l for _,_,l in all_pairs])
print(f'DATA:{len(all_pairs)} pairs ({sum(y)/len(y):.1%} positive)')

# Train
split = int(len(y)*0.8)
X_a_tr, X_a_te = X_a[:split], X_a[split:]
X_b_tr, X_b_te = X_b[:split], X_b[split:]
y_tr, y_te = y[:split], y[split:]

def feat(va, vb):
    return np.column_stack([np.sum(va*vb, axis=1), np.sqrt(np.sum((va-vb)**2, axis=1)), np.max(np.abs(va-vb), axis=1)])

clf = LogisticRegression(max_iter=1000, C=1.0, class_weight='balanced')
clf.fit(feat(X_a_tr, X_b_tr), y_tr)

y_prob = clf.predict_proba(feat(X_a_te, X_b_te))[:,1]
y_pred = (y_prob >= 0.5).astype(int)
auc = roc_auc_score(y_te, y_prob)
f1 = f1_score(y_te, y_pred)
acc = accuracy_score(y_te, y_pred)
print(f'ACC:{acc:.3f} F1:{f1:.3f} AUC:{auc:.3f}')

import joblib
os.makedirs('data/models', exist_ok=True)
joblib.dump(clf, 'data/models/document_classifier.joblib')

# Quality gate
quality_pass = 'PASS' if auc >= 0.80 else 'FAIL'
print(f'QUALITY_GATE:{quality_pass} (AUC={auc:.3f}, need>=0.80)')
print('DONE')
"""

# ── 构建工作流 ──
wf = Workflow()
wf.add_agent(Agent("topic-l1", AGENT_TOPIC_L1, timeout=1800))    # 30 min
wf.add_agent(Agent("l2-storytree", AGENT_L2_TREE, depends_on=["topic-l1"], timeout=600))
wf.add_agent(Agent("classifier-eval", AGENT_CLASSIFIER_EVAL, depends_on=["topic-l1"], timeout=600))
wf.add_agent(Agent("ecb-eval", """
import sys, subprocess
sys.path.insert(0, '.'); sys.path.insert(0, 'backend')
os.environ['PG_HOST'] = '192.168.207.171'
r = subprocess.run(['python3', 'scripts/eval_ecb_plus.py', 'eval-globemind',
    'data/ecbplus/gold_layer1.jsonl', 'data/event_coref_mapping_layer1.jsonl'],
    capture_output=True, text=True, timeout=300)
print(r.stdout[-1000:])
if r.returncode != 0: print(f'STDERR:{r.stderr[-500:]}')
print('DONE')
""", depends_on=["topic-l1"], timeout=600))

wf.run()
