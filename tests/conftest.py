"""Shared fixtures. Tests run offline: synthetic prices, fake quotes, a temporary RISKAWARE_HOME."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# The research headline files are licensed and are not in the repository. A machine that has its own copy
# ($RISKAWARE_NEWS_DIR, or the data/news folder of the real RISKAWARE_HOME) runs the tests that read them; anywhere
# else, CI included, those tests are skipped. This must happen before riskaware.config is imported anywhere.
os.environ.setdefault("RISKAWARE_NEWS_DIR",
                      str(Path(os.environ.get("RISKAWARE_HOME") or "~/.riskaware").expanduser() / "data" / "news"))
os.environ["RISKAWARE_HOME"] = tempfile.mkdtemp(prefix="riskaware-test-")
os.environ.pop("RISKAWARE_CONFIG", None)

from datetime import date  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from riskaware.agents.macro import MarketState  # noqa: E402
from riskaware.config import AppConfig  # noqa: E402
from riskaware.data.prices import SyntheticPrices  # noqa: E402
from riskaware.news.files import PERIOD_FILES, find_news_file  # noqa: E402
from riskaware.news.files import news_file as _news_file  # noqa: E402
from riskaware.strategy.base import PortfolioView, PositionView, StrategyContext  # noqa: E402
from riskaware.types import Quote, Regime  # noqa: E402


def news_file(name: str = "news_2026.jsonl.gz") -> Path:
    """This machine's copy of a research headline file (where it would be when there is none)."""
    return _news_file(name)


#: Whether this machine has all three research headline files (never in CI: they are not distributed).
HAS_NEWS = all(find_news_file(name) for name in PERIOD_FILES)
needs_news = pytest.mark.skipif(not HAS_NEWS, reason="needs the research headline files (licensed, not distributed; "
                                                     "put a copy in ~/.riskaware/data/news to run it)")


@pytest.fixture(autouse=True)
def no_bundled_news(monkeypatch, tmp_path_factory):
    """Unit tests use only the news they create; tests of the shipped file read it through load_bundle()."""
    from riskaware.news import history

    monkeypatch.setattr(history, "BUNDLE_PATH", tmp_path_factory.getbasetemp() / "no-bundle.jsonl.gz")
    monkeypatch.setattr(history, "EARLIER_BUNDLES", ())


@pytest.fixture
def offline_cfg() -> AppConfig:
    cfg = AppConfig()
    cfg.news.enabled = False
    cfg.data.fundamentals = False
    cfg.macro.use_cnn_fear_greed = False
    cfg.execution.require_market_day = False
    cfg.universe.preset = None
    cfg.universe.symbols = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
    cfg.universe.sectors = {"AAA": "Technology", "BBB": "Technology", "CCC": "Health Care", "DDD": "Energy",
                            "EEE": "Financials", "FFF": "Utilities"}
    return cfg


@pytest.fixture
def synthetic() -> SyntheticPrices:
    return SyntheticPrices(seed=3, start=date(2016, 1, 1), end=date(2022, 12, 30))


def make_ohlcv(close: np.ndarray, start: str = "2020-01-01", spread: float = 0.01) -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=len(close))
    close = np.asarray(close, dtype=float)
    return pd.DataFrame({"open": close * (1 - spread / 4), "high": close * (1 + spread), "low": close * (1 - spread),
                         "close": close, "volume": np.full(len(close), 1e6)}, index=idx)


def market_state(regime: Regime = Regime.NEUTRAL, **kw) -> MarketState:
    base = dict(day=date(2024, 6, 3), regime=regime, risk_score=30.0, fear_greed=50.0, vix=16.0, vix_ratio=0.9,
                spy_close=500.0, spy_trend="up", breadth=0.7, credit_z=0.0, event_risk=0.0, macro_score=0.1,
                dip_tailwind=0.0)
    base.update(kw)
    return MarketState(**base)


class FakeRows:
    """Indicator rows keyed by symbol (plain dicts)."""

    def __init__(self, rows: dict[str, dict]):
        self.rows = rows

    def __call__(self, symbol: str):
        r = self.rows.get(symbol)
        return None if r is None else _DictRow(r)


class _DictRow(dict):
    def get(self, key, default=float("nan")):  # match RowView semantics
        return dict.get(self, key, default)


def make_ctx(cfg: AppConfig, regime: Regime, positions: dict[str, PositionView] | None = None, equity: float = 100_000,
             cash: float | None = None, rows: dict[str, dict] | None = None, dte: dict[str, int] | None = None,
             peak: float | None = None, universe: list[str] | None = None) -> StrategyContext:
    positions = positions or {}
    stock = sum(p.value for p in positions.values())
    cash = equity - stock if cash is None else cash
    pf = PortfolioView(equity=equity, cash=cash, positions=positions, peak_equity=peak or equity)
    return StrategyContext(day=date(2024, 6, 3), universe=universe or list((rows or {}).keys()),
                           market=market_state(regime), portfolio=pf, profile=cfg.risk_profile(),
                           rows=FakeRows(rows or {}), days_to_earnings=lambda s: (dte or {}).get(s), mode="test")


def base_row(close: float = 100.0, **kw) -> dict:
    row = dict(open=close, high=close * 1.01, low=close * 0.99, close=close, volume=1e6, ema20=close, ema50=close,
               ema100=close, ema200=close * 0.9, atr14=close * 0.02, atr_pct=0.02, rsi2=50.0, rsi14=50.0, z20=0.0,
               bb_lower=close * 0.95, bb_upper=close * 1.05, adx14=15.0, don55_high=close * 1.1, rel_vol=1.0,
               dd252=-0.05, ema200_slope=0.02, ema100_slope=0.01, ema50_slope=0.01, mom126=0.1, rs126=0.0,
               vol60=0.25, down_streak=0, below_ema50_streak=0, prev_close=close, prev_high=close * 1.01,
               rsi2_prev=50.0, high10=close * 1.05, mom252=0.15)
    row.update(kw)
    return row


def fake_quotes(prices: dict[str, float]):
    def fn(symbols):
        return {s: Quote(symbol=s, last=prices[s], bid=prices[s], ask=prices[s]) for s in symbols if s in prices}

    return fn
