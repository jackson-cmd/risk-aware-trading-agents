"""Streamlit dashboard. Start it with ``riskaware dashboard`` (English / 中文 switch in the sidebar)."""

from __future__ import annotations

import os
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from riskaware import i18n
from riskaware import marketcal as mc
from riskaware.config import PROFILE_PRESETS, load_config

st.set_page_config(page_title="Risk-Aware Trading Agents", page_icon="📈", layout="wide")

LANG_LABELS = {"English": "en", "中文": "zh"}
default_lang = os.environ.get("RISKAWARE_LANG", "en")
choice = st.sidebar.radio("Language / 语言", list(LANG_LABELS), index=0 if default_lang != "zh" else 1, horizontal=True)
LANG = LANG_LABELS[choice]


def T(key: str, **kw) -> str:
    return i18n.t(key, LANG, **kw)


REGIME_COLORS = {"RISK_ON": "#2da44e", "NEUTRAL": "#57606a", "UNCERTAIN": "#bf8700", "RISK_OFF": "#d1242f"}


@st.cache_resource(show_spinner=False)
def get_config(profile: str):
    return load_config(os.environ.get("RISKAWARE_CONFIG"), {"profile": profile})


profile = st.sidebar.selectbox(T("col.strategy") if False else ("Risk profile" if LANG == "en" else "风险档位"),
                               list(PROFILE_PRESETS), index=list(PROFILE_PRESETS).index("balanced"))
cfg = get_config(profile)
st.sidebar.caption(T("disclaimer"))
st.sidebar.markdown("[GitHub](https://github.com/jackson-cmd/risk-aware-trading-agents)")


@st.cache_data(ttl=900, show_spinner=False)
def load_market(profile: str):
    from riskaware.engine import MarketData

    md = MarketData(get_config(profile))
    market = md.load([])
    events = md.calendar.upcoming(date.today(), 45)
    spy = md.sym_data[cfg.universe.benchmark].frame.iloc[-260:][["close", "ema50", "ema200"]]
    macro = md.macro.iloc[-260:][["vix", "fear_greed", "regime"]].copy()
    macro["regime"] = macro["regime"].map(lambda r: getattr(r, "value", str(r)))
    return market, events, spy, macro


st.title(T("app.name"))
st.caption(T("app.tagline"))
tab_market, tab_signals, tab_portfolio, tab_bt, tab_news, tab_journal = st.tabs(
    [T("market.title"), T("scan.title"), T("account.title"), T("bt.title"), T("news.title"),
     "Journal" if LANG == "en" else "交易日志"])

# --------------------------------------------------------------------------- market
with tab_market:
    with st.spinner("Loading market data..."):
        market, events, spy, macro = load_market(profile)
    r = market.regime.value
    st.markdown(
        f"<div style='padding:14px 18px;border-radius:10px;background:{REGIME_COLORS[r]}22;"
        f"border:1px solid {REGIME_COLORS[r]}'><span style='font-size:22px;font-weight:700;color:{REGIME_COLORS[r]}'>"
        f"{i18n.regime(r, LANG, short=True)}</span> &nbsp; {i18n.regime(r, LANG)}</div>", unsafe_allow_html=True)
    c = st.columns(6)
    c[0].metric(T("market.vix_short"), f"{market.vix:.1f}" if market.vix else "n/a", help=T("market.vix"))
    c[1].metric(T("market.term_short"), f"{market.vix_ratio:.2f}" if market.vix_ratio else "n/a",
                help="> 1.0 = backwardation (near-term fear)")
    c[2].metric(f"{T('market.fg_short')} · {i18n.t('fear.' + market.fear_label, LANG)}",
                f"{market.fear_greed:.0f}" if market.fear_greed is not None else "n/a", help=T("market.fear_greed"))
    c[3].metric(T("market.cnn_short"), f"{market.cnn_fear_greed:.0f}" if market.cnn_fear_greed is not None else "n/a",
                help=T("market.cnn"))
    c[4].metric(T("market.breadth_short"), f"{market.breadth * 100:.0f}%" if market.breadth is not None else "n/a",
                help=T("market.breadth"))
    c[5].metric(T("market.risk_short"), f"{market.risk_score:.0f}", help=T("market.risk_score"))
    with st.expander(T("market.why"), expanded=True):
        for line in market.reasons:
            st.write("• " + line)
    left, right = st.columns([3, 2])
    with left:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=spy.index, y=spy["close"], name="SPY", line=dict(width=2)))
        fig.add_trace(go.Scatter(x=spy.index, y=spy["ema50"], name="EMA50", line=dict(width=1, dash="dot")))
        fig.add_trace(go.Scatter(x=spy.index, y=spy["ema200"], name="EMA200", line=dict(width=1, dash="dash")))
        fig.update_layout(height=320, margin=dict(l=10, r=10, t=30, b=10), title="SPY")
        st.plotly_chart(fig, width="stretch")
    with right:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=macro.index, y=macro["fear_greed"], name=T("market.fear_greed"), line=dict(width=2)))
        fig.add_trace(go.Scatter(x=macro.index, y=macro["vix"], name="VIX", yaxis="y2", line=dict(width=1)))
        fig.update_layout(height=320, margin=dict(l=10, r=10, t=30, b=10), title=T("market.fear_greed") + " / VIX",
                          yaxis=dict(range=[0, 100]), yaxis2=dict(overlaying="y", side="right"))
        st.plotly_chart(fig, width="stretch")
    st.subheader(T("events.title"))
    st.dataframe(pd.DataFrame([{
        T("events.date"): f"{e.day:%Y-%m-%d %a}",
        T("events.event"): i18n.event(e.kind, LANG) + (f" ({T('events.estimated')})" if e.estimated else ""),
        T("events.time"): e.time, T("events.impact"): e.impact,
        T("events.in"): mc.trading_days_between(date.today(), e.day)} for e in events]),
        hide_index=True, width="stretch")

