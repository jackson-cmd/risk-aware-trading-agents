"""Strategy lab: an AI assistant writes a strategy as code, and the lab checks it and backtests it.

Over MCP (``strategy_guide``, then ``backtest_code``) Claude, Codex or Gemini can turn a request such as "buy
dips in uptrends, but not after bad news" into a ``Strategy`` class and test it on history. The code is checked
before it runs, then it runs in a separate Python process.

Look-ahead. A backtest only means something when the strategy cannot know what happened later. Inside the
backtest the strategy sees ``ctx``, which holds nothing after the decision day: news items published before
that day's close only, and indicator rows and price history as copies. The check rejects the ways an author,
human or AI, can still bring the future in: a date written into the code (``date(2026, 4, 9)``,
``"2026-04-09"``, ``.year``, ``today()``), stocks named in the code (the stocks come from the request, so a model
cannot pick the ones it knows went up; index and sector ETFs are fine as market gauges), and reading files.

Side effects. Imports are limited to a short list (math, statistics, numpy, pandas, ... and riskaware.strategy);
``open``, ``eval``, ``exec``, ``getattr`` and private or double-underscore attributes are rejected. While the
strategy's own code runs, an audit hook also refuses network connections, subprocesses, file writes and reads
outside Python's own files. The process gets no API keys, a time limit and, where the system allows, a CPU limit.

These checks stop mistakes and casual misuse. They are not a sandbox for hostile code: run code you would not
trust on your machine in a container.
"""

from __future__ import annotations

import ast
import builtins
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import date
from pathlib import Path
from typing import Any

from .config import RISKAWARE_HOME

AI_DIR = RISKAWARE_HOME / "strategies" / "ai"
FILENAME = "<strategy>"
MAX_CODE = 40_000
MAX_PRINT = 200

ALLOWED_MODULES = {
    "__future__", "math", "cmath", "statistics", "random", "numpy", "pandas", "collections", "collections.abc",
    "itertools", "functools", "dataclasses", "typing", "enum", "decimal", "fractions", "heapq", "bisect", "datetime",
}
RISKAWARE_MODULES = {
    "riskaware.strategy": {"Strategy", "Signal", "Action", "StrategyContext", "RowView", "PositionView",
                           "PortfolioView"},
    "riskaware.types": {"Signal", "Action", "Regime", "NewsStatus"},
    "riskaware.indicators": {"ema", "sma", "wilder", "rsi", "true_range", "atr", "adx", "macd", "bollinger",
                             "zscore", "realized_vol", "down_streak", "below_streak", "rolling_beta", "pct_rank"},
}
BANNED_NAMES = {
    "open", "eval", "exec", "compile", "__import__", "globals", "locals", "vars", "getattr", "setattr", "delattr",
    "input", "breakpoint", "help", "memoryview", "exit", "quit", "__builtins__", "__loader__", "__spec__",
}
BANNED_ATTRS = {
    # files, processes, interpreter internals
    "load", "loads", "save", "savez", "savez_compressed", "savetxt", "loadtxt", "genfromtxt", "fromfile", "tofile",
    "fromregex", "memmap", "DataSource", "ctypes", "ctypeslib", "lib", "as_strided", "f2py", "distutils", "testing",
    "io", "os", "sys", "subprocess", "builtins", "importlib", "gc", "inspect", "pickle", "marshal", "system", "popen",
    "HDFStore", "ExcelFile", "ExcelWriter", "to_csv", "to_pickle", "to_parquet", "to_feather", "to_hdf", "to_sql",
    "to_excel", "to_json", "to_html", "to_latex", "to_markdown", "to_stata", "to_clipboard", "to_xml", "to_orc",
    "to_gbq",
    # code from strings
    "eval", "query", "format", "format_map",
}
BANNED_PREFIXES = ("read_", "gi_", "cr_", "ag_", "f_", "tb_", "co_")
DATE_ATTRS = {
    "year", "today", "now", "utcnow", "fromisoformat", "strptime", "fromtimestamp", "utcfromtimestamp",
    "fromordinal", "toordinal", "timestamp", "isoformat", "strftime", "ctime", "timetuple", "isocalendar",
}
DATE_CALLS = {"date", "datetime", "Timestamp", "to_datetime", "datetime64", "Period", "DatetimeIndex", "date_range",
              "bdate_range", "period_range"}
DATE_TEXT = re.compile(r"(?<!\d)(?:19|20)\d{2}[-/.](?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12]\d|3[01])(?!\d)"
                       r"|(?<!\d)(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?!\d)")
