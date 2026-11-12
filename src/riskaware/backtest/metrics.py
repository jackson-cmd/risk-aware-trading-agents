"""Performance metrics for equity curves and trade lists."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def max_drawdown(equity: pd.Series) -> tuple[float, pd.Timestamp | None, pd.Timestamp | None]:
    if equity.empty:
        return 0.0, None, None
    peak = equity.cummax()
    dd = equity / peak - 1.0
    trough = dd.idxmin()
    peak_day = equity.loc[:trough].idxmax()
    return float(dd.min()), peak_day, trough


def drawdown_series(equity: pd.Series) -> pd.Series:
    return equity / equity.cummax() - 1.0


def summarize(equity: pd.Series, rf_daily: pd.Series | None = None, trades: pd.DataFrame | None = None,
              exposure: pd.Series | None = None, cash_ratio: pd.Series | None = None) -> dict[str, float]:
    equity = equity.dropna()
    if len(equity) < 2:
        return {}
    rets = equity.pct_change().dropna()
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1e-9)
    total = equity.iloc[-1] / equity.iloc[0] - 1.0
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1.0 if equity.iloc[0] > 0 else math.nan
    vol = rets.std(ddof=0) * math.sqrt(TRADING_DAYS)
    rf = rf_daily.reindex(rets.index).fillna(0.0) if rf_daily is not None else pd.Series(0.0, index=rets.index)
    excess = rets - rf
    sharpe = excess.mean() / rets.std(ddof=0) * math.sqrt(TRADING_DAYS) if rets.std(ddof=0) > 0 else math.nan
    downside = rets[rets < 0].std(ddof=0)
    sortino = excess.mean() / downside * math.sqrt(TRADING_DAYS) if downside and downside > 0 else math.nan
    mdd, _, _ = max_drawdown(equity)
    out = {
        "total_return": total,
        "cagr": cagr,
        "volatility": vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": mdd,
        "calmar": cagr / abs(mdd) if mdd < 0 else math.nan,
        "best_day": float(rets.max()),
        "worst_day": float(rets.min()),
        "years": years,
    }
    yearly = equity.resample("YE").last()
    first = pd.Series([equity.iloc[0]], index=[equity.index[0]])
    yr = pd.concat([first, yearly]).pct_change().dropna()
    if len(yr):
        out["best_year"] = float(yr.max())
        out["worst_year"] = float(yr.min())
    if exposure is not None and len(exposure):
        out["avg_exposure"] = float(exposure.mean())
    if cash_ratio is not None and len(cash_ratio):
        out["min_cash_ratio"] = float(cash_ratio.min())
        out["avg_cash_ratio"] = float(cash_ratio.mean())
    if trades is not None and not trades.empty:
        sells = trades[trades["pnl"].notna() & ~trades["tag"].isin(["treasury_rebalance"])]
        out["trades"] = float(len(trades))
        out["round_trips"] = float(len(sells))
        if len(sells):
            wins = sells[sells["pnl"] > 0]
            losses = sells[sells["pnl"] <= 0]
            out["win_rate"] = len(wins) / len(sells)
            gross_win = wins["pnl"].sum()
            gross_loss = -losses["pnl"].sum()
            out["profit_factor"] = gross_win / gross_loss if gross_loss > 0 else math.inf
            held = sells.dropna(subset=["holding_days"])
            w = held["value"].abs()
            if len(held):
                out["avg_holding_days"] = float(np.average(held["holding_days"], weights=w) if w.sum() > 0
                                                else held["holding_days"].mean())
        traded = trades["value"].abs().sum()
        out["turnover"] = float(traded / equity.mean() / years / 2)
    return out


def monthly_returns(equity: pd.Series) -> pd.DataFrame:
    month_end = equity.resample("ME").last()
    first = pd.Series([equity.iloc[0]], index=[equity.index[0] - pd.Timedelta(days=1)])
    rets = pd.concat([first, month_end]).pct_change().dropna()
    table = pd.DataFrame({"year": rets.index.year, "month": rets.index.month, "ret": rets.values})
    pivot = table.pivot(index="year", columns="month", values="ret")
    yearly = equity.resample("YE").last()
    yfirst = pd.Series([equity.iloc[0]], index=[equity.index[0] - pd.Timedelta(days=1)])
    yr = pd.concat([yfirst, yearly]).pct_change().dropna()
    pivot["Year"] = pd.Series(yr.values, index=yr.index.year)
    return pivot


METRIC_LABELS = {
    "total_return": ("Total return", "pct"),
    "cagr": ("CAGR", "pct"),
    "volatility": ("Volatility", "pct"),
    "sharpe": ("Sharpe", "num"),
    "sortino": ("Sortino", "num"),
    "max_drawdown": ("Max drawdown", "pct"),
    "calmar": ("Calmar", "num"),
    "best_year": ("Best year", "pct"),
    "worst_year": ("Worst year", "pct"),
    "avg_exposure": ("Avg stock exposure", "pct"),
    "avg_cash_ratio": ("Avg cash + T-bills", "pct"),
    "min_cash_ratio": ("Min cash + T-bills", "pct"),
    "win_rate": ("Win rate (closed)", "pct"),
    "profit_factor": ("Profit factor", "num"),
    "avg_holding_days": ("Avg holding (days)", "int"),
    "round_trips": ("Closed trades", "int"),
    "trades": ("Orders filled", "int"),
    "turnover": ("Turnover / year", "pct"),
}


def fmt_metric(key: str, value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    kind = METRIC_LABELS.get(key, (key, "num"))[1]
    if kind == "pct":
        return f"{value * 100:.1f}%"
    if kind == "int":
        return f"{value:,.0f}"
    if math.isinf(value):
        return "inf"
    return f"{value:.2f}"
