#!/usr/bin/env python3
"""
ECB+ to DB article_id mapping script.

Builds a cross-reference between ECB+ gold sequential article_ids (0..31816)
and the DB article_ids used by the pipeline (2,709,428..5,576,397).

The gold file uses sequential integers as mention-level identifiers. The
pipeline output uses DB-native article_ids at the document level. This script:

1. Parses ECB+ XML annotation files to extract document structure and text
2. Rebuilds the gold article_id → mention_id mapping
3. Extracts text signatures from each ECB+ document for matching
4. Tries to match ECB+ documents to DB articles via URL fragment / text overlap
5. Creates a cross-reference file: ecb_to_db_mapping.json

Output format (ecb_to_db_mapping.json):
{
  "ecb_doc_id -> db_article_id": {
    "ecb_doc_id": "1_1ecbplus",
    "ecb_doc_name": "1_1ecbplus.xml",
    "topic": 1,
    "db_article_id": 5537793,
    "gold_article_ids": [3431, 3439, ...],
    "mention_count": 64,
    "text_snippet": "Lindsay Lohan Leaves Betty...",
    "url_fragment": "www.accesshollywood.com/lindsay-lohan...",
    "match_method": "url_fragment" | "text_overlap" | "unmatched"
  },
  ...
}
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from xml.etree import ElementTree as ET

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("map_ecb_to_db")

# ── Paths ──────────────────────────────────────────────────
REPO = Path(__file__).resolve().parent.parent
DATA_DIR = REPO / "data"
ECB_PLUS_DIR = DATA_DIR / "ecbplus"
ECB_PLUS_ECB_DIR = ECB_PLUS_DIR / "ECB+"
GOLD_L1_PATH = ECB_PLUS_DIR / "gold_layer1.jsonl"
OUTPUT_MAPPING = ECB_PLUS_DIR / "ecb_to_db_mapping.json"
CURATED_DATASET = DATA_DIR / "curated_100k_geopolitical.jsonl"
PIPELINE_OUTPUT = DATA_DIR / "event_coref_mapping_layer1.jsonl"


# ── Step 1: Parse ECB+ XML ────────────────────────────────

def parse_ecbplus_documents() -> List[Dict[str, Any]]:
    """Parse all ECB+ XML annotation files and extract document info."""
    docs = []
    ecb_root = ECB_PLUS_ECB_DIR

    for topic_name in sorted(os.listdir(ecb_root)):
        topic_path = os.path.join(ecb_root, topic_name)
        if not os.path.isdir(topic_path) or not topic_name.isdigit():
            continue
        topic = int(topic_name)
        for xml_file in sorted(os.listdir(topic_path)):
            if not (xml_file.endswith('.xml') and 'ecbplus' in xml_file):
                continue
            filepath = os.path.join(topic_path, xml_file)
            try:
                tree = ET.parse(filepath)
                root = tree.getroot()
            except Exception as e:
                logger.warning("Failed to parse %s: %s", xml_file, e)
                continue

            doc_name = root.get('doc_name', '')
            doc_id_str = root.get('doc_id', '')
            doc_stem = xml_file.replace('.xml', '')

            # Collect all tokens and reconstruct text
            tokens = []
            for tok in root.findall('token'):
                tokens.append(tok.text or '')

            # Reconstruct full text
            full_text = ' '.join(tokens)

            # Extract URL (everything up to "article _ NNNNN" pattern)
            url_parts = []
            article_number = None
            for i, t in enumerate(tokens):
                if t == 'article' and i + 2 < len(tokens) and tokens[i+1] == '_' and tokens[i+2].isdigit():
                    article_number = int(tokens[i+2])
                    break
                url_parts.append(t)
            url_fragment = ' '.join(url_parts) if url_parts else full_text[:200]

            # Extract title (the non-URL part, usually after the URL)
            # The title typically starts after the article_number in the URL
            title_tokens = []
            in_title = False
            for t in tokens:
                if t == 'article':
                    in_title = True
                    continue
                if in_title:
                    if t.lstrip('-').isdigit() and len(t.lstrip('-')) < 10:
                        # Skip article number itself
                        continue
                    title_tokens.append(t)
            title = ' '.join(title_tokens).strip()
            if not title:
                # Fallback: take tokens after URL (from sentence 1)
                sentence_texts = []
                current_sent = []
                for t in tokens:
                    if t in ('.', '!', '?'):
                        if current_sent:
                            sentence_texts.append(' '.join(current_sent))
                            current_sent = []
                    else:
                        current_sent.append(t)
                if current_sent:
                    sentence_texts.append(' '.join(current_sent))
                # Skip URLs (first few words starting with http)
                for s in sentence_texts:
                    if not s.startswith('http') and len(s) > 20:
                        title = s[:100]
                        break

            # Collect mentions from Markables
            mentions = []
            markables = root.find('Markables')
            if markables is not None:
                for ev in markables:
                    mention_id = ev.get('m_id', '')
                    # Get trigger text
                    trigger = ''
                    for child in ev:
                        if child.tag == 'token_anchor':
                            tid = child.get('t_id', '')
                            token_idx = int(tid) - 1  # t_id is 1-indexed
                            if 0 <= token_idx < len(tokens):
                                if trigger:
                                    trigger += ' '
                                trigger += tokens[token_idx]
                    mention_type = ev.tag
                    mentions.append({
                        'mention_id': mention_id,
                        'type': mention_type,
                        'trigger': trigger,
                    })

            docs.append({
                'topic': topic,
                'filename': xml_file,
                'doc_stem': doc_stem,
                'doc_name': doc_name,
                'doc_id_str': doc_id_str,
                'full_text': full_text,
                'text_snippet': full_text[:200],
                'url_fragment': url_fragment[:200],
                'title': title[:150] if title else '',
                'article_number': article_number,
                'mentions': mentions,
                'mention_count': len(mentions),
                'n_tokens': len(tokens),
            })

    logger.info("Parsed %d ECB+ documents", len(docs))
    return docs


# ── Step 2: Rebuild gold article_id → mention_id mapping ──

def rebuild_gold_mapping(docs: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    """Rebuild the mapping from gold article_ids (sequential) to mention info.

    The gold file was generated by corpus_to_l1_jsonl() which:
    1. Creates mention_ids = "{doc_stem}::{mention_id}"
    2. Sorts all mention_ids lexicographically
    3. Assigns sequential integers as article_ids (0..N-1)

    Returns: {gold_article_id: {"doc_stem": ..., "mention_id": ..., "topic": ...}}
    """
    # Collect all mention_ids and their metadata
    mention_entries = []
    for doc in docs:
        doc_stem = doc['doc_stem']
        for m in doc['mentions']:
            mention_id_str = m['mention_id']
            full_mid = f"{doc_stem}::{mention_id_str}"
            mention_entries.append({
                'full_mid': full_mid,
                'doc_stem': doc_stem,
                'mention_id': mention_id_str,
                'type': m['type'],
                'trigger': m['trigger'],
                'topic': doc['topic'],
                'filename': doc['filename'],
            })

    # Sort lexicographically by full_mid (same as corpus_to_l1_jsonl)
    mention_entries.sort(key=lambda x: x['full_mid'])

    # Assign sequential article_ids
    gold_to_mention = {}
    for i, entry in enumerate(mention_entries):
        gold_to_mention[i] = {
            'doc_stem': entry['doc_stem'],
            'mention_id': entry['mention_id'],
            'mention_type': entry['type'],
            'trigger': entry['trigger'],
            'topic': entry['topic'],
            'filename': entry['filename'],
            'full_mid': entry['full_mid'],
        }

    logger.info("Rebuilt gold mapping: %d article_ids -> %d mention_ids",
                len(gold_to_mention), len(set(e['full_mid'] for e in gold_to_mention.values())))
    return gold_to_mention


# ── Step 3: Group gold article_ids by document ────────────

def group_gold_ids_by_doc(
    gold_to_mention: Dict[int, Dict[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    """Group gold article_ids by ECB+ document stem.

    Returns: {doc_stem: {"gold_article_ids": [...], "mention_count": int, ...}}
    """
    doc_map = defaultdict(list)
    doc_info = {}

    for gold_id, info in gold_to_mention.items():
        doc_stem = info['doc_stem']
        doc_map[doc_stem].append(gold_id)

    for doc_stem, gold_ids in doc_map.items():
        doc_info[doc_stem] = {
            'gold_article_ids': sorted(gold_ids),
            'mention_count': len(gold_ids),
        }

    logger.info("Grouped gold IDs: %d documents", len(doc_info))
    return doc_info


# ── Step 4: Try to match ECB+ documents to DB articles ────

def load_pipeline_articles() -> Dict[int, Dict[str, Any]]:
    """Load pipeline output to get DB article_ids used in evaluation."""
    db_articles = {}
    if not PIPELINE_OUTPUT.exists():
        logger.warning("Pipeline output not found at %s", PIPELINE_OUTPUT)
        return db_articles

    with open(PIPELINE_OUTPUT) as f:
        for line in f:
            item = json.loads(line)
            aid = item['article_id']
            cluster_id = item['cluster_id']
            # The cluster_id contains a representative article_id after '_'
            if aid not in db_articles:
                db_articles[aid] = {
                    'article_id': aid,
                    'cluster_ids': set(),
                }
            db_articles[aid]['cluster_ids'].add(cluster_id)

    logger.info("Loaded %d unique DB article_ids from pipeline output", len(db_articles))
    return db_articles


def load_curated_dataset() -> List[Dict[str, Any]]:
    """Load the curated geopolitical dataset for text matching."""
    articles = []
    if not CURATED_DATASET.exists():
        logger.warning("Curated dataset not found at %s", CURATED_DATASET)
        return articles

    with open(CURATED_DATASET) as f:
        for line in f:
            item = json.loads(line)
            articles.append({
                'id': item.get('id'),
                'article_id': item.get('id'),  # use 'id' as surrogate article_id
                'title': item.get('title', ''),
                'body': item.get('body', ''),
                'url': item.get('url', ''),
                'published_at': item.get('published_at', ''),
            })

    logger.info("Loaded %d articles from curated dataset", len(articles))
    return articles


def normalize_text(text: str) -> str:
    """Normalize text for comparison."""
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def compute_text_overlap(text_a: str, text_b: str) -> float:
    """Compute Jaccard-like text overlap score."""
    a_words = set(normalize_text(text_a).split())
    b_words = set(normalize_text(text_b).split())

    if not a_words or not b_words:
        return 0.0

    intersection = a_words & b_words
    union = a_words | b_words
    return len(intersection) / len(union)


def extract_url_domain_fragment(url: str) -> str:
    """Extract a meaningful domain+path fragment from a URL for matching."""
    # Remove protocol
    url = re.sub(r'^https?://', '', url)
    url = re.sub(r'^www\.', '', url)
    # Get domain + first path segment
    parts = url.split('/')
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return parts[0]


def match_ecb_to_db_by_url(
    ecb_docs: List[Dict[str, Any]],
    curated_articles: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Try to match ECB+ documents to curated dataset articles by URL fragment."""
    matches = {}

    # Build index: URL domain+path -> article
    curated_by_url = {}
    for art in curated_articles:
        url = art.get('url', '')
        if url:
            fragment = extract_url_domain_fragment(url)
            if fragment:
                curated_by_url.setdefault(fragment, []).append(art)

    # For each ECB+ document, try to find URL matches
    for doc in ecb_docs:
        ecb_url = doc['url_fragment']
        ecb_domain = extract_url_domain_fragment(ecb_url)

        best_match = None
        best_score = 0.0

        # Try matching by domain
        for fragment, arts in curated_by_url.items():
            if ecb_domain and ecb_domain.split('/')[0] in fragment:
                # Exact or partial domain match found
                for art in arts[:3]:  # check top 3
                    # Compute text overlap on title/body
                    ecb_title = doc.get('title', '')
                    art_title = art.get('title', '')
                    art_body = art.get('body', '')

                    title_overlap = compute_text_overlap(ecb_title, art_title)
                    body_overlap = compute_text_overlap(
                        doc['text_snippet'][:300],
                        (art_title + ' ' + art_body)[:500]
                    )
                    score = max(title_overlap, body_overlap)
                    if score > best_score:
                        best_score = score
                        best_match = art

        if best_match and best_score > 0.2:
            matches[doc['doc_stem']] = {
                'db_article_id': best_match['article_id'],
                'match_method': 'url_and_text',
                'match_score': round(best_score, 4),
                'matched_title': best_match['title'][:100],
            }

    logger.info("URL matching: %d matches found", len(matches))
    return matches


