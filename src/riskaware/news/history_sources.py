"""Historical news for the local archive: SEC EDGAR 8-K filings and your own Benzinga key.

SEC EDGAR
    Public filings with the time SEC accepted them. Free; SEC asks automated
    clients to send a contact email in the User-Agent (``data.contact_email``)
    and to stay under 10 requests a second. Requests without an email are
    refused with HTTP 403.

Benzinga
    Uses your own Benzinga news API key (``BENZINGA_API_KEY``). Headlines go to
    your local archive, outside the repository.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

from ..types import NewsItem
from ..utils import DiskCache, RateLimiter, make_session, tool_user_agent

log = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")

# Short titles of the Form 8-K items (from SEC's Form 8-K instructions)
ITEM_TITLES: dict[str, str] = {
    "1.01": "Material definitive agreement",
    "1.02": "Termination of a material agreement",
    "1.03": "Bankruptcy or receivership",
    "1.04": "Mine safety shutdown",
    "1.05": "Material cybersecurity incident",
    "2.01": "Completed acquisition or disposition",
    "2.02": "Results of operations (earnings)",
    "2.03": "New direct financial obligation",
    "2.04": "Accelerated financial obligation",
    "2.05": "Exit or disposal costs",
    "2.06": "Material impairment",
    "3.01": "Delisting or listing-rule notice",
    "3.02": "Unregistered sale of equity",
    "3.03": "Change to security holders' rights",
    "4.01": "Change of auditor",
    "4.02": "Non-reliance on prior financial statements",
    "5.01": "Change in control",
    "5.02": "Director or officer change",
    "5.03": "Charter or bylaw amendment",
    "5.04": "Benefit plan trading suspension",
    "5.05": "Code of ethics change",
    "5.06": "Shell company status change",
    "5.07": "Shareholder vote results",
    "5.08": "Shareholder director nominations",
    "7.01": "Regulation FD disclosure",
    "8.01": "Other events",
    "9.01": "Financial statements and exhibits",
}


class HistoryError(RuntimeError):
    """A source refused the request in a way the user has to fix (missing email, bad key)."""


def _ny_naive(value: str) -> datetime | None:
    """EDGAR's ``acceptanceDateTime`` (e.g. ``2024-08-01T16:30:42.000Z``).

    The value is read as New York time. EDGAR runs on Eastern time; if a value were
    really UTC, reading it as Eastern only makes the item visible later in a backtest,
    never earlier, so the replay stays free of look-ahead either way.
    """
    m = re.match(r"(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})", value or "")
    if not m:
        return None
    d = date.fromisoformat(m.group(1))
    t = time(int(m.group(2)), int(m.group(3)), int(m.group(4)))
    return datetime.combine(d, t, NY).astimezone(timezone.utc)


def filing_headline(items: str) -> str:
    codes = [c.strip() for c in (items or "").split(",") if c.strip()]
    named = [f"Item {c} {ITEM_TITLES[c]}" for c in codes if c in ITEM_TITLES and c != "9.01"]
    if not named and "9.01" in codes:
        named = ["Item 9.01 Financial statements and exhibits"]
    return "8-K: " + ("; ".join(named) if named else "Current report")


def parse_submissions(payload: dict, symbol: str, cik: int, start: date | None = None,
                      end: date | None = None, forms: tuple[str, ...] = ("8-K",)) -> list[NewsItem]:
    """8-K filings from an EDGAR submissions payload (``filings.recent`` or an older ``files`` page)."""
    block = payload.get("filings", {}).get("recent", payload)
    form = block.get("form", [])
    out: list[NewsItem] = []
    for i, f in enumerate(form):
        if f not in forms:
            continue
        filed = block["filingDate"][i]
        if start and filed < start.isoformat():
            continue
        if end and filed > end.isoformat():
            continue
        ts = _ny_naive(block.get("acceptanceDateTime", [""] * len(form))[i])
        if ts is None:  # no acceptance time: assume the end of the filing day
            ts = datetime.combine(date.fromisoformat(filed), time(23, 59), NY).astimezone(timezone.utc)
        items = block.get("items", [""] * len(form))[i] or ""
        acc = block["accessionNumber"][i]
        doc = block.get("primaryDocument", [""] * len(form))[i]
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}" if doc else \
            f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/"
        codes = ",".join(c.strip() for c in items.split(",") if c.strip())
        out.append(NewsItem(title=filing_headline(items), url=url, publisher="SEC EDGAR", feed="sec",
                            published=ts, symbol=symbol.upper(), summary="items:" + codes))
    return out


class SecFilingHistory:
    TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
    SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
    PAGE_URL = "https://data.sec.gov/submissions/{name}"

    def __init__(self, contact_email: str | None, cache: DiskCache | None = None, timeout: float = 20.0,
                 interval: float = 0.15):
        if not contact_email:
            raise HistoryError("SEC EDGAR asks automated tools for a contact email. Set data.contact_email in "
                               "riskaware.yaml or pass --contact-email.")
        self.session = make_session(tool_user_agent(contact_email), retries=2, backoff=1.0)
        self.limiter = RateLimiter(interval)
        self.cache = cache
        self.timeout = timeout
        self._ciks: dict[str, int] | None = None

    def _get_json(self, url: str):
        self.limiter.wait(url)
        r = self.session.get(url, timeout=self.timeout)
        if r.status_code == 403:
            raise HistoryError("SEC EDGAR refused the request (HTTP 403). Check data.contact_email and try again "
                               "later; SEC limits clients to 10 requests a second.")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def cik_map(self) -> dict[str, int]:
        if self._ciks is None:
            data = self.cache.get_json("sec", "company_tickers", 7 * 86400) if self.cache else None
            if data is None:
                data = self._get_json(self.TICKERS_URL) or {}
                if self.cache is not None and data:
                    self.cache.set_json("sec", "company_tickers", data)
            self._ciks = {str(v["ticker"]).upper(): int(v["cik_str"]) for v in data.values()}
        return self._ciks

    def cik(self, symbol: str) -> int | None:
        m = self.cik_map()
        sym = symbol.upper()
        return m.get(sym) or m.get(sym.replace(".", "-")) or m.get(sym.replace("-", "."))

    def fetch(self, symbol: str, start: date, end: date | None = None) -> list[NewsItem]:
        cik = self.cik(symbol)
        if cik is None:
            log.info("SEC: no CIK for %s (funds and ETFs are usually not in the company list)", symbol)
            return []
        payload = self._get_json(self.SUBMISSIONS_URL.format(cik=cik))
        if not payload:
            return []
        items = parse_submissions(payload, symbol, cik, start, end)
        for page in payload.get("filings", {}).get("files", []):
            if page.get("filingTo", "9999") < start.isoformat():
                continue
            older = self._get_json(self.PAGE_URL.format(name=page["name"]))
            if older:
                items.extend(parse_submissions(older, symbol, cik, start, end))
        return items


# ----------------------------------------------------------------------- Benzinga
def _bz_time(value: str) -> datetime | None:
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError, AttributeError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=NY)
    return dt.astimezone(timezone.utc)


def parse_benzinga(records: list[dict], symbol: str) -> list[NewsItem]:
    out = []
    for r in records or []:
        title = (r.get("title") or "").strip()
        ts = _bz_time(r.get("created") or "")
        if not title or ts is None:
            continue
        out.append(NewsItem(title=title, url=r.get("url", ""), publisher="benzinga", feed="benzinga",
                            published=ts, symbol=symbol.upper()))
    return out


class BenzingaHistory:
    """Benzinga's news API (``api.benzinga.com/api/v2/news``) with your own key, one month at a time."""

    URL = "https://api.benzinga.com/api/v2/news"

    def __init__(self, api_key: str | None, timeout: float = 20.0, interval: float = 0.5, page_size: int = 100,
                 max_pages: int = 50):
        if not api_key:
            raise HistoryError("No Benzinga API key. Put it in .env as BENZINGA_API_KEY (or the variable named by "
                               "news.benzinga_key_env).")
        self.key = api_key
        self.session = make_session(retries=2, backoff=1.0)
        self.session.headers["Accept"] = "application/json"
        self.limiter = RateLimiter(interval)
        self.timeout = timeout
        self.page_size = page_size
        self.max_pages = max_pages

    def _page(self, symbol: str, d0: date, d1: date, page: int) -> list[dict]:
        self.limiter.wait(self.URL)
        params = {"token": self.key, "tickers": symbol, "dateFrom": d0.isoformat(), "dateTo": d1.isoformat(),
                  "pageSize": self.page_size, "page": page, "displayOutput": "headline"}
        r = self.session.get(self.URL, params=params, timeout=self.timeout)
        if r.status_code in (401, 403):
            raise HistoryError(f"Benzinga refused the key (HTTP {r.status_code}); check BENZINGA_API_KEY and "
                               "that your plan includes the news API.")
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    def fetch(self, symbol: str, start: date, end: date | None = None) -> list[NewsItem]:
        end = end or date.today()
        out: list[NewsItem] = []
        d0 = start
        while d0 <= end:
            d1 = min(end, (d0.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1))
            for page in range(self.max_pages):
                batch = self._page(symbol, d0, d1, page)
                out.extend(parse_benzinga(batch, symbol))
                if len(batch) < self.page_size:
                    break
            else:
                log.warning("Benzinga: %s %s..%s hit the page limit; some items may be missing", symbol, d0, d1)
            d0 = d1 + timedelta(days=1)
        return out
