"""Experiments on the frozen market: one call runs a strategy, checks it for look-ahead and writes a record that can
be cited.

``run_experiment(Experiment(strategy="swing_rsi2", news=True))``:

1. backtests the strategy on the bundled data: after each day's close the strategy sees that day's indicators, past
   prices and the news published before the close; its orders fill at the next open (swing trading on daily bars:
   positions are held for days or weeks, never opened and closed within a day);
2. runs it again on copies of the market where every price and headline after a cut day is replaced by random ones,
   for three cut days. A strategy that only uses what it may know decides exactly the same way up to each cut;
   a difference is reported with the first day and decision that changed (the leak check);
3. compares it with SPY and the equal-weight universe: return, Sharpe, drawdown, beta, alpha, a t-statistic and
   bootstrap intervals, and the same split into an in-sample and an out-of-sample part when asked;
4. writes a folder: manifest.json (the data's SHA-256, the package version, every setting, the strategy's source and
   its hash), metrics.json, equity.csv, trades.csv, decisions.csv, report.md and equity.png.

``execution="plain"`` (the default) trades exactly what the strategy says: equal-size positions, at most
``max_positions``, no risk rules on top (see ``execution.PlainPlanner``). ``execution="risk_aware"`` adds the
package's risk manager and its cash floor, regime limits and caps.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import platform
import random
import re
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..backtest import metrics as M
from ..backtest.engine import Backtester, BacktestResult, describe_costs
from ..config import RISKAWARE_HOME, AppConfig, BacktestConfig, ExecutionConfig
from ..data.events import EventCalendar
from ..news.history import NY, NewsArchive, PointInTimeNews, decision_time
from ..news.relevance import company_names
from ..risk import RiskManager
from ..strategy import Strategy, load_strategy
from ..types import Signal
from ..universe import preset_symbols
from . import stats as S
from .data import FrozenMarket, bundled_market, dataset_info
from .day import NO_LOCAL_NEWS, _WithExtra, research_config
from .execution import PlainPlanner
from .periods import DEFAULT_PERIOD, get_period

RUNS_DIR = RISKAWARE_HOME / "research" / "runs"
CASH_VEHICLES = {"SGOV", "BIL", "SHV"}
KEY_METRICS = ["total_return", "cagr", "volatility", "sharpe", "sortino", "max_drawdown", "calmar", "win_rate",
               "profit_factor", "round_trips", "avg_holding_days", "turnover", "avg_exposure"]
PROTOCOL = {
    "decision": "after each trading day's close; the strategy sees that day's bar and indicators, earlier bars, and "
                "news published before 16:00 New York (13:00 on early-close days)",
    "execution": "orders fill at the next trading day's open plus slippage; positions are held across days (swing "
                 "trading on daily bars, no intraday trading)",
    "prices": "total-return prices (split- and dividend-adjusted) from the bundled file; indicators only look back",
    "cash": "uninvested cash earns the daily return of the T-bill ETF BIL",
    "news": "the period's bundled headlines, run through the same relevance filter and cross-verification as live "
            "trading",
}


COST_MODELS = ("fixed", "realistic")


def protocol(period: str | None = None, costs: str = "fixed", slippage_bps: float = 5.0) -> dict[str, str]:
    """How a backtest on a period decides and fills; the news line names that period's sources and, with realistic
    costs, the execution line gives the cost rule."""
    p = get_period(period)
    out = {**PROTOCOL, "news": f"{p.news_feeds} for {p.name}, run through the same relevance filter and "
                               "cross-verification as live trading"}
    if costs == "realistic":
        rule = describe_costs(BacktestConfig(cost_model="realistic", slippage_bps=slippage_bps))
        out["execution"] = (f"orders fill at the next trading day's open with realistic costs: {rule}; positions are "
                            "held across days (swing trading on daily bars, no intraday trading)")
    return out


@dataclass
class Experiment:
    """One experiment's settings. Dates default to the bundled experiment year."""

    strategy: str = "risk_aware"  # a built-in name, a research baseline, or path/to/file.py[:Class]
    params: dict[str, Any] = field(default_factory=dict)
    period: str = DEFAULT_PERIOD  # a bundled period: 2026, 2022 or 2020 (see periods.py)
    start: str | None = None
    end: str | None = None
    universe: str | None = "megacap"  # megacap, tech, defensive, core, sectors, etf
    symbols: list[str] | None = None
    news: bool = True
    execution: str = "plain"  # plain or risk_aware
    mode: str = "long_only"  # plain execution: long_only, long_short or margin (execution.py, docs/short-margin.md)
    mode_params: dict[str, float] | None = None  # override the mode's rules, e.g. {"max_short": 0.1}
    profile: str = "balanced"  # risk profile for execution="risk_aware"
    max_positions: int = 10
    position_size: float | None = None
    cash: float = 100_000.0
    slippage_bps: float = 5.0
    commission_per_share: float = 0.0
    costs: str = "fixed"  # fixed (slippage_bps on every fill) or realistic (+ gap, impact, volume cap; docs/costs.md)
    split: str | None = None  # first day of the out-of-sample part
    leak_check: bool = True
    leak_cuts: int = 3
    label: str | None = None
    extra_news: list[dict[str, Any]] | None = None  # headlines added for this experiment (ts, symbol, headline)
    seed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExperimentResult:
    run_id: str
    folder: Path | None
    experiment: Experiment
    strategy: str
    start: date
    end: date
    symbols: list[str]
    metrics: dict[str, float]
    spy: dict[str, float]
    equal_weight: dict[str, float]
    stats: dict[str, float]
    periods: dict[str, dict[str, Any]]
    leak: dict[str, Any] | None
    trades: pd.DataFrame
    decisions: pd.DataFrame
    equity: pd.DataFrame
    notes: list[str]
    rejections: dict[str, int]
    execution: dict[str, Any]
    backtest: BacktestResult | None = None

    def summary(self) -> dict[str, Any]:
        """A short JSON-friendly summary (what the MCP tool and the CLI print)."""
        t = self.trades
        sells = t[(t["side"] == "SELL") & t["pnl"].notna()] if len(t) else t
        best = sells.nlargest(3, "pnl") if len(sells) else sells
        worst = sells.nsmallest(3, "pnl") if len(sells) else sells

        def trade_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
            return [{"day": str(r["date"])[:10], "symbol": r["symbol"], "pnl": _r(r["pnl"]),
                     "held_days": _r(r["holding_days"]), "reason": r["reason"]} for _, r in df.iterrows()]

        return {
            "run_id": self.run_id, "folder": str(self.folder) if self.folder else None,
            "strategy": self.strategy, "params": self.experiment.params, "period": f"{self.start} to {self.end}",
            "universe": self.experiment.universe if not self.experiment.symbols else "custom",
            "stocks": len(self.symbols), "news": self.experiment.news, "execution": self.execution,
            "costs": {k: _r(v) if isinstance(v, float) else v
                      for k, v in (self.backtest.costs if self.backtest else {}).items()},
            "metrics": {k: _r(self.metrics.get(k)) for k in KEY_METRICS if k in self.metrics},
            "spy": {k: _r(self.spy.get(k)) for k in ("total_return", "sharpe", "max_drawdown", "volatility")},
            "equal_weight": {k: _r(self.equal_weight.get(k)) for k in ("total_return", "sharpe", "max_drawdown")},
            "vs_spy": {k: _r(v) for k, v in self.stats.items()},
            "periods": {k: {kk: _r(vv) if isinstance(vv, float) else vv for kk, vv in v.items()}
                        for k, v in self.periods.items()},
            "leak_check": self.leak,
            "trades": {"orders": int(len(t)), "closed": int(len(sells)), "best": trade_rows(best),
                       "worst": trade_rows(worst)},
            "refused": dict(list(self.rejections.items())[:6]),
            **({"account": acct} if (acct := self.account()) else {}),
            "notes": self.notes,
        }

    def account(self) -> dict[str, Any] | None:
        """long_short and margin runs: what the longs and the shorts made, the borrow fees, margin interest and
        T-bill interest (all as shares of the starting cash), margin calls and exposures. None when long only."""
        a = self.backtest.account if self.backtest is not None else {}
        if not a:
            return None
        cash = float(self.experiment.cash) or 1.0
        out: dict[str, Any] = {"mode": a["mode"]}
        out.update({k: _r(a[k] / cash) for k in ("pnl_long", "pnl_short", "borrow_fees", "margin_interest",
                                                 "cash_interest")})
        out.update({k: _r(a[k]) for k in ("avg_long", "avg_short", "avg_gross", "max_gross")})
        out.update(margin_calls=a["margin_calls"], margin_call_days=a["margin_call_days"], wiped_out=a["wiped_out"])
        return out


