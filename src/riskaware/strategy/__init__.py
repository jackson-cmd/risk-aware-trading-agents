"""Strategies: the ``Strategy`` interface, the built-in risk-aware strategy and examples."""

from ..types import Action, Signal
from .base import PortfolioView, PositionView, RowView, Strategy, StrategyContext
from .examples import BuyAndHold, EmaBounce, GoldenCross, Rsi2MeanReversion
from .loader import BUILTIN, list_strategies, load_strategy
from .risk_aware import RiskAwareStrategy

__all__ = [
    "Action", "Signal", "Strategy", "StrategyContext", "PortfolioView", "PositionView", "RowView",
    "RiskAwareStrategy", "BuyAndHold", "EmaBounce", "GoldenCross", "Rsi2MeanReversion",
    "BUILTIN", "list_strategies", "load_strategy",
]
