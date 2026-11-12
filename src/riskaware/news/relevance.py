"""Is a headline about this company?

Ticker feeds and searches also return stories about other companies and the market (a search for "COST stock"
finds every article about costs). A story only counts toward a symbol when its headline or summary names the
company or its ticker; SEC filings always count because they come from the company's own CIK. This keeps one
company's news, and its red flags, from being pinned on another.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache

from ..types import NewsItem

log = logging.getLogger(__name__)

# names people actually write in headlines, for every stock in the bundled lists
ALIASES: dict[str, list[str]] = {
    "AAPL": ["Apple", "iPhone"], "MSFT": ["Microsoft"], "NVDA": ["Nvidia"], "AVGO": ["Broadcom"],
    "ORCL": ["Oracle"], "ADBE": ["Adobe"], "CRM": ["Salesforce"], "AMD": ["Advanced Micro Devices"],
    "CSCO": ["Cisco"], "QCOM": ["Qualcomm"], "TXN": ["Texas Instruments"], "INTU": ["Intuit", "TurboTax"],
    "NOW": ["ServiceNow"], "GOOGL": ["Alphabet", "Google", "YouTube"], "GOOG": ["Alphabet", "Google", "YouTube"],
    "META": ["Meta Platforms", "Meta", "Facebook", "Instagram", "WhatsApp"], "NFLX": ["Netflix"],
    "DIS": ["Disney"], "TMUS": ["T-Mobile"], "AMZN": ["Amazon", "AWS"], "TSLA": ["Tesla"],
    "HD": ["Home Depot"], "MCD": ["McDonald's", "McDonalds"], "LOW": ["Lowe's", "Lowes"],
    "BKNG": ["Booking Holdings", "Booking.com"], "NKE": ["Nike"], "SBUX": ["Starbucks"], "COST": ["Costco"],
    "WMT": ["Walmart"], "PG": ["Procter & Gamble", "Procter and Gamble", "P&G"], "KO": ["Coca-Cola", "Coke"],
    "PEP": ["PepsiCo", "Pepsi"], "LLY": ["Eli Lilly", "Lilly"], "UNH": ["UnitedHealth", "Optum"],
    "JNJ": ["Johnson & Johnson", "Johnson and Johnson", "J&J"], "ABBV": ["AbbVie"], "MRK": ["Merck"],
    "TMO": ["Thermo Fisher"], "ISRG": ["Intuitive Surgical"], "JPM": ["JPMorgan", "JP Morgan", "J.P. Morgan"],
    "V": ["Visa"], "MA": ["Mastercard"], "BAC": ["Bank of America", "BofA"], "GS": ["Goldman Sachs", "Goldman"],
    "BRK-B": ["Berkshire Hathaway", "Berkshire"], "SPGI": ["S&P Global"], "CAT": ["Caterpillar"],
    "GE": ["GE Aerospace", "General Electric"], "HON": ["Honeywell"], "UNP": ["Union Pacific"],
    "RTX": ["Raytheon"], "DE": ["Deere", "John Deere"], "XOM": ["Exxon", "ExxonMobil"], "CVX": ["Chevron"],
    "NEE": ["NextEra"], "PLD": ["Prologis"], "LIN": ["Linde"], "PANW": ["Palo Alto Networks"],
    "CRWD": ["CrowdStrike"], "SNPS": ["Synopsys"], "CDNS": ["Cadence Design"], "SHOP": ["Shopify"],
    "DUK": ["Duke Energy"], "SO": ["Southern Company", "Southern Co"], "O": ["Realty Income"],
    "MS": ["Morgan Stanley"], "WFC": ["Wells Fargo"], "C": ["Citigroup", "Citi"], "T": ["AT&T"],
    "VZ": ["Verizon"], "PFE": ["Pfizer"], "ABT": ["Abbott"], "BA": ["Boeing"], "INTC": ["Intel"],
    "IBM": ["IBM"], "UBER": ["Uber"], "PLTR": ["Palantir"], "PM": ["Philip Morris"], "CMCSA": ["Comcast"],
}

_SUFFIX = re.compile(r"[,.]?\s+(?:inc|incorporated|corp|corporation|co|company|companies|holdings?|group|ltd|"
                     r"limited|plc|n\.?v|s\.?a|ag|se|lp|l\.p|class [abc]|common stock|ordinary shares)\.?$", re.I)


def name_variants(name: str) -> list[str]:
    """'Costco Wholesale Corporation' -> ['Costco Wholesale']; 'The Home Depot, Inc.' -> ['Home Depot']."""
    n = re.sub(r"^the\s+", "", (name or "").strip(), flags=re.I)
    for _ in range(3):
        n = _SUFFIX.sub("", n).strip(" ,.")
    return [n] if len(n) >= 3 else []


@lru_cache(maxsize=512)
def _yahoo_name(symbol: str) -> str | None:
    try:
        import yfinance as yf

        info = yf.Ticker(symbol).info or {}
        return info.get("shortName") or info.get("longName")
    except Exception as exc:  # offline or rate limited: the ticker and the aliases still work
        log.info("no company name for %s: %s", symbol, exc)
        return None


def company_names(symbol: str, cache=None, fetch: bool = True) -> list[str]:
    """Names to look for in headlines: the built-in aliases plus Yahoo's short name (cached for 30 days)."""
    sym = symbol.upper()
    names = list(ALIASES.get(sym, []))
    if fetch:
        hit = cache.get_json("names", sym, 30 * 24 * 3600) if cache is not None else None
        name = hit.get("name", "") if isinstance(hit, dict) else None
        if name is None:
            name = _yahoo_name(sym) or ""
            if cache is not None:
                cache.set_json("names", sym, {"name": name})
        names += name_variants(name)
    seen, out = set(), []
    for n in names:
        if n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out


@lru_cache(maxsize=2048)
def _patterns(symbol: str, names: tuple[str, ...]) -> tuple[re.Pattern, re.Pattern | None]:
    sym = symbol.upper()
    forms = sorted({sym, sym.replace("-", "."), sym.replace("-", "/"), sym.replace("-", " ")}, key=len, reverse=True)
    alt = "|".join(re.escape(f) for f in forms)
    if len(sym.replace("-", "")) <= 2:  # "V", "MA", "GE": only as $V, (V) or NYSE:V
        ticker = re.compile(rf"(?:\$|\(|\b(?:NYSE|NASDAQ|Nasdaq|AMEX)\s?:\s?)(?:{alt})(?![A-Za-z0-9])")
    else:  # case-sensitive, so "rising cost" is not COST
        ticker = re.compile(rf"(?<![A-Za-z0-9])\$?(?:{alt})(?![A-Za-z0-9])")
    usable = [n for n in names if len(n) >= 3]
    name = (re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(re.escape(n) for n in usable) + r")(?![A-Za-z0-9])", re.I)
            if usable else None)
    return ticker, name


def mentions(text: str, symbol: str, names: list[str] | tuple[str, ...]) -> bool:
    if not text:
        return False
    ticker, name = _patterns(symbol, tuple(names))
    return bool(ticker.search(text) or (name is not None and name.search(text)))


def is_relevant(item: NewsItem, symbol: str, names: list[str] | tuple[str, ...]) -> bool:
    if item.feed == "sec":
        return True
    summary = "" if item.summary.startswith("items:") else item.summary[:400]
    return mentions(item.title, symbol, names) or mentions(summary, symbol, names)
