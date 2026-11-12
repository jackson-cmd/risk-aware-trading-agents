"""Load a strategy by name, by ``module:Class`` or by ``path/to/file.py:Class``."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Any

from .base import Strategy
from .examples import BUILTIN_EXAMPLES
from .options_strategies import OPTION_STRATEGIES
from .risk_aware import RiskAwareStrategy

BUILTIN: dict[str, type[Strategy]] = {"risk_aware": RiskAwareStrategy, **BUILTIN_EXAMPLES, **OPTION_STRATEGIES}


def list_strategies() -> dict[str, str]:
    return {name: cls.description for name, cls in BUILTIN.items()}


def _pick_class(module: Any, class_name: str | None, origin: str) -> type[Strategy]:
    if class_name:
        cls = getattr(module, class_name, None)
        if cls is None:
            raise ValueError(f"{origin}: no class named {class_name}")
        return cls
    found = [obj for _, obj in inspect.getmembers(module, inspect.isclass)
             if issubclass(obj, Strategy) and obj is not Strategy and obj.__module__ == module.__name__]
    if len(found) != 1:
        raise ValueError(f"{origin}: expected exactly one Strategy subclass, found {len(found)}; "
                         f"use {origin}:ClassName")
    return found[0]


def split_spec(spec: str) -> tuple[str, str]:
    """Split "file.py:Class" or "pkg.module:Class" without breaking Windows drive letters ("C:\\x.py")."""
    head, sep, tail = spec.rpartition(":")
    if sep and tail.isidentifier() and not (len(head) == 1 and head.isalpha()):
        return head, tail
    return spec, ""


def load_strategy(spec: str, params: dict[str, Any] | None = None) -> Strategy:
    params = params or {}
    if spec in BUILTIN:
        return BUILTIN[spec](**params)
    target, class_name = split_spec(spec)
    path = Path(target).expanduser()
    if target.endswith(".py") or path.is_file():
        if not path.is_file():
            raise FileNotFoundError(f"strategy file not found: {path}")
        mod_name = f"riskaware_user_{path.stem}"
        module_spec = importlib.util.spec_from_file_location(mod_name, path)
        if module_spec is None or module_spec.loader is None:
            raise ImportError(f"cannot import {path}")
        module = importlib.util.module_from_spec(module_spec)
        sys.modules[mod_name] = module
        module_spec.loader.exec_module(module)
        cls = _pick_class(module, class_name or None, str(path))
    else:
        try:
            module = importlib.import_module(target)
        except ImportError as exc:
            raise ValueError(f"unknown strategy '{spec}'. Built-in: {', '.join(BUILTIN)}; "
                             f"or pass path/to/file.py[:Class] or package.module:Class") from exc
        cls = _pick_class(module, class_name or None, target)
    if not (inspect.isclass(cls) and issubclass(cls, Strategy)):
        raise TypeError(f"{spec} is not a riskaware.strategy.Strategy subclass")
    return cls(**params)


STRATEGY_TEMPLATE = '''"""My strategy for Risk-Aware Trading Agents.

Backtest it:      riskaware backtest --strategy {path}
Compare to ours:  riskaware backtest --strategy {path} --compare risk_aware,buy_and_hold
Paper trade it:   riskaware run --strategy {path}

ctx.row(symbol) gives the indicators at today's close: open high low close volume,
ema20 ema50 ema100 ema200, rsi2 rsi14, atr14 atr_pct, z20, bb_lower bb_upper, macd macd_hist,
adx14, don55_high, rel_vol, dd252 (drawdown from the 52-week high), ema200_slope, mom126, rs126.
ctx.market has the regime (RISK_ON / NEUTRAL / UNCERTAIN / RISK_OFF), VIX, fear & greed and events.
The risk manager sizes every order and enforces the cash floor and caps, so a signal only says
what you would like to do and how sure you are (strength 0..1).
"""

from riskaware.strategy import Action, Signal, Strategy


class {cls}(Strategy):
    name = "{name}"
    description = "Buy oversold dips in uptrends, sell into strength."
    default_params = {{"rsi_entry": 30, "take_profit": 0.20}}

    def on_bar(self, ctx):
        signals = []
        for sym in ctx.universe:
            row = ctx.row(sym)
            if row is None or row.get("ema200") != row.get("ema200"):  # skip NaN warm-up rows
                continue
            pos = ctx.position(sym)
            if pos is None:
                if row["close"] > row["ema200"] and row["rsi14"] < self.params["rsi_entry"]:
                    signals.append(Signal(sym, Action.BUY, strength=0.7, reason="oversold in an uptrend"))
            elif pos.gain >= self.params["take_profit"]:
                signals.append(Signal(sym, Action.SELL, strength=0.8, fraction=0.5, reason="take profit"))
        return signals
'''
