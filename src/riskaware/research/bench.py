"""rta-bench-1: a public benchmark for LLM trading agents on the frozen periods.

A task is one bundled period traded with the real names (``named``) or blind (tickers, company names and dates
hidden, prices rebased to 100): ``2026-blind``, ``2026-named``, ``2022-blind``, ``2022-named``, ``2020-blind`` and
``2020-named``. Every model plays the same game (``SETTINGS``): the 56 stocks of the ``megacap`` list in 11
sectors, one decision after every fifth close, at most 10 positions, up to three headlines per stock that were
published before the close, orders at the next open with realistic fill costs, long only, $100,000.
Each task is run with seeds 0, 1 and 2 (``SEEDS``), one entry per seed; the board shows the mean and the range.

Why megacap and not a smaller list: a model can only show how it makes money if it can choose between sectors (in
this list energy stocks rose 73% on average in 2022 while tech fell 32%; in 2026 energy and health care beat tech);
the 18 stocks of ``tech`` leave it one sector and cash. A decision costs 11,000 to 15,000 input tokens on megacap,
depending on the model's tokenizer (tech would be about 40% of that); with 44, 50 and 51 decisions in 2026, 2022 and
2020, all six tasks are 3 to 4.5 million input tokens: well under a dollar on a small model and about $10 to $15 on
a Sonnet-class model at list prices.

Training cutoffs: a window that starts after a model's training cutoff cannot be in its training text, so its score
is ``clean``; a window that ends before the cutoff ``may_remember`` and is listed apart; a window that the cutoff
falls inside is ``partly`` clean and also scored from its first trading day after the cutoff (``after_cutoff``).
Named and blind runs of the same period are a second memory check: a model that does much better when it sees the
names is likely using what it remembers.

* ``run_task(policy, "2026-blind")`` runs a task with any policy (``bench_policy("openai:gpt-5.6-luna")``, rules, a
  person) and saves the record;
* ``make_entry(folder, model=..., cutoff="2025-03")`` checks a record against the benchmark and returns the entry
  for ``leaderboard/entries/<model>-<task>.json``, with the run analysis of ``research.analysis`` (where the return
  came from, how good the decisions were). A run made with other fill costs is first replayed under the benchmark's
  (``rescore``). The entry keeps the model's replies, so
* ``verify_entry(entry)`` replays them through the backtest (no model is called) and checks every number;
* ``board(entries, anchors)`` puts the entries side by side with the anchors: the five rule strategies, SPY and the
  equal-weight universe (``anchor_rows``).
"""

from __future__ import annotations

import calendar
import hashlib
import json
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from ..news.files import missing_message
from ..universe import preset_symbols
from .agent import ORDER_SCHEMA, SYSTEM_PROMPT, _Replay, run_policy
from .data import bundled_market, dataset_info
from .experiment import CASH_VEHICLES, Experiment, ExperimentResult, run_experiment
from .periods import get_period

BENCH_VERSION = "rta-bench-1"
BENCH_PERIODS = ("2026", "2022", "2020")
MODES = ("blind", "named")
TASKS = tuple(f"{p}-{m}" for p in BENCH_PERIODS for m in MODES)

SETTINGS: dict[str, Any] = {
    "universe": "megacap",
    "decide_every": 5,
    "max_positions": 10,
    "position_size": None,  # 1 / max_positions
    "news": True,
    "max_news": 3,
    "execution": "plain",
    "cash": 100_000.0,
    "slippage_bps": 5.0,
    "commission_per_share": 0.0,
    "costs": "realistic",  # the slippage plus part of the overnight gap plus square-root impact (docs/costs.md)
    "mode": "long_only",
}
# Each task is run with several seeds: a seed fixes the blind aliases and, where the API takes one, the sampling
# seed, and LLM sampling makes two runs differ anyway. The board shows the mean over a model's seeds and their range.
SEEDS = (0, 1, 2)
# How the board's own runs call a model (bench_policy). A submission may call its model differently (a provider
# without a temperature, a reasoning budget); the entry says how under "model".
MODEL_CALL: dict[str, Any] = {
    "system": "research.agent.SYSTEM_PROMPT followed by the order schema", "temperature": 0.2, "max_tokens": 2000,
    "reasoning_effort": "none for GPT-5.x on the OpenAI API", "output": "JSON (strict schema where the API has it)",
    "ollama": "native /api/chat with num_ctx 16384 (the OpenAI-compatible endpoint cuts prompts at 4,096 tokens)",
}
OLLAMA_NUM_CTX = 16384
# what an entry copies from the record's metrics (metrics.json "strategy") and statistics against SPY ("vs_spy")
ENTRY_METRICS = ("total_return", "cagr", "volatility", "sharpe", "sortino", "max_drawdown", "calmar", "win_rate",
                 "profit_factor", "round_trips", "avg_holding_days", "turnover", "avg_exposure", "trades")
ENTRY_STATS = ("beta", "alpha_annual", "excess_return_annual", "information_ratio", "t_stat_excess",
               "sharpe_ci95_low", "sharpe_ci95_high", "p_sharpe_above_spy", "days")
RECORD_FILES = ("manifest.json", "metrics.json", "equity.csv", "trades.csv", "decisions.csv", "policy_log.json")
VERIFY_KEYS = ("total_return", "sharpe", "max_drawdown", "round_trips", "turnover")


