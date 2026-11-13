"""What each number, column and control on the dashboard means: shown in the ? popups.

Every entry says what it is, how it is calculated (from the code, with the default settings of the balanced
profile) and how to use it, in English and Chinese. Lines starting with "- " become a list; `text` in
backticks is shown as code.
"""

from __future__ import annotations

HEADINGS = {"en": ("What it is", "How it is calculated", "How to use it"), "zh": ("是什么", "怎么算", "怎么用")}


def _e(en: tuple[str, str, str, str], zh: tuple[str, str, str, str]) -> dict[str, dict[str, str]]:
    keys = ("title", "what", "how", "use")
    return {"en": dict(zip(keys, en, strict=True)), "zh": dict(zip(keys, zh, strict=True))}


GLOSSARY: dict[str, dict[str, dict[str, str]]] = {
    # ------------------------------------------------------------------------------------------- market
    "regime": _e((
        "Market regime",
        "The market's state in one word. It decides what the agents may do today (sizes for the balanced "
        "profile):\n"
        "- RISK_ON: buy pullbacks, oversold dips, quality dips and trend entries (breakouts only on the aggressive "
        "profile); full size.\n"
        "- NEUTRAL: buy pullbacks to EMA50/100/200, oversold dips and quality dips; 75% size.\n"
        "- UNCERTAIN: hold; buy only at EMA100 or EMA200, when deeply oversold, or quality dips; half size.\n"
        "- RISK_OFF: hold; buy only at EMA200 or quality dips; 35% size.\n"
        "In UNCERTAIN and RISK_OFF nothing is sold except emergency exits (the hard stop, or a disaster the "
        "company itself confirms).",
        "Checked every day; the first rule that matches wins:\n"
        "- RISK_OFF: VIX at 30 or more; or SPY below its EMA200 together with VIX above VIX3M, credit stress "
        "(high-yield bonds lagging Treasuries, z-score -2 or lower) or fear & greed at 15 or less.\n"
        "- UNCERTAIN: an FOMC, CPI or jobs-report window is open, VIX at 20 or more, VIX/VIX3M at 0.97 or more, "
        "fear & greed at 35 or less, or SPY in a downtrend (below EMA200, with EMA50 under EMA200).\n"
        "- RISK_ON: SPY above a rising EMA50 that is above EMA200, VIX under 20, and at least 55% of the 11 sector "
        "ETFs above their EMA200.\n"
        "- Otherwise NEUTRAL.\n"
        "It turns riskier at once, but safer only after two days in a row (or as soon as an event window ends).",
        "The chips under it list the readings behind today's call. A worse regime is not a signal to sell: the "
        "agents stop selling and buy only at strong support, so expect fewer and smaller buys.",
    ), (
        "市场状态",
        "用一个词概括今天的市场，决定智能体今天能做什么（仓位按平衡档）：\n"
        "- RISK_ON（偏多）：回踩、超跌、优质股大跌和顺势入场都可以买（突破只有激进档做）；满仓位。\n"
        "- NEUTRAL（中性）：买回踩 EMA50/100/200、超跌和优质股大跌；仓位 75%。\n"
        "- UNCERTAIN（不确定）：持有不动，只在 EMA100、EMA200、深度超跌或优质股大跌时买；仓位减半。\n"
        "- RISK_OFF（避险）：持有不动，只在 EMA200 或优质股大跌时买；仓位 35%。\n"
        "不确定和避险时，除了紧急退出（硬止损，或公司自己确认的重大利空），什么都不卖。",
        "每天按顺序判断，先满足哪条就是哪个：\n"
        "- 避险：VIX ≥ 30；或者 SPY 跌破 EMA200，同时满足下面任意一条：VIX 高于 VIX3M、信用紧张（高收益债跑输国债，"
        "z 值 ≤ -2）、恐慌贪婪 ≤ 15。\n"
        "- 不确定：处在 FOMC、CPI 或非农的窗口里，VIX ≥ 20，VIX/VIX3M ≥ 0.97，恐慌贪婪 ≤ 35，或者 SPY 处于下跌趋势"
        "（在 EMA200 下方，且 EMA50 低于 EMA200）。\n"
        "- 偏多：SPY 在向上的 EMA50 之上、EMA50 在 EMA200 之上，VIX < 20，并且 11 个行业 ETF 里至少 55% 站上各自的 "
        "EMA200。\n"
        "- 其他情况都是中性。\n"
        "变差时当天就切换；变好要连续两天才切换（事件窗口一结束就立即恢复）。",
        "下面的小标签列出了今天这个判断的依据。状态变差不是卖出信号：智能体会停止卖出，只在强支撑位买，所以买得更少、"
        "更小。",
    )),
    "fear_greed": _e((
        "Fear & greed (computed)",
        "A 0 to 100 gauge of the market's mood: 0 is extreme fear, 100 extreme greed. This app computes it from "
        "free market data. The CNN number under the gauge is CNN's own index, shown for comparison only.",
        "The average of six parts (at least three needed). Each part is scored 0 to 100 by where today's value "
        "sits within the past year:\n"
        "- momentum: SPY against its 125-day average\n"
        "- breadth: the share of the 11 sector ETFs above their EMA200\n"
        "- volatility: VIX against its 50-day average (high VIX counts as fear)\n"
        "- VIX term structure: VIX ÷ VIX3M (high counts as fear)\n"
        "- safe havens: SPY's 20-day return minus long Treasuries' (TLT)\n"
        "- junk bonds: high-yield bonds (HYG) minus Treasuries (IEF) over 20 days\n"
        "Labels: under 25 extreme fear, 25 to 45 fear, 45 to 55 neutral, 55 to 75 greed, over 75 extreme greed.",
        "Fear makes dip buys more attractive: for dip setups the macro part of conviction uses "
        "(100 - fear & greed) ÷ 100 when that is higher. At 35 or below the regime is at least UNCERTAIN; at 15 or "
        "below with SPY under its EMA200 it is RISK_OFF.",
    ), (
        "恐慌贪婪指数（自算）",
        "0 到 100 衡量市场情绪：0 是极度恐慌，100 是极度贪婪。由本程序用免费的市场数据自己算。仪表盘下面的 CNN 数字是 "
        "CNN 自己的指数，只用来对照。",
        "六个分项的平均（至少要有三个）。每个分项按今天的数值在过去一年里排第几，打成 0 到 100 分：\n"
        "- 动量：SPY 相对它的 125 日均线\n"
        "- 广度：11 个行业 ETF 里站上 EMA200 的比例\n"
        "- 波动：VIX 相对它的 50 日均值（VIX 高算恐慌）\n"
        "- VIX 期限结构：VIX ÷ VIX3M（高算恐慌）\n"
        "- 避险需求：SPY 20 日涨幅减去长期国债（TLT）的\n"
        "- 垃圾债需求：高收益债（HYG）减国债（IEF）的 20 日涨幅\n"
        "标签：25 以下极度恐慌，25 到 45 恐慌，45 到 55 中性，55 到 75 贪婪，75 以上极度贪婪。",
        "恐慌时抄底更有吸引力：对抄底类形态，信心里的宏观分会取 (100 - 恐慌贪婪) ÷ 100（如果它更高）。≤ 35 时市场状态至少"
        "是不确定；≤ 15 且 SPY 在 EMA200 下方时是避险。",
    )),
    "vix": _e((
        "VIX",
        "The CBOE Volatility Index: how much the S&P 500 is expected to move over the next 30 days, read from "
        "option prices. It rises when investors pay up for protection, which is why it is called the fear index.",
        "The latest close of ^VIX from Yahoo Finance. The word on the card: calm under 20, elevated from 20 to 30, "
        "stress at 30 or more. The small line shows the last 120 days.",
        "VIX at 20 or more makes the regime at least UNCERTAIN, and 30 or more makes it RISK_OFF. It is also the "
        "largest part of the risk score (up to 35 of 100 points).",
    ), (
        "VIX 恐慌指数",
        "芝加哥期权交易所的波动率指数：从期权价格里读出的、市场预期标普 500 未来 30 天的波动幅度。投资者花钱买保护时它就"
        "上升，所以叫恐慌指数。",
        "Yahoo Finance 上 ^VIX 的最新收盘价。卡片上的字：20 以下平静，20 到 30 偏高，30 及以上紧张。小折线是最近 120 天。",
        "VIX ≥ 20 时市场状态至少是不确定，≥ 30 时是避险。它也是风险分里最大的一项（100 分里最多 35 分）。",
    )),
    "vix_term": _e((
        "VIX / VIX3M",
        "Short-term fear divided by three-month fear, the VIX term structure. Normally it is below 1 (contango): "
        "protection further out costs more. Above 1 (backwardation) means fear is acute right now.",
        "The latest close of ^VIX divided by the latest close of ^VIX3M.",
        "At 0.97 or more the regime is UNCERTAIN; at 1.0 or more with SPY below its EMA200 it is RISK_OFF. It adds "
        "up to 15 points to the risk score as it rises from 0.85 to 1.10.",
    ), (
        "VIX 期限结构",
        "短期恐慌除以三个月恐慌。平时小于 1（正常结构）：越远期的保护越贵。大于 1（倒挂）说明眼下恐慌特别强。",
        "^VIX 最新收盘价除以 ^VIX3M 最新收盘价。",
        "≥ 0.97 时市场状态是不确定；≥ 1.0 且 SPY 在 EMA200 下方时是避险。它从 0.85 升到 1.10 时，给风险分加最多 15 分。",
    )),
    "breadth": _e((
        "Sector breadth",
        "How broad the market's strength is: the share of the 11 S&P sector ETFs (XLK, XLF, XLE, XLV and the rest) "
        "that close above their 200-day EMA.",
        "Sectors above their EMA200 ÷ sectors with enough history (at least three needed).",
        "RISK_ON needs at least 55%. A rally carried by a few sectors is fragile. Low breadth adds up to 10 points "
        "to the risk score and pulls fear & greed down.",
    ), (
        "板块广度",
        "市场的强势有多普遍：11 个标普行业 ETF（XLK、XLF、XLE、XLV 等）里，收盘站上 200 日 EMA 的比例。",
        "站上 EMA200 的行业数 ÷ 有足够历史数据的行业数（至少要三个）。",
        "偏多状态要求至少 55%。只靠少数几个行业撑起来的上涨很脆弱。广度低时给风险分加最多 10 分，也会拉低恐慌贪婪。",
    )),
    "risk_score": _e((
        "Risk score",
        "One stress number for the whole market, from 0 (calm) to 100 (stress). It sums up the same readings the "
        "regime uses.",
        "- VIX: up to 35 points, rising from VIX 12 to 40\n"
        "- VIX/VIX3M: up to 15, from 0.85 to 1.10\n"
        "- trend: 20 if SPY is below its EMA200, 10 if it is only below its EMA50\n"
        "- breadth: up to 10, the share of sectors below their EMA200\n"
        "- credit: up to 10 as high-yield bonds lag Treasuries (z-score from 0 to -3)\n"
        "- events: up to 10, the weight of an open event window (FOMC 1.0, CPI 0.8, jobs report 0.7)",
        "It is for you to read: the trading rules use the regime, not this score. A score that keeps rising often "
        "comes before the regime turns.",
    ), (
        "风险分",
        "整个市场的紧张程度，0（平静）到 100（紧张）。它把市场状态用到的那些读数加总成一个数。",
        "- VIX：最多 35 分，VIX 从 12 到 40 线性增加\n"
        "- VIX/VIX3M：最多 15 分，从 0.85 到 1.10\n"
        "- 趋势：SPY 在 EMA200 下方 20 分，只在 EMA50 下方 10 分\n"
        "- 广度：最多 10 分，按跌破 EMA200 的行业比例\n"
        "- 信用：最多 10 分，高收益债跑输国债越多越高（z 值从 0 到 -3）\n"
        "- 事件：最多 10 分，按正在生效的事件窗口的权重（FOMC 1.0、CPI 0.8、非农 0.7）",
        "它是给人看的：交易规则用的是市场状态，不是这个分数。分数持续上升，往往先于市场状态变差。",
    )),
    "spy_trend": _e((
        "SPY and its EMA200",
        "The S&P 500 ETF's last close, and how far it is above or below its 200-day exponential moving average, the "
        "line many traders use to tell a bull market from a bear market.",
        "close ÷ EMA200 - 1. An EMA weights recent days more than a plain moving average.",
        "Several RISK_OFF rules only apply below EMA200, and RISK_ON needs SPY above a rising EMA50 that is above "
        "EMA200.",
    ), (
        "SPY 与 EMA200",
        "标普 500 ETF 的最新收盘价，以及它高于或低于 200 日指数移动平均线多少。很多交易者用这条线区分牛市和熊市。",
        "收盘价 ÷ EMA200 - 1。指数移动平均比普通均线更看重最近的价格。",
        "好几条避险规则只在跌破 EMA200 时生效；偏多要求 SPY 在向上的 EMA50 之上、EMA50 又在 EMA200 之上。",
    )),
    "events": _e((
        "Macro calendar",
        "Scheduled US releases that move the whole market: the Fed's rate decision (FOMC), inflation (CPI, PCE, "
        "PPI), the jobs report, GDP and the monthly options expiry. 'estimated' means the official date is not "
        "published yet.",
        "Each kind has a weight and a window of trading days:\n"
        "- FOMC 1.0: from two trading days before through the decision day\n"
        "- CPI 0.8 and jobs report 0.7: the trading day before (both come out before the open, so they are known by "
        "the close of their own day)\n"
        "- PCE 0.5, PPI 0.4, GDP 0.4: the trading day before\n"
        "- options expiry 0.2: its own day\n"
        "Colors: red for 0.8 and up, orange for 0.5 and up, blue below.",
        "Inside a window weighing 0.7 or more (FOMC, CPI, jobs report) the regime is UNCERTAIN: the agents hold "
        "and buy only at strong support. The open window's weight also adds to the risk score.",
    ), (
        "宏观日历",
        "会影响整个市场的美国定期数据：美联储议息（FOMC）、通胀（CPI、PCE、PPI）、非农就业、GDP 和每月期权到期日。"
        "「预估」表示官方日期还没公布。",
        "每类事件有一个权重和一个交易日窗口：\n"
        "- FOMC 1.0：决议前两个交易日到决议当天\n"
        "- CPI 0.8、非农 0.7：发布前一个交易日（都在开盘前发布，当天收盘时已经知道结果）\n"
        "- PCE 0.5、PPI 0.4、GDP 0.4：发布前一个交易日\n"
        "- 期权到期 0.2：当天\n"
        "颜色：权重 0.8 及以上红色，0.5 及以上橙色，更低是蓝色。",
        "在权重 ≥ 0.7 的窗口里（FOMC、CPI、非农），市场状态是不确定：智能体持有不动，只在强支撑位买。正在生效的窗口权重"
        "也会加进风险分。",
    )),
    # ------------------------------------------------------------------------------------------- agents
    "agents": _e((
        "Paper-trading agents",
        "Each agent is one strategy with its own simulated account. It decides once every trading day on free "
        "prices, under the same risk manager as live trading, so you can compare strategies before risking money.",
        "The daily job wakes every 15 minutes on weekdays and runs each active agent once per trading day, after "
        "15:40 New York time and before the close (20 minutes before an early close). Orders fill on the paper "
        "account at the current price.",
        "Make one agent per idea, leave them running for weeks, and compare them on the leaderboard against SPY. "
        "A few days of returns is mostly noise.",
    ), (
        "模拟盘智能体",
        "每个智能体就是一个策略加一个独立的模拟账户。它每个交易日用免费价格做一次决定，风控和实盘一样，让你在投入真钱之前"
        "比较不同策略。",
        "每日任务在工作日每 15 分钟醒一次，每个交易日让每个开着的智能体运行一次：纽约时间 15:40 之后、收盘之前（提前收盘的"
        "日子是收盘前 20 分钟）。订单按当时价格在模拟账户里成交。",
        "一个想法建一个智能体，让它们跑几周，在排行榜上和 SPY 比。几天的收益基本是噪音。",
    )),
    "agent_card": _e((
        "Agent card",
        "One agent at a glance.",
        "- Equity: cash plus the value of everything held.\n"
        "- Return: equity ÷ starting cash - 1, since the date shown.\n"
        "- Today: change since the previous recorded day.\n"
        "- vs SPY: the agent's return minus SPY's over the same days, in percentage points.\n"
        "- Max DD: the deepest fall of equity from its own previous high.\n"
        "- Invested: the share of equity in stocks (cash, T-bill and Treasury funds left out).\n"
        "- 🧠: the LLM that reviews its buys.",
        "Read return and max drawdown together: a strategy that earns a little less with half the drawdown is "
        "often the better one.",
    ), (
        "智能体卡片",
        "一眼看一个智能体。",
        "- 资产：现金加上所有持仓的市值。\n"
        "- 收益：资产 ÷ 初始资金 - 1，从卡片上的日期算起。\n"
        "- 今日：相对上一个记录日的变化。\n"
        "- vs SPY：同一段时间里智能体收益减 SPY 收益，单位是百分点。\n"
        "- 最大回撤：资产从此前最高点跌下来的最大幅度。\n"
        "- 持股：股票占资产的比例（不算现金、短债和国债基金）。\n"
        "- 🧠：给它的买入做复核的大模型。",
        "收益和最大回撤要一起看：少赚一点但回撤只有一半的策略，往往更好。",
    )),
    "avg_return": _e((
        "Average return",
        "The plain average of every agent's return since each one started.",
        "Sum of the agents' returns ÷ number of agents. Agents that started on different days are averaged as they "
        "are.",
        "A quick read of how the whole group is doing; compare it with the SPY card next to it.",
    ), (
        "平均收益",
        "所有智能体各自从开始以来收益的简单平均。",
        "各智能体收益之和 ÷ 智能体个数。开始日期不同的智能体也直接平均。",
        "快速看整组表现，和旁边的 SPY 卡片对照。",
    )),
    "spy_since": _e((
        "SPY since the start",
        "SPY's return from the day the first agent was created to today: the yardstick for the agents.",
        "SPY's latest close ÷ its close the day before the first agent was created - 1 (price only). Each agent's "
        "card compares it with SPY over that agent's own days.",
        "An agent that beats this with a smaller drawdown is doing its job.",
    ), (
        "SPY 同期收益",
        "从第一个智能体创建那天到今天，SPY 的收益：衡量智能体的标尺。",
        "SPY 最新收盘价 ÷ 第一个智能体创建前一天的收盘价 - 1（只算价格）。每张智能体卡片则按它自己的日期和 SPY 比。",
        "收益超过它、回撤又更小的智能体，就是做得好的。",
    )),
    "schedule": _e((
        "Daily run",
        "Trades every active agent once per trading day without you opening anything.",
        "A LaunchAgent on macOS, a crontab line on Linux or a scheduled task on Windows wakes every 15 minutes on "
        "weekdays. It trades after 15:40 New York time and before the close (20 minutes before an early close), "
        "skips holidays, catches up after the computer sleeps, and keeps going when one agent fails.",
        "Install it once with `riskaware agents schedule --install`. Run now trades at once instead.",
    ), (
        "每日运行",
        "每个交易日自动让所有开着的智能体交易一次，不需要你打开任何东西。",
        "macOS 上是 LaunchAgent，Linux 上是 crontab，Windows 上是计划任务，工作日每 15 分钟醒一次。纽约时间 15:40 之后、"
        "收盘之前交易（提前收盘的日子是收盘前 20 分钟），跳过节假日，电脑睡眠醒来后会补跑，一个智能体出错不影响其他的。",
        "用 `riskaware agents schedule --install` 装一次就行。「现在运行」会立刻交易。",
    )),
    "llm_review": _e((
        "LLM review",
        "An optional second opinion on each buy. A bull analyst argues for it, a bear analyst against, and a risk "
        "judge weighs both.",
        "The judge returns an adjustment of at most ±0.15 to the conviction, or a veto with a reason. It never "
        "creates a trade or changes a limit, and the risk manager still decides the size. News reaches it only as "
        "quoted text. Every prompt and raw reply is kept in the agent's journal.",
        "Pick it per agent. It only looks at buy candidates, so its cost grows with how often the agent buys. Free "
        "local models (Ollama) work; `claude-cli`, `codex-cli` and `gemini-cli` use your subscription instead of "
        "an API key.",
    ), (
        "LLM 复核",
        "对每笔买入的可选第二意见。多方分析师替它说话，空方分析师反对，风控裁判权衡两边。",
        "裁判给出最多 ±0.15 的信心调整，或者带理由的否决。它不能自己发起交易，也不能改任何限制，买多少仍由风控决定。新闻只"
        "以引用文本的形式给它。每次的 prompt 和原始回复都存在智能体的日志里。",
        "按智能体单独选。它只看买入候选，所以花费随买入次数增加。免费的本地模型（Ollama）可以用；`claude-cli`、"
        "`codex-cli`、`gemini-cli` 走你的订阅，不需要 API key。",
    )),
    # ------------------------------------------------------------------------------------------- signals
    "conviction": _e((
        "Conviction",
        "How sure the strategy is about a signal, from 0 to 1. The risk manager buys only at or above the profile's "
        "minimum (balanced 0.58, conservative 0.65, aggressive 0.52), and buys more the higher it is.",
        "For the built-in risk_aware strategy, a weighted average of the parts it has:\n"
        "- technical (TA) score: weight 0.55\n"
        "- earnings quality ÷ 100: weight 0.20 (live only; backtests have no fundamentals)\n"
        "- news: (news score + 1) ÷ 2, weight 0.15\n"
        "- macro: from the regime, weight 0.10\n"
        "The weights are rescaled over the parts available, a single-source red flag subtracts 0.10, and the "
        "result is kept between 0 and 1. Other strategies set conviction their own way.",
        "Above the minimum, a position grows from 60% to 100% of the position cap as conviction rises to 1. It is a "
        "ranking of setups, not a probability of profit.",
    ), (
        "信心",
        "策略对一个信号有多大把握，0 到 1。风控只在它达到档位下限时才买（平衡 0.58、保守 0.65、激进 0.52），而且越高买得"
        "越多。",
        "内置的 risk_aware 策略对拿得到的几项做加权平均：\n"
        "- 技术面（TA）分：权重 0.55\n"
        "- 财报质量 ÷ 100：权重 0.20（只在实盘有；回测没有基本面数据）\n"
        "- 新闻：(新闻分 + 1) ÷ 2，权重 0.15\n"
        "- 宏观：由市场状态决定，权重 0.10\n"
        "权重只在拿得到的几项之间重新分配；有单一来源的风险信号时减 0.10；结果限制在 0 到 1。其他策略按各自的方式给信心。",
        "超过下限之后，信心从下限升到 1，仓位从单票上限的 60% 增加到 100%。它是给形态排序用的，不是赚钱的概率。",
    )),
    "technical": _e((
        "Technical (TA) score",
        "How good the chart setup is today, from 0 to 1, given by the technical agent. It is the largest part of "
        "conviction (55%).",
        "Each matching setup has a score (bonuses inside each range for RSI, a bullish close, quiet volume or "
        "relative strength):\n"
        "- pullback to a rising EMA50: 0.55 to 0.75\n"
        "- EMA100 support: 0.60 to 0.75\n"
        "- EMA200 support after an 8%+ pullback: 0.58 to 0.83\n"
        "- oversold above EMA200: 0.50 to 0.75\n"
        "- quality dip: 0.62 to 0.75 (0.52 when fundamentals are unknown)\n"
        "- 55-day breakout: 0.55 to 0.70; trend continuation: 0.50 to 0.60\n"
        "Start from the best setup allowed in today's regime, add 0.05 for each other setup scoring 0.5 or more (up "
        "to three), 0.05 if the EMAs are stacked (price > EMA50 > EMA100 > EMA200) and 0.05 if the stock beat SPY "
        "over 126 days; subtract 0.15 in a long-term downtrend (unless it is a quality dip) and 0.25 if the price is "
        "more than 3 ATR above EMA50 (unless it is a breakout). Keep between 0 and 1.",
        "Around 0.7 or more is a clean setup; under 0.5 the chart case is weak. Breakouts need RISK_ON and the "
        "aggressive profile; trend continuation needs RISK_ON.",
    ), (
        "技术面（TA）分",
        "今天的图形形态有多好，0 到 1，由技术面智能体给出。它是信心里最大的一项（55%）。",
        "每个命中的形态有自己的分数（区间内按 RSI、阳线收盘、缩量、相对强度加分）：\n"
        "- 回踩向上的 EMA50：0.55 到 0.75\n"
        "- EMA100 支撑：0.60 到 0.75\n"
        "- 回调 8% 以上后到 EMA200 支撑：0.58 到 0.83\n"
        "- EMA200 之上超跌：0.50 到 0.75\n"
        "- 优质股大跌：0.62 到 0.75（基本面未知时 0.52）\n"
        "- 55 日突破：0.55 到 0.70；趋势延续：0.50 到 0.60\n"
        "从今天市场状态允许的最好形态开始，其他得分 ≥ 0.5 的形态每个加 0.05（最多三个），均线多头排列（价格 > EMA50 > "
        "EMA100 > EMA200）加 0.05，126 天跑赢 SPY 加 0.05；长期下跌趋势里减 0.15（优质股大跌除外），价格高出 EMA50 "
        "超过 3 个 ATR 减 0.25（突破除外）。最后限制在 0 到 1。",
        "大约 0.7 以上是干净的形态；0.5 以下图形上的理由很弱。突破要偏多状态加激进档；趋势延续要偏多状态。",
    )),
    "earnings": _e((
        "Earnings quality",
        "A 0 to 100 check of the company's fundamentals by the earnings agent. A stock that fell needs healthy "
        "numbers before the agent buys the dip.",
        "Points from Yahoo Finance data:\n"
        "- revenue growth year over year: up to 30\n"
        "- net margin: up to 20\n"
        "- positive free cash flow: 15\n"
        "- debt to equity: up to 10\n"
        "- EPS beats in the last four quarters: up to 15\n"
        "- gross-margin trend: up to 10\n"
        "Verdict: QUALITY at the profile's minimum or more (balanced 60), BROKEN under 35, NEUTRAL between, and "
        "UNKNOWN when four or more items are missing. ETFs pass.",
        "BROKEN blocks dip buys (a falling knife). The score ÷ 100 is 20% of conviction. Backtests run without it, "
        "so dip buys there use a technical stand-in.",
    ), (
        "财报质量",
        "财报智能体对公司基本面的检查，0 到 100。股价跌了以后，基本面要健康，智能体才会抄底。",
        "按 Yahoo Finance 的数据打分：\n"
        "- 营收同比增长：最多 30\n"
        "- 净利率：最多 20\n"
        "- 自由现金流为正：15\n"
        "- 负债权益比：最多 10\n"
        "- 最近四个季度每股收益超预期：最多 15\n"
        "- 毛利率趋势：最多 10\n"
        "结论：达到档位下限（平衡档 60）为优质，低于 35 为基本面差，中间为中性；缺四项以上为未知。ETF 直接通过。",
        "基本面差会挡住抄底（接飞刀）。分数 ÷ 100 占信心的 20%。回测没有这项，所以回测里的抄底用技术面的替代条件。",
    )),
    "news_part": _e((
        "News part of conviction",
        "The verified news for the stock, turned into a 0 to 1 part of conviction.",
        "(news score + 1) ÷ 2, so 0.5 means neutral news. Without news this part is left out. Weight 0.15.",
        "A confirmed red flag blocks the buy before this part is even used.",
    ), (
        "信心里的新闻分",
        "这只股票经过核实的新闻，换算成信心里 0 到 1 的一项。",
        "(新闻分 + 1) ÷ 2，所以 0.5 表示新闻中性。没有新闻时这一项不算。权重 0.15。",
        "已确认的风险信号会在用到这一项之前直接挡住买入。",
    )),
    "macro_part": _e((
        "Macro part of conviction",
        "The market's backdrop as a 0 to 1 part of conviction.",
        "From the regime: RISK_ON 0.80, NEUTRAL 0.55, UNCERTAIN 0.40, RISK_OFF 0.30. For dip setups it is "
        "(100 - fear & greed) ÷ 100 when that is higher, so fear helps dip buys. Weight 0.10.",
        "It nudges conviction; the regime itself already limits which setups may buy and how big.",
    ), (
        "信心里的宏观分",
        "市场大环境换算成信心里 0 到 1 的一项。",
        "按市场状态：偏多 0.80、中性 0.55、不确定 0.40、避险 0.30。抄底类形态如果 (100 - 恐慌贪婪) ÷ 100 更高就用它，所以"
        "恐慌有利于抄底。权重 0.10。",
        "它只是微调信心；市场状态本身已经限制了能买哪些形态、买多大。",
    )),
    "setups": _e((
        "Setups",
        "The chart patterns the technical agent found. Each regime allows a different set.",
        "- EMA50 support: a pullback to a rising EMA50 in an uptrend\n"
        "- EMA100 / EMA200 support: the low reached the line and the close held near it; EMA200 needs an 8%+ "
        "pullback from the 52-week high\n"
        "- mean reversion: short-term oversold (RSI2 under 10, z-score under -2 or below the lower Bollinger band) "
        "while above EMA200; deep mean reversion: RSI2 under 5\n"
        "- quality dip: 15%+ below the 52-week high, RSI14 under 40, stabilizing, and the earnings check passes\n"
        "- trend breakout: a new 55-day high on 1.5x volume with ADX over 20, in an aligned uptrend\n"
        "- trend continuation: stacked EMAs, a pullback to EMA20, RSI14 40 to 60, ADX over 20",
        "More than one setup on the same day adds to the technical score. Setups listed as blocked matched but are "
        "not allowed in today's regime.",
    ), (
        "形态",
        "技术面智能体找到的图形形态。不同市场状态允许的形态不一样。",
        "- EMA50 支撑：上升趋势里回踩向上的 EMA50\n"
        "- EMA100 / EMA200 支撑：最低价碰到均线、收盘守在附近；EMA200 还要求从 52 周高点回调 8% 以上\n"
        "- 均值回归：在 EMA200 之上短期超跌（RSI2 < 10、z 值 < -2 或跌破布林下轨）；深度均值回归：RSI2 < 5\n"
        "- 优质股大跌：比 52 周高点低 15% 以上，RSI14 < 40，开始企稳，并且财报检查通过\n"
        "- 趋势突破：在均线多头排列的上升趋势里，放量 1.5 倍创 55 日新高，ADX > 20\n"
        "- 趋势延续：均线多头排列，回踩 EMA20，RSI14 在 40 到 60，ADX > 20",
        "同一天命中多个形态会提高技术面分。标为「不允许」的形态是命中了，但今天的市场状态不允许。",
    )),
    "passed_risk": _e((
        "Risk check",
        "Whether the risk manager let the signal through. A strategy only says what it wants; the risk manager "
        "decides whether and how much.",
        "It says no when: conviction is below the profile's minimum; buying would break the cash floor (30%), the "
        "position cap (7%), the sector cap (25%) or the stock budget; the profile holds its maximum number of "
        "positions; three new positions or six buys were already made today; earnings are 3 trading days away or "
        "less; the account is 20% or more below its high (buys pause); the stock was sold today; or an add-on came "
        "too soon (the next tranche needs 5 days since the last buy and either a price 1 ATR lower or a winning "
        "position in RISK_ON or NEUTRAL). In UNCERTAIN and RISK_OFF "
        "it refuses every sell except emergency exits. Numbers are for the balanced profile.",
        "A blocked signal shows the reason. Many blocks for the same reason mean the profile is full, not that the "
        "strategy is wrong.",
    ), (
        "风控检查",
        "风控有没有放行这个信号。策略只说想做什么；买不买、买多少由风控决定。",
        "下面任何一条都会拒绝：信心低于档位下限；买了会突破现金底线（30%）、单票上限（7%）、行业上限（25%）或股票预算；"
        "持仓数已到上限；今天已经新开了三个仓位或买了六次；离财报 3 个交易日以内；账户比最高点低 20% 以上（暂停买入）；这只"
        "股票今天刚卖过；加仓太快（下一批要距上次买入 5 天，并且价格低了 1 个 ATR，或者在偏多、中性状态下持仓在赚钱）。"
        "不确定和避险时，除紧急退出外的卖出全部拒绝。数字按平衡档。",
        "被拦下的信号会写明原因。很多信号因为同一个原因被拦，说明是档位满了，不是策略错了。",
    )),
    "signals": _e((
        "Today's signals",
        "What the chosen strategy would like to buy or sell at today's prices, before any order is placed.",
        "The strategy runs on the latest daily data for the stock list, then each signal goes through the risk "
        "check. Run scan computes it now (prices, the macro calendar and news are fetched first).",
        "Read the conviction, the setups and the reason together. A signal that fails the risk check would not be "
        "traded.",
    ), (
        "今天的信号",
        "所选策略按今天的价格想买或想卖什么，还没有下单。",
        "策略在股票池的最新日线数据上运行一遍，每个信号再过一遍风控检查。「开始扫描」会马上计算（先取价格、宏观日历和"
        "新闻）。",
        "信心、形态和理由要一起看。没通过风控的信号不会交易。",
    )),
    # ------------------------------------------------------------------------------------------- controls
    "strategy": _e((
        "Strategy",
        "Which strategy decides. risk_aware is the built-in multi-agent strategy; the others are classic single "
        "rules to compare against; entries starting with AI were saved from the strategy lab, where an AI wrote "
        "them through MCP.",
        "Every strategy runs through the same risk manager, so differences come from the rules, not from sizing.",
        "Start with risk_aware, then compare it with one or two others on the same stock list.",
    ), (
        "策略",
        "由哪个策略做决定。risk_aware 是内置的多智能体策略；其他是经典的单一规则，用来对比；以 AI 开头的是在策略实验室里"
        "由 AI 通过 MCP 写好并保存的。",
        "所有策略都走同一个风控，所以结果的差别来自规则本身，不是仓位大小。",
        "先用 risk_aware，再在同一个股票池上和一两个别的策略对比。",
    )),
    "compare": _e((
        "Compare with",
        "Strategies to run on the same data and the same risk engine, for comparison.",
        "Each one is backtested separately over the same window and stock list; SPY buy and hold is always shown "
        "too.",
        "buy_and_hold buys the list once and holds, which shows what the selection alone did.",
    ), (
        "对比策略",
        "在同样的数据和同一个风控下一起跑的策略，用来对比。",
        "每个策略在同一段时间、同一个股票池上单独回测；SPY 买入持有总会一起显示。",
        "buy_and_hold 把整个股票池买一次然后一直拿着，能看出单靠选股本身的效果。",
    )),
    "universe": _e((
        "Stock list",
        "Which stocks the strategy may trade.",
        "- megacap: 56 of the largest US companies across all sectors\n"
        "- tech: 18 technology and growth names\n"
        "- defensive: 20 dividend payers and defensive names\n"
        "- etf: 6 broad-market ETFs and the 11 sector ETFs\n"
        "- sectors: the 11 sector ETFs",
        "These are today's large caps, so long backtests on them carry survivorship bias: they are in the list "
        "because they did well. Bundled 2026 news covers the stocks of megacap, tech, defensive and core.",
    ), (
        "股票池",
        "策略可以交易哪些股票。",
        "- megacap：各行业里美国最大的 56 家公司\n"
        "- tech：18 只科技和成长股\n"
        "- defensive：20 只分红股和防御股\n"
        "- etf：6 只大盘 ETF 和 11 只行业 ETF\n"
        "- sectors：11 只行业 ETF",
        "这些都是今天的大盘股，在上面做长期回测有幸存者偏差：它们在名单里，正是因为过去涨得好。自带的 2026 年新闻覆盖 "
        "megacap、tech、defensive 和 core 里的股票。",
    )),
    "dates": _e((
        "Backtest window",
        "The first and last day of the backtest.",
        "Signals use each day's close and orders fill the next day. About 480 calendar days of prices before the "
        "start are loaded too, so indicators like the EMA200 are ready on the first day.",
        "Use several years that include a sell-off (2020, 2022) to see how the strategy behaves in bad markets, not "
        "only in good ones.",
    ), (
        "回测区间",
        "回测的第一天和最后一天。",
        "每天用收盘价出信号，第二天成交。开始日之前大约 480 天的价格也会一起加载，让 EMA200 这类指标从第一天起就能用。",
        "选几年、并且包含大跌的区间（2020、2022），看策略在坏行情里怎么样，而不只是好行情。",
    )),
    "cash": _e((
        "Initial cash",
        "The starting money of the simulated account.",
        "Position sizes are fractions of equity, so results scale with it; orders below $100 are skipped.",
        "Use roughly the amount you would really trade: options strategies such as the wheel need about $1M on the "
        "megacap list.",
    ), (
        "初始资金",
        "模拟账户的起始资金。",
        "仓位按资产的比例算，所以结果和资金大小成比例；小于 100 美元的订单会被跳过。",
        "用和你真实打算交易差不多的金额：wheel 这类期权策略在 megacap 上大约需要 100 万美元。",
    )),
    "profile": _e((
        "Risk profile",
        "The set of limits the risk manager enforces. The strategy is the same; the profile decides how much risk "
        "it may take.",
        "- Conservative: cash floor 30% plus 15% Treasuries, 5% per stock, 20% per sector, up to 20 positions, "
        "minimum conviction 0.65, hard stop -25%, at most 2 new positions a day, buys pause at -15%.\n"
        "- Balanced: cash floor 30% plus 5% Treasuries, 7% per stock, 25% per sector, up to 15 positions, minimum "
        "conviction 0.58, hard stop -30%, 3 new positions a day, buys pause at -20%.\n"
        "- Aggressive: cash floor 30%, no Treasuries, 10% per stock, 35% per sector, up to 12 positions, minimum "
        "conviction 0.52, hard stop -35%, breakouts on, 4 new positions a day, buys pause at -30%, up to 7% in "
        "speculative names.",
        "Pick the one whose worst case you could sit through. Every profile keeps at least 30% in cash or T-bills.",
    ), (
        "风险档位",
        "风控执行的一整套限制。策略本身不变，档位决定它能承担多少风险。",
        "- 保守：现金底线 30% 加 15% 国债，单票 5%，单行业 20%，最多 20 只，信心下限 0.65，硬止损 -25%，每天最多新开 2 只，"
        "回撤 15% 暂停买入。\n"
        "- 平衡：现金底线 30% 加 5% 国债，单票 7%，单行业 25%，最多 15 只，信心下限 0.58，硬止损 -30%，每天新开 3 只，回撤 "
        "20% 暂停买入。\n"
        "- 激进：现金底线 30%，不配国债，单票 10%，单行业 35%，最多 12 只，信心下限 0.52，硬止损 -35%，允许突破，每天新开 "
        "4 只，回撤 30% 暂停买入，高波动股最多 7%。",
        "选一个最坏情况你也扛得住的。每个档位都至少保留 30% 现金或短债。",
    )),
    "own_file": _e((
        "Your own strategy file",
        "Backtest a strategy you wrote yourself instead of the one picked above.",
        "Give a path like `path/to/my_strategy.py` or `my_strategy.py:ClassName`. The file needs one Strategy "
        "subclass with an `on_bar(ctx)` method; `riskaware new-strategy NAME` writes a template.",
        "For code an AI wrote for you, `riskaware lab FILE` first checks it for look-ahead.",
    ), (
        "你自己的策略文件",
        "回测你自己写的策略，代替上面选的那个。",
        "填路径，比如 `path/to/my_strategy.py` 或 `my_strategy.py:ClassName`。文件里要有一个 Strategy 子类，带 "
        "`on_bar(ctx)` 方法；`riskaware new-strategy 名字` 会生成模板。",
        "AI 帮你写的代码，先用 `riskaware lab 文件` 检查有没有偷看未来。",
    )),
    "replay_news": _e((
        "Replay news",
        "Lets the strategy read the news in the backtest, as it would have seen it on each day.",
        "Each day the strategy sees only items published before that day's close (16:00 New York, 13:00 on "
        "early-close days), from the last 72 hours; orders fill the next day. 2026 headlines and SEC filings for "
        "the built-in lists ship with the package; other years come from `riskaware news-history`.",
        "Turn it on for news strategies and to see the built-in strategy with its news rules. After the run, News "
        "the backtest saw shows the news behind each trade.",
    ), (
        "回放新闻",
        "让策略在回测里读新闻，读到的就是每一天当时能看到的那些。",
        "每天只看到当天收盘前（美东 16:00，提前收盘的日子 13:00）发布的、最近 72 小时的新闻；订单第二天成交。安装包自带"
        "内置股票池 2026 年的标题和 SEC 公告；其他年份用 `riskaware news-history` 下载。",
        "用新闻的策略要打开它，内置策略的新闻规则也要它才生效。跑完以后，「回测看到的新闻」会列出每笔交易背后的新闻。",
    )),
    # ------------------------------------------------------------------------------------------- backtest
    "cagr": _e((
        "CAGR (a year)",
        "Compound annual growth rate: the steady yearly return that turns the starting equity into the final one.",
        "(final ÷ start)^(1 ÷ years) - 1, with years = calendar days ÷ 365.25.",
        "Compare it with SPY's on the same days. For windows shorter than a year it extrapolates, so read the total "
        "return in All metrics instead.",
    ), (
        "年化收益（CAGR）",
        "复合年增长率：要从起始资产变成最终资产，每年需要的稳定收益率。",
        "(最终 ÷ 起始)^(1 ÷ 年数) - 1，年数 = 日历天数 ÷ 365.25。",
        "和同一段时间 SPY 的比。不到一年的区间会被外推放大，这时看「全部指标」里的总收益。",
    )),
    "sharpe": _e((
        "Sharpe ratio",
        "Return per unit of risk: how much the strategy earned above cash for each unit of day-to-day swings.",
        "Average daily return above the T-bill return (BIL) ÷ the standard deviation of daily returns × √252.",
        "Above 1 is good for a stock strategy, above 2 rare. Compare it with SPY's on the same window.",
    ), (
        "夏普比率",
        "每承担一份风险赚多少：策略每承受一单位日常波动，比现金多赚多少。",
        "日收益超出短债（BIL）收益部分的平均值 ÷ 日收益的标准差 × √252。",
        "股票策略 1 以上算好，2 以上很少见。和同一段时间 SPY 的比。",
    )),
    "max_dd": _e((
        "Max drawdown",
        "The deepest fall from a previous high to a later low.",
        "The lowest value over all days of equity ÷ its running maximum - 1.",
        "This is the pain you would have sat through. If you could not stand that drop, the strategy is too risky "
        "for you whatever its return.",
    ), (
        "最大回撤",
        "从此前的最高点跌到之后最低点的最大幅度。",
        "所有交易日里，资产 ÷ 截至当天的最高资产 - 1 的最小值。",
        "这就是你当时要扛住的跌幅。扛不住这个跌幅的话，不管收益多高，这个策略对你都太冒险。",
    )),
    "worst_year": _e((
        "Worst year",
        "The worst calendar-year return in the window.",
        "Equity at each year end ÷ equity at the previous year end - 1; a partial first or last year counts from "
        "the start or to the end.",
        "Most people judge a strategy by its worst year. Compare it with SPY's worst year.",
    ), (
        "最差年份",
        "区间内最差的自然年收益。",
        "每年年底资产 ÷ 上一年年底资产 - 1；不满一年的第一年或最后一年，从开始日算起或算到结束日。",
        "多数人会按最差的那一年评价一个策略。和 SPY 的最差年份比。",
    )),
    "news_seen": _e((
        "News the backtest saw",
        "What the backtest knew about one stock at one close: the news, its verdict, and what the strategy did the "
        "next day.",
        "For the day you pick, only items published before that close (16:00 New York, 13:00 on early-close days) "
        "and within the last 72 hours are shown, exactly what the strategy could read. Each trade in the picker "
        "maps to the close it was decided at: the trading day before it filled.",
        "Pick a trade to check why it happened, or any stock and day to see what the news looked like then.",
    ), (
        "回测看到的新闻",
        "回测在某个收盘时对一只股票知道什么：新闻、对新闻的判断，以及策略第二天做了什么。",
        "对你选的那天，只显示收盘前（美东 16:00，提前收盘的日子 13:00）、最近 72 小时内发布的新闻，正好是策略当时能读到的。"
        "下拉框里每笔交易对应它做决定的那个收盘：成交日的前一个交易日。",
        "选一笔交易，核对它为什么发生；或者任选股票和日期，看当时的新闻是什么样。",
    )),
    # ------------------------------------------------------------------------------------------- news
    "news_score": _e((
        "News score",
        "The verified news for one stock over the last 72 hours in one number, from -1 (bad) to +1 (good).",
        "Headlines are grouped into stories and each story gets a sentiment from its headline. The score is a "
        "weighted average: confirmed stories weigh 1, single-source stories 0.2, and every weight halves each 48 "
        "hours. Opinion pieces and debunked stories are left out. The divisor is at least 1, so one weak story "
        "moves the score only a little.",
        "In the built-in strategy, (score + 1) ÷ 2 is 15% of conviction, and a score of -0.5 or lower with "
        "confirmed stories blocks buys.",
    ), (
        "新闻分",
        "一只股票最近 72 小时经过核实的新闻，压成一个数，-1（利空）到 +1（利好）。",
        "标题先按内容归成一件件「报道」，每篇报道按标题打情绪分。新闻分是加权平均：已确认的报道权重 1，单一来源的 0.2，"
        "每过 48 小时权重减半。观点文章和已被辟谣的报道不算。分母至少按 1 算，所以一篇弱报道只会让分数动一点点。",
        "在内置策略里，(分数 + 1) ÷ 2 占信心的 15%；有已确认报道、分数又 ≤ -0.5 时，挡住买入。",
    )),
    "confirmed": _e((
        "Confirmed stories",
        "Stories the verifier counts as established.",
        "- PRIMARY: the company's own SEC 8-K filing\n"
        "- ISSUER: the company's own press release on a wire (Business Wire, PR Newswire, GlobeNewswire and others)\n"
        "- CONFIRMED: at least two independent outlets\n"
        "Outlets that only repeat a release, and several wires carrying it, do not count as independent.",
        "Only confirmed stories move the score fully, and only red flags on them block buys.",
    ), (
        "已确认的报道",
        "核实程序认为可信的报道。",
        "- 一手来源：公司自己的 SEC 8-K 公告\n"
        "- 公司通稿：公司通过新闻通稿服务发的稿（Business Wire、PR Newswire、GlobeNewswire 等）\n"
        "- 已确认：至少两家独立媒体报道\n"
        "只是转载通稿的媒体、以及多家通稿服务同时发同一篇，都不算独立确认。",
        "只有已确认的报道会完整计入新闻分，也只有它们上面的风险信号会挡住买入。",
    )),
    "single_source": _e((
        "Single-source stories",
        "Stories carried by one outlet only, not yet confirmed by a second one or by the company.",
        "They weigh 0.2 in the news score instead of 1.",
        "A red flag on one lowers the built-in strategy's conviction by 0.10 but never forces a sale: one headline "
        "can be wrong or planted.",
    ), (
        "单一来源的报道",
        "只有一家媒体报道、还没有第二家或公司本身证实的报道。",
        "在新闻分里权重 0.2，而不是 1。",
        "上面有风险信号时，内置策略的信心减 0.10，但永远不会因此卖出：一条标题可能是错的，也可能是有人故意放的。",
    )),
    "red_flags": _e((
        "Red flags",
        "Phrases in confirmed stories that signal trouble.",
        "- Severe: fraud, accounting problems or restatements, going concern, bankruptcy, delisting, SEC or "
        "criminal charges, an auditor leaving or a material weakness, a short-seller report.\n"
        "- Moderate: an investigation, a guidance cut, the CEO or CFO leaving, an FDA rejection, a dividend cut, a "
        "data breach, a recall.\n"
        "Found by phrase rules in the headline. Negated or denied phrases (\"denies bankruptcy report\") do not "
        "count, and a denial at least as well sourced clears them.",
        "A confirmed red flag blocks new buys. A severe one makes the built-in strategy sell only when the company "
        "itself backs it: its own 8-K, or its release confirming what outlets reported at least an hour earlier.",
    ), (
        "风险信号",
        "已确认报道里提示有麻烦的说法。",
        "- 严重：欺诈、会计问题或财报重述、持续经营疑虑、破产、退市、SEC 或刑事指控、审计师离任或重大缺陷、做空报告。\n"
        "- 中等：被调查、下调业绩指引、CEO 或 CFO 离职、FDA 拒绝、削减分红、数据泄露、召回。\n"
        "按标题里的说法规则识别。被否定或辟谣的说法（「否认破产报道」）不算，来源不弱于原报道的辟谣会撤销它们。",
        "已确认的风险信号挡住新的买入。严重信号只有在公司自己证实时才会让内置策略卖出：公司自己的 8-K，或者公司通稿证实了"
        "媒体至少早一小时报道的内容。",
    )),
    "news_status": _e((
        "Status",
        "How well sourced a story is.",
        "- ✅ primary source: the company's SEC filing\n"
        "- ✅ company release: its own press release on a wire\n"
        "- ✅ confirmed: two or more independent outlets\n"
        "- single source: one outlet\n"
        "- opinion: listicles and 'should you buy' pieces, left out of the score\n"
        "- denial: a story that denies or retracts a report; it never confirms what it denies",
        "Trust the ✅ rows; treat single-source rows as rumors until someone else reports them.",
    ), (
        "状态",
        "一篇报道的来源有多可靠。",
        "- ✅ 一手来源：公司的 SEC 公告\n"
        "- ✅ 公司通稿：公司自己通过通稿服务发的稿\n"
        "- ✅ 已确认：两家及以上独立媒体\n"
        "- 单一来源：只有一家媒体\n"
        "- 观点文章：盘点类、「该不该买」类文章，不计入分数\n"
        "- 辟谣：否认或撤回某篇报道的报道；它永远不会反过来证实被它否认的内容",
        "✅ 的可以信；单一来源的先当传闻，等别家也报道了再说。",
    )),
    "sentiment": _e((
        "Sentiment",
        "How good or bad a headline reads, from -1 to +1.",
        "Weighted good and bad phrases in the headline: (good - bad) ÷ (good + bad + 1). Words after 'despite' count "
        "a quarter, negated or denied phrases count 0, a hoax story scores 0, and a story with a severe red flag is "
        "capped at -0.5. A sentiment supplied by the data source replaces this.",
        "A rough reading of the words, not of the facts; the news score weighs it by how well the story is "
        "sourced.",
    ), (
        "情绪",
        "一条标题读起来是好消息还是坏消息，-1 到 +1。",
        "标题里好词和坏词按权重计数：(好 - 坏) ÷ (好 + 坏 + 1)。「despite」后面的词只算四分之一，被否定或辟谣的说法算 0，"
        "假新闻类报道得 0，带严重风险信号的报道最高只到 -0.5。数据源自带情绪分时用它代替。",
        "这是对用词的粗略判断，不是对事实的判断；新闻分会按来源可靠程度给它加权。",
    )),
    # ------------------------------------------------------------------------------------------- account
    "equity": _e((
        "Account equity",
        "The broker account's total value: cash plus every position at market price.",
        "As reported by the broker in the config (the built-in paper account by default).",
        "All the profile's limits are fractions of this number.",
    ), (
        "账户资产",
        "券商账户的总价值：现金加上所有持仓的市值。",
        "由配置里的券商报告（默认是内置的模拟账户）。",
        "档位里的所有限制都是这个数的比例。",
    )),
    "cash_floor": _e((
        "Cash and T-bills",
        "The share of equity in cash or the T-bill fund, against the profile's floor.",
        "(cash + the T-bill ETF, SGOV by default) ÷ equity. The floor is 30% for every profile.",
        "The risk manager never buys below the floor. Idle cash above it is parked in the T-bill fund, so it still "
        "earns interest.",
    ), (
        "现金加短债",
        "现金和短期国债基金占资产的比例，与档位的底线对照。",
        "(现金 + 短债 ETF，默认 SGOV) ÷ 资产。三个档位的底线都是 30%。",
        "风控不会在底线以下买入。底线以上的闲置现金放进短债基金，照样有利息。",
    )),
    "stocks": _e((
        "Stocks",
        "The share of equity in stocks.",
        "Value of all positions except the cash fund and the Treasury fund ÷ equity.",
        "Under the balanced profile it can reach about 65% (100% minus the 30% floor and the 5% Treasury sleeve).",
    ), (
        "股票",
        "股票占资产的比例。",
        "除现金基金和国债基金以外所有持仓的市值 ÷ 资产。",
        "平衡档最多大约 65%（100% 减去 30% 底线和 5% 国债仓位）。",
    )),
    "treasury": _e((
        "Treasuries",
        "The share of equity in the intermediate Treasury fund (IEF by default), the profile's bond sleeve.",
        "Value of the Treasury fund ÷ equity. Target 5% for balanced, 15% for conservative, none for aggressive; "
        "rebalanced when it drifts more than 2 points.",
        "It cushions stock sell-offs, when Treasuries often rise.",
    ), (
        "国债",
        "中期国债基金（默认 IEF）占资产的比例，也就是档位里的债券仓位。",
        "国债基金市值 ÷ 资产。目标：平衡 5%、保守 15%、激进不配；偏离超过 2 个百分点就再平衡。",
        "股票大跌时国债常常上涨，起缓冲作用。",
    )),
}

