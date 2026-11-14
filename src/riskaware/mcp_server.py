"""MCP server: lets Claude, Cursor or any MCP client ask the agents about the market and run them.

Start it with ``riskaware mcp`` (stdio). Add it to Claude Code with::

    claude mcp add riskaware -- riskaware mcp

Every tool is read-only or works on the local paper accounts. There is no tool that can send a live order.
backtest_code runs strategy code an AI wrote, after checks, in a separate process (see riskaware.lab).
"""

from __future__ import annotations

import dataclasses
import enum
import math
from datetime import date, datetime
from typing import Any

from . import i18n
from . import marketcal as mc
from .config import AppConfig, load_config

INSTRUCTIONS = (
    "Risk-Aware Trading Agents for US stocks. Use market_regime first: it explains whether the agents are buying, "
    "holding or only buying at strong support, and which macro events (FOMC, CPI, jobs report) are close. "
    "scan and analyze_stock show the signals and why; backtest tests any built-in strategy on history. To test a "
    "strategy the user describes in words, read strategy_guide, write the Strategy class, and run it with "
    "backtest_code; fix the problems it reports and run it again. The agents_* tools manage paper-trading agents "
    "that run on simulated money every trading day. For experiments that others can repeat (papers, strategy "
    "research), read research_guide: market_day shows everything known at any close of the bundled periods (2020, "
    "2022, 2026), run_experiment tests a strategy on a frozen period with a look-ahead check and saves a citable "
    "record, and "
    "trading_session_start / trading_session_step let you trade the period yourself. Nothing here is investment "
    "advice and no tool can trade a real account."
)


