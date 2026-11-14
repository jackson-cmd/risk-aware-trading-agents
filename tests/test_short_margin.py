"""Short selling and margin in the research kit: the rules, the planner, the backtester's books, the LLM interface."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from riskaware.news import history
from riskaware.research import agent
from riskaware.research.data import bundled_market
from riskaware.research.execution import PlainPlanner, margin_rules
from riskaware.research.experiment import Experiment, reproduce_command, run_experiment
from riskaware.strategy.base import PositionView, Strategy
from riskaware.types import Action, Regime, Signal

from .conftest import make_ctx, news_file

REAL_NEWS = news_file("news_2026.jsonl.gz")
WINDOW = {"start": "2026-02-02", "end": "2026-04-30"}
SPRING_2022 = {"period": "2022", "start": "2022-02-01", "end": "2022-05-31"}
PRICES = {s: 100.0 for s in ("AAA", "BBB", "CCC", "DDD")}


@pytest.fixture(autouse=True)
def bundled_news(monkeypatch):
    monkeypatch.setattr(history, "BUNDLE_PATH", REAL_NEWS)


def pv(sym: str, qty: float, price: float = 100.0, equity: float = 100_000.0) -> PositionView:
    return PositionView(sym, qty=qty, avg_cost=price, price=price, weight=qty * price / equity, days_held=3)


def plan(cfg, mode: str, signals: list[Signal], held: dict | None = None, equity: float = 100_000.0, **kw):
    ctx = make_ctx(cfg, Regime.NEUTRAL, positions=held or {}, equity=equity, rows={s: {"close": 100.0} for s in PRICES})
    d = PlainPlanner(mode=mode, **kw).plan(signals, ctx, PRICES)
    return [(o.symbol, o.side.value, o.qty, o.tag) for o in d.orders], {s.symbol: why for s, why in d.rejected}


# ----------------------------------------------------------------------------------- the rules
def test_rules_of_each_mode():
    assert margin_rules("long_only") is None
    ls, mg = margin_rules("long_short"), margin_rules("margin")
    assert (ls.max_gross, ls.max_short, ls.borrow_fee, ls.margin_spread) == (1.0, 0.2, 0.005, 0.015)
    assert (mg.max_gross, mg.max_short, mg.initial_margin, mg.maint_long, mg.maint_short) == (2.0, 0.4, 0.5, 0.25, 0.3)
    assert margin_rules("long_short", {"max_short": 0.1, "borrow_fee": 0.02}).max_short == 0.1
    assert mg.requirement(100_000, 50_000) == pytest.approx(40_000)
    assert mg.keep_after_call(30_000, 40_000) == pytest.approx(30_000 / 44_000)
    assert mg.keep_after_call(-5, 40_000) == 0.0
    fee, spread, loan = mg.daily_costs(cash=-50_000, short_value=0.0)
    assert fee == 0 and loan == 50_000 and spread == pytest.approx(50_000 * 0.015 / 252)
    fee, _, loan = mg.daily_costs(cash=150_000, short_value=100_000)  # proceeds are collateral, not a loan
    assert loan == 0 and fee == pytest.approx(100_000 * 0.005 / 252)
    assert mg.daily_costs(cash=60_000, short_value=100_000)[2] == 40_000  # cash below the shorts' value is borrowed
    for bad in (("long_only", {"max_short": 0.1}), ("long_short", {"max_gross": 1.5}), ("margin", {"nope": 1}),
                ("margin", {"max_gross": 4.0}), ("sideways", None)):
        with pytest.raises(ValueError):
            margin_rules(*bad)


# ----------------------------------------------------------------------------------- the planner
def test_long_short_planner_flips_caps_and_stays_within_gross(offline_cfg):
    sigs = [Signal("AAA", Action.SELL, 0.5, "flip", target_weight=-0.1),
            Signal("BBB", Action.SELL, 0.6, "short", target_weight=-0.5),
            Signal("CCC", Action.BUY, 0.9, "long", target_weight=0.8),
            Signal("DDD", Action.SELL, 0.5, "a sell is not a short")]
    orders, refused = plan(offline_cfg, "long_short", sigs, held={"AAA": pv("AAA", 300)}, max_positions=4)
    assert orders[0] == ("AAA", "SELL", 300, "exit")  # what lowers exposure goes first
    assert ("CCC", "BUY", 800, "entry") in orders  # strongest first
    assert ("BBB", "SELL", 200, "short") in orders  # capped at 20% of equity
    assert refused["AAA"].startswith("gross exposure limit")  # 80% + 20% is all of it: no room for AAA's short
    assert refused["DDD"] == "nothing to sell"


def test_covers_trims_and_a_buy_that_crosses_zero(offline_cfg):
    short = {"AAA": pv("AAA", -200)}
    assert plan(offline_cfg, "long_short", [Signal("AAA", Action.BUY, 0.5, "half", target_weight=-0.1)], short)[0] == \
        [("AAA", "BUY", 100, "cover_trim")]
    assert plan(offline_cfg, "long_short", [Signal("AAA", Action.BUY, 0.5, "all")], short)[0] == \
        [("AAA", "BUY", 200, "cover")]
    assert plan(offline_cfg, "long_short", [Signal("AAA", Action.BUY, 0.5, "flip", target_weight=0.15)], short)[0] == \
        [("AAA", "BUY", 200, "cover"), ("AAA", "BUY", 150, "entry")]
    orders, refused = plan(offline_cfg, "long_short", [Signal("AAA", Action.SELL, 0.5, "sell")], short)
    assert orders == [] and refused["AAA"] == "a short is reduced by buying"
    orders, refused = plan(offline_cfg, "long_short", [Signal("AAA", Action.SELL, 0.5, "more", target_weight=-0.4)],
                           short)
    assert orders == [] and refused["AAA"].startswith("short limit")


def test_margin_planner_levers_up_to_twice_equity(offline_cfg):
    sigs = [Signal("AAA", Action.BUY, 0.9, "levered", target_weight=1.5),
            Signal("BBB", Action.SELL, 0.5, "short", target_weight=-0.6)]
    orders, _ = plan(offline_cfg, "margin", sigs, max_positions=5)
    assert orders == [("AAA", "BUY", 1500, "entry"), ("BBB", "SELL", 400, "short")]  # short capped at 40%
    orders, refused = plan(offline_cfg, "long_short", sigs[:1])
    assert orders == [("AAA", "BUY", 1000, "entry")]  # no leverage in long_short


def test_margin_call_cuts_every_position_and_refuses_the_day(offline_cfg):
    held = {"AAA": pv("AAA", 1600, equity=40_000), "BBB": pv("BBB", -400, equity=40_000)}  # gross 200k on 40k
    need = 0.25 * 160_000 + 0.30 * 40_000
    keep = 40_000 / (need * 1.1)
    orders, refused = plan(offline_cfg, "margin", [Signal("CCC", Action.BUY, 0.5, "more")], held, equity=40_000)
    assert orders == [("AAA", "SELL", 1600 - int(1600 * keep), "margin_call"),
                      ("BBB", "BUY", 400 - int(400 * keep), "margin_call")]
    assert refused == {"CCC": "margin call"}
    orders, refused = plan(offline_cfg, "margin", [Signal("CCC", Action.BUY, 0.5, "more")], held, equity=-1_000)
    assert orders == [("AAA", "SELL", 1600, "wiped_out"), ("BBB", "BUY", 400, "wiped_out")]
    assert refused == {"CCC": "wiped out"}


def test_long_only_planner_is_unchanged(offline_cfg):
    sigs = [Signal("BBB", Action.SELL, 0.5, "x", target_weight=-0.2), Signal("CCC", Action.BUY, 0.5, "y")]
    orders, refused = plan(offline_cfg, "long_only", sigs, max_positions=4)
    assert orders == [("CCC", "BUY", 250, "entry")] and refused == {"BBB": "nothing to sell"}
    assert PlainPlanner().margin is None and PlainPlanner().describe()["long_only"] is True


# ----------------------------------------------------------------------------------- the backtester's books
class Fixed(Strategy):
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
            if w > cur:
                out.append(Signal(s, Action.BUY, 0.5, "fixed", target_weight=w))
            elif w < cur:
                out.append(Signal(s, Action.SELL, 0.5, "fixed", target_weight=w))
        return out

    def warmup_bars(self):
        return 0


def test_a_short_pays_the_borrow_fee_and_earns_the_t_bill_rate():
    exp = Experiment(strategy="fixed", universe="core", mode="long_short", news=False, leak_check=False, **WINDOW)
    r = run_experiment(exp, factory=lambda: Fixed({"NVDA": -0.2}, every=1000), save=False)
    t = r.trades
    assert list(t["tag"]) == ["short"] and t["side"].iloc[0] == "SELL"
    shares, entry = float(t["qty"].iloc[0]), float(t["price"].iloc[0])
    assert r.backtest.final_holdings == {"NVDA": -shares}
    a, eq = r.backtest.account, r.equity
    last = float(bundled_market().frame("NVDA")["close"].loc[:pd.Timestamp(WINDOW["end"])].iloc[-1])
    assert a["pnl_short"] == pytest.approx((entry - last) * shares, rel=1e-9)
    assert a["borrow_fees"] == pytest.approx(float((eq["short"] * eq["equity"]).sum()) * 0.005 / 252, rel=1e-9)
    assert a["margin_interest"] == 0.0  # 20% short, no borrowed cash
    bil = bundled_market().frame("BIL")["close"]
    days = eq.index
    t_bill = float(bil.loc[days[-1]] / bil.loc[days[0]] - 1)
    proceeds = shares * entry
    assert a["cash_interest"] == pytest.approx((exp.cash + proceeds) * t_bill, rel=0.03)
    assert eq["equity"].iloc[-1] == pytest.approx(exp.cash + a["pnl_short"] + a["cash_interest"] - a["borrow_fees"])
    assert eq["cash_ratio"].iloc[-1] == pytest.approx(1.0, abs=1e-6)  # free cash: the proceeds are collateral
    assert eq["exposure"].iloc[-1] == pytest.approx(-eq["short"].iloc[-1])  # exposure is net


def test_realistic_costs_apply_to_short_sales_and_covers():
    kw = dict(strategy="fixed", universe="core", mode="long_short", news=False, leak_check=False, **WINDOW)
    r = run_experiment(Experiment(costs="realistic", **kw), factory=lambda: Fixed({"NVDA": -0.2, "AAPL": 0.2}, 20),
                       save=False)
    t = r.trades
    short_side = t[t["tag"].isin(["short", "short_add", "cover", "cover_trim"])]
    assert len(short_side) >= 2 and (short_side["cost_bps"] > 5.0).all()  # the gap and the impact on top of 5 bps
    assert r.backtest.costs["fills_at_open"] == len(t)
    sale = short_side.iloc[0]
    open_ = float(bundled_market().frame("NVDA").loc[pd.Timestamp(sale["date"]), "open"])
    assert sale["price"] == pytest.approx(open_ * (1 - sale["cost_bps"] / 10_000))  # a short sale gets less
    fixed = run_experiment(Experiment(**kw), factory=lambda: Fixed({"NVDA": -0.2, "AAPL": 0.2}, 20), save=False)
    assert (fixed.trades["cost_bps"] == 5.0).all()


def test_margin_calls_on_a_levered_long_in_the_2020_crash():
    exp = Experiment(strategy="fixed", universe="core", mode="margin", news=False, period="2020", start="2020-02-03",
                     end="2020-04-30")
    r = run_experiment(exp, factory=lambda: Fixed({"TSLA": 2.0}, every=1000), save=False)  # 2x, held, never trimmed
    a = r.account()
    assert a["margin_calls"] >= 2 and "2020-03-13" in a["margin_call_days"] and a["wiped_out"] is None
    assert a["max_gross"] > 4.0 and a["margin_interest"] > 0  # a call fills at the next open: the crash goes on
    calls = r.trades[r.trades["tag"] == "margin_call"]
    assert len(calls) == a["margin_calls"] and (calls["side"] == "SELL").all()
    eq = r.equity
    for day in a["margin_call_days"]:  # the forced sale filled the day after a close below maintenance
        before = eq.index[eq.index.get_loc(pd.Timestamp(day)) - 1]
        assert 0.25 * eq.loc[before, "long"] > 1.0
    assert r.leak["verdict"] == "pass"
    assert "margin calls" in r.summary()["notes"][-1]
    from riskaware.research.experiment import render_report

    assert "## Short selling and margin (margin)" in render_report(r)


def test_long_short_anchors_pass_the_leak_check_and_write_the_books(tmp_path):
    r = run_experiment(Experiment(strategy="momentum_ls", universe="core", max_positions=9, mode="long_short",
                                  **WINDOW), out_dir=tmp_path / "run", chart=False)
    assert r.leak["verdict"] == "pass"
    eq = pd.read_csv(r.folder / "equity.csv")
    assert {"long", "short", "gross"} <= set(eq.columns) and eq["short"].max() > 0.3
    assert eq["gross"].max() < 1.25 and eq["gross"].median() <= 1.0 + 1e-6  # plus what prices do between rebalances
    man = json.loads((r.folder / "manifest.json").read_text())
    assert man["experiment"]["mode"] == "long_short" and man["execution"]["max_gross_exposure"] == 1.0
    assert "--mode long_short" in man["reproduce"]
    met = json.loads((r.folder / "metrics.json").read_text())
    assert met["account"]["mode"] == "long_short" and met["account"]["borrow_fees"] > 0
    trend = run_experiment(Experiment(strategy="trend_ls", params={"gross": 2.0}, universe="core", max_positions=9,
                                      mode="margin", leak_check=False, **SPRING_2022), save=False)
    assert trend.account()["avg_short"] > 0.2 and trend.account()["max_gross"] < 2.2  # shorts in the 2022 slide
    with pytest.raises(ValueError, match="plain"):
        run_experiment(Experiment(strategy="momentum_ls", universe="core", execution="risk_aware", mode="margin",
                                  **WINDOW), save=False)


def test_reproduce_command_names_the_mode():
    exp = Experiment(strategy="momentum_ls", universe="core", mode="margin", mode_params={"max_short": 0.3})
    cmd = reproduce_command(exp)
    assert "--mode margin" in cmd and "--mode-params '{\"max_short\": 0.3}'" in cmd
    assert "--mode" not in reproduce_command(Experiment(strategy="swing_rsi2"))


# ----------------------------------------------------------------------------------- the LLM interface
def short_the_worst(obs: dict) -> dict:
    """A stand-in for an LLM in a long_short run: long the best month, short the worst."""
    ranked = sorted(obs["stocks"], key=lambda d: -(d.get("ret_21d") or -9))
    return {"orders": [{"symbol": ranked[0]["symbol"], "action": "target", "weight": 0.3, "fraction": 0, "reason": "up"},
                       {"symbol": ranked[-1]["symbol"], "action": "short", "weight": 0.15, "fraction": 0,
                        "reason": "down"}], "note": "pair"}


def test_schema_and_prompt_by_mode():
    assert agent.order_schema("long_only") is agent.ORDER_SCHEMA
    enum = agent.order_schema("margin")["properties"]["orders"]["items"]["properties"]["action"]["enum"]
    assert enum == ["buy", "sell", "target", "short", "cover"]
    assert agent.ORDER_SCHEMA["properties"]["orders"]["items"]["properties"]["action"]["enum"] == ["buy", "sell",
                                                                                                 "target"]
    assert agent.mode_prompt(mode="long_only") == agent.SYSTEM_PROMPT
    assert agent.mode_prompt(mode="long_short").endswith(agent.RULES_LONG_SHORT)
    assert agent.mode_prompt("custom", "margin") == "custom " + agent.RULES_MARGIN
    assert "max_short_weight (20%)" in agent.RULES_LONG_SHORT and "max_gross_exposure (200%)" in agent.RULES_MARGIN
    custom = agent.mode_rules("long_short", margin_rules("long_short", {"max_short": 0.1, "borrow_fee": 0.02}))
    assert "max_short_weight (10%)" in custom and "borrow_fee_annual, 2.0% a year" in custom  # the run's own numbers


def test_short_and_cover_orders(offline_cfg):
    held = {"AAA": pv("AAA", -200), "BBB": pv("BBB", 100)}
    ctx = make_ctx(offline_cfg, Regime.NEUTRAL, positions=held, rows={s: {"close": 100.0} for s in PRICES})
    reply = {"orders": [{"symbol": "AAA", "action": "cover", "weight": 0, "fraction": 0.5, "reason": "half"},
                        {"symbol": "CCC", "action": "short", "weight": 0.5, "fraction": 0, "reason": "big"},
                        {"symbol": "DDD", "action": "target", "weight": -0.1, "fraction": 0, "reason": "neg"},
                        {"symbol": "BBB", "action": "cover", "weight": 0, "fraction": 1, "reason": "not short"},
                        {"symbol": "AAA", "action": "sell", "weight": 0, "fraction": 1, "reason": "wrong way"}]}
    sigs, problems, _ = agent.to_signals(reply, ctx, None, 0.2, margin_rules("long_short"))
    assert [(s.symbol, s.action, s.target_weight) for s in sigs] == [
        ("AAA", Action.BUY, pytest.approx(-0.1)), ("CCC", Action.SELL, -0.2), ("DDD", Action.SELL, -0.1)]
    assert problems == ["BBB: no short position to cover", "AAA: is a short position; use cover"]
    sigs, problems, _ = agent.to_signals(reply, ctx, None, 0.2)  # long only
    assert sigs == [] or all(s.target_weight is None or s.target_weight >= 0 for s in sigs)
    assert "AAA: action must be buy, sell or target" in problems


def test_observation_shows_the_account_only_when_shorts_are_allowed():
    exp = Experiment(universe="core", max_positions=4, mode="long_short", **SPRING_2022)
    seen: list[dict] = []

    def policy(obs):
        seen.append(obs)
        return short_the_worst(obs)

    r = agent.run_policy(policy, exp, decide_every=10, blind=True, save=False)
    assert r.leak["verdict"] == "pass" and r.leak["kind"] == "observations"
    port = seen[-1]["portfolio"]
    assert port["mode"] == "long_short" and port["max_gross_exposure"] == 1.0
    assert {"gross_exposure", "net_exposure", "buying_power", "margin_used", "maintenance_requirement",
            "borrow_fee_annual", "margin_interest_annual", "max_short_weight"} <= set(port)
    assert port["margin_interest_annual"] == "T-bill rate + 1.5%"  # the rate's level would date a blind run
    shorts = [p for p in port["positions"] if p["side"] == "short"]
    assert shorts and all(p["weight"] < 0 for p in shorts)
    assert port["gross_exposure"] == pytest.approx(sum(abs(p["weight"]) for p in port["positions"]), abs=0.01)
    assert r.account()["avg_short"] > 0
    lo: list[dict] = []
    agent.run_policy(lambda o: lo.append(o) or short_the_worst(o), Experiment(universe="core", max_positions=4,
                                                                             **SPRING_2022),
                     decide_every=10, save=False, check=False)
    assert set(lo[-1]["portfolio"]) == {"equity", "cash_share", "return_since_start", "positions", "max_positions",
                                        "default_weight"}
    assert lo[1]["problems_with_last_orders"]  # the short was refused when long only


def test_trading_session_in_margin_mode(tmp_path):
    out = agent.session_start(universe="core", decide_every=20, max_positions=3, mode="margin", **SPRING_2022)
    assert "Reg T" in out["rules"] and out["settings"]["mode"] == "margin"
    assert out["observation"]["portfolio"]["max_gross_exposure"] == 2.0
    sid = out["session_id"]
    while not out["done"]:
        stocks = out["observation"]["stocks"]
        out = agent.session_step(sid, [{"symbol": stocks[0]["symbol"], "action": "buy", "weight": 1.2, "fraction": 0,
                                        "reason": "levered"},
                                       {"symbol": stocks[-1]["symbol"], "action": "short", "weight": 0.3,
                                        "fraction": 0, "reason": "hedge"}])
    rec = Path(out["result"]["folder"])
    man = json.loads((rec / "manifest.json").read_text())
    assert man["experiment"]["mode"] == "margin" and man["leak_check"]["verdict"] == "pass"
    assert json.loads((rec / "metrics.json").read_text())["account"]["avg_gross"] > 1.0
    with pytest.raises(ValueError):
        agent.session_start(universe="core", mode="sideways", **SPRING_2022)


def test_mcp_tools_take_the_mode():
    from riskaware import mcp_server as m

    r = m.run_experiment(strategy="momentum_ls", universe="core", max_positions=9, mode="long_short",
                         leak_check=False, **WINDOW)
    assert r["ok"] and r["account"]["mode"] == "long_short" and r["execution"]["mode"] == "long_short"
    bad = m.run_experiment(strategy="momentum_ls", universe="core", mode="margin", mode_params={"max_gross": 9},
                           **WINDOW)
    assert bad["ok"] is False
    g = m.research_guide()
    assert "margin" in g["modes"] and "momentum_ls" in g["long_short_baselines"]


def test_cli_runs_a_mode(tmp_path):
    from typer.testing import CliRunner

    from riskaware.research.cli import app

    res = CliRunner().invoke(app, ["run", "--strategy", "trend_ls", "--universe", "core", "--max-positions", "9",
                                   "--mode", "margin", "--mode-params", '{"max_short": 0.3}', "--no-leak-check",
                                   "--start", WINDOW["start"], "--end", WINDOW["end"], "--out", str(tmp_path / "r"),
                                   "--json"])
    assert res.exit_code == 0, res.output
    man = json.loads((tmp_path / "r" / "manifest.json").read_text())
    assert man["experiment"]["mode_params"] == {"max_short": 0.3} and man["execution"]["max_short_weight"] == 0.3
    assert np.isfinite(json.loads((tmp_path / "r" / "metrics.json").read_text())["account"]["pnl_long"])
