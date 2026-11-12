# Contributing / 参与贡献

Thanks for helping. Brokers, data sources, strategies, translations and bug reports are all welcome.
感谢参与！新券商、数据源、策略、翻译和问题反馈都非常欢迎。

## Setup / 开发环境

```bash
git clone https://github.com/jackson-cmd/risk-aware-trading-agents
cd risk-aware-trading-agents
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,dashboard,llm]"
pytest                      # offline, about a minute
ruff check src tests
```

## Ground rules / 基本原则

- Tests run offline. Mock HTTP for brokers and data sources (see `tests/test_brokers.py`); mark anything that needs the internet with `@pytest.mark.network`.
  测试必须能离线运行。券商和数据源用 mock（参考 `tests/test_brokers.py`），需要联网的测试加 `@pytest.mark.network`。
- The risk manager has the last word. A strategy or an LLM can suggest; it cannot bypass the cash floor, the caps or the regime rules.
  风控说了算。策略和 LLM 只能提建议，不能绕过现金底线、各类上限和市场状态规则。
- Paper first. Nothing may place a live order without `execution.mode: live`, `--live` and a confirmation.
  默认模拟盘。没有 `execution.mode: live`、`--live` 和确认，任何代码都不能下实盘单。
- No secrets in code, tests, logs or screenshots.
  代码、测试、日志和截图里不能出现密钥、账户号。
- Keep user-facing text in both languages (`src/riskaware/i18n.py`, README / README_zh, docs / docs/zh).
  面向用户的文字请同时更新中英文（`i18n.py`、两份 README、docs 和 docs/zh）。

## Adding a broker / 接入新券商

Subclass `riskaware.brokers.base.Broker`, implement `account`, `positions`, `quotes`, `place_order`, `cancel_order`, `open_orders` and `is_paper`, register it in `riskaware/brokers/__init__.py`, document it in `docs/brokers.md` and `docs/zh/brokers.md`, and add a mocked test.
