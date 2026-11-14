"""A local, point-in-time news archive for backtests.

Live trading reads news from the web as it happens. A backtest has to replay
what was known on each past day, which needs every item's publication time.
This module keeps such an archive on your own disk (``~/.riskaware/news`` by
default, never inside the repository) and answers one question for the
backtester: "what had been published about SYMBOL by the close of DAY?"

The archive is one JSON Lines file per symbol. Items come from:

* the headline files of the research periods (2020, 2022 and 2026 headlines for the
  stocks of the built-in watchlists: licensed headlines, titles only; for 2026 also
  SEC 8-K filings), read-only, when you have them: they are licensed and are not
  distributed with the package, see ``riskaware.news.files`` and ``default_bundles``;
* SEC EDGAR 8-K filings (``riskaware news-history build``), public and stamped
  with the time SEC accepted them;
* your own Benzinga API key (``--source benzinga``), kept local;
* any CSV you have (``riskaware news-history import``), see ``CSV_COLUMNS``.

Backtests use the same clustering and cross-source verification as live
trading, so a story still needs two independent publishers or an SEC filing
before it counts as confirmed.

No look-ahead: for day t the backtest decides at that day's close (16:00 New
York, 13:00 on early-close days) and only sees items published strictly before
it; orders fill on t+1. A timestamp that could be read two ways is read the
later way: a date without a time counts as the end of that day, and a time
without a zone as New York time. Items dated in the future are dropped.
"""

from __future__ import annotations

import bisect
import csv
import gzip
import json
import logging
import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from .. import marketcal as mc
from ..config import RISKAWARE_HOME, NewsConfig
from ..types import NewsItem
from .files import PACKAGE_NEWS_DIR, PERIOD_FILES, news_file
from .relevance import company_names
from .verify import NewsReport, analyze_news

log = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")
DEFAULT_ROOT = RISKAWARE_HOME / "news"
#: The package's own folder, where versions up to 0.7.2 shipped the headline files (still read, see ``news.files``).
BUNDLE_DIR = PACKAGE_NEWS_DIR
#: The 2026 headline file (titles only), read-only, where ``news.files`` finds it when this module is imported:
#: ``$RISKAWARE_NEWS_DIR``, then ``~/.riskaware/data/news``, then the package folder. The headlines are licensed and
#: are not distributed with the package; without a copy this path does not exist and nothing is read.
BUNDLE_PATH = news_file(PERIOD_FILES[-1])
#: The earlier years (2020, 2022), found the same way and read together with ``BUNDLE_PATH`` by default.
EARLIER_BUNDLES: tuple[Path, ...] = tuple(news_file(name) for name in PERIOD_FILES[:-1])


def default_bundles() -> list[Path]:
    """The headline files an archive reads besides your own items: every period's file you have."""
    return [*EARLIER_BUNDLES, BUNDLE_PATH]
#: An item stamped later than this after the moment it is added is bad data and is dropped.
MAX_FUTURE = timedelta(days=1)
_DATE_ONLY = re.compile(r"\d{4}-\d{2}-\d{2}")

#: Columns of the CSV import format. ``timestamp`` and ``symbol`` and ``headline`` are required.
CSV_COLUMNS = ("timestamp", "symbol", "headline", "source", "url", "sentiment")
_TS_ALIASES = ("timestamp", "timestamp_utc", "published", "datetime", "date", "time", "created")
_HEADLINE_ALIASES = ("headline", "title")
_SOURCE_ALIASES = ("source", "publisher")


def _parse_time(value: str, assume_tz: ZoneInfo | timezone = NY) -> datetime | None:
    """A UTC datetime, read so that a wrong guess can only make an item visible later, never earlier.

    A date without a time ("2026-03-05") is read as 23:59:59 New York time that day, so the item is first
    visible the next trading day. A time without a zone is read in ``assume_tz`` (New York by default: if the
    file was really in UTC, New York time only delays the item by a few hours).
    """
    value = (value or "").strip()
    if not value:
        return None
    if _DATE_ONLY.fullmatch(value):
        try:
            return datetime.combine(date.fromisoformat(value), time(23, 59, 59), NY).astimezone(timezone.utc)
        except ValueError:
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
    bundled: int = 0  # items that come from the package, not from your own archive


