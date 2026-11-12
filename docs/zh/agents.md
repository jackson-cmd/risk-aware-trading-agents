# 模拟盘智能体和 MCP 服务器

[English](../agents.md)

一个智能体就是一个策略加一个属于它自己的模拟账户。你选好策略（内置的或你自己写的文件）、风险档位、股票池和初始资金，它就每个交易日用雅虎财经的免费价格在这个账户里交易一次。几个思路不同的智能体可以同时跑，排行榜会告诉你谁跑赢了 SPY。

智能体不会连接任何券商。不管你的配置里写的是哪家券商、有没有开实盘，它们一律只用内置的模拟账户。

## 三条命令上手

```bash
riskaware agents init                  # 四个示例智能体：steady、momentum、breakout、fear-buyer
riskaware agents run                   # 现在让它们交易一次
riskaware agents schedule --install    # 以后每个交易日自动运行
```

`riskaware agents`（或 `riskaware agents list`）显示排行榜；`riskaware dashboard` 打开网页看板，在「智能体」页能看到净值曲线，也能新建、运行、暂停、重置和删除智能体。

| 示例 | 策略 | 档位 | 股票池 |
|---|---|---|---|
| `steady` | `risk_aware` | 平衡 | 配置里的股票池 |
| `momentum` | `momentum_rotation` | 平衡 | 配置里的股票池 |
| `breakout` | `donchian_breakout` | 激进 | 配置里的股票池 |
| `fear-buyer` | `fear_greed_dca` | 保守 | `etf` |

## 创建你自己的智能体

```bash
riskaware agents add dips --strategy rsi2 --universe tech --cash 25000
riskaware agents add mine --strategy ~/strategies/my_dip.py:MyDip --profile aggressive
riskaware agents add slow --strategy ema_bounce --symbols NVDA,AAPL,COST --param band=0.02
```

- `--strategy`：`riskaware strategies` 里的任意名字，或者 `路径/文件.py:类名`（见[编写自己的策略](custom-strategies.md)）。
- `--universe`：`megacap`、`tech`、`defensive`、`etf`、`sectors`，也可以用 `+` 组合；`--symbols` 额外加股票。
- `--param 参数=值` 设置策略参数；参数名拼错会在创建时直接报错，不会等到第二天 15:40 才发现。
- `riskaware agents show 名字` 查看持仓、每天的净值和最近的订单；`pause`、`resume`、`reset`、`remove` 分别是暂停、恢复、重置和删除。

每个智能体的东西都放在 `~/.riskaware/arena/名字/`：模拟账户（`名字.json`）、持仓记录、每次运行、决策和订单的日志（`journal.db`），以及每个交易日一行的 `equity.csv`。所有智能体的定义在 `~/.riskaware/arena/agents.yaml`，也可以直接手动编辑。

## 每日自动运行是怎么工作的

`riskaware agents schedule --install` 会装一个后台任务，工作日每 15 分钟唤醒一次，执行 `riskaware agents run --scheduled`：

- macOS：`~/Library/LaunchAgents/io.github.risk-aware-trading-agents.plist` 里的 LaunchAgent
- Linux：crontab 里带 `# riskaware-agents` 标记的一行
- Windows：名为 `RiskAwareTradingAgents` 的计划任务

每次唤醒都会先看纽约时间：只有在交易日、`execution.run_time`（默认 15:40）之后、收盘之前才动手，然后让今天还没交易的每个活跃智能体交易一次。之所以频繁唤醒而不是只跑一次，是为了不受时区和夏令时影响；笔记本 15:40 在睡眠，醒来时只要还没收盘就会补跑。每天只交易一次：同一天你手动跑过的智能体会被跳过。某个智能体出错（比如策略文件报错）不会影响其他智能体，当天最多重试三次。

```bash
riskaware agents schedule              # 是否已开启、下一个交易日、上次运行
riskaware agents log                   # 后台任务做了什么
riskaware agents schedule --uninstall  # 关闭
```

后台任务在你执行安装命令的那个文件夹里运行，所以会用那个文件夹里的 `riskaware.yaml` 和 `.env`。配置里设置了通知（Telegram、Discord、Slack）的话，每天运行完也会收到一份汇总。

## 在 Claude 里使用（MCP）

MCP 服务器让 AI 助手可以直接向这些智能体提问、让它们运行。需要先装 `mcp` 扩展：

```bash
pip install -e ".[mcp]"
claude mcp add riskaware -- riskaware mcp          # Claude Code
```

Claude Desktop、Cursor 等客户端添加一个运行 `riskaware mcp` 的 stdio 服务器即可：

```json
{
  "mcpServers": {
    "riskaware": { "command": "riskaware", "args": ["mcp"] }
  }
}
```

| 工具 | 作用 |
|---|---|
| `market_regime` | 今天的市场状态、VIX、恐慌贪婪、板块广度、判断依据，以及未来 30 天的宏观事件 |
| `scan` | 某个策略和档位今天的信号，可以扫描股票池或指定的股票 |
| `analyze_stock` | 单只股票的指标、技术形态、财报质量、交叉验证新闻和结论 |
| `stock_news` | 单只股票交叉验证后的新闻和风险标记 |
| `list_strategies` | 内置策略列表 |
| `backtest` | 任意策略的回测，并和 SPY 买入持有对比 |
| `agents_leaderboard` | 所有模拟盘智能体的排名和定时运行状态 |
| `agent_detail` | 单个智能体的设置、持仓、每天的净值和最近的订单 |
| `agents_create` | 新建模拟盘智能体 |
| `agents_run` | 现在让智能体在模拟账户里运行一次（也可以只预演） |

所有工具要么只读，要么只操作本地模拟账户，没有任何工具能向券商发送订单。

之后直接用自然语言问就行，比如：「现在市场是什么状态，这周有什么重要数据？」「用 100 万美元在 megacap 上回测 2018 年以来的 wheel」「建一个在 tech 股票池上跑 bollinger_reversion、2 万美元的智能体，然后给我看排行榜」。
