"""
Keyword co-occurrence graph topic clustering — four-stage pipeline.

Architecture:
  1. Keyword extraction: sklearn TfidfVectorizer 提取每篇文档 top-20 关键词
  2. Keyword co-occurrence graph: networkx 构建关键词共现图（边权重 = 共现次数）
  3. Community detection: python-louvain (community.best_partition) 检测话题社区
  4. Document assignment: 余弦相似度将每篇文档分配到最近的话题中心
  5. Output: {topic_id: [article_ids]}

Relies on scikit-learn for TF-IDF, networkx for graph, python-louvain for community detection.
"""
from __future__ import annotations

import logging
import time
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

try:
    from sklearn.feature_extraction.text import TfidfVectorizer as _TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity as _cosine_sim
    _HAS_SKLEARN = True
except ImportError:
    _TfidfVectorizer = None  # type: ignore
    _cosine_sim = None
    _HAS_SKLEARN = False

try:
    import networkx as nx
    _HAS_NETWORKX = True
except ImportError:
    nx = None  # type: ignore
    _HAS_NETWORKX = False

try:
    import community as _community_louvain
    _HAS_LOUVAIN = True
except ImportError:
    _community_louvain = None
    _HAS_LOUVAIN = False

logger = logging.getLogger("topic_clustering")
# Ensure real-time log visibility even under pipe/redirect
_has_flush_handler = False
for h in logger.handlers:
    if hasattr(h, 'flush'):
        _has_flush_handler = True
if not _has_flush_handler:
    _console = logging.StreamHandler()
    _console.setFormatter(logging.Formatter("%(asctime)s topic_clustering %(message)s"))
    logger.addHandler(_console)
    logger.propagate = False

# ── Default parameters ──────────────────────────────────────
DEFAULT_TOP_K = 20            # Top keywords per document
DEFAULT_NGRAM_RANGE = (1, 3)  # Character n-gram range (handles CJK without segmentation)
DEFAULT_MAX_FEATURES = 5000   # Max vocabulary size
DEFAULT_MIN_DF = 2            # Minimum document frequency for inclusion
DEFAULT_MAX_DF = 0.85         # Maximum document frequency (drops corpus-wide stopwords)
MIN_GRAPH_NODES = 3           # Minimum graph nodes to attempt community detection


