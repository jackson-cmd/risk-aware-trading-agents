"""The dashboard's 'news the backtest saw' panel: trades map to the close they were decided at."""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from riskaware.config import NewsConfig
from riskaware.dashboard import newsview as nv
from riskaware.news.history import NewsArchive, PointInTimeNews
from riskaware.types import NewsItem

NY = ZoneInfo("America/New_York")


def ny(*a):
    return datetime(*a, tzinfo=NY).astimezone(timezone.utc)


TRADES = pd.DataFrame([
    {"date": pd.Timestamp("2026-03-09"), "symbol": "ACME", "side": "BUY", "qty": 10, "price": 50.0,
     "reason": "dip in an uptrend"},
    {"date": pd.Timestamp("2026-03-10"), "symbol": "BOLT", "side": "BUY", "qty": 5, "price": 20.0, "reason": "trend"},
    {"date": pd.Timestamp("2026-03-17"), "symbol": "ACME", "side": "SELL", "qty": 10, "price": 55.0,
     "reason": "take profit"},
])


def test_trades_map_to_the_close_they_were_decided_at():
    picks = nv.trade_picks(TRADES, ["ACME"])
    assert list(picks.values()) == [("ACME", date(2026, 3, 16)), ("ACME", date(2026, 3, 6))]  # newest first
    assert next(iter(picks)).startswith("2026-03-17 · SELL ACME · take profit")
    assert nv.decision_day(date(2026, 3, 9)) == date(2026, 3, 6)  # a Monday fill was decided on Friday
    assert nv.trade_picks(None, ["ACME"]) == {} and nv.trade_picks(TRADES.iloc[0:0], ["ACME"]) == {}
    counted = nv.trade_picks(TRADES, ["ACME"], count=lambda s, d: 3 if d == date(2026, 3, 6) else 0)
    assert [k.rsplit(" · ", 1)[1] for k in counted] == ["no news", "3 news"]
    zh = nv.trade_picks(TRADES, ["ACME"], "zh", count=lambda s, d: 2)
    assert all(k.endswith("2 条新闻") for k in zh)
    assert nv.orders_from(TRADES, "ACME", date(2026, 3, 6))["date"].tolist() == ["2026-03-09"]
    assert nv.orders_from(TRADES, "ACME", date(2026, 3, 9)).empty
    assert nv.orders_from(TRADES, "BOLT", date(2026, 3, 9))["side"].tolist() == ["BUY"]


def test_story_rows_are_newest_first_with_status_and_new_york_time(tmp_path):
    a = NewsArchive(tmp_path, bundled=False)
    a.add([
        NewsItem(title="ACME wins a large defense contract", url="https://r/1", publisher="Reuters", feed="import",
                 published=ny(2026, 3, 5, 9, 30), symbol="ACME"),
        NewsItem(title="ACME wins large defense contract", url="https://b/1", publisher="Bloomberg", feed="import",
                 published=ny(2026, 3, 5, 10, 0), symbol="ACME"),
        NewsItem(title="ACME opens a new plant in Ohio", url="https://z/1", publisher="Benzinga", feed="benzinga",
                 published=ny(2026, 3, 6, 11, 15), symbol="ACME"),
    ])
    rep = PointInTimeNews(a, NewsConfig()).report("ACME", date(2026, 3, 6))
    rows = nv.story_rows(rep, "en")
    assert [r["New York time"] for r in rows] == ["03-06 11:15", "03-05 09:30"]
    assert rows[0]["Status"].endswith("single source") and rows[1]["Status"].startswith("✅")
    assert "Reuters" in rows[1]["Sources"] and "Bloomberg" in rows[1]["Sources"]
    zh = nv.story_rows(rep, "zh")
    assert "美东时间" in zh[0] and zh[1]["状态"].startswith("✅")