class BenchError(ValueError):
    """A record that is not a valid benchmark run; ``problems`` lists why."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Task:
    name: str
    period: str
    blind: bool

    @property
    def mode(self) -> str:
        return "blind" if self.blind else "named"


def get_task(name: str | Task) -> Task:
    """``"2026-blind"`` -> Task(period="2026", blind=True)."""
    if isinstance(name, Task):
        return name
    key = str(name).strip().lower()
    if key not in TASKS:
        raise ValueError(f"unknown task {name!r}; the tasks are {', '.join(TASKS)}")
    period, mode = key.split("-")
    return Task(key, period, mode == "blind")


def bench_symbols() -> list[str]:
    return [s for s in preset_symbols(SETTINGS["universe"]) if s not in CASH_VEHICLES]


def window(period: str) -> tuple[date, date]:
    """First and last day of a task's window: the period's whole test window."""
    p = get_period(period)
    return p.start, p.end


def costs_supported() -> bool:
    """Whether this version of the package has the cost models (``Experiment.costs``)."""
    return "costs" in getattr(Experiment, "__dataclass_fields__", {})


def experiment(task: str | Task, label: str | None = None, seed: int = 0) -> Experiment:
    """The experiment settings of a task with one seed."""
    t = get_task(task)
    s = SETTINGS
    start, end = window(t.period)
    exp = Experiment(strategy="llm_policy", period=t.period, start=start.isoformat(), end=end.isoformat(),
                     universe=s["universe"], news=s["news"], execution=s["execution"],
                     max_positions=s["max_positions"], position_size=s["position_size"], cash=s["cash"],
                     slippage_bps=s["slippage_bps"], commission_per_share=s["commission_per_share"], seed=int(seed),
                     label=label or f"bench-{t.name}")
    if costs_supported():
        exp.costs = s["costs"]  # type: ignore[attr-defined]
    return exp


def describe() -> dict[str, Any]:
    """The benchmark in one JSON object: version, tasks, settings and how its own runs call a model."""
    return {"bench": BENCH_VERSION, "tasks": list(TASKS), "seeds": list(SEEDS), "settings": dict(SETTINGS),
            "symbols": bench_symbols(),
            "model_call": MODEL_CALL,
            "windows": {p: {"start": get_period(p).start.isoformat(), "end": get_period(p).end.isoformat()}
                        for p in BENCH_PERIODS}}


# ----------------------------------------------------------------------------------- running a task
def bench_policy(choice: str, base_url: str | None = None, system_prompt: str = SYSTEM_PROMPT,
                 temperature: float = 0.2, max_tokens: int = 2000,
                 on_reply: Callable[[dict, dict | None], None] | None = None) -> Callable[[dict], dict]:
    """``agent.llm_policy`` with the board's call settings, counting tokens in ``policy.usage``.

    ``ollama:<model>`` goes to Ollama's native chat API with a 16,384-token context: its OpenAI-compatible endpoint
    keeps the server's default context (4,096 tokens) and silently drops the start of a longer prompt, and a
    megacap observation is about 10,000 tokens."""
    usage: dict[str, Any] = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "failed": 0, "seconds": 0.0}
    if choice.lower().startswith("ollama:"):
        ask = _ollama_caller(choice.split(":", 1)[1], base_url, temperature, max_tokens)
        call = {"provider": "ollama", "model": choice.split(":", 1)[1], "num_ctx": OLLAMA_NUM_CTX}
    else:
        from ..llm import llm_config, make_provider

        cfg = llm_config(choice, base_url=base_url)
        cfg.temperature, cfg.max_tokens = float(temperature), int(max_tokens)
        provider = make_provider(cfg)

        def ask(system: str, user: str) -> tuple[dict | None, dict, str | None]:
            reply = provider.complete_json(system, user, ORDER_SCHEMA)
            return reply, dict(getattr(provider, "last_usage", None) or {}), getattr(provider, "last_error", None)

        call = {"provider": cfg.provider, "model": cfg.model, "base_url": cfg.base_url,
                "reasoning_effort": cfg.reasoning_effort}

    def policy(obs: dict) -> dict:
        user = "Today's observation (JSON):\n" + json.dumps(obs, separators=(",", ":"), default=str)
        t0 = time.time()
        reply, u, err = ask(system_prompt, user)
        usage["calls"] += 1
        usage["seconds"] = round(usage["seconds"] + time.time() - t0, 1)
        usage["input_tokens"] += int(u.get("prompt_tokens") or u.get("input_tokens") or 0)
        usage["output_tokens"] += int(u.get("completion_tokens") or u.get("output_tokens") or 0)
        if reply is None:
            usage["failed"] += 1
        if on_reply is not None:
            on_reply(obs, reply)
        return reply or {"orders": [], "note": f"no usable reply: {err or ''}"[:300]}

    policy.usage = usage  # type: ignore[attr-defined]
    policy.model_call = {**call, "temperature": float(temperature), "max_tokens": int(max_tokens),  # type: ignore[attr-defined]
                         "system_prompt_sha256": _sha(system_prompt)}
    return policy


def _ollama_caller(model: str, base_url: str | None, temperature: float, max_tokens: int) -> Callable:
    import requests

    from ..llm.openai_compat import parse_json_object

    root = (base_url or "http://localhost:11434").rstrip("/").removesuffix("/v1")
    session = requests.Session()

    def ask(system: str, user: str) -> tuple[dict | None, dict, str | None]:
        body = {"model": model, "stream": False, "format": ORDER_SCHEMA,
                "messages": [{"role": "system", "content": system + "\n\nReply with one JSON object that matches "
                              "this JSON schema:\n" + json.dumps(ORDER_SCHEMA)}, {"role": "user", "content": user}],
                "options": {"num_ctx": OLLAMA_NUM_CTX, "temperature": float(temperature), "num_predict": int(max_tokens)}}
        data: dict = {}
        for wait in (5, 30, 120, None):  # a server that is loading the model or restarting refuses for a while
            try:
                r = session.post(f"{root}/api/chat", json=body, timeout=900)
                r.raise_for_status()
                data = r.json()
                break
            except requests.ConnectionError as exc:
                if wait is None:
                    return None, {}, f"{type(exc).__name__}: {exc}"[:300]
                time.sleep(wait)
            except (requests.RequestException, ValueError) as exc:
                return None, {}, f"{type(exc).__name__}: {exc}"[:300]
        u = {"prompt_tokens": data.get("prompt_eval_count"), "completion_tokens": data.get("eval_count")}
        if (u["prompt_tokens"] or 0) >= OLLAMA_NUM_CTX - 64:
            return None, u, f"the prompt filled the {OLLAMA_NUM_CTX}-token context"
        parsed = parse_json_object((data.get("message") or {}).get("content") or "")
        return parsed, u, None if parsed is not None else "the reply was not a JSON object"

    return ask


def need_news(period: str) -> None:
    """Stop before anything runs when the benchmark settings use news and the period's headline file is missing."""
    per = get_period(period)
    if SETTINGS["news"] and not per.has_news:
        raise FileNotFoundError(f"{BENCH_VERSION} replays the {per.name} headlines and no copy of {per.news} was "
                                f"found: {missing_message(per.news)}")