def _r(v: Any, nd: int = 4) -> Any:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, nd)


# ----------------------------------------------------------------------------------- recording
class Recorder(Strategy):
    """Wraps a strategy and keeps every signal it returns, by day."""

    def __init__(self, inner: Strategy):  # noqa: D107 - no parameter check: the inner strategy did it
        self.inner = inner
        self.name = inner.name
        self.description = getattr(inner, "description", "")
        self.params = getattr(inner, "params", {})
        self.log: dict[date, list[dict[str, Any]]] = {}

    def on_start(self, universe: list[str]) -> None:
        self.inner.on_start(universe)

    def on_bar(self, ctx: Any) -> list[Signal]:
        signals = list(self.inner.on_bar(ctx) or [])
        self.log[ctx.day] = [{"symbol": s.symbol, "action": s.action.value, "strength": float(s.strength or 0.0),
                              "fraction": s.fraction, "target_weight": s.target_weight, "emergency": bool(s.emergency),
                              "reason": s.reason} for s in signals]
        return signals

    def warmup_bars(self) -> int:
        return self.inner.warmup_bars()


class RecordingPlanner:
    """Wraps a planner (plain or the risk manager) and keeps the orders and refusals of each day."""

    def __init__(self, inner: Any):
        self.inner = inner
        self.margin = getattr(inner, "margin", None)  # the backtester trades signed positions when set
        self.log: dict[date, list[dict[str, Any]]] = {}

    def plan(self, signals: list[Signal], ctx: Any, prices: dict[str, float]) -> Any:
        d = self.inner.plan(signals, ctx, prices)
        rows = [{"symbol": o.symbol, "side": o.side.value, "qty": float(o.qty), "tag": o.tag, "reason": o.reason}
                for o in d.orders]
        rows += [{"symbol": s.symbol, "side": "REFUSED", "qty": 0.0, "tag": "", "reason": why} for s, why in d.rejected]
        self.log[ctx.day] = rows
        return d


