"""领域模型与值对象（无 I/O、无框架依赖）。"""

from stock_recap.domain.run_context import RunContext
from stock_recap.domain.models import (
    BacktestResult,
    EvolutionNote,
    Features,
    FeedbackRequest,
    GenerateRequest,
    GenerateResponse,
    LlmBackend,
    LlmError,
    LlmTokens,
    MarketSnapshot,
    MetricsSnapshot,
    Mode,
    Provider,
    Recap,
    RecapDaily,
    RecapDailySection,
    RecapStrategy,
)

__all__ = [
    "RunContext",
    "BacktestResult",
    "EvolutionNote",
    "Features",
    "FeedbackRequest",
    "GenerateRequest",
    "GenerateResponse",
    "LlmBackend",
    "LlmError",
    "LlmTokens",
    "MarketSnapshot",
    "MetricsSnapshot",
    "Mode",
    "Provider",
    "Recap",
    "RecapDaily",
    "RecapDailySection",
    "RecapStrategy",
]
