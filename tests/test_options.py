import math
from datetime import date

import pytest

from riskaware.backtest.engine import Backtester
from riskaware.config import AppConfig
from riskaware.data.prices import SyntheticPrices
from riskaware.options.pricing import (
    bs_delta,
    bs_price,
    choose_contract,
    model_iv,
    monthly_expiries,
    pick_expiry,
    strike_for_delta,
)
from riskaware.risk import RiskManager
from riskaware.strategy import Strategy, load_strategy
from riskaware.strategy.base import OptionPositionView, PositionView
from riskaware.types import Action, OptionSpec, Regime, Side, Signal
from tests.conftest import base_row, make_ctx

# ----------------------------------------------------------------------------- pricing


def test_put_call_parity_and_delta_bounds():
    s, k, t, r, v = 100.0, 105.0, 0.25, 0.04, 0.3
    c, p = bs_price(s, k, t, r, v, "C"), bs_price(s, k, t, r, v, "P")
    assert abs((c - p) - (s - k * math.exp(-r * t))) < 1e-9
    assert 0 < bs_delta(s, k, t, r, v, "C") < 1 and -1 < bs_delta(s, k, t, r, v, "P") < 0
    assert bs_price(s, 90, 0, r, v, "C") == 10.0 and bs_price(s, 90, 0, r, v, "P") == 0.0


@pytest.mark.parametrize("right,delta", [("C", 0.6), ("C", 0.25), ("P", 0.25), ("P", 0.4)])
def test_strike_for_delta_round_trip(right, delta):
    k = strike_for_delta(250.0, 50 / 365, 0.04, 0.35, right, delta)
    assert abs(abs(bs_delta(250.0, k, 50 / 365, 0.04, 0.35, right)) - delta) < 1e-6


def test_expiries_and_contract_choice():
    exps = monthly_expiries(date(2026, 3, 1), 4)
    assert date(2026, 6, 18) in exps  # June 19, 2026 (Juneteenth) is a holiday: expiry moves to Thursday
    assert all(e.weekday() in (3, 4) for e in exps)
    e = pick_expiry(date(2026, 1, 5), 45)
    assert 30 <= (e - date(2026, 1, 5)).days <= 60
    c, price, delta = choose_contract("AAA", OptionSpec("C", 0.6, 50), 187.3, date(2026, 1, 5), 0.3, 0.04)
    assert c.strike % 2.5 == 0 and price > 0 and 0.5 < delta < 0.7
    floor = choose_contract("AAA", OptionSpec("C", 0.25, 35, Side.SELL, min_strike=230.0), 187.3, date(2026, 1, 5),
                            0.3, 0.04)[0]
    assert floor.strike >= 230.0


def test_model_iv():
    assert model_iv("SPY", 0.1, 0.1, 30.0) == pytest.approx(0.30)
    hi, lo = model_iv("AAA", 0.5, 0.4, 35.0), model_iv("AAA", 0.5, 0.4, 12.0)
    assert hi > lo >= 0.12
    assert model_iv("AAA", float("nan"), None, None) > 0


# ----------------------------------------------------------------------------- risk manager


ROWS = {s: base_row(100.0, vol20=0.3, vol60=0.3) for s in ["AAA", "BBB", "CCC"]}
PRICES = {s: 100.0 for s in ROWS}


def opt_sig(sym="AAA", right="C", side=Side.BUY, delta=0.6, strength=0.8, contracts=None):
    return Signal(sym, Action.BUY if side == Side.BUY else Action.SELL, strength, "t", ["t"],
                  option=OptionSpec(right, delta, 45, side, contracts=contracts))


def rm(cfg):
    return RiskManager(cfg.risk_profile(), cfg.universe, cfg.execution, options=cfg.options)


def test_conservative_profile_does_not_buy_options(offline_cfg):
    offline_cfg.profile = "conservative"
    d = rm(offline_cfg).plan([opt_sig()], make_ctx(offline_cfg, Regime.RISK_ON, rows=ROWS), PRICES)
    assert not d.orders and "does not buy options" in d.rejected[0][1]


