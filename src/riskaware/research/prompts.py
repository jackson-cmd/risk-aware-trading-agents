"""Prompt tests: which system prompt makes an LLM trading agent trade better, with evidence that survives the noise.

A *variant* is a system prompt plus the few settings a prompt study varies with it: how many headlines per stock the
agent sees, how often it decides, whether its reply starts with a short reasoning field, and how strictly the reply
format is enforced. The baseline is the package's own ``agent.SYSTEM_PROMPT`` with the defaults of ``llm_policy``;
every other variant changes exactly one factor, so a difference can be traced to that factor:

* role: ``role_pm``, ``role_quant``, ``role_risk`` put a one-sentence persona in front;
* ``risk_rules``: explicit rules (position cap, stop-loss, cash floor, no buys on red flags);
* ``reason_first``: a short reasoning field before the orders;
* focus: ``focus_news`` and ``focus_trend`` say which evidence to weigh most;
* ``few_shot``: three made-up examples of an observation and a good reply;
* format: ``format_json`` (any JSON object, the schema only in the prompt) and ``format_text`` (nothing enforced);
* headlines: ``news_0`` and ``news_6`` show 0 or 6 headlines per stock instead of 3;
* frequency: ``daily`` decides every trading day instead of every fifth.

``run_lab`` trades every variant on the same frozen periods with the same model, blind, for several seeds (a seed
fixes the blind aliases and, where the API takes one, the sampling seed), and writes one record per run. Each
record's ``manifest.json`` names the variant and carries the SHA-256 of its prompt text and of the whole variant;
``prompt.txt`` holds the text and ``calls.jsonl`` every model call (status, tokens, seconds).

``report`` compares every variant with the baseline on the same period and seed (paired): the mean difference, a
bootstrap interval, an exact sign-flip test and a Holm correction for the number of variants, so that it says plainly
which differences are larger than what two seeds of the same prompt already differ by. It writes ``prompts.json``,
``prompts.md`` and ``prompts.png`` into the lab folder and, on request, the website's data file.

Docs: docs/prompts.md. CLI: ``riskaware research prompts list | run | report``.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from ..config import RISKAWARE_HOME
from .agent import ORDER_SCHEMA, SYSTEM_PROMPT
from .periods import get_period

LABS_DIR = RISKAWARE_HOME / "research" / "prompts"
SET_VERSION = "1"  # the version of the variant set below: a changed text is a new version, never an edit
USER_PREFIX = "Today's observation (JSON):\n"  # what llm_policy sends before the observation
FORMATS = ("schema", "json", "text")
METRICS = ("sharpe", "total_return", "max_drawdown", "excess_vs_spy", "excess_vs_equal_weight", "volatility",
           "turnover", "avg_exposure", "format_error_rate", "invalid_order_rate", "orders_per_step")
PRIMARY = "sharpe"
HIGHER_IS_BETTER = {"sharpe": True, "total_return": True, "max_drawdown": True, "excess_vs_spy": True,
                    "excess_vs_equal_weight": True, "volatility": False, "format_error_rate": False,
                    "invalid_order_rate": False}

REASONED_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"reasoning": {"type": "string"}, **ORDER_SCHEMA["properties"]},
    "required": ["reasoning", *ORDER_SCHEMA["required"]],
    "additionalProperties": False,
}


# ----------------------------------------------------------------------------------- variants
@dataclass(frozen=True)
class Variant:
    """One system prompt and the settings that go with it (see the module docstring)."""

    name: str
    factor: str  # what it changes from the baseline: baseline, role, risk_rules, reasoning, focus, few_shot, ...
    about: str
    about_zh: str
    text: str
    max_news: int = 3
    decide_every: int = 5
    reasoning: bool = False  # the reply starts with a "reasoning" field (REASONED_SCHEMA)
    response_format: str = "schema"  # schema: strict JSON schema; json: any JSON object; text: nothing enforced

    @property
    def schema(self) -> dict[str, Any]:
        return REASONED_SCHEMA if self.reasoning else ORDER_SCHEMA

    @property
    def prompt_sha256(self) -> str:
        """SHA-256 of the system prompt text alone."""
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    def settings(self) -> dict[str, Any]:
        return {"max_news": self.max_news, "decide_every": self.decide_every, "reasoning": self.reasoning,
                "response_format": self.response_format}

    @property
    def sha256(self) -> str:
        """SHA-256 of everything that reaches the model or decides when it is asked: text, schema and settings."""
        blob = json.dumps({"text": self.text, "schema": self.schema, **self.settings()}, sort_keys=True,
                          separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def describe(self, text: bool = False) -> dict[str, Any]:
        out = {"name": self.name, "factor": self.factor, "about": self.about, "about_zh": self.about_zh,
               **self.settings(), "prompt_sha256": self.prompt_sha256, "variant_sha256": self.sha256,
               "set_version": SET_VERSION}
        if text:
            out["text"] = self.text
        return out


ROLES = {
    "role_pm": ("You are an experienced portfolio manager at a long-only equity fund.",
                "a portfolio manager at a long-only equity fund", "长线股票基金的基金经理"),
    "role_quant": ("You are a quantitative researcher at a systematic hedge fund.",
                   "a quantitative researcher at a systematic hedge fund", "系统化对冲基金的量化研究员"),
    "role_risk": ("You are the risk manager of an investment firm.",
                  "the risk manager of an investment firm", "投资公司的风控经理"),
}

RISK_RULES = (
    "Risk rules, before anything else: no stock above 20% of equity (weight 0.2); sell a position in full when its "
    "gain since you bought it is -8% or worse (gain -0.08 or lower); keep at least 20% of equity in cash, and at "
    "least 50% when the market regime is RISK_OFF or VIX is above 30; do not buy a stock with a red flag in its news.")

REASON_FIRST = (
    'Before the orders, write your reasoning in the "reasoning" field: three to five short sentences on the market, '
    "your positions and the stocks that matter most today. Then decide.")

FOCUS_NEWS = (
    "Decide mainly from the news: what each stock's headlines say, whether independent sources confirm them "
    "(status, confirmed, single_source), how recent they are (hours_before_close), the news score and any red flags. "
    "Use prices and indicators mainly to time entries and exits.")

FOCUS_TREND = (
    "Decide mainly from prices and trends: the returns over 5 and 21 days (ret_5d, ret_21d), the distance from the "
    "20-, 50- and 200-day averages (vs_ema20, vs_ema50, vs_ema200), RSI (rsi14, rsi2) and the distance from the "
    "52-week high (off_52w_high). Use the news mainly to avoid stocks with red flags.")

FEW_SHOT = (
    "Three examples with made-up stocks and numbers (not from this market); observations are shortened.\n"
    'Observation: {"market":{"regime":"RISK_ON","vix":15.2},"portfolio":{"cash_share":0.6,"positions":[{"symbol":'
    '"S87","weight":0.2,"gain":0.04,"days_held":10}],"max_positions":5,"default_weight":0.2},"stocks":[{"symbol":'
    '"S81","ret_5d":0.03,"ret_21d":0.09,"vs_ema50":0.06,"rsi14":61,"news":{"score":0.6,"confirmed":2,"red_flags":[],'
    '"headlines":[{"headline":"S81 raises full-year outlook","status":"confirmed","hours_before_close":5}]}}]}\n'
    'Reply: {"orders":[{"symbol":"S81","action":"buy","weight":0.2,"fraction":0,"reason":"uptrend and a confirmed '
    'raised outlook"}],"note":"one new position; keep cash for later"}\n'
    'Observation: {"market":{"regime":"NEUTRAL","vix":21.4},"portfolio":{"cash_share":0.2,"positions":[{"symbol":'
    '"S92","weight":0.2,"gain":-0.05,"days_held":6}]},"stocks":[{"symbol":"S92","ret_5d":-0.07,"vs_ema50":-0.03,'
    '"news":{"score":-0.7,"confirmed":2,"red_flags":["investigation"],"headlines":[{"headline":"Regulator opens probe '
    'into S92 sales practices","status":"confirmed","hours_before_close":3}]}}]}\n'
    'Reply: {"orders":[{"symbol":"S92","action":"sell","weight":0,"fraction":1,"reason":"confirmed probe and a falling '
    'price"}],"note":"exit on a red flag"}\n'
    'Observation: {"market":{"regime":"NEUTRAL","vix":17.8},"portfolio":{"cash_share":0.4,"positions":[{"symbol":'
    '"S95","weight":0.3,"gain":0.02,"days_held":15},{"symbol":"S97","weight":0.3,"gain":0.01,"days_held":15}]},'
    '"stocks":[{"symbol":"S95","ret_5d":0.01,"vs_ema50":0.02,"news":{"score":0.1,"confirmed":0,"red_flags":[],'
    '"headlines":[]}}]}\n'
    'Reply: {"orders":[],"note":"nothing new; hold"}')


def _v(name: str, factor: str, about: str, about_zh: str, text: str = SYSTEM_PROMPT, **kw: Any) -> Variant:
    return Variant(name, factor, about, about_zh, text, **kw)


VARIANTS: dict[str, Variant] = {v.name: v for v in [
    _v("baseline", "baseline", "The package's own prompt (agent.SYSTEM_PROMPT): 3 headlines per stock, a decision "
       "every 5 trading days, the reply held to a strict JSON schema.",
       "包里自带的 prompt（agent.SYSTEM_PROMPT）：每只股票 3 条新闻，每 5 个交易日决策一次，回复受严格 JSON schema 约束。"),
    *[_v(k, "role", f"A persona in front: {en}.", f"开头加角色：{zh}。", f"{sent} {SYSTEM_PROMPT}")
      for k, (sent, en, zh) in ROLES.items()],
    _v("risk_rules", "risk_rules", "Explicit risk rules: 20% cap per stock, sell at -8%, at least 20% cash (50% when "
       "risk-off or VIX above 30), no buys on red flags.",
       "写明风险规则：单只不超过 20%，亏 8% 卖出，至少留 20% 现金（risk-off 或 VIX 高于 30 时 50%），有红旗不买。",
       f"{SYSTEM_PROMPT} {RISK_RULES}"),
    _v("reason_first", "reasoning", "A short reasoning field (3 to 5 sentences) before the orders.",
       "先写 3 到 5 句简短推理，再下单。", f"{SYSTEM_PROMPT} {REASON_FIRST}", reasoning=True),
    _v("focus_news", "focus", "Weigh the news most: headlines, confirmation, recency, red flags.",
       "以新闻为主：标题内容、是否有多家确认、多新、红旗。", f"{SYSTEM_PROMPT} {FOCUS_NEWS}"),
    _v("focus_trend", "focus", "Weigh prices and trends most: returns, moving averages, RSI, distance from the high.",
       "以价格和趋势为主：涨跌幅、均线、RSI、离高点多远。", f"{SYSTEM_PROMPT} {FOCUS_TREND}"),
    _v("few_shot", "few_shot", "Three made-up examples of an observation and a reply (a buy, a sell on a red flag, a "
       "hold).", "三个编造的示例：一次买入、一次因红旗卖出、一次持有。", f"{SYSTEM_PROMPT}\n\n{FEW_SHOT}"),
    _v("format_json", "format", "Any JSON object is accepted (JSON mode); the schema is only described in the prompt.",
       "只要求是 JSON 对象（JSON mode），schema 只写在 prompt 里，不强制。", response_format="json"),
    _v("format_text", "format", "Nothing enforced: no JSON mode and no schema; the reply format is only the prompt's "
       "own sentence.", "完全不强制：不开 JSON mode、不给 schema，只靠 prompt 里那句格式说明。", response_format="text"),
    _v("news_0", "news_count", "No headlines (the news score, confirmation counts and red flags stay).",
       "不给新闻标题（新闻分数、确认数和红旗还在）。", max_news=0),
    _v("news_6", "news_count", "Six headlines per stock instead of three.", "每只股票 6 条新闻，而不是 3 条。",
       max_news=6),
    _v("daily", "frequency", "A decision every trading day instead of every fifth.", "每个交易日都决策，而不是每 5 天一次。",
       decide_every=1),
]}
BASELINE = "baseline"
FACTORS = {"role": "Role / 角色", "risk_rules": "Risk rules / 风险规则", "reasoning": "Reasoning first / 先推理",
           "focus": "Evidence to weigh / 侧重", "few_shot": "Examples / 示例", "format": "Reply format / 输出格式",
           "news_count": "Headlines per stock / 新闻条数", "frequency": "Decision frequency / 决策频率"}


def get_variant(name: str) -> Variant:
    if name not in VARIANTS:
        raise ValueError(f"unknown prompt variant {name!r}; the variants are {', '.join(VARIANTS)}")
    return VARIANTS[name]


def select(names: str | Iterable[str] | None) -> list[Variant]:
    """Variants by name: None or "all" for every one, else a comma-separated string or a list."""
    if names is None or names == "all":
        return list(VARIANTS.values())
    items = [n.strip() for n in names.split(",")] if isinstance(names, str) else list(names)
    return [get_variant(n) for n in items if n]


def from_file(path: str | Path, name: str | None = None, **settings: Any) -> Variant:
    """Your own system prompt from a text file, with the baseline's settings unless ``settings`` change them."""
    p = Path(path).expanduser()
    text = p.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"{p} is empty")
    nm = name or "".join(c if c.isalnum() or c in "-_" else "_" for c in p.stem)[:40] or "custom"
    if nm in VARIANTS:
        raise ValueError(f"{nm!r} is the name of a built-in variant; pick another with --name")
    fmt = settings.get("response_format", "schema")
    if fmt not in FORMATS:
        raise ValueError(f"response_format must be one of {', '.join(FORMATS)}")
    return Variant(nm, "custom", f"Your prompt from {p.name}.", f"你自己的 prompt（{p.name}）。", text, **settings)


