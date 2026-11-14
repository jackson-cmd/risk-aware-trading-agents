# Memory probe: does the model remember what happened next?

[中文](zh/memory.md)

The look-ahead check proves that no future price or headline reaches the prompt. It cannot see what the model brings with it. 2020 and 2022 are in the training data of every current model, and 2026 is in that of the newest ones, so a model asked about Apple on 10 May 2022 may simply remember how Apple did in the weeks that followed. Its backtest on that period then measures memory, not skill. The memory probe measures that memory from the outside, with nothing but the model's answers, so a leaderboard can say whose results on a period are clean.

- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [The memory score](#the-memory-score)
- [The training cutoff and the control](#the-training-cutoff-and-the-control)
- [Verdicts](#verdicts)
- [Cost and the cache](#cost-and-the-cache)
- [Through a batch runner or your own caller](#through-a-batch-runner-or-your-own-caller)
- [What a run writes](#what-a-run-writes)
- [A first run: gpt-5.6-luna and gpt-5.4-nano](#a-first-run-gpt-56-luna-and-gpt-54-nano)
- [Limits](#limits)
- [Related work](#related-work)

## Quick start

```bash
riskaware research memory --llm ollama:llama3.2:3b --period 2022 --pairs 100 --cutoff 2023-12 --estimate
riskaware research memory --llm ollama:llama3.2:3b --period 2022 --pairs 100 --cutoff 2023-12
```

The first line only prints the plan: how many calls, how many tokens, and what they cost. The second runs it and prints the report. `--cutoff` is the model's training cutoff; with it, the probe also asks about points after the cutoff as a control (here from 2026), which is what makes the verdict trustworthy.

From Python, for example to give every model on a leaderboard a memory score:

```python
from riskaware.research.memory import memory_probe

r = memory_probe("ollama:llama3.2:3b", period="2022", pairs=100, cutoff="2023-12")
r.verdict                       # "clean", "contaminated" or "inconclusive"
r.memory_score                  # AUC gap, named minus blind, before the cutoff
r.summary["memory_ci95"]        # its 95% bootstrap interval
r.summary["recall_score"]       # direct recall: rank correlation of remembered and actual moves
print(r.report())               # the report (r.report("zh") in Chinese)
```

`llm` is any model the package can call (`openai:gpt-5.6-luna`, `anthropic:claude-opus-5-5`, `compat:<model>` with `base_url`, `claude-cli`, `codex-cli`, `gemini-cli`, `ollama:<model>`), any object with `complete_json(system, user, schema)`, or a plain function (see [below](#through-a-batch-runner-or-your-own-caller)). `period` takes one bundled period or several (`"2022,2026"`); `pairs` and `recall` count per period.

## How it works

**Paired probe.** The probe draws random (stock, day) points from a period's test window (the same points for the same seed, on any machine; two points of one stock at least 20 trading days apart, so their outcomes do not overlap) and shows each point to the model twice, with the same information both times:

- the last 60 daily closes, oldest first;
- RSI(14), the distance from EMA 50 and EMA 200, ATR as a share of the price, the distance from the 52-week high, relative volume, and the 21-day return next to SPY's;
- up to three headlines published before that day's close, with how many hours before the close each appeared.

The **named** record gives the ticker, the company, the sector, the date and the prices as traded. The **blind** record gives an alias (S01), the sector and "day 0", rebases the prices so that the first close is 100, and masks the headlines: the company's names and ticker (the masking of blind trading mode), its best-known people ("the S01 chief") and products ("[product]"), and every date and year. Per-share dollar amounts in headlines are rebased like the prices, so "Raises Price Target to $264" keeps its distance from the price and stops naming the stock:

```
[a1] LLY (Eli Lilly, Health Care), close of 2022-04-13
closes (oldest first): 245.1 243.1 243.1 240.4 239.5 ... 308.4 311.7 309.0 308.1 302.7
RSI14 66 | vs EMA50 +9.2% | vs EMA200 +21.3% | ATR 2.2% of the price | from the 52-week high -3.6% | ...
headlines:
- 33h before the close: "Goldman Sachs Maintains Neutral on Eli Lilly, Raises Price Target to $264"

[a1] S01 (Health Care), close of day 0
closes (oldest first, the first = 100): 100.0 99.2 99.2 98.1 97.7 ... 125.9 127.2 126.1 125.7 123.5
RSI14 66 | vs EMA50 +9.2% | vs EMA200 +21.3% | ATR 2.2% of the price | from the 52-week high -3.6% | ...
headlines:
- 33h before the close: "Goldman Sachs Maintains Neutral on S01, Raises Price Target to $107.7"
```

Both times the model gives the probability that the stock's total return over the next 20 trading days beats SPY's. The two answers are scored against what happened: accuracy, Brier score, log loss and AUC. The two records differ only in who and when, so a model that does better when it is told is using what it remembers. Named and blind records never share a call: in one prompt the model could match a blind record to its named twin. The system prompt is the same for both and describes both forms.

**Direct recall.** The cheap second probe gives no data at all: "AAPL (Apple): 2022-05-10 to 2022-06-08. Did it rise or fall, counting dividends and adjusting for splits, and by about how many percent?" A model that does not remember can only guess, so its guesses do not rank the actual moves. It is scored by how often it names the direction, against always guessing the more common one, and by the rank correlation between guessed and actual moves, both divided first by the stock's usual 20-day move (from its volatility before the window). Without that division, a model that only knows that 2022 went down and that some stocks swing more than others would rank the moves without remembering any of them; the unscaled correlation is reported too. The first `recall` points of each period are asked this way.

## The memory score

The memory score is the **AUC gap**: the AUC of the named answers minus the AUC of the blind ones. AUC is the chance that a stock that beat SPY got a higher probability than one that did not, so the gap says how much better the model sorts winners from losers when it knows the name and the date. It comes with a paired bootstrap interval (2,000 resamples; points decided in the same week share the market's moves, so whole weeks are resampled together) and a permutation p-value: each version's answers are turned into ranks and the two ranks of random points are swapped, which makes no difference when the name carries no information.

Why AUC and not the Brier score: a format can move a model's answers as a whole. In a first test call, Llama 3.2 3B gave five blind records probabilities between 0 and 0.012. The Brier score, the log loss and the accuracy count a shift like that as better or worse forecasting, with no memory involved; AUC only looks at how the answers rank the stocks, which a shift of one version as a whole leaves alone. The accuracy, Brier and log-loss gaps are still reported, each with its interval.

## The training cutoff and the control

Given the model's training cutoff (`--cutoff 2025-06` covers all of June 2025), each point falls in one of three windows:

- **before**: the whole outcome window ends before the cutoff, so the model may have read how it ended;
- **after**: the decision day is after the cutoff, so it cannot have;
- **straddles**: neither; left out.

The points after the cutoff are the **control**. A format can still help or hurt there (a model may simply read real prices better than rebased ones), but memory cannot. So the probe compares the gap before the cutoff with the gap after it, and reports the difference with its own interval. When none of the requested periods has 30 points after the cutoff, the latest bundled period with enough days after it is added (2026 for any cutoff up to mid-August 2026), with `pairs` points drawn from its days after the cutoff; `--no-control` turns this off.

## Verdicts

| Verdict | Meaning |
|---|---|
| `contaminated` | With a control: the gap before the cutoff is larger than after it (the 95% interval of the difference is above 0). Without one: the gap's interval is above 0. |
| `clean` | The gap before the cutoff stays below 0.05 AUC (its interval's upper end), or it is no larger than the gap after the cutoff within 0.05. Points that all start after the cutoff are clean by construction. |
| `inconclusive` | Fewer than 30 pairs, a model that gives every named record the same probability, an interval too wide to tell, a gap that is about as large after the cutoff (a format effect, or a cutoff later than stated), or forecasts that look clean while direct recall shows the model remembers the moves. |

0.05 is the smallest AUC gap that matters: for picking stocks that beat SPY over a month, an AUC of 0.55 against 0.50 is a real edge. Telling a gap that small from zero takes a few hundred pairs; when a model's named and blind answers are close, the paired design needs far fewer.

Without `--cutoff` the verdict rests on the gap alone, and the report says that a format effect cannot be told apart from memory.

## Cost and the cache

- **Batching.** Five records per call by default (`--batch`), ten recall items per call. A record is about 400 tokens with headlines, so a call is about 2,300 tokens. For Ollama, calls are kept under 3,200 tokens: its default context is 4,096 tokens (in version 0.11), and it cuts longer prompts from the front without a warning.
- **The cache.** Every answer is stored under `~/.riskaware/research/memory/cache/<model>.jsonl`, keyed by model, record and every setting that changes the prompt (the batch size included). A rerun costs nothing, and a bigger run later starts with the points of the smaller one and pays only for the new ones.
- **The estimate.** `--estimate` prints the calls and tokens, and the cost when prices are given (`--price-in` and `--price-out`, USD per million tokens). Ollama models and the `-cli` subscriptions count as free. A run stops before its first call if the estimate is above `--max-cost` (5 dollars by default), and a paid model with no price given runs only with `--yes`. In Python, `max_cost` does the same, and `plan_probe(...)` returns the plan without calling anything.
- **Size.** 100 pairs and 50 recall windows are 45 calls and about 95,000 input tokens per period; the control period doubles it.

## Through a batch runner or your own caller

The calls do not have to come from the probe. With a request queue or a batch API:

```bash
riskaware research memory --llm gpt-5.4-nano --period 2022,2026 --cutoff 2025-08-31 --export-requests requests.jsonl
# run requests.jsonl through the queue or batch API, then score with the same options:
riskaware research memory --llm gpt-5.4-nano --period 2022,2026 --cutoff 2025-08-31 --import-results results.jsonl
```

Each request is one JSON line: `key`, `prompt`, `system` (ending with the reply's JSON schema, as a direct call sends it), `temperature`, `max_tokens` and `meta`, which names the cache key of every record in the request. The results file needs `text` and `meta` back on each line; token counts are added up when present. The answers go into the cache, and the scoring calls nothing. In Python: `export_requests(plan_probe(...), path)`, `import_results(path)`, then `memory_probe(..., offline=True)`.

A function works as the model too, for example one that writes every call to a ledger: `memory_probe(fn, period="2022", cutoff="2025-08-31", model_id="gpt-5.4-nano")`, with `fn(system, user)` returning the reply's text (or a dict). It needs `model_id`, the name its answers are cached under. Pass `temperature=` as the value the function really uses: the probe sets the temperature itself only for the models it calls, and the cache key records it either way.

## What a run writes

A folder under `~/.riskaware/research/memory/runs/` (or `--out`):

| File | Holds |
|---|---|
| `memory.json` | The verdict and why; `memory_score` with `memory_ci95` and `memory_p_value`; `memory_beyond_control` with its interval; `recall_score` with `recall_ci95` and `recall_verdict`; every score of both versions, overall, by period and by window; the recall scores; the estimate and what the calls actually used; the SHA-256 of the data; the settings and the system prompts |
| `answers.csv` | One line per point: stock, day, outcome, both probabilities, the recall answer, the window |
| `report.md`, `report_zh.md` | The report in English and Chinese |
| `example_prompts.json` | The first named, blind and recall prompt, exactly as sent |

## A first run: gpt-5.6-luna and gpt-5.4-nano

Two OpenAI models, each on 2022 and 2026 with the default settings: 100 pairs and 50 recall windows per period, seed 0, five records a call, temperature 0. The calls went through a request queue (`--export-requests`, then `--import-results`): 90 calls and 185,250 input tokens per model, about 5 cents each at list price. The training cutoffs come from OpenAI's model pages: 16 February 2026 for gpt-5.6-luna and 31 August 2025 for gpt-5.4-nano, so 2022 lies before both cutoffs and most of 2026 after them.

| | gpt-5.6-luna | gpt-5.4-nano |
|---|---|---|
| Points before the cutoff | 109 pairs (2022 and the first days of 2026) | 100 pairs (2022) |
| Forecast AUC before the cutoff, named / blind | 0.490 / 0.537 | 0.651 / 0.592 |
| **Memory score** (AUC gap) before the cutoff | −0.047 [−0.180, +0.093] | +0.059 [−0.004, +0.138], p = 0.04 |
| The same gap after the cutoff (the control) | −0.009 [−0.174, +0.148], 83 pairs | +0.007 [−0.082, +0.101], 100 pairs |
| Before minus after | −0.038 [−0.236, +0.189] | +0.052 [−0.064, +0.168] |
| Brier gap before the cutoff | −0.0025 [−0.0199, +0.0156] | +0.0145 [+0.0025, +0.0287] |
| Recall before the cutoff: direction right (best constant guess) | 75% (53%) | 42% (54%) |
| **Recall before the cutoff**: rank correlation | +0.69 [+0.51, +0.80] | −0.03 [−0.35, +0.28] |
| Recall after the cutoff: rank correlation | +0.25 [−0.05, +0.55] | +0.21 [−0.09, +0.47] |
| Verdict | inconclusive | inconclusive |

**gpt-5.6-luna remembers 2022.** Asked with no data, it names the direction of a stock's 20-day move three times out of four, against 53% for always guessing the more common direction, and its guessed moves rank the actual ones (0.69, after dividing both by the stock's usual move). After its cutoff the same correlation is 0.25, with an interval that includes 0. Yet its forecasts do not use that memory: named records rank the winners no better than the blind ones. The verdict is inconclusive, and the report says why: the memory is there, this task does not show it at work, and a strategy that asks the model other questions might.

**gpt-5.4-nano** does not recall 2022 when asked, but its named 2022 forecasts are a little better than the blind ones: AUC +0.059 (permutation p = 0.04) and a Brier gap whose interval lies above 0. After its cutoff the gap is +0.007. The difference between the two windows, +0.052 [−0.064, +0.168], cannot tell memory from chance at 100 pairs per window; it would take about 500.

Recall is the more sensitive probe of what a model knows; the paired gap is what a backtest cares about; and a model can show one without the other. Both blind AUCs on 2022 are above 0.5 (0.537 and 0.592), which may be skill, or the period showing through the sector and the headlines (see the limits).

## Limits

- **Blind records still carry clues.** Partners, drug names, executives below the CEO and unusual events can give a stock away, and a model that recognises the period from its headlines, from SPY's 21-day return or from the price path can still use what it knows about it. That lets the blind version use some memory, so the gap understates memory rather than overstating it.
- **Format effects.** Real prices and rebased ones read differently. The control window is what separates that from memory; without a cutoff, a gap is a warning, not proof.
- **Statistical power.** AUC on a few dozen points is noisy. 100 pairs per window tell a strong memory from none; certifying "clean" to within 0.05 takes a few hundred, or a model whose two answers agree closely.
- **One question, one prompt.** The probe measures memory that shows in a 20-day forecast under this prompt, at temperature 0. A model may use memory differently when it trades, which is why direct recall is asked too.
- **Prices are adjusted for later splits**, as in the bundled data and the trading observations, so a named price can differ from the price quoted at the time.

## Related work

- Haozhe Jia, "HindsightBench: A Black-Box Behavioral Audit Protocol for Parametric Hindsight in Time-Indexed LLM Decision Tasks", [arXiv:2607.18867](https://arxiv.org/abs/2607.18867), 2026. Compares versions of time-indexed tasks with and without the date, with masked and swapped content, and adds date-recovery and outcome-recall probes; across 15 models it finds effective cutoffs that are often earlier than the stated ones. The probe here applies the same paired idea to single stocks and scores the answers against what happened.
- Joohyoung Jeon and Hongchul Lee, "Can Blindfolded LLMs Still Trade? An Anonymization-First Framework for Portfolio Optimization", [arXiv:2603.17692](https://arxiv.org/abs/2603.17692), 2026. Hides tickers and company names from LLM trading agents so that their signals cannot come from memorized tickers.
- Mostapha Benhenda, "Look-Ahead-Bench: a Standardized Benchmark of Look-ahead Bias in Point-in-Time LLMs for Finance", [arXiv:2601.13770](https://arxiv.org/abs/2601.13770), 2026. Measures look-ahead bias by how performance in realistic financial workflows decays across market regimes.
