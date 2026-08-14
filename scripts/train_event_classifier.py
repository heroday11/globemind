#!/usr/bin/env python3
"""
Train event-level classifier with 9 rich features.
"""
import argparse
import json, os, re, sys, time, warnings, math
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import joblib

from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.neural_network import MLPClassifier
from sklearn.model_selection import train_test_split, cross_val_score, StratifiedKFold
from sklearn.metrics import (
    accuracy_score, f1_score, roc_auc_score, average_precision_score,
    precision_recall_curve, roc_curve, classification_report, confusion_matrix
)
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings('ignore')

_REPO = Path(__file__).resolve().parent.parent
DATA_DIR = _REPO / "data"
MODELS_DIR = DATA_DIR / "models"

SEED = 42
np.random.seed(SEED)

# ── Load helpers ──────────────────────────────────────────────────────────
sys.path.insert(0, str(_REPO))
from core_pipeline.entity_normalizer import normalize
from scripts.db_runtime_config import require_database_password


def parse_args():
    parser = argparse.ArgumentParser(description="Train event-level classifier.")
    parser.add_argument(
        "--dataset",
        default=str(DATA_DIR / "training_data_event_level.json"),
        help="Training dataset JSON path",
    )
    parser.add_argument(
        "--checkpoint",
        default=str(DATA_DIR / "checkpoint_v11_240k.jsonl"),
        help="Checkpoint JSONL used for entity lookup",
    )
    return parser.parse_args()


