"""日终复盘 Markdown / 体例渲染。"""
from agent_platform.domain.models import RecapDaily, RecapDailySection
from agent_platform.presentation.render.renderers import (
    daily_headline_and_bullet_matrix,
    render_markdown,
)


def _daily_recap() -> RecapDaily:
    return RecapDaily(
        mode="daily",
        date="2026-04-23",
        sections=[
            RecapDailySection(
                title="指数与量能：研判博弈力度",
                core_conclusion="高位放量分歧，动能转弱。",
                bullets=[
                    "【复盘基准日：2026年04月23日 星期四】",
                    "上证收跌而成交额放大，多空在整数关口换手加剧。",
                    "全市场涨跌结构与指数背离时，情绪往往领先于宽基。",
                ],
            ),
            RecapDailySection(
                title="资金与主线：穿透筹码动向",
                core_conclusion="存量博弈下的风格再平衡。",
                bullets=["内资在成长赛道兑现，防御与红利承接换手。", "北向口径若不可用，仅用已给出的成交与涨跌结构定性。"],
            ),
            RecapDailySection(
                title="外部联动：宏观风险偏好映射",
                core_conclusion="外盘偏强未同步映射至 A 股风险偏好。",
                bullets=["美股反弹与大宗波动对国内风格形成拉扯。", "地缘与加密仅在有输入字段时着墨。"],
            ),
        ],
        risks=["退潮期波动放大"],
        closing_summary="情绪释放利于浮筹清洗；在量能台阶未站稳前以防御为主。",
    )


def test_daily_headline_and_bullet_matrix_strips_benchmark() -> None:
    r = _daily_recap()
    head, mat = daily_headline_and_bullet_matrix(r)
    assert "2026年04月23日" in head
    assert "星期四" in head
    assert len(mat[0]) == 2
    assert not mat[0][0].startswith("【复盘基准日")


def test_render_markdown_daily_research_layout() -> None:
    md = render_markdown(_daily_recap())
    assert md.startswith("## 【复盘基准日：")
    assert "### 1. 指数与量能：研判博弈力度" in md
    assert "* **观点：**" in md
    assert "* **分析：**" in md
    assert "**总结：**" in md
    assert "退潮期" in md


def test_render_markdown_synthesizes_benchmark_when_absent() -> None:
    r = RecapDaily(
        mode="daily",
        date="2026-04-23",
        sections=[
            RecapDailySection(
                title="指数与量能：研判博弈力度",
                core_conclusion="观点。",
                bullets=["分析一", "分析二"],
            ),
            RecapDailySection(
                title="资金与主线：穿透筹码动向",
                core_conclusion="观点。",
                bullets=["b1", "b2"],
            ),
            RecapDailySection(
                title="外部联动：宏观风险偏好映射",
                core_conclusion="观点。",
                bullets=["c1", "c2"],
            ),
        ],
    )
    md = render_markdown(r)
    assert "## 【复盘基准日：2026年04月23日 星期" in md
