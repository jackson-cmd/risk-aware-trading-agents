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
from riskaware.research import prompt_report as R
from riskaware.research import prompts as P

REAL_NEWS = history.period_news_path("news_2026.jsonl.gz")  # your copy (not redistributed), if any
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
    assert {"baseline", "role", "risk_rules", "risk_rule_part", "cash_level", "decision_rule", "reasoning", "focus",
            "few_shot", "format", "news_count", "frequency"} <= factors
    assert {"role_pm", "role_quant", "role_risk", "focus_news", "focus_trend", "news_0", "news_6", "daily",
            "trend_news", "cap_25", "regime_cash", "stop_loss", "above_200", "cash_30", "cash_50"} <= set(P.VARIANTS)
    assert set(P.FACTORS) == factors - {"baseline"}
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


def test_risk_rules_are_the_four_parts_in_the_same_words():
    text = P.VARIANTS["risk_rules"].text
    for name in P.RULE_PARTS:
        part = P.VARIANTS[name].text[len(agent.SYSTEM_PROMPT + P.RULE):]
        assert part.rstrip(".") in text, name
    assert P.VARIANTS["trend_news"].text == agent.SYSTEM_PROMPT + P.TREND_NEWS


def test_tune_sends_the_seed_and_the_reply_format():
    from riskaware.llm import llm_config
    from riskaware.llm.openai_compat import OpenAICompatProvider

    def body(name, seed=7):
        prov = P.tune(OpenAICompatProvider(llm_config("ollama:qwen2.5:3b")), P.VARIANTS[name], seed)
        return prov.request_body("SYS", "USER", P.VARIANTS[name].schema)

    b = body("baseline")
    assert b["seed"] == 7 and b["response_format"]["type"] == "json_schema" and "JSON schema" in b["messages"][0]["content"]
    assert b["stream"] is False and "seed" not in body("baseline", seed=None)
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
    f.write_text("You trade carefully. Reply with JSON.\n", encoding="utf-8")
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
    man = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert man["prompt"]["prompt_sha256"] == P.VARIANTS["risk_rules"].prompt_sha256
    assert man["prompt"]["variant_sha256"] == P.VARIANTS["risk_rules"].sha256
    assert man["llm"]["seed"] == 0 and man["experiment"]["seed"] == 0
    assert (folder / "prompt.txt").read_text(encoding="utf-8").strip() == P.VARIANTS["risk_rules"].text
    assert "--variants risk_rules" in man["reproduce"]
    calls = [json.loads(x) for x in (folder / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [c["status"] for c in calls].count("format") == 1 and calls[1]["raw"] == "I would buy S01."
    row = R.read_run(folder)
    assert row["format_errors"] == 1 and row["invalid_orders"] == row["orders"] > 0  # one typo per good reply
    assert row["invalid_order_rate"] == 0.5

    r = R.report(lab, site=tmp_path / "site" / "prompts.json")
    assert {v["name"] for v in r["variants"]} == {"baseline", "risk_rules", "reason_first"}
    c = next(v for v in r["variants"] if v["name"] == "risk_rules")["vs_baseline"]["sharpe"]
    assert c["pairs"] == 2 and c["verdict"] == "noise" and c["p_holm"] >= c["p"]
    for f in ("prompts.json", "prompts.md", "prompts.png"):
        assert (lab / f).is_file()
    assert "Δ Sharpe vs baseline" in (lab / "prompts.md").read_text(encoding="utf-8")
    site = json.loads((tmp_path / "site" / "prompts.json").read_text(encoding="utf-8"))
    assert site["format"] == "rta-prompt-board-1" and site["baseline"] == "baseline" and site["across_models"] is None
    b = site["boards"][0]
    assert b["model"] == "fake:model" and b["variants"][0]["name"] == "baseline" and b["variants"][0]["vs_baseline"] is None
    assert {"name", "factor", "about", "prompt_sha256", "metrics", "vs_baseline", "attribution"} <= set(b["variants"][1])
    assert b["cost"]["calls"] == sum(v["steps"] for v in r["variants"])

    at = r["attribution"]["risk_rules"]
    assert at["pairs"] == 2 and abs(at["total"] - at["exposure"] - at["selection"] - at["residual"]) < 1e-9
    assert "Where the return difference comes from" in (lab / "prompts.md").read_text(encoding="utf-8")

    # a run made elsewhere joins a lab as one more seed, copied, with the prompt's hashes
    other = P.start_lab(tmp_path / "other", chosen, P.LabSettings(**{**st.to_dict(), "seeds": [5]}))
    dst = P.import_run(folder, other, P.VARIANTS["risk_rules"], 5, note="from the first lab")
    m2 = json.loads((dst / "manifest.json").read_text(encoding="utf-8"))
    assert m2["imported_from"]["folder"] == str(folder) and m2["prompt"]["name"] == "risk_rules"
    assert R.read_run(dst)["seed"] == 5 and (folder / "manifest.json").is_file()
    with pytest.raises(ValueError):  # the record's settings do not match the variant
        P.import_run(P.run_folder(lab, "baseline", "2026", 0), other, P.VARIANTS["news_6"], 5)

    # two labs (two models) make one board with a comparison across them
    lab2 = P.run_lab(chosen, P.LabSettings(**{**st.to_dict(), "llm": "fake:other"}), tmp_path / "lab2", workers=3,
                     provider_factory=lambda v, s: FakeModel(s + 7), check=False)
    both = R.board([lab, lab2], site=tmp_path / "site" / "both.json")
    assert [x["model"] for x in both["boards"]] == ["fake:model", "fake:other"]
    ac = both["across_models"]
    assert ac["models"] == ["fake:model", "fake:other"]
    assert next(v for v in ac["variants"] if v["name"] == "risk_rules")["vs_baseline"]["sharpe"]["pairs"] == 4

    # a second run of the same lab only does what is missing
    again = []
    P.run_lab(chosen, st, lab, workers=2, provider_factory=factory, check=False, progress=again.append)
    assert all(x.get("skipped") for x in again) and len(again) == 6
    with pytest.raises(ValueError):  # same folder, other settings
        P.run_lab(chosen, P.LabSettings(**{**st.to_dict(), "max_positions": 4}), lab, provider_factory=factory)


class Quota(FakeModel):
    """Stops the lab after a number of calls, like a spent daily quota."""

    left = 0

    def complete_json(self, system, user, schema):
        if Quota.left <= 0:
            raise P.LabStopped("quota spent")
        Quota.left -= 1
        return super().complete_json(system, user, schema)


def test_a_lab_stopped_by_a_quota_resumes_by_replaying_its_steps(tmp_path):
    st = P.LabSettings(llm="fake:model", periods=["2026"], seeds=[0], universe="core", max_positions=3, **WINDOW)
    chosen = P.select("baseline,news_6")
    Quota.left = 5
    with pytest.raises(P.LabStopped):
        P.run_lab(chosen, st, tmp_path / "lab", workers=1, provider_factory=lambda v, s: Quota(s), check=False)
    assert not R.collect(tmp_path / "lab") and len((tmp_path / "lab" / "progress.jsonl").read_text(encoding="utf-8").splitlines()) == 5
    Quota.left = 1000
    P.run_lab(chosen, st, tmp_path / "lab", workers=1, provider_factory=lambda v, s: Quota(s), check=False)
    rows = R.collect(tmp_path / "lab")
    assert len(rows) == 2 and all(r["format_errors"] == 0 for r in rows)
    calls = [json.loads(x) for x in (P.run_folder(tmp_path / "lab", "baseline", "2026", 0) / "calls.jsonl")
             .read_text(encoding="utf-8").splitlines()]
    assert sum(1 for c in calls if c.get("replayed")) == 5
    assert len((tmp_path / "lab" / "progress.jsonl").read_text(encoding="utf-8").splitlines()) == sum(r["steps"] for r in rows)
    # the same lab again with a model that would answer differently: nothing is asked, the records stay
    assert 1000 - Quota.left == sum(r["steps"] for r in rows) - 5


def test_a_policy_run_passes_the_look_ahead_check(tmp_path):
    st = P.LabSettings(llm="fake:model", periods=["2026"], seeds=[0], universe="core", max_positions=3, **WINDOW)
    out = P.run_one(P.VARIANTS["news_6"], "2026", 0, st, tmp_path, provider=FakeModel(0), check=True)
    assert out["leak_check"] == "pass"
    log = json.loads((Path(out["folder"]) / "policy_log.json").read_text(encoding="utf-8"))
    assert log["max_news"] == 6 and log["blind"] is True


def test_attribution_puts_a_lower_exposure_in_the_exposure_part(tmp_path):
    import pandas as pd

    days = pd.bdate_range("2026-02-02", periods=40)
    rng = np.random.default_rng(1)
    stock = rng.normal(-0.004, 0.02, len(days))  # a falling market
    cash = R.cash_returns("2026").reindex(days).fillna(0.0).to_numpy()

    def write(folder, expo):
        e = np.full(len(days), expo)  # the same exposure every day: no timing
        prev = np.r_[0.0, e[:-1]]
        r = prev * stock + (1 - prev) * cash
        eq = 100_000 * np.cumprod(1 + r)
        ew = 100_000 * np.cumprod(1 + stock)
        folder.mkdir()
        pd.DataFrame({"date": days.date, "equity": eq, "spy": eq, "equal_weight": ew, "cash_ratio": 1 - e,
                      "exposure": e, "positions": 3, "regime": "NEUTRAL"}).to_csv(folder / "equity.csv", index=False)
        return folder

    full, half = write(tmp_path / "full", 1.0), write(tmp_path / "half", 0.5)
    a = R.attribution(half, full, "2026")
    assert a["total"] > 0.02 and abs(a["exposure"] - a["total"]) < 1e-9
    assert abs(a["selection"]) < 1e-9 and abs(a["exposure_timing"]) < 1e-9 and a["days"] == len(days) - 1
    assert a["exposure_variant"] < a["exposure_baseline"]


def test_a_capped_prompt_is_flagged_as_possibly_cut():
    calls = [{"prompt_tokens": 2050, "prompt_chars": 11_000 + 300 * i} for i in range(10)]
    assert R.truncated(calls, 2050 * 10 / sum(c["prompt_chars"] for c in calls))
    fine = [{"prompt_tokens": 2700 + 80 * i, "prompt_chars": 11_000 + 300 * i} for i in range(10)]
    assert not R.truncated(fine, 0.24)
    assert R.truncated(fine[:3], 0.05)


# ----------------------------------------------------------------------------------- statistics
def test_sign_flip_holm_and_bootstrap():
    assert R.sign_flip_p(np.array([1.0, 1.0, 1.0])) == pytest.approx(2 / 8)
    assert R.sign_flip_p(np.array([1.0, -1.0])) == 1.0
    assert R.sign_flip_p(np.ones(9)) == pytest.approx(2 / 512)
    adj = R.holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert adj["a"] == pytest.approx(0.03) and adj["c"] == pytest.approx(0.06) and adj["b"] == pytest.approx(0.06)
    lo, hi = R.bootstrap_ci(np.array([0.1, 0.2, 0.3, 0.4]), np.array(["a", "a", "b", "b"]))
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
    out = R.compare(rows, "sharpe")
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
