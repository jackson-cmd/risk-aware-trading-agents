"""The five swing strategies the research kit uses as baselines (defined in ``riskaware.strategy.swing``)."""

from ..strategy.swing import (
    SWING_STRATEGIES,
    SwingBreakout,
    SwingNews,
    SwingPullback,
    SwingRsi2,
    SwingTrend,
)

RESEARCH_STRATEGIES = SWING_STRATEGIES

__all__ = ["RESEARCH_STRATEGIES", "SwingBreakout", "SwingNews", "SwingPullback", "SwingRsi2", "SwingTrend"]
