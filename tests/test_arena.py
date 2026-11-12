import plistlib
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from riskaware import marketcal as mc
from riskaware import scheduler
from riskaware.arena import AgentSpec, Arena, QuoteCache, due
from riskaware.cli import app
from riskaware.data.prices import SyntheticPrices
from riskaware.engine import MarketData
from riskaware.journal import Journal
from riskaware.types import Quote

PRICES = SyntheticPrices(seed=4, start=date(2019, 1, 1))


def fake_quotes(symbols):
    out = {}
    for s in symbols:
        df = PRICES.history([s], date(2019, 1, 1)).get(s)
        if df is not None and len(df):
            px = float(df["close"].iloc[-1])
            out[s] = Quote(s, bid=px, ask=px, last=px)
    return out


def data_factory(cfg):
    return MarketData(cfg, prices=PRICES)


@pytest.fixture
def arena(offline_cfg, tmp_path):
    return Arena(tmp_path / "arena", base_cfg=offline_cfg)


def ny(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=mc.NY)


def test_spec_validation():
    assert AgentSpec(name="My-Dips").name == "my-dips"
    with pytest.raises(ValueError):
        AgentSpec(name="has space")
    with pytest.raises(ValueError):
        AgentSpec(name="x", profile="yolo")
    assert AgentSpec(name="x", symbols=[" nvda", "", "aapl"]).symbols == ["NVDA", "AAPL"]


def test_add_load_remove(arena):
    arena.add(AgentSpec(name="a", strategy="ema_bounce"))
    assert [a.name for a in arena.load()] == ["a"]
    with pytest.raises(ValueError):
        arena.add(AgentSpec(name="a"))
    with pytest.raises(ValueError):
        arena.add(AgentSpec(name="b", strategy="no_such_strategy"))
    with pytest.raises(ValueError):
        arena.add(AgentSpec(name="c", preset="nope"))
    arena.add(AgentSpec(name="a", strategy="rsi2"), overwrite=True)
    assert arena.get("a").strategy == "rsi2"
    added = arena.add_starters()
    assert {a.name for a in added} == {"steady", "momentum", "breakout", "fear-buyer"}
    assert arena.add_starters() == []
    arena.remove("a")
    with pytest.raises(KeyError):
        arena.get("a")


def test_config_for_forces_the_paper_account(arena, offline_cfg):
    offline_cfg.broker.name = "alpaca"
    offline_cfg.execution.mode = "live"
    spec = arena.add(AgentSpec(name="tech", strategy="momentum_rotation", profile="aggressive", preset="tech",
                               cash=25_000))
    c = arena.config_for(spec)
    assert c.broker.name == "paper" and c.execution.mode == "paper"
    assert Path(c.broker.options["account_file"]) == arena.dir("tech") / "tech.json"
    assert c.broker.options["starting_cash"] == 25_000
    assert c.profile == "aggressive" and c.strategy.name == "momentum_rotation"
    assert c.universe.preset == "tech" and c.universe.symbols == []
    # the base config is not modified
    assert offline_cfg.broker.name == "alpaca"


def test_two_agents_trade_separate_accounts(arena):
    arena.add(AgentSpec(name="one", strategy="risk_aware"))
    arena.add(AgentSpec(name="two", strategy="buy_and_hold", cash=50_000))
    runs = arena.run(quote_fn=fake_quotes, data_factory=data_factory)
    assert [r.name for r in runs] == ["one", "two"] and all(r.ok for r in runs), [r.errors for r in runs]
    two = arena.account_state("two")
    assert two["positions"], "buy and hold should have bought something"
    assert arena.account_state("one") != two
    for name, cash in (("one", 100_000), ("two", 50_000)):
        hist = arena.equity_history(name)
        assert len(hist) == 1
        assert hist["equity"].iloc[0] == pytest.approx(cash, rel=0.01)
        assert (arena.dir(name) / "journal.db").is_file()
        assert list((arena.dir(name) / "state").glob("*.json"))
    # a second run on the same day sends no duplicate orders and keeps one row per day
    before = len(Journal(arena.dir("two") / "journal.db").recent_orders(500))
    again = arena.run(["two"], quote_fn=fake_quotes, data_factory=data_factory)
    assert again[0].ok
    orders = Journal(arena.dir("two") / "journal.db").recent_orders(500)
    sent = [o for o in orders if o["status"] == "FILLED"]
    assert len({o["client_order_id"] for o in sent}) == len(sent)
    assert len(orders) >= before
    assert len(arena.equity_history("two")) == 1


