"""Research kit: a frozen 2026 market for experiments that others can repeat.

* ``data``: daily bars from January 2024 to November 2026 (64 stocks, 25 ETFs, the VIX term structure, gold, oil,
  the dollar and bitcoin ETFs) shipped with the package, with a manifest of SHA-256 hashes;
* ``day``: what the agents knew at any day's close: the market regime and VIX, indicators and technical setups,
  and the news published before the close;
* ``experiment``: run a strategy on that market with orders at the next open, check it for look-ahead, compare it
  with SPY and the equal-weight universe, and save a record (manifest, metrics, equity, trades, decisions, report);
* ``strategies``: five common swing strategies with a news switch;
* ``agent``: let an LLM trade the period day by day, from Python or over MCP, optionally blind (no tickers, no
  dates) so that it cannot recall what happened.

Docs: docs/research.md. CLI: ``riskaware research --help``. MCP: ``research_guide`` first.
"""

from __future__ import annotations

from typing import Any

__all__ = ["Experiment", "FrozenMarket", "MarketView", "run_experiment", "bundled_market", "RESEARCH_STRATEGIES"]


def __getattr__(name: str) -> Any:  # imported on first use: the strategy loader imports this package early
    if name in ("Experiment", "run_experiment", "ExperimentResult", "list_runs"):
        from . import experiment

        return getattr(experiment, name)
    if name in ("FrozenMarket", "bundled_market"):
        from . import data

        return getattr(data, name)
    if name == "MarketView":
        from .day import MarketView

        return MarketView
    if name == "RESEARCH_STRATEGIES":
        from .strategies import RESEARCH_STRATEGIES

        return RESEARCH_STRATEGIES
    raise AttributeError(name)