def test_long_option_premium_respects_per_trade_cap(offline_cfg):
    offline_cfg.profile = "aggressive"
    d = rm(offline_cfg).plan([opt_sig(contracts=500)], make_ctx(offline_cfg, Regime.RISK_ON, rows=ROWS), PRICES)
    o = d.orders[0]
    assert o.is_option and o.option.right == "C" and o.tag == "option_open"
    cost = o.qty * 100 * o.price * (1 + offline_cfg.options.half_spread)
    assert cost <= 0.03 * 100_000 + 1


def test_cash_secured_put_fits_caps_and_floor(offline_cfg):
    offline_cfg.profile = "aggressive"
    d = rm(offline_cfg).plan([opt_sig(right="P", side=Side.SELL, delta=0.25)],
                             make_ctx(offline_cfg, Regime.UNCERTAIN, rows=ROWS), PRICES)
    o = d.orders[0]
    collateral = o.qty * 100 * o.option.strike
    assert collateral <= 0.10 * 100_000  # single-stock cap applies to the shares an assignment would bring
    expensive = {s: base_row(400.0, vol20=0.3, vol60=0.3) for s in ROWS}
    d2 = rm(offline_cfg).plan([opt_sig(right="P", side=Side.SELL, delta=0.25)],
                              make_ctx(offline_cfg, Regime.RISK_ON, rows=expensive), {s: 400.0 for s in ROWS})
    assert not d2.orders and "no room" in d2.rejected[0][1]


def test_covered_calls_need_shares_and_protect_them(offline_cfg):
    held = {"AAA": PositionView("AAA", 250, 90.0, 100.0, 0.25, 30)}
    ctx = make_ctx(offline_cfg, Regime.RISK_ON, rows=ROWS, positions=held)
    d = rm(offline_cfg).plan([opt_sig(right="C", side=Side.SELL, delta=0.25), opt_sig("BBB", "C", Side.SELL)],
                             ctx, PRICES)
    # 250 shares are 25% of the account: 180 get trimmed back toward the cap first, so only
    # the remaining 70 shares are available and no covered call fits
    assert [o.qty for o in d.orders if o.symbol == "AAA" and not o.is_option] == [180]
    assert not [o for o in d.orders if o.symbol == "AAA" and o.is_option]
    loose = AppConfig(**offline_cfg.model_dump(exclude={"source_path"}))
    loose.profile_overrides = {"max_position": 0.3, "max_sector": 0.5}
    d_loose = rm(loose).plan([opt_sig(right="C", side=Side.SELL, delta=0.25)], ctx, PRICES)
    assert [o.qty for o in d_loose.orders if o.is_option] == [2]
    assert "100 uncovered shares" in dict((s.symbol, w) for s, w in d.rejected)["BBB"]
    # the 200 covered shares cannot be sold while the calls are open
    ctx.portfolio.options = {"k": OptionPositionView("k", "AAA", "C", 110.0, date(2024, 7, 19), -2, 1.0, 0.8, 40, 5)}
    d2 = rm(offline_cfg).plan([Signal("AAA", Action.SELL, 1.0, "exit", fraction=1.0, emergency=True)], ctx, PRICES)
    assert d2.sells()[0].qty == 50


def test_option_exits_are_allowed_when_uncertain(offline_cfg):
    ctx = make_ctx(offline_cfg, Regime.UNCERTAIN, rows=ROWS)
    ctx.portfolio.options = {"k": OptionPositionView("k", "AAA", "C", 100.0, date(2024, 7, 19), 3, 4.0, 2.0, 30, 10)}
    d = rm(offline_cfg).plan([Signal("AAA", Action.SELL, 1.0, "stop", option_key="k")], ctx, PRICES)
    assert d.orders[0].option_key == "k" and d.orders[0].side == Side.SELL and d.orders[0].qty == 3


