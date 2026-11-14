# Backtesting with news, without look-ahead bias

Live trading reads news as it is published. A backtest has to replay what was known on each past day, and that needs the publication time of every item. Without it, a strategy can "see" a headline before it existed, which makes any news strategy look better than it is.

`riskaware news-history` builds a point-in-time archive on your own machine: SEC 8-K filings for free, headlines from a news API with your own key, or any CSV. The backtest replays it under the rules below. The research results in this repository were made with headline files for 2020, 2022 and 2026 that are licensed and not distributed ([below](#the-research-headline-files-not-distributed)); the same rules run on your archive.

## Quick start

```bash
# SEC 8-K filings for the core universe since 2018 (free; SEC asks for a contact email)
riskaware news-history build --preset core --start 2018-01-01 --contact-email you@example.com

# more news from your own sources
riskaware news-history build --symbols AAPL,NVDA --source benzinga      # needs BENZINGA_API_KEY in .env
riskaware news-history import my_news.csv                                # times without a zone count as New York time

# a backtest that replays it
riskaware backtest --strategy examples/strategies/news_earnings_drift.py --preset core --start 2026-01-02 --news --compare buy_and_hold

# check what is there, and what a backtest would see on a given day
riskaware news-history status
riskaware news-history show NVDA --date 2026-05-20
```

On the dashboard, turn on **Replay news** on the Backtest page. Put `data.contact_email` in `riskaware.yaml` once and you can drop `--contact-email`. Set `backtest.news: true` to replay news in every backtest.

## The research headline files (not distributed)

The research periods ([docs/research.md](research.md)), the leaderboard and every number in the docs and on the website that involves news were made with three headline files for the 64 stocks of the built-in watchlists (`core`, `megacap`, `tech`, `defensive`):

| File | Items |
|---|---|
| `news_2020.jsonl.gz` | 19,618 headlines, the whole of 2020 |
| `news_2022.jsonl.gz` | 29,300 headlines, the whole of 2022 |
| `news_2026.jsonl.gz` | 16,945 items, January 1 to November 11, 2026: 16,249 headlines and 696 SEC 8-K filings |

- A headline item is the title, the publication time and the ticker, nothing else (no article text, teaser, link or sentiment). A headline is kept for a stock only when the feed tagged the stock and the headline itself names the company or its ticker. Automated recaps (what an investment would be worth today, dividend calculators, options-flow, short-interest and P/E summaries, TV pick lists) are left out: they repeat prices instead of reporting news.
- An SEC item is an 8-K filing with SEC's acceptance time and the link to the filing.

**The headlines are licensed, so the files are not distributed.** Versions up to 0.7.2 shipped the 2026 file inside the package; it is gone from the package, the repository and the Hugging Face dataset now, and the conclusions and numbers stay published. Each period's manifest keeps the SHA-256 of its headline file, so whoever has a copy can check that it is the same data: `riskaware research data` says `OK`, `changed` or `not distributed`.

**Where the code looks for a copy**, in this order: the folder in `RISKAWARE_NEWS_DIR`, then `~/.riskaware/data/news/`, then the package's own `riskaware/news/data/` (for an older install). Backtests read a copy together with your own archive; when both hold the same item, yours wins, and `remove` only deletes your items. Set `news.bundled: false` to leave the files out. Without a copy, nothing breaks: research experiments with news on run without these headlines and say so in their notes and manifest, and a benchmark run stops before it calls a model.

**Building your own, under the same rules.** Fill an archive with `riskaware news-history build` (SEC 8-K filings, free), a news API with your own key ([Sources](#sources)) or `riskaware news-history import` (any CSV), then cut a period's file from it with `scripts/build_news_bundle.py --archive ~/.riskaware/news --feed FEED --source-name NAME --start 2026-01-01 --end 2026-11-11 --out ~/.riskaware/data/news/news_2026.jsonl.gz` (`--feed` is the headline feed of your archive). The same rules apply: titles only, the relevance filter, publication times, the cross-check. A file built from other sources will not match the manifest's SHA-256, and numbers made with it will differ from the published ones.

## How the backtest avoids look-ahead

- The backtester decides at the close of day *t* and fills orders on day *t+1*. The close is 16:00 New York time, 13:00 on early-close days (July 3, the day after Thanksgiving, Christmas Eve). `ctx.news(symbol)` on day *t* sees only items published strictly before that close, within `news.lookback_hours` (72 by default). An item stamped at 16:00 sharp, which is when many earnings releases go out, is first visible on day *t+1*.
- A timestamp that could be read two ways is read the later way. A date without a time counts as the end of that day. A time without a zone counts as New York time: if it was really UTC, the item shows up a few hours late, while the opposite mistake would show it hours early. Items dated in the future are dropped as bad data.
- The strategy gets copies: an indicator row holds only that day's values, and `ctx.history()` returns a copy of the past rows, so nothing after day *t* can be reached through the price data. The indicators are trailing; a test recomputes them on data cut at day *t* and checks they are identical.
- Backtests never call an LLM. A model trained on text from the test period would know how each story ended.
- Strategies written by an AI through the [strategy lab](strategy-lab.md) are also checked for the author's own foresight: no dates and no stock tickers in the code.
- The visible items go through the same code as live trading: stories that do not name the company or its ticker are dropped (SEC filings always count), headlines are clustered into stories, a story is **confirmed** when two independent publishers carry it or an SEC filing backs it, and a single-source red flag can block a buy but never trigger a sell.
- The built-in `risk_aware` strategy uses the report the same way it does live: confirmed red flags block new buys, and a severe flag (bankruptcy, delisting, restated financials) triggers its emergency exit only when the company's own 8-K or press release carries it.
- Without an archive, or with `--no-news`, `ctx.news()` returns `None`, exactly as before.

## See what a backtest saw

On the dashboard, run a backtest with **Replay news** on. Below the results, the **News the backtest saw** panel lists every trade with the number of news items the backtest had seen when it decided. Pick one, or any stock and day, and it shows:

- the moment of the decision (the close, in New York time) and how many items were visible then;
- what the strategy did with that stock, filled the next trading day;
- the news score, the confirmed and single-source stories, and any red flags;
- every story with its time, status, sentiment and sources.

From the command line, `riskaware news-history show SYMBOL --date DAY` prints the same items and verdict.

## Sources

### SEC EDGAR 8-K filings

Public data from `data.sec.gov`. Each 8-K becomes one item: the headline is built from the item numbers (for example `8-K: Item 2.02 Results of operations (earnings)`), the link points to the filing, and the time is SEC's acceptance time. Item numbers carry meaning for the verifier: 2.02 is an earnings release, 5.02 an officer or director change, 1.03 bankruptcy, 3.01 a delisting notice, 4.02 non-reliance on past financial statements.

- SEC asks automated clients to send a contact email in the User-Agent and to stay under 10 requests a second; requests without an email get HTTP 403. The tool sends `risk-aware-trading-agents/<version> <your email>` and waits 0.15 s between requests.
- EDGAR's acceptance time is read as New York time. If a value were in fact UTC, reading it as New York time only makes the item visible later, never earlier, so the replay cannot look ahead.
- SPY and QQQ are funds, not operating companies, so the 8-K archive has little or nothing for them. The market regime already comes from the FOMC, CPI and jobs calendar in every backtest.
- The filing text is not parsed; the item numbers say what kind of event it was, not whether earnings beat.

### Benzinga (your own key)

`--source benzinga` calls Benzinga's news API (`api.benzinga.com/api/v2/news`) with the key in `BENZINGA_API_KEY` (or the variable named by `news.benzinga_key_env`), one calendar month at a time, 100 headlines per page. How far back you can go depends on your Benzinga plan. The client follows Benzinga's public API documentation and is tested with recorded responses; please report problems with a live key.

Benzinga counts as one publisher, so a Benzinga-only story stays single-source until an SEC filing or a second publisher backs it. What you download with your key goes to your archive in your home folder, outside the repository.

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

Times without a zone are read as New York time, the safe guess (see above); pass `--tz UTC` or another zone when you know it. A date without a time counts as the end of that day. Rows without a timestamp or a headline are skipped, and so are items dated in the future. Use the real publisher in `source`: the two-source check counts publishers, so labeling everything with one name makes every story single-source.

Backtests apply the same relevance filter as live trading: a story counts toward a stock only when its headline (or summary) names the company or its ticker, and SEC filings always count. Headlines in your CSV that name neither are dropped and shown as off-topic in `news-history show`; set `news.relevance_filter: false` to keep them.

## The archive

- Location: `~/.riskaware/news/` (`news.archive_dir` in the config; follows `RISKAWARE_HOME`).
- One JSON Lines file per symbol, sorted by time, de-duplicated by source, link and time. Running `build` again only adds new items.
- `riskaware news-history remove SYMBOL [--feed sec|benzinga|import]` deletes your items. The research headline files are read-only; `status` counts their items in the From files column.

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
- A headline is stored as it read when it was collected; if the publisher edited it later, the stored wording can include the edit, with the original time.
- Free historical headlines with reliable timestamps are rare. A result that depends on news is only as good as the archive behind it; `status` and `show` let you check coverage before trusting a number.
- The universe presets are today's large caps, so every backtest on them carries survivorship bias.
