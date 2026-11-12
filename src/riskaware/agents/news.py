"""News agent: turns a verified news report into a bounded score and red-flag verdicts."""

from __future__ import annotations

from ..news import lexicon as lx
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
        backed = getattr(report, "red_flags_backed", None) or report.red_flags_confirmed
        if report.severe_confirmed:
            verdict = "SEVERE_RED_FLAG"
            warnings.append("severe red flag backed by the company's own 8-K or release: " + ", ".join(backed))
        elif report.red_flags_confirmed:
            verdict = "RED_FLAG"
            warnings.append("confirmed red flag: " + ", ".join(report.red_flags_confirmed))
            severe = [f for f in report.red_flags_confirmed if f in lx.SEVERE_RED_FLAGS]
            if severe:
                warnings.append("severe red flag (" + ", ".join(severe) + ") not backed by the company's own 8-K or "
                                "release: new buys blocked, no forced sale")
        if report.red_flags_unconfirmed:
            warnings.append("unconfirmed red flag (needs a second source): " + ", ".join(report.red_flags_unconfirmed))
        denied = getattr(report, "red_flags_denied", None)
        if denied:
            reasons.append("denied, no longer counted: " + ", ".join(denied))
        for c in report.top(3):
            if c.confirmed:
                reasons.append(f"[{c.status.value.lower()}] {c.headline[:90]} ({c.sentiment:+.2f})")
        return AgentReport(self.name, report.score, verdict, reasons, warnings,
                           data={"confirmed_flags": report.red_flags_confirmed,
                                 "unconfirmed_flags": report.red_flags_unconfirmed,
                                 "denied_flags": list(denied or []),
                                 "severe": report.severe_confirmed})