def extract_keywords_tfidf(
    documents: Dict[int, str],
    top_k: int = DEFAULT_TOP_K,
    ngram_range: Tuple[int, int] = DEFAULT_NGRAM_RANGE,
    max_features: int = DEFAULT_MAX_FEATURES,
    min_df: int = DEFAULT_MIN_DF,
    max_df: float = DEFAULT_MAX_DF,
) -> Tuple["_TfidfVectorizer", np.ndarray, List[str], Dict[int, List[Tuple[str, float]]]]:
    """Extract top-K keywords per document using TF-IDF vectorizer.

    Two-tier: character n-grams handle CJK languages without explicit word
    segmentation, while sublinear TF scaling dampens frequency effects.

    Args:
        documents: {article_id: body_text}
        top_k: Number of top keywords to extract per document.
        ngram_range: Character n-gram range (default (1,3) captures
                     unigrams, bigrams, and trigrams).
        max_features: Maximum vocabulary size for TfidfVectorizer.
        min_df: Minimum document frequency — terms appearing in fewer
                than this many docs are ignored.
        max_df: Maximum document frequency ratio — terms appearing in
                more than this fraction of docs are ignored (corpus
                stopwords like "the", "a" in English).

    Returns:
        Tuple of:
          - Fitted TfidfVectorizer
          - Document-term TF-IDF matrix (n_docs × n_features, CSR)
          - Feature names list (vocabulary)
          - {article_id: [(keyword, tfidf_score), ...]}
          Each document's keywords sorted by TF-IDF score descending.

    Raises:
        ValueError: If documents dict is empty or all texts are empty.
        ValueError: If the resulting vocabulary is empty after filtering.
    """
    if not documents:
        raise ValueError("Empty document set — at least one document required")

    ids = list(documents.keys())
    texts = [documents[a_id] for a_id in ids]

    # Filter truly empty texts
    non_empty_texts = [(i, t) for i, t in enumerate(texts) if t and isinstance(t, str) and t.strip()]
    if not non_empty_texts:
        raise ValueError("All documents have empty body text — cannot extract keywords")

    if len(non_empty_texts) < len(texts):
        logger.info("Filtered %d empty/blank documents from %d total",
                    len(texts) - len(non_empty_texts), len(texts))

    _indices, _texts = zip(*non_empty_texts)
    _ids_used = [ids[i] for i in _indices]
    n_docs = len(_texts)

    # ── Auto-adjust min_df/max_df for very small corpora ──
    # When n_docs is small, min_df=2 may exceed max_df threshold,
    # causing TfidfVectorizer to raise "max_df < min_df" error.
    # Ensure: max_df_{count} >= min_df, i.e. max_df * n_docs >= min_df
    _min_df = min(min_df, max(1, n_docs - 1))
    _max_df = max_df
    # If the ratio-based max_df would filter too aggressively, relax it
    if _max_df < 1.0 and _max_df * n_docs < _min_df:
        _max_df = min(1.0, (_min_df + 0.5) / n_docs)

    if _min_df != min_df or abs(_max_df - max_df) > 0.01:
        logger.info("Adjusted min_df=%d→%d, max_df=%.2f→%.2f for %d docs",
                    min_df, _min_df, max_df, _max_df, n_docs)

    logger.info("TF-IDF extraction: %d docs, max_features=%d, ngram=%s, min_df=%d, max_df=%.2f",
                n_docs, max_features, ngram_range, _min_df, _max_df)

    t0 = time.time()
    vectorizer = _TfidfVectorizer(
        ngram_range=ngram_range,
        max_features=max_features,
        min_df=_min_df,
        max_df=_max_df,
        token_pattern=r'(?u)\b\w\w+\b',
        strip_accents="unicode",
        sublinear_tf=True,
        norm="l2",
    )
    tfidf_matrix = vectorizer.fit_transform(_texts)
    feature_names: List[str] = vectorizer.get_feature_names_out().tolist()

    if not feature_names:
        raise ValueError("Empty vocabulary after TF-IDF fitting — all features filtered")

    logger.info("TF-IDF fit in %.2fs — matrix %s, vocabulary=%d",
                time.time() - t0, str(tfidf_matrix.shape), len(feature_names))

    # Extract top-K keywords per document
    doc_keywords: Dict[int, List[Tuple[str, float]]] = {}
    for i, a_id in enumerate(_ids_used):
        row = tfidf_matrix[i]
        if row.nnz == 0:
            doc_keywords[a_id] = []
            continue

        coo = row.tocoo()
        scores: List[Tuple[int, float]] = list(zip(coo.col, coo.data))
        scores.sort(key=lambda x: -x[1])
        top_scores = scores[:top_k]

        doc_keywords[a_id] = [
            (feature_names[idx], float(score))
            for idx, score in top_scores
        ]

    # For documents that were empty (filtered out), assign empty keyword list
    for a_id in ids:
        if a_id not in doc_keywords:
            doc_keywords[a_id] = []

    return vectorizer, tfidf_matrix, feature_names, doc_keywords