LOOK_AHEAD_NOTE = ("Each day the strategy sees only that day's indicators, past prices and news published before the "
                   "close; the code may not name dates or stocks, and orders fill the next day.")


# ----------------------------------------------------------------------------------- static check
def _gauges() -> set[str]:
    from .universe import BOND_ETFS, BROAD_ETFS, ETF_SYMBOLS, SECTOR_ETFS

    return set(BROAD_ETFS) | set(SECTOR_ETFS) | set(BOND_ETFS) | ETF_SYMBOLS | {"^VIX", "^VIX3M", "VIX"}


def _known_stocks(extra: list[str] | None) -> set[str]:
    from .news.relevance import ALIASES
    from .universe import SECTOR_MAP

    return ({*SECTOR_MAP, *ALIASES, *(s.upper() for s in extra or [])}) - _gauges()


class _Checker(ast.NodeVisitor):
    def __init__(self, stocks: set[str]):
        self.stocks = stocks
        self.problems: list[dict[str, Any]] = []
        self.warnings: list[dict[str, Any]] = []
        self.strategy_classes: list[str] = []
        self.has_on_bar = False

    def flag(self, node: ast.AST, message: str) -> None:
        self.problems.append({"line": getattr(node, "lineno", 0), "problem": message})

    # imports
    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            if a.name.startswith("riskaware"):
                self.flag(node, f"use 'from {a.name} import ...' with the names you need, not 'import {a.name}'")
            elif a.name not in ALLOWED_MODULES:
                self.flag(node, f"import of '{a.name}' is not allowed; allowed: {', '.join(sorted(ALLOWED_MODULES))}"
                                f", and riskaware.strategy")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        names = [a.name for a in node.names]
        if node.level:
            self.flag(node, "relative imports are not allowed")
        elif "*" in names:
            self.flag(node, f"'from {mod} import *' is not allowed; import the names you need")
        elif mod in RISKAWARE_MODULES:
            bad = [n for n in names if n not in RISKAWARE_MODULES[mod]]
            if bad:
                self.flag(node, f"from {mod} you can import {', '.join(sorted(RISKAWARE_MODULES[mod]))}; "
                                f"not {', '.join(bad)}")
        elif mod.startswith("riskaware") or mod not in ALLOWED_MODULES:
            self.flag(node, f"import from '{mod}' is not allowed")
        self.generic_visit(node)

    # names and attributes
    def visit_Name(self, node: ast.Name) -> None:
        if node.id in BANNED_NAMES:
            self.flag(node, f"'{node.id}' is not allowed in a strategy")
        elif node.id.startswith("__") and node.id not in ("__name__",):
            self.flag(node, f"'{node.id}' is not allowed")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        attr = node.attr
        on_self = isinstance(node.value, ast.Name) and node.value.id == "self"
        if attr == "__init__" and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) \
                and node.value.func.id == "super":  # super().__init__(**params) in the strategy's own __init__
            self.generic_visit(node)
            return
        if attr.startswith("__"):
            self.flag(node, f"double-underscore attributes ('{attr}') are not allowed")
        elif attr.startswith("_") and not on_self:
            self.flag(node, f"private attributes of other objects ('.{attr}') are not allowed; use the public "
                            f"ctx methods (ctx.row, ctx.history, ctx.position, ctx.news, ctx.market, ctx.portfolio)")
        elif attr in DATE_ATTRS:
            self.flag(node, f"'.{attr}' is not allowed: a rule that depends on the calendar year or on today's "
                            f"date is look-ahead; month and weekday are fine (ctx.day.month, ctx.day.weekday())")
        elif not on_self and (attr in BANNED_ATTRS or attr.startswith(BANNED_PREFIXES)):
            self.flag(node, f"'.{attr}' is not allowed in a strategy (files, processes or code from strings)")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
        if name in DATE_CALLS:
            self.flag(node, f"'{name}(...)' builds a fixed date, which is look-ahead in a backtest; compare "
                            f"ctx.day with dates the strategy saw itself (e.g. a stored entry day)")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            text = node.value.strip()
            if DATE_TEXT.search(node.value):
                self.flag(node, "a date written in a string is look-ahead in a backtest")
            elif text.lstrip("$") in self.stocks and text.lstrip("$").isupper():
                self.flag(node, f"'{text}' names a stock in the code. Pass the stocks with symbols or universe "
                                f"instead: picking stocks you already know went up is look-ahead. Index and sector "
                                f"ETFs (SPY, QQQ, XLK, ...) are fine as market gauges.")

    def visit_Compare(self, node: ast.Compare) -> None:
        sides = [node.left, *node.comparators]
        cal = any(isinstance(x, ast.Attribute) and x.attr in ("month", "day") for x in sides)
        fixed = any(isinstance(x, (ast.Constant, ast.Tuple, ast.List, ast.Set)) for x in sides)
        if cal and fixed and any(isinstance(op, (ast.Eq, ast.In)) for op in node.ops):
            self.warnings.append({"line": node.lineno, "warning": "compares the calendar with fixed values; a rule "
                                  "that fires on chosen days can encode what you know happened on them"})
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        bases = [b.id if isinstance(b, ast.Name) else b.attr if isinstance(b, ast.Attribute) else "" for b in node.bases]
        if "Strategy" in bases:
            self.strategy_classes.append(node.name)
            if any(isinstance(b, ast.FunctionDef) and b.name == "on_bar" for b in node.body):
                self.has_on_bar = True
        self.generic_visit(node)


