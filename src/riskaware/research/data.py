"""The frozen markets: the daily bars of each bundled period (see ``periods``) that ship with the package.

Experiments in ``riskaware.research`` read prices from here and never from the network, so an experiment gives the
same numbers on any machine and on any later day. Each period has a manifest (``data/manifest.json`` for 2026,
``data/manifest_2022.json`` for 2022) that says what its files hold and gives their SHA-256; every experiment copies
that into its own manifest. ``scripts/build_market_bundle.py`` rebuilds the files.

The file keeps prices as traded (adjusted for splits) plus the factor ``adj`` that turns them into total-return
prices. Backtests and indicators use the total-return prices, like the rest of the package; ``raw()`` gives the
prices as traded.
"""

from __future__ import annotations

import functools
import hashlib
import json
import zlib
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from .periods import DEFAULT_PERIOD, PERIODS, get_period

DATA_DIR = Path(__file__).resolve().parent / "data"
MANIFEST_PATH = DATA_DIR / "manifest.json"
OHLCV = ["open", "high", "low", "close", "volume"]


@functools.lru_cache(maxsize=8)
def manifest(period: str | None = None) -> dict:
    """What a period's market file holds: its dates, the symbols by group, and the SHA-256 of each file."""
    return json.loads((DATA_DIR / get_period(period).manifest).read_text(encoding="utf-8"))


def market_path(period: str | None = None) -> Path:
    name = next(k for k in manifest(period)["files"] if k.endswith(".csv.gz"))
    return DATA_DIR / name


def available_periods() -> list[str]:
    """The periods whose files are present (all of them in an installed package)."""
    return [p for p in sorted(PERIODS) if (DATA_DIR / PERIODS[p].manifest).is_file()]


@functools.lru_cache(maxsize=2)
def _load(path: str, mtime: float) -> dict[str, pd.DataFrame]:
    table = pd.read_csv(path, dtype={"date": str, "symbol": str})
    table["date"] = pd.to_datetime(table["date"])
    out: dict[str, pd.DataFrame] = {}
    for sym, g in table.groupby("symbol", sort=True):
        f = g.drop(columns="symbol").set_index("date").sort_index()
        f.index.name = "date"
        out[str(sym)] = f
    return out


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FrozenMarket:
    """Daily bars of the bundled period, as a price provider for the backtester.

    ``history()`` returns total-return OHLCV and always starts at the first day of the data, whatever start is asked
    for: indicators such as EMA200 then hold the same value on a given day in every experiment, wherever the
    experiment begins. Nothing after ``end`` is returned.
    """

    def __init__(self, frames: dict[str, pd.DataFrame] | None = None, label: str = "bundled",
                 fingerprint: str | None = None, period: str | None = None):
        self.period = get_period(period).name
        if frames is None:
            path = market_path(self.period)
            frames = _load(str(path), path.stat().st_mtime)
            fingerprint = fingerprint or manifest(self.period)["files"][path.name]["sha256"]
            label = label if label != "bundled" else f"bundled {self.period}"
        self._raw = frames
        self.label = label
        self._fingerprint = fingerprint
        self._adjusted: dict[str, pd.DataFrame] = {}

    # ------------------------------------------------------------------ what is inside
    @property
    def symbols(self) -> list[str]:
        return sorted(self._raw)

    def has(self, symbol: str) -> bool:
        return symbol in self._raw

    @property
    def calendar(self) -> pd.DatetimeIndex:
        """The trading days (SPY's)."""
        return self._raw["SPY"].index

    @property
    def first_day(self) -> date:
        return self.calendar[0].date()

    @property
    def last_day(self) -> date:
        return self.calendar[-1].date()

    @property
    def experiment_start(self) -> date:
        """The first trading day of the period's test window; the days before it are warm-up."""
        start = get_period(self.period).start
        if self.first_day <= start <= self.last_day:
            return next(ts.date() for ts in self.calendar if ts.date() >= start)
        year = self.last_day.year  # a market built by hand: the first day of its last year
        return next(ts.date() for ts in self.calendar if ts.year == year)

    def fingerprint(self) -> str:
        """SHA-256 of the bundled file, or of the frames for a modified market (for example a poisoned copy)."""
        if self._fingerprint is None:
            h = hashlib.sha256()
            for sym in self.symbols:
                h.update(sym.encode())
                h.update(pd.util.hash_pandas_object(self._raw[sym], index=True).to_numpy().tobytes())
            self._fingerprint = h.hexdigest()
        return self._fingerprint

    # ------------------------------------------------------------------ prices
    def raw(self, symbol: str) -> pd.DataFrame:
        """Prices as traded (split-adjusted), volume, adj, dividend and split of each day."""
        return self._raw[symbol].copy()

    def frame(self, symbol: str) -> pd.DataFrame:
        """Total-return OHLCV: the traded prices times ``adj``."""
        if symbol not in self._adjusted:
            r = self._raw[symbol]
            adj = r["adj"].where(r["adj"] > 0, 1.0)
            f = pd.DataFrame({c: r[c] * adj for c in ("open", "high", "low", "close")}, index=r.index)
            f["volume"] = r["volume"].astype(float)
            self._adjusted[symbol] = f
        return self._adjusted[symbol].copy()

    def history(self, symbols: list[str], start: date | None = None, end: date | None = None) -> dict[str, pd.DataFrame]:
        out: dict[str, pd.DataFrame] = {}
        stop = pd.Timestamp(end) if end else None
        for sym in dict.fromkeys(symbols):
            if sym not in self._raw:
                continue
            f = self.frame(sym)
            if stop is not None:
                f = f.loc[f.index <= stop]
            if not f.empty:
                out[sym] = f
        return out

    # ------------------------------------------------------------------ calendar
    def trading_days(self, start: date | None = None, end: date | None = None) -> list[date]:
        cal = self.calendar
        lo = pd.Timestamp(start) if start else cal[0]
        hi = pd.Timestamp(end) if end else cal[-1]
        return [ts.date() for ts in cal if lo <= ts <= hi]

    def is_trading_day(self, day: date) -> bool:
        return pd.Timestamp(day) in self._raw["SPY"].index

    def last_trading_day(self, on_or_before: date) -> date | None:
        cal = self.calendar
        i = cal.searchsorted(pd.Timestamp(on_or_before), side="right") - 1
        return cal[i].date() if i >= 0 else None

    def next_trading_day(self, after: date) -> date | None:
        cal = self.calendar
        i = cal.searchsorted(pd.Timestamp(after), side="right")
        return cal[i].date() if i < len(cal) else None

    # ------------------------------------------------------------------ leak check
    def poisoned(self, cut: date, seed: int = 0) -> FrozenMarket:
        """A copy whose bars after ``cut`` are a random walk: prices, ranges and volume, VIX included.

        Bars up to and including ``cut`` are untouched. A strategy that only uses what it may know decides the same
        way up to ``cut`` on both markets; see ``experiment.leak_check``.
        """
        stop = pd.Timestamp(cut)
        frames: dict[str, pd.DataFrame] = {}
        for sym, r in self._raw.items():
            after = r.index > stop
            n = int(after.sum())
            if n == 0 or not (~after).any():
                frames[sym] = r
                continue
            rng = np.random.default_rng([seed, zlib.crc32(sym.encode())])
            last = float(r.loc[~after, "close"].iloc[-1])
            steps = rng.normal(0.0, 0.03, n)
            close = last * np.exp(np.cumsum(steps))
            prev = np.concatenate([[last], close[:-1]])
            open_ = prev * np.exp(rng.normal(0.0, 0.01, n))
            high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0.0, 0.01, n)))
            low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0.0, 0.01, n)))
            f = r.copy()
            f["volume"] = f["volume"].astype(float)
            f.loc[after, "open"], f.loc[after, "high"] = open_, high
            f.loc[after, "low"], f.loc[after, "close"] = low, close
            vol = r.loc[after, "volume"].to_numpy(dtype=float)
            f.loc[after, "volume"] = np.round(vol * rng.lognormal(0.0, 0.5, n))
            f.loc[after, "adj"] = float(r.loc[~after, "adj"].iloc[-1])
            f.loc[after, "dividend"], f.loc[after, "split"] = 0.0, 0.0
            frames[sym] = f
        return FrozenMarket(frames, label=f"poisoned after {cut}", period=self.period)


