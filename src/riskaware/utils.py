"""HTTP session, polite rate limiting, a small disk cache and logging helpers."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import __version__

log = logging.getLogger("riskaware")

PROJECT_URL = "https://github.com/jackson-cmd/risk-aware-trading-agents"
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("urllib3", "yfinance", "peewee", "matplotlib", "curl_cffi"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def tool_user_agent(contact_email: str | None = None) -> str:
    """User-Agent for public-data sites (SEC, BLS) that ask clients to identify themselves."""
    if contact_email:
        # SEC EDGAR expects "<name> <contact email>"
        return f"risk-aware-trading-agents/{__version__} {contact_email}"
    return f"risk-aware-trading-agents/{__version__} (+{PROJECT_URL})"


def make_session(user_agent: str | None = None, retries: int = 3, backoff: float = 0.8) -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update(
        {
            "User-Agent": user_agent or BROWSER_UA,
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    return s


class RateLimiter:
    """Minimum interval between requests to the same host."""

    def __init__(self, min_interval: float = 1.0):
        self.min_interval = min_interval
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, url: str) -> None:
        host = urlparse(url).netloc
        with self._lock:
            now = time.monotonic()
            last = self._last.get(host, 0.0)
            delay = self.min_interval - (now - last)
            if delay > 0:
                time.sleep(delay)
            self._last[host] = time.monotonic()


class DiskCache:
    """Tiny file cache for JSON values and DataFrames, keyed by string with a TTL."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, namespace: str, key: str, ext: str) -> Path:
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in key)[:60]
        folder = self.root / namespace
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{safe}-{digest}.{ext}"

    @staticmethod
    def _fresh(path: Path, ttl: float | None) -> bool:
        if not path.exists():
            return False
        return ttl is None or (time.time() - path.stat().st_mtime) <= ttl

    def get_json(self, namespace: str, key: str, ttl: float | None) -> Any | None:
        p = self._path(namespace, key, "json")
        if not self._fresh(p, ttl):
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def set_json(self, namespace: str, key: str, value: Any) -> None:
        p = self._path(namespace, key, "json")
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, default=str), encoding="utf-8")
        tmp.replace(p)

    def get_frame(self, namespace: str, key: str, ttl: float | None) -> pd.DataFrame | None:
        p = self._path(namespace, key, "csv")
        if not self._fresh(p, ttl):
            return None
        try:
            df = pd.read_csv(p, index_col=0)
            df.index = pd.to_datetime(df.index)
            return df
        except (OSError, ValueError, pd.errors.ParserError):
            return None

    def set_frame(self, namespace: str, key: str, df: pd.DataFrame) -> None:
        p = self._path(namespace, key, "csv")
        buf = io.StringIO()
        df.to_csv(buf)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(buf.getvalue(), encoding="utf-8")
        tmp.replace(p)

    def memo_json(self, namespace: str, key: str, ttl: float | None, fn: Callable[[], Any]) -> Any:
        cached = self.get_json(namespace, key, ttl)
        if cached is not None:
            return cached
        value = fn()
        if value is not None:
            self.set_json(namespace, key, value)
        return value


def clip(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def safe_float(x: Any, default: float | None = None) -> float | None:
    try:
        if x is None:
            return default
        v = float(x)
        if v != v:  # NaN
            return default
        return v
    except (TypeError, ValueError):
        return default


def fmt_pct(x: float | None, digits: int = 1) -> str:
    return "n/a" if x is None else f"{x * 100:.{digits}f}%"


def fmt_money(x: float | None) -> str:
    return "n/a" if x is None else f"${x:,.0f}"
