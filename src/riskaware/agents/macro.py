"""Macro agent: market regime, a price-based fear-and-greed gauge and event risk.

The whole history is computed in one vectorized pass, so backtests and live
runs use the same numbers. ``current()`` is just the last row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from .. import indicators as ind
from ..config import MacroConfig
from ..data.events import EventCalendar
from ..types import MacroEvent, Regime
from ..universe import SECTOR_ETFS

REGIME_ORDER = {Regime.RISK_ON: 0, Regime.NEUTRAL: 1, Regime.UNCERTAIN: 2, Regime.RISK_OFF: 3}


def to_regime(value) -> Regime:
    """Accept a Regime, its value ("RISK_ON") or its repr ("Regime.RISK_ON")."""
    if isinstance(value, Regime):
        return value
    text = str(value)
    return Regime(text.split(".")[-1])


@dataclass
class MarketState:
    day: date
    regime: Regime
    risk_score: float
    fear_greed: float | None
    vix: float | None
    vix_ratio: float | None
    spy_close: float | None
    spy_trend: str
    breadth: float | None
    credit_z: float | None
    event_risk: float
    events: list[MacroEvent] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    macro_score: float = 0.0  # -1..1 tailwind for trend buys
    dip_tailwind: float = 0.0  # -1..1, positive when fear is high (better for buying dips)
    cnn_fear_greed: float | None = None
    risk_free: float = 0.04  # annual rate used to price options

    @property
    def fear_label(self) -> str:
        return fear_label(self.fear_greed)


def fear_label(x: float | None) -> str:
    if x is None or np.isnan(x):
        return "n/a"
    if x < 25:
        return "extreme fear"
    if x < 45:
        return "fear"
    if x <= 55:
        return "neutral"
    if x <= 75:
        return "greed"
    return "extreme greed"


def _close(frames: dict[str, pd.DataFrame], sym: str, index: pd.DatetimeIndex) -> pd.Series:
    df = frames.get(sym)
    if df is None or df.empty:
        return pd.Series(np.nan, index=index)
    return df["close"].reindex(index).ffill(limit=5)


class MacroAgent:
    def __init__(self, cfg: MacroConfig | None = None, calendar: EventCalendar | None = None):
        self.cfg = cfg or MacroConfig()
        self.calendar = calendar

    def compute_series(self, frames: dict[str, pd.DataFrame], index: pd.DatetimeIndex | None = None) -> pd.DataFrame:
        cfg = self.cfg
        if index is None:
            spy_df = frames.get("SPY")
            if spy_df is None or spy_df.empty:
                raise ValueError("macro data needs SPY")
            index = spy_df.index
        spy = _close(frames, "SPY", index)
        vix = _close(frames, "^VIX", index)
        vix3m = _close(frames, "^VIX3M", index)
        hyg, ief, tlt = (_close(frames, s, index) for s in ("HYG", "IEF", "TLT"))

        out = pd.DataFrame(index=index)
        out["spy"] = spy
        out["spy_ema50"] = ind.ema(spy, 50)
        out["spy_ema200"] = ind.ema(spy, 200)
        out["spy_ema50_slope"] = out["spy_ema50"] / out["spy_ema50"].shift(10) - 1
        out["vix"] = vix
        out["vix_ratio"] = vix / vix3m

        above = []
        for etf in SECTOR_ETFS:
            c = _close(frames, etf, index)
            if c.notna().sum() > 220:
                e = ind.ema(c, 200)
                above.append(((c > e).astype(float)).where(e.notna()))
        if len(above) >= 3:
            mat = pd.concat(above, axis=1)
            counts = mat.notna().sum(axis=1)
            out["breadth"] = mat.mean(axis=1, skipna=True).where(counts >= 3)
        else:
            out["breadth"] = np.nan

        credit_mom = (hyg / ief).pct_change(20)
        mu = credit_mom.rolling(252, min_periods=60).mean()
        sd = credit_mom.rolling(252, min_periods=60).std().replace(0, np.nan)
        out["credit_z"] = (credit_mom - mu) / sd

        # fear & greed: each component is a one-year percentile, 0 = fear, 100 = greed
        comps = pd.DataFrame(index=index)
        comps["momentum"] = ind.pct_rank(spy / ind.sma(spy, 125) - 1)
        comps["breadth"] = out["breadth"] * 100
        comps["volatility"] = 100 - ind.pct_rank(vix / ind.sma(vix, 50))
        comps["term"] = 100 - ind.pct_rank(out["vix_ratio"])
        comps["safe_haven"] = ind.pct_rank(spy.pct_change(20) - tlt.pct_change(20))
        comps["junk"] = ind.pct_rank(hyg.pct_change(20) - ief.pct_change(20))
        out["fear_greed"] = comps.mean(axis=1, skipna=True).where(comps.notna().sum(axis=1) >= 3)

        if self.calendar is not None:
            ev = self.calendar.event_risk_series(index)
            out["event_risk"] = ev["risk"].to_numpy()
            out["events"] = ev["events"].to_numpy()
        else:
            out["event_risk"] = 0.0
            out["events"] = ""

        # raw regime
        uptrend = (spy > out["spy_ema50"]) & (out["spy_ema50"] > out["spy_ema200"]) & (out["spy_ema50_slope"] > 0)
        below200 = spy < out["spy_ema200"]
        downtrend = below200 & (out["spy_ema50"] < out["spy_ema200"])
        vr = out["vix_ratio"]
        fg = out["fear_greed"]
        br = out["breadth"]
        risk_off = (
            (vix >= cfg.vix_risk_off)
            | ((vr >= cfg.backwardation_ratio) & below200)
            | ((out["credit_z"] <= cfg.credit_stress_z) & below200)
            | ((fg <= cfg.fear_extreme) & below200)
        )
        uncertain = (
            (out["event_risk"] >= cfg.events.uncertain_threshold)
            | (vix >= cfg.vix_uncertain)
            | (vr >= cfg.flat_term_ratio)
            | (fg <= cfg.fear_uncertain)
            | downtrend
        )
        risk_on = uptrend & (vix < cfg.vix_uncertain) & ((br >= cfg.breadth_risk_on) | br.isna())
        raw = np.where(risk_off, 3, np.where(uncertain, 2, np.where(risk_on, 0, 1)))
        valid = spy.notna() & out["spy_ema200"].notna()
        raw = np.where(valid, raw, 1)
        out["regime_code"] = self._hysteresis(raw, out["event_risk"].to_numpy() >= cfg.events.uncertain_threshold)
        code_to_regime = {0: Regime.RISK_ON, 1: Regime.NEUTRAL, 2: Regime.UNCERTAIN, 3: Regime.RISK_OFF}
        out["regime"] = pd.Series([code_to_regime[int(c)] for c in out["regime_code"]], index=index, dtype=object)

        # 0..100 stress score
        trend_pts = np.where(below200, 20, np.where(spy < out["spy_ema50"], 10, 0))
        out["risk_score"] = (
            ((vix - 12) / 28).clip(0, 1).fillna(0.3) * 35
            + ((vr - 0.85) / 0.25).clip(0, 1).fillna(0.3) * 15
            + trend_pts
            + (1 - br.fillna(0.5)) * 10
            + (-out["credit_z"] / 3).clip(0, 1).fillna(0) * 10
            + out["event_risk"] * 10
        ).clip(0, 100)
        base = out["regime_code"].map({0: 0.6, 1: 0.1, 2: -0.2, 3: -0.4})
        out["macro_score"] = base
        out["dip_tailwind"] = ((50 - fg.fillna(50)) / 50).clip(-1, 1)
        out["spy_trend"] = np.where(uptrend, "up", np.where(downtrend, "down", "mixed"))
        return out

    @staticmethod
    def _hysteresis(raw: np.ndarray, event_active: np.ndarray) -> np.ndarray:
        """Move to a riskier regime at once; move to a safer one only after two confirming days
        (event windows end immediately)."""
        out = raw.copy()
        pending = 0
        for i in range(1, len(raw)):
            prev = out[i - 1]
            if raw[i] >= prev or (event_active[i - 1] and not event_active[i]):
                out[i] = raw[i]
                pending = 0
            else:
                pending += 1
                out[i] = raw[i] if pending >= 2 else prev
                if pending >= 2:
                    pending = 0
        return out

    def state_from_row(self, row: pd.Series, day: date) -> MarketState:
        cfg = self.cfg

        def f(key: str) -> float | None:
            v = row.get(key)
            return None if v is None or (isinstance(v, float) and np.isnan(v)) else float(v)

        regime = to_regime(row["regime"])
        reasons: list[str] = []
        vix, vr, fg, br = f("vix"), f("vix_ratio"), f("fear_greed"), f("breadth")
        if vix is not None:
            tag = " (risk-off level)" if vix >= cfg.vix_risk_off else " (elevated)" if vix >= cfg.vix_uncertain else ""
            reasons.append(f"VIX {vix:.1f}{tag}")
        if vr is not None:
            tag = "backwardation: near-term fear" if vr >= cfg.backwardation_ratio else (
                "flat term structure" if vr >= cfg.flat_term_ratio else "contango (normal)")
            reasons.append(f"VIX/VIX3M {vr:.2f}, {tag}")
        trend = row.get("spy_trend", "mixed")
        reasons.append({"up": "SPY above a rising EMA50 and EMA200", "down": "SPY below EMA200 with EMA50 under EMA200",
                        "mixed": "SPY trend mixed"}[trend])
        if br is not None:
            reasons.append(f"{br * 100:.0f}% of sector ETFs above EMA200")
        if fg is not None:
            reasons.append(f"Fear & greed gauge {fg:.0f} ({fear_label(fg)})")
        cz = f("credit_z")
        if cz is not None and cz <= cfg.credit_stress_z:
            reasons.append(f"credit stress: HYG/IEF momentum z = {cz:.1f}")
        events: list[MacroEvent] = []
        if self.calendar is not None:
            _, events = self.calendar.event_risk(day)
            for ev in events:
                reasons.append(f"event window: {ev.name} on {ev.day:%b %d}" + (" (estimated)" if ev.estimated else ""))
        return MarketState(
            day=day, regime=regime, risk_score=float(row.get("risk_score", 50.0)), fear_greed=fg, vix=vix,
            vix_ratio=vr, spy_close=f("spy"), spy_trend=str(trend), breadth=br, credit_z=cz,
            event_risk=float(row.get("event_risk", 0.0)), events=events, reasons=reasons,
            macro_score=float(row.get("macro_score", 0.0)), dip_tailwind=float(row.get("dip_tailwind", 0.0)),
        )

    def current(self, frames: dict[str, pd.DataFrame]) -> tuple[MarketState, pd.DataFrame]:
        series = self.compute_series(frames)
        last = series.iloc[-1]
        return self.state_from_row(last, series.index[-1].date()), series
