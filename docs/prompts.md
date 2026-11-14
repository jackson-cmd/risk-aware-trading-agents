# Prompt tests: which prompt makes an LLM trading agent trade better

[中文](zh/prompts.md)

People tune the system prompt of a trading agent by feel: add a persona, add risk rules, ask for reasoning, paste a
few examples. `riskaware research prompts` measures these choices one at a time. Each prompt variant changes exactly
one thing from the package's own prompt. Every variant trades the same frozen periods with the same model, blind, with
news replayed by publication time and several seeds. Each variant is compared with the baseline run on the same
period and seed, and the report says which differences are larger than the gap between two seeds of the same prompt.

<!-- RESULTS -->

## The variants

The baseline is `agent.SYSTEM_PROMPT`, the prompt `riskaware research trade` and the MCP trading sessions use, with
three headlines per stock, a decision every fifth trading day, and the reply held to a strict JSON schema. Every
other variant changes one factor:

| Variant | Factor | What changes |
|---|---|---|
| `baseline` | — | The package's own prompt |
| `role_pm` | role | "You are an experienced portfolio manager at a long-only equity fund." in front |
| `role_quant` | role | "You are a quantitative researcher at a systematic hedge fund." in front |
| `role_risk` | role | "You are the risk manager of an investment firm." in front |
| `risk_rules` | risk rules | At most 20% per stock, sell at -8%, at least 20% cash (50% when risk-off or VIX above 30), no buys on red flags |
| `reason_first` | reasoning | A `reasoning` field of 3 to 5 short sentences comes before the orders |
| `focus_news` | focus | Decide mainly from the news (confirmation, recency, red flags); use prices to time entries |
| `focus_trend` | focus | Decide mainly from returns, moving averages, RSI and the distance from the high; use news to avoid red flags |
| `few_shot` | examples | Three made-up examples: a buy, a sell on a red flag, a hold |
| `format_json` | reply format | Any JSON object is accepted (JSON mode); the schema is only described in the prompt |
| `format_text` | reply format | Nothing enforced: no JSON mode and no schema, only the prompt's own sentence about the reply |
| `news_0` | headlines | No headlines; the news score, confirmation counts and red flags stay |
| `news_6` | headlines | Six headlines per stock instead of three |
| `daily` | frequency | A decision every trading day instead of every fifth |

`riskaware research prompts list` prints the table with each variant's SHA-256, and `--show <name>` prints the full
text. The set is versioned (`SET_VERSION`): a changed text becomes a new version, never an edit, so a published number
keeps pointing at the same prompt. The examples in `few_shot` use made-up stocks (S81 to S97) so that they cannot be
mistaken for the blind aliases of real stocks (S01 to S64).

## What stays the same

Within a lab every run uses the same model, temperature, reply cap, universe, number of positions, cash, slippage and
blind mode. A run is one variant on one period with one seed. The seed fixes two things: which alias each stock gets
in blind mode, and the sampling seed the model API is given (OpenAI-compatible servers and Ollama take one). Two
variants on the same period and seed therefore see the same aliases and draw from the same random stream, so the
difference between them is as much the prompt's doing as an experiment with a stochastic model allows.

Every run passes through the same harness as any other LLM run (`agent.run_policy`): the agent sees only what was
known at that day's close, its orders fill at the next open, and the look-ahead check replays its replies on copies of
the market whose prices and headlines after a cut day are random. The observations up to each cut must be identical,
and every record says whether they were.

## What each run records

The run folder (`<lab>/runs/<variant>/<period>-s<seed>/`) is a normal research record (`manifest.json`,
`metrics.json`, `equity.csv`, `trades.csv`, `decisions.csv`, `policy_log.json`, `report.md`, `equity.png`) plus:

- `prompt.txt`: the system prompt as sent;
- `manifest.json` → `prompt`: the variant's name, factor and settings, `prompt_sha256` (the text alone),
  `variant_sha256` (text, reply schema and settings together) and the variant set's version; `llm`: model,
  temperature, seed and reply cap; `reproduce`: the command that runs it again;
- `calls.jsonl`: one line per model call with its status (`ok`, `format` or `transport`), input and output tokens,
  seconds and retries, and the raw reply when it could not be used.

A call that failed on the way (a rate limit, a timeout, a server error) is tried again after a pause, up to four
times, so that an outage is not mistaken for a decision to hold. A reply that is not a JSON object with a list of
orders counts as a format error; an order with an unknown symbol, a sell of a stock not held or an unknown action
counts as an invalid order. In both cases the agent holds for that step, as it would in live use.

## How the variants are compared

For each variant and metric the report pairs every run with the baseline run on the same period and seed, and
takes the difference. Then:

- **the effect**: the mean difference within each period, averaged over the periods so that each period weighs the
  same;
- **a 95% bootstrap interval**: the pairs are resampled within each period, 10,000 times;
- **a p-value**: an exact sign-flip test (every way of flipping the signs of the paired differences), which needs no
  assumption about the shape of the distribution;
