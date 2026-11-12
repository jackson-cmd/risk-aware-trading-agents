"""News sources: Finviz quote-page news (free) and RSS/Atom feeds.

Every source returns ``NewsItem`` objects with the original publisher name, so
the verifier can count independent publishers. Requests are rate limited and
cached for 30 minutes. Respect each site's terms of use; Finviz is scraped
from the public quote page at a polite pace.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Protocol
from zoneinfo import ZoneInfo

import feedparser
from bs4 import BeautifulSoup

from ..types import NewsItem
from ..utils import BROWSER_UA, DiskCache, RateLimiter, make_session, tool_user_agent

log = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")


class NewsSource(Protocol):
    name: str

    def fetch(self, symbol: str) -> list[NewsItem]: ...


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


class _HTTPSource:
    name = "base"
    ua = BROWSER_UA

    def __init__(self, limiter: RateLimiter | None = None, timeout: float = 15.0, user_agent: str | None = None):
        self.session = make_session(user_agent or self.ua, retries=1, backoff=0.5)
        self.limiter = limiter or RateLimiter(1.0)
        self.timeout = timeout

    def _get(self, url: str) -> bytes | None:
        self.limiter.wait(url)
        try:
            r = self.session.get(url, timeout=self.timeout)
        except Exception as exc:
            log.info("%s: request failed: %s", self.name, exc)
            return None
        if not r.ok:
            log.info("%s: HTTP %s for %s", self.name, r.status_code, url)
            return None
        return r.content


class FinvizNews(_HTTPSource):
    """Headlines from the news table on finviz.com/quote.ashx (free, no account)."""

    name = "finviz"
    URL = "https://finviz.com/quote.ashx?t={symbol}&p=d"

    def fetch(self, symbol: str) -> list[NewsItem]:
        content = self._get(self.URL.format(symbol=symbol.replace("-", ".")))
        if not content:
            return []
        return parse_finviz_news(content.decode("utf-8", errors="ignore"), symbol)


def parse_finviz_news(html: str, symbol: str, now: datetime | None = None) -> list[NewsItem]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find(id="news-table")
    if table is None:
        return []
    now = (now or datetime.now(timezone.utc)).astimezone(NY)
    current_day = now.date()
    items: list[NewsItem] = []
    for tr in table.find_all("tr"):
        link = tr.find("a", class_="tab-link-news")
        if link is None:
            continue
        cells = tr.find_all("td")
        stamp = cells[0].get_text(" ").strip() if cells else ""
        when = None
        m = re.match(r"(?:(Today)|([A-Z][a-z]{2}-\d{2}-\d{2}))?\s*(\d{1,2}:\d{2}[AP]M)", stamp)
        if m:
            if m.group(2):
                current_day = datetime.strptime(m.group(2), "%b-%d-%y").date()
            elif m.group(1):
                current_day = now.date()
            t = datetime.strptime(m.group(3), "%I:%M%p").time()
            when = datetime.combine(current_day, t, NY).astimezone(timezone.utc)
        source_el = tr.find("span")
        publisher = source_el.get_text(" ").strip().strip("()") if source_el else "finviz"
        items.append(NewsItem(
            title=_clean(link.get_text(" ")), url=link.get("href", ""), publisher=publisher, feed="finviz",
            published=when, symbol=symbol,
        ))
    return items


class _RSSSource(_HTTPSource):
    URL = ""
    default_publisher = ""

    def url(self, symbol: str) -> str:
        return self.URL.format(symbol=symbol)

    def fetch(self, symbol: str) -> list[NewsItem]:
        content = self._get(self.url(symbol))
        if not content:
            return []
        feed = feedparser.parse(content)
        out = []
        for e in feed.entries:
            item = self.to_item(e, symbol)
            if item is not None and item.title:
                out.append(item)
        return out

    def to_item(self, e, symbol: str) -> NewsItem | None:
        return NewsItem(
            title=_clean(e.get("title", "")), url=e.get("link", ""),
            publisher=e.get("author") or self.default_publisher, feed=self.name,
            published=_parse_dt(e.get("published") or e.get("updated")), symbol=symbol,
            summary=_clean(e.get("summary", ""))[:400],
        )


class GoogleNewsRSS(_RSSSource):
    name = "google"
    URL = "https://news.google.com/rss/search?q={symbol}+stock+when:3d&hl=en-US&gl=US&ceid=US:en"

    def to_item(self, e, symbol: str) -> NewsItem | None:
        title = _clean(e.get("title", ""))
        src = e.get("source") or {}
        publisher = src.get("title") if isinstance(src, dict) else None
        if publisher and title.endswith(f" - {publisher}"):
            title = title[: -len(publisher) - 3]
        elif " - " in title and not publisher:
            title, publisher = title.rsplit(" - ", 1)
        return NewsItem(title=title, url=e.get("link", ""), publisher=publisher or "google news", feed=self.name,
                        published=_parse_dt(e.get("published")), symbol=symbol)


class NasdaqRSS(_RSSSource):
    name = "nasdaq"
    URL = "https://www.nasdaq.com/feed/rssoutbound?symbol={symbol}"
    default_publisher = "nasdaq"


class SeekingAlphaRSS(_RSSSource):
    name = "seekingalpha"
    URL = "https://seekingalpha.com/api/sa/combined/{symbol}.xml"
    default_publisher = "seeking alpha"

    def to_item(self, e, symbol: str) -> NewsItem | None:
        item = super().to_item(e, symbol)
        if item and "/article/" in item.url:
            item.summary = "analysis " + item.summary  # long-form analysis, treated as opinion
        return item


class YahooRSS(_RSSSource):
    name = "yahoo"
    URL = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
    default_publisher = "yahoo finance"


class SecFilings(_RSSSource):
    """The company's own 8-K filings from SEC EDGAR: a primary source for material events.
    SEC asks automated clients to send a User-Agent with contact details (``data.contact_email``)."""

    name = "sec"
    URL = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={symbol}&type=8-K&dateb="
           "&owner=include&count=10&output=atom")

    def __init__(self, contact_email: str | None = None, **kw):
        super().__init__(user_agent=tool_user_agent(contact_email), **kw)

    def to_item(self, e, symbol: str) -> NewsItem | None:
        summary = _clean(e.get("summary", ""))
        items = re.findall(r"Item (\d\.\d{2}):?\s*([A-Za-z ,'/&-]+?)(?=Item \d\.\d{2}|$)", summary)
        filed = re.search(r"Filed:\s*(\d{4}-\d{2}-\d{2})", summary)
        published = _parse_dt(e.get("updated") or e.get("published"))
        if filed and published is None:
            published = datetime.fromisoformat(filed.group(1)).replace(tzinfo=timezone.utc)
        codes = [code for code, _ in items]
        text = "; ".join(f"Item {code} {desc.strip()}" for code, desc in items) or "Current report"
        return NewsItem(title=f"8-K: {text}", url=e.get("link", ""), publisher="SEC EDGAR", feed=self.name,
                        published=published, symbol=symbol, summary="items:" + ",".join(codes))


SOURCE_CLASSES = {
    "finviz": FinvizNews,
    "google": GoogleNewsRSS,
    "nasdaq": NasdaqRSS,
    "seekingalpha": SeekingAlphaRSS,
    "yahoo": YahooRSS,
    "sec": SecFilings,
}


class NewsCollector:
    """Runs the enabled sources for a symbol, with caching and per-host rate limits."""

    def __init__(self, sources: list[str], cache: DiskCache | None = None, contact_email: str | None = None,
                 request_interval: float = 1.0, ttl_minutes: float = 30.0):
        self.cache = cache
        self.ttl = ttl_minutes * 60
        limiter = RateLimiter(request_interval)
        self.sources = []
        for name in sources:
            cls = SOURCE_CLASSES.get(name)
            if cls is None:
                log.warning("unknown news source '%s' (known: %s)", name, ", ".join(SOURCE_CLASSES))
                continue
            if cls is SecFilings:
                self.sources.append(cls(contact_email=contact_email, limiter=limiter))
            else:
                self.sources.append(cls(limiter=limiter))
        self.last_status: dict[str, str] = {}

    def collect(self, symbol: str, lookback_hours: int = 72) -> list[NewsItem]:
        items: list[NewsItem] = []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        for src in self.sources:
            key = f"{src.name}:{symbol}"
            cached = self.cache.get_json("news", key, self.ttl) if self.cache else None
            if cached is not None:
                batch = [_item_from_dict(d) for d in cached]
                self.last_status[src.name] = f"cached ({len(batch)})"
            else:
                try:
                    batch = src.fetch(symbol)
                    self.last_status[src.name] = f"ok ({len(batch)})"
                except Exception as exc:  # a broken source must not stop the others
                    log.info("news source %s failed for %s: %s", src.name, symbol, exc)
                    batch = []
                    self.last_status[src.name] = "failed"
                if self.cache is not None and batch:
                    self.cache.set_json("news", key, [_item_to_dict(i) for i in batch])
            items.extend(i for i in batch if i.published is None or i.published >= cutoff)
        return items


def _item_to_dict(i: NewsItem) -> dict:
    return {"title": i.title, "url": i.url, "publisher": i.publisher, "feed": i.feed,
            "published": i.published.isoformat() if i.published else None, "symbol": i.symbol,
            "summary": i.summary}


def _item_from_dict(d: dict) -> NewsItem:
    pub = d.get("published")
    return NewsItem(title=d.get("title", ""), url=d.get("url", ""), publisher=d.get("publisher", ""),
                    feed=d.get("feed", ""), published=datetime.fromisoformat(pub) if pub else None,
                    symbol=d.get("symbol"), summary=d.get("summary", ""))
