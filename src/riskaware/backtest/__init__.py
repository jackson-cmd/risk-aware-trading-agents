"""Daily backtester, metrics and HTML reports."""

from .engine import Backtester, BacktestResult
from .metrics import summarize

__all__ = ["Backtester", "BacktestResult", "summarize"]
