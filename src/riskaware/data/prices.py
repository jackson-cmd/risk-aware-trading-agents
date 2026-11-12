"""Daily price history providers.

* ``YahooPrices``: free daily bars from Yahoo Finance through ``yfinance``, split-
  and dividend-adjusted, cached on disk.
* ``CSVPrices``: your own files, one ``SYMBOL.csv`` per symbol with Date, Open,
  High, Low, Close, Volume columns (any case).
* ``SyntheticPrices``: reproducible random markets for demos and tests, no network.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd

from ..utils import DiskCache

log = logging.getLogger(__name__)

OHLCV = ["open", "high", "low", "close", "volume"]


class PriceProvider(Protocol):
    def history(self, symbols: list[str], start: date, end: date | None = None) -> dict[str, pd.DataFrame]:
        """Daily OHLCV per symbol, index = tz-naive dates, columns = open high low close volume."""
        ...


def normalize_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Lowercase OHLCV columns, tz-naive date index, sorted, no duplicate days, no empty rows."""
    if df is None or df.empty:
        return pd.DataFrame(columns=OHLCV)
    out = df.copy()
    out.columns = [str(c).strip().lower().replace(" ", "_") for c in out.columns]
    rename = {"adj_close": "adj_close", "date": "date"}
    out = out.rename(columns=rename)
    if "date" in out.columns:
        out = out.set_index("date")
    idx = pd.to_datetime(out.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    out.index = idx.normalize()
    out.index.name = "date"
    if "close" not in out.columns and "adj_close" in out.columns:
        out["close"] = out["adj_close"]
    for col in OHLCV:
        if col not in out.columns:
            out[col] = np.nan if col != "volume" else 0.0
    out = out[OHLCV].apply(pd.to_numeric, errors="coerce")
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out = out.dropna(subset=["close"])
    for col in ("open", "high", "low"):
        out[col] = out[col].fillna(out["close"])
    out["volume"] = out["volume"].fillna(0.0)
    return out


class YahooPrices:
    """Yahoo Finance daily bars via yfinance, with a per-symbol disk cache."""

    def __init__(self, cache: DiskCache | None = None, ttl_hours: float = 1.0, batch_size: int = 40):
        self.cache = cache
        self.ttl = ttl_hours * 3600
        self.batch_size = batch_size

    def _cache_key(self, symbol: str, start: date) -> str:
        return f"{symbol}:{start.year}"

    def history(self, symbols: list[str], start: date, end: date | None = None) -> dict[str, pd.DataFrame]:
        from ..marketcal import last_completed_session

        end = end or date.today()
        result: dict[str, pd.DataFrame] = {}
        missing: list[str] = []
        live = end >= date.today() - timedelta(days=3)
        ttl = self.ttl if live else 30 * 86400
        expected_last = last_completed_session() if live else None
        # The cache key holds the first day of the start year, so a cached frame always
        # covers the requested start (or the listing date for younger symbols).
        fetch_start = date(start.year, 1, 1)
        for sym in dict.fromkeys(symbols):
            df = self.cache.get_frame("prices", self._cache_key(sym, fetch_start), ttl) if self.cache else None
            if df is not None and not df.empty:
                if expected_last is None or df.index.max().date() >= expected_last:
                    result[sym] = df
                    continue
            missing.append(sym)
        for i in range(0, len(missing), self.batch_size):
            batch = missing[i : i + self.batch_size]
            fetched = self._download(batch, fetch_start, max(end, date.today()))
            for sym, df in fetched.items():
                if self.cache is not None and not df.empty:
                    self.cache.set_frame("prices", self._cache_key(sym, fetch_start), df)
                result[sym] = df
        sliced = {}
        for sym, df in result.items():
            part = df.loc[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))]
            if not part.empty:
                sliced[sym] = part
        return sliced

    def _download(self, symbols: list[str], start: date, end: date) -> dict[str, pd.DataFrame]:
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("yfinance is required for Yahoo prices: pip install yfinance") from exc
        out: dict[str, pd.DataFrame] = {}
        for attempt in range(3):
            try:
                raw = yf.download(
                    tickers=symbols,
                    start=start.isoformat(),
                    end=(end + timedelta(days=1)).isoformat(),
                    auto_adjust=True,
                    progress=False,
                    group_by="ticker",
                    threads=True,
                    actions=False,
                )
                break
            except Exception as exc:  # network errors, rate limits
                log.warning("yfinance download failed (attempt %d): %s", attempt + 1, exc)
                time.sleep(2 * (attempt + 1))
        else:
            return out
        if raw is None or raw.empty:
            return out
        if isinstance(raw.columns, pd.MultiIndex):
            level0 = set(map(str, raw.columns.get_level_values(0)))
            for sym in symbols:
                if sym in level0:
                    sub = raw[sym]
                else:
                    try:
                        sub = raw.xs(sym, axis=1, level=1)
                    except KeyError:
                        continue
                df = normalize_frame(sub)
                if not df.empty:
                    out[sym] = df
        elif len(symbols) == 1:
            df = normalize_frame(raw)
            if not df.empty:
                out[symbols[0]] = df
        missing = [s for s in symbols if s not in out]
        if missing:
            log.info("no Yahoo data for: %s", ", ".join(missing))
        return out


