"""``riskaware research``: the frozen 2026 market from the command line."""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

app = typer.Typer(no_args_is_help=True, help=(
    "Experiments on the frozen 2026 market: point-in-time data, a look-ahead check and citable records. "
    "/ 冻结的 2026 年市场：按日期查询、防偷看检查、可引用的实验记录"))
console = Console()


def lang() -> str:
    """The language the main command was given (``--lang``), also under ``python -m riskaware.cli``."""
    import sys

    for name in ("riskaware.cli", "__main__"):
        st = getattr(sys.modules.get(name), "S", None)
        if st is not None and getattr(st, "lang_explicit", None):
            return st.lang
    from ..cli import S

    return S.lang


def L(en: str, zh: str) -> str:
    return zh if lang() == "zh" else en


WEEKDAYS_ZH = {"Monday": "周一", "Tuesday": "周二", "Wednesday": "周三", "Thursday": "周四", "Friday": "周五",
               "Saturday": "周六", "Sunday": "周日"}


def _pct(v: Any, sign: bool = True) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "n/a"
    if math.isnan(f) or math.isinf(f):
        return "n/a"
    return f"{f * 100:+.1f}%" if sign else f"{f * 100:.1f}%"


def _num(v: Any, nd: int = 2) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "n/a"
    return "n/a" if math.isnan(f) or math.isinf(f) else f"{f:.{nd}f}"


PERIOD_HELP = "Bundled period: 2026 (default), 2022 (rate-hike bear market) or 2020 (COVID crash)"
COSTS_HELP = ("fixed (default: the slippage on every fill at the open) or realistic (also part of the overnight gap "
              "and square-root market impact; each order at most 1% of the 20-day volume; docs/costs.md)")
MODE_HELP = ("long_only (default), long_short (shorts, no leverage) or margin (Reg T: shorts and up to 2x gross); "
             "docs/short-margin.md")
MODE_PARAMS_HELP = "Override the mode's rules as JSON, e.g. '{\"max_short\": 0.1, \"borrow_fee\": 0.01}'"


@app.command("data")
def data_cmd(period: Optional[str] = typer.Option(None, help="Show one period (default: all)")) -> None:
    """What the bundled data holds, and a check of its SHA-256. / 自带数据的内容和校验"""
    from .data import available_periods, manifest, verify
    from .periods import get_period

    for name in [get_period(period).name] if period else list(reversed(available_periods())):
        per, m = get_period(name), manifest(name)
        ok = verify(name)
        t = Table(title=L(f"{per.title} (version {m['version']})", f"{per.title_zh}（版本 {m['version']}）"))
        t.add_column(L("What", "项目"))
        t.add_column(L("Value", "内容"))
        t.add_row(L("Test window", "测试区间"), f"{per.start} – {per.end}  " + L(per.about, per.about_zh))
        t.add_row(L("Data", "数据"), f"{m['period']['first']} – {m['period']['last']} ({m['period']['trading_days']} "
                                     + L("trading days; the years before the test window warm up the indicators",
                                         "个交易日；测试区间之前的年份用来预热指标") + ")")
        for group, syms in m["symbols"].items():
            t.add_row(group, f"{len(syms)}: " + ", ".join(syms[:12]) + (" ..." if len(syms) > 12 else ""))
        for fname, meta in m["files"].items():
            t.add_row(fname, f"sha256 {(meta['sha256'] or '')[:16]}... "
                      + ("[green]OK[/]" if ok.get(fname) else "[red]changed[/]"))
        t.add_row(L("News", "新闻"), per.news_feeds)
        t.add_row(L("Source", "来源"), m["source"])
        console.print(t)


