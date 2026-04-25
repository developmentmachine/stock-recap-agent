"""向量记忆基础设施（Qdrant 默认实现；接口可替换为 pgvector 等）。"""

from stock_recap.infrastructure.memory.protocols import EmbeddingProvider, VectorStore

__all__ = ["EmbeddingProvider", "VectorStore"]
