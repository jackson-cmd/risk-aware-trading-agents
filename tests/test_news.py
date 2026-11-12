from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from riskaware.agents.news import NewsAgent
from riskaware.config import NewsConfig
from riskaware.news import lexicon as lx
from riskaware.news.sources import parse_finviz_news
from riskaware.news.verify import analyze_news, cluster_items
from riskaware.strategy.risk_aware import RiskAwareStrategy
from riskaware.types import NewsItem, NewsStatus, Regime

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


# ----------------------------------------------------- press releases, denials and Ch. 11


def held_position_exit(report):
    """What the built-in strategy does with a held ACME position given this news. The market is risk-off and
    the hard stop is off, so only a thesis break (the news emergency exit) can sell."""
    ctx = SimpleNamespace(row=lambda s: {"close": 10.0}, profile=SimpleNamespace(hard_stop=None), mode="live",
                          news=lambda s: report, market=SimpleNamespace(regime=Regime.RISK_OFF))
    return RiskAwareStrategy().exit_signal(ctx, "ACME", SimpleNamespace(gain=-0.4))


def test_a_press_release_repeated_by_others_is_one_source_and_the_company_denial_clears_it():
    # a fake release on a press wire, rebroadcast word for word by a TV channel, then disowned by the company
    fake = [item("ACME Corp restates quarterly earnings and CEO steps down", "Internet Wire", hours_ago=6.0),
            item("ACME Corp restates quarterly earnings and CEO steps down", "Bloomberg Television", hours_ago=5.95)]
    denial = item("ACME Corp asks NASD to halt trading after finding fake release", "ACME Corp", hours_ago=5.25)

    before = analyze_news("ACME", fake, NewsConfig(), now=NOW)
    assert [c.status for c in before.clusters] == [NewsStatus.ISSUER]  # the rebroadcast confirms nothing
    assert {"accounting", "exec_departure"} <= set(before.red_flags_confirmed)  # blocks new buys
    assert not before.severe_confirmed and held_position_exit(before) is None  # but forces no sale
    assert NewsAgent().evaluate(before).verdict == "RED_FLAG" and before.score < 0

    after = analyze_news("ACME", fake + [denial], NewsConfig(), now=NOW)
    assert after.red_flags_confirmed == [] and after.red_flags_unconfirmed == []
    assert after.red_flags_denied == ["accounting", "exec_departure"]
    assert not after.severe_confirmed and held_position_exit(after) is None and after.score >= 0

    wires = [item("Acme raises full-year guidance", "Business Wire"), item("Acme raises full-year guidance", "GlobeNewswire")]
    assert [c.status for c in analyze_news("ACME", wires, NewsConfig(), now=NOW).clusters] == [NewsStatus.ISSUER]


def test_a_denial_is_not_a_red_flag_and_never_confirms_the_rumor():
    denial = "ACME Corp denies bankruptcy report, says it is not filing for Chapter 11"
    rep = analyze_news("ACME", [item(denial, "Reuters", hours_ago=1.0), item(denial, "Bloomberg", hours_ago=0.9)],
                       NewsConfig(), now=NOW)
    assert rep.clusters[0].denial and rep.clusters[0].denies == ["bankruptcy"]
    assert rep.red_flags_confirmed == [] and not rep.severe_confirmed and rep.score >= 0
    assert held_position_exit(rep) is None

    rumor = item("ACME Corp files for Chapter 11 bankruptcy", "totally-real-news.biz", hours_ago=2.0)
    rebuttal = item("ACME Corp denies it is filing for Chapter 11 bankruptcy", "Reuters", hours_ago=1.0)
    rep = analyze_news("ACME", [rumor, rebuttal], NewsConfig(), now=NOW)
    story = next(c for c in rep.clusters if not c.denial)
    assert story.publishers == ["totally-real-news.biz"] and story.status == NewsStatus.UNCONFIRMED
    assert rep.red_flags_unconfirmed == [] and rep.red_flags_denied == ["bankruptcy"]
    assert not rep.severe_confirmed and held_position_exit(rep) is None


def test_ch_11_abbreviations_are_bankruptcy():
    for title in ("ACME Corp files for Ch. 11 to cut costs.", "ACME files for Chap. 11 protection",
                  "ACME weighs a Chap 11 filing", "ACME files for Chapter 11"):
        assert lx.red_flags(title) == (["bankruptcy"], True), title
    stale = item("ACME Corp files for Ch. 11 to cut costs.", "Bloomberg", hours_ago=1.0)
    rep = analyze_news("ACME", [stale], NewsConfig(), now=NOW)
    assert rep.red_flags_unconfirmed == ["bankruptcy"] and rep.score < 0  # one source: a penalty, never a sell
    assert not rep.severe_confirmed and held_position_exit(rep) is None
    company = item("ACME Corp denies ACME Corp bankruptcy rumor", "ACME Corp", hours_ago=0.6)
    rep = analyze_news("ACME", [stale, company], NewsConfig(), now=NOW)
    assert rep.red_flags_unconfirmed == [] and rep.red_flags_denied == ["bankruptcy"]


