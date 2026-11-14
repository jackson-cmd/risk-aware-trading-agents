"""Transaction costs: the fixed model leaves every number as it was; the realistic model charges part of the
overnight gap, square-root market impact and a volume cap, using only what is known at the open."""

from __future__ import annotations

import math
from datetime import date

import pandas as pd
import pytest

from riskaware import indicators as ind
from riskaware.backtest import (
    OpenFill,
    fill_at_open,
    market_before_open,
    realistic_cost_bps,
    volume_cap_shares,
)
from riskaware.backtest.engine import VOLUME_CAP, Backtester
from riskaware.config import AppConfig, BacktestConfig
from riskaware.data.prices import SyntheticPrices
from riskaware.news import history
from riskaware.research.experiment import Experiment, protocol, reproduce_command, run_experiment
from riskaware.strategy import load_strategy
from riskaware.strategy.base import SymbolData
from riskaware.types import Side

from .conftest import needs_news, news_file

REAL_NEWS = news_file("news_2026.jsonl.gz")
WINDOW = {"start": "2026-02-02", "end": "2026-04-30"}
SYMBOLS = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
START, END = date(2018, 1, 2), date(2020, 12, 31)
OPTION_TAGS = {"option_open", "option_close", "option_expiry", "assignment", "called_away"}


@pytest.fixture(autouse=True)
def bundled_news(monkeypatch):
    monkeypatch.setattr(history, "BUNDLE_PATH", REAL_NEWS)


def _prices() -> SyntheticPrices:
    return SyntheticPrices(seed=5, start=date(2016, 1, 1), end=END)


def _run(prices=None, cash: float = 100_000.0, **backtest):
    cfg = AppConfig()
    cfg.universe.preset = None
    cfg.universe.symbols = list(SYMBOLS)
    for k, v in backtest.items():
        setattr(cfg.backtest, k, v)
    bt = Backtester(cfg, prices or _prices())
    res = bt.run(load_strategy("risk_aware"), START, END, initial_cash=cash)
    return res, bt


def _stock_fills(res, bt) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """The stock fills of a run and the indicator frames the run saw."""
    frames = bt.load(bt.universe_for(), START, END)
    t = res.trades[~res.trades["tag"].isin(OPTION_TAGS) & res.trades["symbol"].isin(list(frames))]
    return t, {s: ind.compute(frames[s]) for s in set(t["symbol"])}


def _known_cost(f: pd.DataFrame, ts: pd.Timestamp, qty: float, bt: BacktestConfig) -> float:
    """The realistic cost from the rows before the fill: vol20 of the day before, the shifted 20-day volume."""
    i = f.index.get_loc(ts)
    return realistic_cost_bps(float(f["open"].iloc[i]), float(f["prev_close"].iloc[i]),
                              float(f["vol20"].iloc[i - 1]) / math.sqrt(252), float(f["vol_avg20"].iloc[i]), qty,
                              bt.slippage_bps, bt.gap_cost, bt.impact_coef)


# ----------------------------------------------------------------------------------- the formula
def test_realistic_cost_adds_the_gap_and_square_root_impact_to_the_slippage():
    # 5 bps + 10% of a 1% gap (10 bps) + 0.02 x sqrt(10,000 / 1,000,000) (20 bps)
    assert realistic_cost_bps(101.0, 100.0, 0.02, 1_000_000, 10_000) == pytest.approx(35.0)
    assert realistic_cost_bps(99.0, 100.0, 0.02, 1_000_000, 10_000) == pytest.approx(35.0)  # a gap down costs the same
    assert realistic_cost_bps(100.0, 100.0, 0.02, 1_000_000, 0) == pytest.approx(5.0)
    assert realistic_cost_bps(101.0, 100.0, 0.02, 1e6, 1e4, slippage_bps=0, gap_cost=0, impact_coef=0) == 0.0
    nan = float("nan")
    assert realistic_cost_bps(101.0, nan, nan, nan, 10_000) == pytest.approx(5.0)  # nothing known: the slippage only


