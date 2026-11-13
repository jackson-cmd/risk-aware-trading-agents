"""Example: how much a news backtest gains by reading headlines before they were published.

Run it with the data that ships with the package (no download, no API key):

    python examples/news_timing.py

One simple headline rule is backtested twice on the 2026 Benzinga headlines. A headline scores +1 when it says
a company beat estimates or raised its outlook and -1 when it missed or cut, and the trade holds for one day in
that direction.

- Calendar date: the headline counts on the day it carries, and the trade runs from that day's close to the next
  close. A headline from 16:05 then lands on the row of the 16:00 close, before it existed. Many news backtests
  join data this way.
- Publication time: the headline counts from the first close after it was published (13:00 on early-close
  days), which is how the package's backtester shows news, and the trade runs from the next open to the open
  after that.

The whole difference comes from headlines published after the close. It is a demonstration of timing, not a
strategy, and not investment advice.
"""

from __future__ import annotations

import gzip
import json
from importlib.resources import files

import numpy as np
import pandas as pd

UP = r"\b(?:beats?|tops|raises (?:fy|full-year|annual|guidance|outlook|forecast)|above estimates)\b"
DOWN = (r"\b(?:miss(?:es)?|lowers (?:fy|guidance|outlook|forecast)|cuts (?:guidance|outlook|forecast)"
        r"|below estimates|weak guidance)\b")
NY = "America/New_York"


def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    package = files("riskaware")
    with (package / "research" / "data" / "market_2026.csv.gz").open("rb") as raw:
        prices = pd.read_csv(raw, compression="gzip", parse_dates=["date"])
    with (package / "news" / "data" / "news_2026.jsonl.gz").open("rb") as raw:
        news = pd.DataFrame(json.loads(line) for line in gzip.open(raw, "rt", encoding="utf-8"))
    news["ts"] = pd.to_datetime(news["ts"], utc=True)
    return prices, news


def close_time(day: pd.Timestamp) -> pd.Timestamp:
    early = ((day.month == 7 and day.day == 3) or (day.month == 12 and day.day == 24)
             or (day.month == 11 and day.weekday() == 4 and 23 <= day.day <= 29))  # the day after Thanksgiving
    return pd.Timestamp(day.date()).tz_localize(NY) + pd.Timedelta(hours=13 if early else 16)


def compare(prices: pd.DataFrame, news: pd.DataFrame) -> dict:
    wide = {col: prices.pivot(index="date", columns="symbol", values=col) for col in ("open", "close", "adj")}
    days = wide["close"]["SPY"].dropna().index                        # SPY's trading days
    open_tr = (wide["open"] * wide["adj"]).loc[days]                  # total-return prices
    close_tr = (wide["close"] * wide["adj"]).loc[days]
    where = {day: i for i, day in enumerate(days)}

    local = news["ts"].dt.tz_convert(NY)
    first = pd.DatetimeIndex([close_time(d) for d in days]).searchsorted(local, side="right")
    news = news.assign(usable_day=[days[i] if i < len(days) else pd.NaT for i in first],
                       calendar_day=local.dt.tz_localize(None).dt.normalize(),
                       after_close=local.dt.hour >= 16)

    heads = news[news["feed"] == "benzinga"].copy()
    heads["score"] = (heads["headline"].str.contains(UP, case=False).astype(int)
                      - heads["headline"].str.contains(DOWN, case=False).astype(int))
    heads = heads[heads["score"] != 0]

    def calendar_trade(row) -> float:
        i = where.get(row.calendar_day)
        if i is None or i + 1 >= len(days):
            return np.nan                                             # weekend or holiday, or no next close
        return row.score * (close_tr.at[days[i + 1], row.symbol] / close_tr.at[days[i], row.symbol] - 1)

    def timed_trade(row) -> float:
        i = where.get(row.usable_day)
        if i is None or i + 2 >= len(days):
            return np.nan
        return row.score * (open_tr.at[days[i + 2], row.symbol] / open_tr.at[days[i + 1], row.symbol] - 1)

    heads["calendar date"] = [calendar_trade(r) for r in heads.itertuples()]
    heads["publication time"] = [timed_trade(r) for r in heads.itertuples()]
    joins = ["calendar date", "publication time"]
    trades = heads[joins]
    return {
        "headlines": len(heads),
        "after the close": int(heads["after_close"].sum()),
        "trades": trades.count().to_dict(),
        "bps per trade": (trades.mean() * 1e4).round(1).to_dict(),
        "hit rate": ((trades > 0).sum() / trades.count()).round(3).to_dict(),
        "bps per trade by publication": (heads.groupby("after_close")[joins].mean() * 1e4).round(1).to_dict(),
        "moved to another day": (news["calendar_day"] != news["usable_day"]).groupby(news["feed"]).mean().round(3)
        .to_dict(),
    }


def main() -> None:
    result = compare(*load())
    print(f"{result['headlines']} scored Benzinga headlines in 2026, {result['after the close']} of them published "
          "after the 16:00 close\n")
    print(f"{'join':<18}{'trades':>8}{'mean per trade':>17}{'hit rate':>10}")
    for join in ("calendar date", "publication time"):
        print(f"{join:<18}{result['trades'][join]:>8}{result['bps per trade'][join]:>+13.1f} bp"
              f"{result['hit rate'][join]:>10.0%}")
    by_time = result["bps per trade by publication"]
    print(f"\nHeadlines published after the close: {by_time['calendar date'][True]:+.1f} bp per trade joined by "
          f"calendar date, {by_time['publication time'][True]:+.1f} bp joined by publication time.")
    moved = result["moved to another day"]
    print(f"Respecting the timestamp moves {moved['benzinga']:.0%} of the Benzinga headlines and {moved['sec']:.0%} "
          "of the SEC 8-K filings to a later trading day.")


if __name__ == "__main__":
    main()