@functools.lru_cache(maxsize=4)
def _bundled(period: str) -> FrozenMarket:
    return FrozenMarket(period=period)


def bundled_market(period: str | None = None) -> FrozenMarket:
    """A bundled period's market (2026 by default), loaded once per process."""
    return _bundled(get_period(period).name)


def news_path(period: str | None = None) -> Path:
    """The headline file of a period."""
    return get_period(period).news_path


def dataset_info(period: str | None = None) -> dict:
    """The dataset part of an experiment's manifest: versions and hashes of the price and news files."""
    p = get_period(period)
    m = manifest(p.name)
    path = market_path(p.name)
    news = m["files"].get(p.news, {})
    news_now = _sha256(p.news_path) if p.news_path.is_file() else None
    return {
        "name": m["name"], "version": m["version"], "period": m["period"], "test_window": p.describe(),
        "prices": {"file": path.name, "sha256": m["files"][path.name]["sha256"], "rows": m["files"][path.name]["rows"],
                   "source": m["source"]},
        "news": {"file": p.news, "sha256": news_now, "matches_manifest": news_now == news.get("sha256")},
    }


def verify(period: str | None = None) -> dict:
    """Check the bundled files against the SHA-256 in their manifest (every available period by default)."""
    out = {}
    for name in [get_period(period).name] if period else available_periods():
        p = get_period(name)
        for fname, meta in manifest(name)["files"].items():
            path = DATA_DIR / fname if (DATA_DIR / fname).is_file() else None
            if path is None and fname == p.news:
                path = p.news_path
            out[fname] = bool(path and path.is_file() and _sha256(path) == meta.get("sha256"))
    return out


__all__ = ["DEFAULT_PERIOD", "FrozenMarket", "available_periods", "bundled_market", "dataset_info", "manifest",
           "market_path", "news_path", "verify"]
