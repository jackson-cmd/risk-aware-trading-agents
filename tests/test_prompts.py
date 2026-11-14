"""Prompt tests: the variant set, the prompt hashes in the records, the whole lab with a fake model, the statistics."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from riskaware.news import history
from riskaware.research import agent
from riskaware.research import prompts as P

REAL_NEWS = Path(history.__file__).parent / "data" / "news_2026.jsonl.gz"
WINDOW = {"start": "2026-02-02", "end": "2026-04-30"}


@pytest.fixture(autouse=True)
def bundled_news(monkeypatch):
    monkeypatch.setattr(history, "BUNDLE_PATH", REAL_NEWS)


class FakeModel:
    """Buys one of the strongest stocks; which one depends on the prompt and the seed. Fails the format once for
    risk_rules, so the error count has something to count."""

    def __init__(self, seed: int):
        self.seed, self.last_usage, self.last_error, self.last_raw = seed, {}, None, None
        self.systems: list[str] = []

    def complete_json(self, system, user, schema):
        self.systems.append(system)
        obs = json.loads(user.split("\n", 1)[1])
        self.last_usage = {"prompt_tokens": (len(system) + len(user)) // 4, "completion_tokens": 50}
        if obs["step"] == 2 and "Risk rules" in system:
            self.last_error, self.last_raw = "the reply was not a JSON object", "I would buy S01."
            return None
        ranked = sorted(obs["stocks"], key=lambda s: -(s.get("ret_21d") or 0))
        pick = ranked[(len(system) + self.seed) % 3]["symbol"]
        out = {"orders": [{"symbol": pick, "action": "target", "weight": 0.3, "fraction": 0, "reason": "trend"},
                          {"symbol": "ZZZ", "action": "buy", "weight": 0.1, "fraction": 0, "reason": "typo"}],
               "note": "t"}
        return {"reasoning": "short", **out} if "reasoning" in schema["properties"] else out


# ----------------------------------------------------------------------------------- the variant set
def test_every_factor_of_the_study_has_registered_variants():
    factors = {v.factor for v in P.VARIANTS.values()}
    assert {"baseline", "role", "risk_rules", "reasoning", "focus", "few_shot", "format", "news_count",
            "frequency"} <= factors
    assert {"role_pm", "role_quant", "role_risk", "focus_news", "focus_trend", "news_0", "news_6",
            "daily"} <= set(P.VARIANTS)
    assert P.VARIANTS["news_0"].max_news == 0 and P.VARIANTS["news_6"].max_news == 6
    assert P.VARIANTS["daily"].decide_every == 1 and P.VARIANTS["baseline"].decide_every == 5
    assert {v.response_format for v in P.VARIANTS.values()} == set(P.FORMATS)


def test_baseline_is_the_package_prompt_and_each_variant_changes_one_factor():
    base = P.VARIANTS["baseline"]
    assert base.text == agent.SYSTEM_PROMPT and base.schema == agent.ORDER_SCHEMA
    assert base.prompt_sha256 == hashlib.sha256(agent.SYSTEM_PROMPT.encode()).hexdigest()
    for v in P.VARIANTS.values():
        if v is base:
            continue
        changed = [k for k, x in v.settings().items() if x != base.settings()[k]]
        changed += ["text"] if v.text != base.text and not v.reasoning else []
        assert len(changed) == 1, (v.name, changed)  # reasoning changes the text and the schema together
        assert v.about and v.about_zh
    assert len({v.sha256 for v in P.VARIANTS.values()}) == len(P.VARIANTS)
    assert P.VARIANTS["news_0"].prompt_sha256 == base.prompt_sha256 != P.VARIANTS["news_0"].sha256


def test_tune_sends_the_seed_and_the_reply_format():
    from riskaware.llm import llm_config
    from riskaware.llm.openai_compat import OpenAICompatProvider

    def body(name, seed=7):
        prov = P.tune(OpenAICompatProvider(llm_config("ollama:qwen2.5:3b")), P.VARIANTS[name], seed)
        return prov.request_body("SYS", "USER", P.VARIANTS[name].schema)

    b = body("baseline")
    assert b["seed"] == 7 and b["response_format"]["type"] == "json_schema" and "JSON schema" in b["messages"][0]["content"]
    assert body("format_json")["response_format"] == {"type": "json_object"}
    t = body("format_text")
    assert "response_format" not in t and t["messages"][0]["content"] == "SYS"
    assert body("reason_first")["response_format"]["json_schema"]["schema"]["required"][0] == "reasoning"
    o = P.tune(P.OllamaChat("qwen2.5:3b"), P.VARIANTS["format_text"], 3)
    ob = o.body("SYS", "USER", P.VARIANTS["format_text"].schema)
    assert ob["options"]["num_ctx"] == 16384 and ob["options"]["seed"] == 3 and "format" not in ob
    assert o.url.endswith("/api/chat")


def test_a_custom_prompt_file_is_one_more_variant(tmp_path):
    f = tmp_path / "my prompt.txt"
    f.write_text("You trade carefully. Reply with JSON.\n")
    v = P.from_file(f)
    assert v.name == "my_prompt" and v.factor == "custom" and v.text == "You trade carefully. Reply with JSON."
    with pytest.raises(ValueError):
        P.from_file(f, name="baseline")


# ----------------------------------------------------------------------------------- the lab, with a fake model
def test_lab_with_a_fake_model_runs_records_and_reports(tmp_path):
    st = P.LabSettings(llm="fake:model", periods=["2026"], seeds=[0, 1], universe="core", max_positions=3,
                       price_in=0.2, price_out=1.2, **WINDOW)
    chosen = P.select("baseline,risk_rules,reason_first")
    made = []

    def factory(v, s):
        made.append(FakeModel(s))
        return made[-1]

    lab = P.run_lab(chosen, st, tmp_path / "lab", workers=3, provider_factory=factory, check=False)
    folder = P.run_folder(lab, "risk_rules", "2026", 0)
    man = json.loads((folder / "manifest.json").read_text())
    assert man["prompt"]["prompt_sha256"] == P.VARIANTS["risk_rules"].prompt_sha256
    assert man["prompt"]["variant_sha256"] == P.VARIANTS["risk_rules"].sha256
    assert man["llm"]["seed"] == 0 and man["experiment"]["seed"] == 0
    assert (folder / "prompt.txt").read_text().strip() == P.VARIANTS["risk_rules"].text
    assert "--variants risk_rules" in man["reproduce"]
    calls = [json.loads(x) for x in (folder / "calls.jsonl").read_text().splitlines()]
    assert [c["status"] for c in calls].count("format") == 1 and calls[1]["raw"] == "I would buy S01."
    row = P.read_run(folder)
    assert row["format_errors"] == 1 and row["invalid_orders"] == row["orders"] > 0  # one typo per good reply
    assert row["invalid_order_rate"] == 0.5

    r = P.report(lab, site=tmp_path / "site" / "prompts.json")
    assert {v["name"] for v in r["variants"]} == {"baseline", "risk_rules", "reason_first"}
    c = next(v for v in r["variants"] if v["name"] == "risk_rules")["vs_baseline"]["sharpe"]
    assert c["pairs"] == 2 and c["verdict"] == "noise" and c["p_holm"] >= c["p"]
    for f in ("prompts.json", "prompts.md", "prompts.png"):
        assert (lab / f).is_file()
    assert "Δ Sharpe vs baseline" in (lab / "prompts.md").read_text()
    site = json.loads((tmp_path / "site" / "prompts.json").read_text())
    assert site["format"] == "rta-prompt-board-1" and site["baseline"] == "baseline"
    assert {"name", "factor", "about", "prompt_sha256", "metrics", "vs_baseline"} <= set(site["variants"][0])
    assert site["cost"]["calls"] == sum(v["steps"] for v in r["variants"])

    # a second run of the same lab only does what is missing
    again = []
    P.run_lab(chosen, st, lab, workers=2, provider_factory=factory, check=False, progress=again.append)
    assert all(x.get("skipped") for x in again) and len(again) == 6
    with pytest.raises(ValueError):  # same folder, other settings
        P.run_lab(chosen, P.LabSettings(**{**st.to_dict(), "max_positions": 4}), lab, provider_factory=factory)


def test_a_policy_run_passes_the_look_ahead_check(tmp_path):
    st = P.LabSettings(llm="fake:model", periods=["2026"], seeds=[0], universe="core", max_positions=3, **WINDOW)
    out = P.run_one(P.VARIANTS["news_6"], "2026", 0, st, tmp_path, provider=FakeModel(0), check=True)
    assert out["leak_check"] == "pass"
    log = json.loads((Path(out["folder"]) / "policy_log.json").read_text())
    assert log["max_news"] == 6 and log["blind"] is True


# ----------------------------------------------------------------------------------- statistics
def test_sign_flip_holm_and_bootstrap():
    assert P.sign_flip_p(np.array([1.0, 1.0, 1.0])) == pytest.approx(2 / 8)
    assert P.sign_flip_p(np.array([1.0, -1.0])) == 1.0
    assert P.sign_flip_p(np.ones(9)) == pytest.approx(2 / 512)
    adj = P.holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert adj["a"] == pytest.approx(0.03) and adj["c"] == pytest.approx(0.06) and adj["b"] == pytest.approx(0.06)
    lo, hi = P.bootstrap_ci(np.array([0.1, 0.2, 0.3, 0.4]), np.array(["a", "a", "b", "b"]))
    assert 0.1 <= lo < 0.25 < hi <= 0.4


def test_compare_calls_a_consistent_large_gain_better_and_a_small_one_noise():
    rows = []
    rng = np.random.default_rng(0)
    for p in ("2026", "2022", "2020"):
        for s in range(4):
            b = float(rng.normal())
            rows.append({"variant": "baseline", "period": p, "seed": s, "sharpe": b})
            rows.append({"variant": "good", "period": p, "seed": s, "sharpe": b + 1.0 + 0.05 * s})
            rows.append({"variant": "meh", "period": p, "seed": s, "sharpe": b + (0.1 if s % 2 else -0.1)})
    out = P.compare(rows, "sharpe")
    assert out["good"]["verdict"] == "better" and out["good"]["same_sign_periods"] == 3
    assert out["meh"]["verdict"] == "noise" and out["good"]["pairs"] == 12


# ----------------------------------------------------------------------------------- the command line
def test_cli_lists_variants_and_estimates_a_lab(tmp_path):
    from riskaware.research.cli import app

    r = CliRunner().invoke(app, ["prompts", "list", "--json"])
    assert r.exit_code == 0, r.output
    names = [v["name"] for v in json.loads(r.output)]
    assert names[0] == "baseline" and len(names) == len(P.VARIANTS)
    r = CliRunner().invoke(app, ["prompts", "list", "--show", "risk_rules"])
    assert r.exit_code == 0 and "Risk rules" in r.output
    r = CliRunner().invoke(app, ["prompts", "run", "--llm", "ollama:qwen2.5:3b", "--variants", "baseline,daily",
                                 "--periods", "2026", "--seeds", "2", "--dry-run"])
    assert r.exit_code == 0, r.output
    assert "4 runs" in r.output and "2 variants" in r.output
    est = P.estimate(P.select("baseline,daily"), P.LabSettings(llm="x:y", periods=["2026"], seeds=[0]))
    by = {v["variant"]: v["calls"] for v in est["variants"]}
    assert by["daily"] > 4 * by["baseline"]
