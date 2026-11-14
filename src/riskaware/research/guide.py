"""What an AI assistant (or a person) needs to run experiments on the frozen market: the research_guide tool."""

from __future__ import annotations

from typing import Any

from .data import available_periods, manifest
from .experiment import PROTOCOL
from .periods import DEFAULT_PERIOD, get_period


def guide() -> dict[str, Any]:
    from ..lab import NEWS_EXAMPLE, TEMPLATE, _indicator_columns
    from .strategies import RESEARCH_STRATEGIES

    m = manifest()
    groups = m["symbols"]
    periods = {}
    for name in available_periods():
        p, pm = get_period(name), manifest(name)
        periods[name] = {**p.describe(), "data_from": pm["period"]["first"],
                         "not_trading_yet": pm.get("not_trading_yet", [])}
    return {
        "what": (
            "Frozen copies of the US stock market in three different years, for experiments that others can repeat: "
            "2026 to date (the default), 2022 (the rate-hike bear market) and 2020 (the COVID crash). Each period has "
            "daily bars from two years before it (to warm up slow indicators such as EMA200), the VIX term "
            "structure, and that year's news (Benzinga headlines, titles only; 2026 also SEC 8-K filings). It ships "
            "with the package, so an experiment never touches the network and gives the same numbers on any "
            "machine; each record carries the SHA-256 of the data. Pass period='2022' (or '2020') to "
            "run_experiment and trading_session_start; market_day picks the period from the day."),
        "periods": periods,
        "default_period": DEFAULT_PERIOD,
        "data": {
            "stocks": groups["stocks"], "etfs": groups["etfs"], "volatility": groups["volatility"],
            "cross_asset": {k: v for k, v in m.get("cross_asset", {}).items()},
            "prices": m["prices"], "version": m["version"],
            "universes": "megacap (56 large caps), tech (18), defensive (20), core (SPY, QQQ and 7 large caps), "
                         "sectors (11 sector ETFs), etf; or any list of the bundled symbols",
        },
        "protocol": PROTOCOL,
        "tools": {
            "market_day(day, symbols)": "everything known at that day's close: regime, VIX term structure, fear & "
                                        "greed, breadth, scheduled macro events; per stock prices, indicators, "
                                        "technical setups and the news published before the close",
            "run_experiment(strategy | code, ...)": "backtest with orders at the next open, a look-ahead check, SPY "
                                                    "and equal-weight benchmarks, statistics, and a saved record",
            "trading_session_start / trading_session_step": "trade the period yourself, step by step: you get an "
                                                            "observation, send orders, get the next one",
            "list_experiments / experiment_report": "the saved records",
        },
        "write_a_strategy": {
            "how": ("One Strategy subclass with on_bar(self, ctx) returning Signals, as in strategy_guide: the same "
                    "rules apply (no dates or tickers in the code, no files or network; everything it may know is in "
                    "ctx). Pass it as code to run_experiment."),
            "ctx": "ctx.day, ctx.universe, ctx.row(symbol) (today's indicators), ctx.history(symbol, n), "
                   "ctx.position(symbol), ctx.portfolio, ctx.market (regime, vix, fear_greed, breadth, events), "
                   "ctx.news(symbol) (score, confirmed, unconfirmed, red_flags_confirmed, red_flags_unconfirmed, "
                   "severe_confirmed, clusters)",
            "indicators": _indicator_columns(),
            "signals": ("Signal(symbol, Action.BUY, strength, reason, target_weight=0.1) opens a position of 10% "
                        "of equity (default 1 / max_positions); Signal(symbol, Action.SELL, strength, reason, "
                        "fraction=1.0) sells it. With plain execution, strength only orders the buys when there are "
                        "more candidates than free slots."),
            "template": TEMPLATE,
            "news_example": NEWS_EXAMPLE,
        },
        "execution": {
            "plain (default)": "exactly what the strategy says: equal-size positions, at most max_positions, long "
                               "only, market orders at the next open with 5 bps slippage, sells before buys",
            "risk_aware": "the package's risk manager on top: a cash floor, regime limits, tranches, position and "
                          "sector caps (part of the risk-aware strategy; use it to test that overlay)",
            "costs": "costs='fixed' (default) pays 5 bps on every fill at the open. costs='realistic' pays 5 bps plus "
                     "10% of the overnight gap plus square-root market impact (daily volatility x sqrt(shares / "
                     "20-day average volume)), and fills each order for at most 1% of its 20-day average volume; "
                     "the volatility and volume are those of the days before the fill",
        },
        "baselines": {name: cls.description for name, cls in RESEARCH_STRATEGIES.items()},
        "baseline_params": {name: cls.default_params for name, cls in RESEARCH_STRATEGIES.items()},
        "record": ["manifest.json: data hashes, package version and commit, every setting, the strategy's source "
                   "and hash, the look-ahead verdict", "metrics.json", "equity.csv (strategy, SPY, equal weight, "
                   "exposure, regime by day)", "trades.csv", "decisions.csv (every signal, order and refusal by day)",
                   "report.md (tables to paste)", "equity.png", "strategy.py"],
        "look_ahead": [
            "Prices: each day sees its own close and earlier bars; orders fill at the next open.",
            "Indicators only look back; the tests check that changing everything after a day leaves that day's "
            "values unchanged.",
            "News: an item counts from the first close after its timestamp (16:00 New York, 13:00 on early-close "
            "days); items without a time count at the end of their day; future-dated items are dropped.",
            "The leak check re-runs every experiment with a random future after three cut days and compares the "
            "decisions before each cut. For an LLM or trading session it replays the replies and compares the "
            "observations instead, so the model is not asked twice.",
            "Code may not name dates or tickers, so a model cannot pick the stocks or days it remembers.",
            "A model trained on text from a period may remember how it went: 2020 and 2022 are in the training data "
            "of every current model, 2026 only of the newest ones. Trading sessions have a blind mode (aliases for "
            "tickers and companies, day numbers instead of dates, prices rebased to 100); compare blind and named "
            "runs, and periods before and after the model's training cutoff.",
        ],
        "for_papers": [
            "Decide the hypothesis, period and settings before looking at results; tune on the first half of a "
            "period and test once on the second (split='2026-07-01', '2022-07-01' or '2020-07-01').",
            "Run every strategy on all three periods: a rising market (2026), a bear market (2022) and a crash and "
            "rebound (2020). A result that holds in only one of them is a statement about that year.",
            "Report the intervals: one year is short, and most differences between strategies are within them.",
            "Every variant you try is a test; say how many you ran.",
            "The stock lists are today's large caps, chosen with hindsight (survivorship bias): compare strategies "
            "with each other and with the equal-weight universe, not only with SPY.",
            "Prices are adjusted for splits and dividends as of the data's last day; volumes are as traded.",
            "Cite the data version and the SHA-256 from manifest.json.",
        ],
    }