# ----------------------------------------------------------------------------------- setup
def _symbols(exp: Experiment, market: FrozenMarket) -> list[str]:
    if exp.symbols:
        syms = [s.upper() for s in exp.symbols]
    else:
        syms = preset_symbols(exp.universe or "megacap")
    syms = [s for s in dict.fromkeys(syms) if s not in CASH_VEHICLES]
    missing = [s for s in syms if not market.has(s)]
    if missing:
        raise ValueError(f"not in the bundled data: {', '.join(missing)}; the bundle has {', '.join(market.symbols)}")
    if not syms:
        raise ValueError("no symbols to trade")
    return syms


def _config(exp: Experiment, symbols: list[str]) -> AppConfig:
    if exp.costs not in COST_MODELS:
        raise ValueError(f"costs must be one of {', '.join(COST_MODELS)}, not {exp.costs!r}")
    cfg = research_config({"profile": exp.profile, "backtest": {
        "initial_cash": float(exp.cash), "slippage_bps": float(exp.slippage_bps),
        "commission_per_share": float(exp.commission_per_share), "cost_model": exp.costs}})
    cfg.universe.preset, cfg.universe.symbols = None, list(symbols)
    return cfg


def _dates(exp: Experiment, market: FrozenMarket) -> tuple[date, date]:
    start = date.fromisoformat(exp.start) if exp.start else market.experiment_start
    end = date.fromisoformat(exp.end) if exp.end else market.last_day
    if start < market.first_day + timedelta(days=200):
        raise ValueError(f"start on or after {market.first_day + timedelta(days=200)}: the year before it warms up "
                         f"the indicators (the {market.period} data starts on {market.first_day})")
    if end > market.last_day:
        raise ValueError(f"the bundled {market.period} data ends on {market.last_day}")
    if end <= start:
        raise ValueError("end must be after start")
    return start, end


def _planner(exp: Experiment, cfg: AppConfig) -> Any:
    if exp.execution == "plain":
        return PlainPlanner(max_positions=int(exp.max_positions), position_size=exp.position_size, mode=exp.mode,
                            mode_params=exp.mode_params)
    if exp.mode != "long_only":
        raise ValueError("short selling and margin (mode long_short or margin) need execution='plain'")
    if exp.execution == "risk_aware":
        bt = cfg.backtest
        x = ExecutionConfig(order_type="LIMIT", limit_offset=bt.max_chase, allow_fractional=False)
        return RiskManager(cfg.risk_profile(), cfg.universe, x, manage_cash=True, options=cfg.options)
    raise ValueError("execution must be 'plain' or 'risk_aware'")


def _archive(exp: Experiment) -> NewsArchive:
    base = NewsArchive(NO_LOCAL_NEWS, bundled=True, bundle_path=get_period(exp.period).news_path)
    return _WithExtra(base, exp.extra_news) if exp.extra_news else base


def _news(cfg: AppConfig, archive: NewsArchive, symbols: list[str]) -> PointInTimeNews | None:
    stocks = [s for s in symbols if s in archive.symbols()]
    pit = PointInTimeNews(archive, cfg.news, stocks, names=lambda s: company_names(s, fetch=False))
    return pit if pit.symbols else None


def _run(market: FrozenMarket, cfg: AppConfig, exp: Experiment, factory: Callable[[], Strategy], symbols: list[str],
         start: date, end: date, archive: NewsArchive | None,
         bt: Backtester | None = None) -> tuple[BacktestResult, Recorder, RecordingPlanner, Backtester]:
    planner = RecordingPlanner(_planner(exp, cfg))
    if bt is None:
        news = _news(cfg, archive, symbols) if (exp.news and archive is not None) else None
        bt = Backtester(cfg, market, EventCalendar(cfg.macro.events, cache=None), news=news)
    bt.planner = planner
    random.seed(exp.seed)
    np.random.seed(exp.seed % (2**32))
    rec = Recorder(factory())
    res = bt.run(rec, start, end, universe=symbols, initial_cash=exp.cash)
    return res, rec, planner, bt


# ----------------------------------------------------------------------------------- leak check
FAKE_HEADLINES = [
    ("{n} soars after blowout quarter and raised full-year guidance", "benzinga", "Benzinga", ""),
    ("{n} plunges after SEC opens fraud investigation into its accounting", "benzinga", "Benzinga", ""),
    ("{n} beats estimates and announces a record buyback", "rss", "Reuters", ""),
    ("{n} CEO resigns as auditors flag material weakness", "rss", "Reuters", ""),
    ("8-K: Item 1.03 Bankruptcy or receivership", "sec", "SEC EDGAR", "items:1.03"),
    ("{n} wins a major government contract, shares jump", "benzinga", "Benzinga", ""),
]