# --------------------------------------------------------------------------- signals
with tab_signals:
    st.write("Scan the watchlist with the multi-agent strategy (prices, earnings quality, verified news)."
             if LANG == "en" else "用多智能体策略扫描股票池（价格、财报质量、交叉验证新闻）。")
    extra = st.text_input("Symbols (optional, comma separated)" if LANG == "en" else "股票代码（可选，逗号分隔）")
    if st.button("Run scan" if LANG == "en" else "开始扫描", type="primary"):
        from riskaware.engine import TradingEngine

        with st.spinner("Running agents..."):
            eng = TradingEngine(cfg)
            syms = [s.strip().upper() for s in extra.split(",") if s.strip()] or None
            _, signals, _ = eng.scan(syms)
        if not signals:
            st.info(T("scan.none"))
        else:
            st.dataframe(pd.DataFrame([{
                T("col.symbol"): s.symbol, T("col.action"): i18n.t("action." + s.action.value, LANG),
                T("col.conviction"): round(s.strength, 2),
                T("col.setups"): ", ".join(i18n.setup(x, LANG) for x in s.setups[:3]), T("col.reason"): s.reason}
                for s in sorted(signals, key=lambda x: -x.strength)]), hide_index=True, width="stretch")

# --------------------------------------------------------------------------- portfolio
with tab_portfolio:
    st.write(f"Broker: **{cfg.broker.name}**" if LANG == "en" else f"券商：**{cfg.broker.name}**")
    if st.button("Load account" if LANG == "en" else "读取账户"):
        from riskaware.brokers import make_broker

        try:
            b = make_broker(cfg.broker.name, **cfg.broker.options)
            b.connect()
            acct, pos = b.account(), b.positions()
            b.close()
        except Exception as exc:
            st.error(str(exc))
        else:
            eq = acct.equity or 1.0
            cv = sum(p.market_value for p in pos if p.symbol == cfg.universe.cash_vehicle)
            tr = sum(p.market_value for p in pos if p.symbol == cfg.universe.treasury_symbol)
            stocks = sum(p.market_value for p in pos) - cv - tr
            m = st.columns(4)
            m[0].metric(T("account.equity"), f"${acct.equity:,.0f}")
            m[1].metric(T("account.cash_total"), f"{(acct.cash + cv) / eq:.1%}",
                        f"{T('account.floor')} {cfg.risk_profile().cash_floor:.0%}", delta_color="off")
            m[2].metric(T("account.stocks"), f"{stocks / eq:.1%}")
            m[3].metric(T("account.treasury"), f"{tr / eq:.1%}")
            fig = go.Figure(go.Pie(labels=[T("account.cash"), "T-bills", T("account.treasury"), T("account.stocks")],
                                   values=[max(acct.cash, 0), cv, tr, max(stocks, 0)], hole=0.5))
            fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, width="stretch")
            st.dataframe(pd.DataFrame([{T("col.symbol"): p.symbol, T("col.qty"): p.qty,
                                        T("col.price"): p.market_price, T("col.value"): round(p.market_value, 2),
                                        T("col.weight"): f"{p.market_value / eq:.1%}",
                                        T("col.gain"): f"{p.unrealized_pct:+.1%}"} for p in pos]),
                         hide_index=True, width="stretch")

