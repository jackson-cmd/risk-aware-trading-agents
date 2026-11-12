from riskaware.risk import RiskManager
from riskaware.strategy.base import PositionView
from riskaware.types import Action, Regime, Side, Signal
from tests.conftest import base_row, make_ctx


def rm(cfg, **profile):
    cfg.profile_overrides = profile
    return RiskManager(cfg.risk_profile(), cfg.universe, cfg.execution)


def pos(sym, qty, price, avg=None, days=30, tranches=1, target=None, last_entry=None, since_entry=20):
    return PositionView(symbol=sym, qty=qty, avg_cost=avg or price, price=price, weight=0.0, days_held=days,
                        tranches=tranches, target_weight=target, last_entry_price=last_entry or avg or price,
                        days_since_entry=since_entry)


ROWS = {s: base_row(100.0) for s in ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]}
PRICES = {s: 100.0 for s in ROWS}


def buy(sym, strength=0.8):
    return Signal(sym, Action.BUY, strength, "test", ["ema50_support"])


def test_cash_floor_is_never_breached_by_buys(offline_cfg):
    # equity 100k = cash 31k + Treasury sleeve 10k + stocks 59k: only 1k of cash is above the 30% floor
    held = {f"H{i}": pos(f"H{i}", 118, 100.0) for i in range(5)}
    ctx = make_ctx(offline_cfg, Regime.RISK_ON, rows=ROWS, equity=100_000, cash=31_000, positions=held)
    ctx.portfolio.treasury_value = 10_000
    d = rm(offline_cfg, max_new_positions_per_day=10, max_buys_per_day=10, max_position=0.12).plan(
        [buy(s) for s in ["AAA", "BBB", "CCC", "DDD"]], ctx, PRICES)
    assert not d.sells()  # nothing overweight, nothing to trim
    spent = sum(o.value for o in d.buys())
    assert 31_000 - spent >= 0.30 * 100_000 - 1
    assert any("cash floor" in why for _, why in d.rejected)


def test_sector_and_position_caps(offline_cfg):
    ctx = make_ctx(offline_cfg, Regime.RISK_ON, rows=ROWS, positions={"AAA": pos("AAA", 240, 100.0)})
    # AAA is 24% of the account in Technology (not trimmed: the trim line is 1.5 x 20%)
    d = rm(offline_cfg, max_sector=0.25, max_position=0.20).plan([buy("BBB")], ctx, PRICES)
    assert not d.sells()
    b = d.buys()[0]
    assert b.value <= 0.25 * 100_000 - 24_000 + 1
    assert "sector cap" in b.reason


def test_no_discretionary_sells_when_uncertain(offline_cfg):
    held = {"AAA": pos("AAA", 50, 130.0, avg=100.0)}
    ctx = make_ctx(offline_cfg, Regime.UNCERTAIN, rows=ROWS, positions=held)
    sell = Signal("AAA", Action.SELL, 0.8, "take profit", ["take_profit"], fraction=0.33)
    d = rm(offline_cfg).plan([sell], ctx, {"AAA": 130.0})
    assert not d.sells() and "no selling" in d.rejected[0][1]
    emergency = Signal("AAA", Action.SELL, 1.0, "hard stop", ["hard_stop"], fraction=1.0, emergency=True)
    d2 = rm(offline_cfg).plan([emergency], ctx, {"AAA": 130.0})
    assert d2.sells() and d2.sells()[0].qty == 50


def test_sells_allowed_in_risk_on_and_trim_rounds_up_to_one_share(offline_cfg):
    held = {"AAA": pos("AAA", 2, 300.0, avg=200.0)}
    ctx = make_ctx(offline_cfg, Regime.RISK_ON, rows=ROWS, positions=held)
    d = rm(offline_cfg).plan([Signal("AAA", Action.SELL, 0.8, "tp", ["take_profit"], fraction=0.33)], ctx, {"AAA": 300.0})
    assert d.sells()[0].qty == 1


def test_earnings_blackout_and_min_conviction(offline_cfg):
    ctx = make_ctx(offline_cfg, Regime.RISK_ON, rows=ROWS, dte={"AAA": 2})
    d = rm(offline_cfg).plan([buy("AAA"), buy("BBB", strength=0.3)], ctx, PRICES)
    reasons = dict((s.symbol, why) for s, why in d.rejected)
    assert "earnings" in reasons["AAA"] and "conviction" in reasons["BBB"]


def test_drawdown_pause(offline_cfg):
    ctx = make_ctx(offline_cfg, Regime.RISK_ON, rows=ROWS, equity=75_000, peak=100_000)
    d = rm(offline_cfg).plan([buy("AAA")], ctx, PRICES)
    assert not d.buys() and d.rejected[0][1] == "drawdown pause"


