"""Streamlit dashboard. Start it with ``riskaware dashboard`` (English / 中文 switch at the top right)."""

from __future__ import annotations

import os
import re
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from riskaware import i18n
from riskaware import marketcal as mc
from riskaware.config import PROFILE_PRESETS, load_config
from riskaware.dashboard import ui

st.set_page_config(page_title="Risk-Aware Trading Agents", page_icon="📈", layout="wide",
                   initial_sidebar_state="collapsed")

if "lang" not in st.session_state:
    st.session_state.lang = "zh" if os.environ.get("RISKAWARE_LANG", "en") == "zh" else "en"
LANG = st.session_state.lang
def theme_kind() -> str:
    """The theme Streamlit actually renders. A configured base wins (``riskaware dashboard`` runs with
    --theme.base dark): on the first load st.context.theme still reports the browser's own preference, and a light
    page under the dark theme's light text left the page white and the labels unreadable until a second refresh."""
    base = st.get_option("theme.base")
    if base in ("light", "dark"):
        return base
    kind = getattr(st.context.theme, "type", None)
    return kind if kind in ("light", "dark") else "dark"


P = ui.palette(theme_kind())
st.html(ui.css(P))  # styles only


def L(en: str, zh: str) -> str:
    return zh if LANG == "zh" else en


def T(key: str, **kw) -> str:
    return i18n.t(key, LANG, **kw)


def H(markup: str, where=None) -> None:
    """Render a component. st.html would strip the SVG/img charts, so this goes through markdown;
    blank-line-free and with $ escaped so markdown and LaTeX leave it alone."""
    flat = re.sub(r"\n\s*", " ", markup).replace("$", "&#36;")
    (where or st).markdown(flat, unsafe_allow_html=True)


PROFILE_NAMES = {"conservative": ("Conservative", "保守"), "balanced": ("Balanced", "平衡"),
                 "aggressive": ("Aggressive", "激进")}
UNIVERSES = ["megacap", "tech", "defensive", "etf", "sectors"]


LLM_KINDS = ["off", "openai", "ollama", "anthropic"]


def llm_kind_name(k: str) -> str:
    return {"off": L("Off", "不用"), "openai": "OpenAI (GPT)", "ollama": L("Local model (Ollama)", "本地模型（Ollama）"),
            "anthropic": "Claude"}[k]


def llm_choice_inputs(key: str, current: str | None = None) -> str | None:
    """Two inputs (provider, model) that return 'off' or 'provider:model'."""
    from riskaware.llm import DEFAULT_MODELS

    kind0, _, model0 = (current or "off").partition(":")
    kind0 = kind0 if kind0 in LLM_KINDS else "off"
    c1, c2 = st.columns([1, 1.4])
    kind = c1.selectbox(L("LLM review", "LLM 复核"), LLM_KINDS, index=LLM_KINDS.index(kind0), format_func=llm_kind_name,
                        key=f"{key}_kind", help=L("A bull analyst, a bear analyst and a judge read the evidence for "
                                                  "each buy and can move conviction by at most ±0.15 or veto it.",
                                                  "多方、空方和裁判阅读每个买入候选的证据，最多把信心分调整 ±0.15，或者否决。"))
    model = c2.text_input(L("Model", "模型"), value=model0, key=f"{key}_model",
                          placeholder="gpt-5.6-luna · qwen2.5:3b · claude-opus-5-5")
    if kind == "off":
        return "off"
    return f"{kind}:{model.strip() or DEFAULT_MODELS.get(kind, '')}"


def profile_name(p: str) -> str:
    en, zh = PROFILE_NAMES.get(p, (p, p))
    return L(en, zh)


# ------------------------------------------------------------------------------------------ data
@st.cache_resource(show_spinner=False)
def get_config(profile: str = "balanced"):
    return load_config(os.environ.get("RISKAWARE_CONFIG"), {"profile": profile})


@st.cache_data(ttl=900, show_spinner=False)
def load_market():
    from riskaware.engine import MarketData

    cfg = get_config()
    md = MarketData(cfg)
    market = md.load([])
    events = md.calendar.upcoming(date.today(), 45)
    spy = md.sym_data[cfg.universe.benchmark].frame.iloc[-260:][["close", "ema50", "ema200"]]
    macro = md.macro.iloc[-260:][["vix", "fear_greed", "regime"]].copy()
    macro["regime"] = macro["regime"].map(lambda r: getattr(r, "value", str(r)))
    return market, events, spy, macro


@st.cache_data(ttl=900, show_spinner=False)
def load_benchmark(_key: str):
    from riskaware.arena import Arena

    return Arena(base_cfg=get_config()).benchmark()


@st.cache_data(ttl=600, show_spinner=False)
def load_quotes(symbols: tuple[str, ...]) -> dict[str, float]:
    from riskaware.brokers.paper import yahoo_quotes

    return {s: q.price for s, q in yahoo_quotes(list(symbols)).items() if q.price}


def arena():
    from riskaware.arena import Arena

    return Arena(base_cfg=get_config())


def universe_name(u: str) -> str:
    return L("default list", "默认股票池") if u == "default" else u


def schedule_state(s: str) -> str:
    from riskaware.arena import state_text

    return state_text(s, LANG)


def agent_note(spec) -> str:
    from riskaware.arena import STARTER_AGENTS, STARTER_NOTES_ZH

    starter = next((s for s in STARTER_AGENTS if s["name"] == spec.name), None)
    if LANG == "zh" and starter and spec.note == starter["note"]:
        return STARTER_NOTES_ZH.get(spec.name, spec.note)
    return spec.note


def regime_label(r: str, short: bool = True) -> str:
    return i18n.regime(r, LANG, short=short)


def regime_shapes(macro: pd.DataFrame) -> list[dict]:
    shapes, start, cur = [], None, None
    idx = list(macro.index)
    for i, (ts, r) in enumerate(macro["regime"].items()):
        if r != cur:
            if cur is not None:
                shapes.append((start, ts, cur))
            start, cur = ts, r
        if i == len(idx) - 1 and cur is not None:
            shapes.append((start, ts, cur))
    return [dict(type="rect", xref="x", yref="paper", x0=a, x1=b, y0=0, y1=1, line_width=0, layer="below",
                 fillcolor=ui.regime_color(r, P), opacity=0.13 if r == "RISK_OFF" else 0.07)
            for a, b, r in shapes if r in ("UNCERTAIN", "RISK_OFF")]