@app.command("day")
def day_cmd(
    day: str = typer.Argument(..., help="YYYY-MM-DD"),
    symbols: Optional[str] = typer.Option(None, help="Comma-separated stocks (default: all 64)"),
    headlines: int = typer.Option(3, help="Headlines per stock"),
    period: Optional[str] = typer.Option(None, help="Bundled period (default: the one that holds the day)"),
    as_json: bool = typer.Option(False, "--json", help="Print the full JSON"),
) -> None:
    """Everything known at one day's close: regime, VIX, indicators, setups, news. / 某天收盘时已知的全部信息"""
    from datetime import date as _date

    from .day import MarketView
    from .periods import get_period, period_of

    try:
        per = get_period(period).name if period else period_of(_date.fromisoformat(day)).name
    except ValueError as exc:
        console.print(f"[red]{escape(str(exc))}[/]")
        raise typer.Exit(2) from exc
    view = MarketView(period=per)
    syms = [s.strip().upper() for s in symbols.split(",")] if symbols else None
    try:
        snap = view.snapshot(day, symbols=syms, headlines=headlines)
    except (ValueError, KeyError) as exc:
        console.print(f"[red]{escape(str(exc))}[/]")
        raise typer.Exit(2) from exc
    if as_json:
        console.print_json(json.dumps(snap, default=str))
        return
    from .. import i18n

    lg = lang()
    m = snap["market"]
    wd = snap["weekday"] if lg != "zh" else WEEKDAYS_ZH.get(snap["weekday"], snap["weekday"])
    shown = snap["day"]
    if snap.get("note"):
        note_zh = f"{day} 不是交易日，显示的是 {shown}（{wd}）的收盘"
        console.print(f"[yellow]{L(escape(snap['note']), note_zh)}[/]")
    nxt = snap.get("next_trading_day")
    console.print(f"[bold]{shown} ({wd})[/]  " + L(
        f"decided after the close (16:00 New York); orders fill at the next open, {nxt}",
        f"收盘后（纽约 16:00）决策；订单在下一个交易日 {nxt} 开盘成交"))
    short, meaning = i18n.regime(m["regime"], lg, short=True), i18n.regime(m["regime"], lg)
    console.print(f"{L('Regime', '市场状态')}: [bold]{short}[/] ({escape(meaning)})")
    term = m["term_structure"] or "n/a"
    term_zh = {"contango": "正向", "flat": "平坦", "backwardation": "倒挂"}.get(term, term)
    console.print(f"VIX {m['vix']}  VIX9D {m['vix9d']}  VIX3M {m['vix3m']}  VVIX {m['vvix']}  "
                  f"{L('term structure', '期限结构')} {L(term, term_zh)}  "
                  f"{L('fear & greed', '恐慌贪婪')} {m['fear_greed']}  {L('breadth', '广度')} {m['breadth']}  "
                  f"SPY {m['spy_close']} ({_pct(m['spy_return_1d'])})")
    if m["upcoming_events"]:
        console.print(L("Scheduled: ", "日程：") + ", ".join(f"{i18n.event(e['kind'], lg)} {e['day']}"
                                                          for e in m["upcoming_events"]))
    t = Table()
    for col, zh in (("Stock", "股票"), ("Close", "收盘"), ("1d", "1日"), ("5d", "5日"), ("21d", "21日"),
                    ("RSI14", "RSI14"), ("vs EMA50", "距 EMA50"), ("vs EMA200", "距 EMA200"), ("Setups", "形态"),
                    ("News", "新闻分"), ("Flags", "风险信号")):
        t.add_column(L(col, zh), justify="right" if col not in ("Stock", "Setups", "Flags") else "left")
    for sym, st in snap["stocks"].items():
        n = st.get("news") or {}
        setups = sorted((st.get("technical") or {}).get("setups", {}))
        t.add_row(sym, str(st["close_as_traded"]), _pct(st["return_1d"]), _pct(st["return_5d"]), _pct(st["return_21d"]),
                  _num(st["indicators"].get("rsi14"), 0), f"{st['vs_ema_pct'].get('ema50', float('nan')):+.1f}%",
                  f"{st['vs_ema_pct'].get('ema200', float('nan')):+.1f}%",
                  ", ".join(i18n.setup(x, lg) for x in setups),
                  _num(n.get("score")), ", ".join((n.get("red_flags_confirmed") or []) +
                                                  (n.get("red_flags_single_source") or [])))
    console.print(t)
    if syms and headlines:
        for sym, st in snap["stocks"].items():
            for h in (st.get("news") or {}).get("top", []):
                console.print(f"  [dim]{sym} {h['first_seen']} {h['status'].lower()}[/] {escape(h['headline'])}")