def run_task(policy: Callable[[dict], Any], task: str | Task, label: str = "policy", save: bool = True,
             check: bool = True, seed: int = 0) -> ExperimentResult:
    """Trade one task with ``policy`` under the benchmark settings and save the record (plus ``bench.json``, and
    ``usage.json`` when the policy counts its tokens). The task shows the period's headlines, so a policy that reads
    the observations needs your copy of the headline file (``FileNotFoundError`` before any call when there is
    none). A replay of recorded replies does not: its orders are fixed, so its numbers are the same without it."""
    t = get_task(task)
    if not isinstance(policy, _Replay):
        need_news(t.period)
    t0 = time.time()
    res = run_policy(policy, experiment(t, label=label, seed=seed), decide_every=SETTINGS["decide_every"],
                     blind=t.blind, max_news=SETTINGS["max_news"], label=label, save=save, check=check)
    if save and res.folder is not None:
        folder = Path(res.folder)
        meta = {"bench": BENCH_VERSION, "task": t.name, "seed": int(seed), "settings": SETTINGS,
                "model_call": getattr(policy, "model_call", None), "seconds": round(time.time() - t0, 1)}
        (folder / "bench.json").write_text(json.dumps(meta, indent=1, default=str) + "\n", encoding="utf-8")
        usage = getattr(policy, "usage", None)
        if usage:
            (folder / "usage.json").write_text(json.dumps(usage, indent=1) + "\n", encoding="utf-8")
    return res


# ----------------------------------------------------------------------------------- checking a record
#: What an entry publishes of each order: the action only. The model's reason for an order, and the note of each
#: reply, are free text that can echo the headlines it read, and the headlines are licensed.
ORDER_FIELDS = ("symbol", "action", "weight", "fraction")


def public_replies(replies: list[Any]) -> list[Any]:
    """The replies as an entry publishes them: each order without its reason, each reply with its orders only (no
    note, and no other key a model wrote, such as a malformed ``"note:"``). A replay reads only the orders, so every
    replayed number stays the same."""
    out: list[Any] = []
    for r in replies:
        if not isinstance(r, dict):
            out.append(r)
            continue
        kept = {k: v for k, v in r.items() if k == "orders"}
        if isinstance(kept.get("orders"), list):
            kept["orders"] = [{k: o[k] for k in ORDER_FIELDS if k in o} if isinstance(o, dict) else o
                              for o in kept["orders"]]
        out.append(kept)
    return out


def _sha(obj: Any) -> str:
    data = obj if isinstance(obj, (bytes, str)) else json.dumps(obj, sort_keys=True, separators=(",", ":"),
                                                                  default=str)
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def _read(folder: Path, name: str) -> Any:
    path = folder / name
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    return json.loads(text) if name.endswith(".json") else text


