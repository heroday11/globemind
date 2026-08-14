import psycopg2
from scripts.db_runtime_config import require_database_password

conn = psycopg2.connect(
    host="192.168.207.171",
    port=54333,
    user="postgres",
    password=require_database_password(),
    dbname="globemind_news"
)
cur = conn.cursor()

# Sample 20 US→Iran clusters sorted by created_at
cur.execute("""
    SELECT cluster_id, event_type, article_count, start_date, end_date, created_at
    FROM event_coref_clusters
    WHERE initiator = 'US' AND target = 'Iran'
    ORDER BY created_at DESC
    LIMIT 20;
""")
cols = [d[0] for d in cur.description]
print("=== 20 US→Iran CLUSTERS (sorted by created_at) ===")
print(" | ".join(cols))
for row in cur.fetchall():
    print(" | ".join(str(v)[:50] for v in row))

conn.close()
