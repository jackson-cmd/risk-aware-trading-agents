"""Macro event calendar: FOMC decisions, CPI, jobs report, PPI, GDP, PCE, quad witching.

Bundled dates (``_events_data.py``) cover FOMC 2015-2027 and BLS releases from
2010. ``refresh_online`` merges the latest BLS calendar and Federal Reserve
schedule. Months with no published date get a rule-based estimate marked
``estimated=True`` so the agent never sees an empty calendar.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta

import pandas as pd

from .. import marketcal as mc
from ..config import EventConfig
from ..types import MacroEvent
from ..utils import DiskCache, make_session, tool_user_agent
from . import _events_data as bundled

log = logging.getLogger(__name__)

EVENT_NAMES = {
    "FOMC": "FOMC rate decision",
    "CPI": "CPI inflation report",
    "NFP": "Jobs report (nonfarm payrolls)",
    "PPI": "PPI producer prices",
    "PCE": "PCE inflation (personal income & outlays)",
    "GDP": "GDP release",
    "OPEX": "Quarterly options expiration",
}
RELEASE_TIMES = {"FOMC": "14:00 ET", "CPI": "08:30 ET", "NFP": "08:30 ET", "PPI": "08:30 ET",
                 "PCE": "08:30 ET", "GDP": "08:30 ET", "OPEX": "close"}


def nfp_rule_date(year: int, month: int) -> date:
    """BLS rule: the jobs report for a reference month comes out on the third Friday after
    the week that contains the 12th. ``month`` is the release month; the reference month
    is the previous one. Holidays move the release to the previous business day."""
    ref_year, ref_month = (year, month - 1) if month > 1 else (year - 1, 12)
    twelfth = date(ref_year, ref_month, 12)
    week_end = twelfth + timedelta(days=(5 - twelfth.weekday()) % 7)  # Saturday ending the week
    first_friday = week_end + timedelta(days=6)
    release = first_friday + timedelta(days=14)
    while not mc.is_trading_day(release):
        release -= timedelta(days=1)
    return release


def cpi_estimate(year: int, month: int) -> date:
    """CPI usually comes out between the 10th and the 15th; estimate the Wednesday of that window."""
    d = date(year, month, 10)
    while d.weekday() != 2:
        d += timedelta(days=1)
    while not mc.is_trading_day(d):
        d += timedelta(days=1)
    return d


def ppi_estimate(year: int, month: int) -> date:
    d = cpi_estimate(year, month) + timedelta(days=1)
    while not mc.is_trading_day(d):
        d += timedelta(days=1)
    return d


def quad_witching(year: int, month: int) -> date | None:
    if month not in (3, 6, 9, 12):
        return None
    d = mc._nth_weekday(year, month, 4, 3)  # third Friday
    while not mc.is_trading_day(d):
        d -= timedelta(days=1)
    return d


class EventCalendar:
    def __init__(self, cfg: EventConfig | None = None, cache: DiskCache | None = None,
                 contact_email: str | None = None):
        self.cfg = cfg or EventConfig()
        self.cache = cache
        self.contact_email = contact_email
        self._events: dict[tuple[str, date], MacroEvent] = {}
        self._load_bundled()
        if self.cache is not None:
            self._load_cached_online()

    # ---------------------------------------------------------------- loading
    def _add(self, ev: MacroEvent, replace: bool = False) -> None:
        key = (ev.kind, ev.day)
        if replace or key not in self._events:
            self._events[key] = ev

    def _make(self, kind: str, d: date, estimated: bool = False, note: str = "", sep: bool = False) -> MacroEvent:
        impact = float(self.cfg.weights.get(kind, 0.3))
        name = EVENT_NAMES.get(kind, kind)
        if kind == "FOMC" and sep:
            name += " + projections"
        return MacroEvent(day=d, kind=kind, name=name, impact=impact, time=RELEASE_TIMES.get(kind, ""),
                          estimated=estimated, note=note)

    def _load_bundled(self) -> None:
        for iso, sep, note in bundled.FOMC_DECISIONS:
            self._add(self._make("FOMC", date.fromisoformat(iso), note=note, sep=sep))
        for kind, dates in (("CPI", bundled.CPI_RELEASES), ("NFP", bundled.NFP_RELEASES),
                            ("PPI", bundled.PPI_RELEASES)):
            for iso in dates:
                self._add(self._make(kind, date.fromisoformat(iso)))

    def _load_cached_online(self) -> None:
        data = self.cache.get_json("events", "online", ttl=None) if self.cache else None
        for row in data or []:
            try:
                self._add(self._make(row["kind"], date.fromisoformat(row["day"]), note=row.get("note", ""),
                                     sep=bool(row.get("sep"))), replace=True)
            except (KeyError, ValueError):
                continue

    def _known_months(self, kind: str) -> set[tuple[int, int]]:
        return {(d.year, d.month) for (k, d), ev in self._events.items() if k == kind and not ev.estimated}

    def _fill_estimates(self, start: date, end: date) -> None:
        """Add rule-based dates for months that have no published date yet."""
        y, m = start.year, start.month
        known = {k: self._known_months(k) for k in ("CPI", "NFP", "PPI")}
        while (y, m) <= (end.year, end.month):
            if (y, m) not in known["NFP"]:
                self._add(self._make("NFP", nfp_rule_date(y, m), estimated=True))
            if (y, m) not in known["CPI"]:
                self._add(self._make("CPI", cpi_estimate(y, m), estimated=True))
            if (y, m) not in known["PPI"]:
                self._add(self._make("PPI", ppi_estimate(y, m), estimated=True))
            qw = quad_witching(y, m)
            if qw:
                self._add(self._make("OPEX", qw))
            m += 1
            if m == 13:
                y, m = y + 1, 1

    def refresh_online(self, timeout: float = 20.0) -> int:
        """Fetch the BLS release calendar and the Fed meeting schedule; returns events added."""
        rows: list[dict] = []
        session = make_session(tool_user_agent(self.contact_email))
        try:
            r = session.get("https://www.bls.gov/schedule/news_release/bls.ics", timeout=timeout)
            if r.ok and "BEGIN:VCALENDAR" in r.text:
                rows += parse_bls_ics(r.text)
            else:
                log.info("BLS calendar unavailable (HTTP %s); set data.contact_email to identify the client",
                         r.status_code)
        except Exception as exc:  # network
            log.info("BLS calendar fetch failed: %s", exc)
        try:
            r = session.get("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm", timeout=timeout)
            if r.ok:
                rows += parse_fed_calendar(r.text)
        except Exception as exc:
            log.info("Fed calendar fetch failed: %s", exc)
        try:
            r = session.get("https://www.bea.gov/news/schedule", timeout=timeout)
            if r.ok:
                rows += parse_bea_schedule(r.text)
        except Exception as exc:
            log.info("BEA schedule fetch failed: %s", exc)
        if self.cache is not None and rows:
            self.cache.set_json("events", "online", rows)
        before = len(self._events)
        for row in rows:
            self._add(self._make(row["kind"], date.fromisoformat(row["day"]), note=row.get("note", ""),
                                 sep=bool(row.get("sep"))), replace=True)
        return len(self._events) - before

    # ---------------------------------------------------------------- queries
    def events_between(self, start: date, end: date) -> list[MacroEvent]:
        self._fill_estimates(start - timedelta(days=40), end + timedelta(days=40))
        evs = [ev for (_, d), ev in self._events.items() if start <= d <= end]
        # an estimated date is dropped if a real one exists in the same month
        real = {(ev.kind, ev.day.year, ev.day.month) for ev in evs if not ev.estimated}
        evs = [ev for ev in evs if not (ev.estimated and (ev.kind, ev.day.year, ev.day.month) in real)]
        return sorted(evs, key=lambda e: (e.day, -e.impact))

    def upcoming(self, today: date, days: int = 30) -> list[MacroEvent]:
        return self.events_between(today, today + timedelta(days=days))

    def window(self, kind: str) -> tuple[int, int]:
        w = self.cfg.windows.get(kind, [0, 0])
        return int(w[0]), int(w[1])

    def event_risk(self, d: date) -> tuple[float, list[MacroEvent]]:
        """Highest impact among events whose window covers day d, and those events."""
        if not self.cfg.enabled:
            return 0.0, []
        active = []
        for ev in self.events_between(d - timedelta(days=10), d + timedelta(days=10)):
            lo, hi = self.window(ev.kind)
            offset = mc.trading_days_between(ev.day, d)
            if lo <= offset <= hi:
                active.append(ev)
        risk = max((ev.impact for ev in active), default=0.0)
        return risk, active

    def event_risk_series(self, days: pd.DatetimeIndex) -> pd.DataFrame:
        """Vectorized event risk for a backtest calendar: columns risk, events."""
        if len(days) == 0:
            return pd.DataFrame(columns=["risk", "events"])
        if not self.cfg.enabled:
            return pd.DataFrame({"risk": 0.0, "events": ""}, index=days)
        start, end = days[0].date(), days[-1].date()
        pos = {d.date(): i for i, d in enumerate(days)}
        risk = [0.0] * len(days)
        labels: list[list[str]] = [[] for _ in days]
        for ev in self.events_between(start - timedelta(days=10), end + timedelta(days=10)):
            lo, hi = self.window(ev.kind)
            anchor = ev.day if mc.is_trading_day(ev.day) else mc.next_trading_day(ev.day)
            if anchor in pos:
                center = pos[anchor]
            else:
                continue
            for k in range(lo, hi + 1):
                j = center + k
                if 0 <= j < len(days):
                    if ev.impact > risk[j]:
                        risk[j] = ev.impact
                    labels[j].append(ev.kind)
        return pd.DataFrame({"risk": risk, "events": [",".join(sorted(set(x))) for x in labels]}, index=days)


# -------------------------------------------------------------------- parsers

_ICS_KINDS = {
    "Employment Situation": "NFP",
    "Consumer Price Index": "CPI",
    "Producer Price Index": "PPI",
}


def parse_bls_ics(text: str) -> list[dict]:
    rows = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
        summary = re.search(r"SUMMARY:(.*)", block)
        start = re.search(r"DTSTART[^:]*:(\d{8})", block)
        if not summary or not start:
            continue
        kind = _ICS_KINDS.get(summary.group(1).strip())
        if not kind:
            continue
        s = start.group(1)
        rows.append({"kind": kind, "day": f"{s[:4]}-{s[4:6]}-{s[6:]}"})
    return rows


_MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
     "November", "December"], start=1)}
_MONTHS.update({name[:3]: i for name, i in list(_MONTHS.items())})
_MONTHS["Sept"] = 9


def parse_fed_calendar(html: str) -> list[dict]:
    """Parse federalreserve.gov/monetarypolicy/fomccalendars.htm into decision days."""
    from bs4 import BeautifulSoup

    rows = []
    soup = BeautifulSoup(html, "html.parser")
    for panel in soup.select("div.panel"):
        heading = panel.find("h4")
        m_year = re.search(r"(\d{4}) FOMC Meetings", heading.get_text(" ") if heading else "")
        if not m_year:
            continue
        year = int(m_year.group(1))
        for meeting in panel.select("div.fomc-meeting"):
            month_el = meeting.select_one(".fomc-meeting__month")
            date_el = meeting.select_one(".fomc-meeting__date")
            if not month_el or not date_el:
                continue
            months = [x.strip() for x in month_el.get_text(" ").strip().split("/")]
            days_txt = date_el.get_text(" ").strip()
            low = days_txt.lower()
            if "cancel" in low or "notation" in low:
                continue
            nums = re.findall(r"\d+", days_txt)
            month = _MONTHS.get(months[-1])
            if not nums or not month:
                continue
            try:
                d = date(year, month, int(nums[-1]))
            except ValueError:
                continue
            rows.append({"kind": "FOMC", "day": d.isoformat(), "sep": "*" in days_txt,
                         "note": "unscheduled" if "unscheduled" in low else ""})
    return rows


def parse_bea_schedule(html: str, today: date | None = None) -> list[dict]:
    """Parse bea.gov/news/schedule for GDP and Personal Income and Outlays (PCE) releases."""
    from bs4 import BeautifulSoup

    today = today or date.today()
    rows = []
    soup = BeautifulSoup(html, "html.parser")
    for tr in soup.select("tr"):
        date_el = tr.select_one(".release-date")
        title_el = tr.select_one(".release-title")
        if not date_el or not title_el:
            continue
        title = " ".join(title_el.get_text(" ").split())
        if re.match(r"GDP \((Advance|Second|Third)", title):
            kind = "GDP"
        elif title.startswith("Personal Income and Outlays"):
            kind = "PCE"
        else:
            continue
        m = re.match(r"([A-Za-z]+)\.?\s+(\d{1,2})", date_el.get_text(" ").strip())
        if not m or m.group(1) not in _MONTHS:
            continue
        year = today.year
        try:
            d = date(year, _MONTHS[m.group(1)], int(m.group(2)))
        except ValueError:
            continue
        if d < today - timedelta(days=180):
            d = d.replace(year=year + 1)
        rows.append({"kind": kind, "day": d.isoformat(), "note": title[:80]})
    return rows
