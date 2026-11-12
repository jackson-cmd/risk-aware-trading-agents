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
    "that run on simulated money every trading day. Nothing here is investment advice and no tool can trade a "
    "real account."
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
    2026 headlines and SEC filings for the built-in watchlists ship with the package."""
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


TOOLS = [market_regime, scan, analyze_stock, stock_news, list_strategies, backtest, strategy_guide, backtest_code,
         agents_leaderboard, agent_detail, agent_decisions, agents_create, agents_run]
READ_ONLY = {"market_regime", "scan", "analyze_stock", "stock_news", "list_strategies", "backtest", "strategy_guide",
             "agents_leaderboard", "agent_detail", "agent_decisions"}


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