# ------------------------------------------------------------------------------------------ pages
def page_market() -> None:
    with st.spinner(L("Loading prices, VIX and the macro calendar…", "正在读取价格、VIX 和宏观日历…")):
        market, events, spy, macro = load_market()
    r = market.regime.value
    c = ui.regime_color(r, P)
    reasons = "".join(f'<span class="ra-chip">{ui.esc(x)}</span>' for x in market.reasons[:8])
    fg_label = i18n.t("fear." + market.fear_label, LANG)
    cnn = f"{market.cnn_fear_greed:.0f}" if market.cnn_fear_greed is not None else "–"
    H(f"""<div class="ra-hero">
      <div class="ra-card ra-regime" style="--c:{c}">
        <div class="eyebrow">{L('Market regime', '市场状态')} · {market.day:%Y-%m-%d}</div>
        <div class="big"><span class="ra-dot" style="--c:{c};width:14px;height:14px"></span>{ui.esc(regime_label(r))}</div>
        <div class="what">{ui.esc(regime_label(r, short=False))}</div>
        <div class="ra-chips">{reasons}</div>
      </div>
      <div class="ra-card ra-gauge">
        <div class="eyebrow" style="color:var(--muted);font-size:.76rem;font-weight:600;letter-spacing:.08em;text-transform:uppercase">{L('Fear & greed', '恐慌贪婪指数')}</div>
        {ui.gauge(market.fear_greed, P, fg_label)}
        <div class="cap">CNN Fear &amp; Greed <b class="mono" style="color:var(--text)">{cnn}</b> · {L('computed from free data', '用免费数据自算')}</div>
      </div></div>""")

    vix_tail = macro["vix"].dropna().iloc[-120:].tolist()
    fg_tail = macro["fear_greed"].dropna().iloc[-120:].tolist()
    spy_tail = spy["close"].iloc[-120:].tolist()
    above = (spy["close"].iloc[-1] / spy["ema200"].iloc[-1] - 1) if len(spy) else None
    vix_note = (L("calm", "平静") if (market.vix or 0) < 20 else L("elevated", "偏高") if (market.vix or 0) < 30
                else L("stress", "紧张"))
    term = market.vix_ratio
    term_note = (L("backwardation: near-term fear", "倒挂：短期恐慌") if term and term > 1.0
                 else L("contango: normal", "正常结构"))
    cards = [
        ui.kpi("VIX", ui.num(market.vix), vix_note, ui.sparkline(vix_tail, P, color=P["warn"], height=30, stretch=True),
               help_text=T("market.vix")),
        ui.kpi("VIX / VIX3M", ui.num(term, 2), term_note, "", "down" if term and term > 1 else "muted"),
        ui.kpi(T("market.fg_short"), ui.num(market.fear_greed, 0), fg_label, ui.sparkline(fg_tail, P, color=P["accent"], height=30, stretch=True)),
        ui.kpi(T("market.breadth_short"), ui.pct(market.breadth, 0), L("sectors above EMA200", "板块站上 EMA200")),
        ui.kpi(T("market.risk_short"), ui.num(market.risk_score, 0), L("0 calm · 100 stress", "0 平静 · 100 紧张")),
        ui.kpi("SPY", ui.num(spy["close"].iloc[-1], 2) if len(spy) else "–",
               f'{ui.pct(above, 1, True)} {L("vs EMA200", "相对 EMA200")}', ui.sparkline(spy_tail, P, height=30, stretch=True),
               ui.tone(above)),
    ]
    H(f'<div class="ra-grid ra-g6" style="margin-top:12px">{"".join(cards)}</div>')

    H(ui.section(T("events.title"), L("the agents get careful before FOMC, CPI and the jobs report",
                                              "FOMC、CPI、非农前智能体会更谨慎")))
    if events:
        chips = []
        for e in events[:14]:
            col = P["down"] if e.impact >= 0.8 else P["warn"] if e.impact >= 0.5 else P["accent"]
            n = mc.trading_days_between(date.today(), e.day)
            when = (L("today", "今天") if n <= 0 else L("next trading day", "下一个交易日") if n == 1
                    else L(f"in {n} trading days", f"{n} 个交易日后"))
            est = f" · {T('events.estimated')}" if e.estimated else ""
            mon = e.day.strftime("%b").upper() if LANG == "en" else f"{e.day.month}月"
            chips.append(f'<div class="ra-card ra-event" style="--c:{col}"><div class="bar"></div><div class="date">'
                         f'<div class="mon">{mon}</div><div class="day">{e.day.day}</div></div><div><div class="name">'
                         f'{ui.esc(i18n.event(e.kind, LANG))}</div><div class="in">{when}{est}'
                         f'{" · " + ui.esc(e.time) if e.time else ""}</div></div></div>')
        H(f'<div class="ra-events">{"".join(chips)}</div>')

    left, right = st.columns([3, 2])
    with left:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=spy.index, y=spy["close"], name="SPY", line=dict(width=2.2, color=P["accent"])))
        fig.add_trace(go.Scatter(x=spy.index, y=spy["ema50"], name="EMA50", line=dict(width=1.2, color=P["cyan"])))
        fig.add_trace(go.Scatter(x=spy.index, y=spy["ema200"], name="EMA200", line=dict(width=1.2, color=P["violet"],
                                                                                        dash="dot")))
        fig.update_layout(**ui.plotly_layout(P, 340, L("S&P 500 with regime shading", "标普 500 与市场状态底色"),
                                             shapes=regime_shapes(macro)))
        st.plotly_chart(fig, width="stretch", theme=None, config={"displayModeBar": False})
    with right:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=macro.index, y=macro["fear_greed"], name=T("market.fg_short"),
                                 line=dict(width=2, color=P["accent"]), fill="tozeroy",
                                 fillcolor="rgba(91,140,255,0.08)"))
        fig.add_trace(go.Scatter(x=macro.index, y=macro["vix"], name="VIX", yaxis="y2",
                                 line=dict(width=1.4, color=P["warn"])))
        fig.update_layout(**ui.plotly_layout(P, 340, L("Fear & greed and VIX", "恐慌贪婪与 VIX"),
                                             yaxis=dict(range=[0, 100], gridcolor=P["line"]),
                                             yaxis2=dict(overlaying="y", side="right", showgrid=False)))
        st.plotly_chart(fig, width="stretch", theme=None, config={"displayModeBar": False})