def match_ecb_to_db_by_text(
    ecb_docs: List[Dict[str, Any]],
    curated_articles: List[Dict[str, Any]],
    threshold: float = 0.15,
) -> Dict[str, Dict[str, Any]]:
    """Try to match ECB+ documents to curated dataset articles by text overlap.

    Uses a simplified approach: compare text snippets from ECB+ docs with
    article titles/bodies from the curated dataset.
    """
    matches = {}

    # Build text index from curated articles (title words -> articles)
    # Limit to articles that might match (same domain/news source)
    # Since ECB+ is from 2013, filter articles from that era
    curated_by_year = {'2013': [], '2014': [], '': []}
    for art in curated_articles:
        pub = art.get('published_at', '')
        year_key = pub[:4] if len(pub) >= 4 else ''
        if year_key in ('2013', '2014'):
            curated_by_year.setdefault(year_key, []).append(art)
        curated_by_year[''].append(art)  # all articles

    for doc in ecb_docs:
        ecb_snippet = doc['text_snippet'][:300]
        ecb_title = doc.get('title', '')

        best_match = None
        best_score = 0.0

        # Try matching against 2013/2014 articles first, then all
        candidate_pool = curated_by_year.get('2013', []) + curated_by_year.get('2014', [])
        if not candidate_pool:
            candidate_pool = curated_articles[:5000]  # sample if no year-matched

        for art in candidate_pool[:2000]:  # limit for performance
            art_text = art.get('title', '') + ' ' + (art.get('body', '') or '')[:500]
            # Compute text overlap
            score = compute_text_overlap(ecb_snippet, art_text)

            # Also check title-specific overlap
            if ecb_title:
                title_score = compute_text_overlap(ecb_title, art.get('title', ''))
                score = max(score, title_score * 1.2)  # slight bonus for title match

            if score > best_score:
                best_score = score
                best_match = art

        if best_match and best_score >= threshold:
            matches[doc['doc_stem']] = {
                'db_article_id': best_match['article_id'],
                'match_method': 'text_overlap',
                'match_score': round(best_score, 4),
                'matched_title': best_match['title'][:100],
            }

    logger.info("Text matching: %d matches found (threshold=%.2f)", len(matches), threshold)
    return matches


