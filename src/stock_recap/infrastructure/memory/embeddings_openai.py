"""OpenAI 文本嵌入（企业部署常用；无 key 时由上层跳过向量路径）。"""
from __future__ import annotations

import logging
from typing import List, Sequence

from stock_recap.config.settings import Settings

logger = logging.getLogger("stock_recap.memory.embeddings")


class OpenAIEmbeddingProvider:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        if not texts:
            return []
        key = self._settings.openai_api_key
        if not key:
            raise RuntimeError("OPENAI_API_KEY missing for embeddings")
        try:
            from openai import OpenAI
        except Exception as e:
            raise RuntimeError("openai package required for embeddings") from e

        client = OpenAI(api_key=key)
        model = self._settings.embedding_model
        resp = client.embeddings.create(model=model, input=list(texts))
        # API 保证与 input 顺序一致
        data = sorted(resp.data, key=lambda d: d.index)
        return [list(d.embedding) for d in data]