def _plain(obj: Any, depth: int = 0) -> Any:
    """Turn dataclasses, enums, dates, numpy and pandas values into JSON-friendly data."""
    if depth > 6:
        return str(obj)
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        return None if math.isnan(obj) or math.isinf(obj) else round(obj, 6)
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _plain(getattr(obj, f.name), depth + 1) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {str(k): _plain(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_plain(v, depth + 1) for v in obj]
    if hasattr(obj, "item") and callable(obj.item):  # numpy scalar
        try:
            return _plain(obj.item(), depth + 1)
        except (TypeError, ValueError):
            pass
    if hasattr(obj, "model_dump"):
        return _plain(obj.model_dump(), depth + 1)
    return str(obj)


def _cfg(profile: str | None = None) -> AppConfig:
    return load_config(None, {"profile": profile} if profile else None)


def _signal(s: Any) -> dict[str, Any]:
    out = {"symbol": s.symbol, "action": s.action.value, "conviction": round(float(s.strength), 3),
           "reason": s.reason, "setups": list(s.setups)}
    if getattr(s, "option", None) is not None:
        out["option"] = _plain(s.option)
    return out


# ------------------------------------------------------------------------------------------- market
def market_regime() -> dict[str, Any]:
    """The market regime the agents see today (risk-on, neutral, uncertain, risk-off), the readings behind it,
    and the macro events of the next 30 days."""
    from .engine import MarketData

    md = MarketData(_cfg())
    m = md.load([])
    events = md.calendar.upcoming(date.today(), 30)
    return {
        "date": m.day.isoformat(), "regime": m.regime.value, "what_the_agents_do": i18n.regime(m.regime.value, "en"),
        "vix": _plain(m.vix), "vix_to_vix3m": _plain(m.vix_ratio), "fear_greed": _plain(m.fear_greed),
        "fear_greed_label": m.fear_label, "cnn_fear_greed": _plain(m.cnn_fear_greed),
        "sector_breadth_above_ema200": _plain(m.breadth), "risk_score": _plain(m.risk_score),
        "spy_trend": m.spy_trend, "event_risk": _plain(m.event_risk), "reasons": list(m.reasons),
        "upcoming_events": [{"date": e.day.isoformat(), "event": i18n.event(e.kind, "en"), "time": e.time,
                             "impact": e.impact, "estimated": e.estimated,
                             "trading_days_away": mc.trading_days_between(date.today(), e.day)} for e in events],
    }


def scan(symbols: list[str] | None = None, profile: str = "balanced", strategy: str = "risk_aware") -> dict[str, Any]:
    """Today's buy and sell signals with their reasons. Scans the configured watchlist unless symbols are given."""
    from .engine import TradingEngine
    from .strategy import load_strategy

    eng = TradingEngine(_cfg(profile), strategy=load_strategy(strategy, {}))
    market, signals, _ = eng.scan([s.upper() for s in symbols] if symbols else None)
    return {"regime": market.regime.value, "signals": [_signal(s) for s in sorted(signals, key=lambda x: -x.strength)]}


def analyze_stock(symbol: str) -> dict[str, Any]:
    """What every agent thinks about one stock: technical setups, earnings quality, verified news, the decision."""
    from .engine import TradingEngine

    eng = TradingEngine(_cfg())
    a = eng.analyze_symbol(symbol.upper())
    row = a["row"]
    keep = ("close", "ema20", "ema50", "ema100", "ema200", "rsi2", "rsi14", "atr_pct", "z20", "dd252", "mom126",
            "rs126", "rel_vol", "adx14")
    news = a["news"]
    return {
        "symbol": symbol.upper(), "regime": a["market"].regime.value,
        "indicators": {k: _plain(row.get(k)) for k in keep if k in row},
        "technical": _plain(a["technical"]), "earnings_quality": _plain(a["quality"]),
        "news": None if news is None else {
            "score": _plain(news.score), "confirmed": news.confirmed, "unconfirmed": news.unconfirmed,
            "red_flags_confirmed": list(news.red_flags_confirmed),
            "headlines": [{"headline": c.headline, "status": c.status.value, "publishers": c.publishers[:4],
                           "sentiment": _plain(c.sentiment)} for c in news.top(8)]},
        "days_to_earnings": a["days_to_earnings"], "signals": [_signal(s) for s in a["signals"]],
    }


def stock_news(symbol: str) -> dict[str, Any]:
    """Cross-verified news for one stock: a story counts when two independent outlets, the company's own press
    release or its SEC filing carry it. Only a severe flag the company itself backs (its 8-K, or its release
    confirming earlier reports) counts as a thesis break; denials clear the stories they refute."""
    from .news.relevance import company_names
    from .news.sources import NewsCollector
    from .news.verify import analyze_news
    from .utils import DiskCache

    c = _cfg()
    cache = DiskCache(c.data.cache_path)
    col = NewsCollector((["finviz"] if c.news.finviz else []) + list(c.news.feeds), cache,
                        c.data.contact_email, c.news.request_interval)
    sym = symbol.upper()
    rep = analyze_news(sym, col.collect(sym, c.news.lookback_hours), c.news, sources=col.last_status,
                       names=company_names(sym, cache))
    return {"symbol": sym, "score": _plain(rep.score), "confirmed": rep.confirmed, "off_topic_ignored": rep.off_topic,
            "unconfirmed": rep.unconfirmed, "red_flags_confirmed": list(rep.red_flags_confirmed),
            "red_flags_unconfirmed": list(rep.red_flags_unconfirmed),
            "red_flags_denied": list(rep.red_flags_denied), "thesis_break": rep.severe_confirmed,
            "stories": [{"headline": x.headline, "status": x.status.value, "publishers": x.publishers[:5],
                         "sentiment": _plain(x.sentiment), "flags": list(x.live_flags),
                         "denied_flags": list(x.cleared_flags), "denial": x.denial} for x in rep.top(20)]}


def list_strategies() -> dict[str, Any]:
    """The built-in strategies that backtest and the agents can use, and the strategies saved from backtest_code
    (pass their path as the strategy)."""
    from .lab import saved_strategies
    from .strategy import list_strategies as _ls

    return {"strategies": [{"name": k, "description": v} for k, v in _ls().items()], "saved": saved_strategies()}


def backtest(strategy: str = "risk_aware", start: str = "2018-01-01", end: str | None = None,
             universe: str = "megacap", symbols: list[str] | None = None, profile: str = "balanced",
             cash: float = 100_000.0, news: bool = False) -> dict[str, Any]:
    """Backtest a strategy on daily data (signals at the close, fills the next day) against SPY buy and hold.

    news=True replays the news archive: each day the strategy sees only items published before that day's close.
    The research headline files (2026 headlines and SEC filings for the built-in watchlists) are licensed and do not
    ship with the package; news comes from your local archive and from those files if you have a copy."""
    from .backtest.engine import Backtester
    from .data.prices import make_price_provider
    from .strategy import load_strategy
    from .utils import DiskCache

    c = _cfg(profile)
    c.universe.preset = universe or None
    if symbols:
        c.universe.symbols = [s.upper() for s in symbols]
        if not universe:
            c.universe.preset = None
    cache = DiskCache(c.data.cache_path)
    pit = None
    if news:
        from .news.history import NewsArchive, PointInTimeNews
        from .news.relevance import company_names

        pit = PointInTimeNews(NewsArchive(c.news.archive_path, bundled=c.news.bundled), c.news, c.universe.resolve(),
                              names=lambda s: company_names(s, cache))
        pit = pit if pit.symbols else None
    bt = Backtester(c, make_price_provider(c.data.price_source, cache, c.data.csv_dir), news=pit)
    res = bt.run(load_strategy(strategy), date.fromisoformat(start),
                 date.fromisoformat(end) if end else date.today(), initial_cash=cash)
    return {"strategy": res.strategy, "profile": profile, "metrics": _plain(res.metrics),
            "spy_buy_and_hold": _plain(res.benchmark_metrics), "trades": int(len(res.trades)),
            "notes": list(res.notes)}


def strategy_guide() -> dict[str, Any]:
    """How to write a strategy for backtest_code: the Strategy class, what ctx holds (indicators, price history,
    positions, market regime, point-in-time news), the rules the code must follow, a template and a news
    example. Read it before writing code."""
    from .lab import guide

    return guide()


def backtest_code(code: str, symbols: list[str] | None = None, universe: str | None = "megacap",
                  start: str = "2026-01-02", end: str | None = None, news: bool = True, profile: str = "balanced",
                  cash: float = 100_000.0, params: dict[str, Any] | None = None, compare: list[str] | None = None,
                  save_as: str | None = None) -> dict[str, Any]:
    """Backtest a strategy written as Python code (one Strategy subclass; see strategy_guide).

    The stocks come from symbols or universe (megacap, tech, defensive, core, etf, sectors), never from the code.
    The code is checked first: dates or stock tickers written in the code, files, network and private attributes
    are refused, because they let a backtest see the future or touch the machine. It then runs in a separate
    process, where each day sees only that day's data and news published before the close. Returns metrics,
    SPY and buy-and-hold for comparison, trades and the risk manager's refusals; or the problems to fix, with
    line numbers. save_as='name' keeps the code in ~/.riskaware/strategies/ai/ for backtest and agents_create."""
    from .lab import backtest_code as _run

    return _run(code, symbols=symbols, universe=None if symbols else universe, start=start, end=end, news=news,
                profile=profile, cash=cash, params=params, compare=compare, save_as=save_as)


# ------------------------------------------------------------------------------------------- agents
def agents_leaderboard() -> dict[str, Any]:
    """Every paper-trading agent ranked by return since it started, compared with SPY over the same days."""
    from .arena import Arena

    arena = Arena(base_cfg=_cfg())
    rows = arena.leaderboard(arena.benchmark())
    for r in rows:
        r.pop("curve", None)
    return {"agents": _plain(rows), "schedule": _plain(arena.schedule_status())}


def agent_detail(name: str) -> dict[str, Any]:
    """One agent: its settings, cash and positions at cost, equity by day and its latest orders."""
    from .arena import Arena
    from .journal import Journal

    arena = Arena(base_cfg=_cfg())
    spec = arena.get(name)
    acct = arena.account_state(spec.name)
    jpath = arena.dir(spec.name) / "journal.db"
    return {"agent": spec.model_dump(), "cash": acct.get("cash", spec.cash),
            "positions": acct.get("positions", {}),
            "equity_by_day": arena.equity_history(spec.name).tail(60).to_dict("records"),
            "recent_orders": Journal(jpath).recent_orders(25) if jpath.is_file() else []}


def agents_create(name: str, strategy: str = "risk_aware", profile: str = "balanced", universe: str | None = None,
                  symbols: list[str] | None = None, cash: float = 100_000.0, note: str = "",
                  llm: str | None = None) -> dict[str, Any]:
    """Create a paper-trading agent: a strategy with its own simulated account that trades every trading day.

    strategy is a built-in name or the path of a strategy saved by backtest_code (list_strategies shows both).
    llm optionally adds an LLM review board, e.g. "openai:gpt-5.6-luna", "ollama:qwen2.5:3b" or "off"."""
    from .arena import AgentSpec, Arena

    spec = Arena(base_cfg=_cfg()).add(AgentSpec(name=name, strategy=strategy, profile=profile, preset=universe,
                                                symbols=symbols or [], cash=cash, note=note, llm=llm))
    return {"created": spec.model_dump()}


def agent_decisions(name: str, symbol: str | None = None, limit: int = 20,
                    include_prompts: bool = False) -> dict[str, Any]:
    """How an agent made its recent decisions: the setups and reasons, whether the risk manager let each one
    through, and the LLM's bull case, bear case and verdict when the agent has an LLM. include_prompts adds the
    exact prompt sent to the model and its raw reply."""
    from .arena import Arena

    rows = Arena(base_cfg=_cfg()).decisions(name, symbol, limit, with_prompts=include_prompts)
    return {"agent": name, "decisions": _plain(rows)}


def agents_run(names: list[str] | None = None, dry_run: bool = False) -> dict[str, Any]:
    """Run agents once now on their simulated paper accounts (all active agents when names is empty)."""
    from .arena import Arena

    runs = Arena(base_cfg=_cfg()).run(names or None, mode="dry-run" if dry_run else "paper")
    return {"runs": [{"agent": r.name, "ok": r.ok, "regime": r.regime, "equity": _plain(r.equity),
                      "signals": r.signals, "orders": r.orders, "filled": r.filled, "errors": r.errors,
                      "orders_detail": [{"symbol": o.symbol, "side": o.side.value, "qty": o.qty, "tag": o.tag,
                                         "reason": o.reason} for o in (r.report.approved if r.report else [])]}
                     for r in runs]}


# ------------------------------------------------------------------------------------------- research
_VIEWS: dict[str, Any] = {}


def _market_view(period: str | None = None):
    from .research.periods import get_period

    name = get_period(period).name
    if name not in _VIEWS:
        from .research.day import MarketView

        _VIEWS[name] = MarketView(period=name)
    return _VIEWS[name]


def research_guide() -> dict[str, Any]:
    """Read this first for experiments on the frozen markets: what data ships with the package (three periods:
    2026 to date, the 2022 bear market and the 2020 crash, each with daily bars and the VIX term structure; each
    year's headline file is licensed and not distributed, so news needs your own copy), how a backtest decides and
    fills (after the close,
    at the next open: swing trading), the look-ahead rules and check, how to write a strategy, the five baseline
    swing strategies, what a saved record holds, and advice for papers."""
    from .research.guide import guide

    return guide()


def market_day(day: str, symbols: list[str] | None = None, news: bool = True, headlines: int = 3) -> dict[str, Any]:
    """Everything known at the close of one day of the bundled periods (2018 to 2020, 2020 to 2022, 2024 to
    2026-11-11; news in 2020, 2022 and 2026 when you have a copy of the headline files, which are not distributed):
    the market regime, VIX, VIX9D, VIX3M, VVIX and the term structure, fear & greed, breadth, credit stress, the
    macro events of the next two weeks; and per stock its prices and returns, indicators (EMAs, RSI, MACD, ATR, ADX,
    Bollinger, 52-week high), technical setups and the news published before the close (score, verified status,
    red flags, headlines, last earnings release). Nothing after that close is visible. Without symbols it lists all
    64 stocks in a compact table; name up to 10 symbols for the full detail with headlines. A weekend or holiday
    shows the close before it."""
    try:
        from datetime import date as _date

        from .research.periods import period_of

        view = _market_view(period_of(_date.fromisoformat(day)).name)
        if symbols:
            snap = view.snapshot(day, symbols=[s.upper() for s in symbols[:10]], news=news, headlines=headlines)
            return _plain(snap)
        snap = view.snapshot(day, news=news, headlines=0)
    except (ValueError, KeyError) as exc:
        return {"ok": False, "error": str(exc.args[0]) if exc.args else str(exc)}
    rows = []
    for sym, st in snap["stocks"].items():
        i, t, n = st.get("indicators", {}), st.get("technical", {}), st.get("news") or {}
        rows.append({"symbol": sym, "sector": st.get("sector"), "close": st.get("close_as_traded"),
                     "ret_1d": st.get("return_1d"), "ret_5d": st.get("return_5d"), "ret_21d": st.get("return_21d"),
                     "rsi14": i.get("rsi14"), "rsi2": i.get("rsi2"), "vs_ema50_pct": st["vs_ema_pct"].get("ema50"),
                     "vs_ema200_pct": st["vs_ema_pct"].get("ema200"), "atr_pct": i.get("atr_pct"),
                     "off_52w_high": i.get("dd252"), "setups": sorted(t.get("setups", {})),
                     "ta_score": t.get("score"), "news_score": n.get("score"), "stories": n.get("stories"),
                     "red_flags": (n.get("red_flags_confirmed") or []) + (n.get("red_flags_single_source") or [])})
    snap["stocks"] = rows
    snap["detail"] = "call market_day with symbols=[...] (up to 10) for indicators, setups and headlines"
    return _plain(snap)


def run_experiment(strategy: str | None = None, code: str | None = None, params: dict[str, Any] | None = None,
                   start: str | None = None, end: str | None = None, universe: str | None = "megacap",
                   symbols: list[str] | None = None, news: bool = True, execution: str = "plain",
                   max_positions: int = 10, position_size: float | None = None, split: str | None = None,
                   leak_check: bool = True, label: str | None = None,
                   extra_news: list[dict[str, Any]] | None = None, period: str = "2026",
                   costs: str = "fixed", mode: str = "long_only",
                   mode_params: dict[str, float] | None = None) -> dict[str, Any]:
    """Run an experiment on a frozen period and save a record (see research_guide). period is 2026 (default, a
    rising year), 2022 (the rate-hike bear market) or 2020 (the COVID crash and rebound); start and end default to
    the period's test window. costs='fixed' (default) pays 5 bps on every fill at the open; costs='realistic' adds
    10% of the overnight gap and square-root market impact and fills each order for at most 1% of its 20-day
    average volume. mode (plain execution) is long_only (default), long_short (short positions with a signed
    target_weight, e.g. Signal(sym, Action.SELL, target_weight=-0.1), long plus short at most 100% of equity, each
    short at most 20%, 0.5% a year borrow fee) or margin (Reg T: up to 200% gross, maintenance margin calls, borrowed
    cash pays BIL + 1.5%); mode_params overrides those rules. The rule-based long-short anchors are momentum_ls and
    trend_ls.

    Give either strategy (a built-in name such as swing_rsi2, swing_trend, swing_breakout, swing_pullback,
    swing_news, risk_aware, rsi2, momentum_rotation; or the path of a saved strategy) or code (one Strategy class,
    as in strategy_guide; it is checked and runs in a separate process). Signals at each close, market orders at the
    next open. execution='plain' trades exactly what the strategy says (equal-size positions, at most max_positions);
    'risk_aware' adds the package's risk manager. split='2026-07-01' also reports the parts before and after. The
    leak check re-runs it with a random future after three cut days and compares the decisions. extra_news adds
    headlines ({ts, symbol, headline, source}) for counterfactual or robustness tests. Returns the metrics, SPY and
    the equal-weight universe, beta, alpha, t-statistic and bootstrap intervals, the leak-check verdict, the best and
    worst trades, and the folder of the record (manifest with data hashes, metrics, equity, trades, decisions,
    report.md, chart)."""
    from pathlib import Path

    from .research.periods import get_period

    try:
        per = get_period(period)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if costs not in ("fixed", "realistic"):
        return {"ok": False, "error": f"costs must be 'fixed' or 'realistic', not {costs!r}"}
    settings = {"period": per.name, "start": start, "end": end, "universe": None if symbols else universe,
                "symbols": [s.upper() for s in symbols] if symbols else None, "news": news, "execution": execution,
                "max_positions": max_positions, "position_size": position_size, "split": split,
                "leak_check": leak_check, "label": label, "extra_news": extra_news, "params": params or {},
                "costs": costs, "mode": mode, "mode_params": mode_params}
    if code is None and strategy and strategy.endswith(".py") and Path(strategy).expanduser().is_file():
        code = Path(strategy).expanduser().read_text(encoding="utf-8")
    if code is not None:
        from .lab import backtest_code as _lab

        settings["strategy"] = "code"
        return _lab(code, symbols=symbols, universe=None if symbols else universe,
                    start=start or per.start.isoformat(), end=end, news=news, research=settings, timeout=900.0)
    from .research.experiment import Experiment
    from .research.experiment import run_experiment as _run

    try:
        res = _run(Experiment(strategy=strategy or "risk_aware", **settings))
    except (ValueError, KeyError, FileNotFoundError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, **_plain(res.summary())}


def list_experiments(limit: int = 20) -> dict[str, Any]:
    """Saved research experiments and trading sessions, newest first: strategy, period, return, Sharpe, SPY and the
    look-ahead verdict, with the folder of each record."""
    from .research.agent import list_sessions
    from .research.experiment import list_runs

    return {"experiments": _plain(list_runs(limit)), "trading_sessions": _plain(list_sessions(limit))}


def experiment_report(run_id: str) -> dict[str, Any]:
    """The report (markdown, ready to paste), manifest and file list of one saved experiment."""
    from .research.experiment import load_report

    try:
        return _plain(load_report(run_id))
    except FileNotFoundError as exc:
        return {"ok": False, "error": str(exc)}


def experiment_analysis(run_id: str) -> dict[str, Any]:
    """What a saved experiment or a finished trading session made its money on. Decisions: the excess return over
    SPY 1, 5 and 20 trading days after every buy and sell (a sell counts the excess it avoided), with hit rates and
    t-statistics that allow for same-day decisions and overlapping windows, and the rank IC of each decision day
    against the next five days. Factors: daily returns regressed on the market, size, growth and momentum with
    Newey-West t-statistics, the alpha left after them and what each exposure contributed. P&L: by holding period,
    sector, market regime, news-triggered or not, and stock; the best and worst positions; how much of the profit
    rests on one or two trades. Writes analysis.json, analysis.md and analysis.png into the record. run_id is a run
    id or folder from list_experiments, or a trading session id."""
    from .research.analysis import analyze_run, compact, is_record, resolve

    try:
        folder = resolve(run_id)
        if not is_record(folder):
            return {"ok": False, "error": f"{folder} is not a single record; pass one of the runs inside it"}
        return {"ok": True, **compact(analyze_run(folder))}
    except (FileNotFoundError, ValueError, KeyError) as exc:
        return {"ok": False, "error": str(exc)}


def trading_session_start(start: str | None = None, end: str | None = None, universe: str | None = "core",
                          symbols: list[str] | None = None, news: bool = True, blind: bool = False,
                          decide_every: int = 5, max_positions: int = 5, cash: float = 100_000.0,
                          max_news: int = 3, period: str = "2026", mode: str = "long_only") -> dict[str, Any]:
    """Trade the frozen market yourself, one decision at a time (an LLM-agent experiment). Returns the rules and
    the first observation: the market, your portfolio, and per stock the main indicators and the news published
    before that close. Answer with trading_session_step. decide_every=5 decides once a week (about 44 steps for
    2026); 1 decides every day. blind=True hides tickers, company names and dates so that you cannot recall what
    happened. Orders fill at the next open; long only; at most max_positions stocks. period is 2026 (default), 2022
    or 2020. mode='long_short' also allows short positions (no leverage) and mode='margin' a Reg T margin account
    (up to 2x gross, margin calls); the returned rules say what each allows and costs."""
    from .research.agent import session_start

    try:
        return _plain(session_start(start=start, end=end, universe=None if symbols else universe, symbols=symbols,
                                    news=news, blind=blind, decide_every=decide_every, max_positions=max_positions,
                                    cash=cash, max_news=max_news, period=period, mode=mode))
    except (ValueError, KeyError) as exc:
        return {"ok": False, "error": str(exc)}


def trading_session_step(session_id: str, orders: list[dict[str, Any]] | None = None,
                         note: str | None = None) -> dict[str, Any]:
    """Send the orders for the current step and get the next observation (or, after the last step, the result
    with its saved record). Each order: {"symbol", "action": "buy" | "sell" | "target", "weight" (share of equity
    for buy and target), "fraction" (share of the position for sell, 1 = all), "reason"}. An empty list holds. In a
    long_short or margin session also "short" (weight = size of the short) and "cover" (fraction of a short), and
    target takes a negative weight."""
    from .research.agent import session_step

    try:
        return _plain(session_step(session_id, orders or [], note))
    except (ValueError, FileNotFoundError) as exc:
        return {"ok": False, "error": str(exc)}


def trading_session_status(session_id: str) -> dict[str, Any]:
    """Settings, steps sent so far and the record folder (when finished) of a trading session."""
    from .research.agent import session_status

    try:
        return _plain(session_status(session_id))
    except (ValueError, FileNotFoundError) as exc:
        return {"ok": False, "error": str(exc)}


TOOLS = [market_regime, scan, analyze_stock, stock_news, list_strategies, backtest, strategy_guide, backtest_code,
         agents_leaderboard, agent_detail, agent_decisions, agents_create, agents_run, research_guide, market_day,
         run_experiment, list_experiments, experiment_report, trading_session_start, trading_session_step,
         trading_session_status, experiment_analysis]
READ_ONLY = {"market_regime", "scan", "analyze_stock", "stock_news", "list_strategies", "backtest", "strategy_guide",
             "agents_leaderboard", "agent_detail", "agent_decisions", "research_guide", "market_day",
             "list_experiments", "experiment_report", "trading_session_status"}


def build_server():
    try:
        from mcp.server.mcpserver import MCPServer as Server  # MCP SDK 2.x
    except ImportError:
        from mcp.server.fastmcp import FastMCP as Server  # MCP SDK 1.x
    from mcp.types import ToolAnnotations

    server = Server(name="riskaware", instructions=INSTRUCTIONS)
    for fn in TOOLS:
        ro = fn.__name__ in READ_ONLY
        server.tool(annotations=ToolAnnotations(readOnlyHint=ro, destructiveHint=False, openWorldHint=True))(fn)
    return server


def main() -> None:
    build_server().run("stdio")
