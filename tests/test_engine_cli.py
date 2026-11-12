from datetime import date

import pytest
from typer.testing import CliRunner

from riskaware.brokers.paper import PaperBroker
from riskaware.cli import app
from riskaware.data.prices import SyntheticPrices
from riskaware.engine import MarketData, TradingEngine, reference_price
from riskaware.journal import Journal
from riskaware.types import Quote


def engine(cfg, tmp_path, broker=None):
    prices = SyntheticPrices(seed=4, start=date(2019, 1, 1))
    data = MarketData(cfg, prices=prices)

    def quotes(symbols):
        out = {}
        for s in symbols:
            df = prices.history([s], date(2019, 1, 1)).get(s)
            if df is not None and len(df):
                px = float(df["close"].iloc[-1])
                out[s] = Quote(s, bid=px, ask=px, last=px)
        return out

    broker = broker or PaperBroker(account_file=tmp_path / "paper.json", starting_cash=100_000, quote_fn=quotes)
    return TradingEngine(cfg, broker=broker, data=data, journal=Journal(tmp_path / "journal.db"))


def test_paper_cycle_end_to_end(offline_cfg, tmp_path):
    offline_cfg.execution.mode = "paper"
    eng = engine(offline_cfg, tmp_path)
    rep = eng.run_cycle()
    assert not rep.errors, rep.errors
    assert rep.market is not None and rep.account.equity == pytest.approx(100_000)
    for o in rep.approved:
        assert o.qty > 0
    j = Journal(tmp_path / "journal.db")
    assert j.recent_runs(1)[0]["mode"] == "paper"
    filled = [r for r in rep.submitted if r.status.value == "FILLED"]
    assert len(filled) == len(rep.submitted)
    # the cash floor holds after the fills (cash + T-bill ETF)
    acct = eng.broker.account()
    sgov = sum(p.market_value for p in eng.broker.positions() if p.symbol == "SGOV")
    assert (acct.cash + sgov) / acct.equity >= 0.29


def test_rerun_same_day_does_not_duplicate_orders(offline_cfg, tmp_path):
    offline_cfg.execution.mode = "paper"
    eng = engine(offline_cfg, tmp_path)
    first = eng.run_cycle()
    second = engine(offline_cfg, tmp_path, broker=eng.broker).run_cycle()
    assert not second.errors
    sent_twice = {(r.symbol, r.side) for r in first.submitted} & {(r.symbol, r.side) for r in second.submitted}
    assert not sent_twice or all(r.symbol in ("SGOV",) for r in second.submitted)


def test_dry_run_sends_nothing(offline_cfg, tmp_path):
    eng = engine(offline_cfg, tmp_path)
    rep = eng.run_cycle(mode="dry-run")
    assert rep.submitted == [] and eng.broker.positions() == []


def test_live_broker_refused_in_paper_mode(offline_cfg, tmp_path):
    class LiveishPaper(PaperBroker):
        @property
        def is_paper(self):
            return False

    eng = engine(offline_cfg, tmp_path, broker=LiveishPaper(account_file=tmp_path / "x.json"))
    rep = eng.run_cycle(mode="paper")
    assert rep.errors and "LIVE" in rep.errors[0]


runner = CliRunner()


def test_cli_basics(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["--help"]).exit_code == 0
    assert "risk_aware" in runner.invoke(app, ["strategies"]).output
    assert "alpaca" in runner.invoke(app, ["brokers"]).output
    out = runner.invoke(app, ["init", "--yes"])
    assert out.exit_code == 0 and (tmp_path / "riskaware.yaml").exists()
    cfg_out = runner.invoke(app, ["config"])
    assert cfg_out.exit_code == 0 and "resolved_profile" in cfg_out.output
    assert runner.invoke(app, ["new-strategy", "my_dip"]).exit_code == 0 and (tmp_path / "my_dip.py").exists()
    assert runner.invoke(app, ["halt", "test"]).exit_code == 0
    assert runner.invoke(app, ["resume"]).exit_code == 0


def test_cli_synthetic_backtest_in_chinese(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = runner.invoke(app, ["--lang", "zh", "backtest", "--synthetic", "--symbols", "AAA,BBB,CCC", "--start",
                              "2020-01-01", "--end", "2021-06-30", "--compare", "buy_and_hold", "--out",
                              str(tmp_path / "bt")])
    assert res.exit_code == 0, res.output
    assert "回测" in res.output
    assert list((tmp_path / "bt").glob("backtest-*.html"))


def test_config_yaml_roundtrip(tmp_path):
    from riskaware.config import load_config

    p = tmp_path / "riskaware.yaml"
    p.write_text("profile: aggressive\nprofile_overrides:\n  cash_floor: 0.35\nuniverse:\n  preset: etf\n"
                 "broker:\n  name: alpaca\n  options: {paper: true}\n", encoding="utf-8")
    c = load_config(p)
    prof = c.risk_profile()
    assert prof.name == "aggressive" and prof.cash_floor == 0.35 and prof.breakouts_enabled
    assert "SPY" in c.universe.resolve()
    bad = tmp_path / "bad.yaml"
    bad.write_text("profile: yolo\n", encoding="utf-8")
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        load_config(bad)


def test_reference_price_ignores_wide_after_hours_quotes():
    assert reference_price(Quote("A", bid=99.95, ask=100.05, last=100.0)) == 100.05
    assert reference_price(Quote("A", bid=331.9, ask=366.9, last=355.8)) == 355.8
    assert reference_price(Quote("A", last=12.0)) == 12.0
    assert reference_price(None) is None
