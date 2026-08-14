#!/usr/bin/env python3
"""
Audit samples inside and outside a domain-gate threshold on a Postgres news table.

This answers:
  - Are rows kept by score >= threshold actually geopolitical-related?
  - Are rows excluded by score < threshold mostly non-geopolitical?

The script is read-only against Postgres. It scores the table with the
production TF-IDF+LR model, samples kept/excluded rows, then asks a local
OpenAI-compatible LLM to label each sample as:
  geopolitical_event / geo_relevant_context / reject
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

from db_runtime_config import require_database_password

REPO = Path(__file__).resolve().parent.parent
MODEL_DIR = REPO / "data" / "models"
DEFAULT_OUT_DIR = REPO / "data" / "analysis" / "domain_gate_news_threshold_audit"

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
    parser = argparse.ArgumentParser(description="Audit news table samples around LR threshold.")
    parser.add_argument("--threshold", type=float, default=0.30)
    parser.add_argument("--sample-kept", type=int, default=400)
    parser.add_argument("--sample-excluded", type=int, default=400)
    parser.add_argument("--reservoir-multiplier", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=20000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument("--model", default="qwen2.5-7b-awq")
    parser.add_argument("--base-url", default="http://127.0.0.1:8004/v1/chat/completions")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--db-host", default=os.getenv("PG_HOST", "192.168.207.171"))
    parser.add_argument("--db-port", type=int, default=int(os.getenv("PG_PORT", "54333")))
    parser.add_argument("--db-name", default=os.getenv("PG_DBNAME", "news"))
    parser.add_argument("--db-user", default=os.getenv("PG_WRITE_USER", "postgres"))
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


def reservoir_add(items: list[dict[str, Any]], item: dict[str, Any], limit: int, seen: int, rng: random.Random) -> None:
    if len(items) < limit:
        items.append(item)
        return
    j = rng.randint(0, seen - 1)
    if j < limit:
        items[j] = item


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
        return match.group(1).lower(), raw[:300]
    return "parse_error", raw[:300]


def audit_one(args: argparse.Namespace, row: dict[str, Any]) -> dict[str, Any]:
    user = (
        f"Gate bucket: {row['bucket']}\n"
        f"LR score: {row['score']:.4f}\n"
        f"Title: {row['title']}\n"
        f"Language: {row['language']}\n"
        f"Region: {row['region']}\n"
        f"Source id: {row['media_source_id']}\n"
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
    out = dict(row)
    out.update(
        {
            "business_label": label,
            "reason": reason,
            "raw": raw,
            "error": error,
            "latency_sec": time.time() - t0,
        }
    )
    return out


def label_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["business_label"]] = counts.get(row["business_label"], 0) + 1
    return counts


def summarize_bucket(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = label_counts(rows)
    n = len(rows)
    event = counts.get("geopolitical_event", 0)
    context = counts.get("geo_relevant_context", 0)
    reject = counts.get("reject", 0)
    return {
        "n": n,
        "counts": counts,
        "event_rate": event / n if n else 0.0,
        "context_rate": context / n if n else 0.0,
        "material_rate_event_plus_context": (event + context) / n if n else 0.0,
        "reject_rate": reject / n if n else 0.0,
    }


def main() -> int:
    args = parse_args()
    rng = random.Random(args.seed)
    kept_limit = max(args.sample_kept * args.reservoir_multiplier, args.sample_kept)
    excluded_limit = max(args.sample_excluded * args.reservoir_multiplier, args.sample_excluded)
    kept_pool: list[dict[str, Any]] = []
    excluded_pool: list[dict[str, Any]] = []
    kept_seen = 0
    excluded_seen = 0
    total = 0
    last_id = -1
    t0 = time.time()

    vectorizer = joblib.load(MODEL_DIR / "domain_tfidf_lr.joblib")
    model = joblib.load(MODEL_DIR / "domain_classifier_lr.joblib")

    conn = connect(args)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*), MIN(id), MAX(id) FROM news")
        table_count, min_id, max_id = cur.fetchone()
        while True:
            cur.execute(
                """
                SELECT
                    id,
                    COALESCE(title, ''),
                    LEFT(COALESCE(body, ''), 900),
                    COALESCE(language, ''),
                    COALESCE(region, ''),
                    media_source_id
                FROM news
                WHERE id > %s
                ORDER BY id
                LIMIT %s
                """,
                (last_id, args.batch_size),
            )
            rows = cur.fetchall()
            if not rows:
                break
            texts = [f"{row[1]} {row[2][:500]}" for row in rows]
            scores = model.predict_proba(vectorizer.transform(texts))[:, 1]
            for row, score in zip(rows, scores):
                article_id, title, body, language, region, media_source_id = row
                item = {
                    "article_id": int(article_id),
                    "score": float(score),
                    "title": title,
                    "body": body,
                    "language": language,
                    "region": region,
                    "media_source_id": media_source_id,
                }
                if score >= args.threshold:
                    kept_seen += 1
                    item["bucket"] = "kept"
                    reservoir_add(kept_pool, item, kept_limit, kept_seen, rng)
                else:
                    excluded_seen += 1
                    item["bucket"] = "excluded"
                    reservoir_add(excluded_pool, item, excluded_limit, excluded_seen, rng)
            total += len(rows)
            last_id = int(rows[-1][0])
            if total % 200000 < args.batch_size:
                print(
                    f"scored={total} kept={kept_seen} excluded={excluded_seen} "
                    f"rate={total / max(time.time() - t0, 1e-9):.1f} rows/s",
                    flush=True,
                )
    finally:
        conn.close()

    kept_sample = rng.sample(kept_pool, min(args.sample_kept, len(kept_pool)))
    excluded_sample = rng.sample(excluded_pool, min(args.sample_excluded, len(excluded_pool)))
    tasks = kept_sample + excluded_sample
    rng.shuffle(tasks)

    results: list[dict[str, Any]] = []
    t_audit = time.time()
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [executor.submit(audit_one, args, row) for row in tasks]
        for i, future in enumerate(as_completed(futures), 1):
            results.append(future.result())
            if i % 50 == 0:
                print(f"audited={i}/{len(futures)} elapsed={time.time() - t_audit:.1f}s", flush=True)

    kept_results = [row for row in results if row["bucket"] == "kept"]
    excluded_results = [row for row in results if row["bucket"] == "excluded"]
    summary = {
        "created_at_unix": time.time(),
        "db": {
            "host": args.db_host,
            "port": args.db_port,
            "name": args.db_name,
            "table": "news",
            "table_count_at_start": int(table_count or 0),
            "min_id": int(min_id or 0),
            "max_id": int(max_id or 0),
        },
        "threshold": args.threshold,
        "scored_rows": total,
        "gate_counts": {
            "kept": kept_seen,
            "excluded": excluded_seen,
            "kept_rate": kept_seen / max(total, 1),
            "excluded_rate": excluded_seen / max(total, 1),
        },
        "audit": {
            "kept": summarize_bucket(kept_results),
            "excluded": summarize_bucket(excluded_results),
        },
        "elapsed_sec": time.time() - t0,
        "audit_elapsed_sec": time.time() - t_audit,
        "artifacts": {},
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"news_{args.db_name}_thr{args.threshold:.2f}_kept{len(kept_results)}_excluded{len(excluded_results)}"
    result_path = args.out_dir / f"{stem}.jsonl"
    summary_path = args.out_dir / f"{stem}_summary.json"
    with result_path.open("w", encoding="utf-8") as f:
        for row in sorted(results, key=lambda r: (r["bucket"], -r["score"], r["article_id"])):
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary["artifacts"] = {"results": str(result_path), "summary": str(summary_path)}
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
