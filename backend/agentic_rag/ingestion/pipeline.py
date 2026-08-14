"""
Ingestion pipeline: parse text -> chunk -> embed -> store.

For TB-scale production:
  - Replace file reading with Kafka/Pulsar stream consumer
  - Replace LocalEmbedder with BGE-M3 via Xinference cluster
  - Replace HybridStore with Milvus + PostgreSQL+pgvector
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

from agentic_rag.ingestion.chunker import SentenceAwareChunker
from agentic_rag.ingestion.embedder import get_embedder
from agentic_rag.ingestion.store import HybridStore


class IngestionPipeline:
    def __init__(
        self,
        db_path: str | None = None,
        chunk_size: int = 512,
        overlap: int = 64,
    ):
        db_path = db_path or os.getenv("DB_PATH", "./data/rag.db")
        self.store = HybridStore(db_path=db_path)
        self.chunker = SentenceAwareChunker(chunk_size=chunk_size, overlap=overlap)
        self.embedder = get_embedder()
        print(f"[Pipeline] Store={db_path}, EmbedDim={self.embedder.dim}")

    def ingest_text(
        self,
        text: str,
        doc_id: Optional[str] = None,
        title: str = "Untitled",
        source: str = "manual",
        category: str = "general",
        meta: dict | None = None,
    ) -> dict:
        if doc_id is None:
            doc_id = "doc_" + hashlib.sha256(text.encode()).hexdigest()[:12]

        self.store.upsert_document(
            doc_id=doc_id, title=title, source=source,
            category=category, meta=meta
        )
        chunks = self.chunker.chunk(text, doc_id=doc_id, metadata=meta or {})
        if not chunks:
            return {"doc_id": doc_id, "chunks": 0}

        texts = [c.text for c in chunks]
        vecs = self.embedder.encode(texts)
        self.store.upsert_chunks(chunks, vecs)
        return {"doc_id": doc_id, "chunks": len(chunks)}

    def ingest_file(self, file_path: str, category: str = "file",
                    meta: dict | None = None) -> dict:
        p = Path(file_path)
        if not p.exists():
            raise FileNotFoundError(f"{file_path} not found")
        text = p.read_text(encoding="utf-8", errors="replace")
        doc_id = "file_" + hashlib.md5(str(p.resolve()).encode()).hexdigest()[:12]
        return self.ingest_text(
            text=text, doc_id=doc_id, title=p.name,
            source=str(p.resolve()), category=category, meta=meta,
        )

    def ingest_batch(self, items: List[dict]) -> List[dict]:
        """Batch ingest list of {text, title, source, category, meta}."""
        results = []
        for item in items:
            r = self.ingest_text(
                text=item["text"],
                title=item.get("title", "Untitled"),
                source=item.get("source", "batch"),
                category=item.get("category", "general"),
                meta=item.get("meta"),
            )
            results.append(r)
        return results

    def search(self, query: str, top_k: int = 5,
               mode: str = "hybrid",
               category_filter: Optional[str] = None) -> List[dict]:
        q_vec = self.embedder.encode([query])[0]
        if mode == "vector":
            return self.store.vector_search(q_vec, top_k, category_filter)
        elif mode == "fulltext":
            return self.store.fulltext_search(query, top_k, category_filter)
        else:
            return self.store.hybrid_search(query, q_vec, top_k,
                                             category_filter=category_filter)

    def stats(self) -> dict:
        return self.store.stats()