def check_record(folder: str | Path) -> tuple[Task | None, list[str]]:
    """Which task a saved policy run is, and every way it differs from the benchmark (an empty list: a valid run)."""
    folder = Path(folder).expanduser()
    man, log = _read(folder, "manifest.json"), _read(folder, "policy_log.json")
    if man is None or log is None:
        return None, [f"{folder} is not a policy run: manifest.json and policy_log.json are needed"]
    e = man.get("experiment") or {}
    problems: list[str] = []
    period = str(e.get("period"))
    if period not in BENCH_PERIODS:
        return None, [f"period {period!r} is not one of {', '.join(BENCH_PERIODS)}"]
    task = get_task(f"{period}-{'blind' if log.get('blind') else 'named'}")
    start, end = window(period)
    want = {**SETTINGS, "start": start.isoformat(), "end": end.isoformat()}
    got = {**{k: e.get(k) for k in SETTINGS}, "start": e.get("start"), "end": e.get("end"),
           "decide_every": log.get("decide_every"), "max_news": log.get("max_news"),
           "costs": e.get("costs", "fixed"), "mode": e.get("mode", "long_only")}
    got["universe"] = SETTINGS["universe"] if sorted(man.get("symbols") or []) == sorted(bench_symbols()) else (
        e.get("universe") if not e.get("symbols") else "custom")
    for k, v in want.items():
        g = got.get(k)
        same = (math.isclose(float(g), float(v)) if isinstance(v, float) and isinstance(g, (int, float))
                else g == v)
        if not same:
            problems.append(f"{k} is {g!r}, the benchmark uses {v!r}")
    if e.get("extra_news"):
        problems.append("the run added headlines (extra_news)")
    if e.get("mode_params"):
        problems.append("the run changed the mode's rules (mode_params)")
    if not isinstance(e.get("seed", 0), int) or e.get("seed", 0) < 0:
        problems.append(f"seed must be a whole number, got {e.get('seed')!r}")
    ds, now = man.get("dataset") or {}, dataset_info(period)
    if (ds.get("prices") or {}).get("sha256") != now["prices"]["sha256"]:
        problems.append("the price file differs from the bundled one (SHA-256)")
    run_news = (ds.get("news") or {}).get("sha256")
    if run_news is None:
        problems.append(f"the run saw no headlines: no copy of {now['news']['file']} was found (the headlines are "
                        "licensed and are not distributed with the package; see docs/news-history.md)")
    elif run_news != now["news"]["manifest_sha256"]:
        problems.append("the news file differs from the period's headline file (SHA-256 in the manifest)")
    leak = man.get("leak_check") or {}
    if leak.get("verdict") != "pass":
        problems.append(f"the look-ahead check of the observations is {leak.get('verdict')!r}, not 'pass'")
    replies, steps = log.get("replies"), log.get("steps")
    if not isinstance(replies, list) or not replies:
        problems.append("policy_log.json has no replies")
    elif not isinstance(steps, list) or len(steps) != len(replies):
        problems.append("policy_log.json: the number of steps and replies differ")
    return task, problems


# ----------------------------------------------------------------------------------- training cutoffs
def cutoff_day(cutoff: str) -> date:
    """The last day a cutoff covers: ``"2025-03"`` -> 2025-03-31, ``"2025-03-14"`` -> 2025-03-14."""
    text = str(cutoff).strip()
    if re.fullmatch(r"\d{4}-\d{2}", text):
        y, m = int(text[:4]), int(text[5:])
        return date(y, m, calendar.monthrange(y, m)[1])
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return date.fromisoformat(text)
    raise ValueError(f"a training cutoff looks like YYYY-MM or YYYY-MM-DD, got {cutoff!r}")


def contamination(period: str, cutoff: str | None) -> str:
    """``clean`` (the window starts after the cutoff), ``partly``, ``may_remember`` or ``unknown``."""
    if not cutoff:
        return "unknown"
    (start, end), c = window(period), cutoff_day(cutoff)
    if c < start:
        return "clean"
    if c >= end:
        return "may_remember"
    return "partly"


def _rf(period: str) -> pd.Series | None:
    bil = bundled_market(period).history(["BIL"]).get("BIL")
    return None if bil is None else bil["close"].pct_change().fillna(0.0).clip(-0.01, 0.01)


def window_metrics(equity: pd.DataFrame, period: str, after: date | None = None) -> dict[str, Any] | None:
    """Return, Sharpe and drawdown of the run, SPY and the equal-weight universe over the record's equity curve, from
    the last close on or before ``after`` (all of it when ``after`` is None)."""
    from ..backtest import metrics as M

    eq = equity
    if after is not None:
        base = eq.index[eq.index <= pd.Timestamp(after)]
        eq = eq[eq.index >= base[-1]] if len(base) else eq
    if len(eq) < 21:
        return None
    rf = _rf(period)
    out: dict[str, Any] = {"from": str(eq.index[0].date()), "to": str(eq.index[-1].date()), "days": int(len(eq) - 1)}
    for col, key in (("equity", ""), ("spy", "spy_"), ("equal_weight", "equal_weight_")):
        m = M.summarize(eq[col].astype(float), rf)
        for k in ("total_return", "sharpe", "max_drawdown", "volatility"):
            out[key + k] = _num(m.get(k))
    out["excess_vs_spy"] = _num(out["total_return"] - out["spy_total_return"])
    out["excess_vs_equal_weight"] = _num(out["total_return"] - out["equal_weight_total_return"])
    return out


def _num(v: Any, nd: int = 6) -> Any:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else round(f, nd)


def _equity(folder: Path) -> pd.DataFrame:
    return pd.read_csv(folder / "equity.csv", index_col="date", parse_dates=True)