def test_realistic_cost_rises_with_volatility_gap_and_order_size():
    def cost(**kw) -> float:
        args = dict(open_price=100.0, prev_close=100.0, daily_vol=0.02, avg_volume=1_000_000, qty=10_000)
        return realistic_cost_bps(**{**args, **kw})

    by_vol = [cost(daily_vol=v) for v in (0.005, 0.01, 0.02, 0.04, 0.08)]
    by_gap_up = [cost(open_price=100 * (1 + g)) for g in (0.0, 0.005, 0.01, 0.03, 0.1)]
    by_gap_down = [cost(open_price=100 * (1 - g)) for g in (0.0, 0.005, 0.01, 0.03, 0.1)]
    by_size = [cost(qty=q) for q in (100, 1_000, 10_000, 50_000, 100_000)]
    for seq in (by_vol, by_gap_up, by_gap_down, by_size):
        assert all(b > a for a, b in zip(seq, seq[1:], strict=False)), seq
    # square root: four times the shares move the price twice as much
    assert cost(qty=40_000) - cost(qty=0) == pytest.approx(2 * (cost(qty=10_000) - cost(qty=0)))


def test_fill_at_open_prices_both_sides_under_both_models():
    fixed, real = BacktestConfig(), BacktestConfig(cost_model="realistic")
    known = (101.0, 100.0, 0.02, 1_000_000)  # open, previous close, daily volatility, 20-day average volume
    buy = fill_at_open("BUY", 10_000, *known, fixed)
    assert buy == OpenFill(101.0 * (1 + 5.0 / 10_000), 5.0)
    assert fill_at_open(Side.SELL, 10_000, *known, fixed) == OpenFill(101.0 * (1 - 5.0 / 10_000), 5.0)
    b, s = fill_at_open(Side.BUY, 10_000, *known, real), fill_at_open("sell", 10_000, *known, real)
    assert b.cost_bps == s.cost_bps == pytest.approx(35.0)  # a short sale pays like any sale, a cover like a buy
    assert b.price == pytest.approx(101.0 * 1.0035) and s.price == pytest.approx(101.0 * 0.9965)
    # with realistic costs a limit order never fills beyond its limit; cost_bps is what it really paid
    capped = fill_at_open("BUY", 10_000, *known, real, limit=101.10)
    assert capped.price == 101.10 and capped.cost_bps == pytest.approx((101.10 / 101.0 - 1) * 1e4)
    assert fill_at_open("SELL", 10_000, *known, real, limit=100.95).price == 100.95
    assert fill_at_open("BUY", 10_000, *known, real, limit=102.0) == b  # a limit that is not reached changes nothing
    # the fixed model keeps its old behaviour and ignores the limit, so its numbers stay as they were
    assert fill_at_open("BUY", 10_000, *known, fixed, limit=101.0) == buy
    with pytest.raises(ValueError):
        fill_at_open("HOLD", 1, *known, fixed)


def test_volume_cap_keeps_at_least_one_share_and_ignores_unknown_volume():
    assert volume_cap_shares(5_000, 100_000, 0.01) == 1_000
    assert volume_cap_shares(500, 100_000, 0.01) == 500
    assert volume_cap_shares(50, 80, 0.01) == 1  # a position can always be sold off, a share at a time
    assert volume_cap_shares(5_000, float("nan"), 0.01) == 5_000
    assert volume_cap_shares(5_000, 0.0, 0.01) == 5_000


def test_market_before_open_reads_only_the_days_before():
    frame = ind.compute(_prices().history(["AAA"], START, END)["AAA"])
    sd, ts = SymbolData(frame), frame.index[300]
    prev_close, vol, adv = market_before_open(sd, ts)
    assert prev_close == frame["close"].iloc[299]
    assert vol == pytest.approx(frame["vol20"].iloc[299] / math.sqrt(252))
    assert adv == pytest.approx(frame["volume"].iloc[280:300].mean())
    assert all(math.isnan(v) for v in market_before_open(sd, pd.Timestamp("1990-01-02")))
    assert math.isnan(market_before_open(sd, frame.index[0])[1])


def test_cost_settings_are_validated():
    assert BacktestConfig().cost_model == "fixed"
    with pytest.raises(ValueError):
        BacktestConfig(cost_model="free")
    with pytest.raises(ValueError):
        BacktestConfig(max_participation=0)
    with pytest.raises(ValueError):
        run_experiment(Experiment(strategy="swing_rsi2", universe="core", costs="free", **WINDOW), save=False)


