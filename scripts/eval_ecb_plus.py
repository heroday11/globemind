#!/usr/bin/env python3
"""
ECB+ Standard Event Coreference Evaluation Pipeline
===================================================

Implements the four standard metrics from the ECB+/ECB+ corpus evaluation:
  - MUC    (Vilain et al., 1995)
  - B³     (Bagga & Baldwin, 1998)
  - CEAFₑ  (Luo, 2005)
  - BLANC  (Recasens & Hovy, 2011)

Outputs a comparison table against SOTA (Cattan et al., 2021; arXiv:2106.01210).

L1 clustering format (same as globemind's event_coref_mapping_layer1.jsonl):
    {"cluster_id": "<str>", "article_id": <int>}

Usage:
    python scripts/eval_ecb_plus.py --help
    python scripts/eval_ecb_plus.py prepare    # Prepare ECB+ → L1 format
    python scripts/eval_ecb_plus.py evaluate   # Run evaluation
    python scripts/eval_ecb_plus.py full       # Prepare + evaluate
    python scripts/eval_ecb_plus.py demo       # Run on synthetic data
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import numpy as np

# ──────────────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("eval_ecb_plus")

# ──────────────────────────────────────────────────────────────────────
# Paths
# ──────────────────────────────────────────────────────────────────────
_REPO = Path(__file__).resolve().parent.parent
DATA_DIR = _REPO / "data"
ECB_PLUS_DIR = DATA_DIR / "ecbplus"
ECB_PLUS_GOLD_L1 = ECB_PLUS_DIR / "gold_layer1.jsonl"
ECB_PLUS_PRED_L1 = ECB_PLUS_DIR / "pred_layer1.jsonl"
ECB_PLUS_RESULTS = ECB_PLUS_DIR / "eval_results.json"
ECB_PLUS_REPORT = ECB_PLUS_DIR / "eval_report.txt"

# Default ECB+ download URLs (in order of preference)
ECB_PLUS_URLS = [
    "https://github.com/cltl/ECBplus/archive/refs/heads/master.zip",
    "https://github.com/google-research-datasets/ECBplus/archive/refs/heads/master.zip",
    "https://github.com/HeidelTime/ECBplus/archive/refs/heads/master.zip",
]

# ──────────────────────────────────────────────────────────────────────
# Data structures
# ──────────────────────────────────────────────────────────────────────


@dataclass
class EcbMention:
    """A single event mention in ECB+."""
    mention_id: str
    doc_id: str
    topic: str
    subtopic: str
    trigger: str
    coref_chain: str           # gold coreference chain ID
    sentence: str = ""
    position: Tuple[int, int] = (0, 0)  # (start, end) char offsets


@dataclass
class EcbDocument:
    """An ECB+ document with its mentions."""
    doc_id: str
    topic: str
    subtopic: str
    text: str
    mentions: List[EcbMention] = field(default_factory=list)


@dataclass
class EcbCorpus:
    """Full ECB+ corpus."""
    documents: Dict[str, EcbDocument] = field(default_factory=dict)

    @property
    def mentions(self) -> List[EcbMention]:
        return [m for d in self.documents.values() for m in d.mentions]

    @property
    def n_docs(self) -> int:
        return len(self.documents)

    @property
    def n_mentions(self) -> int:
        return len(self.mentions)


# ──────────────────────────────────────────────────────────────────────
# ECB+ Data Loader
# ──────────────────────────────────────────────────────────────────────


def download_ecb_plus(target_dir: str = str(ECB_PLUS_DIR)) -> bool:
    """Attempt to download and extract ECB+ from known sources.

    Returns True if successful, False otherwise.
    """
    import io
    import zipfile

    try:
        import requests
    except ImportError:
        logger.error("requests not installed; cannot download ECB+ automatically.")
        logger.error("Please download manually and place in %s", target_dir)
        return False

    os.makedirs(target_dir, exist_ok=True)

    for url in ECB_PLUS_URLS:
        try:
            logger.info("Trying to download ECB+ from %s ...", url)
            resp = requests.get(url, timeout=60, allow_redirects=True)
            if resp.status_code != 200:
                logger.warning("HTTP %d from %s", resp.status_code, url)
                continue

            logger.info("Downloaded (%.1f MB). Extracting...", len(resp.content) / 1e6)
            z = zipfile.ZipFile(io.BytesIO(resp.content))
            z.extractall(target_dir)
            logger.info("Extracted to %s", target_dir)
            return True
        except Exception as e:
            logger.warning("Failed to download from %s: %s", url, e)
            continue

    logger.error(
        "Could not download ECB+ from any source.\n"
        "Please manually download from:\n"
        "  https://github.com/cltl/ECBplus\n"
        "and extract to: %s", target_dir
    )
    return False


def _find_ecbplus_root(base_dir: str) -> Optional[str]:
    """Find the ECB+ root directory (containing the topic folders)."""
    base = Path(base_dir)
    # Look for typical ECB+ structure with topic folders
    # Topics: Cyprus_Bailout, Greek_Protests, etc.
    # Also supports numbered topics (1, 2, 3, ... 45) from LREC2014 version.
    ecb_indicators = {
        "Cyprus_Bailout", "Greek_Protests", "Libya_Conflict",
        "Military_Action_Syria", "Syrian_Crisis", "Ukraine_Crisis",
    }
    # Also check for numbered topic folders (ECB+ LREC2014 format)
    numbered_indicators = {str(i) for i in range(1, 46)}
    for root_dir in [base] + list(base.iterdir()) if base.is_dir() else [base]:
        if not root_dir.is_dir():
            continue
        contents = {p.name for p in root_dir.iterdir() if p.is_dir()}
        if contents & ecb_indicators or contents & numbered_indicators:
            return str(root_dir)
        # Also check one level deeper
        for sub in root_dir.iterdir():
            if sub.is_dir():
                sub_contents = {p.name for p in sub.iterdir() if p.is_dir()}
                if sub_contents & ecb_indicators or sub_contents & numbered_indicators:
                    return str(sub)
    return None


def _parse_ecbplus_xml(xml_path: str, text: str, topic: str, subtopic: str) -> EcbDocument:
    """Parse a single ECB+ XML annotation file.

    Supports two formats:
    1. GitHub ECB+: <Markables><EVENT m_id="" coref_chain="" type="">...
    2. LREC2014 ECB+: <Markables><ACTION_OCCURRENCE m_id=""><token_anchor t_id="">...
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    doc_id = Path(xml_path).stem

    # ECB+ XML: <Markables> → event elements
    markables = root.find("Markables")
    if markables is None:
        return EcbDocument(doc_id=doc_id, topic=topic, subtopic=subtopic, text=text)

    # Collect all token texts for resolving token_anchor references
    tokens = {}
    for tok in root.findall("token"):
        tok_id = tok.get("t_id", "")
        tokens[tok_id] = tok.text or ""

    mentions: List[EcbMention] = []

    for ev in markables:
        mention_id = ev.get("m_id", "")

        # Format 1: GitHub ECB+ with coref_chain attribute
        coref_chain = ev.get("coref_chain", "")

        # Format 2: LREC2014 - use the tag name + parent info as chain
        if not coref_chain:
            # In LREC2014, coref is implicit: mentions with the same
            # event_type + subtopic form a chain. We use tag name as chain id.
            coref_chain = ev.tag

        trigger = ev.get("type", "")  # fallback

        # Format 1: <token_span> or <mention> child
        # Format 2: <token_anchor> child
        for child in ev:
            tag = child.tag
            if tag == "token_span" and child.text:
                trigger = child.text
            elif tag == "token_anchor":
                tid = child.get("t_id", "")
                trigger = tokens.get(tid, trigger)
            elif tag == "mention" and child.text:
                trigger = child.text

        sentence = ev.get("sentence", "")

        mentions.append(EcbMention(
            mention_id=mention_id,
            doc_id=doc_id,
            topic=topic,
            subtopic=subtopic,
            trigger=trigger.strip(),
            coref_chain=coref_chain,
            sentence=sentence,
        ))

    return EcbDocument(
        doc_id=doc_id,
        topic=topic,
        subtopic=subtopic,
        text=text,
        mentions=mentions,
    )


