# News factors: do headlines predict stock returns once you stop reading them early?

[中文](zh/news-factors.md)

`riskaware research factors` turns the headlines that ship with the package into ten daily news factors for 64 large US stocks, plus an eleventh from a language model's reading of each headline, using only what was published before each day's close, and tests them against returns from the next open in three very different markets: 2020 (the COVID crash), 2022 (the rate-hike bear market) and 2026 (a rising market). It then computes the same factors the way many news backtests do, joined to prices by calendar date, and measures how much that shortcut inflates them.

**What we found**

- **No news factor survives.** Eight of the ten are noise (pooled |t| below 2 at one and five days). Two, the number of headlines and how many other companies they name, have a small negative one-day IC (-0.013, t = -2.25 and -0.020, t = -2.04) that momentum, reversal and low volatility absorb.
- **A language model reads headlines better, not well enough.** Sentiment scored by gpt-5.6-luna is the strongest news factor: a one-day IC of 0.021 (t = 2.5), positive in all three years, four times the lexicon's. The price factors absorb it too (slope t = 1.1), and it changes as fast as the lexicon: 82% of its top quintile is new every day, so its 8.9 bp daily spread breaks even at 2.7 bp of cost per side.
- **A calendar-date join makes one up.** Joined by calendar date and tested close to close, lexicon sentiment has a rank IC of 0.032 (t = 3.4) and a long-short spread of 36.9 bp a day. Joined by publication time and traded from the next open, it has 0.005 (t = 0.6) and 7.4 bp (t = 1.0). Two-thirds of the gap (+0.018 of IC, t = 4.8) is news published after the close that the calendar join reads early, and it shows up in each of the three periods.
- **Price factors as a yardstick.** 12-1 momentum, the strongest of the four price factors here, has a pooled one-day IC of 0.027 (t = 2.3).

