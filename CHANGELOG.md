# Changelog

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
