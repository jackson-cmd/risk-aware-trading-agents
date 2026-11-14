"""What the market looked like at one day's close, using only what was known by then.

``MarketView`` holds the bundled period with everything the agents read, each value as of a day's close:

* prices: the day's bar, returns over 1, 5 and 21 days, the price as traded and the total-return price;
* indicators: EMA 20/50/100/200, RSI 2/14, MACD, ATR, ADX, Bollinger bands, Donchian channels, 52-week high and
  drawdown, momentum, relative strength and beta against SPY, realized volatility, relative volume; and the
  technical agent's setups (EMA support touches, mean reversion, quality dips, breakouts) with its score;
* the market: the regime (risk-on, neutral, uncertain, risk-off) and the readings behind it (SPY trend, VIX and its
  term structure from VIX9D to VIX3M, VVIX, fear & greed from market data, sector breadth, credit stress, the
  macro events scheduled for the next two weeks);
* news: the verified news report of each stock (score, confirmed and single-source stories, red flags) from items
  published before the close, and the last earnings release (an 8-K with Item 2.02) before it.

Every indicator only looks backward, so its value on a day is what it would be if the data stopped that day; the
tests check it by changing everything after the day. News published at or after the close counts from the next
trading day. VIX indices close at 16:15 New York time, a quarter of an hour after stocks; the decision is taken
after the close, before the next open, when they are known.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .. import indicators as ind
from ..agents.macro import MacroAgent, to_regime
from ..agents.technical import TechnicalAgent
from ..config import AppConfig
from ..data.events import EventCalendar
from ..news.files import missing_message
from ..news.history import NewsArchive, PointInTimeNews, decision_time, load_bundle
from ..universe import MACRO_SYMBOLS, is_etf, sector_of
from .data import DATA_DIR, FrozenMarket, bundled_market
from .periods import get_period

NY = ZoneInfo("America/New_York")
NO_LOCAL_NEWS = DATA_DIR / "_no_local_news"  # never exists: research reads the period's headline file only
VOL_SYMBOLS = ["^VIX9D", "^VIX", "^VIX3M", "^VVIX"]
INDICATOR_KEYS = [
    "ema20", "ema50", "ema100", "ema200", "sma20", "rsi2", "rsi14", "macd", "macd_signal", "macd_hist", "atr14",
    "atr_pct", "adx14", "z20", "bb_lower", "bb_mid", "bb_upper", "don55_high", "don20_low", "high252", "dd252",
    "mom21", "mom126", "mom252", "rs126", "beta120", "vol20", "vol60", "rel_vol", "down_streak",
]
REGIME_MEANING = {
    "RISK_ON": "uptrend and calm volatility: the agents buy their setups normally",
    "NEUTRAL": "no clear trend: the agents buy support touches and dips",
    "UNCERTAIN": "event risk, elevated VIX, a flat VIX curve or a downtrend: hold, buy only at strong support",
    "RISK_OFF": "stress (VIX spike, backwardation, credit stress or extreme fear below EMA200): no new risk",
}


def research_config(overrides: dict[str, Any] | None = None) -> AppConfig:
    """The package defaults, untouched by local config files, so an experiment runs the same on any machine."""
    return AppConfig(**(overrides or {}))


def _num(v: Any, nd: int = 4) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    if abs(f) >= 1:
        return round(f, 2)
    return float(f"{f:.{nd}g}") if f else 0.0


def _ny(dt: datetime | None) -> str | None:
    return dt.astimezone(NY).strftime("%Y-%m-%d %H:%M") if dt else None


class MarketView:
    """The bundled period as the agents saw it, day by day."""

    def __init__(self, market: FrozenMarket | None = None, cfg: AppConfig | None = None, news: bool = True,
                 extra_news: list[dict] | None = None, period: str | None = None):
        self.market = market or bundled_market(period)
        self.period = self.market.period
        self.cfg = cfg or research_config()
        self.calendar = EventCalendar(self.cfg.macro.events, cache=None)
        syms = self.market.symbols
        self.frames = self.market.history(syms)
        self.index = self.frames["SPY"].index
        self._spy = self.frames["SPY"]["close"]
        self._ind: dict[str, pd.DataFrame] = {}
        self.macro = MacroAgent(self.cfg.macro, self.calendar).compute_series(
            {s: f for s, f in self.frames.items() if s in MACRO_SYMBOLS}, self.index)
        self.stocks = [s for s in syms if not s.startswith("^") and not is_etf(s) and s not in {"GLD", "USO", "UUP",
                                                                                              "IBIT"}]
        self.news = news_replay(self.cfg, self.stocks, extra_news, period=self.period) if news else None
        per = get_period(self.period)
        #: Why there are no headlines although news is on (the headline files are not distributed), else None.
        self.news_missing = missing_message(per.news) if news and not per.has_news else None
        self._earnings = earnings_releases(extra_news, period=self.period)
        self._tech = TechnicalAgent(breakouts_enabled=True)

    # ------------------------------------------------------------------ days
    def resolve(self, day: date | str) -> tuple[date, str | None]:
        """The trading day to show for ``day``: itself, or the last trading day before it (with a note)."""
        try:
            d = date.fromisoformat(day) if isinstance(day, str) else day
        except ValueError:
            raise ValueError(f"day must look like 2026-04-08, got {day!r}") from None
        first, last = self.market.first_day, self.market.last_day
        if d < first or d > last:
            raise ValueError(f"{d} is outside the bundled {self.period} data ({first} to {last})")
        t = self.market.last_trading_day(d)
        if t is None:
            raise ValueError(f"no trading day on or before {d}")
        note = None if t == d else f"{d} is not a trading day; this is the close of {t:%A} {t}"
        return t, note

    def indicators(self, symbol: str) -> pd.DataFrame:
        if symbol not in self._ind:
            if symbol not in self.frames:
                raise KeyError(f"{symbol} is not in the bundled data")
            self._ind[symbol] = ind.compute(self.frames[symbol], self._spy)
        return self._ind[symbol]

    def row(self, symbol: str, day: date) -> pd.Series | None:
        f = self.indicators(symbol)
        ts = pd.Timestamp(day)
        return f.loc[ts] if ts in f.index else None

    # ------------------------------------------------------------------ pieces
    def market_state(self, day: date) -> dict[str, Any]:
        ts = pd.Timestamp(day)
        m = self.macro.loc[ts]
        regime = to_regime(m["regime"]).value
        vol = {}
        for s in VOL_SYMBOLS:
            f = self.market.raw(s) if self.market.has(s) else None
            if f is not None:
                v = f["close"].loc[:ts]
                vol[s.lstrip("^").lower()] = _num(v.iloc[-1]) if len(v) else None
        spy = self.frames["SPY"]["close"].loc[:ts]
        ratio = _num(m.get("vix_ratio"))
        mc = self.cfg.macro
        term = None if ratio is None else ("backwardation" if ratio >= mc.backwardation_ratio else
                                           "flat" if ratio >= mc.flat_term_ratio else "contango")
        upcoming = [{"day": e.day.isoformat(), "event": e.name, "kind": e.kind, "days_away": (e.day - day).days,
                     "date_estimated": e.estimated}
                    for e in self.calendar.upcoming(day, 14)]
        return {
            "regime": regime, "regime_meaning": REGIME_MEANING.get(regime, ""),
            "risk_score": _num(m.get("risk_score")), "fear_greed": _num(m.get("fear_greed")),
            "spy_close": _num(self.market.raw("SPY")["close"].loc[:ts].iloc[-1]),
            "spy_return_1d": _num(spy.iloc[-1] / spy.iloc[-2] - 1) if len(spy) > 1 else None,
            "spy_return_5d": _num(spy.iloc[-1] / spy.iloc[-6] - 1) if len(spy) > 5 else None,
            "spy_return_21d": _num(spy.iloc[-1] / spy.iloc[-22] - 1) if len(spy) > 21 else None,
            "spy_trend": str(m.get("spy_trend") or ""), "vix": vol.get("vix"), "vix9d": vol.get("vix9d"),
            "vix3m": vol.get("vix3m"), "vvix": vol.get("vvix"), "vix_to_vix3m": ratio, "term_structure": term,
            "breadth": _num(m.get("breadth")), "credit_z": _num(m.get("credit_z")),
            "event_risk": _num(m.get("event_risk")), "events_now": str(m.get("events") or ""),
            "upcoming_events": upcoming,
        }

    def stock(self, symbol: str, day: date, regime: str | None = None) -> dict[str, Any] | None:
        f = self.indicators(symbol)
        ts = pd.Timestamp(day)
        if ts not in f.index:
            return None
        r = f.loc[ts]
        c = f["close"].loc[:ts]
        raw = self.market.raw(symbol).loc[ts]
        out: dict[str, Any] = {
            "sector": sector_of(symbol), "close_as_traded": _num(raw["close"]), "close": _num(r["close"]),
            "open": _num(r["open"]), "high": _num(r["high"]), "low": _num(r["low"]),
            "volume": int(raw["volume"]) if not pd.isna(raw["volume"]) else None,
            "return_1d": _num(c.iloc[-1] / c.iloc[-2] - 1) if len(c) > 1 else None,
            "return_5d": _num(c.iloc[-1] / c.iloc[-6] - 1) if len(c) > 5 else None,
            "return_21d": _num(c.iloc[-1] / c.iloc[-22] - 1) if len(c) > 21 else None,
        }
        out["indicators"] = {k: _num(r.get(k)) for k in INDICATOR_KEYS if k in r.index}
        close = float(r["close"])
        out["vs_ema_pct"] = {k: _num((close / float(r[k]) - 1) * 100) for k in ("ema20", "ema50", "ema100", "ema200")
                             if k in r.index and not pd.isna(r[k]) and float(r[k]) > 0}
        if regime is not None:
            view = self._tech.evaluate(symbol, r, to_regime(regime), None, is_broad_etf=False)
            out["technical"] = {"score": _num(view.score), "trend": view.trend,
                                "setups": {k: _num(v) for k, v in view.setups.items()},
                                "blocked_in_this_regime": sorted(view.blocked_setups),
                                "reasons": view.reasons[:4], "warnings": view.warnings[:3]}
        return out

    def news_report(self, symbol: str, day: date, headlines: int = 5) -> dict[str, Any] | None:
        if self.news is None:
            return None
        cutoff = decision_time(day)
        earn = [t for t in self._earnings.get(symbol, []) if t < cutoff]
        rep = self.news.report(symbol, day)
        out: dict[str, Any] = {
            "visible_until": _ny(cutoff) + " New York (items at or after the close count from the next day)",
            "last_earnings_release": _ny(earn[-1]) if earn else None,
            "trading_days_since_earnings": (len(self.market.trading_days(earn[-1].astimezone(NY).date(), day)) - 1)
            if earn else None,
        }
        if rep is None:
            out.update({"score": None, "stories": 0, "note": f"nothing published in the {self.cfg.news.lookback_hours} "
                                                          "hours before the close"})
            return out
        out.update({
            "score": _num(rep.score), "confirmed": rep.confirmed, "single_source": rep.unconfirmed,
            "opinion": rep.opinion, "red_flags_confirmed": list(rep.red_flags_confirmed),
            "red_flags_single_source": list(rep.red_flags_unconfirmed), "severe_confirmed": bool(rep.severe_confirmed),
            "stories": len(rep.clusters),
            "top": [{"headline": c.headline, "status": c.status.value, "sentiment": _num(c.sentiment, 2),
                     "publishers": list(c.publishers)[:4], "events": list(c.event_types), "red_flags": c.live_flags,
                     "first_seen": _ny(c.first_seen)} for c in rep.top(headlines)],
        })
        return out

    # ------------------------------------------------------------------ the whole day
    def snapshot(self, day: date | str, symbols: list[str] | None = None, news: bool = True,
                 headlines: int = 5) -> dict[str, Any]:
        """Everything known at the close of ``day``: the market, and for each stock its prices, indicators,
        technical setups and news. ``symbols`` defaults to the 64 bundled stocks."""
        d, note = self.resolve(day)
        nxt = self.market.next_trading_day(d)
        mkt = self.market_state(d)
        syms = [s.upper() for s in symbols] if symbols else list(self.stocks)
        unknown = [s for s in syms if s not in self.frames]
        stocks: dict[str, Any] = {}
        for s in syms:
            if s in unknown:
                continue
            row = self.stock(s, d, mkt["regime"])
            if row is None:
                continue
            if news and not is_etf(s):
                row["news"] = self.news_report(s, d, headlines)
            stocks[s] = row
        out = {
            "day": d.isoformat(), "weekday": f"{d:%A}",
            "decision": (f"after the close of {d} (16:00 New York{', 13:00 early close' if decision_time(d).astimezone(NY).hour == 13 else ''}); "
                         f"orders fill at the next open, {nxt}" if nxt else f"after the close of {d}; the data ends here"),
            "next_trading_day": nxt.isoformat() if nxt else None, "market": mkt, "stocks": stocks,
            "not_visible": "anything after this close: later prices, later news, and how the trade turned out",
        }
        if note:
            out["note"] = note
        if news and self.news_missing:
            out["news_note"] = f"the period's headline file is missing: {self.news_missing}"
        if unknown:
            out["unknown_symbols"] = unknown
        return out


def news_replay(cfg: AppConfig, symbols: list[str], extra_news: list[dict] | None = None,
                period: str | None = None) -> PointInTimeNews | None:
    """Point-in-time news from a period's headline file only (never a local archive), plus ``extra_news`` if given.
    None when there is nothing to show (no copy of the headline file and no ``extra_news``)."""
    archive = NewsArchive(NO_LOCAL_NEWS, bundled=True, bundle_path=get_period(period).news_path)
    if extra_news:
        archive = _WithExtra(archive, extra_news)
    pit = PointInTimeNews(archive, cfg.news, symbols)
    return pit if pit.symbols else None


class _WithExtra(NewsArchive):
    """A period's headline file plus records added for one experiment (counterfactual or injected headlines)."""

    def __init__(self, base: NewsArchive, extra: list[dict]):
        super().__init__(base.root, bundled=base.bundled, bundle_paths=base.bundle_paths)
        self._extra: dict[str, list[dict]] = {}
        for rec in extra:
            r = {"ts": rec["ts"], "symbol": rec["symbol"].upper(), "headline": rec["headline"],
                 "source": rec.get("source") or "experiment", "feed": rec.get("feed") or "experiment"}
            if rec.get("summary"):
                r["summary"] = rec["summary"]
            self._extra.setdefault(r["symbol"], []).append(r)

    def records(self, symbol: str) -> list[dict]:
        base = super().records(symbol)
        extra = self._extra.get(symbol.upper(), [])
        return sorted([*base, *extra], key=lambda r: r["ts"]) if extra else base

    def symbols(self) -> list[str]:
        return sorted(set(super().symbols()) | set(self._extra))


