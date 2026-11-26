"""Open news: sources whose terms let anyone keep and republish what they publish, collected point in time.

``riskaware news-history collect`` reads them and appends every new item to one JSON Lines file per source and day;
the public dataset (`open-market-news`, updated hourly by GitHub Actions) is that folder. ``riskaware news-history
pull`` reads the dataset back into the local archive for backtests.

Sources and why they may be republished
---------------------------------------
* US federal agencies, public domain under 17 U.S.C. 105: the SEC ("may be copied or further distributed ... without
  the SEC's permission"), the Federal Reserve Board, the FDA, the BEA, the BLS and the CFTC. Each asks to be credited.
  The SEC 8-K item is built from EDGAR's filing index (company, form, item numbers, time), facts rather than text.
* GDELT, an open dataset: "You may redistribute, rehost, republish, and mirror any of the GDELT datasets in any form",
  with a citation and a link to https://www.gdeltproject.org/. Kept: the title, link, time and tone of articles on
  business and finance sites that name a stock in RiskAware's lists or the market as a whole.

Every row has ``published`` (the time the source gives) and ``seen`` (when it was first collected; for GDELT, the time
of the 15-minute batch that listed it). Backtests use ``seen``, the later of the two, so a row never shows up before it
could have been read. Commercial feeds (WSJ, Yahoo, Reuters, newswires) do not allow republication and are not here.
"""

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import logging
import re
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from ..types import NewsItem
from .history_sources import filing_headline
from .relevance import ALIASES, mentions

log = logging.getLogger(__name__)

DATASET_REPO = "jackson-cmd/open-market-news"
KEEP_DAYS = 45            # an RSS item older than this is not collected (it was collected before, or is stale)
SUMMARY_CHARS = 400
GDELT_LAST = "http://data.gdeltproject.org/gdeltv2/lastupdate.txt"
GDELT_FILE = "http://data.gdeltproject.org/gdeltv2/{stamp}.gkg.csv.zip"


@dataclass(frozen=True)
class Feed:
    name: str
    publisher: str
    urls: tuple[str, ...]
    kind: str = "rss"                 # rss | sec8k | gdelt
    credit: str = ""                  # the line the source asks for, shown in the dataset README
    terms: str = ""                   # where the source says it may be republished
    contact: bool = False             # the agency asks for a User-Agent with a contact email


SEC_TERMS = "https://www.sec.gov/privacy"
FEEDS: dict[str, Feed] = {f.name: f for f in (
    Feed("sec-press", "U.S. Securities and Exchange Commission", ("https://www.sec.gov/news/pressreleases.rss",),
         credit="Source: U.S. Securities and Exchange Commission", terms=SEC_TERMS, contact=True),
    Feed("sec-enforcement", "U.S. Securities and Exchange Commission", (
        "https://www.sec.gov/enforcement-litigation/litigation-releases/rss",
        "https://www.sec.gov/enforcement-litigation/administrative-proceedings/rss",
        "https://www.sec.gov/enforcement-litigation/trading-suspensions/rss"),
         credit="Source: U.S. Securities and Exchange Commission", terms=SEC_TERMS, contact=True),
    Feed("sec-8k", "SEC EDGAR", (
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&company=&dateb=&owner=include"
        "&start={start}&count=100&output=atom",), kind="sec8k",
         credit="Source: U.S. Securities and Exchange Commission (EDGAR)", terms=SEC_TERMS, contact=True),
    Feed("fed", "Federal Reserve Board", ("https://www.federalreserve.gov/feeds/press_all.xml",),
         credit="Source: Board of Governors of the Federal Reserve System",
         terms="https://www.federalreserve.gov/disclaimer.htm"),
    Feed("fda", "U.S. Food and Drug Administration", (
        "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml",),
         credit="Source: U.S. Food and Drug Administration",
         terms="https://www.fda.gov/about-fda/about-website/website-policies"),
    Feed("bea", "U.S. Bureau of Economic Analysis", ("https://apps.bea.gov/rss/rss.xml",),
         credit="Source: U.S. Bureau of Economic Analysis",
         terms="https://www.bea.gov/help/faq/is-permission-needed-reproduce-information-bea-web-site"),
    Feed("bls", "U.S. Bureau of Labor Statistics", tuple(
        f"https://www.bls.gov/feed/{n}.rss" for n in ("empsit", "cpi", "ppi", "jolts", "eci", "realer", "prod2")),
         credit="Source: U.S. Bureau of Labor Statistics",
         terms="https://www.bls.gov/opub/copyright-information.htm", contact=True),
    Feed("cftc", "Commodity Futures Trading Commission", (
        "https://www.cftc.gov/RSS/RSSGP/rssgp.xml", "https://www.cftc.gov/RSS/RSSENF/rssenf.xml"),
         credit="Source: U.S. Commodity Futures Trading Commission", terms="https://www.cftc.gov/About/terms.html"),
    Feed("gdelt", "GDELT", (GDELT_LAST,), kind="gdelt",
         credit="The GDELT Project, https://www.gdeltproject.org/", terms="https://www.gdeltproject.org/about.html"),
)}