def _print_summary(s: dict[str, Any]) -> None:
    if not s.get("ok", True):
        if s.get("problems"):
            console.print(f"[red]{L('The code was not run. Fix these lines:', '代码没有运行。请改正这些行：')}[/]")
            for pr in s["problems"]:
                console.print(f"  {L('line', '第')} {pr.get('line')}{L('', ' 行')}: {escape(str(pr.get('problem')))}")
        else:
            console.print(f"[red]{escape(str(s.get('error')))}[/]")
            for w in s.get("where") or []:
                console.print(f"  {L('line', '第')} {w.get('line')}{L('', ' 行')} ({escape(str(w.get('in')))})")
        return
    t = Table(title=f"{s['strategy']}  {s['period']}  ({s['stocks']} {L('stocks', '只股票')}, "
                    f"{L('news', '新闻')} {'on' if s['news'] else 'off'})")
    t.add_column("")
    t.add_column(L("Strategy", "策略"), justify="right")
    t.add_column("SPY", justify="right")
    t.add_column(L("Equal weight", "等权"), justify="right")
    m, spy, ew = s["metrics"], s["spy"], s["equal_weight"]
    t.add_row(L("Total return", "总收益"), _pct(m.get("total_return")), _pct(spy.get("total_return")),
              _pct(ew.get("total_return")))
    t.add_row("Sharpe", _num(m.get("sharpe")), _num(spy.get("sharpe")), _num(ew.get("sharpe")))
    t.add_row(L("Volatility", "波动率"), _pct(m.get("volatility"), sign=False), _pct(spy.get("volatility"), sign=False),
              "")
    t.add_row(L("Max drawdown", "最大回撤"), _pct(m.get("max_drawdown")), _pct(spy.get("max_drawdown")),
              _pct(ew.get("max_drawdown")))
    t.add_row(L("Closed trades", "已平仓交易"), _num(m.get("round_trips"), 0), "", "")
    t.add_row(L("Win rate", "胜率"), _pct(m.get("win_rate"), sign=False), "", "")
    t.add_row(L("Avg holding (days)", "平均持有天数"), _num(m.get("avg_holding_days"), 1), "", "")
    console.print(t)
    c = s.get("costs") or {}
    if c.get("model") == "realistic" and c.get("avg_bps") is not None:
        console.print(L(f"Realistic costs: {_num(c['avg_bps'], 1)} bps per fill at the open on average (highest "
                        f"{_num(c.get('max_bps'), 1)}), {c.get('volume_cap_cuts', 0)} orders cut by the volume cap",
                        f"真实成本：开盘成交平均每笔 {_num(c['avg_bps'], 1)} bps（最高 {_num(c.get('max_bps'), 1)}），"
                        f"{c.get('volume_cap_cuts', 0)} 笔订单被成交量上限砍掉一部分"))
    v = s.get("vs_spy") or {}
    if v:
        console.print(f"beta {_num(v.get('beta'))}, alpha {_pct(v.get('alpha_annual'))}/yr, t {_num(v.get('t_stat_excess'))}, "
                      f"Sharpe 95% [{_num(v.get('sharpe_ci95_low'))}, {_num(v.get('sharpe_ci95_high'))}], "
                      f"P(Sharpe > SPY) {_pct(v.get('p_sharpe_above_spy'), sign=False)}")
    for name, p in (s.get("periods") or {}).items():
        console.print(f"{name}: {p['from']} – {p['to']}  {_pct(p.get('total_return'))}  Sharpe {_num(p.get('sharpe'))}"
                      f"  (SPY {_pct(p.get('spy_total_return'))})")
    leak = s.get("leak_check")
    if leak:
        color = {"pass": "green", "fail": "red"}.get(leak["verdict"], "yellow")
        console.print(f"{L('Look-ahead check', '防偷看检查')}: [{color}]{leak['verdict'].upper()}[/]  "
                      + "  ".join(f"{c['cut']}: {'same' if c['same'] else 'DIFFERENT'}" for c in leak.get("cuts", [])))
    if s.get("folder"):
        console.print(f"{L('Record', '实验记录')}: {s['folder']}", soft_wrap=True)


