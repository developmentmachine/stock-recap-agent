# 扩展新 Agent 指导手册

本文档面向需要在 `agent_platform` 基础上**新增一个业务 Agent** 的开发者。

---

## 一、理解平台分层

```
src/agent_platform/
│
├── ── 通用平台层（所有 Agent 共享，通常不需要修改） ─────────────────────
│   ├── infrastructure/llm/          LLM 调用、多 backend 适配、tool runner
│   ├── infrastructure/memory/       向量存储、embedding
│   ├── infrastructure/push/         推送（微信等）
│   ├── application/orchestration/   编排引擎（pipeline、context、token budget）
│   ├── application/memory/          进化记忆管理
│   ├── observability/               tracing、metrics、structured logging
│   ├── policy/                      guardrails、output_rules
│   └── config/settings.py           全局配置（环境变量驱动）
│
├── ── 平台 CLI 分发器 ────────────────────────────────────────────────
│   └── interfaces/cli.py            读取 AGENTS 字典，将子命令分发到各 agent
│
└── ── 业务层（每个 Agent 各自拥有） ────────────────────────────────────
    ├── domain/<agent>.py            领域模型（输入/输出结构、业务规则）
    ├── infrastructure/data/         业务数据源采集
    ├── application/<agent>.py       主用例编排（调用平台层 pipeline）
    ├── skills/<agent>/              Skill 描述（SKILL.md）
    ├── resources/prompts/           System Prompt 文件
    ├── interfaces/agents/<agent>_cli.py   CLI 子命令（实现 register_subparser / run）
    └── interfaces/api/v1/           HTTP 路由（可选）
```

**原则：新 Agent 只在业务层新增文件 + 在 `interfaces/cli.py` 的 `AGENTS` 字典追加一行注册，
不修改通用平台层。**

---

## 二、需要新增的文件清单

最小化实现一个新 Agent 需要以下 7 步：

| 步骤 | 位置 | 说明 |
|------|------|------|
| 1 | `skills/<my_agent>/SKILL.md` | 描述 Agent 能力、输入输出格式 |
| 2 | `domain/my_agent.py` | 定义输入/输出的 Pydantic 模型 |
| 3 | `infrastructure/data/sources/my_source.py` | 数据采集（若有新数据源）|
| 4 | `resources/prompts/system_my_agent.md` | System Prompt |
| 5 | `application/my_agent.py` | 主用例：串联数据→prompt→LLM→输出 |
| 6 | `interfaces/agents/my_agent_cli.py` | CLI 子命令模块（register_subparser + run）|
| 7 | `interfaces/api/v1/my_agent.py` | 增加 HTTP 路由（可选）|

外加在 `interfaces/cli.py` 的 `AGENTS` 字典中追加一行注册。

---

## 三、分步实现指南

### 步骤 1：写 SKILL.md

路径：`src/agent_platform/skills/<my_agent>/SKILL.md`

SKILL 是 Agent 的"说明书"，告诉 LLM 这个 Agent 的职责和 I/O 格式。参考现有示例：

```
src/agent_platform/skills/a_share_daily_recap/SKILL.md
src/agent_platform/skills/a_share_strategy_nextday/SKILL.md
```

最低限度需要包含：
- **功能描述**：这个 Agent 做什么
- **输入**：调用时会提供哪些数据
- **输出格式**：期望 LLM 返回什么结构

Skill 通过 `skills/loader.py` 按 `mode` 自动加载注入到 prompt 中。在 `skills/manifest.json` 中登记即可被自动发现：

```json
{
  "skills": [
    { "mode": "my_agent", "path": "my_agent/SKILL.md" }
  ]
}
```

> 一个 agent 可以有多个 mode（例如 `stock-recap` 就有 `daily` 和 `strategy` 两个 mode）。
> mode 是「同一个 agent 内不同任务变体」的概念，agent 是「不同业务智能体」的概念。

---

### 步骤 2：定义领域模型

路径：`src/agent_platform/domain/my_agent.py`

```python
from pydantic import BaseModel, Field

class MyAgentInput(BaseModel):
    """传给 Agent 的输入数据"""
    topic: str
    extra_context: str = ""

class MyAgentOutput(BaseModel):
    """Agent 生成的输出"""
    summary: str
    recommendations: list[str] = Field(default_factory=list)
```

使用 Pydantic 模型而非 dict，便于类型检查和序列化。

---

### 步骤 3：实现数据采集（可选）

路径：`src/agent_platform/infrastructure/data/sources/my_source.py`

若新 Agent 需要外部数据，在此实现采集逻辑。实现 `domain/data_providers.py` 中定义的协议接口，以便可以注入 mock 数据用于测试：

```python
from agent_platform.domain.data_providers import SomeDataProtocol

class MyDataSource:
    def fetch(self) -> dict:
        ...
```

---

### 步骤 4：写 System Prompt

路径：`src/agent_platform/resources/prompts/system_my_agent.md`

用 Markdown 写 System Prompt，支持模板变量（`{{ variable }}`）。在 `resources/prompts/manifest.json` 中登记：

```json
{
  "prompts": [
    { "mode": "my_agent", "system": "system_my_agent.md" }
  ]
}
```

---

### 步骤 5：实现主用例

路径：`src/agent_platform/application/my_agent.py`

这里串联数据采集 → 构建 prompt → 调用 LLM → 处理输出。**直接复用平台层的 pipeline 和工具**，不需要重新实现：

