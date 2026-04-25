"""Golden prompt-messages 测试：固定 (snapshot, features, prompt_version) → 稳定 messages。

为什么有用：
- prompt 是 LLM 行为最大的方差来源；任何对 ``build_messages`` /
  ``build_system_prompt`` / ``build_user_prompt`` 的修改都直接影响在线效果。
  本测试把「输入 → messages」整体快照下来，PR 中能一眼看到对 prompt 的所有变动；
- 与 ``RECAP_GOLDEN_UPDATE=1`` 结合，升级 prompt 时能集中 review 增量；
- 不打 LLM，几十毫秒级。

输入用 ``collect_mock`` 在固定日期生成，保证跨机器跨时区结果一致（mock 用日期 sha256
作为 RNG seed）。
"""
from __future__ import annotations

from agent_platform.infrastructure.data.collector import collect_snapshot
from agent_platform.infrastructure.data.features import build_features
from agent_platform.infrastructure.llm.prompts import build_messages

from tests.golden._compare import assert_matches_golden_json


_FIXED_DATE = "2025-03-04"
_FIXED_ASOF = "2025-03-04T15:00:00+00:00"
_FIXED_PROMPT_VERSION = "vGOLDEN-1"


def _build_inputs():
    snapshot = collect_snapshot("mock", _FIXED_DATE, skip_trading_check=True)
    # 固化 asof_iso 字段，避免落到 golden 中的运行时刻差异
    snapshot = snapshot.model_copy(update={"asof_iso": _FIXED_ASOF})
    features = build_features(snapshot)
    return snapshot, features


def test_build_messages_daily_golden():
    snapshot, features = _build_inputs()
    msgs = build_messages(
        mode="daily",
        snapshot=snapshot,
        features=features,
        memory=[],
        prompt_version=_FIXED_PROMPT_VERSION,
        evolution_guidance=None,
        feedback_summary=None,
        backtest_context=None,
        pattern_summary=None,
        skill_id_override=None,
    )
    assert_matches_golden_json("messages_daily.json", msgs)


def test_build_messages_strategy_golden():
    snapshot, features = _build_inputs()
    msgs = build_messages(
        mode="strategy",
        snapshot=snapshot,
        features=features,
        memory=[],
        prompt_version=_FIXED_PROMPT_VERSION,
        evolution_guidance=None,
        feedback_summary=None,
        backtest_context=None,
        pattern_summary=None,
        skill_id_override=None,
    )
    assert_matches_golden_json("messages_strategy.json", msgs)
