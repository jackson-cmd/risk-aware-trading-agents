# prompt 测试：什么样的 prompt 让 LLM 交易智能体做得更好

[English](../prompts.md)

大家调交易智能体的 system prompt 多半凭感觉：加个角色、加几条风险规则、让它先推理、贴几个示例。`riskaware research prompts` 把这些做法一个一个量出来。每个 prompt 变体只比包里自带的 prompt 改一处；所有变体用同一个模型、在同样的冻结时段上盲测交易，新闻按发布时间回放，每个都跑几个种子。每个变体都和同一时段、同一种子的基线配对比较，报告会说明哪些差别比「同一个 prompt 换个种子」的差别还大。

<!-- RESULTS -->

## 变体

基线是 `agent.SYSTEM_PROMPT`，也就是 `riskaware research trade` 和 MCP 交易会话用的 prompt：每只股票 3 条新闻，每 5 个交易日决策一次，回复受严格 JSON schema 约束。其余每个变体只改一个因素：

| 变体 | 因素 | 改了什么 |
|---|---|---|
| `baseline` | — | 包里自带的 prompt |
| `role_pm` | 角色 | 开头加「You are an experienced portfolio manager at a long-only equity fund.」 |
| `role_quant` | 角色 | 开头加「You are a quantitative researcher at a systematic hedge fund.」 |
| `role_risk` | 角色 | 开头加「You are the risk manager of an investment firm.」 |
| `risk_rules` | 风险规则 | 单只不超过 20%，亏 8% 卖出，至少留 20% 现金（risk-off 或 VIX 高于 30 时 50%），有红旗不买 |
| `reason_first` | 先推理 | 订单前先写 3 到 5 句简短推理（`reasoning` 字段） |
| `focus_news` | 侧重 | 主要看新闻（是否有多家确认、多新、红旗），价格只用来择时 |
| `focus_trend` | 侧重 | 主要看涨跌幅、均线、RSI 和离高点多远，新闻只用来避开红旗 |
| `few_shot` | 示例 | 三个编造的示例：一次买入、一次因红旗卖出、一次持有 |
| `format_json` | 输出格式 | 只要求是 JSON 对象（JSON mode），schema 只写在 prompt 里 |
| `format_text` | 输出格式 | 什么都不强制：不开 JSON mode、不给 schema，只靠 prompt 里那句格式说明 |
| `news_0` | 新闻条数 | 不给新闻标题；新闻分数、确认数和红旗还在 |
| `news_6` | 新闻条数 | 每只股票 6 条新闻，而不是 3 条 |
| `daily` | 决策频率 | 每个交易日都决策，而不是每 5 天一次 |

`riskaware research prompts list` 列出这张表和每个变体的 SHA-256，`--show <名字>` 打印全文。变体集有版本号（`SET_VERSION`）：改了文字就是新版本，不在旧版本上改，所以发表过的数字永远对得上同一份 prompt。`few_shot` 里的示例股票用的是编造的代号（S81 到 S97），不会和盲测里真实股票的代号（S01 到 S64）混淆。

## 不变的部分

同一个实验文件夹（lab）里，每次运行的模型、温度、回复长度上限、股票池、持仓数、本金、滑点和盲测设置都一样。一次运行就是一个变体、一个时段、一个种子。种子固定两件事：盲测时每只股票拿到哪个代号，以及传给模型 API 的采样种子（OpenAI 兼容的服务和 Ollama 都接受）。所以同一时段、同一种子的两个变体看到的是同一套代号、用的是同一串随机数，它们之间的差别，在模型本身带随机性的前提下，已经尽量只来自 prompt。

每次运行都走和其他 LLM 运行一样的流程（`agent.run_policy`）：智能体只看得到当天收盘时已知的信息，订单在下一个交易日开盘成交；防偷看检查会把它的回复重放到另一份市场上，那份市场在切点之后的价格和新闻都换成随机的，切点之前的观察必须完全一样，每份记录都写明有没有通过。

## 每次运行记下什么

运行文件夹（`<lab>/runs/<变体>/<时段>-s<种子>/`）是一份普通的研究记录（`manifest.json`、`metrics.json`、`equity.csv`、`trades.csv`、`decisions.csv`、`policy_log.json`、`report.md`、`equity.png`），另外还有：

- `prompt.txt`：实际发出去的 system prompt；
- `manifest.json` 里的 `prompt`：变体名字、因素和设置，`prompt_sha256`（只算文字）、`variant_sha256`（文字、回复 schema 和设置一起算）和变体集版本；`llm`：模型、温度、种子和回复上限；`reproduce`：重跑的命令；
- `calls.jsonl`：每次模型调用一行，有状态（`ok`、`format` 或 `transport`）、输入输出 token、耗时、重试次数，回复没法用时还有原文。

调用途中失败（限速、超时、服务器出错）会隔一会儿重试，最多四次，免得把断线当成「持有」的决定。回复不是带订单列表的 JSON 对象，记为格式错误；订单里的股票代号不存在、卖出没持有的股票、动作不认识，记为无效订单。这两种情况下智能体那一步都按持有处理，和实盘一样。

## 怎么比较

对每个变体、每个指标，报告把它的每次运行和同一时段、同一种子的基线配对，取差值，然后：

