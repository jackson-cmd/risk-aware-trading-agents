"""Example: trade the drift after an earnings report, using news from the local archive.

Build the archive once (SEC 8-K filings; SEC asks for a contact email), then backtest with --news:

    riskaware news-history build --preset core --start 2018-01-01 --contact-email you@example.com
    riskaware backtest --strategy examples/strategies/news_earnings_drift.py --preset core --news \\
        --compare buy_and_hold

Rule: once a company has filed its earnings 8-K (Item 2.02), buy if the stock rose at least
3% over the two sessions around the report and still closes above EMA50. Sell after 20
trading days, at +15%, or on a close below EMA50. A confirmed red flag (bankruptcy,
delisting, restated financials) blocks the buy. Without an archive, ctx.news() returns
None and the strategy never buys.
"""

from riskaware.strategy import Action, Signal, Strategy


class NewsEarningsDrift(Strategy):
    name = "news_earnings_drift"
    description = "Buy the drift after an 8-K earnings filing; exit after 20 days, at +15% or below EMA50."
    default_params = {"min_move": 0.03, "hold_days": 20, "take_profit": 0.15}

    def on_bar(self, ctx):
        p = self.params
        signals = []
        for sym in ctx.universe:
            row = ctx.row(sym)
            if row is None or row.get("ema50") != row.get("ema50"):
                continue
            pos = ctx.position(sym)
            if pos is not None:
                if pos.days_held >= p["hold_days"] or pos.gain >= p["take_profit"] or row["close"] < row["ema50"]:
                    why = ("held 20 days" if pos.days_held >= p["hold_days"] else
                           "take profit" if pos.gain >= p["take_profit"] else "closed below EMA50")
                    signals.append(Signal(sym, Action.SELL, 0.9, why, ["earnings_drift_exit"], fraction=1.0))
                continue
            report = ctx.news(sym)  # only items published before today's close
            if report is None or report.red_flags_confirmed:
                continue
            if not any(c.confirmed and "earnings" in c.event_types for c in report.clusters):
                continue
            hist = ctx.history(sym, 3)
            if hist is None or len(hist) < 3:
                continue
            move = float(hist["close"].iloc[-1] / hist["close"].iloc[-3] - 1.0)
            if move >= p["min_move"] and row["close"] > row["ema50"]:
                signals.append(Signal(sym, Action.BUY, min(0.9, 0.6 + 3 * move),
                                      f"earnings 8-K, {move:+.1%} over two sessions", ["earnings_drift"]))
        return signals