class _PoisonedNews(NewsArchive):
    """The experiment's news until a cut time, then random invented headlines (good, bad and severe)."""

    def __init__(self, base: NewsArchive, cut: datetime, days: list[date], seed: int):
        super().__init__(base.root, bundled=base.bundled, bundle_paths=base.bundle_paths)
        self._base, self._cut, self._days, self._seed = base, cut, days, seed

    def records(self, symbol: str) -> list[dict]:
        sym = symbol.upper()
        keep = [r for r in self._base.records(sym)
                if datetime.fromisoformat(str(r["ts"]).replace("Z", "+00:00")) < self._cut]
        rng = np.random.default_rng([self._seed, sum(map(ord, sym))])
        names = company_names(sym, fetch=False) or [sym]
        fakes = []
        for d in self._days:
            for _ in range(int(rng.integers(0, 3))):
                text, feed, source, summary = FAKE_HEADLINES[int(rng.integers(0, len(FAKE_HEADLINES)))]
                when = datetime.combine(d, time(int(rng.integers(7, 20)), int(rng.integers(0, 60))), NY)
                if when.astimezone(timezone.utc) < self._cut:
                    continue
                rec = {"ts": when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), "symbol": sym,
                       "headline": text.format(n=names[0]), "source": source, "feed": feed}
                if summary:
                    rec["summary"] = summary
                fakes.append(rec)
        return sorted([*keep, *fakes], key=lambda r: r["ts"])

    def symbols(self) -> list[str]:
        return self._base.symbols()


def _key_log(rec: Recorder, plan: RecordingPlanner, upto: date | None = None) -> list[tuple]:
    out = []
    for d in sorted(set(rec.log) | set(plan.log)):
        if upto is not None and d > upto:
            break
        sig = tuple((s["symbol"], s["action"], round(s["strength"], 9), s["fraction"], s["target_weight"],
                     s["emergency"], s["reason"]) for s in rec.log.get(d, []))
        orders = tuple((o["symbol"], o["side"], round(o["qty"], 6), o["tag"]) for o in plan.log.get(d, []))
        out.append((d, sig, orders))
    return out


def _first_difference(a: list[tuple], b: list[tuple]) -> dict[str, Any] | None:
    for x, y in zip(a, b, strict=False):
        if x != y:
            what = "signals" if x[1] != y[1] else "orders"
            i = 1 if what == "signals" else 2
            return {"day": str(x[0]), "what": what, "original": [list(v) for v in x[i]][:5],
                    "with_a_different_future": [list(v) for v in y[i]][:5]}
    if len(a) != len(b):
        return {"day": None, "what": "number of decision days", "original": len(a), "with_a_different_future": len(b)}
    return None


