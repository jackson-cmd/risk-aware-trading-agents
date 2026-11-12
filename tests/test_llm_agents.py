import pytest
import requests
from typer.testing import CliRunner

import riskaware.llm as llm_pkg
from riskaware.arena import AgentSpec, Arena
from riskaware.cli import app
from riskaware.config import LLMConfig
from riskaware.journal import Journal
from riskaware.llm import LLMReviewer, llm_config, parse_choice
from riskaware.llm.openai_compat import OpenAICompatProvider, is_reasoning_model
from riskaware.types import Action, Signal

from .test_arena import data_factory, fake_quotes

GOOD = {"bull_case": "Price sits on long-term support.", "bear_case": "No catalyst in the evidence.",
        "adjustment": 0.4, "veto": False, "veto_reason": "", "summary": "Modest support for the buy."}


class FakeProvider:
    name = "fake"
    model = "fake-1"

    def __init__(self, reply=None, fail=False):
        self.reply = GOOD if reply is None else reply
        self.fail = fail
        self.calls = 0
        self.last_raw = None
        self.last_error = None
        self.last_usage = {}

    def complete_json(self, system, user, schema):
        self.calls += 1
        if self.fail:
            raise RuntimeError("model is down")
        self.last_raw = '{"bull_case": "..."}'
        self.last_usage = {"total_tokens": 321}
        return dict(self.reply)


class Market:
    regime = "NEUTRAL"
    vix, fear_greed, event_risk = 17.0, 45.0, 0.1
    reasons = ["VIX 17"]


def test_parse_choice_and_llm_config():
    assert parse_choice("openai:gpt-5.6-luna") == ("openai", "gpt-5.6-luna")
    assert parse_choice("ollama:qwen2.5:3b") == ("ollama", "qwen2.5:3b")
    assert parse_choice("ollama") == ("ollama", "qwen2.5:3b")
    with pytest.raises(ValueError):
        parse_choice("gemini:pro")
    c = llm_config("openai:gpt-5.6-luna")
    assert c.enabled and c.provider == "openai" and c.api_key_env == "OPENAI_API_KEY" and c.reasoning_effort == "none"
    o = llm_config("ollama:qwen2.5:3b")
    assert o.base_url == "http://localhost:11434/v1" and o.timeout >= 180 and o.reasoning_effort is None
    a = llm_config("anthropic:claude-opus-5-5")
    assert a.provider == "anthropic" and a.model == "claude-opus-5-5"
    with pytest.raises(ValueError):
        llm_config("compat:deepseek-chat")  # needs a base_url
    d = llm_config("compat:deepseek-chat", base_url="https://api.deepseek.com/v1", key_env="DEEPSEEK_API_KEY")
    assert d.base_url.startswith("https://api.deepseek") and d.api_key_env == "DEEPSEEK_API_KEY"


def test_request_body_for_reasoning_and_chat_models():
    sys_, user, schema = "s", "u", {"type": "object"}
    luna = OpenAICompatProvider(llm_config("openai:gpt-5.6-luna")).request_body(sys_, user, schema)
    assert luna["reasoning_effort"] == "none" and "temperature" in luna and luna["max_completion_tokens"] == 1200
    assert "max_tokens" not in luna
    effort = LLMConfig(enabled=True, provider="openai", model="gpt-5.6-sol", reasoning_effort="low")
    sol = OpenAICompatProvider(effort).request_body(sys_, user, schema)
    assert sol["reasoning_effort"] == "low" and "temperature" not in sol
    local = OpenAICompatProvider(llm_config("ollama:qwen2.5:3b")).request_body(sys_, user, schema)
    assert "reasoning_effort" not in local and local["temperature"] == 0.2 and local["max_tokens"] == 1200
    assert is_reasoning_model("o4-mini") and not is_reasoning_model("qwen2.5:3b")


class Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code, self._payload, self.text = status, payload or {}, text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


def test_provider_drops_rejected_parameters_and_retries_a_timeout(monkeypatch):
    p = OpenAICompatProvider(llm_config("openai:gpt-5.6-luna"))
    sent = []
    replies = [requests.Timeout(), Resp(400, text="Unsupported parameter: 'temperature'"),
               Resp(200, {"choices": [{"message": {"content": '```json\n{"veto": false}\n```'}}],
                          "usage": {"total_tokens": 9}})]

    def post(url, headers, json, timeout):
        sent.append(dict(json))
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(p.session, "post", post)
    out = p.complete_json("s", "u", {"type": "object"})
    assert out == {"veto": False} and p.last_usage == {"total_tokens": 9}
    assert "temperature" in sent[1] and "temperature" not in sent[2]
    assert p.last_raw.startswith("```json")


def test_reviewer_keeps_a_record_of_every_review():
    cfg = LLMConfig(enabled=True, provider="openai", model="fake-1")
    rv = LLMReviewer(FakeProvider(), cfg)
    sig = Signal("ACME", Action.BUY, 0.6, "price at EMA200 support", setups=["ema200_support"])
    v = rv.review(sig, Market())
    assert v.adjustment == pytest.approx(0.15)  # clipped to the bound
    rv.review(sig, Market())  # same evidence: served from the cache, no second record
    recs = rv.pop_records()
    assert len(recs) == 1 and rv.records == []
    r = recs[0]
    assert r.symbol == "ACME" and r.model == "fake-1" and "ACME" in r.prompt and r.raw and r.error is None
    assert r.strength_before == pytest.approx(0.6) and r.usage == {"total_tokens": 321}
    broken = LLMReviewer(FakeProvider(fail=True), cfg)
    assert broken.review(sig, Market()) is None
    assert "model is down" in broken.pop_records()[0].error