def _agent_cards(rows: list[dict]) -> str:
    cards = []
    for r in rows:
        ret, day, bench = r["return"], r["day"], r["benchmark_return"]
        rel = None if bench is None else ret - bench
        if not r["enabled"]:
            status = ui.pill(L("paused", "已暂停"), P["neutral"])
        elif r["ran_today"]:
            status = ui.pill(L("traded today", "今天已交易"), P["up"])
        else:
            status = ui.pill(L("waiting", "等待运行"), P["accent"])
        first = r["rank"] == 1 and len(rows) > 1 and r["days"] > 0
        cards.append(f"""<div class="ra-card ra-agent{' first' if first else ''}">
          <div class="top"><div><div class="rank">#{r['rank']}{' · ' + L('leader', '领先') if first else ''}</div>
          <div class="name">{ui.esc(r['name'])}</div>
          <div class="meta">{ui.esc(r['strategy'])} · {profile_name(r['profile'])} · {ui.esc(universe_name(r['universe']))}{(' · 🧠 ' + ui.esc(r['llm'])) if r.get('llm') else ''}</div></div>
          <div>{status}</div></div>
          <div style="display:flex;justify-content:space-between;align-items:flex-end;gap:8px">
            <div><div class="eq">{ui.money(r['equity'])}</div>
            <div class="ret {ui.tone(ret)}">{ui.pct(ret, 2, True)} <span class="muted" style="font-weight:500;font-family:Inter">
            {L('since', '起始')} {ui.esc(r['created'])}</span></div></div>
            {ui.sparkline(r['curve'], P, width=120, height=44)}
          </div>
          <div class="stats">
            <div><span>{L('Today', '今日')}</span><b class="{ui.tone(day)}">{ui.pct(day, 2, True)}</b></div>
            <div><span>vs SPY</span><b class="{ui.tone(rel)}">{'–' if rel is None else f'{rel * 100:+.1f}pp'}</b></div>
            <div><span>{L('Max DD', '最大回撤')}</span><b>{ui.pct(r['max_drawdown'], 1)}</b></div>
            <div><span>{L('Invested', '持股')}</span><b>{ui.pct(r['invested'], 0)}</b></div>
          </div></div>""")
    return f'<div class="ra-grid ra-g3">{"".join(cards)}</div>'


def _create_agent_form(a) -> None:
    from riskaware.arena import AgentSpec
    from riskaware.strategy import list_strategies

    strategies = list_strategies()
    with st.form("new_agent", clear_on_submit=False, border=False):
        c1, c2 = st.columns(2)
        name = c1.text_input(L("Name", "名字"), placeholder="my-dips", help=L("lowercase letters, digits, - or _",
                                                                              "小写字母、数字、- 或 _"))
        strat = c2.selectbox(L("Strategy", "策略"), list(strategies), format_func=lambda s: s,
                             help=L("built-in strategies; or give your own file below",
                                    "内置策略；也可以在下面填你自己的策略文件"))
        custom = st.text_input(L("…or your own strategy file", "…或你自己的策略文件"),
                               placeholder="/path/to/my_strategy.py:MyStrategy")
        c3, c4, c5 = st.columns(3)
        profile = c3.selectbox(L("Risk profile", "风险档位"), list(PROFILE_PRESETS),
                               index=list(PROFILE_PRESETS).index("balanced"), format_func=profile_name)
        universe = c4.selectbox(L("Stock list", "股票池"), ["", *UNIVERSES],
                                format_func=lambda u: u or L("from your config", "用配置里的"))
        cash = c5.number_input(L("Starting cash ($)", "初始资金（美元）"), 1_000, 10_000_000, 100_000, 10_000)
        symbols = st.text_input(L("Extra symbols (optional)", "额外股票（可选）"), placeholder="PLTR, SHOP")
        llm = llm_choice_inputs("new_agent_llm")
        note = st.text_input(L("Note (optional)", "备注（可选）"))
        st.caption(strategies.get(strat, ""))
        if st.form_submit_button(L("Create agent", "创建智能体"), type="primary"):
            try:
                spec = a.add(AgentSpec(name=name, strategy=custom.strip() or strat, profile=profile,
                                       preset=universe or None, symbols=symbols.split(","), cash=float(cash),
                                       note=note, llm=llm))
            except Exception as exc:
                st.error(str(exc))
            else:
                st.success(L(f"Created {spec.name}. It trades at the next daily run, or press Run now.",
                             f"已创建 {spec.name}。它会在下一次每日运行时交易，也可以点「现在运行」。"))
                st.rerun()


def _schedule_panel(a) -> None:
    from riskaware import scheduler

    st_ = a.schedule_status()
    on = scheduler.installed()
    last = st_["last_run"]
    done = ", ".join(last.get("done", [])) if last else ""
    H(f"""<div class="ra-card">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap">
        <div><div style="font-weight:700">{L('Daily automatic run', '每日自动运行')}</div>
        <div class="ra-note" style="margin-top:4px">{L(
            f"Every trading day after {st_['run_time']} New York time, before the close. Free Yahoo prices, simulated money.",
            f"每个交易日纽约时间 {st_['run_time']} 之后、收盘之前运行。免费的雅虎价格，模拟资金。")}</div></div>
        <div>{ui.pill(L('installed', '已开启') if on else L('not installed', '未开启'), P['up'] if on else P['warn'])}</div>
      </div>
      <div class="ra-grid ra-g3" style="margin-top:12px">
        <div><div class="ra-note">{L('Now', '现在')}</div><div style="font-weight:600">{ui.esc(schedule_state(st_['state']))}</div></div>
        <div><div class="ra-note">{L('Next trading day', '下一个交易日')}</div><div class="mono">{st_['next_day']}</div></div>
        <div><div class="ra-note">{L('Last run', '上次运行')}</div><div class="mono">{ui.esc(last.get('date', '–')) if last else '–'}
        <span class="ra-note">{ui.esc(done)}</span></div></div>
      </div></div>""")
    b1, b2, _ = st.columns([1, 1, 3])
    if not on and b1.button(L("Turn on daily run", "开启每日运行"), type="primary", icon=":material/schedule:"):
        from pathlib import Path

        cfg = get_config()
        try:
            what = scheduler.install(Path.cwd(), a.log_file.parent / "scheduler.out",
                                     Path(cfg.source_path) if cfg.source_path else None)
        except RuntimeError as exc:
            st.error(str(exc))
        else:
            a.log(f"scheduler installed: {what}")
            st.success(what)
            st.rerun()
    if on and b2.button(L("Turn off", "关闭"), icon=":material/schedule:"):
        st.info(scheduler.uninstall())
        st.rerun()
    H('<div class="ra-code">riskaware agents schedule --install</div>')
    log = a.recent_log(30)
    if log:
        with st.expander(L("Run log", "运行日志")):
            st.code("\n".join(log), language=None)


