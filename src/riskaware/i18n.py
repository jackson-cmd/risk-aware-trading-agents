"""English / Chinese labels for the CLI, the dashboard and reports.

Pick the language with ``language: zh`` in the config, ``--lang zh`` on the CLI,
or the RISKAWARE_LANG environment variable. Agent reasons stay in English; every
heading, label, regime, setup and event name is translated.
"""

from __future__ import annotations

import os

LANGS = ("en", "zh")

M: dict[str, tuple[str, str]] = {
    # app
    "app.name": ("Risk-Aware Trading Agents", "风险感知交易智能体"),
    "app.tagline": ("Risk-aware AI trading agents for US stocks", "会看宏观、懂风控的美股 AI 交易智能体"),
    "disclaimer": ("Not financial advice. Paper trade first; live trading is at your own risk.",
                   "不构成投资建议。请先用模拟盘；实盘交易风险自负。"),
    # regimes
    "regime.RISK_ON": ("Risk-on: follow the trend", "偏多：顺势而为"),
    "regime.NEUTRAL": ("Neutral: buy pullbacks, trim into strength", "中性：回调买入，冲高减仓"),
    "regime.UNCERTAIN": ("Uncertain: hold, no selling, buy only at strong support",
                         "不确定：持有不卖，只在强支撑位买入"),
    "regime.RISK_OFF": ("Risk-off: hold, buy only quality dips", "避险：持有不卖，只抄优质股"),
    "regime.short.RISK_ON": ("RISK-ON", "偏多"),
    "regime.short.NEUTRAL": ("NEUTRAL", "中性"),
    "regime.short.UNCERTAIN": ("UNCERTAIN", "不确定"),
    "regime.short.RISK_OFF": ("RISK-OFF", "避险"),
    # market panel
    "market.title": ("Market regime", "市场状态"),
    "market.vix": ("VIX (fear index)", "VIX 恐慌指数"),
    "market.term": ("VIX / VIX3M", "VIX 期限结构"),
    "market.fear_greed": ("Fear & greed (computed)", "恐慌贪婪指数（自算）"),
    "market.cnn": ("CNN Fear & Greed", "CNN 恐慌贪婪指数"),
    "market.breadth": ("Sectors above EMA200", "站上 EMA200 的板块"),
    "market.trend": ("S&P 500 trend", "标普 500 趋势"),
    "market.risk_score": ("Risk score (0 calm - 100 stress)", "风险分（0 平静 - 100 紧张）"),
    "market.event_risk": ("Event risk", "事件风险"),
    "market.vix_short": ("VIX", "VIX 恐慌指数"),
    "market.term_short": ("VIX / VIX3M", "VIX 期限结构"),
    "market.fg_short": ("Fear & greed", "恐慌贪婪"),
    "market.cnn_short": ("CNN F&G", "CNN 恐慌贪婪"),
    "market.breadth_short": ("Sector breadth", "板块广度"),
    "market.risk_short": ("Risk score", "风险分"),
    "market.why": ("Why", "依据"),
    "market.policy": ("What the agent does now", "智能体当前的做法"),
    "trend.up": ("up", "上升"),
    "trend.down": ("down", "下降"),
    "trend.mixed": ("mixed", "震荡"),
    "fear.extreme fear": ("extreme fear", "极度恐慌"),
    "fear.fear": ("fear", "恐慌"),
    "fear.neutral": ("neutral", "中性"),
    "fear.greed": ("greed", "贪婪"),
    "fear.extreme greed": ("extreme greed", "极度贪婪"),
    "fear.n/a": ("n/a", "无数据"),
    # events
    "events.title": ("Upcoming macro events", "即将到来的宏观事件"),
    "events.date": ("Date", "日期"),
    "events.event": ("Event", "事件"),
    "events.time": ("Time", "时间"),
    "events.impact": ("Impact", "影响"),
    "events.in": ("In (trading days)", "距今（交易日）"),
    "events.estimated": ("estimated", "估计"),
    "event.FOMC": ("FOMC rate decision", "美联储利率决议 (FOMC)"),
    "event.CPI": ("CPI inflation", "CPI 通胀数据"),
    "event.NFP": ("Jobs report (NFP)", "非农就业报告"),
    "event.PPI": ("PPI producer prices", "PPI 生产者物价"),
    "event.PCE": ("PCE inflation", "PCE 通胀"),
    "event.GDP": ("GDP release", "GDP 数据"),
    "event.OPEX": ("Quarterly options expiry", "季度期权到期日"),
    # signals
    "scan.title": ("Signals", "交易信号"),
    "col.symbol": ("Symbol", "代码"),
    "col.action": ("Action", "操作"),
    "col.conviction": ("Conviction", "信心"),
    "col.setups": ("Setups", "形态"),
    "col.reason": ("Reason", "理由"),
    "col.price": ("Price", "价格"),
    "col.qty": ("Qty", "数量"),
    "col.value": ("Value", "金额"),
    "col.tag": ("Type", "类型"),
    "col.status": ("Status", "状态"),
    "col.weight": ("Weight", "仓位"),
    "col.gain": ("Gain", "盈亏"),
    "col.days": ("Days held", "持有天数"),
    "col.sector": ("Sector", "板块"),
    "col.metric": ("Metric", "指标"),
    "col.strategy": ("Strategy", "策略"),
    "col.benchmark": ("SPY buy & hold", "SPY 买入持有"),
    "action.BUY": ("BUY", "买入"),
    "action.SELL": ("SELL", "卖出"),
    "action.HOLD": ("HOLD", "持有"),
    "setup.ema50_support": ("EMA50 pullback", "回踩 EMA50"),
    "setup.ema100_support": ("EMA100 support", "EMA100 支撑"),
    "setup.ema200_support": ("EMA200 major support", "EMA200 强支撑"),
    "setup.mean_reversion": ("Mean reversion", "均值回归"),
    "setup.deep_mean_reversion": ("Deep oversold", "深度超卖"),
    "setup.quality_dip": ("Quality dip (earnings OK)", "抄底（财报过关）"),
    "setup.trend_breakout": ("Trend breakout", "趋势突破"),
    "setup.trend_continuation": ("Trend continuation", "趋势延续"),
    "setup.call_pullback": ("Call on a pullback", "回调买看涨期权"),
    "setup.cash_secured_put": ("Cash-secured put", "卖现金担保看跌"),
    "setup.covered_call": ("Covered call", "卖备兑看涨"),
    "setup.option_exit": ("Option exit", "期权平仓"),
    "setup.momentum": ("Momentum leader", "动量领先"),
    "setup.rotation_exit": ("Rotation exit", "轮动换出"),
    "setup.donchian_breakout": ("55-day breakout", "55 日突破"),
    "setup.donchian_exit": ("20-day low exit", "跌破 20 日低点"),
    "setup.bollinger_dip": ("Below the lower band", "跌破布林下轨"),
    "setup.bollinger_exit": ("Back at the middle band", "回到布林中轨"),
    "setup.fear_dca": ("Buying fear in steps", "恐慌分批买入"),
    "setup.greed_trim": ("Greed trim", "贪婪减仓"),
    "setup.take_profit": ("Take profit", "止盈减仓"),
    "setup.trend_exit": ("Trend exit", "趋势破位减仓"),
    "setup.hard_stop": ("Hard stop", "硬止损"),
    "setup.thesis_break": ("Thesis break", "逻辑破坏清仓"),
    "tag.entry": ("new position", "新建仓"),
    "tag.tranche": ("add tranche", "分批加仓"),
    "tag.take_profit": ("take profit", "止盈"),
    "tag.trend_exit": ("trend exit", "趋势减仓"),
    "tag.hard_stop": ("hard stop", "硬止损"),
    "tag.thesis_break": ("thesis break", "逻辑破坏"),
    "tag.cap_trim": ("trim overweight", "超配减仓"),
    "tag.floor_restore": ("restore cash floor", "恢复现金底线"),
    "tag.cash_management": ("T-bill cash", "国债现金管理"),
    "tag.treasury_rebalance": ("Treasury sleeve", "国债仓位再平衡"),
    "tag.option_open": ("open option", "开期权仓"),
    "tag.option_close": ("close option", "期权平仓"),
    "tag.option_expiry": ("option expiry", "期权到期"),
    "tag.assignment": ("put assigned", "看跌被行权"),
    "tag.called_away": ("called away", "股票被行权卖出"),
    "scan.none": ("No signals today. Holding is a decision too.", "今天没有信号。持有也是一种决定。"),
    "scan.rejected": ("Filtered by the risk manager", "被风控拦下的信号"),
    # plan / run
    "plan.title": ("Order plan", "下单计划"),
    "plan.none": ("No orders. The risk manager found nothing to do.", "没有订单：风控认为今天无需操作。"),
    "plan.blocked": ("Blocked by safety guards", "被安全开关拦下"),
    "plan.submitted": ("Submitted", "已提交"),
    "plan.dry_run": ("Dry run: nothing was sent.", "预演模式：没有发送任何订单。"),
    "plan.confirm_live": ("Send these orders to your LIVE account?", "确认把这些订单发到实盘账户？"),
    "account.title": ("Account", "账户"),
    "account.equity": ("Equity", "总资产"),
    "account.cash": ("Cash", "现金"),
    "account.cash_total": ("Cash + T-bills", "现金 + 短期国债"),
    "account.stocks": ("Stocks", "股票"),
    "account.treasury": ("Treasury sleeve", "国债仓位"),
    "account.drawdown": ("Drawdown from peak", "距高点回撤"),
    "account.floor": ("Cash floor", "现金底线"),
    # backtest
    "bt.title": ("Backtest", "回测"),
    "bt.period": ("Period", "区间"),
    "bt.report_saved": ("Report saved to", "报告已保存到"),
    "bt.notes": ("Notes", "说明"),
    "bt.equity": ("Equity vs SPY", "净值 vs SPY"),
    "bt.drawdown": ("Drawdown", "回撤"),
    "bt.exposure": ("Stock exposure and cash", "股票仓位与现金"),
    "bt.monthly": ("Monthly returns", "月度收益"),
    "bt.trades": ("Trades", "交易记录"),
    "bt.compare": ("Strategy comparison", "策略对比"),
    "bt.rejections": ("Top reasons signals were filtered", "信号被过滤的主要原因"),
    "bt.regimes": ("Time in each regime", "各市场状态占比"),
    # misc
    "news.title": ("Cross-verified news", "交叉验证后的新闻"),
    "news.score": ("News score", "新闻分"),
    "news.status.confirmed": ("confirmed", "已确认"),
    "news.status.primary": ("primary source", "一手来源"),
    "news.status.issuer": ("company release", "公司通稿"),
    "news.denied": ("denied", "已辟谣"),
    "news.denial": ("denial", "辟谣"),
    "news.status.unconfirmed": ("single source", "单一来源"),
    "news.status.opinion": ("opinion", "观点文章"),
    "news.flags": ("Red flags", "风险信号"),
    "news.sources": ("Sources", "来源"),
    "nh.title": ("Local news archive", "本地新闻库"),
    "nh.items": ("Items", "条数"),
    "nh.first": ("From", "最早"),
    "nh.last": ("To", "最晚"),
    "nh.feeds": ("Sources", "来源"),
    "nh.added": ("New items", "新增"),
    "nh.empty": ("The archive is empty. Build it with: riskaware news-history build",
                 "新闻库是空的，先运行：riskaware news-history build"),
    "nh.asof": ("What a backtest sees at the close of {day}", "回测在 {day} 收盘时能看到的新闻"),
    "nh.none": ("Nothing was published in the lookback window before this close.",
                "这个收盘前的回看窗口里没有新闻。"),
    "nh.private": ("Your own Benzinga or other licensed data stays on this machine; do not commit or share it.",
                   "自己的 Benzinga 等授权数据只留在本机，不要提交到仓库或分享。"),
    "bt.news_off": ("The local news archive is empty; this backtest runs without news.",
                    "本地新闻库是空的，这次回测不用新闻。"),
    "analyze.title": ("Analysis", "分析"),
    "analyze.technical": ("Technical", "技术面"),
    "analyze.fundamental": ("Earnings quality", "财报质量"),
    "analyze.news": ("News", "新闻"),
    "analyze.decision": ("Decision", "结论"),
    "analyze.earnings_in": ("Next earnings in", "距下次财报"),
    "doctor.title": ("Environment check", "环境检查"),
    "halt.on": ("Trading halted. Run `riskaware resume` to re-enable.", "交易已暂停。运行 `riskaware resume` 恢复。"),
    "halt.off": ("Trading re-enabled.", "交易已恢复。"),
}


def current_lang(explicit: str | None = None) -> str:
    lang = (explicit or os.environ.get("RISKAWARE_LANG") or "en").lower()
    if lang.startswith("zh") or lang in ("cn", "chinese", "中文"):
        return "zh"
    return "en"


def t(key: str, lang: str = "en", **kw: object) -> str:
    pair = M.get(key)
    if pair is None:
        text = key.split(".", 1)[-1]
    else:
        text = pair[1] if lang == "zh" else pair[0]
    return text.format(**kw) if kw else text


def regime(name: str, lang: str = "en", short: bool = False) -> str:
    return t(f"regime.{'short.' if short else ''}{name}", lang)


def setup(name: str, lang: str = "en") -> str:
    return t(f"setup.{name}", lang) if f"setup.{name}" in M else name


def tag(name: str, lang: str = "en") -> str:
    return t(f"tag.{name}", lang) if f"tag.{name}" in M else name


def event(kind: str, lang: str = "en") -> str:
    return t(f"event.{kind}", lang) if f"event.{kind}" in M else kind
