#!/usr/bin/env python3
"""
Audit the production TF-IDF+LR domain gate under a broader business label.

The old strict label treats only event.domain == "geopolitical" as positive.
For L1 operations we may need a broader material pool:

  geopolitical_event   -> suitable for L1 event clustering
  geo_relevant_context -> useful as background/impact material, not direct L1 input
  reject               -> not useful for geopolitical pipeline

This script keeps the production LR gate unchanged, samples strict false
positives, asks a local OpenAI-compatible LLM to relabel them into the three
business classes, and estimates adjusted precision.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import psycopg2
from sklearn.metrics import confusion_matrix, f1_score, precision_score, recall_score

from db_runtime_config import require_database_password

REPO = Path(__file__).resolve().parent.parent
DATA_DIR = REPO / "data"
MODEL_DIR = DATA_DIR / "models"
DEFAULT_OUT_DIR = DATA_DIR / "analysis" / "domain_gate_business_relevance"


SYSTEM_PROMPT = """You are auditing a geopolitical news pipeline.
Return ONLY valid JSON with this schema:
{"label":"geopolitical_event","reason":"short reason"}

Definitions:
- geopolitical_event: A concrete event suitable for geopolitical event clustering. It involves a state, government, leader, ministry, military/security force, armed group, international organization, election/parliament/government action, diplomacy, sanctions, tariffs/export controls as state policy, border disputes, treaties, ceasefire, war, attacks, protests/repression, migration policy, human-rights policy, or cross-border political/security relations.
- geo_relevant_context: Useful context or impact material for geopolitical analysis, but not a direct L1 event item. Examples: markets/energy/shipping/supply-chain/technology/business stories driven by war, sanctions, tariffs, state policy, export controls, sovereign risk, geopolitical realignment, national-security regulation, or a named international crisis.
- reject: Sports, entertainment, lifestyle, weather, product reviews, ordinary company earnings/stock/crypto, local accidents/crime, health/education, and domestic-only non-policy stories with no clear geopolitical value.

Important:
- The label value MUST be exactly one of these three strings:
  geopolitical_event
  geo_relevant_context
  reject
- Do NOT output pipes, slashes, lists, alternatives, markdown, or copied schema text.
- Macro finance is reject if it is pure market/company news.
- Macro finance is geo_relevant_context if the central cause or impact is sanctions, war, tariffs, trade conflict, sovereign debt, energy security, or state-to-state policy.
- If it is a direct government/security/diplomatic/policy action, choose geopolitical_event rather than context.
- Use the title and excerpt; infer across languages when possible.

Valid output examples:
{"label":"geopolitical_event","reason":"government sanctions against another country"}
{"label":"geo_relevant_context","reason":"oil market impact caused by regional war"}
{"label":"reject","reason":"sports story with no geopolitical value"}
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit LR domain gate business precision.")
    parser.add_argument("--checkpoint", type=Path, default=DATA_DIR / "checkpoint_v13_all.jsonl")
    parser.add_argument("--threshold", type=float, default=0.30)
    parser.add_argument("--sample-fp", type=int, default=400)
    parser.add_argument("--sample-tp", type=int, default=80)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument("--model", default="qwen2.5-7b-awq")
    parser.add_argument("--base-url", default="http://127.0.0.1:8004/v1/chat/completions")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--db-host", default=os.getenv("PG_HOST", "192.168.207.171"))
    parser.add_argument("--db-port", type=int, default=int(os.getenv("PG_PORT", "54333")))
    parser.add_argument("--db-name", default=os.getenv("PG_DBNAME", "globemind_news"))
    parser.add_argument("--db-user", default=os.getenv("PG_WRITE_USER", "postgres"))
    return parser.parse_args()


def load_labels(path: Path) -> tuple[list[int], np.ndarray]:
    ids: list[int] = []
    labels: list[int] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            item = json.loads(line)
            domain = (item.get("event") or {}).get("domain")
            if not domain:
                continue
            ids.append(int(item["article_id"]))
            labels.append(1 if domain == "geopolitical" else 0)
    return ids, np.asarray(labels, dtype=np.int8)


