# 用新闻回测，不用未来函数

实盘里新闻是发生一条读一条。回测要还原每个交易日当时已经知道什么，所以每条新闻都必须有发布时间。没有发布时间的话，策略会在新闻出现之前就"看到"它，任何新闻策略的回测结果都会虚高。

安装包不带新闻。`riskaware news-history` 在你自己的电脑上建一个按时间存的新闻库，三个冻结时段的标题文件如果你有，放在 `~/.riskaware/news/periods`；回测会把两者一起回放。

## 快速开始

```bash
# 2026 年：需要把你自己的标题文件放在 ~/.riskaware/news/periods
riskaware backtest --strategy examples/strategies/news_earnings_drift.py --preset megacap --start 2026-01-02 --news --compare buy_and_hold

# 其他年份：core 股票池 2018 年以来的 SEC 8-K 公告（免费；SEC 要求注明联系邮箱）
riskaware news-history build --preset core --start 2018-01-01 --contact-email you@example.com

# 可选：加入你自己的新闻来源
riskaware news-history build --symbols AAPL,NVDA --source benzinga      # 需要在 .env 里填 BENZINGA_API_KEY
riskaware news-history import my_news.csv                                # 不带时区的时间按美东时间读

# 看看新闻库里有什么，以及回测在某一天能看到什么
riskaware news-history status
riskaware news-history show NVDA --date 2026-05-20
```

看板的回测页打开 **回放新闻** 开关也可以。在 `riskaware.yaml` 里写一次 `data.contact_email`，以后就不用加 `--contact-email`。设置 `backtest.news: true`，每次回测都会回放新闻。

## 冻结时段的标题文件

本项目的结果按冻结时段各回放一个标题文件，覆盖内置股票池（`core`、`megacap`、`tech`、`defensive`）的 64 只股票。2026 年（1 月 1 日到 11 月 11 日）的文件有 16,945 条：

- 标题 16,249 条：只有标题、发布时间和股票代码，别的都没有（没有正文、摘要、链接和情绪分）。只有新闻源给这条新闻标了这只股票、并且标题本身提到这家公司或代码时，才算这只股票的新闻。机器生成的数据汇总（"当年投 1000 美元现在值多少"、股息计算、期权异动、做空比例、市盈率汇总、电视节目荐股清单）没有收：它们只是复述价格，不是新闻。
- SEC 8-K 公告 696 份，带 SEC 受理时间和公告链接。

这些文件不再分发：安装包和代码仓库里都没有。把你自己的副本放到 `~/.riskaware/news/periods`（`news_2026.jsonl.gz`、`news_2022.jsonl.gz`、`news_2020.jsonl.gz`）；`scripts/build_news_bundle.py` 可以从新闻库生成，每个时段 manifest（`src/riskaware/research/data/`）里的 SHA-256 能核对你的文件是不是我们的结果用的那一份。回测会把它们和你自己的新闻库一起读；两边有同一条时以你的为准，`remove` 只删你自己的。不想用的话设置 `news.bundled: false`。

## 回测怎么防止偷看未来

- 回测在第 *t* 天收盘时做决定，第 *t+1* 天成交。收盘是美东时间 16:00，提前收盘的日子是 13:00（7 月 3 日、感恩节次日、平安夜）。第 *t* 天调用 `ctx.news(symbol)`，只能看到收盘时刻**之前**、`news.lookback_hours`（默认 72 小时）以内发布的新闻。正好标在 16:00 的新闻（很多财报就在这一刻发出），要到第 *t+1* 天才看得到。
- 一个时间有两种读法时，按更晚的那种读。只有日期没有时间的，算那天结束时发布。没有时区的时间按美东时间读：万一它其实是 UTC，新闻只会晚几个小时出现；反过来读错，新闻会早几个小时出现。日期在未来的新闻当成坏数据丢掉。
- 策略拿到的都是副本：指标行只存当天的数值，`ctx.history()` 返回过去几行的副本，所以从价格数据里摸不到第 *t* 天之后的任何东西。指标都只往回算；有测试把数据截断在第 *t* 天重算，检查结果完全一样。
- 回测从不调用大模型。用测试期文本训练过的模型知道每件事最后怎么样了。
- AI 通过[策略实验室](strategy-lab.md)写的策略，还会检查作者自己"知道未来"的写法：代码里不能写日期，也不能写股票代码。
- 能看到的新闻走和实盘一样的代码：没提到这家公司或股票代码的新闻先被丢掉（SEC 公告一律算），标题再聚成"同一件事"，有两家独立媒体报道或有 SEC 公告佐证才算**已确认**；单一来源的利空可以挡住买入，但永远不会触发卖出。
- 内置的 `risk_aware` 策略和实盘一样使用新闻：已确认的风险信号挡住新的买入；严重利空（破产、退市、财报重述）只有出现在公司自己的 8-K 或通稿里，才会触发紧急卖出。
- 没有新闻库，或者加了 `--no-news`，`ctx.news()` 返回 `None`，和以前一样。

## 查看回测看到的新闻

在看板上打开 **回放新闻** 跑一次回测。结果下面的 **回测看到的新闻** 面板列出每一笔交易，并标出做决定时回测已经看到几条新闻。选一笔交易，或者任选股票和日期，就能看到：

