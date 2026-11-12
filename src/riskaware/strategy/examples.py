"""Small reference strategies. Use them as templates or as baselines in ``riskaware backtest --compare``."""

from __future__ import annotations

import math

from ..types import Action, Signal
from .base import Strategy, StrategyContext


class BuyAndHold(Strategy):
    """Buy every symbol once in equal tranches and never sell. A baseline, still subject to the cash floor."""

    name = "buy_and_hold"
    description = "Equal-weight buy and hold (baseline)."

    def on_bar(self, ctx: StrategyContext) -> list[Signal]:
        out = []
        for sym in ctx.universe:
            if ctx.row(sym) is None:
                continue
            pos = ctx.position(sym)
            if pos is None or pos.tranches < len(ctx.profile.tranches):
                out.append(Signal(sym, Action.BUY, strength=1.0, reason="buy and hold", setups=["hold"],
                                  details={"ignore_tranche_rules": True}))
        return out


class EmaBounce(Strategy):
    """Buy pullbacks to EMA50/100/200 in an uptrend; take profit when stretched far above EMA50."""

    name = "ema_bounce"
    description = "Buy touches of EMA50/100/200 while EMA200 rises; sell into strength."
    default_params = {"band": 0.015, "take_profit_atr": 4.0, "take_profit_gain": 0.2}

    def on_bar(self, ctx: StrategyContext) -> list[Signal]:
        out = []
        band = self.params["band"]
        for sym in ctx.universe:
            row = ctx.row(sym)
            if row is None or math.isnan(row.get("ema200")):
                continue
            close, low = row["close"], row["low"]
            pos = ctx.position(sym)
            if pos is not None:
                ext = (close - row["ema50"]) / row["atr14"] if row["atr14"] > 0 else 0
                if pos.gain >= self.params["take_profit_gain"] and ext >= self.params["take_profit_atr"]:
                    out.append(Signal(sym, Action.SELL, 0.8, "stretched above EMA50", ["take_profit"], fraction=0.5))
                    continue
            if row["ema200_slope"] <= 0:
                continue
            for span, strength in ((50, 0.62), (100, 0.68), (200, 0.75)):
                level = row[f"ema{span}"]
                if low <= level * (1 + band) and close >= level * (1 - band):
                    out.append(Signal(sym, Action.BUY, strength, f"touch of EMA{span}", [f"ema{span}_support"]))
                    break
        return out


class Rsi2MeanReversion(Strategy):
    """Connors-style RSI(2) dips above the 200-day average, exit on a bounce."""

    name = "rsi2"
    description = "Buy RSI(2) < 10 above EMA200, sell when RSI(2) > 70 or after a 15% gain."
    default_params = {"entry": 10.0, "exit": 70.0, "max_gain": 0.15}

    def on_bar(self, ctx: StrategyContext) -> list[Signal]:
        out = []
        for sym in ctx.universe:
            row = ctx.row(sym)
            if row is None or math.isnan(row.get("ema200")) or math.isnan(row.get("rsi2")):
                continue
            pos = ctx.position(sym)
            if pos is None and row["close"] > row["ema200"] and row["rsi2"] < self.params["entry"]:
                out.append(Signal(sym, Action.BUY, 0.7, f"RSI2 {row['rsi2']:.0f} above EMA200", ["rsi2_dip"]))
            elif pos is not None and (row["rsi2"] > self.params["exit"] or pos.gain >= self.params["max_gain"]):
                out.append(Signal(sym, Action.SELL, 0.7, "RSI2 bounce", ["rsi2_exit"], fraction=1.0))
        return out


class GoldenCross(Strategy):
    """Trend following: hold while EMA50 is above EMA200, exit on the death cross."""

    name = "golden_cross"
    description = "Buy when EMA50 > EMA200 and price > EMA50; sell on EMA50 < EMA200."

    def on_bar(self, ctx: StrategyContext) -> list[Signal]:
        out = []
        for sym in ctx.universe:
            row = ctx.row(sym)
            if row is None or math.isnan(row.get("ema200")):
                continue
            pos = ctx.position(sym)
            up = row["ema50"] > row["ema200"]
            if pos is None and up and row["close"] > row["ema50"]:
                out.append(Signal(sym, Action.BUY, 0.65, "golden cross regime", ["golden_cross"],
                                  details={"ignore_tranche_rules": True}))
            elif pos is not None and not up:
                out.append(Signal(sym, Action.SELL, 0.7, "death cross", ["death_cross"], fraction=1.0))
        return out


BUILTIN_EXAMPLES = {cls.name: cls for cls in (BuyAndHold, EmaBounce, Rsi2MeanReversion, GoldenCross)}
