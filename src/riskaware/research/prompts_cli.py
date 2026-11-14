"""``riskaware research prompts``: which system prompt makes an LLM trading agent trade better (see prompts.py)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.markup import escape
from rich.table import Table

app = typer.Typer(no_args_is_help=True, help=(
    "Prompt tests: versioned system-prompt variants, each changing one factor, traded on the frozen periods and "
    "compared in pairs with the baseline. / prompt 测试：每个变体只改一个因素，在冻结时段上交易，和基线配对比较"))


def _console():
    from .cli import console

    return console


def _L(en: str, zh: str) -> str:
    from .cli import L

    return L(en, zh)


@app.command("list")
def list_cmd(
    show: Optional[str] = typer.Option(None, help="Print the full text of one variant"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """The prompt variants, what each changes, and their SHA-256. / 所有 prompt 变体、各改了什么、哈希"""
    from . import prompts as P

    con = _console()
    if show:
        try:
            v = P.get_variant(show)
        except ValueError as exc:
            con.print(f"[red]{escape(str(exc))}[/]")
            raise typer.Exit(2) from exc
        if as_json:
            con.print_json(json.dumps(v.describe(text=True)))
            return
        con.print(f"[bold]{v.name}[/] ({v.factor}): {escape(_L(v.about, v.about_zh))}")
        con.print(json.dumps(v.settings()))
        con.print(escape(v.text), soft_wrap=True)
        return
    rows = P.variants_table()
    if as_json:
        con.print_json(json.dumps(rows))
        return
    t = Table(title=_L(f"Prompt variants (set {P.SET_VERSION})", f"prompt 变体（第 {P.SET_VERSION} 版）"))
    for col, zh in (("Variant", "变体"), ("Factor", "因素"), ("What changes", "改了什么"), ("News", "新闻"),
                    ("Every", "间隔"), ("Format", "格式"), ("SHA-256", "SHA-256")):
        t.add_column(_L(col, zh), no_wrap=col != "What changes", ratio=1 if col == "What changes" else None)
    for r in rows:
        t.add_row(r["name"], r["factor"], _L(r["about"], r["about_zh"]), str(r["max_news"]), str(r["decide_every"]),
                  r["response_format"] + (" +reasoning" if r["reasoning"] else ""), r["variant_sha256"][:12])
    con.print(t)


@app.command("run")
def run_cmd(
    llm: str = typer.Option(..., help="provider:model, e.g. openai:gpt-5.6-luna, ollama:qwen2.5:3b, claude-cli"),
    variants: str = typer.Option("all", help="Comma-separated variant names, or all"),
    prompt_file: Optional[Path] = typer.Option(None, help="Your own system prompt (a text file), run as one more variant"),
    name: Optional[str] = typer.Option(None, help="Name of the --prompt-file variant (default: the file name)"),
    periods: str = typer.Option("2026,2022,2020", help="Comma-separated bundled periods"),
    seeds: int = typer.Option(4, help="Seeds 0..n-1 (each fixes the blind aliases and the sampling seed); with 13 "
                                      "variants, 3 periods x 4 seeds is the smallest grid that can pass the Holm "
                                      "correction"),
    seed_list: Optional[str] = typer.Option(None, help="Exact seeds instead, e.g. 0,1,2"),
    universe: str = typer.Option("core"),
    symbols: Optional[str] = typer.Option(None, help="Comma-separated symbols instead of a universe"),
    max_positions: int = typer.Option(5),
    temperature: Optional[float] = typer.Option(0.2, help="Sampling temperature (OpenAI-compatible and Ollama)"),
    max_tokens: Optional[int] = typer.Option(None, help="Cap on each reply (default: the package's 1200)"),
    named: bool = typer.Option(False, "--named", help="Show real tickers and dates (default: blind)"),
    base_url: Optional[str] = typer.Option(None, help="For compat:model or a remote Ollama, the server's URL"),
    workers: int = typer.Option(4, help="Runs at the same time"),
    out: Optional[Path] = typer.Option(None, help="Lab folder (default ~/.riskaware/research/prompts/<time>); "
                                                  "an existing lab is resumed"),
    start: Optional[str] = typer.Option(None, help="Shorter window inside one period (pilots)"),
    end: Optional[str] = typer.Option(None),
    price_in: Optional[float] = typer.Option(None, help="USD per million input tokens, for the cost estimate"),
    price_out: Optional[float] = typer.Option(None, help="USD per million output tokens"),
    pilot: Optional[Path] = typer.Option(None, help="A finished pilot lab: estimate tokens and cost from it"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan and the estimate, call no model"),
    check: bool = typer.Option(True, "--check/--no-check", help="Look-ahead check of every run's observations"),
) -> None:
    """Trade every variant on every period and seed, and save one record per run. / 每个变体在每个时段、每个种子上各跑一次"""
    from . import prompts as P

    con = _console()
    try:
        chosen = P.select(variants) if variants else []
        if prompt_file is not None:
            chosen.append(P.from_file(prompt_file, name=name))
        sl = [int(x) for x in seed_list.split(",")] if seed_list else list(range(max(1, seeds)))
        st = P.LabSettings(llm=llm, periods=[x.strip() for x in periods.split(",") if x.strip()], seeds=sl,
                           universe=None if symbols else universe,
                           symbols=[x.strip().upper() for x in symbols.split(",")] if symbols else None,
                           max_positions=max_positions, temperature=temperature, max_tokens=max_tokens,
                           blind=not named, base_url=base_url, price_in=price_in, price_out=price_out, start=start,
                           end=end)
        est = P.estimate(chosen, st, pilot)
    except (ValueError, FileNotFoundError) as exc:
        con.print(f"[red]{escape(str(exc))}[/]")
        raise typer.Exit(2) from exc
    con.print(_L(f"{len(chosen)} variants x {len(st.periods)} periods x {len(sl)} seeds = {est['runs']} runs, "
                 f"{est['calls']:,} model calls",
                 f"{len(chosen)} 个变体 x {len(st.periods)} 个时段 x {len(sl)} 个种子 = {est['runs']} 次，"
                 f"共 {est['calls']:,} 次模型调用"))
    if est["tokens"]:
        con.print(_L(f"Estimated from the pilot: {est['tokens']:,} tokens", f"按 pilot 估计：{est['tokens']:,} token")
                  + (f", ${est['list_price_usd']:.2f}" + _L(" at the given prices", "（按给定价格）")
                     if est["list_price_usd"] is not None else ""))
    if dry_run:
        t = Table()
        for col in ("variant", "runs", "calls", "tokens_per_call", "tokens"):
            t.add_column(col, justify="left" if col == "variant" else "right")
        for r in est["variants"]:
            t.add_row(r["variant"], str(r["runs"]), f"{r['calls']:,}", str(r["tokens_per_call"] or ""),
                      f"{r['tokens']:,}" if r["tokens"] else "")
        con.print(t)
        return

    def show(r: dict) -> None:
        if r.get("skipped"):
            msg = _L("already done", "已有")
        elif r.get("error"):
            msg = f"[red]{escape(r['error'][:200])}[/]"
        else:
            msg = (f"Sharpe {r.get('sharpe') or 0:.2f}  {_L('return', '收益')} {(r.get('total_return') or 0) * 100:+.1f}%"
                   f"  {r.get('steps')} {_L('steps', '步')}  {r.get('seconds')} s  "
                   f"{_L('look-ahead', '防偷看')} {r.get('leak_check')}")
        con.print(f"{r['variant']:<14} {r['period']} s{r['seed']}  {msg}")

    lab = P.run_lab(chosen, st, out, workers=workers, check=check, progress=show)
    con.print(f"{_L('Lab', '实验文件夹')}: {lab}", soft_wrap=True)
    con.print(_L(f"Next: riskaware research prompts report {lab}", f"下一步：riskaware research prompts report {lab}"),
              soft_wrap=True)


@app.command("report")
def report_cmd(
    lab: Path = typer.Argument(..., help="The lab folder"),
    site: Optional[Path] = typer.Option(None, help="Also write the website's data file here"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Paired comparisons with the baseline: intervals, p-values, Holm correction. / 和基线配对比较，给区间和校正后的 p 值"""
    from .prompt_report import report

    con = _console()
    try:
        r = report(lab, site=site)
    except (ValueError, FileNotFoundError) as exc:
        con.print(f"[red]{escape(str(exc))}[/]")
        raise typer.Exit(2) from exc
    if as_json:
        con.print_json(json.dumps({k: v for k, v in r.items() if k != "runs"}, default=str))
        return
    con.print((Path(lab) / "prompts.md").read_text(encoding="utf-8"))
    con.print(f"{_L('Files', '文件')}: {Path(lab) / 'prompts.json'}, prompts.md, prompts.png"
              + (f", {site}" if site else ""), soft_wrap=True)