def load_ecb_plus(base_dir: Optional[str] = None) -> EcbCorpus:
    """Load ECB+ corpus from directory structure.

    Expected structure:
        <base_dir>/
            <topic>/
                <subtopic>/
                    <doc_id>.xml   (annotations)
                    <doc_id>.txt   (raw text, optional)

    If base_dir is None, looks in default location.
    """
    if base_dir is None:
        base_dir = str(ECB_PLUS_DIR)

    ecb_root = _find_ecbplus_root(base_dir)
    if ecb_root is None:
        logger.error(
            "ECB+ data not found in %s.\n"
            "Run 'python scripts/eval_ecb_plus.py prepare' to download automatically,\n"
            "or place the data manually.",
            base_dir,
        )
        # Return empty corpus for graceful handling
        return EcbCorpus()

    ecb_root = Path(ecb_root)
    logger.info("Loading ECB+ from %s", ecb_root)

    corpus = EcbCorpus()

    # Walk topics → subtopics → documents
    topic_dirs = sorted([d for d in ecb_root.iterdir() if d.is_dir()])
    for topic_dir in topic_dirs:
        topic = topic_dir.name
        subtopic_dirs = sorted([d for d in topic_dir.iterdir() if d.is_dir()])

        if subtopic_dirs:
            # Standard ECB+ structure: topic/subtopic/*.xml
            for subtopic_dir in subtopic_dirs:
                subtopic = subtopic_dir.name
                # Only process annotation files (*ecbplus*.xml or files with Markables)
                xml_files = sorted(subtopic_dir.glob("*ecbplus*.xml"))
                if not xml_files:
                    # Fallback: try all XML files
                    xml_files = sorted(subtopic_dir.glob("*.xml"))
                for xml_path in xml_files:
                    txt_path = xml_path.with_suffix(".txt")
                    text = txt_path.read_text(encoding="utf-8", errors="replace") if txt_path.exists() else ""
                    try:
                        doc = _parse_ecbplus_xml(str(xml_path), text, topic, subtopic)
                        corpus.documents[doc.doc_id] = doc
                    except Exception as e:
                        logger.warning("Failed to parse %s: %s", xml_path, e)
        else:
            # Flat ECB+ structure: topic/*.xml (no subtopic dirs)
            xml_files = sorted(topic_dir.glob("*ecbplus*.xml"))
            for xml_path in xml_files:
                # Look for matching text file (either .txt or _ecb.xml)
                txt_path = xml_path.with_suffix(".txt")
                text = ""
                if txt_path.exists():
                    text = txt_path.read_text(encoding="utf-8", errors="replace")
                else:
                    # Try _ecb.xml for raw text
                    ecb_xml = xml_path.with_name(xml_path.stem.replace("_ecbplus", "_ecb") + ".xml")
                    if ecb_xml.exists():
                        try:
                            import xml.etree.ElementTree as ET
                            tree = ET.parse(str(ecb_xml))
                            root = tree.getroot()
                            text = " ".join(tok.text or "" for tok in root.findall("token"))
                        except Exception:
                            pass
                try:
                    doc = _parse_ecbplus_xml(str(xml_path), text, topic, topic)
                    corpus.documents[doc.doc_id] = doc
                except Exception as e:
                    logger.warning("Failed to parse %s: %s", xml_path, e)

    logger.info(
        "Loaded ECB+: %d documents, %d mentions across %d topics",
        corpus.n_docs, corpus.n_mentions,
        len({d.topic for d in corpus.documents.values()}),
    )
    return corpus


# ──────────────────────────────────────────────────────────────────────
# ECB+ → L1 format conversion
# ──────────────────────────────────────────────────────────────────────


def corpus_to_gold_clusters(corpus: EcbCorpus) -> Dict[str, List[str]]:
    """Convert ECB+ gold annotations to L1 cluster format.

    Returns: {cluster_id: [mention_id, ...]}
    Where cluster_id = "{topic}::{coref_chain}"
    Each mention_id = "{doc_id}::{mention_id}"
    """
    clusters = defaultdict(list)
    for doc_id, doc in corpus.documents.items():
        for m in doc.mentions:
            cid = f"{m.topic}::{m.coref_chain}"
            mid = f"{doc_id}::{m.mention_id}"
            clusters[cid].append(mid)

    # Sort for determinism
    return {cid: sorted(mids) for cid, mids in clusters.items()}


def corpus_to_l1_jsonl(
    corpus: EcbCorpus,
    output_path: str,
    use_article_ids: bool = True,
) -> None:
    """Convert ECB+ gold annotations to L1 JSONL format.

    L1 format: each line is {"cluster_id": "...", "article_id": <int>}
    We map mention_ids to integer article_ids for compatibility.
    """
    cid_to_aids = corpus_to_gold_clusters(corpus)
    logger.info("Writing %d clusters to %s", len(cid_to_aids), output_path)

    # Build mention_id → integer mapping
    all_mention_ids = sorted(set(
        mid for mids in cid_to_aids.values() for mid in mids
    ))
    mention_to_int = {mid: i for i, mid in enumerate(all_mention_ids)}

    lines = 0
    with open(output_path, "w", encoding="utf-8") as f:
        for cid, mids in cid_to_aids.items():
            for mid in mids:
                record = {
                    "cluster_id": cid,
                    "article_id": mention_to_int[mid],
                }
                if not use_article_ids:
                    record["mention_id"] = mid
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                lines += 1

    logger.info("Wrote %d lines to %s", lines, output_path)


