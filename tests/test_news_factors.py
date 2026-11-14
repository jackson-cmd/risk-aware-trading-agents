"""research.news_factors: news features that only use what was published before each close, and the statistics
that test them."""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from riskaware.news.history import decision_time, load_bundle
from riskaware.research import news_factors as nf
from riskaware.research.periods import get_period

NY = ZoneInfo("America/New_York")
# enough stocks for a cross-section (the price controls need 15), few enough to keep the tests quick
STOCKS = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "JPM", "XOM", "KO", "PG", "WMT", "UNH", "HD",
          "NFLX", "AMD"]
CUT = date(2026, 4, 8)


def _ns(y, m, d, hh, mm, ss=0) -> int:
    return int(datetime(y, m, d, hh, mm, ss, tzinfo=NY).timestamp() * 1e9)


# --------------------------------------------------------------------------------------------- the joins
def test_publication_join_counts_an_item_from_the_first_close_after_it():
    days = [date(2025, 12, 23), date(2025, 12, 24), date(2025, 12, 26), date(2025, 12, 29)]  # the 24th closes at 13:00
    pub = nf.cutoff_times(days, "publication")
    cal = nf.cutoff_times(days, "calendar")
    cases = {  # timestamp -> (day by publication time, day by calendar date)
        _ns(2025, 12, 23, 15, 59, 59): (0, 0),
        _ns(2025, 12, 23, 16, 0, 0): (1, 0),  # exactly at the close: after it
        _ns(2025, 12, 23, 16, 5): (1, 0),
        _ns(2025, 12, 23, 23, 59): (1, 0),
        _ns(2025, 12, 24, 0, 0): (1, 1),
        _ns(2025, 12, 24, 12, 59): (1, 1),
        _ns(2025, 12, 24, 13, 30): (2, 1),  # after the early close
        _ns(2025, 12, 25, 10, 0): (2, 2),  # a holiday counts on the next trading day either way
        _ns(2025, 12, 27, 10, 0): (3, 3),  # a Saturday
    }
    ts = np.array(list(cases), dtype=np.int64)
    assert nf.assign_days(ts, pub).tolist() == [v[0] for v in cases.values()]
    assert nf.assign_days(ts, cal).tolist() == [v[1] for v in cases.values()]
    assert pub[1] == int(decision_time(date(2025, 12, 24)).timestamp() * 1e9)
    with pytest.raises(ValueError):
        nf.cutoff_times(days, "lunch")


# --------------------------------------------------------------------------------------------- no look-ahead
def _records() -> list[dict]:
    bundle = load_bundle(get_period("2026").news_path)
    return [dict(r) for s in STOCKS for r in bundle.get(s, ())]


