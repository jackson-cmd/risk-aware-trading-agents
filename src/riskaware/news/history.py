"""A local, point-in-time news archive for backtests.

Live trading reads news from the web as it happens. A backtest has to replay
what was known on each past day, which needs every item's publication time.
This module keeps such an archive on your own disk (``~/.riskaware/news`` by
default, never inside the repository) and answers one question for the
backtester: "what had been published about SYMBOL by the close of DAY?"

The archive is one JSON Lines file per symbol. Items come from:

* SEC EDGAR 8-K filings (``riskaware news-history build``), public and stamped
  with the time SEC accepted them;
* your own Benzinga API key (``--source benzinga``), kept local;
* any CSV you have (``riskaware news-history import``), see ``CSV_COLUMNS``.

Backtests use the same clustering and cross-source verification as live
trading, so a story still needs two independent publishers or an SEC filing
before it counts as confirmed.
"""

from __future__ import annotations

import bisect
import csv
import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ..config import RISKAWARE_HOME, NewsConfig
from ..types import NewsItem
from .verify import NewsReport, analyze_news

log = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")
DEFAULT_ROOT = RISKAWARE_HOME / "news"

#: Columns of the CSV import format. ``timestamp`` and ``symbol`` and ``headline`` are required.
CSV_COLUMNS = ("timestamp", "symbol", "headline", "source", "url", "sentiment")
_TS_ALIASES = ("timestamp", "timestamp_utc", "published", "datetime", "date", "time", "created")
_HEADLINE_ALIASES = ("headline", "title")
_SOURCE_ALIASES = ("source", "publisher")


def _parse_time(value: str, assume_tz: ZoneInfo | timezone = timezone.utc) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    text = value.replace("Z", "+00:00") if value.endswith("Z") else value
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        from email.utils import parsedate_to_datetime

        try:
            dt = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=assume_tz)
    return dt.astimezone(timezone.utc)


def item_to_record(item: NewsItem) -> dict:
    rec = {
        "ts": item.published.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if item.published else None,
        "symbol": (item.symbol or "").upper(),
        "headline": item.title,
        "source": item.publisher,
        "feed": item.feed,
        "url": item.url,
    }
    if item.summary:
        rec["summary"] = item.summary
    if item.sentiment is not None:
        rec["sentiment"] = round(float(item.sentiment), 4)
    return rec


def record_to_item(rec: dict) -> NewsItem:
    return NewsItem(
        title=rec.get("headline", ""), url=rec.get("url", ""), publisher=rec.get("source", ""),
        feed=rec.get("feed", "archive"), published=_parse_time(rec.get("ts") or ""),
        symbol=rec.get("symbol"), summary=rec.get("summary", ""), sentiment=rec.get("sentiment"),
    )


def _key(rec: dict) -> tuple:
    return (rec.get("feed"), rec.get("url") or rec.get("headline"), rec.get("ts"))


def _safe_name(symbol: str) -> str:
    sym = symbol.upper().strip()
    if not re.fullmatch(r"[A-Z0-9.\-^=]{1,15}", sym):
        raise ValueError(f"not a ticker symbol: {symbol!r}")
    return sym.replace("^", "_")


@dataclass
class SymbolStats:
    symbol: str
    items: int
    first: datetime | None
    last: datetime | None
    feeds: dict[str, int]