def _agent_detail(a, name: str, bench: pd.Series | None) -> None:
    from riskaware.journal import Journal

    spec = a.get(name)
    hist = a.equity_history(name)
    acct = a.account_state(name)
    t1, t2, t3, t4 = st.tabs([L("Performance", "表现"), L("Positions", "持仓"), L("Orders", "订单"),
                              L("Decisions", "决策")])
    with t1:
        if hist.empty:
            st.info(L("No trading days yet. Press Run now or wait for the daily run.",
                      "还没有交易日记录。点「现在运行」，或者等每日自动运行。"))
        else:
            h = hist.copy()
            h["date"] = pd.to_datetime(h["date"])
            fig = go.Figure()
            x0 = pd.Timestamp(mc.prev_trading_day(date.fromisoformat(spec.created)))
            fig.add_trace(go.Scatter(x=[x0, *h["date"]], y=[spec.cash, *h["equity"]], name=spec.name,
                                     mode="lines+markers", line=dict(width=2.4, color=P["accent"]),
                                     marker=dict(size=6)))
            if bench is not None and len(bench):
                b = bench[bench.index >= x0]
                if len(b):
                    fig.add_trace(go.Scatter(x=b.index, y=b / b.iloc[0] * spec.cash, name="SPY", mode="lines+markers",
                                             line=dict(width=1.4, color=P["neutral"], dash="dot"),
                                             marker=dict(size=4)))
            fig.update_layout(**ui.plotly_layout(P, 320, L("Equity vs SPY (same start)", "净值 vs SPY（同一起点）"),
                                                 xaxis=dict(tickformat="%b %d", gridcolor=P["line"],
                                                            dtick=86_400_000 if len(h) < 15 else None),
                                                 yaxis=dict(tickprefix="$", gridcolor=P["line"])))
            st.plotly_chart(fig, width="stretch", theme=None, config={"displayModeBar": False})
    with t2:
        pos = acct.get("positions", {})
        if not pos:
            st.info(L("No positions.", "没有持仓。"))
        else:
            px = load_quotes(tuple(sorted(pos)))
            rows = []
            for s, p in sorted(pos.items()):
                last = px.get(s, p["avg_cost"])
                rows.append({L("Symbol", "代码"): s, L("Shares", "股数"): p["qty"], L("Avg cost", "成本"): p["avg_cost"],
                             L("Last", "现价"): last, L("Value", "市值"): p["qty"] * last,
                             L("Gain", "盈亏"): last / p["avg_cost"] - 1 if p["avg_cost"] else 0.0})
            df = pd.DataFrame(rows)
            st.dataframe(df, hide_index=True, width="stretch", column_config={
                L("Avg cost", "成本"): st.column_config.NumberColumn(format="$%.2f"),
                L("Last", "现价"): st.column_config.NumberColumn(format="$%.2f"),
                L("Value", "市值"): st.column_config.NumberColumn(format="$%,.0f"),
                L("Gain", "盈亏"): st.column_config.NumberColumn(format="percent")})
            st.caption(L(f"Cash {ui.money(acct.get('cash'))}", f"现金 {ui.money(acct.get('cash'))}"))
    jpath = a.dir(name) / "journal.db"
    with t3:
        orders = pd.DataFrame(Journal(jpath).recent_orders(200)) if jpath.is_file() else pd.DataFrame()
        if orders.empty:
            st.info(L("No orders yet.", "还没有订单。"))
        else:
            st.dataframe(orders[["ts", "side", "symbol", "qty", "limit_price", "status", "tag", "reason"]],
                         hide_index=True, width="stretch")
    with t4:
        _decisions_tab(a, name)
    llm_text = {None: L("LLM as in the config", "LLM 跟随配置"), "off": L("no LLM", "不用 LLM")}.get(
        spec.llm, f"🧠 {spec.llm}")
    st.caption(f"{spec.strategy} · {profile_name(spec.profile)} · {universe_name(spec.universe_label)} · "
               f"{L('start', '初始')} {ui.money(spec.cash)} · {L('since', '起始')} {spec.created} · {llm_text}"
               + (f" · {agent_note(spec)}" if spec.note else ""))
    c1, c2, c3, c4, c5, _ = st.columns([1, 1, 1, 1, 1, 1])
    if c1.button(L("Run now", "现在运行"), key=f"run_{name}", icon=":material/play_arrow:", type="primary"):
        with st.spinner(L(f"Running {name}…", f"正在运行 {name}…")):
            try:
                res = a.run([name])
            except RuntimeError as exc:
                res = []
                st.warning(str(exc))
        for r in res:
            (st.success if r.ok else st.error)(r.summary())
        load_benchmark.clear()
    if c2.button(L("Resume", "恢复") if not spec.enabled else L("Pause", "暂停"), key=f"pause_{name}",
                 icon=":material/pause:" if spec.enabled else ":material/play_circle:"):
        a.set_enabled(name, not spec.enabled)
        st.rerun()
    with c3.popover(L("Reset", "重置"), icon=":material/restart_alt:"):
        st.write(L("Start over with the starting cash and forget the history?", "回到初始资金，清空历史？"))
        if st.button(L("Yes, reset", "确定重置"), key=f"reset_{name}"):
            a.reset(name)
            st.rerun()
    with c5.popover(L("LLM", "LLM"), icon=":material/psychology:"):
        choice = llm_choice_inputs(f"llm_{name}", spec.llm)
        if st.button(L("Save", "保存"), key=f"llm_save_{name}", type="primary"):
            try:
                a.set_llm(name, choice)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.rerun()
        st.caption(L("OpenAI needs OPENAI_API_KEY in your .env; a local model needs `ollama serve`.",
                     "OpenAI 需要在 .env 里设置 OPENAI_API_KEY；本地模型需要先运行 `ollama serve`。"))
    with c4.popover(L("Delete", "删除"), icon=":material/delete:"):
        st.write(L("Delete this agent and its paper account?", "删除这个智能体和它的模拟账户？"))
        if st.button(L("Yes, delete", "确定删除"), key=f"del_{name}", type="primary"):
            a.remove(name)
            st.rerun()


