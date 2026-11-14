# LLM trading agent leaderboard (rta-bench-1)

[中文](zh/leaderboard.md) · [The leaderboard on the website](https://jackson-cmd.github.io/risk-aware-trading-agents/leaderboard.html)

rta-bench-1 is a fixed game for LLM trading agents on the [research kit](research.md)'s frozen markets. Every model trades the same 56 large-cap US stocks through 2026 to date, the 2022 bear market and the 2020 crash, after every fifth close, on prices, indicators and headlines replayed by the minute they were published. It plays each period twice, with the real names and blind. A model's training cutoff decides which of its scores count as skill: those from after the cutoff are ranked, those from before it are listed apart. An entry is one JSON file that holds the model's replies, so anyone can replay it and get the same numbers without calling the model.

- [The tasks](#the-tasks)
- [Why these settings](#why-these-settings)
- [Clean and may-remember scores](#clean-and-may-remember-scores)
- [Run a model](#run-a-model)
- [Submit an entry](#submit-an-entry)
- [What an entry holds](#what-an-entry-holds)
- [How entries are checked](#how-entries-are-checked)
- [How each model makes money](#how-each-model-makes-money)
- [The board's own runs](#the-boards-own-runs)
- [Limits](#limits)

## The tasks

| Task | Window | Decisions | The model sees |
|---|---|---|---|
| `2026-blind`, `2026-named` | 2026-01-02 to 2026-11-11 | 44 | A rising market with a spring sell-off |
| `2022-blind`, `2022-named` | 2022-01-03 to 2022-12-30 | 50 | The rate-hike bear market |
| `2020-blind`, `2020-named` | 2020-01-02 to 2020-12-31 | 51 | The COVID crash and rebound |

In a blind task tickers become S01, S02 and so on, dates become day numbers, prices are rebased to 100 on the first day, and company names and tickers inside headlines become the same aliases. The named task shows everything.

Every task uses the same settings (`riskaware.research.bench.SETTINGS`):

| Setting | Value |
|---|---|
| Stocks | the 56 of the `megacap` list, in 11 sectors |
| Decisions | after every fifth close (`decide_every=5`); orders fill at the next trading day's open |
| Positions | long only, at most 10, $100,000 to start, uninvested cash earns the T-bill ETF BIL |
| News | on; up to 3 headlines per stock per decision, published before that day's close |
| Execution | plain: the model's orders and nothing else (no risk rules on top) |
| Fill costs | realistic: 5 bps plus a tenth of the overnight gap plus square-root market impact, and no fill above a share of the average volume ([costs](costs.md)) |
| Seeds | 0, 1 and 2, one entry each: a seed fixes the blind aliases and, where the API takes one, the sampling seed. The board shows the mean over a model's seeds and their range; fewer seeds are accepted and shown as such |

The board's own runs call each model with the package's system prompt (`research.agent.SYSTEM_PROMPT` plus the order schema), temperature 0.2, at most 2,000 output tokens, and reasoning off where the API has a switch. A submission may call its model differently; the entry says how.

## Why these settings

**56 stocks rather than a smaller list.** A model can only show how it makes money if it can choose between sectors. In this list energy stocks rose 73% on average in 2022 while tech fell 32%; in 2020 energy lost 31% while tech gained 58%; in 2026 energy (+43%) and health care (+25%) beat tech (+12%). The 18 stocks of the `tech` list leave a model one sector and cash. The price is tokens. Measured on real observations with each model's own tokenizer, one decision on megacap averaged 10,900 to 11,900 input tokens for GPT-5.6 Luna, 12,300 to 13,500 for Claude Sonnet 4.6 and 13,700 to 14,700 for Gemini 3.5 Flash in the board's own runs (on the 9 stocks of `core` it was 2,800 for Luna; `tech` would be about 40% of megacap). All six tasks are about 290 decisions, so about 3 to 4.5 million input tokens per seed: well under a dollar on a small model and about $10 to $15 on a Sonnet-class model at list prices (times three for the three seeds). The five rule strategies in [the research doc](research.md#five-swing-strategies-with-and-without-news) run on the same 56 stocks, so they serve as anchors.

**Every fifth close.** Swing trading on daily bars holds for days or weeks; a weekly decision keeps that rhythm at a fifth of the tokens of a daily one.

**Blind and named.** Blind lowers the chance that the model recalls how a stock did; named shows what it does with everything it knows. The gap between the two is a memory check that comes for free.

## Clean and may-remember scores

Each entry gives the model's training cutoff (`--cutoff YYYY-MM`, the training-data cutoff from the vendor's model page, not the release date). For each period:

- `clean`: the window starts after the cutoff. The model cannot have read about it.
- `partly`: the cutoff falls inside the window. The entry is also scored from the last close on or before the cutoff (`after_cutoff`), with SPY and the equal-weight list over the same days.
- `may_remember`: the window ends before the cutoff. Listed apart and not ranked as skill: the model may recall what happened, even blind, since blind mode keeps each stock's sector.

The website's clean table uses the `after_cutoff` scores, so every model there is measured only on days it cannot know about.

## Run a model

```bash
riskaware research bench --llm openai:gpt-5.6-luna                   # all six tasks, seeds 0, 1 and 2
riskaware research bench --llm ollama:llama3.2:3b --task 2026-blind --seed 0   # one task, one seed, a local model
riskaware research bench --llm compat:deepseek-chat --base-url https://api.deepseek.com/v1
riskaware research bench --llm claude-cli                             # your Claude subscription
```

Each run prints its result, the tokens it used and the command to submit it. Local models go to Ollama's native chat API with a 16,384-token context: Ollama's OpenAI-compatible endpoint keeps the server's default context (4,096 tokens on Ollama 0.11) and silently drops the start of a longer prompt, and one megacap observation is about 10,000 tokens.

From Python, any function from an observation to orders is a policy:

```python
from riskaware.research import bench

policy = bench.bench_policy("openai:gpt-5.6-luna")      # or your own function: observation -> {"orders": [...]}
result = bench.run_task(policy, "2026-blind", label="my-model")
print(result.summary()["metrics"], result.folder, policy.usage)
```

## Submit an entry

```bash
riskaware research submit RUN_ID --model gpt-5.6-luna --cutoff 2026-02 --provider OpenAI --closed-weights
```

`submit` checks the run against the benchmark and writes `leaderboard/entries/<model>-<task>-s<seed>.json`. It refuses a run with other settings, other data or a failed look-ahead check, and lists every difference. A run made before the realistic fill costs existed is replayed under them first: the model's orders stay the same, their fills pay the benchmark's costs. Options: `--access` (how the model was called), `--parameters` (its size), `--submitter`, `--notes`, `--memory` (a JSON file with the model's memory-probe result).

Then open a pull request that adds the file. Continuous integration replays every entry and rebuilds the website's tables; a maintainer merges it and the board updates on the next deploy. Run `riskaware research leaderboard` in a checkout to see the board in the terminal, `--verify` to replay every entry yourself.

## What an entry holds

| Field | What |
|---|---|
| `bench`, `task`, `seed` | `rta-bench-1`, the task and the seed |
| `model` | name, provider, how it was called (`access`, `call`: temperature, token limit, reasoning setting), `training_cutoff`, `open_weights`, `parameters` |
| `contamination` | `clean`, `partly` or `may_remember`, from the cutoff and the window |
| `settings`, `data` | the benchmark settings as run, and the SHA-256 of the price and news files |
| `record` | the run id, package version and commit, the SHA-256 of every file of the record (manifest, metrics, equity, trades, decisions, policy log), of the replies and of the observations; the fill costs the model's portfolio showed; the run it was replayed from, if any |
| `look_ahead_check` | the verdict of the observation check (must be `pass`) |
| `metrics`, `vs_spy`, `anchors` | return, Sharpe, Sortino, drawdown, turnover, exposure, holding days, trades; beta, alpha, information ratio, t-statistic and the bootstrap interval of the Sharpe ratio; SPY and the equal-weight list over the same window |
| `after_cutoff` | the same over the days after the cutoff, for `clean` and `partly` windows |
| `analysis` | the [run analysis](analysis.md) of the record: the summary (exposure and alpha, decision excess over SPY, hit rate, IC, news-triggered share), its findings, the decision and IC statistics, the factor regression and the profit and loss by holding period, sector, regime, trigger and stock (top and bottom three) |
| `memory` | the memory-probe result, if given |
| `usage` | calls, input and output tokens, unusable replies, seconds, list-price cost |
| `replies` | every reply of the model, in order |

## How entries are checked

Two levels, both without calling the model:

1. **Form** (`bench.check_entry`, in every test run): the settings are the benchmark's, the data hashes are the bundled files', the replies match their hash, the look-ahead check passed, and the contamination label follows from the cutoff.
2. **Replay** (`bench.verify_entry`, `riskaware research leaderboard --verify`, and the leaderboard workflow on every pull request that touches `leaderboard/`): the replies go through the backtest again, step by step, and return, Sharpe, drawdown, round trips and turnover must come out the same.

The look-ahead check of a run replays its replies on copies of the market whose prices and headlines after three cut days are random: the observations up to each cut must be identical, which proves that nothing from the future reached the prompt. Whether the model remembers the period is a separate question, answered by the cutoff split, the named-minus-blind gap and the memory probe.

## How each model makes money

The website's two tables come from the [run analysis](analysis.md) of each blind record (`research.analysis.analyze_run`), stored in the entry:

- **Where the return came from.** The run's daily return over T-bills is regressed on four factors built from the bundled prices: the market (SPY minus BIL), size (IWM minus SPY), growth (QQQ minus SPY) and momentum. The part the exposures explain is shown as a yearly rate, the rest is alpha, with its Newey-West t-statistic. KTD-Fin (arXiv 2605.28359) found that most of what LLM traders earn is market and style exposure; this is the check. Next to it: the average share in stocks, the market beta, the sectors that made and lost the most (profit and loss of every position, as a share of the starting $100,000), and the average holding period.
- **How good the decisions were.** For every buy and sell, the stock's return minus SPY's over the five trading days from the fill (a buy is credited with the excess it earned, a sell with the excess it avoided), with t-statistics that allow for decisions on the same day and overlapping windows; the share that came out right; the rank IC between what the model did and the next five days' returns; and the share of decisions whose reason cites news, with what the positions opened on news made.

## The board's own runs

The board opened with six models. Every one was called with the system prompt and settings above (temperature 0.2, at most 2,000 output tokens, reasoning or thinking off where the API has a switch).

| Model | Provider | Called through | Training cutoff | Source of the cutoff |
|---|---|---|---|---|
| `llama3.2` | Meta | an OpenAI-compatible gateway serving Ollama's `llama3.2` (3B), with `num_ctx` 16384 in each request | 2023-12 | the Llama 3.2 model card: "The pretraining data has a cutoff of December 2023" |
| `gpt-5.6-luna` | OpenAI | OpenAI API, `reasoning_effort` none, JSON mode | 2026-02-16 | OpenAI's model page: "Feb 16, 2026 knowledge cutoff" |
| `claude-haiku-4.5` | Anthropic | an OpenAI-compatible gateway, thinking off | 2025-07 | Anthropic's model page: training data cutoff July 2025 (reliable knowledge cutoff February 2025) |
| `claude-haiku-5.5` | Anthropic | Anthropic API, thinking off; blind tasks of seed 0 only | 2026-06 | Anthropic's model page: training data cutoff June 2026 |
| `claude-sonnet-4.6` | Anthropic | an OpenAI-compatible gateway, extended thinking off | 2026-01 | Anthropic's model page: training data cutoff January 2026 (reliable knowledge cutoff August 2025) |
| `gemini-3.5-flash` | Google | an OpenAI-compatible gateway, `reasoning_effort` minimal | 2025-01 | the Gemini 3.5 Flash model card on Box's model list: knowledge cutoff January 2025; Google's own card points to Gemini 3 Flash |

So 2026 is clean for Llama, Haiku 4.5 and Gemini, partly clean for Sonnet 4.6 (scored from 30 January), Luna (from 13 February) and Haiku 5.5 (from 30 June), and 2022 and 2020 may be remembered by all six. Sonnet 4.6 has one seed and four of the six tasks, Haiku 5.5 the three blind tasks of one seed; the others get all three seeds as the runs finish. The board says under each model when its runs were made.

The first blind runs (13 November 2026, before the package's change that masks dates and dollar amounts inside headlines) could still read a date or a price target in some headlines; later runs cannot. Each entry records the package version and commit it ran on.

Two traps the runs ran into. Ollama keeps a 2,048- or 4,096-token context unless a request asks for more, and cuts longer prompts from the front without an error: through the gateway, `llama3.2` reported 2,050 prompt tokens for a 9,400-token observation and answered with an echo of a stock record. With `num_ctx` set it read all of it. `bench_policy("ollama:...")` sets it for local servers. And an OpenAI-compatible gateway may answer with server-sent events unless the request says `"stream": false`, which the package's provider now does.

## Limits

- **Three seeds per task.** Sampling makes two runs of the same model differ. The board shows the mean over the seeds and their range; read differences smaller than the range as noise.
- **Survivorship.** The 56 stocks are today's large caps. Buying and holding all of them in 2020 returned +40%, more than anyone could have expected at the time; compare models with each other and with the rules, not only with the equal-weight list.
- **Blind is not airtight.** Blind mode hides tickers and company names, but a headline can still carry a date ("Top WallStreetBets Mentions for Monday May 9, 2022") or a dollar price target that gives the price level away, and a sector, a price path and an unusual headline together may give a stock away.
- **Ten to twelve months per period.** Short samples; the Sharpe interval in each entry says how short.
- **One feed of news.** Benzinga titles, plus SEC 8-K filings in 2026.
