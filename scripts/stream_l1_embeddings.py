#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
from psycopg2.extras import RealDictCursor, execute_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from scripts.ensure_news_l1_infra import add_db_args, connect, ensure_news_l1_infra

LOGGER = logging.getLogger("stream_l1_embeddings")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Incrementally generate BGE-M3 embeddings for L1 event rows."
    )
    add_db_args(parser)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--encode-batch-size", type=int, default=64)
    parser.add_argument("--max-rows", type=int)
    parser.add_argument("--poll-sec", type=float, default=0.0)
    parser.add_argument("--max-empty-polls", type=int, default=0)
    parser.add_argument("--model-name", default="bge-m3")
    parser.add_argument("--model-path", default="/root/data/models/bge-m3")
    parser.add_argument("--device", choices=("cpu", "cuda", "auto"), default="cpu")
    parser.add_argument("--tei-url", default=os.getenv("BGE_TEI_URL", ""))
    parser.add_argument("--max-seq-length", type=int, default=1024)
    parser.add_argument("--body-chars", type=int, default=2400)
    parser.add_argument("--include-general-news", action="store_true")
    parser.add_argument("--target-start")
    parser.add_argument("--target-end")
    parser.add_argument("--log-every", type=int, default=1000)
    return parser.parse_args()


def normalize_space(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def build_embedding_text(title: str | None, body: str | None, body_chars: int) -> str:
    title_clean = normalize_space(title)
    body_clean = normalize_space(body)
    if len(body_clean) > body_chars:
        body_clean = body_clean[:body_chars]
    return f"{title_clean}\n{body_clean}".strip()


def sha256_text(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def fetch_rows(conn: Any, args: argparse.Namespace, remaining: int | None) -> list[dict[str, Any]]:
    limit = args.batch_size if remaining is None else max(1, min(args.batch_size, remaining))
    filters = [
        "e.parse_success IS TRUE",
        "(ne.news_id IS NULL OR ne.model <> %s OR ne.embedding_text_hash IS DISTINCT FROM p.embedding_text_hash)",
    ]
    params: list[Any] = [args.model_name]
    if not args.include_general_news:
        filters.append("e.event_domain = 'political'")
    if args.target_start:
        filters.append("COALESCE(n.published_at, p.published_at_clean) >= %s")
        params.append(args.target_start)
    if args.target_end:
        filters.append("COALESCE(n.published_at, p.published_at_clean) <= %s")
        params.append(args.target_end)
    params.append(limit)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT e.news_id,
                   COALESCE(n.title, '') AS title,
                   COALESCE(n.body, '') AS body,
                   p.embedding_text_hash,
                   p.embedding_text_chars
            FROM public.news_l1_event_extractions AS e
            JOIN public.news_l1_prep AS p ON p.news_id = e.news_id
            JOIN public.news AS n ON n.id = e.news_id
            LEFT JOIN public.news_embeddings AS ne ON ne.news_id = e.news_id
            WHERE {" AND ".join(filters)}
            ORDER BY e.news_id
            LIMIT %s
            """,
            params,
        )
        return [dict(row) for row in cur.fetchall()]


class LocalSentenceTransformerEmbedder:
    def __init__(self, model_path: str, device: str | None, max_seq_length: int) -> None:
        from sentence_transformers import SentenceTransformer

        path = Path(model_path)
        if not path.is_dir():
            raise FileNotFoundError(f"BGE model path not found: {path}")
        if not (path / "config.json").is_file():
            raise FileNotFoundError(f"BGE model config.json not found under: {path}")
        self._model = SentenceTransformer(
            str(path),
            device=device,
            local_files_only=True,
        )
        self._model.max_seq_length = max_seq_length
        self._dim = int(self._model.get_sentence_embedding_dimension() or 1024)

    def encode(
        self,
        texts: list[str],
        *,
        batch_size: int,
        show_progress_bar: bool,
    ) -> np.ndarray:
        if not texts:
            return np.zeros((0, self._dim), dtype=np.float32)
        vectors = self._model.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=True,
            show_progress_bar=show_progress_bar,
            convert_to_numpy=True,
        )
        return np.asarray(vectors, dtype=np.float32)


def make_embedder(args: argparse.Namespace) -> Any:
    os.environ["BGE_MAX_SEQ_LENGTH"] = str(args.max_seq_length)
    if args.tei_url:
        from agentic_rag.ingestion.embedder import TeiRemoteEmbedder

        return TeiRemoteEmbedder(args.tei_url)

    device = None if args.device == "auto" else args.device
    return LocalSentenceTransformerEmbedder(
        args.model_path,
        device=device,
        max_seq_length=args.max_seq_length,
    )


def upsert_embeddings(
    conn: Any,
    rows: list[dict[str, Any]],
    vectors: np.ndarray,
    *,
    model_name: str,
    body_chars: int,
) -> None:
    if not rows:
        return
    values = []
    for row, vec in zip(rows, vectors):
        text = build_embedding_text(row.get("title"), row.get("body"), body_chars)
        arr = np.asarray(vec, dtype=np.float32)
        values.append(
            (
                int(row["news_id"]),
                model_name,
                int(arr.shape[0]),
                [float(x) for x in arr.tolist()],
                sha256_text(text),
                len(text),
            )
        )

    sql = """
        INSERT INTO public.news_embeddings (
            news_id, model, dim, embedding, embedding_text_hash, embedding_text_chars
        )
        VALUES %s
        ON CONFLICT (news_id) DO UPDATE SET
            model = EXCLUDED.model,
            dim = EXCLUDED.dim,
            embedding = EXCLUDED.embedding,
            embedding_text_hash = EXCLUDED.embedding_text_hash,
            embedding_text_chars = EXCLUDED.embedding_text_chars,
            updated_at = now()
    """
    with conn.cursor() as cur:
        execute_values(cur, sql, values, page_size=32)
    conn.commit()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    conn = connect(args)
    ensure_news_l1_infra(conn)

    embedder = None
    total = 0
    empty_polls = 0
    started = time.time()
    try:
        while True:
            remaining = None if args.max_rows is None else max(0, args.max_rows - total)
            if remaining == 0:
                break
            rows = fetch_rows(conn, args, remaining)
            if not rows:
                empty_polls += 1
                if args.poll_sec <= 0:
                    break
                if args.max_empty_polls and empty_polls >= args.max_empty_polls:
                    break
                LOGGER.info("no rows; sleeping %.1fs", args.poll_sec)
                time.sleep(args.poll_sec)
                continue

            empty_polls = 0
            if embedder is None:
                embedder = make_embedder(args)

            texts = [
                build_embedding_text(row.get("title"), row.get("body"), args.body_chars)
                for row in rows
            ]
            vectors = embedder.encode(
                texts,
                batch_size=args.encode_batch_size,
                show_progress_bar=False,
            )
            upsert_embeddings(
                conn,
                rows,
                vectors,
                model_name=args.model_name,
                body_chars=args.body_chars,
            )
            total += len(rows)

            if total % max(1, args.log_every) == 0 or args.max_rows is not None:
                elapsed = time.time() - started
                LOGGER.info(
                    "embedded=%s elapsed=%.1fs rate=%.2f rows/s",
                    total,
                    elapsed,
                    total / max(elapsed, 1.0),
                )
    finally:
        conn.close()

    elapsed = time.time() - started
    LOGGER.info("done embedded=%s elapsed=%.1fs", total, elapsed)


if __name__ == "__main__":
    main()