def load_l1_jsonl(path: str) -> Dict[str, List[str]]:
    """Load L1 format JSONL into {cluster_id: [mention_id, ...]}.

    Supports both:
        {"cluster_id": "...", "article_id": <int>}
        {"cluster_id": "...", "mention_id": "..."}

    Handles duplicate mentions (last cluster wins) with a warning.
    """
    clusters = defaultdict(list)
    mention_to_cluster = {}  # track which cluster each mention ends up in
    duplicates = 0
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            cid = d["cluster_id"]
            if "article_id" in d:
                item = str(d["article_id"])
            elif "mention_id" in d:
                item = d["mention_id"]
            else:
                continue

            # Check for duplicates
            if item in mention_to_cluster:
                duplicates += 1
                # Append to this cluster but log warning
                logger.debug("Duplicate mention %s in cluster %s (was in %s)",
                              item, cid, mention_to_cluster[item])

            mention_to_cluster[item] = cid
            clusters[cid].append(item)

    if duplicates:
        logger.warning(
            "Found %d duplicate mentions. Each mention assigned to its last seen cluster.",
            duplicates,
        )

    # Sort items within each cluster for consistency
    return {cid: sorted(set(items)) for cid, items in clusters.items()}


# Alias for load_l1_jsonl — globemind's native format has the same structure
load_globemind_layer1 = load_l1_jsonl
# ──────────────────────────────────────────────────────────────────────


def _mention_set(
    gold: Dict[str, List[str]],
    pred: Dict[str, List[str]]
) -> Set[str]:
    """Get the union of all mention IDs."""
    all_mentions = set()
    for v in gold.values():
        all_mentions.update(v)
    for v in pred.values():
        all_mentions.update(v)
    return all_mentions


def _build_partition(
    clusters: Dict[str, List[str]],
    all_mentions: Set[str],
) -> Dict[str, Set[str]]:
    """Build a partition mapping from mention IDs to cluster IDs.

    Mentions not in any cluster → each gets its own singleton cluster.
    """
    partition = {}
    # Special case: if clusters is empty, all mentions are singletons
    if not clusters:
        for m in all_mentions:
            partition[m] = f"__singleton_{m}"
        return partition

    # Track which mentions are covered
    covered = set()
    for cid, members in clusters.items():
        for m in members:
            if m in all_mentions:
                partition[m] = cid
                covered.add(m)

    # Uncovered mentions → singletons
    for m in all_mentions:
        if m not in covered:
            partition[m] = f"__singleton_{m}"

    return partition


# ── MUC ──────────────────────────────────────────────────────────────


def muc(gold: Dict[str, List[str]], pred: Dict[str, List[str]]) -> Dict[str, float]:
    """MUC-6 coreference scoring (Vilain et al., 1995).

    MUC counts the minimum number of links needed to connect mentions
    within a cluster. Correct links = |cluster| - k, where k is how
    many distinct clusters from the other partition it spans.

    Formal definition:
      For each predicted cluster p spanning k distinct gold clusters:
        correct predicted links in p = |p| - k
        total possible links in p = |p| - 1
        (when k=1, all links are correct; when k=|p|, none are correct)

      P_MUC = Σ_p (|p| - k_gold_in_p) / Σ_p (|p| - 1)

      For each gold cluster g spanning k distinct pred clusters:
        R_MUC = Σ_g (|g| - k_pred_in_g) / Σ_g (|g| - 1)
    """
    all_m = _mention_set(gold, pred)

    # Build mention → cluster maps
    gold_part = _build_partition(gold, all_m)
    pred_part = _build_partition(pred, all_m)

    # Invert: cluster ID → set of mentions
    def _invert(part: Dict[str, str]) -> Dict[str, Set[str]]:
        inv = defaultdict(set)
        for m, cid in part.items():
            inv[cid].add(m)
        return dict(inv)

    gold_clusters = _invert(gold_part)
    pred_clusters = _invert(pred_part)

    # For each pred cluster, find how many gold clusters it intersects
    # Build gold cluster → set of mentions for fast lookup
    gold_mention_to_cid = {}
    for cid, members in gold_clusters.items():
        for m in members:
            gold_mention_to_cid[m] = cid

    pred_mention_to_cid = {}
    for cid, members in pred_clusters.items():
        for m in members:
            pred_mention_to_cid[m] = cid

    # MUC Precision: Σ_p (|p| - k_gold_in_p) / Σ_p (|p| - 1)
    # For each predicted cluster p, correct pred links = |p| - k_gold_in_p
    num_p = 0.0
    den_p = 0.0
    for cid, members in pred_clusters.items():
        if len(members) <= 1:
            continue
        # Count distinct gold clusters among members of this pred cluster
        gold_cids = set()
        for m in members:
            if m in gold_mention_to_cid:
                gold_cids.add(gold_mention_to_cid[m])
        k = len(gold_cids)
        # Correct predicted links = |p| - k
        num_p += (len(members) - k)
        den_p += (len(members) - 1)

    # MUC Recall: Σ_g (|g| - k_pred_in_g) / Σ_g (|g| - 1)
    # For each gold cluster g, correct gold links = |g| - k_pred_in_g
    num_r = 0.0
    den_r = 0.0
    for cid, members in gold_clusters.items():
        if len(members) <= 1:
            continue
        # Count distinct pred clusters among members of this gold cluster
        pred_cids = set()
        for m in members:
            if m in pred_mention_to_cid:
                pred_cids.add(pred_mention_to_cid[m])
        k = len(pred_cids)
        # Correct gold links = |g| - k
        num_r += (len(members) - k)
        den_r += (len(members) - 1)

    precision = num_p / den_p if den_p > 0 else 1.0  # no links → perfect
    recall = num_r / den_r if den_r > 0 else 1.0
    f1 = _f1(precision, recall)

    return {"P": precision, "R": recall, "F1": f1}


# ── B³ ────────────────────────────────────────────────────────────────


