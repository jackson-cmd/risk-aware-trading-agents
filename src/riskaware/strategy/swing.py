"""Five common swing-trading strategies, written the way textbooks and papers define them, with a news switch.

Each one decides after the close on daily bars and holds for days to weeks:

* ``swing_trend``: trend following. Buy when EMA20 crosses above EMA50 with the close above EMA200; sell when EMA20
  falls back below EMA50 or the close drops below EMA200.
* ``swing_rsi2``: Connors' RSI(2) mean reversion. Buy when RSI(2) is under 10 and the close is above its 200-day
  average; sell when the close rises above its 5-day average, or after 10 days.
* ``swing_breakout``: Donchian breakout. Buy a close above the highest high of the previous 20 days (above EMA200);
  sell on a close below the lowest low of the previous 10 days, or 2 ATR below the entry.
* ``swing_pullback``: buy the dip in an uptrend. EMA50 above EMA200, the close above EMA200 and near EMA50, RSI(14)
  under 40; take profit at +8%, stop at -5%, or sell after 15 days or below EMA200.
* ``swing_news``: post-news drift. Buy a stock whose news score is at least 0.10 with a positive story first seen in
  the last 24 hours and no red flag, in an uptrend (above EMA50); sell after 10 days, at -6%, or on a red flag.
  With ``news=False`` it becomes its price-only twin: the same trade after a day that beat SPY by 2% on 1.3 times
  the usual volume, so a comparison shows what the headlines add beyond the price move they come with.

``news=True`` (the default when the experiment replays news) adds the same filter to the first four: no new
position while the stock has a red flag or a news score below -0.05 (a negative news flow over the last 72 hours),
and an exit when a confirmed red flag appears. The thresholds sit at the tails of the 2026 news-score distribution
(about the top and bottom tenth); they were set from that distribution, not tuned on returns.

Sizing, the number of positions and costs belong to the experiment (``Experiment.max_positions``, plain execution
by default), not to the strategies.
"""

from __future__ import annotations

import math
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ..types import Action, Signal
from .base import Strategy

NY = ZoneInfo("America/New_York")


def _nan(*vals: float) -> bool:
    return any(v is None or (isinstance(v, float) and math.isnan(v)) for v in vals)


class _Swing(Strategy):
    """Shared news filter and exits."""

    default_params: dict[str, Any] = {"news": True, "news_block_below": -0.05}

    def _news(self, ctx: Any, sym: str) -> Any:
        return ctx.news(sym) if self.params.get("news") else None

    def news_blocks_entry(self, ctx: Any, sym: str) -> str | None:
        rep = self._news(ctx, sym)
        if rep is None:
            return None
        if rep.red_flags_confirmed or rep.red_flags_unconfirmed:
            flags = [*rep.red_flags_confirmed, *rep.red_flags_unconfirmed]
            return f"red flag in the news ({', '.join(sorted(set(flags))[:3])})"
        if rep.score < self.params["news_block_below"]:
            return f"negative news flow (score {rep.score:+.2f})"
        return None

    def news_exit(self, ctx: Any, sym: str) -> str | None:
        rep = self._news(ctx, sym)
        if rep is not None and (rep.severe_confirmed or rep.red_flags_confirmed):
            return f"confirmed red flag ({', '.join(rep.red_flags_confirmed[:3]) or 'severe'})"
        return None

    def entries(self, ctx: Any, candidates: list[tuple[str, float, str]]) -> list[Signal]:
        out = []
        for sym, strength, why in candidates:
            block = self.news_blocks_entry(ctx, sym)
            if block is None:
                out.append(Signal(sym, Action.BUY, max(0.05, min(1.0, strength)), why))
        return out

    @staticmethod
    def sell(sym: str, why: str, emergency: bool = False) -> Signal:
        return Signal(sym, Action.SELL, 0.9, why, fraction=1.0, emergency=emergency)