# ── Step 5: Build cross-reference mapping ─────────────────

def build_cross_reference(
    docs: List[Dict[str, Any]],
    gold_to_mention: Dict[int, Dict[str, Any]],
    doc_gold_ids: Dict[str, Dict[str, Any]],
    db_matches: Dict[str, Dict[str, Any]],
    db_articles: Dict[int, Dict[str, Any]],
) -> Dict[str, Any]:
    """Build the final cross-reference mapping file.

    For each ECB+ document, create a mapping to DB article_ids.
    Combines information from all sources.
    """
    # Build doc_stem -> doc lookup
    doc_by_stem = {doc['doc_stem']: doc for doc in docs}

    mapping = {
        "metadata": {
            "description": "ECB+ to DB article_id cross-reference mapping",
            "gold_file": str(GOLD_L1_PATH),
            "pipeline_output": str(PIPELINE_OUTPUT),
            "total_ecb_docs": len(docs),
            "total_gold_entries": len(gold_to_mention),
            "total_db_articles": len(db_articles),
            "matched_docs": len(db_matches),
        },
        "entries": [],
    }

    total_gold_ids_mapped = 0
    unmatched_docs = 0

    for doc in docs:
        doc_stem = doc['doc_stem']
        doc_info = doc_gold_ids.get(doc_stem, {})
        gold_ids = doc_info.get('gold_article_ids', [])

        match_info = db_matches.get(doc_stem, {})

        entry = {
            "ecb_doc_id": doc_stem,
            "ecb_filename": doc['filename'],
            "ecb_doc_name": doc['doc_name'],
            "topic": doc['topic'],
            "db_article_id": match_info.get('db_article_id'),
            "gold_article_ids": gold_ids,
            "mention_count": len(gold_ids),
            "text_snippet": doc['text_snippet'][:150],
            "title": doc['title'],
            "url_fragment": doc['url_fragment'][:150],
            "match_method": match_info.get('match_method', 'unmatched'),
            "match_score": match_info.get('match_score'),
            "matched_title": match_info.get('matched_title'),
        }

        if match_info.get('db_article_id'):
            total_gold_ids_mapped += len(gold_ids)
        else:
            unmatched_docs += 1

        mapping["entries"].append(entry)

    # Summary counts
    mapping["metadata"]["total_gold_ids_mapped"] = total_gold_ids_mapped
    mapping["metadata"]["unmatched_docs"] = unmatched_docs
    mapping["metadata"]["matched_docs"] = len(db_matches)

    # Build inverted index: db_article_id -> list of gold article_ids
    inverted = defaultdict(list)
    for entry in mapping["entries"]:
        db_id = entry.get("db_article_id")
        if db_id is not None:
            for gid in entry["gold_article_ids"]:
                inverted[db_id].append(gid)
    mapping["db_to_gold_ids"] = {str(k): sorted(v) for k, v in inverted.items()}

    # Build inverted index: gold article_id -> db_article_id
    gold_to_db = {}
    for entry in mapping["entries"]:
        db_id = entry.get("db_article_id")
        if db_id is not None:
            for gid in entry["gold_article_ids"]:
                gold_to_db[gid] = db_id
    mapping["gold_to_db"] = {str(k): v for k, v in gold_to_db.items()}

    logger.info(
        "Cross-reference built: %d entries, %d matched, %d unmatched, %d gold IDs mapped",
        len(mapping["entries"]),
        len(db_matches),
        unmatched_docs,
        total_gold_ids_mapped,
    )

    return mapping


