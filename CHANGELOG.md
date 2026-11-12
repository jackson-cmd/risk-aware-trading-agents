# Changelog

[中文](CHANGELOG_zh.md)

## 0.4.0 (2026-11-12)

- **Behavior change: emergency sells on news need the company itself.** A severe red flag now triggers the `thesis_break` exit only when the company's own SEC 8-K carries the item, or when its own press release confirms what at least two independent outlets reported an hour or more earlier, and no denial disputes it. Red flags confirmed by two independent outlets still block new buys and pull the news score down, but no longer force a sale. Allegations made by others (fraud, SEC charges, short-seller reports) never force a sale on their own; the profile's hard stop is the backstop.
- An LLM for each agent: `riskaware agents add NAME --llm openai:gpt-5.6-luna | ollama:qwen2.5:3b | anthropic:MODEL | compat:MODEL`, `riskaware agents llm NAME CHOICE`, and the same choice in the dashboard's create form and agent page. Local models run through Ollama for free.
- Every LLM review is stored in the agent's journal (prompt, raw reply, bull case, bear case, verdict, conviction before and after, latency, tokens). The dashboard's Decisions tab, `riskaware agents why NAME [SYMBOL] [--prompt]` and the new `agent_decisions` MCP tool show how each decision was made.
- `riskaware llm-check CHOICE` sends one sample review to check a key or a local model.
- OpenAI-compatible replies use strict JSON schema output (falling back to JSON mode, then plain text, where an endpoint has no schema support), and the parser reads only the first JSON object; gpt-5.x and o-series models get `max_completion_tokens` and `reasoning_effort` instead of a temperature, and a timed-out request is retried once.
- The daily new-position and buy limits now count positions opened earlier the same day, so several runs on one day cannot exceed them (backtests are unchanged).
- News: a story only counts toward a stock when its headline or summary names the company or its ticker; SEC filings always count. Before, a search for "COST stock" pulled in every article about costs, and another company's news could pin a confirmed red flag (for example an executive departure) on the wrong stock. Built-in names for every stock in the bundled lists, Yahoo's company name for the rest (cached). `news.relevance_filter: false` turns it off.
- Dashboard: the backtest charts keep one color per strategy; screenshot gallery in the READMEs and on the website.
- Press-release wires (Business Wire, PR Newswire, GlobeNewswire, Internet Wire, Accesswire, Newsfile and others) count as the company's own channel. Their stories get the new `ISSUER` status; outlets that repeat a release, and several wires carrying it, no longer count as independent confirmation. A fake press release and the outlets that rebroadcast it can block buys but cannot force a sale.
- Denials and negation: headlines that deny or reject a report, call it false, fake, baseless or a hoax, or say the company is not filing or has no plans to file, no longer raise the red flag they mention. A denial forms its own story, is never counted as a publisher of the story it denies, and clears that story's red flags when it is at least as well sourced (never an 8-K, never allegations by others). `NewsReport.red_flags_denied` lists the cleared flags and `NewsReport.red_flags_backed` the flags behind an emergency sell; the CLI, dashboard and MCP `stock_news` tool show them.
- "Ch. 11" and "Chap. 11" count as bankruptcy, and "restates" or "restated" now count against the news score like "restatement".

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