- [Run it](#run-it)
- [The ten news factors](#the-ten-news-factors)
- [How each factor is tested](#how-each-factor-is-tested)
- [Results](#results)
- [Calendar date against publication time](#calendar-date-against-publication-time)
- [An LLM sentiment factor](#an-llm-sentiment-factor)
- [Limits](#limits)

## Run it

```bash
riskaware research factors                   # all three periods, about 80 seconds
riskaware research factors --period 2022     # one period
riskaware research factors --charts en,zh    # the report and charts in English and Chinese
riskaware research factors --llm             # add a sentiment factor scored by a local LLM (see below)
```

The command prints a summary table and writes a folder under `~/.riskaware/research/factors/` (or `--out`):

| File | What it holds |
|---|---|
| `factors.json` | Every number: verdicts, IC by horizon, quintiles, decay, turnover, price controls, regimes, the four join cells, coverage and the SHA-256 of the data files |
| `factors.md` | The report as tables (`factors_zh.md` with `--charts en,zh`) |
| `ic_en.png` | Mean one-day rank IC of every factor, pooled with a 95% interval, and each period |
| `alignment_en.png`, `cumulative_en.png` | The tone factors under both joins, and the cumulative daily IC of lexicon sentiment in each period |
| `groups_en.png`, `decay_en.png` | Quintile returns, and the IC of the one-day return k days later |
| `daily_ic.csv` | The daily one-day rank IC of each news factor, for your own tests |

From Python:

```python
from riskaware.research.news_factors import NewsPanel, run_study, save_study

study = run_study(["2026", "2022", "2020"])       # FactorStudy
study.verdicts["sentiment"]                        # {"verdict": "noise", ...}
study.pooled_alignment["sentiment"]["h1"]          # the four join cells and the inflation
save_study(study, "factors", langs=("en", "zh"))

feats = NewsPanel("2026").features("publication")  # factor -> DataFrame (trading days x stocks)
```

## The ten news factors

Every value for day t uses only items published before t's close (16:00 New York, 13:00 on early-close days), the moment the package's backtests decide. An item belongs to the first close after its timestamp, so one published at 16:05 counts on the next trading day.

| Factor | What it measures | From |
|---|---|---|
| Headline count | Headlines published since the previous close | today's items |
| Attention anomaly | log((1 + today's headlines) / (1 + their mean over the 20 trading days before)) | today's items and the 20 days before |
| Lexicon sentiment | Mean score of today's headlines from the package's finance lexicon (`news.lexicon`), opinion pieces left out; stocks without news are not ranked | today's items |
| Verified news score | The score the agents read: stories clustered and graded by `news.verify`, weighted by confirmation and age | the last 72 hours |
| Confirmed stories | Stories backed by an SEC filing, the company's own release or two independent outlets | the last 72 hours |
| Single-source stories | Stories from a single outlet | the last 72 hours |
| Red flags | Distinct red flags asserted (fraud, probes, guidance cuts, recalls, delisting, ...); denied ones do not count | the last 72 hours |
| Earnings 8-K | An 8-K with Item 2.02 (results of operations) filed since the previous close; 2026 only | today's filings |
| Novelty | 1 minus the mean word overlap (Jaccard) of today's headlines with those of the five trading days before, company names removed | today and the 5 days before |
| Co-mentions | Mean number of other covered companies named in today's headlines | today's items |

The 2020 and 2022 headlines come from one feed and have no SEC filings, so no story can be confirmed there: confirmed stories and earnings 8-Ks exist in 2026 only.

## How each factor is tested

- **Returns.** Total-return prices (dividends included), from the open of the trading day after the decision to the open 1, 5 and 20 trading days later: the package's orders fill at the next open.
- **Rank IC.** Each day, the Spearman correlation across stocks between the factor and the return that follows (ties get average ranks; at least 10 stocks). The t-statistic of the mean is Newey-West with as many lags as the horizon, because returns over overlapping days are correlated. "Pooled" puts the daily ICs of all three periods together.
- **Quintiles.** Each day the stocks are ranked into five groups (tied values stay in one group, so a factor that is 0 for most stocks fills fewer groups). Group returns are measured against that day's mean of the stocks ranked; the long-short spread is the top group minus the bottom group.
- **Decay.** The IC of the factor with the one-day return that starts k = 0 to 20 trading days later.
- **Turnover and break-even cost.** The share of the top group replaced each day, and the cost per side at which a daily long-short of the top and bottom groups earns nothing.
- **Price controls.** Each day the factor's ranks are regressed on four price factors known at the same close (12-1 momentum, 1-week and 1-day reversal, 60-day low volatility); the residual's IC is the residual IC. Returns are also regressed on the standardized factor and the four controls (Fama-MacBeth); the factor's slope and its t-statistic say whether the news adds anything.
- **Market regimes.** The one-day IC on the days of each regime of the package's macro agent (risk-on, neutral, uncertain, risk-off), read at the decision close.
- **Verdict.** *Useful*: pooled |t| at least 2 at one or five days, the mean IC of the same sign in every period where the factor exists, and a slope of that sign with |t| at least 2 after the price controls. *Weak*: pooled |t| at least 2 without the other two. *Noise*: pooled |t| below 2 at both horizons. The rule is `verdict()` in `research/news_factors.py`, the same for every factor.

## Results

64 stocks; 2026-01-02 to 2026-11-11 (217 trading days), 2022 (251) and 2020 (253). Rank IC (t):

| Factor | Verdict | 1 day | 5 days | 20 days | Slope after price controls, 1 day |
|---|---|---:|---:|---:|---:|
| LLM sentiment (gpt-5.6-luna) | weak | +0.0210 (+2.49) | +0.0226 (+2.06) | +0.0178 (+1.48) | +1.79 bp (+1.09) |
| Headline count | weak | -0.0134 (-2.25) | -0.0104 (-1.00) | -0.0090 (-0.51) | -0.97 bp (-1.01) |
| Co-mentions | weak | -0.0198 (-2.04) | -0.0183 (-1.40) | -0.0335 (-1.61) | -2.13 bp (-1.28) |
| Attention anomaly | noise | -0.0062 (-1.23) | +0.0036 (+0.58) | +0.0142 (+1.99) | -0.26 bp (-0.31) |
| Lexicon sentiment | noise | +0.0048 (+0.55) | +0.0088 (+0.83) | -0.0068 (-0.57) | -0.30 bp (-0.17) |
| Verified news score | noise | -0.0007 (-0.09) | -0.0009 (-0.08) | -0.0097 (-0.69) | -1.35 bp (-1.05) |
| Confirmed stories (2026) | noise | -0.0047 (-0.53) | +0.0029 (+0.25) | +0.0052 (+0.51) | -0.47 bp (-0.31) |
| Single-source stories | noise | -0.0099 (-1.61) | -0.0079 (-0.71) | -0.0099 (-0.54) | -0.45 bp (-0.44) |
| Red flags | noise | +0.0075 (+0.99) | +0.0017 (+0.16) | -0.0055 (-0.40) | +1.06 bp (+0.82) |
| Earnings 8-K (2026) | noise | -0.0082 (-0.60) | -0.0102 (-0.77) | -0.0006 (-0.06) | +0.37 bp (+0.14) |
| Novelty | noise | +0.0016 (+0.17) | +0.0048 (+0.41) | +0.0160 (+0.91) | -0.58 bp (-0.34) |

By period, one day:

| Factor | 2026 | 2022 | 2020 |
|---|---:|---:|---:|
| Headline count | -0.0019 (-0.16) | -0.0230 (-2.30) | -0.0136 (-1.50) |
| Lexicon sentiment | +0.0031 (+0.19) | +0.0255 (+1.86) | -0.0144 (-0.97) |
| Verified news score | -0.0082 (-0.64) | +0.0184 (+1.59) | -0.0131 (-1.01) |
| Novelty | -0.0238 (-1.33) | +0.0175 (+1.13) | +0.0072 (+0.52) |
| LLM sentiment | +0.0184 (+1.13) | +0.0313 (+2.38) | +0.0130 (+0.89) |
| Co-mentions | -0.0094 (-0.57) | -0.0263 (-1.44) | -0.0223 (-1.45) |
| Momentum 12-1 | +0.0188 (+1.07) | +0.0251 (+1.30) | +0.0368 (+1.51) |
| Reversal, 1 week | +0.0106 (+0.60) | +0.0027 (+0.14) | +0.0297 (+1.57) |
| Low volatility | +0.0077 (+0.35) | +0.0356 (+1.25) | -0.0020 (-0.12) |

What the tables say:

- **Tone does not carry over to the next open.** Lexicon sentiment and the verified score come closest in the 2022 bear market (lexicon sentiment 0.026, t = 1.9) and turn negative in 2020. The top quintile of lexicon sentiment did a little better than the bottom one (7.3 bp a day, t = 1.0), not enough to tell from chance, and the decay curves wander around zero.
- **More news, slightly lower next-day returns, explained by price.** Stocks with many headlines, or with headlines that also name other companies, did a little worse the next day. Stocks with many headlines are the day's big movers (the daily rank correlation of headline count with the size of that day's move is about 0.15 in each period); with 1-day and 1-week reversal and the other price factors in the regression, both slopes lose significance (t = -1.0 and -1.3).
- **Too fast to trade.** Lexicon sentiment replaces 85% of its top group every day (the verified score, which looks back 72 hours, 51%). Its 7.3 bp daily spread breaks even at 2.2 bp per side, below the package's default 5 bp slippage.
- **Regimes add nothing reliable.** Of about 36 regime readings of the news factors, three have |t| just above 2 (co-mentions on uncertain days, and lexicon sentiment and the verified score on the 24 neutral days), close to what chance gives.
- **The model's sentiment is steadier than the lexicon's, and still weak.** Its IC is positive in every year (largest in the 2022 bear market: 0.031, t = 2.4), its top quintile beats its bottom one by 8.9 bp a day (t = 1.5), and after the price controls its five-day residual IC is 0.027 (t = 3.4). Its Fama-MacBeth slope with the controls stops short of the rule's bar (t = 1.1 at one day, 1.9 at five), so it counts as weak: a lead worth more data, not a factor to trade.
- **One more lead, not a finding.** After the price controls, lexicon sentiment has a five-day residual IC of 0.018 (t = 2.2), but its Fama-MacBeth slope (t = 1.1) and its raw IC (t = 0.8) do not confirm it. Eleven factors, three horizons and several tests make an occasional |t| above 2 likely by chance.

## Calendar date against publication time

The same factor, computed two ways, tested against two return windows:

| Lexicon sentiment, one day, pooled | Rank IC (t) | Long-short spread |
|---|---:|---:|
| Publication time, from the next open (what could be traded) | **0.005 (0.6)** | 7.4 bp (t = 1.0) |
| Publication time, close to close | 0.014 (1.5) | 7.8 bp |
| Calendar date, close to close (the shortcut) | **0.032 (3.4)** | 36.9 bp (t = 4.8) |
| Calendar date, from the next open | 0.004 (0.4) | 5.6 bp |

The calendar join puts a headline published at 16:05 on the row of that day's 16:00 close, and a close-to-close return then starts before the headline existed. That is the only cell that reads the future. Holding the return window fixed (close to close), the calendar join adds **+0.018 to the IC (t = 4.8)**: two-thirds of the +0.027 gap to the honest replay. The leak appears in every period (+0.0157, t = 1.8 in 2026; +0.0209, t = 3.7 in 2022; +0.0165, t = 3.1 in 2020), and the verified news score shows the same pattern (0.017, t = 2.2 joined by date against -0.001, t = -0.1). So does the language model's sentiment, more strongly: 0.050 (t = 5.6) joined by date and tested close to close, against 0.021 (t = 2.45) joined by publication time; the join alone adds +0.024 (t = 5.7).

The calendar join with next-open returns does not leak (the evening's news is out before the next open) and it is no better than the honest replay, which is the point: the shortcut's IC is the overnight move that follows after-close news, collected by a trade that starts before it.

A calendar-date join moves 13% of the 2026 Benzinga headlines and 62.5% of the 2026 8-K filings (companies report after the close) to an earlier trading day than their publication time allows; 10% of the headlines in 2022 and 11% in 2020. For a simpler demonstration with one headline rule, see `examples/news_timing.py` and the [news backtesting guide](https://jackson-cmd.github.io/risk-aware-trading-agents/news-backtesting.html).

## An LLM sentiment factor

`--llm` adds an eleventh factor: the mean score from -1 to 1 that a language model gives each headline. The default is `ollama:llama3.2:3b`, which runs on your own computer for free (`ollama pull llama3.2:3b`); `--llm-model` takes any model the package can call (`openai:...`, `anthropic:...`, `compat:...`).

- Before scoring, the command prints how many headlines are not scored yet, the number of calls and an estimate of input and output tokens; a paid model asks before it starts (`--yes` skips the question). The bundled files hold 62,722 distinct headlines (15,541 in 2026), about 2.0 million input and 0.3 million output tokens in batches of 25.
- Scores are cached in `~/.riskaware/research/headline_scores/<model>.jsonl`, one line per headline and prompt version, so each headline is scored once; an interrupted run goes on where it stopped (`--llm-limit` scores a few at a time).
- To score through a batch API or a job queue instead, `--llm-export requests.jsonl` writes one request per 40 headlines without a score (`key`, `system`, `prompt`, `max_tokens`, and `meta` with the headline keys) and stops: 1,569 requests for the three periods, about 1.9 million input and 0.26 million output tokens. Run them with any tool that returns each reply as `text` together with the request's `meta`; `--llm-import results.jsonl` then caches the scores and runs the study with them, calling no model.
- The prompt asks the model to judge the headline alone and to ignore what happened afterwards, but a model trained after 2020 or 2022 may still know. llama3.2's training data ends in 2023, so its 2026 scores cannot know the outcome; compare periods with that in mind.
- With Ollama, the scorer uses Ollama's own chat endpoint and can ask for a context size (`HeadlineScorer(..., num_ctx=16384)`), so it shares a model another program has loaded with that size instead of making both reload it.

**The run on this page.** gpt-5.6-luna at temperature 0, with JSON replies and reasoning off, through a job queue: 1,569 requests of 40 headlines, then the 145 batches (9%) that came back with 41 scores for 40 headlines again in batches of 20, which all came back right. 62,722 headlines in all, about 2.2 million input and 0.26 million output tokens. `factors.json` records the model, the route it was served through, the prompt and its SHA-256. luna was trained after 2020 and 2022, and perhaps during part of 2026, so it may know how some stories ended; its IC is not higher in the older years (2020 is its lowest, 0.013), which is some comfort but no proof.

## Limits

- 64 of the largest US stocks: heavily covered and quickly priced. News factors may do better on small caps, where this data has nothing to say.
- Headlines only, from one feed (Benzinga), plus 8-K filings in 2026. No article text, no social media, no analyst data.
- One decision a day at the close. Faster reactions to news (minutes after a headline) are outside what daily bars can test.
- A year per period is short: an IC of 0.01 needs several years to separate from zero.

Data: `riskaware research data` prints the SHA-256 of every file; `factors.json` records the hashes of the files a run used.
