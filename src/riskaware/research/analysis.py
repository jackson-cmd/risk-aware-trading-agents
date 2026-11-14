"""What a strategy or an LLM agent made its money on: decision statistics, factor attribution and a breakdown of the
P&L of a saved experiment.

``analyze_run(folder)`` reads a record (manifest.json, equity.csv, trades.csv, decisions.csv, and policy_log.json for
an LLM run), takes the prices of the record's period from the frozen market, and writes analysis.json (every number),
analysis.md (tables to read or paste) and analysis.png next to the record. Nothing is run again and the record's own
files are not changed. From the command line: ``riskaware research analyze <run id or folder>``; over MCP:
``experiment_analysis``.

1. Decisions. A year of portfolio returns is one sample of a few hundred correlated days, while a run makes hundreds
   of decisions. For every buy and sell in the record: the stock's return minus SPY's over the next 1, 5 and 20
   trading days, from the open of the day the order filled, on total-return prices. A buy is credited with the excess
   it earned, a sell with the excess it avoided (SPY's return minus the stock's). Means, hit rates and t-statistics
   whose standard errors allow for decisions on the same day and for overlapping windows. Then the rank IC of each
   decision day: across the universe, the rank correlation between what the agent did (+1 bought, -1 sold, 0 left
   alone) or what it held afterwards (its weights) and the returns of the next five days.
2. Factors. The run's daily return over T-bills regressed on four factors computed from the bundled prices: the
   market (SPY - BIL), size (IWM - SPY), growth (QQQ - SPY) and momentum (the top third of the bundled stocks by
   their return from 126 to 21 trading days earlier minus the bottom third, sorted at each month end on the prices
   known then). Newey-West t-statistics; the alpha is what is left after those exposures. KTD-Fin (arXiv 2605.28359)
   found that most of what LLM traders earn is market and style exposure; this is the check.
3. P&L. Every position from its first buy to its last sell, plus the T-bill interest on idle cash, adds up to the
   run's P&L. It is split by holding period, sector, the market regime, whether news triggered the entry or the exit
   (the reason the decision gave), and stock, with the best and worst groups and trades and how much of the profit
   rests on one or two trades.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .data import FrozenMarket, bundled_market
from .data import manifest as data_manifest
from .experiment import CASH_VEHICLES, RUNS_DIR

SCHEMA = "riskaware.analysis/1"
TRADING_DAYS = 252
HORIZONS = (1, 5, 20)
IC_HORIZON = 5
MOM_LOOKBACK, MOM_SKIP = 126, 21
FACTORS = ("market", "size", "growth", "momentum")
FACTOR_LABELS = {"market": "Market (SPY - BIL)", "size": "Size (IWM - SPY)", "growth": "Growth (QQQ - SPY)",
                 "momentum": "Momentum (top - bottom third)"}
HOLDING_BUCKETS = ((1, 2), (3, 5), (6, 10), (11, 20), (21, 60), (61, None))
REGIMES = ("RISK_ON", "NEUTRAL", "UNCERTAIN", "RISK_OFF")
REGIME_LABELS = {"RISK_ON": "risk-on", "NEUTRAL": "neutral", "UNCERTAIN": "uncertain", "RISK_OFF": "risk-off"}
FILES = ("analysis.json", "analysis.md", "analysis.png")

# A decision counts as triggered by news when the reason it gave names news. The swing strategies write "fresh
# positive news: <headline>", "red flag in the news" and "confirmed red flag (...)"; an LLM writes its own words.
NEWS_RE = re.compile(
    r"\bnews\b|headline|red flag|\b8-?k\b|\bsec\b|filing|earnings|guidance|upgrade|downgrade|analyst|price target|"
    r"announce|press release|lawsuit|investigation|\bfda\b|merger|acquisition|acquire|buyback|\bceo\b|"
    r"\breport(?:s|ed)?\b|\bstor(?:y|ies)\b|rumou?r|新闻|消息|公告|财报|利好|利空|评级",
    re.IGNORECASE)
NO_REASON = {"", "nan", "none", "buy", "sell", "policy buy", "policy sell", "policy target"}


# ----------------------------------------------------------------------------------- statistics
def mean_test(values: Any, days: Any, overlap: int = 1) -> dict[str, Any]:
    """Mean, median, hit rate (share above zero) and t-statistics of values observed on trading-day numbers ``days``.

    ``t_iid`` treats the values as independent. ``t`` allows for correlation between values whose windows overlap: a
    Bartlett kernel on the distance in trading days, weight ``1 - d / overlap`` for ``d < overlap``. Values from the
    same day count in full (a cluster), and two windows of h days that start d days apart are weighted by the share
    of days they have in common. With ``overlap=1`` it is a t-statistic clustered by day; with one value per day it
    equals ``t_iid``.
    """
    v = np.asarray(values, dtype=float).ravel()
    k = np.asarray(days, dtype=float).ravel()
    ok = np.isfinite(v) & np.isfinite(k)
    v, kd = v[ok], k[ok].astype(int)
    n = int(len(v))
    out: dict[str, Any] = {"n": n, "mean": None, "median": None, "hit_rate": None, "se": None, "t": None,
                           "t_iid": None}
    if n == 0:
        return out
    m = float(v.mean())
    out.update(mean=m, median=float(np.median(v)), hit_rate=float((v > 0).mean()))
    if n < 2:
        return out
    sd = float(v.std(ddof=1))
    if sd > 0:
        out["t_iid"] = m / (sd / math.sqrt(n))
    s = np.bincount(kd - kd.min(), weights=v - m)  # residuals summed by day
    q = float(s @ s)
    h = max(1, int(overlap))
    for d in range(1, min(h, len(s))):
        q += 2.0 * (1.0 - d / h) * float(s[:-d] @ s[d:])
    var = q / (n * (n - 1))  # q / n^2 with the small-sample factor n / (n - 1)
    if var > 1e-30:
        out["se"] = math.sqrt(var)
        out["t"] = m / out["se"]
    return out


def rank_ic(score: pd.Series, forward: pd.Series) -> float | None:
    """Spearman correlation (Pearson on average ranks) of two series over the symbols where both are known."""
    both = pd.concat([score.rename("s"), forward.rename("f")], axis=1).dropna()
    if len(both) < 3 or both["s"].nunique() < 2 or both["f"].nunique() < 2:
        return None
    return float(both["s"].rank().corr(both["f"].rank()))


def newey_west_lags(n: int) -> int:
    """The usual rule of thumb for the number of lags: floor(4 (n / 100)^(2/9)), 4 for a year of days."""
    return max(1, int(4 * (n / 100) ** (2 / 9)))


def ols_newey_west(y: Any, x: Any, names: list[str] | tuple[str, ...], lags: int | None = None) -> dict[str, Any]:
    """OLS of y on a constant and the columns of x, with Newey-West (Bartlett) standard errors.

    The covariance is ``(X'X)^-1 S (X'X)^-1 * n / (n - k)``, with S the Bartlett-weighted sum of the score's
    autocovariances up to ``lags`` (``newey_west_lags(n)`` by default). The constant is reported as ``alpha``.
    """
    yv = np.asarray(y, dtype=float).ravel()
    n = len(yv)
    xv = np.column_stack([np.ones(n), np.asarray(x, dtype=float).reshape(n, -1)])
    k = xv.shape[1]
    if n <= k + 2:
        raise ValueError(f"{n} observations are too few for {k} coefficients")
    xtx_inv = np.linalg.pinv(xv.T @ xv)
    beta = xtx_inv @ (xv.T @ yv)
    u = yv - xv @ beta
    nlags = newey_west_lags(n) if lags is None else max(0, int(lags))
    xu = xv * u[:, None]
    s = xu.T @ xu
    for lag in range(1, min(nlags, n - 1) + 1):
        g = xu[lag:].T @ xu[:-lag]
        s += (1.0 - lag / (nlags + 1)) * (g + g.T)
    cov = xtx_inv @ s @ xtx_inv * n / (n - k)
    se = np.sqrt(np.clip(np.diag(cov), 0.0, None))
    keys = ["alpha", *names]
    tss = float(((yv - yv.mean()) ** 2).sum())
    r2 = 1.0 - float(u @ u) / tss if tss > 0 else math.nan
    return {
        "n": n, "lags": nlags,
        "coef": {kk: float(b) for kk, b in zip(keys, beta, strict=True)},
        "se": {kk: float(e) for kk, e in zip(keys, se, strict=True)},
        "t": {kk: float(b / e) if e > 0 else None for kk, b, e in zip(keys, beta, se, strict=True)},
        "r2": r2, "adj_r2": 1.0 - (1.0 - r2) * (n - 1) / (n - k) if not math.isnan(r2) else math.nan,
        "resid_vol_annual": float(u.std(ddof=k) * math.sqrt(TRADING_DAYS)),
    }


# ----------------------------------------------------------------------------------- factors
def momentum_factor(closes: pd.DataFrame, lookback: int = MOM_LOOKBACK, skip: int = MOM_SKIP) -> pd.Series:
    """Daily return of the top third minus the bottom third of ``closes``' columns by momentum.

    At the close of each month's last trading day, every stock is scored by its return from ``lookback`` to ``skip``
    trading days earlier (closes known that day). The two equal-weight portfolios earn the close-to-close returns of
    the following days, up to and including the next month end, when they are sorted again. Days before the first
    sort are NaN.
    """
    idx = closes.index
    out = pd.Series(np.nan, index=idx, name="momentum")
    if closes.empty or len(idx) < lookback + 2:
        return out
    rets = closes.pct_change()
    ym = np.asarray(idx.year) * 12 + np.asarray(idx.month)
    ends = np.flatnonzero(np.r_[ym[1:] != ym[:-1], True])
    vals = closes.to_numpy(dtype=float)
    for j, e in enumerate(ends):
        if e - lookback < 0 or e + 1 >= len(idx):
            continue
        with np.errstate(divide="ignore", invalid="ignore"):
            score = pd.Series(vals[e - skip] / vals[e - lookback] - 1.0, index=closes.columns)
        score = score[np.isfinite(score)]
        third = len(score) // 3
        if third < 1:
            continue
        order = score.sort_values(kind="mergesort")
        low, high = list(order.index[:third]), list(order.index[-third:])
        stop = ends[j + 1] if j + 1 < len(ends) else len(idx) - 1
        days = idx[e + 1: stop + 1]
        out.loc[days] = rets.loc[days, high].mean(axis=1) - rets.loc[days, low].mean(axis=1)
    return out


def bundled_stocks(market: FrozenMarket) -> list[str]:
    """The stocks of the market's period (the 64 of the bundle), for the momentum factor."""
    try:
        stocks = data_manifest(market.period)["symbols"]["stocks"]
    except (KeyError, OSError, ValueError):
        stocks = []
    return [s for s in stocks if market.has(s)]