def load_training_data(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def load_article_embeddings(article_ids=None):
    """Load article embeddings, from cache files or DB."""
    # Try cache files first
    cache_keys = DATA_DIR / "article_embeddings_dict_keys.npy"
    cache_vals = DATA_DIR / "article_embeddings_dict_vals.npy"
    if cache_keys.exists() and cache_vals.exists():
        keys = np.load(str(cache_keys))
        vals = np.load(str(cache_vals))
        return dict(zip(keys, vals))
    # Fall back to DB
    if article_ids is not None:
        print("    Loading embeddings from DB...")
        import psycopg2
        conn = psycopg2.connect(
            host='192.168.207.171', port=54333, dbname='globemind_news',
            user='postgres', password=require_database_password(), connect_timeout=15,
        )
        conn.autocommit = True
        cur = conn.cursor()
        id_list = list(article_ids)
        embeddings = {}
        for start in range(0, len(id_list), 5000):
            batch = id_list[start : start + 5000]
            cur.execute(
                "SELECT news_id, embedding FROM news_embeddings WHERE news_id = ANY(%s)",
                (batch,),
            )
            for news_id, emb_raw in cur:
                if isinstance(emb_raw, memoryview):
                    emb_raw = bytes(emb_raw)
                if isinstance(emb_raw, bytes):
                    emb_raw = emb_raw.decode()
                if isinstance(emb_raw, str):
                    emb_raw = json.loads(emb_raw)
                embeddings[int(news_id)] = np.array(emb_raw, dtype=np.float32)
        cur.close()
        conn.close()
        return embeddings
    raise FileNotFoundError("No embedding cache found and no article_ids provided")


def load_source_map(article_ids):
    """Load media_source_name for each article, from cache or DB."""
    cache_path = DATA_DIR / "tmp_source_map.json"
    if cache_path.exists():
        with open(str(cache_path), encoding='utf-8') as f:
            raw = json.load(f)
        return {int(k): v for k, v in raw.items()}
    print("    Loading source info from DB...")
    import psycopg2
    conn = psycopg2.connect(
        host='192.168.207.171', port=54333, dbname='globemind_news',
        user='postgres', password=require_database_password(), connect_timeout=15,
    )
    conn.autocommit = True
    cur = conn.cursor()
    result = {}
    id_list = list(article_ids)
    for start in range(0, len(id_list), 5000):
        batch = id_list[start : start + 5000]
        cur.execute(
            "SELECT id, media_source_name, media_source_domain, source_dataset_name FROM news WHERE id = ANY(%s)",
            (batch,),
        )
        for row in cur:
            result[row[0]] = {
                'media_source_name': row[1],
                'media_source_domain': row[2],
                'source_dataset_name': row[3],
            }
    cur.close()
    conn.close()
    return result


def load_article_texts(article_ids):
    """Load article title and abstract, from cache or DB."""
    cache_path = DATA_DIR / "tmp_article_texts.json"
    if cache_path.exists():
        with open(str(cache_path), encoding='utf-8') as f:
            raw = json.load(f)
        return {int(k): v for k, v in raw.items()}
    print("    Loading article texts from DB...")
    import psycopg2
    conn = psycopg2.connect(
        host='192.168.207.171', port=54333, dbname='globemind_news',
        user='postgres', password=require_database_password(), connect_timeout=15,
    )
    conn.autocommit = True
    cur = conn.cursor()
    result = {}
    id_list = list(article_ids)
    for start in range(0, len(id_list), 5000):
        batch = id_list[start : start + 5000]
        cur.execute(
            "SELECT id, title, abstract FROM news WHERE id = ANY(%s)",
            (batch,),
        )
        for row in cur:
            result[row[0]] = {
                'title': row[1] or '',
                'abstract': row[2] or '',
            }
    cur.close()
    conn.close()
    return result


def get_article_entities_from_checkpoint(article_ids, checkpoint_path):
    """Get initiator and target for each article from checkpoint data."""
    result = {}
    with open(checkpoint_path, encoding='utf-8') as f:
        for line in f:
            rec = json.loads(line.strip())
            aid = rec['article_id']
            if aid in article_ids:
                ev = rec.get('event', {})
                if isinstance(ev, dict):
                    result[aid] = {
                        'initiator': normalize(ev.get('initiator', '') or ''),
                        'target': normalize(ev.get('target', '') or ''),
                    }
    return result


def compute_entity_jaccard(entities_1, entities_2):
    """Jaccard similarity between two entity sets."""
    def _entity_set(entities):
        values = set()
        for key in ("initiator", "target"):
            raw = entities.get(key, "") or ""
            raw_parts = raw if isinstance(raw, list) else re.split(r"[,，;/]| and ", str(raw))
            for part in raw_parts:
                for normalized in normalize(str(part or "")):
                    cleaned = normalized.strip().lower()
                    if cleaned and cleaned not in {"null", "none", "unknown"}:
                        values.add(cleaned)
        return values

    set1 = _entity_set(entities_1)
    set2 = _entity_set(entities_2)
    if not set1 and not set2:
        return 0.0
    intersection = set1 & set2
    union = set1 | set2
    return len(intersection) / len(union)


def tokenize(text):
    """Simple tokenization: lowercase alphabetic tokens."""
    return re.findall(r"[a-z0-9']+", text.lower())


def compute_trigger_overlap(title1, title2):
    """Number of shared content words between two article titles."""
    tokens1 = set(tokenize(title1))
    tokens2 = set(tokenize(title2))
    # Filter stop words
    stop_words = {
        'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
        'of', 'with', 'by', 'from', 'as', 'is', 'are', 'was', 'were', 'be',
        'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will',
        'would', 'could', 'should', 'may', 'might', 'shall', 'can', 'its',
        'it', 'this', 'that', 'these', 'those', 'we', 'you', 'they', 'he',
        'she', 'it', 'not', 'no', 'nor', 'so', 'up', 'down', 'out', 'off',
        'over', 'under', 'again', 'further', 'then', 'once', 'here', 'there',
        'all', 'each', 'every', 'both', 'few', 'more', 'most', 'other', 'some',
        'such', 'only', 'own', 'same', 'than', 'too', 'very', 'just', 'also',
        'about', 'above', 'after', 'how', 'what', 'when', 'where', 'which',
        'who', 'whom', 'why',
    }
    tokens1 = {t for t in tokens1 if len(t) > 2 and t not in stop_words}
    tokens2 = {t for t in tokens2 if len(t) > 2 and t not in stop_words}
    return len(tokens1 & tokens2)


def compute_features(pair, embeddings, article_entities, source_map, article_texts):
    """
    Compute all 9 features for a single pair.
    Returns dict of feature_name -> value, or None if missing data.
    """
    aid1 = pair['article_id_1']
    aid2 = pair['article_id_2']

    # Basic (from data or embeddings)
    emb1 = embeddings.get(aid1)
    emb2 = embeddings.get(aid2)
    if emb1 is None or emb2 is None:
        return None

    # 1. cosine_sim (already in data)
    cosine_sim = pair.get('cosine_similarity', None)
    if cosine_sim is None:
        cosine_sim = float(np.dot(emb1, emb2) / (np.linalg.norm(emb1) * np.linalg.norm(emb2)))

    # 2. euclidean_dist
    euclidean_dist = float(np.sqrt(np.sum((emb1 - emb2) ** 2)))

    # 3. max_component_diff
    max_component_diff = float(np.max(np.abs(emb1 - emb2)))

    # 4. time_delta_days (already in data, normalize by dividing by max expected)
    time_delta = pair.get('time_delta_days', 0)

    # 5. entity_jaccard
    entities_1 = article_entities.get(aid1)
    entities_2 = article_entities.get(aid2)
    if entities_1 is None or entities_2 is None:
        return None
    entity_jaccard = compute_entity_jaccard(entities_1, entities_2)

    # 6. source_same
    src1 = source_map.get(aid1, {})
    src2 = source_map.get(aid2, {})
    source_same = 1.0 if src1.get('media_source_name') == src2.get('media_source_name') else 0.0

    # 7. event_type_exact
    et1 = pair.get('event_type_1', '')
    et2 = pair.get('event_type_2', '')
    event_type_exact = 1.0 if et1 == et2 else 0.0

    # 8. length_ratio (using title+abstract length in characters)
    txt1 = article_texts.get(aid1, {})
    txt2 = article_texts.get(aid2, {})
    len1 = len(txt1.get('title', '') + ' ' + txt1.get('abstract', ''))
    len2 = len(txt2.get('title', '') + ' ' + txt2.get('abstract', ''))
    if max(len1, len2) == 0:
        length_ratio = 1.0
    else:
        length_ratio = min(len1, len2) / max(len1, len2)

    # 9. trigger_overlap (using title content words)
    title1 = txt1.get('title', '')
    title2 = txt2.get('title', '')
    trigger_overlap = compute_trigger_overlap(title1, title2)

    # Normalize time_delta_days (log transform + min-max, clip at 200)
    time_delta_norm = np.log1p(min(time_delta, 200)) / np.log1p(200)

    return {
        'cosine_sim': cosine_sim,
        'euclidean_dist': euclidean_dist,
        'max_component_diff': max_component_diff,
        'time_delta_days': time_delta_norm,
        'entity_jaccard': entity_jaccard,
        'source_same': source_same,
        'event_type_exact': event_type_exact,
        'length_ratio': length_ratio,
        'trigger_overlap': trigger_overlap,
        # Note: trigger_overlap is integer count, we'll normalize later
    }


def print_metrics(y_true, y_pred, y_prob, phase='Test'):
    """Print evaluation metrics."""
    acc = accuracy_score(y_true, y_pred)
    f1 = f1_score(y_true, y_pred)
    auc = roc_auc_score(y_true, y_prob)
    ap = average_precision_score(y_true, y_prob)
    cm = confusion_matrix(y_true, y_pred)
    print(f"\n  [{phase}] Metrics:")
    print(f"    Accuracy:  {acc:.4f}")
    print(f"    F1 Score:  {f1:.4f}")
    print(f"    AUC-ROC:   {auc:.4f}")
    print(f"    Avg Prec:  {ap:.4f}")
    print(f"    Confusion Matrix:")
    print(f"      TN={cm[0,0]}  FP={cm[0,1]}")
    print(f"      FN={cm[1,0]}  TP={cm[1,1]}")
    return {'accuracy': acc, 'f1': f1, 'auc': auc, 'average_precision': ap}


def main():
    args = parse_args()
    t0 = time.time()
    print("=" * 70)
    print("Event-Level Classifier Training (9 Features)")
    print("=" * 70)

    # ── 1. Load training data ──
    print(f"\n[1] Loading training data...")
    training_data = load_training_data(args.dataset)
    print(f"    {len(training_data)} pairs loaded")
    label_dist = Counter(d['label'] for d in training_data)
    print(f"    Labels: {dict(label_dist)}")

    # ── 2. Collect unique article IDs ──
    print(f"\n[2] Collecting unique article IDs...")
    train_ids = set()
    for d in training_data:
        train_ids.add(d['article_id_1'])
        train_ids.add(d['article_id_2'])
    print(f"    {len(train_ids)} unique articles")

    # ── 3. Load auxiliary data ──
    print(f"\n[3] Loading auxiliary data...")

    embeddings = load_article_embeddings(train_ids)
    print(f"    {len(embeddings)} article embeddings loaded")

    source_map = load_source_map(train_ids)
    print(f"    {len(source_map)} article source entries loaded")

    article_texts = load_article_texts(train_ids)
    print(f"    {len(article_texts)} article texts loaded")

    # Extract article entities from checkpoint
    article_entities = get_article_entities_from_checkpoint(train_ids, args.checkpoint)
    print(f"    {len(article_entities)} article entity sets loaded")

    # ── 4. Compute features for each pair ──
    print(f"\n[4] Computing all 9 features for each pair...")
    feature_names = [
        'cosine_sim', 'euclidean_dist', 'max_component_diff', 'time_delta_days',
        'entity_jaccard', 'source_same', 'event_type_exact', 'length_ratio',
        'trigger_overlap',
    ]

    X_list = []
    y_list = []
    skipped = 0
    pair_skip_reasons = Counter()

    for pair in training_data:
        features = compute_features(pair, embeddings, article_entities, source_map, article_texts)
        if features is None:
            skipped += 1
            if pair.get('article_id_1') not in embeddings:
                pair_skip_reasons['missing_emb1'] += 1
            elif pair.get('article_id_2') not in embeddings:
                pair_skip_reasons['missing_emb2'] += 1
            else:
                pair_skip_reasons['missing_entity'] += 1
            continue

        # build feature vector (order matters!)
        feat_vec = [features[name] for name in feature_names]
        X_list.append(feat_vec)
        y_list.append(int(pair['label']))

    X = np.array(X_list, dtype=np.float64)
    y = np.array(y_list, dtype=np.int64)

    print(f"    Feature matrix: {X.shape}")
    print(f"    Positive pairs: {int(y.sum())} / {len(y)} ({y.mean()*100:.1f}%)")
    print(f"    Skipped (missing data): {skipped}")
    if skipped > 0:
        print(f"    Skip reasons: {dict(pair_skip_reasons)}")

    y_mean = float(y.mean())

    # Feature stats
    print(f"\n  Feature statistics:")
    for i, name in enumerate(feature_names):
        col = X[:, i]
        print(f"    {name:20s}: mean={col.mean():.4f}, std={col.std():.4f}, "
              f"min={col.min():.4f}, max={col.max():.4f}")

    # ── 5. 80/20 split ──
    print(f"\n[5] Train/test split (80/20)...")
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=SEED, stratify=y
    )
    print(f"    Train: {X_train.shape}, Test: {X_test.shape}")
    print(f"    Train label ratio: {y_train.mean():.3f}, Test label ratio: {y_test.mean():.3f}")

    # Scale features
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    # ── 6. Train models ──
    print(f"\n[6] Training models...")
    models = {}
    results = {}

    # 5a. LogisticRegression (L1 for feature selection)
    print(f"\n  --- LogisticRegression (L1) ---")
    lr = LogisticRegression(
        penalty='l1', solver='saga', C=1.0, max_iter=5000,
        random_state=SEED, n_jobs=-1,
    )
    lr.fit(X_train_scaled, y_train)
    y_prob_lr = lr.predict_proba(X_test_scaled)[:, 1]
    y_pred_lr = lr.predict(X_test_scaled)
    results['logreg'] = print_metrics(y_test, y_pred_lr, y_prob_lr, 'LogReg')
    models['logreg'] = lr

    # 5b. RandomForestClassifier
    print(f"\n  --- RandomForestClassifier ---")
    rf = RandomForestClassifier(
        n_estimators=300, max_depth=12, min_samples_leaf=5,
        n_jobs=-1, random_state=SEED, class_weight='balanced',
    )
    rf.fit(X_train, y_train)  # RF doesn't need scaling
    y_prob_rf = rf.predict_proba(X_test)[:, 1]
    y_pred_rf = rf.predict(X_test)
    results['rf'] = print_metrics(y_test, y_pred_rf, y_prob_rf, 'RF')
    models['rf'] = rf

    # 5c. MLPClassifier (2 hidden layers)
    print(f"\n  --- MLPClassifier ---")
    mlp = MLPClassifier(
        hidden_layer_sizes=(64, 32), activation='relu', solver='adam',
        alpha=0.001, batch_size=256, max_iter=1000, early_stopping=True,
        validation_fraction=0.1, random_state=SEED, verbose=False,
    )
    mlp.fit(X_train_scaled, y_train)
    y_prob_mlp = mlp.predict_proba(X_test_scaled)[:, 1]
    y_pred_mlp = mlp.predict(X_test_scaled)
    results['mlp'] = print_metrics(y_test, y_pred_mlp, y_prob_mlp, 'MLP')
    models['mlp'] = mlp

    # ── 7. Cross-validation ──
    print(f"\n[7] 5-Fold Cross-Validation (AUC)...")
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEED)
    X_scaled = scaler.transform(X)

    cv_results = {}
    for name, model in [
        ('logreg', LogisticRegression(penalty='l1', solver='saga', C=1.0, max_iter=5000, random_state=SEED, n_jobs=-1)),
        ('rf', RandomForestClassifier(n_estimators=300, max_depth=12, min_samples_leaf=5, n_jobs=-1, random_state=SEED, class_weight='balanced')),
        ('mlp', MLPClassifier(hidden_layer_sizes=(64, 32), activation='relu', solver='adam', alpha=0.001, batch_size=256, max_iter=1000, random_state=SEED, verbose=False)),
    ]:
        if name == 'rf':
            scores = cross_val_score(model, X, y, cv=cv, scoring='roc_auc', n_jobs=-1)
        else:
            scores = cross_val_score(model, X_scaled, y, cv=cv, scoring='roc_auc', n_jobs=-1)
        cv_results[name] = {
            'auc_mean': float(scores.mean()),
            'auc_std': float(scores.std()),
            'auc_scores': [float(s) for s in scores],
        }
        print(f"    {name:8s}: AUC = {scores.mean():.4f} +/- {scores.std():.4f}  "
              f"(scores: {[f'{s:.4f}' for s in scores]})")

    # ── 8. Feature importance analysis ──
    print(f"\n[8] Feature importance analysis...")
    print(f"\n  --- RandomForest Feature Importance ---")
    rf_importances = rf.feature_importances_
    rf_ranked = sorted(zip(feature_names, rf_importances), key=lambda x: -x[1])
    for rank, (name, imp) in enumerate(rf_ranked, 1):
        print(f"    {rank:2d}. {name:20s}: {imp:.4f}")

    print(f"\n  --- LogisticRegression Coefficients (L1) ---")
    lr_coefs = lr.coef_[0]
    lr_ranked = sorted(zip(feature_names, lr_coefs), key=lambda x: -abs(x[1]))
    for rank, (name, coef) in enumerate(lr_ranked, 1):
        nonzero = "✓" if abs(coef) > 1e-6 else " "
        print(f"    {rank:2d}. {name:20s}: {coef:+.6f}  [{nonzero}]")

    # Compute permutation importance for MLP
    print(f"\n  --- MLP Permutation Importance ---")
    n_repeats = 10
    rng = np.random.RandomState(SEED)
    baseline_auc = roc_auc_score(y_test, mlp.predict_proba(X_test_scaled)[:, 1])
    perm_importances = np.zeros((len(feature_names), n_repeats))
    for i in range(len(feature_names)):
        for r in range(n_repeats):
            X_perm = X_test_scaled.copy()
            X_perm[:, i] = rng.permutation(X_perm[:, i])
            perm_auc = roc_auc_score(y_test, mlp.predict_proba(X_perm)[:, 1])
            perm_importances[i, r] = baseline_auc - perm_auc
    mlp_importances = perm_importances.mean(axis=1)
    mlp_ranked = sorted(zip(feature_names, mlp_importances), key=lambda x: -x[1])
    for rank, (name, imp) in enumerate(mlp_ranked, 1):
        print(f"    {rank:2d}. {name:20s}: {imp:.4f} (drop in AUC)")

    # ── 9. Save models ──
    print(f"\n[9] Saving models...")
    os.makedirs(str(MODELS_DIR), exist_ok=True)

    joblib.dump(lr, str(MODELS_DIR / "event_classifier_logreg.joblib"))
    joblib.dump(rf, str(MODELS_DIR / "event_classifier_rf.joblib"))
    joblib.dump(mlp, str(MODELS_DIR / "event_classifier_mlp.joblib"))
    joblib.dump(scaler, str(MODELS_DIR / "event_classifier_scaler.joblib"))
    print(f"    Models saved to {MODELS_DIR}")

    # ── 10. Save feature analysis ──
    print(f"\n[10] Saving feature analysis...")

    feature_analysis = {
        'feature_names': feature_names,
        'n_samples': int(len(y)),
        'positive_ratio': round(y_mean, 6),
        'n_features': len(feature_names),
        'label_distribution': {'positive': int(y.sum()), 'negative': int(len(y) - y.sum())},
        'feature_statistics': {
            name: {
                'mean': round(float(X[:, i].mean()), 6),
                'std': round(float(X[:, i].std()), 6),
                'min': round(float(X[:, i].min()), 6),
                'max': round(float(X[:, i].max()), 6),
            }
            for i, name in enumerate(feature_names)
        },
        'logreg': {
            **results['logreg'],
            'coefficients': {name: float(lr.coef_[0][i]) for i, name in enumerate(feature_names)},
            'feature_ranking': [
                {'rank': r, 'feature': name, 'coefficient': float(coef)}
                for r, (name, coef) in enumerate(lr_ranked, 1)
            ],
        },
        'random_forest': {
            **results['rf'],
            'feature_importances': {name: float(rf_importances[i]) for i, name in enumerate(feature_names)},
            'feature_ranking': [
                {'rank': r, 'feature': name, 'importance': float(imp)}
                for r, (name, imp) in enumerate(rf_ranked, 1)
            ],
        },
        'mlp': {
            **results['mlp'],
            'permutation_importances': {name: float(mlp_importances[i]) for i, name in enumerate(feature_names)},
            'feature_ranking': [
                {'rank': r, 'feature': name, 'permutation_importance': float(imp)}
                for r, (name, imp) in enumerate(mlp_ranked, 1)
            ],
        },
        'cross_validation': cv_results,
        'model_comparison': {
            name: {
                'accuracy': round(results[name]['accuracy'], 6),
                'f1': round(results[name]['f1'], 6),
                'auc': round(results[name]['auc'], 6),
                'average_precision': round(results[name]['average_precision'], 6),
                'cv_auc_mean': round(cv_results[name]['auc_mean'], 6),
                'cv_auc_std': round(cv_results[name]['auc_std'], 6),
            }
            for name in ['logreg', 'rf', 'mlp']
        },
        'test_set_size': len(y_test),
        'train_set_size': len(y_train),
    }

    with open(str(MODELS_DIR / "event_classifier_features.json"), "w", encoding="utf-8") as f:
        json.dump(feature_analysis, f, indent=2, ensure_ascii=False)
    print(f"    Feature analysis saved to {MODELS_DIR / 'event_classifier_features.json'}")

    # ── 11. Summary ──
    elapsed = time.time() - t0
    print(f"\n{'=' * 70}")
    print(f"Training Complete in {elapsed/60:.2f} minutes")
    print(f"{'=' * 70}")
    print(f"\nModel Comparison:")
    print(f"  {'Model':12s} {'Acc':>8s} {'F1':>8s} {'AUC':>8s} {'AvgPrec':>8s} {'CV-AUC':>8s}")
    print(f"  {'-'*56}")
    for name in ['logreg', 'rf', 'mlp']:
        r = feature_analysis['model_comparison'][name]
        print(f"  {name:12s} {r['accuracy']:8.4f} {r['f1']:8.4f} {r['auc']:8.4f} "
              f"{r['average_precision']:8.4f} {r['cv_auc_mean']:8.4f}")

    print(f"\nTop-3 Features by RandomForest:")
    for name, imp in rf_ranked[:3]:
        print(f"  {name}: {imp:.4f}")

    print(f"\nTop-3 Features by LogReg (|coef|):")
    for name, coef in lr_ranked[:3]:
        print(f"  {name}: {coef:+.6f} (nonzero={abs(coef)>1e-6})")

    print(f"\nFiles saved:")
    print(f"  {MODELS_DIR / 'event_classifier_logreg.joblib'}")
    print(f"  {MODELS_DIR / 'event_classifier_rf.joblib'}")
    print(f"  {MODELS_DIR / 'event_classifier_mlp.joblib'}")
    print(f"  {MODELS_DIR / 'event_classifier_scaler.joblib'}")
    print(f"  {MODELS_DIR / 'event_classifier_features.json'}")


if __name__ == "__main__":
    main()
