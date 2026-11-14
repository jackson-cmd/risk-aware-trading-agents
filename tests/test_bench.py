"""rta-bench-1: tasks, training cutoffs, checking a run, entries that replay to the same numbers, the board."""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from riskaware.news import history
from riskaware.research import bench
from riskaware.research.agent import ORDER_SCHEMA, run_policy
from riskaware.research.experiment import Experiment
from tests.conftest import needs_news

REAL_NEWS = history.period_news_path("news_2026.jsonl.gz")  # your copy (not redistributed), if any
ROOT = Path(__file__).resolve().parent.parent
WINDOW = (date(2026, 2, 2), date(2026, 4, 30))


@pytest.fixture(autouse=True)
def small_bench(monkeypatch):
    """The benchmark on the nine stocks of `core` and three months of 2026, so a run takes seconds."""
    monkeypatch.setattr(history, "BUNDLE_PATH", REAL_NEWS)
    monkeypatch.setitem(bench.SETTINGS, "universe", "core")
    monkeypatch.setattr(bench, "window", lambda period: WINDOW if period == "2026" else (date(2022, 2, 1),
                                                                                         date(2022, 4, 29)))
    if not bench.costs_supported():
        monkeypatch.setitem(bench.SETTINGS, "costs", "fixed")


def momentum(obs: dict) -> dict:
    top = sorted(obs["stocks"], key=lambda s: s["ret_21d"] or -1, reverse=True)[:3]
    return {"orders": [{"symbol": s["symbol"], "action": "target", "weight": 0.3, "fraction": 0, "reason": "momentum"}
                       for s in top], "note": "top three by 21-day return"}


@pytest.fixture(scope="module")
def blind_run():
    """One saved 2026-blind run of a rule policy (module scope: the fixture above is applied by hand)."""
    mp = pytest.MonkeyPatch()
    mp.setattr(history, "BUNDLE_PATH", REAL_NEWS)
    mp.setitem(bench.SETTINGS, "universe", "core")
    mp.setattr(bench, "window", lambda period: WINDOW)
    if not bench.costs_supported():
        mp.setitem(bench.SETTINGS, "costs", "fixed")
    res = bench.run_task(momentum, "2026-blind", label="rules:momentum")
    yield Path(res.folder)
    mp.undo()


def test_tasks_and_training_cutoffs():
    assert bench.TASKS == ("2026-blind", "2026-named", "2022-blind", "2022-named", "2020-blind", "2020-named")
    t = bench.get_task("2022-Named")
    assert (t.period, t.blind, t.mode) == ("2022", False, "named")
    with pytest.raises(ValueError):
        bench.get_task("2021-blind")
    assert bench.cutoff_day("2025-02") == date(2025, 2, 28)
    assert bench.cutoff_day("2026-03-14") == date(2026, 3, 14)
    with pytest.raises(ValueError):
        bench.cutoff_day("March 2025")
    assert bench.contamination("2026", "2025-06") == "clean"
    assert bench.contamination("2026", "2026-03") == "partly"
    assert bench.contamination("2026", "2026-05") == "may_remember"
    assert bench.contamination("2026", None) == "unknown"