def factor_returns(market: FrozenMarket, universe: list[str] | None = None) -> pd.DataFrame:
    """Daily T-bill return and the four factors over the market's calendar (close to close, total-return prices)."""
    cal = market.calendar

    def ret(sym: str) -> pd.Series:
        if not market.has(sym):
            return pd.Series(np.nan, index=cal)
        return market.frame(sym)["close"].pct_change().reindex(cal)

    rf = ret("BIL").fillna(0.0).clip(-0.01, 0.01)  # what idle cash earns in the backtest
    spy = ret("SPY")
    stocks = universe if universe is not None else bundled_stocks(market)
    closes = pd.DataFrame({s: market.frame(s)["close"].reindex(cal) for s in stocks if market.has(s)}, index=cal)
    return pd.DataFrame({"rf": rf, "market": spy - rf, "size": ret("IWM") - spy, "growth": ret("QQQ") - spy,
                         "momentum": momentum_factor(closes)}, index=cal)


# ----------------------------------------------------------------------------------- the record
@dataclass
class Record:
    folder: Path
    manifest: dict[str, Any]
    equity: pd.DataFrame
    trades: pd.DataFrame
    decisions: pd.DataFrame | None
    policy_log: dict[str, Any] | None

    @property
    def experiment(self) -> dict[str, Any]:
        return self.manifest.get("experiment") or {}

    @property
    def period(self) -> str | None:
        return self.experiment.get("period")

    @property
    def cash(self) -> float:
        return float(self.experiment.get("cash") or self.equity["equity"].iloc[0])

    @property
    def symbols(self) -> list[str]:
        """The run's universe, without the T-bill ETFs."""
        syms = self.manifest.get("symbols") or sorted(set(self.trades.get("symbol", [])))
        return [s for s in syms if s not in CASH_VEHICLES]

    @property
    def strategy(self) -> str:
        return (self.manifest.get("strategy") or {}).get("name") or self.experiment.get("strategy") or "strategy"

    def share_trades(self, market: FrozenMarket) -> pd.DataFrame:
        """Fills of stocks and ETFs (options left out), in the order they happened."""
        t = self.trades
        if t.empty:
            return t
        keep = t["side"].isin(["BUY", "SELL"]) & ~t["tag"].fillna("").str.startswith("option")
        keep &= t["symbol"].map(market.has)
        return t[keep]


def is_record(folder: Path) -> bool:
    return (Path(folder) / "manifest.json").is_file() and (Path(folder) / "equity.csv").is_file()


def resolve(run: str | Path) -> Path:
    """The folder of a run id, a record folder or a finished trading session (a folder of records works too)."""
    p = Path(run).expanduser()
    if p.is_dir():
        return p
    if (RUNS_DIR / str(run)).is_dir():
        return RUNS_DIR / str(run)
    from .agent import SESSIONS_DIR

    rec = SESSIONS_DIR / str(run) / "record"
    if is_record(rec):
        return rec
    raise FileNotFoundError(f"no saved experiment, record folder or finished trading session called {run}")


TRADE_COLUMNS = ["date", "symbol", "side", "qty", "price", "value", "commission", "tag", "reason", "pnl",
                 "holding_days"]


def _csv(path: Path, **kw: Any) -> pd.DataFrame | None:
    if not path.is_file():
        return None
    try:
        return pd.read_csv(path, **kw)
    except pd.errors.EmptyDataError:
        return None


def _with_decision_reasons(trades: pd.DataFrame, dec: pd.DataFrame | None, days: pd.DatetimeIndex) -> pd.DataFrame:
    """Each fill takes the reason of the order behind it in decisions.csv (decided the trading day before)."""
    if dec is None or dec.empty or trades.empty or not {"kind", "date", "symbol", "action", "reason"} <= set(dec):
        return trades
    o = dec[dec["kind"] == "order"]
    nxt = days.searchsorted(pd.to_datetime(o["date"]), side="right")
    fill = [days[i] if i < len(days) else pd.NaT for i in nxt]
    why = {(f, s, a): r for f, s, a, r in zip(fill, o["symbol"], o["action"], o["reason"], strict=True)
           if isinstance(r, str) and r}
    out = trades.copy()
    out["reason"] = [why.get((d, s, a), r) for d, s, a, r in zip(out["date"], out["symbol"], out["side"],
                                                                  out["reason"], strict=True)]
    return out


def load_record(folder: str | Path) -> Record:
    folder = Path(folder)
    if not is_record(folder):
        raise FileNotFoundError(f"{folder} is not an experiment record (it needs manifest.json and equity.csv)")
    man = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    eq = pd.read_csv(folder / "equity.csv", index_col=0)
    eq.index = pd.to_datetime(eq.index)
    tr = _csv(folder / "trades.csv", dtype={"symbol": str, "side": str, "tag": str, "reason": str})
    if tr is None or tr.empty:
        tr = pd.DataFrame(columns=TRADE_COLUMNS)
    for c in TRADE_COLUMNS:
        if c not in tr:
            tr[c] = "" if c in ("tag", "reason") else np.nan
    tr["commission"] = pd.to_numeric(tr["commission"], errors="coerce").fillna(0.0)
    tr["date"] = pd.to_datetime(tr["date"])
    dec = _csv(folder / "decisions.csv")
    tr = _with_decision_reasons(tr.reset_index(drop=True), dec, eq.index)
    ppath = folder / "policy_log.json"
    pol = json.loads(ppath.read_text(encoding="utf-8")) if ppath.is_file() else None
    return Record(folder, man, eq, tr, dec, pol)


class _Prices:
    """Total-return opens and closes of the symbols on the market's calendar (closes carried over gaps)."""

    def __init__(self, market: FrozenMarket, symbols: list[str]):
        self.index = market.calendar
        cols = [s for s in dict.fromkeys(["SPY", *symbols]) if market.has(s)]
        frames = {s: market.frame(s) for s in cols}
        self.open = pd.DataFrame({s: f["open"].reindex(self.index) for s, f in frames.items()}, index=self.index)
        self.close = pd.DataFrame({s: f["close"].reindex(self.index) for s, f in frames.items()},
                                  index=self.index).ffill()
        self.o, self.c = self.open.to_numpy(dtype=float), self.close.to_numpy(dtype=float)

    def days(self, dates: Any) -> np.ndarray:
        return self.index.get_indexer(pd.DatetimeIndex(pd.to_datetime(list(dates))))

    def cols(self, symbols: Any) -> np.ndarray:
        return self.open.columns.get_indexer(list(symbols))

    def forward(self, day: np.ndarray, col: np.ndarray, h: int) -> np.ndarray:
        """Return from the open of ``day`` to the close of the h-th trading day counted from it (NaN if unknown)."""
        out = np.full(len(day), np.nan)
        end = day + h - 1
        ok = (day >= 0) & (col >= 0) & (end < len(self.index))
        with np.errstate(divide="ignore", invalid="ignore"):
            out[ok] = self.c[end[ok], col[ok]] / self.o[day[ok], col[ok]] - 1.0
        return out


def trigger(reason: Any) -> str:
    """'news' when the decision's reason names news, 'no reason' for an empty or default one, else 'other'."""
    text = "" if reason is None or (isinstance(reason, float) and math.isnan(reason)) else str(reason).strip()
    if text.lower() in NO_REASON:
        return "no reason"
    return "news" if NEWS_RE.search(text) else "other"


# ----------------------------------------------------------------------------------- 1. decisions
def decision_rows(rec: Record, px: _Prices, market: FrozenMarket) -> pd.DataFrame:
    """One row per stock buy or sell: its fill day and the excess return over SPY after it, by horizon."""
    t = rec.share_trades(market)
    t = t[~t["symbol"].isin(CASH_VEHICLES)] if len(t) else t
    base = ["date", "symbol", "side", "tag", "reason", "trigger", "day"]
    cols = base + [f"{k}_{h}d" for h in HORIZONS for k in ("excess", "signed")]
    if t.empty:
        return pd.DataFrame(columns=cols)
    rows = t[["date", "symbol", "side", "tag", "reason"]].copy().reset_index(drop=True)
    rows["trigger"] = rows["reason"].map(trigger)
    day = px.days(rows["date"])
    col = px.cols(rows["symbol"])
    spy = np.full(len(rows), px.cols(["SPY"])[0])
    sign = np.where(rows["side"].to_numpy() == "BUY", 1.0, -1.0)
    rows["day"] = day
    for h in HORIZONS:
        ex = px.forward(day, col, h) - px.forward(day, spy, h)
        rows[f"excess_{h}d"] = ex
        rows[f"signed_{h}d"] = sign * ex
    return rows[rows["day"] >= 0][cols].reset_index(drop=True)