class SwingTrend(_Swing):
    name = "swing_trend"
    description = "Trend following: buy when EMA20 crosses above EMA50 above EMA200; sell when it crosses back."
    default_params = {**_Swing.default_params}

    def on_bar(self, ctx):
        out, cands = [], []
        for sym in ctx.universe:
            row = ctx.row(sym)
            if row is None or _nan(row.get("ema20"), row.get("ema50"), row.get("ema200")):
                continue
            pos = ctx.position(sym)
            if pos is not None:
                why = self.news_exit(ctx, sym)
                if why:
                    out.append(self.sell(sym, why, emergency=True))
                elif row["ema20"] < row["ema50"]:
                    out.append(self.sell(sym, "EMA20 crossed below EMA50"))
                elif row["close"] < row["ema200"]:
                    out.append(self.sell(sym, "close below EMA200"))
                continue
            if row["ema20"] <= row["ema50"] or row["close"] <= row["ema200"]:
                continue
            h = ctx.history(sym, 2)
            if h is None or len(h) < 2 or _nan(h["ema20"].iloc[0], h["ema50"].iloc[0]):
                continue
            if h["ema20"].iloc[0] <= h["ema50"].iloc[0]:
                adx = row.get("adx14")
                cands.append((sym, 0.5 + (0.0 if _nan(adx) else min(adx, 50) / 100), "EMA20 crossed above EMA50"))
        return out + self.entries(ctx, cands)


class SwingRsi2(_Swing):
    name = "swing_rsi2"
    description = "Connors RSI(2): buy RSI(2) < 10 above the 200-day average; sell above the 5-day average or after 10 days."
    default_params = {**_Swing.default_params, "entry": 10.0, "max_days": 10}

    def on_bar(self, ctx):
        out, cands = [], []
        p = self.params
        for sym in ctx.universe:
            row = ctx.row(sym)
            if row is None or _nan(row.get("rsi2")):
                continue
            h = ctx.history(sym, 200)
            if h is None or len(h) < 200:
                continue
            close = h["close"]
            sma200, sma5 = float(close.mean()), float(close.iloc[-5:].mean())
            pos = ctx.position(sym)
            if pos is not None:
                why = self.news_exit(ctx, sym)
                if why:
                    out.append(self.sell(sym, why, emergency=True))
                elif row["close"] > sma5:
                    out.append(self.sell(sym, "close above the 5-day average"))
                elif pos.days_held >= p["max_days"]:
                    out.append(self.sell(sym, f"held {p['max_days']} days"))
                continue
            if row["close"] > sma200 and row["rsi2"] < p["entry"]:
                cands.append((sym, 1.0 - row["rsi2"] / 100, f"RSI(2) {row['rsi2']:.1f} above the 200-day average"))
        return out + self.entries(ctx, cands)


class SwingBreakout(_Swing):
    name = "swing_breakout"
    description = "Donchian breakout: buy a close above the prior 20-day high (above EMA200); sell below the 10-day low or 2 ATR under the entry."
    default_params = {**_Swing.default_params, "entry_days": 20, "exit_days": 10, "atr_stop": 2.0}

    def on_bar(self, ctx):
        out, cands = [], []
        p = self.params
        n_in, n_out = int(p["entry_days"]), int(p["exit_days"])
        for sym in ctx.universe:
            row = ctx.row(sym)
            if row is None or _nan(row.get("ema200"), row.get("atr14")):
                continue
            h = ctx.history(sym, n_in + 1)
            if h is None or len(h) < n_in + 1:
                continue
            prior = h.iloc[:-1]  # the previous days, not today
            pos = ctx.position(sym)
            if pos is not None:
                why = self.news_exit(ctx, sym)
                low_n = float(prior["low"].iloc[-n_out:].min())
                if why:
                    out.append(self.sell(sym, why, emergency=True))
                elif row["close"] < low_n:
                    out.append(self.sell(sym, f"close below the {n_out}-day low"))
                elif row["close"] < pos.avg_cost - p["atr_stop"] * row["atr14"]:
                    out.append(self.sell(sym, f"{p['atr_stop']:g} ATR stop"))
                continue
            high_n = float(prior["high"].max())
            if row["close"] > high_n and row["close"] > row["ema200"]:
                rv = row.get("rel_vol")
                cands.append((sym, 0.5 + (0.0 if _nan(rv) else min(rv, 3.0) / 6), f"close above the {n_in}-day high"))
        return out + self.entries(ctx, cands)


