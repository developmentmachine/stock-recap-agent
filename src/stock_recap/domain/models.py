"""所有 Pydantic 数据模型。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

# ─── 类型别名 ──────────────────────────────────────────────────────────────────
Mode = Literal["daily", "strategy"]
Provider = Literal["mock", "live"]
LlmBackend = Literal["openai", "ollama", "cursor-cli", "gemini-cli"]


# ─── 市场快照 ──────────────────────────────────────────────────────────────────
class MarketSnapshot(BaseModel):
    asof: str = Field(description="采集时间（UTC ISO）")
    provider: Provider
    date: str = Field(description="交易日 YYYY-MM-DD")
    is_trading_day: bool = Field(default=True, description="是否为交易日")
    sources: List[Dict[str, Any]] = Field(
        default_factory=list, description="数据来源列表（可追溯）"
    )

    # A 股核心
    a_share_indices: Dict[str, Any] = Field(default_factory=dict)
    market_sentiment: Dict[str, Any] = Field(default_factory=dict)
    sector_performance: Dict[str, Any] = Field(default_factory=dict)

    # 北向资金（原版缺失，新增）
    northbound_flow: Dict[str, Any] = Field(
        default_factory=dict,
        description="北向资金净流入情况（亿元）",
    )

    # 海外市场（原版 live 模式全空，新增采集）
    us_market: Dict[str, Any] = Field(default_factory=dict)
    futures: Dict[str, Any] = Field(default_factory=dict)
    commodities: Dict[str, Any] = Field(default_factory=dict)

    # 跨市场结构化对照（由行情派生，非外网叙事）
    cross_market: Dict[str, Any] = Field(default_factory=dict)

    # 板块资金流：行业 + 概念 主力净流入/流出前列
    sector_fund_flow: Dict[str, Any] = Field(default_factory=dict)
    # 涨停板池摘要：高位连板、题材聚合、封板金额
    limit_up_pool: Dict[str, Any] = Field(default_factory=dict)
    # 涨停连续性：昨涨停今表现、接力率、炸板率、接力梯队
    continuity: Dict[str, Any] = Field(default_factory=dict)
    # 风格因子矩阵：大小盘 / 成长价值 / 微盘 spread
    style_matrix: Dict[str, Any] = Field(default_factory=dict)
    # 龙虎榜：机构席位 / 知名游资识别
    lhb: Dict[str, Any] = Field(default_factory=dict)
    # 程序化生成的明日观察名单
    forward_watchlist: Dict[str, Any] = Field(default_factory=dict)
    # 流动性面板：DR007 / SHIBOR / USD-CNH / 10Y 国债收益率
    liquidity: Dict[str, Any] = Field(default_factory=dict)
    # 强势行业 top3 的板内成分股 top5（含板内涨停数）
    sector_leaders: Dict[str, Any] = Field(default_factory=dict)
    # 强势行业近 5 日累计涨幅，用于区分『新热点』vs『高位扩散』
    sector_5d_strength: Dict[str, Any] = Field(default_factory=dict)


# ─── 特征 ──────────────────────────────────────────────────────────────────────
class Features(BaseModel):
    market_strength: Optional[float] = None
    volume_level: Optional[float] = None
    northbound_signal: Optional[float] = None
    sector_rotation: Dict[str, Any] = Field(default_factory=dict)
    macro_signal: Dict[str, Any] = Field(default_factory=dict)

    # 文本摘要（注入 prompt）
    index_view: str = ""
    sector_view: str = ""
    sentiment_view: str = ""
    macro_view: str = ""


# ─── 复盘输出结构 ───────────────────────────────────────────────────────────────
class RecapDailySection(BaseModel):
    title: str = Field(
        description=(
            "本段核心论点提炼成的小标题（约 8～22 字），须与另外两段在措辞上明显区分、禁止三段复用同一套话；"
            "由当日逻辑自拟，不要固定填「指数与量能」「资金与主线」等模板字样。"
        )
    )
    core_conclusion: str = Field(
        description="对应正文中的「观点」：一两句抛出定性判断，须可被输入数据核对"
    )
    bullets: List[str] = Field(
        description=(
            "对应正文中的「分析」分点；每条可写成完整逻辑句或小段落。"
            "第一大类首条建议固定为「【复盘基准日：YYYY年MM月DD日 星期X】」（与 date 一致），"
            "渲染时会提为文首二级标题，该条不会重复出现在分析列表中。"
        ),
        min_length=2,
    )


class RecapDaily(BaseModel):
    mode: Literal["daily"]
    date: str
    sections: List[RecapDailySection] = Field(min_length=3, max_length=3)
    risks: List[str] = Field(default_factory=list)
    closing_summary: str = Field(
        default="",
        description=(
            "收束段「总结」：用 1～3 句凝练全天博弈含义与策略取向（研报式收束），"
            "不复述新数字；无合适收束语时可留空字符串。"
        ),
    )
    disclaimer: str = "本内容仅供参考，不构成投资建议。投资有风险，入市需谨慎。"


class RecapStrategy(BaseModel):
    mode: Literal["strategy"]
    date: str
    mainline_focus: List[str] = Field(min_length=1)
    risk_warnings: List[str] = Field(min_length=1)
    trading_logic: List[str] = Field(min_length=2)
    disclaimer: str = "本内容仅供参考，不构成投资建议。投资有风险，入市需谨慎。"


Recap = RecapDaily | RecapStrategy


# ─── 进化笔记（LLM 自我分析产出） ────────────────────────────────────────────────
class EvolutionNote(BaseModel):
    """LLM 对历史复盘质量的分析产出，用于驱动 prompt 自动演进。"""

    summary: str = Field(description="整体质量总结（1-3句）")
    problems: List[str] = Field(
        description="发现的问题：幻觉风险、格式漂移、内容空洞等", default_factory=list
    )
    praised_patterns: List[str] = Field(
        description="用户好评的写法/结构（来自高分反馈）", default_factory=list
    )
    low_rated_patterns: List[str] = Field(
        description="用户差评的写法/结构（来自低分反馈）", default_factory=list
    )
    prompt_suggestions: List[str] = Field(
        description="对 system prompt 的具体修改建议（逐条可操作）", default_factory=list
    )
    should_bump_version: bool = Field(
        description="是否建议升级 PROMPT_VERSION（有重大改进时为 True）",
        default=False,
    )


# ─── 回测结果 ──────────────────────────────────────────────────────────────────
class BacktestResult(BaseModel):
    strategy_date: str = Field(description="策略复盘日期（次日策略是为哪天生成的）")
    actual_date: str = Field(description="实际验证的行情日期")
    predicted_sectors: List[str] = Field(description="策略预测的主线方向")
    actual_top_sectors: List[str] = Field(description="实际涨幅前 10 板块")
    hit_count: int = Field(description="命中数量")
    hit_rate: float = Field(description="命中率 0-1")
    detail: str = Field(description="命中情况说明")


# ─── API 请求/响应 ─────────────────────────────────────────────────────────────
class GenerateRequest(BaseModel):
    mode: Mode = "daily"
    provider: Provider = "live"
    date: Optional[str] = Field(
        default=None, description="YYYY-MM-DD；不传则用今天（本地时区）"
    )
    force_llm: bool = Field(default=True, description="是否调用 LLM")
    model: Optional[str] = Field(
        default=None,
        description="覆盖模型名：openai:<m> / ollama:<m> / cursor-cli（兼容 cursor-agent）",
    )
    skip_trading_check: bool = Field(
        default=False, description="跳过交易日检查（非交易日强制生成时使用）"
    )


class GenerateResponse(BaseModel):
    request_id: str
    created_at: str
    prompt_version: str
    model: Optional[str]
    provider: Provider
    snapshot: MarketSnapshot
    features: Features
    recap: Optional[Recap]
    rendered_markdown: Optional[str]
    rendered_wechat_text: Optional[str] = Field(
        default=None, description="适合粘贴到企业微信的纯文本排版"
    )
    eval: Dict[str, Any] = Field(default_factory=dict)
    memory_used: List[Dict[str, Any]] = Field(default_factory=list)
    push_result: Optional[bool] = Field(
        default=None, description="推送结果（True=成功，False=失败，None=未推送）"
    )


class FeedbackRequest(BaseModel):
    request_id: str
    rating: int = Field(ge=1, le=5)
    tags: List[str] = Field(default_factory=list)
    comment: str = ""


# ─── 指标快照 ──────────────────────────────────────────────────────────────────
class MetricsSnapshot(BaseModel):
    total_runs: int = 0
    success_runs: int = 0
    failed_runs: int = 0
    avg_latency_ms: float = 0.0
    today_runs: int = 0
    today_success: int = 0
    current_prompt_version: str = ""
    evolution_count: int = 0
    avg_rating: Optional[float] = None
    last_run_at: Optional[str] = None


# ─── LLM token 统计 ────────────────────────────────────────────────────────────
@dataclass
class LlmTokens:
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None


class LlmError(RuntimeError):
    """所有 LLM 失败的祖先类（保持后向兼容；新代码请用更具体的子类）。"""


class LlmTransportError(LlmError):
    """**可重试**：网络抖动、subprocess 启动失败、HTTP 5xx、超时等传输/基础设施问题。

    此类异常被 ``call_llm`` 的 tenacity 装饰器自动重试 N 次；超过 N 次后向上抛出。
    """


class LlmBusinessError(LlmError):
    """**不可在同次调用内重试**：模型给出的内容本身有问题（schema、解析、约束）。

    重新调一次大概率是相同结果——应交由 Critic 节点用结构化反馈再请求一次，
    而不是 tenacity 盲重试浪费成本。
    """


class LlmParseError(LlmBusinessError):
    """模型输出无法解析为 JSON。"""


class LlmSchemaError(LlmBusinessError):
    """模型输出 JSON 但未通过 Recap schema 校验。"""


class LlmBudgetExceeded(LlmError):
    """预算（工具调用次数 / 墙钟 / token）耗尽，立即中止。

    既不应被 tenacity 重试，也不应触发 Critic（再来一轮预算只会更糟）。
    """

    def __init__(self, kind: str, limit: int, used: int) -> None:
        super().__init__(f"budget exceeded: {kind} limit={limit} used={used}")
        self.kind = kind
        self.limit = limit
        self.used = used
