from datetime import date

import pandas as pd
import pytest

from riskaware.backtest.engine import Backtester
from riskaware.backtest.metrics import monthly_returns, summarize
from riskaware.backtest.report import render_html, save_report
from riskaware.strategy import BUILTIN, load_strategy


@pytest.fixture(scope="module")
def results():
    from riskaware.config import AppConfig
    from riskaware.data.prices import SyntheticPrices

    cfg = AppConfig()
    cfg.universe.preset = None
    cfg.universe.symbols = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH"]
    bt = Backtester(cfg, SyntheticPrices(seed=5, start=date(2016, 1, 1), end=date(2021, 12, 31)))
    return {name: bt.run(load_strategy(name), date(2018, 1, 1), date(2021, 12, 31)) for name in BUILTIN}


def test_all_builtin_strategies_run(results):
    for name, r in results.items():
        assert len(r.equity) > 900, name
        assert r.equity.min() > 0
        assert "cagr" in r.metrics and "max_drawdown" in r.metrics


def test_cash_floor_holds_within_tolerance(results):
    r = results["risk_aware"]
    assert r.cash_ratio.min() >= 0.30 - 0.03
    assert r.exposure.max() <= 0.70 + 0.03


def test_no_discretionary_sells_on_uncertain_or_risk_off_days(results):
    r = results["risk_aware"]
    trades = r.trades
    sells = trades[(trades.side == "SELL") & trades.tag.isin(["take_profit", "trend_exit", "cap_trim", "floor_restore"])]
    # decisions are made the day before the fill
    decided = [r.regimes.index[r.regimes.index.get_loc(d) - 1] for d in pd.to_datetime(sells.date)]
    assert all(r.regimes.loc[d] in ("RISK_ON", "NEUTRAL") for d in decided)


def test_fills_happen_after_the_signal_day(results):
    r = results["risk_aware"]
    first_trade = pd.Timestamp(r.trades.date.min())
    assert first_trade > r.equity.index[0]


def test_metrics_and_report(results, tmp_path):
    r = results["risk_aware"]
    m = summarize(r.equity)
    assert -1 < m["max_drawdown"] <= 0 and m["volatility"] > 0
    table = monthly_returns(r.equity)
    assert "Year" in table.columns
    html = render_html([r, results["buy_and_hold"]], lang="zh")
    assert "<html" in html and "回测" in html
    path = save_report([r], tmp_path)
    assert path.exists() and any(tmp_path.glob("trades-risk_aware-*.csv"))


def test_backtest_is_deterministic():
    from riskaware.config import AppConfig
    from riskaware.data.prices import SyntheticPrices

    cfg = AppConfig()
    cfg.universe.preset = None
    cfg.universe.symbols = ["AAA", "BBB", "CCC"]
    runs = []
    for _ in range(2):
        bt = Backtester(cfg, SyntheticPrices(seed=9, start=date(2017, 1, 1), end=date(2020, 12, 31)))
        runs.append(bt.run(load_strategy("risk_aware"), date(2019, 1, 1), date(2020, 12, 31)).equity.iloc[-1])
    assert runs[0] == runs[1]