- **a correction for testing many variants**: Holm's method across the variants, for each metric. The report also
  gives Benjamini-Hochberg q-values as a less strict companion;
- **a verdict**: `better` or `worse` when the Holm-corrected p-value is below 0.05, otherwise `noise`. For behaviour
  metrics without a better direction (exposure, turnover, orders per step) the verdict is `differs` or `same`;
- **the noise floor**: how much two seeds of the baseline differ on the same period. A variant whose difference is
  no larger than that is indistinguishable from re-running the same prompt.

The metrics are the Sharpe ratio (the primary metric), total return, maximum drawdown, the return over SPY, volatility,
the exposure to stocks, turnover and orders per step (behaviour), the format error rate and the invalid order rate
(reliability), and the tokens per step (cost). SPY and the equal-weight universe are reported for every period.

How much evidence is possible: the smallest two-sided sign-flip p-value that n pairs can produce is 2/2ⁿ, and Holm
multiplies the smallest p-value by the number of variants. With thirteen variants, nine pairs (three periods, three
seeds) can never pass: 13 × 2/512 is 0.051. That is why the default is four seeds: twelve pairs per variant, where a
difference that points the same way in eleven or twelve of them can pass. The report says so when a lab is too small
to show anything. A prompt effect that only appears in some periods or some seeds is not one to rely on.

## Run it yourself

```bash
riskaware research prompts list
```

A pilot first: one variant, one period, one seed, to measure the tokens per step.

```bash
riskaware research prompts run --llm ollama:qwen2.5:3b --variants baseline --periods 2026 --seed-list 0 --out pilot
```

Then the price of the whole grid from the pilot's measured tokens, without calling any model:

```bash
riskaware research prompts run --llm openai:gpt-5.6-luna --pilot pilot --price-in 0.20 --price-out 1.20 --dry-run
```

The grid itself, then the report. An existing lab folder is resumed: finished runs are skipped.

```bash
riskaware research prompts run --llm openai:gpt-5.6-luna --workers 8 --out lab --price-in 0.20 --price-out 1.20
```

```bash
riskaware research prompts report lab
```

Your own prompt runs as one more variant next to the built-in ones (with the baseline's settings):

```bash
riskaware research prompts run --llm ollama:qwen2.5:3b --variants baseline --prompt-file my_prompt.txt --out lab
```

From Python:

```python
from riskaware.research import prompts as P

settings = P.LabSettings(llm="ollama:qwen2.5:3b", periods=["2026", "2022", "2020"], seeds=[0, 1, 2])
lab = P.run_lab(P.select("baseline,risk_rules,reason_first"), settings, "lab", workers=2)
result = P.report(lab)  # also writes prompts.json, prompts.md and prompts.png into the lab folder
```

`run_lab(..., provider_factory=lambda variant, seed: my_client)` runs any object with a
`complete_json(system, user, schema)` method instead of a built-in provider.

Local models: `ollama:` models go through Ollama's own `/api/chat` with a 16,384-token context window on every request.
Ollama's OpenAI-compatible endpoint keeps the server's default context (4,096 tokens on a stock install) and silently
cuts longer prompts, which would cut the observation of the few-shot and six-headline variants.

## The website's data

`riskaware research prompts report lab --site site/assets/data/prompts.json` writes the prompt board for the website
(format `rta-prompt-board-1`):

- `model`, `baseline`, `primary_metric`, `set_version`, `generated_utc`;
- `settings`: universe, symbols, max_positions, temperature, blind, news, periods, seeds;
- `benchmarks`: per period, the window and SPY's and the equal-weight universe's return, Sharpe and drawdown;
- `noise`: the mean Sharpe gap between two seeds of the baseline, and the standard deviation by period;
- `variants`: one entry per variant, the baseline first, with `name`, `factor`, `about` (`en`, `zh`), the two
  SHA-256 hashes, `runs`, `settings`, `metrics` (each with `mean` and `by_period`: sharpe, total_return,
  max_drawdown, excess_vs_spy, turnover, format_error_rate, invalid_order_rate), `tokens_per_step` and `vs_baseline`
  (null for the baseline; else per metric `diff`, `ci95`, `p`, `p_holm`, `pairs`, `by_period` and `verdict`);
- `cost`: calls, input and output tokens, list price;
- `method`: one sentence in English and Chinese on how to read the verdicts.

## Limits

- One model per lab. A prompt that helps one model can hurt another; run the lab again with your model before
  relying on a result.
- Blind mode lowers the chance that a model recognises a stock or a date; it cannot rule it out.
- The cost model is the research kit's default (5 bps of slippage, no commission). Turnover is reported so that a
  variant that only wins by trading more can be spotted.
- Three periods are three markets, not a sample of all markets. A result that holds in all three is stronger than
  one that holds in one, and still not a law.