- 做决定的时刻（当天收盘，美东时间），以及那时能看到几条新闻；
- 策略对这只股票做了什么（下一个交易日成交）；
- 新闻分、已确认和单一来源的报道，以及风险信号；
- 每条报道的时间、状态、情绪和来源。

命令行用 `riskaware news-history show 代码 --date 日期` 也能打印同样的新闻和结论。

## 新闻来源

### SEC EDGAR 8-K 公告

来自 `data.sec.gov` 的公开数据。每份 8-K 是一条新闻：标题由条目编号生成（例如 `8-K: Item 2.02 Results of operations (earnings)`），链接指向公告原文，时间是 SEC 受理的时间。条目编号本身就有含义：2.02 是财报发布，5.02 是高管或董事变动，1.03 是破产，3.01 是退市通知，4.02 是以前的财报不再可信。

- SEC 要求自动程序在 User-Agent 里注明联系邮箱，并且每秒不超过 10 次请求；不带邮箱的请求会返回 HTTP 403。本工具发送 `risk-aware-trading-agents/<版本> <你的邮箱>`，两次请求之间等 0.15 秒。
- EDGAR 的受理时间按美东时间读取。万一某个时间其实是 UTC，按美东时间读只会让新闻在回测里更晚出现，不会更早，所以不会偷看未来。
- SPY、QQQ 是基金不是公司，8-K 新闻库里基本没有它们的公告。市场状态本来就由每次回测里的议息、CPI、非农日历决定。
- 不解析公告正文；条目编号说明是哪类事件，不说明财报是否超预期。

### Benzinga（你自己的 key）

`--source benzinga` 用 `BENZINGA_API_KEY`（或 `news.benzinga_key_env` 指定的变量）里的 key 调用 Benzinga 新闻 API（`api.benzinga.com/api/v2/news`），每次取一个自然月，每页 100 条标题。能取到多早的数据取决于你的 Benzinga 套餐。这部分按 Benzinga 公开的 API 文档编写，用录制的响应做了测试；如果真实 key 下有问题，欢迎反馈。

Benzinga 算一家来源，只有 Benzinga 报道的新闻在有 SEC 公告或第二家媒体佐证之前都算"单一来源"。用你的 key 下载的内容存在你用户目录下的新闻库里，不在代码仓库内。

### CSV

任何你有权使用的新闻，一行一条标题：

| 列 | 必填 | 例子 |
|---|---|---|
| `timestamp`（或 `published`、`datetime`、`date`、`created`） | 是 | `2024-05-22T20:21:00Z`、`2024-05-22 16:21:00-04:00`、`Wed, 22 May 2024 16:21:00 -0400` |
| `symbol` | 是 | `NVDA`，多只用分号：`NVDA;AMD` |
| `headline`（或 `title`） | 是 | `Nvidia beats estimates, announces 10-for-1 split` |
| `source`（或 `publisher`） | 否 | `Reuters`；不填时用 `--source` 或文件名 |
| `url` | 否 | 新闻链接 |
| `sentiment` | 否 | -1 到 1 的数，填了就代替内置的标题情绪词典 |

不带时区的时间按美东时间读，这是更稳妥的猜法（见上文）；知道时区时用 `--tz UTC` 等指定。只有日期没有时间的，算那天结束时发布。没有时间或标题的行会被跳过，日期在未来的也会被跳过。`source` 请填真实的媒体名：交叉验证数的是媒体家数，全部填同一个名字，每条新闻都会变成"单一来源"。

回测和实盘用同一个相关性过滤：标题（或摘要）里提到这家公司或股票代码的新闻才算这只股票的新闻，SEC 公告一律算。CSV 里两样都没提到的标题会被丢掉，在 `news-history show` 里显示为 off-topic；想保留它们，设置 `news.relevance_filter: false`。

## 新闻库

- 位置：`~/.riskaware/news/`（配置里的 `news.archive_dir`；跟随 `RISKAWARE_HOME`）。
- 每只股票一个 JSON Lines 文件，按时间排序，按来源、链接和时间去重。再次运行 `build` 只会加新的新闻。
- `riskaware news-history remove 代码 [--feed sec|benzinga|import]` 删除你自己的新闻。时段标题文件只读；`status` 在"时段文件"一列里单独计数。

## 写一个用新闻的策略

```python
report = ctx.news(sym)          # 回看窗口里没有新闻时为 None
if report is not None:
    report.score                # -1 到 1，已确认的、较新的新闻权重更高
    report.red_flags_confirmed  # 例如 ["accounting", "bankruptcy"]
    for story in report.clusters:
        story.confirmed, story.event_types, story.headline, story.publishers
```

`examples/strategies/news_earnings_drift.py` 在公司发出财报 8-K、股价在发布前后两个交易日涨了至少 3% 时买入，20 个交易日后、涨 15% 或跌破 EMA50 时卖出。它是用来演示接口的，不是验证过的赚钱方法。

## 局限

- 8-K 新闻库只告诉你"发了一份公告、是哪类事件"；除了明显的利空（破产、退市、减值、财报重述），它本身不带情绪。
- 标题按抓取时的样子保存；如果媒体后来改过标题，存下的可能是改过的措辞，时间仍是原来的发布时间。
- 免费的、时间准确的历史新闻标题很少。依赖新闻的回测结果，可信度取决于新闻库本身；先用 `status` 和 `show` 看清覆盖情况，再相信数字。
- 内置股票池都是今天的大盘股，在上面做的回测都有幸存者偏差。
