"""The frozen periods that ship with the package.

Each period is a test window plus the two years before it (to warm up slow indicators such as EMA200), with its own
price file, headline file and manifest. A strategy or an LLM that only did well in one kind of market says little;
the periods are chosen to differ:

* ``2026``: January to November 2026, a rising market with a spring sell-off (SPY +12.1%). The default.
* ``2022``: the whole of 2022, the rate-hike bear market (SPY -18.2%, the Nasdaq 100 -32.6%): eight Fed hikes,
  CPI above 9%, and large caps such as META, NVDA, TSLA and AMZN down 50% or more.
* ``2020``: the whole of 2020, the COVID crash and rebound: SPY down 34% in 23 trading days, VIX above 80, then a
  new high by August.

A new period is a new pair of files and a line here, never a change to an old one: every published number keeps
pointing at the same bytes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

NEWS_DIR = Path(__file__).resolve().parent.parent / "news" / "data"


@dataclass(frozen=True)
class Period:
    name: str
    title: str
    title_zh: str
    manifest: str  # file name in research/data
    news: str  # file name in news/data
    start: date  # first day of the test window (the days before it warm up the indicators)
    end: date  # last day of the data
    split: date  # suggested first day of the out-of-sample part
    about: str
    about_zh: str
    news_feeds: str

    @property
    def news_path(self) -> Path:
        return NEWS_DIR / self.news

    def describe(self) -> dict[str, Any]:
        return {"period": self.name, "title": self.title, "start": self.start.isoformat(), "end": self.end.isoformat(),
                "suggested_split": self.split.isoformat(), "about": self.about, "news": self.news_feeds}


PERIODS: dict[str, Period] = {
    "2026": Period(
        name="2026", title="2026 to date: a rising market with a spring sell-off",
        title_zh="2026 年至今：上涨行情，春季有一波下跌",
        manifest="manifest.json", news="news_2026.jsonl.gz",
        start=date(2026, 1, 2), end=date(2026, 11, 11), split=date(2026, 7, 1),
        about="SPY +12.1% from 2 January to 11 November 2026, with a -8.9% drawdown in March.",
        about_zh="2026 年 1 月 2 日到 11 月 11 日 SPY 涨 12.1%，三月最大回撤 -8.9%。",
        news_feeds="Benzinga headlines (titles only) and SEC 8-K filings"),
    "2022": Period(
        name="2022", title="2022: the rate-hike bear market",
        title_zh="2022 年：加息熊市",
        manifest="manifest_2022.json", news="news_2022.jsonl.gz",
        start=date(2022, 1, 3), end=date(2022, 12, 30), split=date(2022, 7, 1),
        about="SPY -18.2% and the Nasdaq 100 -32.6% in 2022: eight Fed hikes from 0.25% to 4.5%, CPI above 9% in "
              "June, and large caps such as META, NVDA, TSLA and AMZN down 50% or more.",
        about_zh="2022 年 SPY 跌 18.2%，纳斯达克 100 跌 32.6%：美联储加息八次（0.25% 到 4.5%），六月 CPI 超过 9%，"
                 "META、NVDA、TSLA、AMZN 等大盘股跌了一半以上。",
        news_feeds="Benzinga headlines (titles only)"),
    "2020": Period(
        name="2020", title="2020: the COVID crash and rebound",
        title_zh="2020 年：疫情暴跌和反弹",
        manifest="manifest_2020.json", news="news_2020.jsonl.gz",
        start=date(2020, 1, 2), end=date(2020, 12, 31), split=date(2020, 7, 1),
        about="SPY fell 34% in 23 trading days from 19 February to 23 March 2020, the fastest bear market on record, "
              "with VIX closing at 82.7; it was back at a high by August and ended the year +18.3% (the Nasdaq 100 "
              "+48.4%).",
        about_zh="2020 年 2 月 19 日到 3 月 23 日，SPY 在 23 个交易日里跌了 34%，是有记录以来最快的熊市，VIX 收盘最高 82.7；"
                 "八月收复新高，全年涨 18.3%（纳斯达克 100 涨 48.4%）。",
        news_feeds="Benzinga headlines (titles only)"),
}
DEFAULT_PERIOD = "2026"


def get_period(name: str | int | None = None) -> Period:
    """The period called ``name`` (``"2026"`` by default)."""
    key = str(name or DEFAULT_PERIOD).strip()
    if key not in PERIODS:
        raise ValueError(f"unknown period {key!r}; the bundled periods are {', '.join(sorted(PERIODS))}")
    return PERIODS[key]


def period_names() -> list[str]:
    return sorted(PERIODS)


def period_of(day: date) -> Period:
    """The period to show a day from: the one whose test window holds it, else the one whose warm-up years hold it."""
    for p in PERIODS.values():
        if p.start <= day <= p.end:
            return p
    for p in sorted(PERIODS.values(), key=lambda q: q.start):
        if date(p.start.year - 2, 1, 1) <= day <= p.end:
            return p
    raise ValueError(f"{day} is in none of the bundled periods ({', '.join(sorted(PERIODS))})")
