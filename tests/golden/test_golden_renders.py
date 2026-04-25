"""Golden 渲染测试：固定 Recap → ``render_markdown`` / ``render_wechat_text`` 输出稳定。

为什么有用：
- 渲染层是用户能直接看到的最终产出，任何「无意中改了样式 / 字段顺序 / 表情符」
  都会被本测试拦截；
- 配合 ``RECAP_GOLDEN_UPDATE=1`` 可以一次性升级，diff 留在 PR 中评审；
- 不依赖 LLM，运行 ms 级。
"""
from __future__ import annotations

from agent_platform.domain.models import (
    RecapDaily,
    RecapDailySection,
    RecapStrategy,
)
from agent_platform.presentation.render.renderers import (
    render_markdown,
    render_markdown_for_wechat_work,
    render_wechat_text,
)

from tests.golden._compare import assert_matches_golden_text


def _daily_recap() -> RecapDaily:
    s1 = RecapDailySection(
        title="指数缩量分化，沪指弱势调整",
        core_conclusion="主板回调，创业板抗跌；量能继续低位。",
        bullets=[
            "【复盘基准日：2025年03月04日 星期二】",
            "上证-0.18%、深成-0.05%、创业板+0.42%，两市成交 6800 亿环比缩 5%。",
            "北向小幅净流入 8.2 亿，结构上偏防御。",
        ],
    )
    s2 = RecapDailySection(
        title="资金主线：红利低估值与 AI 算力分歧",
        core_conclusion="低位红利继续吸金，AI 算力高位分歧加大。",
        bullets=[
            "电力 +1.6%、煤炭 +1.1% 主力净流入合计约 18 亿元。",
            "光模块板块涨跌互现，龙头资金高位流出 4.5 亿元。",
        ],
    )
    s3 = RecapDailySection(
        title="情绪中性偏弱，连板高度受限",
        core_conclusion="高度板未突破 4 板，情绪未见转折。",
        bullets=[
            "涨停 38 家、跌停 11 家；接力涨停率 28%。",
            "题材轮动加速，持续性差，避免追高。",
        ],
    )
    return RecapDaily(
        mode="daily",
        date="2025-03-04",
        sections=[s1, s2, s3],
        risks=["短期题材分歧加剧", "海外利率波动"],
        closing_summary="存量博弈，红利防御 + 高低切，避免追高动量。",
    )


def _strategy_recap() -> RecapStrategy:
    return RecapStrategy(
        mode="strategy",
        date="2025-03-05",
        mainline_focus=[
            "电力｜+1.6%｜主力净流入 12.4 亿｜延续低位红利防御",
            "煤炭｜+1.1%｜主力净流入 5.2 亿｜油价企稳叠加冬储补库",
        ],
        risk_warnings=[
            "SHIBOR 隔夜+8bp，10Y 国债利率上行 2bp，短端流动性偏紧；",
            "高位连板接力 ≤ 2，情绪退潮风险加大。",
        ],
        trading_logic=[
            "标的甲｜主力净流入 + 板块强度 + 量能突破｜次日竞价高开 ≤ 3% 且分时获主力流入持续",
            "标的乙｜机构席位净买入｜接力涨停率回升至 35% 以上",
        ],
    )


def test_render_markdown_daily_golden():
    out = render_markdown(_daily_recap())
    assert_matches_golden_text("render_markdown_daily.md", out)


def test_render_wechat_text_daily_golden():
    out = render_wechat_text(_daily_recap())
    assert_matches_golden_text("render_wechat_daily.txt", out)


def test_render_markdown_for_wechat_work_daily_golden():
    out = render_markdown_for_wechat_work(_daily_recap())
    assert_matches_golden_text("render_wechat_work_daily.md", out)


def test_render_markdown_strategy_golden():
    out = render_markdown(_strategy_recap())
    assert_matches_golden_text("render_markdown_strategy.md", out)


def test_render_wechat_text_strategy_golden():
    out = render_wechat_text(_strategy_recap())
    assert_matches_golden_text("render_wechat_strategy.txt", out)
