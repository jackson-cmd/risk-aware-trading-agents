"""Fundamental agent: is this dip a bargain or a broken company?

Scores 0..100 from growth, profitability, free cash flow, leverage, recent EPS
surprises and the gross-margin trend. ``QUALITY`` dips are buyable,
``BROKEN`` ones are falling knives. ETFs pass by definition.
"""

from __future__ import annotations

from ..data.fundamentals import FundamentalSnapshot
from ..types import AgentReport


class FundamentalAgent:
    name = "fundamental"

    def evaluate(self, snap: FundamentalSnapshot | None, min_quality: float = 60.0) -> AgentReport:
        if snap is None:
            return AgentReport(self.name, 50.0, "UNKNOWN", warnings=["no fundamental data"])
        if snap.is_etf:
            return AgentReport(self.name, 70.0, "ETF", reasons=["ETF: diversified, no single-company risk"])
        reasons: list[str] = []
        warnings: list[str] = []
        missing = 0
        score = 0.0

        g = snap.revenue_yoy
        if g is None:
            missing += 1
            score += 12
        else:
            pts = 30 if g > 0.15 else 22 if g > 0.05 else 15 if g > 0 else 7 if g > -0.05 else 0
            score += pts
            (reasons if g > 0 else warnings).append(f"revenue {g:+.0%} year over year")

        m = snap.net_margin
        if m is None:
            missing += 1
            score += 8
        else:
            score += 20 if m > 0.15 else 14 if m > 0.05 else 8 if m > 0 else 0
            (reasons if m > 0 else warnings).append(f"net margin {m:.0%}")

        fcf = snap.fcf_ttm
        if fcf is None:
            missing += 1
            score += 7
        elif fcf > 0:
            score += 15
            reasons.append("positive free cash flow (TTM)")
        else:
            warnings.append("negative free cash flow (TTM)")

        de = snap.debt_to_equity
        if de is None:
            missing += 1
            score += 5
        else:
            score += 10 if de < 0.5 else 7 if de < 1.5 else 3 if de < 3 else 0
            if de >= 3:
                warnings.append(f"high leverage (debt/equity {de:.1f})")

        if not snap.eps_surprises:
            missing += 1
            score += 7
        else:
            beats = sum(1 for s in snap.eps_surprises[:4] if s >= 0)
            score += {4: 15, 3: 11, 2: 6, 1: 3}.get(beats, 0)
            last = snap.eps_surprises[0]
            (reasons if last >= 0 else warnings).append(
                f"EPS beat {beats} of last {min(4, len(snap.eps_surprises))}, latest surprise {last:+.1f}%")

        gm = snap.gross_margin_change
        if gm is None:
            missing += 1
            score += 5
        else:
            score += 10 if gm > 0.01 else 6 if gm > -0.01 else 0
            if gm <= -0.01:
                warnings.append(f"gross margin down {abs(gm) * 100:.1f} pts year over year")

        if snap.revenue_qoq_trend is not None and snap.revenue_qoq_trend < -0.10:
            score -= 5
            warnings.append("revenue growth decelerating sharply")

        if missing >= 4:
            return AgentReport(self.name, 50.0, "UNKNOWN", reasons, warnings + ["too little fundamental data"],
                               data={"missing": missing})
        score = max(0.0, min(100.0, score))
        verdict = "QUALITY" if score >= min_quality else "BROKEN" if score < 35 else "NEUTRAL"
        return AgentReport(self.name, score, verdict, reasons, warnings,
                           data={"missing": missing, "next_earnings": snap.next_earnings})