# ── Step 6: Create direct ECB+ doc_id to gold article_id mapping ──

def create_doc_to_gold_mapping(
    docs: List[Dict[str, Any]],
    gold_to_mention: Dict[int, Dict[str, Any]],
) -> Dict[str, Any]:
    """Create a direct mapping from ECB+ document stems to gold article_ids.

    This is the ground-truth mapping that doesn't depend on DB matching.
    It maps each ECB+ document to all its gold article_ids (mentions).
    """
    doc_to_gold = defaultdict(list)
    for gold_id, info in gold_to_mention.items():
        doc_stem = info['doc_stem']
        doc_to_gold[doc_stem].append({
            'gold_article_id': gold_id,
            'mention_id': info['mention_id'],
            'mention_type': info['mention_type'],
            'trigger': info['trigger'],
        })

    result = {}
    for doc_stem, entries in doc_to_gold.items():
        result[doc_stem] = {
            'gold_article_ids': sorted([e['gold_article_id'] for e in entries]),
            'mentions': entries,
        }

    return result


# ── Main ──────────────────────────────────────────────────

def main():
    logger.info("=" * 60)
    logger.info("ECB+ to DB Mapping")
    logger.info("=" * 60)

    # Step 1: Parse ECB+ documents
    logger.info("Step 1: Parsing ECB+ XML documents...")
    docs = parse_ecbplus_documents()
    if not docs:
        logger.error("No ECB+ documents found. Check %s", ECB_PLUS_ECB_DIR)
        sys.exit(1)

    # Step 2: Rebuild gold mapping
    logger.info("Step 2: Rebuilding gold article_id -> mention_id mapping...")
    gold_to_mention = rebuild_gold_mapping(docs)
    logger.info("  Gold article IDs: %d", len(gold_to_mention))

    # Step 3: Group by document
    logger.info("Step 3: Grouping gold IDs by document...")
    doc_gold_ids = group_gold_ids_by_doc(gold_to_mention)
    logger.info("  Documents with gold entries: %d", len(doc_gold_ids))

    # Step 4: Load pipeline DB articles
    logger.info("Step 4: Loading pipeline output...")
    db_articles = load_pipeline_articles()
    logger.info("  DB article IDs: %d", len(db_articles))

    # Step 5: Build cross-reference with structural mapping only
    # No external DB text matching is available — all db_article_id fields remain null
    # and the eval script uses the mapping's doc-to-gold_id relationships.
    logger.info("Step 5: Building cross-reference (structural mapping only)...")
    mapping = build_cross_reference(
        docs, gold_to_mention, doc_gold_ids, {}, db_articles
    )

    # ── Post-process: compute textual signatures for future matching ──
    # Even though we can't match DB articles today, the mapping file contains
    # enough info (gold_article_ids per document) for the eval script to work
    # with DB IDs if they are supplied via the mapping's db_to_gold_ids index.

    # Step 6: Save
    logger.info("Step 6: Saving mapping to %s...", OUTPUT_MAPPING)
    OUTPUT_MAPPING.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_MAPPING, 'w', encoding='utf-8') as f:
        json.dump(mapping, f, indent=2, ensure_ascii=False)
    logger.info("Saved mapping file (%d bytes)", OUTPUT_MAPPING.stat().st_size)

    # Print summary
    print()
    print("=" * 60)
    print("  MAPPING SUMMARY")
    print("=" * 60)
    print(f"  ECB+ documents parsed:       {len(docs)}")
    print(f"  Gold article_ids (mentions): {len(gold_to_mention)}")
    print(f"  DB article_ids (pipeline):   {len(db_articles)}")
    print(f"  Documents in mapping:        {len(mapping['entries'])}")
    print(f"  Gold IDs mapped to docs:     {sum(len(e['gold_article_ids']) for e in mapping['entries'])}")
    print(f"  DB article matches:          none (DB article content unavailable)")
    print(f"  Mapping saved to:            {OUTPUT_MAPPING}")
    print()
    print(f"  IMPORTANT: DB article_id fields are null because we lack access to")
    print(f"  the pipeline's article database. The mapping file contains the")
    print(f"  structural relationship between gold article_ids (mentions) and")
    print(f"  ECB+ documents. To enable DB article_id → gold article_id conversion,")
    print(f"  populate the 'db_to_gold_ids' section of this mapping file with")
    print(f"  actual DB article_ids matched to ECB+ document texts.")
    print()

    # Print sample entries (structural)
    print(f"  Sample document entries:")
    shown = 0
    for entry in mapping["entries"]:
        if shown < 3:
            print(f"    {entry['ecb_doc_id']}: {entry['mention_count']} gold article_ids")
            print(f"      gold_ids: {entry['gold_article_ids'][:5]}...")
            print(f"      text: {entry['text_snippet'][:80]}")
            shown += 1

    return mapping


if __name__ == "__main__":
    main()
