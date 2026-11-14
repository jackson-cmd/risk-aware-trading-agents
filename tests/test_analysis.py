"""What a run made its money on (research/analysis.py): the statistics on synthetic data with known answers, and the
whole analysis on real records (a strategy with news, and a policy)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from riskaware.news import history
from riskaware.research import agent
from riskaware.research import analysis as A
from riskaware.research.data import FrozenMarket
from riskaware.research.experiment import Experiment, run_experiment
from riskaware.strategy.base import Strategy
from riskaware.types import Action, Signal
from tests.conftest import needs_news

REAL_NEWS = history.period_news_path("news_2026.jsonl.gz")  # your copy (not redistributed), if any
STOCKS = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]


# ----------------------------------------------------------------------------------- statistics
def test_mean_test_is_the_plain_t_for_one_value_per_day_far_apart():
    rng = np.random.default_rng(1)
    v = rng.normal(0.002, 0.01, 80)
    m = A.mean_test(v, np.arange(80) * 10, overlap=5)  # no two windows overlap
    sd = v.std(ddof=1)
    assert m["n"] == 80 and m["mean"] == pytest.approx(v.mean())
    assert m["t_iid"] == pytest.approx(v.mean() / (sd / math.sqrt(80)))
    assert m["t"] == pytest.approx(m["t_iid"])
    assert m["hit_rate"] == pytest.approx((v > 0).mean()) and m["median"] == pytest.approx(np.median(v))


def test_mean_test_counts_same_day_decisions_as_one_cluster():
    rng = np.random.default_rng(2)
    v = rng.normal(0.003, 0.01, 300)
    days = np.arange(300) * 7
    once = A.mean_test(v, days)
    twice = A.mean_test(np.r_[v, v], np.r_[days, days])  # every decision made twice on its day
    assert twice["mean"] == pytest.approx(once["mean"])
    assert twice["t_iid"] == pytest.approx(once["t_iid"] * math.sqrt(2), rel=0.01)  # the naive t is fooled
    assert twice["t"] == pytest.approx(once["t"], rel=0.01)  # the clustered one is not


def test_mean_test_overlapping_windows_widen_the_interval():
    rng = np.random.default_rng(3)
    shocks = rng.normal(0.0, 0.01, 400)
    v = np.convolve(shocks, np.ones(5), mode="valid") + 0.004  # 5-day sums of daily shocks, one per day
    days = np.arange(len(v))
    m = A.mean_test(v, days, overlap=5)
    assert abs(m["t"]) < abs(m["t_iid"]) * 0.7  # overlap shrinks the effective sample
    assert A.mean_test([], [])["n"] == 0 and A.mean_test([0.01], [3])["t"] is None


def test_rank_ic_is_spearman():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0], index=list("abcde"))
    assert A.rank_ic(s, s * 3 + 1) == pytest.approx(1.0)
    assert A.rank_ic(s, -s) == pytest.approx(-1.0)
    direction = pd.Series([1.0, 0.0, 0.0, -1.0, 0.0], index=list("abcde"))
    fwd = pd.Series([0.05, 0.01, -0.02, -0.04, 0.0], index=list("abcde"))
    rd, rf = np.array([5, 3, 3, 1, 3.0]), np.array([5, 4, 2, 1, 3.0])  # average ranks, ties shared
    assert A.rank_ic(direction, fwd) == pytest.approx(np.corrcoef(rd, rf)[0, 1])
    assert A.rank_ic(pd.Series(0.0, index=list("abcde")), fwd) is None  # a constant score says nothing
    assert A.rank_ic(direction, fwd.where(fwd > 0)) is None  # fewer than 3 stocks with a return


def test_ols_newey_west_matches_the_textbook_formula():
    rng = np.random.default_rng(4)
    n = 260
    x = rng.normal(0, 0.01, (n, 2))
    u = np.convolve(rng.normal(0, 0.004, n + 2), [1.0, 0.6, 0.3], mode="valid")  # autocorrelated errors
    y = 0.0004 + 0.9 * x[:, 0] - 0.4 * x[:, 1] + u
    out = A.ols_newey_west(y, x, ["a", "b"], lags=3)
    X = np.column_stack([np.ones(n), x])
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    assert [out["coef"][k] for k in ("alpha", "a", "b")] == pytest.approx(list(beta))
    e = y - X @ beta
    meat = sum(e[t] ** 2 * np.outer(X[t], X[t]) for t in range(n))
    for lag in range(1, 4):
        w = 1 - lag / 4
        for t in range(lag, n):
            g = e[t] * e[t - lag] * np.outer(X[t], X[t - lag])
            meat = meat + w * (g + g.T)
    bread = np.linalg.inv(X.T @ X)
    se = np.sqrt(np.diag(bread @ meat @ bread * n / (n - 3)))
    assert [out["se"][k] for k in ("alpha", "a", "b")] == pytest.approx(list(se))
    assert out["t"]["a"] == pytest.approx(beta[1] / se[1])
    assert out["r2"] == pytest.approx(1 - (e @ e) / ((y - y.mean()) @ (y - y.mean())))
    assert A.newey_west_lags(252) == 4 and out["lags"] == 3


def test_triggers_read_the_reason():
    assert A.trigger("fresh positive news: Apple beats estimates") == "news"
    assert A.trigger("confirmed red flag (sec_probe)") == "news"
    assert A.trigger("Q3 earnings beat and raised guidance") == "news"
    assert A.trigger("分析师上调评级，利好") == "news"
    assert A.trigger("EMA20 crossed above EMA50") == "other"
    assert A.trigger("beat SPY by 2.1% on 1.5x volume") == "other"
    assert A.trigger("close above the 5-day average") == "other"
    assert A.trigger("policy buy") == "no reason" and A.trigger(float("nan")) == "no reason"


# ----------------------------------------------------------------------------------- factors
def _walk(rng: np.random.Generator, n: int, drift: float = 0.0, vol: float = 0.015, start: float = 100.0):
    close = start * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    open_ = np.r_[start, close[:-1]] * np.exp(rng.normal(0.0, 0.004, n))
    return open_, close


def _frames(n: int = 330, seed: int = 0) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2025-01-02", periods=n)
    out = {}
    drifts = {"SPY": 0.0004, "BIL": 0.00015, "IWM": 0.0003, "QQQ": 0.0006, "AAA": 0.002, "BBB": 0.0008,
              "CCC": 0.0, "DDD": -0.0005, "EEE": 0.0004, "FFF": -0.001}
    for sym, mu in drifts.items():
        o, c = _walk(rng, n, mu, 0.0005 if sym == "BIL" else 0.015)
        out[sym] = pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.005, "low": np.minimum(o, c) * 0.995,
                                 "close": c, "volume": 1e6, "adj": 1.0, "dividend": 0.0, "split": 0.0}, index=idx)
    return out


def test_momentum_factor_by_hand():
    idx = pd.bdate_range("2025-01-02", periods=200)
    growth = [0.004, 0.003, 0.002, 0.001, 0.0, -0.001]  # steady daily growth: the ranks never change
    closes = pd.DataFrame({s: 100 * (1 + g) ** np.arange(200) for s, g in zip(STOCKS, growth, strict=True)},
                          index=idx)
    mom = A.momentum_factor(closes, lookback=126, skip=21)
    first = mom.first_valid_index()
    ym = idx.year * 12 + idx.month
    ends = [i for i in range(199) if ym[i] != ym[i + 1]]
    assert first == idx[next(e for e in ends if e >= 126) + 1]  # the day after the first month end with 126 days
    # top third AAA, BBB; bottom third EEE, FFF: (0.4% + 0.3%) / 2 - (0% - 0.1%) / 2 = 0.4% a day
    assert mom.dropna().to_numpy() == pytest.approx(0.004)


def test_momentum_factor_only_uses_prices_known_at_the_sort():
    frames = _frames()
    closes = pd.DataFrame({s: frames[s]["close"] for s in STOCKS})
    base = A.momentum_factor(closes)
    cut = closes.index[260]
    changed = closes.copy()
    rng = np.random.default_rng(9)
    after = changed.index > cut
    changed.loc[after] = changed.loc[after].to_numpy() * np.exp(rng.normal(0, 0.2, (after.sum(), len(STOCKS))))
    other = A.momentum_factor(changed)
    pd.testing.assert_series_equal(base[: cut], other[: cut])  # a different future leaves the past alone
    assert not np.allclose(base[after].dropna(), other[after].dropna())


# ----------------------------------------------------------------------------------- a synthetic record
def _record(tmp: Path, market: FrozenMarket, betas: dict[str, float], alpha: float, trades: list[tuple],
            noise: float = 0.0005, policy_days: list[str] | None = None) -> Path:
    days = market.calendar[-160:-20]  # leave 20 days of prices after the run, for the 20-day windows
    fac = A.factor_returns(market, STOCKS).reindex(days)
    rng = np.random.default_rng(5)
    r = fac["rf"] + alpha + sum(b * fac[k] for k, b in betas.items()) + rng.normal(0, noise, len(days))
    r.iloc[0] = fac["rf"].iloc[0]
    equity = 100_000 * (1 + r).cumprod()
    spy = market.frame("SPY")["close"].reindex(days)
    regimes = np.where(np.arange(len(days)) % 3 == 0, "RISK_OFF", "RISK_ON")
    eq = pd.DataFrame({"equity": equity, "spy": spy / spy.iloc[0] * 100_000, "equal_weight": np.nan,
                       "cash_ratio": 0.5, "exposure": 0.5, "positions": 1, "regime": regimes}, index=days)
    eq.index = [d.date() for d in eq.index]
    eq.index.name = "date"
    tmp.mkdir(parents=True, exist_ok=True)
    eq.to_csv(tmp / "equity.csv")
    rows = [{"date": str(days[t[0]].date()), "symbol": t[1], "side": t[2], "qty": t[3], "price": t[4],
             "value": t[3] * t[4], "commission": 0.0,
             "tag": t[6] if len(t) > 6 else ("entry" if t[2] == "BUY" else "exit"), "reason": t[5], "pnl": np.nan,
             "holding_days": np.nan} for t in trades]
    pd.DataFrame(rows).to_csv(tmp / "trades.csv", index=False)
    man = {"run_id": tmp.name, "experiment": {"period": "2026", "cash": 100_000.0, "news": True,
                                              "start": str(days[0].date()), "end": str(days[-1].date())},
           "symbols": STOCKS, "strategy": {"name": "synthetic", "params": {}}}
    (tmp / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    if policy_days is not None:
        (tmp / "policy_log.json").write_text(json.dumps({"steps": [{"step": i + 1, "day": d, "orders": []}
                                                                    for i, d in enumerate(policy_days)]}))
    return tmp


@pytest.fixture(scope="module")
def market() -> FrozenMarket:
    return FrozenMarket(_frames(), label="synthetic", period="2026")


def test_decisions_and_ic_on_a_synthetic_record(tmp_path, market):
    days = market.calendar[-160:-20]
    trades = [(5, "AAA", "BUY", 100, 100.0, "fresh positive news: AAA wins a contract"),
              (5, "FFF", "BUY", 100, 100.0, "breakout"),
              (30, "AAA", "SELL", 100, 110.0, "held 25 days"),
              (40, "CCC", "BUY", 50, 100.0, "pullback"),
              (60, "CCC", "SELL", 50, 95.0, "confirmed red flag (lawsuit)"),
              (61, "FFF", "SELL", 100, 90.0, "stop")]
    folder = _record(tmp_path / "rec", market, {"market": 1.0}, 0.0, trades)
    res = A.analyze_run(folder, market=market, chart=False, momentum_universe=STOCKS)

    f = {s: market.frame(s) for s in [*STOCKS, "SPY"]}
    cal = list(market.calendar)

    def excess(i: int, sym: str, h: int) -> float:
        k = cal.index(days[i])
        r = f[sym]["close"].iloc[k + h - 1] / f[sym]["open"].iloc[k] - 1
        b = f["SPY"]["close"].iloc[k + h - 1] / f["SPY"]["open"].iloc[k] - 1
        return r - b

    for h in (1, 5, 20):
        buys = [excess(i, s, h) for i, s, side, *_ in trades if side == "BUY"]
        sells = [-excess(i, s, h) for i, s, side, *_ in trades if side == "SELL"]
        d = res["decisions"]
        assert d["buys"][f"{h}d"]["n"] == 3 and d["sells"][f"{h}d"]["n"] == 3
        assert d["buys"][f"{h}d"]["mean"] == pytest.approx(np.mean(buys))
        assert d["sells"][f"{h}d"]["mean"] == pytest.approx(np.mean(sells))
        assert d["all"][f"{h}d"]["mean"] == pytest.approx(np.mean(buys + sells))
        assert d["all"][f"{h}d"]["hit_rate"] == pytest.approx(np.mean(np.array(buys + sells) > 0))
    assert res["decisions"]["by_trigger"]["news"]["buys"]["n"] == 1
    zero = {"shorts": 0, "covers": 0, "forced": 0}
    assert res["decisions"]["counts"] == {"news": {"buys": 1, "sells": 1, **zero}, "other": {"buys": 2, "sells": 2,
                                                                                         **zero}}
    assert res["summary"]["news_decision_share"] == pytest.approx(2 / 6)
    assert res["summary"]["news_entry_pnl"] == pytest.approx(1000.0)

    # decision IC: on each fill day, +1 bought / -1 sold / 0 left alone against the next five days
    want = []
    for i in sorted({t[0] for t in trades}):
        k = cal.index(days[i])
        fwd = pd.Series({s: f[s]["close"].iloc[k + 4] / f[s]["open"].iloc[k] - 1 for s in STOCKS})
        direction = pd.Series(0.0, index=STOCKS)
        for j, s, side, *_ in trades:
            if j == i:
                direction[s] = 1.0 if side == "BUY" else -1.0
        rd, rf = direction.rank().to_numpy(), fwd.rank().to_numpy()
        want.append(np.corrcoef(rd, rf)[0, 1])
    ic = res["ic"]["decision"]
    assert ic["days"] == 5 and ic["mean"] == pytest.approx(np.mean(want))
    assert ic["std"] == pytest.approx(np.std(want, ddof=1)) and ic["ir"] == pytest.approx(ic["mean"] / ic["std"])
    # a strategy decides after every close; the weights rank stocks while something is held (fills on days 5-60)
    assert res["ic"]["holdings"]["days"] == 56

    # the P&L of the positions: AAA +1,000, CCC -250, FFF -1,000
    by_stock = {r["group"]: r["pnl"] for r in res["pnl"]["by_stock"]}
    assert by_stock == pytest.approx({"AAA": 1000.0, "CCC": -250.0, "FFF": -1000.0})
    hold = {r["group"]: r["trades"] for r in res["pnl"]["by_holding_period"]}
    assert hold == {"11-20 days": 1, "21-60 days": 2}
    entry = {r["group"]: r["pnl"] for r in res["pnl"]["by_trigger"]["entry"]}
    assert entry == pytest.approx({"news": 1000.0, "other": -1250.0})
    exit_ = {r["group"]: r["trades"] for r in res["pnl"]["by_trigger"]["exit"]}
    assert exit_ == {"news": 1, "other": 2}
    c = res["pnl"]["concentration"]
    assert c["best_trade"]["symbol"] == "AAA" and c["best_trade_share"] is None  # the positions lost in all


def test_factor_regression_recovers_the_exposures(tmp_path, market):
    betas = {"market": 0.8, "size": 0.0, "growth": 0.3, "momentum": 0.5}
    folder = _record(tmp_path / "rec", market, betas, 0.0004, [], noise=0.0003)
    res = A.analyze_run(folder, market=market, chart=False, momentum_universe=STOCKS)
    f = res["factors"]
    for k, b in betas.items():
        assert f["exposures"][k]["beta"] == pytest.approx(b, abs=0.03)
    assert f["alpha_annual"] == pytest.approx(0.0004 * 252, abs=0.03)
    assert f["alpha_t"] > 3 and f["r2"] > 0.95
    c = f["contributions_annual"]
    assert sum(c.values()) == pytest.approx(f["excess_return_annual"])  # the parts add up
    assert res["summary"]["exposure_return_annual"] + f["alpha_annual"] == pytest.approx(f["excess_return_annual"])
    assert f["capm"]["r2"] < f["r2"]  # the market alone explains less
    assert res["decisions"]["all"]["1d"]["n"] == 0 and res["pnl"]["positions"] == 0
    assert any("Verdict" in x for x in res["findings"])


def test_a_policy_decides_on_its_steps_only(tmp_path, market):
    days = market.calendar[-160:-20]
    steps = [str(d.date()) for d in days[:-1:10]]
    trades = [(1, "AAA", "BUY", 100, 100.0, "policy buy"), (11, "AAA", "SELL", 100, 101.0, "policy sell")]
    folder = _record(tmp_path / "rec", market, {"market": 1.0}, 0.0, trades, policy_days=steps)
    res = A.analyze_run(folder, market=market, chart=False, momentum_universe=STOCKS)
    assert res["kind"] == "policy" and "policy's steps" in res["ic"]["decision_days"]
    assert res["ic"]["holdings"]["days"] <= len(steps)
    assert {r["group"] for r in res["pnl"]["by_trigger"]["entry"]} == {"no reason"}


# ----------------------------------------------------------------------------------- real records
@pytest.fixture(scope="module")
def real_run(tmp_path_factory):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(history, "BUNDLE_PATH", REAL_NEWS)
        folder = tmp_path_factory.mktemp("runs") / "swing_news-news"
        run_experiment(Experiment(strategy="swing_news", params={"news": True}, news=True, start="2026-02-02",
                                  end="2026-04-30", max_positions=5, leak_check=False), out_dir=folder)
    return folder


@needs_news
def test_real_record_adds_up(real_run):
    res = A.analyze_run(real_run)
    for name in A.FILES:
        assert (real_run / name).is_file()
    saved = json.loads((real_run / "analysis.json").read_text(encoding="utf-8"))
    assert saved["schema"] == A.SCHEMA and saved["period"] == "2026" and saved["summary"]["decisions"] > 0
    metrics = json.loads((real_run / "metrics.json").read_text(encoding="utf-8"))
    assert res["total_return"] == pytest.approx(metrics["strategy"]["total_return"], abs=1e-6)
    rc = res["pnl"]["reconciliation"]
    assert abs(rc["other"]) < 0.01 and res["pnl"]["account"]["equity_error"] < 0.01  # to the cent
    trades = pd.read_csv(real_run / "trades.csv")
    assert res["decisions"]["buys"]["1d"]["n"] == int((trades["side"] == "BUY").sum())
    assert sum(r["trades"] for r in res["pnl"]["by_stock"]) == res["pnl"]["positions"]
    assert sum(r["pnl"] for r in res["pnl"]["by_sector"]) == pytest.approx(rc["stocks"])
    assert sum(r["pnl"] for r in res["pnl"]["by_regime"]) == pytest.approx(res["pnl"]["total"])
    entry = {r["group"] for r in res["pnl"]["by_trigger"]["entry"]}
    assert entry == {"news"}  # swing_news buys on fresh positive headlines
    assert res["ic"]["decision"]["days"] > 0 and res["ic"]["holdings"]["days"] > 40
    assert "exposures" in res["factors"] and res["factors"]["days"] > 50
    md = (real_run / "analysis.md").read_text(encoding="utf-8")
    assert "## Decisions" in md and "## Factor attribution" in md and "### By market regime" in md
    json.dumps(A.compact(res))  # the MCP answer is plain JSON


@needs_news
def test_cli_and_mcp(real_run):
    from typer.testing import CliRunner

    from riskaware import mcp_server as m
    from riskaware.cli import app

    runner = CliRunner()
    out = runner.invoke(app, ["research", "analyze", str(real_run), "--no-chart"])
    assert out.exit_code == 0, out.output
    assert "Findings" in out.output and "Decision IC" in out.output
    out = runner.invoke(app, ["research", "analyze", str(real_run), "--json", "--no-chart"])
    assert out.exit_code == 0 and '"schema"' in out.output
    out = runner.invoke(app, ["research", "analyze", str(real_run.parent), "--no-chart"])  # a folder of runs
    assert out.exit_code == 0 and (real_run.parent / "analysis.csv").is_file()
    assert runner.invoke(app, ["research", "analyze", "no-such-run"]).exit_code == 2

    assert "experiment_analysis" in {f.__name__ for f in m.TOOLS} and "experiment_analysis" not in m.READ_ONLY
    got = m.experiment_analysis(str(real_run))
    assert got["ok"] is True and got["summary"]["decisions"] > 0 and got["findings"]
    assert m.experiment_analysis("no-such-run")["ok"] is False


def test_a_policy_run_from_the_agent(monkeypatch):
    monkeypatch.setattr(history, "BUNDLE_PATH", REAL_NEWS)

    def policy(obs):
        best = max(obs["stocks"], key=lambda s: s["ret_21d"] or -1)
        return {"orders": [{"symbol": best["symbol"], "action": "target", "weight": 0.3, "reason": "momentum"}]}

    r = agent.run_policy(policy, Experiment(universe="core", max_positions=3, start="2026-02-02", end="2026-04-30"),
                         decide_every=10, check=False)
    res = A.analyze_run(r.folder, chart=False)
    assert res["kind"] == "policy" and res["decisions"]["all"]["1d"]["n"] > 0
    steps = json.loads((Path(r.folder) / "policy_log.json").read_text(encoding="utf-8"))["steps"]
    assert 0 < res["ic"]["holdings"]["days"] <= len(steps)
    assert abs(res["pnl"]["reconciliation"]["other"]) < 0.01


def test_fills_take_the_reason_of_the_order_in_decisions_csv():
    days = pd.bdate_range("2026-03-02", periods=5)
    trades = pd.DataFrame({"date": [days[1], days[3]], "symbol": ["AAA", "AAA"], "side": ["BUY", "SELL"],
                           "reason": ["buy", "sell"]})
    dec = pd.DataFrame({"date": [str(days[0].date()), str(days[0].date()), str(days[2].date())],
                        "kind": ["signal", "order", "order"], "symbol": ["AAA", "AAA", "AAA"],
                        "action": ["BUY", "BUY", "SELL"],
                        "reason": ["fresh positive news: AAA", "fresh positive news: AAA", np.nan]})
    out = A._with_decision_reasons(trades, dec, days)
    assert list(out["reason"]) == ["fresh positive news: AAA", "sell"]  # an order without a reason keeps the fill's


def test_a_folder_without_records_is_an_error(tmp_path):
    from typer.testing import CliRunner

    from riskaware.cli import app

    with pytest.raises(FileNotFoundError):
        A.analyze_many(tmp_path)
    out = CliRunner().invoke(app, ["research", "analyze", str(tmp_path)])
    assert out.exit_code == 2 and "holds none" in " ".join(out.output.split())


# ----------------------------------------------------------------------------------- short selling and margin
SHORT_TRADES = [(5, "AAA", "SELL", 100, 100.0, "bearish: guidance cut", "short"),
                (5, "BBB", "BUY", 100, 50.0, "breakout", "entry"),
                (15, "AAA", "BUY", 100, 90.0, "take the gain", "cover"),
                (20, "BBB", "SELL", 150, 55.0, "turn short", "short"),  # closes the long and opens a short
                (30, "BBB", "BUY", 50, 60.0, "stop", "cover"),
                (35, "CCC", "BUY", 60, 100.0, "pullback", "entry"),
                (40, "CCC", "SELL", 30, 100.0, "margin call: every position cut to 50%", "margin_call"),
                (45, "CCC", "SELL", 30, 110.0, "target", "exit")]


def test_shorts_on_a_synthetic_record(tmp_path, market):
    folder = _record(tmp_path / "rec", market, {"market": 1.0}, 0.0, SHORT_TRADES)
    res = A.analyze_run(folder, market=market, chart=False, momentum_universe=STOCKS)
    d = res["decisions"]
    assert {k: d[k]["1d"]["n"] for k in ("buys", "sells", "shorts", "covers", "forced", "all")} == {
        "buys": 2, "sells": 1, "shorts": 2, "covers": 2, "forced": 1, "all": 7}  # a margin call is not a decision
    f = {s: market.frame(s) for s in ["AAA", "BBB", "SPY"]}
    cal, days = list(market.calendar), market.calendar[-160:-20]

    def excess(i: int, sym: str, h: int = 5) -> float:
        k = cal.index(days[i])
        r = f[sym]["close"].iloc[k + h - 1] / f[sym]["open"].iloc[k] - 1
        return r - (f["SPY"]["close"].iloc[k + h - 1] / f["SPY"]["open"].iloc[k] - 1)

    # a short sale bets against the stock: it is credited with the excess it avoided; a cover counts like a buy
    assert d["shorts"]["5d"]["mean"] == pytest.approx(np.mean([-excess(5, "AAA"), -excess(20, "BBB")]))
    assert d["covers"]["5d"]["mean"] == pytest.approx(np.mean([excess(15, "AAA"), excess(30, "BBB")]))
    assert res["summary"]["short_avoided_5d"] == pytest.approx(d["shorts"]["5d"]["mean"])
    assert res["summary"]["forced_fills"] == 1 and res["summary"]["cover_excess_5d"] is not None
    p = res["pnl"]
    # AAA short +1,000; BBB long +500, then short -250 from the fill that crossed zero; CCC long +300
    by_dir = {r["group"]: (r["trades"], round(r["pnl"], 6)) for r in p["by_direction"]}
    assert by_dir == {"long": (2, 800.0), "short": (2, 750.0)}
    assert p["reconciliation"]["stocks_long"] == pytest.approx(800.0)
    assert p["reconciliation"]["stocks_short"] == pytest.approx(750.0)
    bbb = next(r for r in p["by_stock"] if r["group"] == "BBB")
    assert (bbb["long_pnl"], bbb["short_pnl"], bbb["trades"]) == (pytest.approx(500.0), pytest.approx(-250.0), 2)
    best = p["best_trades"][0]
    assert (best["symbol"], best["direction"], best["holding_days"]) == ("AAA", "short", 10)
    assert best["pnl"] == pytest.approx(1000.0) and best["return"] == pytest.approx(0.1)
    assert any("AAA shorted" in x for x in res["findings"])
    md = A.render_markdown(res)
    assert "| Shorts (avoided) | 5 days |" in md and "### Long and short" in md and "Short positions" in md


class _Fixed(Strategy):
    """Holds fixed signed weights from the first day on."""

    name = "fixed"

    def __init__(self, weights: dict[str, float], every: int = 5):
        super().__init__()
        self.weights, self.every = weights, every

    def on_start(self, universe):
        self.day = 0

    def on_bar(self, ctx):
        self.day += 1
        if (self.day - 1) % self.every:
            return []
        out = []
        for s, w in self.weights.items():
            pos = ctx.position(s)
            cur = pos.weight if pos is not None else 0.0
            if w != cur:
                out.append(Signal(s, Action.BUY if w > cur else Action.SELL, 0.5, "fixed", target_weight=w))
        return out

    def warmup_bars(self):
        return 0


@pytest.fixture(scope="module")
def short_runs(tmp_path_factory):
    base = tmp_path_factory.mktemp("short-runs")
    common = {"universe": "core", "news": False, "leak_check": False}
    runs = {
        "long_short": (Experiment(strategy="momentum_ls", max_positions=9, mode="long_short", start="2026-02-02",
                                  end="2026-04-30", **common), None),
        "margin": (Experiment(strategy="trend_ls", params={"gross": 2.0}, max_positions=9, mode="margin",
                              period="2022", start="2022-02-01", end="2022-05-31", **common), None),
        "margin_call": (Experiment(strategy="fixed", mode="margin", period="2020", start="2020-02-03",
                                   end="2020-04-30", **common), lambda: _Fixed({"TSLA": 2.0}, every=1000)),
    }
    return {name: run_experiment(exp, factory=factory, out_dir=base / name, chart=False).folder
            for name, (exp, factory) in runs.items()}


@pytest.mark.parametrize("name", ["long_short", "margin", "margin_call"])
def test_long_short_and_margin_records_add_up_to_the_cent(short_runs, name):
    folder = short_runs[name]
    res = A.analyze_run(folder, chart=False)
    p, rc = res["pnl"], res["pnl"]["reconciliation"]
    assert res["mode"] == ("long_short" if name == "long_short" else "margin")
    assert p["account"]["equity_error"] < 0.01  # the account rebuilt from the fills is the one the backtest kept
    assert abs(rc["other"]) < 0.01  # positions + interest + borrow fees + margin interest = the run's P&L
    assert rc["stocks_long"] + rc["stocks_short"] == pytest.approx(rc["stocks"])
    acct = json.loads((folder / "metrics.json").read_text(encoding="utf-8"))["account"]
    cash = p["initial_cash"]
    for mine, theirs, sign in (("stocks_long", "pnl_long", 1), ("stocks_short", "pnl_short", 1),
                               ("borrow_fees", "borrow_fees", -1), ("margin_interest", "margin_interest", -1),
                               ("cash_interest", "cash_interest", 1)):
        # metrics.json keeps them as shares of the starting cash with four decimals
        assert rc[mine] == pytest.approx(sign * acct[theirs] * cash, abs=0.5e-4 * cash + 0.01), mine
    assert sum(r["pnl"] for r in p["by_direction"]) == pytest.approx(rc["stocks"])
    d = res["decisions"]
    assert d["all"]["1d"]["n"] == sum(d[k]["1d"]["n"] for k in ("buys", "sells", "shorts", "covers"))
    if name == "margin_call":
        assert d["forced"]["1d"]["n"] >= 2 and res["summary"]["forced_fills"] == d["forced"]["1d"]["n"]
        assert rc["margin_interest"] < -1.0  # borrowed cash at 2x and more
    else:
        assert d["shorts"]["1d"]["n"] > 0 and {r["group"] for r in p["by_direction"]} == {"long", "short"}
        assert rc["borrow_fees"] < 0 and res["summary"]["short_contribution"] == pytest.approx(rc["stocks_short"] / cash)