def _rewrite_after(records: list[dict], cutoff: datetime) -> list[dict]:
    """Every item stamped at or after ``cutoff`` gets another headline, and new items appear after it."""
    out = []
    for i, r in enumerate(records):
        r = dict(r)
        ts = datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))
        if ts >= cutoff:
            r["headline"] = (f"{r['symbol']} files for bankruptcy amid fraud probe, CEO resigns" if i % 2 else
                             f"{r['symbol']} beats estimates and raises its outlook, record quarter")
            if r.get("feed") == "sec":
                r["summary"] = "items:2.02,9.01"
        out.append(r)
    for k, sym in enumerate(STOCKS):
        for minutes in (0, 1, 125, 60 * 24 + 7):  # right at the close, just after, the evening, the next day
            stamp = cutoff + timedelta(minutes=minutes, seconds=k)
            out.append({"ts": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"), "symbol": sym, "source": "Benzinga",
                        "feed": "benzinga", "headline": f"{sym} and AAPL soar after a surprise buyback"})
        out.append({"ts": (cutoff + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"), "symbol": sym,
                    "source": "SEC EDGAR", "feed": "sec", "headline": "8-K: Item 2.02 Results of operations",
                    "summary": "items:2.02,9.01", "url": f"https://example.invalid/{sym}"})
    return out


def _same(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    return a.shape == b.shape and bool(((a == b) | (a.isna() & b.isna())).all().all())


@pytest.fixture(scope="module")
def panels():
    cutoff = decision_time(CUT)
    recs = _records()
    honest = nf.NewsPanel("2026", stocks=STOCKS, records=recs)
    changed = nf.NewsPanel("2026", stocks=STOCKS, records=_rewrite_after(recs, cutoff))
    return honest, changed


def test_features_ignore_everything_published_at_or_after_the_close(panels):
    honest, changed = panels
    a, b = honest.features("publication"), changed.features("publication")
    assert set(a) == {"headlines", "attention", "sentiment", "news_score", "confirmed", "single_source",
                      "red_flags", "earnings_8k", "novelty", "co_mentions"}
    for name in a:
        assert _same(a[name].loc[:CUT], b[name].loc[:CUT]), f"{name} changed on or before {CUT}"
    later = [n for n in a if not _same(a[n].loc[CUT + timedelta(days=1):], b[n].loc[CUT + timedelta(days=1):])]
    assert {"headlines", "sentiment", "news_score", "red_flags", "earnings_8k"} <= set(later)


def test_a_calendar_date_join_reads_the_evening_news(panels):
    """The shortcut the comparison measures: the same rewrite changes the calendar-date features of the cut day."""
    honest, changed = panels
    a, b = honest.features("calendar", report=False), changed.features("calendar", report=False)
    moved = [n for n in a if not _same(a[n].loc[[CUT]], b[n].loc[[CUT]])]
    assert {"headlines", "sentiment", "earnings_8k"} <= set(moved)


def test_the_features_look_like_the_bundled_news(panels):
    honest, _ = panels
    f = honest.features("publication", report=False)
    assert f["headlines"].index[0] == date(2026, 1, 2) and f["headlines"].index[-1] == date(2026, 11, 11)
    assert list(f["headlines"].columns) == STOCKS
    assert f["headlines"].min().min() >= 0 and f["headlines"].sum().sum() > 1000
    assert f["sentiment"].stack().dropna().between(-1, 1).all()
    assert f["novelty"].stack().dropna().between(0, 1).all()
    assert set(np.unique(f["earnings_8k"].to_numpy())) <= {0.0, 1.0} and f["earnings_8k"].to_numpy().sum() >= 40
    assert f["sentiment"].isna().any().any()  # stocks without news are not ranked on tone
    moved = honest.moved_share()
    assert 0.08 < moved["benzinga"] < 0.25 and 0.5 < moved["sec"] < 0.75


# --------------------------------------------------------------------------------------------- statistics
def _spearman(x: pd.Series, y: pd.Series) -> float:
    ok = x.notna() & y.notna()
    return float(np.corrcoef(x[ok].rank(), y[ok].rank())[0, 1])


def test_rank_ic_is_the_daily_spearman_correlation():
    rng = np.random.default_rng(1)
    days, stocks = pd.Index(range(40)), [f"S{i}" for i in range(30)]
    f = pd.DataFrame(rng.integers(0, 6, (40, 30)).astype(float), index=days, columns=stocks)  # many ties
    r = pd.DataFrame(rng.normal(size=(40, 30)), index=days, columns=stocks)
    f[f > 4] = np.nan
    r.iloc[3, :25] = np.nan  # five stocks left that day: too few
    ic = nf.rank_ic(f, r)
    assert math.isnan(ic.iloc[3])
    for d in (0, 7, 39):
        assert ic.iloc[d] == pytest.approx(_spearman(f.iloc[d], r.iloc[d]), abs=1e-12)
    assert nf.rank_ic(r, r).dropna().eq(1.0).all()
    assert np.allclose(nf.rank_ic(-r.pow(3), r).dropna(), -1.0)
    assert nf.rank_ic(f * 0 + 1, r).isna().all()  # no spread in the factor: no IC


def test_a_planted_signal_is_found_and_noise_is_not():
    rng = np.random.default_rng(7)
    days, stocks = pd.Index(range(250)), [f"S{i}" for i in range(60)]
    f = pd.DataFrame(rng.normal(size=(250, 60)), index=days, columns=stocks)
    market = rng.normal(0, 0.01, (250, 1))
    signal = pd.DataFrame(market + 0.002 * f.to_numpy() + rng.normal(0, 0.02, (250, 60)), index=days, columns=stocks)
    noise = pd.DataFrame(market + rng.normal(0, 0.02, (250, 60)), index=days, columns=stocks)
    s = nf.ic_summary(nf.rank_ic(f, signal))
    assert 0.05 < s["mean_ic"] < 0.15 and s["t"] > 8 and s["hit_rate"] > 0.7 and s["days"] == 250
    assert abs(nf.ic_summary(nf.rank_ic(f, noise))["t"]) < 3
    g = nf.group_returns(f, signal)
    means = g.mean()
    assert list(means.index) == [1, 2, 3, 4, 5] and means.is_monotonic_increasing
    assert abs(g.mean(axis=1)).max() < 1e-12  # groups are measured against the day's mean
    sp = nf.spread(g)
    assert sp.mean() == pytest.approx(means[5] - means[1], rel=1e-9)
    assert 0.002 < sp.mean() < 0.008


def test_tied_values_share_a_group_and_the_spread_uses_the_groups_that_exist():
    f = pd.DataFrame([[0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1]], dtype=float)
    r = pd.DataFrame([[0, 0, 0, 0, 0, 0, 0, 0, 4, 4, 4, 4]], dtype=float) / 100
    g = nf.group_returns(f, r)
    filled = g.iloc[0].dropna()
    assert len(filled) == 2  # all zeros in one group, all ones in another
    assert nf.spread(g).iloc[0] == pytest.approx(0.04)


def test_newey_west_matches_the_plain_t_without_lags_and_widens_with_autocorrelation():
    rng = np.random.default_rng(3)
    x = rng.normal(0.1, 1, 500)
    m, se, t, n = nf.newey_west(x, 0)
    assert n == 500 and m == pytest.approx(x.mean())
    assert se == pytest.approx(x.std() / math.sqrt(500))
    ar = np.zeros(500)
    for i in range(1, 500):
        ar[i] = 0.8 * ar[i - 1] + rng.normal()
    assert nf.newey_west(ar, 10)[1] > 2 * nf.newey_west(ar, 0)[1]
    assert math.isnan(nf.newey_west(x[:5], 1)[2])


def test_turnover_of_a_factor_that_never_changes_is_zero():
    f = pd.DataFrame(np.tile(np.arange(20.0), (30, 1)))
    tv = nf.turnover(f)
    assert tv["rank_autocorr"] == pytest.approx(1.0) and tv["top_turnover"] == 0 and tv["bottom_turnover"] == 0


def test_forward_returns_start_at_the_next_open():
    idx = pd.Index([date(2026, 1, d) for d in (5, 6, 7, 8, 9)])
    o = pd.DataFrame({"A": [100.0, 110.0, 121.0, 133.1, 146.41]}, index=idx)
    c = o * 2
    fo = nf.forward_returns(o, c, (1, 2), "open")
    assert fo[1]["A"].iloc[0] == pytest.approx(121 / 110 - 1) and fo[2]["A"].iloc[0] == pytest.approx(133.1 / 110 - 1)
    assert math.isnan(fo[1]["A"].iloc[3])
    fc = nf.forward_returns(o, c, (1,), "close")
    assert fc[1]["A"].iloc[0] == pytest.approx(0.1)
    pf = nf.price_factors(c)
    assert pf["reversal_1d"]["A"].iloc[1] == pytest.approx(-0.1)


# --------------------------------------------------------------------------------------------- the study
@pytest.fixture(scope="module")
def study():
    return nf.run_study(["2026"], stocks=STOCKS, report=False)


def test_the_study_tests_each_factor_and_compares_the_joins(study, tmp_path):
    assert study.periods == ["2026"]
    assert {"headlines", "sentiment", "earnings_8k", "momentum", "low_vol"} <= set(study.pooled)
    for name, v in study.verdicts.items():
        assert v["verdict"] in {"useful", "weak", "noise"}, name
        assert name in nf.NEWS_FACTORS
    s = study.pooled["sentiment"]
    assert set(s) >= {"h1", "h5", "h20", "decay", "turnover", "regimes", "breakeven_cost_bp"}
    assert len(s["decay"]) == len(nf.DECAY_LAGS) and s["decay"][0] == s["h1"]["mean_ic"]
    al = study.pooled_alignment["sentiment"]["h1"]
    assert set(al) == {*nf.ALIGN_CELLS, "leak", "total"}
    assert al["leak"]["mean_ic"] == pytest.approx(al["calendar_close"]["mean_ic"] - al["publication_close"]["mean_ic"],
                                                  abs=2e-4)
    folder = nf.save_study(study, tmp_path / "out", langs=("en", "zh"))
    data = json.loads((folder / "factors.json").read_text(encoding="utf-8"))
    assert data["periods"] == ["2026"] and "sentiment" in data["verdicts"] and data["headline"]["verdicts"]
    assert data["coverage"]["2026"]["dataset"]["news"]
    md = (folder / "factors.md").read_text(encoding="utf-8")
    assert "Calendar date against publication time" in md and "Lexicon sentiment" in md
    assert "按日历日期对齐和按发布时间对齐" in (folder / "factors_zh.md").read_text(encoding="utf-8")
    for name in ("ic", "alignment", "cumulative", "groups", "decay"):
        for lg in ("en", "zh"):
            assert (folder / f"{name}_{lg}.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    daily = pd.read_csv(folder / "daily_ic.csv")
    assert set(daily.columns) == {"period", "day", "factor", "rank_ic_1d"} and len(daily) > 100


def test_the_verdict_rule():
    st = nf.FactorStudy(periods=["2026", "2022"])
    st.pooled["sentiment"] = {"h1": {"mean_ic": 0.02, "t": 3.0}, "h5": {"mean_ic": 0.01, "t": 1.0},
                              "periods": ["2026", "2022"]}
    st.by_period = {"2026": {"sentiment": {"h1": {"mean_ic": 0.03}}}, "2022": {"sentiment": {"h1": {"mean_ic": 0.01}}}}
    st.pooled_controls["sentiment"] = {"h1": {"slope_t": 2.4}}
    assert nf.verdict("sentiment", st)["verdict"] == "useful"
    st.pooled_controls["sentiment"] = {"h1": {"slope_t": 1.1}}
    assert nf.verdict("sentiment", st)["verdict"] == "weak"
    st.pooled_controls["sentiment"] = {"h1": {"slope_t": 2.4}}
    st.by_period["2022"]["sentiment"]["h1"]["mean_ic"] = -0.01
    assert "sign changes" in nf.verdict("sentiment", st)["why"]
    st.pooled["sentiment"]["h1"]["t"] = 1.9
    assert nf.verdict("sentiment", st)["verdict"] == "noise"


def test_the_cli_writes_the_report(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from riskaware.cli import app

    real = nf.run_study
    monkeypatch.setattr(nf, "run_study", lambda periods, **kw: real(periods, stocks=STOCKS, report=False, **{
        k: v for k, v in kw.items() if k not in ("stocks", "report")}))
    res = CliRunner().invoke(app, ["research", "factors", "--period", "2026", "--out", str(tmp_path / "f")])
    assert res.exit_code == 0, res.output
    assert (tmp_path / "f" / "factors.json").is_file() and (tmp_path / "f" / "ic_en.png").is_file()
    bad = CliRunner().invoke(app, ["research", "factors", "--period", "1999"])
    assert bad.exit_code == 2


# --------------------------------------------------------------------------------------------- LLM scores
class FakeProvider:
    """Answers with the wrong number of scores for long batches, like a small model that loses count."""

    def __init__(self, fail: bool = False):
        self.calls, self.fail = 0, fail
        self.last_usage, self.last_error = {}, None

    def complete_json(self, system, user, schema):
        self.calls += 1
        if self.fail:
            self.last_error = "ConnectionError: refused"
            return None
        lines = [ln for ln in user.splitlines() if ln[:1].isdigit() and ". [" in ln]
        n = len(lines) if len(lines) <= 2 else len(lines) - 1
        self.last_usage = {"prompt_tokens": 10 * len(lines), "completion_tokens": 3 * n}
        return {"scores": [(-1.5 if "bankrupt" in ln else 0.5) for ln in lines[:n]]}


def test_headline_scores_are_cached_and_recovered_from_miscounts(tmp_path):
    pairs = [("AAPL", f"Apple headline {i}") for i in range(7)] + [("TSLA", "Tesla goes bankrupt")]
    prov = FakeProvider()
    sc = nf.HeadlineScorer("ollama:llama3.2:3b", cache_dir=tmp_path, batch=8, provider=prov)
    est = sc.estimate(pairs)
    assert est["calls"] == 1 and est["usd"] == 0.0 and est["input_tokens"] > 0
    scores = sc.score(pairs)
    assert len(scores) == 8 and sc.failed == 0 and prov.calls > 1
    assert scores[nf.headline_key("TSLA", "Tesla goes bankrupt")] == -1.0  # clipped to [-1, 1]
    again = nf.HeadlineScorer("ollama:llama3.2:3b", cache_dir=tmp_path, batch=8, provider=FakeProvider())
    assert again.missing(pairs) == [] and again.scores == scores
    down = nf.HeadlineScorer("ollama:llama3.2:3b", cache_dir=tmp_path / "x", provider=FakeProvider(fail=True))
    with pytest.raises(nf.LLMUnavailable):
        down.score(pairs)
    assert not nf.HeadlineScorer("openai:gpt-5.6-luna", cache_dir=tmp_path).local


def test_llm_scores_become_a_factor():
    recs = [r for r in _records() if r["symbol"] in STOCKS[:12]]
    scores = {nf.headline_key(r["symbol"], r["headline"]): (1.0 if "beat" in r["headline"].lower() else -0.2)
              for r in recs if r.get("feed") != "sec"}
    panel = nf.NewsPanel("2026", stocks=STOCKS[:12], records=recs, llm_scores=scores)
    f = panel.features("publication", report=False)["llm_sentiment"]
    assert f.stack().dropna().between(-1, 1).all() and f.notna().sum().sum() > 500
    assert (f.stack().dropna() > 0).any() and (f.stack().dropna() < 0).any()


def test_pairs_to_score_skip_filings_and_duplicates():
    pairs = nf.headlines_to_score(["2026"])
    assert len(pairs) == len(set(pairs)) > 15000
    assert not any(h.startswith("8-K:") for _, h in pairs)


# --------------------------------------------------------------------------------------------- the published numbers
ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent


def test_the_guide_numbers_still_hold():
    """The join comparison the website and docs quote comes from the frozen data of all three periods."""
    parts = [nf.alignment_series("sentiment", nf.load_period(p, report=False)) for p in ("2026", "2022", "2020")]
    h = nf.summarize_alignment(parts)["h1"]
    honest, shortcut, leak = h["publication_open"], h["calendar_close"], h["leak"]
    assert honest["mean_ic"] == pytest.approx(0.0050, abs=1e-4) and honest["t"] == pytest.approx(0.58, abs=0.01)
    assert shortcut["mean_ic"] == pytest.approx(0.0318, abs=1e-4) and shortcut["t"] == pytest.approx(3.43, abs=0.01)
    assert honest["spread_bp"] == pytest.approx(7.4, abs=0.05) and shortcut["spread_bp"] == pytest.approx(36.9, abs=0.05)
    assert leak["mean_ic"] == pytest.approx(0.0178, abs=1e-4) and leak["t"] == pytest.approx(4.78, abs=0.01)
    for page in ("site/news-factors.html", "site/zh/news-factors.html", "docs/news-factors.md",
                 "docs/zh/news-factors.md"):
        text = (ROOT / page).read_text(encoding="utf-8")
        for quoted in ("0.032", "3.4", "0.005", "0.6", "36.9", "7.4", "0.018", "4.8"):
            assert quoted in text, f"{page} does not quote {quoted}"
