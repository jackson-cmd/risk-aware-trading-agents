# Strategy lab: describe a strategy, the AI writes and tests it

Tell Claude, Codex or Gemini what you want in plain words. Through MCP it reads `strategy_guide`, writes the strategy as a Python class, and runs it with `backtest_code`. The code is checked for look-ahead and side effects before it runs, then it is backtested in a separate process, and the AI gets the results, or the problems to fix with their line numbers.

## Set up

```bash
pip install "risk-aware-trading-agents[mcp]"
riskaware mcp-setup claude-desktop --apply   # or: claude-code, codex (your ChatGPT login), gemini, antigravity
```

Then ask, for example:

> Write a strategy that buys two-day pullbacks in uptrends when the recent news is positive, and sells after 10 days or at +8%. Backtest it on the tech list for 2026 with news.

## What happens

1. The AI calls `strategy_guide`: the rules, what `ctx` holds (indicators, price history, positions, the market regime, point-in-time news), a template and a news example.
2. It writes one `Strategy` subclass with an `on_bar(self, ctx)` method.
3. `backtest_code` checks the code, then backtests it on the stocks you asked for (`symbols`, or a `universe` such as `megacap`, `tech` or `defensive`). It returns the metrics next to SPY and buy-and-hold, the trades, and how often the risk manager said no.
4. When something is wrong, the AI gets the line and the reason, fixes the code and runs it again.
5. `save_as="name"` keeps a strategy that works in `~/.riskaware/strategies/ai/`. It then appears in `list_strategies`, on the dashboard's Backtest and Agents pages (as `AI · name`), and `agents_create` can paper trade it every day.

Our test run of the request above, on the 18 stocks of the `tech` list from January 2 to November 11, 2026:

| | the AI's strategy | SPY buy & hold | the same code, news off |
|---|---|---|---|
| Total return | 4.0% | 12.1% | 2.8% |
| Sharpe | 0.59 | 0.77 | |
| Max drawdown | -0.7% | -8.9% | |
| Trades | 64 | | 1 |

It shows the flow, not an edge: without news the strategy almost never buys, because its entry needs a positive news score.

## What the code may not do, and why

| Refused | Why |
|---|---|
| Dates in the code: `date(2026, 4, 9)`, `"2026-04-09"`, `.year`, `today()`, `now()` | A rule tied to a date or a year encodes what you know happened then. `ctx.day.month` and `ctx.day.weekday()` are fine. |
| Stock tickers in the code: `ctx.row("NVDA")` | The stocks come from the request. A model that picks the stocks it knows went up has looked ahead. Index and sector ETFs (SPY, QQQ, XLK, ...) are fine as market gauges. |
| Imports outside a short list | Allowed: math, statistics, random, numpy, pandas, collections, itertools, functools, dataclasses, typing, enum, decimal, fractions, heapq, bisect, datetime, and from riskaware.strategy, riskaware.types and riskaware.indicators the names a strategy needs. |
| Files, network, processes; `open`, `eval`, `exec`, `getattr`; private (`._x`) and double-underscore attributes of other objects | A strategy needs none of them, and they are the ways to reach the future (the news archive, later prices) or to touch your machine. State in `self` is fine. |

Comparing the month or the day with fixed values (`ctx.day.month == 4`) is allowed but reported as a warning, because a rule that fires on chosen days can also encode what happened on them.

## How it runs

- In a separate Python process whose environment has no API keys, with a time limit (10 minutes by default) and, on macOS and Linux, a CPU limit.
- Prices are loaded first. While the strategy's own code runs, an audit hook refuses network connections, subprocesses, file writes and reads outside Python's own files, and imports go through the allowlist again.
- Inside the backtest the strategy sees what any strategy sees: each day's indicators and past prices as copies, and news published before that day's close (see [how the backtest avoids look-ahead](news-history.md#how-the-backtest-avoids-look-ahead)).
- Errors come back with the line in the strategy; `print()` output comes back too.

These checks stop mistakes and casual misuse; they are not a sandbox for hostile code. Read a strategy before you trust it with money, and run code you would not run yourself in a container.

## Without MCP

Code from a chat on the web works too:

```bash
riskaware lab my_strategy.py --preset tech --start 2026-01-02          # check, then backtest with news
riskaware lab my_strategy.py --check-only                              # only the checks
riskaware lab my_strategy.py --symbols AAPL,MSFT,COST --save my_dips   # keep it for agents and the dashboard
```

## Read results with care

- Results on the period you tuned on are optimistic. After a few rounds, confirm once on a period you did not tune on, for example tune on January to June and test July to November.
- A model trained on text from the test period knows how it ended. Take the rules from your request, not from what the model remembers about the period; the most honest test is a period after the model's training data.
- The built-in lists are today's large caps, so every backtest on them carries survivorship bias.
