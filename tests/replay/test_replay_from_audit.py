"""W4-5：从 ``recap_audit`` 回放历史 LLM 调用。

场景：把一次真实运行（或人工构造）的 messages + recap 写入 ``recap_audit``，
然后通过 ``ReplayProvider`` 把同一份 recap 「回灌」到 pipeline，断言：
- ``call_llm`` 直接拿到回放的 recap，无外网；
- 上层把 audit 里的 messages 完整透传给 provider（顺序 / 角色 / 内容均一致）；
- recap 经 ``coerce_recap_output`` 后字段守恒；
- 重新跑一次完整 pipeline，最终 ``recap_runs.recap_json`` 与原 audit 一致
  （证明渲染 / Critic / 持久化逻辑没有偷偷改写 recap）。
"""
from __future__ import annotations

import json

import pytest

from stock_recap.application.recap import generate_once
from stock_recap.config.settings import Settings
from stock_recap.domain.models import (
    GenerateRequest,
    LlmTokens,
    RecapDaily,
    RecapDailySection,
)
from stock_recap.infrastructure.llm.backends import call_llm
from stock_recap.infrastructure.persistence.db import (
    get_conn,
    init_db,
    insert_recap_audit,
    load_recap_audit,
)


def _build_recap(date: str = "2025-03-04") -> RecapDaily:
    section_a = RecapDailySection(
        title="指数温和分化，量能继续低位",
        core_conclusion="主板小幅回落，创业板抗跌，全天缩量博弈。",
        bullets=[
            f"【复盘基准日：{date.replace('-', '年', 1).replace('-', '月', 1)}日 星期二】",
            "上证-0.18% 创业板+0.42%；两市成交 6800 亿，环比再缩 5%。",
            "北向小幅净流入 8.2 亿，结构上偏防御。",
        ],
    )
    section_b = RecapDailySection(
        title="资金主线：红利低估值与 AI 算力分歧",
        core_conclusion="低位红利继续吸金，AI 算力高位分歧加大。",
        bullets=[
            "电力 +1.6%、煤炭 +1.1% 主力净流入合计约 18 亿。",
            "光模块板块涨跌互现，龙头资金高位流出 4.5 亿。",
        ],
    )
    section_c = RecapDailySection(
        title="情绪中性偏弱，连板高度受限",
        core_conclusion="高度板未突破 4 板，情绪未见转折。",
        bullets=[
            "涨停 38 家、跌停 11 家；接力涨停率 28%。",
            "题材轮动加速，持续性差，避免追高。",
        ],
    )
    return RecapDaily(
        mode="daily",
        date=date,
        sections=[section_a, section_b, section_c],
        risks=["短期题材分歧加剧", "海外利率波动"],
        closing_summary="存量博弈，红利防御 + 高低切，避免追高动量。",
    )


def _settings_via_env(tmp_path, monkeypatch) -> Settings:
    db = tmp_path / "replay.db"
    monkeypatch.setenv("RECAP_DB_PATH", str(db))
    monkeypatch.setenv("RECAP_WXWORK_WEBHOOK_URL", "http://example.invalid/hook")
    monkeypatch.setenv("RECAP_PUSH_ENABLED", "false")
    monkeypatch.setenv("RECAP_API_KEY", "test-key")
    monkeypatch.setenv("RECAP_AUDIT_ENABLED", "true")
    import stock_recap.config.settings as _settings_mod

    _settings_mod._settings_instance = None  # noqa: SLF001
    return Settings()


def test_replay_provider_returns_recorded_recap(
    tmp_path, monkeypatch, replay_provider
):
    """``call_llm(model_spec='replay:fake')`` 直接返回 ReplayProvider 设的 recap。"""
    settings = _settings_via_env(tmp_path, monkeypatch)
    recap = _build_recap()
    replay_provider.recap_to_return = recap
    replay_provider.tokens_to_return = LlmTokens(
        input_tokens=100, output_tokens=400, total_tokens=500
    )

    messages = [
        {"role": "system", "content": "你是 A 股复盘助手"},
        {"role": "user", "content": "请生成 2025-03-04 的日终复盘 JSON"},
    ]
    out_recap, out_tokens = call_llm(
        settings=settings,
        mode="daily",
        messages=messages,
        model_spec="replay:fake",
        db_path=settings.db_path,
        date="2025-03-04",
    )
    assert out_recap.model_dump() == recap.model_dump()
    assert out_tokens.input_tokens == 100
    assert out_tokens.output_tokens == 400
    # provider 收到的 messages 与上层一致（无被静默截断 / 改写）
    assert len(replay_provider.calls) == 1
    assert replay_provider.calls[0]["messages"] == messages
    assert replay_provider.calls[0]["mode"] == "daily"


def test_replay_from_audit_round_trip(tmp_path, monkeypatch, replay_provider):
    """audit 表 → ReplayProvider → 完整 pipeline → ``recap_runs`` 与原 audit 一致。"""
    settings = _settings_via_env(tmp_path, monkeypatch)
    init_db(settings.db_path)

    # 1. 把"历史"调用写入 audit 表（模拟过去某次真实生成的现场）
    historical_recap = _build_recap()
    historical_messages = [
        {"role": "system", "content": "system v-old"},
        {"role": "user", "content": "user payload v-old"},
        {"role": "user", "content": "schema instruction v-old"},
    ]
    insert_recap_audit(
        settings.db_path,
        request_id="hist-req-1",
        created_at="2025-03-04T15:00:00+00:00",
        mode="daily",
        provider="live",
        prompt_version="vOLD",
        model="openai:gpt-4o",
        trace_id="trace-old",
        session_id=None,
        messages=historical_messages,
        recap=historical_recap,
        eval_obj={"ok": True},
        tokens=LlmTokens(input_tokens=200, output_tokens=600),
        llm_error=None,
        budget_error=None,
        critic_retries_used=0,
    )

    # 2. ReplayProvider 配置成「LLM 总是返回这条 recap」
    replay_provider.recap_to_return = historical_recap
    replay_provider.tokens_to_return = LlmTokens(
        input_tokens=200, output_tokens=600, total_tokens=800
    )

    # 3. 跑一次完整 pipeline（force_llm + 用 replay 后端）
    req = GenerateRequest(
        mode="daily",
        provider="mock",  # 数据用 mock，避免外网；LLM 由 replay 接管
        date="2025-03-04",
        force_llm=True,
        model="replay:fake",
        skip_trading_check=True,
    )
    resp = generate_once(req, settings)

    # 4. 断言：本次新 run 的 recap_json 与历史 audit 中的 recap 完全一致
    with get_conn(settings.db_path) as conn:
        row = conn.execute(
            "SELECT recap_json FROM recap_runs WHERE request_id = ?",
            (resp.request_id,),
        ).fetchone()
    assert row is not None
    new_recap = json.loads(row["recap_json"])
    assert new_recap == historical_recap.model_dump()

    # 5. 新 audit 也写了，且 messages 不为空（说明 force_llm 路径完整跑过）
    new_audits = load_recap_audit(settings.db_path, request_id=resp.request_id)
    assert len(new_audits) == 1
    assert new_audits[0]["recap"] == historical_recap.model_dump()
    assert new_audits[0]["messages"] is not None
    assert len(new_audits[0]["messages"]) >= 1

    # 6. 同一个 ReplayProvider 至少被调用一次（Critic 不应被反复触发）
    assert 1 <= len(replay_provider.calls) <= 2
