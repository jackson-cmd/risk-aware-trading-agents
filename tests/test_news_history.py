from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from typer.testing import CliRunner

from riskaware.config import AppConfig, NewsConfig
from riskaware.news.history import NewsArchive, PointInTimeNews, decision_time, read_csv
from riskaware.news.history_sources import (
    HistoryError,
    SecFilingHistory,
    filing_headline,
    parse_benzinga,
    parse_submissions,
)
from riskaware.strategy import Strategy
from riskaware.types import NewsItem

NY = ZoneInfo("America/New_York")


def ny(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=NY).astimezone(timezone.utc)


def item(sym, title, when, publisher="reuters", feed="import", summary="", sentiment=None):
    return NewsItem(title=title, url=f"https://x/{publisher}/{title}", publisher=publisher, feed=feed,
                    published=when, symbol=sym, summary=summary, sentiment=sentiment)


def test_archive_stores_sorted_and_deduplicates(tmp_path):
    a = NewsArchive(tmp_path)
    t = ny(2024, 5, 2, 10, 0)
    assert a.add([item("aapl", "B", t + timedelta(hours=1)), item("AAPL", "A", t)]) == 2
    assert a.add([item("AAPL", "A", t), item("AAPL", "no time", None)]) == 0
    fresh = NewsArchive(tmp_path)
    assert [i.title for i in fresh.items("AAPL")] == ["A", "B"]
    assert fresh.symbols() == ["AAPL"]
    st = fresh.stats()[0]
    assert st.items == 2 and st.feeds == {"import": 2}
    assert fresh.remove("AAPL", feed="sec") == 0 and fresh.remove("AAPL") == 2 and fresh.symbols() == []


def test_archive_rejects_path_like_symbols(tmp_path):
    with pytest.raises(ValueError):
        NewsArchive(tmp_path).path("../etc")


def test_csv_import(tmp_path):
    p = tmp_path / "news.csv"
    p.write_text(
        "timestamp,symbol,headline,source,url,sentiment\n"
        "2024-05-02T20:31:00Z,AAPL,Apple beats estimates,Reuters,https://r/1,0.6\n"
        "2024-05-02 16:35:00,AAPL;MSFT,Big tech earnings roundup,,https://b/2,\n"
        ",AAPL,no time,,,\n", encoding="utf-8")
    items = read_csv(p, default_source="my feed", assume_tz="America/New_York")
    assert len(items) == 3
    assert items[0].published == datetime(2024, 5, 2, 20, 31, tzinfo=timezone.utc)
    assert items[0].sentiment == pytest.approx(0.6) and items[0].publisher == "Reuters"
    assert items[1].published == ny(2024, 5, 2, 16, 35) and items[1].publisher == "my feed"
    assert {i.symbol for i in items[1:]} == {"AAPL", "MSFT"}
    bad = tmp_path / "bad.csv"
    bad.write_text("when,ticker\n2024-01-01,AAPL\n", encoding="utf-8")
    with pytest.raises(ValueError):
        read_csv(bad)


def test_point_in_time_cutoff(tmp_path):
    a = NewsArchive(tmp_path)
    a.add([
        item("AAPL", "before the close", ny(2024, 5, 2, 15, 59)),
        item("AAPL", "after the close", ny(2024, 5, 2, 16, 1)),
        item("AAPL", "too old", ny(2024, 4, 26, 12, 0)),
    ])
    pit = PointInTimeNews(a, NewsConfig(lookback_hours=72))
    assert decision_time(date(2024, 5, 2)) == ny(2024, 5, 2, 16, 0)
    same_day = [i.title for i in pit.visible("AAPL", decision_time(date(2024, 5, 2)))]
    next_day = [i.title for i in pit.visible("AAPL", decision_time(date(2024, 5, 3)))]
    assert same_day == ["before the close"]
    assert next_day == ["before the close", "after the close"]
    assert pit.report("AAPL", date(2024, 4, 1)) is None
    assert pit.report("MSFT", date(2024, 5, 3)) is None


def test_supplied_sentiment_is_used(tmp_path):
    a = NewsArchive(tmp_path)
    t = ny(2024, 5, 2, 11, 0)
    a.add([item("AAPL", "Apple supplier deal widens", t, "reuters", sentiment=0.8),
           item("AAPL", "Apple supplier deal widens further", t, "bloomberg", sentiment=0.8)])
    rep = PointInTimeNews(a).report("AAPL", date(2024, 5, 2))
    assert rep.confirmed == 1 and rep.score > 0.5


def test_replay_uses_the_live_relevance_filter(tmp_path):
    a = NewsArchive(tmp_path)
    t = ny(2024, 5, 2, 11, 0)
    a.add([item("AAPL", "Apple supplier deal widens", t, "reuters"),
           item("AAPL", "Costco recalls frozen berries", t, "bloomberg")])
    rep = PointInTimeNews(a).report("AAPL", date(2024, 5, 2))
    assert rep.off_topic == 1 and rep.total_items == 1
    loose = PointInTimeNews(a, NewsConfig(relevance_filter=False)).report("AAPL", date(2024, 5, 2))
    assert loose.off_topic == 0 and loose.total_items == 2


