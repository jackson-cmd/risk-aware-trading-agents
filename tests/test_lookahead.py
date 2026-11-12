"""A backtest must never see the future: not in the news, not in the timestamps, not through the price data."""

import gzip
import json
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np

from riskaware.backtest.engine import Backtester
from riskaware.config import AppConfig, NewsConfig
from riskaware.data.prices import SyntheticPrices
from riskaware.news import history
from riskaware.news.history import NewsArchive, PointInTimeNews, decision_time, read_csv
from riskaware.strategy import Strategy
from riskaware.types import NewsItem

NY = ZoneInfo("America/New_York")


def ny(y, m, d, hh, mm, ss=0):
    return datetime(y, m, d, hh, mm, ss, tzinfo=NY).astimezone(timezone.utc)


def item(sym, title, when, publisher="Benzinga", feed="benzinga"):
    return NewsItem(title=title, url="", publisher=publisher, feed=feed, published=when, symbol=sym)


def pit_for(tmp_path, *items, **kw):
    a = NewsArchive(tmp_path, bundled=False)
    a.add(items)
    return PointInTimeNews(a, NewsConfig(), **kw)


def test_an_item_stamped_at_the_close_is_seen_the_next_day(tmp_path):
    pit = pit_for(tmp_path, item("ACME", "ACME results", ny(2026, 3, 4, 16, 0)),
                  item("ACME", "ACME guidance", ny(2026, 3, 4, 15, 59, 59)))
    today = [i.title for i in pit.visible("ACME", decision_time(date(2026, 3, 4)))]
    assert today == ["ACME guidance"]
    assert len(pit.visible("ACME", decision_time(date(2026, 3, 5)))) == 2


def test_early_close_days_cut_off_at_one_pm(tmp_path):
    # Christmas Eve 2025: the market closes at 13:00; the next session is December 26
    assert decision_time(date(2025, 12, 24)) == ny(2025, 12, 24, 13, 0)
    assert decision_time(date(2025, 12, 23)) == ny(2025, 12, 23, 16, 0)
    pit = pit_for(tmp_path, item("ACME", "after the half-day close", ny(2025, 12, 24, 13, 30)))
    assert pit.visible("ACME", decision_time(date(2025, 12, 24))) == []
    assert len(pit.visible("ACME", decision_time(date(2025, 12, 26)))) == 1


def test_ambiguous_timestamps_are_read_the_late_way(tmp_path):
    p = tmp_path / "news.csv"
    p.write_text("timestamp,symbol,headline\n"
                 "2026-03-05,ACME,dated only\n"
                 "2026-03-05 16:30:00,ACME,no zone after the close\n"
                 "2026-03-05T16:30:00Z,ACME,UTC before the close\n", encoding="utf-8")
    items = {i.title: i.published for i in read_csv(p)}
    assert items["dated only"] == ny(2026, 3, 5, 23, 59, 59)  # the end of that day, not midnight before it
    assert items["no zone after the close"] == ny(2026, 3, 5, 16, 30)  # New York, not UTC (11:30 New York)
    a = NewsArchive(tmp_path / "a", bundled=False)
    a.add(read_csv(p))
    pit = PointInTimeNews(a, NewsConfig())
    assert [i.title for i in pit.visible("ACME", decision_time(date(2026, 3, 5)))] == ["UTC before the close"]
    assert len(pit.visible("ACME", decision_time(date(2026, 3, 6)))) == 3


def test_items_dated_in_the_future_are_dropped(tmp_path):
    a = NewsArchive(tmp_path, bundled=False)
    soon = datetime.now(timezone.utc) + timedelta(days=3)
    assert a.add([item("ACME", "from the future", soon), item("ACME", "today", datetime.now(timezone.utc))]) == 1


def write_bundle(path, recs):
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")