@needs_news  # a benchmark run needs the period's headline file
def test_a_run_becomes_an_entry_that_replays_to_the_same_numbers(blind_run, tmp_path):
    task, problems = bench.check_record(blind_run)
    assert problems == [] and task.name == "2026-blind"
    meta = json.loads((blind_run / "bench.json").read_text())
    assert meta["bench"] == bench.BENCH_VERSION and meta["task"] == "2026-blind"
    entry = bench.make_entry(blind_run, model="Rules: momentum", cutoff="2026-03", provider="none",
                             open_weights=True)
    assert entry["contamination"] == "partly" and entry["after_cutoff"]["from"] == "2026-03-31"
    assert entry["look_ahead_check"] == "pass" and entry["steps"] == len(entry["replies"]) > 5
    assert entry["metrics"]["total_return"] is not None and "excess_vs_equal_weight" in entry["metrics"]
    assert set(entry["record"]["files_sha256"]) >= {"manifest.json", "metrics.json", "policy_log.json"}
    assert entry["record"]["costs_seen_by_model"] == bench.SETTINGS["costs"] and entry["record"]["rescored_from"] is None
    assert entry["analysis"]["summary"]["decisions"] > 0 and "folder" not in entry["analysis"]
    path = bench.write_entry(entry, tmp_path / "entries")
    assert path.name == "rules-momentum-2026-blind-s0.json" and entry["seed"] == 0
    with pytest.raises(FileExistsError):
        bench.write_entry(entry, tmp_path / "entries")
    loaded = bench.load_entries(tmp_path / "entries")
    assert len(loaded) == 1 and bench.check_entry(loaded[0]) == []
    assert bench.verify_entry(loaded[0])["ok"]

    edited = copy.deepcopy(loaded[0])
    edited["replies"][1] = {"orders": [], "note": "changed"}
    assert any("replies" in p for p in bench.check_entry(edited))
    edited = copy.deepcopy(loaded[0])
    edited["metrics"]["total_return"] += 0.05
    out = bench.verify_entry(edited)
    assert not out["ok"] and any(p.startswith("total_return") for p in out["problems"])
    edited = copy.deepcopy(loaded[0])
    edited["contamination"] = "clean"
    assert any("contamination" in p for p in bench.check_entry(edited))


@needs_news  # a benchmark run needs the period's headline file
def test_each_seed_is_its_own_entry_and_replays_with_its_aliases(tmp_path):
    res = bench.run_task(momentum, "2026-blind", label="rules:momentum", seed=1)
    entry = bench.make_entry(res.folder, model="rules-momentum", cutoff="2025-06")
    assert entry["seed"] == 1 and bench.entry_name(entry) == "rules-momentum-2026-blind-s1.json"
    assert bench.verify_entry(entry)["ok"]
    wrong = copy.deepcopy(entry)
    wrong["seed"] = 0  # the same replies under seed 0's aliases are other stocks' orders
    wrong["record"]["replies_sha256"] = entry["record"]["replies_sha256"]
    assert not bench.verify_entry(wrong)["ok"]


@needs_news  # a benchmark run needs the period's headline file
def test_a_run_with_other_fill_costs_is_replayed_under_the_benchmarks():
    if not bench.costs_supported():
        pytest.skip("this version has no cost models")
    exp = bench.experiment("2026-named", label="rules:fixed")
    exp.costs = "fixed"
    res = run_policy(momentum, exp, decide_every=5, blind=False, max_news=3, label="rules:fixed")
    task, problems = bench.check_record(res.folder)
    assert task.name == "2026-named" and problems == ["costs is 'fixed', the benchmark uses 'realistic'"]
    entry = bench.make_entry(res.folder, model="rules-fixed", cutoff="2024-06")
    assert entry["record"]["rescored_from"] == res.run_id and entry["record"]["costs_seen_by_model"] == "fixed"
    assert entry["replies"] == json.loads((Path(res.folder) / "policy_log.json").read_text())["replies"]
    assert entry["metrics"]["total_return"] < res.metrics["total_return"]  # the same orders, dearer fills
    assert bench.verify_entry(entry)["ok"]


def test_a_run_with_other_settings_is_refused(tmp_path):
    exp = Experiment(period="2026", start=WINDOW[0].isoformat(), end=WINDOW[1].isoformat(), universe="core",
                     max_positions=5, label="other", mode="long_short")
    res = run_policy(momentum, exp, decide_every=10, blind=True, check=False)
    task, problems = bench.check_record(res.folder)
    assert task.name == "2026-blind"
    text = " ".join(problems)
    assert "max_positions" in text and "decide_every" in text and "look-ahead" in text and "mode" in text
    with pytest.raises(bench.BenchError):
        bench.make_entry(res.folder, model="x", cutoff="2025-01")


def _entry(model: str, task: str, cutoff: str, ret: float, sharpe: float, seed: int = 0) -> dict:
    return {"bench": bench.BENCH_VERSION, "task": task, "seed": seed, "model": {"name": model, "training_cutoff": cutoff},
            "metrics": {"total_return": ret, "sharpe": sharpe}, "vs_spy": {}, "replies": []}


