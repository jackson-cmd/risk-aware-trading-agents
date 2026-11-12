"""Vectorized technical indicators on daily OHLCV data.

Input frames use lowercase columns: open, high, low, close, volume. Every
function only looks backward, so values at row t never use data after t.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False, min_periods=span).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def wilder(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = wilder(delta.clip(lower=0.0), n)
    loss = wilder((-delta).clip(lower=0.0), n)
    rs = gain / loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    out = out.mask((loss == 0) & (gain > 0), 100.0)
    out = out.mask((loss == 0) & (gain == 0), 50.0)
    return out


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    ranges = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1
    )
    return ranges.max(axis=1, skipna=True)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return wilder(true_range(df), n)


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    tr = wilder(true_range(df), n)
    plus_di = 100 * wilder(plus_dm, n) / tr
    minus_di = 100 * wilder(minus_dm, n) / tr
    denom = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / denom
    return wilder(dx, n)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    fast_ema = close.ewm(span=fast, adjust=False, min_periods=fast).mean()
    slow_ema = close.ewm(span=slow, adjust=False, min_periods=slow).mean()
    line = fast_ema - slow_ema
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "macd_signal": sig, "macd_hist": line - sig})


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    return pd.DataFrame({"bb_mid": mid, "bb_upper": mid + k * sd, "bb_lower": mid - k * sd})


def zscore(close: pd.Series, n: int = 20) -> pd.Series:
    mean = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0).replace(0.0, np.nan)
    return (close - mean) / sd


def realized_vol(close: pd.Series, n: int = 60) -> pd.Series:
    r = np.log(close / close.shift(1))
    return r.rolling(n, min_periods=max(10, n // 2)).std(ddof=0) * np.sqrt(TRADING_DAYS)


def down_streak(close: pd.Series) -> pd.Series:
    """Number of consecutive closes below the previous close, ending at each row."""
    down = (close.diff() < 0).astype(int)
    groups = (down == 0).cumsum()
    return down.groupby(groups).cumsum()


def below_streak(close: pd.Series, level: pd.Series) -> pd.Series:
    """Consecutive closes below a level (e.g. EMA50)."""
    below = (close < level).astype(int)
    groups = (below == 0).cumsum()
    return below.groupby(groups).cumsum()


def rolling_beta(ret: pd.Series, bench_ret: pd.Series, n: int = 120) -> pd.Series:
    cov = ret.rolling(n, min_periods=n // 2).cov(bench_ret)
    var = bench_ret.rolling(n, min_periods=n // 2).var()
    return cov / var.replace(0.0, np.nan)


def pct_rank(s: pd.Series, n: int = 252) -> pd.Series:
    """Rolling percentile rank (0..100) of the latest value within the window."""

    def _rank(window: np.ndarray) -> float:
        last = window[-1]
        if np.isnan(last):
            return np.nan
        valid = window[~np.isnan(window)]
        return 100.0 * (np.sum(valid < last) + 0.5 * np.sum(valid == last) - 0.5) / max(len(valid) - 1, 1)

    return s.rolling(n, min_periods=max(20, n // 4)).apply(_rank, raw=True).clip(0, 100)


def compute(df: pd.DataFrame, benchmark_close: pd.Series | None = None) -> pd.DataFrame:
    """Add every indicator the agents use. Returns a new frame aligned to ``df``."""
    if df.empty:
        return df.copy()
    out = df.copy()
    c = out["close"]
    for span in (20, 50, 100, 200):
        out[f"ema{span}"] = ema(c, span)
    out["sma20"] = sma(c, 20)
    out["atr14"] = atr(out, 14)
    out["atr_pct"] = out["atr14"] / c
    out["rsi2"] = rsi(c, 2)
    out["rsi14"] = rsi(c, 14)
    out["z20"] = zscore(c, 20)
    out = out.join(bollinger(c))
    out = out.join(macd(c))
    out["adx14"] = adx(out, 14)
    out["don55_high"] = out["high"].rolling(55, min_periods=55).max().shift(1)
    out["don20_low"] = out["low"].rolling(20, min_periods=20).min().shift(1)
    vol = out["volume"].astype(float)
    out["vol_avg20"] = vol.rolling(20, min_periods=10).mean().shift(1)
    out["rel_vol"] = vol / out["vol_avg20"].replace(0.0, np.nan)
    out["high252"] = out["high"].rolling(252, min_periods=60).max()
    out["dd252"] = c / out["high252"] - 1.0
    out["ema200_slope"] = out["ema200"] / out["ema200"].shift(20) - 1.0
    out["ema100_slope"] = out["ema100"] / out["ema100"].shift(15) - 1.0
    out["ema50_slope"] = out["ema50"] / out["ema50"].shift(10) - 1.0
    out["mom126"] = c / c.shift(126) - 1.0
    out["mom21"] = c / c.shift(21) - 1.0
    out["vol20"] = realized_vol(c, 20)
    out["vol60"] = realized_vol(c, 60)
    out["down_streak"] = down_streak(c)
    out["below_ema50_streak"] = below_streak(c, out["ema50"])
    out["ret1"] = c.pct_change()
    out["prev_close"] = c.shift(1)
    out["prev_high"] = out["high"].shift(1)
    out["rsi2_prev"] = out["rsi2"].shift(1)
    out["high10"] = out["high"].rolling(10, min_periods=5).max()
    out["mom252"] = c / c.shift(252) - 1.0
    if benchmark_close is not None:
        bench = benchmark_close.reindex(out.index).ffill()
        out["rs126"] = out["mom126"] - (bench / bench.shift(126) - 1.0)
        out["beta120"] = rolling_beta(out["ret1"], bench.pct_change(), 120)
    else:
        out["rs126"] = np.nan
        out["beta120"] = np.nan
    return out


def last_row(frame: pd.DataFrame) -> pd.Series | None:
    if frame is None or frame.empty:
        return None
    return frame.iloc[-1]
