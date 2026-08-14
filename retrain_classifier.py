#!/usr/bin/env python3
"""
Retrain document pair classifier using BGE-M3 embeddings from DB.
Trains LogisticRegression + MLPClassifier and saves to all model paths.
"""
import json
import logging
import random
from collections import defaultdict
from pathlib import Path

import numpy as np
import psycopg2
from sklearn.linear_model import LogisticRegression
from sklearn.neural_network import MLPClassifier
from sklearn.metrics import roc_auc_score, f1_score, accuracy_score
from sklearn.model_selection import train_test_split
import joblib

from scripts.db_runtime_config import require_database_password

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("retrain_classifier")

DB_CONFIG = dict(
    host="192.168.207.171",
    port=54333,
    dbname="globemind_news",
    user="postgres",
)
MAPPING_PATH = Path("/root/data/globemind/data/event_coref_mapping_layer1.jsonl")
MODEL_DIR = Path("/root/data/globemind/data/models")
RANDOM_SEED = 42
N_PAIRS = 20000  # 10K positive + 10K negative

random.seed(RANDOM_SEED)
np.random.seed(RANDOM_SEED)


def load_clusters():
    """Load L1 clusters from JSONL. Returns dict: cluster_id -> list of article_ids."""
    clusters = defaultdict(list)
    with open(MAPPING_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            clusters[rec["cluster_id"]].append(rec["article_id"])
    # Filter to clusters with at least 2 articles (so we can make positive pairs)
    valid = {k: v for k, v in clusters.items() if len(v) >= 2}
    logger.info(f"Loaded {len(clusters)} clusters, {len(valid)} have >=2 articles")
    # Count total articles
    all_ids = set()
    for v in clusters.values():
        all_ids.update(v)
    logger.info(f"Total unique articles: {len(all_ids)}")
    return valid, all_ids


def load_embeddings(article_ids):
    """Load BGE-M3 embeddings from DB for given article IDs."""
    if not article_ids:
        return {}

    conn = psycopg2.connect(**DB_CONFIG, password=require_database_password())
    cur = conn.cursor()

    # Batch fetch to avoid huge queries
    batch_size = 1000
    ids_list = list(article_ids)
    embeddings = {}

    for i in range(0, len(ids_list), batch_size):
        batch = ids_list[i : i + batch_size]
        placeholders = ",".join(["%s"] * len(batch))
        query = f"SELECT news_id, embedding::text FROM news_embeddings WHERE news_id IN ({placeholders})"
        cur.execute(query, batch)
        for news_id, emb_json in cur.fetchall():
            embeddings[news_id] = np.array(json.loads(emb_json), dtype=np.float32)

    cur.close()
    conn.close()
    logger.info(f"Loaded {len(embeddings)} embeddings from DB")
    return embeddings


def generate_pairs(clusters, embeddings, n_pairs=N_PAIRS):
    """
    Generate balanced pairs: n_pairs//2 positive, n_pairs//2 negative.
    Returns X (n_pairs, 3), y (n_pairs,).
    """
    # Build article -> cluster mapping
    art_to_cluster = {}
    for cid, articles in clusters.items():
        for a in articles:
            art_to_cluster[a] = cid

    # Filter to articles with embeddings available
    valid_articles = [a for a in art_to_cluster if a in embeddings]
    logger.info(f"Articles with embeddings available: {len(valid_articles)}")

    # Group by cluster for positive sampling
    cluster_members = defaultdict(list)
    for a in valid_articles:
        cluster_members[art_to_cluster[a]].append(a)
    # Filter clusters with >=2 valid articles
    valid_clusters = {k: v for k, v in cluster_members.items() if len(v) >= 2}
    logger.info(f"Clusters with >=2 embedded articles: {len(valid_clusters)}")

    cluster_ids = list(valid_clusters.keys())
    n_pos = n_pairs // 2
    n_neg = n_pairs - n_pos

    X_list = []
    y_list = []

    # Positive pairs
    pos_generated = 0
    attempts = 0
    max_attempts = n_pos * 10
    while pos_generated < n_pos and attempts < max_attempts:
        attempts += 1
        cid = random.choice(cluster_ids)
        members = valid_clusters[cid]
        if len(members) < 2:
            continue
        a1, a2 = random.sample(members, 2)
        e1, e2 = embeddings[a1], embeddings[a2]
        feat = extract_features(e1, e2)
        X_list.append(feat)
        y_list.append(1)
        pos_generated += 1
    logger.info(f"Generated {pos_generated} positive pairs")

    # Negative pairs: pick from different clusters
    neg_generated = 0
    attempts = 0
    max_attempts = n_neg * 20
    while neg_generated < n_neg and attempts < max_attempts:
        attempts += 1
        cid1 = random.choice(cluster_ids)
        cid2 = random.choice(cluster_ids)
        if cid1 == cid2:
            continue
        a1 = random.choice(valid_clusters[cid1])
        a2 = random.choice(valid_clusters[cid2])
        # Ensure they're not the same article
        if a1 == a2:
            continue
        e1, e2 = embeddings[a1], embeddings[a2]
        feat = extract_features(e1, e2)
        X_list.append(feat)
        y_list.append(0)
        neg_generated += 1
    logger.info(f"Generated {neg_generated} negative pairs")

    X = np.array(X_list, dtype=np.float64)
    y = np.array(y_list, dtype=np.int32)
    logger.info(f"Final dataset: X shape {X.shape}, y shape {y.shape}, "
                f"positive ratio: {y.mean():.3f}")
    return X, y


def extract_features(e1, e2):
    """Extract 3 features from a pair of normalized embeddings."""
    cosine = float(np.dot(e1, e2))
    euclidean = float(np.sqrt(np.sum((e1 - e2) ** 2)))
    max_diff = float(np.max(np.abs(e1 - e2)))
    return [cosine, euclidean, max_diff]


def train_and_save(X, y):
    """Train LogisticRegression and MLPClassifier, save to all paths."""
    # Split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=RANDOM_SEED, stratify=y
    )
    logger.info(f"Train: {len(X_train)}, Test: {len(X_test)}")

    # ── LogisticRegression ──
    logger.info("Training LogisticRegression...")
    lr = LogisticRegression(
        C=10.0, max_iter=1000, random_state=RANDOM_SEED, solver="lbfgs"
    )
    lr.fit(X_train, y_train)

    lr_pred = lr.predict(X_test)
    lr_prob = lr.predict_proba(X_test)[:, 1]
    lr_auc = roc_auc_score(y_test, lr_prob)
    lr_f1 = f1_score(y_test, lr_pred)
    lr_acc = accuracy_score(y_test, lr_pred)
    logger.info(f"LR  - AUC={lr_auc:.4f}, F1={lr_f1:.4f}, Acc={lr_acc:.4f}")
    logger.info(f"LR coef_: {lr.coef_}, intercept: {lr.intercept_}")

    # ── MLPClassifier ──
    logger.info("Training MLPClassifier...")
    mlp = MLPClassifier(
        hidden_layer_sizes=(64, 32),
        activation="relu",
        solver="adam",
        max_iter=500,
        random_state=RANDOM_SEED,
        early_stopping=True,
        validation_fraction=0.1,
        n_iter_no_change=20,
    )
    mlp.fit(X_train, y_train)

    mlp_pred = mlp.predict(X_test)
    mlp_prob = mlp.predict_proba(X_test)[:, 1]
    mlp_auc = roc_auc_score(y_test, mlp_prob)
    mlp_f1 = f1_score(y_test, mlp_pred)
    mlp_acc = accuracy_score(y_test, mlp_pred)
    logger.info(f"MLP - AUC={mlp_auc:.4f}, F1={mlp_f1:.4f}, Acc={mlp_acc:.4f}")

    # ── Save models ──
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    # LogisticRegression paths
    lr_paths = [
        MODEL_DIR / "document_classifier.joblib",
        MODEL_DIR / "document_classifier_v2.joblib",
    ]
    for p in lr_paths:
        joblib.dump(lr, p)
        logger.info(f"Saved LR model to {p}")

    # MLPClassifier paths
    mlp_paths = [
        MODEL_DIR / "document_classifier_mlp.joblib",
        MODEL_DIR / "document_classifier_mlp_v2.joblib",
    ]
    for p in mlp_paths:
        joblib.dump(mlp, p)
        logger.info(f"Saved MLP model to {p}")

    return {
        "lr": {"auc": lr_auc, "f1": lr_f1, "acc": lr_acc},
        "mlp": {"auc": mlp_auc, "f1": mlp_f1, "acc": mlp_acc},
    }