# ----------------------------------------------------------------------------------- entries
def model_slug(model: str) -> str:
    slug = re.sub(r"[^a-z0-9.]+", "-", str(model).lower()).strip("-.")
    if not slug:
        raise ValueError("name the model, e.g. --model gpt-5.6-luna")
    return slug


def entry_name(entry: dict) -> str:
    return f"{model_slug(entry['model']['name'])}-{entry['task']}-s{int(entry.get('seed') or 0)}.json"


def _json_default(v: Any) -> Any:
    if hasattr(v, "item"):
        return v.item()
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return str(v)


def analyze(folder: str | Path) -> dict[str, Any] | None:
    """Where a run's return came from and how good its decisions were, from ``research.analysis``: the summary
    (exposure versus alpha, decision excess over SPY and hit rate, information coefficients, news-triggered share),
    the findings, and the profit and loss by sector, holding period and trigger. None without that module."""
    try:
        from .analysis import analyze_run, compact
    except ImportError:
        return None
    try:
        out = compact(analyze_run(Path(folder), save=False, chart=False))
    except Exception as exc:  # noqa: BLE001 - an analysis error must not block a submission
        return {"error": f"{type(exc).__name__}: {exc}"[:300]}
    for key in ("folder", "files", "notes"):  # local paths stay on the submitter's machine
        out.pop(key, None)
    return out


def rescore(folder: str | Path) -> Path:
    """Replay a saved run's replies under the benchmark settings and save the replayed record in a new folder. For
    runs made with other fill costs: the model's orders stay the same, their fills pay the benchmark's costs."""
    folder = Path(folder).expanduser()
    man, log = _read(folder, "manifest.json"), _read(folder, "policy_log.json")
    period = str(man["experiment"]["period"])
    task = get_task(f"{period}-{'blind' if log.get('blind') else 'named'}")
    label = (man.get("strategy") or {}).get("name") or man["experiment"].get("label") or "policy"
    res = run_task(_Replay(log["replies"]), task, label=label, save=True, check=True,
                   seed=int(man["experiment"].get("seed") or 0))
    new = Path(res.folder)
    meta = _read(folder, "bench.json") or {}
    meta.update({"bench": BENCH_VERSION, "task": task.name, "seed": int(man["experiment"].get("seed") or 0),
                 "settings": SETTINGS, "rescored_from": man.get("run_id"),
                 "costs_seen_by_model": man["experiment"].get("costs", "fixed")})
    (new / "bench.json").write_text(json.dumps(meta, indent=1, default=str) + "\n", encoding="utf-8")
    if (folder / "usage.json").is_file():
        (new / "usage.json").write_bytes((folder / "usage.json").read_bytes())
    return new


def make_entry(folder: str | Path, model: str, cutoff: str, provider: str | None = None, access: str | None = None,
               open_weights: bool | None = None, parameters: str | None = None, submitter: str | None = None,
               notes: str | None = None, memory: dict | None = None, cost_usd: float | None = None,
               rescore_costs: bool = True) -> dict[str, Any]:
    """The leaderboard entry of a saved run, after checking that the run is a benchmark task (``BenchError``
    otherwise). ``cutoff`` is the model's training cutoff (YYYY-MM). A run whose only difference from the benchmark
    is its cost model is replayed under the benchmark's costs first (``rescore``)."""
    folder = Path(folder).expanduser()
    cutoff_day(cutoff)  # raises on a malformed cutoff
    task, problems = check_record(folder)
    if task is not None and problems and rescore_costs and costs_supported() and all(
            p.startswith("costs ") for p in problems):
        folder = rescore(folder)
        task, problems = check_record(folder)
    if task is None or problems:
        raise BenchError(problems)
    man, met, log = _read(folder, "manifest.json"), _read(folder, "metrics.json"), _read(folder, "policy_log.json")
    usage, meta = _read(folder, "usage.json"), _read(folder, "bench.json") or {}
    eq = _equity(folder)
    status = contamination(task.period, cutoff)
    after = None
    if status in ("clean", "partly"):
        after = window_metrics(eq, task.period, cutoff_day(cutoff) if status == "partly" else None)
    strat, stats = met.get("strategy") or {}, met.get("vs_spy") or {}
    if usage is not None and cost_usd is not None:
        usage = {**usage, "cost_usd": cost_usd}
    call = meta.get("model_call") or {}
    entry = {
        "bench": BENCH_VERSION,
        "task": task.name,
        "seed": int(man["experiment"].get("seed") or 0),
        "model": {"name": model, "provider": provider, "access": access or call.get("provider"),
                  "training_cutoff": cutoff, "open_weights": open_weights, "parameters": parameters,
                  "call": call or None},
        "contamination": status,
        "settings": {**SETTINGS, "period": task.period, "blind": task.blind, "start": man["experiment"]["start"],
                     "end": man["experiment"]["end"]},
        "data": {"version": man["dataset"].get("version"), "prices_sha256": man["dataset"]["prices"]["sha256"],
                 "news_sha256": man["dataset"]["news"]["sha256"]},
        "record": {"run_id": man.get("run_id"), "riskaware": man.get("riskaware"), "created_utc": man.get("created_utc"),
                   "files_sha256": {n: hashlib.sha256((folder / n).read_bytes()).hexdigest()
                                    for n in RECORD_FILES if (folder / n).is_file()},
                   "replies_sha256": _sha(public_replies(log["replies"])),
                   "observations_sha256": _sha(log.get("observations_sha256") or []),
                   "costs_seen_by_model": meta.get("costs_seen_by_model", SETTINGS.get("costs")),
                   "rescored_from": meta.get("rescored_from")},
        "look_ahead_check": (man.get("leak_check") or {}).get("verdict"),
        "metrics": {k: _num(strat.get(k)) for k in ENTRY_METRICS if k in strat},
        "vs_spy": {k: _num(stats.get(k)) for k in ENTRY_STATS if k in stats},
        "anchors": {"spy": {k: _num((met.get("spy") or {}).get(k)) for k in ("total_return", "sharpe", "max_drawdown")},
                    "equal_weight": {k: _num((met.get("equal_weight") or {}).get(k))
                                     for k in ("total_return", "sharpe", "max_drawdown")}},
        "after_cutoff": after,
        "analysis": analyze(folder),
        "memory": memory,
        "usage": usage,
        "steps": len(log["replies"]),
        "submitted": date.today().isoformat(),
        "submitter": submitter,
        "notes": notes,
        "replies": public_replies(log["replies"]),
    }
    m = entry["metrics"]
    if m.get("total_return") is not None and entry["anchors"]["equal_weight"].get("total_return") is not None:
        m["excess_vs_equal_weight"] = _num(m["total_return"] - entry["anchors"]["equal_weight"]["total_return"])
    if m.get("total_return") is not None and entry["anchors"]["spy"].get("total_return") is not None:
        m["excess_vs_spy"] = _num(m["total_return"] - entry["anchors"]["spy"]["total_return"])
    return entry


