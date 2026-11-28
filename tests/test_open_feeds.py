"""Open news: parsing each source, tagging stocks, the day files and their de-duplication, one failing source not
stopping the others, GDELT's cursor, and reading a collected folder back into the archive point in time. Offline."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date, datetime, timedelta, timezone

import pytest
import requests

from riskaware.news import open_feeds as of
from riskaware.news.history import NewsArchive

NOW = datetime(2026, 11, 26, 18, 17, tzinfo=timezone.utc)

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Press</title>
<item><title>FDA approves Eli Lilly&#8217;s new obesity pill</title><link>https://www.fda.gov/a</link>
<guid>fda-a</guid><pubDate>Wed, 26 Nov 2026 14:05:00 -0500</pubDate>
<description>&lt;p&gt;The agency approved the drug.&lt;/p&gt;</description></item>
<item><title>Old notice</title><link>https://www.fda.gov/old</link><pubDate>Mon, 01 Jun 2026 10:00:00 GMT</pubDate></item>
<item><title></title><link>https://www.fda.gov/empty</link></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Board</title>
<entry><title>Federal Reserve issues FOMC statement</title><link href="https://www.federalreserve.gov/x.htm"/>
<id>fed-x</id><updated>2026-11-26T19:00:00Z</updated><summary>Rates unchanged.</summary></entry></feed>"""

SEC_CURRENT = """<?xml version="1.0" encoding="ISO-8859-1" ?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>8-K - NVIDIA CORP (0001045810) (Filer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/1045810/000104581026000099/0001045810-26-000099-index.htm"/>
<summary type="html"> &lt;b&gt;Filed:&lt;/b&gt; 2026-11-26 &lt;b&gt;AccNo:&lt;/b&gt; 0001045810-26-000099 &lt;b&gt;Size:&lt;/b&gt; 300 KB&lt;br&gt;Item 2.02: Results of Operations and Financial Condition&lt;br&gt;Item 9.01: Financial Statements and Exhibits</summary>
<updated>2026-11-26T16:21:05-05:00</updated><id>urn:tag:sec.gov,2008:accession-number=0001045810-26-000099</id></entry>
<entry><title>8-K/A - TINY CO (0009999999) (Filer)</title><link href="https://www.sec.gov/t"/>
<summary type="html">AccNo: 0009999999-26-000001 Item 5.02: Departure of Directors</summary>
<updated>2026-11-26T16:30:00-05:00</updated></entry>
<entry><title>10-K - OTHER (0001111111) (Filer)</title><updated>2026-11-26T16:30:00-05:00</updated></entry>
</feed>""".encode("latin-1")


def gkg_row(url: str, title: str, stamp: str = "20261126180000", tone: str = "-3.5,1,4.5") -> str:
    cols = [""] * 27
    cols[0], cols[1], cols[3], cols[4], cols[15] = f"{stamp}-1", stamp, url.split("/")[2], url, tone
    cols[26] = f"<PAGE_TITLE>{title}</PAGE_TITLE>"
    return "\t".join(cols)


GKG = "\n".join([
    gkg_row("https://www.reuters.com/a", "Nvidia shares slide as chip curbs widen"),
    gkg_row("https://www.cnbc.com/b", "Stocks fall as Treasury yields climb"),
    gkg_row("https://www.cnbc.com/c", "Best holiday recipes"),                       # finance site, no stock or market
    gkg_row("https://www.tomshardware.com/d", "Get an RTX 5060 gaming PC cheap"),     # a product, not Raytheon
    gkg_row("https://www.someblog.example/e", "Apple unveils a new iPhone"),          # not a finance site
    gkg_row("https://news.yahoo.com/f", "Apple stock jumps"),                          # Yahoo, but not Finance
    gkg_row("https://finance.yahoo.com/g", "Apple (AAPL) beats estimates", tone="bad"),
]).encode()


def gkg_zip(text: bytes = GKG) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x.gkg.csv", text)
    return buf.getvalue()


class FakeResp:
    def __init__(self, status: int, content: bytes = b""):
        self.status_code, self.content = status, content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)


class FakeSession:
    def __init__(self, pages: dict[str, bytes | int]):
        self.pages, self.calls = pages, []

    def get(self, url, timeout=None):
        self.calls.append(url)
        page = self.pages.get(url, 404)
        return FakeResp(page, b"") if isinstance(page, int) else FakeResp(200, page)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)


