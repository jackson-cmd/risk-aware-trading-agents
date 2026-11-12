"""Position sizing: conviction-scaled caps, a volatility cap and the regime multiplier."""

from __future__ import annotations

import math

from .config import RiskProfile
from .types import Regime


def target_weight(strength: float, profile: RiskProfile, regime: Regime | str, atr_pct: float | None) -> float:
    """Full target weight of a position (all tranches), as a fraction of equity.

    * 60% to 100% of ``max_position`` as conviction rises from the minimum to 1.
    * capped so a two-ATR move costs at most ``risk_per_position`` of equity.
    * multiplied by the regime size multiplier.
    """
    span = max(1e-9, 1.0 - profile.min_conviction)
    scale = 0.6 + 0.4 * min(1.0, max(0.0, (strength - profile.min_conviction) / span))
    w = profile.max_position * scale
    if atr_pct is not None and not math.isnan(atr_pct) and atr_pct > 0:
        w = min(w, profile.risk_per_position / (2.0 * atr_pct))
    return w * profile.size_multiplier(regime)


def tranche_weight(full_weight: float, profile: RiskProfile, tranche_index: int) -> float:
    if tranche_index >= len(profile.tranches):
        return 0.0
    return full_weight * profile.tranches[tranche_index]


def shares_for(value: float, price: float, fractional: bool) -> float:
    if price <= 0 or value <= 0:
        return 0.0
    if fractional:
        return math.floor(value / price * 10_000) / 10_000
    return float(math.floor(value / price))
