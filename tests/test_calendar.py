from datetime import date

import pandas as pd

from riskaware import marketcal as mc
from riskaware.config import EventConfig
from riskaware.data.events import (
    EventCalendar,
    nfp_rule_date,
    parse_bea_schedule,
    parse_bls_ics,
    parse_fed_calendar,
)


def test_nyse_holidays_2025():
    h = mc.holidays(2025)
    for d in [date(2025, 1, 1), date(2025, 1, 9), date(2025, 1, 20), date(2025, 2, 17), date(2025, 4, 18),
              date(2025, 5, 26), date(2025, 6, 19), date(2025, 7, 4), date(2025, 9, 1), date(2025, 11, 27),
              date(2025, 12, 25)]:
        assert d in h, d
    assert mc.is_trading_day(date(2025, 11, 11))  # Veterans Day: markets open


def test_observed_rules():
    assert date(2027, 6, 18) in mc.holidays(2027)  # Juneteenth on a Saturday -> Friday
    assert mc.is_trading_day(date(2021, 12, 31))  # New Year's Day 2022 on a Saturday is not observed
    assert date(2022, 12, 26) in mc.holidays(2022)  # Christmas on a Sunday -> Monday


def test_trading_day_arithmetic():
    assert mc.add_trading_days(date(2025, 7, 3), 1) == date(2025, 7, 7)
    assert mc.trading_days_between(date(2025, 7, 3), date(2025, 7, 7)) == 1
    assert mc.trading_days_between(date(2025, 7, 7), date(2025, 7, 3)) == -1
    assert mc.early_close(date(2025, 11, 28))


def test_nfp_rule_matches_published_dates():
    assert nfp_rule_date(2025, 8) == date(2025, 8, 1)
    assert nfp_rule_date(2025, 9) == date(2025, 9, 5)
    assert nfp_rule_date(2025, 7) == date(2025, 7, 3)  # July 4 holiday moves it to Thursday


def test_bundled_fomc_and_event_windows():
    cal = EventCalendar(EventConfig())
    fomc = [e for e in cal.events_between(date(2026, 1, 1), date(2026, 12, 31)) if e.kind == "FOMC"]
    assert [e.day for e in fomc][:2] == [date(2026, 1, 28), date(2026, 3, 18)]
    assert len(fomc) == 8
    # FOMC window is [-2, 0] trading days
    assert cal.event_risk(date(2026, 3, 16))[0] == 1.0
    assert cal.event_risk(date(2026, 3, 18))[0] == 1.0
    assert cal.event_risk(date(2026, 3, 19))[0] < 1.0
    # CPI on 2026-03-11 (published): uncertain the day before, known on the day
    cpi = [e for e in cal.events_between(date(2026, 3, 1), date(2026, 3, 31)) if e.kind == "CPI"]
    assert cpi and not cpi[0].estimated


def test_event_risk_series_matches_point_queries():
    cal = EventCalendar(EventConfig())
    days = pd.DatetimeIndex([pd.Timestamp(d) for d in mc.trading_days(date(2025, 1, 1), date(2025, 3, 31))])
    series = cal.event_risk_series(days)
    for d in days[::7]:
        assert abs(series.loc[d, "risk"] - cal.event_risk(d.date())[0]) < 1e-9


def test_estimates_fill_unknown_months():
    cal = EventCalendar(EventConfig())
    evs = cal.events_between(date(2030, 1, 1), date(2030, 2, 28))
    kinds = {e.kind for e in evs}
    assert {"CPI", "NFP"} <= kinds
    assert all(e.estimated for e in evs if e.kind in ("CPI", "NFP"))


def test_parsers():
    ics = ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:Employment Situation\nDTSTART;TZID=US-Eastern:20261204T083000\n"
           "END:VEVENT\nBEGIN:VEVENT\nSUMMARY:Consumer Price Index\nDTSTART;TZID=US-Eastern:20261210T083000\n"
           "END:VEVENT\nEND:VCALENDAR")
    assert parse_bls_ics(ics) == [{"kind": "NFP", "day": "2026-12-04"}, {"kind": "CPI", "day": "2026-12-10"}]
    fed = ('<div class="panel"><h4><a>2027 FOMC Meetings</a></h4>'
           '<div class="row fomc-meeting"><div class="fomc-meeting__month"><strong>Jan/Feb</strong></div>'
           '<div class="fomc-meeting__date">31-1</div></div>'
           '<div class="row fomc-meeting"><div class="fomc-meeting__month"><strong>March</strong></div>'
           '<div class="fomc-meeting__date">16-17*</div></div></div>')
    rows = parse_fed_calendar(fed)
    assert rows[0]["day"] == "2027-02-01" and rows[1] == {"kind": "FOMC", "day": "2027-03-17", "sep": True, "note": ""}
    bea = ('<table><tr><td><div class="release-date">November 25</div></td>'
           '<td class="release-title">GDP (Second Estimate), 3rd Quarter 2026</td></tr>'
           '<tr><td><div class="release-date">November 25</div></td>'
           '<td class="release-title">Personal Income and Outlays, October 2026</td></tr>'
           '<tr><td><div class="release-date">December 2</div></td>'
           '<td class="release-title">GDP by County, 2025</td></tr></table>')
    out = parse_bea_schedule(bea, today=date(2026, 11, 11))
    assert {r["kind"] for r in out} == {"GDP", "PCE"} and len(out) == 2
