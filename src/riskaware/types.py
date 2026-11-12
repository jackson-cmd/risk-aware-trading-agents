"""Shared data types used across agents, brokers, the risk engine and the backtester."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class TimeInForce(str, Enum):
    DAY = "DAY"
    GTC = "GTC"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    DRY_RUN = "DRY_RUN"
    UNKNOWN = "UNKNOWN"


class Regime(str, Enum):
    """Market regime. The risk manager enforces a fixed policy per regime."""

    RISK_ON = "RISK_ON"
    NEUTRAL = "NEUTRAL"
    UNCERTAIN = "UNCERTAIN"
    RISK_OFF = "RISK_OFF"

    @property
    def allows_discretionary_sells(self) -> bool:
        return self in (Regime.RISK_ON, Regime.NEUTRAL)

    @property
    def label(self) -> str:
        return {
            Regime.RISK_ON: "Risk-on (trend)",
            Regime.NEUTRAL: "Neutral",
            Regime.UNCERTAIN: "Uncertain (hold, buy support only)",
            Regime.RISK_OFF: "Risk-off (hold, quality dips only)",
        }[self]


class NewsStatus(str, Enum):
    CONFIRMED = "CONFIRMED"  # two or more independent outlets
    PRIMARY = "PRIMARY"  # the company's own SEC 8-K filing
    ISSUER = "ISSUER"  # the company's own press release on a press-release wire
    UNCONFIRMED = "UNCONFIRMED"  # a single publisher
    OPINION = "OPINION"  # listicles, "should you buy" pieces


@dataclass
class Quote:
    symbol: str
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    timestamp: datetime | None = None

    @property
    def mid(self) -> float | None:
        if self.bid and self.ask and self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2
        return self.last

    @property
    def price(self) -> float | None:
        """Best single price: last trade, else mid."""
        return self.last if self.last else self.mid


@dataclass
class Position:
    symbol: str
    qty: float
    avg_cost: float
    market_price: float | None = None
    asset_class: str = "equity"

    @property
    def market_value(self) -> float:
        px = self.market_price if self.market_price is not None else self.avg_cost
        return self.qty * px

    @property
    def unrealized_pct(self) -> float:
        if not self.avg_cost or self.market_price is None:
            return 0.0
        return self.market_price / self.avg_cost - 1.0


@dataclass
class AccountSnapshot:
    broker: str
    account_id: str
    equity: float
    cash: float
    buying_power: float
    currency: str = "USD"
    is_paper: bool = True


@dataclass
class OrderRequest:
    symbol: str
    side: Side
    qty: float
    order_type: OrderType = OrderType.LIMIT
    limit_price: float | None = None
    time_in_force: TimeInForce = TimeInForce.DAY
    client_order_id: str | None = None
    extended_hours: bool = False
    reason: str = ""
    tag: str = ""  # e.g. "entry", "tranche", "take_profit", "cash_management"

    @property
    def notional(self) -> float | None:
        return self.qty * self.limit_price if self.limit_price else None


@dataclass
class OrderResult:
    order_id: str
    symbol: str
    side: Side
    qty: float
    status: OrderStatus
    client_order_id: str | None = None
    filled_qty: float = 0.0
    avg_fill_price: float | None = None
    limit_price: float | None = None
    submitted_at: datetime | None = None
    message: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Signal:
    """What a strategy wants to do with one symbol.

    ``strength`` is a 0..1 conviction. For sells, ``fraction`` is the share of the
    current position to sell (1.0 = all). ``emergency`` sells are allowed in every
    regime when the profile allows emergency exits; other sells only in RISK_ON
    and NEUTRAL.
    """

    symbol: str
    action: Action
    strength: float = 0.0
    reason: str = ""
    setups: list[str] = field(default_factory=list)
    fraction: float | None = None
    target_weight: float | None = None
    emergency: bool = False
    details: dict[str, Any] = field(default_factory=dict)
    option: OptionSpec | None = None  # open an option position on ``symbol`` instead of trading shares
    option_key: str | None = None  # close this option position (long or short)

    @property
    def is_option(self) -> bool:
        return self.option is not None or self.option_key is not None


@dataclass(frozen=True)
class OptionContract:
    underlying: str
    right: str  # "C" or "P"
    strike: float
    expiry: date

    @property
    def key(self) -> str:
        return f"{self.underlying} {self.expiry:%Y-%m-%d} {self.strike:g}{self.right}"

    def dte(self, day: date) -> int:
        return (self.expiry - day).days

    def intrinsic(self, spot: float) -> float:
        return max(spot - self.strike, 0.0) if self.right == "C" else max(self.strike - spot, 0.0)


@dataclass
class OptionSpec:
    """An option a strategy wants to open. The risk manager picks the contract count.

    ``side`` BUY opens a long option; SELL writes one (cash-secured put or covered call).
    The strike is chosen for ``target_delta`` (absolute value) and kept inside
    ``min_strike`` / ``max_strike``; the expiry is the monthly one closest to ``dte`` days.
    """

    right: str
    target_delta: float = 0.5
    dte: int = 45
    side: Side = Side.BUY
    min_strike: float | None = None
    max_strike: float | None = None
    contracts: int | None = None


@dataclass
class NewsItem:
    title: str
    url: str
    publisher: str
    feed: str
    published: datetime | None
    symbol: str | None = None
    summary: str = ""


@dataclass
class NewsCluster:
    headline: str
    items: list[NewsItem]
    publishers: list[str]
    feeds: list[str]
    status: NewsStatus
    sentiment: float
    event_types: list[str]
    red_flags: list[str]
    severe: bool
    first_seen: datetime | None
    last_seen: datetime | None
    denial: bool = False  # the story denies or retracts a report; it never confirms what it denies
    denies: list[str] = field(default_factory=list)  # red flags a denial names
    backed_flags: list[str] = field(default_factory=list)  # severe flags the company itself backs (can force a sale)
    disputed_flags: list[str] = field(default_factory=list)  # flags a later denial disputes
    cleared_flags: list[str] = field(default_factory=list)  # flags withdrawn by a denial at least as well sourced

    @property
    def confirmed(self) -> bool:
        return self.status in (NewsStatus.CONFIRMED, NewsStatus.PRIMARY, NewsStatus.ISSUER)

    @property
    def live_flags(self) -> list[str]:
        return [f for f in self.red_flags if f not in self.cleared_flags]

    @property
    def cleared(self) -> bool:
        """Every red flag of the story was withdrawn by a denial."""
        return bool(self.red_flags) and not self.live_flags


@dataclass
class MacroEvent:
    day: date
    kind: str  # FOMC, CPI, NFP, PPI, PCE, GDP, OPEX
    name: str
    impact: float
    time: str = ""
    estimated: bool = False
    note: str = ""


@dataclass
class AgentReport:
    """Uniform output of every agent, used for explanations and the journal."""

    agent: str
    score: float  # agent-specific scale, documented per agent
    verdict: str
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