def _decisions_tab(a, name: str) -> None:
    rows = a.decisions(name, limit=150, with_prompts=True)
    if not rows:
        st.info(L("No signals yet.", "还没有信号。"))
        return

    def verdict(rv):
        if not rv:
            return "–"
        if rv.get("adjustment") is None:
            return L("error", "出错")
        return "VETO" if rv.get("veto") else f"{rv['adjustment']:+.2f}"

    table = pd.DataFrame([{
        L("Time", "时间"): str(r["ts"])[:16].replace("T", " "), L("Symbol", "代码"): r["symbol"],
        L("Action", "操作"): ("🟢 " if r["action"] == "BUY" else "🔴 ") + i18n.t("action." + r["action"], LANG),
        L("Conviction", "信心"): float(r["strength"]), L("Passed risk check", "通过风控"): bool(r["accepted"]),
        "LLM": verdict(r.get("llm"))} for r in rows])
    st.dataframe(table, hide_index=True, width="stretch", height=260, column_config={
        L("Conviction", "信心"): st.column_config.ProgressColumn(min_value=0.0, max_value=1.0, format="%.2f"),
        L("Passed risk check", "通过风控"): st.column_config.CheckboxColumn()})
    i = st.selectbox(L("How was this decision made?", "这个决定是怎么做出来的？"), range(len(rows)),
                     format_func=lambda k: f"{str(rows[k]['ts'])[:16].replace('T', ' ')} · {rows[k]['symbol']} · "
                                           f"{rows[k]['action']} · {rows[k]['strength']:.2f}",
                     key=f"why_{name}")
    _decision_view(rows[i])


def _decision_view(r: dict) -> None:
    rv = r.get("llm") or {}
    ok = bool(r["accepted"])
    setups = "".join(f'<span class="ra-chip">{ui.esc(i18n.setup(s, LANG))}</span>' for s in r["setups"])
    reasons = "".join(f"<li>{ui.esc(x)}</li>" for x in str(r["reason"]).split("; ") if x)
    check = (ui.pill(L("passed the risk check", "通过风控"), P["up"]) if ok else
             ui.pill(L("blocked by the risk manager", "被风控拦下"), P["warn"]))
    before = rv.get("strength_before")
    conv = (f"{before:.2f} → {r['strength']:.2f}" if before is not None and rv.get("adjustment") is not None
            else f"{r['strength']:.2f}")
    H(f"""<div class="ra-card" style="margin-top:6px">
      <div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;align-items:center">
        <div><div class="ra-note">{ui.esc(str(r['ts'])[:19].replace('T', ' '))} UTC · run #{r['run_id']}</div>
        <div style="font-size:1.3rem;font-weight:800">{ui.esc(r['symbol'])} · {ui.esc(i18n.t('action.' + r['action'], LANG))}</div></div>
        <div>{check} {ui.pill(L('conviction ', '信心 ') + conv, P['accent'], dot=False)}</div>
      </div>
      <div class="ra-note" style="margin-top:12px">{L('1 · What the technical, earnings and news agents found', '1 · 技术面、财报和新闻智能体的发现')}</div>
      <div class="ra-chips" style="margin-top:6px">{setups}</div>
      <ul style="margin:8px 0 0 18px;padding:0">{reasons}</ul>
      <div class="ra-note" style="margin-top:12px">{L('2 · Risk manager', '2 · 风控')}</div>
      <div>{ui.esc(r['reject_reason']) if not ok else L('within the cash floor, position, sector and regime limits', '没有超出现金底线、单票、板块和市场状态的限制')}</div>
    </div>""")
    if not rv:
        st.caption(L("3 · No LLM review: the LLM only looks at buy candidates, and only for agents that have one.",
                     "3 · 没有 LLM 复核：LLM 只看买入候选，而且要智能体开了 LLM。"))
        return
    H(f'<div class="ra-note" style="margin:14px 0 6px">{L("3 · LLM review board", "3 · LLM 复核")} · '
      f'{ui.esc(rv["model"])} · {rv["latency_ms"]} ms</div>')
    if rv.get("adjustment") is None:
        st.warning(f"{rv['model']}: {rv.get('error') or L('no usable answer', '没有可用的回答')}")
    else:
        judge = (f'<b class="down">VETO</b> · {ui.esc(rv["veto_reason"])}' if rv.get("veto")
                 else f'{L("adjustment", "调整")} <b class="{ui.tone(rv["adjustment"])}">{rv["adjustment"]:+.2f}</b>')
        H(f"""<div class="ra-grid ra-g3">
          <div class="ra-card ra-bull"><div class="ra-note">{L('Bull analyst', '多方分析师')}</div><div>{ui.esc(rv['bull'])}</div></div>
          <div class="ra-card ra-bear"><div class="ra-note">{L('Bear analyst', '空方分析师')}</div><div>{ui.esc(rv['bear'])}</div></div>
          <div class="ra-card ra-judge"><div class="ra-note">{L('Risk judge', '风控裁判')} · {judge}</div><div>{ui.esc(rv['summary'])}</div></div>
        </div>""")
    with st.expander(L("Prompt sent to the model", "发给模型的 prompt")):
        st.code((rv.get("system_prompt") or "") + "\n\n" + (rv.get("prompt") or ""), language=None, wrap_lines=True)
    with st.expander(L("Raw reply", "模型的原始回复")):
        st.code(rv.get("raw") or rv.get("error") or "", language="json", wrap_lines=True)


