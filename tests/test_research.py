"""The research kit: frozen data, point-in-time days, plain execution, experiments, the leak check, sessions."""

from __future__ import annotations

import json
import random
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from riskaware.news import history
from riskaware.research import agent
from riskaware.research.data import bundled_market, manifest, verify
from riskaware.research.day import MarketView
from riskaware.research.execution import PlainPlanner
from riskaware.research.experiment import Experiment, run_experiment
from riskaware.strategy.base import PortfolioView, PositionView, Strategy
from riskaware.types import Action, Regime, Signal

from .conftest import make_ctx

REAL_NEWS = Path(history.__file__).parent / "data" / "news_2026.jsonl.gz"
WINDOW = {"start": "2026-02-02", "end": "2026-04-30"}


@pytest.fixture(autouse=True)
def bundled_news(monkeypatch):
    """These tests use the news file that ships with the package."""
    monkeypatch.setattr(history, "BUNDLE_PATH", REAL_NEWS)


@pytest.fixture(scope="module")
def view() -> MarketView:
    return MarketView()


# ----------------------------------------------------------------------------------- data
def test_bundle_matches_its_manifest():
    m = manifest()
    assert all(verify().values())
    mk = bundled_market()
    assert mk.first_day == date.fromisoformat(m["period"]["first"]) and mk.last_day.isoformat() == m["version"]
    assert mk.experiment_start == date(2026, 1, 2)
    for group in ("stocks", "etfs", "volatility", "cross_asset"):
        assert all(mk.has(s) for s in m["symbols"][group])
    assert len(m["symbols"]["stocks"]) == 64 and "^VIX3M" in mk.symbols


def test_frames_are_total_return_prices_up_to_end():
    mk = bundled_market()
    raw, adj = mk.raw("CVX"), mk.frame("CVX")
    assert np.allclose(adj["close"], raw["close"] * raw["adj"])
    h = mk.history(["CVX", "SPY", "NOPE"], start=date(2026, 3, 1), end=date(2026, 3, 31))
    assert set(h) == {"CVX", "SPY"}
    assert h["CVX"].index[0].date() == mk.first_day  # warm-up never depends on the start asked for
    assert h["CVX"].index[-1] <= pd.Timestamp("2026-03-31")


def test_poisoned_market_changes_only_the_future():
    mk = bundled_market()
    cut = date(2026, 5, 15)
    p = mk.poisoned(cut, seed=1)
    a, b = mk.raw("NVDA"), p.raw("NVDA")
    past = a.index <= pd.Timestamp(cut)
    pd.testing.assert_frame_equal(a[past], b[past], check_dtype=False)
    assert not np.allclose(a.loc[~past, "close"], b.loc[~past, "close"])
    assert p.fingerprint() != mk.fingerprint()


# ----------------------------------------------------------------------------------- one day
def test_a_day_only_knows_its_past(view):
    day = date(2026, 4, 8)
    after = MarketView(bundled_market().poisoned(day, seed=3))
    a, b = view.snapshot(day, symbols=["NVDA", "AAPL", "XOM"]), after.snapshot(day, symbols=["NVDA", "AAPL", "XOM"])
    assert a["market"] == b["market"]
    for s in ("NVDA", "AAPL", "XOM"):
        assert a["stocks"][s]["indicators"] == b["stocks"][s]["indicators"]
        assert a["stocks"][s]["technical"] == b["stocks"][s]["technical"]


def test_indicators_on_a_day_equal_those_of_data_cut_at_that_day(view):
    from riskaware import indicators as ind

    day = pd.Timestamp("2026-06-12")
    mk = bundled_market()
    spy = mk.frame("SPY")["close"]
    for sym in ("MSFT", "KO"):
        cut = ind.compute(mk.frame(sym).loc[:day], spy.loc[:day]).loc[day]
        full = view.indicators(sym).loc[day]
        for k in ("ema20", "ema200", "rsi2", "rsi14", "macd_hist", "atr14", "adx14", "dd252", "rs126", "beta120"):
            assert cut[k] == pytest.approx(full[k], rel=1e-9, abs=1e-12), k


def test_news_on_a_day_is_published_before_its_close(view):
    snap = view.snapshot("2026-03-20", symbols=["TSLA", "AAPL"], headlines=10)
    for s in ("TSLA", "AAPL"):
        for h in snap["stocks"][s]["news"]["top"]:
            assert h["first_seen"] < "2026-03-20 16:00"
    assert snap["stocks"]["TSLA"]["news"]["last_earnings_release"] < "2026-03-20"


