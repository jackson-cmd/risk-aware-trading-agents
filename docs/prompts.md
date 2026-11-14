# Prompt tests: which prompt makes an LLM trading agent trade better

[中文](zh/prompts.md)

People tune the system prompt of a trading agent by feel: add a persona, add risk rules, ask for reasoning, paste a
few examples. `riskaware research prompts` measures these choices one at a time. Each prompt variant changes exactly
one thing from the package's own prompt. Every variant trades the same frozen periods with the same model, blind, with
news replayed by publication time and several seeds. Each variant is compared with the baseline run on the same
period and seed, and the report says which differences are larger than the gap between two seeds of the same prompt.

## Results so far

The first batch is a pilot: the baseline and two rule prompts (`risk_rules` and `trend_news`, below) on three models
(`gpt-5.6-luna`, `claude-haiku-5-5`, `gemini-3.1-flash-lite`), the three periods, blind, the `core` universe, a decision
every fifth trading day, one seed: 27 runs, $0.26. Each prompt is paired with the baseline on the same model and
period: nine pairs. A fourth model, `llama3.2` on a university LLM gateway, is left out: the gateway cuts long prompts for
it (an 11,000-token prompt came back as 2,050 tokens read), so there is no telling whether it saw the whole
observation.

| Against the baseline | `risk_rules` | `trend_news` |
|---|---:|---:|
| Total return | +7.3 pp (95% CI -0.7 to +14.5), better in 8 of 9 | +8.9 pp (+1.9 to +14.9), 6 of 9 |
| Sharpe | +0.28 (-0.07 to +0.63) | +0.29 (-0.04 to +0.62) |
| Maximum drawdown | 6.7 pp smaller (3.5 to 9.9) | 6.9 pp smaller (3.9 to 9.2) |
| Return in 2026 / 2022 / 2020 | +3.2 / +16.9 / +1.9 pp | -2.8 / +20.7 / +8.6 pp |
| Holm-corrected p (return; drawdown) | 0.23; 0.11 | 0.23; 0.11 |

- **Rules in the prompt helped in the bear market.** In 2022 both prompts lost less than the baseline with every
  model (+16.9 and +20.7 pp on average). In 2026's rising market the difference was small (+3.2 and -2.8 pp). After
  correcting for testing two prompts, nothing passes 5%: suggestive, not established.
- **The gain came from how much stock the models held, not from which stocks.** Splitting the return differences
  (sums of daily returns, see "Where a difference comes from"): `risk_rules` +9.0 pp = exposure level +4.6, exposure
  timing +3.7, selection +1.0; `trend_news` +10.5 pp = +6.5, +6.0 and -2.3. In 2022 the rules cut the average share of
  equity in stocks from 44% to 19% (`risk_rules`) and 15% (`trend_news`); exposure explains all of that year's gain,
  and selection added little (+1.4 pp) or took away (-5.3 pp).
- **"Less stock" is not the whole story.** In 2020 the rules *raised* the exposure of the most cautious model,
  `claude-haiku-5-5`, from 19% to 49% (`risk_rules`) and 45% (`trend_news`), and that paid off in the rebound. The
  rules change how invested a model is and when; whether that pays depends on the market.
- **The decisions themselves did not get better.** Scored one by one (research/analysis.py: each buy against SPY over
  the next five trading days, each sell by what it avoided), the baseline's decisions were right 37% to 42% of the time
  and trailed SPY by 0.2% to 0.5% each, depending on the model. The rule prompts moved the hit rate by +0.8 pp
  (`risk_rules`) and +5.3 pp (`trend_news`) and the five-day excess by +0.05 pp, all within the noise (Holm p 0.23 to
  1). The rules changed how much the agents held, not how well they picked.
- No reply in the 27 runs broke the format; invalid orders (unknown symbols, sells of stocks not held) stayed between
  0% and 5% of orders under every prompt.

The single-factor grid follows (every variant below, four seeds, one model) and asks the next questions: which of the
four rules carries the effect, and whether a plain cash floor (`cash_30`, `cash_50`) does as well.

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
| `risk_rules` | risk rules | Four rules: at most 25% of equity per stock; at least 30% cash when the regime is UNCERTAIN or RISK_OFF; sell at 8% below the purchase price; prefer stocks above their 200-day average |
| `cap_25` | one rule | Only the 25% cap per stock |
| `regime_cash` | one rule | Only the 30% cash when the regime is UNCERTAIN or RISK_OFF |
| `stop_loss` | one rule | Only the sell at 8% below the purchase price |
| `above_200` | one rule | Only the preference for stocks above their 200-day average |
| `cash_30` | cash floor | At least 30% in cash at all times, nothing else |
| `cash_50` | cash floor | At least 50% in cash at all times, nothing else |
| `trend_news` | decision rule | Buy only when the trend and the news are both positive; sell when the trend breaks or confirmed bad news arrives; otherwise hold; the judgement goes in each order's reason |
| `reason_first` | reasoning | A `reasoning` field of 3 to 5 short sentences comes before the orders |
| `focus_news` | focus | Decide mainly from the news (confirmation, recency, red flags); use prices to time entries |
| `focus_trend` | focus | Decide mainly from returns, moving averages, RSI and the distance from the high; use news to avoid red flags |
| `few_shot` | examples | Three made-up examples: a buy, a sell on a red flag, a hold |
| `format_json` | reply format | Any JSON object is accepted (JSON mode); the schema is only described in the prompt |
| `format_text` | reply format | Nothing enforced: no JSON mode and no schema, only the prompt's own sentence about the reply |
| `news_0` | headlines | No headlines; the news score, confirmation counts and red flags stay |
| `news_6` | headlines | Six headlines per stock instead of three |
| `daily` | frequency | A decision every trading day instead of every fifth |

