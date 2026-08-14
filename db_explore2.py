import psycopg2
from datetime import datetime
from scripts.db_runtime_config import require_database_password

conn = psycopg2.connect(
    host="192.168.207.171",
    port=54333,
    user="postgres",
    password=require_database_password(),
    dbname="globemind_news"
)
cur = conn.cursor()

# Q1: FULL schema of event_coref_clusters
print("=" * 80)
print("Q1: event_coref_clusters FULL SCHEMA")
print("=" * 80)
cur.execute("""
    SELECT column_name, data_type, is_nullable, character_maximum_length,
           COALESCE(column_default, '') as default_val
    FROM information_schema.columns
    WHERE table_name = 'event_coref_clusters'
    ORDER BY ordinal_position;
""")
print(f"{'#':4s} {'Column':30s} {'Type':20s} {'Nullable':8s} {'Default':20s}")
print("-" * 85)
for i, r in enumerate(cur.fetchall(), 1):
    print(f"{i:4d} {r[0]:30s} {r[1]:20s} {r[2]:8s} {str(r[3] or ''):20s}")

# Q2: FULL schema of event_coref_members
print("\n" + "=" * 80)
print("Q2: event_coref_members FULL SCHEMA")
print("=" * 80)
cur.execute("""
    SELECT column_name, data_type, is_nullable, character_maximum_length,
           COALESCE(column_default, '') as default_val
    FROM information_schema.columns
    WHERE table_name = 'event_coref_members'
    ORDER BY ordinal_position;
""")
print(f"{'#':4s} {'Column':30s} {'Type':20s} {'Nullable':8s} {'Default':20s}")
print("-" * 85)
for i, r in enumerate(cur.fetchall(), 1):
    print(f"{i:4d} {r[0]:30s} {r[1]:20s} {r[2]:8s} {str(r[3] or ''):20s}")

# Row counts
cur.execute("SELECT COUNT(*) FROM event_coref_clusters")
print(f"\nTotal clusters: {cur.fetchone()[0]}")
cur.execute("SELECT COUNT(*) FROM event_coref_members")
print(f"Total members: {cur.fetchone()[0]}")

# PRIMARY KEY info
cur.execute("""
    SELECT kcu.column_name, tc.constraint_type
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
      ON tc.constraint_name = kcu.constraint_name
    WHERE tc.table_name IN ('event_coref_clusters', 'event_coref_members')
      AND tc.table_schema = 'public'
    ORDER BY tc.table_name, tc.constraint_type;
""")
print("\n--- Constraints ---")
for r in cur.fetchall():
    print(f"  {r[1]:20s} on {r[0]}")

# Q3: 20 US→Iran clusters with event_type + tone progression
print("\n" + "=" * 80)
print("Q3: 20 US→Iran CLUSTERS (sorted by created_at) with event_type + article_count")
print("=" * 80)
cur.execute("""
    SELECT cluster_id, event_type, article_count, created_at
    FROM event_coref_clusters
    WHERE initiator = 'US' AND target = 'Iran'
    ORDER BY created_at DESC
    LIMIT 20;
""")
cols = [d[0] for d in cur.description]
print(" | ".join(f"{c:30s}" for c in cols))
print("-" * 100)
rows = cur.fetchall()
for row in rows:
    print(" | ".join(f"{str(v)[:28]:30s}" for v in row))

# Count by event_type
cur.execute("""
    SELECT event_type, COUNT(*) as cnt
    FROM event_coref_clusters
    WHERE initiator = 'US' AND target = 'Iran'
    GROUP BY event_type
    ORDER BY cnt DESC;
""")
print("\n--- US→Iran Event Type Distribution ---")
print(f"{'Event Type':30s} {'Count':>8s}")
for r in cur.fetchall():
    print(f"{str(r[0]):30s} {r[1]:8d}")

# Q4: What trigger_verb data is available - explore members with non-null triggers
print("\n" + "=" * 80)
print("Q4: TRIGGER VERB DATA AVAILABILITY")
print("=" * 80)

# Check trigger field in members table
cur.execute("""
    SELECT COUNT(*) as total,
           COUNT(trigger) as with_trigger,
           COUNT(*) FILTER (WHERE trigger IS NOT NULL AND trigger != 'None' AND trigger != '') as non_null_trigger
    FROM event_coref_members;
""")
r = cur.fetchone()
print(f"Total members: {r[0]}, with trigger field set (not None/null): {r[1]}, non-null non-empty: {r[2]}")

