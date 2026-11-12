# 更新记录

[English](CHANGELOG.md)

## 未发布

- 回测可以用新闻了：`riskaware news-history` 在本机建一个按发布时间存的新闻库（`~/.riskaware/news`），来源可以是 SEC EDGAR 8-K 公告、你自己的 Benzinga API key 或任何 CSV；`riskaware backtest --news` 按天回放，走和实盘一样的聚类和两家来源交叉验证。美东 16:00 收盘后发布的新闻，第二天才看得到。
- `news-history show 代码 --date 日期` 打印回测在那天收盘时看到的新闻。
- 新的 `core` 股票池（SPY、QQQ 和美股最大的七只股票）和一个用新闻的示例策略（`examples/strategies/news_earnings_drift.py`）。
- `NewsItem.sentiment`：数据源自带的情绪分（例如导入的 CSV）会代替标题情绪词典。
- 用新闻库回测时，`thesis_break` 紧急卖出在回测里也会触发，规则和实盘一样：严重利空要出现在公司自己的 8-K 或通稿里。

## 0.4.0（2026-11-12）

- **行为变化：新闻触发的紧急卖出要公司自己确认。** 严重利空只有在公司自己的 SEC 8-K 公告里出现对应条目，或者公司自己的通稿证实了至少两家独立媒体提前一小时以上报道的消息、并且没有辟谣时，才会触发 `thesis_break` 清仓。两家独立媒体确认的风险信号仍会挡住新的买入、拉低新闻分，但不再强制卖出。别人提出的指控（造假、SEC 起诉、做空报告）本身不会触发强制卖出，兜底靠档位里的硬止损。
- 每个智能体都能配 LLM：`riskaware agents add 名字 --llm openai:gpt-5.6-luna | ollama:qwen2.5:3b | anthropic:模型 | compat:模型`，`riskaware agents llm 名字 选择`，网页看板的新建表单和智能体页也能选。本地模型通过 Ollama 免费运行。
- 每次 LLM 复核都存进智能体的日志（prompt、原始回复、多方意见、空方意见、结论、复核前后的信心分、耗时、token 数）。看板的「决策」页、`riskaware agents why 名字 [股票代码] [--prompt]` 和新的 MCP 工具 `agent_decisions` 都能看到每个决定是怎么做出来的。
- `riskaware llm-check 选择` 发一次示例复核，检查 key 或本地模型能不能用。
- OpenAI 兼容接口的回复改用严格的 JSON schema 输出（接口不支持时依次退回 JSON 模式和普通文本），解析时只读第一个 JSON 对象；gpt-5.x 和 o 系列模型改传 `max_completion_tokens` 和 `reasoning_effort`，不再传温度；请求超时会自动重试一次。
- 每日新开仓和买入上限现在会算上当天早些时候已经开的仓，同一天跑好几次也不会超（回测结果不变）。
- 新闻：一条新闻只有在标题或摘要里提到这家公司或它的股票代码时才算这只股票的新闻，SEC 文件一律算。之前搜索「COST stock」会把所有讲成本（cost）的文章都抓进来，别家公司的新闻还可能给错的股票打上已确认的风险标记（比如高管离职）。内置股票池里的公司都带了常用名称，其他股票用雅虎的公司名（有缓存）。`news.relevance_filter: false` 可以关闭。
- 网页看板：回测图里每个策略始终用同一种颜色；两份 README 和项目主页加了截图一览。
- 通稿分发服务（Business Wire、PR Newswire、GlobeNewswire、Internet Wire、Accesswire、Newsfile 等）算公司自己的渠道，这类新闻的状态是新的 `ISSUER`（公司通稿）。别家媒体转载通稿、几家通稿服务同时发同一份通稿，都不再算独立确认。一份假通稿加上转播它的媒体可以挡住买入，但没法触发强制卖出。
- 识别否定和辟谣：标题里否认、驳斥某条报道，说它是假的、毫无根据、恶作剧，或者说公司没有申请、没有计划申请破产保护的，不再触发标题里提到的风险信号。辟谣自成一条新闻，不会被算成它所否认的那条新闻的发布方；辟谣的来源不比原新闻弱时，会撤销原新闻的风险信号（8-K 公告和别人的指控不会被撤销）。`NewsReport.red_flags_denied` 列出被撤销的信号，`NewsReport.red_flags_backed` 列出触发紧急卖出的信号，命令行、网页看板和 MCP 的 `stock_news` 工具都会显示。
- "Ch. 11""Chap. 11" 也算破产；"restates""restated" 和 "restatement" 一样会拉低新闻分。

