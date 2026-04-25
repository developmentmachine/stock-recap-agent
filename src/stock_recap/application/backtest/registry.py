"""回测评分器注册表：按 id 解析实现，支持运行时扩展（entry point 可后续接入）。"""
from __future__ import annotations

from typing import Dict

from stock_recap.domain.backtest_strategy import BacktestStrategy

_REGISTRY: Dict[str, BacktestStrategy] = {}


def register_backtest_strategy(name: str, impl: BacktestStrategy) -> None:
    key = (name or "").strip().lower()
    _REGISTRY[key] = impl


def _ensure_builtin() -> None:
    if _REGISTRY:
        return
    from stock_recap.infrastructure.evaluation.backtest_strategies import (
        KeywordSubstringBacktestStrategy,
        NormalizedTokenOverlapBacktestStrategy,
    )

    register_backtest_strategy("keyword_substring", KeywordSubstringBacktestStrategy())
    register_backtest_strategy("normalized_overlap", NormalizedTokenOverlapBacktestStrategy())


def resolve_backtest_strategy(name: str) -> BacktestStrategy:
    _ensure_builtin()
    key = (name or "").strip().lower() or "keyword_substring"
    impl = _REGISTRY.get(key)
    if impl is None:
        known = ", ".join(sorted(_REGISTRY)) or "(empty)"
        raise KeyError(f"unknown backtest scoring strategy {name!r}; known: {known}")
    return impl


__all__ = ["register_backtest_strategy", "resolve_backtest_strategy"]