# Unique triggers in members
cur.execute("""
    SELECT trigger, COUNT(*) as cnt
    FROM event_coref_members
    WHERE trigger IS NOT NULL AND trigger != 'None' AND trigger != ''
    GROUP BY trigger
    ORDER BY cnt DESC
    LIMIT 30;
""")
print("\n--- Top 30 trigger values in event_coref_members ---")
print(f"{'Trigger':40s} {'Count':>8s}")
for r in cur.fetchall():
    print(f"{str(r[0]):40s} {r[1]:8d}")

# Check trigger_verb in other tables (news, news_analysis, etc.)
print("\n--- Checking trigger_verb in other tables ---")
for tbl in ['news', 'news_analysis', 'news_ai_analysis']:
    try:
        cur.execute(f"""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = '{tbl}'
            ORDER BY ordinal_position;
        """)
        cols = cur.fetchall()
        for c in cols:
            if 'trigger' in c[0].lower() or 'verb' in c[0].lower():
                print(f"  {tbl}.{c[0]} ({c[1]})")
    except Exception:
        pass

# Look for tone data
print("\n--- Checking tone-related fields ---")
for tbl in ['news', 'news_analysis', 'news_ai_analysis', 'event_coref_clusters', 'event_coref_members']:
    try:
        cur.execute(f"""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = '{tbl}'
              AND (column_name LIKE '%tone%' OR column_name LIKE '%sentiment%' OR column_name LIKE '%polarity%')
            ORDER BY ordinal_position;
        """)
        for c in cur.fetchall():
            print(f"  {tbl}.{c[0]} ({c[1]})")
    except Exception:
        pass

# Q5: Build a time-ordered US→Iran event chain
print("\n" + "=" * 80)
print("Q5: US→Iran EVENT CHAIN (all clusters sorted by time)")
print("=" * 80)

# Get ALL US→Iran clusters with members (joined) to show event_type + tone transitions
cur.execute("""
    SELECT c.cluster_id, c.event_type, c.article_count, c.created_at,
           m.news_id, m.trigger, m.published_at
    FROM event_coref_clusters c
    LEFT JOIN event_coref_members m ON c.cluster_id = m.cluster_id
    WHERE c.initiator = 'US' AND c.target = 'Iran'
    ORDER BY c.created_at, c.cluster_id
    LIMIT 50;
""")
cols = [d[0] for d in cur.description]
print(" | ".join(f"{c:25s}" for c in cols))
print("-" * 150)
for row in cur.fetchall():
    print(" | ".join(f"{str(v)[:23]:25s}" for v in row))

# Summary of event_type transitions in US→Iran
print("\n--- US→Iran Event Type Sequence Summary ---")
cur.execute("""
    SELECT event_type,
           MIN(created_at) as first_seen,
           MAX(created_at) as last_seen,
           COUNT(*) as cnt
    FROM event_coref_clusters
    WHERE initiator = 'US' AND target = 'Iran'
    GROUP BY event_type
    ORDER BY MIN(created_at);
""")
cols = [d[0] for d in cur.description]
print(" | ".join(f"{c:30s}" for c in cols))
print("-" * 100)
for r in cur.fetchall():
    print(" | ".join(f"{str(v)[:28]:30s}" for v in r))

# Also show the macro_event_coref tables
print("\n" + "=" * 80)
print("BONUS: macro_event_coref & macro_event_coref_members schemas")
print("=" * 80)
for tbl in ['macro_event_coref', 'macro_event_coref_members']:
    cur.execute(f"""
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name = '{tbl}'
        ORDER BY ordinal_position;
    """)
    print(f"\n--- {tbl} ---")
    for r in cur.fetchall():
        print(f"  {r[0]:30s} {r[1]:20s} {r[2]:10s}")

# Sample from news to see what trigger_verb/tone info exists at article level
print("\n" + "=" * 80)
print("BONUS: Sample from news table (relevant columns)")
print("=" * 80)
cur.execute("""
    SELECT column_name, data_type
    FROM information_schema.columns
    WHERE table_name = 'news'
    ORDER BY ordinal_position;
""")
print("\nAll columns in 'news':")
for r in cur.fetchall():
    print(f"  {r[0]:35s} {r[1]:20s}")

conn.close()