def check_code(code: str, symbols: list[str] | None = None) -> list[dict[str, Any]]:
    """Problems that keep the code from running, each with a line number (an empty list means it may run)."""
    return review_code(code, symbols)[0]


def review_code(code: str, symbols: list[str] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(problems, warnings): problems block the run; warnings are returned with the result."""
    if not isinstance(code, str) or not code.strip():
        return [{"line": 0, "problem": "no code"}], []
    if len(code) > MAX_CODE:
        return [{"line": 0, "problem": f"the code is longer than {MAX_CODE} characters"}], []
    try:
        tree = ast.parse(code, FILENAME)
    except SyntaxError as exc:
        return [{"line": exc.lineno or 0, "problem": f"syntax error: {exc.msg}"}], []
    chk = _Checker(_known_stocks(symbols))
    chk.visit(tree)
    if len(chk.strategy_classes) != 1:
        chk.problems.append({"line": 0, "problem": f"define exactly one class that subclasses Strategy "
                                                   f"(found {len(chk.strategy_classes)})"})
    elif not chk.has_on_bar:
        chk.problems.append({"line": 0, "problem": "the Strategy class needs an on_bar(self, ctx) method"})
    seen, out = set(), []
    for p in sorted(chk.problems, key=lambda p: p["line"]):
        key = (p["line"], p["problem"])
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out, chk.warnings


# ----------------------------------------------------------------------------------- runtime guard
_ACTIVE = False
_HOOKED = False
_READ_ROOTS: tuple[str, ...] = ()
_DENY_ROOTS: tuple[str, ...] = ()
_BLOCKED_PREFIXES = ("socket.", "subprocess.", "os.exec", "os.posix_spawn", "os.spawn", "os.fork", "shutil.",
                     "ctypes.", "urllib.", "http.", "ftplib.", "smtplib.", "telnetlib.", "webbrowser.", "winreg.",
                     "_winapi.", "sqlite3.")
_BLOCKED_EVENTS = {"os.system", "os.kill", "os.killpg", "os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.chmod",
                   "os.chown", "os.symlink", "os.link", "os.truncate", "os.utime", "os.putenv", "os.unsetenv",
                   "os.chdir", "pickle.find_class", "marshal.load", "marshal.loads"}
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


def _real(p: str) -> str:
    return os.path.realpath(os.path.expanduser(p))


def _read_roots() -> tuple[tuple[str, ...], tuple[str, ...]]:
    import site
    import sysconfig

    import riskaware

    roots = {sys.prefix, sys.base_prefix, sys.exec_prefix, *(site.getsitepackages() if hasattr(site, "getsitepackages")
                                                            else []),
             *(v for k, v in sysconfig.get_paths().items() if k in ("stdlib", "platstdlib", "purelib", "platlib")),
             str(Path(riskaware.__file__).parent), "/usr/share/zoneinfo", "/usr/lib/locale"}
    deny = {str(RISKAWARE_HOME), str(Path(riskaware.__file__).parent / "news" / "data")}
    return tuple(_real(r) + os.sep for r in roots if r), tuple(_real(d) + os.sep for d in deny)


def _audit(event: str, args: tuple) -> None:
    if not _ACTIVE:
        return
    if event == "open":
        path, mode, flags = (list(args) + [None, None, None])[:3]
        if isinstance(path, int) or path is None:
            return
        text = _real(os.fsdecode(path))
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or \
            (isinstance(flags, int) and bool(flags & _WRITE_FLAGS))
        allowed = (text + os.sep).startswith(_READ_ROOTS) and not (text + os.sep).startswith(_DENY_ROOTS)
        if writing or not allowed:
            raise PermissionError(f"a strategy may not open files ({os.fsdecode(path)})")
        return
    if event in _BLOCKED_EVENTS or event.startswith(_BLOCKED_PREFIXES):
        raise PermissionError(f"a strategy may not do this ({event})")


def install_guard() -> None:
    """Add the audit hook (once per process; it acts only while strategy code runs)."""
    global _HOOKED, _READ_ROOTS, _DENY_ROOTS
    if not _HOOKED:
        _READ_ROOTS, _DENY_ROOTS = _read_roots()
        sys.addaudithook(_audit)
        _HOOKED = True


class _Printed(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.lines = 0

    def write(self, s: str) -> int:
        if self.lines < MAX_PRINT:
            self.lines += s.count("\n")
            return super().write(s)
        return len(s)


_PRINTED = _Printed()


@contextlib.contextmanager
def strategy_code():
    """Run a block as strategy code: the audit hook refuses side effects and print() is captured."""
    global _ACTIVE
    prev, _ACTIVE = _ACTIVE, True
    try:
        with contextlib.redirect_stdout(_PRINTED):
            yield
    finally:
        _ACTIVE = prev


def _guarded_import(name: str, globals_: Any = None, locals_: Any = None, fromlist: Any = (), level: int = 0):
    if level:
        raise ImportError("relative imports are not allowed in a strategy")
    if name not in ALLOWED_MODULES and name not in RISKAWARE_MODULES:
        raise ImportError(f"import of '{name}' is not allowed in a strategy")
    if name in RISKAWARE_MODULES and (not fromlist or any(n not in RISKAWARE_MODULES[name] for n in fromlist)):
        raise ImportError(f"from {name} only {', '.join(sorted(RISKAWARE_MODULES[name]))} may be imported")
    return builtins.__import__(name, globals_, locals_, fromlist, level)


SAFE_BUILTINS = (
    "abs", "all", "any", "bool", "callable", "chr", "dict", "divmod", "enumerate", "filter", "float", "frozenset",
    "hasattr", "hash", "int", "isinstance", "issubclass", "iter", "len", "list", "map", "max", "min", "next",
    "object", "ord", "pow", "print", "property", "range", "repr", "reversed", "round", "set", "slice", "sorted",
    "staticmethod", "classmethod", "str", "sum", "super", "tuple", "type", "zip", "ArithmeticError",
    "AttributeError", "Exception", "IndexError", "KeyError", "LookupError", "NotImplementedError", "OverflowError",
    "RuntimeError", "StopIteration", "TypeError", "ValueError", "ZeroDivisionError", "True", "False", "None",
    "NotImplemented", "Ellipsis", "__build_class__",
)


def load_code(code: str) -> type:
    """Execute the code with limited builtins and return its Strategy subclass."""
    from .strategy import Strategy

    names = {n: getattr(builtins, n) for n in SAFE_BUILTINS}
    names["__import__"] = _guarded_import
    module: dict[str, Any] = {"__name__": "riskaware_ai_strategy", "__builtins__": names}
    with strategy_code():
        exec(compile(code, FILENAME, "exec"), module)  # noqa: S102 - checked above, guarded while it runs
    found = [v for v in module.values() if isinstance(v, type) and issubclass(v, Strategy) and v is not Strategy
             and v.__module__ == "riskaware_ai_strategy"]
    if len(found) != 1:
        raise ValueError(f"expected exactly one Strategy subclass, found {len(found)}")
    return found[0]


def guard_strategy(strat: Any) -> Any:
    """Make the strategy's own methods run under the guard."""
    for name in ("on_start", "on_bar", "warmup_bars"):
        fn = getattr(strat, name)

        def wrapped(*a: Any, _fn: Any = fn, **k: Any) -> Any:
            with strategy_code():
                return _fn(*a, **k)

        setattr(strat, name, wrapped)
    return strat


def _limit_resources(seconds: float) -> None:
    try:
        import resource

        cpu = int(seconds) + 30
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 5))
        if sys.platform.startswith("linux"):
            resource.setrlimit(resource.RLIMIT_AS, (6 << 30, 6 << 30))
    except (ImportError, ValueError, OSError):
        pass


