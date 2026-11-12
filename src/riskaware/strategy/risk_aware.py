"""The built-in multi-agent strategy.

For every symbol it asks the technical agent for a setup, checks the earnings
quality when the setup is a dip, reads the verified news, blends everything
into a conviction, and proposes exits for held positions. The risk manager
decides sizes afterwards.
"""

from __future__ import annotations

import math
from typing import Any

from ..agents.technical import TechnicalAgent
from ..types import Action, Regime, Signal
from .base import PositionView, Strategy, StrategyContext

DIP_SETUPS = {"quality_dip", "ema200_support", "deep_mean_reversion"}
QUALITY_SENSITIVE = {"quality_dip", "ema200_support"}


class RiskAwareStrategy(Strategy):
    name = "risk_aware"
    description = ("Multi-agent long-only strategy: EMA 50/100/200 support, mean reversion, quality dips "
                   "and trend entries, gated by macro regime, event risk, earnings quality and verified news.")
    default_params: dict[str, Any] = {
        "use_news": True,
        "use_fundamentals": True,
        "unconfirmed_flag_penalty": 0.10,
        "negative_news_veto": -0.5,  # confirmed news score below this blocks a buy
        "technical_params": {},
    }

    def __init__(self, **params: Any):
        super().__init__(**params)
        self.tech: TechnicalAgent | None = None

    def on_start(self, universe: list[str]) -> None:
        self.tech = None

    def _tech(self, ctx: StrategyContext) -> TechnicalAgent:
        if self.tech is None:
            self.tech = TechnicalAgent(self.params.get("technical_params") or {},
                                       breakouts_enabled=ctx.profile.breakouts_enabled)
        return self.tech

    # ------------------------------------------------------------------ entries
    def on_bar(self, ctx: StrategyContext) -> list[Signal]:
        signals: list[Signal] = []
        for sym, pos in ctx.portfolio.positions.items():
            sig = self.exit_signal(ctx, sym, pos)
            if sig is not None:
                signals.append(sig)
        sold = {s.symbol for s in signals if s.action == Action.SELL and (s.fraction or 0) >= 0.999}
        for sym in ctx.universe:
            if sym in sold:
                continue
            sig = self.entry_signal(ctx, sym)
            if sig is not None:
                signals.append(sig)
        return signals

    def entry_signal(self, ctx: StrategyContext, sym: str) -> Signal | None:
        row = ctx.row(sym)
        if row is None:
            return None
        regime = ctx.market.regime
        tech = self._tech(ctx)
        broad = ctx.is_broad_etf(sym)
        etf = ctx.is_etf(sym)
        first = tech.evaluate(sym, row, regime, quality="ETF" if etf else None, is_broad_etf=broad)
        matched = set(first.setups) | set(first.blocked_setups)
        if not first.setups and not (matched & QUALITY_SENSITIVE):
            return None

        quality = None
        q_report = None
        if self.params["use_fundamentals"] and not etf:
            q_report = ctx.quality(sym)
            if q_report is not None:
                quality = q_report.verdict
        view = tech.evaluate(sym, row, regime, quality=quality or ("ETF" if etf else None), is_broad_etf=broad) \
            if quality is not None else first
        if not view.setups:
            return None

        reasons = list(view.reasons)
        warnings = list(view.warnings)
        components: dict[str, float] = {"technical": view.score}
        best = view.best_setup or ""

        if q_report is not None and q_report.verdict != "UNKNOWN":
            if q_report.verdict == "BROKEN" and set(view.setups) <= DIP_SETUPS | {"ema100_support", "mean_reversion"}:
                return None  # a falling knife: weak fundamentals behind a dip
            components["fundamental"] = q_report.score / 100.0
            reasons.append(f"earnings quality {q_report.score:.0f}/100 ({q_report.verdict.lower()})")
            reasons.extend(q_report.reasons[:2])
            warnings.extend(q_report.warnings[:2])

        news_pen = 0.0
        if self.params["use_news"]:
            report = ctx.news(sym)
            if report is not None and getattr(report, "has_news", False):
                if report.severe_confirmed or report.red_flags_confirmed:
                    return None  # confirmed red flags block new buys
                if report.score <= self.params["negative_news_veto"] and report.confirmed:
                    return None
                components["news"] = (report.score + 1.0) / 2.0
                reasons.append(f"news {report.score:+.2f} from {report.confirmed} confirmed stories")
                if report.red_flags_unconfirmed:
                    news_pen = self.params["unconfirmed_flag_penalty"]
                    warnings.append("single-source red flag: " + ", ".join(report.red_flags_unconfirmed))

        m = ctx.market
        macro = (m.macro_score + 1.0) / 2.0
        if best in DIP_SETUPS:
            macro = max(macro, (m.dip_tailwind + 1.0) / 2.0)
        components["macro"] = macro

        weights = ctx.scoring_weights
        total_w = sum(weights[k] for k in components)
        conviction = sum(weights[k] * v for k, v in components.items()) / total_w - news_pen
        conviction = max(0.0, min(1.0, conviction))

        pos = ctx.position(sym)
        tag = "tranche" if pos is not None else "entry"
        return Signal(
            symbol=sym, action=Action.BUY, strength=conviction,
            reason="; ".join(reasons[:4]), setups=sorted(view.setups, key=view.setups.get, reverse=True),
            details={"components": components, "warnings": warnings, "atr_pct": view.atr_pct,
                     "trend": view.trend, "tag": tag, "best_setup": best,
                     "quality": quality, "close": view.close},
        )

    # -------------------------------------------------------------------- exits
    def exit_signal(self, ctx: StrategyContext, sym: str, pos: PositionView) -> Signal | None:
        row = ctx.row(sym)
        if row is None:
            return None
        prof = ctx.profile
        close = row.get("close") if hasattr(row, "get") else row["close"]
        gain = pos.gain

        # emergency exits (allowed in every regime when the profile allows them)
        if prof.hard_stop is not None and gain <= -prof.hard_stop:
            return Signal(sym, Action.SELL, 1.0, f"hard stop: {gain:.0%} loss", ["hard_stop"], fraction=1.0,
                          emergency=True, details={"tag": "hard_stop"})
        if self.params["use_news"] and ctx.mode != "backtest":
            report = ctx.news(sym)
            if report is not None and getattr(report, "severe_confirmed", False):
                flags = getattr(report, "red_flags_backed", None) or report.red_flags_confirmed
                return Signal(sym, Action.SELL, 1.0,
                              "thesis break: " + ", ".join(flags) + " backed by the company's own filing or release",
                              ["thesis_break"], fraction=1.0, emergency=True, details={"tag": "thesis_break"})

        if ctx.market.regime not in (Regime.RISK_ON, Regime.NEUTRAL):
            return None  # uncertain or risk-off: hold, no discretionary selling

        atr = row.get("atr14")
        e50 = row.get("ema50")
        rsi14 = row.get("rsi14")
        if any(math.isnan(x) for x in (atr, e50, rsi14)) or atr <= 0:
            return None
        ext = (close - e50) / atr

        if gain >= prof.take_profit_trigger and pos.days_held >= prof.min_hold_days:
            stretched = ext >= prof.take_profit_atr_ext or rsi14 >= prof.take_profit_rsi
            fresh = pos.days_since_trim is None or (
                pos.days_since_trim >= 5 and (pos.last_trim_price is None or close >= pos.last_trim_price + atr))
            if stretched and fresh:
                return Signal(sym, Action.SELL, 0.8,
                              f"take profit: +{gain:.0%}, {ext:.1f} ATR above EMA50, RSI14 {rsi14:.0f}",
                              ["take_profit"], fraction=prof.take_profit_fraction, details={"tag": "take_profit"})

        if prof.trend_exit_enabled and gain >= prof.trend_exit_min_gain:
            streak = row.get("below_ema50_streak")
            slope = row.get("ema50_slope")
            if not math.isnan(streak) and streak >= 3 and not math.isnan(slope) and slope < 0:
                fresh = pos.days_since_trim is None or pos.days_since_trim >= 10
                if fresh:
                    return Signal(sym, Action.SELL, 0.7,
                                  f"trend exit: 3 closes below a falling EMA50, locking +{gain:.0%}",
                                  ["trend_exit"], fraction=prof.trend_exit_fraction, details={"tag": "trend_exit"})
        return None
