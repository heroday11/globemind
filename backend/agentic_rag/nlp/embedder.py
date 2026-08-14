"""BGE-M3 嵌入：委托 ingestion.embedder，保持单例行为不变。"""

from agentic_rag.ingestion.embedder import BgeM3Embedder, get_embedder

__all__ = ["BgeM3Embedder", "get_embedder"]
