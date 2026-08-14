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

# news_ai_analysis schema
print("=" * 80)
print("news_ai_analysis FULL SCHEMA")
print("=" * 80)
cur.execute("""
    SELECT column_name, data_type, is_nullable
    FROM information_schema.columns
    WHERE table_name = 'news_ai_analysis'
    ORDER BY ordinal_position;
""")
for r in cur.fetchall():
    print(f"  {r[0]:35s} {r[1]:25s} {r[2]:10s}")

# Sample entity_pair_sentiments from news_ai_analysis where relevant to US/Iran
print("\n--- Sample entity_pair_sentiments from news_ai_analysis (US/Iran related) ---")
cur.execute("""
    SELECT news_id, entity_pair_sentiments, deepseek_sentiment
    FROM news_ai_analysis
    WHERE entity_pair_sentiments IS NOT NULL
      AND entity_pair_sentiments::text LIKE '%Iran%'
    LIMIT 10;
""")
rows = cur.fetchall()
print(f"Found {len(rows)} rows")
for r in rows:
    print(f"\nnews_id={r[0]}, deepseek_sentiment={r[2]}")
    if r[1]:
        eps = r[1] if isinstance(r[1], dict) else json.loads(r[1])
        print(f"  entity_pair_sentiments keys: {list(eps.keys()) if isinstance(eps, dict) else 'non-dict'}")
        # Print relevant pairs
        if isinstance(eps, dict):
            for k, v in eps.items():
                if 'iran' in k.lower() or 'us' in k.lower() or 'america' in k.lower():
                    print(f"    {k}: {v}")

# Check news_analysis schema
print("\n" + "=" * 80)
print("news_analysis FULL SCHEMA")
print("=" * 80)
cur.execute("""
    SELECT column_name, data_type, is_nullable
    FROM information_schema.columns
    WHERE table_name = 'news_analysis'
    ORDER BY ordinal_position;
""")
for r in cur.fetchall():
    print(f"  {r[0]:35s} {r[1]:25s} {r[2]:10s}")

# Sample entity_pair_sentiments from news_analysis
print("\n--- Sample entity_pair_sentiments from news_analysis (US/Iran) ---")
cur.execute("""
    SELECT news_id, entity_pair_sentiments
    FROM news_analysis
    WHERE entity_pair_sentiments IS NOT NULL
      AND entity_pair_sentiments::text LIKE '%Iran%'
    LIMIT 5;
""")
for r in cur.fetchall():
    print(f"\nnews_id={r[0]}")
    if r[1]:
        eps = r[1] if isinstance(r[1], dict) else json.loads(r[1])
        if isinstance(eps, dict):
            for k, v in eps.items():
                if 'iran' in k.lower() or 'us' in k.lower() or 'america' in k.lower():
                    print(f"    {k}: {v}")

# Look for trigger_verb or location in any table
print("\n" + "=" * 80)
print("Searching for trigger_verb / location columns across all tables")
print("=" * 80)
cur.execute("""
    SELECT table_name, column_name, data_type
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND (column_name LIKE '%trigger%' OR column_name LIKE '%verb%'
           OR column_name LIKE '%tone%' OR column_name LIKE '%location%'
           OR column_name LIKE '%sentiment%')
    ORDER BY table_name, column_name;
""")
for r in cur.fetchall():
    print(f"  {r[0]:30s} {r[1]:35s} {r[2]:20s}")

# Count total US/Iran clusters with detailed breakdown
print("\n" + "=" * 80)
print("US→Iran vs Iran→US comparison")
print("=" * 80)
cur.execute("""
    SELECT initiator, target,
           COUNT(*) as clusters,
           SUM(article_count) as total_articles
    FROM event_coref_clusters
    WHERE (initiator = 'US' AND target = 'Iran')
       OR (initiator = 'Iran' AND target = 'US')
    GROUP BY initiator, target;
""")
for r in cur.fetchall():
    print(f"  {r[0]:20s} -> {r[1]:20s}: {r[2]:6d} clusters, {r[3]:6d} articles")

# Look at event_type distribution for both directions
print("\n--- Event type distribution for US→Iran vs Iran→US ---")
cur.execute("""
    SELECT initiator, target, event_type, COUNT(*) as cnt
    FROM event_coref_clusters
    WHERE (initiator = 'US' AND target = 'Iran')
       OR (initiator = 'Iran' AND target = 'US')
    GROUP BY initiator, target, event_type
    ORDER BY initiator, target, cnt DESC;
""")
print(f"{'Direction':25s} {'Event Type':30s} {'Count':>8s}")
for r in cur.fetchall():
    print(f"{r[0]:10s}->{r[1]:10s} {str(r[2]):30s} {r[3]:8d}")

# Show a US→Iran cluster with multiple members to understand its pattern
print("\n--- A multi-article US→Iran cluster (10_2870118, military, 3 articles) ---")
cur.execute("""
    SELECT c.cluster_id, c.event_type, c.article_count, c.title,
           m.news_id, m.trigger, m.published_at
    FROM event_coref_clusters c
    JOIN event_coref_members m ON c.cluster_id = m.cluster_id
    WHERE c.cluster_id = '10_2870118';
""")
cols = [d[0] for d in cur.description]
print(" | ".join(f"{c:25s}" for c in cols))
for r in cur.fetchall():
    print(" | ".join(f"{str(v)[:23]:25s}" for v in r))

# Check if there's any trigger data hiding in the news table
print("\n--- Sample news body snippets for US/Iran articles ---")
cur.execute("""
    SELECT n.id, n.title, n.abstract
    FROM event_coref_members m
    JOIN news n ON m.news_id = n.id
    JOIN event_coref_clusters c ON m.cluster_id = c.cluster_id
    WHERE c.initiator = 'US' AND c.target = 'Iran'
    LIMIT 5;
""")
for r in cur.fetchall():
    print(f"\n  news_id={r[0]}")
    print(f"  title:   {str(r[1])[:120]}")
    print(f"  abstract: {str(r[2])[:200]}")

conn.close()
