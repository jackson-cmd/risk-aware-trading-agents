# 策略实验室：说出你想要的策略，AI 写代码并回测

用大白话告诉 Claude、Codex 或 Gemini 你想要什么策略。它通过 MCP 读 `strategy_guide`，把策略写成一个 Python 类，再用 `backtest_code` 去跑。代码运行前先检查有没有偷看未来、有没有副作用，检查通过后放到单独的进程里回测，AI 拿到结果；有问题时拿到问题所在的行号和原因。

## 准备

```bash
pip install "risk-aware-trading-agents[mcp]"
riskaware mcp-setup claude-desktop --apply   # 也可以是 claude-code、codex（用你的 ChatGPT 账号）、gemini、antigravity
```

然后直接说，比如：

> 写一个策略：上升趋势里连跌两天、最近新闻偏正面时买入，持有 10 天或涨 8% 卖出。用 2026 年的 tech 股票池带新闻回测。

## 过程

1. AI 调用 `strategy_guide`，拿到规则、`ctx` 里有什么（指标、历史价格、持仓、市场状态、按时间回放的新闻）、一个模板和一个用新闻的例子。
2. 它写一个 `Strategy` 子类，里面有 `on_bar(self, ctx)` 方法。
3. `backtest_code` 先检查代码，再在你指定的股票上回测（`symbols`，或 `megacap`、`tech`、`defensive` 这样的 `universe`）。返回的指标和 SPY、买入持有放在一起，另外还有成交记录，以及风控拒绝了几次。
4. 有问题时，AI 拿到行号和原因，改完再跑。
5. `save_as="名字"` 把满意的策略存到 `~/.riskaware/strategies/ai/`。之后它会出现在 `list_strategies` 里、看板的回测页和智能体页（显示为 `AI · 名字`），`agents_create` 可以让它每天跑模拟盘。

上面那段请求我们实测的结果，`tech` 股票池 18 只股票，2026 年 1 月 2 日到 11 月 11 日：

| | AI 写的策略 | SPY 买入持有 | 同一段代码、关掉新闻 |
|---|---|---|---|
| 总收益 | 4.0% | 12.1% | 2.8% |
| 夏普 | 0.59 | 0.77 | |
| 最大回撤 | -0.7% | -8.9% | |
| 成交笔数 | 64 | | 1 |

这里演示的是流程，不是赚钱方法：这个策略的买入条件要求新闻分数为正，所以关掉新闻后几乎不买。

## 代码里不能写什么，为什么

| 不允许 | 原因 |
|---|---|
| 代码里写日期：`date(2026, 4, 9)`、`"2026-04-09"`、`.year`、`today()`、`now()` | 绑定某个日期或年份的规则，等于把"知道那天发生了什么"写进了策略。`ctx.day.month`、`ctx.day.weekday()` 可以用。 |
| 代码里写股票代码：`ctx.row("NVDA")` | 股票由请求指定。模型挑自己知道后来涨了的股票，就是偷看了未来。指数和行业 ETF（SPY、QQQ、XLK 等）可以当市场参考。 |
| 白名单以外的 import | 允许：math、statistics、random、numpy、pandas、collections、itertools、functools、dataclasses、typing、enum、decimal、fractions、heapq、bisect、datetime，以及从 riskaware.strategy、riskaware.types、riskaware.indicators 导入策略需要的名字。 |
| 文件、网络、子进程；`open`、`eval`、`exec`、`getattr`；其他对象的私有属性（`._x`）和双下划线属性 | 策略用不到这些，而它们正是摸到未来（新闻库、之后的价格）或碰你电脑的途径。存在 `self` 里的状态可以用。 |

把月份或日期和固定值比较（`ctx.day.month == 4`）是允许的，但会给出提醒：在挑定的日子触发的规则，也可能把那几天发生的事写进策略。

## 怎么运行

- 在单独的 Python 进程里跑，进程环境里没有任何 API key，有时间上限（默认 10 分钟），macOS 和 Linux 上还有 CPU 上限。
- 先把价格加载好。策略自己的代码运行时，审计钩子会拒绝联网、开子进程、写文件，以及读取 Python 自身以外的文件；import 也会再按白名单检查一遍。
- 回测里策略能看到的和任何策略一样：每天的指标和过去价格的副本，以及当天收盘前已发布的新闻（见[回测怎么防止偷看未来](news-history.md#回测怎么防止偷看未来)）。
- 出错时返回策略里出错的行号；`print()` 的输出也一并返回。

这些检查挡的是失误和随手的滥用，不是对付恶意代码的沙箱。把策略用在钱上之前先读一遍代码；你自己都不会运行的代码，请放进容器里跑。

## 不用 MCP

从网页聊天里拿到的代码也能这样跑：

```bash
riskaware lab my_strategy.py --preset tech --start 2026-01-02          # 先检查，再带新闻回测
riskaware lab my_strategy.py --check-only                              # 只检查
riskaware lab my_strategy.py --symbols AAPL,MSFT,COST --save my_dips   # 保存下来，智能体和看板都能用
```

## 看结果要小心

- 在你反复调参的那段时间上，结果会偏乐观。调几轮之后，在一段没调过的时间上确认一次，比如用 1 到 6 月调，用 7 到 11 月测。
- 用测试期文本训练过的模型知道后来怎么样了。规则应当来自你的描述，而不是模型对那段时间的记忆；最诚实的检验是模型训练数据之后的时间段。
- 内置股票池都是今天的大盘股，在上面做的回测都有幸存者偏差。