class CSVPrices:
    """Read ``<dir>/<SYMBOL>.csv`` files. Symbols with ^ or / are looked up with those characters removed too."""

    def __init__(self, directory: str | Path):
        self.dir = Path(directory).expanduser()
        if not self.dir.is_dir():
            raise FileNotFoundError(f"CSV price directory not found: {self.dir}")

    def _file(self, symbol: str) -> Path | None:
        for name in (symbol, symbol.replace("^", ""), symbol.replace("/", "-"), symbol.lower()):
            p = self.dir / f"{name}.csv"
            if p.is_file():
                return p
        return None

    def history(self, symbols: list[str], start: date, end: date | None = None) -> dict[str, pd.DataFrame]:
        out = {}
        for sym in symbols:
            p = self._file(sym)
            if p is None:
                continue
            raw = pd.read_csv(p)
            date_col = next((c for c in raw.columns if str(c).lower() in ("date", "datetime", "time")), raw.columns[0])
            raw = raw.set_index(date_col)
            df = normalize_frame(raw)
            mask = df.index >= pd.Timestamp(start)
            if end:
                mask &= df.index <= pd.Timestamp(end)
            if mask.any():
                out[sym] = df.loc[mask]
        return out


class SyntheticPrices:
    """Reproducible synthetic markets with bull, choppy and crash phases.

    Each symbol follows a factor model on a shared market path, so regimes,
    breadth and correlations behave sensibly. VIX-like series are derived from
    realized market volatility. Good for demos and offline tests only.
    """

    def __init__(self, seed: int = 7, start: date = date(2015, 1, 1), end: date | None = None):
        self.seed = seed
        self.start = start
        self.end = end or date.today()
        self._cache: dict[str, pd.DataFrame] = {}
        self._market: pd.DataFrame | None = None

    def _days(self) -> pd.DatetimeIndex:
        from ..marketcal import trading_days

        return pd.DatetimeIndex([pd.Timestamp(d) for d in trading_days(self.start, self.end)])

    def _market_path(self) -> pd.DataFrame:
        if self._market is not None:
            return self._market
        rng = np.random.default_rng(self.seed)
        days = self._days()
        n = len(days)
        drift = np.full(n, 0.0004)
        vol = np.full(n, 0.009)
        i = 0
        while i < n:  # alternate phases
            length = int(rng.integers(120, 380))
            kind = rng.choice(["bull", "chop", "crash"], p=[0.6, 0.28, 0.12])
            sl = slice(i, min(n, i + length))
            if kind == "bull":
                drift[sl], vol[sl] = 0.0007, 0.008
            elif kind == "chop":
                drift[sl], vol[sl] = 0.0, 0.012
            else:
                crash_len = min(60, length)
                drift[i : min(n, i + crash_len)] = -0.004
                vol[i : min(n, i + crash_len)] = 0.028
                drift[min(n, i + crash_len) : sl.stop] = 0.0012
                vol[min(n, i + crash_len) : sl.stop] = 0.015
            i += length
        rets = drift + vol * rng.standard_t(df=5, size=n) / np.sqrt(5 / 3)
        close = 300 * np.exp(np.cumsum(rets))
        realized = pd.Series(rets, index=days).rolling(20, min_periods=5).std().bfill() * np.sqrt(252)
        vix = (realized * 100 * 1.15 + 4 + rng.normal(0, 0.8, n)).clip(9, 85)
        vix3m = (vix.rolling(60, min_periods=1).mean() * 0.6 + vix * 0.4 + 1.2).clip(11, 70)
        self._market = pd.DataFrame({"ret": rets, "close": close, "vix": vix.values, "vix3m": vix3m.values}, index=days)
        return self._market

    def _make(self, symbol: str) -> pd.DataFrame:
        mkt = self._market_path()
        days = mkt.index
        n = len(days)
        if symbol in ("^VIX", "^VIX3M"):
            col = "vix" if symbol == "^VIX" else "vix3m"
            v = mkt[col].to_numpy()
            return pd.DataFrame(
                {"open": v, "high": v * 1.03, "low": v * 0.97, "close": v, "volume": np.zeros(n)}, index=days
            )
        seed = int(hashlib.md5(f"{self.seed}:{symbol}".encode()).hexdigest()[:8], 16)
        rng = np.random.default_rng(seed)
        if symbol in ("BIL", "SGOV", "SHV"):
            rets = np.full(n, 0.00008) + rng.normal(0, 0.00002, n)
        elif symbol in ("IEF", "TLT", "SHY"):
            beta = -0.15 if symbol != "SHY" else -0.03
            rets = 0.00012 + beta * mkt["ret"].to_numpy() + rng.normal(0, 0.004 if symbol == "TLT" else 0.0025, n)
        elif symbol == "SPY":
            rets = mkt["ret"].to_numpy()
        else:
            beta = rng.uniform(0.6, 1.6)
            alpha = rng.normal(0.0001, 0.00025)
            idio = rng.uniform(0.007, 0.02)
            rets = alpha + beta * mkt["ret"].to_numpy() + rng.normal(0, idio, n)
            jumps = rng.random(n) < 0.004
            rets = rets + jumps * rng.normal(-0.02, 0.07, n)
        close = rng.uniform(40, 400) * np.exp(np.cumsum(rets))
        noise = np.abs(rng.normal(0, 0.006, n))
        open_ = close / np.exp(rets) * (1 + rng.normal(0, 0.003, n))
        high = np.maximum(open_, close) * (1 + noise)
        low = np.minimum(open_, close) * (1 - noise)
        volume = rng.lognormal(15, 0.4, n) * (1 + 3 * (np.abs(rets) > 0.03))
        return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=days)

    def history(self, symbols: list[str], start: date, end: date | None = None) -> dict[str, pd.DataFrame]:
        out = {}
        for sym in symbols:
            if sym not in self._cache:
                self._cache[sym] = self._make(sym)
            df = self._cache[sym]
            mask = df.index >= pd.Timestamp(start)
            if end:
                mask &= df.index <= pd.Timestamp(end)
            out[sym] = df.loc[mask]
        return out


def make_price_provider(source: str, cache: DiskCache | None = None, csv_dir: str | None = None) -> PriceProvider:
    if source == "yfinance":
        return YahooPrices(cache=cache)
    if source == "csv":
        if not csv_dir:
            raise ValueError("data.csv_dir must be set when data.price_source is 'csv'")
        return CSVPrices(csv_dir)
    if source == "synthetic":
        return SyntheticPrices()
    raise ValueError(f"unknown price source: {source}")


def to_date(x: date | datetime | str | pd.Timestamp) -> date:
    if isinstance(x, pd.Timestamp):
        return x.date()
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    return pd.Timestamp(x).date()
