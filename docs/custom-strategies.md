# Writing your own strategy

[中文](zh/custom-strategies.md)

A strategy is a Python class with one method, `on_bar(ctx)`, that returns a list of `Signal`s. It runs once per trading day: after the close in backtests, at `execution.run_time` in paper and live trading. The risk manager turns signals into orders, so the cash floor, caps, tranches and regime rules apply to your strategy too.

```bash
riskaware new-strategy my_strategy                  # template in ./my_strategy.py
riskaware backtest --strategy my_strategy.py --compare risk_aware,buy_and_hold --open
riskaware run --strategy my_strategy.py --dry-run   # preview against your account
```

## The signal

```python
Signal(symbol, Action.BUY, strength=0.7, reason="...", setups=["my_setup"])
Signal(symbol, Action.SELL, strength=0.8, fraction=0.5, reason="take half off")
Signal(symbol, Action.SELL, strength=1.0, fraction=1.0, emergency=True, reason="thesis broken")
```

- `strength` (0..1) is your conviction. Buys below the profile's `min_conviction` are ignored; higher conviction gets a larger position (60% to 100% of `max_position`, also capped by volatility).
- `fraction` is the share of the position to sell.
- Ordinary sells only happen in risk-on and neutral markets. `emergency=True` sells go through in every regime when the profile allows emergency exits.
- `details={"ignore_tranche_rules": True}` lets a buy add the next tranche without waiting for a deeper support or a winning position.

## The context

| Attribute | What it is |
|---|---|
| `ctx.day` | decision date |
| `ctx.universe` | symbols to consider |
| `ctx.row(sym)` | indicators at the close: `open high low close volume`, `ema20 ema50 ema100 ema200`, `rsi2 rsi14`, `atr14 atr_pct`, `z20`, `bb_lower bb_upper`, `macd macd_signal macd_hist`, `adx14`, `don55_high don20_low`, `rel_vol`, `high252 dd252`, `ema50_slope ema100_slope ema200_slope`, `mom21 mom126 mom252 rs126`, `vol20 vol60`, `beta120`, `down_streak below_ema50_streak` |
| `ctx.history(sym, 260)` | a DataFrame of the last bars with all indicators |
| `ctx.position(sym)` | `qty, avg_cost, price, weight, gain, days_held, tranches, days_since_entry, days_since_trim` or `None` |
| `ctx.portfolio` | `equity, cash, cash_ratio, stock_exposure, drawdown, positions` |
| `ctx.market` | `regime`, `vix`, `vix_ratio`, `fear_greed`, `breadth`, `event_risk`, `reasons`, `macro_score`, `dip_tailwind` |
| `ctx.quality(sym)` | earnings verdict (`QUALITY`, `NEUTRAL`, `BROKEN`, `UNKNOWN`, `ETF`) and a 0..100 score; live only unless you supply point-in-time data |
| `ctx.news(sym)` | cross-verified news report (`score`, `confirmed`, `red_flags_confirmed`, ...); live only |
| `ctx.days_to_earnings(sym)` | trading days to the next report, live only |
| `ctx.profile` | the active risk profile |

Nothing after `ctx.day` is visible, so backtests have no look-ahead.

## Parameters

```python
class Breakout(Strategy):
    name = "breakout"
    default_params = {"lookback_volume": 1.5}
```

```yaml
strategy:
  name: strategies/breakout.py:Breakout
  params: {lookback_volume: 2.0}
```

Unknown parameters raise an error, so a typo cannot silently fall back to a default.

## Options (backtest only)

Open an option by attaching an `OptionSpec` to a signal; close one by naming its key.

```python
from riskaware.types import OptionSpec, Side

# buy a ~0.6-delta call about 50 days out
Signal("NVDA", Action.BUY, 0.8, "pullback", option=OptionSpec("C", target_delta=0.6, dte=50))
# write a cash-secured put, or a covered call that stays above your cost
Signal("KO", Action.SELL, 0.7, "near support", option=OptionSpec("P", 0.25, 35, Side.SELL))
Signal("KO", Action.SELL, 0.7, "income", option=OptionSpec("C", 0.25, 35, Side.SELL, min_strike=pos.avg_cost))
# close any open option (long or written)
for key, op in ctx.portfolio.options.items():
    if op.pnl_pct >= 1.0 or op.dte <= 10:
        signals.append(Signal(op.underlying, Action.SELL, 1.0, "exit", option_key=key))
```

`ctx.portfolio.options` maps a key to `underlying, right, strike, expiry, contracts` (negative when written), `entry_price, mark, dte, days_held, pnl_pct` (for written options, the share of the premium kept). The risk manager picks the contract count from the profile's option limits, and option exits are allowed in every regime because options expire. See `riskaware/strategy/options_strategies.py` for two complete examples.

## Tips

- Return few, high-conviction signals; the risk manager rejects the rest and the backtest report lists why.
- Compare against `buy_and_hold` and `risk_aware` on the same dates with `--compare`.
- Try several profiles (`--profile conservative|balanced|aggressive`) and universes (`--preset etf`, `--symbols ...`).
- A strategy that only looks good on one period is probably fit to noise; test 2018–2020, 2020–2022 and 2022–today separately.