@app.command("board")
def board_cmd(
    labs: list[Path] = typer.Argument(..., help="Lab folders, one model each"),
    site: Path = typer.Option(Path("site/assets/data/prompts.json"), help="Where to write the website's data"),
) -> None:
    """Combine labs (one model each) into the website's prompt board. / 把几个模型的实验合成网站上的 prompt 榜"""
    from .prompt_report import board

    con = _console()
    try:
        out = board(labs, site=site)
    except (ValueError, FileNotFoundError) as exc:
        con.print(f"[red]{escape(str(exc))}[/]")
        raise typer.Exit(2) from exc
    con.print(_L(f"{len(out['boards'])} boards", f"{len(out['boards'])} 个模型的榜") + f" -> {site}", soft_wrap=True)
    ac = out.get("across_models")
    if ac:
        t = Table(title=_L("Across models: vs the baseline", "跨模型：和基线比"))
        for col in ("variant", "Δ Sharpe", "95% CI", "p (Holm)", "Δ return", "wins", "verdict"):
            t.add_column(col)
        for v in ac["variants"]:
            c, tr = (v["vs_baseline"] or {}).get("sharpe"), (v["vs_baseline"] or {}).get("total_return")
            if not c:
                continue
            t.add_row(v["name"], f"{c['diff']:+.2f}", f"{c['ci95'][0]:+.2f} to {c['ci95'][1]:+.2f}", f"{c['p_holm']:.3f}",
                      f"{tr['diff'] * 100:+.1f}%" if tr else "", f"{c['wins']}/{c['pairs']}", c["verdict"])
        con.print(t)
