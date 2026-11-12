"""Example: buy broad ETFs and quality leaders only when fear is high.

    riskaware backtest --strategy examples/strategies/vix_fear_dip.py --preset etf --compare buy_and_hold
"""

from riskaware.strategy import Action, Signal, Strategy
from riskaware.types import Regime


class VixFearDip(Strategy):
    name = "vix_fear_dip"
    description = "Buy oversold names above EMA200 when VIX is elevated; sell into the next calm rally."
    default_params = {"vix_min": 22.0, "rsi_max": 35.0, "take_profit": 0.12}

    def on_bar(self, ctx):
        signals = []
        vix = ctx.market.vix or 0.0
        for sym in ctx.universe:
            row = ctx.row(sym)
            if row is None or row.get("ema200") != row.get("ema200"):
                continue
            pos = ctx.position(sym)
            if pos is None and vix >= self.params["vix_min"] and row["rsi14"] <= self.params["rsi_max"] \
                    and row["close"] > row["ema200"] * 0.97:
                strength = min(0.9, 0.6 + (vix - self.params["vix_min"]) / 50)
                signals.append(Signal(sym, Action.BUY, strength, f"VIX {vix:.0f}, RSI14 {row['rsi14']:.0f}",
                                      ["fear_dip"]))
            elif pos and pos.gain >= self.params["take_profit"] and ctx.market.regime == Regime.RISK_ON:
                signals.append(Signal(sym, Action.SELL, 0.8, "calm rally: take profit", ["take_profit"], fraction=0.5))
        return signals