METRICS: dict[str, tuple[str, str]] = {
    "total_return": ("Final equity ÷ starting equity - 1.", "最终资产 ÷ 起始资产 - 1。"),
    "cagr": ("The steady yearly return that gives the same result: (final ÷ start)^(1 ÷ years) - 1.",
             "得到同样结果所需的稳定年收益：(最终 ÷ 起始)^(1 ÷ 年数) - 1。"),
    "volatility": ("Standard deviation of daily returns × √252: how much it swings in a year.",
                   "日收益标准差 × √252：一年里大约波动多少。"),
    "sharpe": ("Average daily return above T-bills ÷ daily standard deviation × √252. Above 1 is good.",
               "日收益超出短债的平均值 ÷ 日标准差 × √252。1 以上算好。"),
    "sortino": ("Like Sharpe, but divides by the swings of down days only.", "和夏普一样，但只用下跌日的波动做分母。"),
    "max_drawdown": ("Deepest fall from a previous high to a later low.", "从此前最高点跌到之后最低点的最大幅度。"),
    "calmar": ("CAGR ÷ |max drawdown|: return per unit of the worst fall.", "年化收益 ÷ |最大回撤|：每单位最大跌幅换来的收益。"),
    "best_year": ("Best calendar-year return.", "最好的自然年收益。"),
    "worst_year": ("Worst calendar-year return.", "最差的自然年收益。"),
    "avg_exposure": ("Average share of equity in stocks.", "股票占资产的平均比例。"),
    "avg_cash_ratio": ("Average share of equity in cash and the T-bill fund.", "现金和短债基金占资产的平均比例。"),
    "min_cash_ratio": ("Lowest share of equity in cash and the T-bill fund.", "现金和短债基金占资产的最低比例。"),
    "win_rate": ("Share of closed trades (sells) that made money.", "已平仓交易（卖出）里赚钱的比例。"),
    "profit_factor": ("Money made on winning sells ÷ money lost on losing ones. Above 1 means net profit.",
                      "盈利卖出赚的钱 ÷ 亏损卖出亏的钱。大于 1 表示总体赚钱。"),
    "avg_holding_days": ("Average days a position was held, weighted by its size.", "持仓的平均天数，按金额加权。"),
    "round_trips": ("Number of sells that closed or trimmed a position.", "平掉或减仓的卖出次数。"),
    "trades": ("Number of orders filled, buys and sells.", "成交的订单数，买和卖都算。"),
    "turnover": ("Value traded per year ÷ average equity ÷ 2: how much of the portfolio is replaced each year.",
                 "每年成交金额 ÷ 平均资产 ÷ 2：每年大约换掉多少仓位。"),
}