# ----------------------------------------------------------------------------------- the model call
def tune(provider: Any, variant: Variant, seed: int | None = None) -> Any:
    """Make an OpenAI-compatible provider send what the variant asks for: the sampling seed, and for the json and
    text formats a looser response format (text: no schema hint in the system prompt either). Providers without a
    request body of their own (the -cli and Anthropic ones) are returned unchanged."""
    if isinstance(provider, OllamaChat):
        provider.seed, provider.response_format = seed, variant.response_format
        return provider
    build = getattr(provider, "request_body", None)
    if build is None or getattr(provider, "_tuned_for", None) == (variant.sha256, seed):
        return provider
    build = getattr(provider, "_plain_request_body", build)

    def request_body(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        body = build(system, user, schema)
        if seed is not None:
            body["seed"] = int(seed)
        if variant.response_format == "json":
            body["response_format"] = {"type": "json_object"}
        elif variant.response_format == "text":
            body.pop("response_format", None)
            body["messages"][0]["content"] = system
        return body

    provider._plain_request_body = build
    provider.request_body = request_body
    provider._tuned_for = (variant.sha256, seed)
    return provider


class OllamaChat:
    """A local Ollama model through its own /api/chat endpoint, with a context window set per request.

    Ollama's OpenAI-compatible endpoint (what ``ollama:`` uses elsewhere in the package) keeps the server's default
    context, 4,096 tokens on a stock install, and silently drops the start of a longer prompt; a few-shot prompt or six
    headlines a stock would then lose part of the observation. Here ``num_ctx`` is sent with every request
    (16,384 by default: two labs with different values on one server would make it reload the model each time)."""

    def __init__(self, model: str, base_url: str | None = None, temperature: float | None = 0.2,
                 max_tokens: int = 1200, num_ctx: int = 16384, timeout: float = 600.0):
        import requests

        self.model, self.temperature, self.max_tokens, self.num_ctx = model, temperature, max_tokens, num_ctx
        root = (base_url or "http://localhost:11434").rstrip("/")
        self.url = (root[:-3] if root.endswith("/v1") else root) + "/api/chat"
        self.timeout, self.session = timeout, requests.Session()
        self.seed: int | None = None
        self.response_format = "schema"
        self.last_raw: str | None = None
        self.last_error: str | None = None
        self.last_usage: dict[str, Any] = {}

    def body(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        options: dict[str, Any] = {"num_ctx": self.num_ctx, "num_predict": self.max_tokens}
        if self.temperature is not None:
            options["temperature"] = self.temperature
        if self.seed is not None:
            options["seed"] = self.seed
        sys_text = system
        if self.response_format != "text":
            sys_text += "\n\nReply with one JSON object that matches this JSON schema:\n" + json.dumps(schema)
        out = {"model": self.model, "stream": False, "options": options,
               "messages": [{"role": "system", "content": sys_text}, {"role": "user", "content": user}]}
        if self.response_format == "schema":
            out["format"] = schema
        elif self.response_format == "json":
            out["format"] = "json"
        return out

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any] | None:
        import requests

        from ..llm.openai_compat import parse_json_object

        self.last_raw, self.last_error, self.last_usage = None, None, {}
        try:
            r = self.session.post(self.url, json=self.body(system, user, schema), timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
        except requests.HTTPError as exc:
            self.last_error = f"HTTP {exc.response.status_code}: {exc.response.text[:300]}"
            return None
        except (requests.RequestException, ValueError) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None
        self.last_usage = {"prompt_tokens": data.get("prompt_eval_count"), "completion_tokens": data.get("eval_count")}
        self.last_raw = (data.get("message") or {}).get("content") or ""
        parsed = parse_json_object(self.last_raw)
        if parsed is None:
            self.last_error = "the reply was not a JSON object"
        return parsed


def make_provider_for(llm: str, temperature: float | None = None, base_url: str | None = None,
                      max_tokens: int | None = None) -> Any:
    """A provider for ``provider:model`` (see ``riskaware.llm``) with the lab's temperature and reply cap; local
    ``ollama:`` models go through ``OllamaChat`` so that long prompts are not cut."""
    from ..llm import llm_config, make_provider, parse_choice

    kind, model = parse_choice(llm)
    if kind == "ollama":
        return OllamaChat(model, base_url=base_url, temperature=temperature, max_tokens=max_tokens or 1200)
    cfg = llm_config(llm, base_url=base_url)
    if temperature is not None:
        cfg.temperature = float(temperature)
    if max_tokens is not None:
        cfg.max_tokens = int(max_tokens)
    return make_provider(cfg)


class VariantPolicy:
    """Asks the model with a variant's prompt each step and keeps a record of every call. A call that fails on the
    way (a rate limit, a timeout, a server error) is tried again after a pause, up to ``retries`` times, so that an
    outage does not show up as a decision to hold."""

    def __init__(self, variant: Variant, provider: Any, retries: int = 4, backoff: float = 10.0):
        self.variant, self.provider = variant, provider
        self.retries, self.backoff = retries, backoff
        self.calls: list[dict[str, Any]] = []

    def _ask(self, user: str) -> tuple[Any, str | None]:
        try:
            reply = self.provider.complete_json(self.variant.text, user, self.variant.schema)
        except Exception as exc:  # noqa: BLE001 - a failed call holds, as in llm_policy; the record says why
            return None, f"{type(exc).__name__}: {exc}"
        return reply, getattr(self.provider, "last_error", None) if reply is None else None

    def __call__(self, obs: dict) -> dict:
        user = USER_PREFIX + json.dumps(obs, separators=(",", ":"), default=str)
        t0 = time.time()
        tries = 0
        while True:
            reply, error = self._ask(user)
            status = classify(reply, error)
            if status != "transport" or tries >= self.retries:
                break
            tries += 1
            time.sleep(self.backoff * 2 ** (tries - 1))
        usage = dict(getattr(self.provider, "last_usage", None) or {})
        rec = {"step": obs.get("step"), "day": obs.get("day"), "status": status, "seconds": round(time.time() - t0, 2),
               "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
               "retries": tries}
        if status != "ok":
            rec["error"] = (error or "")[:300]
            raw = getattr(self.provider, "last_raw", None)
            if raw:
                rec["raw"] = str(raw)[:1500]
        self.calls.append(rec)
        if isinstance(reply, dict) and status == "ok":
            return reply
        return {"orders": [], "note": f"no usable reply ({status}): {(error or '')[:120]}"}


TRANSPORT_HINTS = ("HTTP", "Timeout", "ConnectionError", "ConnectTimeout", "ReadTimeout", "SSLError", "RemoteDisconnected",
                   "RequestException", "TimeoutExpired", "not logged in", "rate limit")


def classify(reply: Any, error: str | None) -> str:
    """ok, format (no JSON object, or no list of orders), or transport (the call itself failed: not the prompt's
    doing, and a run with many of them should be repeated)."""
    if isinstance(reply, dict):
        orders = reply.get("orders")
        if isinstance(orders, list) and all(isinstance(o, dict) for o in orders):
            return "ok"
        return "format"
    if error and any(h.lower() in error.lower() for h in TRANSPORT_HINTS):
        return "transport"
    return "format"


# ----------------------------------------------------------------------------------- running a lab
@dataclass
class LabSettings:
    """Everything that is the same for every run of a lab."""

    llm: str
    periods: list[str] = field(default_factory=lambda: ["2026", "2022", "2020"])
    seeds: list[int] = field(default_factory=lambda: [0, 1, 2, 3])
    universe: str | None = "core"
    symbols: list[str] | None = None
    max_positions: int = 5
    temperature: float | None = 0.2
    max_tokens: int | None = None
    blind: bool = True
    news: bool = True
    base_url: str | None = None
    price_in: float | None = None  # USD per million input tokens, for the cost line of the report
    price_out: float | None = None
    start: str | None = None  # a shorter window inside one period (tests, pilots); None = the whole period
    end: str | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_folder(lab: Path, variant: str, period: str, seed: int) -> Path:
    return Path(lab) / "runs" / variant / f"{period}-s{seed}"


def _done(folder: Path) -> bool:
    return (folder / "manifest.json").is_file() and (folder / "calls.jsonl").is_file()


def run_one(variant: Variant, period: str, seed: int, settings: LabSettings, lab: str | Path,
            provider: Any | None = None, check: bool = True) -> dict[str, Any]:
    """Trade one period with one variant and one seed; save the record under ``lab/runs/<variant>/<period>-s<seed>``.
    ``provider`` is any object with ``complete_json(system, user, schema)`` (default: ``settings.llm``)."""
    from .agent import run_policy
    from .experiment import Experiment

    lab = Path(lab)
    folder = run_folder(lab, variant.name, str(period), int(seed))
    if _done(folder):
        return {"variant": variant.name, "period": str(period), "seed": int(seed), "folder": str(folder),
                "skipped": True}
    if folder.exists():  # an interrupted run: start it again
        shutil.rmtree(folder)
    folder.parent.mkdir(parents=True, exist_ok=True)
    prov = provider if provider is not None else make_provider_for(settings.llm, settings.temperature,
                                                                   settings.base_url, settings.max_tokens)
    tune(prov, variant, seed)
    policy = VariantPolicy(variant, prov)
    exp = Experiment(period=get_period(period).name, universe=None if settings.symbols else settings.universe,
                     symbols=settings.symbols, news=settings.news, max_positions=settings.max_positions,
                     start=settings.start, end=settings.end, seed=int(seed), label=f"prompt-{variant.name}")
    t0 = time.time()
    res = run_policy(policy, exp, decide_every=variant.decide_every, blind=settings.blind, max_news=variant.max_news,
                     label=f"prompt:{variant.name}", check=check, out_dir=folder)
    folder = Path(res.folder)
    (folder / "prompt.txt").write_text(variant.text + "\n", encoding="utf-8")
    with open(folder / "calls.jsonl", "w", encoding="utf-8") as fh:
        for c in policy.calls:
            fh.write(json.dumps(c, default=str) + "\n")
    man = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    man["prompt"] = {**variant.describe(), "file": "prompt.txt", "schema": variant.schema}
    man["llm"] = {"model": settings.llm, "temperature": settings.temperature, "seed": int(seed),
                  "base_url": settings.base_url, "max_tokens": settings.max_tokens,
                  "provider": type(prov).__name__, "user_message": USER_PREFIX + "<observation as compact JSON>"}
    man["reproduce"] = reproduce_command(variant, period, seed, settings)
    (folder / "manifest.json").write_text(json.dumps(man, indent=2, default=str) + "\n", encoding="utf-8")
    return {"variant": variant.name, "period": str(period), "seed": int(seed), "folder": str(folder),
            "seconds": round(time.time() - t0, 1), "steps": len(policy.calls),
            "total_return": res.metrics.get("total_return"), "sharpe": res.metrics.get("sharpe"),
            "leak_check": (res.leak or {}).get("verdict")}


def reproduce_command(variant: Variant, period: str, seed: int, settings: LabSettings) -> str:
    parts = ["riskaware research prompts run", f"--llm {settings.llm}"]
    parts.append(f"--variants {variant.name}" if variant.name in VARIANTS else "--prompt-file prompt.txt")
    parts += [f"--periods {get_period(period).name}", f"--seed-list {seed}"]
    parts.append(f"--symbols {','.join(settings.symbols)}" if settings.symbols else f"--universe {settings.universe}")
    parts.append(f"--max-positions {settings.max_positions}")
    if settings.temperature is not None:
        parts.append(f"--temperature {settings.temperature:g}")
    if not settings.blind:
        parts.append("--named")
    return " ".join(parts)


def plan(variants: list[Variant], settings: LabSettings) -> list[tuple[Variant, str, int]]:
    """The runs of a lab, seed by seed (so that an early stop still leaves complete seeds to compare)."""
    return [(v, str(p), int(s)) for s in settings.seeds for p in settings.periods for v in variants]


def estimate(variants: list[Variant], settings: LabSettings, pilot: str | Path | None = None) -> dict[str, Any]:
    """What a lab will cost before it runs: runs, model calls and, from a pilot lab's measured tokens per call (per
    variant where the pilot has it, else its overall mean), tokens and the list price."""
    from .data import bundled_market
    from .experiment import Experiment, _dates

    days = {}
    for p in settings.periods:
        name = get_period(p).name
        mk = bundled_market(name)
        s0, s1 = _dates(Experiment(period=name, start=settings.start, end=settings.end), mk)
        days[name] = len(mk.trading_days(s0, s1)) - 1  # decision days (the last day only closes the books)
    per_call: dict[str, float] = {}
    overall = None
    if pilot is not None:
        rows = collect(pilot)
        tot = [(r["prompt_tokens"] + r["completion_tokens"], r["steps"]) for r in rows if r["steps"]]
        if tot:
            overall = sum(t for t, _ in tot) / sum(n for _, n in tot)
        for name in {r["variant"] for r in rows}:
            rs = [r for r in rows if r["variant"] == name and r["steps"]]
            per_call[name] = sum(r["prompt_tokens"] + r["completion_tokens"] for r in rs) / sum(r["steps"] for r in rs)
    out_rows, calls, tokens = [], 0, 0.0
    for v in variants:
        n = sum(math.ceil(d / v.decide_every) for d in days.values()) * len(settings.seeds)
        t = per_call.get(v.name, overall)
        out_rows.append({"variant": v.name, "runs": len(days) * len(settings.seeds), "calls": n,
                         "tokens_per_call": round(t) if t else None, "tokens": round(n * t) if t else None})
        calls += n
        tokens += n * t if t else 0.0
    price = None
    if overall and settings.price_in is not None and settings.price_out is not None:
        price = round(tokens * (0.9 * settings.price_in + 0.1 * settings.price_out) / 1e6, 2)
    return {"runs": len(variants) * len(days) * len(settings.seeds), "calls": calls,
            "tokens": round(tokens) if overall else None, "list_price_usd": price, "decision_days": days,
            "variants": out_rows, "pilot": str(pilot) if pilot else None,
            "assumes": "input about 90% of the tokens" if price is not None else None}


def start_lab(lab: str | Path, variants: list[Variant], settings: LabSettings) -> Path:
    """Create the lab folder (or check that an existing one has the same settings) and write lab.json."""
    lab = Path(lab).expanduser()
    lab.mkdir(parents=True, exist_ok=True)
    meta_path = lab / "lab.json"
    old = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else None
    known = {v["name"]: v for v in (old or {}).get("variants", [])}
    for v in variants:
        if v.name in known and known[v.name]["variant_sha256"] != v.sha256:
            raise ValueError(f"the lab already has a variant {v.name!r} with a different prompt or settings; use a new "
                             "lab folder or another name")
    if old is not None:
        same = {k: old["settings"].get(k) for k in ("llm", "universe", "symbols", "max_positions", "temperature",
                                                     "blind", "news", "max_tokens", "start", "end")}
        mine = {k: settings.to_dict().get(k) for k in same}
        if same != mine:
            raise ValueError(f"the lab in {lab} was run with other settings ({same}); use a new folder")
    import riskaware

    allv = {**known, **{v.name: v.describe(text=True) for v in variants}}
    meta = {"created_utc": (old or {}).get("created_utc") or _now(), "updated_utc": _now(),
            "riskaware": riskaware.__version__, "set_version": SET_VERSION,
            "settings": {**(old or {}).get("settings", {}), **settings.to_dict(),
                         "periods": sorted(set((old or {}).get("settings", {}).get("periods", [])) | set(settings.periods)),
                         "seeds": sorted(set((old or {}).get("settings", {}).get("seeds", [])) | set(settings.seeds))},
            "variants": list(allv.values())}
    meta_path.write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    return lab


def run_lab(variants: list[Variant], settings: LabSettings, lab: str | Path | None = None, workers: int = 4,
            provider_factory: Callable[[Variant, int], Any] | None = None, check: bool = True,
            progress: Callable[[dict], None] | None = None, stop: Callable[[], bool] | None = None) -> Path:
    """Run every variant on every period and seed (runs already saved are skipped, so a lab can be resumed).
    ``provider_factory(variant, seed)`` builds the model client for one run (default: ``settings.llm``); ``stop()``
    is asked before each run starts and ends the lab early when it says so (a token budget, say)."""
    lab = start_lab(lab or LABS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}", variants, settings)
    jobs = plan(variants, settings)
    lock = threading.Lock()

    def one(job: tuple[Variant, str, int]) -> dict[str, Any]:
        v, p, s = job
        if stop is not None and stop():
            return {"variant": v.name, "period": p, "seed": s, "stopped": True}
        prov = provider_factory(v, s) if provider_factory is not None else None
        try:
            out = run_one(v, p, s, settings, lab, provider=prov, check=check)
        except Exception as exc:  # noqa: BLE001 - one failed run must not end the lab; it is retried on resume
            out = {"variant": v.name, "period": p, "seed": s, "error": f"{type(exc).__name__}: {exc}"}
        with lock:
            if progress is not None:
                progress(out)
        return out

    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        for fut in as_completed([pool.submit(one, j) for j in jobs]):
            fut.result()
    return lab


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ----------------------------------------------------------------------------------- reading the records
def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def read_run(folder: str | Path) -> dict[str, Any] | None:
    """One saved run as a flat row: settings, metrics, benchmarks, format and order counts, tokens."""
    folder = Path(folder)
    if not _done(folder):
        return None
    man = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    met = json.loads((folder / "metrics.json").read_text(encoding="utf-8"))
    log = json.loads((folder / "policy_log.json").read_text(encoding="utf-8"))
    calls = [json.loads(x) for x in (folder / "calls.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    st, spy, ew = met.get("strategy", {}), met.get("spy", {}), met.get("equal_weight", {})
    steps = log.get("steps", [])
    n_orders = sum(len(s.get("orders") or []) for s in steps)
    n_bad = sum(len(s.get("problems") or []) for s in steps)
    status = [c.get("status") for c in calls]
    n_transport = status.count("transport")
    n_format = status.count("format")
    asked = max(1, len(calls) - n_transport)
    pr = man.get("prompt", {})
    exp = man.get("experiment", {})
    row = {
        "variant": pr.get("name"), "factor": pr.get("factor"), "period": str(exp.get("period")),
        "seed": int(man.get("llm", {}).get("seed", exp.get("seed", 0))), "folder": str(folder),
        "prompt_sha256": pr.get("prompt_sha256"), "variant_sha256": pr.get("variant_sha256"),
        "model": man.get("llm", {}).get("model"), "start": exp.get("start"), "end": exp.get("end"),
        "leak_check": (man.get("leak_check") or {}).get("verdict"),
        "steps": len(calls), "transport_errors": n_transport, "format_errors": n_format,
        "orders": n_orders, "invalid_orders": n_bad,
        "format_error_rate": n_format / asked,
        "invalid_order_rate": n_bad / (n_orders + n_bad) if (n_orders + n_bad) else 0.0,
        "orders_per_step": n_orders / max(1, len(steps)),
        "prompt_tokens": sum(int(c.get("prompt_tokens") or 0) for c in calls),
        "completion_tokens": sum(int(c.get("completion_tokens") or 0) for c in calls),
        "seconds": sum(float(c.get("seconds") or 0) for c in calls),
    }
    for k in ("total_return", "sharpe", "max_drawdown", "volatility", "turnover", "avg_exposure", "trades",
              "round_trips", "win_rate"):
        row[k] = _num(st.get(k))
    row["spy_total_return"], row["spy_sharpe"] = _num(spy.get("total_return")), _num(spy.get("sharpe"))
    row["spy_max_drawdown"] = _num(spy.get("max_drawdown"))
    row["ew_total_return"], row["ew_sharpe"] = _num(ew.get("total_return")), _num(ew.get("sharpe"))
    row["ew_max_drawdown"] = _num(ew.get("max_drawdown"))
    tr = row["total_return"]
    row["excess_vs_spy"] = tr - row["spy_total_return"] if None not in (tr, row["spy_total_return"]) else None
    row["excess_vs_equal_weight"] = tr - row["ew_total_return"] if None not in (tr, row["ew_total_return"]) else None
    return row


def collect(lab: str | Path) -> list[dict[str, Any]]:
    rows = []
    for f in sorted((Path(lab) / "runs").glob("*/*-s*")):
        r = read_run(f)
        if r is not None:
            rows.append(r)
    return rows


# ----------------------------------------------------------------------------------- statistics
def sign_flip_p(d: np.ndarray, n_random: int = 20000, seed: int = 0) -> float:
    """Two-sided p-value of a mean difference of zero under random signs: exact up to 16 pairs, sampled beyond."""
    d = np.asarray(d, dtype=float)
    d = d[~np.isnan(d)]
    n = len(d)
    if n == 0:
        return math.nan
    obs = abs(d.mean())
    if n <= 16:
        signs = ((np.arange(2**n)[:, None] >> np.arange(n)) & 1) * 2 - 1
    else:
        signs = np.random.default_rng(seed).choice([-1, 1], size=(n_random, n))
    means = np.abs((signs * d).mean(axis=1))
    return float((means >= obs - 1e-12).mean())


def bootstrap_ci(d: np.ndarray, groups: np.ndarray | None = None, n_boot: int = 10000, seed: int = 0,
                 level: float = 0.95) -> tuple[float, float]:
    """Percentile interval of the mean difference; with ``groups`` (the period of each pair) the pairs are resampled
    within each period and the periods weigh equally."""
    d = np.asarray(d, dtype=float)
    ok = ~np.isnan(d)
    d = d[ok]
    if len(d) < 2:
        return (math.nan, math.nan)
    rng = np.random.default_rng(seed)
    if groups is None:
        means = d[rng.integers(0, len(d), size=(n_boot, len(d)))].mean(axis=1)
    else:
        g = np.asarray(groups)[ok]
        parts = []
        for key in sorted(set(g)):
            x = d[g == key]
            parts.append(x[rng.integers(0, len(x), size=(n_boot, len(x)))].mean(axis=1))
        means = np.mean(parts, axis=0)
    a = (1 - level) / 2 * 100
    lo, hi = np.percentile(means, [a, 100 - a])
    return float(lo), float(hi)


def holm(pvals: dict[str, float]) -> dict[str, float]:
    """Holm-Bonferroni adjusted p-values (a family of tests, one per variant)."""
    items = sorted(((p, k) for k, p in pvals.items() if p == p), key=lambda t: t[0])
    m, out, run = len(items), {}, 0.0
    for i, (p, k) in enumerate(items):
        run = max(run, min(1.0, (m - i) * p))
        out[k] = run
    for k in pvals:
        out.setdefault(k, math.nan)
    return out


def bh(pvals: dict[str, float]) -> dict[str, float]:
    """Benjamini-Hochberg q-values (false discovery rate), a less strict companion to Holm."""
    items = sorted(((p, k) for k, p in pvals.items() if p == p), key=lambda t: t[0])
    m, out, run = len(items), {}, 1.0
    for i in range(m - 1, -1, -1):
        p, k = items[i]
        run = min(run, p * m / (i + 1))
        out[k] = run
    for k in pvals:
        out.setdefault(k, math.nan)
    return out


def _mean(xs: Iterable[float | None]) -> float | None:
    v = [x for x in xs if x is not None and x == x]
    return float(np.mean(v)) if v else None


def compare(rows: list[dict[str, Any]], metric: str, baseline: str = BASELINE,
            seed: int = 0) -> dict[str, dict[str, Any]]:
    """Each variant against the baseline on ``metric``, paired by period and seed (see the module docstring)."""
    base = {(r["period"], r["seed"]): r for r in rows if r["variant"] == baseline}
    names = list(dict.fromkeys(r["variant"] for r in rows if r["variant"] != baseline))
    out: dict[str, dict[str, Any]] = {}
    for name in names:
        pairs = [(r["period"], r[metric] - base[(r["period"], r["seed"])][metric])
                 for r in rows if r["variant"] == name and (r["period"], r["seed"]) in base
                 and r.get(metric) is not None and base[(r["period"], r["seed"])].get(metric) is not None]
        if not pairs:
            continue
        g = np.array([p for p, _ in pairs])
        d = np.array([x for _, x in pairs], dtype=float)
        by = {p: float(d[g == p].mean()) for p in sorted(set(g), reverse=True)}
        lo, hi = bootstrap_ci(d, g, seed=seed)
        out[name] = {"diff": float(np.mean(list(by.values()))), "ci95": [lo, hi], "pairs": int(len(d)),
                     "p": sign_flip_p(d, seed=seed), "by_period": by,
                     "same_sign_periods": int(sum(1 for v in by.values() if np.sign(v) == np.sign(np.mean(d)))),
                     "wins": int((d > 0).sum()), "losses": int((d < 0).sum())}
    adj, q = holm({k: v["p"] for k, v in out.items()}), bh({k: v["p"] for k, v in out.items()})
    for k, v in out.items():
        v["p_holm"], v["q_bh"] = adj[k], q[k]
        better = HIGHER_IS_BETTER.get(metric)
        if better is None or not (v["p_holm"] < 0.05):
            v["verdict"] = "noise" if better is not None else "differs" if v["p_holm"] < 0.05 else "same"
        else:
            v["verdict"] = "better" if (v["diff"] > 0) == better else "worse"
    return out


def reachable(pairs: int, variants: int, alpha: float = 0.05) -> dict[str, Any]:
    """Can the design show anything? The smallest exact sign-flip p-value of ``pairs`` pairs is 2 / 2**pairs, and
    Holm multiplies the smallest p-value by the number of variants. Thirteen variants need at least ten pairs."""
    best = min(1.0, variants * 2.0 / 2**pairs) if pairs else 1.0
    need = next((n for n in range(1, 64) if variants * 2.0 / 2**n < alpha), None)
    return {"pairs": pairs, "variants": variants, "smallest_p_holm": best, "can_pass": best < alpha,
            "pairs_needed": need}


def seed_spread(rows: list[dict[str, Any]], metric: str, variant: str = BASELINE) -> dict[str, Any]:
    """How much two seeds of the same prompt differ: the noise floor any variant has to beat."""
    by: dict[str, list[float]] = {}
    for r in rows:
        if r["variant"] == variant and r.get(metric) is not None:
            by.setdefault(r["period"], []).append(r[metric])
    gaps = [abs(a - b) for xs in by.values() for i, a in enumerate(xs) for b in xs[i + 1:]]
    sds = {p: float(np.std(xs, ddof=1)) for p, xs in by.items() if len(xs) > 1}
    return {"sd_by_period": sds, "mean_abs_gap": float(np.mean(gaps)) if gaps else None, "pairs": len(gaps)}


def summarize(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per variant: the mean of each metric over all runs and by period, the runs, tokens and seconds."""
    out: dict[str, dict[str, Any]] = {}
    for name in dict.fromkeys(r["variant"] for r in rows):
        rs = [r for r in rows if r["variant"] == name]
        periods = sorted({r["period"] for r in rs}, reverse=True)
        out[name] = {"runs": len(rs), "factor": rs[0]["factor"], "prompt_sha256": rs[0]["prompt_sha256"],
                     "variant_sha256": rs[0]["variant_sha256"],
                     "metrics": {m: {"mean": _mean(r.get(m) for r in rs),
                                     "by_period": {p: _mean(r.get(m) for r in rs if r["period"] == p)
                                                   for p in periods}} for m in METRICS},
                     "steps": sum(r["steps"] for r in rs), "transport_errors": sum(r["transport_errors"] for r in rs),
                     "prompt_tokens": sum(r["prompt_tokens"] for r in rs),
                     "completion_tokens": sum(r["completion_tokens"] for r in rs),
                     "seconds": sum(r["seconds"] for r in rs),
                     "leak_checks": {v: sum(1 for r in rs if r["leak_check"] == v) for v in {r["leak_check"] for r in rs}}}
    return out


def benchmarks(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        out.setdefault(r["period"], {"start": r["start"], "end": r["end"],
                                     "spy": {"total_return": r["spy_total_return"], "sharpe": r["spy_sharpe"],
                                             "max_drawdown": r["spy_max_drawdown"]},
                                     "equal_weight": {"total_return": r["ew_total_return"], "sharpe": r["ew_sharpe"],
                                                      "max_drawdown": r["ew_max_drawdown"]}})
    return dict(sorted(out.items(), reverse=True))


# ----------------------------------------------------------------------------------- the report
def report(lab: str | Path, site: str | Path | None = None, analysis: bool = True) -> dict[str, Any]:
    """Compare the variants of a lab (see the module docstring) and write prompts.json, prompts.md, prompts.png;
    with ``site``, also the website's data file."""
    lab = Path(lab).expanduser()
    meta = json.loads((lab / "lab.json").read_text(encoding="utf-8"))
    rows = collect(lab)
    if not rows:
        raise ValueError(f"no finished runs in {lab}")
    settings = meta["settings"]
    known = {v["name"]: v for v in meta["variants"]}
    order = [n for n in known if any(r["variant"] == n for r in rows)]
    summary = summarize(rows)
    comparisons = {m: compare(rows, m) for m in ("sharpe", "total_return", "max_drawdown", "excess_vs_spy",
                                                  "volatility", "turnover", "avg_exposure", "orders_per_step",
                                                  "format_error_rate", "invalid_order_rate")}
    tokens_in = sum(r["prompt_tokens"] for r in rows)
    tokens_out = sum(r["completion_tokens"] for r in rows)
    pi, po = settings.get("price_in"), settings.get("price_out")
    cost = {"calls": sum(r["steps"] for r in rows), "prompt_tokens": tokens_in, "completion_tokens": tokens_out,
            "list_price_usd": round(tokens_in * pi / 1e6 + tokens_out * po / 1e6, 2) if None not in (pi, po) else None,
            "price_per_million": {"input": pi, "output": po}, "note": settings.get("note", "")}
    out = {"generated_utc": _now(), "lab": str(lab), "set_version": meta.get("set_version"),
           "settings": settings, "baseline": BASELINE, "primary_metric": PRIMARY,
           "variants": [{**{k: known[n][k] for k in ("name", "factor", "about", "about_zh", "max_news", "decide_every",
                                                      "reasoning", "response_format", "prompt_sha256",
                                                      "variant_sha256")},
                         **summary[n],
                         "vs_baseline": {m: c[n] for m, c in comparisons.items() if n in c}} for n in order],
           "noise": {m: seed_spread(rows, m) for m in ("sharpe", "total_return", "max_drawdown")},
           "power": reachable(min((c["pairs"] for c in comparisons["sharpe"].values()), default=0),
                              len(comparisons["sharpe"])),
           "benchmarks": benchmarks(rows), "cost": cost, "runs": rows}
    if analysis:
        out["decisions"] = decision_metrics(rows)
    (lab / "prompts.json").write_text(json.dumps(out, indent=1, default=_json) + "\n", encoding="utf-8")
    (lab / "prompts.md").write_text(render(out), encoding="utf-8")
    try:
        draw(out, lab / "prompts.png")
    except Exception:  # noqa: BLE001 - the chart is a nicety; the numbers are saved
        pass
    if site:
        write_site(out, site)
    return out


def decision_metrics(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Decision-level hit rate and IC per variant from research/analysis.py, when that module is installed."""
    try:
        from . import analysis  # type: ignore[attr-defined]
    except ImportError:
        return None
    fn = getattr(analysis, "decision_metrics", None) or getattr(analysis, "analyze_run", None)
    if fn is None:
        return None
    out: dict[str, Any] = {}
    for r in rows:
        try:
            res = fn(r["folder"])
        except Exception as exc:  # noqa: BLE001 - one unreadable run must not stop the report
            res = {"error": str(exc)[:200]}
        out.setdefault(r["variant"], []).append({"period": r["period"], "seed": r["seed"], **(res or {})})
    return out


def _json(v: Any) -> Any:
    if isinstance(v, (np.floating, np.integer)):
        return v.item()
    return str(v)


def _pct(v: Any, sign: bool = True) -> str:
    f = _num(v)
    if f is None:
        return "n/a"
    return f"{f * 100:+.1f}%" if sign else f"{f * 100:.1f}%"


def _f2(v: Any, sign: bool = False) -> str:
    f = _num(v)
    return "n/a" if f is None else (f"{f:+.2f}" if sign else f"{f:.2f}")


def _pv(v: Any) -> str:
    f = _num(v)
    return "n/a" if f is None else ("<0.001" if f < 0.001 else f"{f:.3f}")


def render(r: dict[str, Any]) -> str:
    s = r["settings"]
    per = ", ".join(r["benchmarks"])
    lines = [f"# Prompt test: {s['llm']}", "",
             f"{len(r['variants'])} prompt variants, each on {per}, seeds {', '.join(map(str, s['seeds']))}; "
             f"universe {s['universe'] or 'custom'} ({'blind' if s['blind'] else 'named'}), at most "
             f"{s['max_positions']} positions, temperature {s['temperature']}. Every variant changes one factor from "
             "the baseline. Differences are paired (same period, same seed): mean over periods, 95% bootstrap "
             "interval, exact sign-flip p-value, Holm-corrected across the variants.", ""]
    lines += ["## Sharpe and return by variant", "",
              "| Variant | Factor | Sharpe | Δ Sharpe vs baseline (95% CI) | p (Holm) | Return | Δ return | Max DD | "
              "Format errors | Invalid orders | Verdict |", "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for v in r["variants"]:
        m = v["metrics"]
        c = v["vs_baseline"].get("sharpe")
        ct = v["vs_baseline"].get("total_return")
        if c:
            dsh = f"{_f2(c['diff'], True)} ({_f2(c['ci95'][0], True)} to {_f2(c['ci95'][1], True)})"
            p, verdict = _pv(c["p_holm"]), c["verdict"]
        else:
            dsh, p, verdict = "—", "—", "baseline" if v["name"] == r["baseline"] else "—"
        lines.append(f"| `{v['name']}` | {v['factor']} | {_f2(m['sharpe']['mean'])} | {dsh} | {p} | "
                     f"{_pct(m['total_return']['mean'])} | {_pct(ct['diff']) if ct else '—'} | "
                     f"{_pct(m['max_drawdown']['mean'])} | {_pct(m['format_error_rate']['mean'], False)} | "
                     f"{_pct(m['invalid_order_rate']['mean'], False)} | {verdict} |")
    lines.append("")
    lines += ["## By period (mean Sharpe; Δ vs baseline)", "", "| Variant | " + " | ".join(r["benchmarks"]) + " |",
              "|---|" + "---:|" * len(r["benchmarks"])]
    for v in r["variants"]:
        c = v["vs_baseline"].get("sharpe")
        cells = []
        for p in r["benchmarks"]:
            sh = v["metrics"]["sharpe"]["by_period"].get(p)
            cells.append(_f2(sh) + (f" ({_f2(c['by_period'].get(p), True)})" if c and p in c["by_period"] else ""))
        lines.append(f"| `{v['name']}` | " + " | ".join(cells) + " |")
    lines.append("")
    lines += ["## Benchmarks", "", "| Period | Days | SPY return | SPY Sharpe | Equal-weight return | Equal-weight Sharpe |",
              "|---|---|---:|---:|---:|---:|"]
    for p, b in r["benchmarks"].items():
        lines.append(f"| {p} | {b['start']} to {b['end']} | {_pct(b['spy']['total_return'])} | "
                     f"{_f2(b['spy']['sharpe'])} | {_pct(b['equal_weight']['total_return'])} | "
                     f"{_f2(b['equal_weight']['sharpe'])} |")
    lines.append("")
    pw = r.get("power") or {}
    if pw and not pw.get("can_pass"):
        lines += [f"**Too few pairs to show anything:** with {pw['pairs']} pairs per variant and {pw['variants']} "
                  f"variants the smallest possible Holm-corrected p-value is {_pv(pw['smallest_p_holm'])}, so every "
                  f"verdict is `noise` by construction. {pw['pairs_needed']} pairs (periods x seeds) are needed.", ""]
    nz = r["noise"]["sharpe"]
    lines += ["## Noise floor", "",
              f"Two seeds of the baseline prompt differ in Sharpe by {_f2(nz['mean_abs_gap'])} on average "
              f"({nz['pairs']} pair{'s' if nz['pairs'] != 1 else ''} of runs on the same period); standard deviation across seeds by period: "
              + ", ".join(f"{p} {_f2(x)}" for p, x in nz["sd_by_period"].items()) + ".", ""]
    c = r["cost"]
    lines += ["## Cost", "",
              f"{c['calls']:,} model calls, {c['prompt_tokens']:,} input and {c['completion_tokens']:,} output tokens"
              + (f", ${c['list_price_usd']:.2f} at list price" if c.get("list_price_usd") is not None else "")
              + (f". {c['note']}" if c.get("note") else "") + ".", ""]
    lines += ["## Variants", ""] + [f"- `{v['name']}` ({v['factor']}): {v['about']} Prompt SHA-256 "
                                    f"`{v['prompt_sha256'][:16]}...`" for v in r["variants"]] + [""]
    return "\n".join(lines)


def draw(r: dict[str, Any], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    vs = [v for v in r["variants"] if v["vs_baseline"].get("sharpe")]
    if not vs:
        return
    fig, axes = plt.subplots(1, 2, figsize=(10, 0.42 * len(vs) + 1.4), sharey=True)
    colors = {"better": "#1a7f37", "worse": "#cf222e", "noise": "#57606a"}
    marks = dict(zip(r["benchmarks"], ("o", "s", "^", "D", "v", "P"), strict=False))
    for ax, metric, label, scale in ((axes[0], "sharpe", "Δ Sharpe vs baseline", 1.0),
                                     (axes[1], "total_return", "Δ total return vs baseline (pp)", 100.0)):
        for i, v in enumerate(vs):
            c = v["vs_baseline"][metric]
            y = len(vs) - 1 - i
            col = colors.get(c["verdict"], "#57606a")
            ax.plot([c["ci95"][0] * scale, c["ci95"][1] * scale], [y, y], color=col, linewidth=2.2, alpha=0.8)
            ax.plot(c["diff"] * scale, y, "o", color=col, markersize=7)
            for p, x in c["by_period"].items():
                ax.plot(x * scale, y + 0.22, marks.get(p, "x"), color="#8c959f", markersize=4, alpha=0.9)
        ax.axvline(0, color="#8c959f", linewidth=0.8)
        ax.set_xlabel(label)
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="x", color="#d0d7de", linewidth=0.5)
    axes[0].set_yticks(range(len(vs)))
    axes[0].set_yticklabels([v["name"] for v in reversed(vs)])
    handles = [plt.Line2D([], [], marker=m, linestyle="", color="#8c959f", label=p) for p, m in marks.items()]
    axes[1].legend(handles=handles, title="period", frameon=False, loc="lower right", fontsize=8)
    fig.suptitle(f"Prompt variants vs the baseline ({r['settings']['llm']}): mean of paired differences, 95% CI",
                 fontsize=10, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


# ----------------------------------------------------------------------------------- the website's data
def site_payload(r: dict[str, Any]) -> dict[str, Any]:
    """The prompt board for the website (site/assets/data/prompts.json); the format is described in docs/prompts.md."""
    s = r["settings"]
    rows = []
    for v in r["variants"]:
        m = v["metrics"]
        row = {"name": v["name"], "factor": v["factor"], "about": {"en": v["about"], "zh": v["about_zh"]},
               "prompt_sha256": v["prompt_sha256"], "variant_sha256": v["variant_sha256"], "runs": v["runs"],
               "settings": {k: v[k] for k in ("max_news", "decide_every", "reasoning", "response_format")},
               "metrics": {k: {"mean": _round(m[k]["mean"]),
                               "by_period": {p: _round(x) for p, x in m[k]["by_period"].items()}}
                           for k in ("sharpe", "total_return", "max_drawdown", "excess_vs_spy", "turnover",
                                     "format_error_rate", "invalid_order_rate")},
               "tokens_per_step": _round((v["prompt_tokens"] + v["completion_tokens"]) / max(1, v["steps"]), 0)}
        vb = {}
        for k, c in v["vs_baseline"].items():
            if k in ("sharpe", "total_return", "max_drawdown", "format_error_rate", "invalid_order_rate", "turnover"):
                vb[k] = {"diff": _round(c["diff"]), "ci95": [_round(c["ci95"][0]), _round(c["ci95"][1])],
                         "p": _round(c["p"]), "p_holm": _round(c["p_holm"]), "pairs": c["pairs"],
                         "by_period": {p: _round(x) for p, x in c["by_period"].items()}, "verdict": c["verdict"]}
        row["vs_baseline"] = vb or None
        rows.append(row)
    noise = r["noise"]["sharpe"]
    return {"format": "rta-prompt-board-1", "generated_utc": r["generated_utc"], "set_version": r["set_version"],
            "model": s["llm"], "baseline": r["baseline"], "primary_metric": r["primary_metric"],
            "settings": {k: s.get(k) for k in ("universe", "symbols", "max_positions", "temperature", "blind", "news",
                                               "periods", "seeds")},
            "benchmarks": {p: {"start": b["start"], "end": b["end"],
                               "spy": {k: _round(x) for k, x in b["spy"].items()},
                               "equal_weight": {k: _round(x) for k, x in b["equal_weight"].items()}}
                           for p, b in r["benchmarks"].items()},
            "noise": {"sharpe_mean_abs_gap_between_seeds": _round(noise["mean_abs_gap"]),
                      "sharpe_sd_by_period": {p: _round(x) for p, x in noise["sd_by_period"].items()}},
            "variants": rows,
            "cost": {k: r["cost"][k] for k in ("calls", "prompt_tokens", "completion_tokens", "list_price_usd")},
            "method": {"en": "Each variant changes one factor of the baseline prompt. Paired differences (same period, "
                             "same seed), mean over periods, 95% bootstrap interval, exact sign-flip p-value, Holm "
                             "correction across variants; 'noise' means the difference is not distinguishable from "
                             "seed-to-seed variation.",
                       "zh": "每个变体只改基线 prompt 的一个因素。配对差（同一时段、同一种子），按时段平均，95% bootstrap "
                             "区间，精确符号翻转检验，按变体个数做 Holm 校正；「noise」表示这个差别和换个种子的波动分不开。"}}


def _round(v: Any, nd: int = 4) -> Any:
    f = _num(v)
    if f is None:
        return None
    return int(round(f)) if nd == 0 else round(f, nd)


def write_site(r: dict[str, Any], path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(site_payload(r), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return p


def variants_table() -> list[dict[str, Any]]:
    return [v.describe() for v in VARIANTS.values()]


__all__ = ["VARIANTS", "Variant", "LabSettings", "BASELINE", "get_variant", "select", "from_file", "tune",
           "VariantPolicy", "run_one", "run_lab", "collect", "compare", "report", "site_payload", "replace"]