@app.command("run")
def run_cmd(
    strategy: str = typer.Option("swing_rsi2", "--strategy", "-s", help="Built-in name or path/to/file.py[:Class]"),
    code: Optional[Path] = typer.Option(None, help="A strategy file to check and run like AI-written code (strategy lab)"),
    params: Optional[str] = typer.Option(None, help="Strategy parameters as JSON"),
    period: str = typer.Option("2026", help=PERIOD_HELP),
    start: Optional[str] = typer.Option(None, help="YYYY-MM-DD (default: the first day of the period)"),
    end: Optional[str] = typer.Option(None, help="YYYY-MM-DD (default: the period's last day)"),
    universe: str = typer.Option("megacap", help="megacap, tech, defensive, core, sectors, etf"),
    symbols: Optional[str] = typer.Option(None, help="Comma-separated symbols instead of a universe"),
    news: bool = typer.Option(True, "--news/--no-news"),
    execution: str = typer.Option("plain", help="plain (the strategy's own rules) or risk_aware"),
    mode: str = typer.Option("long_only", help=MODE_HELP),
    mode_params: Optional[str] = typer.Option(None, help=MODE_PARAMS_HELP),
    profile: str = typer.Option("balanced", help="Risk profile for --execution risk_aware"),
    max_positions: int = typer.Option(10, help="Plain execution: most positions at once"),
    position_size: Optional[float] = typer.Option(None, help="Plain execution: share of equity per position"),
    split: Optional[str] = typer.Option(None, help="First day of the out-of-sample part, e.g. 2026-07-01"),
    leak_check: bool = typer.Option(True, "--leak-check/--no-leak-check"),
    slippage_bps: float = typer.Option(5.0),
    costs: str = typer.Option("fixed", help=COSTS_HELP),
    label: Optional[str] = typer.Option(None),
    out: Optional[Path] = typer.Option(None, help="Record folder (default ~/.riskaware/research/runs/<id>)"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Run a strategy on a frozen period and save a record. / 在冻结的某个时段上做一次实验"""
    from .experiment import COST_MODELS, Experiment, run_experiment
    from .periods import get_period

    syms = [s.strip().upper() for s in symbols.split(",")] if symbols else None
    p = json.loads(params) if params else {}
    try:
        per = get_period(period)
        if costs not in COST_MODELS:
            raise ValueError(f"--costs must be one of {', '.join(COST_MODELS)}")
    except ValueError as exc:
        console.print(f"[red]{escape(str(exc))}[/]")
        raise typer.Exit(2) from exc
    settings = dict(params=p, period=per.name, start=start, end=end, universe=None if syms else universe, symbols=syms,
                    news=news,
                    execution=execution, profile=profile, max_positions=max_positions, position_size=position_size,
                    split=split, leak_check=leak_check, slippage_bps=slippage_bps, label=label, costs=costs)
    if mode != "long_only":
        settings.update(mode=mode, mode_params=json.loads(mode_params) if mode_params else None)
    t0 = time.time()
    if code is not None:
        from ..lab import backtest_code

        settings["strategy"] = "code"
        with console.status(L("Checking and running the code ...", "检查并运行代码 ...")):
            s = backtest_code(code.read_text(encoding="utf-8"), symbols=syms, universe=None if syms else universe,
                              start=start or per.start.isoformat(), end=end, news=news, research=settings,
                              timeout=900.0)
    else:
        try:
            with console.status(L("Running ...", "运行中 ...")):
                res = run_experiment(Experiment(strategy=strategy, **settings), out_dir=out)
        except (ValueError, KeyError, FileNotFoundError) as exc:
            console.print(f"[red]{escape(str(exc))}[/]")
            raise typer.Exit(2) from exc
        s = {"ok": True, **res.summary()}
    if as_json:
        console.print_json(json.dumps(s, default=str))
        return
    _print_summary(s)
    console.print(f"[dim]{time.time() - t0:.1f} s[/]")


@app.command("baselines")
def baselines_cmd(
    period: str = typer.Option("2026", help=PERIOD_HELP),
    start: Optional[str] = typer.Option(None),
    end: Optional[str] = typer.Option(None),
    universe: str = typer.Option("megacap"),
    max_positions: int = typer.Option(10),
    split: Optional[str] = typer.Option(None, help="Out-of-sample start (default: 1 July of the period's year)"),
    leak_check: bool = typer.Option(True, "--leak-check/--no-leak-check"),
    costs: str = typer.Option("fixed", help=COSTS_HELP),
    out: Optional[Path] = typer.Option(None),
) -> None:
    """The five swing strategies, with and without news, side by side. / 五种波段策略，开和不开新闻对比"""
    from .compare import run_baselines
    from .experiment import COST_MODELS
    from .periods import get_period

    per = get_period(period)
    if costs not in COST_MODELS:
        console.print(f"[red]--costs must be one of {', '.join(COST_MODELS)}[/]")
        raise typer.Exit(2)
    with console.status("...") as st:
        table, folder = run_baselines(start=start, end=end, universe=universe, max_positions=max_positions,
                                      split=split or per.split.isoformat(), leak_check=leak_check, out_dir=out,
                                      progress=lambda msg: st.update(msg), period=per.name, costs=costs)
    console.print((folder / "baselines.md").read_text(encoding="utf-8"))
    console.print(f"{L('Folder', '文件夹')}: {folder}", soft_wrap=True)


@app.command("runs")
def runs_cmd(limit: int = typer.Option(20)) -> None:
    """Saved experiments, newest first. / 已保存的实验"""
    from .experiment import list_runs

    t = Table()
    for col in ("Run", "Strategy", "Period", "News", "Return", "Sharpe", "SPY", "Leak check"):
        t.add_column(col)
    for r in list_runs(limit):
        t.add_row(r["run_id"], str(r["strategy"]), r["period"], str(r["news"]), _pct(r["total_return"]),
                  _num(r["sharpe"]), _pct(r["spy_total_return"]), str(r["leak_check"]))
    console.print(t)


@app.command("report")
def report_cmd(run_id: str) -> None:
    """Print the report of a saved experiment. / 打印一次实验的报告"""
    from .experiment import load_report

    try:
        console.print(load_report(run_id)["report"])
    except FileNotFoundError as exc:
        console.print(f"[red]{escape(str(exc))}[/]")
        raise typer.Exit(2) from exc


@app.command("analyze")
def analyze_cmd(
    run: str = typer.Argument(..., help="Run id, record folder, trading session id, or a folder of runs (baselines)"),
    as_json: bool = typer.Option(False, "--json", help="Print the full JSON"),
    chart: bool = typer.Option(True, "--chart/--no-chart", help="Draw analysis.png"),
) -> None:
    """What a run made its money on: decisions, factors, P&L. / 一次实验靠什么赚钱：决策、因子、盈亏拆解"""
    from .analysis import analyze_many, analyze_run, is_record, print_analysis, resolve

    try:
        folder = resolve(run)
        if is_record(folder):
            with console.status(L("Analyzing ...", "分析中 ...")):
                res = analyze_run(folder, chart=chart)
        else:
            with console.status("...") as st:
                _, out = analyze_many(folder, chart=chart, progress=lambda name: st.update(name))
            console.print((out / "analysis.md").read_text(encoding="utf-8"))
            return
    except (FileNotFoundError, ValueError, KeyError) as exc:
        console.print(f"[red]{escape(str(exc))}[/]")
        raise typer.Exit(2) from exc
    if as_json:
        from .analysis import jsonable

        console.print_json(json.dumps(jsonable(res)))
        return
    print_analysis(res, console, L)


@app.command("trade")
def trade_cmd(
    llm: str = typer.Option(..., help="provider:model, e.g. ollama:qwen2.5:3b, openai:gpt-5.6-luna, claude-cli"),
    universe: str = typer.Option("core"),
    symbols: Optional[str] = typer.Option(None),
    period: str = typer.Option("2026", help=PERIOD_HELP),
    start: Optional[str] = typer.Option(None),
    end: Optional[str] = typer.Option(None),
    decide_every: int = typer.Option(5, help="Trading days between decisions (5 = weekly)"),
    blind: bool = typer.Option(False, help="Hide tickers, company names and dates from the model"),
    news: bool = typer.Option(True, "--news/--no-news"),
    max_positions: int = typer.Option(5),
    base_url: Optional[str] = typer.Option(None, help="For compat:model, the server's URL"),
    mode: str = typer.Option("long_only", help=MODE_HELP),
) -> None:
    """Let an LLM trade the period step by step and save the record. / 让 LLM 逐步交易这段时间并保存记录"""
    from .agent import llm_policy, run_policy
    from .experiment import Experiment

    syms = [s.strip().upper() for s in symbols.split(",")] if symbols else None
    steps = {"n": 0}

    def show(obs: dict, reply: dict | None) -> None:
        steps["n"] += 1
        orders = (reply or {}).get("orders") or []
        text = ", ".join(f"{o.get('action')} {o.get('symbol')}" for o in orders) or L("hold", "持有")
        console.print(f"[dim]{obs['day']}[/] {escape(text)}")

    policy = llm_policy(llm, base_url=base_url, on_reply=show, mode=mode)
    exp = Experiment(period=period, start=start, end=end, universe=None if syms else universe, symbols=syms,
                     news=news, max_positions=max_positions, label=f"llm-{llm.split(':')[0]}", mode=mode)
    res = run_policy(policy, exp, decide_every=decide_every, blind=blind, label=f"llm:{llm}")
    _print_summary({"ok": True, **res.summary()})


@app.command("factors")
def factors_cmd(
    period: Optional[str] = typer.Option(None, help="One period (2026, 2022 or 2020); default: all three"),
    llm: bool = typer.Option(False, "--llm", help="Add an LLM sentiment factor (scores cached in "
                                                  "~/.riskaware/research/headline_scores)"),
    llm_model: str = typer.Option("ollama:llama3.2:3b", help="provider:model for --llm; local Ollama costs nothing"),
    llm_limit: Optional[int] = typer.Option(None, help="Score at most this many new headlines in this run "
                                                       "(0: use cached scores only)"),
    llm_export: Optional[Path] = typer.Option(None, help="Write batch requests (JSON Lines) for the headlines "
                                                         "without a score, for a batch API or queue, and stop"),
    llm_import: Optional[list[Path]] = typer.Option(None, help="Cache the scores from a batch run's results "
                                                               "(JSON Lines with text and meta) and use them; "
                                                               "repeatable; implies --llm --llm-limit 0"),
    llm_batch: int = typer.Option(40, help="Headlines per batch request with --llm-export"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Score uncached headlines without asking"),
    charts: Optional[str] = typer.Option(None, help="Chart and report languages, e.g. en,zh (default: --lang)"),
    out: Optional[Path] = typer.Option(None, help="Folder (default ~/.riskaware/research/factors/<time>)"),
    as_json: bool = typer.Option(False, "--json", help="Print factors.json"),
) -> None:
    """News factors: IC, quintiles, decay, price controls, and calendar-date against publication-time joins.
    / 新闻因子：IC、分组、衰减、价格因子对照，以及按日历日期和按发布时间对齐的对比"""
    from . import news_factors as nf
    from .periods import get_period, period_names

    try:
        periods = [get_period(period).name] if period else sorted(period_names(), reverse=True)
    except ValueError as exc:
        console.print(f"[red]{escape(str(exc))}[/]")
        raise typer.Exit(2) from exc
    scores = None
    llm_info = None
    if llm_import:  # scores from a batch run: use them, and call no model unless --llm-limit says so
        llm = True
        llm_limit = 0 if llm_limit is None else llm_limit
    if llm or llm_export:
        try:
            scorer = nf.HeadlineScorer(llm_model)
        except ValueError as exc:
            console.print(f"[red]{escape(str(exc))}[/]")
            raise typer.Exit(2) from exc
        pairs = nf.headlines_to_score(periods)
        for path in llm_import or []:
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            c = scorer.import_results(rows, pairs)
            console.print(L(f"{path.name}: {c['scored']:,} scores cached from {c['rows']:,} replies "
                            f"({c['skipped']:,} replies skipped: wrong number of scores or another prompt version)",
                            f"{path.name}：从 {c['rows']:,} 条回复缓存了 {c['scored']:,} 个分数（跳过 {c['skipped']:,} 条："
                            f"分数个数不对或提示词版本不同）"))
        if llm_export:
            reqs = scorer.requests(pairs, batch=llm_batch)
            llm_export.parent.mkdir(parents=True, exist_ok=True)
            llm_export.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in reqs), encoding="utf-8")
            est = nf.HeadlineScorer(llm_model, cache_dir=scorer.dir, batch=llm_batch).estimate(scorer.missing(pairs))
            console.print(L(f"{len(reqs):,} requests for {len(scorer.missing(pairs)):,} headlines in {llm_export}: "
                            f"about {est['input_tokens']:,} input and {est['output_tokens']:,} output tokens. Run "
                            f"them, then --llm-import the results.",
                            f"{llm_export} 里有 {len(reqs):,} 个请求、{len(scorer.missing(pairs)):,} 条标题：约 "
                            f"{est['input_tokens']:,} 个输入 token、{est['output_tokens']:,} 个输出 token。跑完后用 "
                            f"--llm-import 导入结果。"), soft_wrap=True)
            return
    if llm:
        todo = scorer.missing(pairs)[: llm_limit if llm_limit is not None else None]
        if todo:
            est = scorer.estimate(todo)
            cost = L("free (runs on this computer)", "免费（在本机运行）") if scorer.local else L(
                "check these tokens against the provider's price list", "请按服务商的价格表核对这些 token")
            console.print(L(f"{len(todo)} of {len(pairs)} headlines are not scored yet with {llm_model}: "
                            f"{est['calls']} calls, about {est['input_tokens']:,} input and {est['output_tokens']:,} "
                            f"output tokens; {cost}.",
                            f"{len(pairs)} 条标题里有 {len(todo)} 条还没用 {llm_model} 打分：{est['calls']} 次调用，"
                            f"约 {est['input_tokens']:,} 个输入 token、{est['output_tokens']:,} 个输出 token；{cost}。"))
            go = yes or (scorer.local and len(todo) <= 2000) or typer.confirm(L("Score them now?", "现在打分吗？"),
                                                                              default=scorer.local)
            if go:
                try:
                    with console.status(L("Scoring headlines ...", "正在给标题打分 ...")) as st:
                        scorer.score(todo, progress=lambda d, n: st.update(f"{d}/{n}"))
                except nf.LLMUnavailable as exc:
                    console.print(f"[yellow]{escape(str(exc))}[/] " + L(
                        "The scores so far are cached; run the command again to go on.",
                        "已打的分已缓存；再运行一次这个命令就接着打。"))
        scores = scorer.scores
        have = sum(1 for s, h in pairs if nf.headline_key(s, h) in scores)
        llm_info = {"model": llm_model, "scored": have, "headlines": len(pairs),
                    "coverage": have / len(pairs) if pairs else 0.0, "prompt_version": nf.PROMPT_VERSION,
                    "cache": str(scorer.path)}
        if not have:
            console.print(L("[yellow]No headline has an LLM score yet; the LLM factor is left out.[/]",
                            "[yellow]还没有标题有 LLM 分数，这次不算 LLM 因子。[/]"))
            scores = None
    t0 = time.time()
    with console.status(L("Computing features and tests ...", "正在计算特征和检验 ...")) as st:
        study = nf.run_study(periods, llm_scores=scores, progress=lambda m: st.update(m))
    study.llm = llm_info
    langs = [x.strip() for x in charts.split(",") if x.strip()] if charts else [lang()]
    with console.status(L("Writing the report and charts ...", "正在写报告和图 ...")):
        folder = nf.save_study(study, out, langs=langs)
    if as_json:
        console.print_json((folder / "factors.json").read_text(encoding="utf-8"))
        return
    _print_factors(study)
    console.print(f"{L('Folder', '文件夹')}: {folder}  [dim]({time.time() - t0:.0f} s)[/]", soft_wrap=True)


def _print_factors(study: Any) -> None:
    from . import news_factors as nf

    zh = lang() == "zh"
    t = Table(title=L("News factors (rank IC from the next open; t-statistics are Newey-West)",
                      "新闻因子（从次日开盘算的秩 IC；t 值用 Newey-West）"))
    for col, z in (("Factor", "因子"), ("Verdict", "结论"), ("IC 1d (t)", "IC 1天（t）"), ("IC 5d (t)", "IC 5天（t）"),
                   ("Calendar-date join, 1d", "按日历日期对齐 1天"), ("Inflation (t)", "虚增（t）")):
        t.add_column(L(col, z), justify="left" if col in ("Factor", "Verdict") else "right")
    color = {"useful": "green", "weak": "yellow", "noise": "dim"}
    for name in [*nf.NEWS_FACTORS, *nf.PRICE_FACTORS]:
        if name not in study.pooled:
            continue
        p = study.pooled[name]
        v = study.verdicts.get(name, {}).get("verdict", "")
        al = (study.pooled_alignment.get(name) or {}).get("h1") or {}
        label = nf.ALL_FACTORS[name].label_zh if zh else nf.ALL_FACTORS[name].label
        shown = nf.TEXT["zh" if zh else "en"].get(v, "") if v else L("price", "价格")
        t.add_row(label, f"[{color.get(v, 'white')}]{shown}[/]", nf._ic(p.get("h1")), nf._ic(p.get("h5")),
                  nf._ic(al.get("calendar_close")), nf._ic(al.get("leak")))
    console.print(t)