def fetch_rows(args: argparse.Namespace, ids: list[int]) -> tuple[list[str], dict[int, dict[str, Any]]]:
    conn = psycopg2.connect(
        host=args.db_host,
        port=args.db_port,
        dbname=args.db_name,
        user=args.db_user,
        password=require_database_password(),
        connect_timeout=15,
    )
    by_id: dict[int, dict[str, Any]] = {}
    try:
        cur = conn.cursor()
        for start in range(0, len(ids), 5000):
            batch = ids[start : start + 5000]
            cur.execute(
                """
                SELECT
                    id,
                    COALESCE(title, ''),
                    LEFT(COALESCE(body, ''), 900),
                    COALESCE(media_source_domain, ''),
                    COALESCE(source_dataset_name, ''),
                    COALESCE(topic_name, ''),
                    COALESCE(topic_region, ''),
                    COALESCE(language, '')
                FROM news
                WHERE id = ANY(%s)
                """,
                (batch,),
            )
            for row in cur.fetchall():
                article_id, title, body, source_domain, source_dataset, topic, topic_region, language = row
                by_id[int(article_id)] = {
                    "article_id": int(article_id),
                    "title": title,
                    "body": body,
                    "source_domain": source_domain,
                    "source_dataset": source_dataset,
                    "topic": topic,
                    "topic_region": topic_region,
                    "language": language,
                }
    finally:
        conn.close()

    old_texts: list[str] = []
    for article_id in ids:
        row = by_id.get(article_id)
        if row is None:
            old_texts.append("")
            by_id[article_id] = {
                "article_id": article_id,
                "title": "",
                "body": "",
                "source_domain": "",
                "source_dataset": "",
                "topic": "",
                "topic_region": "",
                "language": "",
            }
        else:
            old_texts.append(f"{row['title']} {row['body'][:500]}")
    return old_texts, by_id


def strict_metrics(y_true: np.ndarray, scores: np.ndarray, threshold: float) -> dict[str, Any]:
    pred = (scores >= threshold).astype(np.int8)
    tn, fp, fn, tp = confusion_matrix(y_true, pred, labels=[0, 1]).ravel()
    return {
        "threshold": threshold,
        "precision": float(precision_score(y_true, pred, zero_division=0)),
        "recall": float(recall_score(y_true, pred, zero_division=0)),
        "f1": float(f1_score(y_true, pred, zero_division=0)),
        "tp": int(tp),
        "fp": int(fp),
        "tn": int(tn),
        "fn": int(fn),
        "predicted_positive": int(pred.sum()),
        "candidate_rate": float(pred.mean()),
    }


def parse_llm_label(raw: str) -> tuple[str, str]:
    raw = (raw or "").strip()
    try:
        obj = json.loads(raw)
        label = obj.get("label")
        reason = str(obj.get("reason", ""))[:300]
        if label in {"geopolitical_event", "geo_relevant_context", "reject"}:
            return label, reason
        return "parse_error", f"invalid label: {label!r}; raw={raw[:220]}"
    except Exception:
        pass

    match = re.search(
        r'"label"\s*:\s*"(geopolitical_event|geo_relevant_context|reject)"',
        raw,
        re.I,
    )
    if match:
        label = match.group(1).lower()
        return label, raw[:300]
    return "parse_error", raw[:300]