def build_cooccurrence_graph(
    doc_keywords: Dict[int, List[Tuple[str, float]]],
) -> "nx.Graph":
    """Build weighted keyword co-occurrence graph.

    Nodes = unique keywords across all documents.
    Edges = co-occurrence of two keywords in the same document.
    Edge weight = number of documents in which the pair co-occurs.

    Self-loops and duplicate edges are excluded. The graph is undirected.

    Args:
        doc_keywords: {article_id: [(keyword, tfidf_score), ...]}
                      Only the keyword strings are used; TF-IDF scores
                      are carried for later assignment steps.

    Returns:
        networkx.Graph with:
          - Nodes: keyword strings
          - Edge attribute "weight": integer co-occurrence count

    Raises:
        ValueError: If no keywords found across all documents.
    """
    edge_weight: Dict[Tuple[str, str], int] = defaultdict(int)
    node_set: Set[str] = set()

    for a_id, kw_list in doc_keywords.items():
        keywords = [kw for kw, _ in kw_list]
        if len(keywords) < 2:
            # Single keyword adds its node but no edges
            for kw in keywords:
                node_set.add(kw)
            continue

        # Add nodes
        for kw in keywords:
            node_set.add(kw)

        # Add all unordered pairs with co-occurrence
        for i in range(len(keywords)):
            for j in range(i + 1, len(keywords)):
                k1, k2 = keywords[i], keywords[j]
                if k1 != k2:
                    key: Tuple[str, str] = (k1, k2) if k1 < k2 else (k2, k1)
                    edge_weight[key] += 1

    if not node_set:
        raise ValueError("No keywords found — cannot build co-occurrence graph")

    graph = nx.Graph()
    graph.add_nodes_from(node_set)
    graph.add_edges_from(
        (k1, k2, {"weight": w})
        for (k1, k2), w in edge_weight.items()
    )

    logger.info("Co-occurrence graph: %d nodes, %d edges, avg degree=%.2f",
                graph.number_of_nodes(), graph.number_of_edges(),
                2 * graph.number_of_edges() / max(graph.number_of_nodes(), 1))

    return graph


def detect_topic_communities(
    graph: "nx.Graph",
    resolution: float = 1.0,
) -> Tuple[Dict[str, int], int]:
    """Detect topic communities in keyword co-occurrence graph using Louvain.

    The Louvain method maximizes modularity to partition the keyword graph
    into semantically coherent communities, each representing a distinct topic.

    Args:
        graph: Weighted keyword co-occurrence graph (networkx).
               Edge weights should reflect co-occurrence frequency.
        resolution: Louvain resolution parameter.
            - resolution=1.0: standard modularity
            - resolution > 1.0: favors more, smaller communities
            - resolution < 1.0: favors fewer, larger communities

    Returns:
        Tuple of:
          - {keyword: community_id}: Mapping from keyword to integer
            community label (0-indexed).
          - int: Number of detected communities (topics).

    Raises:
        RuntimeError: If graph is too small for meaningful community detection.
    """
    if graph.number_of_nodes() < MIN_GRAPH_NODES:
        raise RuntimeError(
            f"Graph too small ({graph.number_of_nodes()} nodes) for "
            f"community detection — need at least {MIN_GRAPH_NODES}"
        )

    if graph.number_of_edges() == 0:
        # Isolated nodes only — each node is its own community
        partition: Dict[str, int] = {node: i for i, node in enumerate(graph.nodes())}
        num_topics = len(partition)
        logger.info("No edges in graph — each keyword assigned its own community (%d topics)", num_topics)
        return partition, num_topics

    t0 = time.time()

    # Best_partition does NOT handle random_state in older versions.
    # Provide it compatibly via kwargs (version-detection-free).
    import inspect
    sig = inspect.signature(_community_louvain.best_partition)
    kwargs: Dict[str, Any] = {"weight": "weight", "resolution": resolution}
    if "random_state" in sig.parameters:
        kwargs["random_state"] = 42

    partition = _community_louvain.best_partition(graph, **kwargs)
    elapsed = time.time() - t0

    community_ids: Set[int] = set(partition.values())
    num_topics = len(community_ids)

    # Size distribution for logging
    size_dist = Counter(partition.values())
    size_buckets = Counter()
    for c_id, cnt in size_dist.items():
        bucket = 1 if cnt == 1 else 2 if cnt <= 2 else 5 if cnt <= 5 else 10 if cnt <= 10 else 50 if cnt <= 50 else 100
        size_buckets[bucket] += 1

    size_summary = ", ".join(
        f"≤{b}={size_buckets[b]}" for b in sorted(size_buckets)
    )
    logger.info("Louvain community detection: %d communities in %.2fs [%s]",
                num_topics, elapsed, size_summary)

    return partition, num_topics