def test_dry_run_and_bad_agent_do_not_stop_others(arena, tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("from riskaware.strategy import Strategy\n\nclass Bad(Strategy):\n    name = 'bad'\n"
                   "    def on_bar(self, ctx):\n        raise RuntimeError('boom')\n")
    arena.add(AgentSpec(name="bad", strategy=f"{bad}:Bad"))
    arena.add(AgentSpec(name="good", strategy="buy_and_hold"))
    runs = arena.run(mode="dry-run", quote_fn=fake_quotes, data_factory=data_factory)
    by = {r.name: r for r in runs}
    assert not by["bad"].ok and "boom" in by["bad"].errors[0]
    assert by["good"].ok and by["good"].filled == 0
    assert not arena.account_state("good").get("fills")
    with pytest.raises(ValueError):
        arena.run(mode="live")
    with pytest.raises(KeyError):
        arena.run(["ghost"])


def test_leaderboard_ranks_and_compares_with_spy(arena):
    arena.add(AgentSpec(name="up", created="2026-01-02"))
    arena.add(AgentSpec(name="down", created="2026-01-02"))
    for name, curve in (("up", [100_000, 102_000, 105_000]), ("down", [100_000, 95_000, 97_000])):
        p = arena.dir(name) / "equity.csv"
        p.parent.mkdir(parents=True)
        pd.DataFrame({"date": ["2026-01-02", "2026-01-05", "2026-01-06"], "equity": curve, "cash": 40_000,
                      "invested": 50_000, "bonds": 10_000, "positions": 5, "regime": "NEUTRAL"}).to_csv(p, index=False)
    spy = pd.Series([100.0, 101.0, 103.0], index=pd.to_datetime(["2025-12-31", "2026-01-05", "2026-01-06"]))
    rows = arena.leaderboard(spy)
    assert [r["name"] for r in rows] == ["up", "down"] and rows[0]["rank"] == 1
    up, down = rows
    assert up["return"] == pytest.approx(0.05)
    assert up["day"] == pytest.approx(105 / 102 - 1)
    assert down["max_drawdown"] == pytest.approx(-0.05)
    assert up["benchmark_return"] == pytest.approx(0.03)
    assert up["curve"][0] == 100_000 and up["days"] == 3


def test_due_window():
    assert due(ny(2026, 11, 14, 15, 45))[0] is False            # Saturday
    assert due(ny(2026, 11, 26, 15, 45))[0] is False            # Thanksgiving
    assert due(ny(2026, 11, 12, 15, 0))[0] is False             # before 15:40
    assert due(ny(2026, 11, 12, 15, 41)) == (True, "due")
    assert due(ny(2026, 11, 12, 16, 5))[0] is False             # after the close
    # early close (day after Thanksgiving): run 20 minutes before 13:00
    assert due(ny(2026, 11, 27, 12, 45)) == (True, "due")
    assert due(ny(2026, 11, 27, 13, 5))[0] is False


def test_scheduled_run_trades_each_agent_once_a_day(arena):
    arena.add(AgentSpec(name="a", strategy="buy_and_hold"))
    why, runs = arena.scheduled_run(now=ny(2026, 11, 12, 10, 0), quote_fn=fake_quotes, data_factory=data_factory)
    assert runs == [] and why.startswith("waiting")
    why, runs = arena.scheduled_run(now=ny(2026, 11, 12, 15, 45), quote_fn=fake_quotes, data_factory=data_factory)
    assert why == "ran" and [r.name for r in runs] == ["a"] and runs[0].ok
    why, runs = arena.scheduled_run(now=ny(2026, 11, 12, 15, 50), quote_fn=fake_quotes, data_factory=data_factory)
    assert runs == [] and why == "every agent already ran today"
    # a new agent added later the same day still gets its run
    arena.add(AgentSpec(name="b", strategy="buy_and_hold"))
    why, runs = arena.scheduled_run(now=ny(2026, 11, 12, 15, 55), quote_fn=fake_quotes, data_factory=data_factory)
    assert [r.name for r in runs] == ["b"]
    assert arena.leaderboard()[0]["last_run"] is not None


def test_scheduled_run_gives_up_after_three_failures(arena, tmp_path):
    bad = tmp_path / "bad2.py"
    bad.write_text("from riskaware.strategy import Strategy\n\nclass Bad(Strategy):\n    name = 'bad'\n"
                   "    def on_bar(self, ctx):\n        raise RuntimeError('boom')\n")
    arena.add(AgentSpec(name="bad", strategy=f"{bad}:Bad"))
    attempts = 0
    for minute in (41, 45, 50, 55):
        _, runs = arena.scheduled_run(now=ny(2026, 11, 12, 15, minute), quote_fn=fake_quotes,
                                      data_factory=data_factory)
        attempts += len(runs)
    assert attempts == 3


def test_quote_cache_fetches_each_symbol_once():
    calls = []

    def fn(symbols):
        calls.append(list(symbols))
        return {s: Quote(s, last=1.0) for s in symbols}

    qc = QuoteCache(fn)
    qc(["A", "B"])
    qc(["B", "C"])
    assert calls == [["A", "B"], ["C"]]


def test_scheduler_job_definitions(tmp_path):
    cmd = scheduler.command(tmp_path, tmp_path / "riskaware.yaml", python="/usr/bin/python3")
    assert cmd[:3] == ["/usr/bin/python3", "-m", "riskaware"]
    assert cmd[3:5] == ["--config", str(tmp_path / "riskaware.yaml")]
    assert cmd[5:] == ["agents", "run", "--scheduled", "--cwd", str(tmp_path)]
    plist = plistlib.loads(scheduler.launchd_plist(cmd, tmp_path, tmp_path / "log.txt", {"PATH": "/usr/bin"}))
    assert plist["Label"] == scheduler.LABEL and plist["ProgramArguments"] == cmd
    assert len(plist["StartCalendarInterval"]) == 20
    assert {i["Weekday"] for i in plist["StartCalendarInterval"]} == {1, 2, 3, 4, 5}
    line = scheduler.cron_line(cmd, tmp_path / "log.txt")
    assert line.startswith("*/15 * * * 1-5 ") and line.endswith(scheduler.CRON_MARK)
    win = scheduler.schtasks_create(["C:\\Py\\python.exe", "-m", "riskaware", "agents", "run", "--scheduled"])
    assert win[:3] == ["schtasks", "/Create", "/F"] and "MINUTE" in win and scheduler.WIN_TASK in win


def test_scheduler_install_on_linux_uses_crontab(monkeypatch, tmp_path):
    calls = []

    class R:
        def __init__(self, rc=0, out=""):
            self.returncode, self.stdout, self.stderr = rc, out, ""

    def fake_run(args, input_text=None):
        calls.append((args, input_text))
        if args == ["crontab", "-l"]:
            return R(0, "0 1 * * * backup.sh\n")
        return R()

    monkeypatch.setattr(scheduler, "system", lambda: "linux")
    monkeypatch.setattr(scheduler, "_run", fake_run)
    scheduler.install(tmp_path, tmp_path / "logs" / "out.log")
    written = calls[-1][1]
    assert "backup.sh" in written and scheduler.CRON_MARK in written


def test_cli_agents(tmp_path, monkeypatch, offline_cfg):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Arena, "benchmark", lambda self, symbol=None: None)
    runner = CliRunner()
    res = runner.invoke(app, ["agents", "list"])
    assert res.exit_code == 0 and "agents init" in res.output
    res = runner.invoke(app, ["agents", "add", "dips", "--strategy", "rsi2", "--universe", "etf", "--cash", "20000",
                              "--param", "entri=5"])
    assert res.exit_code != 0  # a misspelled parameter fails at creation, not at 15:40
    res = runner.invoke(app, ["agents", "add", "dips", "--strategy", "rsi2", "--universe", "etf", "--cash", "20000"])
    assert res.exit_code == 0, res.output
    res = runner.invoke(app, ["agents", "init"])
    assert res.exit_code == 0 and "steady" in res.output
    res = runner.invoke(app, ["--lang", "zh", "agents"])
    assert res.exit_code == 0 and "dips" in res.output and "模拟盘智能体" in res.output
    res = runner.invoke(app, ["agents", "pause", "dips"])
    assert res.exit_code == 0
    res = runner.invoke(app, ["agents", "remove", "dips", "--yes"])
    assert res.exit_code == 0
    res = runner.invoke(app, ["agents", "show", "dips"])
    assert res.exit_code == 1


