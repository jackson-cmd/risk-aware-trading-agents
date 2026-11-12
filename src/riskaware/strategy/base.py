"""The strategy interface.

A strategy is a class with one required method, ``on_bar(ctx) -> list[Signal]``.
It runs once per trading day after the close (backtests) or at the scheduled
time (paper and live). It only says what it would like to do; the risk manager
then sizes the orders and enforces the cash floor, caps and regime rules.

Minimal example::

    from riskaware.strategy import Strategy, Signal, Action

    class Rsi2Dip(Strategy):
        name = "rsi2_dip"
        default_params = {"entry": 10, "exit": 70}

        def on_bar(self, ctx):
            out = []
            for sym in ctx.universe:
                row = ctx.row(sym)
                if row is None:
                    continue
                if ctx.position(sym) is None and row["close"] > row["ema200"] and row["rsi2"] < self.params["entry"]:
                    out.append(Signal(sym, Action.BUY, strength=0.7, reason="RSI2 dip above EMA200"))
                elif ctx.position(sym) and row["rsi2"] > self.params["exit"]:
                    out.append(Signal(sym, Action.SELL, fraction=1.0, reason="RSI2 recovered"))
            return out
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from ..agents.macro import MarketState
from ..config import RiskProfile
from ..types import Action, AgentReport, Signal  # noqa: F401  (re-exported for strategy authors)
from ..universe import BROAD_INDEX_ETFS, is_etf


class RowView:
    """One indicator row: today's values only.

    Supports ``row["close"]``, ``row.close`` and ``row.get("rsi2")``; missing values are NaN. The row holds its
    own copy of the values, so nothing reachable from it leads to later rows of the backtest.
    """

    __slots__ = ("_vals", "_cols")

    def __init__(self, arr: np.ndarray, cols: dict[str, int], i: int):
        self._vals = np.array(arr[i], dtype=float, copy=True)
        self._cols = cols

    def __getitem__(self, key: str) -> float:
        return float(self._vals[self._cols[key]])

    def get(self, key: str, default: float = math.nan) -> float:
        j = self._cols.get(key)
        return default if j is None else float(self._vals[j])

    def __getattr__(self, key: str) -> float:
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def keys(self) -> list[str]:
        return list(self._cols)

    def to_dict(self) -> dict[str, float]:
        return {k: float(self._vals[j]) for k, j in self._cols.items()}


class SymbolData:
    """Indicator frame of one symbol, converted once to a float array for fast row access."""

    def __init__(self, frame: pd.DataFrame):
        self.frame = frame
        numeric = frame.select_dtypes(include=[np.number])
        self.cols = {c: j for j, c in enumerate(numeric.columns)}
        self.arr = numeric.to_numpy(dtype=float)
        self.index = frame.index
        self._pos = {ts: i for i, ts in enumerate(frame.index)}

    def row_at(self, ts: pd.Timestamp) -> RowView | None:
        i = self._pos.get(ts)
        if i is None:
            return None
        return RowView(self.arr, self.cols, i)

    def position_of(self, ts: pd.Timestamp) -> int | None:
        return self._pos.get(ts)


@dataclass
class PositionView:
    symbol: str
    qty: float
    avg_cost: float
    price: float
    weight: float
    days_held: int
    tranches: int = 1
    target_weight: float | None = None
    last_entry_price: float | None = None
    days_since_entry: int = 0
    days_since_trim: int | None = None
    last_trim_price: float | None = None
    peak_price: float | None = None

    @property
    def value(self) -> float:
        return self.qty * self.price

    @property
    def gain(self) -> float:
        return self.price / self.avg_cost - 1.0 if self.avg_cost > 0 else 0.0


@dataclass
class OptionPositionView:
    key: str
    underlying: str
    right: str
    strike: float
    expiry: date
    contracts: int  # positive = long, negative = written (short)
    entry_price: float  # premium per share at entry
    mark: float  # model price per share now
    dte: int
    days_held: int

    @property
    def is_long(self) -> bool:
        return self.contracts > 0

    @property
    def value(self) -> float:
        """Market value: positive for long options, negative (a liability) for written ones."""
        return self.contracts * 100 * self.mark

    @property
    def pnl_pct(self) -> float:
        """Long: mark / entry - 1. Written: share of the premium kept (1.0 = expires worthless)."""
        if self.entry_price <= 0:
            return 0.0
        if self.is_long:
            return self.mark / self.entry_price - 1.0
        return (self.entry_price - self.mark) / self.entry_price


@dataclass
class PortfolioView:
    equity: float
    cash: float
    cash_equivalents: float = 0.0  # T-bill ETF value, counts toward the cash floor
    treasury_value: float = 0.0
    positions: dict[str, PositionView] = field(default_factory=dict)
    peak_equity: float = 0.0
    options: dict[str, OptionPositionView] = field(default_factory=dict)
    option_collateral: float = 0.0  # cash set aside for written puts; it cannot count toward the floor
    # filled earlier the same day (live and paper runs), so the daily limits hold across several runs a day
    new_positions_today: int = 0
    buys_today: int = 0

    @property
    def free_cash(self) -> float:
        return self.cash + self.cash_equivalents - self.option_collateral

    @property
    def long_option_value(self) -> float:
        return sum(o.value for o in self.options.values() if o.is_long)

    def covered_shares(self, symbol: str) -> int:
        """Shares already promised to written calls on ``symbol``."""
        return sum(-o.contracts * 100 for o in self.options.values()
                   if o.underlying == symbol and o.right == "C" and not o.is_long)

    @property
    def cash_total(self) -> float:
        return self.cash + self.cash_equivalents

    @property
    def cash_ratio(self) -> float:
        return self.cash_total / self.equity if self.equity > 0 else 1.0

    @property
    def stock_value(self) -> float:
        return sum(p.value for p in self.positions.values())

    @property
    def stock_exposure(self) -> float:
        return self.stock_value / self.equity if self.equity > 0 else 0.0

    @property
    def drawdown(self) -> float:
        return 1.0 - self.equity / self.peak_equity if self.peak_equity > 0 else 0.0


class StrategyContext:
    """Everything a strategy may look at for one decision day. Nothing after ``day`` is visible."""

    def __init__(
        self,
        day: date,
        universe: list[str],
        market: MarketState,
        portfolio: PortfolioView,
        profile: RiskProfile,
        rows: Callable[[str], RowView | None],
        history: Callable[[str, int], pd.DataFrame | None] | None = None,
        fundamentals: Callable[[str], Any] | None = None,
        quality: Callable[[str], AgentReport | None] | None = None,
        news: Callable[[str], Any] | None = None,
        days_to_earnings: Callable[[str], int | None] | None = None,
        mode: str = "backtest",
        scoring_weights: dict[str, float] | None = None,
        speculative: Callable[[str], bool] | None = None,
    ):
        self.day = day
        self.universe = universe
        self.market = market
        self.portfolio = portfolio
        self.profile = profile
        self.mode = mode
        self.scoring_weights = scoring_weights or {"technical": 0.55, "fundamental": 0.2, "news": 0.15, "macro": 0.1}
        self._rows = rows
        self._history = history
        self._fundamentals = fundamentals
        self._quality = quality
        self._news = news
        self._dte = days_to_earnings
        self._speculative = speculative
        self._cache: dict[tuple[str, str], Any] = {}

    @property
    def regime(self):
        return self.market.regime

    def row(self, symbol: str) -> RowView | None:
        return self._rows(symbol)

    def history(self, symbol: str, lookback: int = 260) -> pd.DataFrame | None:
        return self._history(symbol, lookback) if self._history else None

    def position(self, symbol: str) -> PositionView | None:
        return self.portfolio.positions.get(symbol)

    def _memo(self, kind: str, symbol: str, fn: Callable[[str], Any] | None) -> Any:
        if fn is None:
            return None
        key = (kind, symbol)
        if key not in self._cache:
            self._cache[key] = fn(symbol)
        return self._cache[key]

    def fundamentals(self, symbol: str):
        return self._memo("fundamentals", symbol, self._fundamentals)

    def quality(self, symbol: str) -> AgentReport | None:
        """Fundamental agent verdict (QUALITY / NEUTRAL / BROKEN / UNKNOWN / ETF), or None if unavailable."""
        return self._memo("quality", symbol, self._quality)

    def news(self, symbol: str):
        """Verified news report; in backtests only with a local news archive (``riskaware news-history``)."""
        return self._memo("news", symbol, self._news)

    def days_to_earnings(self, symbol: str) -> int | None:
        return self._memo("dte", symbol, self._dte)

    def is_etf(self, symbol: str) -> bool:
        return is_etf(symbol)

    def is_broad_etf(self, symbol: str) -> bool:
        return symbol in BROAD_INDEX_ETFS

    def is_speculative(self, symbol: str) -> bool:
        return bool(self._speculative(symbol)) if self._speculative else False


class Strategy:
    """Base class for strategies. Override ``on_bar``; everything else is optional."""

    name: str = "base"
    description: str = ""
    default_params: dict[str, Any] = {}

    def __init__(self, **params: Any):
        unknown = set(params) - set(self.default_params) if self.default_params else set()
        if unknown:
            raise ValueError(f"{self.name}: unknown parameters {sorted(unknown)}")
        self.params: dict[str, Any] = {**self.default_params, **params}

    def on_start(self, universe: list[str]) -> None:
        """Called once before the first bar."""

    def on_bar(self, ctx: StrategyContext) -> list[Signal]:
        raise NotImplementedError

    def warmup_bars(self) -> int:
        """Bars of history needed before the first decision."""
        return 260

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.params})"
