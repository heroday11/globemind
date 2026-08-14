"""
Comprehensive edge type analysis and validation.
"""
import psycopg2, os, json

from scripts.db_runtime_config import require_database_password

PG_HOST = os.environ.get("PG_HOST", "192.168.207.171")
PG_PORT = os.environ.get("PG_PORT", "54333")
PG_USER = os.environ.get("PG_USER", "postgres")
PG_PASSWORD = require_database_password("PG_PASSWORD", "DB_PASSWORD")

conn = psycopg2.connect(
    host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASSWORD,
    dbname='globemind_news'
)
conn.set_session(autocommit=True)
cur = conn.cursor()

# Check progression and continuation distributions
cur.execute('''
    SELECT e.edge_type,
           fc.event_type as from_type,
           tc.event_type as to_type,
           COUNT(*) as cnt
    FROM story_edges e
    LEFT JOIN event_coref_clusters fc ON e.from_cluster_id = fc.cluster_id
    LEFT JOIN event_coref_clusters tc ON e.to_cluster_id = tc.cluster_id
    WHERE e.edge_type IN ('progression', 'continuation')
    GROUP BY e.edge_type, fc.event_type, tc.event_type
    ORDER BY e.edge_type, cnt DESC
    LIMIT 20
''')
print('=== PROGRESSION AND CONTINUATION TRANSITIONS ===')
for r in cur.fetchall():
    print(f'  {r[0]}: {r[1]} -> {r[2]} = {r[3]}')

# Check gap edge count
cur.execute("SELECT COUNT(*) FROM story_edges WHERE edge_type = 'gap'")
print(f'\nGap edges: {cur.fetchone()[0]}')

# Check keyword_match vs tone-based classification
# For escalation, how many come from keyword match vs tone?
# We can infer by looking at military->diplomacy edges (should NOT be keyword-based escalation)
cur.execute('''
    SELECT COUNT(*) as cnt
    FROM story_edges e
    LEFT JOIN event_coref_clusters fc ON e.from_cluster_id = fc.cluster_id
    LEFT JOIN event_coref_clusters tc ON e.to_cluster_id = tc.cluster_id
    WHERE e.edge_type = 'escalation'
      AND fc.event_type = 'diplomacy'
      AND tc.event_type = 'military'
''')
print(f'\nDiplomacy -> military escalation (valid): {cur.fetchone()[0]}')

cur.execute('''
    SELECT COUNT(*) as cnt
    FROM story_edges e
    LEFT JOIN event_coref_clusters fc ON e.from_cluster_id = fc.cluster_id
    LEFT JOIN event_coref_clusters tc ON e.to_cluster_id = tc.cluster_id
    WHERE e.edge_type = 'escalation'
      AND fc.event_type = 'military'
      AND tc.event_type = 'diplomacy'
''')
print(f'Military -> diplomacy escalation (questionable): {cur.fetchone()[0]}')

cur.execute('''
    SELECT COUNT(*) as cnt
    FROM story_edges e
    LEFT JOIN event_coref_clusters fc ON e.from_cluster_id = fc.cluster_id
    LEFT JOIN event_coref_clusters tc ON e.to_cluster_id = tc.cluster_id
    WHERE e.edge_type = 'escalation'
      AND fc.event_type = 'military'
      AND tc.event_type = 'military'
''')
print(f'Military -> military escalation (should be continuation if same tone): {cur.fetchone()[0]}')

cur.execute('''
    SELECT COUNT(*) as cnt
    FROM story_edges e
    LEFT JOIN event_coref_clusters fc ON e.from_cluster_id = fc.cluster_id
    LEFT JOIN event_coref_clusters tc ON e.to_cluster_id = tc.cluster_id
    WHERE e.edge_type = 'escalation'
      AND fc.event_type = 'diplomacy'
      AND tc.event_type = 'diplomacy'
''')
print(f'Diplomacy -> diplomacy escalation (questionable): {cur.fetchone()[0]}')

# Resolution edges analysis
cur.execute('''
    SELECT fc.event_type as from_type,
           tc.event_type as to_type,
           COUNT(*) as cnt
    FROM story_edges e
    LEFT JOIN event_coref_clusters fc ON e.from_cluster_id = fc.cluster_id
    LEFT JOIN event_coref_clusters tc ON e.to_cluster_id = tc.cluster_id
    WHERE e.edge_type = 'resolution'
    GROUP BY fc.event_type, tc.event_type
    ORDER BY cnt DESC
    LIMIT 10
''')
print('\n=== RESOLUTION EDGES BY EVENT TYPE TRANSITION ===')
for r in cur.fetchall():
    print(f'  {r[0]} -> {r[1]} = {r[2]}')

# De-escalation edges analysis
cur.execute('''
    SELECT fc.event_type as from_type,
           tc.event_type as to_type,
           COUNT(*) as cnt
    FROM story_edges e
    LEFT JOIN event_coref_clusters fc ON e.from_cluster_id = fc.cluster_id
    LEFT JOIN event_coref_clusters tc ON e.to_cluster_id = tc.cluster_id
    WHERE e.edge_type = 'de-escalation'
    GROUP BY fc.event_type, tc.event_type
    ORDER BY cnt DESC
    LIMIT 10
''')
print('\n=== DE-ESCALATION EDGES BY EVENT TYPE TRANSITION ===')
for r in cur.fetchall():
    print(f'  {r[0]} -> {r[1]} = {r[2]}')

# Resolution edges - check for ceasefire/peace keywords
print('\n=== SAMPLING RESOLUTION EDGES FOR CEASEFIRE/PEACE KEYWORDS ===')
cur.execute('''
    SELECT e.story_id, e.from_cluster_id, e.to_cluster_id
    FROM story_edges e
    WHERE e.edge_type = 'resolution'
    LIMIT 3
''')
res_rows = cur.fetchall()

checkpoint_path = '/root/data/globemind/data/checkpoint_v13_all.jsonl'
article_data = {}
with open(checkpoint_path) as f:
    for line in f:
        d = json.loads(line)
        article_data[d['article_id']] = d.get('event', {})

for i, r in enumerate(res_rows, 1):
    print(f'\n--- Resolution Edge {i} ---')
    print(f'  {r[1]} -> {r[2]}')

    for cid, label in [(r[1], 'source'), (r[2], 'target')]:
        cur.execute('''
            SELECT n.title, m.news_id
            FROM event_coref_members m
            JOIN news n ON m.news_id = n.id
            WHERE m.cluster_id = %s
            LIMIT 3
        ''', (cid,))
        articles = cur.fetchall()
        print(f'  {label} cluster {cid}:')
        for a in articles:
            ev = article_data.get(a[1], {})
            print(f'    - {a[0][:100] if a[0] else "(no title)"} '
                  f'tone={ev.get("tone","?")} verb={ev.get("trigger_verb","?")}')

conn.close()
