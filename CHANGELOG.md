# Changelog

[中文](CHANGELOG_zh.md)

## Unreleased

- News in backtests: `riskaware news-history` builds a local, point-in-time news archive (`~/.riskaware/news`) from SEC EDGAR 8-K filings, your own Benzinga API key, or any CSV, and `riskaware backtest --news` replays it through the same clustering and two-source verification used live. Items stamped after the 16:00 New York close become visible the next day.
- `news-history show SYMBOL --date DAY` prints exactly what a backtest saw at that close.
- New `core` universe preset (SPY, QQQ and the seven largest US stocks) and an example news strategy (`examples/strategies/news_earnings_drift.py`).
- `NewsItem.sentiment`: a sentiment score supplied by the data source (for example an imported CSV) overrides the headline lexicon.
- Confirmed severe news (bankruptcy, delisting, restatement) now triggers the emergency exit in backtests too, when an archive is used.

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