def test_manual_run_counts_as_todays_trade(arena, monkeypatch):
    monkeypatch.setattr(mc, "now_ny", lambda: ny(2026, 11, 12, 11, 0))
    arena.add(AgentSpec(name="a", strategy="buy_and_hold"))
    arena.run(quote_fn=fake_quotes, data_factory=data_factory)
    assert arena.leaderboard()[0]["ran_today"]
    why, runs = arena.scheduled_run(now=ny(2026, 11, 12, 15, 45), quote_fn=fake_quotes, data_factory=data_factory)
    assert runs == [] and why == "every agent already ran today"
    # a dry run does not count
    arena.add(AgentSpec(name="b", strategy="buy_and_hold"))
    arena.run(["b"], mode="dry-run", quote_fn=fake_quotes, data_factory=data_factory)
    _, runs = arena.scheduled_run(now=ny(2026, 11, 12, 15, 50), quote_fn=fake_quotes, data_factory=data_factory)
    assert [r.name for r in runs] == ["b"]


def test_runs_never_overlap(arena):
    arena.add(AgentSpec(name="a", strategy="buy_and_hold"))
    assert arena._acquire()  # e.g. the daily job is trading right now
    with pytest.raises(RuntimeError):
        arena.run(quote_fn=fake_quotes, data_factory=data_factory)
    why, runs = arena.scheduled_run(now=ny(2026, 11, 12, 15, 45), quote_fn=fake_quotes, data_factory=data_factory)
    assert runs == [] and why == "another run is in progress"
    arena._release()
    assert arena.run(quote_fn=fake_quotes, data_factory=data_factory)[0].ok
    assert not (arena.root / "run.lock").exists()


