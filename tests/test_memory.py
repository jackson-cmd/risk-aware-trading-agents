"""The memory probe: a model that peeks at the outcome when it knows the name must score memory, one that does not
must not, and the blind records must hide the name and the date while using only the past."""

from __future__ import annotations

import json
import re
from datetime import date

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from riskaware.research import memory as M
from riskaware.research.data import bundled_market

NAMED = re.compile(r"^\[(a\d+)\] ([A-Z][A-Z.\-]*) \(.*\), close of (\d{4}-\d{2}-\d{2})$", re.M)
BLIND = re.compile(r"^\[(a\d+)\] S\d\d \(.*\), close of day 0$", re.M)
RECALL = re.compile(r"^\[(q\d+)\] ([A-Z][A-Z.\-]*).*: (\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})$", re.M)


def outcome(period: str, symbol: str, day: str, horizon: int = 20) -> tuple[float, float]:
    """What happened next, worked out here from the bundled prices (not from the module under test)."""
    mk = bundled_market(period)
    cal = list(mk.calendar)
    i = cal.index(pd.Timestamp(day))
    s, spy = mk.frame(symbol)["close"], mk.frame("SPY")["close"]
    a, b = cal[i], cal[i + horizon]
    return float(s[b] / s[a] - 1), float(spy[b] / spy[a] - 1)


class Fake:
    name = "fake"

    def __init__(self, model: str = "fake-1"):
        self.model = model
        self.calls = 0
        self.users: list[str] = []
        self.last_usage = {"prompt_tokens": 100, "completion_tokens": 10}
        self.last_error = None

    def complete_json(self, system, user, schema):
        self.calls += 1
        self.users.append(user)
        if "Items:" in user:
            return {"answers": [self.recall(m.group(1), m.group(2), m.group(3), m.group(4))
                                for m in RECALL.finditer(user)]}
        answers = [{"id": m.group(1), "p": self.named(m.group(2), m.group(3), user)} for m in NAMED.finditer(user)]
        answers += [{"id": m.group(1), "p": self.blind(user)} for m in BLIND.finditer(user)]
        return {"answers": answers}

    def recall(self, label, symbol, start, end):
        return {"id": label, "direction": "up", "change_pct": 1.0}


class Omniscient(Fake):
    """Knows how every named stock did; sees nothing in a blind record."""

    def named(self, symbol, day, user):
        ret, spy = outcome("2022", symbol, day)
        return 0.9 if ret > spy else 0.1

    def blind(self, user):
        return 0.5

    def recall(self, label, symbol, start, end):
        ret, _ = outcome("2022", symbol, start)
        return {"id": label, "direction": "up" if ret > 0 else "down", "change_pct": round(ret * 100, 1)}


class Chartist(Fake):
    """Reads only what both versions show (the 21-day return against SPY), so its two answers are the same."""

    def _p(self, user, label):
        block = user.split(f"[{label}]", 1)[1].split("\n\n", 1)[0]
        m = re.search(r"21-day return ([+-][\d.]+)% \(SPY ([+-][\d.]+)%\)", block)
        return float(np.clip(0.5 + (float(m.group(1)) - float(m.group(2))) / 40, 0.05, 0.95))

    def complete_json(self, system, user, schema):
        self.calls += 1
        if "Items:" in user:
            return {"answers": [{"id": m.group(1), "direction": "up", "change_pct": 2.0} for m in RECALL.finditer(user)]}
        labels = re.findall(r"^\[(a\d+)\]", user, re.M)
        return {"answers": [{"id": la, "p": self._p(user, la)} for la in labels]}


class CoinFlip(Fake):
    """Answers at random, named or not."""

    def __init__(self, model="coin"):
        super().__init__(model)
        self.rng = np.random.default_rng(7)

    def named(self, symbol, day, user):
        return float(self.rng.uniform(0.2, 0.8))

    def blind(self, user):
        return float(self.rng.uniform(0.2, 0.8))

    def recall(self, label, symbol, start, end):
        x = float(self.rng.normal(0, 8))
        return {"id": label, "direction": "up" if x > 0 else "down", "change_pct": x}


def probe(model, tmp_path, **kw):
    opts = dict(period="2022", pairs=40, recall=20, news=False, cache_dir=tmp_path / "cache", out_dir=tmp_path / "run")
    opts.update(kw)
    return M.memory_probe(model, **opts)


