"""Unit tests for llm/backends.py — no real LLM calls."""
import json
import pytest

from agent_platform.infrastructure.llm.backends import (
    _interpret_model_spec,
    llm_backend_effective,
    model_effective,
    parse_json_from_text,
)
from agent_platform.config.settings import Settings


# ─── _interpret_model_spec ────────────────────────────────────────────────────

@pytest.mark.parametrize("spec,expected_backend,expected_model", [
    ("openai:gpt-4o", "openai", "gpt-4o"),
    ("ollama:qwen2.5", "ollama", "qwen2.5"),
    ("cursor-cli", "cursor-cli", None),
    ("cursor-agent", "cursor-cli", None),
    ("gemini-cli", "gemini-cli", None),
    ("gemini:gemini-2.0-flash", "gemini-cli", "gemini-2.0-flash"),
    ("gpt-4o", None, "gpt-4o"),          # no prefix → no backend inferred
])
def test_interpret_model_spec(spec, expected_backend, expected_model):
    backend, model = _interpret_model_spec(spec)
    assert backend == expected_backend
    assert model == expected_model


# ─── llm_backend_effective ────────────────────────────────────────────────────

def test_backend_from_model_spec():
    assert llm_backend_effective("ollama:qwen2.5") == "ollama"


def test_backend_from_settings_env(monkeypatch):
    monkeypatch.setenv("RECAP_LLM_BACKEND", "gemini-cli")
    s = Settings()
    assert llm_backend_effective(None, s) == "gemini-cli"


def test_backend_model_spec_overrides_settings(monkeypatch):
    monkeypatch.setenv("RECAP_LLM_BACKEND", "gemini-cli")
    s = Settings()
    # explicit model_spec prefix wins
    assert llm_backend_effective("ollama:qwen2.5", s) == "ollama"


def test_backend_legacy_cursor_agent_env_normalizes(monkeypatch):
    monkeypatch.setenv("RECAP_LLM_BACKEND", "cursor-agent")
    s = Settings(_env_file=None)
    assert llm_backend_effective(None, s) == "cursor-cli"


def test_backend_default_openai(monkeypatch):
    monkeypatch.delenv("RECAP_LLM_BACKEND", raising=False)
    s = Settings(_env_file=None)
    assert llm_backend_effective(None, s) == "openai"


# ─── model_effective ──────────────────────────────────────────────────────────

def test_model_from_spec():
    s = Settings()
    assert model_effective(s, "openai:gpt-4o") == "gpt-4o"


def test_model_fallback_to_settings():
    s = Settings()
    assert model_effective(s, None) == s.model


def test_model_cursor_cli_no_model():
    s = Settings()
    assert model_effective(s, "cursor-cli") == s.model
    assert model_effective(s, "cursor-agent") == s.model


# ─── parse_json_from_text ─────────────────────────────────────────────────────

def test_parse_plain_json():
    raw = '{"mode": "daily", "date": "2024-01-02"}'
    result = parse_json_from_text(raw)
    assert result["mode"] == "daily"


def test_parse_markdown_fenced_json():
    raw = '```json\n{"key": "value"}\n```'
    result = parse_json_from_text(raw)
    assert result["key"] == "value"


def test_parse_json_with_leading_text():
    raw = 'Here is the result:\n{"answer": 42}'
    result = parse_json_from_text(raw)
    assert result["answer"] == 42


def test_parse_invalid_raises():
    with pytest.raises(Exception):
        parse_json_from_text("not json at all !!!")
