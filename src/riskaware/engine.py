"""The live / paper trading cycle and the read-only scans behind the CLI and dashboard.

One cycle: connect to the broker -> load prices and macro data -> build the
portfolio view -> strategy signals -> optional LLM review -> risk manager ->
safety guards -> submit (or preview) orders -> journal, state, notifications.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd

from . import indicators as ind
from . import marketcal as mc
from .agents.fundamental import FundamentalAgent
from .agents.macro import MacroAgent, MarketState
from .agents.technical import TechnicalAgent, TechnicalView
from .brokers import Broker, BrokerError, client_order_id, make_broker
from .config import AppConfig
from .data.events import EventCalendar
from .data.fundamentals import YahooFundamentals
from .data.prices import PriceProvider, make_price_provider
from .data.sentiment import fetch_cnn_fear_greed
from .guards import GuardError, Guards
from .journal import Journal
from .news.sources import NewsCollector
from .news.verify import NewsReport, analyze_news
from .notify import send as notify
from .risk import PlannedOrder, RiskDecision, RiskManager
from .state import StateStore
from .strategy import load_strategy
from .strategy.base import PortfolioView, PositionView, Strategy, StrategyContext, SymbolData
from .types import (
    AccountSnapshot,
    Action,
    OrderRequest,
    OrderResult,
    OrderStatus,
    OrderType,
    Position,
    Quote,
    Signal,
)
from .universe import MACRO_SYMBOLS, is_etf
from .utils import DiskCache

log = logging.getLogger(__name__)


@dataclass
class CycleReport:
    mode: str
    broker: str
    started: datetime
    market: MarketState | None = None
    account: AccountSnapshot | None = None
    portfolio: PortfolioView | None = None
    signals: list[Signal] = field(default_factory=list)
    decision: RiskDecision | None = None
    approved: list[PlannedOrder] = field(default_factory=list)
    blocked: list[tuple[PlannedOrder, str]] = field(default_factory=list)
    submitted: list[OrderResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    run_id: int | None = None


def reference_price(q: Quote | None, max_spread: float = 0.01) -> float | None:
    """Price used for sizing and limits: the ask when the quote is tight, otherwise the last trade.
    After-hours quotes can be several percent wide, and an ask from them would overpay."""
    if q is None:
        return None
    if q.bid and q.ask and q.ask >= q.bid > 0:
        mid = (q.ask + q.bid) / 2
        if (q.ask - q.bid) / mid <= max_spread:
            return q.ask
    return q.last or q.mid


class MarketData:
    """Prices, indicators and the macro state for live use, loaded once per run."""

    def __init__(self, cfg: AppConfig, prices: PriceProvider | None = None, cache: DiskCache | None = None,
                 calendar: EventCalendar | None = None):
        self.cfg = cfg
        self.cache = cache or DiskCache(cfg.data.cache_path)
        self.prices = prices or make_price_provider(cfg.data.price_source, self.cache, cfg.data.csv_dir)
        self.calendar = calendar or EventCalendar(cfg.macro.events, self.cache, cfg.data.contact_email)
        self.frames: dict[str, pd.DataFrame] = {}
        self.sym_data: dict[str, SymbolData] = {}
        self.macro: pd.DataFrame | None = None
        self.market: MarketState | None = None

    def load(self, symbols: list[str], end: date | None = None) -> MarketState:
        end = end or date.today()
        start = end - timedelta(days=self.cfg.data.history_days)
        u = self.cfg.universe
        extra = [u.benchmark, u.treasury_symbol] + ([u.cash_vehicle] if u.cash_vehicle else [])
        wanted = list(dict.fromkeys([*symbols, *MACRO_SYMBOLS, *extra]))
        self.frames = self.prices.history(wanted, start, end)
        bench = self.frames.get(u.benchmark)
        if bench is None or bench.empty:
            raise RuntimeError(f"no price data for {u.benchmark}; check the internet connection or data source")
        for s in dict.fromkeys([*symbols, *extra]):
            if s in self.frames:
                self.sym_data[s] = SymbolData(ind.compute(self.frames[s], bench["close"]))
        agent = MacroAgent(self.cfg.macro, self.calendar)
        self.macro = agent.compute_series(self.frames, bench.index)
        last = self.macro.iloc[-1]
        self.market = agent.state_from_row(last, self.macro.index[-1].date())
        if self.cfg.macro.use_cnn_fear_greed:
            fg = fetch_cnn_fear_greed(self.cache)
            if fg is not None:
                self.market.cnn_fear_greed = fg.score
        return self.market

    def row(self, symbol: str):
        sd = self.sym_data.get(symbol)
        if sd is None or len(sd.index) == 0:
            return None
        return sd.row_at(sd.index[-1])

    def last_close(self, symbol: str) -> float | None:
        r = self.row(symbol)
        return None if r is None else r["close"]

    def history(self, symbol: str, lookback: int = 260) -> pd.DataFrame | None:
        sd = self.sym_data.get(symbol)
        return None if sd is None else sd.frame.iloc[-lookback:]


class TradingEngine:
    def __init__(self, cfg: AppConfig, strategy: Strategy | None = None, broker: Broker | None = None,
                 data: MarketData | None = None, journal: Journal | None = None):
        self.cfg = cfg
        self.profile = cfg.risk_profile()
        self.strategy = strategy or load_strategy(cfg.strategy.name, cfg.strategy.params)
        self._broker = broker
        self.data = data or MarketData(cfg)
        self._journal = journal
        self.fund_provider = YahooFundamentals(self.data.cache) if cfg.data.fundamentals else None
        self.fund_agent = FundamentalAgent()
        self.news_collector = NewsCollector(
            (["finviz"] if cfg.news.finviz else []) + list(cfg.news.feeds), self.data.cache,
            cfg.data.contact_email, cfg.news.request_interval) if cfg.news.enabled else None
        self._news_budget = cfg.news.max_symbols_per_run
        self.reviewer = None
        if cfg.llm.enabled:
            from .llm import make_reviewer

            self.reviewer = make_reviewer(cfg.llm)

    # ---------------------------------------------------------------- helpers
    @property
    def broker(self) -> Broker:
        if self._broker is None:
            self._broker = make_broker(self.cfg.broker.name, **self.cfg.broker.options)
        return self._broker

    @property
    def journal(self) -> Journal:
        if self._journal is None:
            self._journal = Journal()
        return self._journal

    def universe(self, held: list[str] | None = None) -> list[str]:
        u = self.cfg.universe
        skip = {u.cash_vehicle, u.treasury_symbol} - {None}
        return [s for s in u.resolve() if s not in skip]

    def fundamentals(self, symbol: str):
        return self.fund_provider.snapshot(symbol) if self.fund_provider else None

    def quality(self, symbol: str):
        if is_etf(symbol):
            return self.fund_agent.evaluate(None if not self.fund_provider else self.fundamentals(symbol),
                                            self.profile.min_quality_for_dip)
        snap = self.fundamentals(symbol)
        return self.fund_agent.evaluate(snap, self.profile.min_quality_for_dip) if snap is not None else None

    def news(self, symbol: str) -> NewsReport | None:
        if self.news_collector is None:
            return None
        if self._news_budget <= 0:
            return None
        self._news_budget -= 1
        items = self.news_collector.collect(symbol, self.cfg.news.lookback_hours)
        return analyze_news(symbol, items, self.cfg.news, sources=dict(self.news_collector.last_status))

    def days_to_earnings(self, symbol: str, today: date) -> int | None:
        if is_etf(symbol):
            return None
        snap = self.fundamentals(symbol)
        nxt = snap.next_earnings_date if snap else None
        if nxt is None:
            return None
        return mc.trading_days_between(today, nxt)

    def portfolio_view(self, account: AccountSnapshot, positions: list[Position], state: StateStore | None,
                       today: date) -> PortfolioView:
        u = self.cfg.universe
        cash_eq = treasury = 0.0
        views: dict[str, PositionView] = {}
        equity = account.equity if account.equity > 0 else account.cash + sum(p.market_value for p in positions)
        for p in positions:
            if p.qty <= 0:
                continue
            px = p.market_price or self.data.last_close(p.symbol) or p.avg_cost
            value = p.qty * px
            if u.cash_vehicle and p.symbol == u.cash_vehicle:
                cash_eq += value
                continue
            if p.symbol == u.treasury_symbol and self.profile.treasury_sleeve > 0:
                treasury += value
                continue
            meta = state.positions.get(p.symbol) if state else None
            first = date.fromisoformat(meta.first_entry) if meta else mc.add_trading_days(today, -20)
            last = date.fromisoformat(meta.last_entry) if meta else first
            trim = date.fromisoformat(meta.last_trim) if meta and meta.last_trim else None
            views[p.symbol] = PositionView(
                symbol=p.symbol, qty=p.qty, avg_cost=p.avg_cost, price=px, weight=value / equity if equity else 0.0,
                days_held=max(0, mc.trading_days_between(first, today)), tranches=meta.tranches if meta else 1,
                target_weight=meta.target_weight if meta else None,
                last_entry_price=meta.last_entry_price if meta else p.avg_cost,
                days_since_entry=max(0, mc.trading_days_between(last, today)),
                days_since_trim=None if trim is None else max(0, mc.trading_days_between(trim, today)),
                last_trim_price=meta.last_trim_price if meta else None,
                peak_price=meta.peak_price if meta else px)
        peak = max(state.peak_equity if state else 0.0, equity)
        return PortfolioView(equity=equity, cash=account.cash, cash_equivalents=cash_eq, treasury_value=treasury,
                             positions=views, peak_equity=peak)

    def context(self, portfolio: PortfolioView, universe: list[str], mode: str) -> StrategyContext:
        today = self.data.market.day if self.data.market else date.today()
        return StrategyContext(
            day=today, universe=universe, market=self.data.market, portfolio=portfolio, profile=self.profile,
            rows=self.data.row, history=self.data.history, fundamentals=self.fundamentals, quality=self.quality,
            news=self.news, days_to_earnings=lambda s: self.days_to_earnings(s, today), mode=mode,
            scoring_weights=self.cfg.scoring.weights())

    def review_with_llm(self, ctx: StrategyContext, signals: list[Signal]) -> list[str]:
        if self.reviewer is None:
            return []
        notes = []
        buys = sorted((s for s in signals if s.action == Action.BUY), key=lambda s: -s.strength)
        for sig in buys[: self.cfg.llm.max_symbols_per_run]:
            verdict = self.reviewer.review(sig, ctx.market, ctx.news(sig.symbol), ctx.quality(sig.symbol))
            self.reviewer.apply(sig, verdict)
            if verdict is not None:
                notes.append(f"LLM {sig.symbol}: {'VETO ' + verdict.veto_reason if verdict.veto else f'{verdict.adjustment:+.2f}'}")
        return notes

    # ---------------------------------------------------------------- read-only
    def scan(self, symbols: list[str] | None = None, portfolio: PortfolioView | None = None
             ) -> tuple[MarketState, list[Signal], StrategyContext]:
        universe = symbols or self.universe()
        held = list(portfolio.positions) if portfolio else []
        market = self.data.load(list(dict.fromkeys([*universe, *held])))
        pf = portfolio or PortfolioView(equity=self.cfg.backtest.initial_cash, cash=self.cfg.backtest.initial_cash,
                                        peak_equity=self.cfg.backtest.initial_cash)
        ctx = self.context(pf, universe, "scan")
        self.strategy.on_start(universe)
        signals = self.strategy.on_bar(ctx)
        return market, signals, ctx

    def analyze_symbol(self, symbol: str) -> dict[str, Any]:
        market = self.data.load([symbol])
        row = self.data.row(symbol)
        if row is None:
            raise RuntimeError(f"no price data for {symbol}")
        quality = self.quality(symbol)
        tech: TechnicalView = TechnicalAgent(breakouts_enabled=self.profile.breakouts_enabled).evaluate(
            symbol, row, market.regime, quality=quality.verdict if quality else ("ETF" if is_etf(symbol) else None),
            is_broad_etf=symbol in ("SPY", "QQQ", "IWM", "DIA", "VTI", "VOO", "IVV"))
        news = self.news(symbol)
        dte = self.days_to_earnings(symbol, market.day)
        pf = PortfolioView(equity=100_000, cash=100_000, peak_equity=100_000)
        ctx = self.context(pf, [symbol], "scan")
        signal = self.strategy.on_bar(ctx) if self.strategy.name != "risk_aware" else [
            s for s in [self.strategy.entry_signal(ctx, symbol)] if s]  # type: ignore[attr-defined]
        return {"market": market, "row": row.to_dict(), "technical": tech, "quality": quality, "news": news,
                "days_to_earnings": dte, "signals": signal}

    # ---------------------------------------------------------------- the cycle
    def run_cycle(self, mode: str | None = None, allow_live: bool = False,
                  confirm: Callable[[CycleReport], bool] | None = None) -> CycleReport:
        mode = mode or self.cfg.execution.mode
        report = CycleReport(mode=mode, broker=self.cfg.broker.name, started=datetime.now(timezone.utc))
        guards = Guards(self.cfg)
        broker = self.broker
        try:
            broker.connect()
        except BrokerError as exc:
            report.errors.append(str(exc))
            return report
        try:
            guards.preflight(mode, broker.is_paper, allow_live)
        except GuardError as exc:
            report.errors.append(str(exc))
            broker.close()
            return report
        try:
            account = broker.account()
            positions = broker.positions()
        except BrokerError as exc:
            report.errors.append(f"broker read failed: {exc}")
            broker.close()
            return report
        report.account = account
        today = mc.now_ny().date()
        state = StateStore(broker.name, account.account_id)
        report.notes += state.reconcile(positions, broker.get_order, today)

        universe = self.universe()
        held = [p.symbol for p in positions]
        self.data.load(list(dict.fromkeys([*universe, *held])))
        market = self.data.market
        report.market = market
        pf = self.portfolio_view(account, positions, state, today)
        state.peak_equity = pf.peak_equity
        report.portfolio = pf
        ctx = self.context(pf, universe, mode)
        self.strategy.on_start(universe)
        signals = self.strategy.on_bar(ctx)
        report.notes += self.review_with_llm(ctx, signals)
        report.signals = signals

        # prices for sizing and limits: broker quotes, falling back to the latest bar
        u = self.cfg.universe
        need = {s.symbol for s in signals} | set(pf.positions) | ({u.cash_vehicle} if u.cash_vehicle else set()) | {
            u.treasury_symbol}
        quotes: dict[str, Quote] = {}
        try:
            quotes = broker.quotes(sorted(need))
        except BrokerError as exc:
            report.notes.append(f"broker quotes unavailable ({exc}); using the latest bars")
        prices = {}
        for s in need:
            px = reference_price(quotes.get(s)) or self.data.last_close(s)
            if px:
                prices[s] = float(px)
        risk = RiskManager(self.profile, u, self.cfg.execution, manage_cash=self.cfg.execution.manage_cash_vehicle)
        decision = risk.plan(signals, ctx, prices)
        report.decision = decision
        report.notes += decision.notes
        checked = guards.check(decision.orders, quotes, pf.equity, self.profile.max_position)
        report.approved, report.blocked = checked.approved, checked.blocked

        run_id = self.journal.start_run(mode=mode, broker=broker.describe(), account=account.account_id,
                                        strategy=self.strategy.name, regime=market.regime.value, equity=account.equity,
                                        cash=account.cash, stock_exposure=pf.stock_exposure,
                                        fear_greed=market.fear_greed, vix=market.vix)
        report.run_id = run_id
        rejected = {id(sig): why for sig, why in decision.rejected}
        accepted_syms = {(o.symbol, o.side.value) for o in decision.orders}
        for sig in signals:
            ok = (sig.symbol, "BUY" if sig.action == Action.BUY else "SELL") in accepted_syms
            self.journal.log_decision(run_id, sig.symbol, sig.action.value, sig.strength, sig.reason, sig.setups, ok,
                                      rejected.get(id(sig), ""))

        go = mode != "dry-run" and report.approved
        if go and not broker.is_paper and self.cfg.execution.confirm_live:
            go = bool(confirm and confirm(report))
            if not go:
                report.notes.append("live orders not confirmed; nothing sent")
        for o in report.approved:
            tag = f"{o.tag}{o.tranche or ''}"
            coid = client_order_id(today, o.symbol, o.side, tag)
            req = OrderRequest(symbol=o.symbol, side=o.side, qty=o.qty,
                               order_type=OrderType.LIMIT if o.limit_price else OrderType.MARKET,
                               limit_price=o.limit_price, client_order_id=coid,
                               extended_hours=self.cfg.execution.extended_hours, reason=o.reason, tag=o.tag)
            if not go:
                self.journal.log_order(run_id, coid, None, o.symbol, o.side.value, o.qty, o.limit_price, o.tag,
                                       OrderStatus.DRY_RUN.value, reason=o.reason)
                continue
            if self.journal.seen_client_order_id(coid):
                report.notes.append(f"skipped {o.symbol} {o.side.value}: already sent today ({coid})")
                continue
            try:
                res = broker.place_order(req)
                report.submitted.append(res)
                state.add_pending(res.order_id, o.symbol, o.side.value, o.tag, o.limit_price, o.target_weight, today)
                if res.status == OrderStatus.FILLED:
                    state.reconcile(broker.positions(), broker.get_order, today)
                self.journal.log_order(run_id, coid, res.order_id, o.symbol, o.side.value, o.qty, o.limit_price, o.tag,
                                       res.status.value, res.message, o.reason)
            except BrokerError as exc:
                report.errors.append(f"{o.symbol} {o.side.value}: {exc}")
                self.journal.log_order(run_id, coid, None, o.symbol, o.side.value, o.qty, o.limit_price, o.tag,
                                       "ERROR", str(exc), o.reason)
        self.journal.commit()
        state.save()
        broker.close()
        if report.submitted:
            lines = [f"{r.side.value} {r.qty:g} {r.symbol} @ {r.limit_price or 'MKT'} [{r.status.value}]"
                     for r in report.submitted]
            notify(self.cfg.notify, f"Risk-Aware Trading Agents ({mode}, {market.regime.value}):\n" + "\n".join(lines))
        if report.errors:
            notify(self.cfg.notify, "Risk-Aware Trading Agents errors:\n" + "\n".join(report.errors), event="errors")
        return report
