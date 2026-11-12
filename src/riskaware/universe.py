"""Built-in watchlists and a static sector map so sector caps work offline.

The lists are starting points, not recommendations. Backtests on today's large
caps carry survivorship bias: the names are in the list because they did well.
Use your own list (``universe.symbols``) or a CSV of point-in-time constituents
for serious research.
"""

from __future__ import annotations

SECTOR_ETFS: dict[str, str] = {
    "XLK": "Technology",
    "XLC": "Communication Services",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLV": "Health Care",
    "XLF": "Financials",
    "XLI": "Industrials",
    "XLE": "Energy",
    "XLU": "Utilities",
    "XLRE": "Real Estate",
    "XLB": "Materials",
}

BROAD_ETFS: dict[str, str] = {
    "SPY": "Broad Market",
    "QQQ": "Broad Market",
    "IWM": "Broad Market",
    "DIA": "Broad Market",
    "VTI": "Broad Market",
    "RSP": "Broad Market",
}

BOND_ETFS: dict[str, str] = {
    "SGOV": "T-Bills",
    "BIL": "T-Bills",
    "SHV": "T-Bills",
    "SHY": "Treasuries",
    "IEF": "Treasuries",
    "TLT": "Treasuries",
    "HYG": "Credit",
    "LQD": "Credit",
}

MEGACAP: dict[str, str] = {
    # Technology
    "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology", "AVGO": "Technology",
    "ORCL": "Technology", "ADBE": "Technology", "CRM": "Technology", "AMD": "Technology",
    "CSCO": "Technology", "QCOM": "Technology", "TXN": "Technology", "INTU": "Technology",
    "NOW": "Technology",
    # Communication Services
    "GOOGL": "Communication Services", "META": "Communication Services",
    "NFLX": "Communication Services", "DIS": "Communication Services", "TMUS": "Communication Services",
    # Consumer Discretionary
    "AMZN": "Consumer Discretionary", "TSLA": "Consumer Discretionary", "HD": "Consumer Discretionary",
    "MCD": "Consumer Discretionary", "LOW": "Consumer Discretionary", "BKNG": "Consumer Discretionary",
    "NKE": "Consumer Discretionary", "SBUX": "Consumer Discretionary",
    # Consumer Staples
    "COST": "Consumer Staples", "WMT": "Consumer Staples", "PG": "Consumer Staples",
    "KO": "Consumer Staples", "PEP": "Consumer Staples",
    # Health Care
    "LLY": "Health Care", "UNH": "Health Care", "JNJ": "Health Care", "ABBV": "Health Care",
    "MRK": "Health Care", "TMO": "Health Care", "ISRG": "Health Care",
    # Financials
    "JPM": "Financials", "V": "Financials", "MA": "Financials", "BAC": "Financials",
    "GS": "Financials", "BRK-B": "Financials", "SPGI": "Financials",
    # Industrials
    "CAT": "Industrials", "GE": "Industrials", "HON": "Industrials", "UNP": "Industrials",
    "RTX": "Industrials", "DE": "Industrials",
    # Energy, Utilities, Real Estate, Materials
    "XOM": "Energy", "CVX": "Energy", "NEE": "Utilities", "PLD": "Real Estate", "LIN": "Materials",
}

TECH_GROWTH: dict[str, str] = {
    "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology", "AVGO": "Technology",
    "AMD": "Technology", "ADBE": "Technology", "CRM": "Technology", "NOW": "Technology",
    "PANW": "Technology", "CRWD": "Technology", "SNPS": "Technology", "CDNS": "Technology",
    "GOOGL": "Communication Services", "META": "Communication Services", "NFLX": "Communication Services",
    "AMZN": "Consumer Discretionary", "TSLA": "Consumer Discretionary", "SHOP": "Technology",
}

DIVIDEND_DEFENSIVE: dict[str, str] = {
    "JNJ": "Health Care", "PG": "Consumer Staples", "KO": "Consumer Staples", "PEP": "Consumer Staples",
    "WMT": "Consumer Staples", "COST": "Consumer Staples", "MCD": "Consumer Discretionary",
    "ABBV": "Health Care", "MRK": "Health Care", "XOM": "Energy", "CVX": "Energy", "NEE": "Utilities",
    "DUK": "Utilities", "SO": "Utilities", "PLD": "Real Estate", "O": "Real Estate", "LIN": "Materials",
    "HON": "Industrials", "UNP": "Industrials", "JPM": "Financials",
}

PRESETS: dict[str, dict[str, str]] = {
    "megacap": MEGACAP,
    "tech": TECH_GROWTH,
    "defensive": DIVIDEND_DEFENSIVE,
    "sectors": SECTOR_ETFS,
    "etf": {**BROAD_ETFS, **SECTOR_ETFS},
}

SECTOR_MAP: dict[str, str] = {}
for _m in (MEGACAP, TECH_GROWTH, DIVIDEND_DEFENSIVE, SECTOR_ETFS, BROAD_ETFS, BOND_ETFS):
    SECTOR_MAP.update(_m)

ETF_SYMBOLS: set[str] = set(SECTOR_ETFS) | set(BROAD_ETFS) | set(BOND_ETFS) | {
    "VOO", "IVV", "VEA", "VWO", "EFA", "EEM", "GLD", "SLV", "SMH", "SOXX", "XBI", "IBB", "KRE", "ARKK",
}

BROAD_INDEX_ETFS: set[str] = set(BROAD_ETFS) | {"VOO", "IVV", "VEA", "EFA"}

MACRO_SYMBOLS: list[str] = ["SPY", "^VIX", "^VIX3M", "HYG", "IEF", "TLT", *SECTOR_ETFS]


def preset_symbols(name: str) -> list[str]:
    key = name.lower()
    if key in ("", "none", "custom"):
        return []
    if key not in PRESETS:
        raise ValueError(f"unknown universe preset '{name}', choose one of {sorted(PRESETS)}")
    return list(PRESETS[key])


def sector_of(symbol: str, overrides: dict[str, str] | None = None) -> str:
    if overrides and symbol in overrides:
        return overrides[symbol]
    return SECTOR_MAP.get(symbol, "Unknown")


def is_etf(symbol: str) -> bool:
    return symbol in ETF_SYMBOLS
