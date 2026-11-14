"""Reading a prompt lab's records, comparing the variants with the baseline, and the website's prompt board.

See prompts.py for the variants and how a lab runs; docs/prompts.md for the method.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np

from .periods import get_period
from .prompts import BASELINE, DECISION_METRICS, HIGHER_IS_BETTER, METRICS, PRIMARY, _done, _now


# ----------------------------------------------------------------------------------- reading the records
def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def read_run(folder: str | Path) -> dict[str, Any] | None:
    """One saved run as a flat row: settings, metrics, benchmarks, format and order counts, tokens."""
    folder = Path(folder)
    if not _done(folder):
        return None
    man = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    met = json.loads((folder / "metrics.json").read_text(encoding="utf-8"))
    log = json.loads((folder / "policy_log.json").read_text(encoding="utf-8"))
    calls = [json.loads(x) for x in (folder / "calls.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    st, spy, ew = met.get("strategy", {}), met.get("spy", {}), met.get("equal_weight", {})
    steps = log.get("steps", [])
    n_orders = sum(len(s.get("orders") or []) for s in steps)
    n_bad = sum(len(s.get("problems") or []) for s in steps)
    status = [c.get("status") for c in calls]
    n_transport = status.count("transport")
    n_format = status.count("format")
    asked = max(1, len(calls) - n_transport)
    pr = man.get("prompt", {})
    exp = man.get("experiment", {})
    row = {
        "variant": pr.get("name"), "factor": pr.get("factor"), "period": str(exp.get("period")),
        "seed": int(man.get("llm", {}).get("seed", exp.get("seed", 0))), "folder": str(folder),
        "prompt_sha256": pr.get("prompt_sha256"), "variant_sha256": pr.get("variant_sha256"),
        "model": man.get("llm", {}).get("model"), "start": exp.get("start"), "end": exp.get("end"),
        "leak_check": (man.get("leak_check") or {}).get("verdict"),
        "steps": len(calls), "transport_errors": n_transport, "format_errors": n_format,
        "orders": n_orders, "invalid_orders": n_bad,
        "format_error_rate": n_format / asked,
        "invalid_order_rate": n_bad / (n_orders + n_bad) if (n_orders + n_bad) else 0.0,
        "orders_per_step": n_orders / max(1, len(steps)),
        "prompt_tokens": sum(int(c.get("prompt_tokens") or 0) for c in calls),
        "completion_tokens": sum(int(c.get("completion_tokens") or 0) for c in calls),
        "seconds": sum(float(c.get("seconds") or 0) for c in calls),
    }
    sized = [c for c in calls if c.get("prompt_chars") and c.get("prompt_tokens")]
    ratio = (sum(int(c["prompt_tokens"]) for c in sized) / sum(int(c["prompt_chars"]) for c in sized)) if sized else None
    row["tokens_per_char"] = ratio
    row["possibly_truncated"] = truncated(sized, ratio)
    for k in ("total_return", "sharpe", "max_drawdown", "volatility", "turnover", "avg_exposure", "trades",
              "round_trips", "win_rate"):
        row[k] = _num(st.get(k))
    row["spy_total_return"], row["spy_sharpe"] = _num(spy.get("total_return")), _num(spy.get("sharpe"))
    row["spy_max_drawdown"] = _num(spy.get("max_drawdown"))
    row["ew_total_return"], row["ew_sharpe"] = _num(ew.get("total_return")), _num(ew.get("sharpe"))
    row["ew_max_drawdown"] = _num(ew.get("max_drawdown"))
    tr = row["total_return"]
    row["excess_vs_spy"] = tr - row["spy_total_return"] if None not in (tr, row["spy_total_return"]) else None
    row["excess_vs_equal_weight"] = tr - row["ew_total_return"] if None not in (tr, row["ew_total_return"]) else None
    return row


TRUNCATED_BELOW = 0.15  # tokens read per prompt character: the observations run about 0.2 to 0.4 depending on the
# tokenizer; far below that, the model read less than it was sent (a gateway or a local server cut the prompt)


def truncated(calls: list[dict[str, Any]], ratio: float | None) -> bool:
    """Did the model read less than it was sent? Either far fewer tokens per character than any tokenizer gives, or
    a cap: many calls report exactly the same number of input tokens although their prompts differ in length."""
    if ratio is not None and ratio < TRUNCATED_BELOW:
        return True
    if len(calls) < 5:
        return False
    from collections import Counter

    tokens, n = Counter(int(c["prompt_tokens"]) for c in calls).most_common(1)[0]
    lengths = {int(c["prompt_chars"]) for c in calls if int(c["prompt_tokens"]) == tokens}
    return n >= 0.3 * len(calls) and len(lengths) > 1 and max(lengths) > 1.05 * min(lengths)


def collect(lab: str | Path) -> list[dict[str, Any]]:
    rows = []
    for f in sorted((Path(lab) / "runs").glob("*/*-s*")):
        r = read_run(f)
        if r is not None:
            rows.append(r)
    return rows


# ----------------------------------------------------------------------------------- where a difference comes from
_CASH: dict[str, Any] = {}


def cash_returns(period: str) -> Any:
    """Daily return of the T-bill ETF BIL in a period: what uninvested cash earns in the backtest."""
    import pandas as pd

    name = get_period(period).name
    if name not in _CASH:
        from .data import bundled_market

        bil = bundled_market(name).history(["BIL"]).get("BIL")
        _CASH[name] = bil["close"].pct_change().clip(-0.01, 0.01) if bil is not None else pd.Series(dtype=float)
    return _CASH[name]


def _equity(folder: str | Path) -> Any:
    import pandas as pd

    return pd.read_csv(Path(folder) / "equity.csv", parse_dates=["date"], index_col="date")


def attribution(variant: str | Path, baseline: str | Path, period: str | None = None,
                min_exposure: float = 0.05) -> dict[str, float]:
    """Split the return difference between two runs of the same period into holding more or less stock and holding
    other stocks (a Brinson-style split of daily returns).

    Each day's return is r = e·s + (1 - e)·c: e is the share of equity in stocks at the previous close, s the return
    of the stock part and c what cash earns (BIL). Then r_v - r_b = (e_v - e_b)(s_b - c) + e_v(s_v - s_b): the first
    term is the *exposure* effect (the baseline's stocks, held at the variant's exposure), the second the
    *selection* effect (the variant's stocks against the baseline's). The exposure effect splits further into its
    *level* (the mean exposure difference times the baseline's stock return over cash) and its *timing* (being in or
    out on the right days). Sums of daily returns, so the parts add up to the difference in summed daily returns;
    ``residual`` is what the approximation leaves (trades at the open of a decision day). When a run holds almost no
    stock (exposure below ``min_exposure``) its stock return is not defined: the equal-weight universe stands in for
    the baseline's and the baseline's for the variant's."""
    v, b = _equity(variant), _equity(baseline)
    idx = v.index.intersection(b.index)
    v, b = v.loc[idx], b.loc[idx]
    if period is None:
        man = json.loads((Path(variant) / "manifest.json").read_text(encoding="utf-8"))
        period = str(man.get("experiment", {}).get("period"))
    c = cash_returns(period).reindex(idx).fillna(0.0)
    rv, rb = v["equity"].pct_change().fillna(0.0), b["equity"].pct_change().fillna(0.0)
    ew = v["equal_weight"].pct_change().fillna(0.0)
    ev, eb = v["exposure"].shift(1).fillna(0.0), b["exposure"].shift(1).fillna(0.0)
    sb = ((rb - (1 - eb) * c) / eb.where(eb >= min_exposure)).fillna(ew)
    sv = ((rv - (1 - ev) * c) / ev.where(ev >= min_exposure)).fillna(sb)
    rv, rb, c, ev, eb, sb, sv = (x.iloc[1:] for x in (rv, rb, c, ev, eb, sb, sv))  # the first day has no return
    expo = float(((ev - eb) * (sb - c)).sum())
    level = float((ev - eb).mean() * (sb - c).sum())
    sel = float((ev * (sv - sb)).sum())
    total = float((rv - rb).sum())
    return {"total": total, "exposure": expo, "exposure_level": level, "exposure_timing": expo - level,
            "selection": sel, "residual": total - expo - sel, "exposure_variant": float(ev.mean()),
            "exposure_baseline": float(eb.mean()), "days": int(len(rv))}


