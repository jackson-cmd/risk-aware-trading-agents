# Paper-trading agents and the MCP server

[中文](zh/agents.md)

An agent is a strategy with its own paper account. You pick the strategy (a built-in one or your own file), a risk profile, a stock list and the starting cash; the agent then trades that account once every trading day on free Yahoo Finance prices. Several agents with different ideas run side by side, and the leaderboard shows which one is doing better than SPY.

Agents never touch a broker. They always use the built-in paper account, whatever your configuration says about brokers or live trading.

## Start in three commands

```bash
riskaware agents init                  # four starter agents: steady, momentum, breakout, fear-buyer
riskaware agents run                   # trade them once now
riskaware agents schedule --install    # from now on, every trading day by itself
```

`riskaware agents` (or `riskaware agents list`) prints the leaderboard; `riskaware dashboard` shows it with equity curves on the **Agents** page, where you can also create, run, pause, reset and delete agents.

| Starter | Strategy | Profile | Stock list |
|---|---|---|---|
| `steady` | `risk_aware` | balanced | your config's list |
| `momentum` | `momentum_rotation` | balanced | your config's list |
| `breakout` | `donchian_breakout` | aggressive | your config's list |
| `fear-buyer` | `fear_greed_dca` | conservative | `etf` |

## Your own agents

```bash
riskaware agents add dips --strategy rsi2 --universe tech --cash 25000
riskaware agents add mine --strategy ~/strategies/my_dip.py:MyDip --profile aggressive
riskaware agents add slow --strategy ema_bounce --symbols NVDA,AAPL,COST --param band=0.02
```

- `--strategy`: any name from `riskaware strategies`, or `path/to/file.py:ClassName` (see [custom strategies](custom-strategies.md)).
- `--universe`: `megacap`, `tech`, `defensive`, `etf`, `sectors`, or several joined with `+`; `--symbols` adds names.
- `--param key=value` passes strategy parameters; a misspelled parameter is rejected when you create the agent, not at 15:40 the next day.
- `riskaware agents show NAME` lists positions, the equity by day and the latest orders; `pause`, `resume`, `reset` and `remove` do what they say.

Everything an agent owns lives in `~/.riskaware/arena/NAME/`: the paper account (`NAME.json`), its position memory, a journal of every run, decision and order (`journal.db`) and one row per trading day in `equity.csv`. The definitions are in `~/.riskaware/arena/agents.yaml`, which you can also edit by hand.

## How the daily run works

`riskaware agents schedule --install` adds a background job that wakes up every 15 minutes on weekdays and runs `riskaware agents run --scheduled`:

- macOS: a LaunchAgent in `~/Library/LaunchAgents/io.github.risk-aware-trading-agents.plist`
- Linux: one line in your crontab, marked `# riskaware-agents`
- Windows: a Task Scheduler task named `RiskAwareTradingAgents`

On each wake-up the command checks New York time and does nothing unless it is a trading day, after `execution.run_time` (15:40 by default) and before the close. Then it trades every active agent that has not traded yet today. Waking often instead of once keeps it right across time zones and daylight saving, and a laptop that was asleep at 15:40 catches up when it wakes, as long as the market is still open. A day is traded once: an agent you ran by hand earlier the same day is skipped. If an agent fails (for example, its strategy file raises an error), the others still trade, and it is retried at most three times that day.

```bash
riskaware agents schedule              # installed or not, next trading day, last run
riskaware agents log                   # what the job did
riskaware agents schedule --uninstall  # stop it
```

The job runs in the folder where you installed it, so it uses that folder's `riskaware.yaml` and `.env`. Notifications (Telegram, Discord, Slack) set up in the config also receive a summary after each daily run.

## Use it from Claude (MCP)

The MCP server lets an AI assistant ask the agents questions and run them. It needs the `mcp` extra:

```bash
pip install -e ".[mcp]"
claude mcp add riskaware -- riskaware mcp          # Claude Code
```

For Claude Desktop, Cursor or another client, add a stdio server that runs `riskaware mcp`:

```json
{
  "mcpServers": {
    "riskaware": { "command": "riskaware", "args": ["mcp"] }
  }
}
```

| Tool | What it does |
|---|---|
| `market_regime` | today's regime, VIX, fear and greed, breadth, the reasons, and the macro events of the next 30 days |
| `scan` | today's signals for a strategy and a profile, on your list or the symbols you name |
| `analyze_stock` | indicators, technical setups, earnings quality, verified news and the decision for one stock |
| `stock_news` | cross-verified headlines and red flags for one stock |
| `list_strategies` | the built-in strategies |
| `backtest` | a backtest of any strategy against SPY buy and hold |
| `agents_leaderboard` | all paper agents ranked, with the schedule status |
| `agent_detail` | one agent's settings, positions, equity by day and recent orders |
| `agents_create` | create a paper agent |
| `agents_run` | run agents once now on their paper accounts (or as a dry run) |

All tools are read-only or act on local paper accounts. There is no tool that can send an order to a broker.

Then ask in plain words, for example: "What regime is the market in and what is coming up this week?", "Backtest the wheel on megacap with $1M since 2018", or "Create an agent that runs bollinger_reversion on the tech list with $20k and show me the leaderboard."