def test_two_outlets_block_buys_but_only_the_company_forces_a_sale():
    reports = [item("ACME preparing Chapter 11 filing, sources say", "Reuters", hours_ago=10.0),
               item("ACME preparing to file for Chapter 11, sources say", "Bloomberg", hours_ago=9.5)]
    media = analyze_news("ACME", reports, NewsConfig(), now=NOW)
    assert media.red_flags_confirmed == ["bankruptcy"] and media.score < -0.2
    assert not media.severe_confirmed and held_position_exit(media) is None
    assert NewsAgent().evaluate(media).verdict == "RED_FLAG"

    release = item("ACME files voluntary Chapter 11 petitions", "Business Wire", hours_ago=2.0)
    confirmed = analyze_news("ACME", reports + [release], NewsConfig(), now=NOW)
    assert confirmed.severe_confirmed and confirmed.red_flags_backed == ["bankruptcy"]
    assert held_position_exit(confirmed).setups == ["thesis_break"]
    assert NewsAgent().evaluate(confirmed).verdict == "SEVERE_RED_FLAG"

    # the same release when the outlets only repeat it afterwards: still one source
    rewrites = [item("ACME files for Chapter 11 bankruptcy", "Reuters", hours_ago=1.9),
                item("ACME files for Chapter 11 bankruptcy", "Bloomberg", hours_ago=1.8)]
    alone = analyze_news("ACME", [release] + rewrites, NewsConfig(), now=NOW)
    assert "bankruptcy" in alone.red_flags_confirmed and not alone.severe_confirmed

    sec = item("8-K: Item 1.03 Bankruptcy or Receivership", "SEC EDGAR", feed="sec", hours_ago=1.0, summary="items:1.03")
    filed = analyze_news("ACME", [release, sec] + rewrites, NewsConfig(), now=NOW)
    assert filed.severe_confirmed and held_position_exit(filed).setups == ["thesis_break"]


def test_a_denial_answers_only_what_came_before_it():
    fake = item("ACME Corp restates quarterly earnings and CEO steps down", "Internet Wire", hours_ago=6.0)
    denial = item("ACME Corp asks NASD to halt trading after finding fake release", "ACME Corp", hours_ago=5.2)
    reports = [item("ACME preparing Chapter 11 filing, sources say", "Reuters", hours_ago=10.0),
               item("ACME preparing to file for Chapter 11, sources say", "Bloomberg", hours_ago=9.5)]
    rep = analyze_news("ACME", [fake, denial] + reports, NewsConfig(), now=NOW)
    # "fake release" answers the release, not what two outlets reported on their own
    assert rep.red_flags_denied == ["accounting", "exec_departure"] and rep.red_flags_confirmed == ["bankruptcy"]

    # the company files after its denial: its own release counts again and confirms the earlier reports
    release = item("ACME files voluntary Chapter 11 petitions", "Business Wire", hours_ago=2.0)
    no_plans = item("ACME Corp says it has no plans to file for Chapter 11", "ACME Corp", hours_ago=8.0)
    rep = analyze_news("ACME", reports + [no_plans, release], NewsConfig(), now=NOW)
    assert rep.severe_confirmed and rep.red_flags_backed == ["bankruptcy"]
    assert held_position_exit(rep).setups == ["thesis_break"]


def test_denials_need_enough_sourcing_and_cannot_clear_allegations():
    story = [item("ACME restates three years of results", "Reuters", hours_ago=6.0),
             item("ACME restates three years of results", "Wall Street Journal", hours_ago=5.9)]
    blog = item("ACME denies restatement report", "acme-investor-blog.net", hours_ago=2.0)
    rep = analyze_news("ACME", story + [blog], NewsConfig(), now=NOW)
    assert "accounting" in rep.red_flags_confirmed and rep.red_flags_denied == []  # one blog < two outlets

    allegation = [item("Short seller report alleges fraud at ACME", "Reuters", hours_ago=5.0),
                  item("ACME hit by short seller report alleging fraud", "Bloomberg", hours_ago=4.8)]
    response = item("ACME denies fraud allegations, calls short report baseless", "Business Wire", hours_ago=3.0)
    rep = analyze_news("ACME", allegation + [response], NewsConfig(), now=NOW)
    assert {"fraud", "short_report"} <= set(rep.red_flags_confirmed) and rep.red_flags_denied == []
    assert not rep.severe_confirmed


def test_negated_red_flags():
    denied = {
        "ACME has no plans to file for bankruptcy": "bankruptcy",
        "ACME bankruptcy rumors are false, CFO says": "bankruptcy",
        "ACME calls Chapter 11 report baseless": "bankruptcy",
        "ACME says it will not be delisted": "delisting",
        "Fake press release says ACME CEO resigns": "exec_departure",
    }
    for title, flag in denied.items():
        assert lx.red_flags(title)[0] == [] and lx.denied_flags(title) == [flag] and lx.is_denial(title), title
    asserted = {
        "Judge rejects ACME bankruptcy plan": ["bankruptcy"],
        "FDA rejects ACME drug, Reuters reports": ["regulatory_rejection"],
        "SEC: ACME made false statements in fraud scheme": ["fraud"],
        "ACME restates results that were false": ["accounting"],
        "ACME will not pay dividend and may file for bankruptcy": ["bankruptcy"],
    }
    for title, flags in asserted.items():
        assert lx.red_flags(title)[0] == flags and not lx.is_denial(title), title
    assert lx.sentiment("ACME denies bankruptcy report") == 0.0
    assert lx.sentiment("ACME does not expect to raise guidance") == 0.0
