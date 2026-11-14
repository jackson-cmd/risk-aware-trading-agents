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
from rich.markup import escape
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
news_app = typer.Typer(help="Local news archive for backtests (SEC 8-K, your Benzinga key, CSV). / 回测用的本地新闻库",
                       no_args_is_help=True)
app.add_typer(news_app, name="news-history")
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
    env_example = Path(__file__).resolve().parents[2] / "examples" / ".env.example"
    console.print(Panel.fit(
        (f"已写入 {target}\n\n下一步：\n  riskaware doctor     检查环境\n  riskaware regime     看市场状态\n"
         f"  riskaware scan       看今天的信号\n  riskaware backtest   回测\n  riskaware run        模拟盘下单")
        if zh else
        (f"Wrote {target}\n\nNext:\n  riskaware doctor     check your setup\n  riskaware regime     market regime\n"
         f"  riskaware scan       today's signals\n  riskaware backtest   backtest\n  riskaware run        paper trade"),
        title=T("app.name")))
    if broker != "paper":
        console.print(("把券商 key 写进 .env（参考 examples/.env.example）。" if zh else
                       "Put your broker keys in a .env file (see examples/.env.example).")
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
    from .news.relevance import company_names
    from .news.sources import NewsCollector
    from .news.verify import analyze_news
    from .utils import DiskCache

    c = cfg()
    symbol = symbol.upper()
    cache = DiskCache(c.data.cache_path)
    collector = NewsCollector((["finviz"] if c.news.finviz else []) + c.news.feeds, cache,
                              c.data.contact_email, c.news.request_interval)
    with console.status(f"Collecting news for {symbol}..."):
        items = collector.collect(symbol, c.news.lookback_hours)
        rep = analyze_news(symbol, items, c.news, sources=collector.last_status, names=company_names(symbol, cache))
    console.print(f"[bold]{T('news.title')}: {symbol}[/]  {T('news.score')} {rep.score:+.2f} · "
                  f"{rep.confirmed} {T('news.status.confirmed')} / {rep.unconfirmed} {T('news.status.unconfirmed')} / "
                  f"{rep.opinion} {T('news.status.opinion')}")
    console.print(f"[dim]{T('news.sources')}: " + ", ".join(f"{k} {v}" for k, v in rep.sources.items()) + "[/]")
    if rep.off_topic:
        console.print(f"[dim]{rep.off_topic} " + ("headlines about other companies or the market were ignored"
                                                  if S.lang == "en" else "条讲其他公司或大盘的新闻已忽略") + "[/]")
    if rep.red_flags_confirmed or rep.red_flags_unconfirmed or rep.red_flags_denied:
        console.print(f"[bold red]{T('news.flags')}:[/] confirmed {rep.red_flags_confirmed}, "
                      f"single-source {rep.red_flags_unconfirmed}"
                      + (f", {T('news.denied')} {rep.red_flags_denied}" if rep.red_flags_denied else ""))
    tbl = Table(show_lines=False)
    tbl.add_column("")
    tbl.add_column("Sent.", justify="right")
    tbl.add_column("Headline")
    tbl.add_column("Publishers")
    shown = rep.clusters if show_all else [x for x in rep.clusters if x.confirmed]
    order = {"PRIMARY": 0, "ISSUER": 1, "CONFIRMED": 2, "UNCONFIRMED": 3, "OPINION": 4}
    badge = {"PRIMARY": "[green]●[/]", "ISSUER": "[green]◆[/]", "CONFIRMED": "[green]✓[/]",
             "UNCONFIRMED": "[yellow]?[/]", "OPINION": "[dim]~[/]"}
    for cl in sorted(shown, key=lambda x: (order[x.status.value], -(x.last_seen.timestamp() if x.last_seen else 0)))[:40]:
        color = "green" if cl.sentiment > 0.1 else "red" if cl.sentiment < -0.1 else "white"
        note = (" ⚑" + ",".join(cl.live_flags) if cl.live_flags else "") + \
               (f" ✗{T('news.denied')}:" + ",".join(cl.cleared_flags) if cl.cleared_flags else "") + \
               (" ↩ " + T("news.denial") if cl.denial else "")
        tbl.add_row(badge[cl.status.value], f"[{color}]{cl.sentiment:+.2f}[/]", cl.headline[:100],
                    ", ".join(cl.publishers[:4]) + note)
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
        if news_rep.red_flags_denied:
            nl.append(f"[dim]{T('news.denied')}: {', '.join(news_rep.red_flags_denied)}[/]")
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
    news: Optional[bool] = typer.Option(None, "--news/--no-news",
                                        help="Replay the local news archive (default: backtest.news in the config)"),
    costs: Optional[str] = typer.Option(None, "--costs", help="fixed (slippage only) or realistic (also part of the "
                                        "overnight gap, square-root market impact and a 1% volume cap; docs/costs.md)"
                                        "; default: backtest.cost_model in the config"),
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
    if costs is not None:
        if costs not in ("fixed", "realistic"):
            console.print("[red]--costs must be fixed or realistic[/]")
            raise typer.Exit(2)
        c.backtest.cost_model = costs
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
    pit = None
    if (c.backtest.news if news is None else news) and not synthetic:
        from .news.history import NewsArchive, PointInTimeNews
        from .news.relevance import company_names
        from .utils import DiskCache as _Cache

        archive = NewsArchive(c.news.archive_path, bundled=c.news.bundled)
        name_cache = _Cache(c.data.cache_path)
        pit = PointInTimeNews(archive, c.news, c.universe.resolve(), names=lambda s: company_names(s, name_cache))
        if not pit.symbols:
            pit = None
            console.print(f"[yellow]{T('bt.news_off')}[/]")
    bt = Backtester(c, prices, news=pit)
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


@app.command("lab")
def lab_cmd(
    path: Path = typer.Argument(..., help="A strategy file, e.g. one an AI wrote for you"),
    symbols: Optional[str] = typer.Option(None, help="Comma-separated symbols"),
    preset: str = typer.Option("megacap", help="Universe preset when no --symbols"),
    start: str = typer.Option("2026-01-02", help="YYYY-MM-DD"),
    end: Optional[str] = typer.Option(None, help="YYYY-MM-DD (default today)"),
    news: bool = typer.Option(True, "--news/--no-news", help="Replay the news archive (2026 ships with the package)"),
    profile: str = typer.Option("balanced", help="Risk profile"),
    check_only: bool = typer.Option(False, "--check-only", help="Only check the code; do not run it"),
    save: Optional[str] = typer.Option(None, help="Keep the strategy under this name in ~/.riskaware/strategies/ai"),
) -> None:
    """Check a strategy for look-ahead and side effects, then backtest it in a separate process. / 检查并回测策略代码"""
    from rich.markup import escape

    from . import lab

    code = path.read_text(encoding="utf-8")
    syms = [x.strip().upper() for x in symbols.split(",") if x.strip()] if symbols else None
    names = syms or _symbols_arg(None, preset)
    problems, warnings = lab.review_code(code, names)
    for p in problems:
        console.print(f"[red]line {p['line']}:[/] {escape(p['problem'])}", highlight=False)
    for w in warnings:
        console.print(f"[yellow]line {w['line']}:[/] {escape(w['warning'])}", highlight=False)
    if problems:
        raise typer.Exit(1)
    if check_only:
        console.print("[green]No problems found.[/]")
        return
    with console.status("backtesting in a separate process..."):
        out = lab.backtest_code(code, symbols=syms, universe=None if syms else preset, start=start, end=end,
                                news=news, profile=profile, save_as=save)
    if not out.get("ok"):
        where = ", ".join(f"line {w['line']}" for w in out.get("where") or [])
        console.print(f"[red]{escape(str(out.get('error') or out.get('problems')))}[/]"
                      + (f" ({where})" if where else ""), highlight=False)
        raise typer.Exit(1)
    from .backtest.metrics import METRIC_LABELS, fmt_metric

    cmp = out.get("compare") or {}
    tbl = Table(title=f"{out['strategy']} · {out['period']}")
    tbl.add_column(T("col.metric"))
    for col in (out["strategy"], *cmp, "SPY"):
        tbl.add_column(col, justify="right")
    for k, (label, _) in METRIC_LABELS.items():
        if k in out["metrics"]:
            tbl.add_row(label, fmt_metric(k, out["metrics"].get(k)), *[fmt_metric(k, v.get(k)) for v in cmp.values()],
                        fmt_metric(k, out["spy_buy_and_hold"].get(k)))
    console.print(tbl)
    for n in out.get("notes", []):
        console.print(f"[dim]• {escape(n)}[/]", highlight=False)
    if out.get("saved"):
        console.print(f"saved: {out['saved']['path']}")


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
                    escape(f'pip install "risk-aware-trading-agents[{spec["install"]}]"') if spec["install"]
                    else "built in",
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
        console.print('Install it first: pip install "risk-aware-trading-agents[dashboard]"', markup=False)
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
        console.print('Install it first: pip install "risk-aware-trading-agents[mcp]"', style="red", markup=False)
        raise typer.Exit(1)
    from .mcp_server import main as serve

    serve()


def _tokens(usage: dict) -> int | None:
    if not usage:
        return None
    if usage.get("total_tokens"):
        return int(usage["total_tokens"])
    parts = [usage.get(k) for k in ("input_tokens", "output_tokens", "prompt_tokens", "completion_tokens")]
    return sum(int(x) for x in parts if x) or None


@app.command("mcp-setup")
def mcp_setup(client: str = typer.Argument(..., help="claude-code, claude-desktop, codex, gemini or antigravity"),
              do_apply: bool = typer.Option(False, "--apply", help="register it now instead of printing the command"),
              tool: Optional[str] = typer.Option(None, "--command", help="the client's executable or wrapper, "
                                                                         "e.g. agyw")) -> None:
    """Connect the MCP server to Claude, Codex (ChatGPT login), Gemini CLI or Antigravity. / 把 MCP 接到 AI 客户端"""
    import json
    import shlex

    from . import mcp_setup as ms

    if client not in ms.CLIENTS:
        console.print(f"[red]choose one of {', '.join(ms.CLIENTS)}[/]")
        raise typer.Exit(1)
    if importlib.util.find_spec("mcp") is None:
        typer.echo('The server needs the MCP extra first: pip install "risk-aware-trading-agents[mcp]"',
                   err=True)  # stderr, so the command printed below can be copied or piped as it is
    if do_apply:
        try:
            console.print(ms.apply(client, tool))
        except (RuntimeError, ValueError, OSError) as exc:
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(1) from exc
        return
    if client == "claude-desktop":  # plain output, so the lines can be copied as they are
        typer.echo(f"Add this to {ms.desktop_config_path()} (or run with --apply), then restart Claude Desktop:")
        typer.echo(json.dumps(ms.desktop_snippet(), indent=2, ensure_ascii=False))
        return
    typer.echo(shlex.join(ms.setup_command(client, tool=tool)))


@app.command("llm-check")
def llm_check(choice: str = typer.Argument("openai:gpt-5.6-luna", help="provider:model, e.g. ollama:qwen2.5:3b, "
                                                                        "codex-cli, gemini-cli, claude-cli:sonnet"),
              base_url: Optional[str] = typer.Option(None, "--base-url"),
              key_env: Optional[str] = typer.Option(None, "--key-env"),
              command: Optional[str] = typer.Option(None, "--command", help="for the -cli choices")) -> None:
    """Send one sample review to an LLM and show its answer: checks a key or a local model. / 测试 LLM 连接"""
    from .llm import llm_config, make_reviewer
    from .types import Action, Signal

    try:
        c = llm_config(choice, base=cfg().llm, base_url=base_url, key_env=key_env, command=command)
        reviewer = make_reviewer(c)
    except (ValueError, RuntimeError) as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc

    class _Market:
        regime = "NEUTRAL"
        vix, fear_greed, event_risk = 17.5, 45.0, 0.2
        reasons = ["VIX 17.5", "SPY above a rising EMA200"]

    sig = Signal("ACME", Action.BUY, 0.66, "price at the EMA200 major support (+1.2%); 18% below the 52-week high",
                 setups=["ema200_support"])
    with console.status(f"Asking {c.model}..."):
        verdict = reviewer.review(sig, _Market())
    rec = reviewer.pop_records()[0]
    if verdict is None:
        console.print(f"[red]no usable answer from {c.model}[/] ({rec.error}); raw reply: {(rec.raw or '')[:300]}")
        raise typer.Exit(1)
    console.print(Panel(f"bull: {verdict.bull_case}\nbear: {verdict.bear_case}\njudge: {verdict.summary}\n"
                        f"adjustment {verdict.adjustment:+.2f} · veto {verdict.veto} · {rec.latency_ms} ms"
                        + (f" · {_tokens(rec.usage)} tokens" if _tokens(rec.usage) else ""),
                        title=f"{c.model} via {c.command or c.base_url or c.provider}", expand=False))


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
        tbl.add_row(str(r["rank"]), r["name"] + ("" if r["enabled"] else " (off)"),
                    r["strategy"] + (f" + {r['llm']}" if r.get("llm") else ""),
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
    llm: Optional[str] = typer.Option(None, "--llm", help="LLM review: openai:gpt-5.6-luna, ollama:qwen2.5:3b, "
                                                           "anthropic:MODEL, compat:MODEL; without an API key: "
                                                           "claude-cli, codex-cli, gemini-cli; or off"),
    llm_base_url: Optional[str] = typer.Option(None, "--llm-base-url", help="for compat: or a remote Ollama"),
    llm_key_env: Optional[str] = typer.Option(None, "--llm-key-env", help="environment variable with the API key"),
    llm_command: Optional[str] = typer.Option(None, "--llm-command", help="for the -cli choices: the executable "
                                                                           "or wrapper to run"),
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
                                      symbols=(symbols or "").split(","), cash=cash, params=params, note=note,
                                      llm=llm, llm_base_url=llm_base_url, llm_key_env=llm_key_env,
                                      llm_command=llm_command),
                            overwrite=overwrite)
    except Exception as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    console.print(f"[green]+[/] {spec.name}: {spec.strategy} · {spec.profile} · {spec.universe_label} · "
                  f"{fmt_money(spec.cash)}" + (f" · LLM {spec.llm}" if spec.llm else ""))