# ----------------------------------------------------------------------------------- the backtest (child process)
def _num(v: Any) -> Any:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return None if f != f or f in (float("inf"), float("-inf")) else round(f, 4)


def _user_error(exc: BaseException) -> dict[str, Any]:
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == FILENAME]
    lines = [{"line": f.lineno, "in": f.name} for f in frames[-3:]]
    return {"ok": False, "stage": "run", "error": f"{type(exc).__name__}: {exc}"[:500],
            "where": lines or None, "printed": _PRINTED.getvalue()[-4000:] or None}


def _trades(trades: Any) -> dict[str, Any]:
    if trades is None or len(trades) == 0:
        return {"count": 0}
    t = trades
    sells = t[t["side"] == "SELL"] if "side" in t else t.iloc[0:0]
    closed = sells["pnl"].dropna() if "pnl" in sells else []
    by_sym = t.groupby("symbol").agg(trades=("symbol", "size"), pnl=("pnl", "sum")).sort_values("trades",
                                                                                              ascending=False)
    keep = [c for c in ("date", "symbol", "side", "qty", "price", "pnl", "reason") if c in t]
    recent = t[keep].tail(12).copy()
    if "date" in recent:
        recent["date"] = recent["date"].astype(str).str[:10]
    return {"count": int(len(t)), "buys": int((t["side"] == "BUY").sum()) if "side" in t else None,
            "sells": int(len(sells)), "sells_with_gain": int((closed > 0).sum()) if len(closed) else 0,
            "realized_pnl": _num(closed.sum()) if len(closed) else 0.0,
            "by_symbol": [{"symbol": s, "trades": int(r.trades), "pnl": _num(r.pnl)}
                          for s, r in by_sym.head(10).iterrows()],
            "recent": [{k: _num(v) if k in ("qty", "price", "pnl") else v for k, v in row.items()}
                       for row in recent.to_dict("records")]}


