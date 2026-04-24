"""渲染层：将 Recap 对象转为 Markdown 和企业微信纯文本格式。"""
from __future__ import annotations

import html
import re
from datetime import datetime

from stock_recap.domain.models import Recap, RecapDaily, RecapStrategy

_BENCHMARK_RE = re.compile(r"^【复盘基准日：.+】\s*$")


def is_benchmark_bullet_line(line: str) -> bool:
    return bool(_BENCHMARK_RE.match(str(line).strip()))


def _default_benchmark_line(iso_date: str) -> str:
    dt = datetime.strptime(iso_date, "%Y-%m-%d")
    wk = ["一", "二", "三", "四", "五", "六", "日"]
    return f"【复盘基准日：{dt.year}年{dt.month:02d}月{dt.day:02d}日 星期{wk[dt.weekday()]}】"


def daily_headline_and_bullet_matrix(recap: RecapDaily) -> tuple[str, list[list[str]]]:
    """文首基准日标题 + 各大类用于「分析」区的分条（剔除第一大类首条基准日句）。"""
    headline: str | None = None
    matrix: list[list[str]] = []
    for i, sec in enumerate(recap.sections):
        bs = [str(b).strip() for b in sec.bullets]
        if i == 0 and bs and is_benchmark_bullet_line(bs[0]):
            headline = bs[0]
            bs = bs[1:]
        matrix.append(bs)
    if headline is None:
        headline = _default_benchmark_line(recap.date)
    return headline, matrix


def render_markdown(recap: Recap) -> str:
    if recap.mode == "daily":
        assert isinstance(recap, RecapDaily)
        headline, bullet_matrix = daily_headline_and_bullet_matrix(recap)
        lines: list[str] = [f"## {headline}", ""]
        for i, sec in enumerate(recap.sections, 1):
            lines.append(f"### {i}. {sec.title}")
            lines.append("")
            lines.append(f"* **观点：** {sec.core_conclusion}")
            lines.append("")
            lines.append("* **分析：**")
            for b in bullet_matrix[i - 1]:
                lines.append(f"    * {b}")
            lines.append("")
        if recap.risks:
            lines.append("## 风险提示")
            lines.append("")
            for r in recap.risks:
                lines.append(f"- {r}")
            lines.append("")
        cs = (recap.closing_summary or "").strip()
        if cs:
            lines.append("---")
            lines.append("")
            lines.append(f"**总结：** {cs}")
            lines.append("")
        lines.append("---")
        lines.append("")
        lines.append(recap.disclaimer)
        lines.append("")
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
        headline, bullet_matrix = daily_headline_and_bullet_matrix(recap)
        out: list[str] = []
        out.append(headline)
        out.append(f"{recap.date} A股复盘（日终）")
        out.append("（仅供参考，不构成投资建议）")
        out.append("")
        for i, sec in enumerate(recap.sections, 1):
            out.append(f"【{i}】{sec.title}")
            out.append(f"观点：{sec.core_conclusion}")
            out.append("分析：")
            for b in bullet_matrix[i - 1]:
                out.append(f"  · {b}")
            out.append("")
        if recap.risks:
            out.append("【风险提示】")
            for r in recap.risks:
                out.append(f"  · {r}")
            out.append("")
        cs = (recap.closing_summary or "").strip()
        if cs:
            out.append("【总结】")
            out.append(cs)
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
        headline, bullet_matrix = daily_headline_and_bullet_matrix(recap)
        lines = [f"## {headline}", "", f"### {recap.date} A股复盘（日终）", ""]
        for i, sec in enumerate(recap.sections, 1):
            lines.append(f"**{i}. {sec.title}**")
            lines.append(f"> 观点：{sec.core_conclusion}")
            lines.append("分析：")
            for b in bullet_matrix[i - 1]:
                lines.append(f"- {b}")
            lines.append("")
        if recap.risks:
            lines.append("**风险提示**")
            for r in recap.risks:
                lines.append(f"- {r}")
            lines.append("")
        cs = (recap.closing_summary or "").strip()
        if cs:
            lines.append("**总结**")
            lines.append(f"> {cs}")
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

    # 企业微信 markdown 单条上限 4096 字节，超出时截断 + 追加提示。
    _LIMIT = 4096
    encoded = content.encode("utf-8")
    if len(encoded) > _LIMIT:
        suffix = "\n\n…(已截断)"
        budget = _LIMIT - len(suffix.encode("utf-8"))
        truncated = encoded[:budget].decode("utf-8", errors="ignore")
        content = truncated + suffix
    return content