def write_entry(entry: dict, directory: str | Path, overwrite: bool = False) -> Path:
    directory = Path(directory).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / entry_name(entry)
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} exists; pass overwrite=True (--force) to replace it")
    clean = {k: v for k, v in entry.items() if not k.startswith("_")}  # load_entries adds _file
    path.write_text(json.dumps(clean, indent=1, ensure_ascii=False, default=_json_default) + "\n", encoding="utf-8")
    return path


def check_entry(entry: dict) -> list[str]:
    """Problems with an entry's form, without replaying it (fast; ``verify_entry`` replays)."""
    problems: list[str] = []
    for key in ("bench", "task", "model", "settings", "data", "record", "metrics", "replies"):
        if key not in entry:
            problems.append(f"missing {key}")
    if problems:
        return problems
    if entry["bench"] != BENCH_VERSION:
        problems.append(f"bench is {entry['bench']!r}, this package scores {BENCH_VERSION}")
    try:
        task = get_task(entry["task"])
    except ValueError as exc:
        return [*problems, str(exc)]
    s = entry["settings"]
    if not isinstance(entry.get("seed", 0), int) or entry.get("seed", 0) < 0:
        problems.append("seed must be a whole number")
    for k, v in SETTINGS.items():
        if s.get(k) != v:
            problems.append(f"settings.{k} is {s.get(k)!r}, the benchmark uses {v!r}")
    if s.get("period") != task.period or bool(s.get("blind")) != task.blind:
        problems.append("settings.period / settings.blind do not match the task")
    now = dataset_info(task.period)
    if entry["data"].get("prices_sha256") != now["prices"]["sha256"]:
        problems.append("data.prices_sha256 is not the bundled price file")
    if entry["data"].get("news_sha256") != now["news"]["manifest_sha256"]:
        problems.append("data.news_sha256 is not the period's headline file (SHA-256 in the manifest)")
    if public_replies(entry["replies"]) != entry["replies"]:
        problems.append("the replies carry the model's text (an order's reason or a reply's note); an entry publishes "
                        "the orders only (scripts/strip_entry_text.py)")
    if _sha(entry["replies"]) != entry["record"].get("replies_sha256"):
        problems.append("the replies do not match record.replies_sha256")
    if entry.get("look_ahead_check") != "pass":
        problems.append("look_ahead_check is not 'pass'")
    cutoff = (entry["model"] or {}).get("training_cutoff")
    try:
        want = contamination(task.period, cutoff)
    except ValueError as exc:
        problems.append(str(exc))
    else:
        if entry.get("contamination") != want:
            problems.append(f"contamination is {entry.get('contamination')!r}; with cutoff {cutoff} it is {want!r}")
    return problems


def replay(entry: dict, check: bool = True) -> ExperimentResult:
    """Run an entry's task again with its recorded replies: the same orders on the same days, no model call."""
    task = get_task(entry["task"])
    return run_task(_Replay(entry["replies"]), task, label=f"replay:{entry['model']['name']}", save=False,
                    check=check, seed=int(entry.get("seed") or 0))


def verify_entry(entry: dict, check: bool = False, tolerance: float = 1e-4) -> dict[str, Any]:
    """Check an entry's form, replay its replies and compare the replayed metrics with the entry's. ``check`` also
    reruns the look-ahead check of the observations (four more backtests)."""
    problems = check_entry(entry)
    if any(p.startswith("missing") for p in problems):
        return {"ok": False, "problems": problems}
    res = replay(entry, check=check)
    got = res.metrics
    for k in VERIFY_KEYS:
        a, b = _num(got.get(k)), (entry.get("metrics") or {}).get(k)
        if a is None and b is None:
            continue
        if a is None or b is None or not math.isclose(a, b, rel_tol=tolerance, abs_tol=tolerance):
            problems.append(f"{k}: the entry says {b}, the replay gives {a}")
    if check and (res.leak or {}).get("verdict") != "pass":
        problems.append(f"replayed look-ahead check: {(res.leak or {}).get('verdict')}")
    return {"ok": not problems, "problems": problems,
            "replayed": {k: _num(got.get(k)) for k in VERIFY_KEYS}}


