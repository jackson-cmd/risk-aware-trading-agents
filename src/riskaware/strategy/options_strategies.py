"""Option strategies. Backtest-only in this version: live runs skip option orders.

The risk manager still has the last word: long premium is capped by the profile's
``options_sleeve`` and ``options_per_trade``; written puts must be fully cash-secured
without touching the cash floor; covered calls need 100 uncovered shares per contract.
"""

from __future__ import annotations

import math

from ..types import Action, OptionSpec, Regime, Side, Signal
from .base import Strategy, StrategyContext


def _nan(*vals: float) -> bool:
    return any(v is None or math.isnan(v) for v in vals)


class AggressiveCalls(Strategy):
    """Leverage strong trends with calls: buy ~0.6-delta calls 45-60 days out on pullbacks.

    Exits: +100% take profit, -50% stop, 10 days before expiry, or a break below a falling EMA50.
    Needs a profile with an options sleeve (balanced 3%, aggressive 12% of equity).
    """

    name = "aggressive_calls"
    description = ("Aggressive: buy ~0.6-delta calls 45-60 days out on pullbacks in strong uptrends; "
                   "+100% take profit, -50% stop, exit 10 days before expiry.")
    default_params = {"delta": 0.6, "dte": 50, "take_profit": 1.0, "stop": -0.5, "exit_dte": 10,
                      "max_open": 5, "vix_max": 28.0, "min_rs": 0.0}

    def on_bar(self, ctx: StrategyContext) -> list[Signal]:
        p = self.params
        out: list[Signal] = []
        open_names: set[str] = set()
        for key, op in ctx.portfolio.options.items():
            if not op.is_long or op.right != "C":
                continue
            open_names.add(op.underlying)
            row = ctx.row(op.underlying)
            reason = None
            if op.pnl_pct >= p["take_profit"]:
                reason = f"take profit {op.pnl_pct:+.0%}"
            elif op.pnl_pct <= p["stop"]:
                reason = f"stop {op.pnl_pct:+.0%}"
            elif op.dte <= p["exit_dte"]:
                reason = f"{op.dte} days to expiry"
            elif row is not None and not _nan(row.get("close"), row.get("ema50"), row.get("ema50_slope")) \
                    and row.get("close") < row.get("ema50") and row.get("ema50_slope") < 0:
                reason = "trend broke: close below a falling EMA50"
            if reason:
                out.append(Signal(op.underlying, Action.SELL, 1.0, reason, ["option_exit"], option_key=key))
        vix = ctx.market.vix
        if ctx.market.regime not in (Regime.RISK_ON, Regime.NEUTRAL) or (vix is not None and vix >= p["vix_max"]):
            return out
        slots = int(p["max_open"]) - len(open_names)
        candidates = []
        for sym in ctx.universe:
            if sym in open_names:
                continue
            row = ctx.row(sym)
            if row is None:
                continue
            c, e20, e50, e100, e200 = (row.get(k) for k in ("close", "ema20", "ema50", "ema100", "ema200"))
            slope, rs, adx = row.get("ema50_slope"), row.get("rs126"), row.get("adx14")
            if _nan(c, e20, e50, e100, e200, slope, rs) or not (c > e50 > e100 > e200 and slope > 0):
                continue
            if rs < p["min_rs"] or not (row.get("low") <= e20 * 1.01 or row.get("rsi2") < 15):
                continue
            strength = 0.62 + (0.08 if not _nan(adx) and adx > 25 else 0.0) + min(0.15, max(0.0, rs))
            candidates.append((min(strength, 0.95), sym))
        for strength, sym in sorted(candidates, reverse=True)[: max(0, slots)]:
            out.append(Signal(sym, Action.BUY, strength, "call on a pullback in a strong uptrend", ["call_pullback"],
                              option=OptionSpec("C", p["delta"], int(p["dte"]), Side.BUY)))
        return out


class Wheel(Strategy):
    """Income: sell ~0.25-delta cash-secured puts near support; if assigned, sell covered calls above cost.

    Written options are bought back at 50% of the maximum profit. Assignment and call-away
    follow the contract, whatever the market regime.
    """

    name = "wheel"
    description = ("Income: sell ~0.25-delta cash-secured puts near support (more when fear is high); "
                   "after assignment, sell covered calls above the cost basis.")
    default_params = {"put_delta": 0.25, "call_delta": 0.25, "dte": 35, "take_profit": 0.5, "max_puts": 6,
                      "vix_min": 0.0, "crash_vix": 40.0}

    def on_bar(self, ctx: StrategyContext) -> list[Signal]:
        p = self.params
        pf = ctx.portfolio
        out: list[Signal] = []
        puts_open: set[str] = set()
        calls_open: set[str] = set()
        for key, op in pf.options.items():
            if op.is_long:
                continue
            (puts_open if op.right == "P" else calls_open).add(op.underlying)
            if op.pnl_pct >= p["take_profit"] and op.dte > 5:
                out.append(Signal(op.underlying, Action.BUY, 1.0, f"buy back at {op.pnl_pct:.0%} of the premium",
                                  ["option_exit"], option_key=key))
        for sym, pos in pf.positions.items():
            if sym not in calls_open and pos.qty - pf.covered_shares(sym) >= 100:
                out.append(Signal(sym, Action.SELL, 0.8, "covered call on held shares", ["covered_call"],
                                  option=OptionSpec("C", p["call_delta"], int(p["dte"]), Side.SELL,
                                                    min_strike=pos.avg_cost)))
        vix = ctx.market.vix or 0.0
        if vix < p["vix_min"] or (ctx.market.regime == Regime.RISK_OFF and vix >= p["crash_vix"]):
            return out
        candidates = []
        for sym in ctx.universe:
            if sym in puts_open or sym in pf.positions:
                continue
            row = ctx.row(sym)
            if row is None:
                continue
            c, e50, e100, e200, slope, rsi14 = (row.get(k) for k in ("close", "ema50", "ema100", "ema200",
                                                                    "ema200_slope", "rsi14"))
            if _nan(c, e50, e100, e200, slope, rsi14) or c < e200 * 0.97 or slope <= 0:
                continue
            low = row.get("low")
            if not (low <= e50 * 1.01 or low <= e100 * 1.01 or rsi14 < 40):
                continue
            strength = 0.66 + (0.10 if vix >= 20 else 0.0) + (0.05 if rsi14 < 35 else 0.0)
            candidates.append((strength, sym))
        for strength, sym in sorted(candidates, reverse=True)[: max(0, int(p["max_puts"]) - len(puts_open))]:
            out.append(Signal(sym, Action.SELL, strength, "cash-secured put near support", ["cash_secured_put"],
                              option=OptionSpec("P", p["put_delta"], int(p["dte"]), Side.SELL)))
        return out


OPTION_STRATEGIES = {cls.name: cls for cls in (AggressiveCalls, Wheel)}