- **效果**：每个时段内差值的平均，再对时段取平均，每个时段权重一样；
- **95% bootstrap 区间**：在每个时段内重抽配对，抽 10,000 次；
- **p 值**：精确的符号翻转检验（把配对差的正负号所有翻法都算一遍），不需要假设分布的形状；
- **多重比较校正**：每个指标在所有变体之间做 Holm 校正；报告另外给出 Benjamini-Hochberg q 值作为宽松一些的参考；
- **结论**：Holm 校正后 p 小于 0.05 时记为 `better` 或 `worse`，否则记为 `noise`。没有好坏方向的行为指标（股票仓位、换手、每步订单数）记为 `differs` 或 `same`；
- **噪音底线**：同一个时段上基线两个种子之间差多少。差别不比这个大的变体，和把同一个 prompt 再跑一遍分不开。

指标有 Sharpe 比率（主指标）、总收益、最大回撤、相对 SPY 的收益、波动率、股票仓位、换手和每步订单数（行为）、格式错误率和无效订单率（可靠性），以及每步 token（成本）。每个时段都列出 SPY 和股票池等权组合作为参照。

证据能有多强：n 对数据的双侧符号翻转检验，最小的 p 值是 2/2ⁿ；Holm 校正会把最小的 p 值乘以变体个数。十三个变体时，九对（三个时段、三个种子）永远过不了：13 × 2/512 是 0.051。所以默认用四个种子，每个变体十二对，十二对里有十一或十二对同向的差别才可能通过。实验太小、什么都显示不出来时，报告会直接说明。只在某些时段、某些种子上出现的 prompt 效果，不值得依赖。

## 自己跑

```bash
riskaware research prompts list
```

先跑 pilot：一个变体、一个时段、一个种子，量出每步的 token。

```bash
riskaware research prompts run --llm ollama:qwen2.5:3b --variants baseline --periods 2026 --seed-list 0 --out pilot
```

再按 pilot 实测的 token 估算整组实验的价钱，不调用任何模型：

```bash
riskaware research prompts run --llm openai:gpt-5.6-luna --pilot pilot --price-in 0.20 --price-out 1.20 --dry-run
```

然后跑整组实验，再出报告。已有的实验文件夹会接着跑，跑完的会跳过。

```bash
riskaware research prompts run --llm openai:gpt-5.6-luna --workers 8 --out lab --price-in 0.20 --price-out 1.20
```

```bash
riskaware research prompts report lab
```

你自己的 prompt 作为一个额外的变体，和内置变体一起跑（用基线的设置）：

```bash
riskaware research prompts run --llm ollama:qwen2.5:3b --variants baseline --prompt-file my_prompt.txt --out lab
```

在 Python 里：

```python
from riskaware.research import prompts as P

settings = P.LabSettings(llm="ollama:qwen2.5:3b", periods=["2026", "2022", "2020"], seeds=[0, 1, 2])
lab = P.run_lab(P.select("baseline,risk_rules,reason_first"), settings, "lab", workers=2)
result = P.report(lab)  # 同时在实验文件夹里写 prompts.json、prompts.md 和 prompts.png
```

`run_lab(..., provider_factory=lambda variant, seed: my_client)` 可以换成任何有 `complete_json(system, user, schema)` 方法的对象，不用内置的模型调用。

本地模型：`ollama:` 模型走 Ollama 自己的 `/api/chat`，每次请求都带 16,384 token 的上下文窗口。Ollama 的 OpenAI 兼容端点用服务器默认的上下文（默认安装是 4,096 token），更长的 prompt 会被悄悄截掉，few-shot 和 6 条新闻的变体就会丢掉一部分观察内容。

## 网页数据

`riskaware research prompts report lab --site site/assets/data/prompts.json` 写出网站上 prompt 榜的数据（格式 `rta-prompt-board-1`）：

- `model`、`baseline`、`primary_metric`、`set_version`、`generated_utc`；
- `settings`：股票池、股票列表、最多持仓数、温度、是否盲测、是否有新闻、时段、种子；
- `benchmarks`：每个时段的起止日期，以及 SPY 和等权组合的收益、Sharpe、回撤；
- `noise`：基线两个种子之间 Sharpe 的平均差距，以及各时段的标准差；
- `variants`：每个变体一条，基线在最前，有 `name`、`factor`、`about`（`en`、`zh`）、两个 SHA-256、`runs`、`settings`、`metrics`（每项有 `mean` 和 `by_period`：sharpe、total_return、max_drawdown、excess_vs_spy、turnover、format_error_rate、invalid_order_rate）、`tokens_per_step` 和 `vs_baseline`（基线为 null；其他变体每个指标有 `diff`、`ci95`、`p`、`p_holm`、`pairs`、`by_period` 和 `verdict`）；
- `cost`：调用次数、输入输出 token、按标价算的金额；
- `method`：一句中英文说明，告诉读者怎么看结论。

## 局限

- 一个实验文件夹只用一个模型。对一个模型有用的 prompt 可能对另一个有害；要依赖某个结论，先用你自己的模型再跑一遍。
- 盲测降低了模型认出股票或日期的可能，但不能完全排除。
- 成本用的是研究工具箱的默认值（5 个基点滑点，不收佣金）。报告列出换手，方便看出哪个变体只是靠多交易赢的。
- 三个时段是三种行情，不是所有行情的样本。三个时段都成立的结论比只在一个时段成立的强，但仍然不是定律。
