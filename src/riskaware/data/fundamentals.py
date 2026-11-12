"""Company fundamentals for the earnings-quality check behind dip buying.

``YahooFundamentals`` reads quarterly statements, EPS surprises and the next
earnings date from Yahoo Finance through yfinance and caches them for 12 hours.
Values are as currently reported, so they are meant for live use; backtests
treat fundamentals as unknown unless a point-in-time source is configured.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

import pandas as pd

from ..universe import is_etf
from ..utils import DiskCache, safe_float

log = logging.getLogger(__name__)


@dataclass
class FundamentalSnapshot:
    symbol: str
    as_of: str
    is_etf: bool = False
    revenue_yoy: float | None = None  # latest quarter vs same quarter a year earlier
    revenue_qoq_trend: float | None = None  # change in YoY growth vs the prior quarter
    eps_ttm: float | None = None
    eps_yoy: float | None = None
    net_margin: float | None = None
    gross_margin: float | None = None
    gross_margin_change: float | None = None  # latest minus a year earlier, percentage points
    fcf_ttm: float | None = None
    debt_to_equity: float | None = None
    eps_surprises: list[float] = field(default_factory=list)  # most recent first, percent
    last_earnings: str | None = None
    next_earnings: str | None = None
    market_cap: float | None = None
    quarters: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FundamentalSnapshot:
        fields = {k: d.get(k) for k in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        fields["eps_surprises"] = fields.get("eps_surprises") or []
        fields["quarters"] = fields.get("quarters") or 0
        fields["is_etf"] = bool(fields.get("is_etf"))
        return cls(**fields)

    @property
    def next_earnings_date(self) -> date | None:
        return date.fromisoformat(self.next_earnings) if self.next_earnings else None

    @property
    def last_earnings_date(self) -> date | None:
        return date.fromisoformat(self.last_earnings) if self.last_earnings else None


def _row(df: pd.DataFrame | None, *names: str) -> pd.Series | None:
    if df is None or df.empty:
        return None
    for n in names:
        if n in df.index:
            s = pd.to_numeric(df.loc[n], errors="coerce")
            if isinstance(s, pd.DataFrame):
                s = s.iloc[0]
            s = s.sort_index(ascending=False)  # newest first
            return s
    return None


def _yoy(s: pd.Series | None, lag: int = 4) -> float | None:
    if s is None or len(s) <= lag:
        return None
    cur, prev = safe_float(s.iloc[0]), safe_float(s.iloc[lag])
    if cur is None or prev is None or prev == 0:
        return None
    return cur / abs(prev) - 1.0


def snapshot_from_frames(
    symbol: str,
    income: pd.DataFrame | None,
    cashflow: pd.DataFrame | None,
    balance: pd.DataFrame | None,
    earnings: pd.DataFrame | None,
    next_earnings: date | None = None,
    market_cap: float | None = None,
    today: date | None = None,
) -> FundamentalSnapshot:
    """Build a snapshot from yfinance-shaped frames (rows = line items, columns = quarter ends)."""
    today = today or date.today()
    snap = FundamentalSnapshot(symbol=symbol, as_of=today.isoformat(), market_cap=market_cap)
    rev = _row(income, "Total Revenue", "Operating Revenue")
    gross = _row(income, "Gross Profit")
    eps = _row(income, "Diluted EPS", "Basic EPS")
    ni = _row(income, "Net Income", "Net Income Common Stockholders")
    if rev is not None:
        rev = rev.dropna()
        snap.quarters = len(rev)
        snap.revenue_yoy = _yoy(rev)
        if len(rev) > 5:
            prev_yoy = _yoy(rev.iloc[1:])
            if snap.revenue_yoy is not None and prev_yoy is not None:
                snap.revenue_qoq_trend = snap.revenue_yoy - prev_yoy
    if eps is not None:
        eps = eps.dropna()
        if len(eps) >= 4:
            snap.eps_ttm = float(eps.iloc[:4].sum())
        if len(eps) >= 8:
            prev_ttm = float(eps.iloc[4:8].sum())
            if prev_ttm:
                snap.eps_yoy = snap.eps_ttm / abs(prev_ttm) - 1.0
        elif len(eps) > 4:
            snap.eps_yoy = _yoy(eps)
    if rev is not None and ni is not None and len(rev) and len(ni.dropna()):
        r0, n0 = safe_float(rev.iloc[0]), safe_float(ni.dropna().iloc[0])
        if r0 and n0 is not None:
            snap.net_margin = n0 / r0
    if rev is not None and gross is not None:
        g = (gross / rev).dropna()
        if len(g):
            snap.gross_margin = float(g.iloc[0])
            if len(g) > 4:
                snap.gross_margin_change = float(g.iloc[0] - g.iloc[4])
    fcf = _row(cashflow, "Free Cash Flow")
    if fcf is None:
        ocf, capex = _row(cashflow, "Operating Cash Flow"), _row(cashflow, "Capital Expenditure")
        if ocf is not None and capex is not None:
            fcf = ocf + capex  # capex is reported as a negative number
    if fcf is not None:
        fcf = fcf.dropna()
        if len(fcf) >= 4:
            snap.fcf_ttm = float(fcf.iloc[:4].sum())
        elif len(fcf):
            snap.fcf_ttm = float(fcf.mean() * 4)
    debt = _row(balance, "Total Debt")
    equity = _row(balance, "Stockholders Equity", "Common Stock Equity")
    if debt is not None and equity is not None and len(debt.dropna()) and len(equity.dropna()):
        d0, e0 = safe_float(debt.dropna().iloc[0]), safe_float(equity.dropna().iloc[0])
        if d0 is not None and e0:
            snap.debt_to_equity = d0 / e0 if e0 > 0 else 99.0
    if earnings is not None and not earnings.empty:
        ed = earnings.copy()
        idx = pd.to_datetime(ed.index)
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_convert("America/New_York").tz_localize(None)
        ed.index = idx
        ed = ed.sort_index(ascending=False)
        reported = ed.dropna(subset=[c for c in ed.columns if "Reported" in c] or None)
        sur_col = next((c for c in ed.columns if "Surprise" in c), None)
        if sur_col is not None:
            snap.eps_surprises = [float(x) for x in pd.to_numeric(reported[sur_col], errors="coerce").dropna().iloc[:4]]
        if len(reported):
            snap.last_earnings = reported.index[0].date().isoformat()
        future = [d.date() for d in ed.index if d.date() >= today]
        if future and next_earnings is None:
            next_earnings = min(future)
    if next_earnings is not None:
        snap.next_earnings = next_earnings.isoformat()
    return snap


class YahooFundamentals:
    def __init__(self, cache: DiskCache | None = None, ttl_hours: float = 12.0):
        self.cache = cache
        self.ttl = ttl_hours * 3600

    def snapshot(self, symbol: str) -> FundamentalSnapshot | None:
        if is_etf(symbol) or symbol.startswith("^"):
            return FundamentalSnapshot(symbol=symbol, as_of=date.today().isoformat(), is_etf=True)
        if self.cache is not None:
            hit = self.cache.get_json("fundamentals", symbol, self.ttl)
            if hit:
                return FundamentalSnapshot.from_dict(hit)
        snap = self._fetch(symbol)
        if snap is not None and self.cache is not None:
            self.cache.set_json("fundamentals", symbol, snap.to_dict())
        return snap

    def _fetch(self, symbol: str) -> FundamentalSnapshot | None:
        try:
            import yfinance as yf
        except ImportError:  # pragma: no cover
            return None
        try:
            t = yf.Ticker(symbol)
            income = _safe(lambda: t.quarterly_income_stmt)
            cashflow = _safe(lambda: t.quarterly_cashflow)
            balance = _safe(lambda: t.quarterly_balance_sheet)
            earnings = _safe(lambda: t.get_earnings_dates(limit=12))
            cal = _safe(lambda: t.calendar) or {}
            nxt = None
            if isinstance(cal, dict):
                ed = cal.get("Earnings Date")
                if isinstance(ed, list) and ed:
                    nxt = ed[0] if isinstance(ed[0], date) else None
                    if isinstance(nxt, datetime):
                        nxt = nxt.date()
            mcap = None
            try:
                mcap = safe_float(t.fast_info["marketCap"])
            except Exception:
                pass
            if income is None and earnings is None:
                return None
            return snapshot_from_frames(symbol, income, cashflow, balance, earnings, nxt, mcap)
        except Exception as exc:
            log.info("fundamentals unavailable for %s: %s", symbol, exc)
            return None


def _safe(fn):
    try:
        return fn()
    except Exception as exc:
        log.debug("yfinance call failed: %s", exc)
        return None