def test_an_omniscient_model_shows_memory(tmp_path):
    fake = Omniscient()
    res = probe(fake, tmp_path)
    s = res.summary
    g = s["paired"]["all"]
    assert g["pairs"] == 40 and g["named"]["accuracy"] == 1.0 and g["blind"]["accuracy"] == 0.5
    assert s["memory_score"] > 0.2 and s["memory_ci95"][0] > 0 and s["memory_p_value"] < 0.01
    assert s["verdict"] == "contaminated"
    r = s["recall"]["all"]
    assert r["direction_accuracy"] == 1.0 and r["rank_correlation"] > 0.99 and r["verdict"] == "recalls"
    # the record holds the summary, one row per point, and both reports
    saved = json.loads((tmp_path / "run" / "memory.json").read_text(encoding="utf-8"))
    assert saved["verdict"] == "contaminated" and saved["data"]["2022"]["prices_sha256"]
    assert len(pd.read_csv(tmp_path / "run" / "answers.csv")) == 40
    assert "Verdict: contaminated" in (tmp_path / "run" / "report.md").read_text(encoding="utf-8")
    assert "有记忆" in (tmp_path / "run" / "report_zh.md").read_text(encoding="utf-8")


def test_a_model_that_reads_only_the_data_shows_none(tmp_path):
    res = probe(Chartist("chartist"), tmp_path)
    g = res.summary["paired"]["all"]
    assert g["mean_abs_difference"] == 0.0  # the same information gives the same answer
    assert res.memory_score == 0.0 and res.verdict == "clean"


def test_a_guesser_shows_no_memory(tmp_path):
    res = probe(CoinFlip(), tmp_path, pairs=120, recall=40)
    lo, hi = res.summary["memory_ci95"]
    assert lo < 0 < hi and abs(res.memory_score) < 0.15  # two AUCs of random answers on 120 points
    assert res.verdict != "contaminated"
    assert res.summary["recall"]["all"]["verdict"] != "recalls"


def test_blind_records_hide_the_name_and_the_date():
    plan = M.plan_probe("ollama:llama3.2:3b", "2022", pairs=30, recall=0, cache=False)
    blind = [c for c in plan.calls if c.version == "blind"]
    named = [c for c in plan.calls if c.version == "named"]
    assert blind and named and len(plan.calls) == len(blind) + len(named)
    word = r"(?<![A-Za-z0-9]){}(?![A-Za-z0-9])"
    for c in blind:
        assert not re.search(r"(?<!\d)20(?:21|22)(?!\d)", c.user) and not re.search(r"\d{4}-\d{2}-\d{2}", c.user)
        blocks = {b.split("]", 1)[0].lstrip("["): b for b in c.user.split("\n\n")[1:]}
        for label, s in c.items:
            block = blocks[label]  # a record may quote another company; its own names must be gone
            names = [n for n in M.ALIASES.get(s.symbol, []) if len(n) > 2] + M.PEOPLE.get(s.symbol, [])
            assert not any(re.search(word.format(re.escape(n)), block, re.I) for n in names), (s.symbol, block)
            if len(s.symbol) > 1:
                assert not re.search(word.format(re.escape(s.symbol)), block), (s.symbol, block)
    assert any(s.day.isoformat() in c.user and s.symbol in c.user for c in named for _, s in c.items)


def test_masking_of_headlines():
    b = M.masker("TSLA", "S03")
    assert M.mask_headline("Elon Musk's Twitter Bid: Tesla Shares Fall On May 9, 2022", b, 1.0) == \
        "the S03 chief's Twitter Bid: S03 Shares Fall On [date]"
    assert M.mask_headline("Tesla Q3 2022 EPS $1.05 Beats $0.99 Estimate, Sales $21.45B Miss", b, 0.1) == \
        "S03 Q3 [year] EPS $0.105 Beats $0.099 Estimate, Sales $21.45B Miss"
    assert M.mask_headline("Goldman Raises Price Target to $1,100; FY22 Model Y Outlook", b, 0.1) == \
        "Goldman Raises Price Target to $110.0; FY [year] [product] Outlook"
    assert M.mask_headline("24/7 Wall St.: $5 billion buyback, 12% up", b, 0.5) == \
        "24/7 Wall St.: $5 billion buyback, 12% up"
    assert M.mask_headline("Tesla Adds 7M Cars In 2019, Plans For 2027 Or 2028", b, 1.0) == \
        "S03 Adds 7M Cars In [year], Plans For [year] Or [year]"


def test_records_use_only_the_past():
    """The same record on a market whose prices after the day are random: nothing may change."""
    from riskaware.research.day import MarketView

    view = M.market_view("2022", news=False)
    s = M.draw_samples("2022", 5, view=view)[0]
    poisoned = MarketView(market=view.market.poisoned(s.day, seed=3), news=False)
    for alias in (None, "S01"):
        assert M.render_record(view, s, "a1", alias) == M.render_record(poisoned, s, "a1", alias)
    # and the outcome really is different on the poisoned market, so the check has teeth
    assert view.market.frame(s.symbol)["close"][pd.Timestamp(s.end)] != \
        poisoned.market.frame(s.symbol)["close"][pd.Timestamp(s.end)]