```python
from agent_platform.application.orchestration.pipeline import run_pipeline
from agent_platform.application.orchestration.context import RunContext
from agent_platform.infrastructure.llm.prompts import build_messages
from agent_platform.config.settings import Settings

async def run_my_agent(settings: Settings, mode: str = "my_agent") -> str:
    data = MyDataSource().fetch()

    ctx = RunContext(mode=mode, settings=settings)
    messages = build_messages(ctx, data=data)

    result = await run_pipeline(ctx, messages)

    return result.content
```

---

### 步骤 6：实现 CLI 子命令模块

路径：`src/agent_platform/interfaces/agents/my_agent_cli.py`

每个 agent 在 `interfaces/agents/` 下有自己独立的 CLI 模块，**只需实现两个函数**：

```python
"""我的 Agent — 一句话描述（会被父 parser 当作 help 文本）"""
from __future__ import annotations

import argparse
import asyncio

from agent_platform.application.my_agent import run_my_agent
from agent_platform.config.settings import Settings


def register_subparser(sub: argparse.ArgumentParser) -> None:
    """向平台分发器注册该 agent 的所有 argparse 参数。"""
    sub.add_argument("--topic", required=True, help="本次任务主题")
    sub.add_argument("--provider", default="mock", choices=["mock", "live"])
    sub.add_argument("--no-llm", action="store_true")


def run(
    args: argparse.Namespace,
    settings: Settings,
    parser: argparse.ArgumentParser,
) -> int:
    """执行 agent，返回进程 exit code。"""
    result = asyncio.run(run_my_agent(settings))
    print(result)
    return 0
```

参考完整示例：[interfaces/agents/stock_recap_cli.py](../src/agent_platform/interfaces/agents/stock_recap_cli.py)

接着在平台分发器 [interfaces/cli.py](../src/agent_platform/interfaces/cli.py) 的 `AGENTS` 字典里追加一行：

```python
from agent_platform.interfaces.agents import my_agent_cli, stock_recap_cli

AGENTS: dict[str, Any] = {
    "stock-recap": stock_recap_cli,
    "my-agent": my_agent_cli,   # ← 新增
}
```

调用方式：

```bash
uv run agent_platform my-agent --topic "本周科技行情" --provider mock
uv run agent_platform my-agent --help
```

---

### 步骤 7：添加 HTTP 路由（可选）

路径：`src/agent_platform/interfaces/api/v1/my_agent.py`

```python
from fastapi import APIRouter, Depends
from agent_platform.application.my_agent import run_my_agent
from agent_platform.interfaces.api.deps import get_settings

router = APIRouter(prefix="/v1/my-agent", tags=["my-agent"])

@router.post("/run")
async def run(settings=Depends(get_settings)):
    result = await run_my_agent(settings)
    return {"result": result}
```

在 `interfaces/api/routes.py` 中注册这个 router 即可。

---

## 四、测试策略

新 Agent 的测试跟着同样的分层：

| 测试类型 | 建议路径 | 要点 |
|----------|----------|------|
| 单元测试 | `tests/test_my_agent.py` | 用 `--provider mock` 或直接 monkeypatch 数据源 |
| Prompt 测试 | `tests/test_my_agent_prompt.py` | 验证 `build_messages()` 输出的内容和结构 |
| CLI 测试 | `tests/test_my_agent_cli.py` | `register_subparser` 注册的参数符合预期；`run()` 在 mock provider 下能正常返回 0 |
| 集成测试 | `tests/test_my_agent_integration.py` | 用 `provider=mock` 跑完整 pipeline |

参考现有测试：`tests/test_recap_audit.py`、`tests/test_prompt_experiments.py`。

---

## 五、注意事项

1. **不要修改通用平台层**。如果发现通用层有不满足需求之处，优先考虑通过**依赖注入**或**协议扩展**解决，而不是直接修改。

2. **agent 名遵循 kebab-case**。CLI 子命令名建议短横线分隔（如 `stock-recap`、`news-digest`），与 `pyproject.toml` 风格一致；Python 模块名仍用下划线（`stock_recap_cli.py`）。

3. **配置项用新的环境变量前缀**。在 `config/settings.py` 里为新 Agent 的专属配置加前缀（如 `MY_AGENT_XXX`），避免与现有配置污染。

4. **Skill 是首要设计文档**。先写好 `SKILL.md`，描述清楚输入输出，再写代码，顺序不要反。

5. **用 mock provider 先跑通**。在接入真实数据源前，用 `--provider mock` 验证整个流程是否畅通。

6. **遵循领域边界**。新 Agent 的 domain 模型不要依赖其他 Agent 的 domain 模型；共用结构放到 `domain/shared.py` 或提取到更通用的名称。

7. **`--mcp-tools` 是平台级能力**。它在 `interfaces/cli.py` 顶层，不在任何 agent 子命令下；新 agent 要暴露 MCP 工具时通过 `infrastructure/tools/` 注册即可。

---

## 六、完整文件变动对照表

以下是新增一个名为 `news-digest`（新闻摘要）Agent 的完整文件清单示例：

```
新增文件（8 个）：
  src/agent_platform/skills/news_digest/SKILL.md
  src/agent_platform/domain/news_digest.py
  src/agent_platform/infrastructure/data/sources/news_feed.py
  src/agent_platform/resources/prompts/system_news_digest.md
  src/agent_platform/application/news_digest.py
  src/agent_platform/interfaces/agents/news_digest_cli.py
  src/agent_platform/interfaces/api/v1/news_digest.py
  tests/test_news_digest.py

修改文件（3 个）：
  src/agent_platform/skills/manifest.json             (+1 行)
  src/agent_platform/resources/prompts/manifest.json  (+1 行)
  src/agent_platform/interfaces/cli.py                (+2 行：import + AGENTS 字典追加一行)
```

**没有任何文件需要被复制或删除。**