def decision_stats(rows: pd.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {"horizons": list(HORIZONS), "buys": {}, "sells": {}, "all": {}, "by_trigger": {},
                           "counts": {}}
    buys = rows["side"] == "BUY"
    for trig, g in rows.groupby("trigger"):  # every decision, whether or not a window fits after it
        out["counts"][str(trig)] = {"buys": int((g["side"] == "BUY").sum()), "sells": int((g["side"] != "BUY").sum())}
    for h in HORIZONS:
        key = f"signed_{h}d"
        for name, mask in (("buys", buys), ("sells", ~buys), ("all", pd.Series(True, index=rows.index))):
            out[name][f"{h}d"] = mean_test(rows.loc[mask, key], rows.loc[mask, "day"], overlap=h)
    for trig, g in rows.groupby("trigger"):
        b = g["side"] == "BUY"
        out["by_trigger"][str(trig)] = {
            "buys": mean_test(g.loc[b, f"signed_{IC_HORIZON}d"], g.loc[b, "day"], overlap=IC_HORIZON),
            "sells": mean_test(g.loc[~b, f"signed_{IC_HORIZON}d"], g.loc[~b, "day"], overlap=IC_HORIZON)}
    return out


def holdings(rec: Record, px: _Prices, market: FrozenMarket) -> pd.DataFrame:
    """Shares held after each day's fills, on the market's calendar."""
    t = rec.share_trades(market)
    cols = sorted(set(rec.symbols) | set(t["symbol"] if len(t) else []))
    if t.empty:
        return pd.DataFrame(0.0, index=px.index, columns=cols)
    signed = t["qty"].astype(float) * np.where(t["side"] == "BUY", 1.0, -1.0)
    delta = signed.groupby([t["date"], t["symbol"]]).sum().unstack(fill_value=0.0)
    q = delta.reindex(index=px.index, columns=cols, fill_value=0.0).fillna(0.0).cumsum()
    return q.mask(q.abs() < 1e-9, 0.0)


def ic_summary(values: list[float], days: list[int]) -> dict[str, Any]:
    m = mean_test(values, days, overlap=IC_HORIZON)
    sd = float(np.std(values, ddof=1)) if len(values) > 1 else None
    return {"days": m["n"], "mean": m["mean"], "std": sd,
            "ir": m["mean"] / sd if sd and m["mean"] is not None else None,
            "t": m["t"], "t_iid": m["t_iid"], "positive_share": m["hit_rate"]}


def ic_stats(rec: Record, px: _Prices, q: pd.DataFrame, market: FrozenMarket) -> dict[str, Any]:
    """Rank IC of each decision day against the next five days' returns, from the open of the fill day."""
    universe = [s for s in rec.symbols if s in px.open.columns]
    out: dict[str, Any] = {"horizon_days": IC_HORIZON, "universe": len(universe)}
    if len(universe) < 3:
        out["skipped"] = "fewer than 3 stocks in the universe"
        return out
    steps = (rec.policy_log or {}).get("steps") or []
    if steps:  # a policy decides on its steps only
        when = sorted({pd.Timestamp(s["day"]) for s in steps if s.get("day")})
        out["decision_days"] = "the policy's steps (policy_log.json)"
    else:
        when = list(rec.equity.index[:-1])
        out["decision_days"] = "every trading day of the run but the last (a strategy decides after every close)"
    t = rec.share_trades(market)
    t = t[t["symbol"].isin(universe)] if len(t) else t
    fills: dict[int, pd.Series] = {}
    if len(t):
        signed = t["qty"].astype(float) * np.where(t["side"] == "BUY", 1.0, -1.0)
        for d, g in signed.groupby(px.days(t["date"])):
            fills[int(d)] = g.groupby(t.loc[g.index, "symbol"]).sum()
    ucol = px.cols(universe)
    qv = q.reindex(columns=universe).to_numpy(dtype=float)
    held_ic: list[float] = []
    held_days: list[int] = []
    dir_ic: list[float] = []
    dir_days: list[int] = []
    n = len(px.index)
    for p in px.days(when):
        f = int(p) + 1  # orders fill at the next open
        if p < 0 or f + IC_HORIZON - 1 >= n:
            continue
        with np.errstate(divide="ignore", invalid="ignore"):
            fwd = pd.Series(px.c[f + IC_HORIZON - 1, ucol] / px.o[f, ucol] - 1.0, index=universe)
        weights = pd.Series(qv[f] * px.o[f, ucol], index=universe)
        ic = rank_ic(weights, fwd)
        if ic is not None:
            held_ic.append(ic)
            held_days.append(f)
        flow = fills.get(f)
        if flow is not None and (flow != 0).any():
            direction = pd.Series(0.0, index=universe)
            direction.loc[flow.index] = np.sign(flow.to_numpy())
            ic = rank_ic(direction, fwd)
            if ic is not None:
                dir_ic.append(ic)
                dir_days.append(f)
    out["decision"] = {"score": "+1 bought, -1 sold, 0 left alone, on each day it traded",
                       **ic_summary(dir_ic, dir_days)}
    out["holdings"] = {"score": "the weights it held after each decision (0 for stocks not held)",
                       **ic_summary(held_ic, held_days)}
    return out


# ----------------------------------------------------------------------------------- 2. factors
def factor_attribution(rec: Record, fac: pd.DataFrame) -> dict[str, Any]:
    eq = rec.equity["equity"].astype(float)
    r = eq.pct_change().iloc[1:]
    f = fac.reindex(r.index)
    y = r - f["rf"]
    x = f[list(FACTORS)]
    ok = y.notna() & x.notna().all(axis=1)
    out: dict[str, Any] = {"days": int(ok.sum()), "factors": {k: FACTOR_LABELS[k] for k in FACTORS}}
    if ok.sum() < 30:
        out["skipped"] = "fewer than 30 days with all factors"
        return out
    yy, xx = y[ok], x[ok]
    four = ols_newey_west(yy, xx, list(FACTORS))
    capm = ols_newey_west(yy, xx[["market"]], ["market"], lags=four["lags"])
    means = xx.mean()
    out.update({
        "lags": four["lags"],
        "alpha_annual": four["coef"]["alpha"] * TRADING_DAYS, "alpha_t": four["t"]["alpha"],
        "exposures": {k: {"beta": four["coef"][k], "t": four["t"][k]} for k in FACTORS},
        "r2": four["r2"], "adj_r2": four["adj_r2"], "resid_vol_annual": four["resid_vol_annual"],
        "factor_means_annual": {k: float(means[k] * TRADING_DAYS) for k in FACTORS},
        "contributions_annual": {**{k: float(four["coef"][k] * means[k] * TRADING_DAYS) for k in FACTORS},
                                 "alpha": four["coef"]["alpha"] * TRADING_DAYS},
        "excess_return_annual": float(yy.mean() * TRADING_DAYS),
        "capm": {"beta": capm["coef"]["market"], "beta_t": capm["t"]["market"],
                 "alpha_annual": capm["coef"]["alpha"] * TRADING_DAYS, "alpha_t": capm["t"]["alpha"],
                 "r2": capm["r2"]},
    })
    return out


# ----------------------------------------------------------------------------------- 3. P&L
def episodes(rec: Record, px: _Prices, market: FrozenMarket) -> list[dict[str, Any]]:
    """Every position from its first buy to the sell that closes it; positions still open are marked at the last
    close. A position's P&L is the cash it returned minus the cash it took, commissions included."""
    from ..universe import sector_of

    t = rec.share_trades(market)
    if t.empty:
        return []
    days = px.days(t["date"])
    last = rec.equity.index[-1]
    last_day = int(px.days([last])[0])
    open_: dict[str, dict[str, Any]] = {}
    done: list[dict[str, Any]] = []
    for (_, row), day in zip(t.iterrows(), days, strict=True):
        sym, qty, price = row["symbol"], float(row["qty"]), float(row["price"])
        comm = float(row["commission"]) if pd.notna(row["commission"]) else 0.0
        reason = "" if pd.isna(row["reason"]) else str(row["reason"])
        ep = open_.get(sym)
        if row["side"] == "BUY":
            if ep is None:
                ep = open_[sym] = {"symbol": sym, "entry": row["date"], "entry_day": int(day), "entry_reason": reason,
                                   "qty": 0.0, "flow": 0.0, "cost": 0.0, "buys": 0, "sells": 0}
            ep["qty"] += qty
            ep["flow"] -= qty * price + comm
            ep["cost"] += qty * price + comm
            ep["buys"] += 1
        elif ep is not None:
            ep["qty"] -= qty
            ep["flow"] += qty * price - comm
            ep["sells"] += 1
            if ep["qty"] <= 1e-9:
                ep.update(exit=row["date"], exit_day=int(day), exit_reason=reason, open=False, pnl=ep["flow"])
                done.append(ep)
                del open_[sym]
    for sym, ep in open_.items():
        mark = float(px.close.at[last, sym]) if sym in px.close.columns else math.nan
        ep.update(exit=None, exit_day=last_day, exit_reason=None, open=True, pnl=ep["flow"] + ep["qty"] * mark)
        done.append(ep)
    for ep in done:
        ep["holding_days"] = ep["exit_day"] - ep["entry_day"]
        ep["return"] = ep["pnl"] / ep["cost"] if ep["cost"] > 0 else None
        ep["sector"] = "T-bills" if ep["symbol"] in CASH_VEHICLES else sector_of(ep["symbol"])
        ep["cash_vehicle"] = ep["symbol"] in CASH_VEHICLES
        ep["entry_trigger"] = trigger(ep["entry_reason"])
        ep["exit_trigger"] = "still open" if ep["open"] else trigger(ep["exit_reason"])
    return sorted(done, key=lambda e: (e["entry_day"], e["symbol"]))


def cash_interest(rec: Record, market: FrozenMarket) -> float | None:
    """Interest on idle cash: the engine multiplies cash by 1 + BIL's daily return after each day's fills."""
    eq = rec.equity
    if "cash_ratio" not in eq or not market.has("BIL"):
        return None
    bil = market.frame("BIL")["close"].pct_change().reindex(eq.index).fillna(0.0).clip(-0.01, 0.01)
    cash = eq["cash_ratio"].astype(float) * eq["equity"].astype(float)
    return float((cash * bil / (1.0 + bil)).sum())


def _bucket(days: int) -> str:
    for lo, hi in HOLDING_BUCKETS:
        if days >= lo and (hi is None or days <= hi):
            return f"{lo}+ days" if hi is None else f"{lo}-{hi} days"
    return "same day"


def _group(eps: list[dict[str, Any]], key: str, cash: float, order: list[str] | None = None) -> list[dict[str, Any]]:
    """P&L by group. ``contribution`` is the P&L over the starting cash (percentage points of the run's return);
    ``share`` is the share of the positions' net P&L, which runs past 100% when winners and losers nearly cancel."""
    total = sum(e["pnl"] for e in eps)
    groups: dict[str, list[dict[str, Any]]] = {}
    for e in eps:
        groups.setdefault(str(e[key]), []).append(e)
    rows = []
    for g, items in groups.items():
        pnl = float(sum(e["pnl"] for e in items))
        rows.append({"group": g, "pnl": pnl, "contribution": pnl / cash if cash else None,
                     "share": pnl / total if abs(total) > 1e-9 else None,
                     "trades": len(items), "win_rate": sum(e["pnl"] > 0 for e in items) / len(items),
                     "avg_pnl": pnl / len(items), "still_open": sum(bool(e["open"]) for e in items)})
    if order:
        rank = {g: i for i, g in enumerate(order)}
        return sorted(rows, key=lambda r: rank.get(r["group"], len(rank)))
    return sorted(rows, key=lambda r: -r["pnl"])


def regime_table(rec: Record) -> list[dict[str, Any]]:
    """The run's P&L and return, and SPY's, by the market regime of the previous close (the one positions were
    held under)."""
    eq = rec.equity
    if "regime" not in eq:
        return []
    e = eq["equity"].astype(float)
    prev = e.shift(1).fillna(rec.cash)
    pnl, r = e - prev, e / prev - 1.0
    spy = eq["spy"].astype(float) if "spy" in eq else None
    rs = spy / spy.shift(1).fillna(rec.cash) - 1.0 if spy is not None else None
    regime = eq["regime"].astype(str).shift(1)
    regime.iloc[0] = str(eq["regime"].iloc[0])
    expo = eq["exposure"].astype(float).shift(1).fillna(0.0) if "exposure" in eq else None
    total = float(pnl.sum())
    rows = []
    for g in [*REGIMES, *sorted(set(regime) - set(REGIMES))]:
        m = regime == g
        if not m.any():
            continue
        rows.append({"group": g, "label": REGIME_LABELS.get(g, g.lower()), "days": int(m.sum()),
                     "share_of_days": float(m.mean()), "pnl": float(pnl[m].sum()),
                     "contribution": float(pnl[m].sum()) / rec.cash,
                     "share": float(pnl[m].sum()) / total if abs(total) > 1e-9 else None,
                     "return": float((1.0 + r[m]).prod() - 1.0),
                     "spy_return": float((1.0 + rs[m]).prod() - 1.0) if rs is not None else None,
                     "avg_exposure": float(expo[m].mean()) if expo is not None else None})
    return rows


def _trade_row(e: dict[str, Any]) -> dict[str, Any]:
    return {"symbol": e["symbol"], "sector": e["sector"], "entry": e["entry"], "exit": e["exit"],
            "holding_days": e["holding_days"], "pnl": e["pnl"], "return": e["return"], "open": e["open"],
            "entry_reason": e["entry_reason"], "exit_reason": e["exit_reason"]}


def pnl_breakdown(rec: Record, px: _Prices, market: FrozenMarket) -> dict[str, Any]:
    eps = episodes(rec, px, market)
    stocks = [e for e in eps if not e["cash_vehicle"]]
    total = float(rec.equity["equity"].iloc[-1]) - rec.cash
    stock_pnl = float(sum(e["pnl"] for e in stocks))
    tbill_etfs = float(sum(e["pnl"] for e in eps if e["cash_vehicle"]))
    interest = cash_interest(rec, market)
    other = total - stock_pnl - tbill_etfs - (interest or 0.0)
    for e in stocks:
        e["holding"] = _bucket(int(e["holding_days"]))
    ranked = sorted(stocks, key=lambda e: -e["pnl"])
    gains = sum(max(e["pnl"], 0.0) for e in stocks)
    cash = rec.cash
    by_stock = _group(stocks, "symbol", cash)
    conc: dict[str, Any] = {"trades": len(stocks)}
    if ranked:
        best, top2 = ranked[0]["pnl"], sum(max(e["pnl"], 0.0) for e in ranked[:2])
        conc.update({
            "best_trade": _trade_row(ranked[0]),
            "best_trade_share": best / stock_pnl if stock_pnl > 0 else None,
            "top2_share": top2 / stock_pnl if stock_pnl > 0 else None,
            "best_trade_share_of_gains": best / gains if gains > 0 else None,
            "top2_share_of_gains": top2 / gains if gains > 0 else None,
            "best_stock": by_stock[0]["group"],
            "best_stock_share": by_stock[0]["pnl"] / stock_pnl if stock_pnl > 0 else None,
            "return": total / cash,
            "return_without_best_trade": (total - max(best, 0.0)) / cash,
            "return_without_top2": (total - top2) / cash,
            # the profit rests on the best two positions: they made half of the positions' net P&L or more
            "concentrated": bool(stock_pnl > 0 and top2 >= 0.5 * stock_pnl),
        })
    return {
        "initial_cash": cash, "total": total, "return": total / cash,
        "reconciliation": {"stocks": stock_pnl, "tbill_etfs": tbill_etfs, "cash_interest": interest, "other": other},
        "positions": len(stocks),
        "by_holding_period": _group(stocks, "holding", cash,
                                    [f"{lo}+ days" if hi is None else f"{lo}-{hi} days" for lo, hi in HOLDING_BUCKETS]),
        "by_regime": regime_table(rec),
        "by_trigger": {"entry": _group(stocks, "entry_trigger", cash, ["news", "other", "no reason"]),
                       "exit": _group(stocks, "exit_trigger", cash, ["news", "other", "no reason", "still open"])},
        "by_sector": _group(stocks, "sector", cash),
        "by_stock": by_stock,
        "best_trades": [_trade_row(e) for e in ranked[:5]],
        "worst_trades": [_trade_row(e) for e in reversed(ranked[-5:]) if e["pnl"] < 0],
        "concentration": conc,
    }


# ----------------------------------------------------------------------------------- the whole analysis
def analyze_run(run: str | Path, market: FrozenMarket | None = None, save: bool = True, chart: bool = True,
                momentum_universe: list[str] | None = None) -> dict[str, Any]:
    """Analyze a saved record (a run id or its folder); see the module docstring. With ``save`` it writes
    analysis.json, analysis.md and (with ``chart``) analysis.png into the record's folder. ``market`` defaults to
    the bundled market of the record's period."""
    folder = resolve(run)
    rec = load_record(folder)
    if market is None:
        market = bundled_market(rec.period)
    notes = []
    data_sha = ((rec.manifest.get("dataset") or {}).get("prices") or {}).get("sha256")
    if data_sha and data_sha != market.fingerprint():
        notes.append(f"The record was made on prices with SHA-256 {data_sha[:16]}..., the analysis uses "
                     f"{market.fingerprint()[:16]}...: numbers can differ from those of the run.")
    traded = sorted(set(rec.trades["symbol"].dropna())) if len(rec.trades) else []
    px = _Prices(market, [*rec.symbols, *traded])
    rows = decision_rows(rec, px, market)
    q = holdings(rec, px, market)
    fac = factor_returns(market, momentum_universe)
    e = rec.equity
    res: dict[str, Any] = {
        "schema": SCHEMA, "run_id": rec.manifest.get("run_id") or folder.name, "folder": str(folder),
        "strategy": rec.strategy, "kind": "policy" if rec.policy_log is not None else "strategy",
        "period": market.period, "start": str(e.index[0].date()), "end": str(e.index[-1].date()),
        "stocks": len(rec.symbols), "news": rec.experiment.get("news"),
        "params": (rec.manifest.get("strategy") or {}).get("params") or rec.experiment.get("params"),
        "total_return": float(e["equity"].iloc[-1] / e["equity"].iloc[0]) - 1.0,  # as metrics.json has it
        "spy_return": float(e["spy"].iloc[-1] / e["spy"].iloc[0] - 1.0) if "spy" in e else None,
        "decisions": decision_stats(rows),
        "ic": ic_stats(rec, px, q, market),
        "factors": factor_attribution(rec, fac),
        "pnl": pnl_breakdown(rec, px, market),
        "notes": notes,
    }
    res["summary"] = summary(res)
    res["findings"] = findings(res)
    if save:
        clean = jsonable(res)
        (folder / "analysis.json").write_text(json.dumps(clean, indent=1) + "\n", encoding="utf-8")
        (folder / "analysis.md").write_text(render_markdown(res), encoding="utf-8")
        files = ["analysis.json", "analysis.md"]
        if chart:
            try:
                draw(res, folder / "analysis.png")
                files.append("analysis.png")
            except Exception as exc:  # noqa: BLE001 - the chart is a nicety; the numbers are saved
                res["notes"].append(f"no chart: {exc}")
        res["files"] = files
    return res


def summary(res: dict[str, Any]) -> dict[str, Any]:
    """The headline numbers, flat, for leaderboards."""
    d, ic, f, c = res["decisions"], res["ic"], res["factors"], res["pnl"]["concentration"]
    a5, b5, s5 = d["all"]["5d"], d["buys"]["5d"], d["sells"]["5d"]
    ex = f.get("exposures") or {}
    counts = d.get("counts") or {}
    n_all = sum(v["buys"] + v["sells"] for v in counts.values())
    news = counts.get("news") or {}
    entry = {r["group"]: r for r in res["pnl"]["by_trigger"]["entry"]}
    contrib = f.get("contributions_annual") or {}
    return {
        "total_return": res["total_return"], "spy_return": res["spy_return"],
        "decisions": a5["n"] if a5["n"] else d["all"]["1d"]["n"],
        "decision_excess_5d": a5["mean"], "decision_excess_5d_t": a5["t"], "decision_hit_rate_5d": a5["hit_rate"],
        "buy_excess_5d": b5["mean"], "buy_excess_5d_t": b5["t"],
        "sell_avoided_5d": s5["mean"], "sell_avoided_5d_t": s5["t"],
        "decision_ic": (ic.get("decision") or {}).get("mean"), "decision_ic_t": (ic.get("decision") or {}).get("t"),
        "holdings_ic": (ic.get("holdings") or {}).get("mean"), "holdings_ic_t": (ic.get("holdings") or {}).get("t"),
        "alpha_annual": f.get("alpha_annual"), "alpha_t": f.get("alpha_t"),
        "capm_alpha_annual": (f.get("capm") or {}).get("alpha_annual"),
        "capm_alpha_t": (f.get("capm") or {}).get("alpha_t"),
        **{f"beta_{k}": (ex.get(k) or {}).get("beta") for k in FACTORS},
        "r2": f.get("r2"),
        # the part of the mean excess return a year that the four exposures explain; the rest is alpha_annual
        "exposure_return_annual": sum(contrib[k] for k in FACTORS) if contrib else None,
        "news_decision_share": (news.get("buys", 0) + news.get("sells", 0)) / n_all if n_all else None,
        "news_entry_pnl": entry["news"]["pnl"] if "news" in entry else (0.0 if entry else None),
        "best_trade_share": c.get("best_trade_share"), "return_without_best_trade": c.get("return_without_best_trade"),
        "concentrated": c.get("concentrated"),
    }


# ----------------------------------------------------------------------------------- words
def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


def pct(v: Any, nd: int = 1, sign: bool = True) -> str:
    x = _f(v)
    if x is None:
        return "n/a"
    return f"{x * 100:+.{nd}f}%" if sign else f"{x * 100:.{nd}f}%"


def money(v: Any) -> str:
    x = _f(v)
    if x is None:
        return "n/a"
    return f"{'+' if x >= 0 else '-'}${abs(x):,.0f}"


def num(v: Any, nd: int = 2) -> str:
    x = _f(v)
    return "n/a" if x is None else f"{x:.{nd}f}"


def _sig(t: Any) -> str:
    x = _f(t)
    if x is None:
        return ""
    return "significant at 5%" if abs(x) >= 1.96 else "within noise"


def _day(v: Any) -> str:
    return "" if v is None or (isinstance(v, float) and math.isnan(v)) else str(v)[:10]


def _count(n: int, what: str = "position") -> str:
    return f"{n} {what}{'' if n == 1 else 's'}"


def _factor_verdict(f: dict[str, Any]) -> str:
    """Is the return factor exposure, a significant alpha, or too noisy to tell?"""
    c = f["contributions_annual"]
    excess = _f(f["excess_return_annual"]) or 0.0
    exposure = float(sum(c[k] for k in FACTORS))
    at = _f(f["alpha_t"])
    sig = at is not None and abs(at) >= 1.96
    if excess != 0 and exposure / excess >= 0.5:
        main = max(FACTORS, key=lambda k: c[k] * math.copysign(1.0, excess))
        return (f"Verdict: most of the {'return' if excess > 0 else 'loss'} is factor exposure ({pct(exposure)} a "
                f"year, {main} above all); the alpha left is {'significant too' if sig else 'not significant'}.")
    if sig:
        return (f"Verdict: the alpha is significant: the run earned {'more' if (at or 0) > 0 else 'less'} than its "
                "market and style exposures explain.")
    return (f"Verdict: the exposures account for {pct(exposure)} a year of it, and the {pct(f['alpha_annual'])} a "
            "year left over is not significant: one period of daily returns cannot tell it from luck.")


def findings(res: dict[str, Any]) -> list[str]:
    """The answers in plain English, from the numbers."""
    out: list[str] = []
    d = res["decisions"]
    b5, s5 = d["buys"]["5d"], d["sells"]["5d"]
    if b5["n"]:
        m = _f(b5["mean"]) or 0.0
        out.append(f"Buys: over the five trading days from the fill, the stocks it bought "
                   f"{'beat' if m >= 0 else 'trailed'} SPY by {pct(abs(m), 2, False)} on average ({b5['n']} buys, "
                   f"{pct(b5['hit_rate'], 0, False)} of them ahead of SPY; t = {num(b5['t'], 1)}, {_sig(b5['t'])}).")
    if s5["n"]:
        m = _f(s5["mean"]) or 0.0
        what = (f"lagged SPY by {pct(m, 2, False)}, so selling avoided that" if m >= 0
                else f"beat SPY by {pct(-m, 2, False)}, so selling gave that up")
        out.append(f"Sells: over the five trading days from the fill, the stocks it sold {what} ({s5['n']} sells; "
                   f"t = {num(s5['t'], 1)}, {_sig(s5['t'])}).")
    if not b5["n"] and not s5["n"]:
        out.append("No decision has five trading days of prices after it, so there are no decision statistics.")
    ic = res["ic"]
    di, hi = ic.get("decision") or {}, ic.get("holdings") or {}
    if di.get("days"):
        out.append(f"Decision IC: on the {di['days']} days it traded, what it bought ranked against what it sold and "
                   f"left alone by a mean rank IC of {num(di['mean'], 3)} with the next five days' returns (IC IR "
                   f"{num(di['ir'])}, t = {num(di['t'], 1)}, {_sig(di['t'])}).")
    if hi.get("days"):
        out.append(f"Holdings IC: the weights it held after each of {hi['days']} decision days had a mean rank IC of "
                   f"{num(hi['mean'], 3)} with the next five days' returns (t = {num(hi['t'], 1)}, {_sig(hi['t'])}).")
    f = res["factors"]
    if "exposures" in f:
        ex = f["exposures"]
        betas = ", ".join(f"{k} {num(ex[k]['beta'])} (t {num(ex[k]['t'], 1)})" for k in FACTORS)
        out.append(f"Factor exposures: {betas}; together they explain {pct(f['r2'], 0, False)} of the daily "
                   "variation (R²).")
        c = f["contributions_annual"]
        parts = sorted(FACTORS, key=lambda k: -abs(c[k]))
        out.append(f"Of the {pct(f['excess_return_annual'])} a year it earned over T-bills (arithmetic mean): "
                   + ", ".join(f"{k} {pct(c[k])}" for k in parts)
                   + f"; the alpha left is {pct(f['alpha_annual'])} a year (t = {num(f['alpha_t'], 1)}, "
                   f"{_sig(f['alpha_t'])}).")
        out.append(_factor_verdict(f))
    p = res["pnl"]
    rc = p["reconciliation"]
    if p["positions"]:
        out.append(f"P&L: {money(p['total'])} in all, {money(rc['stocks'])} from {p['positions']} positions and "
                   f"{money(rc['cash_interest'])} of interest on idle cash.")
    for key, what in (("by_holding_period", "By holding period"), ("by_sector", "By sector"),
                      ("by_stock", "By stock")):
        rows = p[key]
        if len(rows) >= 2:
            best, worst = max(rows, key=lambda r: r["pnl"]), min(rows, key=lambda r: r["pnl"])
            out.append(f"{what}: {best['group']} made the most ({money(best['pnl'])}, {_count(best['trades'])}); "
                       f"{worst['group']} the least ({money(worst['pnl'])}, {_count(worst['trades'])}).")
    if p["by_regime"]:
        parts = [f"{r['label']} {r['days']} days {pct(r['return'])} (SPY {pct(r['spy_return'])})" for r in p["by_regime"]]
        out.append("By market regime of the previous close: " + "; ".join(parts) + ".")
    ent = {r["group"]: r for r in p["by_trigger"]["entry"]}
    ext = {r["group"]: r for r in p["by_trigger"]["exit"]}
    if "news" in ent or "news" in ext:
        bits = []
        if "news" in ent:
            rest = p["positions"] - ent["news"]["trades"]
            if rest:
                other = sum(r["pnl"] for g, r in ent.items() if g != "news")
                bits.append(f"{ent['news']['trades']} positions opened on news made {money(ent['news']['pnl'])}, the "
                            f"other {rest} {money(other)}")
            else:
                bits.append(f"all {ent['news']['trades']} positions were opened on news and made "
                            f"{money(ent['news']['pnl'])}")
        if "news" in ext:
            bits.append(f"{ext['news']['trades']} positions closed on news made {money(ext['news']['pnl'])}")
        nb = (d["by_trigger"].get("news") or {}).get("buys") or {}
        if nb.get("n"):
            m = _f(nb["mean"]) or 0.0
            bits.append(f"over the five days from the fill, news-triggered buys {'beat' if m >= 0 else 'trailed'} SPY "
                        f"by {pct(abs(m), 2, False)} (t {num(nb['t'], 1)})")
        out.append("News: " + "; ".join(bits) + ".")
    elif p["positions"] and res.get("news") is not False:
        out.append("News: no entry or exit gave news as its reason. News can still have shaped the run: a filter "
                   "that keeps it out of a stock leaves no decision behind, so compare with the run without news.")
    out += _concentration_words(p)
    return out


def _concentration_words(p: dict[str, Any]) -> list[str]:
    c = p["concentration"]
    bt = c.get("best_trade")
    if not bt:
        return []
    held = "still open at the end" if bt["open"] else f"sold {_day(bt['exit'])}"
    best = f"{bt['symbol']} bought {_day(bt['entry'])} and {held}, {money(bt['pnl'])}"
    stocks = p["reconciliation"]["stocks"]
    if stocks <= 0:
        text = f"Concentration: the positions lost {money(stocks)} in all; the best was {best}."
    elif c["concentrated"]:
        if (c.get("top2_share_of_gains") or 0) >= 0.4:
            text = (f"Concentration: the profit rests on the best two positions, which made "
                    f"{pct(c['top2_share'], 0, False)} of the positions' net P&L and "
                    f"{pct(c['top2_share_of_gains'], 0, False)} of all their gains; the best was {best}.")
        else:
            text = (f"Concentration: the gains are spread out (the best two positions made "
                    f"{pct(c['top2_share_of_gains'], 0, False)} of them) but winners and losers nearly cancel, so the "
                    f"best two made {pct(c['top2_share'], 0, False)} of the net P&L; the best was {best}.")
    else:
        text = (f"Concentration: the profit is spread out; the best two positions made "
                f"{pct(c['top2_share'], 0, False)} of the positions' net P&L, the best being {best}.")
    return [text + f" Had the best position made nothing, the run would have returned "
                   f"{pct(c['return_without_best_trade'])} instead of {pct(c['return'])}."]


# ----------------------------------------------------------------------------------- markdown
def _table(head: list[str], rows: list[list[str]], align: str | None = None) -> list[str]:
    align = align or ("---|" + "---:|" * (len(head) - 1))
    return ["| " + " | ".join(head) + " |", "|" + align] + ["| " + " | ".join(r) + " |" for r in rows] + [""]


def _group_rows(rows: list[dict[str, Any]], label: str = "group") -> list[list[str]]:
    return [[str(r[label]), str(r["trades"]), money(r["pnl"]), pct(r["contribution"]),
             pct(r["win_rate"], 0, False), str(r["still_open"] or "")] for r in rows]


def render_markdown(res: dict[str, Any]) -> str:
    d, ic, f, p = res["decisions"], res["ic"], res["factors"], res["pnl"]
    lines = [f"# What drove {res['strategy']}: {res['start']} to {res['end']}", ""]
    news = {True: "news replayed", False: "news off"}.get(res.get("news"), "")
    lines += [f"Run `{res['run_id']}`: the {res['period']} period, {res['stocks']} stocks{', ' + news if news else ''}. "
              f"Return {pct(res['total_return'])} against SPY's {pct(res['spy_return'])}. Written by `riskaware "
              "research analyze`; definitions at the end.", ""]
    lines += ["## Findings", ""] + [f"- {x}" for x in res["findings"]] + [""]
    for n in res.get("notes") or []:
        lines += [f"> {n}", ""]

    lines += ["## Decisions", "",
              "The stock's return minus SPY's after each buy and sell, from the open of the day the order filled, on "
              "total-return prices. A sell is credited with the excess it avoided (SPY's return minus the stock's). "
              "*t* allows for decisions on the same day and for overlapping windows; *t (iid)* treats every decision "
              "as independent.", ""]
    rows = []
    for side, label in (("buys", "Buys"), ("sells", "Sells (avoided)"), ("all", "All")):
        for h in HORIZONS:
            m = d[side][f"{h}d"]
            if m["n"]:
                rows.append([label, f"{h} day{'s' if h > 1 else ''}", str(m["n"]), pct(m["mean"], 2),
                             pct(m["median"], 2), pct(m["hit_rate"], 0, False), num(m["t"], 1), num(m["t_iid"], 1)])
    if rows:
        lines += _table(["Decisions", "Horizon", "Count", "Mean excess", "Median", "Hit rate", "t", "t (iid)"], rows,
                        "---|---|---:|---:|---:|---:|---:|---:|")
    else:
        lines += ["No decisions with enough prices after them.", ""]
    trig = d["by_trigger"]
    if len(trig) > 1 or "news" in trig:
        trows = []
        for g in ("news", "other", "no reason"):
            if g in trig:
                b, s = trig[g]["buys"], trig[g]["sells"]
                trows.append([g, str(b["n"]), pct(b["mean"], 2), num(b["t"], 1), str(s["n"]), pct(s["mean"], 2),
                              num(s["t"], 1)])
        lines += ["By the reason the decision gave, five days:", ""]
        lines += _table(["Trigger", "Buys", "Mean excess", "t", "Sells", "Mean avoided", "t"], trows)
    lines += ["### Rank IC", "",
              f"Across the {ic.get('universe', 0)} stocks of the universe, the rank correlation between what the agent "
              f"did and the next {IC_HORIZON} days' returns from the open of the fill day. Decision days: "
              f"{ic.get('decision_days', 'n/a')}.", ""]
    if "skipped" in ic:
        lines += [f"Skipped: {ic['skipped']}.", ""]
    else:
        irows = []
        for key, label in (("decision", "What it bought (+1) and sold (-1)"), ("holdings", "What it held (weights)")):
            m = ic[key]
            irows.append([label, str(m["days"]), num(m["mean"], 3), num(m["std"], 3), num(m["ir"]), num(m["t"], 1),
                          pct(m["positive_share"], 0, False)])
        lines += _table(["Score", "Days", "Mean IC", "Std", "IC IR", "t", "Days with IC > 0"], irows)

    lines += ["## Factor attribution", ""]
    if "skipped" in f:
        lines += [f"Skipped: {f['skipped']}.", ""]
    else:
        lines += [f"The daily return over T-bills regressed on four factors over {f['days']} days, with Newey-West "
                  f"t-statistics ({f['lags']} lags). A contribution is the exposure times the factor's mean return, "
                  "a year; the contributions and the alpha add up to the mean excess return.", ""]
        frows = [[FACTOR_LABELS[k], num(f["exposures"][k]["beta"]), num(f["exposures"][k]["t"], 1),
                  pct(f["factor_means_annual"][k]), pct(f["contributions_annual"][k])] for k in FACTORS]
        frows.append(["Alpha", "", num(f["alpha_t"], 1), "", pct(f["alpha_annual"])])
        frows.append(["Excess return over T-bills", "", "", "", pct(f["excess_return_annual"])])
        lines += _table(["", "Exposure", "t", "Factor a year", "Contribution a year"], frows)
        cp = f["capm"]
        lines += [f"R² {pct(f['r2'], 0, False)} (adjusted {pct(f['adj_r2'], 0, False)}); residual volatility "
                  f"{pct(f['resid_vol_annual'], 1, False)} a year. Market alone (CAPM): beta {num(cp['beta'])} "
                  f"(t {num(cp['beta_t'], 1)}), alpha {pct(cp['alpha_annual'])} a year (t {num(cp['alpha_t'], 1)}).",
                  ""]

    rc = p["reconciliation"]
    lines += ["## Where the P&L came from", "",
              "Each position runs from its first buy to the sell that closes it; positions still open are marked at "
              "the last close. The parts add up to the run's P&L. *Of return* is the P&L over the starting cash, in "
              "percentage points of the run's return.", ""]
    recon = [["Positions in stocks", money(rc["stocks"])]]
    if abs(rc["tbill_etfs"]) > 0.5:
        recon.append(["T-bill ETFs", money(rc["tbill_etfs"])])
    recon.append(["Interest on idle cash", money(rc["cash_interest"])])
    if abs(rc["other"]) > 1.0:
        recon.append(["Other (options, costs not in trades.csv)", money(rc["other"])])
    recon.append([f"Total ({pct(p['return'])})", money(p["total"])])
    lines += _table(["", "P&L"], recon, "---|---:|")
    gh = ["Group", "Positions", "P&L", "Of return", "Win rate", "Still open"]
    if p["positions"]:
        lines += ["### By holding period", "", "Trading days from the first buy to the sell that closed the position.",
                  ""] + _table(gh, _group_rows(p["by_holding_period"]))
    if p["by_regime"]:
        lines += ["### By market regime", "", "The regime at the previous close, which positions were held under. "
                  "Returns compound the run's and SPY's daily returns over those days; P&L includes interest.", ""]
        lines += _table(["Regime", "Days", "P&L", "Of return", "Run's return", "SPY's return", "Avg exposure"],
                        [[r["label"], str(r["days"]), money(r["pnl"]), pct(r["contribution"]), pct(r["return"]),
                          pct(r["spy_return"]), pct(r["avg_exposure"], 0, False)] for r in p["by_regime"]])
    if p["positions"]:
        lines += ["### By trigger", "", "Whether the reason of the entry (or the exit) names news.", ""]
        lines += _table(["Entry", *gh[1:]], _group_rows(p["by_trigger"]["entry"]))
        lines += _table(["Exit", *gh[1:]], _group_rows(p["by_trigger"]["exit"]))
        lines += ["### By sector", ""] + _table(gh, _group_rows(p["by_sector"]))
        stocks = p["by_stock"]
        shown = stocks if len(stocks) <= 20 else stocks[:10] + stocks[-10:]
        lines += [f"### By stock{'' if len(stocks) <= 20 else f' (best 10 and worst 10 of {len(stocks)})'}", ""]
        lines += _table(gh, _group_rows(shown))
        trows = [[t["symbol"], _day(t["entry"]), _day(t["exit"]) or "open", str(t["holding_days"]), money(t["pnl"]),
                  pct(t["return"]), (t["entry_reason"] or "")[:60].replace("|", "/")]
                 for t in p["best_trades"] + p["worst_trades"]]
        lines += ["### Best and worst positions", ""]
        lines += _table(["Stock", "Bought", "Sold", "Days", "P&L", "Return", "Why it bought"], trows,
                        "---|---|---|---:|---:|---:|---|")
        c = p["concentration"]
        lines += ["### Concentration", ""]
        lines += _table(["", "Best position", "Best two", "Best stock"], [
            ["Share of the positions' net P&L", pct(c.get("best_trade_share"), 0, False),
             pct(c.get("top2_share"), 0, False), f"{c.get('best_stock')} {pct(c.get('best_stock_share'), 0, False)}"],
            ["Share of their gains", pct(c.get("best_trade_share_of_gains"), 0, False),
             pct(c.get("top2_share_of_gains"), 0, False), ""],
            ["Run's return had it made nothing", pct(c.get("return_without_best_trade")),
             pct(c.get("return_without_top2")), ""]])
        lines += [f"Return of the run: {pct(c.get('return'))}. A net share above 100% means the other positions lost "
                  "money together.", ""]
    lines += ["## How it is computed", "",
              "- **Decisions**: every buy and sell of a stock in trades.csv. The window starts at the open of the fill "
              "day and ends at the close of the h-th trading day counted from it (1 day = open to close of the fill "
              "day). Total-return prices of the record's frozen period. A sell's value is SPY's return minus the "
              "stock's.",
              "- ***t***: the standard error of the mean adds the products of the deviations of decisions less than "
              "h trading days apart, weighted 1 - distance / h (a Bartlett kernel): decisions on the same day count as "
              "one cluster and overlapping windows share what they overlap.",
              f"- **Rank IC**: Spearman correlation across the universe on each decision day, against the return from "
              f"the open of the fill day to the close {IC_HORIZON} trading days later. IC IR = mean / standard "
              f"deviation; its *t* uses the same kernel with h = {IC_HORIZON}.",
              "- **Factors**: market = SPY - BIL, size = IWM - SPY, growth = QQQ - SPY, momentum = the top third minus "
              f"the bottom third of the period's bundled stocks by their return from {MOM_LOOKBACK} to {MOM_SKIP} "
              "trading days before each month end, equal weight, held through the next month. Close-to-close "
              "total-return prices. OLS with Newey-West standard errors (Bartlett, floor(4 (n/100)^(2/9)) lags).",
              "- **P&L**: a position's P&L is the cash it returned minus the cash it took (commissions included), "
              "plus its value at the last close if still open. Interest on idle cash is the cash at each close times "
              "BIL's return that day, as the backtest pays it. Triggers read the reason of the decision "
              "(decisions.csv and trades.csv): 'news' when it names news (news, headline, red flag, 8-K, earnings, "
              "analyst, ...).", ""]
    return "\n".join(lines)


# ----------------------------------------------------------------------------------- chart
INK, INK2, GRID, AXIS = "#0b0b0b", "#52514e", "#e1e0d9", "#c3c2b7"
BLUE, RED, ORANGE, GRAY = "#2a78d6", "#e34948", "#eb6834", "#898781"


def _style(ax: Any, value_axis: str = "y") -> None:
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color(AXIS)
    ax.tick_params(colors=INK2, labelsize=8)
    ax.grid(axis=value_axis, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def _empty(ax: Any, title: str, text: str) -> None:
    ax.set_title(title, loc="left", fontsize=10, color=INK)
    ax.text(0.5, 0.5, text, ha="center", va="center", color=INK2, fontsize=9, transform=ax.transAxes)
    ax.set_axis_off()


def _hbars(ax: Any, labels: list[str], values: list[float], fmt: str, title: str) -> None:
    y = np.arange(len(values))[::-1]
    ax.barh(y, values, height=0.66, color=[BLUE if v >= 0 else RED for v in values])
    ax.set_yticks(y, labels)
    ax.axvline(0, color=AXIS, linewidth=0.8)
    span = max([abs(v) for v in values] + [1e-9])
    for yi, v in zip(y, values, strict=True):
        ax.annotate(fmt.format(v), (v, yi), xytext=(3 if v >= 0 else -3, 0), textcoords="offset points",
                    ha="left" if v >= 0 else "right", va="center", fontsize=7.5, color=INK2)
    lo, hi = min(values + [0.0]), max(values + [0.0])
    ax.set_xlim(lo - 0.28 * span if lo < 0 else -0.05 * span, hi + 0.28 * span if hi > 0 else 0.05 * span)
    ax.set_title(title, loc="left", fontsize=10, color=INK)
    _style(ax, "x")


def draw(res: dict[str, Any], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 3, figsize=(15, 8.6))
    d, f, p = res["decisions"], res["factors"], res["pnl"]

    ax = axes[0, 0]
    if d["all"]["1d"]["n"]:
        x = np.arange(len(HORIZONS))
        w = 0.38
        for k, (side, label, color) in enumerate((("buys", "Buys: excess earned", BLUE),
                                                  ("sells", "Sells: excess avoided", ORANGE))):
            m = [_f(d[side][f"{h}d"]["mean"]) for h in HORIZONS]
            se = [_f(d[side][f"{h}d"]["se"]) for h in HORIZONS]
            vals = [v * 100 if v is not None else 0.0 for v in m]
            err = [1.96 * s * 100 if s is not None else 0.0 for s in se]
            n = d[side]["1d"]["n"]
            ax.bar(x + (k - 0.5) * w, vals, w * 0.92, color=color, label=f"{label} ({n})", yerr=err,
                   error_kw={"ecolor": INK2, "elinewidth": 0.9, "capsize": 3})
        ax.axhline(0, color=AXIS, linewidth=0.8)
        ax.set_xticks(x, [f"{h} day{'s' if h > 1 else ''}" for h in HORIZONS])
        ax.set_ylabel("Excess return over SPY (%)", fontsize=8, color=INK2)
        ax.legend(frameon=False, fontsize=8, loc="best")
        ax.set_title("After each decision (mean, 95% interval)", loc="left", fontsize=10, color=INK)
        _style(ax)
    else:
        _empty(axes[0, 0], "After each decision", "no decisions")

    ax = axes[0, 1]
    if "contributions_annual" in f:
        keys = [*FACTORS, "alpha"]
        vals = [f["contributions_annual"][k] * 100 for k in keys]
        _hbars(ax, [k.capitalize() for k in keys], vals, "{:+.1f}%",
               f"Return a year by source (R² {pct(f['r2'], 0, False)})")
        ax.set_xlabel(f"% a year of the {pct(f['excess_return_annual'])} excess over T-bills; alpha t "
                      f"{num(f['alpha_t'], 1)}", fontsize=8, color=INK2)
    else:
        _empty(ax, "Return a year by source", f.get("skipped", "no factor regression"))

    ax = axes[0, 2]
    reg = p["by_regime"]
    if reg:
        x = np.arange(len(reg))
        w = 0.38
        ax.bar(x - w / 2, [r["return"] * 100 for r in reg], w * 0.92, color=BLUE, label=res["strategy"])
        ax.bar(x + w / 2, [(r["spy_return"] or 0.0) * 100 for r in reg], w * 0.92, color=GRAY, label="SPY")
        ax.axhline(0, color=AXIS, linewidth=0.8)
        ax.set_xticks(x, [f"{r['label']}\n{r['days']} days" for r in reg])
        ax.set_ylabel("Return over those days (%)", fontsize=8, color=INK2)
        ax.legend(frameon=False, fontsize=8, loc="best")
        ax.set_title("By market regime (previous close)", loc="left", fontsize=10, color=INK)
        _style(ax)
    else:
        _empty(ax, "By market regime", "no regime column")

    ax = axes[1, 0]
    stocks = p["by_stock"]
    if stocks:
        shown = stocks if len(stocks) <= 12 else stocks[:6] + stocks[-6:]
        title = "P&L by stock ($ thousand)" + ("" if len(stocks) <= 12 else f", best and worst 6 of {len(stocks)}")
        _hbars(ax, [r["group"] for r in shown], [r["pnl"] / 1000 for r in shown], "{:+.1f}", title)
    else:
        _empty(ax, "P&L by stock", "no trades")

    ax = axes[1, 1]
    hp = p["by_holding_period"]
    if hp:
        vals = [r["pnl"] / 1000 for r in hp]
        x = np.arange(len(hp))
        ax.bar(x, vals, 0.62, color=[BLUE if v >= 0 else RED for v in vals])
        ax.axhline(0, color=AXIS, linewidth=0.8)
        ax.set_xticks(x, [f"{r['group'].replace(' days', '')}\n{r['trades']} pos." for r in hp])
        ax.set_xlabel("Holding period (trading days) and positions", fontsize=8, color=INK2)
        ax.set_ylabel("P&L ($ thousand)", fontsize=8, color=INK2)
        ax.set_title("P&L by holding period", loc="left", fontsize=10, color=INK)
        _style(ax)
    else:
        _empty(ax, "P&L by holding period", "no trades")

    ax = axes[1, 2]
    sec = p["by_sector"]
    if sec:
        _hbars(ax, [r["group"] for r in sec], [r["pnl"] / 1000 for r in sec], "{:+.1f}", "P&L by sector ($ thousand)")
    else:
        _empty(ax, "P&L by sector", "no trades")

    fig.suptitle(f"{res['strategy']}, {res['start']} to {res['end']}: return {pct(res['total_return'])}, SPY "
                 f"{pct(res['spy_return'])}", x=0.01, ha="left", fontsize=12, color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(path, dpi=130, facecolor="white")
    plt.close(fig)


# ----------------------------------------------------------------------------------- many runs, JSON, MCP
COMPARE = ["strategy", "news", "total_return", "decisions", "decision_excess_5d", "decision_excess_5d_t",
           "buy_excess_5d", "buy_excess_5d_t", "sell_avoided_5d", "sell_avoided_5d_t", "decision_ic", "decision_ic_t",
           "holdings_ic", "holdings_ic_t", "alpha_annual", "alpha_t", "beta_market", "beta_size", "beta_growth",
           "beta_momentum", "r2", "best_trade", "best_trade_share", "return_without_best_trade", "run_id"]


def records_in(folder: str | Path) -> list[Path]:
    """The records in a folder of runs (``riskaware research baselines`` writes one)."""
    folder = Path(folder)
    return sorted(p for p in folder.iterdir() if p.is_dir() and is_record(p))


def analyze_many(folder: str | Path, chart: bool = True,
                 progress: Any = None) -> tuple[pd.DataFrame, Path]:
    """Analyze every record in a folder of runs and write analysis.csv and analysis.md (one row per run) there."""
    folder = Path(folder)
    records = records_in(folder)
    if not records:
        raise FileNotFoundError(f"{folder} is not an experiment record and holds none")
    rows = []
    for rec in records:
        if progress:
            progress(rec.name)
        res = analyze_run(rec, chart=chart)
        best = res["pnl"]["concentration"].get("best_trade") or {}
        rows.append({**res["summary"], "strategy": res["strategy"], "news": res["news"],
                     "best_trade": f"{best.get('symbol')} {_day(best.get('entry'))}" if best else None,
                     "run_id": res["run_id"], "period": res["period"]})
    table = pd.DataFrame(rows, columns=[*COMPARE, "period"])
    table.to_csv(folder / "analysis.csv", index=False, float_format="%.6f")
    (folder / "analysis.md").write_text(compare_markdown(table), encoding="utf-8")
    return table, folder


def compare_markdown(t: pd.DataFrame) -> str:
    head = ["Strategy", "News", "Return", "Decisions", "Buys, 5 days (t)", "Sells avoided (t)", "Decision IC (t)",
            "Alpha a year (t)", "Market", "Size", "Growth", "Momentum", "R²", "Best position", "Return without it"]
    rows = []
    for _, r in t.iterrows():
        news = {True: "with", False: "without"}.get(r["news"], "")
        rows.append([str(r["strategy"]), news, pct(r["total_return"]), str(int(r["decisions"] or 0)),
                     f"{pct(r['buy_excess_5d'], 2)} ({num(r['buy_excess_5d_t'], 1)})",
                     f"{pct(r['sell_avoided_5d'], 2)} ({num(r['sell_avoided_5d_t'], 1)})",
                     f"{num(r['decision_ic'], 3)} ({num(r['decision_ic_t'], 1)})",
                     f"{pct(r['alpha_annual'])} ({num(r['alpha_t'], 1)})",
                     *(num(r[f"beta_{k}"]) for k in FACTORS), pct(r["r2"], 0, False),
                     str(r["best_trade"] or ""), pct(r["return_without_best_trade"])])
    period = t["period"].iloc[0] if len(t) else ""
    return "\n".join([f"# What drove each run ({period})", "", *_table(head, rows, "---|---|" + "---:|" * 11 + "---|---:|"),
                      "Buys: the excess return over SPY in the five trading days from the fill; sells: the excess "
                      "they avoided. t-statistics allow for decisions on the same day and overlapping windows. "
                      "Decision IC: rank IC of what it bought (+1) and sold (-1) against the next five days. Alpha "
                      "and the exposures to the market, size, growth and momentum factors: OLS with Newey-West t. "
                      "Return without it: the run's return had its best position made nothing. Each run's folder has "
                      "its analysis.md.", ""])


def jsonable(v: Any) -> Any:
    """JSON-friendly: numpy and pandas values, NaN and infinity as null, dates as text."""
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        x = float(v)
        return None if math.isnan(x) or math.isinf(x) else round(x, 6)
    if isinstance(v, pd.Timestamp):
        return str(v.date())
    if v is None or isinstance(v, str):
        return v
    return str(v)


def compact(res: dict[str, Any]) -> dict[str, Any]:
    """A shorter version for MCP: the summary, findings, statistics and the top and bottom of each breakdown."""
    p = res["pnl"]

    def ends(rows: list[dict[str, Any]], k: int = 3) -> list[dict[str, Any]]:
        return rows if len(rows) <= 2 * k else rows[:k] + rows[-k:]

    out = {k: res[k] for k in ("run_id", "folder", "strategy", "kind", "period", "start", "end", "stocks", "news",
                               "total_return", "spy_return", "summary", "findings", "decisions", "ic", "factors")}
    out["pnl"] = {"total": p["total"], "reconciliation": p["reconciliation"], "positions": p["positions"],
                  "by_holding_period": p["by_holding_period"], "by_regime": p["by_regime"],
                  "by_trigger": p["by_trigger"], "by_sector": ends(p["by_sector"]), "by_stock": ends(p["by_stock"]),
                  "best_trades": p["best_trades"][:3], "worst_trades": p["worst_trades"][:3],
                  "concentration": p["concentration"]}
    out["files"] = [str(Path(res["folder"]) / f) for f in res.get("files", [])]
    out["notes"] = res.get("notes", [])
    return jsonable(out)


def print_analysis(res: dict[str, Any], console: Any, L: Any) -> None:
    """The CLI's summary of one analysis (``L(en, zh)`` picks the language)."""
    from rich.markup import escape
    from rich.table import Table

    d, ic, f = res["decisions"], res["ic"], res["factors"]
    console.print(f"[bold]{escape(res['strategy'])}[/]  {res['start']} – {res['end']}  {L('return', '收益')} "
                  f"{pct(res['total_return'])}  SPY {pct(res['spy_return'])}")
    t = Table(title=L("Excess return over SPY after each decision: mean, hit rate, t",
                      "每个决策之后相对 SPY 的超额收益：均值、命中率、t 值"))
    t.add_column("")
    for side, en, zh in (("buys", "Buys", "买入"), ("sells", "Sells, avoided", "卖出（躲开的）"), ("all", "All", "全部")):
        t.add_column(f"{L(en, zh)} ({d[side]['1d']['n']})", justify="right")
    for h in HORIZONS:
        cells = []
        for side in ("buys", "sells", "all"):
            m = d[side][f"{h}d"]
            cells.append(f"{pct(m['mean'], 2)} {pct(m['hit_rate'], 0, False)} t{num(m['t'], 1)}" if m["n"] else "")
        t.add_row(L(f"{h} day{'s' if h > 1 else ''}", f"{h} 日"), *cells)
    console.print(t)
    for key, en, zh in (("decision", "Decision IC", "决策 IC"), ("holdings", "Holdings IC", "持仓 IC")):
        m = ic.get(key) or {}
        if m.get("days"):
            console.print(f"{L(en, zh)}: {num(m['mean'], 3)}  IC IR {num(m['ir'])}  t {num(m['t'], 1)}  "
                          f"({m['days']} {L('days', '天')})")
    if "exposures" in f:
        t = Table(title=L(f"Factor attribution (Newey-West t, {f['days']} days, R² {pct(f['r2'], 0, False)})",
                          f"因子归因（Newey-West t 值，{f['days']} 天，R² {pct(f['r2'], 0, False)}）"))
        for col, zh in (("Factor", "因子"), ("Exposure", "暴露"), ("t", "t"), ("Contribution a year", "年化贡献")):
            t.add_column(L(col, zh), justify="left" if col == "Factor" else "right")
        for k in FACTORS:
            t.add_row(FACTOR_LABELS[k], num(f["exposures"][k]["beta"]), num(f["exposures"][k]["t"], 1),
                      pct(f["contributions_annual"][k]))
        t.add_row("Alpha", "", num(f["alpha_t"], 1), pct(f["alpha_annual"]))
        console.print(t)
    console.print(f"[bold]{L('Findings', '结论')}[/]")
    for x in res["findings"]:
        console.print(f"- {escape(x)}", soft_wrap=True)
    for n in res.get("notes") or []:
        console.print(f"[yellow]{escape(n)}[/]")
    if res.get("files"):
        console.print(f"{L('Written', '已写入')}: {res['folder']}/{{{', '.join(res['files'])}}}", soft_wrap=True)


__all__ = ["analyze_many", "analyze_run", "compact", "factor_returns", "jsonable", "load_record", "mean_test",
           "momentum_factor", "ols_newey_west", "rank_ic", "resolve"]
