"""Self-contained HTML backtest reports (charts embedded as PNG) plus CSV exports."""

from __future__ import annotations

import base64
import html
import io
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from .. import i18n  # noqa: E402
from . import metrics as M  # noqa: E402
from .engine import BacktestResult  # noqa: E402

REGIME_COLORS = {"RISK_ON": "#e8f5e9", "NEUTRAL": "#ffffff", "UNCERTAIN": "#fff8e1", "RISK_OFF": "#fdecea"}
CJK_FONTS = ["PingFang SC", "Hiragino Sans GB", "Heiti SC", "STHeiti", "Songti SC", "Microsoft YaHei", "SimHei",
             "Noto Sans CJK SC", "Noto Sans SC", "Source Han Sans SC", "WenQuanYi Micro Hei", "Arial Unicode MS"]
_FONT_CACHE: dict[str, bool] = {}


def chart_lang(lang: str) -> str:
    """Use Chinese chart text only when a CJK font is installed; otherwise fall back to English."""
    if lang != "zh":
        return lang
    if "zh" not in _FONT_CACHE:
        from matplotlib import font_manager

        installed = {f.name for f in font_manager.fontManager.ttflist}
        found = [f for f in CJK_FONTS if f in installed]
        if found:
            matplotlib.rcParams["font.sans-serif"] = found + list(matplotlib.rcParams["font.sans-serif"])
            matplotlib.rcParams["font.family"] = "sans-serif"
            matplotlib.rcParams["axes.unicode_minus"] = False
        _FONT_CACHE["zh"] = bool(found)
    return "zh" if _FONT_CACHE["zh"] else "en"
PALETTE = ["#1f6feb", "#d1242f", "#2da44e", "#bf8700", "#8250df", "#57606a"]


def _png(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _shade_regimes(ax, regimes: pd.Series) -> None:
    if regimes is None or regimes.empty:
        return
    current, start = None, None
    for ts, r in regimes.items():
        if r != current:
            if current is not None and current != "NEUTRAL":
                ax.axvspan(start, ts, color=REGIME_COLORS.get(current, "#fff"), lw=0)
            current, start = r, ts
    if current is not None and current != "NEUTRAL":
        ax.axvspan(start, regimes.index[-1], color=REGIME_COLORS.get(current, "#fff"), lw=0)


def equity_chart(results: list[BacktestResult], lang: str = "en") -> str:
    fig, ax = plt.subplots(figsize=(11, 4.2))
    base = results[0]
    _shade_regimes(ax, base.regimes)
    ax.plot(base.benchmark.index, base.benchmark.values, color="#8c959f", lw=1.3, label="SPY")
    for i, r in enumerate(results):
        ax.plot(r.equity.index, r.equity.values, color=PALETTE[i % len(PALETTE)], lw=1.7, label=r.strategy)
    ax.set_title(i18n.t("bt.equity", lang) + "  (shading: green risk-on, yellow uncertain, red risk-off)"
                 if lang == "en" else i18n.t("bt.equity", lang) + "（底色：绿=偏多 黄=不确定 红=避险）", fontsize=11)
    ax.grid(alpha=0.25)
    ax.legend(loc="upper left", frameon=False)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"${v / 1000:,.0f}k"))
    return _png(fig)


def drawdown_chart(results: list[BacktestResult], lang: str = "en") -> str:
    fig, ax = plt.subplots(figsize=(11, 2.6))
    bdd = M.drawdown_series(results[0].benchmark)
    ax.fill_between(bdd.index, bdd.values * 100, 0, color="#8c959f", alpha=0.25, label="SPY")
    for i, r in enumerate(results):
        dd = M.drawdown_series(r.equity)
        ax.plot(dd.index, dd.values * 100, color=PALETTE[i % len(PALETTE)], lw=1.3, label=r.strategy)
    ax.set_ylabel("%")
    ax.set_title(i18n.t("bt.drawdown", lang), fontsize=11)
    ax.grid(alpha=0.25)
    ax.legend(loc="lower left", frameon=False)
    return _png(fig)


def exposure_chart(r: BacktestResult, lang: str = "en") -> str:
    fig, ax = plt.subplots(figsize=(11, 2.6))
    ax.plot(r.exposure.index, r.exposure.values * 100, color="#1f6feb", lw=1.2,
            label=i18n.t("account.stocks", lang))
    ax.plot(r.cash_ratio.index, r.cash_ratio.values * 100, color="#2da44e", lw=1.2,
            label=i18n.t("account.cash_total", lang))
    ax.axhline(30, color="#d1242f", lw=0.8, ls="--", label=i18n.t("account.floor", lang) + " 30%")
    ax.set_ylim(0, 100)
    ax.set_ylabel("%")
    ax.set_title(i18n.t("bt.exposure", lang), fontsize=11)
    ax.grid(alpha=0.25)
    ax.legend(loc="upper right", frameon=False, ncol=3)
    return _png(fig)


def _monthly_table(equity: pd.Series) -> str:
    table = M.monthly_returns(equity)
    head = "".join(f"<th>{c if c != 'Year' else 'Year'}</th>" for c in
                   ["", *[pd.Timestamp(2000, m, 1).strftime("%b") for m in range(1, 13)], "Year"])
    rows = []
    for year, row in table.iterrows():
        cells = []
        for m in [*range(1, 13), "Year"]:
            v = row.get(m)
            if v is None or pd.isna(v):
                cells.append("<td></td>")
                continue
            alpha = min(0.85, abs(v) * 6)
            color = f"rgba(45,164,78,{alpha:.2f})" if v >= 0 else f"rgba(209,36,47,{alpha:.2f})"
            weight = "font-weight:600;" if m == "Year" else ""
            cells.append(f'<td style="background:{color};{weight}">{v * 100:.1f}</td>')
        rows.append(f"<tr><th>{year}</th>{''.join(cells)}</tr>")
    return f'<table class="monthly"><tr>{head}</tr>{"".join(rows)}</table>'