@agents_app.command("llm")
def agents_llm(name: str, choice: str = typer.Argument(..., help="openai:gpt-5.6-luna, ollama:qwen2.5:3b, "
                                                                  "anthropic:MODEL, compat:MODEL, claude-cli, "
                                                                  "codex-cli, gemini-cli, off, or config"),
               base_url: Optional[str] = typer.Option(None, "--base-url"),
               key_env: Optional[str] = typer.Option(None, "--key-env"),
               command: Optional[str] = typer.Option(None, "--command", help="for the -cli choices")) -> None:
    """Give an agent an LLM review board, or turn it off. / 设置智能体的 LLM 复核"""
    try:
        spec = _arena().set_llm(name, None if choice == "config" else choice, base_url, key_env, command)
    except (KeyError, ValueError) as exc:
        console.print(f"[red]{exc.args[0] if exc.args else exc}[/]")
        raise typer.Exit(1) from exc
    console.print(f"{spec.name}: LLM " + (spec.llm or "as in the config"))


@agents_app.command("why")
def agents_why(name: str, symbol: Optional[str] = typer.Argument(None, help="only this stock"),
               limit: int = typer.Option(10, "--limit", "-n"),
               prompt: bool = typer.Option(False, "--prompt", help="also print the prompt sent to the LLM and its "
                                                                   "raw reply")) -> None:
    """How an agent made its latest decisions: setups, the risk check and the LLM's bull, bear and judge.
    / 智能体是怎么做决定的"""
    arena = _arena()
    try:
        arena.get(name)
    except KeyError as exc:
        console.print(f"[red]{exc.args[0]}[/]")
        raise typer.Exit(1) from exc
    zh = S.lang == "zh"
    rows = arena.decisions(name, symbol, limit, with_prompts=prompt)
    if not rows:
        console.print("No decisions yet." if not zh else "还没有决策记录。")
        return
    for r in rows:
        ok = "[green]" + ("accepted" if not zh else "通过") + "[/]" if r["accepted"] else \
            "[yellow]" + ("blocked" if not zh else "被拦下") + f"[/] ({r['reject_reason']})"
        lines = [f"{r['action']} · {'conviction' if not zh else '信心'} {r['strength']:.2f} · {ok}",
                 f"[dim]{'setups' if not zh else '形态'}:[/] {', '.join(i18n.setup(s, S.lang) for s in r['setups'])}",
                 f"[dim]{'why' if not zh else '理由'}:[/] {r['reason']}"]
        rv = r.get("llm")
        if rv:
            if rv.get("error") and rv.get("adjustment") is None:
                lines.append(f"[red]LLM {rv['model']}: {rv['error']}[/]")
            else:
                verdict = (f"[red]VETO[/] {rv['veto_reason']}" if rv.get("veto")
                           else f"{'adjustment' if not zh else '调整'} {rv['adjustment']:+.2f}")
                lines += [f"[bold]LLM {rv['model']}[/] · {verdict} · {rv['latency_ms']} ms",
                          f"[green]{'bull' if not zh else '多方'}:[/] {rv['bull']}",
                          f"[red]{'bear' if not zh else '空方'}:[/] {rv['bear']}",
                          f"[cyan]{'judge' if not zh else '裁判'}:[/] {rv['summary']}"]
            if prompt:
                lines += ["[dim]--- prompt ---[/]", rv.get("prompt") or "", "[dim]--- raw reply ---[/]",
                          rv.get("raw") or ""]
        console.print(Panel("\n".join(lines), title=f"{r['symbol']} · {str(r['ts'])[:16]}", expand=False))


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