def audit_one(args: argparse.Namespace, row: dict[str, Any], score: float, strict_gold: int) -> dict[str, Any]:
    user = (
        f"LR score: {score:.4f}\n"
        f"Strict old gold: {'geopolitical' if strict_gold else 'not_geopolitical'}\n"
        f"Title: {row['title']}\n"
        f"Language: {row['language']}\n"
        f"Source domain: {row['source_domain']}\n"
        f"Source dataset: {row['source_dataset']}\n"
        f"Topic: {row['topic']} {row['topic_region']}\n"
        f"Body excerpt: {row['body'][:900]}\n\n"
        "Return only JSON."
    )
    payload = {
        "model": args.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "max_tokens": 48,
    }
    req = urllib.request.Request(
        args.base_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        raw = data["choices"][0]["message"]["content"]
        label, reason = parse_llm_label(raw)
        error = None
    except Exception as exc:
        raw = repr(exc)
        label, reason = "request_error", raw[:300]
        error = raw
    return {
        "article_id": row["article_id"],
        "score": float(score),
        "strict_gold": int(strict_gold),
        "business_label": label,
        "reason": reason,
        "title": row["title"],
        "source_domain": row["source_domain"],
        "language": row["language"],
        "raw": raw,
        "error": error,
        "latency_sec": time.time() - t0,
    }


def summarize(strict: dict[str, Any], fp_results: list[dict[str, Any]], tp_results: list[dict[str, Any]]) -> dict[str, Any]:
    fp_n = len(fp_results)
    tp_n = len(tp_results)
    fp_counts: dict[str, int] = {}
    tp_counts: dict[str, int] = {}
    for row in fp_results:
        fp_counts[row["business_label"]] = fp_counts.get(row["business_label"], 0) + 1
    for row in tp_results:
        tp_counts[row["business_label"]] = tp_counts.get(row["business_label"], 0) + 1

    fp_event_rate = fp_counts.get("geopolitical_event", 0) / fp_n if fp_n else 0.0
    fp_material_rate = (
        fp_counts.get("geopolitical_event", 0) + fp_counts.get("geo_relevant_context", 0)
    ) / fp_n if fp_n else 0.0
    tp_event_rate = tp_counts.get("geopolitical_event", 0) / tp_n if tp_n else 1.0
    tp_material_rate = (
        tp_counts.get("geopolitical_event", 0) + tp_counts.get("geo_relevant_context", 0)
    ) / tp_n if tp_n else 1.0

    tp = strict["tp"]
    fp = strict["fp"]
    denom = max(tp + fp, 1)

    adjusted_l1_precision = (tp * tp_event_rate + fp * fp_event_rate) / denom
    adjusted_material_precision = (tp * tp_material_rate + fp * fp_material_rate) / denom

    return {
        "strict_metrics": strict,
        "audit_sample": {
            "fp_n": fp_n,
            "fp_counts": fp_counts,
            "tp_n": tp_n,
            "tp_counts": tp_counts,
            "fp_event_rate": fp_event_rate,
            "fp_material_rate_event_plus_context": fp_material_rate,
            "tp_event_rate": tp_event_rate,
            "tp_material_rate_event_plus_context": tp_material_rate,
        },
        "adjusted_precision_estimate": {
            "l1_event_precision": adjusted_l1_precision,
            "material_pool_precision_event_plus_context": adjusted_material_precision,
            "method": (
                "strict TP/FP counts from full data; business rates estimated from audited "
                "samples of strict FP and TP candidates."
            ),
        },
    }


def main() -> int:
    args = parse_args()
    random.seed(args.seed)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    ids, y_true = load_labels(args.checkpoint)
    old_texts, rows_by_id = fetch_rows(args, ids)
    vectorizer = joblib.load(MODEL_DIR / "domain_tfidf_lr.joblib")
    model = joblib.load(MODEL_DIR / "domain_classifier_lr.joblib")
    scores = model.predict_proba(vectorizer.transform(old_texts))[:, 1]

    strict = strict_metrics(y_true, scores, args.threshold)
    pred = scores >= args.threshold
    fp_indices = np.flatnonzero(pred & (y_true == 0)).tolist()
    tp_indices = np.flatnonzero(pred & (y_true == 1)).tolist()
    sampled_fp = random.sample(fp_indices, min(args.sample_fp, len(fp_indices)))
    sampled_tp = random.sample(tp_indices, min(args.sample_tp, len(tp_indices)))

    stamp = f"thr{args.threshold:.2f}_fp{len(sampled_fp)}_tp{len(sampled_tp)}"
    result_path = args.out_dir / f"audit_{stamp}.jsonl"
    summary_path = args.out_dir / f"summary_{stamp}.json"

    tasks: list[tuple[str, int]] = [("fp", idx) for idx in sampled_fp] + [("tp", idx) for idx in sampled_tp]
    random.shuffle(tasks)
    results: list[dict[str, Any]] = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = []
        for kind, idx in tasks:
            row = rows_by_id[ids[idx]]
            futures.append(executor.submit(audit_one, args, row, float(scores[idx]), int(y_true[idx])))
        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            results.append(result)
            if i % 50 == 0:
                print(f"audited {i}/{len(futures)} elapsed={time.time() - t0:.1f}s", flush=True)

    fp_results = [r for r in results if r["strict_gold"] == 0]
    tp_results = [r for r in results if r["strict_gold"] == 1]
    summary = summarize(strict, fp_results, tp_results)
    summary.update(
        {
            "args": {
                "threshold": args.threshold,
                "sample_fp": args.sample_fp,
                "sample_tp": args.sample_tp,
                "seed": args.seed,
                "model": args.model,
                "concurrency": args.concurrency,
            },
            "elapsed_sec": time.time() - t0,
            "throughput_rps": len(results) / max(time.time() - t0, 1e-9),
            "artifacts": {
                "results": str(result_path),
                "summary": str(summary_path),
            },
        }
    )

    with result_path.open("w", encoding="utf-8") as f:
        for row in sorted(results, key=lambda r: (r["strict_gold"], -r["score"], r["article_id"])):
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