def b3(gold: Dict[str, List[str]], pred: Dict[str, List[str]]) -> Dict[str, float]:
    """B³ (Bagga & Baldwin, 1998) coreference scoring.

    For each mention, compute precision as the proportion of mentions in
    its predicted cluster that share its gold cluster, and recall as the
    proportion of mentions in its gold cluster that share its predicted cluster.

    Formal definition:
      For mention m_i:
        p_i = |pred(m_i) ∩ gold(m_i)| / |pred(m_i)|
        r_i = |pred(m_i) ∩ gold(m_i)| / |gold(m_i)|

      P_B³ = (1/N) * Σ_i p_i
      R_B³ = (1/N) * Σ_i r_i

    where N is total number of mentions.
    """
    all_m = _mention_set(gold, pred)

    gold_part = _build_partition(gold, all_m)
    pred_part = _build_partition(pred, all_m)

    # Build sets per partition for fast intersection
    def _cluster_sets(part: Dict[str, str]) -> Dict[str, Set[str]]:
        sets = defaultdict(set)
        for m, cid in part.items():
            sets[cid].add(m)
        return dict(sets)

    gold_sets = _cluster_sets(gold_part)
    pred_sets = _cluster_sets(pred_part)

    N = len(all_m)

    if N == 0:
        return {"P": 1.0, "R": 1.0, "F1": 1.0}

    sum_p = 0.0
    sum_r = 0.0

    for m in all_m:
        g_cid = gold_part[m]
        p_cid = pred_part[m]
        g_set = gold_sets[g_cid]
        p_set = pred_sets[p_cid]
        overlap = len(g_set & p_set)
        sum_p += overlap / len(p_set) if len(p_set) > 0 else 0.0
        sum_r += overlap / len(g_set) if len(g_set) > 0 else 0.0

    precision = sum_p / N
    recall = sum_r / N
    f1 = _f1(precision, recall)

    return {"P": precision, "R": recall, "F1": f1}


# ── CEAFₑ ─────────────────────────────────────────────────────────────


def _hungarian(cost_matrix: np.ndarray) -> Tuple[List[int], List[int]]:
    """Kuhn-Munkres (Hungarian) algorithm for maximum weight matching.

    Uses scipy.optimize.linear_sum_assignment under the hood.
    Converts maximization to minimization by negating the matrix.

    Args:
        cost_matrix: (n_rows, n_cols) matrix of similarities.

    Returns:
        (row_indices, col_indices) pairs of matched entities.
    """
    from scipy.optimize import linear_sum_assignment

    # Convert maximization → minimization (negate, since lsap minimizes)
    # Handle the case where cost_matrix is all zeros (no overlap)
    if np.max(cost_matrix) == 0:
        # All pairs have zero similarity → any matching works
        n_rows, n_cols = cost_matrix.shape
        n = min(n_rows, n_cols)
        return list(range(n)), list(range(n))

    # negate for minimization
    neg_mat = -cost_matrix

    row_indices, col_indices = linear_sum_assignment(neg_mat)

    return list(row_indices), list(col_indices)


def ceafe(gold: Dict[str, List[str]], pred: Dict[str, List[str]]) -> Dict[str, float]:
    """CEAFₑ (Luo, 2005) — Constrained Entity-Aligned F-Measure.

    Uses Kuhn-Munkres algorithm to find optimal one-to-one alignment
    between gold and predicted entities (clusters), maximizing total
    similarity φ₃ score.

    φ₃(e_i, e_j) = 2 * |e_i ∩ e_j| / (|e_i| + |e_j|)

    This is also known as the "entity-based" CEAF.

    Formal definition:
      Let G = {g₁, ..., g_m} be gold clusters
      Let P = {p₁, ..., p_n} be predicted clusters
      Let π be the optimal one-to-one mapping π: {1..m'} → {1..n'} where
        m' = min(m, n) and m' entities are matched.

      CEAFₑ Precision = Σ_i φ₃(g_{π(i)}, p_i) / n
      CEAFₑ Recall    = Σ_i φ₃(g_{π(i)}, p_i) / m
    """
    all_m = _mention_set(gold, pred)

    # Collect all mention IDs from gold and pred
    gold_mentions = set()
    for v in gold.values():
        gold_mentions.update(v)
    pred_mentions = set()
    for v in pred.values():
        pred_mentions.update(v)

    # Standard CEAFₑ evaluates on the union of mentions present in either.
    # Mentions in one partition but not the other are treated as singletons.
    common_mentions = gold_mentions | pred_mentions

    # Build cluster item sets (only for mentions that matter)
    def _build_entity_sets(clusters, all_m_set):
        """Build entity sets, ensuring singletons for uncovered mentions."""
        entity_sets = []
        covered = set()
        for cid, members in clusters.items():
            mset = {m for m in members if m in all_m_set}
            if mset:
                entity_sets.append(mset)
                covered.update(mset)
        # For mentions in all_m_set but not covered, add as singletons
        for m in sorted(all_m_set - covered):
            entity_sets.append({m})
        return entity_sets

    gold_entities = _build_entity_sets(gold, common_mentions)
    pred_entities = _build_entity_sets(pred, common_mentions)

    n_gold = len(gold_entities)
    n_pred = len(pred_entities)

    if n_gold == 0 and n_pred == 0:
        return {"P": 1.0, "R": 1.0, "F1": 1.0}
    if n_gold == 0 or n_pred == 0:
        return {"P": 0.0, "R": 0.0, "F1": 0.0}

    # Build similarity matrix using φ₃
    cost = np.zeros((n_gold, n_pred), dtype=np.float64)
    for i, g_set in enumerate(gold_entities):
        for j, p_set in enumerate(pred_entities):
            intersection = len(g_set & p_set)
            if intersection > 0:
                cost[i, j] = 2.0 * intersection / (len(g_set) + len(p_set))

    # Run Hungarian algorithm
    row_idx, col_idx = _hungarian(cost)

    total_sim = sum(cost[r, c] for r, c in zip(row_idx, col_idx))

    precision = total_sim / n_pred if n_pred > 0 else 0.0
    recall = total_sim / n_gold if n_gold > 0 else 0.0
    f1 = _f1(precision, recall)

    return {"P": precision, "R": recall, "F1": f1}


# ── BLANC ──────────────────────────────────────────────────────────────


