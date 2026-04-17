"""内部历史复盘查询工具实现。"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger("stock_recap.infrastructure.tools.history")


def run_query_history(db_path: str, mode: str, limit: int = 5) -> str:
    """查询内部历史复盘记录。"""
    try:
        from stock_recap.infrastructure.persistence.db import load_history

        rows = load_history(db_path, limit=limit)
        filtered = [r for r in rows if r.get("mode") == mode]
        return json.dumps(filtered, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("query_history 失败: %s", e)
        return f"查询失败: {e}"
