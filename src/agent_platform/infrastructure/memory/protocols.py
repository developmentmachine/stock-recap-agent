"""记忆向量层抽象：与具体向量库（Qdrant / 未来 pgvector）解耦。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, Sequence, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """文本 → 稠密向量。"""

    def embed(self, texts: Sequence[str]) -> List[List[float]]: ...


@runtime_checkable
class VectorStore(Protocol):
    """向量写入与相似度检索（带 metadata 过滤）。"""

    def ensure_collection(self, *, vector_size: int) -> None: ...

    def upsert(
        self,
        *,
        points: List[Dict[str, Any]],
    ) -> None:
        """points: [{\"id\": str, \"vector\": list[float], \"payload\": dict}, ...]"""

    def query(
        self,
        *,
        vector: Sequence[float],
        limit: int,
        filter_must: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """返回 [{\"id\", \"score\", \"payload\"}, ...]"""


__all__ = ["EmbeddingProvider", "VectorStore"]
