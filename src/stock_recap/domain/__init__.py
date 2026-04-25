"""领域模型与值对象（无 I/O、无框架依赖）。"""

from stock_recap.domain.backtest_strategy import BacktestStrategy
from stock_recap.domain.data_providers import (
    DataProviderRegistry,
    DataProviderSpec,
)
from stock_recap.domain.run_context import RunContext
from stock_recap.domain.models import (
    BacktestResult,
    DailyMarketEvent,
    EvolutionNote,
    Features,
    FeedbackRequest,
    GenerateRequest,
    GenerateResponse,
    HighlightedSector,
    LlmBackend,
    LlmError,
    LlmTokens,
    MarketSnapshot,
    MetricsSnapshot,
    Mode,
    NamedIndexRef,
    Provider,
    Recap,
    RecapDaily,
    RecapDailySection,
    RecapStrategy,
)

__all__ = [
    "BacktestStrategy",
    "DataProviderRegistry",
    "DataProviderSpec",
    "RunContext",
    "BacktestResult",
    "DailyMarketEvent",
    "EvolutionNote",
    "Features",
    "FeedbackRequest",
    "GenerateRequest",
    "GenerateResponse",
    "HighlightedSector",
    "LlmBackend",
    "LlmError",
    "LlmTokens",
    "MarketSnapshot",
    "MetricsSnapshot",
    "Mode",
    "NamedIndexRef",
    "Provider",
    "Recap",
    "RecapDaily",
    "RecapDailySection",
    "RecapStrategy",
]
