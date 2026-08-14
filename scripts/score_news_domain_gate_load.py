#!/usr/bin/env python3
"""
Score the Postgres news table with the production TF-IDF+LR domain gate.

This script is read-only against Postgres. It estimates how many rows would
continue into LLM/domain extraction at different LR thresholds.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import psycopg2

from db_runtime_config import require_database_password

REPO = Path(__file__).resolve().parent.parent
MODEL_DIR = REPO / "data" / "models"
DEFAULT_OUT_DIR = REPO / "data" / "analysis" / "domain_gate_news_table_load"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score news table with domain LR gate.")
    parser.add_argument("--db-host", default=os.getenv("PG_HOST", "192.168.207.171"))
    parser.add_argument("--db-port", type=int, default=int(os.getenv("PG_PORT", "54333")))
    parser.add_argument("--db-name", default=os.getenv("PG_DBNAME", "globemind_news"))
    parser.add_argument("--db-user", default=os.getenv("PG_WRITE_USER", "postgres"))
    parser.add_argument("--batch-size", type=int, default=10000)
    parser.add_argument("--limit", type=int, default=0, help="0 means full table.")
    parser.add_argument("--progress-every", type=int, default=100000)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--thresholds",
        default="0.10,0.15,0.20,0.25,0.30,0.35,0.40,0.50,0.65,0.75,0.85,0.90",
    )
    return parser.parse_args()


def connect(args: argparse.Namespace):
    return psycopg2.connect(
        host=args.db_host,
        port=args.db_port,
        dbname=args.db_name,
        user=args.db_user,
        password=require_database_password(),
        connect_timeout=15,
    )


def main() -> int:
    args = parse_args()
    thresholds = [float(x.strip()) for x in args.thresholds.split(",") if x.strip()]
    counts = {f"{threshold:.2f}": 0 for threshold in thresholds}
    score_hist = {
        "0.00_0.10": 0,
        "0.10_0.20": 0,
        "0.20_0.30": 0,
        "0.30_0.50": 0,
        "0.50_0.65": 0,
        "0.65_0.85": 0,
        "0.85_1.00": 0,
    }

    vectorizer = joblib.load(MODEL_DIR / "domain_tfidf_lr.joblib")
    model = joblib.load(MODEL_DIR / "domain_classifier_lr.joblib")

    conn = connect(args)
    total_rows = 0
    table_count = None
    min_id = None
    max_id = None
    last_id = -1
    t0 = time.time()
    last_progress = 0

    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*), MIN(id), MAX(id) FROM news")
        table_count, min_id, max_id = cur.fetchone()

        while True:
            if args.limit and total_rows >= args.limit:
                break
            batch_limit = args.batch_size
            if args.limit:
                batch_limit = min(batch_limit, args.limit - total_rows)
            cur.execute(
                """
                SELECT id, COALESCE(title, '') || ' ' || LEFT(COALESCE(body, ''), 500)
                FROM news
                WHERE id > %s
                ORDER BY id
                LIMIT %s
                """,
                (last_id, batch_limit),
            )
            rows = cur.fetchall()
            if not rows:
                break

            ids = [int(row[0]) for row in rows]
            texts = [row[1] or "" for row in rows]
            scores = model.predict_proba(vectorizer.transform(texts))[:, 1]
            total_rows += len(rows)
            last_id = ids[-1]

            for threshold in thresholds:
                counts[f"{threshold:.2f}"] += int(np.count_nonzero(scores >= threshold))

            score_hist["0.00_0.10"] += int(np.count_nonzero(scores < 0.10))
            score_hist["0.10_0.20"] += int(np.count_nonzero((scores >= 0.10) & (scores < 0.20)))
            score_hist["0.20_0.30"] += int(np.count_nonzero((scores >= 0.20) & (scores < 0.30)))
            score_hist["0.30_0.50"] += int(np.count_nonzero((scores >= 0.30) & (scores < 0.50)))
            score_hist["0.50_0.65"] += int(np.count_nonzero((scores >= 0.50) & (scores < 0.65)))
            score_hist["0.65_0.85"] += int(np.count_nonzero((scores >= 0.65) & (scores < 0.85)))
            score_hist["0.85_1.00"] += int(np.count_nonzero(scores >= 0.85))

            if total_rows - last_progress >= args.progress_every:
                elapsed = time.time() - t0
                print(
                    f"processed={total_rows} last_id={last_id} "
                    f"rate={total_rows / max(elapsed, 1e-9):.1f} rows/s",
                    flush=True,
                )
                last_progress = total_rows
    finally:
        conn.close()

    elapsed = time.time() - t0
    threshold_summary: dict[str, dict[str, Any]] = {}
    denominator = max(total_rows, 1)
    for threshold_key, candidate_count in counts.items():
        threshold_summary[threshold_key] = {
            "candidate_count": candidate_count,
            "candidate_rate": candidate_count / denominator,
            "reduction_rate": 1.0 - candidate_count / denominator,
            "reduction_x": denominator / max(candidate_count, 1),
        }

    report = {
        "created_at_unix": time.time(),
        "db": {
            "host": args.db_host,
            "port": args.db_port,
            "name": args.db_name,
            "table": "news",
            "table_count": int(table_count or 0),
            "min_id": int(min_id or 0),
            "max_id": int(max_id or 0),
        },
        "scored_rows": total_rows,
        "limit": args.limit,
        "elapsed_sec": elapsed,
        "throughput_rows_per_sec": total_rows / max(elapsed, 1e-9),
        "model": {
            "classifier": str(MODEL_DIR / "domain_classifier_lr.joblib"),
            "vectorizer": str(MODEL_DIR / "domain_tfidf_lr.joblib"),
            "text": "COALESCE(title,'') || ' ' || LEFT(COALESCE(body,''),500)",
        },
        "thresholds": threshold_summary,
        "score_histogram": score_hist,
        "performance_note": {
            "threshold_0.30_strict_v13": {
                "precision": 0.4232872965951046,
                "recall": 0.982776410826256,
                "f1": 0.5917182385128192,
            },
            "threshold_0.30_business_relevance_audit": {
                "l1_event_precision_estimate": 0.5136161629974019,
                "material_pool_precision_event_plus_context_estimate": 0.7741162997401887,
                "source": str(
                    REPO
                    / "data"
                    / "analysis"
                    / "domain_gate_business_relevance"
                    / "summary_thr0.30_fp400_tp80.json"
                ),
            },
        },
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    suffix = "full" if not args.limit else f"limit{args.limit}"
    out_path = args.out_dir / f"news_domain_gate_load_{suffix}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    print(f"report={out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
