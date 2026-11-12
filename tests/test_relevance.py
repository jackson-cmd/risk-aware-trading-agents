from datetime import datetime, timedelta, timezone

from riskaware.config import NewsConfig
from riskaware.news import relevance as rel
from riskaware.news.verify import analyze_news
from riskaware.types import NewsItem
from riskaware.utils import DiskCache

NOW = datetime(2026, 11, 12, 20, 0, tzinfo=timezone.utc)


def item(title, publisher, feed="google", symbol="COST", summary=""):
    return NewsItem(title=title, url=f"https://x/{abs(hash((title, publisher)))}", publisher=publisher, feed=feed,
                    published=NOW - timedelta(hours=2), symbol=symbol, summary=summary)


def test_name_variants_strip_legal_suffixes():
    assert rel.name_variants("Costco Wholesale Corporation") == ["Costco Wholesale"]
    assert rel.name_variants("The Home Depot, Inc.") == ["Home Depot"]
    assert rel.name_variants("Apple Inc.") == ["Apple"]
    assert rel.name_variants("NVIDIA Corporation") == ["NVIDIA"]
    assert rel.name_variants("") == []


def test_mentions_ticker_and_names_but_not_the_word():
    names = ["Costco"]
    assert rel.mentions("Costco Stock Nears All-Time Peak Amid Dividend", "COST", names)
    assert rel.mentions("COST Stock Draws Street Attention After $30 Cash Dividend", "COST", names)
    assert rel.mentions("Why $COST is up today", "COST", names)
    assert not rel.mentions("Alignment Healthcare Shares Fall Amid Rising Cost Concerns", "COST", names)
    assert not rel.mentions("Nvidia stock is pricing in 2 major risks as CEO Jensen Huang exits", "COST", names)
    assert rel.mentions("Nvidia stock is pricing in 2 major risks as CEO Jensen Huang exits", "NVDA", ["Nvidia"])


def test_short_tickers_need_a_marker():
    names = ["Visa"]
    assert rel.mentions("Visa (V) shares rise after earnings", "V", names)
    assert rel.mentions("Why NYSE: V is a buy", "V", [])
    assert rel.mentions("Buy $V before the split", "V", [])
    assert not rel.mentions("A V-shaped recovery is under way", "V", [])
    assert rel.mentions("BRK.B hits a record high", "BRK-B", [])


def test_sec_filings_always_count():
    sec = item("8-K - Current report", "SEC EDGAR", feed="sec", summary="items:5.02")
    assert rel.is_relevant(sec, "COST", [])


def test_other_companies_red_flags_are_not_pinned_on_this_symbol():
    items = [
        item("Nvidia CEO resigns, shares slide", "CNBC"),
        item("Nvidia CEO resigns as the chip giant reshuffles", "Reuters", feed="finviz"),
        item("Costco beats estimates and raises its dividend", "Barron's"),
        item("Costco tops estimates, raises dividend", "Yahoo Finance", feed="yahoo"),
        item("Shares fall amid rising cost of shipping", "MarketWatch", feed="yahoo"),
    ]
    unfiltered = analyze_news("COST", items, NewsConfig(), now=NOW)
    assert "exec_departure" in unfiltered.red_flags_confirmed  # the bug this guards against
    rep = analyze_news("COST", items, NewsConfig(), now=NOW, names=rel.company_names("COST", fetch=False))
    assert rep.red_flags_confirmed == [] and rep.off_topic == 3
    assert rep.confirmed == 1 and rep.score > 0
    off = analyze_news("COST", items, NewsConfig(relevance_filter=False), now=NOW, names=["Costco"])
    assert off.off_topic == 0 and "exec_departure" in off.red_flags_confirmed


def test_company_names_uses_aliases_and_caches_yahoo(monkeypatch, tmp_path):
    calls = []

    def fake(symbol):
        calls.append(symbol)
        return "Acme Rockets Holdings, Inc."

    monkeypatch.setattr(rel, "_yahoo_name", fake)
    cache = DiskCache(tmp_path)
    assert rel.company_names("ACME", cache) == ["Acme Rockets"]
    assert rel.company_names("ACME", cache) == ["Acme Rockets"]
    assert calls == ["ACME"]  # the second call is served from the cache
    assert rel.company_names("GOOGL", fetch=False) == ["Alphabet", "Google", "YouTube"]