def test_points_are_reproducible_and_have_their_outcome():
    a = M.draw_samples("2022", 25, seed=4)
    assert a == M.draw_samples("2022", 25, seed=4) and a != M.draw_samples("2022", 25, seed=5)
    assert len({s.key for s in a}) == 25
    for s in a[:5]:
        ret, spy = outcome("2022", s.symbol, s.day.isoformat())
        assert s.ret == pytest.approx(ret) and s.spy == pytest.approx(spy)
        assert date(2022, 1, 3) <= s.day < s.end <= date(2022, 12, 30)


def test_answers_are_cached_per_model_and_record(tmp_path):
    first = Omniscient("cache-me")
    probe(first, tmp_path, pairs=12, recall=6)
    assert first.calls > 0
    again = Omniscient("cache-me")
    res = probe(again, tmp_path, pairs=12, recall=6)
    assert again.calls == 0 and res.summary["calls"]["answers_from_cache"] == 30
    more = Omniscient("cache-me")
    probe(more, tmp_path, pairs=14, recall=6)  # only the two new points are asked, named and blind
    assert sum(len(re.findall(r"^\[a\d+\]", u, re.M)) for u in more.users) == 4
    other = Omniscient("another-model")
    probe(other, tmp_path, pairs=12, recall=6)
    assert other.calls > 0


def test_the_plan_prices_the_run_before_any_call(tmp_path):
    plan = M.plan_probe("openai:gpt-5.6-luna", "2022", pairs=20, recall=10, news=False, cache=False,
                        price_in=0.2, price_out=1.2)
    d = plan.describe()
    assert d["calls"] == len(plan.calls) and d["answers_needed"] == 50 and d["tokens_in"] > 10_000
    assert d["cost_usd"] == pytest.approx((d["tokens_in"] * 0.2 + d["tokens_out"] * 1.2) / 1e6, abs=1e-4)
    assert all(c.tokens_in <= 16_000 for c in plan.calls)
    local = M.plan_probe("ollama:llama3.2:3b", "2022", pairs=20, recall=0, cache=False)
    assert local.free and local.cost_usd == 0.0 and all(c.tokens_in <= 3200 for c in local.calls)
    fake = Omniscient("too-dear")
    with pytest.raises(M.CostLimitError):
        probe(fake, tmp_path, price_in=1000.0, price_out=1000.0, max_cost=0.01)
    with pytest.raises(M.CostLimitError):  # a paid model without a price cannot be checked
        probe(fake, tmp_path, max_cost=5.0)
    assert fake.calls == 0


def test_scores_and_verdicts():
    y = np.array([1, 0] * 20)
    perfect = M.paired_scores(np.where(y == 1, 0.9, 0.1), np.full(40, 0.5), y)
    assert perfect["gap"]["brier"]["mean"] == pytest.approx(0.24) and perfect["verdict"] == "contaminated"
    same = M.paired_scores(np.full(40, 0.6), np.full(40, 0.6), y)
    assert same["verdict"] == "inconclusive" and "same probability" in same["why"]
    few = M.paired_scores([0.9] * 10, [0.5] * 10, [1] * 10)
    assert few["verdict"] == "inconclusive" and "10 pairs" in few["why"]
    ci = M.mean_ci(np.zeros(50))
    assert ci["mean"] == 0 and ci["ci95"] == [0.0, 0.0] and ci["p_value"] == 1.0


def test_cutoff_windows():
    assert M.parse_cutoff("2023-12") == (date(2023, 12, 1), date(2023, 12, 31))
    assert M.parse_cutoff("2025") == (date(2025, 1, 1), date(2025, 12, 31))
    assert M.parse_cutoff("2025-06-15") == (date(2025, 6, 15), date(2025, 6, 15))
    with pytest.raises(ValueError):
        M.parse_cutoff("June 2025")
    cut = M.parse_cutoff("2022-06")
    s = M.Sample
    assert M.window_of(s("2022", "AAPL", date(2022, 4, 1), date(2022, 4, 29), 0, 0), cut) == "before"
    assert M.window_of(s("2022", "AAPL", date(2022, 5, 20), date(2022, 6, 17), 0, 0), cut) == "straddles"
    assert M.window_of(s("2022", "AAPL", date(2022, 7, 1), date(2022, 7, 29), 0, 0), cut) == "after"
    # a model that remembers only what happened before the cutoff: a gap before it and none after
    rows = []
    for i, r in enumerate(M.draw_samples("2022", 160, seed=1)):
        w = M.window_of(r, cut)
        known = w == "before"
        rows.append({"period": "2022", "beat": r.beat, "ret": r.ret, "window": w, "in_recall": False,
                     "p_named": (0.8 if r.beat else 0.2) if known else 0.5 + 0.1 * ((i % 3) - 1),
                     "p_blind": 0.5 + 0.1 * ((i % 3) - 1)})
    out = M.summarize(rows, cut)["paired"]["by_window"]
    assert out["before"]["verdict"] == "contaminated" and out["after"]["gap"]["brier"]["mean"] == 0.0