class NewsArchive:
    """One JSON Lines file per symbol, sorted by publication time, de-duplicated."""

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root).expanduser() if root else DEFAULT_ROOT
        self._mem: dict[str, list[dict]] = {}

    def path(self, symbol: str) -> Path:
        return self.root / f"{_safe_name(symbol)}.jsonl"

    def _read(self, symbol: str) -> list[dict]:
        sym = symbol.upper()
        if sym not in self._mem:
            p = self.path(sym)
            recs: list[dict] = []
            if p.exists():
                with p.open(encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if line:
                            try:
                                recs.append(json.loads(line))
                            except json.JSONDecodeError:
                                log.warning("skipping a damaged line in %s", p)
            self._mem[sym] = recs
        return self._mem[sym]

    def add(self, items: Iterable[NewsItem]) -> int:
        """Store items (those without a timestamp or symbol are skipped). Returns how many were new."""
        by_sym: dict[str, list[dict]] = {}
        for it in items:
            if it.published is None or not it.symbol or not it.title:
                continue
            by_sym.setdefault(it.symbol.upper(), []).append(item_to_record(it))
        added = 0
        for sym, recs in by_sym.items():
            current = self._read(sym)
            seen = {_key(r) for r in current}
            fresh = []
            for r in recs:
                k = _key(r)
                if k not in seen:
                    seen.add(k)
                    fresh.append(r)
            if not fresh:
                continue
            merged = sorted(current + fresh, key=lambda r: r["ts"])
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = self.path(sym).with_suffix(".jsonl.tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                for r in merged:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            tmp.replace(self.path(sym))
            self._mem[sym] = merged
            added += len(fresh)
        return added

    def items(self, symbol: str) -> list[NewsItem]:
        return [record_to_item(r) for r in self._read(symbol)]

    def symbols(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(p.stem.replace("_", "^") for p in self.root.glob("*.jsonl"))

    def stats(self) -> list[SymbolStats]:
        out = []
        for sym in self.symbols():
            recs = self._read(sym)
            feeds: dict[str, int] = {}
            for r in recs:
                feeds[r.get("feed", "?")] = feeds.get(r.get("feed", "?"), 0) + 1
            out.append(SymbolStats(sym, len(recs), _parse_time(recs[0]["ts"]) if recs else None,
                                   _parse_time(recs[-1]["ts"]) if recs else None, feeds))
        return out

    def remove(self, symbol: str, feed: str | None = None) -> int:
        """Delete a symbol's items (all of them, or only those from one feed)."""
        recs = self._read(symbol)
        keep = [] if feed is None else [r for r in recs if r.get("feed") != feed]
        removed = len(recs) - len(keep)
        p = self.path(symbol)
        if not keep:
            p.unlink(missing_ok=True)
        elif removed:
            with p.open("w", encoding="utf-8") as fh:
                for r in keep:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        self._mem[symbol.upper()] = keep
        return removed


# --------------------------------------------------------------------- CSV import
def _pick(row: dict, names: tuple[str, ...]) -> str:
    for n in names:
        if n in row and row[n] not in (None, ""):
            return str(row[n])
    return ""


def read_csv(path: str | Path, default_source: str | None = None, assume_tz: str = "UTC",
             feed: str = "import") -> list[NewsItem]:
    """Read a news CSV. Required columns: a timestamp, ``symbol`` and ``headline``.

    Timestamps may be ISO 8601 (``2024-05-02T16:31:00Z``, ``2024-05-02 12:31:00-04:00``) or RFC 2822.
    Times without a zone are read in ``assume_tz`` (``UTC`` by default; pass ``America/New_York``
    for exchange-time exports). ``symbol`` may hold several tickers separated by ``,``, ``;`` or ``|``.
    """
    tz: ZoneInfo | timezone = timezone.utc if assume_tz.upper() == "UTC" else ZoneInfo(assume_tz)
    p = Path(path).expanduser()
    out: list[NewsItem] = []
    with p.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        cols = {c.strip().lower() for c in (reader.fieldnames or [])}
        if not (cols & set(_TS_ALIASES)) or "symbol" not in cols or not (cols & set(_HEADLINE_ALIASES)):
            raise ValueError(f"{p.name}: needs a timestamp column, 'symbol' and 'headline' (found: {sorted(cols)})")
        for n, raw in enumerate(reader, start=2):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
            ts = _parse_time(_pick(row, _TS_ALIASES), tz)
            title = _pick(row, _HEADLINE_ALIASES)
            if ts is None or not title:
                log.info("%s line %d: no usable timestamp or headline, skipped", p.name, n)
                continue
            senti = _pick(row, ("sentiment",))
            try:
                s_val = max(-1.0, min(1.0, float(senti))) if senti else None
            except ValueError:
                s_val = None
            source = _pick(row, _SOURCE_ALIASES) or default_source or p.stem
            for sym in re.split(r"[,;| ]+", row.get("symbol", "")):
                if sym:
                    out.append(NewsItem(title=title, url=_pick(row, ("url", "link")), publisher=source,
                                        feed=_pick(row, ("feed",)) or feed, published=ts, symbol=sym.upper(),
                                        sentiment=s_val))
    return out


# ------------------------------------------------------------- point-in-time view
def decision_time(day: date, close: time = time(16, 0)) -> datetime:
    """The moment a backtest decides for ``day``: the New York close, in UTC."""
    return datetime.combine(day, close, NY).astimezone(timezone.utc)


class PointInTimeNews:
    """What the news looked like at the close of a given day, using only items published before it.

    A backtest decides at the close of day t and fills on t+1, so an item stamped after
    16:00 New York time on day t is first visible on t+1.
    """

    def __init__(self, archive: NewsArchive, cfg: NewsConfig | None = None, symbols: Iterable[str] | None = None):
        self.cfg = cfg or NewsConfig()
        self.archive = archive
        self._items: dict[str, list[NewsItem]] = {}
        self._times: dict[str, list[float]] = {}
        for sym in symbols if symbols is not None else archive.symbols():
            items = [i for i in archive.items(sym) if i.published is not None]
            if items:
                self._items[sym.upper()] = items
                self._times[sym.upper()] = [i.published.timestamp() for i in items]

    @property
    def symbols(self) -> list[str]:
        return sorted(self._items)

    @property
    def total(self) -> int:
        return sum(len(v) for v in self._items.values())

    def feeds(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for items in self._items.values():
            for i in items:
                out[i.feed] = out.get(i.feed, 0) + 1
        return out

    def visible(self, symbol: str, cutoff: datetime) -> list[NewsItem]:
        """Items published in the lookback window that ends at ``cutoff`` (inclusive)."""
        sym = symbol.upper()
        times = self._times.get(sym)
        if not times:
            return []
        lo = bisect.bisect_left(times, (cutoff - timedelta(hours=self.cfg.lookback_hours)).timestamp())
        hi = bisect.bisect_right(times, cutoff.timestamp())
        return self._items[sym][lo:hi]

    def report(self, symbol: str, day: date) -> NewsReport | None:
        cutoff = decision_time(day)
        items = self.visible(symbol, cutoff)
        if not items:
            return None
        return analyze_news(symbol, items, self.cfg, now=cutoff, sources={"archive": f"{len(items)} items"})
