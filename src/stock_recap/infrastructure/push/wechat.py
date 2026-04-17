"""企业微信推送层。

支持：
- 企业微信群机器人 Webhook（Markdown 消息 / Text 消息）
- 自动降级：Markdown 失败时降级为纯文本
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx

from stock_recap.domain.models import Recap
from stock_recap.infrastructure.push import PushProvider
from stock_recap.presentation.render.renderers import render_markdown_for_wechat_work, render_wechat_text

logger = logging.getLogger("stock_recap.infrastructure.push.wechat")


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class WechatWorkProvider(PushProvider):
    """企业微信群机器人推送 provider。"""

    def __init__(self, webhook_url: str, fallback_text: bool = True, timeout: int = 10):
        self.webhook_url = webhook_url
        self.fallback_text = fallback_text
        self.timeout = timeout

    def push(self, recap: Recap) -> bool:
        return push_wechat_work(
            self.webhook_url,
            recap,
            fallback_text=self.fallback_text,
            timeout=self.timeout,
        )

    def test(self) -> bool:
        return test_push(self.webhook_url)


def push_wechat_work(
    webhook_url: str,
    recap: Recap,
    fallback_text: bool = True,
    timeout: int = 10,
) -> bool:
    """
    向企业微信群机器人推送复盘报告。

    Args:
        webhook_url: 企业微信群机器人 Webhook URL
        recap: 已生成的复盘对象
        fallback_text: 若 Markdown 推送失败，是否降级为纯文本
        timeout: HTTP 超时秒数

    Returns:
        True 推送成功，False 推送失败
    """
    md_content = render_markdown_for_wechat_work(recap)

    # 优先：Markdown 消息
    if _send_wechat_markdown(webhook_url, md_content, timeout=timeout):
        logger.info(_stable_json({"event": "push_success", "type": "markdown"}))
        return True

    if not fallback_text:
        return False

    # 降级：纯文本消息
    text_content = render_wechat_text(recap)
    if _send_wechat_text(webhook_url, text_content, timeout=timeout):
        logger.info(_stable_json({"event": "push_success", "type": "text_fallback"}))
        return True

    logger.warning(_stable_json({"event": "push_failed", "webhook": webhook_url[:40]}))
    return False


def _send_wechat_markdown(url: str, content: str, timeout: int = 10) -> bool:
    payload = {
        "msgtype": "markdown",
        "markdown": {"content": content},
    }
    return _post(url, payload, timeout)


def _send_wechat_text(url: str, content: str, timeout: int = 10) -> bool:
    payload = {
        "msgtype": "text",
        "text": {"content": content},
    }
    return _post(url, payload, timeout)


def _post(url: str, payload: dict, timeout: int) -> bool:
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(url, json=payload)
            r.raise_for_status()
            data = r.json()
            ok = data.get("errcode") == 0
            if not ok:
                logger.warning(
                    _stable_json({"event": "wechat_api_error", "resp": data})
                )
            return ok
    except Exception as e:
        logger.warning(_stable_json({"event": "wechat_http_error", "error": str(e)}))
        return False


def test_push(webhook_url: str) -> bool:
    """发送一条测试消息验证 Webhook 配置。"""
    payload = {
        "msgtype": "text",
        "text": {"content": "[stock-recap] Webhook 推送测试 - 配置正常"},
    }
    result = _post(webhook_url, payload, timeout=10)
    if result:
        logger.info(_stable_json({"event": "push_test_success"}))
    else:
        logger.error(_stable_json({"event": "push_test_failed", "url": webhook_url[:40]}))
    return result
