# 更新记录

[English](CHANGELOG.md)

## 0.7.0（2026-11-12）

- **研究工具箱**（`riskaware.research`、`riskaware research`、[docs/zh/research.md](docs/zh/research.md)）：安装包里带了一份冻结的 2026 年市场，实验在任何机器上都得出同样的数字。内容是 64 只股票池股票、25 个 ETF、VIX 期限结构（VIX9D、VIX、VIX3M、VVIX）和 4 个跨资产 ETF 从 2024-01-02 到 2026-11-11 的日线（`research/data/market_2026.csv.gz`，1.4 MB，附带记录 SHA-256 的清单；`scripts/build_market_bundle.py` 可以重建），再加上已有的 2026 年新闻。
- `riskaware research day 日期`（MCP `market_day`）：某天收盘时已知的全部信息：市场状态、VIX 期限结构、恐慌贪婪、广度、信用压力和事先公布的宏观日程；每只股票的价格、技术指标、技术形态、收盘前发布的已核实新闻，以及最近一次财报公告。
- `riskaware research run`（MCP `run_experiment`）：在冻结的市场上回测内置策略、策略文件或 AI 写的代码（先检查，再在单独的进程里运行），收盘出信号，次日开盘成交。纯执行规则完全照策略说的做（等额仓位，最多 N 个，只做多）；`--execution risk_aware` 加上风控。报告给出 SPY 和等权组合、beta、alpha、t 统计量、移动块 bootstrap 区间以及样本内外的拆分，并保存记录：清单（数据哈希、版本、commit、设置、策略源码）、指标、净值、成交、每一个决定、report.md 和图。
- 每次实验都做防偷看检查：把三个切点之后的每个价格和每条标题换成随机的再跑一遍，每个切点之前的决定必须一模一样。结论有三种：通过、失败（写出第一个变化的日期和决定）、不可重复。
- LLM 智能体：`riskaware research trade --llm ...` 和 `run_policy` 让模型一步一步交易这段时间，MCP 的 `trading_session_start` / `trading_session_step` 让 Claude 来做。盲测模式隐藏股票代码、公司名和日期。`extra_news` 可以给一次实验加标题，做反事实测试。
- 五种常见的波段策略，可开关新闻：`swing_trend`、`swing_rsi2`、`swing_breakout`、`swing_pullback` 和 `swing_news`，在 `riskaware backtest` 里也能用。`riskaware research baselines` 会开和不开新闻各跑一遍：2026 年趋势跟随赚了 +21.1%（Sharpe 1.44），同期 SPY 是 +12.1%（0.77）；加新闻过滤让每个策略都略差一点。
- 回测引擎可以换用别的下单规则，并在同一份数据上多次运行时复用技术指标。

## 0.6.3（2026-11-12）

- 看板上的长任务改在后台跑：信号页的扫描、回测、智能体的「立即运行」和「全部立即运行」、获取新闻，开始以后切到别的页面或刷新浏览器都不会中断，结果留在原来的页面上。任务在跑的时候，它所在的页面显示进度和已用时间，回测还有「停止」按钮。其他页面顶部有一条提示，列出还在跑或已经跑完的任务，点它就回到结果。同一种任务一次只跑一个。任务由看板进程保管，所以每个打开的标签页看到的都是同一批任务。

## 0.6.2（2026-11-12）

- 看板上每个数字都能点开看说明：每张卡片、每个小标题、每个表格列和每个输入框旁边都有 ?，点开弹窗（浏览器自带的弹出层：点击打开，点窗外或按 Esc 关闭），说明它是什么、怎么算、怎么用，中英文都有。内容照代码和平衡档的默认设置写：市场状态的判定规则、恐慌贪婪和风险分的构成、宏观日历的权重和窗口、技术面（TA）分和各个形态、信心及其分项、财报检查、风控规则、风险档位、新闻分、报道状态、情绪和风险信号，以及回测的各项指标。说明都在 `riskaware/dashboard/glossary.py`。
- 信号页把信心的分项列出来：技术面（TA）、财报、新闻、宏观。
- 「全部指标」表新增一列，解释每个指标。
- 修复：智能体页的 SPY 卡片写着「自第一个智能体开始」，实际用的是排名第一的智能体的开始日期；现在改成最早创建的那个智能体。
- 英文版的板块广度卡片标题改为 Breadth，放得下一行。

