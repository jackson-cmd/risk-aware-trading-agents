"""Event-driven daily backtester.

Timeline for each trading day t:
1. Orders decided at the close of t-1 are filled at t: at the open if the open is
   inside the limit, at the limit if the day's range reaches it, otherwise they
   expire. Slippage and commission are applied.
2. Cash earns the T-bill ETF return of day t (the cash reserve is assumed parked).
3. The portfolio is marked to the close of t.
4. The strategy sees data up to the close of t and returns signals; the risk
   manager turns them into orders for t+1.

The strategy, agents and risk manager are the same objects used live.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from .. import indicators as ind
from ..agents.macro import MacroAgent, MarketState, to_regime
from ..config import AppConfig, ExecutionConfig, RiskProfile
from ..data.events import EventCalendar
from ..data.prices import PriceProvider
from ..news.history import PointInTimeNews
from ..options.pricing import bs_price, model_iv, year_fraction
from ..risk import PlannedOrder, RiskManager
from ..strategy.base import (
    OptionPositionView,
    PortfolioView,
    PositionView,
    Strategy,
    StrategyContext,
    SymbolData,
)
from ..types import OptionContract, Side
from ..universe import MACRO_SYMBOLS
from . import metrics as M

log = logging.getLogger(__name__)


@dataclass
class Holding:
    qty: float
    avg_cost: float
    first_entry: int
    last_entry: int
    last_entry_price: float
    tranches: int = 1
    target_weight: float | None = None
    last_trim: int | None = None
    last_trim_price: float | None = None
    peak_price: float = 0.0


@dataclass
class OptionHolding:
    contract: OptionContract
    contracts: int  # positive long, negative written
    entry_price: float
    entry_idx: int
    iv: float
    mark: float = 0.0


@dataclass
class BacktestResult:
    strategy: str
    profile: str
    start: date
    end: date
    initial_cash: float
    equity: pd.Series
    benchmark: pd.Series
    cash_ratio: pd.Series
    exposure: pd.Series
    regimes: pd.Series
    fear_greed: pd.Series
    positions: pd.Series
    trades: pd.DataFrame
    metrics: dict[str, float]
    benchmark_metrics: dict[str, float]
    notes: list[str] = field(default_factory=list)
    rejections: dict[str, int] = field(default_factory=dict)
    final_holdings: dict[str, float] = field(default_factory=dict)

    def summary_rows(self) -> list[tuple[str, str, str]]:
        rows = []
        for key, (label, _) in M.METRIC_LABELS.items():
            if key in self.metrics or key in self.benchmark_metrics:
                rows.append((label, M.fmt_metric(key, self.metrics.get(key)),
                             M.fmt_metric(key, self.benchmark_metrics.get(key))))
        return rows


class Backtester:
    def __init__(self, cfg: AppConfig, prices: PriceProvider, calendar: EventCalendar | None = None,
                 profile: RiskProfile | None = None, quality_fn: Callable[[str, date], Any] | None = None,
                 news: PointInTimeNews | None = None, planner: Any = None):
        self.cfg = cfg
        self.prices = prices
        self.calendar = calendar if calendar is not None else EventCalendar(cfg.macro.events)
        self.profile = profile or cfg.risk_profile()
        self.quality_fn = quality_fn
        self.news = news
        # turns signals into orders: the risk manager by default, or anything with the same plan() method
        # (riskaware.research.execution.PlainPlanner sizes orders without the risk rules)
        self.planner = planner
        self._data_cache: dict[tuple, dict[str, pd.DataFrame]] = {}
        self._frames_key: tuple | None = None
        self._derived: dict[tuple, Any] = {}  # indicators and macro series per loaded data, reused by later runs

    # ------------------------------------------------------------------ data
    def load(self, symbols: list[str], start: date, end: date) -> dict[str, pd.DataFrame]:
        bt = self.cfg.backtest
        extra = [self.cfg.universe.benchmark, self.cfg.universe.treasury_symbol]
        if bt.cash_yield_symbol:
            extra.append(bt.cash_yield_symbol)
        wanted = list(dict.fromkeys([*symbols, *MACRO_SYMBOLS, *extra]))
        data_start = start - timedelta(days=int(bt.warmup_days * 1.5))
        key = (tuple(wanted), data_start, end)
        if key not in self._data_cache:
            self._data_cache[key] = self.prices.history(wanted, data_start, end)
        self._frames_key = key
        return self._data_cache[key]

    def _cached(self, *key: Any, make: Callable[[], Any]) -> Any:
        k = (self._frames_key, *key)
        if k not in self._derived:
            self._derived[k] = make()
        return self._derived[k]

    def universe_for(self, universe: list[str] | None = None) -> list[str]:
        """The symbols a run trades: the given list or the configured universe, without the cash vehicles."""
        tsym = self.cfg.universe.treasury_symbol if self.profile.treasury_sleeve > 0 else None
        skip = {self.cfg.universe.cash_vehicle, self.cfg.backtest.cash_yield_symbol, tsym} - {None}
        return [s for s in (universe or self.cfg.universe.resolve()) if s not in skip]

    # ------------------------------------------------------------------- run
    def run(self, strategy: Strategy, start: date, end: date | None = None, universe: list[str] | None = None,
            initial_cash: float | None = None, progress: Callable[[int, int], None] | None = None) -> BacktestResult:
        cfg, prof = self.cfg, self.profile
        bt = cfg.backtest
        end = end or date.today()
        initial_cash = float(initial_cash or bt.initial_cash)
        tsym = cfg.universe.treasury_symbol if prof.treasury_sleeve > 0 else None
        universe = self.universe_for(universe)
        frames = self.load(universe, start, end)
        bench_sym = cfg.universe.benchmark
        if bench_sym not in frames:
            raise RuntimeError(f"no price data for the benchmark {bench_sym}; check your data source")
        spy_close = frames[bench_sym]["close"]
        full_index = frames[bench_sym].index
        days = full_index[(full_index >= pd.Timestamp(start)) & (full_index <= pd.Timestamp(end))]
        if len(days) < 2:
            raise RuntimeError("not enough trading days in the backtest window")
        notes = ["Signals use the close of day t; orders fill on day t+1 (open or limit)."]
        if self.news is None:
            notes.append("News is not used in this backtest (turn it on with --news; 2026 headlines ship with the "
                         "package, other years come from `riskaware news-history`).")
        else:
            covered = [s for s in universe if s in self.news.symbols]
            feeds = ", ".join(f"{k} {v}" for k, v in sorted(self.news.feeds().items()))
            first, last = self.news.coverage()
            span = f", {first:%Y-%m-%d} to {last:%Y-%m-%d}" if first and last else ""
            notes.append(f"News replayed for {len(covered)}/{len(universe)} symbols ({feeds}{span}). Each day sees "
                         "only items published before its close (16:00 New York, 13:00 on early-close days); "
                         "orders fill the next day.")
        if self.quality_fn is None:
            notes.append("Fundamentals are unknown in this backtest; dip buys use the technical proxy.")
        missing = [s for s in universe if s not in frames]
        if missing:
            notes.append(f"No data for: {', '.join(missing)}")
        trade_syms = [s for s in universe if s in frames]
        if tsym and tsym in frames:
            trade_syms_with_t = [*trade_syms, tsym]
        else:
            trade_syms_with_t = trade_syms
            tsym = None
        sym_data: dict[str, SymbolData] = {}
        for s in trade_syms_with_t:
            sym_data[s] = self._cached("ind", s, make=lambda _s=s: SymbolData(ind.compute(frames[_s], spy_close)))
        macro = self._cached("macro", make=lambda: MacroAgent(cfg.macro, self.calendar).compute_series(frames,
                                                                                                        full_index))
        bil = frames.get(bt.cash_yield_symbol) if bt.cash_yield_symbol else None
        cash_ret = bil["close"].pct_change().reindex(full_index).fillna(0.0).clip(-0.01, 0.01) if bil is not None \
            else pd.Series(0.0, index=full_index)
        if bil is None and bt.cash_yield_symbol:
            notes.append(f"No data for {bt.cash_yield_symbol}: cash earns nothing.")

        x = ExecutionConfig(order_type="LIMIT", limit_offset=bt.max_chase, allow_fractional=False)
        risk = self.planner if self.planner is not None else RiskManager(prof, cfg.universe, x, manage_cash=True,
                                                                            options=cfg.options)
        rate = (cash_ret.rolling(63, min_periods=10).mean() * 252).clip(0.0, 0.08).fillna(0.02)
        vix_s = macro["vix"]
        oc = cfg.options
        weights = cfg.scoring.weights()
        strategy.on_start(trade_syms)

        cash = initial_cash
        collateral = 0.0
        opts: dict[str, OptionHolding] = {}
        holdings: dict[str, Holding] = {}
        pending: list[PlannedOrder] = []
        peak = initial_cash
        rec_equity, rec_cash, rec_expo, rec_pos = [], [], [], []
        trades: list[dict[str, Any]] = []
        rejections: dict[str, int] = {}
        slip = bt.slippage_bps / 10_000
        n_days = len(days)

        def last_close(sym: str, ts: pd.Timestamp) -> float | None:
            sd = sym_data.get(sym)
            if sd is None:
                return None
            j = sd.index.searchsorted(ts, side="right") - 1
            return float(sd.arr[j, sd.cols["close"]]) if j >= 0 else None

        def iv_at(sym: str, ts: pd.Timestamp) -> float | None:
            sd = sym_data.get(sym)
            j = sd.index.searchsorted(ts, side="right") - 1 if sd is not None else -1
            if j < 0:
                return None
            v = vix_s.get(ts)
            return model_iv(sym, float(sd.arr[j, sd.cols["vol20"]]), float(sd.arr[j, sd.cols["vol60"]]),
                            None if v is None or pd.isna(v) else float(v), oc.iv_premium)

        def record(ts, symbol, side, qty, price, mult, tag, reason, pnl=np.nan, held=np.nan, commission=0.0):
            trades.append(dict(date=ts, symbol=symbol, side=side, qty=qty, price=price, value=qty * price * mult,
                               commission=commission, tag=tag, reason=reason, pnl=pnl, holding_days=held))

        def add_shares(sym: str, qty: float, px: float, t_idx: int) -> None:
            h_ = holdings.get(sym)
            if h_ is None:
                holdings[sym] = Holding(qty=qty, avg_cost=px, first_entry=t_idx, last_entry=t_idx,
                                        last_entry_price=px, tranches=1, peak_price=px)
            else:
                h_.avg_cost = (h_.avg_cost * h_.qty + px * qty) / (h_.qty + qty)
                h_.qty += qty
                h_.last_entry, h_.last_entry_price = t_idx, px

        def fill_option(order: PlannedOrder, ts: pd.Timestamp, t_idx: int) -> None:
            nonlocal cash, collateral
            day = ts.date()
            r = float(rate.get(ts, 0.02))
            if order.option_key:
                h_ = opts.get(order.option_key)
                if h_ is None:
                    return
                c = h_.contract
                b = bar(c.underlying, ts)
                if b is None:
                    return
                mid = bs_price(b[0], c.strike, year_fraction(day, c.expiry), r, h_.iv, c.right)
                n = abs(h_.contracts)
                fee = oc.commission_per_contract * n
                if h_.contracts > 0:
                    px = mid * (1 - oc.half_spread)
                    cash += px * 100 * n - fee
                    pnl = (px - h_.entry_price) * 100 * n - fee
                    record(ts, c.key, "SELL", n, px, 100, "option_close", order.reason, pnl, t_idx - h_.entry_idx, fee)
                else:
                    px = mid * (1 + oc.half_spread)
                    cash -= px * 100 * n + fee
                    if c.right == "P":
                        collateral -= c.strike * 100 * n
                    pnl = (h_.entry_price - px) * 100 * n - fee
                    record(ts, c.key, "BUY", n, px, 100, "option_close", order.reason, pnl, t_idx - h_.entry_idx, fee)
                del opts[order.option_key]
                return
            c = order.option
            b = bar(c.underlying, ts)
            if b is None or c.expiry <= day:
                return
            iv = order.iv or iv_at(c.underlying, ts) or 0.3
            mid = bs_price(b[0], c.strike, year_fraction(day, c.expiry), r, iv, c.right)
            n = int(order.qty)
            if order.side == Side.BUY:
                px = mid * (1 + oc.half_spread)
                if px < 0.01:
                    return
                unit = px * 100 + oc.commission_per_contract
                n = min(n, int((cash - collateral) // unit))
                if n < 1:
                    return
                cash -= unit * n
                signed = n
            else:
                px = mid * (1 - oc.half_spread)
                if px < 0.01:
                    return
                if c.right == "P":
                    n = min(n, int((cash - collateral) // (c.strike * 100)))
                    collateral += c.strike * 100 * max(n, 0)
                else:
                    held = holdings.get(c.underlying)
                    covered = sum(-o.contracts * 100 for o in opts.values()
                                  if o.contract.underlying == c.underlying and o.contract.right == "C" and o.contracts < 0)
                    n = min(n, int(((held.qty if held else 0) - covered) // 100))
                if n < 1:
                    return
                cash += (px * 100 - oc.commission_per_contract) * n
                signed = -n
            fee = oc.commission_per_contract * n
            h_ = opts.get(c.key)
            if h_ is None:
                opts[c.key] = OptionHolding(c, signed, px, t_idx, iv, mid)
            else:
                total = h_.contracts + signed
                h_.entry_price = (h_.entry_price * abs(h_.contracts) + px * n) / max(abs(total), 1)
                h_.contracts = total
            record(ts, c.key, "BUY" if signed > 0 else "SELL", n, px, 100, "option_open", order.reason,
                   commission=fee)

        def settle(key: str, h_: OptionHolding, spot: float, ts: pd.Timestamp, t_idx: int) -> None:
            nonlocal cash, collateral
            c = h_.contract
            n = abs(h_.contracts)
            intrinsic = c.intrinsic(spot)
            held_days = t_idx - h_.entry_idx
            if h_.contracts > 0:
                cash += intrinsic * 100 * n
                record(ts, key, "SELL", n, intrinsic, 100, "option_expiry", "expired" if intrinsic == 0 else
                       "exercised for intrinsic value", (intrinsic - h_.entry_price) * 100 * n, held_days)
            elif c.right == "P":
                collateral -= c.strike * 100 * n
                if intrinsic > 0:
                    cash -= c.strike * 100 * n
                    add_shares(c.underlying, 100 * n, c.strike, t_idx)
                    record(ts, c.underlying, "BUY", 100 * n, c.strike, 1, "assignment", f"put assigned ({key})")
                record(ts, key, "BUY", n, 0.0, 100, "option_expiry",
                       "assigned" if intrinsic > 0 else "expired worthless", h_.entry_price * 100 * n, held_days)
            else:
                held = holdings.get(c.underlying)
                if intrinsic > 0 and held is not None and held.qty >= 100 * n:
                    cash += c.strike * 100 * n
                    record(ts, c.underlying, "SELL", 100 * n, c.strike, 1, "called_away", f"call assigned ({key})",
                           (c.strike - held.avg_cost) * 100 * n, t_idx - held.first_entry)
                    held.qty -= 100 * n
                    if held.qty <= 1e-9:
                        del holdings[c.underlying]
                elif intrinsic > 0:
                    cash -= intrinsic * 100 * n
                record(ts, key, "BUY", n, 0.0, 100, "option_expiry",
                       "called away" if intrinsic > 0 else "expired worthless", h_.entry_price * 100 * n, held_days)
            del opts[key]

        def bar(sym: str, ts: pd.Timestamp) -> tuple[float, float, float, float] | None:
            sd = sym_data.get(sym)
            if sd is None:
                return None
            i = sd.position_of(ts)
            if i is None:
                return None
            r = sd.arr[i]
            c = sd.cols
            vals = (r[c["open"]], r[c["high"]], r[c["low"]], r[c["close"]])
            return None if any(math.isnan(v) for v in vals) else vals

        for t_idx, ts in enumerate(days):
            # 1. fills ------------------------------------------------------
            for order in pending:
                if order.is_option:
                    fill_option(order, ts, t_idx)
                    continue
                b = bar(order.symbol, ts)
                if b is None:
                    continue
                o, h, lo, _c = b
                lim = order.limit_price
                if order.side == Side.BUY:
                    if lim is None or o <= lim:
                        px = o * (1 + slip)
                    elif lo <= lim:
                        px = lim
                    else:
                        continue
                    qty = order.qty
                    commission = max(bt.commission_min, bt.commission_per_share * qty) if qty else 0.0
                    if qty * px + commission > cash - collateral:
                        qty = math.floor((cash - collateral - bt.commission_min) / (px * (1 + 1e-9)))
                        commission = max(bt.commission_min, bt.commission_per_share * qty)
                    if qty <= 0:
                        continue
                    cost = qty * px + commission
                    cash -= cost
                    h_ = holdings.get(order.symbol)
                    if h_ is None:
                        holdings[order.symbol] = Holding(qty=qty, avg_cost=px, first_entry=t_idx, last_entry=t_idx,
                                                         last_entry_price=px, tranches=1,
                                                         target_weight=order.target_weight, peak_price=px)
                    else:
                        new_qty = h_.qty + qty
                        h_.avg_cost = (h_.avg_cost * h_.qty + px * qty) / new_qty
                        h_.qty = new_qty
                        h_.last_entry = t_idx
                        h_.last_entry_price = px
                        h_.tranches += 1
                        if order.target_weight:
                            h_.target_weight = h_.target_weight or order.target_weight
                    trades.append(dict(date=ts, symbol=order.symbol, side="BUY", qty=qty, price=px, value=qty * px,
                                       commission=commission, tag=order.tag, reason=order.reason, pnl=np.nan,
                                       holding_days=np.nan))
                else:
                    h_ = holdings.get(order.symbol)
                    if h_ is None:
                        continue
                    if lim is None or o >= lim:
                        px = o * (1 - slip)
                    elif h >= lim:
                        px = lim
                    else:
                        continue
                    covered = sum(-o.contracts * 100 for o in opts.values()
                                  if o.contract.underlying == order.symbol and o.contract.right == "C" and o.contracts < 0)
                    qty = min(order.qty, h_.qty - covered)
                    if qty <= 0:
                        continue
                    commission = max(bt.commission_min, bt.commission_per_share * qty)
                    cash += qty * px - commission
                    pnl = (px - h_.avg_cost) * qty - commission
                    trades.append(dict(date=ts, symbol=order.symbol, side="SELL", qty=qty, price=px, value=qty * px,
                                       commission=commission, tag=order.tag, reason=order.reason, pnl=pnl,
                                       holding_days=t_idx - h_.first_entry))
                    h_.qty -= qty
                    if h_.qty <= 1e-9:
                        del holdings[order.symbol]
                    else:
                        h_.last_trim = t_idx
                        h_.last_trim_price = px
            pending = []

            # 2. cash yield; expiring options settle first (assignment can add or remove shares)
            cash *= 1.0 + float(cash_ret.get(ts, 0.0))
            day_now = ts.date()
            for key, h_ in list(opts.items()):
                if day_now >= h_.contract.expiry:
                    b = bar(h_.contract.underlying, ts)
                    spot = b[3] if b else last_close(h_.contract.underlying, ts)
                    if spot is not None:
                        settle(key, h_, spot, ts, t_idx)

            # 3. mark to market ---------------------------------------------------
            closes: dict[str, float] = {}
            value = 0.0
            for s, h_ in holdings.items():
                b = bar(s, ts)
                px = b[3] if b else (last_close(s, ts) or h_.last_entry_price)
                closes[s] = px
                h_.peak_price = max(h_.peak_price, px)
                value += h_.qty * px
            option_value = 0.0
            for h_ in opts.values():
                c = h_.contract
                spot = closes.get(c.underlying) or last_close(c.underlying, ts)
                if spot is None:
                    continue
                h_.iv = iv_at(c.underlying, ts) or h_.iv
                h_.mark = bs_price(spot, c.strike, year_fraction(day_now, c.expiry), float(rate.get(ts, 0.02)),
                                   h_.iv, c.right)
                option_value += h_.contracts * 100 * h_.mark
            equity = cash + value + option_value
            peak = max(peak, equity)
            stock_val = sum(h_.qty * closes[s] for s, h_ in holdings.items() if s != tsym)
            rec_equity.append(equity)
            rec_cash.append((cash - collateral) / equity if equity > 0 else 1.0)
            rec_expo.append(stock_val / equity if equity > 0 else 0.0)
            rec_pos.append(sum(1 for s in holdings if s != tsym))
            if progress and (t_idx % 50 == 0 or t_idx == n_days - 1):
                progress(t_idx + 1, n_days)
            if t_idx == n_days - 1:
                break

            # 4. decide ---------------------------------------------------------
            mrow = macro.loc[ts]
            market = _fast_state(mrow, ts.date())
            market.risk_free = float(rate.get(ts, 0.02))
            positions = {}
            for s, h_ in holdings.items():
                if s == tsym:
                    continue
                px = closes[s]
                positions[s] = PositionView(
                    symbol=s, qty=h_.qty, avg_cost=h_.avg_cost, price=px, weight=h_.qty * px / equity,
                    days_held=t_idx - h_.first_entry, tranches=h_.tranches, target_weight=h_.target_weight,
                    last_entry_price=h_.last_entry_price, days_since_entry=t_idx - h_.last_entry,
                    days_since_trim=None if h_.last_trim is None else t_idx - h_.last_trim,
                    last_trim_price=h_.last_trim_price, peak_price=h_.peak_price)
            tval = holdings[tsym].qty * closes[tsym] if tsym and tsym in holdings else 0.0
            option_views = {
                key: OptionPositionView(key=key, underlying=h_.contract.underlying, right=h_.contract.right,
                                        strike=h_.contract.strike, expiry=h_.contract.expiry, contracts=h_.contracts,
                                        entry_price=h_.entry_price, mark=h_.mark, dte=h_.contract.dte(ts.date()),
                                        days_held=t_idx - h_.entry_idx)
                for key, h_ in opts.items()}
            pf = PortfolioView(equity=equity, cash=cash, cash_equivalents=0.0, treasury_value=tval,
                               positions=positions, peak_equity=peak, options=option_views,
                               option_collateral=collateral)
            day = ts.date()

            def rows(sym: str, _ts=ts):
                sd = sym_data.get(sym)
                return sd.row_at(_ts) if sd else None

            def history(sym: str, lookback: int = 260, _ts=ts):
                sd = sym_data.get(sym)
                if sd is None:
                    return None
                i = sd.position_of(_ts)
                # a copy: a slice could still reach the later rows through its underlying arrays
                return None if i is None else sd.frame.iloc[max(0, i - lookback + 1): i + 1].copy()

            quality = None
            if self.quality_fn is not None:
                qf = self.quality_fn
                quality = lambda sym, _d=day, _qf=qf: _qf(sym, _d)  # noqa: E731
            news_fn = None
            if self.news is not None:
                pit = self.news
                news_fn = lambda sym, _d=day, _p=pit: _p.report(sym, _d)  # noqa: E731
            ctx = StrategyContext(day, trade_syms, market, pf, prof, rows=rows, history=history, quality=quality,
                                  news=news_fn, mode="backtest", scoring_weights=weights)
            signals = strategy.on_bar(ctx)
            prices_now = {s: float(sd.arr[i, sd.cols["close"]])
                          for s, sd in sym_data.items() if (i := sd.position_of(ts)) is not None}
            decision = risk.plan(signals, ctx, prices_now)
            for _sig, why in decision.rejected:
                key = why.split(" (")[0].split(":")[0]
                rejections[key] = rejections.get(key, 0) + 1
            pending = decision.orders

        idx = days[: len(rec_equity)]
        equity_s = pd.Series(rec_equity, index=idx, name="equity")
        bench = spy_close.reindex(idx).ffill()
        bench_eq = bench / bench.iloc[0] * initial_cash
        trades_df = pd.DataFrame(trades, columns=["date", "symbol", "side", "qty", "price", "value", "commission",
                                                  "tag", "reason", "pnl", "holding_days"])
        rf = cash_ret.reindex(idx)
        cash_ratio = pd.Series(rec_cash, index=idx)
        exposure = pd.Series(rec_expo, index=idx)
        metrics = M.summarize(equity_s, rf, trades_df, exposure, cash_ratio)
        bench_metrics = M.summarize(bench_eq, rf)
        regimes = macro["regime"].reindex(idx).map(lambda r: to_regime(r).value)
        final = {s: h_.qty for s, h_ in holdings.items()}
        final.update({k: h_.contracts for k, h_ in opts.items()})
        if any(t["tag"].startswith("option") for t in trades):
            notes.append("Options are priced with Black-Scholes on modelled implied volatility (realized volatility "
                         "and VIX), paying half the bid-ask spread; there are no historical option quotes.")
        return BacktestResult(
            strategy=strategy.name, profile=prof.name, start=idx[0].date(), end=idx[-1].date(),
            initial_cash=initial_cash, equity=equity_s, benchmark=bench_eq, cash_ratio=cash_ratio,
            exposure=exposure, regimes=regimes, fear_greed=macro["fear_greed"].reindex(idx),
            positions=pd.Series(rec_pos, index=idx), trades=trades_df, metrics=metrics,
            benchmark_metrics=bench_metrics, notes=notes, rejections=dict(sorted(rejections.items(), key=lambda kv: -kv[1])),
            final_holdings=final,
        )


def _fast_state(row: pd.Series, day: date) -> MarketState:
    """MarketState from a precomputed macro row, without calendar lookups (backtest speed)."""

    def f(key: str) -> float | None:
        v = row.get(key)
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return None if math.isnan(v) else v

    events = str(row.get("events") or "")
    return MarketState(
        day=day, regime=to_regime(row["regime"]), risk_score=f("risk_score") or 0.0, fear_greed=f("fear_greed"),
        vix=f("vix"), vix_ratio=f("vix_ratio"), spy_close=f("spy"), spy_trend=str(row.get("spy_trend", "mixed")),
        breadth=f("breadth"), credit_z=f("credit_z"), event_risk=f("event_risk") or 0.0,
        reasons=[f"events: {events}"] if events else [], macro_score=f("macro_score") or 0.0,
        dip_tailwind=f("dip_tailwind") or 0.0,
    )