def test_cli_estimate_makes_no_call(monkeypatch):
    from riskaware.cli import app

    monkeypatch.setattr("riskaware.llm.make_provider", lambda cfg: pytest.fail("the estimate must not call a model"))
    out = CliRunner().invoke(app, ["research", "memory", "--llm", "ollama:llama3.2:3b", "--period", "2022",
                                   "--pairs", "10", "--recall", "5", "--no-news", "--estimate", "--json"])
    assert out.exit_code == 0, out.output
    assert '"calls"' in out.output and "free" in out.output
    paid = CliRunner().invoke(app, ["research", "memory", "--llm", "openai:gpt-5.6-luna", "--period", "2022",
                                    "--pairs", "10", "--no-news"])
    assert paid.exit_code == 2 and "--price-in" in paid.output


def test_points_of_one_stock_do_not_overlap():
    pts = M.draw_samples("2022", 300, seed=2)
    cal = [ts.date() for ts in bundled_market("2022").calendar]
    pos = {d: i for i, d in enumerate(cal)}
    by: dict[str, list[int]] = {}
    for s in pts:
        by.setdefault(s.symbol, []).append(pos[s.day])
    assert all(abs(a - b) >= 20 for days in by.values() for a in days for b in days if a != b)
    assert all(s.vol > 0 for s in pts)


def test_recall_does_not_mistake_volatility_for_memory():
    rng = np.random.default_rng(0)
    vol = rng.uniform(1, 10, 200)
    actual = -vol * 0.5 + vol * rng.normal(0, 1, 200)  # a bear market: the volatile stocks fell more
    guess = -vol  # "it was a bear market, and these swing more": no memory of any one stock
    raw = M.recall_scores([False] * 200, guess, actual / 100, scale=None)
    scaled = M.recall_scores([False] * 200, guess, actual / 100, scale=vol)
    assert raw["rank_correlation"] > 0.2 and raw["verdict"] == "recalls"
    assert scaled["rank_correlation"] is None and scaled["verdict"] != "recalls"
    knows = M.recall_scores(actual > 0, actual, actual / 100, scale=vol)
    assert knows["rank_correlation"] > 0.99 and knows["verdict"] == "recalls"


def test_a_run_cut_short_keeps_whole_pairs(tmp_path):
    class Stops(Omniscient):
        def complete_json(self, system, user, schema):
            if self.calls >= 2 + 8:  # the two recall calls, then the first block of 20 points named and blind
                raise KeyboardInterrupt
            return super().complete_json(system, user, schema)

    with pytest.raises(KeyboardInterrupt):
        probe(Stops("cut-short"), tmp_path, pairs=40, recall=20)
    again = Omniscient("cut-short")
    res = probe(again, tmp_path, pairs=20, recall=20)
    assert again.calls == 0 and res.summary["paired"]["all"]["pairs"] == 20


def test_cli_runs_a_probe_and_writes_the_record(monkeypatch, tmp_path):
    from riskaware.cli import app

    monkeypatch.setattr("riskaware.llm.make_provider", lambda cfg: Omniscient("cli"))
    out = CliRunner().invoke(app, ["research", "memory", "--llm", "ollama:cli-fake", "--period", "2022", "--pairs",
                                   "12", "--recall", "6", "--no-news", "--out", str(tmp_path / "rec")])
    assert out.exit_code == 0, out.output
    assert "Verdict: inconclusive" in out.output and "only 12 pairs" in out.output
    saved = json.loads((tmp_path / "rec" / "memory.json").read_text(encoding="utf-8"))
    assert saved["model"] == "ollama:cli-fake" and saved["calls"]["made"] > 0 and saved["calls"]["cost_usd"] == 0.0
    assert set(json.loads((tmp_path / "rec" / "example_prompts.json").read_text(encoding="utf-8"))) == {"named", "blind", "recall"}


