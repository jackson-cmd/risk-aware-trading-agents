# 编写自己的策略

[English](../custom-strategies.md)

策略就是一个 Python 类，只需要实现一个方法 `on_bar(ctx)`，返回一组 `Signal`（信号）。它每个交易日运行一次：回测时在收盘后运行，模拟盘和实盘在 `execution.run_time` 运行。信号会交给风控变成订单，所以现金底线、各类上限、分批建仓和市场状态规则对你的策略同样有效。

```bash
riskaware new-strategy my_strategy                  # 在当前目录生成 my_strategy.py 模板
riskaware backtest --strategy my_strategy.py --compare risk_aware,buy_and_hold --open
riskaware run --strategy my_strategy.py --dry-run   # 用你的账户预演，不下单
```

## 信号

```python
Signal(symbol, Action.BUY, strength=0.7, reason="...", setups=["my_setup"])
Signal(symbol, Action.SELL, strength=0.8, fraction=0.5, reason="先卖一半")
Signal(symbol, Action.SELL, strength=1.0, fraction=1.0, emergency=True, reason="逻辑被破坏")
```

- `strength`（0 到 1）是你的信心分。低于档位里 `min_conviction` 的买入会被忽略；信心越高仓位越大（`max_position` 的 60% 到 100%，同时受波动率限制）。
- `fraction` 是要卖掉的仓位比例。
- 普通卖出只在偏多和中性状态下执行。`emergency=True` 的紧急卖出，只要档位允许，任何状态下都会执行。
- `details={"ignore_tranche_rules": True}` 表示加仓时不必等更深的支撑或持仓盈利。

## 上下文 ctx

| 属性 | 含义 |
|---|---|
| `ctx.day` | 决策日期 |
| `ctx.universe` | 本次考虑的股票 |
| `ctx.row(sym)` | 收盘时的各项指标：`open high low close volume`、`ema20 ema50 ema100 ema200`、`rsi2 rsi14`、`atr14 atr_pct`、`z20`、`bb_lower bb_upper`、`macd macd_signal macd_hist`、`adx14`、`don55_high don20_low`、`rel_vol`、`high252 dd252`、`ema50_slope ema100_slope ema200_slope`、`mom21 mom126 mom252 rs126`、`vol20 vol60`、`beta120`、`down_streak below_ema50_streak` |
| `ctx.history(sym, 260)` | 最近若干根 K 线及全部指标的 DataFrame |
| `ctx.position(sym)` | 持仓信息：`qty, avg_cost, price, weight, gain, days_held, tranches, days_since_entry, days_since_trim`，没有持仓时为 `None` |
| `ctx.portfolio` | 账户：`equity, cash, cash_ratio, stock_exposure, drawdown, positions` |
| `ctx.market` | 市场：`regime`（状态）、`vix`、`vix_ratio`、`fear_greed`、`breadth`、`event_risk`、`reasons`、`macro_score`、`dip_tailwind` |
| `ctx.quality(sym)` | 财报结论（`QUALITY` 优质、`NEUTRAL`、`BROKEN` 变差、`UNKNOWN`、`ETF`）和 0 到 100 的分数；只在实盘可用，除非你提供历史时点数据 |
| `ctx.news(sym)` | 交叉验证后的新闻报告（`score`、`confirmed`、`red_flags_confirmed`、`clusters` 等）；实盘可用，回测加 `--news` 时也可用（[news-history.md](news-history.md)） |
| `ctx.days_to_earnings(sym)` | 距离下次财报的交易日数，只在实盘可用 |
| `ctx.profile` | 当前风险档位 |

`ctx.day` 之后的数据一律看不到，所以回测不会用到未来数据。

## 参数

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

传入未定义的参数会直接报错，拼错参数名不会悄悄用回默认值。

## 建议

- 少而精：只发信心高的信号，其余会被风控拦下，回测报告里会列出原因。
- 用 `--compare` 在同一时间段和 `buy_and_hold`、`risk_aware` 对比。
- 换不同档位（`--profile conservative|balanced|aggressive`）和股票池（`--preset etf`、`--symbols ...`）再测。
- 只在某一段行情里好看的策略，多半是过拟合；把 2018–2020、2020–2022、2022 至今分开测。
