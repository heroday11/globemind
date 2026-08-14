#!/usr/bin/env python3
"""
Match ECB+ documents to DB news articles via text similarity.

Strategy:
1. Extract clean key phrases from ECB+ text (URL segments, topic words, named entities)
2. Search DB title using ILIKE (fast with GIN trigram index)
3. Score candidates by how many key phrases/words match at word boundaries
4. Accept if combined similarity score > 0.3

Output: /root/data/globemind/data/ecbplus/ecb_to_db_mapping.json
"""

import json
import re
import sys
import os
from collections import defaultdict, Counter
from urllib.parse import urlparse

import psycopg2
import psycopg2.extras

from db_runtime_config import require_database_password

ECB_CORPUS_PATH = '/root/data/globemind/data/ecbplus/ecb_corpus.json'
GOLD_LAYER1_PATH = '/root/data/globemind/data/ecbplus/gold_layer1.jsonl'
OUTPUT_PATH = '/root/data/globemind/data/ecbplus/ecb_to_db_mapping.json'

DB_CONFIG = {
    'host': '192.168.207.171',
    'port': 54333,
    'dbname': 'globemind_news',
    'user': 'postgres',
}

STOP = frozenset(
    'a an the and or but in on at to for of with by from is are was were be been '
    'has have had do does did will would could should may might can shall not no '
    'its his her their our your this that these those i we you they he she it me '
    'my myself'.split()
)

GENERIC = frozenset(
    'article news story index html htm php asp cfm aspx shtml jsp page video blog '
    'amp www http https com org net edu gov local audio item node print rss feed '
    'atom category tag home default search result list view detail full single new '
    'old top bottom side bar header footer menu nav navigation content year month '
    'week day hour time number world state city area place part way back also just '
    'even still well much many some any each every general very been last most over '
    'before after between through another around about above below during without '
    'within along going getting looking making taking coming knowing saying thinking '
    'using seeing giving feeling keeping putting running standing sitting watching '
    'reading writing hearing calling speaking telling asking answering helping '
    'showing trying starting stopping moving working playing living dying eating '
    'drinking sleeping waiting caring needing wanting loving hating missing finding '
    'losing winning buying selling paying sending receiving building growing '
    'breaking changing beginning ending opening closing turning running cutting '
    'killing'.split()
)


def get_db_connection():
    conn = psycopg2.connect(**DB_CONFIG, password=require_database_password())
    conn.set_client_encoding('UTF8')
    return conn


def extract_queries(raw_text, topic, max_queries=8):
    """
    Extract search queries from ECB+ document.
    Returns list of (query, score_weight) tuples, ordered by specificity.

    score_weight indicates how much a match of this query contributes to the total.
    """
    queries = []  # list of (phrase_text, weight, is_multi_word)

    m = re.match(r'(https?://\S+)', raw_text)
    url = m.group(1) if m else ''

    if url:
        try:
            path = urlparse(url).path if '://' in url else ''
        except Exception:
            path = ''

        # Get last path segment (most specific)
        segments = [s for s in path.split('/') if s and len(s) > 3]
        for seg in segments[-1:]:
            words = [
                w for w in re.split(r'[\-_.]', seg)
                if len(w) > 2 and not re.match(r'^\d+$', w)
                and w.lower() not in STOP and w.lower() not in GENERIC
            ]
            # Multi-word phrases from adjacent word pairs
            for i in range(len(words) - 1):
                wt = words[i].lower()
                wt2 = words[i + 1].lower()
                queries.append((f"{wt} {wt2}", 0.7, True))
            # Single words (entity-level, not generic)
            for w in words:
                wl = w.lower()
                if wl not in GENERIC and len(wl) > 3:
                    queries.append((wl, 0.35, False))

    # Topic-derived keywords
    topic_words = topic.split('_')
    for w in topic_words:
        wl = w.lower()
        if len(wl) > 4 and wl not in GENERIC:
            queries.append((wl, 0.25, False))

    # Named entities from raw text (multi-word capitalized sequences)
    text_body = raw_text.split(' ', 1)[-1] if ' ' in raw_text else raw_text
    entities = re.findall(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)', text_body)
    for e in entities:
        e_words = e.split()
        if 2 <= len(e_words) <= 4 and len(e) < 50:
            el = e.lower()
            queries.append((el, 0.5, True))

    # Individual capitalized words appearing 2+ times (these are likely entities)
    cap_words = re.findall(r'\b[A-Z][a-z]{2,}\b', text_body)
    cap_counter = Counter(cap_words)
    for w, c in cap_counter.items():
        wl = w.lower()
        if c >= 2 and wl not in GENERIC and wl not in STOP and len(wl) > 3:
            queries.append((wl, 0.3, False))

    # Deduplicate keeping highest weight
    seen = {}
    for q in queries:
        key = q[0]
        if key not in seen or q[1] > seen[key][0]:
            seen[key] = (q[1], q[2])

    # Sort by weight descending, then length descending
    # seen.items() -> (key, (weight, is_multi))
    result = sorted(seen.items(), key=lambda x: (-x[1][0], -len(x[0])))
    # Flatten to list of (query_text, weight, is_multi) tuples
    flat = [(k, v[0], v[1]) for k, v in result]
    return flat[:max_queries]