def attribute(rows: list[dict[str, Any]], baseline: str = BASELINE) -> dict[str, dict[str, Any]]:
    """``attribution`` for every variant against the baseline (same period and seed), averaged like ``compare``."""
    base = {(r["period"], r["seed"]): r for r in rows if r["variant"] == baseline}
    out: dict[str, dict[str, Any]] = {}
    for name in dict.fromkeys(r["variant"] for r in rows if r["variant"] != baseline):
        parts = []
        for r in rows:
            b = base.get((r["period"], r["seed"]))
            if r["variant"] == name and b is not None:
                parts.append({"period": r["period"], **attribution(r["folder"], b["folder"], r["period"])})
        if not parts:
            continue
        keys = ("total", "exposure", "exposure_level", "exposure_timing", "selection", "residual",
                "exposure_variant", "exposure_baseline")
        by = {p: {k: float(np.mean([x[k] for x in parts if x["period"] == p])) for k in keys}
              for p in sorted({x["period"] for x in parts}, reverse=True)}
        out[name] = {"pairs": len(parts), "by_period": by,
                     **{k: float(np.mean([by[p][k] for p in by])) for k in keys}}
    return out


# ----------------------------------------------------------------------------------- statistics
def sign_flip_p(d: np.ndarray, n_random: int = 20000, seed: int = 0) -> float:
    """Two-sided p-value of a mean difference of zero under random signs: exact up to 16 pairs, sampled beyond."""
    d = np.asarray(d, dtype=float)
    d = d[~np.isnan(d)]
    n = len(d)
    if n == 0:
        return math.nan
    obs = abs(d.mean())
    if n <= 16:
        signs = ((np.arange(2**n)[:, None] >> np.arange(n)) & 1) * 2 - 1
    else:
        signs = np.random.default_rng(seed).choice([-1, 1], size=(n_random, n))
    means = np.abs((signs * d).mean(axis=1))
    return float((means >= obs - 1e-12).mean())


