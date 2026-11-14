"""The bundled periods (2020, 2022, 2026) and the look-ahead check of LLM runs."""

from __future__ import annotations

import gzip
import json
from datetime import date, timedelta

import pytest

from riskaware.news import history
from riskaware.news.relevance import company_names, mentions
from riskaware.research import agent
from riskaware.research.data import available_periods, bundled_market, dataset_info, manifest, verify
from riskaware.research.day import MarketView
from riskaware.research.experiment import Experiment, run_experiment
from riskaware.research.periods import PERIODS, get_period, period_of

from .conftest import HAS_NEWS, needs_news, news_file

SPRING_2022 = {"period": "2022", "start": "2022-02-01", "end": "2022-05-31"}


def momentum(obs: dict) -> dict:
    """A stand-in for an LLM: hold the two stocks with the best month."""
    best = sorted(obs["stocks"], key=lambda d: -(d.get("ret_21d") or -9))[:2]
    return {"orders": [{"symbol": d["symbol"], "action": "target", "weight": 0.4} for d in best], "note": "momentum"}


# ----------------------------------------------------------------------------------- data
def test_every_period_ships_and_matches_its_manifest():
    assert available_periods() == ["2020", "2022", "2026"]
    checks = verify()
    assert len(checks) == 6
    for name, p in PERIODS.items():
        assert checks[f"market_{p.end.year}.csv.gz"] is True
        # the headlines are licensed and not distributed: checked against the manifest only where there is a copy
        assert checks[p.news] is (True if HAS_NEWS else None)
        mk = bundled_market(name)
        assert mk.period == name and mk.last_day == p.end and mk.experiment_start >= p.start
        assert mk.first_day.year == p.start.year - 2  # two warm-up years
        assert len(manifest(name)["symbols"]["stocks"]) >= 63
        info = dataset_info(name)
        assert info["prices"]["file"] == f"market_{p.end.year}.csv.gz"
        news = info["news"]
        assert news["manifest_sha256"] == manifest(name)["files"][p.news]["sha256"] and news["distributed"] is False
        assert news["status"] == ("ok" if HAS_NEWS else "not distributed")
        assert news["matches_manifest"] is (True if HAS_NEWS else None)


def test_periods_by_name_and_by_day():
    assert get_period(None).name == "2026" and get_period(2022).name == "2022"
    with pytest.raises(ValueError):
        get_period("1999")
    assert period_of(date(2022, 6, 13)).name == "2022"
    assert period_of(date(2021, 3, 1)).name == "2022"  # 2022's warm-up
    assert period_of(date(2020, 3, 16)).name == "2020"  # a test window wins over another period's warm-up
    assert period_of(date(2025, 5, 1)).name == "2026"
    with pytest.raises(ValueError):
        period_of(date(2023, 6, 1))


def test_symbols_that_did_not_trade_yet_are_left_out():
    assert bundled_market("2026").has("IBIT") and not bundled_market("2022").has("IBIT")
    assert "IBIT" in manifest("2020")["not_trading_yet"]
    assert bundled_market("2020").raw("CRWD").index[0].year == 2019  # listed in June 2019


@needs_news
@pytest.mark.parametrize("name", ["2020", "2022"])
def test_period_news_is_in_its_window_and_names_its_stock(name):
    p = get_period(name)
    with gzip.open(p.news_path, "rt", encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh]
    feeds = {r["feed"] for r in rows}
    assert len(rows) > 15_000 and len(feeds) == 1 and "sec" not in feeds  # one headline feed, no SEC filings
    assert min(r["ts"] for r in rows) >= f"{name}-01-01" and max(r["ts"][:10] for r in rows) <= p.end.isoformat()
    assert len({r["symbol"] for r in rows}) == 64
    for r in rows[:: len(rows) // 400]:
        named = mentions(r["headline"], r["symbol"], company_names(r["symbol"], fetch=False))
        assert named or (r["symbol"] == "META" and mentions(r["headline"], "FB", [])), r


@needs_news
def test_an_archive_reads_every_bundled_year_unless_given_one(monkeypatch, tmp_path):
    monkeypatch.setattr(history, "BUNDLE_PATH", news_file("news_2026.jsonl.gz"))
    monkeypatch.setattr(history, "EARLIER_BUNDLES", (news_file("news_2020.jsonl.gz"), news_file("news_2022.jsonl.gz")))
    years = {r["ts"][:4] for r in history.NewsArchive(tmp_path, bundled=True).records("NVDA")}
    assert {"2020", "2022", "2026"} <= years
    one = history.NewsArchive(tmp_path, bundled=True, bundle_path=get_period("2022").news_path)
    assert {r["ts"][:4] for r in one.records("NVDA")} == {"2022"}


# ----------------------------------------------------------------------------------- experiments
def test_market_view_and_experiment_on_2022():
    snap = MarketView(period="2022").snapshot("2022-06-13", symbols=["NVDA"], headlines=2)
    assert snap["day"] == "2022-06-13" and snap["market"]["vix"] > 30  # the day CPI sent SPY into a bear market
    r = run_experiment(Experiment(strategy="swing_rsi2", universe="core", **SPRING_2022), save=False)
    assert r.leak["verdict"] == "pass" and r.spy["total_return"] < -0.05
    assert r.start >= date(2022, 2, 1) and r.end <= date(2022, 5, 31)
    with pytest.raises(ValueError, match="2026 period"):
        run_experiment(Experiment(universe="core", **SPRING_2022), market=bundled_market("2026"), save=False)


@needs_news
def test_observation_check_passes_and_catches_a_news_timing_leak(monkeypatch):
    exp = Experiment(universe="core", max_positions=3, **SPRING_2022)
    r = agent.run_policy(momentum, exp, decide_every=5, save=False)
    assert r.leak["verdict"] == "pass" and r.leak["kind"] == "observations"
    assert all(c["decision_days_compared"] > 0 for c in r.leak["cuts"])
    # a harness bug: news counted three days before it was published
    real = history.decision_time
    monkeypatch.setattr(history, "decision_time", lambda day, close=None: real(day, close) + timedelta(days=3))
    bad = agent.run_policy(momentum, Experiment(universe="core", max_positions=3, **SPRING_2022), decide_every=5,
                           save=False)
    assert bad.leak["verdict"] == "fail"
    assert any(not c["same"] for c in bad.leak["cuts"])


def test_trading_session_on_2022_writes_a_checked_record():
    out = agent.session_start(universe="core", decide_every=20, max_positions=2, **SPRING_2022)
    assert out["settings"]["period"] == "2022"
    sid = out["session_id"]
    while not out.get("done"):
        out = agent.session_step(sid, [])
    rec = json.loads((agent.SESSIONS_DIR / sid / "record" / "manifest.json").read_text(encoding="utf-8"))
    assert rec["experiment"]["period"] == "2022" and rec["dataset"]["prices"]["file"] == "market_2022.csv.gz"
    assert rec["leak_check"]["verdict"] == "pass" and rec["leak_check"]["kind"] == "observations"
