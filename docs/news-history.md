# News in backtests

Live trading reads news as it is published. A backtest has to replay what was known on each past day, and that needs the publication time of every item. Without it, a strategy can "see" a headline before it existed, which makes any news strategy look better than it is.

`riskaware news-history` keeps a point-in-time archive on your own machine and `riskaware backtest --news` replays it.

## Quick start

```bash
# 1. SEC 8-K filings for the core universe since 2018 (free; SEC asks for a contact email)
riskaware news-history build --preset core --start 2018-01-01 --contact-email you@example.com

# 2. optional: more news from your own sources
riskaware news-history build --symbols AAPL,NVDA --source benzinga      # needs BENZINGA_API_KEY in .env
riskaware news-history import my_news.csv --tz America/New_York

# 3. check what is there, and what a backtest would see on a given day
riskaware news-history status
riskaware news-history show NVDA --date 2024-05-22

# 4. backtest with news
riskaware backtest --strategy examples/strategies/news_earnings_drift.py --preset core --news --compare buy_and_hold
```

Put `data.contact_email` in `riskaware.yaml` once and you can drop `--contact-email`. Set `backtest.news: true` to replay news in every backtest.

## How the replay works

- The backtester decides at the close of day *t* (16:00 New York time) and fills orders on day *t+1*. `ctx.news(symbol)` on day *t* sees only items published at or before that close, within `news.lookback_hours` (72 by default). An item stamped 16:01 on day *t* is first visible on day *t+1*.
- The visible items go through the same code as live trading: headlines are clustered into stories, a story is **confirmed** when two independent publishers carry it or an SEC filing backs it, and a single-source red flag can block a buy but never trigger a sell.
- The built-in `risk_aware` strategy uses the report the same way it does live: confirmed red flags block new buys, and a confirmed severe flag (bankruptcy, delisting, restated financials) triggers its emergency exit.
- Without an archive, or with `--no-news`, `ctx.news()` returns `None`, exactly as before.

`riskaware news-history show SYMBOL --date DAY` prints the items and the verdict a backtest saw at that close, so you can check any trade.

## Sources

### SEC EDGAR 8-K filings

Public data from `data.sec.gov`. Each 8-K becomes one item: the headline is built from the item numbers (for example `8-K: Item 2.02 Results of operations (earnings)`), the link points to the filing, and the time is SEC's acceptance time. Item numbers carry meaning for the verifier: 2.02 is an earnings release, 5.02 an officer or director change, 1.03 bankruptcy, 3.01 a delisting notice, 4.02 non-reliance on past financial statements.

- SEC asks automated clients to send a contact email in the User-Agent and to stay under 10 requests a second; requests without an email get HTTP 403. The tool sends `risk-aware-trading-agents/<version> <your email>` and waits 0.15 s between requests.
- EDGAR's acceptance time is read as New York time. If a value were in fact UTC, reading it as New York time only makes the item visible later, never earlier, so the replay cannot look ahead.
- SPY and QQQ are funds, not operating companies, so the 8-K archive has little or nothing for them. The market regime already comes from the FOMC, CPI and jobs calendar in every backtest.
- The filing text is not parsed; the item numbers say what kind of event it was, not whether earnings beat.

### Benzinga (your own key)

`--source benzinga` calls Benzinga's news API (`api.benzinga.com/api/v2/news`) with the key in `BENZINGA_API_KEY` (or the variable named by `news.benzinga_key_env`), one calendar month at a time, 100 headlines per page. How far back you can go depends on your Benzinga plan. The client follows Benzinga's public API documentation and is tested with recorded responses; please report problems with a live key.

Benzinga counts as one publisher, so a Benzinga-only story stays single-source until an SEC filing or a second publisher backs it. **Benzinga's terms do not allow redistributing its content.** The archive lives in your home folder, outside the repository; do not commit it, upload it or share it.

### CSV

Any news you have the right to use. One row per headline:

| Column | Required | Example |
|---|---|---|
| `timestamp` (or `published`, `datetime`, `date`, `created`) | yes | `2024-05-22T20:21:00Z`, `2024-05-22 16:21:00-04:00`, `Wed, 22 May 2024 16:21:00 -0400` |
| `symbol` | yes | `NVDA`, or several: `NVDA;AMD` |
| `headline` (or `title`) | yes | `Nvidia beats estimates, announces 10-for-1 split` |
| `source` (or `publisher`) | no | `Reuters`; defaults to `--source` or the file name |
| `url` | no | link to the story |
| `sentiment` | no | a number from -1 to 1; replaces the built-in headline lexicon for that row |

Timestamps without a time zone are read as UTC; pass `--tz America/New_York` for exchange-time exports. Rows without a timestamp or a headline are skipped. Use the real publisher in `source`: the two-source check counts publishers, so labeling everything with one name makes every story single-source.

## The archive

- Location: `~/.riskaware/news/` (`news.archive_dir` in the config; follows `RISKAWARE_HOME`).
- One JSON Lines file per symbol, sorted by time, de-duplicated by source, link and time. Running `build` again only adds new items.
- `riskaware news-history remove SYMBOL [--feed sec|benzinga|import]` deletes items.

## Writing a strategy that uses news

```python
report = ctx.news(sym)          # None when nothing was published in the lookback window
if report is not None:
    report.score                # -1..1, weighted toward confirmed stories and recent ones
    report.red_flags_confirmed  # e.g. ["accounting", "bankruptcy"]
    for story in report.clusters:
        story.confirmed, story.event_types, story.headline, story.publishers
```

`examples/strategies/news_earnings_drift.py` buys after an earnings 8-K when the stock rose at least 3% over the two sessions around the report, and sells after 20 trading days, at +15%, or below EMA50. It is a demonstration of the interface, not a tested edge.

## Limits

- The 8-K archive tells you that something was filed and what kind of event it was; it carries no sentiment of its own except for the clearly bad items (bankruptcy, delisting, impairment, restatement).
- Free historical headlines with reliable timestamps are rare. A result that depends on news is only as good as the archive behind it; `status` and `show` let you check coverage before trusting a number.
- The universe presets are today's large caps, so every backtest on them carries survivorship bias.
