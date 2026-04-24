"""全局配置：所有环境变量均以 RECAP_ 为前缀。

支持 .env 文件加载（放在工作目录下）。

使用示例：
    export OPENAI_API_KEY=sk-...
    export RECAP_WXWORK_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...
    export RECAP_SCHEDULER_ENABLED=true
"""
from __future__ import annotations

import os
from typing import Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM
    openai_api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")
    gemini_api_key: Optional[str] = Field(default=None, alias="GEMINI_API_KEY")
    llm_backend: Optional[str] = Field(
        default=None,
        alias="RECAP_LLM_BACKEND",
        description="强制指定后端：openai / ollama / cursor-cli / gemini-cli（兼容旧值 cursor-agent）",
    )
    model: str = Field(default="gpt-4.1-mini", alias="RECAP_MODEL")
    temperature: float = Field(default=0.2, alias="RECAP_TEMPERATURE")
    timeout_s: int = Field(default=60, alias="RECAP_TIMEOUT_S")
    cursor_timeout_s: int = Field(default=300, alias="RECAP_CURSOR_TIMEOUT_S")
    gemini_timeout_s: int = Field(default=300, alias="RECAP_GEMINI_TIMEOUT_S")
    ollama_base_url: str = Field(
        default="http://127.0.0.1:11434", alias="RECAP_OLLAMA_BASE_URL"
    )
    cursor_cli_cmd: str = Field(
        default="agent",
        validation_alias=AliasChoices("RECAP_CURSOR_CLI_CMD", "RECAP_CURSOR_AGENT_CMD"),
        description="Cursor CLI 可执行及其参数前缀（官方 CLI 命令为 agent），默认 agent",
    )
    gemini_cli_cmd: str = Field(
        default="gemini", alias="RECAP_GEMINI_CLI_CMD"
    )

    # 存储
    db_path: str = Field(default="recap_system.db", alias="RECAP_DB_PATH")

    # API 安全
    recap_api_key: Optional[str] = Field(default=None, alias="RECAP_API_KEY")
    rate_limit_rpm: int = Field(default=10, alias="RECAP_RATE_LIMIT_RPM")
    cors_origins: Optional[str] = Field(
        default=None,
        alias="RECAP_CORS_ORIGINS",
        description="逗号分隔的浏览器 Origin，非空时启用 CORS（如 https://app.example.com,http://localhost:5173）",
    )

    # 推送
    wxwork_webhook_url: Optional[str] = Field(
        default=None, alias="RECAP_WXWORK_WEBHOOK_URL"
    )
    push_enabled: bool = Field(default=False, alias="RECAP_PUSH_ENABLED")
    push_fallback_text: bool = Field(
        default=True,
        alias="RECAP_PUSH_FALLBACK_TEXT",
        description="推送失败时是否降级为纯文本",
    )

    # 调度
    scheduler_enabled: bool = Field(default=False, alias="RECAP_SCHEDULER_ENABLED")
    scheduler_daily_hour: int = Field(default=15, alias="RECAP_SCHEDULER_DAILY_HOUR")
    scheduler_daily_minute: int = Field(
        default=30, alias="RECAP_SCHEDULER_DAILY_MINUTE"
    )
    scheduler_strategy_minute: int = Field(
        default=35, alias="RECAP_SCHEDULER_STRATEGY_MINUTE"
    )
    scheduler_backtest_minute: int = Field(
        default=40, alias="RECAP_SCHEDULER_BACKTEST_MINUTE"
    )
    output_dir: str = Field(default=".", alias="RECAP_OUTPUT_DIR")

    # 记忆与进化
    max_history_for_context: int = Field(
        default=5, alias="RECAP_MAX_HISTORY_FOR_CONTEXT"
    )
    evolution_enabled: bool = Field(default=True, alias="RECAP_EVOLUTION_ENABLED")
    evolution_min_runs: int = Field(default=5, alias="RECAP_EVOLUTION_MIN_RUNS")
    pattern_extraction_days: int = Field(
        default=10, alias="RECAP_PATTERN_EXTRACTION_DAYS"
    )
    skill_id_override: Optional[str] = Field(
        default=None,
        alias="RECAP_SKILL_ID",
        description="覆盖 manifest 的 mode→skill 映射，用于 A/B 或临时切 skill",
    )

    # 观测
    log_level: str = Field(default="INFO", alias="RECAP_LOG_LEVEL")
    otel_enabled: bool = Field(default=False, alias="RECAP_OTEL_ENABLED")
    otel_exporter: str = Field(
        default="none",
        alias="RECAP_OTEL_EXPORTER",
        description="none | console | otlp（otlp 需 OTEL_EXPORTER_OTLP_ENDPOINT）",
    )
    otel_service_name: str = Field(default="stock-recap", alias="RECAP_OTEL_SERVICE_NAME")
    otel_otlp_endpoint: Optional[str] = Field(
        default=None,
        alias="RECAP_OTEL_OTLP_ENDPOINT",
        description="可选；未设时使用环境变量 OTEL_EXPORTER_OTLP_ENDPOINT",
    )

    # LLM function calling（进程内 OpenAI/Ollama 工具循环；独立 MCP 见 stock_recap.interfaces.mcp_stdio）
    tools_enabled: bool = Field(default=False, alias="RECAP_TOOLS_ENABLED")
    tools_web_search: bool = Field(default=True, alias="RECAP_TOOLS_WEB_SEARCH")
    tools_market_data: bool = Field(default=True, alias="RECAP_TOOLS_MARKET_DATA")
    tools_history: bool = Field(default=True, alias="RECAP_TOOLS_HISTORY")

    # 工具治理（per-tool policy / 审计）
    tool_audit_enabled: bool = Field(
        default=True,
        alias="RECAP_TOOL_AUDIT_ENABLED",
        description="是否将每次工具调用（成功/失败/拒绝/超时）落库 tool_invocations 表",
    )
    principal_role: str = Field(
        default="user",
        alias="RECAP_PRINCIPAL_ROLE",
        description=(
            "当前进程默认 principal 角色（guest|user|operator|admin），"
            "用于 ToolPolicy.required_role 的 RBAC 校验；Wave 5 接入 PrincipalContext 后将被请求级 ctx 覆盖。"
        ),
    )

    # Agent 单次运行预算（超限抛 LlmBudgetExceeded，立即中止；不会被 tenacity 重试）
    agent_max_tool_calls: int = Field(
        default=8,
        alias="RECAP_AGENT_MAX_TOOL_CALLS",
        description="单次 generate 内允许的工具调用累计次数上限",
    )
    agent_max_tokens: int = Field(
        default=50_000,
        alias="RECAP_AGENT_MAX_TOKENS",
        description="单次 generate 内累计 token（input+output）上限；0 表示不限制",
    )
    agent_max_wall_ms: int = Field(
        default=180_000,
        alias="RECAP_AGENT_MAX_WALL_MS",
        description="单次 generate 的墙钟超时（毫秒）；0 表示不限制",
    )
    agent_critic_max_retries: int = Field(
        default=1,
        alias="RECAP_AGENT_CRITIC_MAX_RETRIES",
        description=(
            "Critic 重入次数：业务异常（schema/parse 校验失败）发生时，把结构化反馈塞回 "
            "messages 再调一次 LLM。设 0 关闭。"
        ),
    )

    # Outbox（pending_actions）周期 sweep 间隔（秒）；最小 15s。
    outbox_sweep_interval_seconds: int = Field(
        default=60,
        alias="RECAP_OUTBOX_SWEEP_INTERVAL_S",
        description=(
            "调度器周期消费 outbox（pending_actions）任务的间隔。"
            "BackgroundTasks 在线消费失败的任务会按指数退避在这里被兜底重试。"
        ),
    )


_settings_instance: Optional[Settings] = None


def get_settings() -> Settings:
    """单例，供 FastAPI Depends 使用。"""
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
    return _settings_instance