def test_agent_spec_llm_and_config(tmp_path, offline_cfg):
    arena = Arena(tmp_path / "arena", base_cfg=offline_cfg)
    spec = arena.add(AgentSpec(name="gpt", llm="openai:gpt-5.6-luna"))
    c = arena.config_for(spec)
    assert c.llm.enabled and c.llm.model == "gpt-5.6-luna" and spec.llm_label == "gpt-5.6-luna"
    assert AgentSpec(name="x", llm="OFF").llm == "off"
    assert AgentSpec(name="x", llm="").llm is None
    with pytest.raises(ValueError):
        AgentSpec(name="x", llm="gemini:pro")
    off = arena.set_llm("gpt", "off")
    assert off.llm == "off" and not arena.config_for(off).llm.enabled
    local = arena.set_llm("gpt", "ollama:qwen2.5:3b")
    assert arena.config_for(local).llm.base_url == "http://localhost:11434/v1"
    assert arena.leaderboard()[0]["llm"] == "qwen2.5:3b"


def test_llm_reviews_reach_the_journal_and_the_decisions(tmp_path, offline_cfg, monkeypatch):
    monkeypatch.setattr(llm_pkg, "make_reviewer", lambda cfg: LLMReviewer(FakeProvider(), cfg))
    offline_cfg.profile_overrides = {"min_conviction": 0.0}
    arena = Arena(tmp_path / "arena", base_cfg=offline_cfg)
    arena.add(AgentSpec(name="judge", strategy="buy_and_hold", llm="openai:fake-1"))
    run = arena.run(quote_fn=fake_quotes, data_factory=data_factory)[0]
    assert run.ok, run.errors
    reviews = Journal(arena.dir("judge") / "journal.db").llm_reviews(50)
    buys = [s for s in run.report.signals if s.action == Action.BUY]
    assert len(reviews) == len(run.report.llm_reviews) == min(len(buys), offline_cfg.llm.max_symbols_per_run) > 0
    r = reviews[0]
    assert r["model"] == "fake-1" and r["bull"] and r["prompt"] and r["adjustment"] == pytest.approx(0.15)
    rows = arena.decisions("judge", with_prompts=True)
    with_llm = [d for d in rows if d["llm"]]
    assert with_llm and with_llm[0]["llm"]["summary"] and "prompt" in with_llm[0]["llm"]
    # the conviction stored with the decision is the one after the review
    assert with_llm[0]["strength"] == pytest.approx(min(1.0, with_llm[0]["llm"]["strength_before"] + 0.15))
    assert "prompt" not in [d for d in arena.decisions("judge") if d["llm"]][0]["llm"]


def test_cli_llm_commands(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(llm_pkg, "make_reviewer", lambda cfg: LLMReviewer(FakeProvider(), cfg))
    runner = CliRunner()
    res = runner.invoke(app, ["llm-check", "ollama:qwen2.5:3b"])
    assert res.exit_code == 0, res.output
    assert "bull" in res.output and "321 tokens" in res.output
    res = runner.invoke(app, ["agents", "add", "llm-cli", "--strategy", "rsi2", "--llm", "openai:gpt-5.6-luna"])
    assert res.exit_code == 0 and "gpt-5.6-luna" in res.output
    res = runner.invoke(app, ["agents", "llm", "llm-cli", "off"])
    assert res.exit_code == 0 and "off" in res.output
    res = runner.invoke(app, ["agents", "llm", "llm-cli", "gemini:pro"])
    assert res.exit_code == 1
    res = runner.invoke(app, ["agents", "why", "llm-cli"])
    assert res.exit_code == 0 and "No decisions yet" in res.output
    runner.invoke(app, ["agents", "remove", "llm-cli", "--yes"])


def test_mcp_agent_decisions(tmp_path, monkeypatch):
    from riskaware import mcp_server as m

    monkeypatch.chdir(tmp_path)
    m.agents_create("mcp-llm", strategy="rsi2", llm="ollama:qwen2.5:3b")
    out = m.agent_decisions("mcp-llm")
    assert out == {"agent": "mcp-llm", "decisions": []}
    assert m.agent_detail("mcp-llm")["agent"]["llm"] == "ollama:qwen2.5:3b"


def test_strict_schema_falls_back_to_json_object(monkeypatch):
    p = OpenAICompatProvider(llm_config("ollama:qwen2.5:3b"))
    body = p.request_body("s", "u", {"type": "object"})
    assert body["response_format"]["type"] == "json_schema" and body["response_format"]["json_schema"]["strict"]
    sent = []
    replies = [Resp(400, text="response_format json_schema is not supported"),
               Resp(200, {"choices": [{"message": {"content": '{"veto": true}'}}]})]

    def post(url, headers, json, timeout):
        sent.append(json["response_format"]["type"])
        return replies.pop(0)

    monkeypatch.setattr(p.session, "post", post)
    assert p.complete_json("s", "u", {"type": "object"}) == {"veto": True}
    assert sent == ["json_schema", "json_object"]


def test_parse_ignores_text_after_the_object():
    from riskaware.llm.openai_compat import parse_json_object

    looped = '{"bull_case": "x", "adjustment": 0.05, "veto": false} final.}马会.} final.}马会.} } } }'
    assert parse_json_object(looped) == {"bull_case": "x", "adjustment": 0.05, "veto": False}
    assert parse_json_object('Sure! ```json\n{"a": 1}\n``` done') == {"a": 1}
    assert parse_json_object("no json here") is None
    assert parse_json_object('{"bull_case": "cut off') is None