def test_a_headline_after_the_close_counts_the_next_day():
    ts = datetime(2026, 3, 20, 20, 5, tzinfo=timezone.utc).isoformat()  # 16:05 New York
    v = MarketView(extra_news=[{"ts": ts, "symbol": "KO", "headline": "Coca-Cola faces fraud probe by SEC",
                                "source": "Reuters"}])
    same_day = json.dumps(v.snapshot("2026-03-20", symbols=["KO"], headlines=20)["stocks"]["KO"]["news"])
    next_day = json.dumps(v.snapshot("2026-03-23", symbols=["KO"], headlines=20)["stocks"]["KO"]["news"])
    assert "fraud probe" not in same_day and "fraud probe" in next_day


def test_weekends_show_the_close_before(view):
    snap = view.snapshot("2026-04-05")
    assert snap["day"] == "2026-04-02" and "not a trading day" in snap["note"]
    with pytest.raises(ValueError):
        view.snapshot("2027-01-04")


# ----------------------------------------------------------------------------------- plain execution
def test_plain_planner_sells_first_and_sizes_equally(offline_cfg):
    held = {"AAA": PositionView("AAA", qty=100, avg_cost=90, price=100, weight=0.1, days_held=5)}
    ctx = make_ctx(offline_cfg, Regime.RISK_OFF, positions=held, equity=100_000, cash=90_000,
                   rows={s: {"close": 100.0} for s in ("AAA", "BBB", "CCC", "DDD")})
    sigs = [Signal("BBB", Action.BUY, 0.4, "b"), Signal("CCC", Action.BUY, 0.9, "c"),
            Signal("DDD", Action.BUY, 0.6, "d", target_weight=0.25), Signal("AAA", Action.SELL, 0.5, "exit")]
    d = PlainPlanner(max_positions=3).plan(sigs, ctx, {"AAA": 100.0, "BBB": 100.0, "CCC": 100.0, "DDD": 100.0})
    sides = [(o.symbol, o.side.value, o.qty) for o in d.orders]
    assert sides[0] == ("AAA", "SELL", 100)  # sells first, in any regime: no risk rules
    assert ("CCC", "BUY", 333) in sides and ("DDD", "BUY", 250) in sides  # strongest first; target_weight wins
    assert ("BBB", "BUY", 333) in sides  # AAA's slot freed by the sell
    pf = PortfolioView(equity=100_000, cash=1_000, positions={})
    ctx2 = make_ctx(offline_cfg, Regime.NEUTRAL, equity=100_000, cash=1_000, rows={"BBB": {"close": 100.0}})
    ctx2.portfolio = pf
    d2 = PlainPlanner(max_positions=10).plan([Signal("BBB", Action.BUY, 0.5)], ctx2, {"BBB": 100.0})
    assert d2.orders[0].qty == 10  # never more than the cash


# ----------------------------------------------------------------------------------- experiments
def test_experiment_record_and_leak_check(tmp_path):
    r = run_experiment(Experiment(strategy="swing_rsi2", universe="core", split="2026-04-01", **WINDOW),
                       out_dir=tmp_path / "run", chart=False)
    files = {p.name for p in r.folder.iterdir()}
    assert {"manifest.json", "metrics.json", "equity.csv", "trades.csv", "decisions.csv", "report.md",
            "strategy.py"} <= files
    man = json.loads((r.folder / "manifest.json").read_text())
    assert man["dataset"]["prices"]["sha256"] == manifest()["files"]["market_2026.csv.gz"]["sha256"]
    assert man["strategy"]["source_sha256"] and man["experiment"]["news"] is True
    assert r.leak["verdict"] == "pass" and len(r.leak["cuts"]) == 3
    assert set(r.periods) == {"in_sample", "out_of_sample"} and "beta" in r.stats
    assert r.metrics["round_trips"] > 0 and len(r.decisions) > 0
    s = r.summary()
    assert s["leak_check"]["verdict"] == "pass" and s["metrics"]["total_return"] is not None


