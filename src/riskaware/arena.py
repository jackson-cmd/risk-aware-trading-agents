"""Paper-trading agents: named strategies, each with its own local paper account, run once per trading day.

Everything lives under ``~/.riskaware/arena`` (or ``$RISKAWARE_HOME/arena``)::

    agents.yaml          the agent definitions (name, strategy, params, profile, universe, starting cash)
    <name>/<name>.json   the agent's paper account (cash, positions, orders, fills)
    <name>/state/        position memory: entry dates, tranches, trims
    <name>/journal.db    every run, decision and order
    <name>/equity.csv    one row per trading day: equity, cash, invested, regime
    last_run.json        which agents already traded today (a day is traded once)
    logs/agents.log      what the scheduler did

Agents only ever use the built-in paper broker with free Yahoo Finance prices: no account, no API key, and
no live orders, whatever the main configuration says.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import marketcal as mc
from .config import PROFILE_PRESETS, RISKAWARE_HOME, AppConfig, BrokerConfig, StrategyConfig, load_config
from .types import Quote

log = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

# a starter set: four different temperaments, all under the same 30% cash floor
STARTER_AGENTS: list[dict[str, Any]] = [
    {"name": "steady", "strategy": "risk_aware", "profile": "balanced",
     "note": "The default multi-agent strategy: regimes, EMA support, earnings-checked dips."},
    {"name": "momentum", "strategy": "momentum_rotation", "profile": "balanced",
     "note": "Holds the strongest large caps by 6-month relative strength, rebalanced monthly."},
    {"name": "breakout", "strategy": "donchian_breakout", "profile": "aggressive",
     "note": "Turtle-style 55-day breakouts on volume, out on a 20-day low."},
    {"name": "fear-buyer", "strategy": "fear_greed_dca", "profile": "conservative", "preset": "etf",
     "note": "Buys index and sector ETFs in steps when fear is high, trims in extreme greed."},
]


STARTER_NOTES_ZH = {
    "steady": "默认的多智能体策略：看市场状态、EMA 支撑位、财报过关才抄底。",
    "momentum": "持有 6 个月相对强度最强的大盘股，每月调仓。",
    "breakout": "海龟式：放量突破 55 日新高买入，跌破 20 日低点卖出。",
    "fear-buyer": "市场恐慌时分批买入指数和行业 ETF，极度贪婪时减仓。",
}


class AgentSpec(BaseModel):
    """One paper-trading agent."""

    model_config = ConfigDict(extra="forbid")

    name: str
    strategy: str = "risk_aware"
    params: dict[str, Any] = Field(default_factory=dict)
    profile: str = "balanced"
    preset: str | None = None
    symbols: list[str] = Field(default_factory=list)
    cash: float = Field(100_000.0, gt=0)
    note: str = ""
    enabled: bool = True
    llm: str | None = None  # None: as in the main config; "off"; or provider:model, e.g. openai:gpt-5.6-luna
    llm_base_url: str | None = None
    llm_key_env: str | None = None
    llm_command: str | None = None  # claude-cli / codex-cli / gemini-cli: the executable or a wrapper
    created: str = Field(default_factory=lambda: mc.now_ny().date().isoformat())

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip().lower()
        if not NAME_RE.match(v):
            raise ValueError("agent names use 1-32 lowercase letters, digits, '-' or '_' and start with a letter "
                             "or digit")
        return v

    @field_validator("profile")
    @classmethod
    def _profile(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in PROFILE_PRESETS:
            raise ValueError(f"profile must be one of {sorted(PROFILE_PRESETS)}")
        return v

    @field_validator("symbols")
    @classmethod
    def _symbols(cls, v: list[str]) -> list[str]:
        return [s.strip().upper() for s in v if s.strip()]

    @field_validator("llm")
    @classmethod
    def _llm(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        v = v.strip()
        if v.lower() in ("off", "none", "no"):
            return "off"
        from .llm import parse_choice

        kind, model = parse_choice(v)
        return f"{kind}:{model}"

    @property
    def llm_label(self) -> str:
        if self.llm is None:
            return "config"
        if self.llm == "off":
            return "off"
        kind, model = self.llm.split(":", 1)
        if kind.endswith("-cli"):  # a subscription: say which one
            return kind if model == "default" else f"{kind} {model}"
        return model

    @property
    def universe_label(self) -> str:
        parts = [self.preset] if self.preset else []
        if self.symbols:
            parts.append(",".join(self.symbols[:4]) + ("…" if len(self.symbols) > 4 else ""))
        return " + ".join(parts) or "default"


@dataclass
class AgentRun:
    name: str
    ok: bool
    regime: str | None = None
    equity: float | None = None
    orders: int = 0
    filled: int = 0
    blocked: int = 0
    signals: int = 0
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    report: Any = None

    def summary(self) -> str:
        if not self.ok:
            return f"{self.name}: FAILED {'; '.join(self.errors)[:300]}"
        eq = f"${self.equity:,.0f}" if self.equity is not None else "n/a"
        return f"{self.name}: {self.regime} · equity {eq} · {self.signals} signals · {self.filled}/{self.orders} filled"


class QuoteCache:
    """One price per symbol per run, shared by every agent, so they all trade against the same prices."""

    def __init__(self, fn: Callable[[list[str]], dict[str, Quote]] | None = None):
        if fn is None:
            from .brokers.paper import yahoo_quotes

            fn = yahoo_quotes
        self.fn = fn
        self.quotes: dict[str, Quote] = {}

    def __call__(self, symbols: list[str]) -> dict[str, Quote]:
        missing = [s for s in dict.fromkeys(symbols) if s not in self.quotes]
        if missing:
            self.quotes.update(self.fn(missing))
        return {s: self.quotes[s] for s in symbols if s in self.quotes}


def state_text(state: str, lang: str = "en") -> str:
    """The scheduler state from ``due`` in English or Chinese."""
    if lang != "zh":
        return state
    if state.startswith("waiting until"):
        return "等到纽约时间 " + state.split("until", 1)[1].replace("New York", "").strip()
    if state.endswith("is not a trading day"):
        return "今天不是交易日"
    return {"due": "正在运行时段", "the market has closed for today": "今天已收盘"}.get(state, state)


def due(now: datetime, run_time: str = "15:40", lead_minutes: int = 20) -> tuple[bool, str]:
    """Whether a scheduled run should trade now (New York time): a trading day, after run_time, before the close.

    On early-close days the run moves to ``lead_minutes`` before the close.
    """
    now = now.astimezone(mc.NY)
    d = now.date()
    hours = mc.market_hours(d)
    if hours is None:
        return False, f"{d:%a %Y-%m-%d} is not a trading day"
    hh, mm = (int(x) for x in run_time.split(":"))
    start = datetime.combine(d, time(hh, mm), mc.NY)
    start = min(start, hours[1] - timedelta(minutes=lead_minutes))
    if now < start:
        return False, f"waiting until {start:%H:%M} New York"
    if now >= hours[1]:
        return False, "the market has closed for today"
    return True, "due"


class Arena:
    """Load, save, run and score the paper-trading agents."""

    def __init__(self, root: str | Path | None = None, base_cfg: AppConfig | None = None):
        self.root = Path(root).expanduser() if root else RISKAWARE_HOME / "arena"
        self._base_cfg = base_cfg

    # definitions -----------------------------------------------------------------------
    @property
    def file(self) -> Path:
        return self.root / "agents.yaml"

    @property
    def base_cfg(self) -> AppConfig:
        if self._base_cfg is None:
            self._base_cfg = load_config()
        return self._base_cfg

    def load(self) -> list[AgentSpec]:
        if not self.file.is_file():
            return []
        raw = yaml.safe_load(self.file.read_text(encoding="utf-8")) or {}
        return [AgentSpec(**a) for a in raw.get("agents", [])]

    def save(self, agents: Iterable[AgentSpec]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        data = {"agents": [a.model_dump(exclude_defaults=False) for a in agents]}
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text("# Paper-trading agents. Edit here or with `riskaware agents add/remove`.\n"
                       + yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
        tmp.replace(self.file)

    def get(self, name: str) -> AgentSpec:
        for a in self.load():
            if a.name == name.lower():
                return a
        raise KeyError(f"no agent named '{name}'")

    def add(self, spec: AgentSpec, overwrite: bool = False) -> AgentSpec:
        from .strategy import load_strategy

        load_strategy(spec.strategy, spec.params)  # fail now, not at 15:40 tomorrow
        if spec.preset:
            from .universe import preset_symbols

            for part in spec.preset.split("+"):
                if not preset_symbols(part.strip()):
                    raise ValueError(f"unknown universe preset '{part}'")
        agents = self.load()
        if any(a.name == spec.name for a in agents):
            if not overwrite:
                raise ValueError(f"an agent named '{spec.name}' already exists")
            agents = [spec if a.name == spec.name else a for a in agents]
        else:
            agents.append(spec)
        self.save(agents)
        return spec

    def add_starters(self) -> list[AgentSpec]:
        have = {a.name for a in self.load()}
        added = []
        for s in STARTER_AGENTS:
            if s["name"] not in have:
                added.append(self.add(AgentSpec(**s)))
        return added

    def remove(self, name: str, delete_data: bool = True) -> None:
        spec = self.get(name)
        self.save([a for a in self.load() if a.name != spec.name])
        if delete_data:
            shutil.rmtree(self.dir(spec.name), ignore_errors=True)

    def reset(self, name: str) -> None:
        spec = self.get(name)
        shutil.rmtree(self.dir(spec.name), ignore_errors=True)
        last = self._last_run()
        if spec.name in last.get("done", []):
            last["done"].remove(spec.name)
            self._write_last_run(last)

    def set_enabled(self, name: str, enabled: bool) -> None:
        spec = self.get(name)
        self.add(spec.model_copy(update={"enabled": enabled}), overwrite=True)

    def set_llm(self, name: str, choice: str | None, base_url: str | None = None, key_env: str | None = None,
                command: str | None = None) -> AgentSpec:
        """Give an agent an LLM review (``provider:model``), turn it off (``off``) or follow the config (None)."""
        spec = self.get(name)
        new = AgentSpec(**{**spec.model_dump(), "llm": choice, "llm_base_url": base_url, "llm_key_env": key_env,
                           "llm_command": command})
        return self.add(new, overwrite=True)

    def decisions(self, name: str, symbol: str | None = None, limit: int = 50,
                  with_prompts: bool = False) -> list[dict[str, Any]]:
        """The agent's latest decisions, each with its LLM review (bull, bear, judge) when there was one."""
        from .journal import Journal

        jpath = self.dir(name) / "journal.db"
        if not jpath.is_file():
            return []
        j = Journal(jpath)
        try:
            rows = j.recent_decisions(limit, symbol)
            reviews: dict[tuple[int, str], dict[str, Any]] = {}
            for r in j.llm_reviews(limit * 4, symbol):
                reviews.setdefault((r["run_id"], r["symbol"]), r)
        finally:
            j.close()
        for row in rows:
            rv = reviews.get((row["run_id"], row["symbol"]))
            if rv and not with_prompts:
                rv = {k: v for k, v in rv.items() if k not in ("system_prompt", "prompt", "raw")}
            row["llm"] = rv
        return rows

    # per-agent wiring ----------------------------------------------------------------------
    def dir(self, name: str) -> Path:
        return self.root / name

    def account_file(self, name: str) -> Path:
        return self.dir(name) / f"{name}.json"

    def config_for(self, spec: AgentSpec) -> AppConfig:
        c = self.base_cfg.model_copy(deep=True)
        c.profile = spec.profile
        c.strategy = StrategyConfig(name=spec.strategy, params=dict(spec.params))
        if spec.preset is not None or spec.symbols:
            c.universe.preset = spec.preset
            c.universe.symbols = list(spec.symbols)
        c.broker = BrokerConfig(name="paper", options={"account_file": str(self.account_file(spec.name)),
                                                       "starting_cash": spec.cash})
        c.execution.mode = "paper"
        if spec.llm == "off":
            c.llm.enabled = False
        elif spec.llm:
            from .llm import llm_config

            c.llm = llm_config(spec.llm, base=c.llm, base_url=spec.llm_base_url, key_env=spec.llm_key_env,
                               command=spec.llm_command)
        return c

    def broker_for(self, spec: AgentSpec, quote_fn: Callable[[list[str]], dict[str, Quote]] | None = None):
        from .brokers.paper import PaperBroker

        return PaperBroker(account_file=self.account_file(spec.name), starting_cash=spec.cash, quote_fn=quote_fn)

    def engine_for(self, spec: AgentSpec, quote_fn: Callable[[list[str]], dict[str, Quote]] | None = None,
                   data: Any = None):
        from .engine import TradingEngine
        from .journal import Journal

        return TradingEngine(self.config_for(spec), broker=self.broker_for(spec, quote_fn), data=data,
                             journal=Journal(self.dir(spec.name) / "journal.db"),
                             state_root=self.dir(spec.name) / "state")

    # running ---------------------------------------------------------------------------------
    LOCK_STALE_SECONDS = 3 * 3600

    def _acquire(self) -> bool:
        """Take the run lock atomically, so a manual run and the daily job never trade at the same time."""
        self.root.mkdir(parents=True, exist_ok=True)
        lock = self.root / "run.lock"
        for _ in range(2):
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    stale = datetime.now().timestamp() - lock.stat().st_mtime > self.LOCK_STALE_SECONDS
                except FileNotFoundError:
                    continue
                if not stale:
                    return False
                lock.unlink(missing_ok=True)  # left behind by a crashed run
                continue
            with os.fdopen(fd, "w") as fh:
                fh.write(str(os.getpid()))
            return True
        return False

    def _release(self) -> None:
        (self.root / "run.lock").unlink(missing_ok=True)

    def run(self, names: Iterable[str] | None = None, mode: str = "paper",
            quote_fn: Callable[[list[str]], dict[str, Quote]] | None = None,
            data_factory: Callable[[AppConfig], Any] | None = None,
            progress: Callable[[str], None] | None = None, _locked: bool = False) -> list[AgentRun]:
        """Run one cycle for each agent (all enabled agents when ``names`` is empty).

        Raises RuntimeError when another run (for example the daily job) is still trading.
        """
        if mode not in ("paper", "dry-run"):
            raise ValueError("agents only trade the built-in paper account (mode paper or dry-run)")
        wanted = {n.lower() for n in names} if names else None
        specs = [a for a in self.load() if (a.name in wanted if wanted else a.enabled)]
        if wanted and len(specs) < len(wanted):
            missing = wanted - {a.name for a in specs}
            raise KeyError(f"no agent named {', '.join(sorted(missing))}")
        if not _locked and not self._acquire():
            raise RuntimeError("another agents run is in progress (maybe the daily job); try again in a few minutes")
        try:
            quotes = quote_fn if isinstance(quote_fn, QuoteCache) else QuoteCache(quote_fn)
            out = []
            for spec in specs:
                if progress:
                    progress(spec.name)
                out.append(self._run_one(spec, mode, quotes, data_factory))
            if mode == "paper":  # a day is traded once: the scheduled run skips agents that already traded today
                today = mc.now_ny().date().isoformat()
                last = self._last_run()
                if last.get("date") != today:
                    last = {"date": today, "done": [], "attempts": {}}
                last["done"] = sorted(set(last.get("done", [])) | {r.name for r in out if r.ok})
                self._write_last_run(last)
            return out
        finally:
            if not _locked:
                self._release()

    def _run_one(self, spec: AgentSpec, mode: str, quotes: QuoteCache,
                 data_factory: Callable[[AppConfig], Any] | None) -> AgentRun:
        try:
            data = data_factory(self.config_for(spec)) if data_factory else None
            eng = self.engine_for(spec, quotes, data)
            rep = eng.run_cycle(mode=mode)
        except Exception as exc:  # one broken agent must not stop the others
            log.exception("agent %s failed", spec.name)
            return AgentRun(spec.name, ok=False, errors=[f"{type(exc).__name__}: {exc}"])
        run = AgentRun(spec.name, ok=not rep.errors, regime=rep.market.regime.value if rep.market else None,
                       equity=rep.account.equity if rep.account else None, orders=len(rep.approved),
                       filled=sum(1 for r in rep.submitted if r.status.value == "FILLED"),
                       blocked=len(rep.blocked), signals=len(rep.signals), errors=list(rep.errors),
                       notes=list(rep.notes), report=rep)
        if mode == "paper" and run.ok:
            snap = self.snapshot(spec, quotes, run.regime)
            run.equity = snap["equity"]
        return run

    def snapshot(self, spec: AgentSpec, quote_fn: Callable | None = None, regime: str | None = None,
                 day: date | None = None) -> dict[str, Any]:
        """Mark the account to market and store today's row in equity.csv."""
        b = self.broker_for(spec, quote_fn)
        b.connect()
        acct, pos = b.account(), b.positions()
        u = self.config_for(spec).universe
        cash_like = sum(p.market_value for p in pos if p.symbol in {u.cash_vehicle, u.treasury_symbol})
        row = {"date": (day or mc.now_ny().date()).isoformat(), "equity": round(acct.equity, 2),
               "cash": round(acct.cash, 2), "invested": round(sum(p.market_value for p in pos) - cash_like, 2),
               "bonds": round(cash_like, 2), "positions": sum(1 for p in pos if p.symbol not in
                                                              {u.cash_vehicle, u.treasury_symbol}),
               "regime": regime or ""}
        path = self.dir(spec.name) / "equity.csv"
        df = self.equity_history(spec.name)
        df = df[df["date"] != row["date"]] if not df.empty else df
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True).sort_values("date")
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False)
        return row

    def equity_history(self, name: str) -> pd.DataFrame:
        path = self.dir(name) / "equity.csv"
        if not path.is_file():
            return pd.DataFrame(columns=["date", "equity", "cash", "invested", "bonds", "positions", "regime"])
        df = pd.read_csv(path, dtype={"date": str, "regime": str})
        df["regime"] = df["regime"].fillna("")
        return df

    def account_state(self, name: str) -> dict[str, Any]:
        """The raw paper account (cash, positions at cost, orders, fills) without fetching prices."""
        path = self.account_file(name)
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    # scoring --------------------------------------------------------------------------------
    def leaderboard(self, benchmark: pd.Series | None = None) -> list[dict[str, Any]]:
        """One row per agent, best total return first. ``benchmark`` is a daily close series (e.g. SPY)."""
        rows = []
        last = self._last_run()
        for spec in self.load():
            hist = self.equity_history(spec.name)
            eq = hist["equity"].astype(float) if not hist.empty else pd.Series(dtype=float)
            equity = float(eq.iloc[-1]) if len(eq) else spec.cash
            curve = [spec.cash, *eq.tolist()]
            peak = pd.Series(curve).cummax()
            mdd = float((pd.Series(curve) / peak - 1).min())
            prev = float(eq.iloc[-2]) if len(eq) >= 2 else spec.cash
            bench_ret = None
            if benchmark is not None and len(benchmark):
                b = benchmark.dropna()
                since = b[b.index >= pd.Timestamp(spec.created)]
                if len(since):
                    base = b[b.index < pd.Timestamp(spec.created)]
                    start_px = float(base.iloc[-1]) if len(base) else float(since.iloc[0])
                    bench_ret = float(since.iloc[-1]) / start_px - 1
            last_row = hist.iloc[-1].to_dict() if not hist.empty else {}
            rows.append({
                "name": spec.name, "strategy": spec.strategy, "profile": spec.profile,
                "universe": spec.universe_label, "enabled": spec.enabled, "note": spec.note,
                "llm": (spec.llm_label if spec.llm and spec.llm != "off"
                        else (self.base_cfg.llm.model if spec.llm is None and self.base_cfg.llm.enabled else None)),
                "created": spec.created, "start_cash": spec.cash, "equity": equity,
                "return": equity / spec.cash - 1, "day": equity / prev - 1 if len(eq) else 0.0,
                "max_drawdown": mdd, "days": len(eq), "curve": curve,
                "invested": float(last_row.get("invested", 0.0)) / equity if equity else 0.0,
                "cash_like": (float(last_row.get("cash", spec.cash)) + float(last_row.get("bonds", 0.0))) / equity
                if equity else 1.0,
                "positions": int(last_row.get("positions", 0) or 0),
                "last_run": str(last_row.get("date", "")) or None,
                "ran_today": str(last_row.get("date", "")) == mc.now_ny().date().isoformat()
                or (spec.name in last.get("done", []) and last.get("date") == mc.now_ny().date().isoformat()),
                "benchmark_return": bench_ret,
            })
        rows.sort(key=lambda r: (-r["return"], r["name"]))
        for i, r in enumerate(rows, 1):
            r["rank"] = i
        return rows

    def benchmark(self, symbol: str | None = None) -> pd.Series | None:
        """Daily closes of the benchmark (SPY by default) since the oldest agent was created."""
        from .data.prices import make_price_provider
        from .utils import DiskCache

        agents = self.load()
        if not agents:
            return None
        c = self.base_cfg
        sym = symbol or c.universe.benchmark
        start = min(date.fromisoformat(a.created) for a in agents) - timedelta(days=10)
        try:
            prices = make_price_provider(c.data.price_source, DiskCache(c.data.cache_path), c.data.csv_dir)
            df = prices.history([sym], start, mc.now_ny().date()).get(sym)
        except Exception as exc:  # offline: the leaderboard still works without the benchmark
            log.info("benchmark unavailable: %s", exc)
            return None
        return df["close"] if df is not None and len(df) else None

    # scheduled runs ---------------------------------------------------------------------------
    @property
    def log_file(self) -> Path:
        return self.root / "logs" / "agents.log"

    def log(self, text: str) -> None:
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        stamp = mc.now_ny().strftime("%Y-%m-%d %H:%M:%S %Z")
        with open(self.log_file, "a", encoding="utf-8") as fh:
            for line in text.splitlines() or [""]:
                fh.write(f"{stamp}  {line}\n")

    def recent_log(self, lines: int = 40) -> list[str]:
        if not self.log_file.is_file():
            return []
        return self.log_file.read_text(encoding="utf-8").splitlines()[-lines:]

    def _last_run(self) -> dict[str, Any]:
        p = self.root / "last_run.json"
        if not p.is_file():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write_last_run(self, data: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "last_run.json").write_text(json.dumps(data, indent=2), encoding="utf-8")

    def scheduled_run(self, now: datetime | None = None, max_attempts: int = 3,
                      quote_fn: Callable[[list[str]], dict[str, Quote]] | None = None,
                      data_factory: Callable[[AppConfig], Any] | None = None) -> tuple[str, list[AgentRun]]:
        """What the scheduler calls every 15 minutes: trade each enabled agent once per trading day."""
        now = now or mc.now_ny()
        ok, why = due(now, self.base_cfg.execution.run_time)
        if not ok:
            return why, []
        today = now.astimezone(mc.NY).date().isoformat()
        last = self._last_run()
        if last.get("date") != today:
            last = {"date": today, "done": [], "attempts": {}}
        pending = [a.name for a in self.load() if a.enabled and a.name not in last["done"]
                   and last["attempts"].get(a.name, 0) < max_attempts]
        if not pending:
            return "every agent already ran today", []
        if not self._acquire():
            return "another run is in progress", []
        try:
            runs = self.run(pending, mode="paper", quote_fn=quote_fn, data_factory=data_factory, _locked=True)
        finally:
            self._release()
        last = {**self._last_run(), "attempts": last["attempts"]}
        for r in runs:
            last["attempts"][r.name] = last["attempts"].get(r.name, 0) + 1
        last["finished"] = datetime.now().astimezone().isoformat(timespec="seconds")
        self._write_last_run(last)
        return "ran", runs

    def schedule_status(self) -> dict[str, Any]:
        last = self._last_run()
        now = mc.now_ny()
        ok, why = due(now, self.base_cfg.execution.run_time)
        d = now.date()
        if ok or (mc.is_trading_day(d) and why.startswith("waiting") and last.get("date") != d.isoformat()):
            nxt = d
        else:
            nxt = mc.next_trading_day(d)
        return {"last_run": last, "now": now.isoformat(timespec="minutes"), "state": why,
                "next_day": nxt.isoformat(), "run_time": self.base_cfg.execution.run_time}
