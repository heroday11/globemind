import psycopg2
import json
from scripts.db_runtime_config import require_database_password

conn = psycopg2.connect(
    host="192.168.207.171",
    port=54333,
    user="postgres",
    password=require_database_password(),
    dbname="globemind_news"
)
cur = conn.cursor()

# Check entity_pair_sentiments coverage
print("=== entity_pair_sentiments coverage ===")
for tbl in ['news_ai_analysis', 'news_analysis']:
    cur.execute(f"""
        SELECT COUNT(*) as total,
               COUNT(*) FILTER (WHERE entity_pair_sentiments IS NOT NULL) as not_null,
               COUNT(*) FILTER (WHERE entity_pair_sentiments IS NOT NULL AND entity_pair_sentiments::text != 'null' AND entity_pair_sentiments != '{{}}'::jsonb) as has_data
        FROM {tbl};
    """)
    r = cur.fetchone()
    print(f"{tbl}: total={r[0]}, not_null={r[1]}, has_data={r[2]}")

# Show some entity_pair_sentiments examples
cur.execute("""
    SELECT news_id, entity_pair_sentiments
    FROM news_ai_analysis
    WHERE entity_pair_sentiments IS NOT NULL
      AND entity_pair_sentiments::text != 'null'
      AND entity_pair_sentiments != '{}'::jsonb
    LIMIT 5;
""")
print("\n--- Sample entity_pair_sentiments from news_ai_analysis ---")
for r in cur.fetchall():
    print(f"\nnews_id={r[0]}")
    if r[1]:
        try:
            eps = json.loads(r[1]) if isinstance(r[1], str) else r[1]
            print(json.dumps(eps, indent=2)[:500])
        except:
            print(f"  (raw: {str(r[1])[:200]})")

# Check deepseek_sentiment in news_ai_analysis
cur.execute("""
    SELECT COUNT(*) as total,
           COUNT(deepseek_sentiment) as not_null,
           MIN(deepseek_sentiment) as min_s,
           MAX(deepseek_sentiment) as max_s,
           AVG(deepseek_sentiment) as avg_s
    FROM news_ai_analysis
    WHERE deepseek_sentiment IS NOT NULL;
""")
r = cur.fetchone()
print(f"\n=== deepseek_sentiment coverage ===")
print(f"not_null: {r[1]}/{r[0]}, range: {r[2]} to {r[3]}, avg: {r[4]:.4f}")

# Check china_impact_sentiment
cur.execute("""
    SELECT COUNT(*) as total,
           COUNT(china_impact_sentiment) as not_null,
           MIN(china_impact_sentiment) as min_s,
           MAX(china_impact_sentiment) as max_s,
           AVG(china_impact_sentiment) as avg_s
    FROM news_ai_analysis
    WHERE china_impact_sentiment IS NOT NULL;
""")
r = cur.fetchone()
print(f"\n=== china_impact_sentiment coverage ===")
print(f"not_null: {r[1]}/{r[0]}, range: {r[2]} to {r[3]}, avg: {r[4]:.4f}")

# Join event_coref_members with news_ai_analysis to see tone for US/Iran clusters
cur.execute("""
    SELECT m.cluster_id, m.news_id, a.deepseek_sentiment,
           a.entity_pair_sentiments IS NOT NULL as has_eps
    FROM event_coref_members m
    LEFT JOIN news_ai_analysis a ON m.news_id = a.news_id
    JOIN event_coref_clusters c ON m.cluster_id = c.cluster_id
    WHERE c.initiator = 'US' AND c.target = 'Iran'
      AND a.deepseek_sentiment IS NOT NULL
    LIMIT 10;
""")
print("\n=== US→Iran members with deepseek_sentiment ===")
cols = [d[0] for d in cur.description]
print(" | ".join(f"{c:25s}" for c in cols))
for r in cur.fetchall():
    print(" | ".join(f"{str(v)[:23]:25s}" for v in r))

# Check if news_ai_analysis.event_type, event_initiator, event_target correlate with clusters
cur.execute("""
    SELECT a.event_type, a.event_initiator, a.event_target,
           c.event_type as cluster_etype, c.initiator, c.target
    FROM event_coref_members m
    JOIN event_coref_clusters c ON m.cluster_id = c.cluster_id
    LEFT JOIN news_ai_analysis a ON m.news_id = a.news_id
    WHERE c.initiator = 'US' AND c.target = 'Iran'
      AND a.event_type IS NOT NULL
    LIMIT 10;
""")
print("\n=== news_ai_analysis event fields vs cluster fields (US→Iran) ===")
cols = [d[0] for d in cur.description]
print(" | ".join(f"{c:25s}" for c in cols))
for r in cur.fetchall():
    print(" | ".join(f"{str(v)[:23]:25s}" for v in r))

conn.close()
