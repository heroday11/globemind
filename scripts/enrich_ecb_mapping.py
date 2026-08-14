#!/usr/bin/env python3
"""
Enrich the ECB+ mapping file by attempting text matching between ECB+ documents
and available article datasets.

This script is a supplement to map_ecb_to_db.py. It tries harder to find
text matches using TF-IDF and n-gram overlap methods.

Usage:
    python scripts/enrich_ecb_mapping.py
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("enrich_ecb_mapping")

REPO = Path(__file__).resolve().parent.parent
ECB_PLUS_DIR = REPO / "data" / "ecbplus"
ECB_ECB_DIR = ECB_PLUS_DIR / "ECB+"
MAPPING_PATH = ECB_PLUS_DIR / "ecb_to_db_mapping.json"
CURATED = REPO / "data" / "curated_100k_geopolitical.jsonl"

# Token-based text extraction from ECB+ XML
def extract_ecb_texts() -> dict[str, str]:
    """Extract plain text from each ECB+ document (skipping URL tokens)."""
    texts = {}
    for topic_name in sorted(os.listdir(ECB_ECB_DIR)):
        topic_path = os.path.join(ECB_ECB_DIR, topic_name)
        if not os.path.isdir(topic_path) or not topic_name.isdigit():
            continue
        for xml_file in sorted(os.listdir(topic_path)):
            if not (xml_file.endswith('.xml') and 'ecbplus' in xml_file):
                continue
            try:
                tree = ET.parse(os.path.join(topic_path, xml_file))
                root = tree.getroot()
                tokens = [tok.text or '' for tok in root.findall('token')]
            except Exception:
                continue

            # Skip URL tokens (everything before the first sentence)
            # URL tokens typically end before a capital-letter word or after "article _ NNNNN"
            body_start = 0
            for i, t in enumerate(tokens):
                if i > 15 and t and t[0].isupper() and len(t) > 1:
                    # This looks like start of body text
                    body_start = i
                    break

            body_tokens = tokens[body_start:]
            body_text = ' '.join(body_tokens)

            # Clean up: remove extra spaces around punctuation
            body_text = re.sub(r'\s+([.,;:!?)\'])', r'\1', body_text)
            body_text = re.sub(r'([(])\s+', r'\1', body_text)

            doc_stem = xml_file.replace('.xml', '')
            texts[doc_stem] = body_text[:2000]  # keep first 2000 chars

    return texts


def normalize(t: str) -> str:
    t = t.lower()
    t = re.sub(r'[^a-z0-9\s\'-]', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def compute_ngram_overlap(a: str, b: str, n: int = 3) -> float:
    """Compute character n-gram overlap."""
    a_ngrams = set(a[i:i+n] for i in range(len(a)-n+1))
    b_ngrams = set(b[i:i+n] for i in range(len(b)-n+1))
    if not a_ngrams or not b_ngrams:
        return 0.0
    return len(a_ngrams & b_ngrams) / max(len(a_ngrams | b_ngrams), 1)


def try_match_via_curated(ecb_texts: dict[str, str]) -> dict[str, dict]:
    """Try to match ECB+ documents to curated dataset articles."""
    if not CURATED.exists():
        logger.warning("Curated dataset not found at %s", CURATED)
        return {}

    matches = {}

    # Load curated articles
    curated = []
    with open(CURATED) as f:
        for line in f:
            item = json.loads(line)
            body = item.get('body', '') or ''
            title = item.get('title', '') or ''
            curated.append({
                'id': item.get('id'),
                'title': title,
                'body': body,
                'full_text': f"{title} {body[:2000]}",
            })

    logger.info("Loaded %d curated articles for matching", len(curated))

    # For each ECB+ document, find the best match
    for doc_stem, ecb_text in ecb_texts.items():
        ecb_norm = normalize(ecb_text)
        if len(ecb_norm.split()) < 10:
            continue

        best_score = 0
        best_match = None

        for art in curated:
            art_norm = normalize(art['full_text'])
            if len(art_norm.split()) < 10:
                continue

            # Compute word overlap (Jaccard)
            ecb_words = set(ecb_norm.split())
            art_words = set(art_norm.split())
            jaccard = len(ecb_words & art_words) / max(len(ecb_words | art_words), 1)

            # Compute character 3-gram overlap
            ngram = compute_ngram_overlap(ecb_norm[:500], art_norm[:500], 3)

            score = max(jaccard, ngram * 0.8)
            if score > best_score:
                best_score = score
                best_match = art

        if best_score >= 0.25:
            matches[doc_stem] = {
                'db_article_id': best_match['id'],
                'match_method': 'text_ngram',
                'match_score': round(best_score, 4),
                'matched_title': best_match['title'][:100],
            }

    logger.info("Found %d matches with threshold 0.25", len(matches))
    return matches


def main():
    logger.info("Extracting ECB+ document texts...")
    ecb_texts = extract_ecb_texts()
    logger.info("Extracted %d documents", len(ecb_texts))

    # Load existing mapping
    if not MAPPING_PATH.exists():
        logger.error("Mapping file not found at %s", MAPPING_PATH)
        sys.exit(1)

    with open(MAPPING_PATH) as f:
        mapping = json.load(f)

    # Try matching
    logger.info("Trying to match against curated dataset...")
    curated_matches = try_match_via_curated(ecb_texts)

    # Update mapping entries with any matches
    update_count = 0
    for entry in mapping['entries']:
        doc_stem = entry['ecb_doc_id']
        if doc_stem in curated_matches:
            m = curated_matches[doc_stem]
            entry['db_article_id'] = m['db_article_id']
            entry['match_method'] = m['match_method']
            entry['match_score'] = m['match_score']
            entry['matched_title'] = m['matched_title']
            update_count += 1

    if update_count:
        # Rebuild inverted indices
        db_to_gold = defaultdict(list)
        gold_to_db = {}
        for entry in mapping['entries']:
            db_id = entry.get('db_article_id')
            if db_id is not None:
                for gid in entry['gold_article_ids']:
                    db_to_gold[db_id].append(gid)
                    gold_to_db[gid] = db_id

        mapping['db_to_gold_ids'] = {str(k): sorted(v) for k, v in db_to_gold.items()}
        mapping['gold_to_db'] = {str(k): v for k, v in gold_to_db.items()}
        mapping['metadata']['matched_docs'] = update_count
        mapping['metadata']['total_gold_ids_mapped'] = sum(len(v) for v in db_to_gold.values())

        with open(MAPPING_PATH, 'w') as f:
            json.dump(mapping, f, indent=2, ensure_ascii=False)
        logger.info("Updated mapping with %d new matches", update_count)
    else:
        logger.info("No new matches found")

    print(f"\nMatching summary: {update_count} / {len(mapping['entries'])} documents matched")
    print(f"  db_to_gold entries: {len(mapping.get('db_to_gold_ids', {}))}")


if __name__ == "__main__":
    main()