SUBMISSIONS = {
    "cik": "320193",
    "filings": {
        "recent": {
            "accessionNumber": ["0000320193-24-000081", "0000320193-24-000080", "0001140361-24-000001"],
            "filingDate": ["2024-08-01", "2024-07-30", "2023-01-05"],
            "acceptanceDateTime": ["2024-08-01T16:30:42.000Z", "2024-07-30T18:00:00.000Z", "2023-01-05T09:00:00.000Z"],
            "form": ["8-K", "4", "8-K"],
            "items": ["2.02,9.01", "", "5.02"],
            "primaryDocument": ["aapl-20240801.htm", "x.xml", "d1.htm"],
        },
        "files": [{"name": "CIK0000320193-submissions-001.json", "filingFrom": "1994-01-01", "filingTo": "2015-12-31"}],
    },
}


def test_parse_sec_submissions():
    items = parse_submissions(SUBMISSIONS, "aapl", 320193, start=date(2024, 1, 1))
    assert len(items) == 1
    it = items[0]
    # the acceptance time is read as New York time (16:30 EDT = 20:30 UTC)
    assert it.published == datetime(2024, 8, 1, 20, 30, 42, tzinfo=timezone.utc)
    assert it.symbol == "AAPL" and it.feed == "sec" and it.summary == "items:2.02,9.01"
    assert "Results of operations" in it.title and "9.01" not in it.title
    assert it.url.endswith("/320193/000032019324000081/aapl-20240801.htm")
    assert len(parse_submissions(SUBMISSIONS, "AAPL", 320193)) == 2
    assert filing_headline("9.01") == "8-K: Item 9.01 Financial statements and exhibits"


def test_sec_history_needs_contact_email():
    with pytest.raises(HistoryError):
        SecFilingHistory(None)


def test_sec_history_skips_old_pages(monkeypatch):
    h = SecFilingHistory("me@example.com")
    calls = []

    def fake_get(url):
        calls.append(url)
        return {"0": {"cik_str": 320193, "ticker": "AAPL"}} if "company_tickers" in url else SUBMISSIONS

    monkeypatch.setattr(h, "_get_json", fake_get)
    items = h.fetch("AAPL", date(2018, 1, 1))
    assert len(items) == 2
    assert not any("submissions-001" in u for u in calls)  # that page ends in 2015
    assert h.fetch("SPY", date(2018, 1, 1)) == []


def test_parse_benzinga():
    recs = [
        {"created": "Thu, 01 Aug 2024 16:35:12 -0400", "title": "Apple Q3 Revenue Beats", "url": "https://bz/1"},
        {"created": "", "title": "no time"},
        {"created": "Thu, 01 Aug 2024 16:40:00 -0400", "title": ""},
    ]
    items = parse_benzinga(recs, "aapl")
    assert len(items) == 1
    assert items[0].published == datetime(2024, 8, 1, 20, 35, 12, tzinfo=timezone.utc)
    assert items[0].publisher == "benzinga" and items[0].symbol == "AAPL"


class Recorder(Strategy):
    name = "recorder"

    def on_start(self, universe):
        self.seen = {}

    def on_bar(self, ctx):
        rep = ctx.news("AAA")
        if rep is not None:
            self.seen[ctx.day] = rep
        return []


def test_backtest_replays_archive_without_lookahead(tmp_path):
    from riskaware.backtest.engine import Backtester
    from riskaware.data.prices import SyntheticPrices

    cfg = AppConfig()
    cfg.universe.preset = None
    cfg.universe.symbols = ["AAA", "BBB"]
    a = NewsArchive(tmp_path)
    # an 8-K with Item 4.02 (non-reliance on past financials) filed after the close on a Wednesday
    a.add([NewsItem(title="8-K: Item 4.02", url="https://sec/1", publisher="SEC EDGAR", feed="sec",
                    published=ny(2019, 6, 5, 17, 5), symbol="AAA", summary="items:4.02")])
    pit = PointInTimeNews(a, cfg.news)
    bt = Backtester(cfg, SyntheticPrices(seed=3, start=date(2018, 1, 1), end=date(2019, 12, 31)), news=pit)
    strat = Recorder()
    res = bt.run(strat, date(2019, 6, 1), date(2019, 6, 20))
    assert date(2019, 6, 5) not in strat.seen
    assert date(2019, 6, 6) in strat.seen
    rep = strat.seen[date(2019, 6, 6)]
    assert rep.severe_confirmed and "accounting" in rep.red_flags_confirmed
    assert max(strat.seen) <= date(2019, 6, 10)  # 72-hour lookback
    assert any("News replayed for" in n and "before its close" in n for n in res.notes)


def test_cli_import_status_show(tmp_path):
    from riskaware.cli import S, app

    cfg_file = tmp_path / "riskaware.yaml"
    cfg_file.write_text(f"news:\n  archive_dir: {tmp_path / 'news'}\n", encoding="utf-8")
    csv_file = tmp_path / "n.csv"
    csv_file.write_text("timestamp,symbol,headline,source\n2024-05-02T14:00:00Z,AAPL,Apple news,reuters\n",
                        encoding="utf-8")
    runner = CliRunner()
    S.cfg = None
    r = runner.invoke(app, ["--config", str(cfg_file), "news-history", "import", str(csv_file)])
    assert r.exit_code == 0, r.output
    S.cfg = None
    r = runner.invoke(app, ["--config", str(cfg_file), "news-history", "status"])
    assert r.exit_code == 0 and "AAPL" in r.output
    S.cfg = None
    r = runner.invoke(app, ["--config", str(cfg_file), "news-history", "show", "AAPL", "--date", "2024-05-02"])
    assert r.exit_code == 0 and "Apple news" in r.output
    S.cfg = None
