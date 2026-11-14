# Transaction costs: fixed slippage or a realistic model

[中文](zh/costs.md)

Every backtest in the package fills its orders at the next trading day's open. The cost model decides what a fill pays on top of that open:

- **`fixed`** (the default): 5 bps on every fill at the open. Every published number uses it, and every one of them stays exactly as it was.
- **`realistic`**: the same 5 bps, plus a tenth of the overnight gap, plus market impact that grows with the square root of the order's share of daily volume; and no order fills more than 1% of the stock's 20-day average volume.

With a $100,000 account trading the 56 megacap stocks, realistic costs come to 13 to 17 bps per fill instead of 5. That is enough to halve the return of a rule that trades every few days and, in 2026, to drop it from above SPY to below it. Rules that trade a few dozen times a year hardly change. A benchmark that charges a flat 5 bps flatters strategies and agents that trade often; run them under both models.

- [Turn it on](#turn-it-on)
- [The model](#the-model)
- [Nothing from after the open](#nothing-from-after-the-open)
- [What the records show](#what-the-records-show)
- [Five swing strategies in three years](#five-swing-strategies-in-three-years)
- [Bigger accounts](#bigger-accounts)
- [What it leaves out](#what-it-leaves-out)

## Turn it on

```bash
riskaware backtest --costs realistic                                  # any backtest
riskaware research run --strategy swing_rsi2 --costs realistic        # one experiment on a frozen period
riskaware research baselines --period 2020 --costs realistic          # the five swing strategies
```

In Python, `Experiment(strategy="swing_rsi2", period="2022", costs="realistic")` for `run_experiment`, and the same `Experiment` for an LLM policy in `run_policy`. Through MCP (Claude or any other client), `run_experiment(..., costs="realistic")`. A config file sets the model for `riskaware backtest`, and only there can the coefficients change:

```yaml
backtest:
  cost_model: realistic     # fixed (default) or realistic
  slippage_bps: 5           # both models
  gap_cost: 0.1             # realistic: share of the overnight gap
  impact_coef: 1.0          # realistic: k in the square-root impact
  max_participation: 0.01   # realistic: most of the 20-day average volume one order may fill
```

Research experiments always use these defaults, so two experiments run with `costs="realistic"` can be compared.

## The model

A fill at the open of day t pays, in basis points of the open (above it for a buy, below it for a sell):

```
cost = slippage_bps                                5 bps
     + gap_cost × |open(t) / close(t−1) − 1|       a tenth of the overnight gap
     + impact_coef × σ × √(shares / ADV20)         square-root market impact
```

σ is the daily volatility of the 20 daily returns up to the close of t−1 (the annualized `vol20` of day t−1 divided by √252). ADV20 is the average volume of the 20 trading days before t. An order for more than `max_participation` × ADV20 shares is cut to that size, the rest is not filled, and the backtest counts the order under `volume cap` with its other refusals. Commission is a separate setting (`commission_per_share`, zero by default).

| Setting | Default | Why |
|---|---|---|
| `slippage_bps` | 5 bps | Half the bid-ask spread of a large-cap stock plus a little slippage; spreads are widest in the first minutes of trading, when these orders fill. It is also what a fixed run pays, so the two models differ only by the terms below. |
| `gap_cost` | 0.1 | After a large overnight move the open is the least settled price of the day: the opening auction absorbs the night's orders and spreads stay wide until trading calms down. A market order at the open after a 3% gap pays 30 bps more than one after a quiet night, whichever way the gap went. This is a rule of thumb, not an estimate from data; set it to 0 to leave it out. |
| `impact_coef` | 1.0 | The square-root law of market impact: across markets and years of order data, buying or selling Q shares moves the price by about Y × σ × √(Q / V), where V is the daily volume and Y is of order one ([Tóth et al., Physical Review X, 2011](https://doi.org/10.1103/PhysRevX.1.021006); [Bouchaud et al., *Trades, Quotes and Prices*, 2018](https://doi.org/10.1017/9781316659335)). The default takes Y = 1. |
| `max_participation` | 1% | The square-root law describes orders worked through a trading day, while these orders fill at one moment, the open. Capping an order at 1% of a day's average volume keeps it in the range where the formula is still a fair guess. |

For scale: a $10,000 order in a stock that trades $5 billion a day is 0.0002% of its volume; at 2% daily volatility the impact is 2% × √0.000002, about 0.3 bps. A small account pays through the gap: in the runs below the gap term averaged 8 to 11 bps per fill.

## Nothing from after the open

Every input is known before the order fills:

- The gap compares the open of day t, the price the order fills at, with the close of day t−1.
- The package's `vol20` on day t already includes the close of day t, which is not known at the open, so the cost takes `vol20` from day t−1.
- `vol_avg20` is shifted by a day in the indicators: on day t it averages the volumes of days t−20 to t−1.

`tests/test_costs.py` checks this from two sides. Every fill's cost equals the formula computed on the values of the day before, and changing a day's close, range and volume after its open leaves the cost of that morning's fill unchanged (the test fails if the model reads `vol20` of the fill day). The research kit's leak check also holds: all 60 realistic runs below passed it, like their fixed twins.

## What the records show

- `trades.csv` has a `cost_bps` column in both models: what each fill paid above or below its reference price. Fills at the open show the model's cost. A limit order of the risk-aware execution that fills inside the day's range fills at its limit and shows 0; the volume cap still applies to it. Options keep their own model (half the spread on the Black-Scholes price, 200 bps by default), and expiries and assignments show 0.
- With realistic costs, the notes of a backtest give the average, median and highest cost per fill and the number of orders the volume cap cut. An experiment's summary and `metrics.json` hold the same numbers under `costs`, `manifest.json` gives the rule under `execution.costs` and `protocol.execution`, the reproduce command ends with `--costs realistic`, and the run id ends with `-realistic`. `riskaware research baselines --costs realistic` adds the average cost per fill to its table.
- A fixed run gives the same numbers as before this model existed. For the five baselines on all three periods (30 runs), `equity.csv`, `metrics.json`, `decisions.csv`, the leak-check results and the baselines tables (apart from the run ids, which carry the time of the run) are byte for byte those of the previous version; `trades.csv` gains only the `cost_bps` column, 5 on every fill at the open.

## Five swing strategies in three years

The [five swing strategies](research.md#five-swing-strategies-with-and-without-news) with the same settings as in the research kit: the 56 megacap stocks, plain execution with at most 10 equal positions, orders at the next open, $100,000. Cost is the average a fill at the open paid with realistic costs, in bps; with fixed costs every one paid 5.

#### 2026

| Strategy | News | Return, fixed | Return, realistic | Sharpe, fixed | Sharpe, realistic | Cost (bps) |
|---|---|---:|---:|---:|---:|---:|
| swing_trend | without | +21.1% | +20.5% | 1.44 | 1.39 | 13.3 |
| swing_trend | with | +19.6% | +19.0% | 1.32 | 1.27 | 13.2 |
| swing_rsi2 | without | +12.7% | +6.1% | 0.85 | 0.32 | 14.9 |
| swing_rsi2 | with | +10.8% | +4.9% | 0.75 | 0.23 | 14.6 |
| swing_breakout | without | -0.3% | -3.0% | -0.15 | -0.34 | 15.0 |
| swing_breakout | with | -3.5% | -6.5% | -0.38 | -0.60 | 15.2 |
| swing_pullback | without | +2.3% | +1.5% | -0.13 | -0.28 | 12.3 |
| swing_pullback | with | +1.7% | +1.1% | -0.26 | -0.39 | 12.0 |
| swing_news | without | +15.7% | +12.0% | 1.01 | 0.75 | 15.8 |
| swing_news | with | +4.4% | +2.0% | 0.18 | -0.05 | 14.8 |
| SPY (buy and hold) | | +12.1% | +12.1% | 0.77 | 0.77 | |
| Equal weight (buy and hold) | | +11.4% | +11.4% | 0.85 | 0.85 | |

#### 2022

| Strategy | News | Return, fixed | Return, realistic | Sharpe, fixed | Sharpe, realistic | Cost (bps) |
|---|---|---:|---:|---:|---:|---:|
| swing_trend | without | -6.0% | -7.1% | -0.48 | -0.56 | 14.3 |
| swing_trend | with | -5.0% | -5.9% | -0.40 | -0.47 | 14.2 |
| swing_rsi2 | without | -2.2% | -5.3% | -0.31 | -0.63 | 13.2 |
| swing_rsi2 | with | -5.7% | -8.6% | -0.67 | -0.98 | 13.2 |
| swing_breakout | without | -12.8% | -14.0% | -1.14 | -1.25 | 14.5 |
| swing_breakout | with | -13.7% | -15.1% | -1.23 | -1.35 | 14.4 |
| swing_pullback | without | -4.7% | -5.0% | -1.07 | -1.12 | 11.6 |
| swing_pullback | with | -4.0% | -4.2% | -0.99 | -1.03 | 11.9 |
| swing_news | without | -13.3% | -14.8% | -0.97 | -1.09 | 12.8 |
| swing_news | with | -14.1% | -16.3% | -1.18 | -1.37 | 12.9 |
| SPY (buy and hold) | | -18.6% | -18.6% | -0.80 | -0.80 | |
| Equal weight (buy and hold) | | -12.4% | -12.4% | -0.53 | -0.53 | |

#### 2020

| Strategy | News | Return, fixed | Return, realistic | Sharpe, fixed | Sharpe, realistic | Cost (bps) |
|---|---|---:|---:|---:|---:|---:|
| swing_trend | without | +36.6% | +35.7% | 1.62 | 1.58 | 17.9 |
| swing_trend | with | +68.4% | +67.4% | 2.20 | 2.18 | 17.7 |
| swing_rsi2 | without | +14.0% | +7.1% | 0.69 | 0.40 | 16.5 |
| swing_rsi2 | with | +12.7% | +6.3% | 0.69 | 0.39 | 16.4 |
| swing_breakout | without | +33.1% | +30.2% | 1.39 | 1.29 | 20.6 |
| swing_breakout | with | +30.4% | +27.6% | 1.33 | 1.23 | 20.4 |
| swing_pullback | without | +2.3% | +1.2% | 0.25 | 0.14 | 15.3 |
| swing_pullback | with | +3.3% | +2.2% | 0.37 | 0.25 | 15.4 |
| swing_news | without | +9.4% | +4.3% | 0.50 | 0.29 | 19.0 |
| swing_news | with | +28.4% | +23.4% | 1.38 | 1.18 | 15.6 |
| SPY (buy and hold) | | +17.2% | +17.2% | 0.63 | 0.63 | |
| Equal weight (buy and hold) | | +40.1% | +40.1% | 1.13 | 1.13 | |

Where the cost comes from, over every fill at the open of the ten realistic runs of each period (bps):

| Period | Fills at the open | Average cost | of which: slippage | gap | impact | Median | 95th percentile | Highest |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026 | 2,603 | 14.7 | 5.0 | 9.3 | 0.4 | 11.2 | 35.1 | 200 |
| 2022 | 2,194 | 13.3 | 5.0 | 7.8 | 0.5 | 10.7 | 29.5 | 130 |
| 2020 | 2,549 | 17.1 | 5.0 | 11.5 | 0.6 | 11.8 | 42.9 | 154 |

How to read it:

- **Turnover decides who pays.** RSI(2) closed about 300 trades in 2026; realistic costs take 6.6 points off its return (+12.7% to +6.1%), and its Sharpe falls from 0.85, above SPY's 0.77, to 0.32. The same happens in 2020 (6.9 points) and, smaller, in 2022 (3.1 points). Trend following closed 23 to 58 trades per period and loses at most 1.1 points in any of them.
- **Some conclusions change.** In 2026, three rules beat SPY's +12.1% with fixed costs (trend following, RSI(2) and the price-only twin of the news rule); with realistic costs only trend following does, and the price-only rule ends level with SPY (+12.0%). In 2022 without news, RSI(2) was the best of the five with fixed costs (−2.2%) and falls just behind pullback buying with realistic costs (−5.3% against −5.0%). Every other ordering within a period stays the same, every rule still lost less than SPY in 2022, and in 2020 the same rules beat SPY's return as before (by Sharpe ratio, RSI(2) falls from above SPY's to below it: 0.69 to 0.40, against 0.63).
- **The gap is most of the cost.** For a $100,000 account the impact term averages under 1 bp per fill, and the largest order was 0.006% of its stock's average daily volume, far below the 1% cap; the gap term averages 8 to 11 bps. 2020 was the most expensive period to trade at the open: in March a fill paid 42 bps on average, and 21 of the year's 40 most expensive fills came in that month.
- **The leak check passed** in all 60 runs, with fixed and with realistic costs.

The intervals in the [research kit's tables](research.md#five-swing-strategies-with-and-without-news) still apply: one year is short, and most of these differences between strategies are within them. What costs change reliably is the gap between frequent and rare traders.

Reproduce the tables with `riskaware research baselines --period 2026` (or `2022`, `2020`), once as it is and once with `--costs realistic`.

## Bigger accounts

The square-root term matters once orders get large. RSI(2) without news in 2026, with the same rules and more money:

| Account | Costs | Return | Sharpe | Cost per fill (bps) | Orders cut by the volume cap | Average stock exposure |
|---|---|---:|---:|---:|---:|---:|
| $100,000 | fixed | +12.7% | 0.85 | 5.0 | | 53% |
| $100,000 | realistic | +6.1% | 0.32 | 14.9 | 0 | 53% |
| $10 million | realistic | +4.0% | 0.14 | 18.6 | 0 | 54% |
| $100 million | realistic | +0.2% | -0.18 | 26.2 | 82 | 50% |
| $1 billion | realistic | +2.1% | -0.08 | 30.0 | 372 | 17% |

2022 and 2020 follow the same pattern. Across the three periods the cost per fill rises from 13 to 17 bps at $100,000 to 18 to 22 at $10 million and 25 to 29 at $100 million, where the cap starts cutting orders (82 to 231 a year). At $1 billion the cap cuts 256 to 372 orders a year, and the portfolio holds 5 to 17% in stocks on average instead of 33 to 54%. Its return then says more about cash than about the rule: in 2022, staying out of the market is what paid (−0.2% at $1 billion against −8.8% at $100 million).

Research experiments take any amount with `Experiment(cash=...)`.

## What it leaves out

- The data is daily bars: there are no quotes and no intraday prices. The 5 bps and the tenth of the gap are rules of thumb, not estimates from this data, and the coefficients were set before the runs above, not tuned on them.
- The square-root law describes orders worked through the day; one fill of up to 1% of a day's volume right at the open probably costs more than the formula says.
- Impact is paid on the fill and fades; it does not move the prices that later fills or the mark to market see.
- Limit orders that fill inside the day's range pay nothing on top of their limit in either model; with realistic costs only the volume cap applies to them.
- Orders are counted in total-return shares, which exceed the shares as traded by the dividends paid since (a few percent), so the impact term can be up to about 2% too high.
- Trading is long only, so there are no borrowing costs; commission is a separate setting and zero by default.

For papers: report both models when a strategy or an agent trades more than about once a week, and say which one each number uses (the run's manifest records it).