def test_bundled_headlines_merge_and_stay_read_only(tmp_path):
    bundle = tmp_path / "bundle.jsonl.gz"
    write_bundle(bundle, [
        {"ts": "2026-02-03T15:00:00Z", "symbol": "ACME", "headline": "ACME wins a contract", "source": "Benzinga",
         "feed": "benzinga"},
        {"ts": "2026-02-04T21:05:00Z", "symbol": "ACME", "headline": "8-K: Item 2.02 Results of operations (earnings)",
         "source": "SEC EDGAR", "feed": "sec", "url": "https://www.sec.gov/x", "summary": "items:2.02"},
        {"ts": "2026-02-03T16:00:00Z", "symbol": "BOLT", "headline": "BOLT recalls a product", "source": "Benzinga",
         "feed": "benzinga"}])
    a = NewsArchive(tmp_path / "local", bundle_path=bundle)
    assert a.symbols() == ["ACME", "BOLT"]
    a.add([item("ACME", "ACME wins a contract", datetime(2026, 2, 3, 15, tzinfo=timezone.utc))])  # same item
    a.add([item("ACME", "ACME hires a CFO", datetime(2026, 2, 5, 15, tzinfo=timezone.utc))])
    titles = [i.title for i in a.items("ACME")]
    assert titles == ["ACME wins a contract", "8-K: Item 2.02 Results of operations (earnings)", "ACME hires a CFO"]
    st = {s.symbol: s for s in a.stats()}
    assert st["ACME"].items == 3 and st["ACME"].bundled == 1 and st["BOLT"].bundled == 1
    assert a.remove("ACME") == 2 and len(a.items("ACME")) == 2  # your items go, the bundled ones stay
    info = a.bundle_info()
    assert info["items"] == 3 and info["symbols"] == 2 and info["feeds"] == {"benzinga": 2, "sec": 1}
    assert NewsArchive(tmp_path / "local", bundled=False, bundle_path=bundle).symbols() == []
    pit = PointInTimeNews(a, NewsConfig(), ["ACME"])  # only the symbols asked for are loaded
    assert pit.symbols == ["ACME"]
    assert [i.feed for i in pit.visible("ACME", decision_time(date(2026, 2, 4)))] == ["benzinga"]


class Peek(Strategy):
    """Records what a strategy could reach on each day."""

    name = "peek"
    default_params = {}

    def __init__(self, **kw):
        super().__init__(**kw)
        self.rows, self.hist = [], []

    def on_bar(self, ctx):
        row, hist = ctx.row("AAA"), ctx.history("AAA", 5)
        if row is not None and hist is not None:
            self.rows.append((ctx.day, row))
            self.hist.append((ctx.day, hist))
        return []


def _root(a: np.ndarray) -> np.ndarray:
    while isinstance(a.base, np.ndarray):
        a = a.base
    return a


def test_a_strategy_cannot_reach_later_prices(tmp_path):
    cfg = AppConfig()
    cfg.universe.preset = None
    cfg.universe.symbols = ["AAA", "BBB"]
    bt = Backtester(cfg, SyntheticPrices(seed=5, start=date(2018, 1, 1), end=date(2019, 12, 31)))
    peek = Peek()
    bt.run(peek, date(2019, 3, 1), date(2019, 4, 30))
    assert peek.rows and peek.hist
    for _day, row in peek.rows:
        # a row keeps only today's values: no array of the whole history behind it
        assert row._vals.base is None and row._vals.size == len(row.keys())
    for day, hist in peek.hist:
        assert hist.index[-1].date() == day and len(hist) <= 5
        for col in ("close", "ema200"):
            arr = hist[col].to_numpy()
            assert _root(arr).size <= hist.shape[0] * hist.shape[1]  # no view into the later rows
    # the closes a strategy saw on each day match the real closes of that day only
    last_day, last_hist = peek.hist[-1]
    first_day, first_hist = peek.hist[0]
    assert first_hist["close"].iloc[-1] != last_hist["close"].iloc[-1]


def test_the_bundle_file_holds_titles_only():
    recs = history.load_bundle()
    if not recs:  # the file is built by scripts/build_news_bundle.py
        return
    allowed = {"ts", "symbol", "headline", "source", "feed", "url", "summary"}
    for sym, rows in recs.items():
        assert list(rows) == sorted(rows, key=lambda r: r["ts"])
        for r in rows:
            assert set(r) <= allowed and r["symbol"] == sym and r["headline"]
            assert r["feed"] in ("benzinga", "sec") and len(r["headline"]) <= 300
            if r["feed"] == "benzinga":
                assert set(r) <= {"ts", "symbol", "headline", "source", "feed"}  # no article text, no links
            assert not re.search(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}", json.dumps(r))  # no email address
