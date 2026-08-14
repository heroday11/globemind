"""
Hybrid vector + full-text store backed by SQLite.

Architecture:
  documents   - raw document metadata
  chunks      - text chunks
  vectors     - float32 blob vectors (numpy)
  chunks_fts  - standalone FTS5 table for BM25 keyword search

For TB-scale production: swap SQLite for Milvus (vectors) +
Postgres+pgvector (structured) while keeping this same interface.
"""
from __future__ import annotations
import json
import sqlite3
import struct
from pathlib import Path
from typing import List, Optional
import numpy as np


class HybridStore:
    def __init__(self, db_path: str = "./data/rag.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()

    def _init_schema(self):
        c = self._conn
        c.executescript("""
            CREATE TABLE IF NOT EXISTS documents (
                id         TEXT PRIMARY KEY,
                title      TEXT,
                source     TEXT,
                category   TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                meta       TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS chunks (
                id        TEXT PRIMARY KEY,
                doc_id    TEXT NOT NULL,
                chunk_idx INTEGER NOT NULL,
                text      TEXT NOT NULL,
                meta      TEXT DEFAULT '{}',
                FOREIGN KEY(doc_id) REFERENCES documents(id)
            );

            CREATE TABLE IF NOT EXISTS vectors (
                chunk_id TEXT PRIMARY KEY,
                dim      INTEGER NOT NULL,
                data     BLOB NOT NULL
            );

            -- Standalone FTS5 (not a content table) — simple and reliable
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
            USING fts5(chunk_id UNINDEXED, text);
        """)
        c.commit()

    # ------------------------------------------------------------------ #
    #  Ingestion                                                           #
    # ------------------------------------------------------------------ #
    def upsert_document(self, doc_id: str, title: str, source: str,
                        category: str = "general", meta: dict | None = None):
        self._conn.execute(
            "INSERT OR REPLACE INTO documents(id, title, source, category, meta) VALUES(?,?,?,?,?)",
            (doc_id, title, source, category, json.dumps(meta or {}))
        )
        self._conn.commit()

    def upsert_chunks(self, chunks: list, vectors: np.ndarray):
        """Insert chunk texts + their vectors in one transaction."""
        assert len(chunks) == len(vectors), "chunks and vectors must align"
        c = self._conn
        for chunk, vec in zip(chunks, vectors):
            # Insert chunk
            existed = c.execute(
                "SELECT 1 FROM chunks WHERE id=?", (chunk.uid,)
            ).fetchone()
            if not existed:
                c.execute(
                    "INSERT INTO chunks(id, doc_id, chunk_idx, text, meta) VALUES(?,?,?,?,?)",
                    (chunk.uid, chunk.doc_id, chunk.chunk_index,
                     chunk.text, json.dumps(chunk.metadata))
                )
                # Insert into FTS
                c.execute(
                    "INSERT INTO chunks_fts(chunk_id, text) VALUES(?,?)",
                    (chunk.uid, chunk.text)
                )

            # Upsert vector
            blob = struct.pack(f"{len(vec)}f", *vec.tolist())
            c.execute(
                "INSERT OR REPLACE INTO vectors(chunk_id, dim, data) VALUES(?,?,?)",
                (chunk.uid, len(vec), blob)
            )
        c.commit()

    # ------------------------------------------------------------------ #
    #  Vector search (cosine, brute-force — swap with Milvus ANN at TB)  #
    # ------------------------------------------------------------------ #
    def vector_search(self, query_vec: np.ndarray, top_k: int = 5,
                      category_filter: Optional[str] = None) -> List[dict]:
        sql = """
            SELECT v.chunk_id, v.dim, v.data, c.text, c.doc_id, c.meta,
                   d.title, d.source, d.category
            FROM vectors v
            JOIN chunks c ON c.id = v.chunk_id
            JOIN documents d ON d.id = c.doc_id
        """
        params: tuple = ()
        if category_filter:
            sql += " WHERE d.category = ?"
            params = (category_filter,)

        rows = self._conn.execute(sql, params).fetchall()
        if not rows:
            return []

        ids, texts, doc_ids, metas, titles, sources, categories = [], [], [], [], [], [], []
        matrix = []
        for chunk_id, dim, blob, text, doc_id, meta, title, source, cat in rows:
            vec = np.array(struct.unpack(f"{dim}f", blob), dtype=np.float32)
            matrix.append(vec)
            ids.append(chunk_id)
            texts.append(text)
            doc_ids.append(doc_id)
            metas.append(json.loads(meta))
            titles.append(title)
            sources.append(source)
            categories.append(cat)

        mat = np.stack(matrix)
        q = query_vec / (np.linalg.norm(query_vec) + 1e-9)
        M = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
        scores = M @ q
        top_idx = np.argsort(scores)[::-1][:top_k]

        return [
            {
                "chunk_id": ids[i], "doc_id": doc_ids[i],
                "title": titles[i], "source": sources[i],
                "category": categories[i], "text": texts[i],
                "score": float(scores[i]), "meta": metas[i],
            }
            for i in top_idx
        ]

    # ------------------------------------------------------------------ #
    #  Full-text search (BM25 via FTS5)                                   #
    # ------------------------------------------------------------------ #
    def fulltext_search(self, query: str, top_k: int = 5,
                        category_filter: Optional[str] = None) -> List[dict]:
        sql = """
            SELECT f.chunk_id, c.doc_id, c.text, c.meta,
                   d.title, d.source, d.category,
                   f.rank
            FROM chunks_fts f
            JOIN chunks c ON c.id = f.chunk_id
            JOIN documents d ON d.id = c.doc_id
            WHERE chunks_fts MATCH ?
        """
        params: list = [query]
        if category_filter:
            sql += " AND d.category = ?"
            params.append(category_filter)
        sql += " ORDER BY f.rank LIMIT ?"
        params.append(top_k)

        try:
            rows = self._conn.execute(sql, params).fetchall()
        except Exception:
            safe = ' '.join('"' + w + '"' for w in query.split())
            params[0] = safe
            try:
                rows = self._conn.execute(sql, params).fetchall()
            except Exception:
                rows = []
        return [
            {
                "chunk_id": r[0], "doc_id": r[1], "text": r[2],
                "meta": json.loads(r[3]), "title": r[4],
                "source": r[5], "category": r[6], "score": -r[7]
            }
            for r in rows
        ]

    # ------------------------------------------------------------------ #
    #  Hybrid RRF fusion                                                   #
    # ------------------------------------------------------------------ #
    def hybrid_search(self, query: str, query_vec: np.ndarray,
                      top_k: int = 5, alpha: float = 0.7,
                      category_filter: Optional[str] = None) -> List[dict]:
        """Reciprocal Rank Fusion of vector + BM25 results."""
        vec_hits = self.vector_search(query_vec, top_k * 2, category_filter)
        fts_hits = self.fulltext_search(query, top_k * 2, category_filter)

        rrf_k = 60
        scores: dict[str, float] = {}
        meta_map: dict[str, dict] = {}

        for rank, hit in enumerate(vec_hits):
            cid = hit["chunk_id"]
            scores[cid] = scores.get(cid, 0) + alpha / (rrf_k + rank + 1)
            meta_map[cid] = hit

        for rank, hit in enumerate(fts_hits):
            cid = hit["chunk_id"]
            scores[cid] = scores.get(cid, 0) + (1 - alpha) / (rrf_k + rank + 1)
            if cid not in meta_map:
                meta_map[cid] = hit

        sorted_ids = sorted(scores, key=lambda x: scores[x], reverse=True)[:top_k]
        results = []
        for cid in sorted_ids:
            hit = meta_map[cid].copy()
            hit["rrf_score"] = scores[cid]
            results.append(hit)
        return results

    # ------------------------------------------------------------------ #
    #  Stats                                                              #
    # ------------------------------------------------------------------ #
    def stats(self) -> dict:
        c = self._conn
        n_docs = c.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        n_chunks = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        n_vecs = c.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
        return {"documents": n_docs, "chunks": n_chunks, "vectors": n_vecs}

    def close(self):
        self._conn.close()
