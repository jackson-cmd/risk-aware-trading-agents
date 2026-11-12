from datetime import datetime, timedelta, timezone

from riskaware.config import NewsConfig
from riskaware.news import lexicon as lx
from riskaware.news.sources import parse_finviz_news
from riskaware.news.verify import analyze_news, cluster_items
from riskaware.types import NewsItem, NewsStatus

NOW = datetime(2026, 11, 11, 20, 0, tzinfo=timezone.utc)


def item(title, publisher, feed="google", hours_ago=2.0, summary=""):
    return NewsItem(title=title, url=f"https://x/{abs(hash((title, publisher)))}", publisher=publisher, feed=feed,
                    published=NOW - timedelta(hours=hours_ago), symbol="ACME", summary=summary)


def test_two_independent_publishers_confirm_a_story():
    items = [item("Acme beats estimates and raises full-year guidance", "Reuters"),
             item("Acme tops Wall Street estimates, raises guidance for the year", "CNBC", feed="finviz"),
             item("Acme unveils a new widget line", "Acme Blog")]
    rep = analyze_news("ACME", items, NewsConfig(), now=NOW)
    statuses = sorted(c.status.value for c in rep.clusters)
    assert statuses == ["CONFIRMED", "UNCONFIRMED"]
    assert rep.score > 0.2 and rep.confirmed == 1


def test_same_owner_outlets_do_not_confirm_each_other():
    items = [item("Acme beats estimates and raises guidance", "Barron's"),
             item("Acme beats estimates, raises guidance", "MarketWatch")]
    rep = analyze_news("ACME", items, NewsConfig(), now=NOW)
    assert rep.clusters[0].status == NewsStatus.UNCONFIRMED


def test_single_fake_bankruptcy_headline_cannot_trigger_a_sell():
    items = [item("BREAKING: Acme files for Chapter 11 bankruptcy", "totally-real-news.biz")]
    rep = analyze_news("ACME", items, NewsConfig(), now=NOW)
    assert not rep.severe_confirmed
    assert "bankruptcy" in rep.red_flags_unconfirmed
    assert rep.score > -0.2  # a single source carries a fifth of the weight


def test_sec_filing_is_a_primary_source():
    items = [item("8-K: Item 1.03 Bankruptcy or Receivership", "SEC EDGAR", feed="sec", summary="items:1.03"),
             item("Acme seeks court protection from creditors", "Reuters", hours_ago=3)]
    rep = analyze_news("ACME", items, NewsConfig(), now=NOW)
    primary = [c for c in rep.clusters if c.status == NewsStatus.PRIMARY]
    assert primary and primary[0].severe and rep.severe_confirmed


def test_opinion_pieces_are_ignored():
    items = [item("3 Stocks to Buy Before It's Too Late", "Motley Fool"),
             item("Is Acme a Buy Right Now?", "Zacks")]
    rep = analyze_news("ACME", items, NewsConfig(), now=NOW)
    assert all(c.status == NewsStatus.OPINION for c in rep.clusters) and rep.score == 0.0


def test_old_items_drop_out_and_duplicates_merge():
    a = item("Acme wins a $2 billion contract", "Reuters", hours_ago=1)
    dup = NewsItem(a.title, a.url, a.publisher, "finviz", a.published, "ACME")
    old = item("Acme wins old contract", "Reuters", hours_ago=200)
    rep = analyze_news("ACME", [a, dup, old], NewsConfig(), now=NOW)
    assert rep.total_items == 1


def test_clustering_respects_time_window():
    a = item("Acme announces buyback program", "Reuters", hours_ago=1)
    b = item("Acme announces buyback program", "CNBC", hours_ago=100)
    assert len(cluster_items([a, b], window_hours=72)) == 2


def test_lexicon():
    assert lx.sentiment("Acme beats estimates and raises guidance") > 0.5
    assert lx.sentiment("Acme plunges after guidance cut") < -0.3
    assert lx.sentiment("Why Acme stock fell despite a record quarter") < 0
    flags, severe = lx.red_flags("SEC charges Acme with accounting fraud")
    assert severe and "fraud" in flags
    assert lx.is_opinion("Should You Buy Acme Stock?")
    assert lx.normalize_publisher("(Barrons.com)") == "barron's"


def test_finviz_parser():
    html = """<table id="news-table">
    <tr><td>Nov-10-26 06:00PM</td><td><a class="tab-link-news" href="https://a/1">Acme beats estimates</a>
        <span>(Reuters)</span></td></tr>
    <tr><td>09:15AM</td><td><a class="tab-link-news" href="https://a/2">Acme opens new plant</a>
        <span>(Business Wire)</span></td></tr>
    <tr><td>Today 03:13PM</td><td><a class="tab-link-news" href="https://a/3">Acme rallies</a>
        <span>(CNBC)</span></td></tr></table>"""
    items = parse_finviz_news(html, "ACME", now=NOW)
    assert [i.publisher for i in items] == ["Reuters", "Business Wire", "CNBC"]
    assert items[0].published.date().isoformat() == "2026-11-10"
    assert items[1].published < items[0].published + timedelta(days=1)
    assert items[2].published.astimezone(timezone.utc).date() == NOW.date()
