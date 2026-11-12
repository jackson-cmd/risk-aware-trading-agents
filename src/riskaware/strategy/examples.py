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


def _bad(*vals: float) -> bool:
    return any(v is None or math.isnan(v) for v in vals)


class MomentumRotation(Strategy):
    """Every month hold the strongest names by 6-month relative strength, only above EMA200."""

    name = "momentum_rotation"
    description = "Monthly: hold the top names by 6-month relative strength above EMA200; rotate out of laggards."
    default_params = {"top_n": 6, "keep_rank": 12, "rebalance_days": 21}

    def on_start(self, universe: list[str]) -> None:
        self._last = None

    def on_bar(self, ctx: StrategyContext) -> list[Signal]:
        last = getattr(self, "_last", None)
        if last is not None and (ctx.day - last).days < self.params["rebalance_days"] * 7 / 5:
            return []
        self._last = ctx.day
        ranked = []
        for sym in ctx.universe:
            row = ctx.row(sym)
            if row is None or _bad(row.get("rs126"), row.get("ema200")) or row["close"] <= row["ema200"]:
                continue
            ranked.append((row["rs126"], sym))
        ranked.sort(reverse=True)
        top = [s for _, s in ranked[: self.params["top_n"]]]
        keep = {s for _, s in ranked[: self.params["keep_rank"]]}
        out = [Signal(sym, Action.SELL, 0.7, "fell out of the momentum leaders", ["rotation_exit"], fraction=1.0)
               for sym in ctx.portfolio.positions if sym not in keep]
        for i, sym in enumerate(top):
            out.append(Signal(sym, Action.BUY, 0.8 - 0.02 * i, f"momentum leader #{i + 1}", ["momentum"],
                              details={"ignore_tranche_rules": True}))
        return out


class DonchianBreakout(Strategy):
    """Turtle-style trend following: 55-day breakouts on volume, exit on a 20-day low."""

    name = "donchian_breakout"
    description = "Buy 55-day highs on rising volume above EMA200; exit on a 20-day low."
    default_params = {"rel_vol": 1.3}

    def on_bar(self, ctx: StrategyContext) -> list[Signal]:
        out = []
        for sym in ctx.universe:
            row = ctx.row(sym)
            if row is None or _bad(row.get("don55_high"), row.get("don20_low"), row.get("ema200")):
                continue
            pos = ctx.position(sym)
            if pos is not None and row["close"] < row["don20_low"]:
                out.append(Signal(sym, Action.SELL, 0.8, "close below the 20-day low", ["donchian_exit"], fraction=1.0))
            elif pos is None and row["close"] > row["don55_high"] and row["close"] > row["ema200"] \
                    and row.get("rel_vol", 0.0) >= self.params["rel_vol"]:
                out.append(Signal(sym, Action.BUY, 0.72, "55-day breakout on volume", ["donchian_breakout"]))
        return out


class BollingerReversion(Strategy):
    """Buy closes below the lower Bollinger band in uptrends; sell at the middle band."""

    name = "bollinger_reversion"
    description = "Buy closes below the lower Bollinger band above EMA200; sell at the middle band or after 20 days."
    default_params = {"max_gain": 0.10, "max_days": 20}

    def on_bar(self, ctx: StrategyContext) -> list[Signal]:
        out = []
        for sym in ctx.universe:
            row = ctx.row(sym)
            if row is None or _bad(row.get("bb_lower"), row.get("bb_mid"), row.get("ema200")):
                continue
            pos = ctx.position(sym)
            if pos is not None and (row["close"] >= row["bb_mid"] or pos.gain >= self.params["max_gain"]
                                    or pos.days_held >= self.params["max_days"]):
                out.append(Signal(sym, Action.SELL, 0.7, "back at the middle band", ["bollinger_exit"], fraction=1.0))
            elif pos is None and row["close"] < row["bb_lower"] and row["close"] > row["ema200"]:
                out.append(Signal(sym, Action.BUY, 0.7, "close below the lower Bollinger band", ["bollinger_dip"]))
        return out


class FearGreedDCA(Strategy):
    """Buy in steps when the fear gauge is low or VIX is high; trim in extreme greed. Best on ETFs."""

    name = "fear_greed_dca"
    description = "Buy in steps when fear is high (fear gauge low or VIX high); trim winners in extreme greed."
    default_params = {"fear_buy": 30.0, "vix_buy": 25.0, "greed_trim": 80.0, "step_days": 5}

    def on_bar(self, ctx: StrategyContext) -> list[Signal]:
        p = self.params
        fg, vix = ctx.market.fear_greed, ctx.market.vix
        fearful = (fg is not None and fg <= p["fear_buy"]) or (vix is not None and vix >= p["vix_buy"])
        out = []
        for sym in ctx.universe:
            row = ctx.row(sym)
            if row is None or _bad(row.get("close")):
                continue
            pos = ctx.position(sym)
            if fearful and (pos is None or pos.days_since_entry >= p["step_days"]):
                depth = 0.0 if fg is None else max(0.0, (p["fear_buy"] - fg) / p["fear_buy"])
                out.append(Signal(sym, Action.BUY, min(0.95, 0.7 + 0.2 * depth), "buying fear in steps",
                                  ["fear_dca"], details={"ignore_tranche_rules": True}))
            elif pos is not None and fg is not None and fg >= p["greed_trim"] and pos.gain >= 0.10 \
                    and (pos.days_since_trim is None or pos.days_since_trim >= 10):
                out.append(Signal(sym, Action.SELL, 0.7, "extreme greed: trim a third", ["greed_trim"],
                                  fraction=0.33))
        return out


BUILTIN_EXAMPLES = {cls.name: cls for cls in (BuyAndHold, EmaBounce, Rsi2MeanReversion, GoldenCross, MomentumRotation,
                                               DonchianBreakout, BollingerReversion, FearGreedDCA)}