def page_agents() -> None:
    a = arena()
    agents = a.load()
    head_l, head_r = st.columns([3, 2], vertical_alignment="bottom")
    H(where=head_l, markup=ui.section(L("Paper-trading agents", "模拟盘智能体"),
                           L("each agent is a strategy with its own simulated account",
                             "每个智能体是一个策略，配一个独立的模拟账户")))
    with head_r:
        b1, b2 = st.columns(2)
        with b1.popover(L("New agent", "新建智能体"), icon=":material/add:", width="stretch"):
            _create_agent_form(a)
        if agents and b2.button(L("Run all now", "全部现在运行"), icon=":material/play_arrow:", type="primary",
                                width="stretch"):
            with st.status(L("Running agents…", "正在运行智能体…"), expanded=True) as box:
                try:
                    runs = a.run(progress=lambda n: box.write(f"→ {n}"))
                except RuntimeError as exc:
                    box.update(label=str(exc), state="error")
                else:
                    for r in runs:
                        box.write(("✅ " if r.ok else "❌ ") + r.summary())
                    box.update(label=L("Done", "完成"), state="complete")
            load_benchmark.clear()
    if not agents:
        H(ui.empty("🤖", L("No agents yet", "还没有智能体"),
                         L("Start with four agents of different temperaments, or create your own from any strategy.",
                           "先用四个风格不同的示例智能体，或者用任意策略创建你自己的。")))
        if st.button(L("Create 4 starter agents", "创建 4 个示例智能体"), type="primary", icon=":material/bolt:"):
            a.add_starters()
            st.rerun()
        _schedule_panel(a)
        return
    bench = load_benchmark("spy")
    rows = a.leaderboard(bench)
    best = rows[0]
    avg = sum(r["return"] for r in rows) / len(rows)
    traded = sum(1 for r in rows if r["ran_today"])
    bench_ret = next((r["benchmark_return"] for r in rows if r["benchmark_return"] is not None), None)
    H('<div class="ra-grid ra-g4">' + "".join([
        ui.kpi(L("Agents", "智能体"), str(len(rows)), L(f"{traded} traded today", f"今天已交易 {traded} 个")),
        ui.kpi(L("Leader", "领先"), ui.esc(best["name"]), f'<span class="{ui.tone(best["return"])}">'
               f'{ui.pct(best["return"], 2, True)}</span>'),
        ui.kpi(L("Average return", "平均收益"), ui.pct(avg, 2, True), L("all agents", "全部智能体"), "", ui.tone(avg)),
        ui.kpi("SPY", ui.pct(bench_ret, 2, True), L("since the first agent started", "自第一个智能体开始"), "",
               ui.tone(bench_ret)),
    ]) + "</div>")
    H(ui.section(L("Leaderboard", "排行榜"), L("ranked by return since start", "按开始以来的收益排名")))
    H(_agent_cards(rows))
    H(ui.section(L("Agent details", "智能体详情")))
    name = st.segmented_control(L("Agent", "智能体"), [r["name"] for r in rows], default=rows[0]["name"],
                                label_visibility="collapsed")
    if name:
        _agent_detail(a, name, bench)
    H(ui.section(L("Schedule", "定时运行")))
    _schedule_panel(a)


def page_signals() -> None:
    from riskaware.strategy import list_strategies

    H(ui.section(L("Today's signals", "今天的信号"),
                       L("what each strategy would buy or sell at today's prices", "每个策略按今天的价格会买什么、卖什么")))
    with st.container(border=True):
        c1, c2, c3 = st.columns([1.2, 1, 2])
        strat = c1.selectbox(L("Strategy", "策略"), list(list_strategies()))
        profile = c2.selectbox(L("Risk profile", "风险档位"), list(PROFILE_PRESETS),
                               index=list(PROFILE_PRESETS).index("balanced"), format_func=profile_name)
        extra = c3.text_input(L("Symbols (optional, comma separated)", "股票代码（可选，逗号分隔）"),
                              placeholder="NVDA, AAPL, COST")
        go_ = st.button(L("Run scan", "开始扫描"), type="primary", icon=":material/radar:")
    if go_:
        from riskaware.engine import TradingEngine
        from riskaware.strategy import load_strategy

        with st.spinner(L("Running the agents…", "智能体分析中…")):
            eng = TradingEngine(get_config(profile), strategy=load_strategy(strat, {}))
            syms = [s.strip().upper() for s in extra.split(",") if s.strip()] or None
            market, signals, _ = eng.scan(syms)
        H(f'<div style="margin:6px 0 10px">{ui.pill(regime_label(market.regime.value), ui.regime_color(market.regime.value, P))}</div>')
        if not signals:
            H(ui.empty("🧘", T("scan.none"), L("The agents only act on setups that pass every check.",
                                                       "只有通过全部检查的机会，智能体才会行动。")))
            return
        df = pd.DataFrame([{
            L("Symbol", "代码"): s.symbol,
            L("Action", "操作"): ("🟢 " if s.action.value == "BUY" else "🔴 ") + i18n.t("action." + s.action.value, LANG),
            L("Conviction", "信心"): float(s.strength),
            L("Setups", "形态"): ", ".join(i18n.setup(x, LANG) for x in s.setups[:3]),
            L("Why", "理由"): s.reason} for s in sorted(signals, key=lambda x: -x.strength)])
        st.dataframe(df, hide_index=True, width="stretch", column_config={
            L("Conviction", "信心"): st.column_config.ProgressColumn(min_value=0.0, max_value=1.0, format="%.2f"),
            L("Why", "理由"): st.column_config.TextColumn(width="large")})


