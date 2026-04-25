"""领域模型与值对象（无 I/O、无框架依赖）。"""

from agent_platform.domain.backtest_strategy import BacktestStrategy
from agent_platform.domain.data_providers import (
    DataProviderRegistry,
    DataProviderSpec,
)
from agent_platform.domain.run_context import RunContext
from agent_platform.domain.models import (
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