# ----------------------------------------------------------------------------- news history
def _archive():
    from .news.history import NewsArchive

    c = cfg()
    return NewsArchive(c.news.archive_path, bundled=c.news.bundled)


def _symbols_arg(symbols: Optional[str], preset: Optional[str]) -> list[str]:
    from .universe import preset_symbols

    if symbols:
        return [x.strip().upper() for x in symbols.split(",") if x.strip()]
    out: list[str] = []
    for name in (preset or "core").split("+"):
        out.extend(preset_symbols(name.strip()))
    return list(dict.fromkeys(out))


@news_app.command("build")
def news_history_build(
    symbols: Optional[str] = typer.Option(None, help="Comma-separated symbols (default: the 'core' preset)"),
    preset: Optional[str] = typer.Option(None, help="Universe preset instead of --symbols: core, megacap, tech, ..."),
    start: str = typer.Option("2018-01-01", help="YYYY-MM-DD"),
    end: Optional[str] = typer.Option(None, help="YYYY-MM-DD (default today)"),
    source: str = typer.Option("sec", help="sec, benzinga, or both: sec,benzinga"),
    contact_email: Optional[str] = typer.Option(None, help="Contact email SEC asks for (default: data.contact_email)"),
) -> None:
    """Download point-in-time news into the local archive. / 下载历史新闻到本地新闻库"""
    from .config import env
    from .news.history_sources import BenzingaHistory, HistoryError, SecFilingHistory
    from .utils import DiskCache

    c = cfg()
    syms = _symbols_arg(symbols, preset)
    d0 = date.fromisoformat(start)
    d1 = date.fromisoformat(end) if end else date.today()
    wanted = [x.strip().lower() for x in source.split(",") if x.strip()]
    archive = _archive()
    try:
        fetchers = []
        if "sec" in wanted:
            fetchers.append(("sec", SecFilingHistory(contact_email or c.data.contact_email, DiskCache(c.data.cache_path))))
        if "benzinga" in wanted:
            fetchers.append(("benzinga", BenzingaHistory(env(c.news.benzinga_key_env))))
            console.print(f"[dim]{T('nh.private')}[/]")
        if not fetchers:
            raise HistoryError(f"unknown source '{source}' (use sec, benzinga or sec,benzinga)")
        tbl = Table(title=f"{T('nh.title')} · {d0} → {d1}")
        for k in ("col.symbol", "nh.feeds", "nh.added", "nh.items"):
            tbl.add_column(T(k), justify="left" if k in ("col.symbol", "nh.feeds") else "right")
        for sym in syms:
            for name, f in fetchers:
                with console.status(f"{name}: {sym}..."):
                    items = f.fetch(sym, d0, d1)
                added = archive.add(items)
                tbl.add_row(sym, name, str(added), str(len(archive.items(sym))))
    except HistoryError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(2) from exc
    console.print(tbl)
    console.print(f"[dim]{archive.root}[/]")