def entry(key: str, lang: str = "en") -> dict[str, str] | None:
    """The popup text for ``key`` in ``lang`` ("en" or "zh"), or None for an unknown key."""
    item = GLOSSARY.get(key)
    return None if item is None else item["zh" if lang == "zh" else "en"]


def _md(text: str) -> str:
    """Markdown for a help tooltip: paragraphs and lists separated by blank lines, list items kept together."""
    out: list[str] = []
    prev_bullet = None
    for line in (text or "").strip().splitlines():
        line = line.strip()
        if not line:
            continue
        bullet = line.startswith("- ")
        if out and (not bullet or not prev_bullet):
            out.append("")
        out.append(line)
        prev_bullet = bullet
    return "\n".join(out)


def help_md(key: str, lang: str = "en") -> str:
    """The same text as Markdown, for Streamlit's ``help=`` tooltips on inputs and table columns."""
    e = entry(key, lang)
    if e is None:
        return ""
    zh = lang == "zh"
    sep = "：" if zh else ": "
    parts = [f"**{e['title']}**"]
    for k, h in zip(("what", "how", "use"), HEADINGS["zh" if zh else "en"], strict=True):
        if e.get(k):
            body = _md(e[k])
            parts.append(f"**{h}**{sep}" + ("\n\n" + body if body.startswith("- ") else body))
    return "\n\n".join(parts)


def metric_help(key: str, lang: str = "en") -> str:
    pair = METRICS.get(key)
    return "" if pair is None else pair[1 if lang == "zh" else 0]