def assign_documents_to_topics(
    documents: Dict[int, str],
    ids: List[int],
    tfidf_matrix: np.ndarray,
    feature_names: List[str],
    keyword_topic_map: Dict[str, int],
    vectorizer: _TfidfVectorizer,
) -> Dict[int, str]:
    """Assign each document to the nearest topic via cosine similarity.

    For each topic community, builds a centroid vector over the TF-IDF
    feature space (IDF-weighted membership of keywords in that community).
    Each document's TF-IDF vector is compared to all topic centroids via
    cosine similarity; the document is assigned to the highest-scoring topic.

    Args:
        documents: {article_id: body_text} — used for ID ordering.
        ids: Ordered list of article IDs matching tfidf_matrix rows.
        tfidf_matrix: Document-term TF-IDF matrix (n_docs × n_features).
                      Expected to be L2-normalized per row.
        feature_names: Vocabulary list matching columns of tfidf_matrix.
        keyword_topic_map: {keyword: topic_id} from community detection.
        vectorizer: Fitted TfidfVectorizer (used to access IDF weights).

    Returns:
        {article_id: topic_id_str} mapping where topic_id_str is a string
        representation of the integer community label (e.g., "0", "1").
    """
    # ── Build feature-index → topic lookup ──
    # feature_to_topic[i] = topic_id if feature_names[i] is in keyword_topic_map
    # We create a dict for O(1) lookup
    topic_of_feature: Dict[int, int] = {}
    for feat_idx, feat_name in enumerate(feature_names):
        if feat_name in keyword_topic_map:
            topic_of_feature[feat_idx] = keyword_topic_map[feat_name]

    if not topic_of_feature:
        # Fallback: no features matched any keyword (edge case for empty vocab)
        logger.warning("No feature-to-topic mapping — assigning all docs to topic '0'")
        return {a_id: "0" for a_id in ids}

    unique_topics: List[int] = sorted(set(keyword_topic_map.values()))
    n_topics = len(unique_topics)
    n_features = len(feature_names)

    # ── Build topic centroid vectors ──
    # Centroid[t][j] = IDF_weight(j) if feature j belongs to topic t, else 0
    idf = getattr(vectorizer, "idf_", None)

    topic_vectors = np.zeros((n_topics, n_features), dtype=np.float64)
    for feat_idx, topic_id in topic_of_feature.items():
        t_idx = unique_topics.index(topic_id)
        if idf is not None:
            topic_vectors[t_idx, feat_idx] = idf[feat_idx]
        else:
            topic_vectors[t_idx, feat_idx] = 1.0

    # Normalize each topic vector to unit length for cosine similarity
    norms = np.linalg.norm(topic_vectors, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    topic_vectors = topic_vectors / norms

    # ── Compute cosine similarity ──
    # tfidf_matrix is L2-normalized by vectorizer, so dot product = cosine sim
    doc_topic_sim: np.ndarray = tfidf_matrix @ topic_vectors.T  # (n_docs, n_topics)

    # ── Assign each document ──
    assignments: Dict[int, str] = {}
    n_docs = len(ids)
    for i, a_id in enumerate(ids):
        if i < n_docs and i < doc_topic_sim.shape[0]:
            best_topic_idx = int(np.argmax(doc_topic_sim[i]))
            topic_id = unique_topics[best_topic_idx]
            assignments[a_id] = str(topic_id)
        else:
            assignments[a_id] = str(unique_topics[0])

    # Log assignment distribution
    topic_doc_counts = Counter(assignments.values())
    logger.info("Document assignment: %d docs across %d topics",
                len(assignments), len(topic_doc_counts))

    return assignments


def cluster_topics(
    documents: Dict[int, str],
    top_k: int = DEFAULT_TOP_K,
    ngram_range: Tuple[int, int] = DEFAULT_NGRAM_RANGE,
    max_features: int = DEFAULT_MAX_FEATURES,
    min_df: int = DEFAULT_MIN_DF,
    max_df: float = DEFAULT_MAX_DF,
    resolution: float = 1.0,
) -> Dict[str, List[int]]:
    """Cluster documents into topics via keyword co-occurrence graph.

    Full four-stage pipeline:
      1. **Keyword extraction** — sklearn TfidfVectorizer extracts top-K
         keywords per document using character n-grams (handles CJK).
      2. **Co-occurrence graph** — networkx graph where nodes are keywords,
         edges weighted by co-occurrence count across documents.
      3. **Community detection** — python-louvain (Louvain algorithm) partitions
         the keyword graph into topic communities via modularity maximization.
      4. **Document assignment** — each document's TF-IDF vector is compared
         to IDF-weighted topic centroids via cosine similarity; assigned to
         the nearest topic.

    Args:
        documents: {article_id: body_text}
            Article bodies for keyword extraction. Non-string or empty values
            are filtered out with a warning.
        top_k: Number of top keywords to extract per document (default 20).
        ngram_range: Character n-gram range for TF-IDF
            (default (1,3) covers unigrams, bigrams, and trigrams).
        max_features: Maximum vocabulary size for TfidfVectorizer (default 5000).
        min_df: Minimum document frequency for TF-IDF terms (default 2).
        max_df: Maximum document frequency ratio for TF-IDF terms (default 0.85).
        resolution: Louvain resolution parameter (default 1.0).
            > 1.0 → more fine-grained topics; < 1.0 → fewer broader topics.

    Returns:
        {topic_id: [article_id, ...]}
        topic_id is a string (community label, e.g. "0", "1", ...).
        Article IDs within each topic are sorted ascending.

    Raises:
        ImportError: If required dependencies (sklearn, networkx, community)
                     are not installed.
        ValueError: If input documents are invalid or pipeline fails.
        RuntimeError: If community detection fails on the keyword graph.

    Example:
        >>> docs = {1: "China and US discuss trade tariffs", 2: "US tariffs on Chinese goods"}
        >>> result = cluster_topics(docs, top_k=5)
        >>> isinstance(result, dict)
        True
        >>> all(isinstance(v, list) for v in result.values())
        True
    """
    # ── Dependency check ──
    if not _HAS_SKLEARN:
        raise ImportError(
            "sklearn is required for topic clustering. "
            "Install with: pip install scikit-learn"
        )
    if not _HAS_NETWORKX:
        raise ImportError(
            "networkx is required for topic clustering. "
            "Install with: pip install networkx"
        )
    if not _HAS_LOUVAIN:
        raise ImportError(
            "python-louvain is required for topic clustering. "
            "Install with: pip install python-louvain"
        )

    # ── Input validation ──
    if not documents:
        logger.warning("Empty document set — returning empty result")
        return {}

    # Filter out empty/non-string documents
    non_empty: Dict[int, str] = {}
    for a_id, txt in documents.items():
        if txt and isinstance(txt, str) and txt.strip():
            non_empty[a_id] = txt
        else:
            logger.debug("Skipping empty/invalid document %s", a_id)

    if not non_empty:
        logger.warning("No non-empty documents after filtering — returning empty result")
        return {}

    filtered_count = len(documents) - len(non_empty)
    if filtered_count:
        logger.info("Filtered %d empty/invalid documents", filtered_count)

    t_start = time.time()

    # ── Step 1: Keyword extraction ──
    logger.info("── Topic clustering pipeline ──")
    logger.info("Step 1/4: TF-IDF keyword extraction (top-%d, max_features=%d)...",
                top_k, max_features)
    try:
        vectorizer, tfidf_matrix, feature_names, doc_keywords = extract_keywords_tfidf(
            documents=non_empty,
            top_k=top_k,
            ngram_range=ngram_range,
            max_features=max_features,
            min_df=min_df,
            max_df=max_df,
        )
    except ValueError as e:
        logger.error("Keyword extraction failed: %s", e)
        # Fallback: single topic containing all documents
        logger.warning("Falling back to single-topic assignment")
        return {"0": sorted(non_empty.keys())}

    if not feature_names:
        logger.warning("Empty TF-IDF vocabulary — single-topic fallback")
        return {"0": sorted(non_empty.keys())}

    # ── Step 2: Build co-occurrence graph ──
    logger.info("Step 2/4: Building keyword co-occurrence graph...")
    try:
        graph = build_cooccurrence_graph(doc_keywords)
    except ValueError as e:
        logger.error("Co-occurrence graph build failed: %s", e)
        logger.warning("Falling back to single-topic assignment")
        return {"0": sorted(non_empty.keys())}

    if graph.number_of_nodes() == 0:
        logger.warning("Empty keyword graph — single-topic fallback")
        return {"0": sorted(non_empty.keys())}

    # ── Step 3: Community detection ──
    logger.info("Step 3/4: Detecting topic communities (Louvain, resolution=%.2f)...",
                resolution)
    try:
        keyword_topic_map, num_topics = detect_topic_communities(
            graph, resolution=resolution,
        )
    except RuntimeError as e:
        logger.warning("Community detection skipped: %s", e)
        logger.warning("Assigning all keywords to a single topic")
        keyword_topic_map = {node: 0 for node in graph.nodes()}
        num_topics = 1

    # ── Step 4: Document assignment ──
    logger.info("Step 4/4: Assigning %d documents to %d topics...",
                len(non_empty), num_topics)
    ids_ordered: List[int] = list(non_empty.keys())
    doc_assignments = assign_documents_to_topics(
        documents=non_empty,
        ids=ids_ordered,
        tfidf_matrix=tfidf_matrix,
        feature_names=feature_names,
        keyword_topic_map=keyword_topic_map,
        vectorizer=vectorizer,
    )

    if not doc_assignments:
        logger.warning("Document assignment returned empty — single-topic fallback")
        return {"0": sorted(non_empty.keys())}

    # ── Build output: {topic_id: [article_ids]} ──
    result: Dict[str, List[int]] = defaultdict(list)
    for a_id, topic_id in doc_assignments.items():
        result[topic_id].append(a_id)

    # Sort article IDs within each topic
    for topic_id in result:
        result[topic_id].sort()

    # Sort topic keys by size descending (largest topic first)
    result = dict(
        sorted(result.items(), key=lambda x: -len(x[1]))
    )

    elapsed = time.time() - t_start
    n_singleton_topics = sum(1 for v in result.values() if len(v) == 1)
    n_total_docs = sum(len(v) for v in result.values())
    logger.info(
        "Topic clustering complete: %d topics (%d singletons), %d documents in %.1fs",
        len(result), n_singleton_topics, n_total_docs, elapsed,
    )

    return result


def print_cluster_summary(
    clusters: Dict[str, List[int]],
    documents: Optional[Dict[int, str]] = None,
    doc_keywords: Optional[Dict[int, List[Tuple[str, float]]]] = None,
) -> None:
    """Print human-readable topic clustering summary.

    Displays topic size distribution, top keywords per topic, and sample
    article IDs for each topic.

    Args:
        clusters: {topic_id: [article_id, ...]} from cluster_topics.
        documents: Optional {article_id: body_text} for displaying snippets.
        doc_keywords: Optional {article_id: [(keyword, score), ...]}
                      for displaying topic keyword profiles.
    """
    if not clusters:
        print("\n[TEMPTY] No topic clusters found.\n")
        return

    print(f"\n{'=' * 60}")
    print("TOPIC CLUSTERING SUMMARY")
    print('=' * 60)

    n_topics = len(clusters)
    n_articles = sum(len(v) for v in clusters.values())
    n_singletons = sum(1 for v in clusters.values() if len(v) == 1)

    print(f"\nTotal topics:     {n_topics}")
    print(f"Total articles:   {n_articles}")
    print(f"Singletons:       {n_singletons} ({100 * n_singletons // max(n_topics, 1)}%)")

    # Size distribution
    size_dist = Counter()
    for aids in clusters.values():
        sz = len(aids)
        bucket = (
            1 if sz == 1 else
            2 if sz <= 2 else
            3 if sz <= 3 else
            5 if sz <= 5 else
            10 if sz <= 10 else
            20 if sz <= 20 else
            50 if sz <= 50 else
            100 if sz <= 100 else
            999
        )
        size_dist[bucket] += 1

    print(f"\nTopic size distribution:")
    for b in [1, 2, 3, 5, 10, 20, 50, 100, 999]:
        label = {
            1: "1", 2: "2", 3: "3", 5: "4-5",
            10: "6-10", 20: "11-20", 50: "21-50",
            100: "51-100", 999: "100+",
        }
        if b in size_dist:
            print(f"  {label[b]:>8}: {size_dist[b]}")

    # Per-topic details
    sorted_topics = sorted(clusters.items(), key=lambda x: -len(x[1]))
    print(f"\n── Topic details (top {min(10, len(sorted_topics))}) ──")

    for t_id, aids in sorted_topics[:10]:
        snippet = ""
        if documents and aids:
            txt = documents.get(aids[0], "")
            if txt:
                snippet = txt[:80].replace("\n", " ") + "..."

        # Collect top keywords across documents in this topic
        topic_keywords: Counter = Counter()
        if doc_keywords:
            for a_id in aids:
                for kw, score in doc_keywords.get(a_id, []):
                    topic_keywords[kw] += 1
            top_kws = [kw for kw, _ in topic_keywords.most_common(5)]
        else:
            top_kws = []

        kw_str = f" | keywords: {', '.join(top_kws)}" if top_kws else ""
        print(f"\n  [Topic {t_id}] {len(aids):>4} articles{kw_str}")
        if snippet:
            print(f"    e.g.: {snippet}")
        if len(aids) <= 5:
            print(f"    IDs: {aids}")
        else:
            print(f"    IDs: {aids[:5]} ... +{len(aids) - 5} more")

    print()


def validate_document_format(documents: Dict[int, str]) -> Dict[int, str]:
    """Validate and normalize document input for topic clustering.

    Ensures all values are strings, strips whitespace, and logs warnings
    for any malformed entries.

    Args:
        documents: Raw {article_id: body_text} input.

    Returns:
        Cleaned {article_id: body_text} with invalid entries removed.
    """
    validated: Dict[int, str] = {}
    warnings = 0
    for a_id, txt in documents.items():
        # Convert key to int if possible; skip string keys that aren't numeric
        if isinstance(a_id, int):
            key = a_id
        elif isinstance(a_id, str):
            try:
                key = int(a_id)
            except (ValueError, TypeError):
                logger.warning("Skipping document with non-numeric string key: '%s'", a_id)
                warnings += 1
                continue
        else:
            logger.warning("Skipping document with non-integer key: %s (type=%s)",
                           a_id, type(a_id).__name__)
            warnings += 1
            continue

        if not isinstance(txt, str):
            logger.warning("Skipping document %s: body is not a string (type=%s)",
                           a_id, type(txt).__name__)
            warnings += 1
            continue
        stripped = txt.strip()
        if not stripped:
            logger.warning("Skipping document %s: empty body", a_id)
            warnings += 1
            continue
        validated[key] = stripped

    if warnings:
        logger.info("validate_document_format: %d documents removed, %d kept",
                    warnings, len(validated))

    return validated