# ----------------------------------------------------------------------------------- fixed: nothing changes
def test_fixed_costs_fill_at_the_open_plus_the_slippage_exactly():
    res, bt = _run()
    fills, _ = _stock_fills(res, bt)
    frames = bt.load(bt.universe_for(), START, END)
    assert len(fills) > 50 and res.costs["model"] == "fixed" and VOLUME_CAP not in res.rejections
    at_open = 0
    for r in fills.itertuples():
        bar = frames[r.symbol].loc[r.date]
        if r.cost_bps == 5.0:
            at_open += 1
            assert r.price == bar["open"] * (1 + 5.0 / 10_000 if r.side == "BUY" else 1 - 5.0 / 10_000)
        else:  # a limit order filled inside the day's range, at its limit
            assert r.cost_bps == 0.0 and bar["low"] <= r.price <= bar["high"]
    assert at_open > 0


@pytest.mark.parametrize("kw, equity, trades, value", [
    pytest.param(dict(strategy="swing_rsi2", universe="core"), 102132.8496833413, 30, 297940.8331728964,
                 marks=needs_news),  # with news: these numbers need the 2026 headline file
    (dict(strategy="swing_trend", universe="megacap", news=False), 109682.95588468251, 28, 267390.72103457444),
])
def test_fixed_costs_leave_existing_backtests_unchanged(kw, equity, trades, value):
    """The numbers the same experiments gave before the cost models existed (commit 45ab6e3)."""
    for costs in ({}, {"costs": "fixed"}):
        r = run_experiment(Experiment(leak_check=False, **WINDOW, **kw, **costs), save=False)
        assert float(r.equity["equity"].iloc[-1]) == pytest.approx(equity, rel=1e-9)
        assert len(r.trades) == trades and float(r.trades["value"].sum()) == pytest.approx(value, rel=1e-9)
        assert set(r.trades["cost_bps"]) == {5.0}
        assert not any(n.startswith("Realistic costs") for n in r.notes)


# ----------------------------------------------------------------------------------- realistic
def test_each_realistic_fill_pays_the_formula_on_what_was_known_at_the_open():
    res, bt = _run(cost_model="realistic")
    fills, ind_frames = _stock_fills(res, bt)
    at_open = fills[fills["cost_bps"] > 0]
    assert len(at_open) > 50 and at_open["cost_bps"].max() > 10.0
    for r in at_open.itertuples():
        f = ind_frames[r.symbol]
        known = _known_cost(f, r.date, r.qty, bt.cfg.backtest)
        o = float(f.loc[r.date, "open"])
        assert r.price == pytest.approx(o * (1 + r.cost_bps / 1e4 if r.side == "BUY" else 1 - r.cost_bps / 1e4))
        if r.cost_bps != pytest.approx(known, rel=1e-12):  # a limit order, filled at its limit (rounded to cents)
            assert r.cost_bps < known and round(r.price, 2) == r.price
    assert res.costs["fills_at_open"] == len(at_open)
    assert res.costs["avg_bps"] == pytest.approx(at_open["cost_bps"].mean())
    assert any(n.startswith("Realistic costs:") for n in res.notes)


class _EditedDay:
    """The synthetic market with one bar's close, range and volume changed after its open."""

    def __init__(self, base: SyntheticPrices, symbol: str, day: pd.Timestamp):
        self.base, self.symbol, self.day = base, symbol, day

    def history(self, symbols, start, end):
        frames = dict(self.base.history(symbols, start, end))
        f = frames[self.symbol].copy()
        r = f.loc[self.day]
        close = float(r["close"]) * 1.25
        f.loc[self.day, ["close", "high", "low", "volume"]] = [close, max(float(r["high"]), close),
                                                                 min(float(r["low"]), float(r["open"])),
                                                                 float(r["volume"]) * 20]
        frames[self.symbol] = f
        return frames


