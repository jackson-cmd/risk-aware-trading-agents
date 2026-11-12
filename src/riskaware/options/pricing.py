"""Black-Scholes pricing, delta-based strike selection, monthly expiries and an implied-volatility model.

There is no free archive of historical option quotes, so backtests price options
with Black-Scholes. Implied volatility is modelled from the underlying's realized
volatility and VIX: broad index ETFs use VIX directly; single stocks use a blend of
20- and 60-day realized volatility, a volatility risk premium and a VIX scaling.
Fills pay half of a bid-ask spread on top of the model price. Treat the results as
an estimate of a strategy's shape, not of exact option P&L.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from .. import marketcal as mc
from ..types import OptionContract, OptionSpec

SQRT2 = math.sqrt(2.0)
INDEX_ETFS = {"SPY", "QQQ", "IWM", "DIA", "VOO", "IVV", "VTI", "RSP"}


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / SQRT2))


def norm_ppf(p: float) -> float:
    """Inverse standard normal CDF (Acklam's approximation, |error| < 1.2e-9)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    a = (-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02, 1.383577518672690e02,
         -3.066479806614716e01, 2.506628277459239e00)
    b = (-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02, 6.680131188771972e01,
         -1.328068155288572e01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00, -2.549732539343734e00,
         4.374664141464968e00, 2.938163982698783e00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00)
    lo, hi = 0.02425, 1 - 0.02425
    if p < lo:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > hi:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
        (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def _d1_d2(spot: float, strike: float, t: float, r: float, sigma: float) -> tuple[float, float]:
    vt = sigma * math.sqrt(t)
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * t) / vt
    return d1, d1 - vt


def bs_price(spot: float, strike: float, t: float, r: float, sigma: float, right: str) -> float:
    """Price per share. At or after expiry (t <= 0) this is the intrinsic value."""
    if t <= 0 or sigma <= 0:
        return max(spot - strike, 0.0) if right == "C" else max(strike - spot, 0.0)
    d1, d2 = _d1_d2(spot, strike, t, r, sigma)
    disc = math.exp(-r * t)
    if right == "C":
        return spot * norm_cdf(d1) - strike * disc * norm_cdf(d2)
    return strike * disc * norm_cdf(-d2) - spot * norm_cdf(-d1)


def bs_delta(spot: float, strike: float, t: float, r: float, sigma: float, right: str) -> float:
    if t <= 0 or sigma <= 0:
        itm = spot > strike if right == "C" else spot < strike
        return (1.0 if right == "C" else -1.0) if itm else 0.0
    d1, _ = _d1_d2(spot, strike, t, r, sigma)
    return norm_cdf(d1) if right == "C" else norm_cdf(d1) - 1.0


def strike_step(spot: float) -> float:
    if spot < 25:
        return 0.5
    if spot < 100:
        return 1.0
    if spot < 250:
        return 2.5
    return 5.0


def strike_for_delta(spot: float, t: float, r: float, sigma: float, right: str, target_delta: float) -> float:
    """Strike whose Black-Scholes delta equals ``target_delta`` (absolute value, 0..1)."""
    target = min(max(abs(target_delta), 0.02), 0.98)
    d1 = norm_ppf(target) if right == "C" else norm_ppf(1.0 - target)
    return spot * math.exp((r + 0.5 * sigma * sigma) * t - d1 * sigma * math.sqrt(t))


def monthly_expiries(after: date, months: int = 6) -> list[date]:
    """Third-Friday monthly expiries (moved to Thursday when Friday is a market holiday)."""
    out = []
    y, m = after.year, after.month
    for _ in range(months + 1):
        d = mc._nth_weekday(y, m, 4, 3)
        while not mc.is_trading_day(d):
            d -= timedelta(days=1)
        if d > after:
            out.append(d)
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def pick_expiry(day: date, target_dte: int) -> date:
    candidates = [d for d in monthly_expiries(day, 6) if (d - day).days >= max(7, target_dte // 2)]
    return min(candidates, key=lambda d: abs((d - day).days - target_dte))


def model_iv(symbol: str, vol20: float | None, vol60: float | None, vix: float | None,
             iv_premium: float = 1.1) -> float:
    """Implied volatility estimate from realized volatility and VIX."""

    def ok(x: float | None) -> bool:
        return x is not None and not math.isnan(x) and x > 0

    if symbol in INDEX_ETFS and ok(vix):
        base = vix / 100.0
        if symbol in ("QQQ",):
            base *= 1.2
        elif symbol in ("IWM",):
            base *= 1.3
        return min(max(base, 0.08), 1.5)
    rv = [x for x in (vol20, vol60) if ok(x)]
    base = (0.5 * rv[0] + 0.5 * rv[-1]) if rv else 0.35
    if rv:
        base = max(base, rv[0] * 0.8)
    scale = math.sqrt(vix / 18.0) if ok(vix) else 1.0
    return min(max(base * iv_premium * scale, 0.12), 2.0)


def choose_contract(symbol: str, spec: OptionSpec, spot: float, day: date, iv: float, r: float
                    ) -> tuple[OptionContract, float, float]:
    """Contract for a spec: (contract, model price per share, delta)."""
    expiry = pick_expiry(day, spec.dte)
    t = max((expiry - day).days, 1) / 365.0
    raw = strike_for_delta(spot, t, r, iv, spec.right, spec.target_delta)
    step = strike_step(spot)
    strike = round(raw / step) * step
    if spec.min_strike is not None:
        strike = max(strike, math.ceil(spec.min_strike / step) * step)
    if spec.max_strike is not None:
        strike = min(strike, math.floor(spec.max_strike / step) * step)
    strike = max(strike, step)
    contract = OptionContract(symbol, spec.right, round(strike, 2), expiry)
    return contract, bs_price(spot, strike, t, r, iv, spec.right), bs_delta(spot, strike, t, r, iv, spec.right)


def year_fraction(day: date, expiry: date) -> float:
    return max((expiry - day).days, 0) / 365.0
