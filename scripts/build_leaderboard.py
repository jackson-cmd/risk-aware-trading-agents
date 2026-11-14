"""Build the website's leaderboard tables from ``leaderboard/entries`` and ``leaderboard/anchors.json``.

    python scripts/build_leaderboard.py          # write site/assets/data/leaderboard.json and the tables
    python scripts/build_leaderboard.py --check  # exit 1 if a page or the data file is out of date (tests run this)

The tables go between ``<!-- board:NAME -->`` and ``<!-- /board:NAME -->`` in site/leaderboard.html,
site/zh/leaderboard.html and the two home pages, so the pages stay plain HTML that search engines can read. The
prompt board on the same page is drawn in the browser from site/assets/data/prompts.json. For AI search tools, the
line that starts with ``- Leaderboard models`` in site/llms.txt and site/llms-full.txt lists the models with entries,
and the ``## Current results`` section of site/llms-full.txt gives the results as text.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from riskaware.research import bench  # noqa: E402

DATA = ROOT / "site" / "assets" / "data" / "leaderboard.json"
PAGES = {"en": ROOT / "site" / "leaderboard.html", "zh": ROOT / "site" / "zh" / "leaderboard.html"}
HOMES = {"en": ROOT / "site" / "index.html", "zh": ROOT / "site" / "zh" / "index.html"}
LLMS = ROOT / "site" / "llms.txt"
LLMS_FULL = ROOT / "site" / "llms-full.txt"

SECTORS_ZH = {"Technology": "科技", "Communication Services": "通信服务", "Consumer Discretionary": "可选消费",
              "Consumer Staples": "必需消费", "Health Care": "医疗", "Financials": "金融", "Industrials": "工业",
              "Energy": "能源", "Utilities": "公用事业", "Real Estate": "房地产", "Materials": "原材料"}
RULES_ZH = {"swing_trend": "趋势跟随", "swing_news": "新闻后的惯性", "swing_rsi2": "Connors RSI(2)",
            "swing_pullback": "上升趋势中买回调", "swing_breakout": "20 日突破", "SPY": "SPY 买入持有",
            "Equal weight": "等权持有全部 56 只"}
RULES_EN = {"swing_trend": "Trend following", "swing_news": "Post-news drift", "swing_rsi2": "Connors RSI(2)",
            "swing_pullback": "Pullback in an uptrend", "swing_breakout": "20-day breakout",
            "SPY": "SPY buy and hold", "Equal weight": "Equal weight, all 56"}
L10N = {
    "en": {"model": "Model", "cutoff": "Training cutoff", "from": "Scored from", "ret": "Return",
           "spy_same": "SPY", "ew_same": "Equal weight", "sharpe": "Sharpe",
           "mdd": "Max drawdown", "nb": "Named − blind", "blind_ret": "Blind return", "vs_ew": "vs equal weight",
           "named_ret": "Named return", "whole": "Whole window", "period": "Period", "in_stocks": "In stocks",
           "market": "From being invested", "selection": "From picking stocks", "best": "Best sector",
           "worst": "Worst sector", "hold": "Median hold", "news": "Buys citing news", "rule": "rule",
           "beta": "Market beta", "exposures": "From factor exposures", "alpha": "Alpha", "hold_avg": "Average hold",
           "decisions": "Decisions", "buys5": "Buys vs SPY, next 5 days", "hit": "Hit rate",
           "ic": "Decision IC", "news_share": "Triggered by news", "news_pnl": "P&L of news-triggered buys",
           "money_title": "Where the return came from", "dec_title": "How good the decisions were", "seeds": "Seeds",
           "ran": "run",
           "bh": "buy and hold", "none": "No entries yet.", "open": "open weights", "days": "d",
           "anchors": "Rules and buy-and-hold over the whole window", "memory": "Memory probe",
           "per_year": "/yr"},
    "zh": {"model": "模型", "cutoff": "训练截止", "from": "计分起点", "ret": "收益",
           "spy_same": "SPY", "ew_same": "等权", "sharpe": "Sharpe", "mdd": "最大回撤",
           "nb": "实名 − 盲测", "blind_ret": "盲测收益", "vs_ew": "相对等权", "named_ret": "实名收益",
           "whole": "整个窗口", "period": "时段", "in_stocks": "平均持股", "market": "来自持仓暴露",
           "selection": "来自选股", "best": "赚得最多的行业", "worst": "亏得最多的行业", "hold": "持有中位数",
           "news": "引用新闻的买单", "rule": "规则", "bh": "买入持有", "none": "暂无条目。", "open": "开源权重",
           "beta": "市场 beta", "exposures": "来自因子暴露", "alpha": "Alpha", "hold_avg": "平均持有",
           "decisions": "决策数", "buys5": "买入后 5 日相对 SPY", "hit": "命中率", "ic": "决策 IC",
           "news_share": "新闻触发的比例", "news_pnl": "新闻触发买入的盈亏",
           "money_title": "收益从哪来", "dec_title": "决策好不好", "seeds": "种子数", "ran": "运行于",
           "days": "天", "anchors": "规则策略和买入持有（整个窗口）", "memory": "记忆探针", "per_year": "/年"},
}


def pct(v: Any, sign: bool = True, nd: int = 1) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"{f * 100:+.{nd}f}%" if sign else f"{f * 100:.{nd}f}%"


def ranged(text: str, rng: Any, fmt: Any) -> str:
    """A mean with the range over seeds, when there is more than one seed."""
    if not rng or text == "—":
        return text
    return f'{text} <span class="muted">{fmt(rng[0])} … {fmt(rng[1])}</span>'


def num(v: Any, nd: int = 2) -> str:
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return "—"


def esc(s: Any) -> str:
    return html.escape(str(s), quote=True)


def run_dates(r: dict) -> str:
    dates = sorted({d for mode in ("blind", "named") for d in ((r.get(mode) or {}).get("run_dates") or [])})
    return "" if not dates else dates[0] if len(dates) == 1 else f"{dates[0]} – {dates[-1]}"


def model_cell(r: dict, lang: str) -> str:
    badge = f' <span class="badge">{L10N[lang]["open"]}</span>' if r.get("open_weights") else ""
    prov = f'<span class="muted"> · {esc(r["provider"])}</span>' if r.get("provider") else ""
    when = run_dates(r)
    when = f'<br><span class="muted small">{L10N[lang]["ran"]} {esc(when)}</span>' if when else ""
    return f"<td><b>{esc(r['model'])}</b>{prov}{badge}{when}</td>"


def updated_block(b: dict, lang: str) -> str:
    when = esc(b.get("generated_utc") or "—")
    if lang == "zh":
        return (f'<p class="box"><b>持续更新中。</b>结果跑完一批更新一批；最近一次更新 {when}，'
                f'{b["entries"]} 个条目、{b["models"]} 个模型。每个模型名下是它的运行日期（UTC）。</p>')
    return (f'<p class="box"><b>Updated as runs finish.</b> Last update {when}: {b["entries"]} entries from '
            f'{b["models"]} models. The date under each model is when its runs were made (UTC).</p>')


def table(head: list[str], rows: list[str], cls: str = "board") -> str:
    num_cls = ' class="num"'
    th = "".join(f"<th{num_cls if i else ''}>{h}</th>" for i, h in enumerate(head))
    return (f'<div class="table-wrap"><table class="{cls}"><thead><tr>{th}</tr></thead><tbody>\n'
            + "\n".join(rows) + "\n</tbody></table></div>")


def td(v: str) -> str:
    return f'<td class="num">{v}</td>'


def clean_block(b: dict, lang: str) -> str:
    t, out = L10N[lang], []
    for period, p in b["periods"].items():
        rows = []
        for r in p["clean"]:
            bl, nm = r.get("blind") or {}, r.get("named") or {}
            a = bl.get("after_cutoff") or {}
            an = nm.get("after_cutoff") or {}
            gap = (an.get("total_return") - a.get("total_return")) if an.get("total_return") is not None and \
                a.get("total_return") is not None else None
            rows.append(f"<tr>{model_cell(r, lang)}{td(esc(r.get('cutoff') or '—'))}{td(esc(a.get('from', '—')))}"
                        f"{td(str(bl.get('n') or '—'))}"
                        f"{td(ranged(pct(a.get('total_return')), a.get('total_return_range'), pct))}"
                        f"{td(pct(a.get('spy_total_return')))}{td(pct(a.get('equal_weight_total_return')))}"
                        f"{td(ranged(num(a.get('sharpe')), a.get('sharpe_range'), num))}"
                        f"{td(pct(a.get('max_drawdown')))}{td(pct(bl.get('total_return')))}{td(pct(gap))}</tr>")
        if not rows:
            continue
        head = [t["model"], t["cutoff"], t["from"], t["seeds"], t["ret"], t["spy_same"], t["ew_same"], t["sharpe"],
                t["mdd"], t["whole"], t["nb"]]
        out.append(f'<h3 id="clean-{period}">{period} · {esc(p["window"]["start"])} – {esc(p["window"]["end"])}</h3>')
        out.append(table(head, rows))
        if p["anchors"]:
            out.append(f'<p class="note">{t["anchors"]}</p>' + anchor_table(p["anchors"], lang))
    return "\n".join(out) or f"<p>{t['none']}</p>"


def anchor_name(a: dict, lang: str) -> str:
    names = RULES_ZH if lang == "zh" else RULES_EN
    kind = L10N[lang]["rule"] if a.get("kind") == "rule" else L10N[lang]["bh"]
    return f'<td>{esc(names.get(a["name"], a["name"]))} <span class="muted">· {kind}</span></td>'


def anchor_table(anchors: list[dict], lang: str) -> str:
    t = L10N[lang]
    rows = [f"<tr class=\"anchor\">{anchor_name(a, lang)}{td(pct(a.get('total_return')))}{td(num(a.get('sharpe')))}"
            f"{td(pct(a.get('max_drawdown')))}{td(pct(a.get('excess_vs_equal_weight')))}"
            f"{td(pct(a.get('avg_exposure'), sign=False, nd=0))}</tr>" for a in anchors]
    return table([t["model"], t["ret"], t["sharpe"], t["mdd"], t["vs_ew"], t["in_stocks"]], rows, "board anchors")


def remember_block(b: dict, lang: str) -> str:
    t, out = L10N[lang], []
    for period, p in b["periods"].items():
        rows = []
        for r in p["may_remember"]:
            bl, nm = r.get("blind") or {}, r.get("named") or {}
            rows.append(f"<tr>{model_cell(r, lang)}{td(esc(r.get('cutoff') or '—'))}{td(str(bl.get('n') or '—'))}"
                        f"{td(ranged(pct(bl.get('total_return')), bl.get('total_return_range'), pct))}"
                        f"{td(ranged(num(bl.get('sharpe')), bl.get('sharpe_range'), num))}{td(pct(bl.get('max_drawdown')))}"
                        f"{td(pct(bl.get('excess_vs_equal_weight')))}{td(pct(nm.get('total_return')))}"
                        f"{td(pct(r.get('named_minus_blind')))}</tr>")
        for a in p["anchors"]:
            rows.append(f"<tr class=\"anchor\">{anchor_name(a, lang)}{td('')}{td('')}{td(pct(a.get('total_return')))}"
                        f"{td(num(a.get('sharpe')))}{td(pct(a.get('max_drawdown')))}"
                        f"{td(pct(a.get('excess_vs_equal_weight')))}{td('')}{td('')}</tr>")
        if not p["may_remember"]:
            continue
        head = [t["model"], t["cutoff"], t["seeds"], t["blind_ret"], t["sharpe"], t["mdd"], t["vs_ew"], t["named_ret"],
                t["nb"]]
        out.append(f'<h3 id="remember-{period}">{period} · {esc(p["window"]["start"])} – {esc(p["window"]["end"])}</h3>')
        out.append(table(head, rows))
    return "\n".join(out) or f"<p>{t['none']}</p>"


def with_t(v: Any, t: Any, fmt: Any = None) -> str:
    text = (fmt or pct)(v)
    return f'{text} <span class="muted">t {num(t, 1)}</span>' if text != "—" and t is not None else text


def _sector(rows: list[dict] | None, lang: str, worst: bool = False) -> str:
    rows = [r for r in rows or [] if r.get("contribution") is not None]
    if not rows:
        return "—"
    r = min(rows, key=lambda x: x["contribution"]) if worst else max(rows, key=lambda x: x["contribution"])
    name = SECTORS_ZH.get(r["group"], r["group"]) if lang == "zh" else r["group"]
    return f"{esc(name)} {pct(r['contribution'])}"


def _blind_rows(b: dict):
    for period, p in b["periods"].items():
        for r in p["clean"] + p["may_remember"]:
            run = r.get("blind") or {}
            a = run.get("analysis") or {}
            if a.get("summary"):
                yield period, r, run, a


def money_block(b: dict, lang: str) -> str:
    t, money, good = L10N[lang], [], []
    for period, r, run, a in _blind_rows(b):
        s = a["summary"]
        money.append(f"<tr>{model_cell(r, lang)}{td(period)}{td(pct(run.get('avg_exposure'), sign=False, nd=0))}"
                     f"{td(num(s.get('beta_market')))}{td(pct(s.get('exposure_return_annual')) + t['per_year'])}"
                     f"{td(with_t(s.get('alpha_annual'), s.get('alpha_t')))}"
                     f"{td(_sector(a.get('by_sector'), lang))}{td(_sector(a.get('by_sector'), lang, worst=True))}"
                     f"{td(num(run.get('avg_holding_days'), 0) + ' ' + t['days'] if run.get('avg_holding_days') is not None else '—')}"
                     "</tr>")
        news = next((x for x in a.get("by_trigger") or [] if x.get("group") == "news"), None)
        good.append(f"<tr>{model_cell(r, lang)}{td(period)}{td(num(s.get('decisions'), 0))}"
                    f"{td(with_t(s.get('buy_excess_5d'), s.get('buy_excess_5d_t'), lambda v: pct(v, nd=2)))}"
                    f"{td(pct(s.get('decision_hit_rate_5d'), sign=False, nd=0))}"
                    f"{td(with_t(s.get('decision_ic'), s.get('decision_ic_t'), lambda v: num(v, 3)))}"
                    f"{td(pct(s.get('news_decision_share'), sign=False, nd=0))}"
                    f"{td(pct(news.get('contribution')) if news else '—')}</tr>")
    if not money:
        return f"<p>{t['none']}</p>"
    return (f"<h3>{t['money_title']}</h3>\n"
            + table([t["model"], t["period"], t["in_stocks"], t["beta"], t["exposures"], t["alpha"], t["best"],
                     t["worst"], t["hold_avg"]], money)
            + f"\n<h3>{t['dec_title']}</h3>\n"
            + table([t["model"], t["period"], t["decisions"], t["buys5"], t["hit"], t["ic"], t["news_share"],
                     t["news_pnl"]], good))


VERDICTS = {"en": {"clean": "no sign", "contaminated": "remembers", "inconclusive": "inconclusive"},
            "zh": {"clean": "没有迹象", "contaminated": "记得", "inconclusive": "无定论"}}
# direct recall alone, where the paired probe was not run (riskaware.research.memory.recall_scores)
RECALL = {"en": {"recalls": "recalls", "no sign": "no sign", "too few": "too few", "_": "recall only"},
          "zh": {"recalls": "记得", "no sign": "没有迹象", "too few": "样本太少", "_": "只有直接回忆"}}


def memory_block(b: dict, lang: str) -> str:
    t = L10N[lang]
    models: dict[str, dict] = {}
    for period, p in b["periods"].items():
        for r in p["clean"] + p["may_remember"]:
            m = models.setdefault(r["slug"], {"row": r, "gaps": {}, "probe": {}})
            m["gaps"][period] = r.get("named_minus_blind")
            if isinstance(r.get("memory"), dict):
                m["probe"][period] = r["memory"]
    periods = list(b["periods"])
    rows = []
    for m in models.values():
        cells = []
        for p in periods:
            cells.append(td(pct(m["gaps"].get(p))))
            pr = m["probe"].get(p)
            if pr and pr.get("score") is not None:
                word = VERDICTS[lang].get(pr.get("verdict"), pr.get("verdict") or "")
                cells.append(td(f'{num(pr["score"])} <span class="muted">{esc(word)}</span>'))
            elif pr and pr.get("recall_score") is not None:
                word = RECALL[lang].get(pr.get("recall_verdict"), pr.get("recall_verdict") or "")
                cells.append(td(f'{num(pr["recall_score"])} <span class="muted">{RECALL[lang]["_"]}: {esc(word)}</span>'))
            else:
                cells.append(td("—"))
        rows.append(f"<tr>{model_cell(m['row'], lang)}" + "".join(cells) + "</tr>")
    if not rows:
        return f"<p>{t['none']}</p>"
    head = [t["model"]]
    for p in periods:
        head += [f"{t['nb']} {p}", f"{t['memory']} {p}"]
    return table(head, rows)


def teaser_block(b: dict, lang: str) -> str:
    t = L10N[lang]
    p = b["periods"].get("2026") or {}
    rows = []
    for r in (p.get("clean") or [])[:6]:
        a = (r.get("blind") or {}).get("after_cutoff") or {}
        rows.append(f"<tr>{model_cell(r, lang)}{td(esc(a.get('from', '—')))}{td(pct(a.get('total_return')))}"
                    f"{td(pct(a.get('spy_total_return')))}{td(pct(a.get('equal_weight_total_return')))}"
                    f"{td(num(a.get('sharpe')))}</tr>")
    if not rows:
        return f"<p>{t['none']}</p>"
    return table([t["model"], t["from"], t["ret"], t["spy_same"], t["ew_same"], t["sharpe"]], rows)


def model_list(b: dict) -> str:
    seen: dict[str, dict] = {}
    for p in b["periods"].values():
        for r in p["clean"] + p["may_remember"]:
            seen.setdefault(r["model"], r)
    rows = sorted(seen.values(), key=lambda r: ((r.get("provider") or "").lower(), r["model"]))
    return ", ".join(f"{r['model']} ({r.get('provider') or 'unknown provider'}, training cutoff "
                     f"{r.get('cutoff') or 'unknown'})" for r in rows)


def llms_models(text: str, b: dict) -> str:
    """The generated line of llms.txt and llms-full.txt: the models that have entries."""
    line = (f"- Leaderboard models so far (last update {b.get('generated_utc') or '—'}, {b['entries']} entries): "
            f"{model_list(b) or 'none yet'}.")
    return re.sub(r"^- Leaderboard models.*$", lambda m: line, text, count=1, flags=re.M)


def _seeds(n: Any) -> str:
    return f"{n} seed" + ("" if n == 1 else "s")


def llms_results(b: dict) -> str:
    """The results as plain text, for llms-full.txt."""
    out = [f"Last update {b.get('generated_utc') or '—'}: {b['entries']} entries from {b['models']} models, blind "
           "runs, the mean over the seeds each model has so far. Clean: from the model's training cutoff to the end "
           "of the period, next to SPY and the equal-weight portfolio over the same days. May remember: the whole "
           "period, before the cutoff. Tables: https://jackson-cmd.github.io/risk-aware-trading-agents/"
           "leaderboard.html; data: https://jackson-cmd.github.io/risk-aware-trading-agents/assets/data/"
           "leaderboard.json", ""]
    for period, p in b["periods"].items():
        out.append(f"{period} ({p['window']['start']} to {p['window']['end']}):")
        for r in p["clean"]:
            bl = r.get("blind") or {}
            a = bl.get("after_cutoff") or {}
            out.append(f"- {r['model']} ({r.get('provider')}, cutoff {r.get('cutoff')}, {_seeds(bl.get('n'))}), "
                       f"clean: {pct(a.get('total_return'))} from {a.get('from', '—')}, Sharpe {num(a.get('sharpe'))}; "
                       f"SPY {pct(a.get('spy_total_return'))}, equal weight "
                       f"{pct(a.get('equal_weight_total_return'))}")
        for r in p["may_remember"]:
            bl = r.get("blind") or {}
            out.append(f"- {r['model']} ({r.get('provider')}, cutoff {r.get('cutoff')}, {_seeds(bl.get('n'))}), "
                       f"may remember: {pct(bl.get('total_return'))}, Sharpe {num(bl.get('sharpe'))}, "
                       f"{pct(bl.get('excess_vs_equal_weight'))} against equal weight")
        held = {a["name"]: a for a in p["anchors"]}
        names = {"SPY": "SPY buy and hold", "Equal weight": "equal weight of the 56 stocks"}
        bh = [f"{names[n]} {pct(held[n].get('total_return'))}" for n in names if n in held]
        if bh:
            out.append(f"- Whole period: {', '.join(bh)}")
        out.append("")
    return "\n".join(out)


def put_section(text: str, heading: str, content: str) -> str:
    """Replace the body of a markdown section, from its heading to the next ``## `` heading."""
    pat = re.compile(rf"(^## {re.escape(heading)}\n\n)(.*?)(?=^## )", re.S | re.M)
    return pat.sub(lambda m: m.group(1) + content.rstrip("\n") + "\n\n", text, count=1)