@news_app.command("import")
def news_history_import(
    path: str = typer.Argument(..., help="CSV with timestamp, symbol, headline[, source, url, sentiment]"),
    source: Optional[str] = typer.Option(None, help="Publisher name for rows without a source column"),
    tz: str = typer.Option("America/New_York",
                           help="Time zone for timestamps without one (New York by default: a wrong guess then "
                                "only delays an item, never shows it early)"),
) -> None:
    """Import news from a CSV into the local archive. / 从 CSV 导入新闻"""
    from .news.history import read_csv

    try:
        items = read_csv(path, default_source=source, assume_tz=tz)
    except (OSError, ValueError) as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(2) from exc
    archive = _archive()
    added = archive.add(items)
    syms = sorted({i.symbol for i in items if i.symbol})
    console.print(f"{T('nh.added')}: {added} / {len(items)} · {len(syms)} {T('col.symbol')}: {', '.join(syms[:20])}")
    console.print(f"[dim]{T('nh.private')}[/]")


@news_app.command("status")
def news_history_status() -> None:
    """What the local archive holds. / 查看本地新闻库"""
    archive = _archive()
    stats = archive.stats()
    if not stats:
        console.print(T("nh.empty"))
        return
    tbl = Table(title=f"{T('nh.title')} · {archive.root}")
    for k in ("col.symbol", "nh.items", "nh.bundled", "nh.first", "nh.last", "nh.feeds"):
        tbl.add_column(T(k), justify="right" if k in ("nh.items", "nh.bundled") else "left")
    for st in stats:
        tbl.add_row(st.symbol, str(st.items), str(st.bundled) if st.bundled else "-",
                    st.first.date().isoformat() if st.first else "-", st.last.date().isoformat() if st.last else "-",
                    ", ".join(f"{k} {v}" for k, v in sorted(st.feeds.items())))
    console.print(tbl)
    info = archive.bundle_info()
    if info:
        console.print("[dim]" + T("nh.bundle", items=info["items"], symbols=info["symbols"],
                                  first=info["first"].date().isoformat(), last=info["last"].date().isoformat(),
                                  feeds=", ".join(f"{k} {v}" for k, v in sorted(info["feeds"].items()))) + "[/]")


