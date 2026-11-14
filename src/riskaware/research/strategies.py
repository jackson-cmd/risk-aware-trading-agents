"""The research kit's baselines: five swing strategies (defined in ``riskaware.strategy.swing``) and two rule-based
long-short strategies, anchors for the long_short and margin modes (docs/short-margin.md)."""

from __future__ import annotations

from typing import Any

from ..strategy.base import Strategy
from ..strategy.swing import (
    SWING_STRATEGIES,
    SwingBreakout,
    SwingNews,
    SwingPullback,
    SwingRsi2,
    SwingTrend,
)
from ..types import Action, Signal

RESEARCH_STRATEGIES = SWING_STRATEGIES


def _momentum(ctx: Any, lookback: int, skip: int) -> dict[str, float]:
    """Each stock's return over ``lookback`` trading days up to ``skip`` days ago (12-1 momentum by default)."""
    out = {}
    for sym in ctx.universe:
        h = ctx.history(sym, lookback + 1)
        if h is None or len(h) < lookback + 1:
            continue
        c = h["close"].to_numpy()
        if c[0] > 0 and c[-1 - skip] == c[-1 - skip]:
            out[sym] = float(c[-1 - skip] / c[0] - 1.0)
    return out


def _rebalance(ctx: Any, targets: dict[str, float], strength: dict[str, float]) -> list[Signal]:
    """Signals that move every stock to its signed target weight (0 for the ones not in ``targets``)."""
    signals = []
    for sym in ctx.universe:
        pos = ctx.position(sym)
        cur = pos.weight if pos is not None else 0.0
        t = targets.get(sym, 0.0)
        if abs(t - cur) < 1e-9 or (pos is None and t == 0.0):
            continue
        action = Action.BUY if t > cur else Action.SELL
        why = "long" if t > 0 else "short" if t < 0 else "out of the book"
        signals.append(Signal(sym, action, strength.get(sym, 0.1), why, target_weight=t))
    return signals


class MomentumLongShort(Strategy):
    """Cross-sectional momentum: long the third of the universe with the best 12-1 month return, short the worst
    third, equal weights, rebalanced every ``every`` trading days. Dollar neutral with ``gross`` of equity in all
    (half long, half short): 1.0 fits the long_short mode, 2.0 the margin mode. Long only, the shorts are refused."""

    name = "momentum_ls"
    description = ("Long the best third of the universe by 12-1 month return, short the worst third, equal weights, "
                   "weekly; dollar neutral (needs mode long_short or margin for the short side)")
    default_params: dict[str, Any] = {"lookback": 252, "skip": 21, "every": 5, "top": 1 / 3, "gross": 1.0}

    def on_start(self, universe: list[str]) -> None:
        self.day = 0

    def warmup_bars(self) -> int:
        return int(self.params["lookback"]) + 1

    def on_bar(self, ctx: Any) -> list[Signal]:
        p = self.params
        self.day += 1
        if (self.day - 1) % max(1, int(p["every"])):
            return []
        mom = _momentum(ctx, int(p["lookback"]), int(p["skip"]))
        if len(mom) < 2:
            return []
        k = min(max(1, round(len(mom) * float(p["top"]))), len(mom) // 2)
        ranked = sorted(mom, key=lambda s: (-mom[s], s))
        w = float(p["gross"]) / (2 * k)
        targets = {**{s: w for s in ranked[:k]}, **{s: -w for s in ranked[-k:]}}
        strength = {s: 0.5 + 0.5 * (1 - i / len(ranked)) for i, s in enumerate(ranked)}
        strength.update({s: 0.5 + 0.5 * (i + 1) / len(ranked) for i, s in enumerate(ranked) if targets.get(s, 0) < 0})
        return _rebalance(ctx, targets, strength)


class TrendLongShort(Strategy):
    """Time-series trend: long every stock whose 12-1 month return is positive and that closes above its 200-day
    average, short every stock with a negative return below it, flat otherwise; each position ``gross`` / N of
    equity for a universe of N stocks, rebalanced every ``every`` trading days. Net long in a rising market, net short
    in a falling one."""

    name = "trend_ls"
    description = ("Long stocks with a positive 12-1 month return above their 200-day average, short those with a "
                   "negative one below it, equal slices of equity, weekly (needs mode long_short or margin to short)")
    default_params: dict[str, Any] = {"lookback": 252, "skip": 21, "every": 5, "gross": 1.0}

    def on_start(self, universe: list[str]) -> None:
        self.day = 0

    def warmup_bars(self) -> int:
        return int(self.params["lookback"]) + 1

    def on_bar(self, ctx: Any) -> list[Signal]:
        p = self.params
        self.day += 1
        if (self.day - 1) % max(1, int(p["every"])):
            return []
        mom = _momentum(ctx, int(p["lookback"]), int(p["skip"]))
        if not mom:
            return []
        w = float(p["gross"]) / len(mom)
        targets: dict[str, float] = {}
        for sym, m in mom.items():
            row = ctx.row(sym)
            ema = row.get("ema200") if row is not None else None
            if ema is None or ema != ema:
                continue
            if m > 0 and row["close"] > ema:
                targets[sym] = w
            elif m < 0 and row["close"] < ema:
                targets[sym] = -w
        strength = {s: min(1.0, 0.5 + abs(m)) for s, m in mom.items()}
        return _rebalance(ctx, targets, strength)


LONG_SHORT_STRATEGIES: dict[str, type[Strategy]] = {"momentum_ls": MomentumLongShort, "trend_ls": TrendLongShort}

__all__ = ["LONG_SHORT_STRATEGIES", "RESEARCH_STRATEGIES", "MomentumLongShort", "SwingBreakout", "SwingNews",
           "SwingPullback", "SwingRsi2", "SwingTrend", "TrendLongShort"]