def build() -> tuple[dict, dict[Path, str]]:
    root = ROOT / "leaderboard"
    entries = bench.load_entries(root / "entries")
    anchors = json.loads((root / "anchors.json").read_text(encoding="utf-8")) if (root / "anchors.json").is_file() else []
    b = bench.board(entries, anchors)
    b["generated_utc"] = max((e.get("submitted") or "" for e in entries), default=None)  # stable between builds
    out: dict[Path, str] = {DATA: json.dumps(b, indent=1, ensure_ascii=False, sort_keys=True) + "\n"}
    for lang, page in PAGES.items():
        if page.is_file():
            text = page.read_text(encoding="utf-8")
            for name, fn in (("clean", clean_block), ("remember", remember_block), ("money", money_block),
                             ("memory", memory_block), ("updated", updated_block)):
                text = put(text, name, fn(b, lang))
            out[page] = text
    for lang, page in HOMES.items():
        if page.is_file() and "<!-- board:teaser -->" in page.read_text(encoding="utf-8"):
            # the English home page carries both languages and shows one (data-lang)
            body = (f'<div lang="en">{teaser_block(b, "en")}</div>\n<div lang="zh">{teaser_block(b, "zh")}</div>'
                    if lang == "en" else teaser_block(b, lang))
            out[page] = put(page.read_text(encoding="utf-8"), "teaser", body)
    if LLMS.is_file():
        out[LLMS] = llms_models(LLMS.read_text(encoding="utf-8"), b)
    if LLMS_FULL.is_file():
        out[LLMS_FULL] = put_section(llms_models(LLMS_FULL.read_text(encoding="utf-8"), b), "Current results",
                                     llms_results(b))
    return b, out


def put(text: str, name: str, content: str) -> str:
    pat = re.compile(rf"(<!-- board:{name} -->)(.*?)(<!-- /board:{name} -->)", re.S)
    if not pat.search(text):
        return text
    return pat.sub(lambda m: m.group(1) + "\n" + content + "\n" + m.group(3), text)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if a file would change")
    a = ap.parse_args()
    _, out = build()
    stale = [p for p, text in out.items() if not p.is_file() or p.read_text(encoding="utf-8") != text]
    if a.check:
        for p in stale:
            print(f"out of date: {p.relative_to(ROOT)} (run python scripts/build_leaderboard.py)")
        return 1 if stale else 0
    for p in stale:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(out[p], encoding="utf-8")
        print(f"wrote {p.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