# ----------------------------------------------------------------------------- backtester


class OneOption(Strategy):
    name = "one_option"
    default_params = {"right": "C", "side": "BUY", "delta": 0.5, "symbol": "AAA"}

    def on_start(self, universe):
        self.done = False

    def on_bar(self, ctx):
        if self.done:
            return []
        self.done = True
        p = self.params
        side = Side.BUY if p["side"] == "BUY" else Side.SELL
        return [Signal(p["symbol"], Action.BUY, 0.9, "test", ["test"],
                       option=OptionSpec(p["right"], p["delta"], 30, side, contracts=1))]


def run(strategy, profile="aggressive", cash=1_000_000, symbols=("AAA", "BBB"), seed=6):
    cfg = AppConfig(profile=profile)
    cfg.universe.preset = None
    cfg.universe.symbols = list(symbols)
    bt = Backtester(cfg, SyntheticPrices(seed=seed, start=date(2016, 1, 1), end=date(2020, 12, 31)))
    return bt.run(strategy, date(2019, 1, 2), date(2019, 6, 28), initial_cash=cash)


def test_long_call_held_to_expiry_settles_at_intrinsic():
    r = run(OneOption())
    tags = list(r.trades.tag)
    assert tags.count("option_open") == 1 and tags.count("option_expiry") == 1
    opened = r.trades[r.trades.tag == "option_open"].iloc[0]
    settled = r.trades[r.trades.tag == "option_expiry"].iloc[0]
    assert settled.pnl == pytest.approx((settled.price - opened.price) * 100, abs=1e-6)
    assert r.final_holdings == {}


def test_deep_itm_written_put_is_assigned_into_shares():
    r = run(OneOption(right="P", side="SELL", delta=0.95))
    assert "assignment" in set(r.trades.tag)
    assert r.final_holdings.get("AAA") == 100
    assert r.cash_ratio.min() >= 0.30 - 0.02  # the collateral never ate into the floor


def test_wheel_and_aggressive_calls_backtest_invariants():
    wheel = run(load_strategy("wheel"), symbols=("AAA", "BBB", "CCC", "DDD"))
    assert wheel.cash_ratio.min() >= 0.30 - 0.03
    calls = run(load_strategy("aggressive_calls"), symbols=("AAA", "BBB", "CCC", "DDD"))
    opens = calls.trades[calls.trades.tag == "option_open"]
    for t in opens.itertuples():
        eq = calls.equity.loc[:t.date].iloc[-1]
        assert t.value <= 0.03 * eq * 1.05 + 100


@pytest.mark.parametrize("name", ["momentum_rotation", "donchian_breakout", "bollinger_reversion", "fear_greed_dca",
                                  "aggressive_calls", "wheel"])
def test_new_strategies_run_and_respect_the_floor(name):
    r = run(load_strategy(name), profile="balanced", cash=500_000, symbols=("AAA", "BBB", "CCC", "DDD", "EEE"))
    assert r.equity.min() > 0 and r.cash_ratio.min() >= 0.30 - 0.03


def test_live_runs_skip_option_orders(offline_cfg, tmp_path):
    from tests.test_engine_cli import engine

    offline_cfg.execution.mode = "paper"
    offline_cfg.profile = "aggressive"
    # synthetic prices drift far by today; give the test a budget that always fits one contract
    offline_cfg.profile_overrides = {"options_per_trade": 0.2, "options_sleeve": 0.5}
    eng = engine(offline_cfg, tmp_path)

    class Always(Strategy):
        name = "always"

        def on_bar(self, ctx):
            # a cheap out-of-the-money call, so it fits the premium budget in any regime
            return [Signal(ctx.universe[0], Action.BUY, 0.9, "t", option=OptionSpec("C", 0.08, 30))]

    eng.strategy = Always()
    rep = eng.run_cycle()
    assert not rep.errors
    assert all(not o.is_option for o in rep.approved)
    assert any("option order" in n for n in rep.notes)