The four rules of `risk_rules` and the rule of `trend_news` are word for word the ones of the pilot above, so those
runs count as seed 0 of the same variants (same SHA-256). `cap_25`, `regime_cash`, `stop_loss` and `above_200` state
one of the four rules alone in the same words; `cash_30` and `cash_50` only change how much stays in cash, which tells
whether the rules do more than hold less stock.

`riskaware research prompts list` prints the table with each variant's SHA-256, and `--show <name>` prints the full
text. The set is versioned (`SET_VERSION`): a changed text becomes a new version, never an edit, so a published number
keeps pointing at the same prompt. The examples in `few_shot` use made-up stocks (S81 to S97) so that they cannot be
mistaken for the blind aliases of real stocks (S01 to S64).

## What stays the same

Within a lab every run uses the same model, temperature, reply cap, universe, number of positions, cash, slippage and
blind mode. A run is one variant on one period with one seed. The seed fixes which alias each stock gets in blind
mode, so two variants on the same period and seed see the same market under the same names; with `--api-seed` it is
also sent as the API's sampling seed (OpenAI-compatible servers and Ollama take one). Without it, as in the results
above, the model samples freely at the lab's temperature, and the seeds are independent repeats that show how much
the same prompt varies from run to run.

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
multiplies the smallest p-value by the number of variants. With twenty variants against the baseline, nine pairs
(three periods, three seeds) can never pass: 20 × 2/512 is 0.078. That is why the default is four seeds: twelve
pairs per variant, where a difference that points the same way in eleven or twelve of them can pass. The report says
so when a lab is too small to show anything. A prompt effect that only appears in some periods or some seeds is not
one to rely on.

## Where a difference comes from

A prompt can earn more by holding more or less stock, or by holding other stocks. The report splits each paired
return difference into these parts. Every day the backtest's return is r = e·s + (1 - e)·c, where e is the share of
equity in stocks at the previous close, s the return of the stock part and c what cash earns (the T-bill ETF BIL).
Between a variant v and the baseline b:

r_v - r_b = (e_v - e_b)(s_b - c) + e_v (s_v - s_b)

The first term is the **exposure** effect: the baseline's own stocks, held at the variant's exposure. It splits into
the **level** (the average exposure difference times the baseline's stock return over cash) and the **timing** (being
in or out on the right days). The second term is **selection**: the variant's stocks against the baseline's. The
parts are sums of daily returns, so they add up to the summed daily return difference, which is close to the
difference in total return when returns are moderate. What is left (trades at the open of a decision day) is shown
as "other". When a run holds almost no stock (less than 5% of equity), its stock return is undefined; the
equal-weight universe stands in for the baseline's and the baseline's for the variant's.

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

Several labs, one model each, make the website's prompt board, with a comparison across the models:

```bash
riskaware research prompts board lab-luna lab-haiku --site site/assets/data/prompts.json
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

`riskaware research prompts board <lab> [<lab> ...] --site site/assets/data/prompts.json` reports every lab (one model
each) and writes the prompt board for the website (format `rta-prompt-board-1`):

- `generated_utc`, `set_version`, `baseline`, `primary_metric` (`sharpe`), `method` (`en`, `zh`: how to read it);
- `boards`: one per lab, with
  - `model`, `lab_note`, `settings` (universe, symbols, max_positions, temperature, blind, news, periods, seeds,
    api_seed);
  - `benchmarks`: per period, the window and SPY's and the equal-weight universe's return, Sharpe and drawdown;
  - `noise`: the mean Sharpe gap between two seeds of the baseline and the standard deviation by period (null with
    one seed); `power`: whether the lab has enough pairs to pass the correction at all;
  - `variants`: the baseline first, then the others, each with `name`, `factor`, `about` (`en`, `zh`), the two
    SHA-256 hashes, `runs`, `settings`, `metrics` (`mean` and `by_period` of sharpe, total_return, max_drawdown,
    excess_vs_spy, avg_exposure, turnover, format_error_rate, invalid_order_rate), `tokens_per_step`, `vs_baseline`
    (null for the baseline; per metric `diff`, `ci95`, `p`, `p_holm`, `q_bh`, `pairs`, `wins`, `losses`,
    `by_period`, `verdict`) and `attribution` (`total`, `exposure_level`, `exposure_timing`, `selection`,
    `residual`, the mean exposure of the variant and the baseline, and the same by period);
  - `cost`: calls, input and output tokens, list price;
- `across_models` (null with one lab): the variants every lab has, compared with the baseline over all models,
  periods and seeds at once, with `vs_baseline` and `attribution` as above;
- `notes` (`en`, `zh`, or null): what a reader should know, such as a model left out and why (`--note`, `--note-zh`).

Fractions are fractions (0.123 is 12.3%); Sharpe ratios are annualised.

## Limits

- One model per lab. A prompt that helps one model can hurt another; run the lab again with your model before
  relying on a result.
- Blind mode lowers the chance that a model recognises a stock or a date; it cannot rule it out.
- A gateway or a local server can cut a long prompt without saying so. Every call records the prompt's length and
  the tokens the model read, and the report flags a run whose model read far fewer tokens than its prompts hold.
- The cost model is the research kit's default (5 bps of slippage, no commission). Turnover is reported so that a
  variant that only wins by trading more can be spotted.
- Three periods are three markets, not a sample of all markets. A result that holds in all three is stronger than
  one that holds in one, and still not a law.
