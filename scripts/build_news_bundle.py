"""Build the headline file of a frozen period: ~/.riskaware/news/periods/news_<year>.jsonl.gz.

    python scripts/build_news_bundle.py --archive DIR --start 2026-01-01 --end 2026-11-11
    python scripts/build_news_bundle.py --parquet news_2022.parquet --start 2022-01-01 --end 2022-12-30 \
        --out ~/.riskaware/news/periods/news_2022.jsonl.gz

The file stays on your machine: neither the package nor the repository carries headlines (they are not
redistributed). The SHA-256 in the period's manifest (src/riskaware/research/data/manifest*.json) says whether your
file is byte for byte the one the published results used.

DIR is a local news archive (see ``riskaware news-history``) that already holds the source items, for example
filled with ``RISKAWARE_CONFIG=build.yaml riskaware news-history build --source sec ...`` plus an import of
headlines. ``--parquet`` reads a Benzinga export instead (columns title, tickers, published_at; the body and teaser
are not read). For the stocks of the built-in watchlists the file keeps:

* headlines from the ``benzinga`` feed: the title, the publication time and the ticker. No article text,
  teaser, link or sentiment. A headline is kept for a ticker only when it names the company or the ticker,
  the rule live trading uses to drop off-topic stories. Automated data recaps (what an investment would be
  worth today, dividend calculators, options-flow, short-interest and P/E summaries, TV pick lists) are
  left out: they repeat prices instead of reporting news;
* SEC 8-K filings from the ``sec`` feed: the form items, the time SEC accepted the filing and its sec.gov link.

The output is sorted and written with a fixed gzip timestamp, so the same input gives the same file.
"""

from __future__ import annotations

import argparse
import gzip
import html
import json
import re
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from riskaware.news.history import NewsArchive, period_news_path
from riskaware.news.relevance import company_names, mentions
from riskaware.universe import PRESETS, is_etf

WATCHLISTS = ("core", "megacap", "tech", "defensive")
MAX_TITLE = 300
RECAP = re.compile(
    r"(?i)^(here's how much|if you invested|\$[\d,]+ invested in|how to earn \$|cnbc halftime report|live on cnbc)"
    r"|\boptions (activity|market dynamics|trading|frenzy)|smart money is betting big|\bwhales\b"
    r"|p/e ratio insights|price over earnings|short interest|short percent of float")


def default_symbols() -> list[str]:
    return sorted({s for name in WATCHLISTS for s in PRESETS[name] if not is_etf(s)})


def clean_title(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(html.unescape(text or ""))).strip()


def bundle_records(archive: NewsArchive, symbols: list[str], start: date, end: date) -> list[dict]:
    out: dict[tuple, dict] = {}
    for sym in symbols:
        names = company_names(sym, fetch=False)
        for rec in archive.records(sym):
            ts = rec.get("ts") or ""
            day = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).date() if ts else None
            if day is None or not start <= day <= end:
                continue
            feed = rec.get("feed")
            if feed == "benzinga":
                title = clean_title(rec.get("headline", ""))
                if not title or len(title) > MAX_TITLE or RECAP.search(title) or not mentions(title, sym, names):
                    continue
                row = {"ts": ts, "symbol": sym, "headline": title, "source": "Benzinga", "feed": "benzinga"}
            elif feed == "sec":
                row = {"ts": ts, "symbol": sym, "headline": rec["headline"], "source": "SEC EDGAR", "feed": "sec",
                       "url": rec.get("url", ""), "summary": rec.get("summary", "")}
            else:
                continue
            out.setdefault((feed, sym, row["headline"], ts), row)
    return sorted(out.values(), key=lambda r: (r["ts"], r["symbol"], r["feed"], r["headline"]))


#: Tickers a company traded under before (a headline using the old one still names it).
FORMER_TICKERS = {"META": ["FB"]}


def parquet_records(path: str, symbols: list[str], start: date, end: date) -> list[dict]:
    """Benzinga headlines from a parquet export, with the same filters as ``bundle_records``.

    Rows tagged with tickers are kept for the tagged stocks the title names. Exports that lost their ticker tags
    (most of 2022) are matched by the title alone: a headline goes to every stock whose company name or ticker it
    contains, by the rule live trading uses to decide whether a story is about a company.
    """
    import pandas as pd

    cols = ["title", "tickers", "published_at"]
    import pyarrow.parquet as pq

    have = set(pq.read_schema(path).names)
    df = pd.read_parquet(path, columns=[c for c in cols if c in have])
    if "tickers" not in df:
        df["tickers"] = None
    df = df[df["published_at"].notna()]
    day = df["published_at"].dt.tz_convert("UTC").dt.date
    df = df[(day >= start) & (day <= end)]
    wanted = set(symbols)
    out: dict[tuple, dict] = {}
    names = {s: company_names(s, fetch=False) for s in symbols}
    for title, tickers, ts in zip(df["title"], df["tickers"], df["published_at"], strict=True):
        tagged = [str(t).upper() for t in (tickers if tickers is not None else ())]
        syms = wanted.intersection(tagged) if tagged else wanted
        if not syms:
            continue
        clean = clean_title(str(title or ""))
        if not clean or len(clean) > MAX_TITLE or RECAP.search(clean):
            continue
        stamp = ts.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
        for sym in sorted(syms):
            if not mentions(clean, sym, names[sym]) and not any(
                    mentions(clean, old, []) for old in FORMER_TICKERS.get(sym, [])):
                continue
            row = {"ts": stamp, "symbol": sym, "headline": clean, "source": "Benzinga", "feed": "benzinga"}
            out.setdefault(("benzinga", sym, clean, stamp), row)
    return sorted(out.values(), key=lambda r: (r["ts"], r["symbol"], r["feed"], r["headline"]))


def write(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
        for r in rows:
            gz.write((json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--archive", help="local news archive with the source items")
    src.add_argument("--parquet", help="a Benzinga parquet export (title, tickers, published_at)")
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", required=True, help="last day to include, YYYY-MM-DD")
    ap.add_argument("--symbols", help="comma-separated (default: the stocks of the built-in watchlists)")
    ap.add_argument("--out", default=str(period_news_path("news_2026.jsonl.gz")))
    a = ap.parse_args()
    symbols = [s.strip().upper() for s in a.symbols.split(",")] if a.symbols else default_symbols()
    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    if a.parquet:
        rows = parquet_records(a.parquet, symbols, start, end)
    else:
        rows = bundle_records(NewsArchive(a.archive, bundled=False), symbols, start, end)
    write(rows, Path(a.out))
    feeds = Counter(r["feed"] for r in rows)
    per = Counter(r["symbol"] for r in rows)
    print(f"{len(rows)} items for {len(per)} of {len(symbols)} symbols ({dict(feeds)}), "
          f"{rows[0]['ts'][:10] if rows else '-'} to {rows[-1]['ts'][:10] if rows else '-'} -> {a.out} "
          f"({Path(a.out).stat().st_size / 1e6:.2f} MB)")
    missing = [s for s in symbols if s not in per]
    if missing:
        print("no items for:", ", ".join(missing))


if __name__ == "__main__":
    main()