@news_app.command("show")
def news_history_show(
    symbol: str,
    day: str = typer.Option(..., "--date", help="YYYY-MM-DD: show what a backtest sees at that day's close"),
) -> None:
    """Replay one day: the news a backtest sees for SYMBOL at the close. / 回放某一天回测能看到的新闻"""
    from .news.history import PointInTimeNews, decision_time
    from .news.relevance import company_names
    from .utils import DiskCache

    c = cfg()
    d = date.fromisoformat(day)
    cache = DiskCache(c.data.cache_path)
    pit = PointInTimeNews(_archive(), c.news, [symbol], names=lambda s: company_names(s, cache))
    cutoff = decision_time(d)
    items = pit.visible(symbol, cutoff)
    console.print(f"[bold]{T('nh.asof', day=d.isoformat())}: {symbol.upper()}[/] "
                  f"[dim](< {cutoff.strftime('%Y-%m-%d %H:%M')} UTC, {c.news.lookback_hours}h)[/]")
    if not items:
        console.print(T("nh.none"))
        return
    rep = pit.report(symbol, d)
    console.print(f"{T('news.score')} {rep.score:+.2f} · {rep.confirmed} {T('news.status.confirmed')} / "
                  f"{rep.unconfirmed} {T('news.status.unconfirmed')} / {rep.opinion} {T('news.status.opinion')}"
                  + (f" · [dim]{rep.off_topic} off-topic[/]" if rep.off_topic else "")
                  + (f" · [bold red]{T('news.flags')}[/] {rep.red_flags_confirmed}" if rep.red_flags_confirmed else ""))
    tbl = Table()
    tbl.add_column("UTC")
    tbl.add_column(T("nh.feeds"))
    tbl.add_column("Headline")
    for it in items[-40:]:
        tbl.add_row(it.published.strftime("%m-%d %H:%M"), it.publisher, it.title[:100])
    console.print(tbl)


@news_app.command("remove")
def news_history_remove(
    symbol: str,
    feed: Optional[str] = typer.Option(None, help="Only items from this feed (sec, benzinga, import)"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Delete a symbol's items from the local archive. / 删除某只股票的本地新闻"""
    if not yes and not Confirm.ask(f"Remove {symbol.upper()} {feed or 'all feeds'} from the archive?"):
        raise typer.Exit(1)
    n = _archive().remove(symbol, feed)
    console.print(f"removed {n}")


from .research.cli import app as research_app  # noqa: E402 - registered last, after the shared state above

app.add_typer(research_app, name="research")


def main() -> None:
    logging.captureWarnings(True)
    app()


if __name__ == "__main__":
    main()
