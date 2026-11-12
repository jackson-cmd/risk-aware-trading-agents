# 用新闻回测

实盘里新闻是发生一条读一条。回测要还原每个交易日当时已经知道什么，所以每条新闻都必须有发布时间。没有发布时间的话，策略会在新闻出现之前就"看到"它，任何新闻策略的回测结果都会虚高。

`riskaware news-history` 在你自己的电脑上建一个按时间存的新闻库，`riskaware backtest --news` 按天回放它。

## 快速开始

```bash
# 1. core 股票池 2018 年以来的 SEC 8-K 公告（免费；SEC 要求注明联系邮箱）
riskaware news-history build --preset core --start 2018-01-01 --contact-email you@example.com

# 2. 可选：加入你自己的新闻来源
riskaware news-history build --symbols AAPL,NVDA --source benzinga      # 需要在 .env 里填 BENZINGA_API_KEY
riskaware news-history import my_news.csv --tz America/New_York

# 3. 看看新闻库里有什么，以及回测在某一天能看到什么
riskaware news-history status
riskaware news-history show NVDA --date 2024-05-22

# 4. 带新闻回测
riskaware backtest --strategy examples/strategies/news_earnings_drift.py --preset core --news --compare buy_and_hold
```

在 `riskaware.yaml` 里写一次 `data.contact_email`，以后就不用加 `--contact-email`。设置 `backtest.news: true`，每次回测都会回放新闻。

## 回放规则

- 回测在第 *t* 天收盘（美东时间 16:00）做决定，第 *t+1* 天成交。第 *t* 天调用 `ctx.news(symbol)`，只能看到这个收盘时刻以前、`news.lookback_hours`（默认 72 小时）以内发布的新闻。第 *t* 天 16:01 发布的新闻，第 *t+1* 天才看得到。
- 能看到的新闻走和实盘一样的代码：没提到这家公司或股票代码的新闻先被丢掉（SEC 公告一律算），标题再聚成"同一件事"，有两家独立媒体报道或有 SEC 公告佐证才算**已确认**；单一来源的利空可以挡住买入，但永远不会触发卖出。
- 内置的 `risk_aware` 策略和实盘一样使用新闻：已确认的风险信号挡住新的买入；严重利空（破产、退市、财报重述）只有出现在公司自己的 8-K 或通稿里，才会触发紧急卖出。
- 没有新闻库，或者加了 `--no-news`，`ctx.news()` 返回 `None`，和以前一样。

`riskaware news-history show 代码 --date 日期` 会打印回测在那天收盘时看到的新闻和结论，任何一笔交易都能核对。

## 新闻来源

### SEC EDGAR 8-K 公告

来自 `data.sec.gov` 的公开数据。每份 8-K 是一条新闻：标题由条目编号生成（例如 `8-K: Item 2.02 Results of operations (earnings)`），链接指向公告原文，时间是 SEC 受理的时间。条目编号本身就有含义：2.02 是财报发布，5.02 是高管或董事变动，1.03 是破产，3.01 是退市通知，4.02 是以前的财报不再可信。

- SEC 要求自动程序在 User-Agent 里注明联系邮箱，并且每秒不超过 10 次请求；不带邮箱的请求会返回 HTTP 403。本工具发送 `risk-aware-trading-agents/<版本> <你的邮箱>`，两次请求之间等 0.15 秒。
- EDGAR 的受理时间按美东时间读取。万一某个时间其实是 UTC，按美东时间读只会让新闻在回测里更晚出现，不会更早，所以不会偷看未来。
- SPY、QQQ 是基金不是公司，8-K 新闻库里基本没有它们的公告。市场状态本来就由每次回测里的议息、CPI、非农日历决定。
- 不解析公告正文；条目编号说明是哪类事件，不说明财报是否超预期。

### Benzinga（你自己的 key）

`--source benzinga` 用 `BENZINGA_API_KEY`（或 `news.benzinga_key_env` 指定的变量）里的 key 调用 Benzinga 新闻 API（`api.benzinga.com/api/v2/news`），每次取一个自然月，每页 100 条标题。能取到多早的数据取决于你的 Benzinga 套餐。这部分按 Benzinga 公开的 API 文档编写，用录制的响应做了测试；如果真实 key 下有问题，欢迎反馈。

Benzinga 算一家来源，只有 Benzinga 报道的新闻在有 SEC 公告或第二家媒体佐证之前都算"单一来源"。**Benzinga 的条款不允许再分发它的内容。** 新闻库放在你的用户目录里，不在代码仓库内；不要提交、上传或分享它。

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

不带时区的时间按 UTC 读；如果导出的是美东时间，加 `--tz America/New_York`。没有时间或标题的行会被跳过。`source` 请填真实的媒体名：交叉验证数的是媒体家数，全部填同一个名字，每条新闻都会变成"单一来源"。

回测和实盘用同一个相关性过滤：标题（或摘要）里提到这家公司或股票代码的新闻才算这只股票的新闻，SEC 公告一律算。CSV 里两样都没提到的标题会被丢掉，在 `news-history show` 里显示为 off-topic；想保留它们，设置 `news.relevance_filter: false`。

## 新闻库

- 位置：`~/.riskaware/news/`（配置里的 `news.archive_dir`；跟随 `RISKAWARE_HOME`）。
- 每只股票一个 JSON Lines 文件，按时间排序，按来源、链接和时间去重。再次运行 `build` 只会加新的新闻。
- `riskaware news-history remove 代码 [--feed sec|benzinga|import]` 删除新闻。

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
- 免费的、时间准确的历史新闻标题很少。依赖新闻的回测结果，可信度取决于新闻库本身；先用 `status` 和 `show` 看清覆盖情况，再相信数字。
- 内置股票池都是今天的大盘股，在上面做的回测都有幸存者偏差。
