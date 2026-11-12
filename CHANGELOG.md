# Changelog

[中文](CHANGELOG_zh.md)

## 0.3.0 (2026-11-12)

- Paper-trading agents: name any strategy (built-in or your own file) with a risk profile, a stock list and starting cash, and it trades its own local paper account every trading day. `riskaware agents init | add | run | list | show | pause | resume | reset | remove` (#5).
- Daily runs without a server: `riskaware agents schedule --install` adds a LaunchAgent (macOS), a crontab line (Linux) or a scheduled task (Windows). Each agent trades once per trading day after `execution.run_time` New York time, catches up after sleep, and skips holidays; a broken agent is retried at most three times and never stops the others.
- MCP server: `riskaware mcp` lets Claude, Cursor or any MCP client read the market regime, scan, analyze a stock, check the news, backtest and manage the paper agents. No tool can touch a live account.
- New dashboard: dark trading-terminal design with top navigation, a regime card, a fear-and-greed gauge, KPI cards with sparklines, an event timeline, an agents leaderboard with equity curves and controls, and backtest results that survive reruns. `riskaware dashboard` hides Streamlit's deploy button.
- `TradingEngine` takes a `state_root`, so several accounts can keep separate position memory.

## 0.2.0 (2026-11-12)

- Options in the backtester: Black-Scholes pricing on modelled implied volatility, long calls and puts, cash-secured puts, covered calls, daily marks, expiry, assignment and call-away (#1).
- Risk limits for options: premium sleeve and per-trade cap per profile, put collateral kept out of the cash floor and inside the single-stock cap, shares behind written calls protected from sells.
- New strategies: `aggressive_calls` and `wheel` (options), `momentum_rotation`, `donchian_breakout`, `bollinger_reversion`, `fear_greed_dca` (stocks).
- Live and paper runs skip option orders with a note; options are backtest-only for now.
- Fixes: a written covered call could be planned on shares sold in the same cycle; average holding time crashed when every closed trade had zero value.

## 0.1.0 (2026-11-11)

First public release.

- Macro regime engine: VIX, VIX term structure, sector breadth, credit, safe-haven demand, computed fear & greed gauge, CNN Fear & Greed, FOMC / CPI / jobs report / PPI / GDP / PCE calendar (FOMC 2015–2027 and BLS releases since 2010 bundled).
- Technical agent: EMA 50/100/200 support, mean reversion, quality dips with an earnings check, breakouts and trend continuation; take-profit and trend exits.
- Fundamental agent (earnings quality) and cross-verified news agent (Finviz, Google News, Nasdaq, Seeking Alpha, Yahoo, SEC 8-K).
- Risk manager: 30% cash + T-bill floor, position / sector / speculative caps, tranches with rebuild after trims, Treasury sleeve, T-bill cash management, drawdown pause, regime rules (no selling when uncertain).
- Brokers: paper, Alpaca, Public.com, Tradier, Interactive Brokers, Charles Schwab, tastytrade, Futu / moomoo, Tiger Brokers, Longbridge.
- Backtester with HTML reports and strategy comparison; custom strategy loader and template.
- Optional bull / bear / judge LLM review (Anthropic SDK or any OpenAI-compatible endpoint) with bounded influence.
- Bilingual CLI, Streamlit dashboard, reports, docs and website (English / 中文).
