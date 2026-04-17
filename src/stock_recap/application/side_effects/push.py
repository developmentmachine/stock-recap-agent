"""推送副作用（通过 ``PushProvider`` 抽象）。

当前只是薄薄的协调层，为后续功能预留位置：
- 接入幂等键（按 ``request_id`` 去重），配合 Wave 2 outbox 表；
- 多通道路由（按租户 / 按场景选择 provider）；
- 失败统计与降级。

管线主路径仍通过 ``get_push_provider(settings).push(recap)`` 直接调用；
此模块暴露一个统一入口，便于日后接入 outbox/幂等不再改动调用点。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from stock_recap.config.settings import Settings
from stock_recap.domain.models import Recap
from stock_recap.infrastructure.push import get_push_provider

logger = logging.getLogger("stock_recap.side_effects.push")


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def push_recap(
    settings: Settings,
    recap: Recap,
    *,
    request_id: Optional[str] = None,
) -> bool:
    """按配置将 ``recap`` 推送到外部通道；未启用或失败时返回 False。"""
    provider = get_push_provider(settings)
    if provider is None:
        return False
    try:
        return bool(provider.push(recap))
    except Exception as e:
        logger.warning(
            _stable_json(
                {
                    "event": "push_failed",
                    "request_id": request_id,
                    "error": str(e),
                }
            )
        )
        return False
