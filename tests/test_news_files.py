"""The research headline files are not distributed: where the code looks for a copy, and what a run says without
one. These tests hide every copy, so they run the same on any machine."""

from __future__ import annotations

import gzip
import json

import pytest

from riskaware.news import files
from riskaware.research import bench
from riskaware.research.agent import _Replay
from riskaware.research.data import dataset_info, news_status, verify
from riskaware.research.day import MarketView
from riskaware.research.experiment import Experiment, run_experiment
from riskaware.research.periods import get_period

SHORT_2022 = {"period": "2022", "start": "2022-03-01", "end": "2022-04-29"}


@pytest.fixture
def folders(monkeypatch, tmp_path):
    """Empty versions of the three folders: $RISKAWARE_NEWS_DIR, ~/.riskaware/data/news and the package's."""
    env, private, package = (tmp_path / n for n in ("env", "private", "package"))
    for d in (env, private, package):
        d.mkdir()
    monkeypatch.setenv(files.NEWS_DIR_ENV, str(env))
    monkeypatch.setattr(files, "PRIVATE_NEWS_DIR", private)
    monkeypatch.setattr(files, "PACKAGE_NEWS_DIR", package)
    return env, private, package


def test_the_lookup_order(folders, monkeypatch):
    env, private, package = folders
    name = "news_2022.jsonl.gz"
    assert files.news_dirs() == [env, private, package]
    assert files.find_news_file(name) is None and files.news_file(name) == env / name  # where to put it
    (package / name).write_bytes(b"old install")
    assert files.find_news_file(name) == package / name and "package" in files.where(package / name)
    (private / name).write_bytes(b"private")
    assert files.find_news_file(name) == private / name
    assert files.where(private / name) in ("~/.riskaware/data/news", "$RISKAWARE_HOME/data/news")
    (env / name).write_bytes(b"env")
    assert files.find_news_file(name) == env / name and files.where(env / name) == files.NEWS_DIR_ENV
    assert get_period("2022").news_path == env / name and get_period("2022").has_news
    monkeypatch.delenv(files.NEWS_DIR_ENV)
    assert files.news_dirs() == [private, package] and files.find_news_file(name) == private / name


def test_without_a_copy_the_news_is_not_distributed_not_changed(folders):
    for period in ("2020", "2022", "2026"):
        p = get_period(period)
        st = news_status(period)
        assert not p.has_news and st["status"] == "not distributed" and st["sha256"] is None
        assert st["matches_manifest"] is None and st["available"] is False and st["distributed"] is False
        assert len(st["manifest_sha256"]) == 64 and "not distributed" in st["note"]
        assert dataset_info(period)["news"]["status"] == "not distributed"
        checks = verify(period)
        assert checks[p.news] is None and checks[f"market_{p.end.year}.csv.gz"] is True


def test_a_copy_that_differs_from_the_manifest_is_changed(folders):
    env, _, _ = folders
    with gzip.open(env / "news_2022.jsonl.gz", "wt", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": "2022-03-02T15:00:00Z", "symbol": "NVDA", "headline": "Nvidia unveils a new chip",
                             "source": "Example", "feed": "headlines"}) + "\n")
    st = news_status("2022")
    assert st["status"] == "changed" and st["matches_manifest"] is False and st["found_in"] == files.NEWS_DIR_ENV
    assert verify("2022")["news_2022.jsonl.gz"] is False


def test_a_run_with_news_on_and_no_copy_says_so(folders, tmp_path):
    exp = Experiment(strategy="swing_rsi2", universe="core", news=True, leak_check=False, **SHORT_2022)
    res = run_experiment(exp, out_dir=tmp_path / "run", chart=False)
    assert res.notes[0].startswith("news is on, but no copy of news_2022.jsonl.gz was found")
    assert res.summary()["news_missing"] == res.notes[0]
    man = json.loads((tmp_path / "run" / "manifest.json").read_text(encoding="utf-8"))
    assert man["notes"][0] == res.notes[0] and man["dataset"]["news"]["status"] == "not distributed"
    assert man["dataset"]["news"]["sha256"] is None and len(man["dataset"]["news"]["manifest_sha256"]) == 64
    report = (tmp_path / "run" / "report.md").read_text(encoding="utf-8")
    assert "not distributed with the package" in report and "news is on, but no copy" in report
    quiet = run_experiment(Experiment(strategy="swing_rsi2", universe="core", news=False, leak_check=False,
                                      **SHORT_2022), save=False)
    assert not any("no copy" in n for n in quiet.notes) and "news_missing" not in quiet.summary()


def test_the_day_view_says_why_there_are_no_headlines(folders):
    snap = MarketView(period="2022").snapshot("2022-03-15", symbols=["NVDA"], headlines=2)
    assert "news_2022.jsonl.gz" in snap["news_note"] and snap["stocks"]["NVDA"].get("news") is None
    assert "news_note" not in MarketView(period="2022", news=False).snapshot("2022-03-15", symbols=["NVDA"],
                                                                             news=False)


def test_a_benchmark_run_stops_before_calling_a_model_but_a_replay_does_not(folders, monkeypatch):
    calls = []

    def policy(obs: dict) -> dict:
        calls.append(obs)
        return {"orders": []}

    with pytest.raises(FileNotFoundError, match="not distributed"):
        bench.run_task(policy, "2022-blind", save=False)
    assert calls == []
    with pytest.raises(FileNotFoundError):
        bench.anchor_rows(periods=("2022",), leak_check=False)
    monkeypatch.setitem(bench.SETTINGS, "universe", "core")
    monkeypatch.setattr(bench, "window", lambda period: (get_period(period).start, get_period(period).start.replace(
        month=3)))
    res = bench.run_task(_Replay([{"orders": []}] * 20), "2022-blind", save=False, check=False)
    # fixed replies need no headline file: the run happens, and its notes say what it did not see
    assert res.metrics["total_return"] is not None and res.notes[0].startswith("news is on, but no copy")


def test_the_cli_reports_the_headlines_as_not_distributed(folders):
    from typer.testing import CliRunner

    from riskaware.cli import app

    out = CliRunner().invoke(app, ["research", "data", "--period", "2020"])
    assert out.exit_code == 0 and "not distributed" in out.output
    out = CliRunner().invoke(app, ["research", "factors", "--period", "2020"])
    assert out.exit_code == 2 and "not distributed" in out.output