def run_research_job(job: dict[str, Any]) -> dict[str, Any]:
    """Run the code as a research experiment on the frozen market (called in the child process)."""
    from .research.data import bundled_market
    from .research.experiment import Experiment, run_experiment

    settings = dict(job["research"])
    exp = Experiment(**settings)
    market = bundled_market(exp.period)  # read the data before any strategy code runs
    install_guard()
    try:
        cls = load_code(job["code"])
        params = dict(settings.get("params") or {})
        res = run_experiment(exp, factory=lambda: guard_strategy(cls(**params)), market=market, save=True,
                             code=job["code"])
    except Exception as exc:  # noqa: BLE001 - every error in the strategy goes back to the author
        return _user_error(exc)
    out: dict[str, Any] = {"ok": True, **res.summary(), "look_ahead": LOOK_AHEAD_NOTE}
    printed = _PRINTED.getvalue()
    if printed:
        out["printed"] = printed[-4000:]
    return out


def run_job(job: dict[str, Any]) -> dict[str, Any]:
    """Check and backtest one strategy (called in the child process)."""
    if job.get("research"):
        return run_research_job(job)
    from .backtest.engine import Backtester
    from .config import load_config
    from .data.prices import SyntheticPrices, make_price_provider
    from .news.history import NewsArchive, PointInTimeNews
    from .news.relevance import company_names
    from .strategy import load_strategy
    from .utils import DiskCache

    t0 = time.time()
    cfg = load_config(job.get("config_path"), {"profile": job.get("profile") or "balanced"})
    if job.get("symbols"):
        cfg.universe.preset = None
        cfg.universe.symbols = [s.upper() for s in job["symbols"]]
    elif job.get("universe"):
        cfg.universe.preset = job["universe"]
        cfg.universe.symbols = []
    start = date.fromisoformat(job["start"])
    end = date.fromisoformat(job["end"]) if job.get("end") else date.today()
    cache = DiskCache(cfg.data.cache_path)
    prices = SyntheticPrices(seed=7, start=start.replace(year=start.year - 3), end=end) if job.get("synthetic") \
        else make_price_provider(cfg.data.price_source, cache, cfg.data.csv_dir)
    bt = Backtester(cfg, prices)
    universe = bt.universe_for()
    if not job.get("_skip_check"):
        problems = check_code(job["code"], universe)
        if problems:
            return {"ok": False, "stage": "check", "problems": problems}
    pit = None
    if job.get("news") and not job.get("synthetic"):
        archive = NewsArchive(cfg.news.archive_path, bundled=cfg.news.bundled)
        pit = PointInTimeNews(archive, cfg.news, universe, names=lambda s: company_names(s, cache))
        pit = pit if pit.symbols else None
    bt.news = pit
    bt.load(universe, start, end)  # prices are fetched here, before any strategy code runs
    install_guard()
    try:
        cls = load_code(job["code"])
        strat = guard_strategy(cls(**(job.get("params") or {})))
        res = bt.run(strat, start, end, initial_cash=job.get("cash"))
    except Exception as exc:  # noqa: BLE001 - every error in the strategy goes back to the author
        return _user_error(exc)
    out: dict[str, Any] = {
        "ok": True, "strategy": res.strategy, "period": f"{res.start} to {res.end}", "profile": res.profile,
        "symbols": universe, "metrics": {k: _num(v) for k, v in res.metrics.items()},
        "spy_buy_and_hold": {k: _num(v) for k, v in res.benchmark_metrics.items()}, "trades": _trades(res.trades),
        "risk_manager_rejections": dict(res.rejections), "notes": list(res.notes), "look_ahead": LOOK_AHEAD_NOTE,
    }
    compare = {}
    for name in job.get("compare") or []:
        try:
            r = bt.run(load_strategy(name), start, end, initial_cash=job.get("cash"))
            compare[name] = {k: _num(v) for k, v in r.metrics.items()}
        except Exception as exc:  # noqa: BLE001
            compare[name] = {"error": str(exc)[:200]}
    if compare:
        out["compare"] = compare
    printed = _PRINTED.getvalue()
    if printed:
        out["printed"] = printed[-4000:]
    out["seconds"] = round(time.time() - t0, 1)
    return out