def test_a_batch_runner_can_ask_instead(tmp_path):
    """Export the calls as requests, answer them elsewhere, import the results and score without calling."""
    plan = M.plan_probe("queue-model", "2022", pairs=40, recall=20, news=False, cache_dir=tmp_path / "cache")
    n = M.export_requests(plan, tmp_path / "reqs.jsonl")
    reqs = [json.loads(line) for line in (tmp_path / "reqs.jsonl").read_text(encoding="utf-8").splitlines()]
    assert n == len(reqs) == len(plan.calls) and len({r["key"] for r in reqs}) == n
    assert all(r["system"].endswith(json.dumps(M.ANSWER_SCHEMA if r["meta"]["kind"] == "paired" else M.RECALL_SCHEMA))
               for r in reqs)
    oracle = Omniscient("oracle")
    with open(tmp_path / "results.jsonl", "w", encoding="utf-8") as fh:
        for r in reqs:
            reply = oracle.complete_json(r["system"], r["prompt"], None)
            fh.write(json.dumps({"key": r["key"], "text": "```json\n" + json.dumps(reply) + "\n```",
                                 "in_tokens": 900, "out_tokens": 60, "meta": r["meta"]}) + "\n")
        fh.write(json.dumps({"key": "other-task", "text": "{}", "meta": {"x": 1}}) + "\n")  # not ours: skipped
    got = M.import_results(str(tmp_path / "res*.jsonl"), cache_dir=tmp_path / "cache")
    assert got["lines"] == n and got["answers"] == 40 * 2 + 20 and got["tokens_in"] == 900 * n
    asked = Fake("must-not-be-called")
    res = M.memory_probe(asked, "2022", 40, 20, news=False, cache_dir=tmp_path / "cache", model_id="queue-model",
                         offline=True, usage=got, out_dir=tmp_path / "run")
    assert asked.calls == 0 and res.summary["calls"]["offline"] and res.summary["calls"]["tokens_in"] == 900 * n
    assert res.verdict == "contaminated" and res.summary["paired"]["all"]["pairs"] == 40


def test_a_plain_function_can_be_the_model(tmp_path):
    oracle = Omniscient("fn")

    def caller(system, user):  # a text-in, text-out caller, as a ledger wrapper would be
        assert "JSON schema" in system
        return json.dumps(oracle.complete_json(system, user, None))

    res = probe(caller, tmp_path, model_id="ledgered-oracle")
    assert res.verdict == "contaminated" and res.summary["model"] == "ledgered-oracle"
    with pytest.raises(ValueError):
        probe(caller, tmp_path)  # no model_id: answers of different models could share a cache


def test_cli_exports_requests_and_scores_imported_results(tmp_path):
    from riskaware.cli import app

    args = ["research", "memory", "--llm", "queue-cli", "--period", "2022", "--pairs", "30", "--recall", "10",
            "--no-news"]
    out = CliRunner().invoke(app, [*args, "--export-requests", str(tmp_path / "reqs.jsonl")])
    assert out.exit_code == 0, out.output
    reqs = [json.loads(line) for line in (tmp_path / "reqs.jsonl").read_text(encoding="utf-8").splitlines()]
    oracle = Omniscient("oracle")
    (tmp_path / "res.jsonl").write_text("".join(
        json.dumps({"key": r["key"], "text": json.dumps(oracle.complete_json(r["system"], r["prompt"], None)),
                    "meta": r["meta"]}) + "\n" for r in reqs), encoding="utf-8")
    scored = CliRunner().invoke(app, [*args, "--import-results", str(tmp_path / "res.jsonl"), "--out",
                                      str(tmp_path / "rec")])
    assert scored.exit_code == 0, scored.output
    assert "70 answers" in scored.output and "Verdict: contaminated" in scored.output
    assert json.loads((tmp_path / "rec" / "memory.json").read_text(encoding="utf-8"))["calls"]["made"] == 0


def test_without_headline_data_every_record_is_asked_without_headlines(monkeypatch):
    """The headline data does not ship with the package: where a period has none, no record shows headlines, and
    the cache keys say so (an answer to a record with headlines is never reused for one without)."""
    import riskaware.research.day as day

    monkeypatch.setattr(M, "_VIEWS", {})
    monkeypatch.setattr(day, "news_replay", lambda *a, **k: None)
    plan = M.plan_probe("no-news-model", "2022", pairs=10, recall=0, cache=False)
    assert plan.settings["news"] is False and plan.settings["news_missing"] == ["2022"]
    assert not any("headlines" in c.user for c in plan.calls)
    s = plan.samples["2022"][0]
    assert M._key("named", s, plan.settings).endswith("|n0|b5")