#: Business and finance sites whose articles the GDELT part keeps (host or a parent domain).
FINANCE_SITES = (
    "reuters.com", "cnbc.com", "marketwatch.com", "wsj.com", "barrons.com", "bloomberg.com", "ft.com",
    "finance.yahoo.com", "investing.com", "seekingalpha.com", "fool.com", "investors.com", "thestreet.com",
    "benzinga.com", "zacks.com", "nasdaq.com", "morningstar.com", "kiplinger.com", "foxbusiness.com",
    "businessinsider.com", "forbes.com", "fortune.com", "axios.com", "apnews.com", "cnn.com", "nytimes.com",
    "washingtonpost.com", "latimes.com", "economist.com", "techcrunch.com", "theverge.com", "theinformation.com",
    "fiercebiotech.com", "fiercepharma.com", "biopharmadive.com", "statnews.com", "endpts.com", "retaildive.com",
    "supplychaindive.com", "bankingdive.com", "americanbanker.com", "freightwaves.com", "electrek.co",
    "tomshardware.com", "semianalysis.com", "digitimes.com", "nikkei.com", "scmp.com", "theglobeandmail.com",
)
_MARKET = re.compile(
    r"\b(?:stocks?|stock market|S&P 500|Wall Street|Nasdaq|Federal Reserve|the Fed|Fed's|Treasury yields?|"
    r"bond yields?|inflation|CPI|jobs report|payrolls|unemployment rate|recession|rate cuts?|rate hikes?|"
    r"interest rates?|tariffs?|VIX|selloff|sell-off|bear market|bull market|correction)\b", re.I)
_DOW = re.compile(r"\bDow\b")


