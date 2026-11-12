from riskaware.agents.fundamental import FundamentalAgent
from riskaware.agents.technical import TechnicalAgent
from riskaware.data.fundamentals import FundamentalSnapshot
from riskaware.types import Regime
from tests.conftest import _DictRow, base_row

T = TechnicalAgent()


def ema50_touch_row():
    # uptrend, the low dips to EMA50 and the close holds just above it
    return _DictRow(base_row(close=101.0, low=99.6, ema50=100.0, ema100=96.0, ema200=90.0, ema50_slope=0.01,
                             rsi14=45.0, high10=106.0, open=100.0))


def test_ema50_pullback_in_uptrend():
    v = T.evaluate("AAA", ema50_touch_row(), Regime.NEUTRAL)
    assert "ema50_support" in v.setups and v.score >= 0.6


def test_uncertain_regime_blocks_ema50_but_allows_ema200():
    v = T.evaluate("AAA", ema50_touch_row(), Regime.UNCERTAIN)
    assert "ema50_support" not in v.setups and "ema50_support" in v.blocked_setups
    row = _DictRow(base_row(close=100.5, low=99.2, ema50=110, ema100=106, ema200=100.0, ema200_slope=0.01,
                            dd252=-0.18, rsi14=36.0, high10=108.0))
    v2 = T.evaluate("AAA", row, Regime.UNCERTAIN)
    assert "ema200_support" in v2.setups


def test_touch_from_below_is_not_support():
    row = _DictRow(base_row(close=99.5, low=98.0, ema50=100.0, ema200=90.0, high10=99.8))
    v = T.evaluate("AAA", row, Regime.NEUTRAL)
    assert "ema50_support" not in v.setups


def test_big_reversal_day_far_above_ema_is_not_a_support_buy():
    row = _DictRow(base_row(close=108.6, low=100.5, ema50=100.0, ema200=90.0, high10=110.0, atr_pct=0.03))
    v = T.evaluate("AAA", row, Regime.NEUTRAL)
    assert "ema50_support" not in v.setups


def test_quality_dip_requires_quality_or_trend_proxy():
    row = _DictRow(base_row(close=80.0, prev_high=79.0, dd252=-0.25, rsi14=33.0, ema50=88, ema100=90, ema200=92,
                            ema200_slope=-0.05, mom252=-0.3, high10=86.0))
    broken = T.evaluate("AAA", row, Regime.RISK_OFF, quality="BROKEN")
    assert "quality_dip" not in broken.setups
    unknown = T.evaluate("AAA", row, Regime.RISK_OFF, quality=None)
    assert "quality_dip" not in unknown.setups  # falling EMA200 and a weak year: no proxy
    good = T.evaluate("AAA", row, Regime.RISK_OFF, quality="QUALITY")
    assert "quality_dip" in good.setups


def test_mean_reversion_and_overextension_penalty():
    row = _DictRow(base_row(close=105.0, rsi2=4.0, z20=-2.3, ema200=95.0, down_streak=4))
    v = T.evaluate("AAA", row, Regime.NEUTRAL)
    assert "mean_reversion" in v.setups and not v.blocked_setups.get("deep_mean_reversion")
    u = T.evaluate("AAA", row, Regime.UNCERTAIN)
    assert "deep_mean_reversion" in u.setups and "mean_reversion" not in u.setups
    hot = _DictRow(base_row(close=130.0, rsi2=4.0, ema50=110.0, ema200=95.0, atr14=2.0))
    hv = T.evaluate("AAA", hot, Regime.NEUTRAL)
    assert any("extended" in w for w in hv.warnings)


def test_breakout_only_in_risk_on():
    row = _DictRow(base_row(close=121.0, don55_high=120.0, rel_vol=2.0, adx14=28.0, ema50=110.0, ema100=105.0,
                            ema200=100.0, rs126=0.05))
    assert "trend_breakout" in T.evaluate("AAA", row, Regime.RISK_ON).setups
    assert "trend_breakout" not in T.evaluate("AAA", row, Regime.NEUTRAL).setups


def test_not_enough_history():
    row = _DictRow(base_row(ema200=float("nan")))
    assert T.evaluate("AAA", row, Regime.NEUTRAL).score == 0.0


def test_fundamental_agent_verdicts():
    fa = FundamentalAgent()
    good = FundamentalSnapshot("AAA", "2025-01-01", revenue_yoy=0.2, net_margin=0.2, fcf_ttm=1e9, debt_to_equity=0.3,
                               eps_surprises=[5, 3, 2, 1], gross_margin_change=0.02)
    bad = FundamentalSnapshot("BBB", "2025-01-01", revenue_yoy=-0.2, net_margin=-0.1, fcf_ttm=-1e8, debt_to_equity=4.0,
                              eps_surprises=[-10, -5, -2, -1], gross_margin_change=-0.05)
    assert fa.evaluate(good).verdict == "QUALITY"
    assert fa.evaluate(bad).verdict == "BROKEN"
    assert fa.evaluate(None).verdict == "UNKNOWN"
    assert fa.evaluate(FundamentalSnapshot("SPY", "2025-01-01", is_etf=True)).verdict == "ETF"
