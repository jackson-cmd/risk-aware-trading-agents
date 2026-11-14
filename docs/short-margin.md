# Short selling and margin: three execution modes for backtests and LLM agents

[中文](zh/short-margin.md)

The research kit's plain execution is long only by default: a sell only sells what you hold, a buy only spends the cash you have. Two more modes let a strategy or an LLM agent sell short and borrow, with the costs and the margin rules of a real brokerage account. All three modes run on the same frozen periods (a rising 2026, the 2022 bear market, the 2020 crash), so you can ask whether shorting and leverage help an agent when the market falls, and what they cost it when it rises.

- [The three modes](#the-three-modes)
- [Run it](#run-it)
- [How the account works](#how-the-account-works)
- [What an LLM sees and can send](#what-an-llm-sees-and-can-send)
- [Rule-based anchors](#rule-based-anchors)
- [Results: three LLMs, three modes, three periods](#results-three-llms-three-modes-three-periods)
- [Limits](#limits)

## The three modes

| | `long_only` (default) | `long_short` | `margin` |
|---|---|---|---|
| Short positions | no | yes | yes |
| Long plus short positions (gross), as a share of equity | at most 100% (cash only) | at most 100% | at most 200% (Reg T initial margin 50%) |
| Largest single short | - | 20% of equity | 40% of equity |
| Short-sale proceeds | - | held as collateral: they earn the T-bill rate, cannot pay for purchases | the same |
| Borrow fee on shorts | - | 0.5% a year of their value, charged every trading day | the same |
| Borrowed cash | never | only if losses force it | pays the BIL return plus 1.5% a year, every trading day |
| Maintenance margin | - | 25% of the longs plus 30% of the shorts | the same |
| Margin call | - | when the requirement exceeds equity at a close: every position cut by the same share at the next open | the same |
| Equity at or below zero | - | everything closed, trading stops | the same |

Every number is a parameter (`mode_params`): `max_gross`, `max_short`, `borrow_fee`, `margin_spread`, `maint_long`, `maint_short` and `cushion` (see [the margin call](#margin-calls)). The single-short cap defaults to a fifth of the gross limit, so it scales with leverage. In `long_only` nothing changes: every existing backtest, record and LLM observation stays byte for byte the same.

## Run it

```bash
riskaware research run --strategy momentum_ls --mode long_short --universe core --max-positions 9
riskaware research run --strategy trend_ls --params '{"gross": 2}' --mode margin --period 2022
riskaware research run --strategy momentum_ls --mode margin --mode-params '{"max_short": 0.25, "borrow_fee": 0.01}'
riskaware research trade --llm openai:gpt-5.6-luna --mode margin --period 2020 --blind
```

```python
from riskaware.research import Experiment, run_experiment
from riskaware.research.agent import llm_policy, run_policy

r = run_experiment(Experiment(strategy="momentum_ls", period="2022", universe="core", max_positions=9,
                              mode="long_short"))
print(r.summary()["account"])   # longs' and shorts' P&L, fees, interest, margin calls

exp = Experiment(period="2020", universe="core", max_positions=5, mode="margin")
r = run_policy(llm_policy("openai:gpt-5.6-luna", mode="margin"), exp, decide_every=5, blind=True)
```

Over MCP, `run_experiment` and `trading_session_start` take `mode` (and `run_experiment` also `mode_params`); the session's rules then describe the mode, and `trading_session_step` accepts the extra actions.

A strategy goes short with a signed `target_weight`: `Signal("NVDA", Action.SELL, 0.5, "short", target_weight=-0.1)` sells down to a short of 10% of equity (closing a long first if it holds one), and `Signal("NVDA", Action.BUY, 0.5, "cover", target_weight=0)` buys it back. A `SELL` without a target only sells a long, so long-only strategies behave the same in every mode. Short and margin modes need `execution="plain"`; the risk manager stays long only.

## How the account works

**Positions and equity.** A short is a negative quantity. Equity is cash plus the market value of every position, so a short's value counts against it; prices are total-return prices, which charges a short the dividends it owes, as a real short seller pays them to the lender.

**Orders.** The planner works in signed weights at the day's close. Orders that lower exposure (selling a long, covering a short) go first; then the ones that add to it, strongest first, as far as the gross limit, the short cap and `max_positions` (longs and shorts together) allow. A move that crosses zero becomes two orders, a full close and a new position. At the next open the backtester checks the gross limit again at the open's prices and trims an order that a gap pushed over it ("gross limit at the open" in the refusals).

**Cash.** Short-sale proceeds stay in the account as collateral. Cash, proceeds included, earns the daily return of the T-bill ETF BIL, which is the usual institutional short rebate (the T-bill rate, with the borrow fee charged separately). Free cash is cash minus the value of the shorts; when it falls below zero the account has borrowed that much, and the loan pays the BIL return plus `margin_spread`. Because purchases can only use free cash and equity, `long_short` never borrows unless losses force it.

**Fees.** Every trading day the backtester charges `borrow_fee / 252` on the shorts' value at the close and `margin_spread / 252` on the loan (the BIL part comes with the daily cash return). The record keeps both, with the T-bill interest on cash and proceeds.

<a id="margin-calls"></a>**Margin calls.** After each close the planner computes the maintenance requirement, 25% of the longs' value plus 30% of the shorts'. If it exceeds equity, every position is cut by the same share at the next open, so that the requirement plus a 10% cushion fits in equity again, and that day's signals are refused with the reason "margin call". The decision is on the close and the sale at the next open, so a gap can make it worse: in the package's tests, a 2x long in TSLA held through March 2020 got four calls and reached 9x before the sales caught up. A broker liquidates intraday; daily bars cannot.

**Wiped out.** At or below zero equity everything is closed at the next open and nothing trades again; an LLM is not asked any more.

**The record.** `metrics.json` and the summary get an `account` block (as shares of the starting cash): `pnl_long` and `pnl_short` (closed trades plus what is still open at the last close), `borrow_fees`, `margin_interest`, `cash_interest`, the average and highest gross exposure, the average long and short exposure, the margin calls with their days, and `wiped_out`. `equity.csv` adds `long`, `short` and `gross` by day (`exposure` is the net exposure, `cash_ratio` the free cash). `trades.csv` tags short sales `short` / `short_add`, covers `cover` / `cover_trim`, forced sales `margin_call` / `wiped_out`. The manifest names the mode and its rules, and the reproduce command includes `--mode`. The look-ahead checks run unchanged: the strategy check compares signals and orders, the LLM check compares observations. `riskaware research analyze` ([analysis.md](analysis.md)) reads these records too, with the longs and the shorts apart and the fees and interest reconciled to the cent.

## What an LLM sees and can send

The reply schema has two more actions outside `long_only`: `{"action": "short", "weight": 0.1}` opens or adds to a short of 10% of equity, and `{"action": "cover", "fraction": 0.5}` buys back half of one. `target` takes negative weights. In `long_only` the schema and the prompt are exactly what they were, so long-only results stay comparable with earlier runs.

The system prompt gets the mode's rules appended (`agent.RULES_LONG_SHORT`, `agent.RULES_MARGIN`): what is allowed, what it costs, and how margin calls work. The observation's portfolio gets, as shares of equity:

| Field | |
|---|---|
| `positions[].side`, `weight` | `long` or `short`; shorts have negative weights, and `gain` is the short's own gain (positive when the price fell) |
| `gross_exposure`, `net_exposure` | longs plus shorts; longs minus shorts |
| `max_gross_exposure`, `buying_power` | the gross limit, and how much of it is left |
| `max_short_weight` | the largest single short |
| `margin_used` | initial margin in use (gross / max gross) |
| `maintenance_requirement` | the maintenance requirement; a margin call comes when it passes 1 |
| `cash_share` | free cash: proceeds of short sales are not counted, below zero is borrowed |
| `borrow_fee_annual`, `margin_interest_annual` | the costs. In blind runs the margin interest says "T-bill rate + 1.5%" instead of a number, because the T-bill level would date the period |

## Rule-based anchors

Two textbook long-short strategies give each mode a rule-based reference (`riskaware.research.strategies`):

- `momentum_ls`: long the third of the universe with the best 12-1 month return, short the worst third, equal weights, rebalanced weekly; dollar neutral with `gross` (default 1.0) of equity in all.
- `trend_ls`: long every stock with a positive 12-1 month return above its 200-day average, short every one with a negative return below it, each `gross / N` of equity; net long in a rising market, net short in a falling one.

In `long_only` their shorts are refused, which leaves the long side alone.

## Results: three LLMs, three modes, three periods

**Setup.** Blind, the `core` universe (SPY, QQQ, AAPL, MSFT, NVDA, AMZN, TSLA, GOOGL, META), a decision every 5 trading days, at most 5 positions, news on (3 headlines a stock), fixed costs of 5 bps, $100,000, the whole test window of each period, one run per cell. Models: `gpt-5.6-luna`, `gemini-3.1-flash-lite` and `claude-haiku-5-5`; `llama3.2` is left out: the gateway that serves it cuts every prompt to about 2,048 tokens, while one step's prompt here is about 2,700 to 5,000 tokens (depending on the tokenizer), so it would see only the end of the stock list. Two prompts: the package's system prompt plus the mode's rules (`baseline`), and the same with four risk rules (`risk_rules`: at most 25% in a stock, 30% cash in uncertain or risk-off regimes, an 8% stop, prefer stocks above their 200-day average). That makes 3 models x 2 prompts x 3 modes x 3 periods = 54 runs: 2,610 model calls, 9.8 million input tokens and 0.44 million output tokens, about $1.16 at list prices. Every run passed the look-ahead check of its observations, and no step failed.

**Nobody used the leverage.** With 200% of equity available in `margin`, no model ever ordered a long above 100% of equity; the highest gross exposure in any LLM run was 125% (luna, `long_short`, 2020, from prices moving between decisions), and a `margin` run's average gross exposure was 47% (8% to 91%). No LLM run had a margin call. So `margin` against `long_short` measures what the margin rules in the prompt do to a model's behaviour, not what leverage does.

**The models differ in how much they short.** Gemini sent 291 short orders over its 12 runs with shorts allowed, luna 111, Haiku 12. Shorting cost little: borrow fees and margin interest were at most 0.13% of the starting cash in any run.

**Shorts helped in the bear market, hurt in the crash and rebound, and did little in the rising year.** Each model and prompt against its own `long_only` run:

| Period | Mode | Pairs | Return vs long_only | Better | Sharpe vs long_only | Drawdown vs long_only | Shorts made | Gross |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 2026 | long_short | 6 | -1.7% | 2 of 6 | -0.23 | +0.2% | -2.1% | 55.5% |
| 2026 | margin | 6 | +0.8% | 4 of 6 | +0.05 | +0.1% | -0.2% | 65.9% |
| 2022 | long_short | 6 | +9.0% | 4 of 6 | +0.69 | +4.5% | +4.2% | 29.6% |
| 2022 | margin | 6 | +9.9% | 4 of 6 | +0.71 | +2.6% | +6.9% | 29.8% |
| 2020 | long_short | 6 | -14.4% | 1 of 6 | -0.72 | -0.6% | -9.4% | 39.9% |
| 2020 | margin | 6 | +4.4% | 2 of 6 | +0.28 | +2.1% | -4.6% | 44.7% |

In 2022 allowing shorts lifted the return by 9 to 10 points and the Sharpe by 0.7 (4 of 6 pairs better); 4 to 7 points of it came from the shorts themselves, the rest from holding less net long. In 2020 the shorts lost 9% of the starting cash on average in the rebound, and `long_short` trailed long only by 14 points (1 of 6 better). With 6 pairs a sign test cannot tell any of these from chance (4 of 6 gives p = 0.69, 1 of 6 p = 0.22): they are directions to test with more runs, and the rule-based anchors below point the same way.

Averages over the 6 runs of each cell (3 models x 2 prompts):

| Period | Mode | Return | Sharpe | Max drawdown |
|---|---|---:|---:|---:|
| 2026 | long_only | -0.9% | -0.29 | -11.3% |
| 2026 | long_short | -2.6% | -0.51 | -11.1% |
| 2026 | margin | -0.1% | -0.24 | -11.2% |
| 2022 | long_only | -20.8% | -2.04 | -22.1% |
| 2022 | long_short | -11.8% | -1.35 | -17.5% |
| 2022 | margin | -10.9% | -1.33 | -19.4% |
| 2020 | long_only | +17.9% | 1.03 | -14.3% |
| 2020 | long_short | +3.5% | 0.31 | -14.9% |
| 2020 | margin | +22.3% | 1.31 | -12.2% |

### What the decisions show

`riskaware research analyze` ([docs/analysis.md](analysis.md)) scores every order against SPY over the next 5 and 20 trading days (a short is credited with how much its stock trailed SPY) and splits each run's excess return into factor exposures and selection. It rebuilds every account day by day on its own, and its long and short P&L match the backtester's to within rounding. On the 54 runs:

**The shorts were well chosen in 2022 and badly timed in 2020.** Pooled over the three models and both prompts, a stock shorted in 2022 trailed SPY by 1.1% over the next 5 trading days and by 2.4% over 20; one shorted in 2020 beat SPY by 2.2% and 11.4%, because the shorts went in after the crash and the stocks rallied; in 2026 there was no edge. The t-statistics pool the runs as if they were independent, but they trade the same days, so they overstate the evidence somewhat.

| Period | Shorts | Next 5 days | t | Hit rate | Next 20 days | t |
|---|---:|---:|---:|---:|---:|---:|
| 2026 | 73 | +0.1% | +0.2 | 44% | -0.6% | -0.4 |
| 2022 | 195 | +1.1% | +3.2 | 55% | +2.4% | +2.5 |
| 2020 | 99 | -2.2% | -3.1 | 33% | -11.4% | -5.5 |

By model (short sales that filled, so fewer than the orders sent), Gemini's 251 shorts avoided 0.4% over 5 days (t = 1.1), luna's 104 lost 0.6% (t = -1.2) and Haiku's 12 lost 2.1% (t = -2.1). No model's buys beat SPY: luna -0.4% (t = -2.2), Gemini -0.3%, Haiku +0.1%.

**Shorting changed the exposure more than the selection.** Allowing shorts lowered the market beta in 2026 (0.73 to 0.43 in `long_short`) and in 2022 (0.22 to 0.02 and 0.06); in 2022 most of the short modes' gain came from that lower exposure. The selection part, the alpha left after the market, size, growth and momentum factors, stayed negative in every cell but `margin` in 2020. Means of the 6 runs of each cell:

| Period | Mode | Market beta | From factor exposure | Selection (alpha) |
|---|---|---:|---:|---:|
| 2026 | long_only | 0.73 | +8.5%/yr | -12.2%/yr |
| 2026 | long_short | 0.43 | +5.4%/yr | -11.5%/yr |
| 2026 | margin | 0.73 | +8.1%/yr | -11.1%/yr |
| 2022 | long_only | 0.22 | -11.4%/yr | -13.8%/yr |
| 2022 | long_short | 0.02 | -2.2%/yr | -11.2%/yr |
| 2022 | margin | 0.06 | -3.5%/yr | -9.4%/yr |
| 2020 | long_only | 0.25 | +17.7%/yr | -0.2%/yr |
| 2020 | long_short | 0.25 | +12.9%/yr | -8.8%/yr |
| 2020 | margin | 0.23 | +14.5%/yr | +6.1%/yr |

### Every run

The `baseline` prompt; SPY and the equal-weight universe for each period above its table. "Net" and "Gross" are average exposures; "Longs made" and "Shorts made" are their P&L as a share of the starting cash (closed trades plus what was open at the end); the rest of the return is the T-bill interest on cash and short-sale proceeds, minus fees and interest.

**2026**. SPY +12.1% (Sharpe 0.77, max drawdown -8.9%); equal-weight core +6.3% (Sharpe 0.28, max drawdown -15.3%).

| Model | Mode | Return | Sharpe | Max drawdown | Net | Gross | Longs made | Shorts made | Fees and interest |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| gpt-5.6-luna | long_only | -6.6% | -0.69 | -15.0% | 82.2% | 82.2% | - | - | - |
| gpt-5.6-luna | long_short | -0.3% | -0.26 | -9.0% | 58.5% | 61.1% | -3.3% | +1.8% | -0.01% |
| gpt-5.6-luna | margin | -3.6% | -0.67 | -8.8% | 54.6% | 55.6% | -4.7% | -0.2% | 0.00% |
| gemini-3.1-flash-lite | long_only | +2.7% | 0.07 | -14.8% | 89.9% | 89.9% | - | - | - |
| gemini-3.1-flash-lite | long_short | -1.7% | -0.30 | -13.1% | 27.7% | 63.7% | +1.2% | -5.0% | -0.07% |
| gemini-3.1-flash-lite | margin | -10.2% | -0.78 | -16.8% | 73.8% | 91.1% | -6.1% | -4.8% | -0.08% |
| claude-haiku-5-5 | long_only | +0.4% | -0.36 | -5.3% | 26.6% | 26.6% | - | - | - |
| claude-haiku-5-5 | long_short | -0.7% | -0.52 | -7.0% | 33.1% | 33.5% | -1.9% | -0.8% | 0.00% |
| claude-haiku-5-5 | margin | +1.6% | -0.04 | -9.6% | 60.0% | 60.0% | +0.3% | +0.0% | 0.00% |

**2022**. SPY -18.6% (Sharpe -0.80, max drawdown -24.5%); equal-weight core -42.5% (Sharpe -1.34, max drawdown -44.2%).

| Model | Mode | Return | Sharpe | Max drawdown | Net | Gross | Longs made | Shorts made | Fees and interest |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| gpt-5.6-luna | long_only | -35.6% | -2.30 | -36.3% | 54.7% | 54.7% | - | - | - |
| gpt-5.6-luna | long_short | -24.0% | -1.85 | -28.4% | 29.5% | 38.2% | -24.3% | -0.6% | -0.02% |
| gpt-5.6-luna | margin | -24.3% | -2.60 | -25.3% | 25.4% | 26.3% | -25.7% | +0.5% | 0.00% |
| gemini-3.1-flash-lite | long_only | -36.4% | -2.03 | -38.3% | 46.7% | 46.7% | - | - | - |
| gemini-3.1-flash-lite | long_short | -14.4% | -0.75 | -21.6% | -2.0% | 48.4% | -26.9% | +11.3% | -0.11% |
| gemini-3.1-flash-lite | margin | +15.1% | 0.68 | -20.1% | -5.1% | 46.2% | -15.3% | +28.9% | -0.12% |
| claude-haiku-5-5 | long_only | -3.9% | -1.15 | -6.5% | 4.7% | 4.7% | - | - | - |
| claude-haiku-5-5 | long_short | -7.1% | -1.30 | -7.7% | 16.7% | 18.5% | -10.7% | +2.4% | 0.00% |
| claude-haiku-5-5 | margin | -22.0% | -2.10 | -23.4% | 29.2% | 30.6% | -24.2% | +1.5% | 0.00% |

**2020**. SPY +17.2% (Sharpe 0.63, max drawdown -33.7%); equal-weight core +127.7% (Sharpe 2.08, max drawdown -33.5%).

| Model | Mode | Return | Sharpe | Max drawdown | Net | Gross | Longs made | Shorts made | Fees and interest |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| gpt-5.6-luna | long_only | +27.3% | 1.23 | -15.4% | 62.6% | 62.6% | - | - | - |
| gpt-5.6-luna | long_short | -4.1% | -0.22 | -20.8% | 31.7% | 39.7% | +12.2% | -16.6% | -0.02% |
| gpt-5.6-luna | margin | +19.2% | 1.62 | -9.0% | 33.5% | 34.7% | +18.1% | +0.9% | 0.00% |
| gemini-3.1-flash-lite | long_only | +16.5% | 0.73 | -17.3% | 66.7% | 66.7% | - | - | - |
| gemini-3.1-flash-lite | long_short | -7.3% | -0.14 | -19.9% | 39.8% | 61.8% | +5.1% | -12.5% | -0.05% |
| gemini-3.1-flash-lite | margin | +10.5% | 0.51 | -14.7% | 53.7% | 71.9% | +24.5% | -14.0% | -0.06% |
| claude-haiku-5-5 | long_only | +10.1% | 0.88 | -8.3% | 15.9% | 15.9% | - | - | - |
| claude-haiku-5-5 | long_short | -1.8% | -0.02 | -17.8% | 24.5% | 29.8% | +4.4% | -6.4% | -0.01% |
| claude-haiku-5-5 | margin | +45.8% | 2.08 | -12.4% | 49.6% | 49.6% | +45.5% | +0.0% | 0.00% |

<details>
<summary>The same with the risk_rules prompt</summary>

**2026**. SPY +12.1% (Sharpe 0.77, max drawdown -8.9%); equal-weight core +6.3% (Sharpe 0.28, max drawdown -15.3%).

| Model | Mode | Return | Sharpe | Max drawdown | Net | Gross | Longs made | Shorts made | Fees and interest |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| gpt-5.6-luna | long_only | -3.1% | -0.52 | -13.9% | 64.9% | 64.9% | - | - | - |
| gpt-5.6-luna | long_short | +1.2% | -0.16 | -9.0% | 52.8% | 54.0% | -0.7% | +0.5% | 0.00% |
| gpt-5.6-luna | margin | -1.9% | -0.51 | -9.6% | 57.9% | 60.0% | -2.9% | -0.2% | 0.00% |
| gemini-3.1-flash-lite | long_only | -3.1% | -0.37 | -11.8% | 81.3% | 81.3% | - | - | - |
| gemini-3.1-flash-lite | long_short | -10.7% | -1.20 | -18.2% | 53.7% | 73.7% | -3.2% | -8.8% | -0.04% |
| gemini-3.1-flash-lite | margin | +15.0% | 0.91 | -12.1% | 76.1% | 76.9% | +10.2% | +4.0% | -0.04% |
| claude-haiku-5-5 | long_only | +4.0% | 0.15 | -6.9% | 60.9% | 60.9% | - | - | - |
| claude-haiku-5-5 | long_short | -3.7% | -0.63 | -9.9% | 45.7% | 47.1% | -5.1% | -0.3% | 0.00% |
| claude-haiku-5-5 | margin | -1.6% | -0.34 | -10.6% | 51.7% | 51.7% | -3.0% | +0.0% | 0.00% |

**2022**. SPY -18.6% (Sharpe -0.80, max drawdown -24.5%); equal-weight core -42.5% (Sharpe -1.34, max drawdown -44.2%).

| Model | Mode | Return | Sharpe | Max drawdown | Net | Gross | Longs made | Shorts made | Fees and interest |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| gpt-5.6-luna | long_only | -10.1% | -1.44 | -11.5% | 14.7% | 14.7% | - | - | - |
| gpt-5.6-luna | long_short | -16.8% | -1.31 | -17.5% | 5.4% | 21.1% | -13.8% | -4.1% | -0.03% |
| gpt-5.6-luna | margin | -16.0% | -1.51 | -20.4% | 5.2% | 21.5% | -16.4% | -0.8% | -0.03% |
| gemini-3.1-flash-lite | long_only | -23.0% | -2.47 | -23.1% | 23.4% | 23.4% | - | - | - |
| gemini-3.1-flash-lite | long_short | +1.1% | 0.09 | -20.0% | -13.4% | 45.5% | -16.5% | +15.9% | -0.13% |
| gemini-3.1-flash-lite | margin | -9.3% | -0.42 | -17.6% | -1.1% | 46.0% | -22.2% | +11.3% | -0.10% |
| claude-haiku-5-5 | long_only | -15.8% | -2.87 | -16.5% | 16.1% | 16.1% | - | - | - |
| claude-haiku-5-5 | long_short | -9.5% | -2.98 | -10.1% | 6.1% | 6.1% | -10.8% | +0.0% | 0.00% |
| claude-haiku-5-5 | margin | -9.1% | -2.05 | -9.6% | 8.1% | 8.1% | -10.3% | +0.0% | 0.00% |

**2020**. SPY +17.2% (Sharpe 0.63, max drawdown -33.7%); equal-weight core +127.7% (Sharpe 2.08, max drawdown -33.5%).

| Model | Mode | Return | Sharpe | Max drawdown | Net | Gross | Longs made | Shorts made | Fees and interest |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| gpt-5.6-luna | long_only | +8.4% | 0.86 | -10.1% | 28.9% | 28.9% | - | - | - |
| gpt-5.6-luna | long_short | -6.7% | -0.49 | -14.5% | 23.7% | 30.7% | +2.1% | -9.0% | -0.02% |
| gpt-5.6-luna | margin | +3.5% | 0.31 | -15.3% | 22.8% | 29.6% | +11.3% | -8.0% | -0.02% |
| gemini-3.1-flash-lite | long_only | +32.8% | 1.85 | -11.1% | 49.0% | 49.0% | - | - | - |
| gemini-3.1-flash-lite | long_short | +13.7% | 0.82 | -7.6% | 36.9% | 51.4% | +25.3% | -11.9% | -0.04% |
| gemini-3.1-flash-lite | margin | +30.9% | 1.76 | -10.3% | 51.5% | 53.8% | +37.5% | -6.8% | -0.01% |
| claude-haiku-5-5 | long_only | +12.6% | 0.67 | -23.6% | 33.5% | 33.5% | - | - | - |
| claude-haiku-5-5 | long_short | +27.3% | 1.94 | -9.1% | 25.8% | 26.0% | +27.3% | -0.2% | 0.00% |
| claude-haiku-5-5 | margin | +24.1% | 1.60 | -11.5% | 28.4% | 28.4% | +23.9% | +0.0% | 0.00% |

</details>

### The anchors' results

`momentum_ls` and `trend_ls` on the same universe and periods, at most 9 positions, gross 1 in `long_only` and `long_short` and gross 2 in `margin` (in `long_only` only their long side trades). The pattern matches the LLMs': the dollar-neutral momentum book made money in 2022 while its long side lost 19%, and gave up more than half of the 2020 rally to its shorts. Leverage doubled both legs: momentum's 2022 legs went from -19% and +21% to -37% and +40%, which left its total about where it was, while trend following's loss in 2022 doubled to -34% and its 2020 gain grew to +146%.

| Period | Strategy | Mode | Return | Sharpe | Max drawdown | Longs made | Shorts made | Fees and interest |
|---|---|---|---:|---:|---:|---:|---:|---:|
| 2026 | momentum_ls (gross 1) | long_only | +4.7% | 0.22 | -8.1% | - | - | - |
| 2026 | trend_ls (gross 1) | long_only | +0.6% | -0.15 | -9.1% | - | - | - |
| 2026 | momentum_ls (gross 1) | long_short | -2.3% | -0.44 | -13.4% | +3.4% | -8.6% | -0.22% |
| 2026 | trend_ls (gross 1) | long_short | -8.9% | -1.07 | -17.4% | -0.1% | -10.1% | -0.04% |
| 2026 | momentum_ls (gross 2) | margin | -8.0% | -0.41 | -26.1% | +7.2% | -17.9% | -0.49% |
| 2026 | trend_ls (gross 2) | margin | -21.1% | -1.09 | -33.8% | -0.4% | -19.6% | -1.77% |
| 2022 | momentum_ls (gross 1) | long_only | -18.1% | -0.93 | -21.1% | - | - | - |
| 2022 | trend_ls (gross 1) | long_only | -26.8% | -2.49 | -27.3% | - | - | - |
| 2022 | momentum_ls (gross 1) | long_short | +3.5% | 0.22 | -9.5% | -18.6% | +21.0% | -0.24% |
| 2022 | trend_ls (gross 1) | long_short | -15.5% | -0.57 | -32.0% | -27.6% | +10.4% | -0.20% |
| 2022 | momentum_ls (gross 2) | margin | +4.0% | 0.23 | -18.2% | -36.9% | +40.0% | -0.52% |
| 2022 | trend_ls (gross 2) | margin | -34.3% | -0.57 | -55.8% | -48.7% | +12.8% | -0.41% |
| 2020 | momentum_ls (gross 1) | long_only | +68.1% | 2.10 | -23.2% | - | - | - |
| 2020 | trend_ls (gross 1) | long_only | +69.1% | 1.82 | -26.3% | - | - | - |
| 2020 | momentum_ls (gross 1) | long_short | +31.8% | 1.70 | -11.3% | +59.5% | -27.8% | -0.28% |
| 2020 | trend_ls (gross 1) | long_short | +63.5% | 1.72 | -26.4% | +67.0% | -3.5% | -0.01% |
| 2020 | momentum_ls (gross 2) | margin | +68.9% | 1.71 | -21.8% | +128.0% | -58.8% | -0.64% |
| 2020 | trend_ls (gross 2) | margin | +145.6% | 1.75 | -46.4% | +154.8% | -6.7% | -2.41% |

### Reproduce

The anchors: `riskaware research run --strategy momentum_ls --params '{"gross": 2}' --mode margin --period 2022 --universe core --max-positions 9` (each record's manifest has its command). The LLM runs: `run_policy(llm_policy(model, mode=mode), Experiment(period=..., universe="core", max_positions=5, mode=mode), decide_every=5, blind=True)`; each record keeps the model's replies, so its look-ahead check replays them without calling the model.

## Limits

- **Daily bars.** A margin call is decided at a close and filled at the next open. Intraday liquidations, and the deeper losses a gap can still cause, are not modelled beyond that.
- **One borrow fee for every stock.** The default universe is large caps that are easy to borrow; hard-to-borrow fees, recalls, failed locates and short squeezes are not modelled. Raise `borrow_fee` to test sensitivity.
- **Short rebate.** Proceeds earn the T-bill rate, as institutions get; many retail accounts are paid less or nothing on them.
- **One run per cell.** LLM replies vary from run to run (temperature 0.2), and one period is short; read differences between single runs as anecdotes unless they repeat across models and periods.