def load_entries(directory: str | Path) -> list[dict]:
    out = []
    for path in sorted(Path(directory).expanduser().glob("*.json")):
        try:
            e = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(e, dict) and e.get("bench") == BENCH_VERSION:
            e["_file"] = path.name
            out.append(e)
    return out


def default_dir() -> Path | None:
    """``leaderboard`` in the current folder, else in the source checkout this package runs from."""
    for root in (Path.cwd(), Path(__file__).resolve().parents[3]):
        if (root / "leaderboard" / "entries").is_dir():
            return root / "leaderboard"
    return None


# ----------------------------------------------------------------------------------- anchors and the board
def anchor_rows(periods: tuple[str, ...] = BENCH_PERIODS, leak_check: bool = True,
                progress: Callable[[str], None] | None = None) -> list[dict[str, Any]]:
    """The five rule strategies (with news) on each period under the benchmark settings, plus SPY and the
    equal-weight universe: the rows the board compares the models with. The rules decide after every close."""
    from .strategies import RESEARCH_STRATEGIES

    rows: list[dict[str, Any]] = []
    for period in periods:
        need_news(period)
    for period in periods:
        last = None
        for name in RESEARCH_STRATEGIES:
            if progress:
                progress(f"{name} {period}")
            exp = experiment(f"{period}-named", label=name)
            exp.strategy, exp.params, exp.leak_check = name, {"news": True}, leak_check
            res = run_experiment(exp, save=False, chart=False)
            rows.append(_anchor(period, name, "rule", res.metrics, res.equity, (res.leak or {}).get("verdict")))
            last = res
        if last is not None:
            for col, label in (("spy", "SPY"), ("equal_weight", "Equal weight")):
                from ..backtest import metrics as M

                m = M.summarize(last.equity[col].astype(float), _rf(period))
                rows.append(_anchor(period, label, "buy and hold", m, None, None))
    return rows


def _anchor(period: str, name: str, kind: str, m: dict, equity: pd.DataFrame | None, leak: str | None) -> dict:
    row = {"period": period, "name": name, "kind": kind, "leak_check": leak,
           **{k: _num(m.get(k)) for k in ("total_return", "sharpe", "max_drawdown", "volatility", "avg_exposure",
                                          "avg_holding_days", "round_trips", "turnover")}}
    if equity is not None:
        row["excess_vs_equal_weight"] = _num(m.get("total_return") - (equity["equal_weight"].iloc[-1] /
                                                                     equity["equal_weight"].iloc[0] - 1))
    return row


RUN_KEYS = ("total_return", "sharpe", "max_drawdown", "avg_exposure", "avg_holding_days", "turnover", "round_trips",
            "excess_vs_equal_weight", "excess_vs_spy")
AFTER_KEYS = ("total_return", "sharpe", "max_drawdown", "volatility", "spy_total_return", "spy_sharpe",
              "equal_weight_total_return", "equal_weight_sharpe", "excess_vs_spy", "excess_vs_equal_weight")


def _mean(values: Any) -> float | None:
    v = [float(x) for x in values if isinstance(x, (int, float)) and not isinstance(x, bool)]
    return _num(sum(v) / len(v)) if v else None


def _range(values: Any) -> list[float] | None:
    v = [float(x) for x in values if isinstance(x, (int, float)) and not isinstance(x, bool)]
    return [_num(min(v)), _num(max(v))] if len(v) > 1 else None


def _groups(rows_per_run: list[list[dict] | None], n: int) -> list[dict]:
    """Mean contribution per group (sector, trigger) over the runs that list it."""
    acc: dict[str, list[float]] = {}
    for rows in rows_per_run:
        for r in rows or []:
            if r.get("contribution") is not None:
                acc.setdefault(str(r.get("group")), []).append(float(r["contribution"]))
    out = [{"group": g, "contribution": _num(sum(v) / len(v)), "runs": len(v)} for g, v in acc.items()]
    return sorted(out, key=lambda r: -(r["contribution"] or 0.0))


def _analysis(items: list[Any]) -> dict | None:
    """The run analyses of one model and task, averaged over its seeds."""
    items = [a for a in items if isinstance(a, dict) and isinstance(a.get("summary"), dict)]
    if not items:
        return None
    summary: dict[str, Any] = {}
    for k in items[0]["summary"]:
        vals = [a["summary"].get(k) for a in items]
        nums = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
        summary[k] = _mean(nums) if nums else (any(bool(v) for v in vals) if k == "concentrated" else vals[0])
    pnl = [a.get("pnl") or {} for a in items]
    return {"runs": len(items), "summary": summary,
            "by_sector": _groups([p.get("by_sector") for p in pnl], len(items)),
            "by_trigger": _groups([(p.get("by_trigger") or {}).get("entry") for p in pnl], len(items)),
            "findings": items[0].get("findings") if len(items) == 1 else None}


