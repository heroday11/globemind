"""
Analyze military->diplomacy escalation edges for validation.
"""
import psycopg2, os, json, sys

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

# Load checkpoint for tone/verb data
checkpoint_path = '/root/data/globemind/data/checkpoint_v13_all.jsonl'
article_data = {}
with open(checkpoint_path) as f:
    for line in f:
        d = json.loads(line)
        article_data[d['article_id']] = d.get('event', {})

# military -> diplomacy escalation edges
print('=== MILITARY -> DIPLOMACY ESCALATION EDGES ===')
cur.execute('''
    SELECT e.story_id, e.from_cluster_id, e.to_cluster_id, e.weight
    FROM story_edges e
    LEFT JOIN event_coref_clusters fc ON e.from_cluster_id = fc.cluster_id
    LEFT JOIN event_coref_clusters tc ON e.to_cluster_id = tc.cluster_id
    WHERE e.edge_type = 'escalation'
      AND fc.event_type = 'military'
      AND tc.event_type = 'diplomacy'
    LIMIT 5
''')
rows = cur.fetchall()
for i, r in enumerate(rows, 1):
    print(f'\n--- Edge {i} ---')
    print(f'  Story ID: {r[0]}, Weight: {r[3]}')
    print(f'  {r[1]} (military) -> {r[2]} (diplomacy)')

    # Source cluster articles
    cur.execute('''
        SELECT n.title, n.published_at, m.news_id
        FROM event_coref_members m
        JOIN news n ON m.news_id = n.id
        WHERE m.cluster_id = %s
        LIMIT 3
    ''', (r[1],))
    articles = cur.fetchall()
    print(f'  Source cluster {r[1]}:')
    for a in articles:
        e = article_data.get(a[2], {})
        print(f'    - [{a[1]}] {a[0][:100] if a[0] else "(no title)"} '
              f'tone={e.get("tone","?")} verb={e.get("trigger_verb","?")}')

    # Target cluster articles
    cur.execute('''
        SELECT n.title, n.published_at, m.news_id
        FROM event_coref_members m
        JOIN news n ON m.news_id = n.id
        WHERE m.cluster_id = %s
        LIMIT 3
    ''', (r[2],))
    articles = cur.fetchall()
    print(f'  Target cluster {r[2]}:')
    for a in articles:
        e = article_data.get(a[2], {})
        print(f'    - [{a[1]}] {a[0][:100] if a[0] else "(no title)"} '
              f'tone={e.get("tone","?")} verb={e.get("trigger_verb","?")}')

conn.close()