## 0.6.1（2026-11-12）

- 看板回测页新增 **回测看到的新闻** 面板。打开回放新闻跑完回测后，选一笔交易（每笔都标出做决定时回测已看到几条新闻），或者任选股票和日期，就能看到：做决定的收盘时刻、策略第二天下的单、新闻分（含已确认和单一来源的报道、风险信号），以及每条报道的美东时间、状态、情绪和来源。默认停在最近一笔有新闻的交易上。
- 新闻页的汇总卡片和新面板共用同一个组件。

## 0.6.0（2026-11-12）

- 安装包自带 2026 年新闻：内置股票池 64 只股票的 16,945 条，2026 年 1 月 1 日到 11 月 11 日（Benzinga 标题 16,249 条，只有标题、时间和股票代码；SEC 8-K 公告 696 份），放在 `riskaware/news/data/news_2026.jsonl.gz`（0.7 MB）。回测加 `--news` 时会和你自己的新闻库一起读；`news.bundled: false` 可以不用它。只有 Benzinga 给新闻标了这只股票、并且标题提到这家公司或代码时才算这只股票的新闻；机器生成的价格汇总没有收。`scripts/build_news_bundle.py` 可以重新生成这个文件。
- 策略实验室：MCP 新工具 `strategy_guide` 和 `backtest_code`，让 Claude、Codex 或 Gemini 根据大白话描述把策略写成代码并回测。代码先检查（代码里不能写日期和股票代码，只能 import 白名单里的模块，不能读写文件、联网、用 `eval`、`getattr` 或私有属性），再放到单独的进程里跑：没有任何 API key，有时间上限，系统支持时还有 CPU 上限；策略代码运行时，审计钩子会拒绝联网、开子进程和访问文件。出错时返回策略里出错的行。`save_as` 把策略存到 `~/.riskaware/strategies/ai/`，之后 `list_strategies`、看板和 `agents_create` 都能用。命令行用 `riskaware lab 文件` 也一样。
- 回测不偷看未来：每天只能看到收盘时刻之前发布的新闻，提前收盘的日子收盘时刻是 13:00；只有日期没有时间的，算那天结束时发布；没有时区的时间按美东时间读（CSV 导入以前默认按 UTC）；日期在未来的新闻被丢掉。指标行现在只存当天数值的副本，`ctx.history()` 返回副本，策略没法再通过背后的数组摸到之后的价格。每条规则都有新测试，另有一个测试把数据截断在每一天重算指标，证明指标没有用到之后的数据。
- 看板的回测页新增 **回放新闻** 开关；保存下来的 AI 策略会出现在回测页和创建智能体的策略列表里。
- MCP 的 `backtest` 工具支持 `news=True`；回测只加载要交易的股票的新闻。
- `news-history status` 把自带的新闻单独列一栏，并打印自带数据的概况。
- README 和网站加上 2026 年带新闻的回测结果（1 月 2 日到 11 月 11 日，megacap）：收益 +15.9%，SPY +12.1%；最大回撤 -5.3%，SPY -8.9%。

## 0.5.0（2026-11-12）

- 不需要 API key：`--llm claude-cli[:模型]`、`codex-cli[:模型]` 和 `gemini-cli[:模型]` 通过 Claude Code、OpenAI Codex、Google 的 Gemini CLI 或 Antigravity 做每次复核，走你已经登录的订阅。工具在一个空的临时文件夹里运行，关掉它的工具调用，要求同样严格的 JSON schema；`--llm-command` 或 `RISKAWARE_*_CLI` 可以换成别的可执行文件或包装脚本。看板的 LLM 选择和 `riskaware llm-check` 也都支持。
- `riskaware mcp-setup claude-desktop | claude-code | codex | gemini | antigravity [--apply]` 打印或直接执行把 MCP 服务器接到对应应用的那一步（Claude Desktop 的配置文件会就地修改并留备份）。
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