# --------------------------------------------------------------------------- backtest
with tab_bt:
    from riskaware.strategy import list_strategies

    builtins = list(list_strategies())
    c1, c2, c3 = st.columns(3)
    strat = c1.selectbox(T("col.strategy"), builtins, index=0)
    custom = c1.text_input("…or path/to/my_strategy.py:Class" if LANG == "en" else "…或自定义策略 path.py:Class")
    compare = c2.multiselect("Compare with" if LANG == "en" else "对比策略", [b for b in builtins if b != strat],
                             default=["buy_and_hold"])
    preset = c3.selectbox("Universe" if LANG == "en" else "股票池", ["megacap", "tech", "defensive", "etf", "sectors"])
    d1, d2, d3 = st.columns(3)
    start = d1.date_input("Start" if LANG == "en" else "开始", date(2018, 1, 1))
    end = d2.date_input("End" if LANG == "en" else "结束", date.today())
    cash = d3.number_input("Initial cash" if LANG == "en" else "初始资金", value=100_000, step=10_000)
    if st.button("Run backtest" if LANG == "en" else "开始回测", type="primary"):
        from riskaware.backtest.engine import Backtester
        from riskaware.backtest.metrics import METRIC_LABELS, drawdown_series, fmt_metric
        from riskaware.data.prices import make_price_provider
        from riskaware.strategy import load_strategy
        from riskaware.utils import DiskCache

        bcfg = cfg.model_copy(deep=True)
        bcfg.universe.preset = preset
        bt = Backtester(bcfg, make_price_provider(bcfg.data.price_source, DiskCache(bcfg.data.cache_path),
                                                   bcfg.data.csv_dir))
        names = [custom.strip() or strat, *compare]
        results = []
        bar = st.progress(0.0)
        for k, name in enumerate(names):
            res = bt.run(load_strategy(name), start, end, initial_cash=cash,
                         progress=lambda i, n, _k=k: bar.progress(min(1.0, (_k + i / n) / len(names))))
            results.append(res)
        bar.empty()
        rows = []
        for key, (label, _) in METRIC_LABELS.items():
            if any(key in r.metrics for r in results):
                rows.append({T("col.metric"): label, **{r.strategy: fmt_metric(key, r.metrics.get(key)) for r in results},
                             T("col.benchmark"): fmt_metric(key, results[0].benchmark_metrics.get(key))})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=results[0].benchmark.index, y=results[0].benchmark, name="SPY",
                                 line=dict(color="#8c959f")))
        for r in results:
            fig.add_trace(go.Scatter(x=r.equity.index, y=r.equity, name=r.strategy))
        fig.update_layout(height=380, title=T("bt.equity"), margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(fig, width="stretch")
        fig = go.Figure()
        for r in results:
            dd = drawdown_series(r.equity) * 100
            fig.add_trace(go.Scatter(x=dd.index, y=dd, name=r.strategy, fill="tozeroy"))
        fig.update_layout(height=260, title=T("bt.drawdown") + " %", margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(fig, width="stretch")
        st.subheader(T("bt.trades") + f" ({results[0].strategy})")
        st.dataframe(results[0].trades.iloc[::-1], hide_index=True, width="stretch")
        for n in results[0].notes:
            st.caption("• " + n)

# --------------------------------------------------------------------------- news
with tab_news:
    sym = st.text_input("Symbol" if LANG == "en" else "股票代码", "AAPL").strip().upper()
    if st.button("Fetch news" if LANG == "en" else "获取新闻") and sym:
        from riskaware.news.sources import NewsCollector
        from riskaware.news.verify import analyze_news
        from riskaware.utils import DiskCache

        with st.spinner("Collecting Finviz + RSS + SEC..."):
            col = NewsCollector((["finviz"] if cfg.news.finviz else []) + cfg.news.feeds,
                                DiskCache(cfg.data.cache_path), cfg.data.contact_email, cfg.news.request_interval)
            rep = analyze_news(sym, col.collect(sym, cfg.news.lookback_hours), cfg.news, sources=col.last_status)
        st.metric(T("news.score"), f"{rep.score:+.2f}",
                  f"{rep.confirmed} {T('news.status.confirmed')} / {rep.unconfirmed} {T('news.status.unconfirmed')}",
                  delta_color="off")
        if rep.red_flags_confirmed:
            st.error(f"{T('news.flags')}: {', '.join(rep.red_flags_confirmed)}")
        if rep.red_flags_unconfirmed:
            st.warning(f"{T('news.flags')} ({T('news.status.unconfirmed')}): {', '.join(rep.red_flags_unconfirmed)}")
        st.dataframe(pd.DataFrame([{
            "": T("news.status." + c.status.value.lower()), "sentiment": round(c.sentiment, 2), "headline": c.headline,
            "publishers": ", ".join(c.publishers[:5]), "flags": ", ".join(c.red_flags)} for c in rep.top(60)]),
            hide_index=True, width="stretch")
        st.caption(T("news.sources") + ": " + ", ".join(f"{k} {v}" for k, v in rep.sources.items()))

# --------------------------------------------------------------------------- journal
with tab_journal:
    from riskaware.journal import Journal

    j = Journal()
    runs = pd.DataFrame(j.recent_runs(50))
    orders = pd.DataFrame(j.recent_orders(200))
    if runs.empty:
        st.info("No runs yet. Try `riskaware run --dry-run`." if LANG == "en" else "还没有运行记录。试试 `riskaware run --dry-run`。")
    else:
        st.dataframe(runs, hide_index=True, width="stretch")
        st.dataframe(orders, hide_index=True, width="stretch")