def test_leak_check_catches_an_indicator_that_peeks(monkeypatch):
    from riskaware import indicators as ind

    real = ind.compute

    def peeking(df, bench=None):  # a buggy indicator: tomorrow's return
        out = real(df, bench)
        out["next_ret"] = out["close"].shift(-1) / out["close"] - 1
        return out

    monkeypatch.setattr(ind, "compute", peeking)

    class Peeker(Strategy):
        name = "peeker"

        def on_bar(self, ctx):
            out = []
            for s in ctx.universe:
                row = ctx.row(s)
                if row is None or row.get("next_ret") != row.get("next_ret"):
                    continue
                if ctx.position(s) is None and row["next_ret"] > 0.01:
                    out.append(Signal(s, Action.BUY, 0.5, "knows tomorrow"))
                elif ctx.position(s) is not None and row["next_ret"] < 0:
                    out.append(Signal(s, Action.SELL, 0.5, "knows tomorrow", fraction=1.0))
            return out

    r = run_experiment(Experiment(strategy="peeker", universe="core", news=False, **WINDOW), factory=Peeker,
                       save=False)
    assert r.leak["verdict"] == "fail"
    assert any(not c["same"] for c in r.leak["cuts"])


def test_leak_check_tells_randomness_from_a_leak():
    class Coin(Strategy):
        name = "coin"

        def on_bar(self, ctx):
            rng = random.SystemRandom()
            return [Signal(s, Action.BUY if rng.random() < 0.5 else Action.SELL, 0.5, "coin", fraction=1.0)
                    for s in ctx.universe if rng.random() < 0.2]

    r = run_experiment(Experiment(strategy="coin", universe="core", news=False, **WINDOW), factory=Coin, save=False)
    assert r.leak["verdict"] == "not repeatable"


def test_research_strategies_are_built_in_and_follow_the_news_switch():
    from riskaware.strategy import BUILTIN

    for name in ("swing_trend", "swing_rsi2", "swing_breakout", "swing_pullback", "swing_news"):
        assert name in BUILTIN
    r = run_experiment(Experiment(strategy="swing_news", universe="core", news=False, leak_check=False, **WINDOW),
                       save=False)
    assert r.backtest is not None and r.experiment.news is False


# ----------------------------------------------------------------------------------- LLM sessions
def test_trading_session_steps_to_a_record_and_hides_names_when_blind():
    out = agent.session_start(universe="core", decide_every=15, blind=True, max_positions=3, **WINDOW)
    obs = out["observation"]
    text = json.dumps(obs)
    for real in ("AAPL", "NVDA", "Apple", "Nvidia", "2026-"):
        assert real not in text
    assert obs["day"] == "day 1" and {s["symbol"] for s in obs["stocks"]} <= {f"S{i:02d}" for i in range(1, 10)}
    sid, n = out["session_id"], 0
    while not out["done"]:
        first = out["observation"]["stocks"][0]["symbol"]
        out = agent.session_step(sid, [{"symbol": first, "action": "buy", "weight": 0.2, "fraction": 0, "reason": "t"},
                                       {"symbol": "XXX", "action": "buy", "weight": 0.1, "fraction": 0, "reason": "t"}])
        n += 1
        if not out["done"]:
            assert out["observation"]["problems_with_last_orders"] == ["unknown symbol 'XXX'"]
    assert n == out["steps"] and out["result"]["folder"]
    assert (Path(out["result"]["folder"]) / "policy_log.json").is_file()
    assert agent.session_status(sid)["done"] is True


def test_run_policy_with_a_rule_based_policy():
    calls = []

    def policy(obs):
        calls.append(obs["day"])
        best = max(obs["stocks"], key=lambda s: s["ret_21d"] or -1)
        return {"orders": [{"symbol": best["symbol"], "action": "target", "weight": 0.3}], "note": "momentum"}

    r = agent.run_policy(policy, Experiment(universe="core", max_positions=3, **WINDOW), decide_every=10, save=False)
    assert len(calls) >= 5 and r.metrics["trades"] > 0


# ----------------------------------------------------------------------------------- MCP
def test_mcp_research_tools():
    from riskaware import mcp_server as m

    names = {f.__name__ for f in m.TOOLS}
    assert {"research_guide", "market_day", "run_experiment", "list_experiments", "experiment_report",
            "trading_session_start", "trading_session_step", "trading_session_status"} <= names
    assert "run_experiment" not in m.READ_ONLY and "market_day" in m.READ_ONLY
    g = m.research_guide()
    assert "swing_rsi2" in g["baselines"] and g["protocol"]["execution"].startswith("orders fill at the next")
    day = m.market_day("2026-05-05")
    assert len(day["stocks"]) == 64 and {"symbol", "rsi14", "news_score", "setups"} <= set(day["stocks"][0])
    bad = m.run_experiment(strategy="swing_rsi2", symbols=["NOPE"], **WINDOW)
    assert bad["ok"] is False and "NOPE" in bad["error"]