def verify_model(path):
    """Verify a saved model loads and infers correctly."""
    m = joblib.load(path)
    e1 = np.random.randn(1024).astype(np.float32)
    e1 /= np.linalg.norm(e1)
    e2 = np.random.randn(1024).astype(np.float32)
    e2 /= np.linalg.norm(e2)
    feat = np.column_stack([
        np.sum(e1 * e2),
        np.sqrt(np.sum((e1 - e2) ** 2)),
        np.max(np.abs(e1 - e2)),
    ])
    prob = m.predict_proba(feat.reshape(1, -1))[0, 1]
    logger.info(f"Verify {path.name}: prob={prob:.6f}")
    return prob


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("Document Classifier Retraining")
    logger.info("=" * 60)

    # Step 1: Load clusters
    clusters, all_ids = load_clusters()

    # Step 2: Load embeddings
    embeddings = load_embeddings(all_ids)

    # Step 3: Generate balanced pairs
    X, y = generate_pairs(clusters, embeddings, N_PAIRS)

    # Step 4: Train and save
    metrics = train_and_save(X, y)

    # Step 5: Verify
    logger.info("─" * 40)
    logger.info("Verifying saved models...")
    for name in [
        "document_classifier.joblib",
        "document_classifier_v2.joblib",
        "document_classifier_mlp.joblib",
        "document_classifier_mlp_v2.joblib",
    ]:
        verify_model(MODEL_DIR / name)

    logger.info("=" * 60)
    logger.info("Retraining complete!")
    logger.info(f"LR  - AUC={metrics['lr']['auc']:.4f}, F1={metrics['lr']['f1']:.4f}, Acc={metrics['lr']['acc']:.4f}")
    logger.info(f"MLP - AUC={metrics['mlp']['auc']:.4f}, F1={metrics['mlp']['f1']:.4f}, Acc={metrics['mlp']['acc']:.4f}")
    logger.info("=" * 60)
