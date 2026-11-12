"""NYSE trading calendar: regular holidays, special closures and early closes.

Rules follow NYSE practice: Saturday holidays are observed on Friday, Sunday
holidays on Monday, except New Year's Day on a Saturday, which is not observed.
Juneteenth is a holiday from 2022 on.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

# One-off closures (national days of mourning, weather, 9/11).
SPECIAL_CLOSURES: set[date] = {
    date(2001, 9, 11), date(2001, 9, 12), date(2001, 9, 13), date(2001, 9, 14),
    date(2004, 6, 11),  # President Reagan
    date(2007, 1, 2),  # President Ford
    date(2012, 10, 29), date(2012, 10, 30),  # Hurricane Sandy
    date(2018, 12, 5),  # President G.H.W. Bush
    date(2025, 1, 9),  # President Carter
}


def _easter(year: int) -> date:
    """Gregorian Easter Sunday (anonymous algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-th weekday (0=Monday) of a month; n=-1 means the last one."""
    if n > 0:
        d = date(year, month, 1)
        offset = (weekday - d.weekday()) % 7
        return d + timedelta(days=offset + 7 * (n - 1))
    d = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    offset = (d.weekday() - weekday) % 7
    return d - timedelta(days=offset)


def _observed(d: date) -> date:
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=128)
def holidays(year: int) -> frozenset[date]:
    out: set[date] = set()
    ny = date(year, 1, 1)
    if ny.weekday() == 6:
        out.add(ny + timedelta(days=1))
    elif ny.weekday() < 5:
        out.add(ny)
    if year >= 1998:
        out.add(_nth_weekday(year, 1, 0, 3))  # Martin Luther King Jr. Day
    out.add(_nth_weekday(year, 2, 0, 3))  # Washington's Birthday
    out.add(_easter(year) - timedelta(days=2))  # Good Friday
    out.add(_nth_weekday(year, 5, 0, -1))  # Memorial Day
    if year >= 2022:
        out.add(_observed(date(year, 6, 19)))  # Juneteenth
    out.add(_observed(date(year, 7, 4)))  # Independence Day
    out.add(_nth_weekday(year, 9, 0, 1))  # Labor Day
    out.add(_nth_weekday(year, 11, 3, 4))  # Thanksgiving
    out.add(_observed(date(year, 12, 25)))  # Christmas
    out |= {d for d in SPECIAL_CLOSURES if d.year == year}
    return frozenset(out)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in holidays(d.year)


def early_close(d: date) -> bool:
    """1:00 pm closes: July 3, the day after Thanksgiving, Christmas Eve."""
    if not is_trading_day(d):
        return False
    if d.month == 7 and d.day == 3:
        return True
    if d.month == 11 and d == _nth_weekday(d.year, 11, 3, 4) + timedelta(days=1):
        return True
    return d.month == 12 and d.day == 24


def next_trading_day(d: date, include_today: bool = False) -> date:
    cur = d if include_today else d + timedelta(days=1)
    while not is_trading_day(cur):
        cur += timedelta(days=1)
    return cur


def prev_trading_day(d: date, include_today: bool = False) -> date:
    cur = d if include_today else d - timedelta(days=1)
    while not is_trading_day(cur):
        cur -= timedelta(days=1)
    return cur


def add_trading_days(d: date, n: int) -> date:
    """Move n trading days from d (n may be negative). d itself need not be a trading day."""
    cur = d
    step = 1 if n >= 0 else -1
    remaining = abs(n)
    if remaining == 0:
        return d if is_trading_day(d) else next_trading_day(d)
    while remaining:
        cur += timedelta(days=step)
        if is_trading_day(cur):
            remaining -= 1
    return cur


def trading_days(start: date, end: date) -> list[date]:
    out = []
    cur = start
    while cur <= end:
        if is_trading_day(cur):
            out.append(cur)
        cur += timedelta(days=1)
    return out


def trading_days_between(a: date, b: date) -> int:
    """Signed number of trading days from a to b (0 if same day)."""
    if a == b:
        return 0
    if a < b:
        return sum(1 for d in trading_days(a + timedelta(days=1), b))
    return -sum(1 for d in trading_days(b + timedelta(days=1), a))


def now_ny() -> datetime:
    return datetime.now(NY)


def market_hours(d: date) -> tuple[datetime, datetime] | None:
    if not is_trading_day(d):
        return None
    open_t = datetime.combine(d, time(9, 30), NY)
    close_t = datetime.combine(d, time(13, 0) if early_close(d) else time(16, 0), NY)
    return open_t, close_t


def is_market_open(at: datetime | None = None) -> bool:
    at = at or now_ny()
    at = at.astimezone(NY)
    hours = market_hours(at.date())
    return bool(hours and hours[0] <= at < hours[1])


def last_completed_session(at: datetime | None = None) -> date:
    """The most recent trading day whose regular session has closed."""
    at = (at or now_ny()).astimezone(NY)
    hours = market_hours(at.date())
    if hours and at >= hours[1]:
        return at.date()
    return prev_trading_day(at.date())
