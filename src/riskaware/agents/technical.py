"""Technical agent: EMA 50/100/200 support touches, mean reversion, quality dips and trends.

All rules read one indicator row (values as of the close of day t). The row can
be a pandas Series, a dict, or the fast row view the backtester uses.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..types import Regime

SETUPS_BY_REGIME: dict[Regime, set[str]] = {
    Regime.RISK_ON: {"ema50_support", "ema100_support", "ema200_support", "mean_reversion", "quality_dip",
                     "trend_breakout", "trend_continuation"},
    Regime.NEUTRAL: {"ema50_support", "ema100_support", "ema200_support", "mean_reversion", "quality_dip"},
    Regime.UNCERTAIN: {"ema100_support", "ema200_support", "deep_mean_reversion", "quality_dip"},
    Regime.RISK_OFF: {"ema200_support", "quality_dip"},
}

DEFAULT_PARAMS: dict[str, float] = {
    "touch_band_min": 0.01,  # low must reach within max(1%, 0.5 ATR) of the EMA
    "touch_band_atr": 0.5,
    "undercut_min": 0.02,  # close may sit at most max(2%, 1 ATR) below the EMA
    "undercut_atr": 1.0,
    "dip_min_drawdown": 0.15,
    "ema200_min_pullback": 0.08,
    "rsi2_oversold": 10.0,
    "rsi2_deep": 5.0,
    "z_oversold": -2.0,
    "breakout_rel_vol": 1.5,
    "adx_trend": 20.0,
    "overextended_atr": 3.0,
}


def _g(row: Mapping[str, Any] | Any, key: str) -> float:
    try:
        v = row[key]
    except (KeyError, IndexError):
        return math.nan
    try:
        return float(v)
    except (TypeError, ValueError):
        return math.nan


def _ok(*vals: float) -> bool:
    return all(not math.isnan(v) for v in vals)


@dataclass
class TechnicalView:
    symbol: str
    close: float
    setups: dict[str, float] = field(default_factory=dict)  # eligible setups and their scores
    blocked_setups: dict[str, float] = field(default_factory=dict)  # matched but not allowed in this regime
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    score: float = 0.0  # 0..1 technical buy score
    trend: str = "mixed"
    atr_pct: float = math.nan
    extension_atr: float = math.nan  # (close - EMA50) / ATR

    @property
    def best_setup(self) -> str | None:
        return max(self.setups, key=self.setups.get) if self.setups else None


class TechnicalAgent:
    def __init__(self, params: dict[str, float] | None = None, breakouts_enabled: bool = True):
        self.p = {**DEFAULT_PARAMS, **(params or {})}
        self.breakouts_enabled = breakouts_enabled

    # ---------------------------------------------------------------- helpers
    def _touch(self, row: Any, level_key: str) -> tuple[bool, float]:
        level, low, close, atrp, high10 = (_g(row, k) for k in (level_key, "low", "close", "atr_pct", "high10"))
        if not _ok(level, low, close) or level <= 0:
            return False, math.nan
        atrp = atrp if _ok(atrp) else 0.02
        band_up = max(self.p["touch_band_min"], self.p["touch_band_atr"] * atrp)
        band_dn = max(self.p["undercut_min"], self.p["undercut_atr"] * atrp)
        touched = low <= level * (1 + band_up)
        holding = close >= level * (1 - band_dn)
        # buy near the level, not after a big reversal day has already left it behind
        not_bounced_away = close <= level * (1 + band_up + 0.5 * atrp)
        from_above = (not _ok(high10)) or high10 >= level * (1 + band_up)  # a pullback, not a test from below
        return touched and holding and not_bounced_away and from_above, close / level - 1

    @staticmethod
    def _bullish_close(row: Any) -> bool:
        o, c, h, lo = (_g(row, k) for k in ("open", "close", "high", "low"))
        if not _ok(o, c, h, lo) or h <= lo:
            return False
        return c >= o or (c - lo) / (h - lo) >= 0.6

    def _stabilizing(self, row: Any) -> bool:
        c, ph, pc, r2, r2p = (_g(row, k) for k in ("close", "prev_high", "prev_close", "rsi2", "rsi2_prev"))
        if _ok(c, ph) and c > ph:
            return True
        if _ok(r2, r2p) and r2p < 15 and r2 > r2p + 10:
            return True
        return self._bullish_close(row) and _ok(c, pc) and c > pc

    # ---------------------------------------------------------------- main
    def evaluate(self, symbol: str, row: Any, regime: Regime, quality: str | None = None,
                 is_broad_etf: bool = False) -> TechnicalView:
        """``quality``: fundamental verdict (QUALITY, NEUTRAL, BROKEN, UNKNOWN, ETF) or None."""
        p = self.p
        close = _g(row, "close")
        e20, e50, e100, e200 = (_g(row, k) for k in ("ema20", "ema50", "ema100", "ema200"))
        atr14, atrp = _g(row, "atr14"), _g(row, "atr_pct")
        rsi2, rsi14, z20 = _g(row, "rsi2"), _g(row, "rsi14"), _g(row, "z20")
        s200, s50 = _g(row, "ema200_slope"), _g(row, "ema50_slope")
        dd = _g(row, "dd252")
        view = TechnicalView(symbol=symbol, close=close, atr_pct=atrp)
        if not _ok(close, e50, e200, atr14):
            view.warnings.append("not enough history for EMA200")
            return view
        stack = _ok(e100) and close > e50 > e100 > e200
        view.trend = "up" if (e50 > e200 and _ok(s200) and s200 > 0) else "down" if (close < e200 and e50 < e200) else "mixed"
        view.extension_atr = (close - e50) / atr14 if atr14 > 0 else math.nan
        matched: dict[str, float] = {}
        why: dict[str, str] = {}

        # 1. EMA support touches -------------------------------------------------
        hit, dist = self._touch(row, "ema50")
        if hit and e50 > e200 and _ok(s50) and s50 > 0:
            sc = 0.55 + (0.10 if _ok(rsi14) and 35 <= rsi14 <= 55 else 0) + (0.05 if self._bullish_close(row) else 0)
            sc += 0.05 if _ok(_g(row, "rel_vol")) and _g(row, "rel_vol") < 1.2 else 0
            matched["ema50_support"] = min(sc, 0.8)
            why["ema50_support"] = f"pullback to a rising EMA50 ({dist:+.1%} from it) in an uptrend"
        if _ok(e100):
            hit, dist = self._touch(row, "ema100")
            if hit and (e100 >= e200 * 0.98 or (_ok(s200) and s200 >= 0)):
                sc = 0.60 + (0.10 if _ok(rsi14) and rsi14 < 45 else 0) + (0.05 if self._bullish_close(row) else 0)
                matched["ema100_support"] = min(sc, 0.82)
                why["ema100_support"] = f"price at EMA100 support ({dist:+.1%})"
        hit, dist = self._touch(row, "ema200")
        if hit and _ok(s200, dd) and s200 >= -0.01 and dd <= -p["ema200_min_pullback"]:
            sc = 0.68 + (0.10 if _ok(rsi14) and rsi14 < 40 else 0) + (0.05 if self._bullish_close(row) else 0)
            if s200 < 0 and quality not in ("QUALITY", "ETF"):
                sc -= 0.10
            matched["ema200_support"] = min(sc, 0.88)
            why["ema200_support"] = f"price at the EMA200 major support ({dist:+.1%}), {abs(dd):.0%} below the 52-week high"

        # 2. Mean reversion inside an uptrend -----------------------------------
        if close > e200:
            bb_low = _g(row, "bb_lower")
            oversold = (_ok(rsi2) and rsi2 < p["rsi2_oversold"]) or (_ok(z20) and z20 < p["z_oversold"]) or (
                _ok(bb_low) and close < bb_low)
            if oversold:
                sc = 0.50 + (0.15 if _ok(rsi2) and rsi2 < p["rsi2_deep"] else 0)
                sc += 0.05 if _g(row, "down_streak") >= 3 else 0
                sc += 0.05 if _ok(s200) and s200 > 0 else 0
                matched["mean_reversion"] = min(sc, 0.75)
                why["mean_reversion"] = f"short-term oversold above EMA200 (RSI2 {rsi2:.0f}, z20 {z20:.1f})"
                if _ok(rsi2) and rsi2 < p["rsi2_deep"]:
                    matched["deep_mean_reversion"] = min(sc, 0.75)
                    why["deep_mean_reversion"] = f"deeply oversold above EMA200 (RSI2 {rsi2:.1f})"

        # 3. Quality dip (buying fear, with an earnings check) -------------------
        if _ok(dd, rsi14) and dd <= -p["dip_min_drawdown"] and rsi14 < 40 and self._stabilizing(row):
            mom252 = _g(row, "mom252")
            if quality in ("QUALITY", "ETF") or is_broad_etf:
                sc = 0.62 + (0.08 if dd <= -0.25 else 0) + (0.05 if quality == "QUALITY" else 0)
                matched["quality_dip"] = min(sc, 0.85)
                why["quality_dip"] = f"{abs(dd):.0%} below the 52-week high, stabilizing, fundamentals pass"
            elif quality in (None, "UNKNOWN") and _ok(s200, mom252) and s200 > -0.02 and mom252 > -0.10 and dd > -0.45:
                matched["quality_dip"] = 0.52
                why["quality_dip"] = f"{abs(dd):.0%} dip with the long-term trend intact (fundamentals unknown)"

        # 4. Trend entries (risk-on only) ----------------------------------------
        adx = _g(row, "adx14")
        don = _g(row, "don55_high")
        relv = _g(row, "rel_vol")
        rs = _g(row, "rs126")
        if self.breakouts_enabled and stack and _ok(don, relv, adx) and close > don and relv >= p["breakout_rel_vol"] \
                and adx > p["adx_trend"]:
            sc = 0.55 + (0.10 if _ok(rs) and rs > 0 else 0) + (0.05 if adx > 30 else 0)
            matched["trend_breakout"] = sc
            why["trend_breakout"] = f"55-day breakout on {relv:.1f}x volume, ADX {adx:.0f}"
        if stack and _ok(e20, rsi14, adx) and _g(row, "low") <= e20 * 1.01 and close >= e20 * 0.99 \
                and 40 <= rsi14 <= 60 and adx > p["adx_trend"]:
            matched["trend_continuation"] = 0.50 + (0.10 if _ok(rs) and rs > 0 else 0)
            why["trend_continuation"] = "aligned EMA stack, pullback to EMA20"

        # eligibility by regime ----------------------------------------------------
        allowed = SETUPS_BY_REGIME[regime]
        if "mean_reversion" in allowed:
            matched.pop("deep_mean_reversion", None)  # covered by the general rule
        for name, sc in matched.items():
            (view.setups if name in allowed else view.blocked_setups)[name] = sc
        if regime == Regime.RISK_OFF and is_broad_etf and "ema100_support" in matched:
            view.setups["ema100_support"] = matched["ema100_support"]
            view.blocked_setups.pop("ema100_support", None)
        view.reasons = [why[k] for k in view.setups]
        for k in view.blocked_setups:
            view.warnings.append(f"{k} matched but not allowed in {regime.value}")

        if not view.setups:
            return view
        best = max(view.setups.values())
        extra = sum(1 for v in view.setups.values() if v >= 0.5) - 1
        score = best + 0.05 * min(max(extra, 0), 3)
        if stack:
            score += 0.05
        if _ok(rs) and rs > 0:
            score += 0.05
        if view.trend == "down" and "quality_dip" not in view.setups:
            score -= 0.15
            view.warnings.append("long-term downtrend: EMA200 falling and price below it")
        if _ok(view.extension_atr) and view.extension_atr > p["overextended_atr"] and "trend_breakout" not in view.setups:
            score -= 0.25
            view.warnings.append(f"extended {view.extension_atr:.1f} ATR above EMA50")
        view.score = max(0.0, min(1.0, score))
        return view