def test_the_board_puts_clean_and_may_remember_apart():
    entries = [_entry("old", "2026-blind", "2025-01", 0.03, 0.2), _entry("old", "2026-blind", "2025-01", 0.07, 0.6, 1),
               _entry("old", "2026-named", "2025-01", 0.09, 0.8),
               _entry("new", "2026-blind", "2026-12", 0.20, 1.5), _entry("mid", "2026-blind", "2025-06", 0.10, 0.9)]
    anchors = [{"period": "2026", "name": "SPY", "kind": "buy and hold", "total_return": 0.04}]
    b = bench.board(entries, anchors)
    p = b["periods"]["2026"]
    assert [r["model"] for r in p["clean"]] == ["mid", "old"]  # by blind Sharpe
    assert [r["model"] for r in p["may_remember"]] == ["new"]
    old = next(r for r in p["clean"] if r["model"] == "old")
    assert old["blind"]["n"] == 2 and old["blind"]["seeds"] == [0, 1] and old["blind"]["total_return"] == 0.05
    assert old["blind"]["total_return_range"] == [0.03, 0.07] and old["named"]["total_return_range"] is None
    assert old["named_minus_blind"] == pytest.approx(0.04)
    assert p["anchors"] == anchors and b["models"] == 3 and b["entries"] == 5


@needs_news  # a benchmark run needs the period's headline file
def test_cli_submit_and_leaderboard(blind_run, tmp_path):
    from riskaware.research.cli import app

    runner = CliRunner()
    out = runner.invoke(app, ["submit", str(blind_run), "--model", "rules-momentum", "--cutoff", "2025-06",
                              "--out", str(tmp_path / "entries")])
    assert out.exit_code == 0, out.output
    assert (tmp_path / "entries" / "rules-momentum-2026-blind-s0.json").is_file()
    out = runner.invoke(app, ["leaderboard", "--dir", str(tmp_path), "--out", str(tmp_path / "board.json")])
    assert out.exit_code == 0, out.output
    assert "Entries: 1 · models: 1 · with problems: 0" in out.output
    b = json.loads((tmp_path / "board.json").read_text())
    assert b["periods"]["2026"]["clean"][0]["model"] == "rules-momentum"
    out = runner.invoke(app, ["submit", str(blind_run), "--model", "x", "--cutoff", "soon",
                              "--out", str(tmp_path / "entries")])
    assert out.exit_code == 2


def test_ollama_goes_to_the_native_api_with_a_long_context(monkeypatch):
    import requests

    sent = {}

    class Reply:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": '{"orders": [], "note": "hold"}'}, "prompt_eval_count": 9000,
                    "eval_count": 12}

    def post(self, url, json=None, timeout=None):  # noqa: A002 - requests' own keyword
        sent.update(url=url, body=json)
        return Reply()

    monkeypatch.setattr(requests.Session, "post", post)
    policy = bench.bench_policy("ollama:llama3.2:3b")
    reply = policy({"step": 1, "stocks": []})
    assert reply == {"orders": [], "note": "hold"}
    assert sent["url"] == "http://localhost:11434/api/chat"
    assert sent["body"]["options"]["num_ctx"] == bench.OLLAMA_NUM_CTX and sent["body"]["format"] == ORDER_SCHEMA
    assert policy.usage["input_tokens"] == 9000 and policy.usage["calls"] == 1


def test_shipped_entries_are_well_formed():
    """Every entry in leaderboard/entries passes the fast checks (CI replays them in the leaderboard workflow)."""
    folder = ROOT / "leaderboard" / "entries"
    if not folder.is_dir():
        pytest.skip("no leaderboard folder in this checkout")
    mp = pytest.MonkeyPatch()
    mp.setattr(bench, "window", lambda period: (bench.get_period(period).start, bench.get_period(period).end))
    mp.setitem(bench.SETTINGS, "universe", "megacap")
    mp.setitem(bench.SETTINGS, "costs", "realistic")
    try:
        for e in bench.load_entries(folder):
            assert bench.check_entry(e) == [], e["_file"]
            assert e["_file"] == bench.entry_name(e)
    finally:
        mp.undo()