def _metrics_table(results: list[BacktestResult], lang: str) -> str:
    keys = [k for k in M.METRIC_LABELS if any(k in r.metrics for r in results)]
    head = f"<th>{i18n.t('col.metric', lang)}</th>" + "".join(f"<th>{html.escape(r.strategy)}</th>" for r in results)
    head += f"<th>{i18n.t('col.benchmark', lang)}</th>"
    rows = []
    for k in keys:
        cells = "".join(f"<td>{M.fmt_metric(k, r.metrics.get(k))}</td>" for r in results)
        cells += f"<td>{M.fmt_metric(k, results[0].benchmark_metrics.get(k))}</td>"
        rows.append(f"<tr><th>{M.METRIC_LABELS[k][0]}</th>{cells}</tr>")
    return f"<table><tr>{head}</tr>{''.join(rows)}</table>"


def render_html(results: list[BacktestResult], lang: str = "en", title: str | None = None) -> str:
    r0 = results[0]
    title = title or f"{i18n.t('bt.title', lang)}: {', '.join(r.strategy for r in results)}"
    regimes = r0.regimes.value_counts(normalize=True)
    regime_rows = "".join(f"<tr><th>{i18n.regime(k, lang, short=True)}</th><td>{v * 100:.0f}%</td></tr>"
                          for k, v in regimes.items())
    rej = "".join(f"<tr><td>{html.escape(k)}</td><td>{v:,}</td></tr>" for k, v in list(r0.rejections.items())[:10])
    trades = r0.trades.tail(300).iloc[::-1]
    trade_rows = "".join(
        f"<tr><td>{pd.Timestamp(t.date).date()}</td><td>{t.symbol}</td><td>{t.side}</td><td>{t.qty:g}</td>"
        f"<td>{t.price:,.2f}</td><td>{t.value:,.0f}</td><td>{html.escape(i18n.tag(t.tag, lang))}</td>"
        f"<td>{'' if pd.isna(t.pnl) else f'{t.pnl:,.0f}'}</td><td class='reason'>{html.escape(str(t.reason))[:140]}</td></tr>"
        for t in trades.itertuples())
    notes = "".join(f"<li>{html.escape(n)}</li>" for n in r0.notes)
    css = """
    :root{--fg:#1f2328;--muted:#59636e;--bg:#ffffff;--card:#f6f8fa;--line:#d1d9e0}
    @media (prefers-color-scheme: dark){:root{--fg:#e6edf3;--muted:#9198a1;--bg:#0d1117;--card:#151b23;--line:#3d444d}}
    body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
         color:var(--fg);background:var(--bg);max-width:1180px;margin:24px auto;padding:0 16px}
    h1{font-size:22px;margin:0 0 4px} h2{font-size:17px;margin:28px 0 10px}
    .sub{color:var(--muted);margin-bottom:18px}
    table{border-collapse:collapse;font-size:13px;margin:6px 0 12px}
    th,td{border-bottom:1px solid var(--line);padding:5px 9px;text-align:right}
    th:first-child,td.reason{text-align:left}
    .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}
    .card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 14px}
    img{max-width:100%;border-radius:6px;background:#fff}
    .monthly td{min-width:38px}
    .trades{max-height:520px;overflow:auto;display:block}
    """
    cl = chart_lang(lang)
    charts = (f'<img src="{equity_chart(results, cl)}"><img src="{drawdown_chart(results, cl)}">'
              f'<img src="{exposure_chart(r0, cl)}">')
    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style>{css}</style></head><body>
<h1>{html.escape(title)}</h1>
<div class="sub">{i18n.t('bt.period', lang)}: {r0.start} → {r0.end} · {i18n.t('account.equity', lang)} ${r0.initial_cash:,.0f}
 · profile: {r0.profile} · {i18n.t('disclaimer', lang)}</div>
{_metrics_table(results, lang)}
{charts}
<h2>{i18n.t('bt.monthly', lang)} ({html.escape(r0.strategy)}, %)</h2>{_monthly_table(r0.equity)}
<div class="grid">
<div class="card"><h2>{i18n.t('bt.regimes', lang)}</h2><table>{regime_rows}</table></div>
<div class="card"><h2>{i18n.t('bt.rejections', lang)}</h2><table>{rej}</table></div>
<div class="card"><h2>{i18n.t('bt.notes', lang)}</h2><ul>{notes}</ul></div>
</div>
<h2>{i18n.t('bt.trades', lang)} ({html.escape(r0.strategy)})</h2>
<table class="trades"><tr><th>Date</th><th>Symbol</th><th>Side</th><th>Qty</th><th>Price</th><th>Value</th><th>Type</th>
<th>P&amp;L</th><th>Reason</th></tr>{trade_rows}</table>
</body></html>"""


def save_report(results: list[BacktestResult], out_dir: str | Path, lang: str = "en") -> Path:
    out = Path(out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    stamp = f"{results[0].start:%Y%m%d}-{results[0].end:%Y%m%d}"
    name = "-vs-".join(r.strategy for r in results)
    path = out / f"backtest-{name}-{stamp}.html"
    path.write_text(render_html(results, lang), encoding="utf-8")
    for r in results:
        r.trades.to_csv(out / f"trades-{r.strategy}-{stamp}.csv", index=False)
        pd.DataFrame({"equity": r.equity, "benchmark": r.benchmark, "cash_ratio": r.cash_ratio,
                      "stock_exposure": r.exposure, "regime": r.regimes}).to_csv(out / f"equity-{r.strategy}-{stamp}.csv")
    return path
