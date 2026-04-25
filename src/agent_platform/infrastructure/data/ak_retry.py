"""AkShare / 东财 HTTP 偶发断连时的有限次重试。"""
from __future__ import annotations

import logging
from typing import Callable, TypeVar

from tenacity import retry, stop_after_attempt, wait_exponential
from tenacity.retry import retry_if_exception

logger = logging.getLogger("agent_platform.data.ak_retry")

T = TypeVar("T")


def _transient(exc: BaseException) -> bool:
    s = f"{type(exc).__name__}:{exc}".lower()
    keys = (
        "remote",
        "connection",
        "timeout",
        "reset",
        "refused",
        "broken",
        "disconnected",
        "temporarily",
        "502",
        "503",
        "504",
    )
    return any(k in s for k in keys)


def ak_call(fn: Callable[[], T], *, label: str = "") -> T:
    """对无参工厂函数执行带抖动的重试（成功即返回，最后一次异常向上抛）。"""

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=0.35, min=0.5, max=10),
        retry=retry_if_exception(_transient),
        reraise=True,
    )
    def _run() -> T:
        return fn()

    try:
        return _run()
    except Exception as e:
        logger.warning("ak_call exhausted label=%s err=%s", label, e)
        raise