def bootstrap_ci(d: np.ndarray, groups: np.ndarray | None = None, n_boot: int = 10000, seed: int = 0,
                 level: float = 0.95) -> tuple[float, float]:
    """Percentile interval of the mean difference; with ``groups`` (the period of each pair) the pairs are resampled
    within each period and the periods weigh equally (when every period has at least two pairs)."""
    d = np.asarray(d, dtype=float)
    ok = ~np.isnan(d)
    d = d[ok]
    if len(d) < 2:
        return (math.nan, math.nan)
    rng = np.random.default_rng(seed)
    if groups is not None and min(np.unique(np.asarray(groups)[ok], return_counts=True)[1]) < 2:
        groups = None  # a period with a single pair cannot be resampled: resample all pairs together
    if groups is None:
        means = d[rng.integers(0, len(d), size=(n_boot, len(d)))].mean(axis=1)
    else:
        g = np.asarray(groups)[ok]
        parts = []
        for key in sorted(set(g)):
            x = d[g == key]
            parts.append(x[rng.integers(0, len(x), size=(n_boot, len(x)))].mean(axis=1))
        means = np.mean(parts, axis=0)
    a = (1 - level) / 2 * 100
    lo, hi = np.percentile(means, [a, 100 - a])
    return float(lo), float(hi)


def holm(pvals: dict[str, float]) -> dict[str, float]:
    """Holm-Bonferroni adjusted p-values (a family of tests, one per variant)."""
    items = sorted(((p, k) for k, p in pvals.items() if p == p), key=lambda t: t[0])
    m, out, run = len(items), {}, 0.0
    for i, (p, k) in enumerate(items):
        run = max(run, min(1.0, (m - i) * p))
        out[k] = run
    for k in pvals:
        out.setdefault(k, math.nan)
    return out


def bh(pvals: dict[str, float]) -> dict[str, float]:
    """Benjamini-Hochberg q-values (false discovery rate), a less strict companion to Holm."""
    items = sorted(((p, k) for k, p in pvals.items() if p == p), key=lambda t: t[0])
    m, out, run = len(items), {}, 1.0
    for i in range(m - 1, -1, -1):
        p, k = items[i]
        run = min(run, p * m / (i + 1))
        out[k] = run
    for k in pvals:
        out.setdefault(k, math.nan)
    return out


def _mean(xs: Iterable[float | None]) -> float | None:
    v = [x for x in xs if x is not None and x == x]
    return float(np.mean(v)) if v else None


def compare(rows: list[dict[str, Any]], metric: str, baseline: str = BASELINE,
            seed: int = 0) -> dict[str, dict[str, Any]]:
    """Each variant against the baseline on ``metric``, paired by period and seed (see the module docstring)."""
    base = {(r["period"], r["seed"]): r for r in rows if r["variant"] == baseline}
    names = list(dict.fromkeys(r["variant"] for r in rows if r["variant"] != baseline))
    out: dict[str, dict[str, Any]] = {}
    for name in names:
        pairs = [(r["period"], r[metric] - base[(r["period"], r["seed"])][metric])
                 for r in rows if r["variant"] == name and (r["period"], r["seed"]) in base
                 and r.get(metric) is not None and base[(r["period"], r["seed"])].get(metric) is not None]
        if not pairs:
            continue
        g = np.array([p for p, _ in pairs])
        d = np.array([x for _, x in pairs], dtype=float)
        by = {p: float(d[g == p].mean()) for p in sorted(set(g), reverse=True)}
        lo, hi = bootstrap_ci(d, g, seed=seed)
        out[name] = {"diff": float(np.mean(list(by.values()))), "ci95": [lo, hi], "pairs": int(len(d)),
                     "p": sign_flip_p(d, seed=seed), "by_period": by,
                     "same_sign_periods": int(sum(1 for v in by.values() if np.sign(v) == np.sign(np.mean(d)))),
                     "wins": int((d > 0).sum()), "losses": int((d < 0).sum())}
    adj, q = holm({k: v["p"] for k, v in out.items()}), bh({k: v["p"] for k, v in out.items()})
    for k, v in out.items():
        v["p_holm"], v["q_bh"] = adj[k], q[k]
        better = HIGHER_IS_BETTER.get(metric)
        if better is None or not (v["p_holm"] < 0.05):
            v["verdict"] = "noise" if better is not None else "differs" if v["p_holm"] < 0.05 else "same"
        else:
            v["verdict"] = "better" if (v["diff"] > 0) == better else "worse"
    return out


def reachable(pairs: int, variants: int, alpha: float = 0.05) -> dict[str, Any]:
    """Can the design show anything? The smallest exact sign-flip p-value of ``pairs`` pairs is 2 / 2**pairs, and
    Holm multiplies the smallest p-value by the number of variants. Thirteen variants need at least ten pairs."""
    best = min(1.0, variants * 2.0 / 2**pairs) if pairs else 1.0
    need = next((n for n in range(1, 64) if variants * 2.0 / 2**n < alpha), None)
    return {"pairs": pairs, "variants": variants, "smallest_p_holm": best, "can_pass": best < alpha,
            "pairs_needed": need}