def page_backtest() -> None:
    from riskaware.strategy import list_strategies

    strategies = list_strategies()
    H(ui.section(L("Backtest", "回测"), L("signals at the close, fills the next day, 5 bps slippage",
                                               "收盘出信号、次日成交、5 个基点滑点")))
    with st.form("bt"):
        c1, c2, c3 = st.columns(3)
        strat = c1.selectbox(L("Strategy", "策略"), list(strategies))
        compare = c2.multiselect(L("Compare with", "对比策略"), list(strategies), default=["buy_and_hold"])
        preset = c3.selectbox(L("Stock list", "股票池"), UNIVERSES)
        d1, d2, d3, d4 = st.columns(4)
        start = d1.date_input(L("Start", "开始"), date(2018, 1, 1))
        end = d2.date_input(L("End", "结束"), date.today())
        cash = d3.number_input(L("Initial cash", "初始资金"), value=100_000, step=10_000)
        profile = d4.selectbox(L("Risk profile", "风险档位"), list(PROFILE_PRESETS),
                               index=list(PROFILE_PRESETS).index("balanced"), format_func=profile_name)
        custom = st.text_input(L("…or your own strategy file", "…或你自己的策略文件"),
                               placeholder="/path/to/my_strategy.py:MyStrategy")
        run = st.form_submit_button(L("Run backtest", "开始回测"), type="primary")
    if run:
        from riskaware.backtest.engine import Backtester
        from riskaware.data.prices import make_price_provider
        from riskaware.strategy import load_strategy
        from riskaware.utils import DiskCache

        bcfg = get_config(profile).model_copy(deep=True)
        bcfg.universe.preset = preset
        bt = Backtester(bcfg, make_price_provider(bcfg.data.price_source, DiskCache(bcfg.data.cache_path),
                                                   bcfg.data.csv_dir))
        names = list(dict.fromkeys([custom.strip() or strat, *[c for c in compare if c != strat]]))
        results = []
        bar = st.progress(0.0, text=L("Downloading prices and simulating…", "下载价格并模拟中…"))
        try:
            for k, name in enumerate(names):
                results.append(bt.run(load_strategy(name), start, end, initial_cash=cash,
                                      progress=lambda i, n, _k=k: bar.progress(min(1.0, (_k + i / n) / len(names)))))
        except Exception as exc:
            bar.empty()
            st.error(str(exc))
            return
        bar.empty()
        st.session_state.bt_results = results  # survives reruns (e.g. the log-scale toggle)
    results = st.session_state.get("bt_results")
    if not results:
        st.caption(L("Option strategies (aggressive_calls, wheel) are priced with Black-Scholes on modelled "
                     "volatility. The wheel needs about $1M on the megacap list.",
                     "期权策略（aggressive_calls、wheel）用 Black-Scholes 和模型估算的波动率定价。wheel 在 megacap "
                     "股票池上大约需要 100 万美元。"))
        return
    from riskaware.backtest.metrics import drawdown_series

    bm = results[0].benchmark_metrics
    cards = []
    for r in [*results, None]:
        m = r.metrics if r else bm
        title = r.strategy if r else L("SPY buy & hold", "SPY 买入持有")
        cards.append(f"""<div class="ra-card ra-kpi"><div class="label">{ui.esc(title)}</div>
          <div class="value {ui.tone(m.get('cagr'))}">{ui.pct(m.get('cagr'), 1)}</div>
          <div class="sub">{L('a year', '年化')}</div>
          <div class="stats" style="display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:10px;border-top:1px solid var(--line);padding-top:8px">
            <div><span class="ra-note">Sharpe</span><div class="mono">{ui.num(m.get('sharpe'), 2)}</div></div>
            <div><span class="ra-note">{L('Max DD', '最大回撤')}</span><div class="mono down">{ui.pct(m.get('max_drawdown'), 1)}</div></div>
            <div><span class="ra-note">{L('Worst yr', '最差年')}</span><div class="mono">{ui.pct(m.get('worst_year'), 1)}</div></div>
          </div></div>""")
    H(f'<div class="ra-grid ra-g4">{"".join(cards)}</div>')
    log_y = st.toggle(L("Log scale", "对数坐标"), value=False)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=results[0].benchmark.index, y=results[0].benchmark, name="SPY",
                             line=dict(color=P["neutral"], width=1.4, dash="dot")))
    colors = [P[k] for k in ("accent", "warn", "violet", "cyan", "up", "down")]
    for i, r in enumerate(results):
        fig.add_trace(go.Scatter(x=r.equity.index, y=r.equity, name=r.strategy,
                                 line=dict(width=2, color=colors[i % len(colors)])))
    fig.update_layout(**ui.plotly_layout(P, 400, T("bt.equity"), yaxis=dict(type="log" if log_y else "linear",
                                                                             gridcolor=P["line"])))
    st.plotly_chart(fig, width="stretch", theme=None, config={"displayModeBar": False})
    fig = go.Figure()
    for i, r in enumerate(results):
        dd = drawdown_series(r.equity) * 100
        fig.add_trace(go.Scatter(x=dd.index, y=dd, name=r.strategy, fill="tozeroy",
                                 line=dict(width=1.2, color=colors[i % len(colors)])))
    fig.update_layout(**ui.plotly_layout(P, 240, T("bt.drawdown") + " %"))
    st.plotly_chart(fig, width="stretch", theme=None, config={"displayModeBar": False})
    from riskaware.backtest.metrics import METRIC_LABELS, fmt_metric

    rows = [{L("Metric", "指标"): label, **{r.strategy: fmt_metric(k, r.metrics.get(k)) for r in results},
             "SPY": fmt_metric(k, bm.get(k))} for k, (label, _) in METRIC_LABELS.items()
            if any(k in r.metrics for r in results)]
    with st.expander(L("All metrics", "全部指标")):
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    with st.expander(T("bt.trades") + f" · {results[0].strategy}"):
        st.dataframe(results[0].trades.iloc[::-1], hide_index=True, width="stretch")
    for n in results[0].notes:
        st.caption("• " + n)


def page_news() -> None:
    cfg = get_config()
    H(ui.section(T("news.title"), L("a story counts when two independent outlets, the company's own release or its SEC "
                                    "filing carry it; only the company's 8-K, or its release confirming earlier "
                                    "reports, can force a sale",
                                    "两家独立媒体、公司自己的通稿或 SEC 公告报道了才算确认；"
                                    "只有公司自己的 8-K 公告，或证实了先前报道的公司通稿，才能触发强制卖出")))
    c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
    sym = c1.text_input(L("Symbol", "股票代码"), "AAPL").strip().upper()
    if not (c2.button(L("Fetch news", "获取新闻"), type="primary", width="stretch", icon=":material/newspaper:")
            and sym):
        return
    from riskaware.news.relevance import company_names
    from riskaware.news.sources import NewsCollector
    from riskaware.news.verify import analyze_news
    from riskaware.utils import DiskCache

    with st.spinner("Finviz + RSS + SEC…"):
        cache = DiskCache(cfg.data.cache_path)
        col = NewsCollector((["finviz"] if cfg.news.finviz else []) + cfg.news.feeds, cache,
                            cfg.data.contact_email, cfg.news.request_interval)
        rep = analyze_news(sym, col.collect(sym, cfg.news.lookback_hours), cfg.news, sources=col.last_status,
                           names=company_names(sym, cache))
    H('<div class="ra-grid ra-g4">' + "".join([
        ui.kpi(T("news.score"), f"{rep.score:+.2f}", L("-1 bearish · +1 bullish", "-1 利空 · +1 利好"), "",
               ui.tone(rep.score)),
        ui.kpi(T("news.status.confirmed"), str(rep.confirmed), L("stories", "条")),
        ui.kpi(T("news.status.unconfirmed"), str(rep.unconfirmed), L("not counted", "不计入")),
        ui.kpi(T("news.flags"), str(len(rep.red_flags_confirmed)),
               ", ".join(rep.red_flags_confirmed) or L("none confirmed", "无已确认"), "",
               "down" if rep.red_flags_confirmed else "muted"),
    ]) + "</div>")
    if rep.red_flags_unconfirmed:
        st.warning(f"{T('news.flags')} ({T('news.status.unconfirmed')}): {', '.join(rep.red_flags_unconfirmed)}")
    if rep.red_flags_denied:
        st.info(f"{T('news.flags')} ({T('news.denied')}): {', '.join(rep.red_flags_denied)}")
    st.dataframe(pd.DataFrame([{
        L("Status", "状态"): ("✅ " if c.status.value != "UNCONFIRMED" else "· ") + T("news.status." + c.status.value.lower())
        + (" · " + T("news.denial") if c.denial else ""),
        L("Sentiment", "情绪"): round(c.sentiment, 2), L("Headline", "标题"): c.headline,
        L("Publishers", "来源"): ", ".join(c.publishers[:5]),
        L("Flags", "风险"): ", ".join(c.live_flags)
        + (f" ({T('news.denied')}: {', '.join(c.cleared_flags)})" if c.cleared_flags else "")}
        for c in rep.top(60)]), hide_index=True, width="stretch", column_config={
        L("Headline", "标题"): st.column_config.TextColumn(width="large")})
    st.caption(T("news.sources") + ": " + ", ".join(f"{k} {v}" for k, v in rep.sources.items())
               + (L(f" · {rep.off_topic} headlines about other companies or the market were ignored",
                    f" · 已忽略 {rep.off_topic} 条讲其他公司或大盘的新闻") if rep.off_topic else ""))


