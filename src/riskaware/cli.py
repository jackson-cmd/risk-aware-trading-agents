"""Command line interface: ``riskaware --help``."""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from . import __version__, i18n
from . import marketcal as mc
from .config import PROFILE_PRESETS, AppConfig, load_config
from .utils import fmt_money, fmt_pct, setup_logging

app = typer.Typer(add_completion=False, no_args_is_help=True, rich_markup_mode="rich",
                  help="Risk-Aware Trading Agents: macro-aware, risk-first stock trading agents. / 风险感知交易智能体")
paper_app = typer.Typer(help="Local paper account / 本地模拟盘", no_args_is_help=True)
app.add_typer(paper_app, name="paper")
agents_app = typer.Typer(help="Paper-trading agents that run every trading day / 每个交易日自动运行的模拟盘智能体",
                         invoke_without_command=True)
app.add_typer(agents_app, name="agents")
console = Console()


class State:
    config_path: Optional[str] = None
    lang: str = "en"
    lang_explicit: Optional[str] = None
    profile: Optional[str] = None
    cfg: Optional[AppConfig] = None


S = State()


def cfg() -> AppConfig:
    if S.cfg is None:
        overrides = {"profile": S.profile} if S.profile else None
        try:
            S.cfg = load_config(S.config_path, overrides)
        except Exception as exc:  # pydantic validation, YAML errors
            console.print(f"[red]Config error:[/] {exc}")
            raise typer.Exit(2) from exc
        if S.lang_explicit is None:
            S.lang = S.cfg.language
    return S.cfg



def T(key: str, **kw) -> str:
    return i18n.t(key, S.lang, **kw)


