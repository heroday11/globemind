#!/usr/bin/env python3
"""
Train a v2 geopolitical pre-filter without overwriting the production model.

The current production gate uses:
  data/models/domain_tfidf_lr.joblib
  data/models/domain_classifier_lr.joblib

This script trains richer TF-IDF + LogisticRegression variants and writes:
  data/models/domain_tfidf_lr_v2.joblib
  data/models/domain_classifier_lr_v2.joblib
  data/models/domain_classifier_lr_v2_report.json

The report compares v2 variants against the current production model on the
same holdout split, including precision/recall/F1 at a target recall.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import joblib
import numpy as np
import psycopg2
from sklearn.exceptions import ConvergenceWarning
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import FeatureUnion

from db_runtime_config import require_database_password

import warnings

warnings.filterwarnings("ignore", category=ConvergenceWarning)

REPO = Path(__file__).resolve().parent.parent
DATA_DIR = REPO / "data"
MODEL_DIR = DATA_DIR / "models"

DEFAULT_CHECKPOINT = DATA_DIR / "checkpoint_v13_all.jsonl"
DEFAULT_OLD_MODEL = MODEL_DIR / "domain_classifier_lr.joblib"
DEFAULT_OLD_VECTORIZER = MODEL_DIR / "domain_tfidf_lr.joblib"
DEFAULT_OUT_MODEL = MODEL_DIR / "domain_classifier_lr_v2.joblib"
DEFAULT_OUT_VECTORIZER = MODEL_DIR / "domain_tfidf_lr_v2.joblib"
DEFAULT_REPORT = MODEL_DIR / "domain_classifier_lr_v2_report.json"

LOG = logging.getLogger("train_domain_classifier_v2")


@dataclass
class MetricRow:
    threshold: float
    precision: float
    recall: float
    f1: float
    accuracy: float
    tp: int
    fp: int
    tn: int
    fn: int
    predicted_positive: int
    candidate_rate: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train and evaluate a v2 geopolitical domain pre-filter."
    )
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--out-model", type=Path, default=DEFAULT_OUT_MODEL)
    parser.add_argument("--out-vectorizer", type=Path, default=DEFAULT_OUT_VECTORIZER)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--old-model", type=Path, default=DEFAULT_OLD_MODEL)
    parser.add_argument("--old-vectorizer", type=Path, default=DEFAULT_OLD_VECTORIZER)
    parser.add_argument(
        "--save-even-if-worse",
        action="store_true",
        help="Save v2 artifacts even if they underperform the old model at target recall.",
    )
    parser.add_argument("--target-recall", type=float, default=0.98)
    parser.add_argument("--test-size", type=float, default=0.2)
    parser.add_argument("--sample-limit", type=int, default=0, help="0 means all rows.")
    parser.add_argument("--body-chars", type=int, default=1200)
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument(
        "--feature-mode",
        choices=["old_word", "word", "word_char"],
        default="word_char",
        help="old_word matches production; word is faster/wider; word_char is slower.",
    )
    parser.add_argument(
        "--text-mode",
        choices=["enhanced", "old"],
        default="enhanced",
        help="enhanced uses metadata + longer body; old uses title + first 500 body chars.",
    )
    parser.add_argument(
        "--variants",
        default="plain,balanced,hardneg",
        help="Comma-separated subset of plain,balanced,hardneg,hardneg_balanced.",
    )
    parser.add_argument("--db-host", default=os.getenv("PG_HOST", "192.168.207.171"))
    parser.add_argument("--db-port", type=int, default=int(os.getenv("PG_PORT", "54333")))
    parser.add_argument("--db-name", default=os.getenv("PG_DBNAME", "globemind_news"))
    parser.add_argument("--db-user", default=os.getenv("PG_WRITE_USER", "postgres"))
    parser.add_argument("--connect-timeout", type=int, default=15)
    return parser.parse_args()


def load_labels(path: Path) -> tuple[list[int], np.ndarray, dict[str, int]]:
    ids: list[int] = []
    labels: list[int] = []
    domains: dict[str, int] = {}

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            item = json.loads(line)
            event = item.get("event") or {}
            domain = event.get("domain")
            if not domain:
                continue
            domains[domain] = domains.get(domain, 0) + 1
            ids.append(int(item["article_id"]))
            labels.append(1 if domain == "geopolitical" else 0)

    return ids, np.asarray(labels, dtype=np.int8), domains


def stratified_sample(
    ids: list[int], labels: np.ndarray, limit: int, seed: int
) -> tuple[list[int], np.ndarray]:
    if not limit or limit >= len(ids):
        return ids, labels

    rng = np.random.default_rng(seed)
    labels_arr = np.asarray(labels)
    selected: list[int] = []

    for label in (0, 1):
        idx = np.flatnonzero(labels_arr == label)
        n = max(1, round(limit * len(idx) / len(labels_arr)))
        n = min(n, len(idx))
        selected.extend(rng.choice(idx, size=n, replace=False).tolist())

    selected = sorted(selected)
    return [ids[i] for i in selected], labels_arr[selected]


def connect(args: argparse.Namespace):
    return psycopg2.connect(
        host=args.db_host,
        port=args.db_port,
        dbname=args.db_name,
        user=args.db_user,
        password=require_database_password(),
        connect_timeout=args.connect_timeout,
    )


def fetch_texts(
    args: argparse.Namespace, ids: list[int]
) -> tuple[list[str], list[str], dict[str, int]]:
    """Return enhanced texts and old production texts in the input id order."""
    enhanced_by_id: dict[int, str] = {}
    old_by_id: dict[int, str] = {}
    stats = {"requested": len(ids), "found": 0, "missing": 0, "fallback_queries": 0}

    primary_sql = """
        SELECT
            id,
            COALESCE(title, ''),
            LEFT(COALESCE(body, ''), %s),
            COALESCE(abstract, ''),
            COALESCE(url, ''),
            COALESCE(source_dataset_name, ''),
            COALESCE(media_source_name, ''),
            COALESCE(media_source_domain, ''),
            COALESCE(language, ''),
            COALESCE(country, ''),
            COALESCE(region, ''),
            COALESCE(topic_name, ''),
            COALESCE(topic_region, ''),
            COALESCE(categories::text, ''),
            COALESCE(tags::text, '')
        FROM news
        WHERE id = ANY(%s)
    """
    fallback_sql = """
        SELECT
            id,
            COALESCE(title, ''),
            LEFT(COALESCE(body, ''), %s),
            '',
            COALESCE(url, ''),
            '',
            '',
            '',
            COALESCE(language, ''),
            '',
            COALESCE(region, ''),
            '',
            '',
            '',
            ''
        FROM news
        WHERE id = ANY(%s)
    """

    conn = connect(args)
    try:
        cur = conn.cursor()
        use_fallback = False
        for start in range(0, len(ids), args.batch_size):
            batch = ids[start : start + args.batch_size]
            sql = fallback_sql if use_fallback else primary_sql
            try:
                cur.execute(sql, (args.body_chars, batch))
            except Exception:
                conn.rollback()
                if use_fallback:
                    raise
                use_fallback = True
                stats["fallback_queries"] += 1
                cur.execute(fallback_sql, (args.body_chars, batch))

            rows = cur.fetchall()
            stats["found"] += len(rows)
            for row in rows:
                (
                    article_id,
                    title,
                    body,
                    abstract,
                    url,
                    source_dataset_name,
                    media_source_name,
                    media_source_domain,
                    language,
                    country,
                    region,
                    topic_name,
                    topic_region,
                    categories,
                    tags,
                ) = row
                old_text = f"{title} {body[:500]}"
                parts = [
                    f"title: {title}",
                    f"source_dataset: {source_dataset_name}",
                    f"source_name: {media_source_name}",
                    f"source_domain: {media_source_domain}",
                    f"language: {language}",
                    f"country: {country}",
                    f"region: {region}",
                    f"topic: {topic_name} {topic_region}",
                    f"categories: {categories}",
                    f"tags: {tags}",
                    f"url: {url}",
                    f"abstract: {abstract}",
                    f"body: {body}",
                ]
                enhanced_by_id[int(article_id)] = "\n".join(p for p in parts if p.strip())
                old_by_id[int(article_id)] = old_text
    finally:
        conn.close()

    enhanced: list[str] = []
    old: list[str] = []
    missing = 0
    for article_id in ids:
        text = enhanced_by_id.get(article_id)
        if text is None:
            missing += 1
            text = ""
        enhanced.append(text)
        old.append(old_by_id.get(article_id, ""))
    stats["missing"] = missing
    return enhanced, old, stats


def build_word_vectorizer(max_features: int = 120000) -> TfidfVectorizer:
    return TfidfVectorizer(
        analyzer="word",
        ngram_range=(1, 2),
        max_features=max_features,
        min_df=2,
        max_df=0.95,
        strip_accents="unicode",
        sublinear_tf=True,
        token_pattern=r"(?u)\b\w[\w-]+\b",
    )


def build_old_word_vectorizer() -> TfidfVectorizer:
    return TfidfVectorizer(
        analyzer="word",
        ngram_range=(1, 3),
        max_features=30000,
        min_df=2,
        max_df=0.8,
        sublinear_tf=True,
    )


def build_vectorizer(args: argparse.Namespace):
    if args.feature_mode == "old_word":
        return build_old_word_vectorizer()

    if args.feature_mode == "word":
        return build_word_vectorizer()

    return FeatureUnion(
        [
            ("word", build_word_vectorizer(max_features=90000)),
            (
                "char_wb",
                TfidfVectorizer(
                    analyzer="char_wb",
                    ngram_range=(3, 5),
                    max_features=90000,
                    min_df=2,
                    max_df=0.98,
                    sublinear_tf=True,
                ),
            ),
        ],
        n_jobs=args.jobs,
    )


def metric_at_threshold(y_true: np.ndarray, scores: np.ndarray, threshold: float) -> MetricRow:
    pred = (scores >= threshold).astype(np.int8)
    tn, fp, fn, tp = confusion_matrix(y_true, pred, labels=[0, 1]).ravel()
    return MetricRow(
        threshold=float(threshold),
        precision=float(precision_score(y_true, pred, zero_division=0)),
        recall=float(recall_score(y_true, pred, zero_division=0)),
        f1=float(f1_score(y_true, pred, zero_division=0)),
        accuracy=float(accuracy_score(y_true, pred)),
        tp=int(tp),
        fp=int(fp),
        tn=int(tn),
        fn=int(fn),
        predicted_positive=int(pred.sum()),
        candidate_rate=float(pred.mean()),
    )


def select_threshold(
    y_true: np.ndarray, scores: np.ndarray, target_recall: float
) -> MetricRow:
    precision, recall, thresholds = precision_recall_curve(y_true, scores)
    valid = np.flatnonzero(recall[:-1] + 1e-12 >= target_recall)
    if len(valid) == 0:
        # Fall back to the lowest observed threshold if the target recall is not reachable.
        return metric_at_threshold(y_true, scores, float(np.min(scores)))

    f1_values = (2.0 * precision[:-1] * recall[:-1]) / np.maximum(
        precision[:-1] + recall[:-1], 1e-12
    )
    best_idx = max(
        valid.tolist(),
        key=lambda i: (float(precision[i]), float(f1_values[i]), float(thresholds[i])),
    )
    return metric_at_threshold(y_true, scores, float(thresholds[best_idx]))


def evaluate_scores(
    y_true: np.ndarray, scores: np.ndarray, target_recall: float
) -> dict[str, object]:
    fixed_thresholds = [0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.65]
    by_fixed = {
        f"{threshold:.2f}": asdict(metric_at_threshold(y_true, scores, threshold))
        for threshold in fixed_thresholds
    }
    by_recall_target = {
        f"{target:.2f}": asdict(select_threshold(y_true, scores, target))
        for target in [0.95, 0.97, target_recall, 0.99]
    }
    return {
        "selected_for_target_recall": asdict(select_threshold(y_true, scores, target_recall)),
        "fixed_thresholds": by_fixed,
        "recall_targets": by_recall_target,
        "score_quantiles": {
            str(q): float(np.quantile(scores, q))
            for q in [0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99]
        },
    }


def old_model_scores(
    args: argparse.Namespace, train_old_texts: list[str], test_old_texts: list[str]
) -> tuple[np.ndarray | None, np.ndarray | None, dict[str, str]]:
    if not args.old_model.exists() or not args.old_vectorizer.exists():
        return None, None, {"status": "missing old model/vectorizer"}

    t0 = time.time()
    old_model = joblib.load(args.old_model)
    old_vectorizer = joblib.load(args.old_vectorizer)
    train_scores = old_model.predict_proba(old_vectorizer.transform(train_old_texts))[:, 1]
    test_scores = old_model.predict_proba(old_vectorizer.transform(test_old_texts))[:, 1]
    return train_scores, test_scores, {
        "status": "ok",
        "elapsed_sec": f"{time.time() - t0:.3f}",
        "model": str(args.old_model),
        "vectorizer": str(args.old_vectorizer),
    }


def build_hard_weights(y_train: np.ndarray, old_train_scores: np.ndarray | None) -> np.ndarray:
    weights = np.ones(len(y_train), dtype=np.float64)
    if old_train_scores is None:
        return weights

    neg = y_train == 0
    pos = y_train == 1

    # Penalize the exact errors that are expensive downstream: old high-score negatives.
    weights[neg & (old_train_scores >= 0.65)] = 5.0
    weights[neg & (old_train_scores >= 0.30) & (old_train_scores < 0.65)] = 3.0

    # Keep recall pressure by up-weighting positives the old gate almost missed.
    weights[pos & (old_train_scores < 0.30)] = 4.0
    weights[pos & (old_train_scores >= 0.30) & (old_train_scores < 0.65)] = 2.0
    return weights


def variant_specs(names: Iterable[str]) -> list[dict[str, object]]:
    specs: list[dict[str, object]] = []
    for name in names:
        name = name.strip()
        if not name:
            continue
        if name == "plain":
            specs.append(
                {
                    "name": name,
                    "class_weight": None,
                    "use_hard_weights": False,
                    "C": 2.0,
                    "solver": "saga",
                    "max_iter": 500,
                }
            )
        elif name == "balanced":
            specs.append(
                {
                    "name": name,
                    "class_weight": "balanced",
                    "use_hard_weights": False,
                    "C": 1.0,
                    "solver": "saga",
                    "max_iter": 500,
                }
            )
        elif name == "old_lr":
            specs.append(
                {
                    "name": name,
                    "class_weight": "balanced",
                    "use_hard_weights": False,
                    "C": 1.0,
                    "solver": "lbfgs",
                    "max_iter": 1000,
                }
            )
        elif name == "hardneg":
            specs.append(
                {
                    "name": name,
                    "class_weight": None,
                    "use_hard_weights": True,
                    "C": 2.0,
                    "solver": "saga",
                    "max_iter": 500,
                }
            )
        elif name == "hardneg_balanced":
            specs.append(
                {
                    "name": name,
                    "class_weight": "balanced",
                    "use_hard_weights": True,
                    "C": 1.0,
                    "solver": "saga",
                    "max_iter": 500,
                }
            )
        else:
            raise ValueError(f"Unknown variant: {name}")
    if not specs:
        raise ValueError("No variants selected.")
    return specs


def fit_variant(
    spec: dict[str, object],
    args: argparse.Namespace,
    x_train,
    y_train: np.ndarray,
    sample_weights: np.ndarray,
) -> LogisticRegression:
    model = LogisticRegression(
        C=float(spec["C"]),
        solver=str(spec["solver"]),
        penalty="l2",
        max_iter=int(spec["max_iter"]),
        tol=1e-3,
        class_weight=spec["class_weight"],
        n_jobs=args.jobs,
        random_state=args.seed,
        verbose=0,
    )
    fit_kwargs = {}
    if spec["use_hard_weights"]:
        fit_kwargs["sample_weight"] = sample_weights
    model.fit(x_train, y_train, **fit_kwargs)
    return model


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    t_start = time.time()
    LOG.info("Loading labels from %s", args.checkpoint)
    ids, labels, domain_counts = load_labels(args.checkpoint)
    ids, labels = stratified_sample(ids, labels, args.sample_limit, args.seed)
    LOG.info(
        "Loaded %d labeled rows: positives=%d negatives=%d domains=%s",
        len(ids),
        int(labels.sum()),
        int((labels == 0).sum()),
        domain_counts,
    )

    LOG.info("Fetching DB texts from %s:%s/%s", args.db_host, args.db_port, args.db_name)
    enhanced_texts, old_texts, fetch_stats = fetch_texts(args, ids)
    LOG.info("Fetched texts: %s", fetch_stats)

    split = train_test_split(
        ids,
        enhanced_texts,
        old_texts,
        labels,
        test_size=args.test_size,
        random_state=args.seed,
        stratify=labels,
    )
    (
        train_ids,
        test_ids,
        train_texts,
        test_texts,
        train_old_texts,
        test_old_texts,
        y_train,
        y_test,
    ) = split
    y_train = np.asarray(y_train, dtype=np.int8)
    y_test = np.asarray(y_test, dtype=np.int8)
    LOG.info(
        "Split: train=%d pos=%d, test=%d pos=%d",
        len(y_train),
        int(y_train.sum()),
        len(y_test),
        int(y_test.sum()),
    )

    LOG.info("Evaluating old production model on the same holdout split")
    old_train_scores, old_test_scores, old_meta = old_model_scores(
        args, train_old_texts, test_old_texts
    )
    old_eval = None
    if old_test_scores is not None:
        old_eval = evaluate_scores(y_test, old_test_scores, args.target_recall)
        selected = old_eval["selected_for_target_recall"]
        prod030 = old_eval["fixed_thresholds"]["0.30"]
        LOG.info(
            "Old model: @0.30 precision=%.4f recall=%.4f f1=%.4f; target %.3f -> precision=%.4f threshold=%.4f",
            prod030["precision"],
            prod030["recall"],
            prod030["f1"],
            args.target_recall,
            selected["precision"],
            selected["threshold"],
        )

    model_train_texts = train_old_texts if args.text_mode == "old" else train_texts
    model_test_texts = test_old_texts if args.text_mode == "old" else test_texts

    LOG.info("Fitting vectorizer: feature_mode=%s text_mode=%s", args.feature_mode, args.text_mode)
    vectorizer = build_vectorizer(args)
    x_train = vectorizer.fit_transform(model_train_texts)
    x_test = vectorizer.transform(model_test_texts)
    LOG.info("Vectorized: train_shape=%s test_shape=%s", x_train.shape, x_test.shape)

    hard_weights = build_hard_weights(y_train, old_train_scores)
    variants = variant_specs(args.variants.split(","))
    variant_reports: dict[str, object] = {}
    models: dict[str, LogisticRegression] = {}

    best_name: str | None = None
    best_eval: dict[str, object] | None = None
    best_model: LogisticRegression | None = None

    for spec in variants:
        name = str(spec["name"])
        LOG.info("Training variant: %s", name)
        t0 = time.time()
        model = fit_variant(spec, args, x_train, y_train, hard_weights)
        elapsed = time.time() - t0
        scores = model.predict_proba(x_test)[:, 1]
        eval_report = evaluate_scores(y_test, scores, args.target_recall)
        selected = eval_report["selected_for_target_recall"]
        variant_reports[name] = {
            "spec": spec,
            "elapsed_sec": elapsed,
            "evaluation": eval_report,
        }
        models[name] = model
        LOG.info(
            "%s: target %.3f -> threshold=%.4f precision=%.4f recall=%.4f f1=%.4f candidate_rate=%.4f",
            name,
            args.target_recall,
            selected["threshold"],
            selected["precision"],
            selected["recall"],
            selected["f1"],
            selected["candidate_rate"],
        )

        if best_eval is None:
            best_name, best_eval, best_model = name, eval_report, model
            continue
        current = eval_report["selected_for_target_recall"]
        incumbent = best_eval["selected_for_target_recall"]
        if (current["precision"], current["f1"], -current["candidate_rate"]) > (
            incumbent["precision"],
            incumbent["f1"],
            -incumbent["candidate_rate"],
        ):
            best_name, best_eval, best_model = name, eval_report, model

    assert best_name is not None and best_eval is not None and best_model is not None

    args.out_model.parent.mkdir(parents=True, exist_ok=True)
    args.out_vectorizer.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    old_selected = (
        old_eval["selected_for_target_recall"] if old_eval is not None else None
    )
    best_selected = best_eval["selected_for_target_recall"]
    should_save = True
    save_reason = "no old model baseline"
    if old_selected is not None:
        should_save = (
            best_selected["precision"],
            best_selected["f1"],
            -best_selected["candidate_rate"],
        ) > (
            old_selected["precision"],
            old_selected["f1"],
            -old_selected["candidate_rate"],
        )
        save_reason = "v2 beats old baseline at target recall" if should_save else (
            "v2 does not beat old baseline at target recall"
        )
    if args.save_even_if_worse:
        should_save = True
        save_reason += "; forced by --save-even-if-worse"

    if should_save:
        LOG.info("Saving best variant %s: %s", best_name, save_reason)
        joblib.dump(vectorizer, args.out_vectorizer)
        joblib.dump(best_model, args.out_model)
    else:
        LOG.warning("Not saving v2 model/vectorizer: %s", save_reason)

    report = {
        "created_at_unix": time.time(),
        "elapsed_sec": time.time() - t_start,
        "args": {
            key: str(value) if isinstance(value, Path) else value
            for key, value in vars(args).items()
        },
        "domain_counts_before_sampling": domain_counts,
        "sampled_rows": {
            "n": len(ids),
            "positive": int(labels.sum()),
            "negative": int((labels == 0).sum()),
            "positive_rate": float(labels.mean()),
        },
        "fetch_stats": fetch_stats,
        "split": {
            "train_n": len(y_train),
            "train_positive": int(y_train.sum()),
            "test_n": len(y_test),
            "test_positive": int(y_test.sum()),
            "test_ids_preview": train_ids[:0] + test_ids[:10],
        },
        "old_model_meta": old_meta,
        "old_model_evaluation": old_eval,
        "variants": variant_reports,
        "best_variant": best_name,
        "best_evaluation": best_eval,
        "save_decision": {
            "saved": should_save,
            "reason": save_reason,
            "save_even_if_worse": bool(args.save_even_if_worse),
        },
        "artifacts": {
            "model": str(args.out_model) if should_save else None,
            "vectorizer": str(args.out_vectorizer) if should_save else None,
            "report": str(args.report),
        },
        "production_note": (
            "Do not replace production until the target-recall metric is accepted. "
            "The selected threshold is in best_evaluation.selected_for_target_recall.threshold."
        ),
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(
        {
            "best_variant": best_name,
            "target_recall": args.target_recall,
            "old_selected": old_selected,
            "v2_selected": best_selected,
            "save_decision": {
                "saved": should_save,
                "reason": save_reason,
            },
            "report": str(args.report),
            "model": str(args.out_model) if should_save else None,
            "vectorizer": str(args.out_vectorizer) if should_save else None,
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