def word_matches_in_text(word, text_lower):
    """Check if word appears as a whole word (word boundary) in text."""
    return bool(re.search(r'\b' + re.escape(word) + r'\b', text_lower))


def score_candidate(queries, title):
    """
    Score a DB article title against ECB+ queries.

    Each query contributes its weight if it matches in the title.
    Multi-word queries (is_multi=True) require ALL words to match as whole words.
    Single-word queries require the word to match as a whole word.

    Returns score in [0, 1].
    """
    if not title:
        return 0.0

    title_lower = title.lower()
    total_weight = 0.0
    matched_weight = 0.0

    for query_text, weight, is_multi in queries:
        total_weight += weight

        if is_multi:
            # Multi-word: ALL words must appear as whole words
            words = query_text.split()
            if all(word_matches_in_text(w, title_lower) for w in words):
                matched_weight += weight
        else:
            # Single word: must appear as whole word
            if word_matches_in_text(query_text, title_lower):
                matched_weight += weight

    if total_weight == 0:
        return 0.0

    return matched_weight / total_weight


def main():
    print("[1] Loading ECB+ corpus...", flush=True)
    with open(ECB_CORPUS_PATH) as f:
        corpus = json.load(f)['documents']
    print(f"    {len(corpus)} documents loaded", flush=True)

    print("[2] Loading gold_layer1...", flush=True)
    gold_entries = []
    with open(GOLD_LAYER1_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                gold_entries.append(json.loads(line))
    print(f"    {len(gold_entries)} gold entries loaded", flush=True)

    # Build mention lookup: gold_article_id -> list of cluster_ids
    gold_mentions = defaultdict(list)
    for e in gold_entries:
        gold_mentions[e['article_id']].append(e['cluster_id'])

    # Build article_id ranges for each ECB+ doc
    # Each doc covers [gold_article_id, next_gold_article_id - 1]
    sorted_docs = sorted(corpus, key=lambda d: d['gold_article_id'])
    doc_ranges = {}
    for i, d in enumerate(sorted_docs):
        start = d['gold_article_id']
        if i + 1 < len(sorted_docs):
            end = sorted_docs[i + 1]['gold_article_id'] - 1
        else:
            end = max(e['article_id'] for e in gold_entries)
        doc_ranges[d['gold_article_id']] = (start, end)

    print("[3] Connecting to DB...", flush=True)
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    results = []
    matched_count = 0
    total = len(corpus)

    for idx, doc in enumerate(corpus):
        ecb_doc_id = doc['ecb_doc_id']
        topic = doc['topic']
        raw_text = doc['full_text']
        gold_id = doc['gold_article_id']

        if (idx + 1) % 25 == 0 or idx == 0:
            print(f"    Processing {idx+1}/{total} ({ecb_doc_id})", flush=True)

        # Extract queries
        queries = extract_queries(raw_text, topic, max_queries=8)

        if not queries:
            results.append({
                'ecb_doc_id': ecb_doc_id,
                'topic': topic,
                'gold_article_id': gold_id,
                'db_article_id': None,
                'match_method': 'unmatched',
                'match_score': None,
                'mention_count': len(gold_mentions.get(gold_id, [])),
            })
            continue

        # Search DB using each query
        all_candidates = {}
        best_candidate = None
        best_score = 0.0

        for query_text, weight, is_multi in queries:
            if len(query_text) < 3:
                continue

            like_pattern = f'%{query_text}%'
            try:
                cur.execute(
                    "SELECT id, title FROM news WHERE language='en' AND title ILIKE %s LIMIT 15",
                    (like_pattern,)
                )
                rows = cur.fetchall()
            except Exception as e:
                print(f"    Query error '{query_text}': {e}", flush=True)
                continue

            for row in rows:
                cid = row['id']
                ctitle = row['title'] or ''
                if cid not in all_candidates:
                    all_candidates[cid] = ctitle

            # If we found high-quality candidates, narrow down
            if len(all_candidates) >= 30:
                break

        # Score all candidates
        for cid, ctitle in all_candidates.items():
            score = score_candidate(queries, ctitle)
            if score > best_score:
                best_score = score
                best_candidate = cid

        # Also try body search if title search gave nothing
        if not best_candidate:
            for query_text, weight, is_multi in queries[:3]:
                try:
                    cur.execute(
                        "SELECT id, title FROM news WHERE language='en' AND body ILIKE %s LIMIT 10",
                        (f'%{query_text}%',)
                    )
                    for row in cur.fetchall():
                        cid = row['id']
                        ctitle = row['title'] or ''
                        if cid not in all_candidates:
                            all_candidates[cid] = ctitle
                            score = score_candidate(queries, ctitle)
                            if score > best_score:
                                best_score = score
                                best_candidate = cid
                except Exception as e:
                    continue

        accepted = best_score >= 0.3
        if accepted:
            matched_count += 1

        entry = {
            'ecb_doc_id': ecb_doc_id,
            'ecb_filename': os.path.basename(doc.get('file_path', '')),
            'ecb_doc_name': os.path.basename(doc.get('file_path', '')),
            'topic': topic,
            'db_article_id': best_candidate,
            'gold_article_id': gold_id,
            'mention_count': len(gold_mentions.get(gold_id, [])),
            'match_method': 'text_similarity' if accepted else 'unmatched',
            'match_score': round(best_score, 4) if best_score > 0 else None,
        }
        results.append(entry)

    conn.close()

    # Build article_id ranges for coverage counting
    # Each matched gold_id covers [start, end] range
    gold_id_to_range = {}
    sorted_docs_for_range = sorted(corpus, key=lambda d: d['gold_article_id'])
    for i, d in enumerate(sorted_docs_for_range):
        start = d['gold_article_id']
        if i + 1 < len(sorted_docs_for_range):
            end = sorted_docs_for_range[i + 1]['gold_article_id'] - 1
        else:
            end = max(e['article_id'] for e in gold_entries)
        gold_id_to_range[d['gold_article_id']] = (start, end)

    # Build mapping dictionaries
    db_to_gold_ids = defaultdict(list)
    gold_to_db = {}
    mapped_article_id_set = set()  # all article_ids covered by matched docs

    for entry in results:
        db_id = entry['db_article_id']
        gold_id = entry['gold_article_id']
        # Only count as mapped if score >= 0.3 threshold
        if db_id is not None and entry['match_method'] == 'text_similarity':
            # Get the range of article_ids for this ECB+ doc
            start, end = gold_id_to_range.get(gold_id, (gold_id, gold_id))
            # Map the primary gold_id
            db_to_gold_ids[str(db_id)].append(str(gold_id))
            if str(gold_id) not in gold_to_db:
                gold_to_db[str(gold_id)] = db_id
            # Add all article_ids in the range to mapped set
            for aid in range(start, end + 1):
                mapped_article_id_set.add(aid)

    # Count gold_layer1 mentions covered (using ranges)
    mapped_mentions = sum(
        1 for e in gold_entries if e['article_id'] in mapped_article_id_set
    )

    stats = {
        'total_ecb_docs': total,
        'matched_docs': matched_count,
        'match_rate': round(matched_count / total, 4) if total else 0.0,
        'total_gold_mentions': len(gold_entries),
        'mapped_mentions': mapped_mentions,
        'mention_coverage_rate': round(mapped_mentions / len(gold_entries), 4)
        if gold_entries else 0.0,
    }

    output = {
        'metadata': {
            'description': 'ECB+ to DB article_id cross-reference mapping',
            'gold_file': GOLD_LAYER1_PATH,
            'total_ecb_docs': total,
            'total_gold_entries': len(gold_entries),
            'matched_docs': matched_count,
            'unmatched_docs': total - matched_count,
        },
        'mapping_stats': stats,
        'db_to_gold_ids': dict(db_to_gold_ids),
        'gold_to_db': gold_to_db,
        'entries': results,
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, 'w') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n[+] Mapping written to {OUTPUT_PATH}", flush=True)
    print(f"[+] Stats: {json.dumps(stats, indent=2)}", flush=True)


if __name__ == '__main__':
    main()
