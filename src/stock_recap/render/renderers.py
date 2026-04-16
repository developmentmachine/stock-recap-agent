"""渲染层：将 Recap 对象转为 Markdown 和企业微信纯文本格式。"""
from __future__ import annotations

from stock_recap.models import Recap, RecapDaily, RecapStrategy


def render_markdown(recap: Recap) -> str:
    if recap.mode == "daily":
        assert isinstance(recap, RecapDaily)
        lines = [f"# {recap.date} A股复盘（日终）", ""]
        for i, sec in enumerate(recap.sections, 1):
            lines.append(f"## {i}. {sec.title}")
            lines.append("")
            lines.append(f"**核心结论**：{sec.core_conclusion}")
            lines.append("")
            for b in sec.bullets:
                lines.append(f"- {b}")
            lines.append("")
        if recap.risks:
            lines.append("## 风险提示")
            lines.append("")
            for r in recap.risks:
                lines.append(f"- {r}")
            lines.append("")
        lines.append(f"---\n\n{recap.disclaimer}\n")
        return "\n".join(lines)

    # strategy
    assert isinstance(recap, RecapStrategy)
    lines = [f"# {recap.date} 次日策略（信号）", "", "## 主线关注方向", ""]
    for x in recap.mainline_focus:
        lines.append(f"- {x}")
    lines.append("")
    lines.append("## 风险提示")
    lines.append("")
    for x in recap.risk_warnings:
        lines.append(f"- {x}")
    lines.append("")
    lines.append("## 交易逻辑说明")
    lines.append("")
    for x in recap.trading_logic:
        lines.append(f"- {x}")
    lines.append(f"\n---\n\n{recap.disclaimer}\n")
    return "\n".join(lines)


def render_wechat_text(recap: Recap) -> str:
    """
    企业微信纯文本排版（不依赖 Markdown 语法，全角分隔，分层清晰）。
    企业微信群机器人支持 Markdown，但 wechat_text 作为备用纯文本格式保留。
    """
    if recap.mode == "daily":
        assert isinstance(recap, RecapDaily)
        out = []
        out.append(f"{recap.date} A股复盘（日终）")
        out.append("（仅供参考，不构成投资建议）")
        out.append("")
        for i, sec in enumerate(recap.sections, 1):
            out.append(f"【{i}】{sec.title}")
            out.append(f"核心结论：{sec.core_conclusion}")
            out.append("逻辑要点：")
            for b in sec.bullets:
                out.append(f"  · {b}")
            out.append("")
        if recap.risks:
            out.append("【风险提示】")
            for r in recap.risks:
                out.append(f"  · {r}")
            out.append("")
        out.append(recap.disclaimer)
        return "\n".join(out).strip() + "\n"

    assert isinstance(recap, RecapStrategy)
    out = []
    out.append(f"{recap.date} 次日策略（信号）")
    out.append("（仅供参考，不构成投资建议）")
    out.append("")
    out.append("【主线关注方向】")
    for x in recap.mainline_focus:
        out.append(f"  · {x}")
    out.append("")
    out.append("【风险提示】")
    for x in recap.risk_warnings:
        out.append(f"  · {x}")
    out.append("")
    out.append("【交易逻辑说明】")
    for x in recap.trading_logic:
        out.append(f"  · {x}")
    out.append("")
    out.append(recap.disclaimer)
    return "\n".join(out).strip() + "\n"


def render_markdown_for_wechat_work(recap: Recap) -> str:
    """
    企业微信群机器人 Markdown 格式（支持有限的 Markdown 子集）。
    字符限制：4096 字节，超出时截断并附提示。
    """
    if recap.mode == "daily":
        assert isinstance(recap, RecapDaily)
        lines = [f"## {recap.date} A股复盘（日终）", ""]
        for i, sec in enumerate(recap.sections, 1):
            lines.append(f"**{i}. {sec.title}**")
            lines.append(f"> {sec.core_conclusion}")
            for b in sec.bullets:
                lines.append(f"- {b}")
            lines.append("")
        if recap.risks:
            lines.append("**风险提示**")
            for r in recap.risks:
                lines.append(f"- {r}")
            lines.append("")
        lines.append(f"*{recap.disclaimer}*")
        content = "\n".join(lines)
    else:
        assert isinstance(recap, RecapStrategy)
        lines = [f"## {recap.date} 次日策略（信号）", "", "**主线关注方向**"]
        for x in recap.mainline_focus:
            lines.append(f"- {x}")
        lines.append("")
        lines.append("**风险提示**")
        for x in recap.risk_warnings:
            lines.append(f"- {x}")
        lines.append("")
        lines.append("**交易逻辑**")
        for x in recap.trading_logic:
            lines.append(f"- {x}")
        lines.append(f"\n*{recap.disclaimer}*")
        content = "\n".join(lines)

    # 企业微信 Markdown 消息限 4096 字节（UTF-8）
    encoded = content.encode("utf-8")
    if len(encoded) > 4000:
        content = encoded[:4000].decode("utf-8", errors="ignore") + "\n\n…（内容已截断）"
    return content