def child_main() -> None:
    job = json.loads(sys.stdin.read())
    _limit_resources(float(job.get("timeout") or 600))
    try:
        out = run_job(job)
    except Exception as exc:  # noqa: BLE001 - a broken setup (no prices, bad dates) is reported, not raised
        out = {"ok": False, "stage": "setup", "error": f"{type(exc).__name__}: {exc}"[:500]}
    sys.__stdout__.write("\n" + json.dumps(out, default=str) + "\n")
    sys.__stdout__.flush()


# ----------------------------------------------------------------------------------- the guide
TEMPLATE = """from riskaware.strategy import Action, Signal, Strategy


class DipInUptrend(Strategy):
    name = "dip_in_uptrend"
    description = "Buy oversold dips above the 200-day EMA; sell into strength or when the trend breaks."
    default_params = {"rsi_entry": 30, "take_profit": 0.15, "max_days": 40}

    def on_bar(self, ctx):
        p = self.params
        signals = []
        for sym in ctx.universe:
            row = ctx.row(sym)
            if row is None or row.get("ema200") != row.get("ema200"):  # NaN while indicators warm up
                continue
            pos = ctx.position(sym)
            if pos is None:
                if row["close"] > row["ema200"] and row["rsi14"] < p["rsi_entry"]:
                    signals.append(Signal(sym, Action.BUY, strength=0.7, reason="oversold in an uptrend"))
            elif pos.gain >= p["take_profit"] or pos.days_held >= p["max_days"] or row["close"] < row["ema200"]:
                signals.append(Signal(sym, Action.SELL, strength=0.8, fraction=1.0, reason="exit"))
        return signals
"""

NEWS_EXAMPLE = """from riskaware.strategy import Action, Signal, Strategy


class CalmNewsMomentum(Strategy):
    name = "calm_news_momentum"
    description = "Momentum buys while the news is not negative; sell on a confirmed red flag."
    default_params = {"min_mom": 0.10}

    def on_bar(self, ctx):
        signals = []
        for sym in ctx.universe:
            row = ctx.row(sym)
            if row is None or row.get("mom126") != row.get("mom126"):
                continue
            news = ctx.news(sym)  # None when nothing was published in the lookback window
            pos = ctx.position(sym)
            if pos is not None and news is not None and news.red_flags_confirmed:
                signals.append(Signal(sym, Action.SELL, 0.9, "confirmed red flag", fraction=1.0, emergency=True))
            elif pos is None and row["mom126"] > self.params["min_mom"] and (news is None or news.score >= 0):
                signals.append(Signal(sym, Action.BUY, 0.6, "momentum, calm news"))
        return signals
"""


def _indicator_columns() -> list[str]:
    from . import indicators as ind
    from .data.prices import SyntheticPrices

    d0, d1 = date(2020, 1, 1), date(2021, 6, 30)
    fr = SyntheticPrices(seed=1, start=d0, end=d1).history(["AAA", "SPY"], d0, d1)
    return list(ind.compute(fr["AAA"], fr["SPY"]["close"]).columns)


