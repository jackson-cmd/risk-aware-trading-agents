"""News agent: turns a verified news report into a bounded score and red-flag verdicts."""

from __future__ import annotations

from ..news.verify import NewsReport
from ..types import AgentReport


class NewsAgent:
    name = "news"

    def evaluate(self, report: NewsReport | None) -> AgentReport:
        if report is None or not report.has_news:
            return AgentReport(self.name, 0.0, "NO_NEWS", warnings=["no recent news collected"])
        reasons = [f"{report.confirmed} confirmed / {report.unconfirmed} single-source / {report.opinion} opinion stories"]
        warnings = []
        verdict = "POSITIVE" if report.score > 0.15 else "NEGATIVE" if report.score < -0.15 else "MIXED"
        if report.severe_confirmed:
            verdict = "SEVERE_RED_FLAG"
            warnings.append("confirmed severe red flag: " + ", ".join(report.red_flags_confirmed))
        elif report.red_flags_confirmed:
            verdict = "RED_FLAG"
            warnings.append("confirmed red flag: " + ", ".join(report.red_flags_confirmed))
        if report.red_flags_unconfirmed:
            warnings.append("unconfirmed red flag (needs a second source): " + ", ".join(report.red_flags_unconfirmed))
        for c in report.top(3):
            if c.confirmed:
                reasons.append(f"[{c.status.value.lower()}] {c.headline[:90]} ({c.sentiment:+.2f})")
        return AgentReport(self.name, report.score, verdict, reasons, warnings,
                           data={"confirmed_flags": report.red_flags_confirmed,
                                 "unconfirmed_flags": report.red_flags_unconfirmed,
                                 "severe": report.severe_confirmed})