def cut_days(days: list[date], n: int) -> list[date]:
    """The cut days of a leak check: 30%, 55% and 80% of the way through the decision days (for n = 3)."""
    n = max(1, int(n))
    return sorted({days[int(len(days) * (0.3 + 0.5 * k / max(1, n - 1)))] if n > 1 else days[len(days) // 2]
                   for k in range(n)})


def leak_check(market: FrozenMarket, cfg: AppConfig, exp: Experiment, factory: Callable[[], Strategy],
               symbols: list[str], start: date, end: date, base: tuple[Recorder, RecordingPlanner],
               archive: NewsArchive | None) -> dict[str, Any]:
    """Re-run with a random future after several cut days and compare the decisions up to each cut."""
    days = market.trading_days(start, end)[:-1]  # decision days
    if len(days) < 10:
        return {"verdict": "skipped", "why": "fewer than 10 decision days"}
    cuts = cut_days(days, exp.leak_cuts)
    checks = []
    for k, cut in enumerate(cuts):
        pm = market.poisoned(cut, seed=exp.seed + 101 + k)
        pa = None
        if archive is not None and exp.news:
            after = [d for d in market.trading_days(cut, None) if d > cut]
            pa = _PoisonedNews(archive, decision_time(cut), after, seed=exp.seed + 202 + k)
        # decisions after the cut are not compared, so the run can stop the day after it
        stop = market.next_trading_day(cut) or end
        _, rec, plan, _ = _run(pm, cfg, exp, factory, symbols, start, min(stop, end), pa)
        diff = _first_difference(_key_log(*base, upto=cut), _key_log(rec, plan, upto=cut))
        checks.append({"cut": str(cut), "decision_days_compared": sum(1 for d in days if d <= cut),
                       "same": diff is None, "first_difference": diff})
    if all(c["same"] for c in checks):
        return {"verdict": "pass", "cuts": checks,
                "meaning": "with every price and headline after each cut day replaced by random ones, the strategy "
                           "made exactly the same decisions up to the cut: it does not see the future"}
    # a difference: is the strategy simply not repeatable?
    _, rec2, plan2, _ = _run(market, cfg, exp, factory, symbols, start, end, archive)
    again = _first_difference(_key_log(*base), _key_log(rec2, plan2))
    if again is not None:
        return {"verdict": "not repeatable", "cuts": checks, "rerun_difference": again,
                "meaning": "two runs on the same data decided differently (randomness or state outside the "
                           "strategy), so the leak check cannot tell; seed any randomness or remove it"}
    return {"verdict": "fail", "cuts": checks,
            "meaning": "decisions before a cut changed when only the data after the cut changed: the strategy (or "
                       "something it calls) reads the future. The first difference is listed for each cut"}


# ----------------------------------------------------------------------------------- the run
def _source(strategy: Strategy, code: str | None) -> tuple[str | None, str | None]:
    if code:
        return code, hashlib.sha256(code.encode()).hexdigest()
    try:
        src = inspect.getsource(type(strategy))
    except (OSError, TypeError):
        return None, None
    return src, hashlib.sha256(src.encode()).hexdigest()


def _git_commit() -> str | None:
    root = Path(__file__).resolve().parents[3]
    if not (root / ".git").exists():
        return None
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "--short=12", "HEAD"], capture_output=True,
                             text=True, timeout=5, check=False)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def run_experiment(exp: Experiment, factory: Callable[[], Strategy] | None = None, market: FrozenMarket | None = None,
                   save: bool = True, out_dir: str | Path | None = None, code: str | None = None,
                   chart: bool = True) -> ExperimentResult:
    """Run one experiment (see the module docstring). ``factory`` makes a fresh strategy for each run (the leak
    check needs several); by default it loads ``exp.strategy`` with ``exp.params``. ``code`` is the strategy's source
    when it came as text (the strategy lab), so the record keeps it."""
    if market is None:
        market = bundled_market(exp.period)
    elif market.period != get_period(exp.period).name:
        raise ValueError(f"the market is the {market.period} period but the experiment asks for {exp.period}")
    symbols = _symbols(exp, market)
    start, end = _dates(exp, market)
    cfg = _config(exp, symbols)
    if factory is None:
        from .strategies import LONG_SHORT_STRATEGIES, RESEARCH_STRATEGIES

        def factory() -> Strategy:
            params = dict(exp.params)
            cls = RESEARCH_STRATEGIES.get(exp.strategy)
            if cls is not None:
                params.setdefault("news", bool(exp.news))  # the news switch follows the experiment unless set
                return cls(**params)
            if exp.strategy in LONG_SHORT_STRATEGIES:
                return LONG_SHORT_STRATEGIES[exp.strategy](**params)
            return load_strategy(exp.strategy, params)

    archive = _archive(exp) if exp.news else None
    res, rec, plan, bt = _run(market, cfg, exp, factory, symbols, start, end, archive)
    first = factory()
    strat_name = res.strategy or first.name
    leak = leak_check(market, cfg, exp, factory, symbols, start, end, (rec, plan), archive) if exp.leak_check else None

    idx = res.equity.index
    bil = market.history(["BIL"]).get("BIL")
    rf_daily = bil["close"].pct_change().reindex(idx).fillna(0.0).clip(-0.01, 0.01) if bil is not None else None
    closes = {s: f["close"] for s, f in market.history(symbols).items()}
    ew = S.equal_weight_curve(closes, idx, exp.cash)
    metrics = dict(res.metrics)
    spy = dict(res.benchmark_metrics)
    ew_metrics = M.summarize(ew, rf_daily)
    stats = S.excess_stats(res.equity, res.benchmark, rf_daily, seed=exp.seed)
    periods: dict[str, dict[str, Any]] = {}
    if exp.split:
        cut = pd.Timestamp(exp.split)
        for name, mask in (("in_sample", idx < cut), ("out_of_sample", idx >= cut)):
            if mask.sum() > 5:
                e, b = res.equity[mask], res.benchmark[mask]
                m = M.summarize(e, rf_daily)
                mb = M.summarize(b, rf_daily)
                periods[name] = {"from": str(e.index[0].date()), "to": str(e.index[-1].date()),
                                 "total_return": m.get("total_return"), "sharpe": m.get("sharpe"),
                                 "max_drawdown": m.get("max_drawdown"), "spy_total_return": mb.get("total_return"),
                                 "spy_sharpe": mb.get("sharpe")}
    equity = pd.DataFrame({"equity": res.equity, "spy": res.benchmark, "equal_weight": ew, "cash_ratio": res.cash_ratio,
                           "exposure": res.exposure, "positions": res.positions, "regime": res.regimes})
    if res.account:  # long_short and margin: exposure is net; the longs, the shorts and gross as shares of equity
        equity = equity.join(res.account["series"])
    decisions = _decisions_frame(rec, plan)
    run_id = _run_id(exp, strat_name)
    execution = PlainPlanner(int(exp.max_positions), exp.position_size, mode=exp.mode,
                             mode_params=exp.mode_params).describe() if exp.execution == "plain" else {
        "execution": "risk_aware", "profile": exp.profile,
        "orders": "the risk manager's limit orders at the next open (cash floor, regime limits, caps, tranches)"}
    execution["costs"] = f"{exp.costs}: {describe_costs(cfg.backtest)}"
    result = ExperimentResult(
        run_id=run_id, folder=None, experiment=exp, strategy=strat_name, start=res.start, end=res.end,
        symbols=symbols, metrics=metrics, spy=spy, equal_weight=ew_metrics, stats=stats, periods=periods, leak=leak,
        trades=res.trades, decisions=decisions, equity=equity, notes=list(res.notes), rejections=dict(res.rejections),
        execution=execution, backtest=res)
    if save:
        folder = Path(out_dir).expanduser() if out_dir else RUNS_DIR / run_id
        save_run(result, folder, first, code, chart=chart)
    return result


