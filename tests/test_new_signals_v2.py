"""第二批资深信号：liquidity / sector_leaders / sector_5d_strength / 路由修复。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from agent_platform.infrastructure.data.sources.liquidity import fetch_liquidity
from agent_platform.infrastructure.data.sources.sector_leaders import (
    _top_strong_industry_names,
    fetch_industry_5d_strength,
    fetch_sector_leaders,
)


# ─── liquidity ────────────────────────────────────────────────────

def test_liquidity_returns_empty_when_ak_none():
    assert fetch_liquidity(None) == {}


def test_liquidity_aggregates_when_partial():
    """即使只有 10Y 国债，liquidity 也应有结构化输出。"""
    df_yield = pd.DataFrame(
        [
            {"日期": "2026-04-23", "10年": 2.30},
            {"日期": "2026-04-24", "10年": 2.27},
        ]
    )

    fake_ak = SimpleNamespace(
        bond_china_yield=lambda: df_yield,
        # 让 SHIBOR 抛错，验证单源失败不影响全局
        rate_interbank=lambda **kw: (_ for _ in ()).throw(RuntimeError("disconnected")),
        bond_repo_zh_summary=lambda: (_ for _ in ()).throw(RuntimeError("disconnected")),
    )
    out = fetch_liquidity(fake_ak)
    assert out, "应至少返回 10Y 国债指标"
    assert out["中国10年国债"]["10Y国债收益率(%)"] == 2.27
    assert out["中国10年国债"]["环比变动(bp)"] == -3.0
    assert "定性" in out


# ─── sector_leaders / sector_5d_strength ───────────────────────────

def test_top_strong_industry_names_extracts_names():
    sector = {
        "涨幅前10": [
            {"板块名称": "半导体", "涨跌幅": 3.4},
            {"板块名称": "电池", "涨跌幅": 2.1},
            {"板块名称": "AI算力", "涨跌幅": 1.8},
            {"板块名称": "白酒", "涨跌幅": -0.5},
        ]
    }
    assert _top_strong_industry_names(sector, n=2) == ["半导体", "电池"]


def test_fetch_sector_leaders_with_fake_ak():
    cons_df = pd.DataFrame(
        [
            {"代码": "300001", "名称": "MockA", "涨跌幅": 9.99},
            {"代码": "300002", "名称": "MockB", "涨跌幅": 8.4},
            {"代码": "300003", "名称": "MockC", "涨跌幅": 6.2},
            {"代码": "300004", "名称": "MockD", "涨跌幅": 4.8},
            {"代码": "300005", "名称": "MockE", "涨跌幅": 3.5},
            {"代码": "300006", "名称": "MockF", "涨跌幅": 1.2},
        ]
    )
    fake_ak = SimpleNamespace(stock_board_industry_cons_em=lambda symbol: cons_df)
    sector = {"涨幅前10": [{"板块名称": "半导体", "涨跌幅": 3.4}]}
    out = fetch_sector_leaders(fake_ak, sector, top_industries=1)
    assert out["强势行业龙头矩阵"][0]["板块"] == "半导体"
    assert len(out["强势行业龙头矩阵"][0]["成分股_top5"]) == 5
    assert out["强势行业龙头矩阵"][0]["板内涨停数"] == 1


def test_fetch_industry_5d_strength_with_fake_ak():
    hist_df = pd.DataFrame(
        [
            {"日期": "2026-04-18", "收盘": 100.0},
            {"日期": "2026-04-21", "收盘": 102.0},
            {"日期": "2026-04-22", "收盘": 104.0},
            {"日期": "2026-04-23", "收盘": 105.0},
            {"日期": "2026-04-24", "收盘": 108.0},
        ]
    )
    fake_ak = SimpleNamespace(
        stock_board_industry_hist_em=lambda symbol, period, adjust: hist_df,
    )
    out = fetch_industry_5d_strength(fake_ak, ["半导体"], days=5)
    assert out["样本"][0]["板块"] == "半导体"
    assert out["样本"][0]["近5日累计涨跌幅(%)"] == 8.0


# ─── extract_market_patterns / check_and_run_evolution 路由修复 ─────

def test_extract_market_patterns_skips_non_openai_backend():
    """gemini-cli backend 时不应触发 openai 调用。"""
    from agent_platform.application.memory.manager import extract_market_patterns

    settings = SimpleNamespace(
        llm_backend="gemini-cli",
        openai_api_key="should-not-be-used",
        model="gpt-4.1-mini",
        gemini_cli_cmd="echo",
        cursor_cli_cmd="echo",
        ollama_base_url="http://localhost:11434",
        gemini_api_key=None,
        pattern_extraction_days=5,
    )
    # 即便有 openai_api_key，因为 backend=gemini-cli，也必须返回 None 并跳过
    result = extract_market_patterns(
        db_path=":memory:",
        days=5,
        settings=settings,
        model_spec="gemini-cli",
    )
    assert result is None


def test_check_and_run_evolution_skips_non_openai_backend():
    from agent_platform.application.memory.manager import check_and_run_evolution

    settings = SimpleNamespace(
        llm_backend="gemini-cli",
        openai_api_key="should-not-be-used",
        model="gpt-4.1-mini",
        gemini_cli_cmd="echo",
        cursor_cli_cmd="echo",
        ollama_base_url="http://localhost:11434",
        gemini_api_key=None,
        evolution_enabled=True,
        evolution_min_runs=10,
    )
    result = check_and_run_evolution(
        db_path=":memory:",
        settings=settings,
        trigger_run_id=None,
        force=True,
        model_spec="gemini-cli",
    )
    assert result is None