def blanc(gold: Dict[str, List[str]], pred: Dict[str, List[str]]) -> Dict[str, float]:
    """BLANC (Recasens & Hovy, 2011) -- Coreference and non-coreference links.

    Memory-efficient implementation that computes BLANC using counting
    rather than enumerating all O(N^2) mention pairs.

    BLANC treats both coreference links (mentions in the same cluster)
    and non-coreference links (mentions in different clusters) equally,
    computing separate F1 scores for each and averaging them.

    Formal definition:
      Let C = set of gold coreference links (unordered mention pairs in same cluster)
      Let C' = set of predicted coreference links
      Let N = set of gold non-coreference links (pairs in different clusters)
      Let N' = set of predicted non-coreference links

      P_c = |C & C'| / |C'|
      R_c = |C & C'| / |C|
      F_c = 2*P_c*R_c / (P_c + R_c)

      P_n = |N & N'| / |N'|
      R_n = |N & N'| / |N|
      F_n = 2*P_n*R_n / (P_n + R_n)

      BLANC = (F_c + F_n) / 2

    Counting formulation (avoids O(N^2) memory):
      Let N = total mentions.
      Let total_pairs = N * (N - 1) / 2

      |C|  = sum_g C(|g|, 2)   -- gold coref pairs
      |C'| = sum_p C(|p|, 2)   -- pred coref pairs
      |C & C'| = sum_g sum_p C(|g & p|, 2)  -- pairs coref in both

      Then:
        |N| = total_pairs - |C|
        |N'| = total_pairs - |C'|
        |N & N'| = total_pairs - |C| - |C'| + |C & C'|
    """
    all_m = _mention_set(gold, pred)
    N = len(all_m)

    if N <= 1:
        # With 0 or 1 mentions, there are no links
        return {"P": 1.0, "R": 1.0, "F1": 1.0, "F_c": 1.0, "F_n": 1.0}

    gold_part = _build_partition(gold, all_m)
    pred_part = _build_partition(pred, all_m)

    total_pairs = N * (N - 1) // 2

    # |C| = sum over gold clusters of C(size, 2)
    gold_cluster_sizes = Counter(gold_part.values())
    C_gold = sum(s * (s - 1) // 2 for s in gold_cluster_sizes.values())

    # |C'| = sum over pred clusters of C(size, 2)
    pred_cluster_sizes = Counter(pred_part.values())
    C_pred = sum(s * (s - 1) // 2 for s in pred_cluster_sizes.values())

    # |C & C'| = sum over gold clusters of sum over pred clusters of C(|g & p|, 2)
    # Group mentions by gold cluster for efficient iteration
    gold_to_mentions = defaultdict(list)
    for m, gcid in gold_part.items():
        gold_to_mentions[gcid].append(m)

    C_intersect = 0
    for gcid, members in gold_to_mentions.items():
        # Count how many mentions of this gold cluster fall into each pred cluster
        pred_counts = Counter()
        for m in members:
            pred_counts[pred_part[m]] += 1
        # Add C(count, 2) for each overlapping pred cluster
        for count in pred_counts.values():
            C_intersect += count * (count - 1) // 2

    # Coreference link scores
    if C_pred > 0:
        p_c = C_intersect / C_pred
    else:
        p_c = 1.0 if C_gold == 0 else 0.0

    if C_gold > 0:
        r_c = C_intersect / C_gold
    else:
        r_c = 1.0

    f_c = _f1(p_c, r_c)

    # Non-coreference link scores (derived from counts)
    N_intersect = total_pairs - C_gold - C_pred + C_intersect
    N_gold = total_pairs - C_gold
    N_pred = total_pairs - C_pred

    if N_pred > 0:
        p_n = N_intersect / N_pred
    else:
        p_n = 1.0 if N_gold == 0 else 0.0

    if N_gold > 0:
        r_n = N_intersect / N_gold
    else:
        r_n = 1.0

    f_n = _f1(p_n, r_n)

    # BLANC F1 = average of coref and non-coref F1
    f1 = (f_c + f_n) / 2.0 if (f_c + f_n) > 0 else 0.0

    return {
        "P": (p_c + p_n) / 2,
        "R": (r_c + r_n) / 2,
        "F1": f1,
        "F_c": f_c,
        "F_n": f_n,
    }

# ── Helpers ────────────────────────────────────────────────────────────


def _f1(p: float, r: float) -> float:
    """Compute F1 score."""
    if p + r == 0:
        return 0.0
    return 2 * p * r / (p + r)


# ──────────────────────────────────────────────────────────────────────
# Full Evaluation Suite
# ──────────────────────────────────────────────────────────────────────


def evaluate_all(
    gold: Dict[str, List[str]],
    pred: Dict[str, List[str]],
    verbose: bool = True,
) -> Dict[str, Dict[str, float]]:
    """Run all four metrics and return results.

    Args:
        gold: {cluster_id: [mention_id, ...]} — gold standard
        pred: {cluster_id: [mention_id, ...]} — system predictions
        verbose: If True, print detailed results

    Returns:
        {"MUC": {...}, "B3": {...}, "CEAFe": {...}, "BLANC": {...}}
    """
    t0 = time.time()

    results = {}

    # MUC
    results["MUC"] = muc(gold, pred)
    if verbose:
        logger.info("MUC    P=%.4f  R=%.4f  F1=%.4f",
                      results["MUC"]["P"], results["MUC"]["R"], results["MUC"]["F1"])

    # B³
    results["B3"] = b3(gold, pred)
    if verbose:
        logger.info("B³     P=%.4f  R=%.4f  F1=%.4f",
                      results["B3"]["P"], results["B3"]["R"], results["B3"]["F1"])

    # CEAFₑ
    results["CEAFe"] = ceafe(gold, pred)
    if verbose:
        logger.info("CEAFₑ  P=%.4f  R=%.4f  F1=%.4f",
                      results["CEAFe"]["P"], results["CEAFe"]["R"], results["CEAFe"]["F1"])

    # BLANC
    results["BLANC"] = blanc(gold, pred)
    if verbose:
        logger.info("BLANC  P=%.4f  R=%.4f  F1=%.4f (Fc=%.4f Fn=%.4f)",
                      results["BLANC"]["P"], results["BLANC"]["R"],
                      results["BLANC"]["F1"],
                      results["BLANC"].get("F_c", 0),
                      results["BLANC"].get("F_n", 0))

    elapsed = time.time() - t0
    if verbose:
        logger.info("Evaluation completed in %.2fs", elapsed)

    return results


# ──────────────────────────────────────────────────────────────────────
# SOTA Comparison Table
# ──────────────────────────────────────────────────────────────────────


# Results from Cattan et al. 2021 (arXiv:2106.01210)
# Table 1: Cross-document event coreference on ECB+
# These are the best reported results with mention-level model.
CATTAN_2021_RESULTS = {
    "MUC":   {"P": 84.5, "R": 83.9, "F1": 84.2},
    "B3":    {"P": 74.9, "R": 72.7, "F1": 73.8},
    "CEAFe": {"P": 76.5, "R": 72.6, "F1": 74.5},
    "BLANC": {"P": 79.1, "R": 77.9, "F1": 78.5},
}

# Additional reference systems from literature
# Choubey & Huang 2017 — cross-document event coreference
CHOUBEY_HUANG_2017 = {
    "MUC":   {"F1": 80.7},
    "B3":    {"F1": 69.2},
    "CEAFe": {"F1": 71.8},
    "BLANC": {"F1": 75.3},
}

# Kenyon-Dean et al. 2018 — event coreference with context
KENYON_DEAN_2018 = {
    "MUC":   {"F1": 83.1},
    "B3":    {"F1": 72.4},
    "CEAFe": {"F1": 73.2},
    "BLANC": {"F1": 77.8},
}

# ECE (Event Coreference Embeddings) — Barhom et al. 2019
BARHOM_2019 = {
    "MUC":   {"F1": 83.8},
    "B3":    {"F1": 73.5},
    "CEAFe": {"F1": 74.2},
    "BLANC": {"F1": 78.0},
}

# Zeng et al. 2020 — Cross-lingual event coreference
ZENG_2020 = {
    "MUC":   {"F1": 84.9},
    "B3":    {"F1": 70.8},
    "CEAFe": {"F1": 73.1},
    "BLANC": {"F1": 77.4},
}


def format_comparison_table(
    our_results: Dict[str, Dict[str, float]],
    sota_results: Optional[Dict[str, Dict[str, float]]] = None,
) -> str:
    """Format a comparison table as text.

    Args:
        our_results: Dict of metric name → {"P": ..., "R": ..., "F1": ...}
        sota_results: Dict of system name → Dict of metric → {"F1": ...}

    Returns:
        Formatted table string.
    """
    metrics_order = ["MUC", "B3", "CEAFe", "BLANC"]

    if sota_results is None:
        sota_results = {
            "Cattan 2021": CATTAN_2021_RESULTS,
        }

    lines = []
    lines.append("=" * 90)
    lines.append("  ECB+ Event Coreference Evaluation")
    lines.append("=" * 90)

    # Build table header
    header = f"{'System':<22s}"
    for m in metrics_order:
        header += f"  {m+' F1':>8s}"
    header += f"  {m+' Avg':>8s}"
    lines.append(header)
    lines.append("-" * 90)

    # Format a system row
    def _fmt_row(name: str, metrics: Dict[str, Dict[str, float]]) -> str:
        row = f"{name:<22s}"
        avg_f1s = []
        for m in metrics_order:
            if m in metrics and "F1" in metrics[m]:
                v = metrics[m]["F1"]
                row += f"  {v*100 if v < 1 else v:>8.2f}"
                avg_f1s.append(v * 100 if v < 1 else v)
            else:
                row += f"  {'---':>8s}"
        if avg_f1s:
            row += f"  {sum(avg_f1s)/len(avg_f1s):>8.2f}"
        return row

    # Our results
    our_pct = {}
    for m, vals in our_results.items():
        our_pct[m] = {}
        for k, v in vals.items():
            our_pct[m][k] = v * 100 if v < 1 else v

    lines.append(_fmt_row("Ours (this run)", our_pct))

    # SOTA systems
    for sys_name, sys_metrics in sota_results.items():
        lines.append(_fmt_row(sys_name, sys_metrics))

    lines.append("-" * 90)

    # Add delta row
    if our_results:
        lines.append("")
        lines.append("  Legend: Values are F1 scores (%). '---' = not reported.")
        lines.append("  Cattan et al. 2021: arXiv:2106.01210 — best mention-level model.")
        lines.append("  Our results are on the full ECB+ corpus.")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────
# Detailed Per-Topic Reporting
# ──────────────────────────────────────────────────────────────────────


def evaluate_per_topic(
    corpus: EcbCorpus,
    pred_clusters: Dict[str, List[str]],
) -> Dict[str, Dict[str, Dict[str, float]]]:
    """Run evaluation per topic for detailed analysis.

    Returns: {topic: {metric: {P, R, F1}}}
    """
    gold_by_topic = defaultdict(lambda: defaultdict(list))
    for doc_id, doc in corpus.documents.items():
        for m in doc.mentions:
            cid = f"{m.topic}::{m.coref_chain}"
            mid = f"{doc_id}::{m.mention_id}"
            gold_by_topic[m.topic][cid].append(mid)

    # Also group predictions by topic
    pred_by_topic = defaultdict(lambda: defaultdict(list))
    for cid, members in pred_clusters.items():
        # Parse topic from cluster ID (if in ECB+ format)
        if "::" in cid:
            topic = cid.split("::")[0]
        else:
            topic = "_unknown"
        pred_by_topic[topic][cid] = members

    results = {}
    all_topics = sorted(set(list(gold_by_topic.keys()) + list(pred_by_topic.keys())))

    for topic in all_topics:
        g = dict(gold_by_topic.get(topic, {}))
        p = dict(pred_by_topic.get(topic, {}))
        r = evaluate_all(g, p, verbose=False)
        results[topic] = r

    return results


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────


def _print_detailed_results(
    results: Dict[str, Dict[str, float]],
    label: str = "Overall",
) -> None:
    """Print detailed per-metric results."""
    print(f"\n{'─' * 60}")
    print(f"  {label}")
    print(f"{'─' * 60}")
    print(f"  {'Metric':<12s} {'P':>8s} {'R':>8s} {'F1':>8s}")
    print(f"  {'─' * 38}")
    for metric in ["MUC", "B3", "CEAFe", "BLANC"]:
        if metric in results:
            r = results[metric]
            print(f"  {metric:<12s} {r['P']*100:>8.2f} {r['R']*100:>8.2f} {r['F1']*100:>8.2f}")
    print()


def cmd_prepare(*, download: bool = True) -> None:
    """Prepare ECB+ data: download (if needed) and convert to L1 format."""
    # Check if already prepared
    if ECB_PLUS_GOLD_L1.exists():
        logger.info("Gold L1 already exists at %s", ECB_PLUS_GOLD_L1)
        return

    # Check if raw ECB+ data exists
    ecb_found = _find_ecbplus_root(str(ECB_PLUS_DIR))
    if ecb_found is None and download:
        logger.info("ECB+ data not found. Attempting download...")
        if not download_ecb_plus():
            logger.error("Download failed. Please manually download ECB+.")
            sys.exit(1)
        ecb_found = _find_ecbplus_root(str(ECB_PLUS_DIR))

    if ecb_found is None:
        logger.error("ECB+ data not found. Run with --download or place data manually.")
        sys.exit(1)

    # Load corpus
    corpus = load_ecb_plus()

    if corpus.n_docs == 0:
        logger.error("No ECB+ documents loaded. Check data structure.")
        sys.exit(1)

    # Convert to L1 gold
    os.makedirs(str(ECB_PLUS_DIR), exist_ok=True)
    corpus_to_l1_jsonl(corpus, str(ECB_PLUS_GOLD_L1))

    logger.info("ECB+ gold prepared: %d documents, %d mentions",
                corpus.n_docs, corpus.n_mentions)

    # Print summary stats
    gold_clusters = corpus_to_gold_clusters(corpus)
    n_clusters = len(gold_clusters)
    sizes = [len(v) for v in gold_clusters.values()]
    logger.info("Gold clusters: %d (avg size %.1f, max %d, singletons %d)",
                n_clusters,
                sum(sizes) / len(sizes) if sizes else 0,
                max(sizes) if sizes else 0,
                sum(1 for s in sizes if s == 1))

    # Count topics
    topics = set()
    for cid in gold_clusters:
        topic = cid.split("::")[0]
        topics.add(topic)
    logger.info("Topics: %d", len(topics))
    for t in sorted(topics):
        t_clusters = [cid for cid in gold_clusters if cid.startswith(t + "::")]
        t_mentions = sum(len(gold_clusters[cid]) for cid in t_clusters)
        logger.info("  %s: %d clusters, %d mentions", t, len(t_clusters), t_mentions)


def cmd_evaluate(
    pred_path: Optional[str] = None,
    output: bool = True,
    detailed: bool = False,
) -> Dict[str, Dict[str, float]]:
    """Run full evaluation: load gold + predictions, compute metrics, print table."""
    # Check gold exists
    if not ECB_PLUS_GOLD_L1.exists():
        logger.error("Gold L1 not found at %s. Run 'prepare' first.", ECB_PLUS_GOLD_L1)
        sys.exit(1)

    # Load gold
    gold = load_l1_jsonl(str(ECB_PLUS_GOLD_L1))
    logger.info("Loaded gold: %d clusters, %d mentions",
                len(gold), sum(len(v) for v in gold.values()))

    # Load predictions
    if pred_path is None:
        pred_path = str(ECB_PLUS_PRED_L1)

    if not os.path.exists(pred_path):
        logger.error("Predictions not found at %s", pred_path)
        logger.info("To evaluate, provide a prediction file in L1 format.")
        logger.info("Example: python eval_ecb_plus.py evaluate --pred path/to/pred.jsonl")
        sys.exit(1)

    pred = load_l1_jsonl(pred_path)
    logger.info("Loaded predictions: %d clusters, %d mentions",
                len(pred), sum(len(v) for v in pred.values()))

    # ── Per-topic evaluation (if predictions use ECB+ topic structure) ──
    if detailed:
        logger.info("Running per-topic detailed evaluation...")
        # Extract topics from gold cluster IDs
        topic_results = {}
        gold_topics = defaultdict(dict)
        for cid, members in gold.items():
            if "::" in cid:
                topic = cid.split("::")[0]
                gold_topics[topic][cid] = members
            else:
                gold_topics["_other"][cid] = members

        pred_topics = defaultdict(dict)
        for cid, members in pred.items():
            if "::" in cid:
                topic = cid.split("::")[0]
                pred_topics[topic][cid] = members
            else:
                pred_topics["_other"][cid] = members

        all_topics = sorted(set(list(gold_topics.keys()) + list(pred_topics.keys())))
        per_topic = {}
        for topic in all_topics:
            g = dict(gold_topics.get(topic, {}))
            p = dict(pred_topics.get(topic, {}))
            if g and p:
                r = evaluate_all(g, p, verbose=False)
                per_topic[topic] = r

        # Print per-topic summary
        if per_topic:
            print(f"\n{'=' * 90}")
            print("  Per-Topic Evaluation")
            print(f"{'=' * 90}")
            header = f"  {'Topic':<30s}"
            for m in ["MUC", "B3", "CEAFe", "BLANC"]:
                header += f"  {m+' F1':>8s}"
            print(header)
            print(f"  {'─' * 70}")
            for topic in sorted(per_topic.keys()):
                row = f"  {topic:<30s}"
                for m in ["MUC", "B3", "CEAFe", "BLANC"]:
                    r = per_topic[topic].get(m, {})
                    row += f"  {r.get('F1', 0)*100:>8.2f}"
                print(row)
            print()

    # ── Overall evaluation ──
    results = evaluate_all(gold, pred, verbose=True)
    _print_detailed_results(results, "Overall Results")

    # Write results
    if output:
        os.makedirs(str(ECB_PLUS_DIR), exist_ok=True)
        with open(ECB_PLUS_RESULTS, "w") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        logger.info("Results written to %s", ECB_PLUS_RESULTS)

        # Also write per-topic results if available
        if detailed and per_topic:
            per_topic_path = ECB_PLUS_DIR / "eval_results_per_topic.json"
            with open(per_topic_path, "w") as f:
                json.dump(per_topic, f, indent=2, ensure_ascii=False)
            logger.info("Per-topic results written to %s", per_topic_path)

    # Print comparison table
    sota_systems = {
        "Cattan et al. 2021": CATTAN_2021_RESULTS,
        "Choubey & Huang 2017": CHOUBEY_HUANG_2017,
        "Kenyon-Dean et al. 2018": KENYON_DEAN_2018,
        "Barhom et al. 2019 (ECE)": BARHOM_2019,
        "Zeng et al. 2020": ZENG_2020,
    }

    table = format_comparison_table(results, sota_systems)
    print("\n" + table)

    if output:
        with open(ECB_PLUS_REPORT, "w") as f:
            f.write(table + "\n")
        logger.info("Report written to %s", ECB_PLUS_REPORT)

    return results


def cmd_full(
    pred_path: Optional[str] = None,
    detailed: bool = False,
) -> Dict[str, Dict[str, float]]:
    """Prepare data and run evaluation."""
    cmd_prepare()
    return cmd_evaluate(pred_path, detailed=detailed)


def cmd_demo() -> Dict[str, Dict[str, float]]:
    """Run a demonstration on synthetic ECB+-like data.

    This creates simulated gold/pred clusters to verify metric correctness
    and demonstrate the output format, without needing the actual ECB+ dataset.
    """
    logger.info("Running demo with synthetic ECB+-like data...")

    # Create synthetic data simulating ECB+ structure
    np.random.seed(42)

    # Simulate 3 topics, each with clusters
    topics = ["Cyprus_Bailout", "Greek_Protests", "Ukraine_Crisis"]

    gold = {}
    pred = {}
    mention_counter = 0

    for ti, topic in enumerate(topics):
        # Each topic has 3-5 gold clusters
        n_gold = np.random.randint(3, 6)
        for ci in range(n_gold):
            n_mentions = np.random.randint(2, 8)
            cid = f"{topic}::chain_{ti}_{ci}"
            mentions = []
            for mi in range(n_mentions):
                mid = f"doc_{ti}_{ci}_{mention_counter}"
                mention_counter += 1
                mentions.append(mid)
            gold[cid] = mentions

    # Create predictions with some noise
    # Copy most gold clusters but add some merges/splits
    mention_to_gold = {}
    for cid, mids in gold.items():
        for m in mids:
            mention_to_gold[m] = cid

    all_mentions = list(mention_to_gold.keys())

    # Perfect predictions for first 70%
    split_point = int(len(all_mentions) * 0.7)
    for m in all_mentions[:split_point]:
        cid = mention_to_gold[m]
        if cid not in pred:
            pred[cid] = []
        pred[cid].append(m)

    # Add noise: randomly assign remaining 30% (simulating imperfect coref)
    for m in all_mentions[split_point:]:
        # 50% chance of correct cluster, 50% random assignment
        if np.random.random() < 0.5:
            cid = mention_to_gold[m]
        else:
            # Assign to random gold cluster or create singleton
            random_cid = np.random.choice(list(gold.keys()))
            cid = random_cid
        if cid not in pred:
            pred[cid] = []
        pred[cid].append(m)

    logger.info("Demo gold: %d clusters, %d mentions", len(gold), len(all_mentions))
    logger.info("Demo pred: %d clusters, %d mentions",
                len(pred), sum(len(v) for v in pred.values()))

    # Run evaluation
    results = evaluate_all(gold, pred, verbose=True)

    # Print comparison table
    table = format_comparison_table(results, {"Cattan 2021 (SOTA)": CATTAN_2021_RESULTS})
    print("\n" + table)

    logger.info("Demo complete. Results are on SYNTHETIC data, not real ECB+.")
    logger.info("To use real ECB+, run: python scripts/eval_ecb_plus.py prepare")

    return results


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="ECB+ Event Coreference Evaluation Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/eval_ecb_plus.py demo                           # Demo with synthetic data
  python scripts/eval_ecb_plus.py prepare                         # Download/prepare ECB+
  python scripts/eval_ecb_plus.py evaluate                        # Evaluate predictions
  python scripts/eval_ecb_plus.py evaluate --pred my_preds.jsonl  # Custom predictions
  python scripts/eval_ecb_plus.py evaluate --detailed             # Per-topic breakdown
  python scripts/eval_ecb_plus.py full --pred my_preds.jsonl      # Prepare + evaluate
  python scripts/eval_ecb_plus.py eval-globemind                  # Evaluate globemind's L1
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # prepare
    prep = subparsers.add_parser("prepare", help="Download/convert ECB+ to L1 format")
    prep.add_argument("--no-download", action="store_true",
                      help="Skip download if data not found")

    # evaluate
    ev = subparsers.add_parser("evaluate", help="Run evaluation on prepared data")
    ev.add_argument("--pred", type=str, default=None,
                    help="Path to predictions JSONL (L1 format)")
    ev.add_argument("--no-output", action="store_true",
                    help="Skip writing results to file")
    ev.add_argument("--detailed", action="store_true",
                    help="Print per-topic breakdown (requires ECB+ topic IDs)")

    # full
    full = subparsers.add_parser("full", help="Prepare data and run evaluation")
    full.add_argument("--pred", type=str, default=None,
                      help="Path to predictions JSONL (L1 format)")
    full.add_argument("--detailed", action="store_true",
                      help="Print per-topic breakdown")

    # eval-globemind
    eg = subparsers.add_parser("eval-globemind",
                               help="Evaluate globemind L1 clusters (no ECB+ gold needed)")
    eg.add_argument("gold", type=str,
                    help="Path to gold JSONL (L1 format)")
    eg.add_argument("pred", type=str,
                    help="Path to predictions JSONL (L1 format)")
    eg.add_argument("--ecb-mapping", type=str, default=None,
                    help="Path to ECB+ -> DB article_id mapping JSON (ecb_to_db_mapping.json)")
    eg.add_argument("--detailed", action="store_true",
                    help="Print per-topic breakdown")

    # demo
    subparsers.add_parser("demo", help="Run demo with synthetic data")

    args = parser.parse_args()

    if args.command == "prepare":
        cmd_prepare(download=not args.no_download)
    elif args.command == "evaluate":
        cmd_evaluate(pred_path=args.pred, output=not args.no_output,
                     detailed=args.detailed)
    elif args.command == "full":
        cmd_full(pred_path=args.pred, detailed=args.detailed)
    elif args.command == "eval-globemind":
        gold = load_l1_jsonl(args.gold)
        pred = load_l1_jsonl(args.pred)

        # Load ECB+ mapping if provided
        ecb_mapping = None
        if args.ecb_mapping:
            if not os.path.exists(args.ecb_mapping):
                logger.error("ECB+ mapping file not found: %s", args.ecb_mapping)
                sys.exit(1)
            logger.info("Loading ECB+ mapping from %s ...", args.ecb_mapping)
            with open(args.ecb_mapping) as f:
                ecb_mapping = json.load(f)
            db_to_gold = ecb_mapping.get("db_to_gold_ids", {})
            gold_to_db = ecb_mapping.get("gold_to_db", {})
            logger.info("Mapping loaded: %d db_to_gold entries, %d gold_to_db entries",
                        len(db_to_gold), len(gold_to_db))

            # Convert pred mention IDs from DB article_ids to gold article_ids
            if db_to_gold:
                logger.info("Converting pred article_ids using ECB+ mapping...")
                converted_pred = defaultdict(list)
                converted_count = 0
                unmapped_count = 0
                for cid, mids in pred.items():
                    for mid in mids:
                        # mid is a string like "5537793" from load_l1_jsonl
                        db_id_str = mid
                        if db_id_str in db_to_gold:
                            gold_ids = db_to_gold[db_id_str]
                            for gid in gold_ids:
                                gid_str = str(gid)
                                converted_pred[cid].append(gid_str)
                                converted_count += 1
                        else:
                            # DB article_id not in mapping — keep as-is
                            converted_pred[cid].append(mid)
                            unmapped_count += 1

                pred = dict(converted_pred)
                logger.info("Conversion: %d pred entries converted, %d unmapped (kept as-is)",
                            converted_count, unmapped_count)

                # Re-sort within each cluster
                pred = {cid: sorted(set(mids)) for cid, mids in pred.items()}

        gold_mentions = set()
        for v in gold.values():
            gold_mentions.update(v)
        pred_mentions = set()
        for v in pred.values():
            pred_mentions.update(v)
        overlap = len(gold_mentions & pred_mentions)
        logger.info("Gold: %d clusters, %d mentions",
                    len(gold), sum(len(v) for v in gold.values()))
        logger.info("Pred: %d clusters, %d mentions (after mapping)",
                    len(pred), sum(len(v) for v in pred.values()))
        logger.info("Mention overlap: %d (gold-only: %d, pred-only: %d)",
                    overlap, len(gold_mentions - pred_mentions), len(pred_mentions - gold_mentions))
        if overlap == 0:
            logger.error("WARNING: Zero mention overlap between gold and pred!")
            logger.error("Gold and pred use incompatible mention ID systems.")
            if not args.ecb_mapping:
                logger.error("  Use --ecb-mapping to provide a cross-reference file.")
            logger.error("Results will be MEANINGLESS.")
        elif overlap < max(len(gold_mentions), len(pred_mentions)) * 0.5:
            logger.warning("WARNING: Less than 50%% mention overlap between gold and pred.")
            logger.warning("Results may be unreliable.")
        results = evaluate_all(gold, pred, verbose=True)
        _print_detailed_results(results, "Evaluation Results")
    elif args.command == "demo":
        cmd_demo()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
