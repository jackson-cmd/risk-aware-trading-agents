"""MCP server: lets Claude, Cursor or any MCP client ask the agents about the market and run them.

Start it with ``riskaware mcp`` (stdio). Add it to Claude Code with::

    claude mcp add riskaware -- riskaware mcp

Every tool is read-only or works on the local paper accounts. There is no tool that can send a live order.
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
    "scan and analyze_stock show the signals and why; backtest tests any built-in strategy on history; the agents_* "
    "tools manage paper-trading agents that run on simulated money every trading day. Nothing here is investment "
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
    """Cross-verified news for one stock: a story counts only when two independent publishers or an SEC filing
    carry it."""
    from .news.sources import NewsCollector
    from .news.verify import analyze_news
    from .utils import DiskCache

    c = _cfg()
    col = NewsCollector((["finviz"] if c.news.finviz else []) + list(c.news.feeds), DiskCache(c.data.cache_path),
                        c.data.contact_email, c.news.request_interval)
    rep = analyze_news(symbol.upper(), col.collect(symbol.upper(), c.news.lookback_hours), c.news,
                       sources=col.last_status)
    return {"symbol": symbol.upper(), "score": _plain(rep.score), "confirmed": rep.confirmed,
            "unconfirmed": rep.unconfirmed, "red_flags_confirmed": list(rep.red_flags_confirmed),
            "red_flags_unconfirmed": list(rep.red_flags_unconfirmed),
            "stories": [{"headline": x.headline, "status": x.status.value, "publishers": x.publishers[:5],
                         "sentiment": _plain(x.sentiment), "flags": list(x.red_flags)} for x in rep.top(20)]}


def list_strategies() -> dict[str, Any]:
    """The built-in strategies that backtest and the agents can use."""
    from .strategy import list_strategies as _ls

    return {"strategies": [{"name": k, "description": v} for k, v in _ls().items()]}


def backtest(strategy: str = "risk_aware", start: str = "2018-01-01", end: str | None = None,
             universe: str = "megacap", symbols: list[str] | None = None, profile: str = "balanced",
             cash: float = 100_000.0) -> dict[str, Any]:
    """Backtest a strategy on daily data (signals at the close, fills the next day) against SPY buy and hold."""
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
    bt = Backtester(c, make_price_provider(c.data.price_source, DiskCache(c.data.cache_path), c.data.csv_dir))
    res = bt.run(load_strategy(strategy), date.fromisoformat(start),
                 date.fromisoformat(end) if end else date.today(), initial_cash=cash)
    return {"strategy": res.strategy, "profile": profile, "metrics": _plain(res.metrics),
            "spy_buy_and_hold": _plain(res.benchmark_metrics), "trades": int(len(res.trades)),
            "notes": list(res.notes)}


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
                  symbols: list[str] | None = None, cash: float = 100_000.0, note: str = "") -> dict[str, Any]:
    """Create a paper-trading agent: a strategy with its own simulated account that trades every trading day."""
    from .arena import AgentSpec, Arena

    spec = Arena(base_cfg=_cfg()).add(AgentSpec(name=name, strategy=strategy, profile=profile, preset=universe,
                                                symbols=symbols or [], cash=cash, note=note))
    return {"created": spec.model_dump()}


def agents_run(names: list[str] | None = None, dry_run: bool = False) -> dict[str, Any]:
    """Run agents once now on their simulated paper accounts (all active agents when names is empty)."""
    from .arena import Arena

    runs = Arena(base_cfg=_cfg()).run(names or None, mode="dry-run" if dry_run else "paper")
    return {"runs": [{"agent": r.name, "ok": r.ok, "regime": r.regime, "equity": _plain(r.equity),
                      "signals": r.signals, "orders": r.orders, "filled": r.filled, "errors": r.errors,
                      "orders_detail": [{"symbol": o.symbol, "side": o.side.value, "qty": o.qty, "tag": o.tag,
                                         "reason": o.reason} for o in (r.report.approved if r.report else [])]}
                     for r in runs]}


TOOLS = [market_regime, scan, analyze_stock, stock_news, list_strategies, backtest, agents_leaderboard,
         agent_detail, agents_create, agents_run]
READ_ONLY = {"market_regime", "scan", "analyze_stock", "stock_news", "list_strategies", "backtest",
             "agents_leaderboard", "agent_detail"}


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