# --------------------------------------------------------------------------- rows
def _utc(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def parse_time(value: str | None) -> datetime | None:
    v = (value or "").strip()
    if not v:
        return None
    try:
        dt = parsedate_to_datetime(v)
    except (TypeError, ValueError, IndexError):
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def row_id(source: str, key: str) -> str:
    return hashlib.sha1(f"{source}|{key}".encode()).hexdigest()[:16]


def _clean(text: str | None) -> str:
    t = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", t).strip()


@lru_cache(maxsize=4096)
def _strict(symbol: str) -> re.Pattern:
    """A ticker written as a ticker: $RTX, (RTX), NYSE: RTX or "RTX stock", so a product called "RTX 5060" is not
    Raytheon. Bare tickers are left to the names, which headlines use far more often."""
    sym = symbol.upper()
    alt = "|".join(re.escape(f) for f in sorted({sym, sym.replace("-", "."), sym.replace("-", "/")}, key=len,
                                                 reverse=True))
    forms = [rf"\$(?:{alt})(?![A-Za-z0-9])", rf"\((?:{alt})\)",
             rf"\b(?:NYSE|NASDAQ|Nasdaq|AMEX|NYSEARCA)\s?:\s?(?:{alt})(?![A-Za-z0-9])"]
    if len(sym.replace("-", "")) > 2:
        forms.append(rf"(?<![A-Za-z0-9$])(?:{alt})\s+(?:stock|shares)\b")
    return re.compile("|".join(forms))


def tag_symbols(text: str, symbols: Iterable[str] | None = None) -> list[str]:
    """Stocks a headline names, by the names people write (relevance.ALIASES) or a ticker written as one."""
    syms = list(symbols) if symbols is not None else list(ALIASES)
    out = []
    for s in syms:
        names = ALIASES.get(s, [])
        if _strict(s).search(text or "") or (names and mentions(text, "\x00", names)):
            out.append(s)
    return sorted(out)


def is_market(text: str) -> bool:
    return bool(_MARKET.search(text or "") or _DOW.search(text or ""))


@dataclass
class Row:
    source: str
    publisher: str
    title: str
    url: str
    published: datetime | None
    seen: datetime
    key: str
    symbols: list[str] = field(default_factory=list)
    summary: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return row_id(self.source, self.key)

    def to_json(self) -> dict:
        d = {"id": self.id, "source": self.source, "publisher": self.publisher, "title": self.title, "url": self.url,
             "published": _utc(self.published), "seen": _utc(self.seen), "symbols": self.symbols}
        if self.summary:
            d["summary"] = self.summary
        d.update(self.extra)
        return d


# --------------------------------------------------------------------------- parsers
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el, name: str) -> str:
    for c in el:
        if _local(c.tag) == name:
            if name == "link" and c.get("href"):
                return c.get("href")
            return (c.text or "").strip()
    return ""


def parse_rss(xml: bytes | str, feed: Feed, seen: datetime) -> list[Row]:
    """RSS 2.0 or Atom items as rows (the title, link, time and a short summary)."""
    root = ET.fromstring(xml)
    out: list[Row] = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        title = _clean(_child(el, "title"))
        if not title:
            continue
        url = _child(el, "link")
        guid = _child(el, "guid") or _child(el, "id") or url or title
        when = parse_time(_child(el, "pubDate") or _child(el, "published") or _child(el, "updated")
                          or _child(el, "date"))
        summary = _clean(_child(el, "description") or _child(el, "summary"))[:SUMMARY_CHARS]
        text = f"{title}. {summary}"
        out.append(Row(feed.name, feed.publisher, title, url, when, seen, guid, tag_symbols(text), summary))
    return out


_8K = re.compile(r"^8-K(?:/A)?\s+-\s+(?P<name>.+?)\s+\((?P<cik>\d{10})\)\s+\((?P<role>[^)]*)\)\s*$")


def parse_sec_current(xml: bytes | str, feed: Feed, seen: datetime, tickers: dict[int, str]) -> list[Row]:
    """EDGAR's current-filings Atom feed for 8-Ks: company, items and acceptance time."""
    root = ET.fromstring(xml)
    out: list[Row] = []
    for el in root.iter():
        if _local(el.tag) != "entry":
            continue
        m = _8K.match(_child(el, "title"))
        if not m:
            continue
        summary = _clean(_child(el, "summary"))
        acc = re.search(r"AccNo:\s*([\d-]+)", summary)
        key = acc.group(1) if acc else _child(el, "id")
        codes = re.findall(r"Item (\d+\.\d+)", summary)
        cik = int(m.group("cik"))
        sym = tickers.get(cik)
        name = m.group("name").strip()
        title = f"{name}: {filing_headline(','.join(codes))}"
        out.append(Row(feed.name, feed.publisher, title, _child(el, "link"), parse_time(_child(el, "updated")), seen,
                       key, [sym] if sym else [], "",
                       {"cik": cik, "form": "8-K/A" if _child(el, "title").startswith("8-K/A") else "8-K",
                        "items": codes, "company": name}))
    return out


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def finance_site(url: str) -> bool:
    host = _host(url)
    if host == "yahoo.com" or host.endswith(".yahoo.com"):
        return host == "finance.yahoo.com" or urlparse(url).path.startswith("/finance")
    return any(host == s or host.endswith("." + s) for s in FINANCE_SITES)