def earnings_releases(extra_news: list[dict] | None = None, period: str | None = None) -> dict[str, list[datetime]]:
    """Times of 8-K filings with Item 2.02 (results of operations) per stock, from the SEC records of a period's
    headline file (empty without a copy of it)."""
    out: dict[str, list[datetime]] = {}
    recs = [r for v in load_bundle(get_period(period).news_path).values() for r in v]
    for r in [*recs, *(extra_news or [])]:
        if r.get("feed") != "sec" or "2.02" not in str(r.get("summary", "")):
            continue
        ts = datetime.fromisoformat(str(r["ts"]).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        out.setdefault(str(r["symbol"]).upper(), []).append(ts)
    return {k: sorted(v) for k, v in out.items()}


def market_table(view: MarketView, day: date, symbols: list[str]) -> pd.DataFrame:
    """A compact table of the main readings per stock (for LLM prompts and quick looks)."""
    rows = []
    ts = pd.Timestamp(day)
    for s in symbols:
        f = view.indicators(s)
        if ts not in f.index:
            continue
        r = f.loc[ts]
        c = f["close"].loc[:ts]
        rows.append({
            "symbol": s, "close": r["close"], "ret_1d": c.iloc[-1] / c.iloc[-2] - 1 if len(c) > 1 else np.nan,
            "ret_5d": c.iloc[-1] / c.iloc[-6] - 1 if len(c) > 5 else np.nan,
            "ret_21d": c.iloc[-1] / c.iloc[-22] - 1 if len(c) > 21 else np.nan,
            "rsi14": r.get("rsi14"), "rsi2": r.get("rsi2"), "vs_ema50": r["close"] / r["ema50"] - 1,
            "vs_ema200": r["close"] / r["ema200"] - 1, "atr_pct": r.get("atr_pct"), "dd252": r.get("dd252"),
            "rel_vol": r.get("rel_vol"),
        })
    return pd.DataFrame(rows).set_index("symbol") if rows else pd.DataFrame()
