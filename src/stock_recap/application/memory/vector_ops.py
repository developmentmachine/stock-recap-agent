"""分层记忆中的向量层：写入（index）与召回（query），Qdrant 为默认后端。"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from stock_recap.config.settings import Settings
from stock_recap.domain.models import Features, MarketSnapshot, Mode, Recap, RecapDaily, RecapStrategy
from stock_recap.infrastructure.memory.embeddings_openai import OpenAIEmbeddingProvider
from stock_recap.infrastructure.memory.qdrant_store import QdrantVectorStore

logger = logging.getLogger("stock_recap.memory.vector_ops")


def _tenant_key(tenant_id: Optional[str]) -> str:
    return tenant_id or "default"


def vector_stack_ready(settings: Settings) -> Tuple[bool, Optional[str]]:
    if not (settings.qdrant_url or "").strip():
        return False, "qdrant_disabled_no_url"
    if not (settings.openai_api_key or "").strip():
        return False, "embeddings_disabled_no_openai_key"
    return True, None


def build_embedding_query_text(
    snapshot: MarketSnapshot, features: Features, mode: Mode
) -> str:
    parts: List[str] = [f"date={snapshot.date}", f"mode={mode}"]
    for block in (
        features.index_view,
        features.sector_view,
        features.sentiment_view,
        features.macro_view,
    ):
        b = (block or "").strip()
        if b:
            parts.append(b)
    return "\n".join(parts)


def recall_vector_memory(
    settings: Settings,
    *,
    tenant_id: Optional[str],
    mode: Mode,
    snapshot: MarketSnapshot,
    features: Features,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """返回 (long_memory_blocks, entity_memory_blocks, meta)。"""
    ok, reason = vector_stack_ready(settings)
    meta: Dict[str, Any] = {
        "vector_enabled": ok,
        "reason": reason,
        "long_hits": 0,
        "entity_hits": 0,
    }
    if not ok:
        return [], [], meta

    qtext = build_embedding_query_text(snapshot, features, mode)
    try:
        store = QdrantVectorStore(settings)
        emb = OpenAIEmbeddingProvider(settings)
        (qv,) = emb.embed([qtext])
        tk = _tenant_key(tenant_id)
        long_raw = store.query(
            vector=qv,
            limit=max(1, int(settings.vector_recall_top_k)),
            filter_must={
                "tenant_id": tk,
                "mode": str(mode),
                "memory_kind": "recap_run",
            },
        )
        ent_raw = store.query(
            vector=qv,
            limit=max(1, int(settings.vector_entity_top_k)),
            filter_must={"tenant_id": tk, "memory_kind": "entity_anchor"},
        )
    except Exception as e:
        logger.warning(
            json.dumps(
                {"event": "vector_recall_failed", "error": str(e)},
                ensure_ascii=False,
            )
        )
        meta["vector_enabled"] = False
        meta["reason"] = f"recall_error:{e}"
        return [], [], meta

    long_blocks = [_hit_to_memory_block(h) for h in long_raw]
    ent_blocks = [_hit_to_memory_block(h) for h in ent_raw]
    meta["long_hits"] = len(long_blocks)
    meta["entity_hits"] = len(ent_blocks)
    return long_blocks, ent_blocks, meta


def _hit_to_memory_block(hit: Dict[str, Any]) -> Dict[str, Any]:
    pl = hit.get("payload") or {}
    return {
        "score": hit.get("score"),
        "text": pl.get("text", ""),
        "date": pl.get("date"),
        "mode": pl.get("mode"),
        "request_id": pl.get("request_id"),
        "memory_kind": pl.get("memory_kind"),
        "entity_name": pl.get("entity_name"),
    }


def _recap_to_index_text(recap: Recap) -> str:
    if recap.mode == "daily":
        assert isinstance(recap, RecapDaily)
        lines: List[str] = [f"date={recap.date}", "mode=daily"]
        for s in recap.sections:
            lines.append(f"title={s.title}")
            lines.append(f"conclusion={s.core_conclusion}")
            lines.extend([f"bullet={b}" for b in s.bullets])
        if recap.closing_summary:
            lines.append(f"closing={recap.closing_summary}")
        for n in recap.named_indices:
            lines.append(f"index={n.name}")
        for h in recap.highlighted_sectors:
            lines.append(f"sector={h.name}")
        return "\n".join(lines)
    assert isinstance(recap, RecapStrategy)
    lines = [f"date={recap.date}", "mode=strategy", "mainline:"]
    lines.extend(recap.mainline_focus)
    lines.append("logic:")
    lines.extend(recap.trading_logic)
    return "\n".join(lines)


def index_recap_for_memory(
    settings: Settings,
    *,
    tenant_id: Optional[str],
    request_id: str,
    mode: Mode,
    recap: Recap,
) -> None:
    ok, reason = vector_stack_ready(settings)
    if not ok:
        logger.debug("skip vector index: %s", reason)
        return
    try:
        store = QdrantVectorStore(settings)
        emb = OpenAIEmbeddingProvider(settings)
        main = _recap_to_index_text(recap)
        vec = emb.embed([main])[0]
        tk = _tenant_key(tenant_id)
        rid = f"{tk}:{request_id}:recap"
        points: List[Dict[str, Any]] = [
            {
                "id": rid,
                "vector": vec,
                "payload": {
                    "tenant_id": tk,
                    "memory_kind": "recap_run",
                    "mode": str(mode),
                    "date": recap.date,
                    "request_id": request_id,
                    "text": main[:8000],
                },
            }
        ]
        if recap.mode == "daily":
            assert isinstance(recap, RecapDaily)
            hs = recap.highlighted_sectors or []
            if hs:
                texts = [h.name for h in hs if h.name.strip()]
                if texts:
                    evs = emb.embed(texts)
                    for i, (h, v) in enumerate(zip(hs, evs)):
                        eid = f"{tk}:{request_id}:sec:{i}"
                        points.append(
                            {
                                "id": eid,
                                "vector": v,
                                "payload": {
                                    "tenant_id": tk,
                                    "memory_kind": "entity_anchor",
                                    "entity_type": "sector",
                                    "entity_name": h.name,
                                    "date": recap.date,
                                    "request_id": request_id,
                                    "text": f"{h.name}\nevidence={h.evidence_path}",
                                },
                            }
                        )
        store.upsert(points=points)
    except Exception as e:
        logger.warning(
            json.dumps(
                {"event": "vector_index_failed", "request_id": request_id, "error": str(e)},
                ensure_ascii=False,
            )
        )


__all__ = [
    "build_embedding_query_text",
    "index_recap_for_memory",
    "recall_vector_memory",
    "vector_stack_ready",
]