_TITLE = re.compile(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>", re.S)


def parse_gkg(data: bytes, feed: Feed, symbols: Iterable[str] | None = None) -> list[Row]:
    """One GDELT 2.0 GKG batch (the unzipped CSV): articles on finance sites that name a stock or the market."""
    syms = list(symbols) if symbols is not None else None
    csv.field_size_limit(1 << 30)
    out: list[Row] = []
    for r in csv.reader(io.StringIO(data.decode("utf-8", "replace")), delimiter="\t"):
        if len(r) < 27:
            continue
        url = r[4]
        if not url.startswith("http") or not finance_site(url):
            continue
        m = _TITLE.search(r[26])
        title = _clean(m.group(1)) if m else ""
        if not title:
            continue
        tagged = tag_symbols(title, syms)
        market = is_market(title)
        if not tagged and not market:
            continue
        try:
            when = datetime.strptime(r[1], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        try:
            tone = round(float(r[15].split(",")[0]), 2)
        except (ValueError, IndexError):
            tone = None
        extra = {"site": _host(url), "tone": tone}
        if market:
            extra["market"] = True
        out.append(Row(feed.name, _host(url), title, url, when, when, url, tagged, "", extra))
    return out


# --------------------------------------------------------------------------- the store
class Store:
    """<root>/data/<source>/<YYYY>/<YYYY-MM-DD>.jsonl (UTC day of ``seen``) and <root>/state.json."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def path(self, source: str, day: date) -> Path:
        return self.root / "data" / source / f"{day:%Y}" / f"{day:%Y-%m-%d}.jsonl"

    def ids(self, source: str, days: Iterable[date]) -> set[str]:
        out: set[str] = set()
        for d in days:
            p = self.path(source, d)
            if p.exists():
                for line in p.read_text(encoding="utf-8").splitlines():
                    try:
                        out.add(json.loads(line)["id"])
                    except (ValueError, KeyError):
                        continue
        return out

    def add(self, rows: list[Row], lookback_days: int) -> int:
        """Append rows not stored in the last ``lookback_days`` days. Returns how many were new."""
        if not rows:
            return 0
        by_src: dict[str, list[Row]] = {}
        for r in rows:
            by_src.setdefault(r.source, []).append(r)
        added = 0
        for src, rs in by_src.items():
            last = max(r.seen for r in rs).date()
            known = self.ids(src, (last - timedelta(days=i) for i in range(lookback_days + 1)))
            fresh: dict[date, list[Row]] = {}
            for r in sorted(rs, key=lambda r: (r.seen, r.published or r.seen)):
                if r.id in known:
                    continue
                known.add(r.id)
                fresh.setdefault(r.seen.date(), []).append(r)
            for day, batch in fresh.items():
                p = self.path(src, day)
                p.parent.mkdir(parents=True, exist_ok=True)
                with p.open("a", encoding="utf-8") as fh:
                    for r in batch:
                        fh.write(json.dumps(r.to_json(), ensure_ascii=False, separators=(",", ":")) + "\n")
                added += len(batch)
        return added

    def state(self) -> dict:
        try:
            data = json.loads((self.root / "state.json").read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_state(self, data: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "state.json").write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    def rows(self, start: date, end: date, sources: Iterable[str] | None = None) -> Iterator[dict]:
        data = self.root / "data"
        names = list(sources) if sources else sorted(p.name for p in data.iterdir() if p.is_dir()) \
            if data.exists() else []
        d = start
        while d <= end:
            for src in names:
                p = self.path(src, d)
                if p.exists():
                    for line in p.read_text(encoding="utf-8").splitlines():
                        try:
                            yield json.loads(line)
                        except ValueError:
                            continue
            d += timedelta(days=1)


# --------------------------------------------------------------------------- collecting
class Collector:
    def __init__(self, store: Store, contact: str | None = None, timeout: float = 30.0, session=None,
                 now: datetime | None = None):
        from ..utils import BROWSER_UA, make_session, tool_user_agent

        self.store = store
        self.contact = contact
        self.timeout = timeout
        self.now = now or datetime.now(timezone.utc).replace(microsecond=0)
        self.web = session or make_session(BROWSER_UA, retries=2)
        self.gov = session or make_session(tool_user_agent(contact) if contact else tool_user_agent(), retries=2)
        self._tickers: dict[int, str] | None = None

    def _get(self, url: str, gov: bool) -> bytes:
        import time as _time

        if gov:
            _time.sleep(0.15)   # SEC asks for at most 10 requests a second
        r = (self.gov if gov else self.web).get(url, timeout=self.timeout)
        r.raise_for_status()
        return r.content

    def tickers(self) -> dict[int, str]:
        """CIK -> ticker from SEC's company list (the first ticker of each company)."""
        if self._tickers is None:
            data = json.loads(self._get("https://www.sec.gov/files/company_tickers.json", True))
            out: dict[int, str] = {}
            for v in data.values():
                out.setdefault(int(v["cik_str"]), str(v["ticker"]).upper().replace(".", "-"))
            self._tickers = out
        return self._tickers

    def collect(self, names: Iterable[str], gdelt_batches: int = 8) -> dict[str, dict]:
        """Collect each source once; one failing source never stops the others. Returns {source: report}."""
        report: dict[str, dict] = {}
        state = self.store.state()
        for name in names:
            feed = FEEDS[name]
            try:
                if feed.contact and not self.contact:
                    raise RuntimeError("SEC and BLS ask for a contact email (--contact-email or RISKAWARE_CONTACT_EMAIL)")
                if feed.kind == "gdelt":
                    rows, state = self._gdelt(feed, state, gdelt_batches)
                    added = self.store.add(rows, 1)
                elif feed.kind == "sec8k":
                    rows = self._sec8k(feed)
                    added = self.store.add(rows, 3)
                else:
                    rows = []
                    for url in feed.urls:
                        rows += parse_rss(self._get(url, feed.contact), feed, self.now)
                    cutoff = self.now - timedelta(days=KEEP_DAYS)
                    rows = [r for r in rows if (r.published or self.now) >= cutoff]
                    added = self.store.add(rows, KEEP_DAYS)
                report[name] = {"read": len(rows), "new": added}
            except Exception as exc:   # noqa: BLE001 - a source may fail in any way; the others go on
                log.warning("%s failed: %s", name, exc)
                report[name] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
        state.setdefault("runs", {})
        for name, rep in report.items():
            state["runs"][name] = {"at": _utc(self.now), **rep}
        self.store.save_state(state)
        return report

    def _sec8k(self, feed: Feed) -> list[Row]:
        tickers = self.tickers()
        rows: list[Row] = []
        newest_known = self.store.ids(feed.name, (self.now.date() - timedelta(days=i) for i in range(4)))
        for start in range(0, 400, 100):
            page = parse_sec_current(self._get(feed.urls[0].format(start=start), True), feed, self.now, tickers)
            rows += page
            if not page or any(r.id in newest_known for r in page):
                break
        return rows

    def _gdelt(self, feed: Feed, state: dict, batches: int) -> tuple[list[Row], dict]:
        latest = None
        for line in self._get(GDELT_LAST, False).decode().splitlines():
            m = re.search(r"/(\d{14})\.gkg\.csv\.zip", line)
            if m:
                latest = datetime.strptime(m.group(1), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        if latest is None:
            raise RuntimeError("GDELT lastupdate.txt lists no GKG file")
        cursor = state.get("gdelt", {}).get("last")
        last = (datetime.strptime(cursor, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc) if cursor
                else latest - timedelta(hours=1))
        stamps = []
        t = last + timedelta(minutes=15)
        while t <= latest and len(stamps) < batches:
            stamps.append(t)
            t += timedelta(minutes=15)
        rows: list[Row] = []
        for t in stamps:
            rows += gdelt_batch(self, feed, t)
            last = t
        state = {**state, "gdelt": {"last": f"{last:%Y%m%d%H%M%S}"}}
        return rows, state


def gdelt_batch(col: Collector, feed: Feed, t: datetime, symbols: Iterable[str] | None = None) -> list[Row]:
    """One 15-minute GDELT batch (a missing batch is skipped, as GDELT sometimes leaves gaps)."""
    import requests

    url = GDELT_FILE.format(stamp=f"{t:%Y%m%d%H%M%S}")
    try:
        raw = col._get(url, False)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            log.info("GDELT has no batch %s", url)
            return []
        raise
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        return parse_gkg(z.read(z.namelist()[0]), feed, symbols)


def backfill_gdelt(store: Store, start: datetime, end: datetime, collector: Collector | None = None,
                   progress=None) -> int:
    """GDELT batches from ``start`` to ``end`` (UTC, both included), for periods before the hourly job ran."""
    col = collector or Collector(store)
    feed = FEEDS["gdelt"]
    t = start.replace(minute=start.minute - start.minute % 15, second=0, microsecond=0)
    added = 0
    while t <= end:
        rows = gdelt_batch(col, feed, t)
        added += store.add(rows, 0)
        if progress:
            progress(t, added)
        t += timedelta(minutes=15)
    return added


# --------------------------------------------------------------------------- back into the archive
def to_items(rows: Iterable[dict]) -> list[NewsItem]:
    """Rows that name a stock, as archive items dated when they were first seen (never earlier than readable)."""
    out: list[NewsItem] = []
    for r in rows:
        seen = parse_time(r.get("seen"))
        if seen is None:
            continue
        src = r.get("source", "open")
        for sym in r.get("symbols") or []:
            if src == "sec-8k":
                items = ",".join(r.get("items") or [])
                out.append(NewsItem(title=filing_headline(items), url=r.get("url", ""), publisher="SEC EDGAR",
                                    feed="sec", published=seen, symbol=sym, summary="items:" + items))
            else:
                out.append(NewsItem(title=r.get("title", ""), url=r.get("url", ""),
                                    publisher=r.get("publisher") or src, feed=f"open:{src}", published=seen,
                                    symbol=sym, summary=r.get("summary", "")))
    return out


def download_dataset(dest: str | Path, start: date, end: date, repo: str = DATASET_REPO, branch: str = "main",
                     sources: Iterable[str] | None = None, timeout: float = 120) -> Path:
    """The dataset's files for ``start``..``end`` from GitHub, streamed from one archive into ``dest``."""
    import tarfile

    import requests

    want = set(sources or [])
    url = f"https://codeload.github.com/{repo}/tar.gz/refs/heads/{branch}"
    dest = Path(dest)
    with requests.get(url, timeout=timeout, stream=True) as r:
        r.raise_for_status()
        r.raw.decode_content = True
        with tarfile.open(fileobj=r.raw, mode="r|gz") as tar:
            for m in tar:
                parts = Path(m.name).parts
                # <repo>-<branch>/data/<source>/<YYYY>/<YYYY-MM-DD>.jsonl
                if not m.isfile() or len(parts) != 5 or parts[1] != "data" or ".." in parts:
                    continue
                src, name = parts[2], parts[4]
                try:
                    day = date.fromisoformat(name.removesuffix(".jsonl"))
                except ValueError:
                    continue
                if (want and src not in want) or not (start <= day <= end):
                    continue
                target = dest / Path(*parts[1:])
                target.parent.mkdir(parents=True, exist_ok=True)
                fh = tar.extractfile(m)
                if fh is not None:
                    target.write_bytes(fh.read())
    return dest