def test_a_fill_at_the_open_does_not_see_the_rest_of_its_day():
    """vol20 on a day includes that day's close; the cost must come from the day before."""
    base = _prices()
    res, bt = _run(base, cost_model="realistic")
    fills, _ = _stock_fills(res, bt)
    at_open = fills[fills["cost_bps"] > 0]
    for k in (len(at_open) // 3, 2 * len(at_open) // 3):
        r = at_open.iloc[k]
        again, _ = _run(_EditedDay(base, r["symbol"], r["date"]), cost_model="realistic")
        t = again.trades
        same = t[(t["date"] == r["date"]) & (t["symbol"] == r["symbol"]) & (t["side"] == r["side"])]
        assert len(same) == 1
        assert same["cost_bps"].iloc[0] == r["cost_bps"] and same["price"].iloc[0] == r["price"]


def test_bigger_orders_pay_more_and_the_volume_cap_holds():
    small, _ = _run(cost_model="realistic")
    big, bt = _run(cash=5e9, cost_model="realistic")
    assert big.costs["avg_bps"] > small.costs["avg_bps"] + 5  # market impact grows with the size of the orders
    fills, ind_frames = _stock_fills(big, bt)
    for r in fills.itertuples():
        adv = float(ind_frames[r.symbol].loc[r.date, "vol_avg20"])
        assert r.qty <= math.floor(0.01 * adv)
    assert big.rejections[VOLUME_CAP] == big.costs["volume_cap_cuts"] > 0
    assert any("hit the volume cap" in n for n in big.notes)
    # without the cap the same account trades more than 1% of a day's volume
    fixed, bt2 = _run(cash=5e9)
    fills2, ind2 = _stock_fills(fixed, bt2)
    assert any(r.qty > 0.01 * float(ind2[r.symbol].loc[r.date, "vol_avg20"]) for r in fills2.itertuples())


# ----------------------------------------------------------------------------------- the research kit
def test_realistic_experiment_passes_the_leak_check_and_records_its_costs(tmp_path):
    exp = Experiment(strategy="swing_rsi2", universe="core", costs="realistic", **WINDOW)
    r = run_experiment(exp, out_dir=tmp_path / "run", chart=False)
    assert r.leak["verdict"] == "pass" and all(c["same"] for c in r.leak["cuts"])
    fixed = run_experiment(Experiment(strategy="swing_rsi2", universe="core", leak_check=False, **WINDOW), save=False)
    assert r.metrics["total_return"] < fixed.metrics["total_return"]
    s = r.summary()
    assert s["costs"]["model"] == "realistic" and s["costs"]["avg_bps"] > 5.0
    assert s["execution"]["costs"].startswith("realistic: 5 bps plus 10% of the overnight gap")
    import json

    man = json.loads((r.folder / "manifest.json").read_text())
    assert man["experiment"]["costs"] == "realistic" and man["execution"]["costs"].startswith("realistic")
    assert "realistic costs" in man["protocol"]["execution"] and man["reproduce"].endswith("--costs realistic")
    assert json.loads((r.folder / "metrics.json").read_text())["costs"]["model"] == "realistic"
    assert "cost_bps" in pd.read_csv(r.folder / "trades.csv").columns
    assert r.run_id.endswith("-realistic")
    assert protocol("2026")["execution"] == protocol("2026", "fixed")["execution"]
    assert "--costs" not in reproduce_command(Experiment())


def test_costs_from_the_command_line_and_mcp(tmp_path):
    from typer.testing import CliRunner

    from riskaware import mcp_server as m
    from riskaware.cli import app

    runner = CliRunner()
    out = runner.invoke(app, ["backtest", "--synthetic", "--costs", "realistic", "--no-report", "--symbols",
                              "AAA,BBB,CCC", "--start", "2019-01-02", "--end", "2019-06-28"])
    assert out.exit_code == 0, out.output
    assert "Realistic costs" in out.output
    assert runner.invoke(app, ["backtest", "--synthetic", "--costs", "free", "--no-report"]).exit_code == 2
    assert runner.invoke(app, ["research", "run", "--costs", "free", "--no-leak-check"]).exit_code == 2
    assert runner.invoke(app, ["research", "baselines", "--costs", "free"]).exit_code == 2
    bad = m.run_experiment(strategy="swing_rsi2", costs="free", **WINDOW)
    assert bad["ok"] is False and "costs" in bad["error"]
    assert "costs" in m.research_guide()["execution"]
