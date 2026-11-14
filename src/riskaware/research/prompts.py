"""Prompt tests: which system prompt makes an LLM trading agent trade better, with evidence that survives the noise.

A *variant* is a system prompt plus the few settings a prompt study varies with it: how many headlines per stock the
agent sees, how often it decides, whether its reply starts with a short reasoning field, and how strictly the reply
format is enforced. The baseline is the package's own ``agent.SYSTEM_PROMPT`` with the defaults of ``llm_policy``;
every other variant changes exactly one factor, so a difference can be traced to that factor:

* role: ``role_pm``, ``role_quant``, ``role_risk`` put a one-sentence persona in front;
* ``risk_rules``: four explicit rules (a 25% cap per stock, 30% cash when the regime is UNCERTAIN or RISK_OFF, sell at
  8% below the purchase price, prefer stocks above their 200-day average); ``cap_25``, ``regime_cash``,
  ``stop_loss`` and ``above_200`` state one of the four alone;
* cash level: ``cash_30`` and ``cash_50`` keep a fixed share in cash at all times (does holding fewer stocks alone
  explain what the rules do?);
* ``trend_news``: an explicit decision rule (buy only when trend and news agree, sell when either turns);
* ``reason_first``: a short reasoning field before the orders;
* focus: ``focus_news`` and ``focus_trend`` say which evidence to weigh most;
* ``few_shot``: three made-up examples of an observation and a good reply;
* format: ``format_json`` (any JSON object, the schema only in the prompt) and ``format_text`` (nothing enforced);
* headlines: ``news_0`` and ``news_6`` show 0 or 6 headlines per stock instead of 3;
* frequency: ``daily`` decides every trading day instead of every fifth.

``run_lab`` trades every variant on the same frozen periods with the same model, blind, for several seeds (a seed
fixes the blind aliases; with ``api_seed`` also the API's sampling seed), and writes one record per run. Each
record's ``manifest.json`` names the variant and carries the SHA-256 of its prompt text and of the whole variant;
``prompt.txt`` holds the text and ``calls.jsonl`` every model call (status, tokens, seconds).

``prompt_report.report`` compares every variant with the baseline on the same period and seed (paired): the mean
difference, a bootstrap interval, an exact sign-flip test and a Holm correction for the number of variants, so that it
says plainly which differences are larger than what two seeds of the same prompt already differ by.
``prompt_report.attribution`` splits each paired return difference into holding more or less stock (on average and at
which times) and holding other stocks. The report goes to ``prompts.json``, ``prompts.md`` and ``prompts.png`` in the
lab folder; ``prompt_report.board`` combines labs (one model each) into the website's data file.

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
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
                    "invalid_order_rate": False, "hit_rate_5d": True, "decision_excess_5d": True,
                    "buy_excess_5d": True, "sell_avoided_5d": True, "decision_ic": True, "holdings_ic": True}
DECISION_METRICS = {  # from research/analysis.py (analyze_run's summary), when that module is installed
    "hit_rate_5d": "decision_hit_rate_5d", "decision_excess_5d": "decision_excess_5d",
    "buy_excess_5d": "buy_excess_5d", "sell_avoided_5d": "sell_avoided_5d", "decision_ic": "decision_ic",
    "holdings_ic": "holdings_ic", "decisions": "decisions"}

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

RULE = " Risk rule you must follow: "
RISK_RULES = (
    " Risk rules you must follow: never hold more than 25% of equity in one stock; keep at least 30% in cash "
    "when the market regime is UNCERTAIN or RISK_OFF; sell a position that has fallen 8% below where you bought "
    "it; prefer stocks trading above their 200-day average.")
RULE_PARTS = {  # each of the four rules alone, in the same words
    "cap_25": ("never hold more than 25% of equity in one stock.", "the 25% cap per stock", "单只不超过 25%"),
    "regime_cash": ("keep at least 30% in cash when the market regime is UNCERTAIN or RISK_OFF.",
                    "30% cash when the regime is UNCERTAIN or RISK_OFF", "UNCERTAIN 或 RISK_OFF 时至少留 30% 现金"),
    "stop_loss": ("sell a position that has fallen 8% below where you bought it.", "the stop at 8% below the purchase "
                  "price", "跌破买入价 8% 卖出"),
    "above_200": ("prefer stocks trading above their 200-day average.", "preferring stocks above their 200-day average",
                  "优先买在 200 日均线之上的股票"),
}
CASH_LEVELS = {"cash_30": 30, "cash_50": 50}
TREND_NEWS = (
    " Before you order, decide for each stock whether its price trend (returns and the distance from its moving "
    "averages) and its news point the same way. Buy only when both are positive; sell when the trend breaks or "
    "confirmed bad news arrives; otherwise hold. Put that judgement in each order's reason.")

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
    _v("risk_rules", "risk_rules", "Four risk rules: at most 25% per stock, at least 30% cash when the regime is "
       "UNCERTAIN or RISK_OFF, sell 8% below the purchase price, prefer stocks above their 200-day average.",
       "四条风险规则：单只不超过 25%，UNCERTAIN 或 RISK_OFF 时至少留 30% 现金，跌破买入价 8% 卖出，优先买在 200 日均线"
       "之上的股票。", SYSTEM_PROMPT + RISK_RULES),
    *[_v(k, "risk_rule_part", f"One of the four risk rules alone: {en}.", f"四条风险规则里只留一条：{zh}。",
         SYSTEM_PROMPT + RULE + sent) for k, (sent, en, zh) in RULE_PARTS.items()],
    *[_v(k, "cash_level", f"A fixed cash floor: at least {n}% in cash at all times, nothing else.",
         f"固定的现金下限：任何时候至少留 {n}% 现金，其他不变。", SYSTEM_PROMPT + RULE + f"keep at least {n}% in cash at all times.")
      for k, n in CASH_LEVELS.items()],
    _v("trend_news", "decision_rule", "A decision rule: buy only when the trend and the news are both positive, sell "
       "when the trend breaks or confirmed bad news arrives, otherwise hold; the judgement goes in each reason.",
       "一条决策规则：趋势和新闻都向好才买，趋势破位或有已确认的坏消息就卖，否则持有，判断写进每笔订单的 reason。",
       SYSTEM_PROMPT + TREND_NEWS),
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
FACTORS = {"role": "Role / 角色", "risk_rules": "Risk rules / 风险规则",
           "risk_rule_part": "One risk rule / 单条风险规则", "cash_level": "Cash floor / 现金下限",
           "decision_rule": "Decision rule / 决策规则", "reasoning": "Reasoning first / 先推理",
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
    """Make an OpenAI-compatible provider send what the variant asks for: for the json and text formats a looser
    response format (text: no schema hint in the system prompt either), the sampling seed when one is given, and
    ``"stream": false``. Providers without a request body of their own (the -cli and Anthropic ones) are returned
    unchanged."""
    if isinstance(provider, OllamaChat):
        provider.seed, provider.response_format = seed, variant.response_format
        return provider
    build = getattr(provider, "request_body", None)
    if build is None or getattr(provider, "_tuned_for", None) == (variant.sha256, seed):
        return provider
    build = getattr(provider, "_plain_request_body", build)

    def request_body(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        body = build(system, user, schema)
        body["stream"] = False  # some OpenAI-compatible gateways stream unless told not to
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
                      max_tokens: int | None = None, key_env: str | None = None) -> Any:
    """A provider for ``provider:model`` (see ``riskaware.llm``) with the lab's temperature and reply cap; local
    ``ollama:`` models go through ``OllamaChat`` so that long prompts are not cut."""
    from ..llm import llm_config, make_provider, parse_choice

    kind, model = parse_choice(llm)
    if kind == "ollama":
        return OllamaChat(model, base_url=base_url, temperature=temperature, max_tokens=max_tokens or 1200)
    cfg = llm_config(llm, base_url=base_url, key_env=key_env)
    if temperature is not None:
        cfg.temperature = float(temperature)
    if max_tokens is not None:
        cfg.max_tokens = int(max_tokens)
    return make_provider(cfg)


class VariantPolicy:
    """Asks the model with a variant's prompt each step and keeps a record of every call. A call that fails on the
    way (a rate limit, a timeout, a server error) is tried again after a pause, up to ``retries`` times, so that an
    outage does not show up as a decision to hold."""

    def __init__(self, variant: Variant, provider: Any, retries: int = 4, backoff: float = 10.0,
                 recorded: list[dict[str, Any]] | None = None, on_step: Callable[[dict], None] | None = None):
        self.variant, self.provider = variant, provider
        self.retries, self.backoff = retries, backoff
        self.calls: list[dict[str, Any]] = []
        self.recorded = list(recorded or [])  # steps of an earlier, interrupted try: replayed, not asked again
        self.on_step = on_step

    def _ask(self, user: str) -> tuple[Any, str | None]:
        try:
            reply = self.provider.complete_json(self.variant.text, user, self.variant.schema)
        except Exception as exc:  # noqa: BLE001 - a failed call holds, as in llm_policy; the record says why
            return None, f"{type(exc).__name__}: {exc}"
        return reply, getattr(self.provider, "last_error", None) if reply is None else None

    def __call__(self, obs: dict) -> dict:
        from .agent import _digest

        user = USER_PREFIX + json.dumps(obs, separators=(",", ":"), default=str)
        digest = _digest(obs)
        i = len(self.calls)
        if i < len(self.recorded):
            old = self.recorded[i]
            if old.get("step") == obs.get("step") and old.get("observation_sha256") == digest:
                self.calls.append({**old["call"], "replayed": True})
                return old["reply"]
            del self.recorded[i:]  # the observation changed: nothing after this point can be replayed
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
               "prompt_chars": len(self.variant.text) + len(user), "retries": tries}
        if status != "ok":
            rec["error"] = (error or "")[:300]
            raw = getattr(self.provider, "last_raw", None)
            if raw:
                rec["raw"] = str(raw)[:1500]
        self.calls.append(rec)
        out = reply if isinstance(reply, dict) and status == "ok" else {
            "orders": [], "note": f"no usable reply ({status}): {(error or '')[:120]}"}
        if self.on_step is not None:
            self.on_step({"step": obs.get("step"), "day": obs.get("day"), "observation_sha256": digest,
                          "reply": out, "call": rec})
        return out


TRANSPORT_HINTS = ("HTTP", "Timeout", "ConnectionError", "ConnectTimeout", "ReadTimeout", "SSLError", "RemoteDisconnected",
                   "RequestException", "TimeoutExpired", "not logged in", "rate limit", "RateLimit", "APITimeout",
                   "APIConnection", "InternalServer", "ServiceUnavailable", "Error code: 429", "Error code: 5",
                   "overloaded", "temporarily")


class LabStopped(BaseException):
    """Raised by a provider to end the lab at once (a spent quota or budget): it is not a reply to hold on. Runs
    already saved stay; the interrupted one continues from its recorded steps next time."""


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
    api_seed: bool = False  # also send the seed as the API's sampling seed (off: the seed only sets the aliases)
    costs: str = "fixed"  # the backtest's cost model: fixed (5 bps a fill) or realistic (docs/costs.md)
    base_url: str | None = None
    key_env: str | None = None  # the environment variable with the API key (compat: and openai: models)
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


def steps_path(lab: Path, variant: str, period: str, seed: int) -> Path:
    """Every answered step of one run, kept outside the run folder so that a run cut short can be replayed."""
    return Path(lab) / "steps" / variant / f"{period}-s{seed}.jsonl"


def _read_lines(path: Path) -> list[dict[str, Any]]:
    out = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:  # a line cut by a kill: the step is asked again
                break
    return out


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
    if folder.exists():  # an interrupted run: start it again (its recorded steps are replayed)
        shutil.rmtree(folder)
    folder.parent.mkdir(parents=True, exist_ok=True)
    steps_file = steps_path(lab, variant.name, str(period), int(seed))
    recorded = _read_lines(steps_file)
    lock = threading.Lock()

    def on_step(row: dict) -> None:
        with lock:
            steps_file.parent.mkdir(parents=True, exist_ok=True)
            with open(steps_file, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, default=str) + "\n")
            with open(lab / "progress.jsonl", "a", encoding="utf-8") as fh:  # one line per model call: progress
                fh.write(json.dumps({"variant": variant.name, "period": str(period), "seed": int(seed),
                                     "step": row["step"], "status": row["call"]["status"]}) + "\n")
    prov = provider if provider is not None else make_provider_for(settings.llm, settings.temperature,
                                                                   settings.base_url, settings.max_tokens,
                                                                   settings.key_env)
    tune(prov, variant, int(seed) if settings.api_seed else None)
    policy = VariantPolicy(variant, prov, recorded=recorded, on_step=on_step)
    exp = Experiment(period=get_period(period).name, universe=None if settings.symbols else settings.universe,
                     symbols=settings.symbols, news=settings.news, max_positions=settings.max_positions,
                     start=settings.start, end=settings.end, seed=int(seed), label=f"prompt-{variant.name}",
                     costs=settings.costs)
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
                  "api_seed": int(seed) if settings.api_seed else None,
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
    if settings.costs != "fixed":
        parts.append(f"--costs {settings.costs}")
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
        from .prompt_report import collect

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
                                                     "blind", "news", "max_tokens", "start", "end", "api_seed",
                                                     "costs")}
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
        futures = [pool.submit(one, j) for j in jobs]
        try:
            for fut in as_completed(futures):
                fut.result()
        except BaseException:  # a spent quota (LabStopped) or an interrupt: start nothing new, keep what is saved
            for fut in futures:
                fut.cancel()
            raise
    return lab


def import_run(folder: str | Path, lab: str | Path, variant: Variant, seed: int, note: str = "",
               tokens: tuple[int, int] | None = None) -> Path:
    """Bring a finished ``run_policy`` record made outside the lab (a pilot, say) into it as one run of ``variant``.

    The caller vouches that the run used the variant's text and the lab's settings. The record is copied (the
    original stays as it is), the manifest gets the prompt's hashes and the original folder, and ``calls.jsonl`` is
    rebuilt from ``policy_log.json``; ``tokens`` (input, output) for the whole run, when known, are spread evenly over
    its steps."""
    src = Path(folder).expanduser()
    if not (src / "manifest.json").is_file() or not (src / "policy_log.json").is_file():
        raise FileNotFoundError(f"{src} is not a finished policy run (manifest.json and policy_log.json)")
    man = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    period = get_period(man.get("experiment", {}).get("period")).name
    dst = run_folder(Path(lab), variant.name, period, int(seed))
    if _done(dst):
        return dst
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    log = json.loads((dst / "policy_log.json").read_text(encoding="utf-8"))
    if int(log.get("max_news", variant.max_news)) != variant.max_news or \
            int(log.get("decide_every", variant.decide_every)) != variant.decide_every:
        shutil.rmtree(dst)
        raise ValueError(f"{src}: max_news or decide_every differ from the variant {variant.name}")
    steps = log.get("steps", [])
    share = [(t // max(1, len(steps)), t % max(1, len(steps))) for t in tokens] if tokens else None
    with open(dst / "calls.jsonl", "w", encoding="utf-8") as fh:
        for i, st in enumerate(steps):
            note_ = str(st.get("note") or "")
            status = classify(None, note_) if note_.startswith("no usable reply") else "ok"
            tin, tout = ((q + (1 if i < r else 0)) for q, r in share) if share else (None, None)
            fh.write(json.dumps({"step": st.get("step"), "day": st.get("day"), "status": status, "seconds": None,
                                 "prompt_tokens": tin, "completion_tokens": tout, "imported": True}) + "\n")
    (dst / "prompt.txt").write_text(variant.text + "\n", encoding="utf-8")
    man["prompt"] = {**variant.describe(), "file": "prompt.txt", "schema": variant.schema}
    man.setdefault("llm", {})["seed"] = int(seed)
    man["imported_from"] = {"folder": str(src), "note": note}
    (dst / "manifest.json").write_text(json.dumps(man, indent=2, default=str) + "\n", encoding="utf-8")
    return dst


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def variants_table() -> list[dict[str, Any]]:
    return [v.describe() for v in VARIANTS.values()]


__all__ = ["VARIANTS", "Variant", "LabSettings", "BASELINE", "LabStopped", "get_variant", "select", "from_file", "tune",
           "VariantPolicy", "OllamaChat", "run_one", "run_lab", "import_run", "estimate", "variants_table"]