def guide() -> dict[str, Any]:
    """Everything an AI needs to write a strategy for backtest_code."""
    from .news.history import load_bundle

    bundle = load_bundle()
    news_note = (f"2026 news: {sum(len(v) for v in bundle.values())} headlines and SEC 8-K filings for "
                 f"{len(bundle)} stocks of the built-in watchlists, January to November 2026 (your own copy)."
                 if bundle else "No news ships with the package (the headlines are not redistributed); news comes "
                                "from your local archive.")
    return {
        "how_it_works": (
            "Write one Python class that subclasses Strategy and defines on_bar(self, ctx). Each trading day, "
            "after the close, on_bar returns Signals (what you would like to buy or sell and how sure you are). "
            "The risk manager sizes every order, keeps a cash floor, caps positions and sectors, and may refuse a "
            "signal in a risky market regime. Orders fill at the next day's open. Then call backtest_code with "
            "the code; it reports metrics, trades and comparisons, or the problems to fix."),
        "rules": [
            "Imports: math, statistics, random, numpy, pandas, collections, itertools, functools, dataclasses, "
            "typing, enum, decimal, fractions, heapq, bisect, datetime; from riskaware.strategy import Strategy, "
            "Signal, Action; from riskaware.indicators import ema, sma, rsi, atr, macd, bollinger, zscore, ...",
            "No dates in the code: no date(...), Timestamp(...), '2026-04-09', .year, today() or now(). ctx.day "
            "is the decision day; use ctx.day.month or ctx.day.weekday() for seasonality, and remember days you "
            "saw yourself (for example an entry day stored in self).",
            "No stock tickers in the code: the stocks come from the request (symbols or universe) and arrive in "
            "ctx.universe. Index and sector ETFs (SPY, QQQ, XLK, ...) are fine as market gauges via ctx.row.",
            "No files, network, processes, open, eval, exec, getattr, or private and double-underscore "
            "attributes. Keep state in self (self._entries = {...} is fine).",
            "Everything the strategy may know is in ctx, and ctx holds nothing after ctx.day.",
        ],
        "ctx": {
            "ctx.day": "the decision day (datetime.date)",
            "ctx.universe": "the symbols to trade",
            "ctx.row(symbol)": "today's indicators: row['close'], row.get('rsi14'), row.keys(); None without data",
            "ctx.history(symbol, n=260)": "pandas DataFrame of the last n days up to today, the same columns",
            "ctx.position(symbol)": "None, or a position: qty, avg_cost, price, weight, days_held, gain, value, "
                                    "peak_price, days_since_entry",
            "ctx.portfolio": "equity, cash, positions (dict), drawdown, cash_ratio, stock_exposure",
            "ctx.market": "regime (.value: RISK_ON, NEUTRAL, UNCERTAIN, RISK_OFF), vix, fear_greed (0-100), "
                          "spy_trend, breadth, event_risk, events (scheduled FOMC, CPI, jobs)",
            "ctx.news(symbol)": "None, or a report of what was published before today's close: score (-1..1), "
                                "confirmed, unconfirmed, red_flags_confirmed (list), severe_confirmed (bool), "
                                "clusters (stories: headline, status, publishers, sentiment, event_types such as "
                                "'earnings', 'mna', 'legal', 'management', red_flags, first_seen). Needs news=True.",
            "ctx.is_etf(symbol), ctx.is_broad_etf(symbol)": "True for ETFs",
        },
        "indicators": _indicator_columns(),
        "signal": ("Signal(symbol, Action.BUY or Action.SELL, strength 0..1, reason, setups=[...], fraction=share of "
                   "the position to sell (1.0 = all), emergency=True for a sell that must go through in any "
                   "regime). Ordinary sells only go through in RISK_ON and NEUTRAL markets."),
        "template": TEMPLATE,
        "news_example": NEWS_EXAMPLE,
        "news": news_note,
        "tips": [
            "Start from the template and change one idea at a time.",
            "Compare with buy_and_hold and SPY (backtest_code does both by default).",
            "Results on the period you tuned on are optimistic. After several tries, confirm on a period you did "
            "not tune on (for example tune on January to June, then test July to November once).",
            "A model trained on text from the test period knows how it ended; take the rules from the request, "
            "not from what you remember about the period.",
            "save_as='name' keeps a strategy that works; agents_create can then paper trade it every day.",
            "For an experiment others can repeat (a frozen 2026 market, a look-ahead check, a saved record with "
            "data hashes), read research_guide and pass the same code to run_experiment.",
        ],
    }


