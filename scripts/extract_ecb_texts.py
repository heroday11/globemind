#!/usr/bin/env python3
"""
Extract full text from all ECB+ XML documents and build a searchable corpus.

Scans all ECB+ directories for XML files (both ecb and ecbplus), extracts
full text from token elements, collects mention triggers from Markables,
and maps documents to gold article_ids from the cross-reference mapping.

Output: /root/data/globemind/data/ecbplus/ecb_corpus.json
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("extract_ecb_texts")

# ── Paths ──────────────────────────────────────────────────
REPO = Path(__file__).resolve().parent.parent
ECB_PLUS_DIR = REPO / "data" / "ecbplus"
ECB_DIR = ECB_PLUS_DIR / "ECB+"
MAPPING_PATH = ECB_PLUS_DIR / "ecb_to_db_mapping.json"
OUTPUT_PATH = ECB_PLUS_DIR / "ecb_corpus.json"

# ECB+ topic number → topic name mapping
# Based on the ECB original dataset topic names (Bejan & Harabagiu, 2010)
# and the ECB+ annotation guidelines (Cybulska & Vossen, 2014).
ECB_TOPIC_NAMES: Dict[int, str] = {
    1: "Celebrity_Legal_Cases",
    2: "Entertainment_Awards",
    3: "Prison_Escape",
    4: "Celebrity_Deaths",
    5: "NBA_Coach_Hiring",
    6: "Movie_Sequel_Production",
    7: "Boxing_Heavyweight_Title",
    8: "Greek_Protest_Violence",
    9: "Endangered_Species_Protection",
    10: "Sports_Contract_Negotiations",
    11: "Elections_Turkmenistan",
    12: "Piracy_Naval_Interception",
    13: "Fire_Building_Insurance",
    14: "Supermarket_Fire",
    16: "Death_Sentence_Rulings",
    18: "Workplace_Violence_Shooting",
    19: "Police_Shooting_Protests",
    20: "Earthquake_Tremors",
    21: "Hit_and_Run_Accident",
    22: "Workplace_Shooting_Trial",
    23: "Climbing_Death_Accident",
    24: "Jewelry_Store_Robbery",
    25: "NFL_Injury_Roster_Moves",
    26: "Mafia_Boss_Death",
    27: "Software_Security_Patches",
    28: "Watergate_Figure_Death",
    29: "NFL_Playoff_Clinch",
    30: "ISP_Service_Restoration",
    31: "College_Basketball_Tournament",
    32: "Double_Murder_Charge",
    33: "Gang_Murder_Confession",
    34: "Surgeon_General_Nomination",
    35: "NFL_DUI_Arrest",
    36: "Polygamist_Sect_Trial",
    37: "Indonesia_Earthquake",
    38: "California_Earthquake",
    39: "Doctor_Who_Casting",
    40: "Apple_Laptop_Refresh",
    41: "Sudan_South_Sudan_Conflict",
    42: "Smartphone_Release",
    43: "Tech_Acquisition_AMD",
    44: "Tech_Acquisition_HP",
    45: "Murder_Conviction_Trial",
}


def reconstruct_text(root: ET.Element) -> str:
    """Reconstruct full text from token elements, organized by sentence."""
    # Group tokens by sentence
    sentences: Dict[int, List[str]] = {}
    for tok in root.findall("token"):
        s = int(tok.get("sentence", "0"))
        if s not in sentences:
            sentences[s] = []
        sentences[s].append(tok.text or "")

    # Build full text sentence by sentence
    parts: List[str] = []
    for s in sorted(sentences.keys()):
        sent_text = "".join(sentences[s])
        # Clean up spacing around punctuation
        sent_text = re.sub(r"\s+([.,;:!?)\"'])", r"\1", sent_text)
        sent_text = re.sub(r"([(])\s+", r"\1", sent_text)
        sent_text = re.sub(r'\s+', ' ', sent_text).strip()
        parts.append(sent_text)

    return " ".join(parts)


def extract_mention_triggers(root: ET.Element) -> List[str]:
    """Extract trigger phrases from Markables section."""
    triggers: List[str] = []
    # Build token lookup
    tokens: Dict[str, str] = {}
    for tok in root.findall("token"):
        tokens[tok.get("t_id", "")] = tok.text or ""

    markables = root.find("Markables")
    if markables is None:
        return triggers

    for ev in markables:
        trigger_parts: List[str] = []
        for child in ev:
            if child.tag == "token_anchor":
                tid = child.get("t_id", "")
                if tid in tokens:
                    trigger_parts.append(tokens[tid])
        if trigger_parts:
            triggers.append(" ".join(trigger_parts))

    return triggers


def load_mapping() -> Dict[str, Any]:
    """Load the ECB+ to gold article_id cross-reference mapping."""
    with open(MAPPING_PATH, encoding="utf-8") as f:
        return json.load(f)


def get_topic_name(topic_num: int) -> str:
    """Map ECB topic number to a human-readable topic name."""
    return ECB_TOPIC_NAMES.get(topic_num, f"Topic_{topic_num}")


def main():
    logger.info("=" * 60)
    logger.info("ECB+ Text Extraction")
    logger.info("=" * 60)

    # Load the mapping file for gold article_id cross-references
    logger.info("Loading mapping file...")
    mapping = load_mapping()
    entries = mapping["entries"]
    logger.info("Loaded %d mapping entries", len(entries))

    # Build doc_stem → mapping entry lookup
    doc_map: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        doc_map[entry["ecb_doc_id"]] = entry

    # Scan all ECB+ XML files
    documents: List[Dict[str, Any]] = []
    xml_count = 0
    txt_count = 0

    for topic_name in sorted(os.listdir(ECB_DIR), key=lambda x: int(x) if x.isdigit() else 999):
        topic_path = os.path.join(ECB_DIR, topic_name)
        if not os.path.isdir(topic_path):
            continue
        if not topic_name.isdigit():
            logger.warning("Skipping non-numeric directory: %s", topic_name)
            continue

        topic_num = int(topic_name)
        subtopic = topic_name  # subtopic is the directory number

        for xml_file in sorted(os.listdir(topic_path)):
            filepath = os.path.join(topic_path, xml_file)

            if xml_file.endswith(".txt"):
                # TXT file: read raw text
                txt_count += 1
                try:
                    with open(filepath, encoding="utf-8", errors="replace") as f:
                        full_text = f.read()
                except Exception as e:
                    logger.warning("Failed to read TXT %s: %s", xml_file, e)
                    continue

                ecb_doc_id = xml_file.replace(".txt", "")
                doc_stem = ecb_doc_id
                mapping_entry = doc_map.get(doc_stem, None)

                mention_triggers: List[str] = []
                gold_article_id: Optional[int] = None
                if mapping_entry:
                    gold_ids = mapping_entry.get("gold_article_ids", [])
                    gold_article_id = gold_ids[0] if gold_ids else None

                documents.append({
                    "ecb_doc_id": doc_stem,
                    "topic": get_topic_name(topic_num),
                    "subtopic": subtopic,
                    "file_path": filepath,
                    "full_text": full_text,
                    "mention_triggers": mention_triggers,
                    "gold_article_id": gold_article_id,
                })
                continue

            if not (xml_file.endswith(".xml") and "ecbplus" in xml_file):
                continue

            xml_count += 1
            try:
                tree = ET.parse(filepath)
                root = tree.getroot()
            except Exception as e:
                logger.warning("Failed to parse XML %s: %s", xml_file, e)
                continue

            # Reconstruct full text
            full_text = reconstruct_text(root)

            # Extract mention triggers
            mention_triggers = extract_mention_triggers(root)

            # Determine ecb_doc_id
            doc_stem = xml_file.replace(".xml", "")
            mapping_entry = doc_map.get(doc_stem, None)

            gold_article_id: Optional[int] = None
            if mapping_entry:
                gold_ids = mapping_entry.get("gold_article_ids", [])
                gold_article_id = gold_ids[0] if gold_ids else None

            documents.append({
                "ecb_doc_id": doc_stem,
                "topic": get_topic_name(topic_num),
                "subtopic": subtopic,
                "file_path": filepath,
                "full_text": full_text,
                "mention_triggers": mention_triggers,
                "gold_article_id": gold_article_id,
            })

    # Build output
    corpus = {
        "documents": documents,
        "total_docs": len(documents),
    }

    # Save
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(corpus, f, indent=2, ensure_ascii=False)

    logger.info("Saved corpus to %s", OUTPUT_PATH)
    logger.info("Total XML files processed: %d", xml_count)
    logger.info("Total TXT files processed: %d", txt_count)
    logger.info("Total documents in corpus: %d", len(documents))

    # Summary
    print()
    print("=" * 60)
    print("  EXTRACTION SUMMARY")
    print("=" * 60)
    print(f"  ECB+ XML files processed: {xml_count}")
    print(f"  TXT files processed:       {txt_count}")
    print(f"  Total documents:           {len(documents)}")
    print(f"  Topics:                    {len(set(d['topic'] for d in documents))}")
    lengths = [len(d.get("full_text", "")) for d in documents]
    print(f"  Text length stats:")
    print(f"    Avg: {sum(lengths) / max(len(lengths), 1):.0f} chars")
    print(f"    Min: {min(lengths)}, Max: {max(lengths)}")
    print(f"    Docs with text:  {sum(1 for l in lengths if l > 0)}")
    print(f"    Docs with empty: {sum(1 for l in lengths if l == 0)}")

    with_gold = sum(1 for d in documents if d["gold_article_id"] is not None)
    print(f"  Documents with gold_article_id: {with_gold}")
    print(f"  Total mention triggers: {sum(len(d['mention_triggers']) for d in documents)}")

    return corpus


if __name__ == "__main__":
    main()