@app.callback()
def main_callback(
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Path to riskaware.yaml"),
    lang: Optional[str] = typer.Option(None, "--lang", "-l", help="en | zh"),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="conservative | balanced | aggressive"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    setup_logging("INFO" if verbose else "WARNING")
    S.config_path = config
    S.profile = profile
    S.lang_explicit = lang
    S.lang = i18n.current_lang(lang)


def _regime_style(name: str) -> str:
    return {"RISK_ON": "bold green", "NEUTRAL": "bold white", "UNCERTAIN": "bold yellow", "RISK_OFF": "bold red"}[name]


def _engine(strategy: Optional[str] = None):
    from .engine import TradingEngine
    from .strategy import load_strategy

    c = cfg()
    strat = load_strategy(strategy, {}) if strategy else None
    return TradingEngine(c, strategy=strat)


# ----------------------------------------------------------------------------- init
@app.command()
def init(
    path: Optional[Path] = typer.Option(None, help="Where to write the config (default ./riskaware.yaml)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Accept all defaults"),
) -> None:
    """Create a config file with a short wizard. / 生成配置文件"""
    target = path or Path.cwd() / "riskaware.yaml"
    if target.exists() and not yes and not Confirm.ask(f"{target} exists. Overwrite?", default=False):
        raise typer.Exit()
    ask = (lambda q, choices, default: default) if yes else (
        lambda q, choices, default: Prompt.ask(q, choices=choices, default=default))
    language = ask("Language / 语言", ["en", "zh"], S.lang)
    S.lang = language
    zh = language == "zh"
    profile = ask("风险档位" if zh else "Risk profile", list(PROFILE_PRESETS), "balanced")
    broker = ask("券商" if zh else "Broker",
                 ["paper", "alpaca", "public", "tradier", "ibkr", "schwab", "tastytrade", "futu", "tiger", "longbridge"],
                 "paper")
    preset = ask("股票池" if zh else "Universe", ["megacap", "tech", "defensive", "etf", "sectors", "none"], "megacap")
    extra = "" if yes else Prompt.ask("额外股票（逗号分隔，可空）" if zh else "Extra symbols (comma separated, optional)",
                                      default="")
    contact = "" if yes else Prompt.ask(
        "联系邮箱（SEC/BLS 要求，可空）" if zh else "Contact email for SEC/BLS data requests (optional)", default="")
    llm = False if yes else Confirm.ask("启用 LLM 多空辩论复核？" if zh else "Enable the optional LLM review?", default=False)
    provider, model = "anthropic", "claude-opus-5-5"
    if llm:
        provider = Prompt.ask("LLM provider", choices=["anthropic", "openai"], default="anthropic")
        model = "claude-opus-5-5" if provider == "anthropic" else Prompt.ask("model", default="gpt-5-mini")
    options = {"paper": "{}", "alpaca": "{paper: true}", "tradier": "{sandbox: true}", "ibkr": "{port: 7497}",
               "futu": "{simulate: true, security_firm: FUTUINC}", "tiger": "{paper: true}",
               "longbridge": "{paper: true}", "tastytrade": "{sandbox: false}"}.get(broker, "{}")
    symbols = [s.strip().upper() for s in extra.split(",") if s.strip()]
    template = (Path(__file__).parent / "defaults" / "config.yaml").read_text(encoding="utf-8")
    text = template.format(language=language, profile=profile, broker=broker, broker_options=options,
                           preset=preset if preset != "none" else "null", symbols="[" + ", ".join(symbols) + "]",
                           contact=contact or "null", llm_enabled=str(llm).lower(), llm_provider=provider,
                           llm_model=model)
    target.write_text(text, encoding="utf-8")
    env_example = Path(__file__).resolve().parents[2] / ".env.example"
    console.print(Panel.fit(
        (f"已写入 {target}\n\n下一步：\n  riskaware doctor     检查环境\n  riskaware regime     看市场状态\n"
         f"  riskaware scan       看今天的信号\n  riskaware backtest   回测\n  riskaware run        模拟盘下单")
        if zh else
        (f"Wrote {target}\n\nNext:\n  riskaware doctor     check your setup\n  riskaware regime     market regime\n"
         f"  riskaware scan       today's signals\n  riskaware backtest   backtest\n  riskaware run        paper trade"),
        title=T("app.name")))
    if broker != "paper":
        console.print(("把券商 key 写进 .env（参考 .env.example）。" if zh else
                       "Put your broker keys in a .env file (see .env.example).")
                      + (f" {env_example}" if env_example.exists() else ""))


# ----------------------------------------------------------------------------- doctor
@app.command()
def doctor(broker_check: bool = typer.Option(True, "--broker/--no-broker")) -> None:
    """Check Python, packages, data sources and the broker connection. / 检查环境"""
    c = cfg()
    tbl = Table(title=T("doctor.title"), show_lines=False)
    tbl.add_column("Check")
    tbl.add_column("Result")
    tbl.add_row("Python", sys.version.split()[0])
    tbl.add_row("riskaware", __version__)
    tbl.add_row("Config", c.source_path or "(defaults)")
    for pkg, extra in [("ib_async", "ibkr"), ("schwab", "schwab"), ("tastytrade", "tastytrade"), ("futu", "futu"),
                       ("tigeropen", "tiger"), ("longport", "longbridge"), ("streamlit", "dashboard"),
                       ("anthropic", "llm")]:
        ok = importlib.util.find_spec(pkg) is not None
        tbl.add_row(f"  {pkg}", "[green]installed[/]" if ok else f"[dim]not installed (extra: {extra})[/]")
    from .data.prices import YahooPrices
    from .utils import make_session, tool_user_agent

    t0 = time.time()
    data = YahooPrices().history(["SPY"], date.today() - timedelta(days=10))
    tbl.add_row("Yahoo Finance prices", f"[green]ok[/] ({time.time() - t0:.1f}s)" if data else "[red]failed[/]")
    s = make_session()
    for name, url in [("Finviz news", "https://finviz.com/quote.ashx?t=AAPL&p=d"),
                      ("Google News RSS", "https://news.google.com/rss/search?q=SPY&hl=en-US&gl=US&ceid=US:en"),
                      ("Federal Reserve calendar", "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm")]:
        try:
            r = s.get(url, timeout=12)
            tbl.add_row(name, "[green]ok[/]" if r.ok else f"[yellow]HTTP {r.status_code}[/]")
        except Exception as exc:
            tbl.add_row(name, f"[red]{type(exc).__name__}[/]")
    try:
        r = make_session(tool_user_agent(c.data.contact_email)).get(
            "https://www.bls.gov/schedule/news_release/bls.ics", timeout=12)
        hint = "" if r.ok else " (set data.contact_email)"
        tbl.add_row("BLS release calendar", ("[green]ok[/]" if r.ok else f"[yellow]HTTP {r.status_code}[/]") + hint)
    except Exception as exc:
        tbl.add_row("BLS release calendar", f"[red]{type(exc).__name__}[/]")
    from .guards import is_halted

    tbl.add_row("Kill switch", "[red]HALTED[/]" if is_halted() is not None else "[green]off[/]")
    if broker_check:
        from .brokers import make_broker

        try:
            b = make_broker(c.broker.name, **c.broker.options)
            b.connect()
            a = b.account()
            tbl.add_row(f"Broker: {b.describe()}", f"[green]ok[/] equity {fmt_money(a.equity)}, cash {fmt_money(a.cash)}")
            b.close()
        except Exception as exc:
            tbl.add_row(f"Broker: {c.broker.name}", f"[red]{exc}[/]")
    console.print(tbl)


# ----------------------------------------------------------------------------- market
def _market_panel(market) -> Panel:
    r = market.regime.value
    lines = [f"[{_regime_style(r)}]{i18n.regime(r, S.lang, short=True)}[/]  {i18n.regime(r, S.lang)}", ""]

    def row(label: str, value: str) -> None:
        lines.append(f"{label:<34}{value}")

    row(T("market.vix"), f"{market.vix:.2f}" if market.vix else "n/a")
    row(T("market.term"), f"{market.vix_ratio:.2f}" if market.vix_ratio else "n/a")
    fg = f"{market.fear_greed:.0f} ({i18n.t('fear.' + market.fear_label, S.lang)})" if market.fear_greed is not None else "n/a"
    row(T("market.fear_greed"), fg)
    if market.cnn_fear_greed is not None:
        row(T("market.cnn"), f"{market.cnn_fear_greed:.0f}")
    row(T("market.breadth"), fmt_pct(market.breadth, 0) if market.breadth is not None else "n/a")
    row(T("market.trend"), i18n.t("trend." + market.spy_trend, S.lang))
    row(T("market.risk_score"), f"{market.risk_score:.0f}")
    row(T("market.event_risk"), f"{market.event_risk:.1f}")
    lines += ["", f"[bold]{T('market.why')}[/]"] + [f"  • {x}" for x in market.reasons]
    return Panel("\n".join(lines), title=f"{T('market.title')} · {market.day}", expand=False)


def _events_table(events, today: date) -> Table:
    tbl = Table(title=T("events.title"))
    for col in ("events.date", "events.event", "events.time", "events.impact", "events.in"):
        tbl.add_column(T(col))
    for ev in events:
        name = i18n.event(ev.kind, S.lang) + (" " + ("+ 经济预测" if S.lang == "zh" else "+ projections")
                                              if "projections" in ev.name else "")
        if ev.estimated:
            name += f" ({T('events.estimated')})"
        style = "bold red" if ev.impact >= 0.8 else "yellow" if ev.impact >= 0.5 else ""
        tbl.add_row(f"{ev.day:%Y-%m-%d %a}", f"[{style}]{name}[/]" if style else name, ev.time, f"{ev.impact:.1f}",
                    str(mc.trading_days_between(today, ev.day)))
    return tbl


@app.command()
def regime() -> None:
    """Market regime: VIX, term structure, fear & greed, breadth, FOMC/CPI windows. / 市场状态"""
    from .engine import MarketData

    c = cfg()
    with console.status("Loading market data..."):
        md = MarketData(c)
        market = md.load([])
    console.print(_market_panel(market))
    console.print(_events_table(md.calendar.upcoming(date.today(), 30), date.today()))


@app.command()
def calendar(days: int = typer.Option(45, help="Days ahead"),
             refresh: bool = typer.Option(False, help="Fetch the latest BLS / Fed / BEA calendars"),
             earnings: bool = typer.Option(False, help="Also list earnings dates of the watchlist (slower)")) -> None:
    """Upcoming FOMC, CPI, jobs report, PPI, GDP, PCE and earnings. / 宏观日历与财报日"""
    from .data.events import EventCalendar
    from .utils import DiskCache

    c = cfg()
    cache = DiskCache(c.data.cache_path)
    cal = EventCalendar(c.macro.events, cache, c.data.contact_email)
    if refresh:
        with console.status("Refreshing calendars..."):
            added = cal.refresh_online()
        console.print(f"refreshed ({added} new dates)")
    console.print(_events_table(cal.upcoming(date.today(), days), date.today()))
    if earnings:
        from .data.fundamentals import YahooFundamentals

        fp = YahooFundamentals(cache)
        rows = []
        with console.status("Loading earnings dates..."):
            for s in c.universe.resolve():
                snap = fp.snapshot(s)
                if snap and snap.next_earnings_date and snap.next_earnings_date <= date.today() + timedelta(days=days):
                    rows.append((snap.next_earnings_date, s))
        tbl = Table(title="Earnings" if S.lang == "en" else "财报日")
        tbl.add_column(T("events.date"))
        tbl.add_column(T("col.symbol"))
        for d, s in sorted(rows):
            tbl.add_row(f"{d:%Y-%m-%d %a}", s)
        console.print(tbl)


@app.command()
def news(symbol: str, show_all: bool = typer.Option(False, "--all", help="Show single-source and opinion stories")) -> None:
    """Cross-verified news for one symbol (Finviz + RSS + SEC filings). / 交叉验证新闻"""
    from .news.sources import NewsCollector
    from .news.verify import analyze_news
    from .utils import DiskCache

    c = cfg()
    symbol = symbol.upper()
    collector = NewsCollector((["finviz"] if c.news.finviz else []) + c.news.feeds, DiskCache(c.data.cache_path),
                              c.data.contact_email, c.news.request_interval)
    with console.status(f"Collecting news for {symbol}..."):
        items = collector.collect(symbol, c.news.lookback_hours)
        rep = analyze_news(symbol, items, c.news, sources=collector.last_status)
    console.print(f"[bold]{T('news.title')}: {symbol}[/]  {T('news.score')} {rep.score:+.2f} · "
                  f"{rep.confirmed} {T('news.status.confirmed')} / {rep.unconfirmed} {T('news.status.unconfirmed')} / "
                  f"{rep.opinion} {T('news.status.opinion')}")
    console.print(f"[dim]{T('news.sources')}: " + ", ".join(f"{k} {v}" for k, v in rep.sources.items()) + "[/]")
    if rep.red_flags_confirmed or rep.red_flags_unconfirmed:
        console.print(f"[bold red]{T('news.flags')}:[/] confirmed {rep.red_flags_confirmed}, "
                      f"single-source {rep.red_flags_unconfirmed}")
    tbl = Table(show_lines=False)
    tbl.add_column("")
    tbl.add_column("Sent.", justify="right")
    tbl.add_column("Headline")
    tbl.add_column("Publishers")
    shown = rep.clusters if show_all else [x for x in rep.clusters if x.confirmed]
    order = {"PRIMARY": 0, "CONFIRMED": 1, "UNCONFIRMED": 2, "OPINION": 3}
    for cl in sorted(shown, key=lambda x: (order[x.status.value], -(x.last_seen.timestamp() if x.last_seen else 0)))[:40]:
        badge = {"PRIMARY": "[green]●[/]", "CONFIRMED": "[green]✓[/]", "UNCONFIRMED": "[yellow]?[/]", "OPINION": "[dim]~[/]"}
        color = "green" if cl.sentiment > 0.1 else "red" if cl.sentiment < -0.1 else "white"
        tbl.add_row(badge[cl.status.value], f"[{color}]{cl.sentiment:+.2f}[/]", cl.headline[:100],
                    ", ".join(cl.publishers[:4]) + (" ⚑" + ",".join(cl.red_flags) if cl.red_flags else ""))
    console.print(tbl)
    if not show_all:
        console.print("[dim]--all shows single-source and opinion stories too[/]")


# ----------------------------------------------------------------------------- signals
def _signals_table(signals, title: str) -> Table:
    tbl = Table(title=title)
    for k in ("col.symbol", "col.action", "col.conviction", "col.setups", "col.reason"):
        tbl.add_column(T(k), overflow="fold")
    for s in sorted(signals, key=lambda x: (x.action.value != "SELL", -x.strength)):
        color = "green" if s.action.value == "BUY" else "red"
        setups = ", ".join(i18n.setup(x, S.lang) for x in s.setups[:3])
        frac = f" {s.fraction:.0%}" if s.action.value == "SELL" and s.fraction else ""
        tbl.add_row(s.symbol, f"[{color}]{i18n.t('action.' + s.action.value, S.lang)}{frac}[/]", f"{s.strength:.2f}",
                    setups, s.reason[:160])
    return tbl


@app.command()
def scan(symbols: Optional[str] = typer.Option(None, help="Comma-separated symbols (default: the universe)"),
         strategy: Optional[str] = typer.Option(None, help="Strategy name or path.py:Class"),
         min_conviction: float = typer.Option(0.0, help="Hide buy signals below this conviction")) -> None:
    """Today's signals for the watchlist (no account needed). / 今日信号"""
    eng = _engine(strategy)
    syms = [s.strip().upper() for s in symbols.split(",")] if symbols else None
    with console.status("Running agents..."):
        market, signals, _ = eng.scan(syms)
    console.print(_market_panel(market))
    signals = [s for s in signals if s.action.value == "SELL" or s.strength >= min_conviction]
    if not signals:
        console.print(T("scan.none"))
        return
    console.print(_signals_table(signals, f"{T('scan.title')} · {eng.strategy.name} · {eng.profile.name}"))
    console.print(f"[dim]{T('disclaimer')}[/]")


@app.command()
def analyze(symbol: str) -> None:
    """Explain what every agent thinks about one symbol. / 单只股票的完整分析"""
    eng = _engine()
    symbol = symbol.upper()
    with console.status(f"Analyzing {symbol}..."):
        res = eng.analyze_symbol(symbol)
    market, tech, quality, news_rep, row = res["market"], res["technical"], res["quality"], res["news"], res["row"]
    console.print(f"[bold]{T('analyze.title')}: {symbol}[/]  close {row['close']:.2f} · "
                  f"[{_regime_style(market.regime.value)}]{i18n.regime(market.regime.value, S.lang, short=True)}[/]")
    lines = [f"EMA50 {row['ema50']:.2f} · EMA100 {row['ema100']:.2f} · EMA200 {row['ema200']:.2f} · "
             f"RSI14 {row['rsi14']:.0f} · RSI2 {row['rsi2']:.0f} · ATR {row['atr_pct'] * 100:.1f}% · "
             f"{T('market.trend')}: {i18n.t('trend.' + tech.trend, S.lang)} · 52w {row['dd252'] * 100:+.0f}%"]
    for k, v in tech.setups.items():
        lines.append(f"[green]✓ {i18n.setup(k, S.lang)}[/] {v:.2f}")
    for k in tech.blocked_setups:
        lines.append(f"[yellow]○ {i18n.setup(k, S.lang)}[/] (not allowed in {market.regime.value})")
    lines += [f"  {r}" for r in tech.reasons] + [f"[yellow]! {w}[/]" for w in tech.warnings]
    if not tech.setups and not tech.blocked_setups:
        lines.append("[dim]no buy setup today[/]")
    console.print(Panel("\n".join(lines), title=T("analyze.technical"), expand=False))
    if quality is not None:
        ql = [f"{quality.verdict} {quality.score:.0f}/100"] + [f"  + {r}" for r in quality.reasons] + \
             [f"  - {w}" for w in quality.warnings]
        if res["days_to_earnings"] is not None:
            ql.append(f"{T('analyze.earnings_in')}: {res['days_to_earnings']} trading days")
        console.print(Panel("\n".join(ql), title=T("analyze.fundamental"), expand=False))
    if news_rep is not None and news_rep.has_news:
        nl = [f"{T('news.score')} {news_rep.score:+.2f} · {news_rep.confirmed} confirmed · {news_rep.unconfirmed} single-source"]
        for cl in news_rep.top(5):
            nl.append(f"  [{cl.status.value.lower()}] {cl.headline[:90]} ({cl.sentiment:+.2f})")
        if news_rep.red_flags_confirmed or news_rep.red_flags_unconfirmed:
            nl.append(f"[red]{T('news.flags')}: {news_rep.red_flags_confirmed or ''} {news_rep.red_flags_unconfirmed or ''}[/]")
        console.print(Panel("\n".join(nl), title=T("analyze.news"), expand=False))
    sigs = res["signals"]
    if sigs:
        console.print(_signals_table(sigs, T("analyze.decision")))
    else:
        console.print(f"[bold]{T('analyze.decision')}:[/] {i18n.t('action.HOLD', S.lang)} / no entry")


# ----------------------------------------------------------------------------- trading
def _print_report(rep) -> None:
    if rep.account:
        a = rep.account
        pf = rep.portfolio
        acct = (f"{T('account.equity')} {fmt_money(a.equity)} · {T('account.cash')} {fmt_money(a.cash)}"
                + (f" · {T('account.cash_total')} {fmt_pct(pf.cash_ratio)} · {T('account.stocks')} "
                   f"{fmt_pct(pf.stock_exposure)} · {T('account.drawdown')} {fmt_pct(pf.drawdown)}" if pf else ""))
        console.print(Panel(acct, title=f"{T('account.title')} · {rep.broker} · {rep.mode}", expand=False))
    if rep.market:
        console.print(_market_panel(rep.market))
    if rep.signals:
        console.print(_signals_table(rep.signals, T("scan.title")))
    if rep.decision is not None:
        if rep.approved:
            tbl = Table(title=T("plan.title"))
            for k in ("col.symbol", "col.action", "col.qty", "col.price", "col.value", "col.tag", "col.reason"):
                tbl.add_column(T(k), overflow="fold")
            for o in rep.approved:
                color = "green" if o.side.value == "BUY" else "red"
                tbl.add_row(o.symbol, f"[{color}]{i18n.t('action.' + o.side.value, S.lang)}[/]", f"{o.qty:g}",
                            f"{(o.limit_price or o.price):,.2f}", fmt_money(o.value), i18n.tag(o.tag, S.lang),
                            o.reason[:120])
            console.print(tbl)
        else:
            console.print(T("plan.none"))
        if rep.decision.rejected:
            tbl = Table(title=T("scan.rejected"))
            tbl.add_column(T("col.symbol"))
            tbl.add_column(T("col.action"))
            tbl.add_column(T("col.reason"))
            for sig, why in rep.decision.rejected[:25]:
                tbl.add_row(sig.symbol, sig.action.value, why)
            console.print(tbl)
    for o, why in rep.blocked:
        console.print(f"[yellow]{T('plan.blocked')}:[/] {o.symbol} {o.side.value} {o.qty:g} – {why}")
    if rep.mode == "dry-run":
        console.print(f"[cyan]{T('plan.dry_run')}[/]")
    for r in rep.submitted:
        console.print(f"[green]{T('plan.submitted')}:[/] {r.side.value} {r.qty:g} {r.symbol} "
                      f"@ {r.limit_price or 'MKT'} → {r.status.value} (id {r.order_id})")
    for n in rep.notes:
        console.print(f"[dim]• {n}[/]")
    for e in rep.errors:
        console.print(f"[red]✗ {e}[/]")


@app.command()
def run(dry_run: bool = typer.Option(False, "--dry-run", help="Plan only, send nothing"),
        live: bool = typer.Option(False, "--live", help="Allow a LIVE account (also needs execution.mode: live)"),
        yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask before sending live orders"),
        strategy: Optional[str] = typer.Option(None, help="Strategy name or path.py:Class")) -> None:
    """Run one trading cycle: signals -> risk manager -> guards -> orders. / 运行一次交易循环"""
    c = cfg()
    mode = "dry-run" if dry_run else ("live" if live else c.execution.mode)
    eng = _engine(strategy)

    def confirm(rep) -> bool:
        if yes:
            return True
        _print_report(rep)
        return Confirm.ask(f"[bold red]{T('plan.confirm_live')}[/]", default=False)

    if mode == "live":
        console.print("[bold red]LIVE mode[/]")
        rep = eng.run_cycle(mode=mode, allow_live=live, confirm=confirm)
    else:
        with console.status(f"Running cycle ({mode})..."):
            rep = eng.run_cycle(mode=mode, allow_live=live, confirm=confirm)
    _print_report(rep)
    if rep.errors:
        raise typer.Exit(1)


@app.command()
def plan(strategy: Optional[str] = typer.Option(None)) -> None:
    """Preview today's orders against your broker account (sends nothing). / 预演今天的订单"""
    run(dry_run=True, live=False, yes=False, strategy=strategy)


@app.command()
def portfolio() -> None:
    """Account, positions, weights and open orders. / 账户与持仓"""
    from .brokers import make_broker
    from .universe import sector_of

    c = cfg()
    b = make_broker(c.broker.name, **c.broker.options)
    b.connect()
    a = b.account()
    pos = b.positions()
    orders = b.open_orders()
    b.close()
    equity = a.equity or 1.0
    cv = sum(p.market_value for p in pos if p.symbol == c.universe.cash_vehicle)
    console.print(Panel(f"{T('account.equity')} {fmt_money(a.equity)} · {T('account.cash')} {fmt_money(a.cash)} · "
                        f"{T('account.cash_total')} {fmt_pct((a.cash + cv) / equity)} "
                        f"({T('account.floor')} {fmt_pct(c.risk_profile().cash_floor, 0)})",
                        title=f"{T('account.title')} · {b.describe()}", expand=False))
    tbl = Table()
    for k in ("col.symbol", "col.qty", "col.price", "col.value", "col.weight", "col.gain", "col.sector"):
        tbl.add_column(T(k))
    for p in sorted(pos, key=lambda x: -x.market_value):
        g = p.unrealized_pct
        tbl.add_row(p.symbol, f"{p.qty:g}", f"{(p.market_price or 0):,.2f}", fmt_money(p.market_value),
                    fmt_pct(p.market_value / equity), f"[{'green' if g >= 0 else 'red'}]{g * 100:+.1f}%[/]",
                    sector_of(p.symbol, c.universe.sectors))
    console.print(tbl)
    if orders:
        console.print(f"open orders: {len(orders)}")
        for o in orders:
            console.print(f"  {o.side.value} {o.qty:g} {o.symbol} @ {o.limit_price} [{o.status.value}] id {o.order_id}")


@app.command()
def schedule(print_cron: bool = typer.Option(False, help="Print a cron line instead of running"),
             live: bool = typer.Option(False, "--live")) -> None:
    """Run the cycle every trading day at execution.run_time (New York time). / 每个交易日定时运行"""
    c = cfg()
    hh, mm = (int(x) for x in c.execution.run_time.split(":"))
    if print_cron:
        console.print(f"# runs at {hh:02d}:{mm:02d} America/New_York on weekdays; holidays are skipped by the CLI\n"
                      f"CRON_TZ=America/New_York\n{mm} {hh} * * 1-5 cd {Path.cwd()} && riskaware run"
                      + (" --live --yes" if live else ""))
        return
    console.print(f"scheduler started: {hh:02d}:{mm:02d} America/New_York, Ctrl+C to stop")
    while True:
        now = mc.now_ny()
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if target <= now or not mc.is_trading_day(target.date()):
            d = mc.next_trading_day(now.date())
            target = datetime.combine(d, target.timetz())
        console.print(f"next run: {target:%Y-%m-%d %H:%M %Z}")
        time.sleep(max(1, (target - mc.now_ny()).total_seconds()))
        try:
            S.cfg = None
            rep = _engine().run_cycle(mode="live" if live else None, allow_live=live, confirm=lambda r: live)
            _print_report(rep)
        except Exception as exc:  # keep the scheduler alive
            console.print(f"[red]cycle failed: {exc}[/]")


# ----------------------------------------------------------------------------- backtest
@app.command()
def backtest(
    strategy: str = typer.Option("risk_aware", "--strategy", "-s", help="Name or path/to/file.py[:Class]"),
    compare: Optional[str] = typer.Option(None, help="Also run these, comma-separated (e.g. buy_and_hold,rsi2)"),
    start: Optional[str] = typer.Option(None, help="YYYY-MM-DD (default from config)"),
    end: Optional[str] = typer.Option(None, help="YYYY-MM-DD (default today)"),
    symbols: Optional[str] = typer.Option(None, help="Comma-separated symbols (default: universe)"),
    preset: Optional[str] = typer.Option(None, help="Universe preset: megacap, tech, defensive, etf, sectors"),
    cash: Optional[float] = typer.Option(None, help="Initial cash"),
    synthetic: bool = typer.Option(False, help="Offline demo on synthetic prices"),
    out: Optional[str] = typer.Option(None, help="Report folder (default ./backtests)"),
    report: bool = typer.Option(True, "--report/--no-report"),
    open_report: bool = typer.Option(False, "--open", help="Open the HTML report"),
) -> None:
    """Backtest one or more strategies with the same risk engine. / 回测与策略对比"""
    from rich.progress import Progress

    from .backtest.engine import Backtester
    from .backtest.metrics import METRIC_LABELS, fmt_metric
    from .backtest.report import save_report
    from .data.prices import SyntheticPrices, make_price_provider
    from .strategy import load_strategy
    from .utils import DiskCache

    c = cfg()
    if preset:
        c.universe.preset = preset
        c.universe.symbols = []
    if symbols:
        c.universe.preset = None
        c.universe.symbols = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    d0 = date.fromisoformat(start or c.backtest.start)
    d1 = date.fromisoformat(end) if end else (c.backtest.end and date.fromisoformat(c.backtest.end)) or date.today()
    prices = SyntheticPrices(seed=7, start=d0 - timedelta(days=700), end=d1) if synthetic else \
        make_price_provider(c.data.price_source, DiskCache(c.data.cache_path), c.data.csv_dir)
    bt = Backtester(c, prices)
    names = [strategy] + ([x.strip() for x in compare.split(",") if x.strip()] if compare else [])
    results = []
    with Progress(console=console) as progress:
        for name in names:
            strat = load_strategy(name, c.strategy.params if name == c.strategy.name else {})
            task = progress.add_task(f"{strat.name}", total=100)
            res = bt.run(strat, d0, d1, initial_cash=cash,
                         progress=lambda i, n, _t=task: progress.update(_t, completed=100 * i / n))
            results.append(res)
    tbl = Table(title=f"{T('bt.title')} · {results[0].start} → {results[0].end} · {c.risk_profile().name}")
    tbl.add_column(T("col.metric"))
    for r in results:
        tbl.add_column(r.strategy, justify="right")
    tbl.add_column(T("col.benchmark"), justify="right")
    for k, (label, _) in METRIC_LABELS.items():
        if not any(k in r.metrics for r in results):
            continue
        tbl.add_row(label, *[fmt_metric(k, r.metrics.get(k)) for r in results],
                    fmt_metric(k, results[0].benchmark_metrics.get(k)))
    console.print(tbl)
    for n in results[0].notes:
        console.print(f"[dim]• {n}[/]")
    if synthetic:
        console.print("[yellow]Synthetic prices: a demo of the mechanics, not evidence about real markets.[/]")
    if report:
        path = save_report(results, out or c.backtest.output_dir, S.lang)
        console.print(f"{T('bt.report_saved')}: [link=file://{path.resolve()}]{path}[/link]")
        if open_report:
            import webbrowser

            webbrowser.open(path.resolve().as_uri())


# ----------------------------------------------------------------------------- strategies / brokers
@app.command()
def strategies() -> None:
    """List built-in strategies. / 内置策略"""
    from .strategy import list_strategies

    tbl = Table()
    tbl.add_column("name")
    tbl.add_column("description")
    for name, desc in list_strategies().items():
        tbl.add_row(name, desc)
    console.print(tbl)
    console.print("Write your own: riskaware new-strategy my_strategy")


@app.command("new-strategy")
def new_strategy(name: str, path: Optional[Path] = typer.Option(None)) -> None:
    """Create a strategy file from a template. / 生成自定义策略模板"""
    from .strategy.loader import STRATEGY_TEMPLATE

    target = path or Path.cwd() / f"{name}.py"
    if target.exists():
        console.print(f"[red]{target} already exists[/]")
        raise typer.Exit(1)
    cls = "".join(part.capitalize() for part in name.replace("-", "_").split("_")) or "MyStrategy"
    target.write_text(STRATEGY_TEMPLATE.format(path=target.name, cls=cls, name=name), encoding="utf-8")
    console.print(f"created {target}\n  riskaware backtest --strategy {target.name} --compare risk_aware,buy_and_hold")


@app.command()
def brokers() -> None:
    """Supported brokers and what each needs. / 支持的券商"""
    from .brokers import BROKERS

    mods = {"ibkr": "ib_async", "schwab": "schwab", "tastytrade": "tastytrade", "futu": "futu", "tiger": "tigeropen",
            "longbridge": "longport"}
    tbl = Table()
    for col in ("broker", "name", "paper trading", "install", "env vars", "SDK"):
        tbl.add_column(col)
    for key, spec in BROKERS.items():
        mod = mods.get(key)
        ok = mod is None or importlib.util.find_spec(mod) is not None
        tbl.add_row(key, spec["label"], spec["paper"],
                    f'pip install "risk-aware-trading-agents[{spec["install"]}]"' if spec["install"] else "built in",
                    ", ".join(spec["env"]) or "-", "[green]ready[/]" if ok else "[dim]missing[/]")
    console.print(tbl)


@app.command()
def halt(reason: str = typer.Argument("manual halt")) -> None:
    """Kill switch: block all order submission until `riskaware resume`. / 一键暂停交易"""
    from .guards import halt as _halt

    _halt(reason)
    console.print(f"[bold red]{T('halt.on')}[/]")


@app.command()
def resume() -> None:
    """Re-enable order submission. / 恢复交易"""
    from .guards import resume as _resume

    _resume()
    console.print(f"[green]{T('halt.off')}[/]")


@app.command()
def dashboard(port: int = typer.Option(8501)) -> None:
    """Open the web dashboard (needs the [dashboard] extra). / 打开网页看板"""
    if importlib.util.find_spec("streamlit") is None:
        console.print('Install it first: pip install "risk-aware-trading-agents[dashboard]"')
        raise typer.Exit(1)
    import subprocess

    app_path = Path(__file__).parent / "dashboard" / "app.py"
    env = dict(os.environ)
    if S.config_path:
        env["RISKAWARE_CONFIG"] = str(Path(S.config_path).resolve())
    env["RISKAWARE_LANG"] = S.lang
    from .dashboard.ui import THEME_FLAGS

    subprocess.run([sys.executable, "-m", "streamlit", "run", str(app_path), "--server.port", str(port),
                    "--browser.gatherUsageStats", "false", *THEME_FLAGS], env=env, check=False)


@app.command()
def mcp() -> None:
    """Serve the agents to Claude or any MCP client over stdio (needs the [mcp] extra). / MCP 服务器"""
    if importlib.util.find_spec("mcp") is None:
        console.print('Install it first: pip install "risk-aware-trading-agents[mcp]"', style="red")
        raise typer.Exit(1)
    from .mcp_server import main as serve

    serve()


@app.command("config")
def show_config() -> None:
    """Print the resolved configuration. / 显示当前配置"""
    import yaml

    c = cfg()
    console.print(f"# source: {c.source_path or '(defaults)'}")
    data = c.model_dump(mode="json")
    data["resolved_profile"] = c.risk_profile().model_dump(mode="json")
    console.print(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


# ----------------------------------------------------------------------------- agents
def _arena():
    from .arena import Arena

    return Arena(base_cfg=cfg())


_PROFILE_ZH = {"conservative": "保守", "balanced": "平衡", "aggressive": "激进"}


def _lb_table(rows: list[dict], title: str) -> Table:
    zh = S.lang == "zh"
    tbl = Table(title=title)
    for h in (("#", "#"), ("Agent", "智能体"), ("Strategy", "策略"), ("Profile", "档位"), ("Equity", "净值"),
              ("Return", "收益"), ("Today", "今日"), ("Max DD", "最大回撤"), ("vs SPY", "对比 SPY"),
              ("Invested", "持股"), ("Days", "天数"), ("Last run", "上次运行")):
        tbl.add_column(h[1] if zh else h[0], justify="right" if h[0] in ("Equity", "Return", "Today", "Max DD",
                                                                          "vs SPY", "Invested", "Days") else "left")
    for r in rows:
        ret, day = r["return"], r["day"]
        bench = r["benchmark_return"]
        tbl.add_row(str(r["rank"]), r["name"] + ("" if r["enabled"] else " (off)"), r["strategy"],
                    _PROFILE_ZH.get(r["profile"], r["profile"]) if zh else r["profile"],
                    fmt_money(r["equity"]), f"[{'green' if ret >= 0 else 'red'}]{ret * 100:+.2f}%[/]",
                    f"[{'green' if day >= 0 else 'red'}]{day * 100:+.2f}%[/]", f"{round(r['max_drawdown'] * 100, 1) + 0.0:.1f}%",
                    "n/a" if bench is None else f"{(ret - bench) * 100:+.2f} pp", fmt_pct(r["invested"]),
                    str(r["days"]), r["last_run"] or "-")
    return tbl


@agents_app.callback()
def agents_main(ctx: typer.Context) -> None:
    """Paper-trading agents: each one is a strategy with its own local paper account. / 模拟盘智能体"""
    if ctx.invoked_subcommand is None:
        agents_list()


@agents_app.command("list")
def agents_list() -> None:
    """Leaderboard of all agents. / 排行榜"""
    arena = _arena()
    if not arena.load():
        console.print("No agents yet. Create four starter agents with `riskaware agents init`, or your own with "
                      "`riskaware agents add NAME --strategy ...`." if S.lang == "en" else
                      "还没有智能体。用 `riskaware agents init` 创建四个示例，或者用 `riskaware agents add 名字 --strategy ...` "
                      "创建你自己的。")
        return
    with console.status("Loading SPY for comparison..."):
        bench = arena.benchmark()
    console.print(_lb_table(arena.leaderboard(bench), "Paper-trading agents" if S.lang == "en" else "模拟盘智能体"))
    st = arena.schedule_status()
    from .arena import state_text
    from .scheduler import installed

    zh = S.lang == "zh"
    on = installed()
    console.print(("每日运行：" if zh else "Daily run: ")
                  + (f"[green]{'已开启' if zh else 'installed'}[/]" if on else
                     f"[yellow]{'未开启' if zh else 'not installed'}[/] (`riskaware agents schedule --install`)")
                  + (f" · 纽约时间 {st['run_time']} · " if zh else f" · {st['run_time']} New York · ")
                  + state_text(st["state"], S.lang))


@agents_app.command("init")
def agents_init() -> None:
    """Create four starter agents (steady, momentum, breakout, fear-buyer). / 创建四个示例智能体"""
    added = _arena().add_starters()
    if not added:
        console.print("The starter agents already exist." if S.lang == "en" else "示例智能体已经存在。")
    for a in added:
        console.print(f"[green]+[/] {a.name}: {a.strategy} · {a.profile} · {a.universe_label} · {fmt_money(a.cash)}")
    if added:
        console.print("Next: `riskaware agents run` trades them once now; `riskaware agents schedule --install` runs "
                      "them every trading day." if S.lang == "en" else
                      "下一步：`riskaware agents run` 现在跑一次；`riskaware agents schedule --install` 让它们每个交易日自动运行。")


@agents_app.command("add")
def agents_add(
    name: str = typer.Argument(..., help="lowercase name, e.g. my-dips"),
    strategy: str = typer.Option("risk_aware", "--strategy", "-s", help="built-in name or path.py:Class"),
    profile: str = typer.Option("balanced", help="conservative | balanced | aggressive"),
    universe: Optional[str] = typer.Option(None, "--universe", "-u",
                                           help="megacap | tech | defensive | etf | sectors (combine with +)"),
    symbols: Optional[str] = typer.Option(None, help="extra symbols, comma separated"),
    cash: float = typer.Option(100_000.0, help="starting cash"),
    param: Optional[list[str]] = typer.Option(None, "--param", help="strategy parameter key=value (repeatable)"),
    note: str = typer.Option("", help="a short description"),
    overwrite: bool = typer.Option(False, "--overwrite", help="replace an agent with the same name"),
) -> None:
    """Create a paper-trading agent from any strategy, including your own file. / 新建智能体"""
    import yaml

    from .arena import AgentSpec

    params = {}
    for kv in param or []:
        if "=" not in kv:
            raise typer.BadParameter(f"--param needs key=value, got '{kv}'")
        k, v = kv.split("=", 1)
        params[k.strip()] = yaml.safe_load(v)
    if strategy.endswith(".py") or ".py:" in strategy:
        path, _, cls = strategy.partition(".py")
        strategy = str(Path(path + ".py").expanduser().resolve()) + cls
    try:
        spec = _arena().add(AgentSpec(name=name, strategy=strategy, profile=profile, preset=universe,
                                      symbols=(symbols or "").split(","), cash=cash, params=params, note=note),
                            overwrite=overwrite)
    except Exception as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    console.print(f"[green]+[/] {spec.name}: {spec.strategy} · {spec.profile} · {spec.universe_label} · "
                  f"{fmt_money(spec.cash)}")


@agents_app.command("remove")
def agents_remove(name: str, keep_data: bool = typer.Option(False, "--keep-data", help="keep the account files"),
                  yes: bool = typer.Option(False, "--yes", "-y")) -> None:
    """Delete an agent and its paper account. / 删除智能体"""
    if not yes and not Confirm.ask(f"Delete agent '{name}'" + ("" if keep_data else " and its paper account") + "?",
                                   default=False):
        raise typer.Exit()
    try:
        _arena().remove(name, delete_data=not keep_data)
    except KeyError as exc:
        console.print(f"[red]{exc.args[0]}[/]")
        raise typer.Exit(1) from exc
    console.print(f"removed {name}")


@agents_app.command("reset")
def agents_reset(name: str, yes: bool = typer.Option(False, "--yes", "-y")) -> None:
    """Start an agent over with its starting cash. / 重置智能体的模拟账户"""
    if not yes and not Confirm.ask(f"Reset '{name}' to its starting cash and forget its history?", default=False):
        raise typer.Exit()
    try:
        _arena().reset(name)
    except KeyError as exc:
        console.print(f"[red]{exc.args[0]}[/]")
        raise typer.Exit(1) from exc
    console.print(f"reset {name}")


@agents_app.command("pause")
def agents_pause(name: str) -> None:
    """Skip an agent in daily runs. / 暂停"""
    _arena().set_enabled(name, False)
    console.print(f"{name}: paused")


@agents_app.command("resume")
def agents_resume(name: str) -> None:
    """Include an agent in daily runs again. / 恢复"""
    _arena().set_enabled(name, True)
    console.print(f"{name}: active")


@agents_app.command("show")
def agents_show(name: str) -> None:
    """Positions, recent orders and the equity history of one agent. / 查看单个智能体"""
    from .journal import Journal

    arena = _arena()
    try:
        spec = arena.get(name)
    except KeyError as exc:
        console.print(f"[red]{exc.args[0]}[/]")
        raise typer.Exit(1) from exc
    console.print(Panel(f"{spec.strategy} · {spec.profile} · {spec.universe_label} · start {fmt_money(spec.cash)} "
                        f"· since {spec.created}" + (f"\n{spec.note}" if spec.note else ""), title=spec.name,
                        expand=False))
    acct = arena.account_state(spec.name)
    if acct.get("positions"):
        tbl = Table(title=T("account.title"))
        for k in ("col.symbol", "col.qty", "col.price"):
            tbl.add_column(T(k))
        for s, p in sorted(acct["positions"].items()):
            tbl.add_row(s, f"{p['qty']:g}", f"{p['avg_cost']:,.2f}")
        console.print(tbl)
        console.print(f"{T('account.cash')} {fmt_money(acct['cash'])}")
    hist = arena.equity_history(spec.name)
    if not hist.empty:
        console.print(hist.tail(10).to_string(index=False))
    jpath = arena.dir(spec.name) / "journal.db"
    if jpath.is_file():
        orders = Journal(jpath).recent_orders(15)
        for o in orders:
            console.print(f"{o['ts'][:16]}  {o['side']:<4} {o['qty']:g} {o['symbol']:<6} {o['status']:<9} {o['reason'][:80]}")


@agents_app.command("run")
def agents_run(
    names: Optional[list[str]] = typer.Argument(None, help="agents to run (default: every active agent)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="plan only, send nothing"),
    scheduled: bool = typer.Option(False, "--scheduled", help="what the background job calls: trade each agent "
                                                               "once per trading day after execution.run_time"),
    cwd: Optional[Path] = typer.Option(None, "--cwd", help="change to this folder first (for .env and the config)"),
    verbose: bool = typer.Option(False, "--details", help="print every agent's full report"),
) -> None:
    """Run the agents once now on their paper accounts. / 现在运行一次"""
    if cwd:
        os.chdir(cwd)
        S.cfg = None
    arena = _arena()
    if scheduled:
        why, runs = arena.scheduled_run()
        if not runs:  # the job fires every 15 minutes; only log what someone would want to know
            if why == "another run is in progress":
                arena.log(f"skip: {why}")
            return
        lines = [r.summary() for r in runs]
        arena.log("\n".join(["run:", *lines]))
        from .notify import send

        send(cfg().notify, "Risk-Aware Trading Agents, daily paper run:\n" + "\n".join(lines))
        return
    if not arena.load():
        console.print("No agents yet: `riskaware agents init`." if S.lang == "en" else "还没有智能体：`riskaware agents init`。")
        raise typer.Exit(1)
    with console.status("Running agents...") as status:
        try:
            runs = arena.run(names or None, mode="dry-run" if dry_run else "paper",
                             progress=lambda n: status.update(f"Running {n}..."))
        except (KeyError, RuntimeError) as exc:
            console.print(f"[red]{exc.args[0]}[/]")
            raise typer.Exit(1) from exc
    tbl = Table(title="Agents run" if S.lang == "en" else "本次运行")
    for h in ("Agent", "Regime", "Equity", "Signals", "Orders", "Filled", "Blocked", "Errors"):
        tbl.add_column(h)
    for r in runs:
        tbl.add_row(r.name, r.regime or "-", fmt_money(r.equity) if r.equity is not None else "-", str(r.signals),
                    str(r.orders), str(r.filled), str(r.blocked), "; ".join(r.errors)[:80] or "")
        if verbose and r.report is not None:
            _print_report(r.report)
    console.print(tbl)
    if not dry_run:
        arena.log("manual run:\n" + "\n".join(r.summary() for r in runs))
    if any(not r.ok for r in runs):
        raise typer.Exit(1)


@agents_app.command("schedule")
def agents_schedule(install: bool = typer.Option(False, "--install", help="run the agents every trading day"),
                    uninstall: bool = typer.Option(False, "--uninstall", help="remove the background job")) -> None:
    """Run every agent automatically each trading day (macOS, Linux, Windows). / 每个交易日自动运行"""
    from . import scheduler

    arena = _arena()
    if install and uninstall:
        raise typer.BadParameter("choose --install or --uninstall")
    if uninstall:
        console.print(scheduler.uninstall())
        return
    if install:
        if not arena.load():
            arena.add_starters()
            console.print("No agents yet, so the four starter agents were created." if S.lang == "en" else
                          "还没有智能体，已创建四个示例智能体。")
        config = Path(cfg().source_path) if cfg().source_path else None
        try:
            what = scheduler.install(Path.cwd(), arena.log_file.parent / "scheduler.out", config)
        except RuntimeError as exc:
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(1) from exc
        arena.log(f"scheduler installed: {what}")
        console.print(f"[green]installed[/] {what}")
    st = arena.schedule_status()
    from .arena import state_text

    on = scheduler.installed()
    if S.lang == "zh":
        console.print(f"{'已开启' if on else '未开启'} · 每个交易日纽约时间 {st['run_time']} 之后交易一次 · "
                      f"下一个交易日 {st['next_day']} · 现在：{state_text(st['state'], 'zh')}")
    else:
        console.print(f"{'installed' if on else 'not installed'} · trades after {st['run_time']} New York, once per "
                      f"trading day · next trading day {st['next_day']} · now: {st['state']}")
    last = st["last_run"]
    if last:
        done = ", ".join(last.get("done", [])) or "-"
        console.print(f"上次运行 {last.get('date')}：{done}" if S.lang == "zh" else f"last run {last.get('date')}: {done}")
    console.print(f"{'日志' if S.lang == 'zh' else 'log'}: {arena.log_file}")


@agents_app.command("log")
def agents_log(lines: int = typer.Option(40, "--lines", "-n")) -> None:
    """What the daily job did recently. / 运行日志"""
    for ln in _arena().recent_log(lines):
        console.print(ln, markup=False, highlight=False)


@paper_app.command("reset")
def paper_reset(cash: float = typer.Option(100_000.0), account: str = typer.Option("default")) -> None:
    """Reset the local paper account. / 重置本地模拟盘"""
    from .brokers.paper import PaperBroker

    b = PaperBroker(account_name=account, starting_cash=cash)
    b.reset(cash)
    console.print(f"paper account '{account}' reset to {fmt_money(cash)} ({b.path})")


@app.command()
def version() -> None:
    """Show the version."""
    console.print(f"risk-aware-trading-agents {__version__}")


def main() -> None:
    logging.captureWarnings(True)
    app()


if __name__ == "__main__":
    main()