def _decisions_frame(rec: Recorder, plan: RecordingPlanner) -> pd.DataFrame:
    rows = []
    for d in sorted(set(rec.log) | set(plan.log)):
        for s in rec.log.get(d, []):
            rows.append({"date": d, "kind": "signal", "symbol": s["symbol"], "action": s["action"],
                         "strength": s["strength"], "fraction": s["fraction"], "target_weight": s["target_weight"],
                         "qty": None, "reason": s["reason"]})
        for o in plan.log.get(d, []):
            kind = "refused" if o["side"] == "REFUSED" else "order"
            rows.append({"date": d, "kind": kind, "symbol": o["symbol"], "action": o["side"] if kind == "order" else "",
                         "strength": None, "fraction": None, "target_weight": None,
                         "qty": o["qty"] if kind == "order" else None, "reason": o["reason"]})
    return pd.DataFrame(rows, columns=["date", "kind", "symbol", "action", "strength", "fraction", "target_weight",
                                       "qty", "reason"])


def _run_id(exp: Experiment, name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (exp.label or name).lower()).strip("-")[:40] or "run"
    news = "news" if exp.news else "nonews"
    costs = "-realistic" if exp.costs == "realistic" else ""
    mode = "" if exp.mode == "long_only" else "-" + exp.mode.replace("_", "-")
    return f"{datetime.now():%Y%m%d-%H%M%S}-{slug}-{get_period(exp.period).name}-{news}{costs}{mode}"


# ----------------------------------------------------------------------------------- the record
def reproduce_command(exp: Experiment) -> str:
    parts = ["riskaware research run", f"--strategy {exp.strategy}"]
    if get_period(exp.period).name != DEFAULT_PERIOD:
        parts.append(f"--period {get_period(exp.period).name}")
    if exp.params:
        parts.append(f"--params '{json.dumps(exp.params, sort_keys=True)}'")
    if exp.start:
        parts.append(f"--start {exp.start}")
    if exp.end:
        parts.append(f"--end {exp.end}")
    parts.append(f"--symbols {','.join(exp.symbols)}" if exp.symbols else f"--universe {exp.universe}")
    parts.append("--news" if exp.news else "--no-news")
    parts.append(f"--execution {exp.execution}")
    if exp.mode != "long_only":
        parts.append(f"--mode {exp.mode}")
        if exp.mode_params:
            parts.append(f"--mode-params '{json.dumps(exp.mode_params, sort_keys=True)}'")
    if exp.execution == "plain":
        parts.append(f"--max-positions {exp.max_positions}")
        if exp.position_size:
            parts.append(f"--position-size {exp.position_size}")
    else:
        parts.append(f"--profile {exp.profile}")
    if exp.split:
        parts.append(f"--split {exp.split}")
    if exp.slippage_bps != 5.0:
        parts.append(f"--slippage-bps {exp.slippage_bps}")
    if exp.costs != "fixed":
        parts.append(f"--costs {exp.costs}")
    return " ".join(parts)


