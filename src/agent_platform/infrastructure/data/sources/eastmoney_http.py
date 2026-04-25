"""统一的东方财富 push2/push2ex 调用封装：共享 client + host 排序 + 节流 + 退避。

资深视角：东方财富的 push2/push2ex 在批量请求时极容易触发 server 主动断连
（"Server disconnected without sending a response"）。解决方法：
  1. 全局共享 httpx.Client，复用 TCP 连接（HTTP/2 + keep-alive）；
  2. 全局节流：相邻请求 ≥ MIN_INTERVAL_S，避免短时打满；
  3. 智能 host 排序：上一次成功的 host 优先（粘性）；
  4. 不在所有 host 间无脑重试 — 单 host 单 attempt 就降级到下一个；
  5. 节假日 / 非交易日 push2ex pool 必空，不要无限重试。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Iterable, List, Optional

import httpx

logger = logging.getLogger("agent_platform.sources.eastmoney")

_DEFAULT_HOSTS: List[str] = ["1", "17", "19", "29", "79", "100"]
_MIN_INTERVAL_S = 0.35  # 全局相邻请求最短间隔
_TIMEOUT_S = 8.0

_lock = threading.Lock()
_last_call_at: float = 0.0
_client: Optional[httpx.Client] = None
_sticky_host: Optional[str] = None  # 上次成功 host，下次优先尝试


def _get_client() -> httpx.Client:
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                limits = httpx.Limits(max_keepalive_connections=8, max_connections=16)
                _client = httpx.Client(
                    timeout=_TIMEOUT_S,
                    limits=limits,
                    http2=False,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) "
                                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                                       "Chrome/124.0 Safari/537.36",
                        "Referer": "https://quote.eastmoney.com/",
                        "Accept": "*/*",
                        "Connection": "keep-alive",
                    },
                )
    return _client


def _throttle() -> None:
    """全局节流：保证两次请求间隔 ≥ _MIN_INTERVAL_S。"""
    global _last_call_at
    with _lock:
        delta = time.monotonic() - _last_call_at
        if delta < _MIN_INTERVAL_S:
            time.sleep(_MIN_INTERVAL_S - delta)
        _last_call_at = time.monotonic()


def _ordered_hosts(hosts: Iterable[str]) -> List[str]:
    arr = list(hosts)
    if _sticky_host and _sticky_host in arr:
        arr.remove(_sticky_host)
        arr.insert(0, _sticky_host)
    return arr


def _mark_success(host: str) -> None:
    global _sticky_host
    _sticky_host = host


def push2_clist(
    fs: str,
    fields: str,
    *,
    pn: int = 1,
    pz: int = 50,
    fid: str = "f3",
    po: str = "1",
    extra_params: Optional[Dict[str, Any]] = None,
    hosts: Iterable[str] = _DEFAULT_HOSTS,
    timeout: float = _TIMEOUT_S,
    retries_per_host: int = 1,
    sleep_base: float = 0.4,
) -> List[Dict[str, Any]]:
    """调用 https://{host}.push2.eastmoney.com/api/qt/clist/get，返回 diff 列表。"""
    params: Dict[str, Any] = {
        "pn": str(pn),
        "pz": str(pz),
        "po": po,
        "np": "1",
        "ut": "b2884a393a59ad64002292a3e90d46a5",
        "fltt": "2",
        "invt": "2",
        "fid": fid,
        "fs": fs,
        "fields": fields,
    }
    if extra_params:
        params.update(extra_params)

    client = _get_client()
    last_err: Optional[Exception] = None
    for host in _ordered_hosts(hosts):
        url = f"https://{host}.push2.eastmoney.com/api/qt/clist/get"
        for attempt in range(retries_per_host):
            _throttle()
            try:
                r = client.get(url, params=params, timeout=timeout)
                r.raise_for_status()
                js = r.json()
                diff = ((js.get("data") or {}).get("diff")) or []
                _mark_success(host)
                return list(diff)
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt + 1 < retries_per_host:
                    time.sleep(sleep_base * (attempt + 1))
        logger.debug("eastmoney_push2 host=%s 全部重试失败：%s", host, last_err)

    logger.warning("eastmoney_push2 全部 host 失败：fs=%s err=%s", fs, last_err)
    return []


def push2ex_zt_pool(date_yyyymmdd: str, *, pagesize: int = 100) -> List[Dict[str, Any]]:
    """涨停板池（push2ex），date 形如 20260423，返回 pool 列表。"""
    url = "https://push2ex.eastmoney.com/getTopicZTPool"
    params = {
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
        "dpt": "wz.ztzt",
        "Pageindex": "0",
        "pagesize": str(pagesize),
        "sort": "fbt:asc",
        "date": date_yyyymmdd,
    }
    client = _get_client()
    last_err: Optional[Exception] = None
    for attempt in range(2):
        _throttle()
        try:
            r = client.get(url, params=params, timeout=_TIMEOUT_S)
            r.raise_for_status()
            pool = ((r.json().get("data") or {}).get("pool")) or []
            return list(pool)
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(0.5 * (attempt + 1))
    logger.warning("push2ex_zt_pool 失败：date=%s err=%s", date_yyyymmdd, last_err)
    return []


def shutdown_client() -> None:
    """供测试与进程退出钩子调用。"""
    global _client
    with _lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:
                pass
            _client = None


__all__ = ["push2_clist", "push2ex_zt_pool", "shutdown_client"]
