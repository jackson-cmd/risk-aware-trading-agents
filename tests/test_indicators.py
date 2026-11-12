import numpy as np
import pandas as pd

from riskaware import indicators as ind
from tests.conftest import make_ohlcv


def test_ema_matches_pandas_and_warms_up():
    s = pd.Series(np.linspace(1, 100, 300))
    e = ind.ema(s, 50)
    assert e.iloc[:49].isna().all()
    ref = s.ewm(span=50, adjust=False).mean()
    assert abs(e.iloc[-1] - ref.iloc[-1]) < 1e-9


def test_rsi_extremes():
    up = pd.Series(np.arange(1, 60, dtype=float))
    down = up[::-1].reset_index(drop=True)
    assert ind.rsi(up, 14).iloc[-1] == 100.0
    assert ind.rsi(down, 14).iloc[-1] < 1.0
    flat = pd.Series(np.full(40, 5.0))
    assert ind.rsi(flat, 14).iloc[-1] == 50.0


def test_rsi_range_on_random_walk():
    rng = np.random.default_rng(0)
    s = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 500))))
    r = ind.rsi(s, 14).dropna()
    assert ((r >= 0) & (r <= 100)).all()


def test_atr_and_true_range_positive():
    df = make_ohlcv(np.linspace(100, 120, 60))
    a = ind.atr(df, 14).dropna()
    assert (a > 0).all()


def test_down_streak_and_below_streak():
    s = pd.Series([5, 4, 3, 4, 3, 2, 1], dtype=float)
    assert list(ind.down_streak(s)) == [0, 1, 2, 0, 1, 2, 3]
    lvl = pd.Series([3.5] * 7)
    assert list(ind.below_streak(s, lvl)) == [0, 0, 1, 0, 1, 2, 3]


def test_compute_has_all_columns_and_no_lookahead():
    rng = np.random.default_rng(1)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.01, 400)))
    df = make_ohlcv(close)
    full = ind.compute(df)
    part = ind.compute(df.iloc[:300])
    for col in ("ema50", "ema200", "rsi2", "atr_pct", "dd252", "don55_high", "z20"):
        assert col in full.columns
        # values at row 299 must not change when later rows are added
        a, b = full[col].iloc[299], part[col].iloc[299]
        assert (np.isnan(a) and np.isnan(b)) or abs(a - b) < 1e-9


def test_pct_rank_bounds():
    s = pd.Series(np.arange(300, dtype=float))
    r = ind.pct_rank(s, 100).dropna()
    assert r.iloc[-1] == 100.0
    assert ((r >= 0) & (r <= 100)).all()
