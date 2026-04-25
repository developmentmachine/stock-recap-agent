"""数据源抽象层：Strategy Pattern + Chain of Responsibility。

使用方式：
    fetcher = DataFetcher([TencentIndexSource(), EastMoneyIndexSource()])
    indices = fetcher.fetch()  # 自动 fallback，失败返回 {}
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, runtime_checkable

from typing import Protocol

logger = logging.getLogger("agent_platform.sources")


@runtime_checkable
class DataSource(Protocol):
    """数据源协议：所有 source 实现此接口。"""

    @property
    def name(self) -> str:
        """数据源标识，用于日志。"""
        ...

    def fetch(self) -> Dict[str, Any]:
        """
        拉取数据。
        - 成功：返回非空 dict
        - 失败：返回 {}，不抛异常
        """
        ...


class DataFetcher:
    """
    责任链执行器：按优先级尝试 sources，第一个非空结果返回。

    Args:
        sources: DataSource 列表，按优先级排列（高优先级在前）
        label:   日志标识，便于追踪哪个 fetcher 失败
    """

    def __init__(self, sources: List[DataSource], label: str = "") -> None:
        self.sources = sources
        self.label = label

    def fetch(self) -> Dict[str, Any]:
        for source in self.sources:
            try:
                result = source.fetch()
                if result:
                    logger.debug(
                        json.dumps({"event": "source_ok", "fetcher": self.label, "source": source.name},
                                   ensure_ascii=False)
                    )
                    return result
            except Exception as e:
                logger.warning(
                    json.dumps({"event": "source_failed", "fetcher": self.label,
                                "source": source.name, "error": str(e)},
                               ensure_ascii=False)
                )
        if self.sources:
            logger.warning(
                json.dumps({"event": "all_sources_failed", "fetcher": self.label},
                           ensure_ascii=False)
            )
        return {}
