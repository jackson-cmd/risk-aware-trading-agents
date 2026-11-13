"""Statistics an experiment reports next to the usual metrics: risk-adjusted excess return over SPY, a t-statistic,
and bootstrap confidence intervals.

Ten months of daily returns are a short sample. The intervals say how much of a difference between two strategies
could be luck: a moving-block bootstrap (blocks of 5 trading days, so a week of correlated days stays together)
resamples the paired daily returns of the strategy and SPY 2,000 times with a fixed seed.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def _sharpe(r: np.ndarray, rf: np.ndarray) -> float:
    sd = r.std()
    return float((r - rf).mean() / sd * math.sqrt(TRADING_DAYS)) if sd > 0 else math.nan


def excess_stats(equity: pd.Series, bench: pd.Series, rf_daily: pd.Series | None = None,
                 n_boot: int = 2000, block: int = 5, seed: int = 0) -> dict[str, float]:
    """Beta, alpha, information ratio, t-statistic and bootstrap intervals of a strategy against a benchmark."""
    eq = equity.dropna()
    b = bench.reindex(eq.index).ffill()
    r = eq.pct_change().dropna()
    br = b.pct_change().reindex(r.index).fillna(0.0)
    rf = rf_daily.reindex(r.index).fillna(0.0) if rf_daily is not None else pd.Series(0.0, index=r.index)
    n = len(r)
    if n < 20:
        return {"days": float(n)}
    ra, ba, fa = r.to_numpy(), br.to_numpy(), rf.to_numpy()
    xs, xb = ra - fa, ba - fa
    var_b = xb.var()
    beta = float(np.cov(xs, xb, ddof=0)[0, 1] / var_b) if var_b > 0 else math.nan
    alpha = float((xs - beta * xb).mean() * TRADING_DAYS) if not math.isnan(beta) else math.nan
    diff = ra - ba
    te = diff.std(ddof=1)
    out = {
        "days": float(n),
        "beta": beta,
        "alpha_annual": alpha,
        "excess_return_annual": float(diff.mean() * TRADING_DAYS),
        "tracking_error": float(te * math.sqrt(TRADING_DAYS)),
        "information_ratio": float(diff.mean() / te * math.sqrt(TRADING_DAYS)) if te > 0 else math.nan,
        "t_stat_excess": float(diff.mean() / (te / math.sqrt(n))) if te > 0 else math.nan,
        "correlation": float(np.corrcoef(ra, ba)[0, 1]) if ra.std() > 0 and ba.std() > 0 else math.nan,
    }
    # moving-block bootstrap of the paired daily returns
    rng = np.random.default_rng(seed)
    nb = max(1, math.ceil(n / block))
    starts = rng.integers(0, max(1, n - block + 1), size=(n_boot, nb))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n_boot, -1)[:, :n]
    sr = np.array([_sharpe(ra[i], fa[i]) for i in idx])
    sb = np.array([_sharpe(ba[i], fa[i]) for i in idx])
    ex = (ra[idx] - ba[idx]).mean(axis=1) * TRADING_DAYS
    ok = ~np.isnan(sr) & ~np.isnan(sb)
    if ok.any():
        out["sharpe_ci95_low"], out["sharpe_ci95_high"] = (float(x) for x in np.percentile(sr[ok], [2.5, 97.5]))
        d = sr[ok] - sb[ok]
        out["sharpe_minus_spy_ci95_low"], out["sharpe_minus_spy_ci95_high"] = (float(x) for x in
                                                                             np.percentile(d, [2.5, 97.5]))
        out["p_sharpe_above_spy"] = float((d > 0).mean())
    out["excess_ci95_low"], out["excess_ci95_high"] = (float(x) for x in np.percentile(ex, [2.5, 97.5]))
    out["p_excess_above_zero"] = float((ex > 0).mean())
    return out


def equal_weight_curve(closes: dict[str, pd.Series], index: pd.DatetimeIndex, cash: float) -> pd.Series:
    """Buy every stock in equal amounts at the first close and hold: the universe's own buy-and-hold."""
    cols = []
    for s in closes.values():
        c = s.reindex(index).ffill()
        if pd.notna(c.iloc[0]) and c.iloc[0] > 0:
            cols.append(c / c.iloc[0])
    if not cols:
        return pd.Series(np.nan, index=index)
    return pd.concat(cols, axis=1).mean(axis=1) * cash