## 0.3.0（2026-11-12）

- 模拟盘智能体：给任意策略（内置的或你自己的文件）起个名字，配上风险档位、股票池和初始资金，它就在自己的本地模拟账户里每个交易日自动交易。命令：`riskaware agents init | add | run | list | show | pause | resume | reset | remove`（#5）。
- 不用服务器的每日运行：`riskaware agents schedule --install` 会在 macOS 上装一个 LaunchAgent，在 Linux 上加一行 crontab，在 Windows 上建一个计划任务。每个智能体每个交易日在纽约时间 `execution.run_time` 之后交易一次，电脑睡眠醒来会补跑，节假日自动跳过；出错的智能体最多重试三次，不会影响其他智能体。
- MCP 服务器：`riskaware mcp` 让 Claude、Cursor 或任何 MCP 客户端都能查市场状态、扫描信号、分析个股、看新闻、回测，以及管理模拟盘智能体。没有任何工具能碰实盘账户。
- 新看板：深色交易终端风格，顶部导航，市场状态卡片、恐慌贪婪仪表盘、带迷你走势的指标卡、宏观事件时间线、带净值曲线和操作按钮的智能体排行榜，回测结果在页面刷新后保留。`riskaware dashboard` 会隐藏 Streamlit 的 Deploy 按钮。
- `TradingEngine` 新增 `state_root` 参数，多个账户可以各自保存持仓记录。

## 0.2.0（2026-11-12）

- 回测支持期权：在模型估算的隐含波动率上用 Black-Scholes 定价，支持买入看涨和看跌、卖现金担保看跌、卖备兑看涨，每天估值，处理到期、行权和备兑股票被买走（#1）。
- 期权风控：每个档位有权利金总额和单笔上限；卖看跌的保证金不占用现金底线，并计入单票上限；为已卖出看涨担保的股票不会被卖掉。
- 新策略：`aggressive_calls` 和 `wheel`（期权），`momentum_rotation`、`donchian_breakout`、`bollinger_reversion`、`fear_greed_dca`（股票）。
- 模拟盘和实盘运行会跳过期权订单并给出提示；期权目前只支持回测。
- 修复：备兑看涨可能建立在同一轮刚卖出的股票上；所有已平仓交易金额都是 0 时，平均持仓天数的计算会出错。

## 0.1.0（2026-11-11）

首个公开版本。

- 宏观市场状态引擎：VIX、VIX 期限结构、板块广度、信用偏好、避险需求、自算的恐慌贪婪指数、CNN 恐慌贪婪指数，以及 FOMC / CPI / 非农 / PPI / GDP / PCE 日历（内置 2015–2027 年的 FOMC 和 2010 年以来的 BLS 发布日期）。
- 技术面智能体：EMA 50/100/200 支撑、均值回归、财报过关才抄底、突破和趋势延续；止盈和趋势减仓。
- 基本面智能体（财报质量）和交叉验证的新闻智能体（Finviz、Google News、Nasdaq、Seeking Alpha、Yahoo、SEC 8-K）。
- 风控：30% 现金 + 短期国债底线，单票 / 行业 / 投机仓上限，分批建仓且减仓后可重建，国债仓位，闲置现金买短期国债 ETF，回撤暂停，按市场状态定的规则（不确定时不卖出）。
- 券商：内置模拟盘、Alpaca、Public.com、Tradier、Interactive Brokers、Charles Schwab、tastytrade、Futu / moomoo、Tiger Brokers、Longbridge。
- 回测器，带 HTML 报告和策略对比；自定义策略的加载器和模板。
- 可选的 LLM 多方 / 空方 / 裁判复核（Anthropic SDK 或任何 OpenAI 兼容接口），影响幅度有上限。
- 双语的命令行、看板、报告、文档和网站（English / 中文）。
