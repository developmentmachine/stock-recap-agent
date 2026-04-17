"""Unit tests for settings.py."""
import os
import pytest

from stock_recap.config.settings import Settings, get_settings


def test_defaults(monkeypatch):
    monkeypatch.delenv("RECAP_DB_PATH", raising=False)
    monkeypatch.delenv("RECAP_LLM_BACKEND", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    s = Settings(_env_file=None)
    assert s.model == "gpt-4.1-mini"
    assert s.db_path == "recap_system.db"
    assert s.log_level == "INFO"
    assert s.push_enabled is False
    assert s.scheduler_enabled is False
    assert s.evolution_enabled is True


def test_memory_db_via_env(monkeypatch):
    monkeypatch.setenv("RECAP_DB_PATH", ":memory:")
    s = Settings()
    assert s.db_path == ":memory:"


def test_openai_api_key_via_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
    s = Settings()
    assert s.openai_api_key == "sk-test-123"


def test_gemini_api_key_via_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gm-test-456")
    s = Settings()
    assert s.gemini_api_key == "gm-test-456"


def test_llm_backend_via_env(monkeypatch):
    monkeypatch.setenv("RECAP_LLM_BACKEND", "gemini-cli")
    s = Settings()
    assert s.llm_backend == "gemini-cli"


def test_cursor_cli_cmd_via_primary_env(monkeypatch):
    monkeypatch.setenv("RECAP_CURSOR_CLI_CMD", "agent --verbose")
    s = Settings(_env_file=None)
    assert s.cursor_cli_cmd == "agent --verbose"


def test_cursor_cli_cmd_via_legacy_env(monkeypatch):
    monkeypatch.setenv("RECAP_CURSOR_AGENT_CMD", "/opt/cursor/bin/agent")
    s = Settings(_env_file=None)
    assert s.cursor_cli_cmd == "/opt/cursor/bin/agent"


def test_cursor_cli_cmd_default(monkeypatch):
    monkeypatch.delenv("RECAP_CURSOR_CLI_CMD", raising=False)
    monkeypatch.delenv("RECAP_CURSOR_AGENT_CMD", raising=False)
    s = Settings(_env_file=None)
    assert s.cursor_cli_cmd == "agent"


def test_gemini_cli_cmd_via_env(monkeypatch):
    monkeypatch.setenv("RECAP_GEMINI_CLI_CMD", "gemini")
    s = Settings()
    assert s.gemini_cli_cmd == "gemini"


def test_cors_origins_via_env(monkeypatch):
    monkeypatch.setenv("RECAP_CORS_ORIGINS", "http://localhost:3000, https://a.example")
    s = Settings(_env_file=None)
    assert "localhost:3000" in (s.cors_origins or "")


def test_get_settings_singleton():
    import stock_recap.config.settings as settings_module
    settings_module._settings_instance = None
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2
    settings_module._settings_instance = None
