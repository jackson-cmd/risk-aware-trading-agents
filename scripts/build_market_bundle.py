"""Build the market data of a bundled period: src/riskaware/research/data/market_<year>.csv.gz and its manifest.

    python scripts/build_market_bundle.py --period 2026     # 2024-01-02 to 2026-11-11, manifest.json
    python scripts/build_market_bundle.py --period 2022     # 2020-01-02 to 2022-12-30, manifest_2022.json

Daily bars from Yahoo Finance (yfinance, ``auto_adjust=False``) for:

* the 64 stocks of the built-in watchlists (core, megacap, tech, defensive), the same stocks as the period headline files;
* the index, sector and bond ETFs the agents use (SPY, QQQ, the 11 sector ETFs, BIL, SGOV, IEF, TLT, HYG, ...);
* the VIX term structure and its volatility: ^VIX9D, ^VIX, ^VIX3M, ^VVIX;
* a few cross-asset ETFs that close with the stock market: GLD (gold), USO (oil), UUP (dollar), IBIT (bitcoin).
  Futures, the dollar index and bitcoin itself are left out: they settle after 16:00 New York time, so their
  "close" for a day would be later than a decision made at that day's close.

Each row keeps the prices as traded (adjusted for splits only), the volume, the cash dividend and split of that
day, and ``adj``, the factor that turns the prices into total-return prices (Yahoo's Adj Close / Close). The two
years before a period's test window are there so that slow indicators (EMA200, 52-week highs) are fully warmed up
on its first day. A symbol that did not trade yet (IBIT before 2024) is left out of that period and listed in the
manifest.

The output is sorted and written with a fixed gzip timestamp; manifest.json next to it records the period, the
symbols and the SHA-256 of this file and of the period's headline file (your copy; not redistributed). Prices on Yahoo change when a dividend is paid
or data is corrected, so a later rebuild of the same period can differ slightly: the point of shipping the file
is that every experiment runs on exactly the same numbers.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import math
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from riskaware.research.periods import get_period
from riskaware.universe import PRESETS, SECTOR_MAP, is_etf

OUT_DIR = Path(__file__).resolve().parent.parent / "src" / "riskaware" / "research" / "data"
WATCHLISTS = ("core", "megacap", "tech", "defensive")
VOLATILITY = ["^VIX9D", "^VIX", "^VIX3M", "^VVIX"]
CROSS_ASSET = {"GLD": "gold", "USO": "oil", "UUP": "US dollar", "IBIT": "bitcoin"}
COLUMNS = ["date", "symbol", "open", "high", "low", "close", "volume", "adj", "dividend", "split"]


def default_symbols() -> dict[str, list[str]]:
    stocks = sorted({s for name in WATCHLISTS for s in PRESETS[name] if not is_etf(s)})
    etfs = sorted(s for s in SECTOR_MAP if is_etf(s))
    return {"stocks": stocks, "etfs": etfs, "volatility": VOLATILITY, "cross_asset": list(CROSS_ASSET)}


def download(symbols: list[str], start: date, end: date, batch: int = 25) -> dict[str, pd.DataFrame]:
    import yfinance as yf

    out: dict[str, pd.DataFrame] = {}
    for i in range(0, len(symbols), batch):
        part = symbols[i:i + batch]
        for attempt in range(4):
            try:
                raw = yf.download(tickers=part, start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                                  auto_adjust=False, actions=True, group_by="ticker", progress=False, threads=True)
                break
            except Exception as exc:  # noqa: BLE001 - rate limits and timeouts: wait and retry
                print(f"  retry {attempt + 1} for {part[0]}..: {exc}")
                time.sleep(5 * (attempt + 1))
        else:
            raise RuntimeError(f"download failed for {part}")
        for sym in part:
            try:
                df = raw[sym].dropna(how="all")
            except KeyError:
                continue
            if not df.empty:
                out[sym] = df
        print(f"  {min(i + batch, len(symbols))}/{len(symbols)} symbols")
    return out


def tidy(sym: str, df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    d = df.copy()
    d.index = pd.to_datetime(d.index).tz_localize(None).normalize()
    d = d[(d.index >= pd.Timestamp(start)) & (d.index <= pd.Timestamp(end))]
    d = d[d["Close"].notna() & (d["Close"] > 0)]
    o = d["Open"].where(d["Open"] > 0, d["Close"])
    h = pd.concat([d["High"], o, d["Close"]], axis=1).max(axis=1)  # a bar is always inside its own range
    lo = pd.concat([d["Low"].where(d["Low"] > 0), o, d["Close"]], axis=1).min(axis=1)
    adj = (d["Adj Close"] / d["Close"]).where(d["Adj Close"] > 0, 1.0)
    return pd.DataFrame({
        "date": d.index.strftime("%Y-%m-%d"), "symbol": sym,
        "open": o.round(4).to_numpy(), "high": h.round(4).to_numpy(), "low": lo.round(4).to_numpy(),
        "close": d["Close"].round(4).to_numpy(), "volume": d["Volume"].fillna(0).round().astype("int64").to_numpy(),
        "adj": adj.round(7).to_numpy(),
        "dividend": (d["Dividends"] if "Dividends" in d else 0.0).fillna(0.0).round(6).to_numpy(),
        "split": (d["Stock Splits"] if "Stock Splits" in d else 0.0).fillna(0.0).round(6).to_numpy(),
    })


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--period", default="2026", help="a bundled period (see riskaware.research.periods)")
    ap.add_argument("--start", help="first day of data (default: two years before the period's test window)")
    ap.add_argument("--end", help="last day of data (default: the period's last day)")
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args()
    per = get_period(args.period)
    start = date.fromisoformat(args.start) if args.start else date(per.start.year - 2, 1, 2)
    end = date.fromisoformat(args.end) if args.end else per.end
    groups = default_symbols()
    symbols = [s for g in groups.values() for s in g]
    print(f"downloading {len(symbols)} symbols, {start} to {end}")
    raw = download(symbols, start, end)
    missing = [s for s in symbols if s not in raw or tidy(s, raw[s], start, end).empty]
    if "SPY" in missing:
        raise SystemExit("no data for SPY")
    if missing:
        print(f"not trading yet in this period, left out: {missing}")
        groups = {k: [s for s in v if s not in missing] for k, v in groups.items()}
        symbols = [s for s in symbols if s not in missing]
    table = pd.concat([tidy(s, raw[s], start, end) for s in symbols], ignore_index=True)
    table = table.sort_values(["date", "symbol"]).reset_index(drop=True)
    spy_days = set(table.loc[table["symbol"] == "SPY", "date"])
    gaps = {s: len(spy_days - set(g["date"])) for s, g in table.groupby("symbol")}
    late = {s: g["date"].min() for s, g in table.groupby("symbol") if g["date"].min() > min(spy_days)}

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"market_{end.year}.csv.gz"
    buf = io.StringIO()
    table.to_csv(buf, index=False, columns=COLUMNS, lineterminator="\n", float_format="%.7g")
    with open(path, "wb") as fh, gzip.GzipFile(filename="", mode="wb", fileobj=fh, mtime=0, compresslevel=9) as gz:
        gz.write(buf.getvalue().encode("utf-8"))

    first_trading = min(d for d in spy_days if d >= per.start.isoformat())
    news_file = per.news_path  # your copy: the headlines are not redistributed
    manifest = {
        "name": f"riskaware market {end.year}",
        "version": end.isoformat(),
        "built": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "period": {"first": min(spy_days), "last": max(spy_days), "experiment_start": first_trading,
                   "trading_days": len(spy_days)},
        "files": {
            path.name: {"sha256": sha256(path), "rows": int(len(table)), "columns": COLUMNS},
            per.news: {"sha256": sha256(news_file) if news_file.is_file() else None,
                       "path": f"~/.riskaware/news/periods/{per.news}"},
        },
        "symbols": groups,
        "cross_asset": {k: v for k, v in CROSS_ASSET.items() if k in symbols},
        "not_trading_yet": missing,
        "late_start": late,
        "missing_days": {s: n for s, n in gaps.items() if n and s not in late and not s.startswith("^")},
        "source": "Yahoo Finance daily bars via yfinance (auto_adjust=False, actions=True)",
        "prices": "open, high, low, close as traded (split-adjusted); adj = Adj Close / Close turns them into "
                  "total-return prices; dividend and split are the cash dividend and split ratio of that day",
    }
    (out_dir / per.manifest).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    size = path.stat().st_size / 1e6
    print(f"wrote {path} ({len(table):,} rows, {size:.2f} MB) and {per.manifest}")
    if late:
        print("start later than the period:", late)
    bad = {s: n for s, n in manifest["missing_days"].items()}
    if bad:
        print("days missing against SPY:", bad)
    nan = table[["open", "high", "low", "close"]].isna().sum().sum()
    if nan or not math.isfinite(table["close"].sum()):
        raise SystemExit("NaN prices in the output")


if __name__ == "__main__":
    main()
