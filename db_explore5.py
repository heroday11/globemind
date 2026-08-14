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

# Deeper look at news_ai_analysis fields relevant to events
print("=== news_ai_analysis event fields coverage ===")
for field in ['event_type', 'event_initiator', 'event_target', 'event_domain', 'frame_classification']:
    cur.execute(f"""
        SELECT COUNT(*) as total,
               COUNT({field}) as not_null,
               COUNT(*) FILTER (WHERE {field} IS NOT NULL AND {field} != '') as non_empty
        FROM news_ai_analysis;
    """)
    r = cur.fetchone()
    print(f"  {field:25s}: total={r[0]}, not_null={r[1]}, non_empty={r[2]}")

# Distinct event_types in news_ai_analysis
cur.execute("""
    SELECT event_type, COUNT(*) as cnt
    FROM news_ai_analysis
    WHERE event_type IS NOT NULL AND event_type != ''
    GROUP BY event_type
    ORDER BY cnt DESC
    LIMIT 20;
""")
print("\n=== news_ai_analysis distinct event_types ===")
for r in cur.fetchall():
    print(f"  {str(r[0]):30s} {r[1]:8d}")

# Distinct event_domain values
cur.execute("""
    SELECT event_domain, COUNT(*) as cnt
    FROM news_ai_analysis
    WHERE event_domain IS NOT NULL AND event_domain != ''
    GROUP BY event_domain
    ORDER BY cnt DESC
    LIMIT 20;
""")
print("\n=== news_ai_analysis distinct event_domains ===")
for r in cur.fetchall():
    print(f"  {str(r[0]):30s} {r[1]:8d}")

# Check how many US/Iran cluster members have news_ai_analysis data
cur.execute("""
    SELECT COUNT(DISTINCT m.news_id) as members_with_analysis
    FROM event_coref_members m
    JOIN event_coref_clusters c ON m.cluster_id = c.cluster_id
    JOIN news_ai_analysis a ON m.news_id = a.news_id
    WHERE c.initiator = 'US' AND c.target = 'Iran';
""")
r = cur.fetchone()
cur.execute("""
    SELECT COUNT(DISTINCT m.news_id) as total_members
    FROM event_coref_members m
    JOIN event_coref_clusters c ON m.cluster_id = c.cluster_id
    WHERE c.initiator = 'US' AND c.target = 'Iran';
""")
r2 = cur.fetchone()
print(f"\n=== US→Iran members join with news_ai_analysis ===")
print(f"  Total members: {r2[0]}, with analysis: {r[0]}")

# Build a proper US→Iran event chain: cluster-level summary with deepseek_sentiment averaged per cluster
cur.execute("""
    SELECT c.cluster_id, c.event_type, c.article_count,
           AVG(a.deepseek_sentiment) as avg_sentiment,
           MIN(a.deepseek_sentiment) as min_sentiment,
           MAX(a.deepseek_sentiment) as max_sentiment,
           c.created_at
    FROM event_coref_clusters c
    JOIN event_coref_members m ON c.cluster_id = m.cluster_id
    LEFT JOIN news_ai_analysis a ON m.news_id = a.news_id
    WHERE c.initiator = 'US' AND c.target = 'Iran'
    GROUP BY c.cluster_id, c.event_type, c.article_count, c.created_at
    ORDER BY c.created_at
    LIMIT 30;
""")
cols = [d[0] for d in cur.description]
print(f"\n=== US→Iran EVENT CHAIN (cluster-level with tone, chronological) ===")
print(" | ".join(f"{c:30s}" for c in cols))
print("-" * 180)
rows = cur.fetchall()
for r in rows:
    vals = []
    for v in r:
        if v is None:
            vals.append("None".ljust(28))
        elif isinstance(v, float):
            vals.append(f"{v:>8.2f}".ljust(28))
        else:
            vals.append(str(v)[:26].ljust(28))
    print(" | ".join(vals))

# Overall tone stats per event_type for US→Iran
cur.execute("""
    SELECT c.event_type,
           COUNT(DISTINCT c.cluster_id) as clusters,
           AVG(a.deepseek_sentiment) as avg_tone,
           MIN(a.deepseek_sentiment) as min_tone,
           MAX(a.deepseek_sentiment) as max_tone
    FROM event_coref_clusters c
    JOIN event_coref_members m ON c.cluster_id = m.cluster_id
    LEFT JOIN news_ai_analysis a ON m.news_id = a.news_id
    WHERE c.initiator = 'US' AND c.target = 'Iran'
    GROUP BY c.event_type
    ORDER BY avg_tone;
""")
print(f"\n=== US→Iran Tone by Event Type ===")
print(f"{'Event Type':30s} {'Clusters':>10s} {'Avg Tone':>10s} {'Min':>8s} {'Max':>8s}")
for r in cur.fetchall():
    print(f"{str(r[0]):30s} {r[1]:10d} {r[2] if r[2] else 0:>10.2f} {r[3] if r[3] else 0:>8.2f} {r[4] if r[4] else 0:>8.2f}")

# Transition matrix: what event_types follow what
cur.execute("""
    WITH sorted AS (
        SELECT event_type, created_at,
               LEAD(event_type) OVER (ORDER BY created_at) as next_event_type
        FROM event_coref_clusters
        WHERE initiator = 'US' AND target = 'Iran'
    )
    SELECT event_type, next_event_type, COUNT(*) as transitions
    FROM sorted
    WHERE next_event_type IS NOT NULL
    GROUP BY event_type, next_event_type
    ORDER BY COUNT(*) DESC
    LIMIT 20;
""")
print(f"\n=== US→Iran Event Type Transitions (most common) ===")
print(f"{'From':30s} {'To':30s} {'Count':>8s}")
for r in cur.fetchall():
    print(f"{str(r[0]):30s} {str(r[1]):30s} {r[2]:8d}")

conn.close()
