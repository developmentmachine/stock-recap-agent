"""推送 provider 抽象层。

新增推送渠道只需：
1. 继承 PushProvider
2. 实现 push() 和 test() 方法
3. 在 get_push_provider() 中注册
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from agent_platform.domain.models import Recap


class PushProvider(ABC):
    @abstractmethod
    def push(self, recap: Recap) -> bool:
        """推送复盘内容，返回 True 表示成功。"""
        ...

    @abstractmethod
    def test(self) -> bool:
        """发送测试消息，验证配置是否正确。"""
        ...


def get_push_provider(settings: object) -> "PushProvider | None":
    """根据 settings 返回合适的 push provider，未配置时返回 None。"""
    from agent_platform.config.settings import Settings
    s: Settings = settings  # type: ignore[assignment]

    if s.push_enabled and s.wxwork_webhook_url:
        from agent_platform.infrastructure.push.wechat import WechatWorkProvider
        return WechatWorkProvider(
            webhook_url=s.wxwork_webhook_url,
            fallback_text=s.push_fallback_text,
        )

    return None