# ----------------------------------------------------------------------------------- the parent side
def _child_env() -> dict[str, str]:
    """A small environment for the child: no API keys, the same riskaware package and data folders."""
    import riskaware

    keep = ("PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL",
            "APPDATA", "LOCALAPPDATA", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy",
            "no_proxy", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "RISKAWARE_HOME", "RISKAWARE_CONFIG")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env["PYTHONPATH"] = str(Path(riskaware.__file__).resolve().parent.parent)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


_NAME = re.compile(r"[a-z][a-z0-9_]{0,39}")


def save_code(code: str, name: str) -> Path:
    if not _NAME.fullmatch(name or ""):
        raise ValueError("save_as must be 1-40 lowercase letters, digits or underscores, starting with a letter")
    AI_DIR.mkdir(parents=True, exist_ok=True)
    path = AI_DIR / f"{name}.py"
    path.write_text(code if code.endswith("\n") else code + "\n", encoding="utf-8")
    return path


def saved_strategies() -> list[dict[str, str]]:
    if not AI_DIR.is_dir():
        return []
    return [{"name": p.stem, "path": str(p)} for p in sorted(AI_DIR.glob("*.py"))]


def backtest_code(code: str, symbols: list[str] | None = None, universe: str | None = "megacap",
                  start: str = "2026-01-02", end: str | None = None, news: bool = True, profile: str = "balanced",
                  cash: float = 100_000.0, params: dict[str, Any] | None = None, compare: list[str] | None = None,
                  save_as: str | None = None, timeout: float = 600.0, synthetic: bool = False,
                  config_path: str | None = None, research: dict[str, Any] | None = None) -> dict[str, Any]:
    """Check the code, backtest it in a separate process and return the result (see the module docstring).

    ``research`` runs it as a research experiment instead (riskaware.research): the settings of an ``Experiment``
    (start, end, universe or symbols, news, execution, max_positions, split, leak_check, ...); the result is the
    experiment's summary and the folder of its record."""
    from .config import load_config

    cfg = load_config(config_path, {"profile": profile})
    if symbols:
        cfg.universe.preset, cfg.universe.symbols = None, [s.upper() for s in symbols]
    elif universe:
        cfg.universe.preset, cfg.universe.symbols = universe, []
    try:
        names = cfg.universe.resolve()
        date.fromisoformat(start)
        if end:
            date.fromisoformat(end)
    except ValueError as exc:
        return {"ok": False, "stage": "request", "error": str(exc)}
    problems, warnings = review_code(code, names)
    if problems:
        return {"ok": False, "stage": "check", "problems": problems,
                "fix": "Change the lines listed and call backtest_code again. strategy_guide has the rules."}
    if save_as and not _NAME.fullmatch(save_as):
        return {"ok": False, "stage": "request", "error": "save_as must be 1-40 lowercase letters, digits or "
                                                          "underscores, starting with a letter"}
    job = {"code": code, "symbols": symbols, "universe": universe, "start": start, "end": end, "news": news,
           "profile": profile, "cash": cash, "params": params or {}, "compare": list(compare or ["buy_and_hold"]),
           "synthetic": synthetic, "timeout": timeout,
           "config_path": config_path or getattr(cfg, "source_path", None), "research": research}
    with tempfile.TemporaryDirectory(prefix="riskaware-lab-") as tmp:
        try:
            proc = subprocess.run([sys.executable, "-m", "riskaware.lab", "--child"], input=json.dumps(job),
                                  capture_output=True, text=True, timeout=timeout, cwd=tmp, env=_child_env(),
                                  check=False)
        except subprocess.TimeoutExpired:
            return {"ok": False, "stage": "run", "error": f"the backtest took longer than {timeout:.0f} s; look for "
                                                          f"an endless loop or heavy work inside on_bar"}
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    try:
        out = json.loads(lines[-1])
    except (IndexError, json.JSONDecodeError):
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        return {"ok": False, "stage": "run", "error": f"the backtest process stopped (exit {proc.returncode}): "
                                                      + " | ".join(tail)[:500]}
    if warnings:
        out["warnings"] = warnings
    if out.get("ok") and save_as:
        path = save_code(code, save_as)
        out["saved"] = {"path": str(path), "backtest": f"riskaware backtest --strategy {path}",
                        "paper_agent": f"agents_create(name=..., strategy='{path}')"}
    return out


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if args == ["--child"]:
        child_main()
    else:
        print("usage: python -m riskaware.lab --child < job.json", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