# --------------------------------------------------------------------------- parsing and tagging
def test_parse_rss_and_atom():
    rows = of.parse_rss(RSS, of.FEEDS["fda"], NOW)
    assert [r.title for r in rows] == ["FDA approves Eli Lilly’s new obesity pill", "Old notice"]
    first = rows[0]
    assert first.published == datetime(2026, 11, 26, 19, 5, tzinfo=timezone.utc) and first.seen == NOW
    assert first.summary == "The agency approved the drug." and first.symbols == ["LLY"]
    assert first.to_json()["published"] == "2026-11-26T19:05:00Z"
    atom = of.parse_rss(ATOM, of.FEEDS["fed"], NOW)
    assert atom[0].url == "https://www.federalreserve.gov/x.htm" and atom[0].key == "fed-x"


def test_parse_sec_current_8k():
    rows = of.parse_sec_current(SEC_CURRENT, of.FEEDS["sec-8k"], NOW, {1045810: "NVDA"})
    assert len(rows) == 2
    nv = rows[0].to_json()
    assert nv["symbols"] == ["NVDA"] and nv["items"] == ["2.02", "9.01"] and nv["form"] == "8-K"
    assert nv["title"].startswith("NVIDIA CORP: 8-K: Item 2.02 Results of operations")
    assert nv["published"] == "2026-11-26T21:21:05Z" and nv["cik"] == 1045810
    assert rows[1].symbols == [] and rows[1].extra["form"] == "8-K/A"
    assert rows[0].id != rows[1].id


@pytest.mark.parametrize("text,expected", [
    ("Get an RTX 5060 gaming PC", []), ("Raytheon wins a contract", ["RTX"]), ("RTX stock jumps", ["RTX"]),
    ("Rising cost of living", []), ("Costco sales rise", ["COST"]), ("$V rallies", ["V"]),
    ("Apple (AAPL) beats", ["AAPL"]), ("NYSE: GE climbs", ["GE"]), ("Visa and Mastercard settle", ["MA", "V"]),
])
def test_tag_symbols(text, expected):
    assert of.tag_symbols(text) == expected


def test_parse_gkg_keeps_finance_sites_with_a_stock_or_the_market():
    rows = of.parse_gkg(GKG, of.FEEDS["gdelt"])
    got = {r.url: r for r in rows}
    assert set(got) == {"https://www.reuters.com/a", "https://www.cnbc.com/b", "https://finance.yahoo.com/g"}
    nv = got["https://www.reuters.com/a"].to_json()
    assert nv["symbols"] == ["NVDA"] and nv["site"] == "reuters.com" and nv["tone"] == -3.5
    assert nv["published"] == nv["seen"] == "2026-11-26T18:00:00Z" and "market" not in nv
    mk = got["https://www.cnbc.com/b"].to_json()
    assert mk["symbols"] == [] and mk["market"] is True
    assert got["https://finance.yahoo.com/g"].to_json()["tone"] is None


def test_finance_site():
    assert of.finance_site("https://www.reuters.com/x") and of.finance_site("https://markets.businessinsider.com/x")
    assert of.finance_site("https://finance.yahoo.com/x") and of.finance_site("https://www.yahoo.com/finance/x")
    assert not of.finance_site("https://news.yahoo.com/x") and not of.finance_site("https://notreuters.com/x")


# --------------------------------------------------------------------------- the store
def test_store_appends_new_rows_to_day_files(tmp_path):
    store = of.Store(tmp_path)
    rows = of.parse_rss(RSS, of.FEEDS["fda"], NOW)
    assert store.add(rows, 45) == 2
    assert store.add(of.parse_rss(RSS, of.FEEDS["fda"], NOW + timedelta(days=1)), 45) == 0   # seen yesterday
    p = store.path("fda", NOW.date())
    assert p == tmp_path / "data" / "fda" / "2026" / "2026-11-26.jsonl"
    lines = [json.loads(x) for x in p.read_text().splitlines()]
    assert len(lines) == 2 and all(x["source"] == "fda" for x in lines)
    titles = [r["title"] for r in store.rows(date(2026, 11, 1), date(2026, 11, 30))]
    assert titles == ["Old notice", "FDA approves Eli Lilly’s new obesity pill"]   # oldest first within a run


def test_collect_reports_each_source_and_never_stops(tmp_path):
    store = of.Store(tmp_path)
    s = FakeSession({of.FEEDS["fda"].urls[0]: RSS, of.FEEDS["fed"].urls[0]: 500})
    rep = of.Collector(store, session=s, now=NOW).collect(["fed", "fda", "sec-press"])
    assert "error" in rep["fed"] and rep["fda"] == {"read": 1, "new": 1}   # the June item is older than 45 days
    assert "contact email" in rep["sec-press"]["error"]
    state = store.state()
    assert state["runs"]["fda"]["new"] == 1 and state["runs"]["fda"]["at"] == "2026-11-26T18:17:00Z"


