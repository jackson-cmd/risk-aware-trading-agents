import numpy as np
import pandas as pd

from riskaware.agents.macro import MacroAgent, fear_label, to_regime
from riskaware.config import EventConfig, MacroConfig
from riskaware.data.events import EventCalendar
from riskaware.types import Regime
from riskaware.universe import SECTOR_ETFS
from tests.conftest import make_ohlcv


def frames(n: int = 400, spy_drift: float = 0.0008, vix: float = 13.0, vix3m: float = 16.0, last_vix: float | None = None,
           last_ratio: float | None = None):
    spy = 300 * np.exp(np.cumsum(np.full(n, spy_drift)))
    out = {"SPY": make_ohlcv(spy, "2022-01-03")}
    v = np.full(n, vix)
    v3 = np.full(n, vix3m)
    if last_vix is not None:
        v[-5:] = last_vix
        v3[-5:] = last_vix / (last_ratio or 0.9)
    out["^VIX"] = make_ohlcv(v, "2022-01-03")
    out["^VIX3M"] = make_ohlcv(v3, "2022-01-03")
    for etf in SECTOR_ETFS:
        out[etf] = make_ohlcv(100 * np.exp(np.cumsum(np.full(n, spy_drift))), "2022-01-03")
    for s in ("HYG", "IEF", "TLT"):
        out[s] = make_ohlcv(np.linspace(100, 101, n), "2022-01-03")
    return out


def agent(events: bool = False) -> MacroAgent:
    cfg = MacroConfig()
    cfg.events = EventConfig(enabled=events)
    return MacroAgent(cfg, EventCalendar(cfg.events) if events else None)


def test_calm_uptrend_is_risk_on():
    s = agent().compute_series(frames())
    assert to_regime(s["regime"].iloc[-1]) == Regime.RISK_ON
    assert s["macro_score"].iloc[-1] > 0


def test_high_vix_is_risk_off_and_moderate_is_uncertain():
    s = agent().compute_series(frames(last_vix=34))
    assert to_regime(s["regime"].iloc[-1]) == Regime.RISK_OFF
    s = agent().compute_series(frames(last_vix=23))
    assert to_regime(s["regime"].iloc[-1]) == Regime.UNCERTAIN


def test_downtrend_is_uncertain_even_with_low_vix():
    s = agent().compute_series(frames(spy_drift=-0.001))
    assert to_regime(s["regime"].iloc[-1]) in (Regime.UNCERTAIN, Regime.RISK_OFF)


def test_hysteresis_needs_two_days_to_relax():
    raw = np.array([3, 3, 1, 3, 1, 1, 1])
    out = MacroAgent._hysteresis(raw, np.zeros(len(raw), dtype=bool))
    assert list(out) == [3, 3, 3, 3, 3, 1, 1]


def test_event_window_forces_uncertain():
    f = frames()
    idx = f["SPY"].index
    s = agent(events=True).compute_series(f, idx)
    day = pd.Timestamp("2023-03-21")  # FOMC decision 2023-03-22 -> window covers the 21st
    assert day in s.index
    assert to_regime(s.loc[day, "regime"]) == Regime.UNCERTAIN
    assert "FOMC" in s.loc[day, "events"]


def test_fear_labels_and_regime_parsing():
    assert fear_label(10) == "extreme fear" and fear_label(50) == "neutral" and fear_label(90) == "extreme greed"
    assert to_regime("Regime.RISK_OFF") == Regime.RISK_OFF and to_regime("NEUTRAL") == Regime.NEUTRAL
