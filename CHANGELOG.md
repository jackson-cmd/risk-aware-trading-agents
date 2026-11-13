# Changelog

[中文](CHANGELOG_zh.md)

## 0.6.2 (2026-11-12)

- Every number on the dashboard explains itself: a ? next to each card, section, table column and form field opens a popup (the browser's popover: click to open, click outside or Esc to close) with what it is, how it is calculated and how to use it, in English and Chinese. The texts follow the code and the balanced profile's defaults: the regime rules, the fear & greed and risk-score parts, the macro calendar's weights and windows, the technical (TA) score and setups, conviction and its parts, the earnings check, the risk manager's rules, the risk profiles, the news score, statuses, sentiment and red flags, and the backtest metrics. They live in `riskaware/dashboard/glossary.py`.
- The Signals page shows conviction's parts as columns: technical (TA), earnings, news and macro.
- The All metrics table has a column that explains each metric.
- Fix: the Agents page's SPY card said "since the first agent started" but used the leading agent's start date; it now uses the oldest agent's.
- The breadth card is labeled Breadth so it fits on one line.

## 0.6.1 (2026-11-12)

- The dashboard's Backtest page has a **News the backtest saw** panel. After a run with Replay news on, pick a trade (each one shows how many news items the backtest had seen when it decided), or any stock and day, to see the close at which it was decided, the order the strategy placed the next day, the news score with its confirmed and single-source stories and red flags, and every story with its New York time, status, sentiment and sources. It opens at the latest trade that had news.
- The News page's summary cards and the new panel share one component.

## 0.6.0 (2026-11-12)

- 2026 news ships with the package: 16,945 items for the 64 stocks of the built-in lists, January 1 to November 11, 2026 (16,249 Benzinga headlines with only the title, time and ticker, and 696 SEC 8-K filings), in `riskaware/news/data/news_2026.jsonl.gz` (0.7 MB). Backtests with `--news` read it together with your own archive; `news.bundled: false` leaves it out. A headline counts for a stock only when Benzinga tagged the stock and the headline names the company or its ticker; automated price recaps are left out. `scripts/build_news_bundle.py` rebuilds the file.
- Strategy lab: the MCP tools `strategy_guide` and `backtest_code` let Claude, Codex or Gemini write a strategy as code from a plain-words request and backtest it. The code is checked first (no dates or stock tickers in the code, imports from a short list, no files, network, `eval`, `getattr` or private attributes) and runs in a separate process with no API keys, a time limit, a CPU limit where available, and an audit hook that refuses network, subprocesses and file access while strategy code runs. Errors come back with the line in the strategy. `save_as` keeps a strategy in `~/.riskaware/strategies/ai/`; it then appears in `list_strategies`, on the dashboard and for `agents_create`. `riskaware lab FILE` does the same from the command line.
- No look-ahead: a backtest day sees only news published strictly before its close, and the close is 13:00 on early-close days; a date without a time counts as the end of that day; times without a zone are read as New York time (the CSV import default was UTC); items dated in the future are dropped. Indicator rows now hold a copy of that day's values and `ctx.history()` returns a copy, so a strategy can no longer reach later prices through the arrays behind them. New tests cover each rule, and a test recomputes the indicators on data cut at each day to show they use no later data.
- The dashboard's Backtest page has a **Replay news** switch, and saved AI strategies appear in its strategy lists and in the create-agent form.
- The MCP `backtest` tool takes `news=True`; backtests load news only for the symbols they trade.
- `news-history status` counts bundled items in their own column and prints what the bundle holds.
- The READMEs and the website show the 2026 backtest with news (January 2 to November 11, megacap): +15.9% against SPY's +12.1%, max drawdown -5.3% against -8.9%.

## 0.5.0 (2026-11-12)

- No API key needed: `--llm claude-cli[:MODEL]`, `codex-cli[:MODEL]` and `gemini-cli[:MODEL]` run each review through Claude Code, OpenAI Codex, Google's Gemini CLI or Antigravity, on the plan you are logged in to. The tool runs in an empty temporary folder with its tools off and the same strict JSON schema; `--llm-command` / `RISKAWARE_*_CLI` choose a different executable or wrapper. Also in the dashboard's LLM picker and `riskaware llm-check`.
- `riskaware mcp-setup claude-desktop | claude-code | codex | gemini | antigravity [--apply]` prints or runs the one step that connects the MCP server to that app (Claude Desktop's config is edited in place with a backup).
- News in backtests: `riskaware news-history` builds a local, point-in-time news archive (`~/.riskaware/news`) from SEC EDGAR 8-K filings, your own Benzinga API key, or any CSV, and `riskaware backtest --news` replays it through the same clustering and two-source verification used live. Items stamped after the 16:00 New York close become visible the next day.
- `news-history show SYMBOL --date DAY` prints exactly what a backtest saw at that close.
- New `core` universe preset (SPY, QQQ and the seven largest US stocks) and an example news strategy (`examples/strategies/news_earnings_drift.py`).
- `NewsItem.sentiment`: a sentiment score supplied by the data source (for example an imported CSV) overrides the headline lexicon.
- The `thesis_break` emergency exit now also works in backtests that use an archive, under the same rule as live trading: the company's own 8-K or press release must carry the severe flag.

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
