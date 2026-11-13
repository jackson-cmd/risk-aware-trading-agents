# Contributing / 参与贡献

Thanks for helping. Brokers, data sources, strategies, translations and bug reports are all welcome.
感谢参与！新券商、数据源、策略、翻译和问题反馈都非常欢迎。

Please follow the [code of conduct](CODE_OF_CONDUCT.md). 参与时请遵守[行为准则](CODE_OF_CONDUCT.md)。

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

## Releasing / 发布新版本

1. In the PR, bump the version in `pyproject.toml` and `src/riskaware/__init__.py`, add its section to `CHANGELOG.md` and `docs/zh/CHANGELOG.md`, and update the version badge in both READMEs. `tests/test_version.py` fails if any of them disagree.
   在 PR 里同时改 `pyproject.toml` 和 `src/riskaware/__init__.py` 的版本号，在 `CHANGELOG.md` 和 `docs/zh/CHANGELOG.md` 加上这一版的说明，并更新两份 README 的版本徽章；有一处对不上，`tests/test_version.py` 就会失败。
2. Merge it. When CI passes on main, `.github/workflows/release.yml` tags the commit `vX.Y.Z`, publishes a GitHub release whose notes are that changelog section, attaches the wheel and the source archive, and pushes the Docker image `ghcr.io/jackson-cmd/risk-aware-trading-agents` (tags `X.Y.Z`, `X.Y` and `latest`). Nothing else to do.
   合并即可。main 上的 CI 通过后，`.github/workflows/release.yml` 会给这个提交打上 `vX.Y.Z` 标签，用那一节更新说明发布 GitHub Release，附上 wheel 和源码包，并推送 Docker 镜像 `ghcr.io/jackson-cmd/risk-aware-trading-agents`（标签 `X.Y.Z`、`X.Y` 和 `latest`），不用手动操作。
3. PyPI uploads run once the repository variable `PYPI_PUBLISH` is `true` and the PyPI project trusts this workflow (trusted publisher: workflow `release.yml`, environment `pypi`). To publish an existing release again, for example right after setting that up, run the workflow by hand with its tag: `gh workflow run release.yml -f tag=vX.Y.Z`.
   仓库变量 `PYPI_PUBLISH` 设为 `true`、并且 PyPI 项目信任这个工作流（可信发布者：工作流 `release.yml`，环境 `pypi`）之后，才会上传到 PyPI。要重新发布已有的版本（比如刚配好 PyPI），手动运行这个工作流并带上标签：`gh workflow run release.yml -f tag=vX.Y.Z`。

## Adding a broker / 接入新券商

Subclass `riskaware.brokers.base.Broker`, implement `account`, `positions`, `quotes`, `place_order`, `cancel_order`, `open_orders` and `is_paper`, register it in `riskaware/brokers/__init__.py`, document it in `docs/brokers.md` and `docs/zh/brokers.md`, and add a mocked test.
