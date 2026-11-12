"""Risk-Aware Trading Agents.

A long-only, multi-agent stock trading system that reads the macro calendar,
volatility and news before it buys, keeps a cash floor, and runs the same
strategy in backtests, paper trading and live trading.
"""

__version__ = "0.3.0"

from .types import (  # noqa: E402
    Action,
    OrderRequest,
    OrderResult,
    Position,
    Regime,
    Side,
    Signal,
)

__all__ = [
    "__version__",
    "Action",
    "OrderRequest",
    "OrderResult",
    "Position",
    "Regime",
    "Side",
    "Signal",
]