def page_account() -> None:
    cfg = get_config()
    H(ui.section(L("Broker account", "券商账户"), L(f"broker: {cfg.broker.name}", f"券商：{cfg.broker.name}")))
    if st.button(L("Load account", "读取账户"), icon=":material/account_balance_wallet:", type="primary"):
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
            floor = cfg.risk_profile().cash_floor
            cash_ratio = (acct.cash + cv) / eq
            H('<div class="ra-grid ra-g4">' + "".join([
                ui.kpi(T("account.equity"), ui.money(acct.equity), acct.account_id),
                ui.kpi(T("account.cash_total"), ui.pct(cash_ratio, 1), f"{T('account.floor')} {ui.pct(floor, 0)}", "",
                       "up" if cash_ratio >= floor else "down"),
                ui.kpi(T("account.stocks"), ui.pct(stocks / eq, 1), f"{len(pos)} {L('positions', '个持仓')}"),
                ui.kpi(T("account.treasury"), ui.pct(tr / eq, 1), cfg.universe.treasury_symbol),
            ]) + "</div>")
            c1, c2 = st.columns([1, 2])
            with c1:
                fig = go.Figure(go.Pie(labels=[T("account.cash"), "T-bills", T("account.treasury"), T("account.stocks")],
                                       values=[max(acct.cash, 0), cv, tr, max(stocks, 0)], hole=0.62, sort=False,
                                       marker=dict(colors=[P["neutral"], P["cyan"], P["violet"], P["accent"]])))
                fig.update_layout(**ui.plotly_layout(P, 280, showlegend=True))
                st.plotly_chart(fig, width="stretch", theme=None, config={"displayModeBar": False})
            with c2:
                st.dataframe(pd.DataFrame([{L("Symbol", "代码"): p.symbol, L("Shares", "股数"): p.qty,
                                            L("Price", "价格"): p.market_price, L("Value", "市值"): p.market_value,
                                            L("Weight", "权重"): p.market_value / eq,
                                            L("Gain", "盈亏"): p.unrealized_pct} for p in pos]),
                             hide_index=True, width="stretch", column_config={
                        L("Value", "市值"): st.column_config.NumberColumn(format="$%,.0f"),
                        L("Weight", "权重"): st.column_config.NumberColumn(format="percent"),
                        L("Gain", "盈亏"): st.column_config.NumberColumn(format="percent")})
    from riskaware.journal import Journal

    H(ui.section(L("Journal", "交易日志"), L("every run, decision and order of `riskaware run`",
                                                  "`riskaware run` 的每次运行、决策和订单")))
    j = Journal()
    runs = pd.DataFrame(j.recent_runs(50))
    if runs.empty:
        H(ui.empty("📒", L("No runs yet", "还没有运行记录"),
                         L("Try `riskaware run --dry-run`, or use the agents page.",
                           "试试 `riskaware run --dry-run`，或者用智能体页面。")))
        return
    st.dataframe(runs, hide_index=True, width="stretch")
    st.dataframe(pd.DataFrame(j.recent_orders(200)), hide_index=True, width="stretch")


# ------------------------------------------------------------------------------------------ layout
pages = [
    st.Page(page_market, title=L("Market", "市场"), icon=":material/monitoring:", default=True),
    st.Page(page_agents, title=L("Agents", "智能体"), icon=":material/smart_toy:", url_path="agents"),
    st.Page(page_signals, title=L("Signals", "信号"), icon=":material/radar:", url_path="signals"),
    st.Page(page_backtest, title=L("Backtest", "回测"), icon=":material/query_stats:", url_path="backtest"),
    st.Page(page_news, title=L("News", "新闻"), icon=":material/newspaper:", url_path="news"),
    st.Page(page_account, title=L("Account", "账户"), icon=":material/account_balance_wallet:", url_path="account"),
]
nav = st.navigation(pages, position="top")

top_l, top_r = st.columns([5, 1.3], vertical_alignment="center")
H(where=top_l, markup=ui.brand(T("app.name"), L("Macro-aware, risk-first trading agents for US stocks · paper trading first",
                                      "会看宏观、风控优先的美股交易智能体 · 默认模拟盘")))
with top_r:
    choice = st.segmented_control("lang", ["English", "中文"], default="中文" if LANG == "zh" else "English",
                                  label_visibility="collapsed", key="lang_switch")
    if choice and (choice == "中文") != (LANG == "zh"):
        st.session_state.lang = "zh" if choice == "中文" else "en"
        st.rerun()

nav.run()
H(f'<div class="ra-note" style="margin-top:40px;text-align:center">{ui.esc(T("disclaimer"))} · '
        f'<a href="https://github.com/jackson-cmd/risk-aware-trading-agents" target="_blank">GitHub</a></div>')