def _aggregate(runs: list[dict]) -> dict[str, Any]:
    """One model on one task: the mean over its seeds, with the range of the return and the Sharpe ratio."""
    runs = sorted(runs, key=lambda e: int(e.get("seed") or 0))
    met = [e.get("metrics") or {} for e in runs]
    out: dict[str, Any] = {k: _mean(m.get(k) for m in met) for k in RUN_KEYS}
    out.update({"seeds": [int(e.get("seed") or 0) for e in runs], "n": len(runs),
                "total_return_range": _range(m.get("total_return") for m in met),
                "sharpe_range": _range(m.get("sharpe") for m in met),
                "beta": _mean((e.get("vs_spy") or {}).get("beta") for e in runs),
                "alpha_annual": _mean((e.get("vs_spy") or {}).get("alpha_annual") for e in runs),
                "analysis": _analysis([e.get("analysis") for e in runs]),
                "files": [e.get("_file") for e in runs], "steps": runs[0].get("steps"),
                "run_dates": sorted({str((e.get("record") or {}).get("created_utc") or "")[:10] for e in runs} - {""})})
    acs = [e["after_cutoff"] for e in runs if e.get("after_cutoff")]
    out["after_cutoff"] = None if not acs else {
        "from": acs[0].get("from"), "to": acs[0].get("to"), "days": acs[0].get("days"),
        **{k: _mean(a.get(k) for a in acs) for k in AFTER_KEYS},
        "total_return_range": _range(a.get("total_return") for a in acs),
        "sharpe_range": _range(a.get("sharpe") for a in acs)}
    usage = [e.get("usage") or {} for e in runs]
    out["usage"] = {k: _num(sum(float(u.get(k) or 0) for u in usage), 4)
                    for k in ("calls", "input_tokens", "output_tokens", "list_price_usd") if any(k in u for u in usage)}
    return out


def board(entries: list[dict], anchors: list[dict] | None = None) -> dict[str, Any]:
    """The leaderboard: per period, one row per model with its blind and named scores (the mean over its seeds),
    clean and may-remember apart, and the anchors of that period. Clean rows are sorted by the Sharpe ratio after
    the cutoff, the others by the whole window's, both of the blind task."""
    by: dict[tuple[str, str], dict[str, list[dict]]] = {}
    models: dict[str, dict] = {}
    memory: dict[tuple[str, str], Any] = {}
    for e in entries:
        t = get_task(e["task"])
        key = model_slug(e["model"]["name"])
        models.setdefault(key, e["model"])
        by.setdefault((key, t.period), {}).setdefault(t.mode, []).append(e)
        if e.get("memory") is not None:
            memory.setdefault((key, t.period), e["memory"])
    periods: dict[str, Any] = {}
    for period in BENCH_PERIODS:
        rows = []
        for (key, p), runs in by.items():
            if p != period:
                continue
            model = models[key]
            row: dict[str, Any] = {"model": model["name"], "slug": key, "provider": model.get("provider"),
                                   "open_weights": model.get("open_weights"), "cutoff": model.get("training_cutoff"),
                                   "status": contamination(period, model.get("training_cutoff")),
                                   "memory": memory.get((key, period))}
            for mode in MODES:
                if runs.get(mode):
                    row[mode] = _aggregate(runs[mode])
            if "blind" in row and "named" in row:
                b, n = row["blind"].get("total_return"), row["named"].get("total_return")
                row["named_minus_blind"] = _num(n - b) if b is not None and n is not None else None
                ab, an = row["blind"].get("after_cutoff") or {}, row["named"].get("after_cutoff") or {}
                if ab.get("total_return") is not None and an.get("total_return") is not None:
                    row["named_minus_blind_after_cutoff"] = _num(an["total_return"] - ab["total_return"])
            rows.append(row)

        def score(r: dict) -> float:
            run = r.get("blind") or r.get("named") or {}
            v = (run.get("after_cutoff") or {}).get("sharpe") if r["status"] in ("clean", "partly") else None
            v = run.get("sharpe") if v is None else v
            return -1e9 if v is None else float(v)

        rows.sort(key=score, reverse=True)
        periods[period] = {
            "window": {"start": get_period(period).start.isoformat(), "end": get_period(period).end.isoformat()},
            "title": get_period(period).title,
            "clean": [r for r in rows if r["status"] in ("clean", "partly")],
            "may_remember": [r for r in rows if r["status"] not in ("clean", "partly")],
            "anchors": [a for a in (anchors or []) if a["period"] == period],
        }
    return {"bench": BENCH_VERSION, "generated_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "settings": SETTINGS, "seeds": list(SEEDS), "periods": periods, "models": len(models),
            "entries": len(entries)}


__all__ = ["BENCH_PERIODS", "BENCH_VERSION", "MODES", "SEEDS", "SETTINGS", "TASKS", "BenchError", "Task", "anchor_rows",
           "analyze", "bench_policy", "board", "check_entry", "check_record", "contamination", "costs_supported",
           "cutoff_day", "describe", "experiment", "get_task", "load_entries", "make_entry", "replay", "rescore",
           "need_news", "public_replies", "run_task", "verify_entry", "window", "window_metrics", "write_entry"]
