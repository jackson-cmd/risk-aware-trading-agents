"""Run the five swing strategies with and without news on the same settings and put them side by side.

``riskaware research baselines`` writes a folder with each run's record, ``baselines.csv``, ``baselines.md`` (the
table) and ``baselines.png`` (equity curves with news, and returns with and without news).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .experiment import COST_MODELS, RUNS_DIR, Experiment, ExperimentResult, run_experiment
from .strategies import RESEARCH_STRATEGIES

COLUMNS = ["strategy", "news", "total_return", "cagr", "volatility", "sharpe", "sharpe_ci95_low", "sharpe_ci95_high",
           "max_drawdown", "calmar", "round_trips", "win_rate", "avg_holding_days", "avg_exposure", "beta",
           "alpha_annual", "t_stat_excess", "p_sharpe_above_spy", "leak_check", "run_id"]
COST_COLUMNS = ["avg_cost_bps", "volume_cap_cuts"]  # added to the table for realistic costs


def _row(r: ExperimentResult, news: bool) -> dict[str, Any]:
    m, s = r.metrics, r.stats
    row = {"strategy": r.strategy, "news": news, **{k: m.get(k) for k in ("total_return", "cagr", "volatility",
                                                                           "sharpe", "max_drawdown", "calmar",
                                                                           "round_trips", "win_rate",
                                                                           "avg_holding_days", "avg_exposure")},
           **{k: s.get(k) for k in ("sharpe_ci95_low", "sharpe_ci95_high", "beta", "alpha_annual", "t_stat_excess",
                                    "p_sharpe_above_spy")},
           "leak_check": (r.leak or {}).get("verdict"), "run_id": r.run_id}
    if r.experiment.costs != "fixed":
        c = r.backtest.costs if r.backtest is not None else {}
        row.update(avg_cost_bps=c.get("avg_bps"), volume_cap_cuts=c.get("volume_cap_cuts"))
    return row


def run_baselines(start: str | None = None, end: str | None = None, universe: str = "megacap",
                  max_positions: int = 10, strategies: list[str] | None = None, leak_check: bool = True,
                  out_dir: str | Path | None = None, split: str | None = None,
                  progress: Callable[[str], None] | None = None,
                  period: str | None = None, costs: str = "fixed") -> tuple[pd.DataFrame, Path]:
    from .periods import get_period

    per = get_period(period).name
    if costs not in COST_MODELS:
        raise ValueError(f"costs must be one of {', '.join(COST_MODELS)}, not {costs!r}")
    names = strategies or list(RESEARCH_STRATEGIES)
    suffix = "" if costs == "fixed" else f"-{costs}"
    folder = (Path(out_dir).expanduser() if out_dir
              else RUNS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-baselines-{per}{suffix}")
    folder.mkdir(parents=True, exist_ok=True)
    rows, curves = [], {}
    last: ExperimentResult | None = None
    for name in names:
        for news in (False, True):
            if progress:
                progress(f"{name} {'with' if news else 'without'} news")
            exp = Experiment(strategy=name, params={"news": news}, period=per, start=start, end=end, universe=universe,
                             news=news, max_positions=max_positions, leak_check=leak_check, split=split,
                             label=name, costs=costs)
            r = run_experiment(exp, out_dir=folder / f"{name}-{'news' if news else 'nonews'}")
            rows.append(_row(r, news))
            curves[(name, news)] = r.equity["equity"]
            last = r
    columns = COLUMNS + (COST_COLUMNS if costs != "fixed" else [])
    table = pd.DataFrame(rows, columns=columns)
    if last is not None:
        bench = pd.DataFrame([
            {"strategy": "SPY (buy and hold)", "news": None, **{k: last.spy.get(k) for k in COLUMNS if k in last.spy}},
            {"strategy": "Equal weight (buy and hold)", "news": None,
             **{k: last.equal_weight.get(k) for k in COLUMNS if k in last.equal_weight}}], columns=columns)
        table = pd.concat([table, bench], ignore_index=True)
        curves[("SPY", None)] = last.equity["spy"]
        curves[("Equal weight", None)] = last.equity["equal_weight"]
    table.to_csv(folder / "baselines.csv", index=False, float_format="%.6f")
    (folder / "baselines.md").write_text(markdown_table(table, last), encoding="utf-8")
    try:
        draw(curves, table, folder / "baselines.png")
    except Exception:  # noqa: BLE001 - the chart is a nicety
        pass
    return table, folder


def _p(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return ""
    return "" if math.isnan(f) else f"{f * 100:+.1f}%"


def _n(v: Any, nd: int = 2) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return ""
    return "" if math.isnan(f) else f"{f:.{nd}f}"


def markdown_table(t: pd.DataFrame, last: ExperimentResult | None) -> str:
    costs = "avg_cost_bps" in t.columns
    head = "| Strategy | News | Return | Sharpe (95% interval) | Max drawdown | Trades | Win rate | Avg hold (days) | Leak check |"
    lines = [head + (" Cost (bps) |" if costs else ""), "|---|---|---:|---:|---:|---:|---:|---:|---|"
             + ("---:|" if costs else "")]
    for _, r in t.iterrows():
        news = "" if r["news"] is None or (isinstance(r["news"], float) and math.isnan(r["news"])) else (
            "with" if r["news"] else "without")
        ci = f" ({_n(r['sharpe_ci95_low'])} to {_n(r['sharpe_ci95_high'])})" if _n(r["sharpe_ci95_low"]) else ""
        lines.append(f"| {r['strategy']} | {news} | {_p(r['total_return'])} | {_n(r['sharpe'])}{ci} | "
                     f"{_p(r['max_drawdown'])} | {_n(r['round_trips'], 0)} | {_p(r['win_rate']).lstrip('+')} | "
                     f"{_n(r['avg_holding_days'], 1)} | {r['leak_check'] if isinstance(r['leak_check'], str) else ''} |"
                     + (f" {_n(r['avg_cost_bps'], 1)} |" if costs else ""))
    if last is not None:
        e = last.experiment
        rule = (last.backtest.costs if last.backtest is not None else {}).get("rule")
        fills = (f"{e.slippage_bps:g} bps slippage" if e.costs == "fixed" else
                 f"realistic costs ({rule})" if rule else "realistic costs")
        lines += ["", f"{last.start} to {last.end}, {len(last.symbols)} stocks ({e.universe}), plain execution: at "
                      f"most {e.max_positions} positions of equal size, orders at the next open, {fills}, idle cash in "
                      "T-bills. Sharpe intervals: 2,000 block-bootstrap resamples."
                      + (" Cost: the average a fill at the open paid, in basis points." if costs else "")]
    return "\n".join(lines) + "\n"


def draw(curves: dict[tuple, pd.Series], table: pd.DataFrame, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    colors = ["#1f6feb", "#cf222e", "#1a7f37", "#8250df", "#bf8700"]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.6), gridspec_kw={"width_ratios": [1.6, 1]})
    names = [n for (n, news) in curves if news is True]
    for i, n in enumerate(names):
        s = curves[(n, True)].dropna()
        a1.plot(s.index, (s / s.iloc[0] - 1) * 100, color=colors[i % len(colors)], linewidth=1.6, label=n)
    for key, color, style in ((("SPY", None), "#24292f", "--"), (("Equal weight", None), "#8c959f", ":")):
        if key in curves:
            s = curves[key].dropna()
            a1.plot(s.index, (s / s.iloc[0] - 1) * 100, color=color, linewidth=1.4, linestyle=style, label=key[0])
    a1.axhline(0, color="#d0d7de", linewidth=0.8)
    a1.set_ylabel("Return (%)")
    a1.set_title("With news", loc="left", fontsize=11)
    a1.legend(frameon=False, fontsize=8, ncol=2, loc="upper left")
    loc = mdates.AutoDateLocator()
    a1.xaxis.set_major_locator(loc)
    a1.xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc))
    t = table[table["news"].isin([True, False])]
    xs = range(len(names))
    w = 0.38
    off = [float(t[(t["strategy"] == n) & (t["news"] == False)]["total_return"].iloc[0]) * 100 for n in names]  # noqa: E712
    on = [float(t[(t["strategy"] == n) & (t["news"] == True)]["total_return"].iloc[0]) * 100 for n in names]  # noqa: E712
    a2.bar([x - w / 2 for x in xs], off, w, color="#8c959f", label="without news")
    a2.bar([x + w / 2 for x in xs], on, w, color="#1f6feb", label="with news")
    a2.axhline(0, color="#57606a", linewidth=0.8)
    spy = table[table["strategy"].str.startswith("SPY")]["total_return"]
    if len(spy):
        a2.axhline(float(spy.iloc[0]) * 100, color="#24292f", linestyle="--", linewidth=1.0, label="SPY")
    a2.set_xticks(list(xs))
    a2.set_xticklabels([n.replace("swing_", "") for n in names], fontsize=9)
    a2.set_ylabel("Total return (%)")
    a2.set_title("Without and with news", loc="left", fontsize=11)
    a2.legend(frameon=False, fontsize=8)
    for a in (a1, a2):
        a.spines[["top", "right"]].set_visible(False)
        a.grid(axis="y", color="#eaeef2", linewidth=0.6)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