def test_speculative_sleeve(offline_cfg):
    rows = dict(ROWS)
    rows["AAA"] = base_row(100.0, vol60=0.9)
    ctx = make_ctx(offline_cfg, Regime.RISK_ON, rows=rows)
    d = rm(offline_cfg, speculative_sleeve=0.0, speculative_max_names=0).plan([buy("AAA")], ctx, PRICES)
    assert not d.buys()


def test_max_positions_and_daily_new_position_limit(offline_cfg):
    held = {s: pos(s, 10, 100.0) for s in ["AAA", "BBB"]}
    ctx = make_ctx(offline_cfg, Regime.RISK_ON, rows=ROWS, positions=held)
    d = rm(offline_cfg, max_positions=2).plan([buy("CCC")], ctx, PRICES)
    assert "max positions" in d.rejected[0][1]
    ctx2 = make_ctx(offline_cfg, Regime.RISK_ON, rows=ROWS)
    d2 = rm(offline_cfg, max_new_positions_per_day=2).plan([buy(s) for s in ["AAA", "BBB", "CCC"]], ctx2, PRICES)
    assert len(d2.buys()) == 2


def test_first_tranche_and_rebuild_after_trim(offline_cfg):
    ctx = make_ctx(offline_cfg, Regime.RISK_ON, rows=ROWS)
    d = rm(offline_cfg).plan([buy("AAA")], ctx, PRICES)
    first = d.buys()[0]
    assert first.tranche == 1 and first.value <= first.target_weight * 0.5 * 100_000 + 200
    # a trimmed, profitable position (3 tranches done, now 2% of a 6% target) can be rebuilt
    held = {"AAA": pos("AAA", 20, 100.0, avg=80.0, tranches=3, target=0.06, since_entry=30)}
    ctx2 = make_ctx(offline_cfg, Regime.RISK_ON, rows=ROWS, positions=held)
    d2 = rm(offline_cfg).plan([buy("AAA")], ctx2, PRICES)
    assert d2.buys() and d2.buys()[0].value <= (0.06 - 0.02) * 100_000 + 200
    # a full position is not added to
    full = {"AAA": pos("AAA", 60, 100.0, tranches=3, target=0.06)}
    d3 = rm(offline_cfg).plan([buy("AAA")], make_ctx(offline_cfg, Regime.RISK_ON, rows=ROWS, positions=full), PRICES)
    assert not d3.buys() and "fully built" in d3.rejected[0][1]


def test_add_on_needs_deeper_support_or_profit(offline_cfg):
    held = {"AAA": pos("AAA", 10, 100.0, avg=110.0, last_entry=101.0, since_entry=10, target=0.06)}
    ctx = make_ctx(offline_cfg, Regime.UNCERTAIN, rows=ROWS, positions=held)
    d = rm(offline_cfg).plan([buy("AAA")], ctx, PRICES)
    assert not d.buys() and "deeper support" in d.rejected[0][1]


def test_floor_restore_trims_winners_only_when_selling_is_allowed(offline_cfg):
    held = {"AAA": pos("AAA", 500, 160.0, avg=100.0), "BBB": pos("BBB", 300, 110.0, avg=100.0)}
    equity = 500 * 160 + 300 * 110 + 15_000
    ctx = make_ctx(offline_cfg, Regime.RISK_ON, rows=ROWS, positions=held, equity=equity, cash=15_000)
    loose = dict(max_position=0.5, max_sector=0.8)  # keep the overweight trim out of the way
    d = rm(offline_cfg, **loose).plan([], ctx, {"AAA": 160.0, "BBB": 110.0})
    sells = d.sells()
    assert sells and sells[0].symbol == "AAA" and sells[0].tag == "floor_restore"
    ctx2 = make_ctx(offline_cfg, Regime.UNCERTAIN, rows=ROWS, positions=held, equity=equity, cash=15_000)
    assert not rm(offline_cfg, **loose).plan([], ctx2, {"AAA": 160.0, "BBB": 110.0}).sells()


def test_cash_vehicle_orders_live_only(offline_cfg):
    ctx = make_ctx(offline_cfg, Regime.RISK_ON, rows=ROWS)
    ctx.mode = "paper"
    d = rm(offline_cfg).plan([], ctx, {**PRICES, "SGOV": 100.0, "IEF": 95.0})
    tags = {o.tag for o in d.orders}
    assert "cash_management" in tags and "treasury_rebalance" in tags
    sgov = next(o for o in d.orders if o.tag == "cash_management")
    assert sgov.side == Side.BUY
    ctx.mode = "backtest"
    d2 = rm(offline_cfg).plan([], ctx, {**PRICES, "SGOV": 100.0, "IEF": 95.0})
    assert "cash_management" not in {o.tag for o in d2.orders}
