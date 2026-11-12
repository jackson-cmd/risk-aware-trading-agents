# 更新记录

[English](CHANGELOG.md)

## 未发布

- 回测可以用新闻了：`riskaware news-history` 在本机建一个按发布时间存的新闻库（`~/.riskaware/news`），来源可以是 SEC EDGAR 8-K 公告、你自己的 Benzinga API key 或任何 CSV；`riskaware backtest --news` 按天回放，走和实盘一样的聚类和两家来源交叉验证。美东 16:00 收盘后发布的新闻，第二天才看得到。
- `news-history show 代码 --date 日期` 打印回测在那天收盘时看到的新闻。
- 新的 `core` 股票池（SPY、QQQ 和美股最大的七只股票）和一个用新闻的示例策略（`examples/strategies/news_earnings_drift.py`）。
- `NewsItem.sentiment`：数据源自带的情绪分（例如导入的 CSV）会代替标题情绪词典。
- 用新闻库回测时，已确认的严重利空（破产、退市、财报重述）在回测里也会触发紧急卖出。

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