def render_wechat_mp_html(recap: Recap) -> str:
    """
    微信公众号 HTML 排版。
    - 无 Markdown，纯 inline style
    - 适合直接粘贴到公众号编辑器或通过草稿箱 API 提交
    - 字体、间距、颜色符合公众号阅读习惯
    """
    def _section_html(title: str, body: str) -> str:
        return (
            f'<section style="margin:24px 0;">'
            f'<h2 style="font-size:17px;font-weight:bold;color:#1a1a1a;'
            f'border-left:4px solid #2c7ef8;padding-left:10px;margin-bottom:10px;">'
            f'{title}</h2>'
            f'{body}'
            f'</section>'
        )

    def _bullet(text: str) -> str:
        return (
            f'<p style="font-size:15px;color:#333;line-height:1.8;'
            f'padding-left:16px;margin:6px 0;">'
            f'· {text}</p>'
        )

    def _conclusion(text: str) -> str:
        return (
            f'<p style="font-size:15px;font-weight:bold;color:#2c7ef8;'
            f'line-height:1.8;margin:8px 0;">{text}</p>'
        )

    def _disclaimer(text: str) -> str:
        return (
            f'<p style="font-size:12px;color:#999;text-align:center;'
            f'margin-top:32px;padding-top:12px;border-top:1px solid #eee;">'
            f'{text}</p>'
        )

    sections_html = ""

    if recap.mode == "daily":
        assert isinstance(recap, RecapDaily)
        headline, bullet_matrix = daily_headline_and_bullet_matrix(recap)
        title = f"{recap.date} A股复盘"
        bench_html = (
            f'<p style="font-size:16px;font-weight:bold;color:#1a1a1a;'
            f'text-align:center;margin:0 0 16px 0;">{headline}</p>'
        )
        sections_html += bench_html
        for i, sec in enumerate(recap.sections, 1):
            sub = f"{i}. {sec.title}"
            body = '<p style="font-size:14px;color:#666;margin:4px 0;">观点</p>'
            body += _conclusion(sec.core_conclusion)
            body += '<p style="font-size:14px;color:#666;margin:12px 0 4px 0;">分析</p>'
            body += "".join(_bullet(b) for b in bullet_matrix[i - 1])
            sections_html += _section_html(sub, body)
        if recap.risks:
            risk_body = "".join(_bullet(r) for r in recap.risks)
            sections_html += _section_html("风险提示", risk_body)
        cs = (recap.closing_summary or "").strip()
        if cs:
            sections_html += _section_html(
                "总结",
                f'<p style="font-size:15px;line-height:1.8;">{html.escape(cs)}</p>',
            )
        disclaimer = recap.disclaimer
    else:
        assert isinstance(recap, RecapStrategy)
        title = f"{recap.date} 次日策略"
        focus_body = "".join(_bullet(x) for x in recap.mainline_focus)
        sections_html += _section_html("主线关注方向", focus_body)
        logic_body = "".join(_bullet(x) for x in recap.trading_logic)
        sections_html += _section_html("交易逻辑", logic_body)
        risk_body = "".join(_bullet(x) for x in recap.risk_warnings)
        sections_html += _section_html("风险提示", risk_body)
        disclaimer = recap.disclaimer

    return (
        f'<section style="font-family:-apple-system,BlinkMacSystemFont,'
        f'\'PingFang SC\',\'Helvetica Neue\',sans-serif;'
        f'max-width:677px;margin:0 auto;padding:16px;">'
        f'<h1 style="font-size:20px;font-weight:bold;color:#1a1a1a;'
        f'text-align:center;margin-bottom:24px;">{title}</h1>'
        f'{sections_html}'
        f'{_disclaimer(disclaimer)}'
        f'</section>'
    )