def test_stale_lock_from_a_crash_is_ignored(arena):
    import os

    arena.add(AgentSpec(name="a", strategy="buy_and_hold"))
    assert arena._acquire()
    old = datetime.now().timestamp() - 4 * 3600
    os.utime(arena.root / "run.lock", (old, old))
    assert arena.run(quote_fn=fake_quotes, data_factory=data_factory)[0].ok


def test_state_text_in_chinese():
    from riskaware.arena import state_text

    assert state_text("waiting until 15:40 New York", "zh") == "等到纽约时间 15:40"
    assert state_text("Sat 2026-11-14 is not a trading day", "zh") == "今天不是交易日"
    assert state_text("due", "en") == "due"


def test_daily_new_position_limit_holds_across_runs(arena):
    arena.add(AgentSpec(name="a", strategy="buy_and_hold", cash=10_000_000))  # big enough that sizes fit
    limit = arena.config_for(arena.get("a")).risk_profile().max_new_positions_per_day
    cash_like = {"SGOV", "IEF"}

    def stocks():
        return {s for s in arena.account_state("a")["positions"] if s not in cash_like}

    arena.run(quote_fn=fake_quotes, data_factory=data_factory)
    first = stocks()
    assert len(first) == limit  # six candidates, the limit binds
    arena.run(quote_fn=fake_quotes, data_factory=data_factory)  # a second run the same day opens nothing new
    assert stocks() == first