@lru_cache(maxsize=4)
def _load_bundle(path: str, mtime: float) -> dict[str, tuple[dict, ...]]:
    out: dict[str, list[dict]] = {}
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rec = json.loads(line)
                out.setdefault(rec["symbol"], []).append(rec)
    return {sym: tuple(sorted(recs, key=lambda r: r["ts"])) for sym, recs in out.items()}


def load_bundle(path: str | Path | None = None) -> dict[str, tuple[dict, ...]]:
    """The headlines of one headline file (the 2026 one by default), by symbol, oldest first (empty if missing)."""
    p = Path(path) if path is not None else BUNDLE_PATH
    if not p.is_file():
        return {}
    return _load_bundle(str(p), p.stat().st_mtime)


@lru_cache(maxsize=4)
def _merge_bundles(files: tuple[tuple[str, float], ...]) -> dict[str, tuple[dict, ...]]:
    out: dict[str, list[dict]] = {}
    for path, mtime in files:
        for sym, recs in _load_bundle(path, mtime).items():
            out.setdefault(sym, []).extend(recs)
    return {sym: tuple(sorted(recs, key=lambda r: r["ts"])) for sym, recs in out.items()}


def load_bundles(paths: Iterable[str | Path]) -> dict[str, tuple[dict, ...]]:
    """Several bundled files together, by symbol, oldest first (missing files are skipped)."""
    files = tuple((str(p), p.stat().st_mtime) for p in map(Path, paths) if p.is_file())
    if len(files) == 1:
        return _load_bundle(*files[0])
    return _merge_bundles(files) if files else {}


