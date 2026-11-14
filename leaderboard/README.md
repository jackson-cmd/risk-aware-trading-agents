# rta-bench-1 entries

Each file in `entries/` is one model on one task of the LLM trading agent leaderboard. The board is on the [website](https://jackson-cmd.github.io/risk-aware-trading-agents/leaderboard.html); how the benchmark works is in [docs/leaderboard.md](../docs/leaderboard.md).

To add a model:

```bash
pip install risk-aware-trading-agents
riskaware research bench --llm openai:gpt-5.6-luna            # six tasks x seeds 0, 1, 2 (--task, --seed for fewer)
riskaware research submit RUN_ID --model gpt-5.6-luna --cutoff 2026-02 --provider OpenAI
python scripts/build_leaderboard.py                           # in a checkout: the website's tables
```

Open a pull request with the new files. The Leaderboard workflow replays every entry (no model is called) and checks that the website's tables match. `anchors.json` holds the five rule strategies, SPY and the equal-weight list on the same settings; `riskaware research leaderboard --anchors` rebuilds it.

## 中文

`entries/` 里每个文件是一个模型在 LLM 交易智能体榜单上的一个任务。榜单在[网站](https://jackson-cmd.github.io/risk-aware-trading-agents/zh/leaderboard.html)上，基准怎么定的见 [docs/zh/leaderboard.md](../docs/zh/leaderboard.md)。

添加一个模型：用上面的命令跑任务、生成条目，在仓库目录里运行 `python scripts/build_leaderboard.py` 更新网站表格，然后开 PR 提交新文件。Leaderboard 工作流会重放每个条目（不调用模型），并检查网站表格和条目一致。`anchors.json` 是同样设置下五个规则策略、SPY 和等权组合的成绩，`riskaware research leaderboard --anchors` 可以重新生成。
