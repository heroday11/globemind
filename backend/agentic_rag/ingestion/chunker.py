"""
Text chunking strategies for the ingestion pipeline.
Supports fixed-size, sentence-aware, and semantic chunking.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import List


@dataclass
class Chunk:
    text: str
    doc_id: str
    chunk_index: int
    metadata: dict = field(default_factory=dict)

    @property
    def uid(self) -> str:
        return f"{self.doc_id}::chunk_{self.chunk_index}"


class SentenceAwareChunker:
    """
    Splits text into overlapping sentence-aware chunks.
    Designed to preserve semantic boundaries while keeping
    chunk size manageable for embedding models.
    """

    def __init__(
        self,
        chunk_size: int = 512,
        overlap: int = 64,
        min_chunk_size: int = 64,
    ):
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.min_chunk_size = min_chunk_size

    def _split_sentences(self, text: str) -> List[str]:
        # Split after sentence-final punctuation even when no whitespace follows.
        blocks = re.split(r"\n+", text.strip())
        sentences: List[str] = []
        for block in blocks:
            block = block.strip()
            if not block:
                continue
            parts = re.split(r"(?<=[.!?。！？；;])", block)
            for part in parts:
                part = part.strip()
                if part:
                    sentences.append(part)
        return sentences

    def _split_oversize_sentence(self, sentence: str) -> List[str]:
        if len(sentence) <= self.chunk_size:
            return [sentence]

        for sep in ["；", "，", ";", ",", "：", ":", " "]:
            parts = [p.strip() for p in sentence.split(sep) if p.strip()]
            if len(parts) <= 1:
                continue
            merged: List[str] = []
            current = ""
            for part in parts:
                candidate = f"{current}{sep if current else ''}{part}"
                if len(candidate) <= self.chunk_size:
                    current = candidate
                else:
                    if current:
                        merged.append(current)
                    if len(part) > self.chunk_size:
                        merged.extend(self._split_oversize_sentence(part))
                        current = ""
                    else:
                        current = part
            if current:
                merged.append(current)
            if merged:
                return merged

        step = max(1, self.chunk_size - min(self.overlap, self.chunk_size - 1))
        return [
            sentence[i : i + self.chunk_size].strip()
            for i in range(0, len(sentence), step)
            if sentence[i : i + self.chunk_size].strip()
        ]

    def _joined_length(self, units: List[str]) -> int:
        if not units:
            return 0
        return sum(len(u) for u in units) + max(0, len(units) - 1)

    def _build_overlap_units(self, units: List[str]) -> List[str]:
        if not self.overlap or not units:
            return []
        selected: List[str] = []
        current_len = 0
        for unit in reversed(units):
            add_len = len(unit) + (1 if selected else 0)
            if selected and current_len + add_len > self.overlap:
                break
            if not selected and len(unit) > self.overlap:
                return [unit]
            selected.append(unit)
            current_len += add_len
        return list(reversed(selected))

    def chunk(self, text: str, doc_id: str, metadata: dict | None = None) -> List[Chunk]:
        if not text or not text.strip():
            return []

        sentences: List[str] = []
        for sentence in self._split_sentences(text):
            sentences.extend(self._split_oversize_sentence(sentence))
        chunks: List[Chunk] = []
        current_units: List[str] = []
        current_len = 0
        chunk_idx = 0

        for sentence in sentences:
            s_len = len(sentence)
            if current_len + s_len > self.chunk_size and current_len >= self.min_chunk_size:
                chunk_text = " ".join(current_units)
                chunks.append(Chunk(
                    text=chunk_text,
                    doc_id=doc_id,
                    chunk_index=chunk_idx,
                    metadata=metadata or {}
                ))
                chunk_idx += 1
                current_units = self._build_overlap_units(current_units)
                current_len = self._joined_length(current_units)

            current_units.append(sentence)
            current_len += s_len + 1  # +1 for space

        # Flush remaining
        if current_units:
            remaining = " ".join(current_units).strip()
            if len(remaining) >= self.min_chunk_size or not chunks:
                chunks.append(Chunk(
                    text=remaining,
                    doc_id=doc_id,
                    chunk_index=chunk_idx,
                    metadata=metadata or {}
                ))

        return chunks


class RecursiveCharacterChunker:
    """Fixed-size recursive character splitter (fast, no NLP dependency)."""

    def __init__(self, chunk_size: int = 1000, overlap: int = 200):
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.separators = ["\n\n", "\n", ". ", " ", ""]

    def _split(self, text: str, sep: str) -> List[str]:
        if not sep:
            return list(text)
        return text.split(sep)

    def chunk(self, text: str, doc_id: str, metadata: dict | None = None) -> List[Chunk]:
        chunks: List[Chunk] = []
        raw = self._recursive_split(text)
        for i, c in enumerate(raw):
            chunks.append(Chunk(text=c, doc_id=doc_id, chunk_index=i, metadata=metadata or {}))
        return chunks

    def _recursive_split(self, text: str) -> List[str]:
        if len(text) <= self.chunk_size:
            return [text]
        for sep in self.separators:
            parts = self._split(text, sep)
            if len(parts) > 1:
                break
        result = []
        current = ""
        for part in parts:
            candidate = current + (sep if current else "") + part
            if len(candidate) <= self.chunk_size:
                current = candidate
            else:
                if current:
                    result.append(current)
                if len(part) > self.chunk_size:
                    result.extend(self._recursive_split(part))
                    current = ""
                else:
                    # Start new chunk with overlap
                    overlap_start = max(0, len(current) - self.overlap)
                    current = current[overlap_start:] + (sep if current else "") + part
        if current:
            result.append(current)
        return [r for r in result if r.strip()]
