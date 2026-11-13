"""Plain execution: signals become market orders for the next open, sized equally, with no risk rules on top.

The package's risk manager adds its own rules to every strategy: a cash floor, regime limits, buying in tranches,
sector caps. Those rules are part of the risk-aware strategy. An experiment that measures what a strategy's own
rules do turns them off with this planner, which only does what the strategy says:

* a SELL sells ``fraction`` of the position (all of it by default), or down to ``target_weight``;
* a BUY opens a position of ``target_weight`` of equity, or ``position_size`` (1 / max_positions by default), or
  tops a position up to ``target_weight`` when one is given; strongest signals first;
* at most ``max_positions`` stocks at a time, long only, no leverage: a buy never spends more than the cash left
  after the day's sells.

Orders fill at the next day's open with the backtest's slippage (see ``riskaware.backtest.engine``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from ..risk import PlannedOrder, RiskDecision
from ..types import Action, Side, Signal


@dataclass
class PlainPlanner:
    max_positions: int = 10
    position_size: float | None = None  # share of equity for a new position; default 1 / max_positions
    min_trade: float = 0.002  # weight changes smaller than this share of equity are not traded

    @property
    def size(self) -> float:
        return float(self.position_size) if self.position_size else 1.0 / max(1, self.max_positions)

    def describe(self) -> dict[str, Any]:
        return {"execution": "plain", "max_positions": self.max_positions, "position_size": round(self.size, 6),
                "min_trade": self.min_trade, "orders": "market orders at the next open, sells before buys",
                "long_only": True, "leverage": False}

    def plan(self, signals: list[Signal], ctx: Any, prices: dict[str, float]) -> RiskDecision:
        decision = RiskDecision()
        pf = ctx.portfolio
        equity = float(pf.equity)
        if equity <= 0:
            decision.rejected.extend((s, "no equity") for s in signals)
            return decision
        held = dict(pf.positions)
        cash = float(pf.cash) - float(pf.option_collateral)
        sells: list[PlannedOrder] = []
        buys: list[Signal] = []
        sold_out: set[str] = set()
        for sig in signals:
            px = prices.get(sig.symbol)
            if sig.is_option:
                decision.rejected.append((sig, "options are not simulated in plain execution"))
                continue
            if px is None or not math.isfinite(px) or px <= 0:
                decision.rejected.append((sig, "no price"))
                continue
            if sig.action == Action.SELL:
                pos = held.get(sig.symbol)
                if pos is None or pos.qty <= 0 or sig.symbol in sold_out:
                    decision.rejected.append((sig, "nothing to sell"))
                    continue
                frac = 1.0 if sig.fraction is None else min(max(float(sig.fraction), 0.0), 1.0)
                if sig.target_weight is not None and pos.weight > 0:
                    frac = min(max(1.0 - float(sig.target_weight) / pos.weight, 0.0), 1.0)
                qty = pos.qty if frac >= 0.999 else math.floor(pos.qty * frac)
                if qty <= 0:
                    decision.rejected.append((sig, "sell too small"))
                    continue
                if qty >= pos.qty:
                    sold_out.add(sig.symbol)
                sells.append(PlannedOrder(sig.symbol, Side.SELL, qty, px, None, sig.reason or "sell",
                                          "exit" if qty >= pos.qty else "trim", strength=float(sig.strength)))
                cash += qty * px
            elif sig.action == Action.BUY:
                buys.append(sig)
        open_count = sum(1 for s, p in held.items() if p.qty > 0 and s not in sold_out)
        planned: set[str] = set()
        orders_buy: list[PlannedOrder] = []
        for sig in sorted(buys, key=lambda s: -float(s.strength or 0.0)):
            sym, px = sig.symbol, prices[sig.symbol]
            if sym in planned:
                continue
            pos = held.get(sym) if sym not in sold_out else None
            target = float(sig.target_weight) if sig.target_weight is not None else self.size
            target = min(max(target, 0.0), 1.0)
            cur = pos.weight if pos is not None else 0.0
            if pos is None and open_count >= self.max_positions:
                decision.rejected.append((sig, f"max positions ({self.max_positions})"))
                continue
            if pos is not None and sig.target_weight is None:
                decision.rejected.append((sig, "already held"))
                continue
            add = target - cur
            if add < self.min_trade:
                decision.rejected.append((sig, "already at target"))
                continue
            value = min(add * equity, cash)
            qty = math.floor(value / px)
            if qty <= 0:
                decision.rejected.append((sig, "not enough cash"))
                continue
            orders_buy.append(PlannedOrder(sym, Side.BUY, qty, px, None, sig.reason or "buy",
                                           "entry" if pos is None else "add", strength=float(sig.strength),
                                           target_weight=target, setups=list(sig.setups)))
            planned.add(sym)
            cash -= qty * px
            if pos is None:
                open_count += 1
        decision.orders = sells + orders_buy
        decision.budget = {"equity": equity, "cash_after": cash}
        return decision