class NewsArchive:
    """One JSON Lines file per symbol, sorted by publication time, de-duplicated.

    Reads also include the headline files of the research periods when you have them (``bundled=True``, see
    ``news.files``); writes and deletes only touch your own files.
    """

    def __init__(self, root: str | Path | None = None, bundled: bool = True, bundle_path: str | Path | None = None,
                 bundle_paths: Iterable[str | Path] | None = None):
        self.root = Path(root).expanduser() if root else DEFAULT_ROOT
        self.bundled = bundled
        if bundle_paths is not None:
            self.bundle_paths = [Path(p) for p in bundle_paths]
        else:
            self.bundle_paths = [Path(bundle_path)] if bundle_path else default_bundles()
        self.bundle_path = self.bundle_paths[-1] if self.bundle_paths else BUNDLE_PATH
        self._mem: dict[str, list[dict]] = {}

    def path(self, symbol: str) -> Path:
        return self.root / f"{_safe_name(symbol)}.jsonl"

    def bundle(self) -> dict[str, tuple[dict, ...]]:
        return load_bundles(self.bundle_paths) if self.bundled else {}

    def records(self, symbol: str) -> list[dict]:
        """Your records and the bundled ones for a symbol, oldest first; your copy of an item wins."""
        local = self._read(symbol)
        extra = self.bundle().get(symbol.upper(), ())
        if not extra:
            return list(local)
        seen = {_key(r) for r in local}
        return sorted([*local, *(r for r in extra if _key(r) not in seen)], key=lambda r: r["ts"])

    def bundle_info(self) -> dict | None:
        """What the headline files hold: item and symbol counts, first and last item, items per feed (None: no file)."""
        b = self.bundle()
        if not b:
            return None
        recs = [r for v in b.values() for r in v]
        return {"path": ", ".join(str(p) for p in self.bundle_paths if p.is_file()), "items": len(recs),
                "symbols": len(b),
                "first": _parse_time(min(r["ts"] for r in recs)), "last": _parse_time(max(r["ts"] for r in recs)),
                "feeds": dict(Counter(r.get("feed", "?") for r in recs))}

    def _read(self, symbol: str) -> list[dict]:
        """Your own records (the local file only)."""
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
        limit = datetime.now(timezone.utc) + MAX_FUTURE
        future = 0
        for it in items:
            if it.published is None or not it.symbol or not it.title:
                continue
            if it.published > limit:  # a timestamp in the future is an error in the source
                future += 1
                continue
            by_sym.setdefault(it.symbol.upper(), []).append(item_to_record(it))
        if future:
            log.warning("skipped %d items dated in the future", future)
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
        return [record_to_item(r) for r in self.records(symbol)]

    def local_symbols(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(p.stem.replace("_", "^") for p in self.root.glob("*.jsonl"))

    def symbols(self) -> list[str]:
        return sorted(set(self.local_symbols()) | set(self.bundle()))

    def stats(self) -> list[SymbolStats]:
        out = []
        for sym in self.symbols():
            recs = self.records(sym)
            feeds: dict[str, int] = {}
            for r in recs:
                feeds[r.get("feed", "?")] = feeds.get(r.get("feed", "?"), 0) + 1
            out.append(SymbolStats(sym, len(recs), _parse_time(recs[0]["ts"]) if recs else None,
                                   _parse_time(recs[-1]["ts"]) if recs else None, feeds,
                                   bundled=len(recs) - len(self._read(sym))))
        return out

    def remove(self, symbol: str, feed: str | None = None) -> int:
        """Delete a symbol's items from your archive (all of them, or one feed); bundled items stay."""
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


def read_csv(path: str | Path, default_source: str | None = None, assume_tz: str = "America/New_York",
             feed: str = "import") -> list[NewsItem]:
    """Read a news CSV. Required columns: a timestamp, ``symbol`` and ``headline``.

    Timestamps may be ISO 8601 (``2024-05-02T16:31:00Z``, ``2024-05-02 12:31:00-04:00``) or RFC 2822.
    Times without a zone are read in ``assume_tz`` (New York by default, the safe guess: a UTC time read as
    New York time only shows up later, while a New York time read as UTC would show up hours early). A date
    without a time counts as the end of that day. ``symbol`` may hold several tickers separated by ``,``,
    ``;`` or ``|``.
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
def decision_time(day: date, close: time | None = None) -> datetime:
    """The moment a backtest decides for ``day``: that day's New York close (13:00 on early-close days), in UTC."""
    if close is None:
        close = time(13, 0) if mc.early_close(day) else time(16, 0)
    return datetime.combine(day, close, NY).astimezone(timezone.utc)


class PointInTimeNews:
    """What the news looked like at the close of a given day, using only items published before it.

    A backtest decides at the close of day t and fills on t+1, so an item stamped at or after the close
    (16:00 New York time, 13:00 on early-close days) is first visible on t+1. Stories go through the same
    relevance filter as live trading (``news.relevance_filter``): ``names(symbol)`` gives the company names to
    look for; by default only the built-in aliases are used, so a backtest needs no network.
    """

    def __init__(self, archive: NewsArchive, cfg: NewsConfig | None = None, symbols: Iterable[str] | None = None,
                 names: Callable[[str], list[str]] | None = None):
        self.cfg = cfg or NewsConfig()
        self.archive = archive
        self._items: dict[str, list[NewsItem]] = {}
        self._times: dict[str, list[float]] = {}
        self._names: dict[str, list[str]] = {}
        for sym in symbols if symbols is not None else archive.symbols():
            items = sorted((i for i in archive.items(sym) if i.published is not None), key=lambda i: i.published)
            if items:
                key = sym.upper()
                self._items[key] = items
                self._times[key] = [i.published.timestamp() for i in items]
                self._names[key] = names(key) if names else company_names(key, fetch=False)

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

    def coverage(self) -> tuple[datetime | None, datetime | None]:
        """The first and the last publication time among the loaded items."""
        firsts = [v[0] for v in self._times.values() if v]
        lasts = [v[-1] for v in self._times.values() if v]
        if not firsts:
            return None, None
        return (datetime.fromtimestamp(min(firsts), timezone.utc), datetime.fromtimestamp(max(lasts), timezone.utc))

    def visible(self, symbol: str, cutoff: datetime) -> list[NewsItem]:
        """Items published in the lookback window that ends at ``cutoff``, strictly before ``cutoff``."""
        sym = symbol.upper()
        times = self._times.get(sym)
        if not times:
            return []
        lo = bisect.bisect_left(times, (cutoff - timedelta(hours=self.cfg.lookback_hours)).timestamp())
        hi = bisect.bisect_left(times, cutoff.timestamp())  # an item stamped exactly at the close is after it
        return list(self._items[sym][lo:hi])

    def report(self, symbol: str, day: date) -> NewsReport | None:
        cutoff = decision_time(day)
        items = self.visible(symbol, cutoff)
        if not items:
            return None
        return analyze_news(symbol, items, self.cfg, now=cutoff, sources={"archive": f"{len(items)} items"},
                            names=self._names.get(symbol.upper()))
