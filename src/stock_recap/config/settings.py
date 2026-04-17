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


_settings_instance: Optional[Settings] = None


def get_settings() -> Settings:
    """单例，供 FastAPI Depends 使用。"""
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
    return _settings_instance