def seed_spread(rows: list[dict[str, Any]], metric: str, variant: str = BASELINE) -> dict[str, Any]:
    """How much two seeds of the same prompt differ: the noise floor any variant has to beat."""
    by: dict[str, list[float]] = {}
    for r in rows:
        if r["variant"] == variant and r.get(metric) is not None:
            by.setdefault(r["period"], []).append(r[metric])
    gaps = [abs(a - b) for xs in by.values() for i, a in enumerate(xs) for b in xs[i + 1:]]
    sds = {p: float(np.std(xs, ddof=1)) for p, xs in by.items() if len(xs) > 1}
    return {"sd_by_period": sds, "mean_abs_gap": float(np.mean(gaps)) if gaps else None, "pairs": len(gaps)}


def summarize(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per variant: the mean of each metric over all runs and by period, the runs, tokens and seconds."""
    out: dict[str, dict[str, Any]] = {}
    for name in dict.fromkeys(r["variant"] for r in rows):
        rs = [r for r in rows if r["variant"] == name]
        periods = sorted({r["period"] for r in rs}, reverse=True)
        out[name] = {"runs": len(rs), "factor": rs[0]["factor"], "prompt_sha256": rs[0]["prompt_sha256"],
                     "variant_sha256": rs[0]["variant_sha256"],
                     "metrics": {m: {"mean": _mean(r.get(m) for r in rs),
                                     "by_period": {p: _mean(r.get(m) for r in rs if r["period"] == p)
                                                   for p in periods}} for m in (*METRICS, *DECISION_METRICS)},
                     "steps": sum(r["steps"] for r in rs), "transport_errors": sum(r["transport_errors"] for r in rs),
                     "prompt_tokens": sum(r["prompt_tokens"] for r in rs),
                     "completion_tokens": sum(r["completion_tokens"] for r in rs),
                     "seconds": sum(r["seconds"] for r in rs),
                     "leak_checks": {v: sum(1 for r in rs if r["leak_check"] == v) for v in {r["leak_check"] for r in rs}}}
    return out


def benchmarks(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        out.setdefault(r["period"], {"start": r["start"], "end": r["end"],
                                     "spy": {"total_return": r["spy_total_return"], "sharpe": r["spy_sharpe"],
                                             "max_drawdown": r["spy_max_drawdown"]},
                                     "equal_weight": {"total_return": r["ew_total_return"], "sharpe": r["ew_sharpe"],
                                                      "max_drawdown": r["ew_max_drawdown"]}})
    return dict(sorted(out.items(), reverse=True))


# ----------------------------------------------------------------------------------- the report
def report(lab: str | Path, site: str | Path | None = None, analysis: bool = True) -> dict[str, Any]:
    """Compare the variants of a lab (see the module docstring) and write prompts.json, prompts.md, prompts.png;
    with ``site``, also the website's data file."""
    lab = Path(lab).expanduser()
    meta = json.loads((lab / "lab.json").read_text(encoding="utf-8"))
    rows = collect(lab)
    if not rows:
        raise ValueError(f"no finished runs in {lab}")
    decisions = add_decision_metrics(rows) if analysis else False
    settings = meta["settings"]
    known = {v["name"]: v for v in meta["variants"]}
    order = [n for n in known if any(r["variant"] == n for r in rows)]
    summary = summarize(rows)
    comparisons = {m: compare(rows, m) for m in ("sharpe", "total_return", "max_drawdown", "excess_vs_spy",
                                                  "volatility", "turnover", "avg_exposure", "orders_per_step",
                                                  "format_error_rate", "invalid_order_rate",
                                                  *(("hit_rate_5d", "decision_excess_5d", "decision_ic",
                                                     "holdings_ic") if decisions else ()))}
    tokens_in = sum(r["prompt_tokens"] for r in rows)
    tokens_out = sum(r["completion_tokens"] for r in rows)
    pi, po = settings.get("price_in"), settings.get("price_out")
    cost = {"calls": sum(r["steps"] for r in rows), "prompt_tokens": tokens_in, "completion_tokens": tokens_out,
            "list_price_usd": round(tokens_in * pi / 1e6 + tokens_out * po / 1e6, 2) if None not in (pi, po) else None,
            "price_per_million": {"input": pi, "output": po}, "note": settings.get("note", "")}
    out = {"generated_utc": _now(), "lab": str(lab), "set_version": meta.get("set_version"),
           "settings": settings, "baseline": BASELINE, "primary_metric": PRIMARY,
           "variants": [{**{k: known[n][k] for k in ("name", "factor", "about", "about_zh", "max_news", "decide_every",
                                                      "reasoning", "response_format", "prompt_sha256",
                                                      "variant_sha256")},
                         **summary[n],
                         "vs_baseline": {m: c[n] for m, c in comparisons.items() if n in c}} for n in order],
           "noise": {m: seed_spread(rows, m) for m in ("sharpe", "total_return", "max_drawdown")},
           "power": reachable(min((c["pairs"] for c in comparisons["sharpe"].values()), default=0),
                              len(comparisons["sharpe"])),
           "benchmarks": benchmarks(rows), "cost": cost, "runs": rows}
    try:
        out["attribution"] = attribute(rows)
    except Exception as exc:  # noqa: BLE001 - an old record without the columns it needs: report without it
        out["attribution"] = {"error": f"{type(exc).__name__}: {exc}"}
    out["decision_metrics"] = decisions
    (lab / "prompts.json").write_text(json.dumps(out, indent=1, default=_json) + "\n", encoding="utf-8")
    (lab / "prompts.md").write_text(render(out), encoding="utf-8")
    try:
        draw(out, lab / "prompts.png")
    except Exception:  # noqa: BLE001 - the chart is a nicety; the numbers are saved
        pass
    if site:
        write_site(out, site)
    return out


def _analysis() -> tuple[Any, str] | None:
    """research/analysis.py's ``analyze_run`` and the SHA-256 of its source, or None before that module exists."""
    try:
        from . import analysis  # type: ignore[attr-defined]
    except ImportError:
        return None
    fn = getattr(analysis, "analyze_run", None)
    if fn is None:
        return None
    import hashlib

    src = Path(analysis.__file__).read_bytes() if getattr(analysis, "__file__", None) else b""
    return fn, hashlib.sha256(src).hexdigest()


def add_decision_metrics(rows: list[dict[str, Any]]) -> bool:
    """Decision-level metrics for every run (``analysis.analyze_run``: the 5-day hit rate and excess of the decisions,
    the decision and holdings IC), cached in ``decision_metrics.json`` next to each record and recomputed when the
    analysis code changes. Returns False when the analysis module is not installed."""
    found = _analysis()
    if found is None:
        return False
    fn, sha = found
    for r in rows:
        cache = Path(r["folder"]) / "decision_metrics.json"
        got = None
        if cache.is_file():
            old = json.loads(cache.read_text(encoding="utf-8"))
            got = old.get("summary") if old.get("analysis_sha256") == sha else None
        if got is None:
            try:
                res = fn(r["folder"], save=False, chart=False)
                summ = res.get("summary", {}) if isinstance(res, dict) else {}
                got = {k: _num(summ.get(v)) for k, v in DECISION_METRICS.items()}
                got.update({f"{k}_t": _num(summ.get(f"{v}_t")) for k, v in DECISION_METRICS.items()
                            if f"{v}_t" in summ})
            except Exception as exc:  # noqa: BLE001 - one record it cannot read must not stop the report
                got = {"error": f"{type(exc).__name__}: {exc}"[:200]}
            cache.write_text(json.dumps({"analysis_sha256": sha, "summary": got}, indent=1) + "\n", encoding="utf-8")
        for k in DECISION_METRICS:
            r[k] = got.get(k)
        r["decision_t"] = {k: got.get(f"{k}_t") for k in DECISION_METRICS if got.get(f"{k}_t") is not None}
    return True


def _json(v: Any) -> Any:
    if isinstance(v, (np.floating, np.integer)):
        return v.item()
    return str(v)


def _pct(v: Any, sign: bool = True) -> str:
    f = _num(v)
    if f is None:
        return "n/a"
    return f"{f * 100:+.1f}%" if sign else f"{f * 100:.1f}%"


def _f2(v: Any, sign: bool = False) -> str:
    f = _num(v)
    return "n/a" if f is None else (f"{f:+.2f}" if sign else f"{f:.2f}")


def _f0(v: Any) -> str:
    f = _num(v)
    return "n/a" if f is None else f"{f:.0f}"


def _pv(v: Any) -> str:
    f = _num(v)
    return "n/a" if f is None else ("<0.001" if f < 0.001 else f"{f:.3f}")


def render(r: dict[str, Any]) -> str:
    s = r["settings"]
    per = ", ".join(r["benchmarks"])
    lines = [f"# Prompt test: {s['llm']}", "",
             f"{len(r['variants'])} prompt variants, each on {per}, seeds {', '.join(map(str, s['seeds']))}; "
             f"universe {s['universe'] or 'custom'} ({'blind' if s['blind'] else 'named'}), at most "
             f"{s['max_positions']} positions, temperature {s['temperature']}. Every variant changes one factor from "
             "the baseline. Differences are paired (same period, same seed): mean over periods, 95% bootstrap "
             "interval, exact sign-flip p-value, Holm-corrected across the variants.", ""]
    lines += ["## Sharpe and return by variant", "",
              "| Variant | Factor | Sharpe | Δ Sharpe vs baseline (95% CI) | p (Holm) | Return | Δ return | Max DD | "
              "Format errors | Invalid orders | Verdict |", "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for v in r["variants"]:
        m = v["metrics"]
        c = v["vs_baseline"].get("sharpe")
        ct = v["vs_baseline"].get("total_return")
        if c:
            dsh = f"{_f2(c['diff'], True)} ({_f2(c['ci95'][0], True)} to {_f2(c['ci95'][1], True)})"
            p, verdict = _pv(c["p_holm"]), c["verdict"]
        else:
            dsh, p, verdict = "—", "—", "baseline" if v["name"] == r["baseline"] else "—"
        lines.append(f"| `{v['name']}` | {v['factor']} | {_f2(m['sharpe']['mean'])} | {dsh} | {p} | "
                     f"{_pct(m['total_return']['mean'])} | {_pct(ct['diff']) if ct else '—'} | "
                     f"{_pct(m['max_drawdown']['mean'])} | {_pct(m['format_error_rate']['mean'], False)} | "
                     f"{_pct(m['invalid_order_rate']['mean'], False)} | {verdict} |")
    lines.append("")
    lines += ["## By period (mean Sharpe; Δ vs baseline)", "", "| Variant | " + " | ".join(r["benchmarks"]) + " |",
              "|---|" + "---:|" * len(r["benchmarks"])]
    for v in r["variants"]:
        c = v["vs_baseline"].get("sharpe")
        cells = []
        for p in r["benchmarks"]:
            sh = v["metrics"]["sharpe"]["by_period"].get(p)
            cells.append(_f2(sh) + (f" ({_f2(c['by_period'].get(p), True)})" if c and p in c["by_period"] else ""))
        lines.append(f"| `{v['name']}` | " + " | ".join(cells) + " |")
    lines.append("")
    if r.get("decision_metrics"):
        lines += ["## Decisions", "",
                  "From research/analysis.py: each buy scores the stock's return over SPY from the next open to five "
                  "trading days later, each sell the return it avoided; the IC is the rank correlation of the "
                  "decisions (or the holdings) with those returns across the stocks.", "",
                  "| Variant | Decisions | Hit rate (5d) | Excess (5d) | Δ excess vs baseline | Decision IC | "
                  "Holdings IC | Δ holdings IC |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for v in r["variants"]:
            m, c = v["metrics"], v["vs_baseline"]
            de, hi = c.get("decision_excess_5d"), c.get("holdings_ic")
            lines.append(f"| `{v['name']}` | {_f0(m['decisions']['mean'])} | {_pct(m['hit_rate_5d']['mean'], False)} | "
                         f"{_pct(m['decision_excess_5d']['mean'])} | "
                         + (f"{_pct(de['diff'])} (p {_pv(de['p_holm'])})" if de else "—")
                         + f" | {_f2(m['decision_ic']['mean'], True)} | {_f2(m['holdings_ic']['mean'], True)} | "
                         + (f"{_f2(hi['diff'], True)} (p {_pv(hi['p_holm'])})" if hi else "—") + " |")
        lines.append("")
    at = r.get("attribution") or {}
    if at and "error" not in at:
        lines += ["## Where the return difference comes from", "",
                  "Sums of daily returns against the baseline on the same period and seed, split into holding more or "
                  "less stock (level: on average; timing: on which days) and holding other stocks (selection).", "",
                  "| Variant | Difference | Exposure level | Exposure timing | Selection | Other | Mean exposure "
                  "(baseline) |", "|---|---:|---:|---:|---:|---:|---:|"]
        for name, a in at.items():
            lines.append(f"| `{name}` | {_pct(a['total'])} | {_pct(a['exposure_level'])} | "
                         f"{_pct(a['exposure_timing'])} | {_pct(a['selection'])} | {_pct(a['residual'])} | "
                         f"{_pct(a['exposure_variant'], False)} ({_pct(a['exposure_baseline'], False)}) |")
        lines.append("")
    lines += ["## Benchmarks", "", "| Period | Days | SPY return | SPY Sharpe | Equal-weight return | Equal-weight Sharpe |",
              "|---|---|---:|---:|---:|---:|"]
    for p, b in r["benchmarks"].items():
        lines.append(f"| {p} | {b['start']} to {b['end']} | {_pct(b['spy']['total_return'])} | "
                     f"{_f2(b['spy']['sharpe'])} | {_pct(b['equal_weight']['total_return'])} | "
                     f"{_f2(b['equal_weight']['sharpe'])} |")
    lines.append("")
    cut = [x for x in r.get("runs", []) if x.get("possibly_truncated")]
    if cut:
        lines += [f"**Possibly cut prompts:** in {len(cut)} run{'s' if len(cut) != 1 else ''} the model read only "
                  f"{min(x['tokens_per_char'] for x in cut):.2f} to {max(x['tokens_per_char'] for x in cut):.2f} tokens "
                  "per prompt character (about 0.2 to 0.4 is normal), or many calls read exactly the same number of "
                  "tokens: a gateway or server may have cut the prompts, so the model may not have seen the whole "
                  "observation. Runs: "
                  + ", ".join(f"`{x['variant']}` {x['period']} s{x['seed']}" for x in cut[:8])
                  + (" ..." if len(cut) > 8 else "") + ".", ""]
    pw = r.get("power") or {}
    if pw and not pw.get("can_pass"):
        lines += [f"**Too few pairs to show anything:** with {pw['pairs']} pairs per variant and {pw['variants']} "
                  f"variants the smallest possible Holm-corrected p-value is {_pv(pw['smallest_p_holm'])}, so every "
                  f"verdict is `noise` by construction. {pw['pairs_needed']} pairs (periods x seeds) are needed.", ""]
    nz = r["noise"]["sharpe"]
    if nz["pairs"]:
        lines += ["## Noise floor", "",
                  f"Two seeds of the baseline prompt differ in Sharpe by {_f2(nz['mean_abs_gap'])} on average "
                  f"({nz['pairs']} pair{'s' if nz['pairs'] != 1 else ''} of runs on the same period); standard "
                  "deviation across seeds by period: "
                  + ", ".join(f"{p} {_f2(x)}" for p, x in nz["sd_by_period"].items()) + ".", ""]
    else:
        lines += ["## Noise floor", "", "Not measured: the baseline has one seed per period, so nothing shows how much "
                  "the same prompt differs from run to run.", ""]
    c = r["cost"]
    lines += ["## Cost", "",
              f"{c['calls']:,} model calls, {c['prompt_tokens']:,} input and {c['completion_tokens']:,} output tokens"
              + (f", ${c['list_price_usd']:.2f} at list price" if c.get("list_price_usd") is not None else "")
              + "." + (f" Note: {c['note']}." if c.get("note") else ""), ""]
    lines += ["## Variants", ""] + [f"- `{v['name']}` ({v['factor']}): {v['about']} Prompt SHA-256 "
                                    f"`{v['prompt_sha256'][:16]}...`" for v in r["variants"]] + [""]
    return "\n".join(lines)


def draw(r: dict[str, Any], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    vs = [v for v in r["variants"] if v["vs_baseline"].get("sharpe")]
    if not vs:
        return
    fig, axes = plt.subplots(1, 2, figsize=(10, 0.42 * len(vs) + 1.4), sharey=True)
    colors = {"better": "#1a7f37", "worse": "#cf222e", "noise": "#57606a"}
    marks = dict(zip(r["benchmarks"], ("o", "s", "^", "D", "v", "P"), strict=False))
    for ax, metric, label, scale in ((axes[0], "sharpe", "Δ Sharpe vs baseline", 1.0),
                                     (axes[1], "total_return", "Δ total return vs baseline (pp)", 100.0)):
        for i, v in enumerate(vs):
            c = v["vs_baseline"][metric]
            y = len(vs) - 1 - i
            col = colors.get(c["verdict"], "#57606a")
            ax.plot([c["ci95"][0] * scale, c["ci95"][1] * scale], [y, y], color=col, linewidth=2.2, alpha=0.8)
            ax.plot(c["diff"] * scale, y, "o", color=col, markersize=7)
            for p, x in c["by_period"].items():
                ax.plot(x * scale, y + 0.22, marks.get(p, "x"), color="#8c959f", markersize=4, alpha=0.9)
        ax.axvline(0, color="#8c959f", linewidth=0.8)
        ax.set_xlabel(label)
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="x", color="#d0d7de", linewidth=0.5)
    axes[0].set_yticks(range(len(vs)))
    axes[0].set_yticklabels([v["name"] for v in reversed(vs)])
    handles = [plt.Line2D([], [], marker=m, linestyle="", color="#8c959f", label=p) for p, m in marks.items()]
    axes[1].legend(handles=handles, title="period", frameon=False, loc="lower right", fontsize=8)
    fig.suptitle(f"Prompt variants vs the baseline ({r['settings']['llm']}): mean of paired differences, 95% CI",
                 fontsize=10, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


# ----------------------------------------------------------------------------------- the website's data
SITE_METRICS = ("sharpe", "total_return", "max_drawdown", "excess_vs_spy", "avg_exposure", "turnover",
                "format_error_rate", "invalid_order_rate", "hit_rate_5d", "decision_excess_5d", "decision_ic",
                "holdings_ic")
SITE_COMPARE = ("sharpe", "total_return", "max_drawdown", "avg_exposure", "turnover", "format_error_rate",
                "invalid_order_rate", "hit_rate_5d", "decision_excess_5d", "decision_ic", "holdings_ic")
METHOD = {
    "en": "Each variant changes one factor of the baseline prompt. Paired differences (same model, period and seed), "
          "mean over periods, 95% bootstrap interval, exact sign-flip p-value, Holm correction across the variants; "
          "'noise' means the difference cannot be told apart from running the same prompt again. The attribution "
          "splits a return difference into holding more or less stock (level, timing) and holding other stocks "
          "(selection), in sums of daily returns.",
    "zh": "每个变体只改基线 prompt 的一个因素。配对差（同一模型、同一时段、同一种子），按时段平均，95% bootstrap 区间，"
          "精确符号翻转检验，按变体个数做 Holm 校正；「noise」表示这个差别和把同一个 prompt 再跑一遍分不开。归因把收益差拆成"
          "持股多少（水平、择时）和持哪些股票（选股），按每日收益相加计算。"}


def _cmp(c: dict[str, Any]) -> dict[str, Any]:
    return {"diff": _round(c["diff"]), "ci95": [_round(c["ci95"][0]), _round(c["ci95"][1])], "p": _round(c["p"]),
            "p_holm": _round(c["p_holm"]), "q_bh": _round(c.get("q_bh")), "pairs": c["pairs"], "wins": c.get("wins"),
            "losses": c.get("losses"), "by_period": {p: _round(x) for p, x in c["by_period"].items()},
            "verdict": c["verdict"]}


def _attr(a: dict[str, Any] | None) -> dict[str, Any] | None:
    if not a or "total" not in a:
        return None
    keys = ("total", "exposure_level", "exposure_timing", "selection", "residual", "exposure_variant",
            "exposure_baseline")
    return {**{k: _round(a[k]) for k in keys},
            "by_period": {p: {k: _round(v[k]) for k in keys} for p, v in a.get("by_period", {}).items()}}


def board_entry(r: dict[str, Any]) -> dict[str, Any]:
    """One model's prompt board (one lab's report) in the website's format."""
    s = r["settings"]
    at = r.get("attribution") or {}
    rows = []
    for v in r["variants"]:
        m = v["metrics"]
        rows.append({
            "name": v["name"], "factor": v["factor"], "about": {"en": v["about"], "zh": v["about_zh"]},
            "prompt_sha256": v["prompt_sha256"], "variant_sha256": v["variant_sha256"], "runs": v["runs"],
            "settings": {k: v[k] for k in ("max_news", "decide_every", "reasoning", "response_format")},
            "metrics": {k: {"mean": _round(m[k]["mean"]), "by_period": {p: _round(x) for p, x in m[k]["by_period"].items()}}
                        for k in SITE_METRICS},
            "tokens_per_step": _round((v["prompt_tokens"] + v["completion_tokens"]) / max(1, v["steps"]), 0),
            "vs_baseline": {k: _cmp(c) for k, c in v["vs_baseline"].items() if k in SITE_COMPARE} or None,
            "attribution": _attr(at.get(v["name"]) if isinstance(at, dict) else None)})
    noise = r["noise"]["sharpe"]
    return {"model": s["llm"], "lab_note": s.get("note", ""),
            "settings": {k: s.get(k) for k in ("universe", "symbols", "max_positions", "temperature", "blind", "news",
                                               "periods", "seeds", "api_seed")},
            "benchmarks": {p: {"start": b["start"], "end": b["end"],
                               "spy": {k: _round(x) for k, x in b["spy"].items()},
                               "equal_weight": {k: _round(x) for k, x in b["equal_weight"].items()}}
                           for p, b in r["benchmarks"].items()},
            "noise": {"sharpe_mean_abs_gap_between_seeds": _round(noise["mean_abs_gap"]),
                      "sharpe_sd_by_period": {p: _round(x) for p, x in noise["sd_by_period"].items()}},
            "power": r.get("power"), "variants": rows,
            "cost": {k: r["cost"][k] for k in ("calls", "prompt_tokens", "completion_tokens", "list_price_usd")}}


def across(reports: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The variants that several labs (one model each) share, compared with the baseline over every model, period
    and seed at once: does a prompt's effect hold across models?"""
    if len(reports) < 2:
        return None
    rows = [{**x, "seed": i * 1000 + int(x["seed"])} for i, r in enumerate(reports) for x in r["runs"]]
    names = [v["name"] for v in reports[0]["variants"]
             if all(any(w["name"] == v["name"] for w in r["variants"]) for r in reports)]
    rows = [x for x in rows if x["variant"] in names]
    if BASELINE not in names or len(names) < 2:
        return None
    comps = {m: compare(rows, m) for m in SITE_COMPARE}
    try:
        at = attribute(rows)
    except Exception:  # noqa: BLE001 - records without exposure columns: no attribution
        at = {}
    first = {v["name"]: v for v in reports[0]["variants"]}
    return {"models": [r["settings"]["llm"] for r in reports], "pairs_by": "model x period x seed",
            "variants": [{"name": n, "factor": first[n]["factor"],
                          "about": {"en": first[n]["about"], "zh": first[n]["about_zh"]},
                          "vs_baseline": {m: _cmp(c[n]) for m, c in comps.items() if n in c} or None,
                          "attribution": _attr(at.get(n))} for n in names]}


def site_payload(reports: dict[str, Any] | list[dict[str, Any]], notes: dict[str, str] | None = None) -> dict[str, Any]:
    """The prompt board for the website (site/assets/data/prompts.json): one board per lab (one model each) and,
    with several labs, the comparison across models; ``notes`` ({"en", "zh"}) says what a reader should know (a model
    left out, say). The format is described in docs/prompts.md."""
    reports = [reports] if isinstance(reports, dict) else list(reports)
    return {"format": "rta-prompt-board-1", "generated_utc": _now(),
            "set_version": reports[0]["set_version"], "baseline": BASELINE, "primary_metric": PRIMARY,
            "boards": [board_entry(r) for r in reports], "across_models": across(reports), "method": METHOD,
            "notes": notes}


def board(labs: Iterable[str | Path], site: str | Path | None = None,
          notes: dict[str, str] | None = None) -> dict[str, Any]:
    """Report every lab (one model each) and combine them into the website's prompt board."""
    reports = [report(lab) for lab in labs]
    out = site_payload(reports, notes)
    if site:
        p = Path(site)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def _round(v: Any, nd: int = 4) -> Any:
    f = _num(v)
    if f is None:
        return None
    return int(round(f)) if nd == 0 else round(f, nd)


def write_site(r: dict[str, Any] | list[dict[str, Any]], path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(site_payload(r), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return p
