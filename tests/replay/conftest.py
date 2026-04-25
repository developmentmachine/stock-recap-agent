"""replay 子目录共享 fixture：注册 ``replay`` backend + 安装 ``ReplayProvider``。"""
from __future__ import annotations

import pytest

from agent_platform.domain.registries import (
    LlmBackendSpec,
    default_backend_registry,
)
from agent_platform.infrastructure.llm.providers import (
    default_provider_registry,
    register_provider,
)

from tests.replay._provider import ReplayProvider


@pytest.fixture
def replay_provider() -> ReplayProvider:
    """注册 ``replay`` 后端 + 实例 provider；测试结束后从注册表移除。"""
    backend_reg = default_backend_registry()
    if backend_reg.get("replay") is None:
        backend_reg.register(
            LlmBackendSpec(
                name="replay",
                display_name="Replay (test)",
                requires_api_key_env=None,
                supports_function_calling=False,
                aliases=("replay",),
            )
        )
    rp = ReplayProvider()
    register_provider("replay", rp)
    yield rp
    # cleanup：避免 provider 实例污染其他测试
    prov_reg = default_provider_registry()
    prov_reg._providers.pop("replay", None)  # noqa: SLF001
    backend_reg._specs.pop("replay", None)  # noqa: SLF001
    backend_reg._alias_to_name.pop("replay", None)  # noqa: SLF001