def save_run(result: ExperimentResult, folder: Path, strategy: Strategy, code: str | None = None,
             chart: bool = True) -> Path:
    folder = Path(folder)
    if folder.exists() and any(folder.iterdir()):
        n = 2
        while (folder.parent / f"{folder.name}-{n}").exists():
            n += 1
        folder = folder.parent / f"{folder.name}-{n}"
    folder.mkdir(parents=True, exist_ok=True)
    result.folder = folder
    exp = result.experiment
    src, src_hash = _source(strategy, code)
    if src:
        (folder / "strategy.py").write_text(src if src.endswith("\n") else src + "\n", encoding="utf-8")
    import riskaware

    manifest = {
        "run_id": result.run_id,
        "created_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "riskaware": {"version": riskaware.__version__, "git_commit": _git_commit()},
        "python": platform.python_version(), "platform": platform.platform(),
        "packages": {"pandas": pd.__version__, "numpy": np.__version__},
        "dataset": dataset_info(exp.period),
        "experiment": {**exp.to_dict(), "start": str(result.start), "end": str(result.end)},
        "symbols": result.symbols,
        "strategy": {"name": result.strategy, "class": type(strategy).__name__,
                     "description": getattr(strategy, "description", ""), "params": getattr(strategy, "params", {}),
                     "source_sha256": src_hash, "source_file": "strategy.py" if src else None},
        "execution": result.execution,
        "protocol": protocol(exp.period, exp.costs, exp.slippage_bps),
        "leak_check": result.leak,
        "reproduce": reproduce_command(exp) if not code else "riskaware research run --code strategy.py (same settings)",
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")
    metrics = {"strategy": result.metrics, "spy": result.spy, "equal_weight": result.equal_weight,
               "vs_spy": result.stats, "periods": result.periods}
    if exp.costs != "fixed" and result.backtest is not None:
        metrics["costs"] = result.backtest.costs  # what the fills paid (a fixed run pays slippage_bps on each)
    if result.account():
        metrics["account"] = result.account()
    (folder / "metrics.json").write_text(json.dumps(metrics, indent=2, default=_json_num) + "\n", encoding="utf-8")
    eq = result.equity.copy()
    eq.index = [d.date() for d in eq.index]
    eq.index.name = "date"
    eq.to_csv(folder / "equity.csv", float_format="%.6f")
    tr = result.trades.copy()
    if len(tr):
        tr["date"] = pd.to_datetime(tr["date"]).dt.date
    tr.to_csv(folder / "trades.csv", index=False, float_format="%.6f")
    result.decisions.to_csv(folder / "decisions.csv", index=False, float_format="%.6f")
    (folder / "report.md").write_text(render_report(result), encoding="utf-8")
    if chart:
        try:
            draw_chart(result, folder / "equity.png")
        except Exception:  # noqa: BLE001 - a chart is a nicety; the numbers are already saved
            pass
    return folder


def _json_num(v: Any) -> Any:
    if isinstance(v, (np.floating, np.integer)):
        return v.item()
    return str(v)


def _pct(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "n/a"
    return "n/a" if math.isnan(f) or math.isinf(f) else f"{f * 100:+.1f}%"


def _upct(v: Any) -> str:
    return _pct(v).lstrip("+")


def _num2(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "n/a"
    return "n/a" if math.isnan(f) or math.isinf(f) else f"{f:.2f}"


def render_report(r: ExperimentResult) -> str:
    exp = r.experiment
    lines = [f"# {r.strategy}: {r.start} to {r.end}", ""]
    lines += [f"Run `{r.run_id}`. Strategy parameters: `{json.dumps(exp.params) if exp.params else 'defaults'}`. "
              f"{len(r.symbols)} stocks ({exp.universe if not exp.symbols else 'custom list'}), news "
              f"{'replayed' if exp.news else 'off'}, {r.execution.get('execution')} execution"
              + (f" with at most {exp.max_positions} positions" if exp.execution == "plain" else "")
              + (f", {exp.slippage_bps:g} bps slippage" if exp.costs == "fixed" else
                 f", realistic costs ({exp.slippage_bps:g} bps, the overnight gap and square-root impact; see below)")
              + f", ${exp.cash:,.0f} to start.", ""]
    lines += ["| | Strategy | SPY | Equal weight |", "|---|---:|---:|---:|"]
    for key, label, fmt in (("total_return", "Total return", _pct), ("cagr", "Annualized", _pct),
                            ("volatility", "Volatility", _upct), ("sharpe", "Sharpe", _num2),
                            ("sortino", "Sortino", _num2), ("max_drawdown", "Max drawdown", _pct),
                            ("calmar", "Calmar", _num2)):
        lines.append(f"| {label} | {fmt(r.metrics.get(key))} | {fmt(r.spy.get(key))} | {fmt(r.equal_weight.get(key))} |")
    lines.append("")
    m = r.metrics
    lines += [f"Trades: {int(m.get('trades', 0))} orders, {int(m.get('round_trips', 0))} closed, win rate "
              f"{_pct(m.get('win_rate')).lstrip('+')}, profit factor {_num2(m.get('profit_factor'))}, average holding "
              f"{_num2(m.get('avg_holding_days'))} trading days, average stock exposure "
              f"{_pct(m.get('avg_exposure')).lstrip('+')}.", ""]
    a = r.account()
    if a:
        calls = a["margin_calls"]
        lines += [f"## Short selling and margin ({a['mode']})", "",
                  f"As shares of the starting cash: the longs made {_pct(a['pnl_long'])}, the shorts "
                  f"{_pct(a['pnl_short'])}; borrow fees cost {_upct(a['borrow_fees'])} and margin interest "
                  f"{_upct(a['margin_interest'])}; cash and short-sale proceeds earned {_pct(a['cash_interest'])} at the "
                  f"T-bill rate. Average exposure: longs {_upct(a['avg_long'])}, shorts {_upct(a['avg_short'])}, gross "
                  f"{_upct(a['avg_gross'])} (highest {_upct(a['max_gross'])}); the exposure above is net. "
                  + (f"{calls} margin call{'s' if calls != 1 else ''} ({', '.join(a['margin_call_days'][:8])}"
                     f"{' ...' if calls > 8 else ''})." if calls else "No margin call.")
                  + (f" Equity reached zero on {a['wiped_out']} and trading stopped." if a["wiped_out"] else ""), ""]
    s = r.stats
    if "beta" in s:
        lines += ["## Against SPY", "",
                  f"Beta {_num2(s.get('beta'))}, alpha {_pct(s.get('alpha_annual'))} a year, excess return "
                  f"{_pct(s.get('excess_return_annual'))} a year (95% interval {_pct(s.get('excess_ci95_low'))} to "
                  f"{_pct(s.get('excess_ci95_high'))}), information ratio {_num2(s.get('information_ratio'))}, "
                  f"t-statistic {_num2(s.get('t_stat_excess'))} over {int(s.get('days', 0))} days.", ""]
        if "sharpe_ci95_low" in s:
            lines += [f"Sharpe 95% interval {_num2(s['sharpe_ci95_low'])} to {_num2(s['sharpe_ci95_high'])}; the "
                      f"strategy's Sharpe was above SPY's in {s.get('p_sharpe_above_spy', 0) * 100:.0f}% of 2,000 "
                      "block-bootstrap resamples (blocks of 5 days).", ""]
    if r.periods:
        lines += ["## In-sample and out-of-sample", "", "| Part | Days | Return | Sharpe | Max drawdown | SPY return |",
                  "|---|---|---:|---:|---:|---:|"]
        for name, p in r.periods.items():
            lines.append(f"| {name.replace('_', '-')} | {p['from']} to {p['to']} | {_pct(p.get('total_return'))} | "
                         f"{_num2(p.get('sharpe'))} | {_pct(p.get('max_drawdown'))} | {_pct(p.get('spy_total_return'))} |")
        lines.append("")
    lines += ["## Look-ahead check", ""]
    if r.leak is None:
        lines += ["Not run.", ""]
    else:
        lines += [f"**{r.leak['verdict'].upper()}**. {r.leak.get('meaning', r.leak.get('why', ''))}.", ""]
        for c in r.leak.get("cuts", []):
            lines.append(f"- cut after {c['cut']}: {c['decision_days_compared']} decision days compared, "
                         + ("same decisions" if c["same"] else f"first difference on {c['first_difference']['day']}"))
        lines.append("")
    lines += ["## How the backtest works", ""] + [f"- **{k}**: {v}" for k, v in
                                                  protocol(exp.period, exp.costs, exp.slippage_bps).items()] + [""]
    if r.notes:
        lines += ["## Notes", ""] + [f"- {n}" for n in r.notes] + [""]
    ds = dataset_info(exp.period)
    lines += ["## Data", "", f"- {ds['name']}, version {ds['version']}: `{ds['prices']['file']}` sha256 "
              f"`{ds['prices']['sha256'][:16]}...`; news `{ds['news']['file']}` sha256 `{(ds['news']['sha256'] or '')[:16]}...`",
              f"- Reproduce: `{reproduce_command(exp)}`", ""]
    return "\n".join(lines)


def draw_chart(r: ExperimentResult, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    eq = r.equity
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(9, 5.4), sharex=True, gridspec_kw={"height_ratios": [3, 1.2]})
    for col, label, color, width in (("equity", r.strategy, "#1f6feb", 2.0), ("spy", "SPY", "#57606a", 1.4),
                                     ("equal_weight", "Equal weight", "#bf8700", 1.2)):
        s = eq[col].dropna()
        if len(s):
            a1.plot(s.index, (s / s.iloc[0] - 1) * 100, label=label, color=color, linewidth=width)
    a1.axhline(0, color="#8c959f", linewidth=0.6)
    a1.set_ylabel("Return (%)")
    a1.legend(loc="upper left", frameon=False)
    a1.set_title(f"{r.strategy}, {r.start} to {r.end}", loc="left", fontsize=11)
    dd = (eq["equity"] / eq["equity"].cummax() - 1) * 100
    sd = (eq["spy"] / eq["spy"].cummax() - 1) * 100
    a2.fill_between(dd.index, dd, 0, color="#1f6feb", alpha=0.35, label=r.strategy)
    a2.plot(sd.index, sd, color="#57606a", linewidth=1.0, label="SPY")
    a2.set_ylabel("Drawdown (%)")
    for a in (a1, a2):
        a.spines[["top", "right"]].set_visible(False)
        a.grid(axis="y", color="#d0d7de", linewidth=0.5)
    loc = mdates.AutoDateLocator()
    a2.xaxis.set_major_locator(loc)
    a2.xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc))
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    """Saved experiments, newest first: id, strategy, period, return, Sharpe and the leak-check verdict."""
    if not RUNS_DIR.is_dir():
        return []
    out = []
    for d in sorted((p for p in RUNS_DIR.iterdir() if (p / "manifest.json").is_file()), reverse=True)[:limit]:
        try:
            man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
            met = json.loads((d / "metrics.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        e = man.get("experiment", {})
        out.append({"run_id": man.get("run_id"), "folder": str(d), "strategy": man.get("strategy", {}).get("name"),
                    "period": f"{e.get('start')} to {e.get('end')}", "news": e.get("news"),
                    "total_return": _r(met.get("strategy", {}).get("total_return")),
                    "sharpe": _r(met.get("strategy", {}).get("sharpe")),
                    "spy_total_return": _r(met.get("spy", {}).get("total_return")),
                    "leak_check": (man.get("leak_check") or {}).get("verdict")})
    return out


def run_folder(run_id: str) -> Path:
    p = Path(run_id).expanduser()
    if p.is_dir():
        return p
    p = RUNS_DIR / run_id
    if not p.is_dir():
        raise FileNotFoundError(f"no saved experiment {run_id} in {RUNS_DIR}")
    return p


def load_report(run_id: str) -> dict[str, Any]:
    folder = run_folder(run_id)
    return {"folder": str(folder),
            "report": (folder / "report.md").read_text(encoding="utf-8") if (folder / "report.md").is_file() else "",
            "manifest": json.loads((folder / "manifest.json").read_text(encoding="utf-8")),
            "files": sorted(p.name for p in folder.iterdir())}