class SwingPullback(_Swing):
    name = "swing_pullback"
    description = "Buy the dip in an uptrend: RSI(14) < 40 near EMA50 while EMA50 > EMA200; +8% target, -5% stop, 15 days."
    default_params = {**_Swing.default_params, "rsi_entry": 40.0, "near_ema50": 0.03, "take_profit": 0.08,
                      "stop_loss": 0.05, "max_days": 15}

    def on_bar(self, ctx):
        out, cands = [], []
        p = self.params
        for sym in ctx.universe:
            row = ctx.row(sym)
            if row is None or _nan(row.get("ema50"), row.get("ema200"), row.get("rsi14")):
                continue
            pos = ctx.position(sym)
            if pos is not None:
                why = self.news_exit(ctx, sym)
                if why:
                    out.append(self.sell(sym, why, emergency=True))
                elif pos.gain >= p["take_profit"]:
                    out.append(self.sell(sym, f"+{p['take_profit']:.0%} target"))
                elif pos.gain <= -p["stop_loss"]:
                    out.append(self.sell(sym, f"-{p['stop_loss']:.0%} stop"))
                elif pos.days_held >= p["max_days"]:
                    out.append(self.sell(sym, f"held {p['max_days']} days"))
                elif row["close"] < row["ema200"]:
                    out.append(self.sell(sym, "close below EMA200"))
                continue
            up = row["ema50"] > row["ema200"] and row["close"] > row["ema200"]
            near = row["close"] >= row["ema50"] * (1 - p["near_ema50"])
            if up and near and row["rsi14"] < p["rsi_entry"]:
                cands.append((sym, (p["rsi_entry"] - row["rsi14"]) / p["rsi_entry"] + 0.3,
                              f"RSI(14) {row['rsi14']:.0f} near EMA50 in an uptrend"))
        return out + self.entries(ctx, cands)


class SwingNews(_Swing):
    name = "swing_news"
    description = ("Post-news drift: buy fresh positive news (score >= 0.10, no red flag) above EMA50; sell after "
                   "10 days, at -6% or on a red flag. news=False: the same trade after a +2% day versus SPY on heavy "
                   "volume.")
    default_params = {**_Swing.default_params, "min_score": 0.10, "fresh_hours": 24, "max_days": 10,
                      "stop_loss": 0.06, "shock": 0.02, "shock_volume": 1.3}

    def on_start(self, universe):
        self._spy_prev = None

    def on_bar(self, ctx):
        out, cands = [], []
        p = self.params
        spy = ctx.market.spy_close
        spy_ret = (spy / self._spy_prev - 1) if (spy and getattr(self, "_spy_prev", None)) else 0.0
        self._spy_prev = spy
        close_time = datetime.combine(ctx.day, time(16, 0), NY)
        fresh_after = close_time - timedelta(hours=float(p["fresh_hours"]))
        for sym in ctx.universe:
            row = ctx.row(sym)
            if row is None or _nan(row.get("ema50")):
                continue
            pos = ctx.position(sym)
            rep = self._news(ctx, sym)
            if pos is not None:
                if rep is not None and (rep.red_flags_confirmed or rep.red_flags_unconfirmed):
                    out.append(self.sell(sym, "red flag in the news", emergency=bool(rep.red_flags_confirmed)))
                elif pos.gain <= -p["stop_loss"]:
                    out.append(self.sell(sym, f"-{p['stop_loss']:.0%} stop"))
                elif pos.days_held >= p["max_days"]:
                    out.append(self.sell(sym, f"held {p['max_days']} days"))
                continue
            if row["close"] <= row["ema50"]:
                continue
            if p["news"]:
                if rep is None or rep.score < p["min_score"] or rep.red_flags_confirmed or rep.red_flags_unconfirmed:
                    continue
                fresh = [c for c in rep.clusters if c.first_seen is not None and c.first_seen >= fresh_after
                         and c.sentiment > 0 and not c.live_flags]
                if fresh:
                    best = max(fresh, key=lambda c: c.sentiment)
                    cands.append((sym, 0.4 + min(rep.score, 0.6), f"fresh positive news: {best.headline[:80]}"))
            else:
                r1, rv = row.get("ret1"), row.get("rel_vol")
                if not _nan(r1, rv) and r1 - spy_ret >= p["shock"] and rv >= p["shock_volume"]:
                    cands.append((sym, 0.4 + min(r1 - spy_ret, 0.1) * 5,
                                  f"beat SPY by {(r1 - spy_ret) * 100:.1f}% on {rv:.1f}x volume"))
        return out + [Signal(s, Action.BUY, max(0.05, min(1.0, k)), why) for s, k, why in cands]


SWING_STRATEGIES: dict[str, type[Strategy]] = {
    cls.name: cls for cls in (SwingTrend, SwingRsi2, SwingBreakout, SwingPullback, SwingNews)
}