def test_collect_sec_8k_maps_ciks_to_tickers(tmp_path):
    tickers = json.dumps({"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"}}).encode()
    url = of.FEEDS["sec-8k"].urls[0]
    s = FakeSession({"https://www.sec.gov/files/company_tickers.json": tickers, url.format(start=0): SEC_CURRENT,
                     url.format(start=100): b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'})
    rep = of.Collector(of.Store(tmp_path), contact="me@example.com", session=s, now=NOW).collect(["sec-8k"])
    assert rep["sec-8k"] == {"read": 2, "new": 2}


def test_gdelt_cursor_and_missing_batches(tmp_path):
    store = of.Store(tmp_path)
    store.save_state({"gdelt": {"last": "20261126171500"}})
    last = b"1 2 http://data.gdeltproject.org/gdeltv2/20261126180000.gkg.csv.zip\n"
    pages = {of.GDELT_LAST: last,
             of.GDELT_FILE.format(stamp="20261126173000"): gkg_zip(),
             of.GDELT_FILE.format(stamp="20261126174500"): 404,
             of.GDELT_FILE.format(stamp="20261126180000"): gkg_zip()}
    rep = of.Collector(store, session=FakeSession(pages), now=NOW).collect(["gdelt"])
    assert rep["gdelt"] == {"read": 6, "new": 3}          # the same three articles in two batches count once
    assert store.state()["gdelt"]["last"] == "20261126180000"
    again = of.Collector(store, session=FakeSession(pages), now=NOW).collect(["gdelt"], gdelt_batches=8)
    assert again["gdelt"] == {"read": 0, "new": 0}


def test_backfill_gdelt(tmp_path):
    pages = {of.GDELT_FILE.format(stamp="20200316150000"): gkg_zip(GKG.replace(b"20261126180000",
                                                                               b"20200316150000"))}
    store = of.Store(tmp_path)
    col = of.Collector(store, session=FakeSession(pages), now=NOW)
    start = datetime(2020, 3, 16, 14, 50, tzinfo=timezone.utc)
    assert of.backfill_gdelt(store, start, start + timedelta(minutes=20), col) == 3
    assert store.path("gdelt", date(2020, 3, 16)).exists()


# --------------------------------------------------------------------------- back into the archive
def test_to_items_dates_rows_when_first_seen(tmp_path):
    rows = [
        {"source": "fda", "publisher": "U.S. Food and Drug Administration", "title": "FDA approves Lilly drug",
         "url": "u1", "published": "2026-11-20T10:00:00Z", "seen": "2026-11-26T18:17:00Z", "symbols": ["LLY"]},
        {"source": "sec-8k", "title": "NVIDIA CORP: 8-K", "url": "u2", "published": "2026-11-26T21:21:05Z",
         "seen": "2026-11-26T22:17:00Z", "symbols": ["NVDA"], "items": ["2.02", "9.01"]},
        {"source": "fed", "title": "FOMC statement", "url": "u3", "seen": "2026-11-26T19:17:00Z", "symbols": []},
    ]
    items = of.to_items(rows)
    assert [i.symbol for i in items] == ["LLY", "NVDA"]
    assert items[0].published == datetime(2026, 11, 26, 18, 17, tzinfo=timezone.utc) and items[0].feed == "open:fda"
    assert items[1].feed == "sec" and items[1].summary == "items:2.02,9.01" and items[1].publisher == "SEC EDGAR"
    archive = NewsArchive(tmp_path / "archive", bundled=False)
    assert archive.add(items) == 2 and archive.add(items) == 0


def test_pull_imports_a_collected_folder(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from riskaware import cli

    folder = tmp_path / "open-news"
    path = of.Store(folder).path("fda", date(2026, 11, 26))
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"id": "fda:1", "source": "fda", "publisher": "U.S. Food and Drug Administration",
                                "title": "FDA approves Lilly drug", "url": "u1", "seen": "2026-11-26T18:17:00Z",
                                "symbols": ["LLY"]}) + "\n", encoding="utf-8")
    archive = NewsArchive(tmp_path / "archive", bundled=False)
    monkeypatch.setattr(cli, "_archive", lambda: archive)
    res = CliRunner().invoke(cli.app, ["news-history", "pull", "--start", "2026-11-01", "--end", "2026-11-30",
                                       "--from", str(folder)])
    assert res.exit_code == 0, res.output
    assert [i.symbol for i in archive.items("LLY")] == ["LLY"]
    missing = CliRunner().invoke(cli.app, ["news-history", "pull", "--start", "2026-11-01",
                                           "--from", str(tmp_path / "nowhere")])
    assert missing.exit_code == 2 and "collect" in missing.output


def test_every_feed_says_where_it_may_be_republished():
    for f in of.FEEDS.values():
        assert f.terms.startswith("https://") and f.credit
