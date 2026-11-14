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

``mode`` picks the account (docs/short-margin.md); the above is ``long_only``, the default. The other two allow
short positions, and there a target weight is signed (-0.1 = short 10% of equity):

* ``long_short``: no leverage. Long plus short positions at most 100% of equity, each short at most 20%. The money
  from a short sale stays in the account as collateral: it earns the T-bill rate like cash but cannot pay for
  purchases. Shorts pay a borrow fee of 0.5% a year, charged every trading day.
* ``margin``: Reg T. Initial margin 50%, so long plus short positions reach at most 200% of equity (each short at
  most 40%); maintenance margin 25% of the longs and 30% of the shorts. Borrowed cash pays the BIL return plus 1.5%
  a year and shorts the same borrow fee, both every trading day.

In both, a SELL with a target below the current weight sells down to it (closing a long and opening a short when it
crosses zero) and a BUY with a target above it buys up to it; orders that lower exposure go first, then those that
add to it, strongest first, as far as the gross limit allows. When the maintenance requirement exceeds equity at a
close, every position is cut by the same share at the next open and the day's signals are refused (a margin call);
at zero equity everything is closed and trading stops. The backtester books the fees and interest.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from typing import Any

from ..risk import PlannedOrder, RiskDecision
from ..types import Action, Side, Signal

MODES = ("long_only", "long_short", "margin")
TRADING_DAYS = 252


@dataclass(frozen=True)
class MarginRules:
    """What a ``long_short`` or ``margin`` account may hold and what it pays. Rates are a year; the backtester
    charges them every trading day (1/252 of the rate)."""

    mode: str = "long_short"
    max_gross: float = 1.0  # long plus short positions, as a share of equity, that new orders may reach
    max_short: float = 0.2  # largest short position, as a share of equity
    borrow_fee: float = 0.005  # on the value of the shorts
    margin_spread: float = 0.015  # on borrowed cash, over the T-bill (BIL) return that borrowed cash also pays
    maint_long: float = 0.25  # maintenance margin: equity must cover this share of the longs' value ...
    maint_short: float = 0.30  # ... plus this share of the shorts' value
    cushion: float = 0.10  # a margin call cuts every position until requirement x (1 + cushion) fits in equity

    @classmethod
    def for_mode(cls, mode: str = "long_only", **overrides: float) -> MarginRules | None:
        """The rules of a mode, with any field overridden; None for ``long_only``."""
        if mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}, not {mode!r}")
        if mode == "long_only":
            if overrides:
                raise ValueError("mode_params apply to the long_short and margin modes only")
            return None
        known = {f.name for f in fields(cls)} - {"mode"}
        unknown = sorted(set(overrides) - known)
        if unknown:
            raise ValueError(f"unknown mode_params {unknown}; known: {', '.join(sorted(known))}")
        params: dict[str, float] = {"max_gross": 1.0 if mode == "long_short" else 2.0}
        params.update({k: float(v) for k, v in overrides.items()})
        params.setdefault("max_short", 0.2 * params["max_gross"])
        rules = cls(mode=mode, **params)
        rules.check()
        return rules

    def check(self) -> None:
        if not 0 < self.max_gross <= (1.0 if self.mode == "long_short" else 10.0):
            raise ValueError("max_gross must be above 0 and at most 1 in long_short (no leverage)")
        if not 0 <= self.max_short <= self.max_gross:
            raise ValueError("max_short must be between 0 and max_gross")
        if min(self.borrow_fee, self.margin_spread, self.cushion) < 0:
            raise ValueError("borrow_fee, margin_spread and cushion cannot be negative")
        if not (0 <= self.maint_long < 1 and 0 <= self.maint_short < 1):
            raise ValueError("maintenance margins are shares of the positions' value, from 0 to below 1")
        if max(self.maint_long, self.maint_short) * self.max_gross >= 1:
            raise ValueError("at the gross limit the maintenance requirement would exceed equity: a margin call "
                             "on the first day")

    @property
    def initial_margin(self) -> float:
        """Equity that new positions need, as a share of their value (50% under Reg T)."""
        return 1.0 / self.max_gross

    def requirement(self, long_value: float, short_value: float) -> float:
        """The maintenance requirement in dollars: a margin call comes when it exceeds equity."""
        return self.maint_long * long_value + self.maint_short * short_value

    def keep_after_call(self, equity: float, requirement: float) -> float:
        """The share of every position a margin call keeps: requirement x (1 + cushion) fits in equity again."""
        if equity <= 0:
            return 0.0
        if requirement <= 0:
            return 1.0
        return min(1.0, equity / (requirement * (1.0 + self.cushion)))

    def daily_costs(self, cash: float, short_value: float) -> tuple[float, float, float]:
        """One trading day's (borrow fee, margin spread, loan). The loan is the cash an account has borrowed:
        short-sale proceeds are collateral, so any cash below the value of the shorts is borrowed. The T-bill part
        of the loan's interest comes with the daily cash return (a negative balance shrinks by it)."""
        loan = max(0.0, short_value - cash)
        return (short_value * self.borrow_fee / TRADING_DAYS, loan * self.margin_spread / TRADING_DAYS, loan)

    def account(self, portfolio: Any, risk_free: float | None = None) -> dict[str, Any]:
        """The account as a share of equity, for an observation: exposures, buying power, margin and the rates
        (``risk_free`` is the T-bill rate; without it the margin interest is given as the spread over it)."""
        equity = float(portfolio.equity)
        if equity <= 0:
            return {"gross_exposure": 0.0, "net_exposure": 0.0, "buying_power": 0.0, "margin_used": 0.0,
                    "maintenance_requirement": 0.0}
        long_v = sum(p.qty * p.price for p in portfolio.positions.values() if p.qty > 0)
        short_v = -sum(p.qty * p.price for p in portfolio.positions.values() if p.qty < 0)
        gross = (long_v + short_v) / equity
        out = {"mode": self.mode, "gross_exposure": gross, "net_exposure": (long_v - short_v) / equity,
               "max_gross_exposure": self.max_gross, "buying_power": max(0.0, self.max_gross - gross),
               "max_short_weight": self.max_short, "margin_used": gross * self.initial_margin,
               "maintenance_requirement": self.requirement(long_v, short_v) / equity,
               "borrow_fee_annual": self.borrow_fee}
        # the T-bill level would date a blind run, so without it only the spread is given
        out["margin_interest_annual"] = (round(float(risk_free) + self.margin_spread, 3) if risk_free is not None
                                         else f"T-bill rate + {self.margin_spread:.1%}")
        return out

    def describe(self) -> dict[str, Any]:
        return {"mode": self.mode, "max_gross_exposure": self.max_gross, "max_short_weight": round(self.max_short, 6),
                "initial_margin": round(self.initial_margin, 6),
                "maintenance_margin": {"long": self.maint_long, "short": self.maint_short},
                "borrow_fee_annual": self.borrow_fee,
                "margin_interest": f"BIL return + {self.margin_spread * 100:g}% a year on borrowed cash",
                "short_proceeds": "held as collateral: they earn the T-bill rate but cannot pay for purchases",
                "margin_call": f"when the maintenance requirement exceeds equity at a close, every position is cut by "
                               f"the same share at the next open until the requirement plus {self.cushion:.0%} fits; "
                               "that day's signals are refused; at zero equity everything is closed and trading stops"}


def margin_rules(mode: str = "long_only", params: dict[str, float] | None = None) -> MarginRules | None:
    """The rules of an execution mode (None for long_only), with ``params`` overriding fields."""
    return MarginRules.for_mode(mode, **(params or {}))


@dataclass
class PlainPlanner:
    max_positions: int = 10
    position_size: float | None = None  # share of equity for a new position; default 1 / max_positions
    min_trade: float = 0.002  # weight changes smaller than this share of equity are not traded
    mode: str = "long_only"  # long_only, long_short or margin (see the module docstring)
    mode_params: dict[str, float] | None = None  # MarginRules fields to override, e.g. {"max_short": 0.1}

    def __post_init__(self) -> None:
        self.margin = margin_rules(self.mode, self.mode_params)  # read by the backtester; None when long only

    @property
    def size(self) -> float:
        return float(self.position_size) if self.position_size else 1.0 / max(1, self.max_positions)

    def describe(self) -> dict[str, Any]:
        if self.margin is not None:
            return {"execution": "plain", "max_positions": self.max_positions, "position_size": round(self.size, 6),
                    "min_trade": self.min_trade, "orders": "market orders at the next open, orders that lower "
                    "exposure first", "long_only": False, "leverage": self.margin.max_gross > 1,
                    **self.margin.describe()}
        return {"execution": "plain", "mode": "long_only", "max_positions": self.max_positions,
                "position_size": round(self.size, 6),
                "min_trade": self.min_trade, "orders": "market orders at the next open, sells before buys",
                "long_only": True, "leverage": False}

    def plan(self, signals: list[Signal], ctx: Any, prices: dict[str, float]) -> RiskDecision:
        if self.margin is not None:
            return self._plan_signed(signals, ctx, prices)
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

    # ------------------------------------------------------------------ long_short and margin
    def _plan_signed(self, signals: list[Signal], ctx: Any, prices: dict[str, float]) -> RiskDecision:
        """Signed target weights within the gross limit; a margin call or zero equity replaces the day's orders."""
        m = self.margin
        decision = RiskDecision()
        pf = ctx.portfolio
        equity = float(pf.equity)
        held = {s: p for s, p in pf.positions.items() if p.qty != 0}
        long_v = sum(p.qty * p.price for p in held.values() if p.qty > 0)
        short_v = -sum(p.qty * p.price for p in held.values() if p.qty < 0)
        need = m.requirement(long_v, short_v)
        if equity <= 0 or need > equity:
            return self._forced(signals, held, prices, equity, need)
        moves: list[tuple[Signal, float, Any, float, float]] = []  # signal, price, position, weight now, target
        seen: set[str] = set()
        for sig in signals:
            px = prices.get(sig.symbol)
            if sig.is_option:
                decision.rejected.append((sig, "options are not simulated in plain execution"))
                continue
            if px is None or not math.isfinite(px) or px <= 0:
                decision.rejected.append((sig, "no price"))
                continue
            if sig.symbol in seen:
                decision.rejected.append((sig, "one order per stock and day"))
                continue
            pos = held.get(sig.symbol)
            w0 = float(pos.weight) if pos is not None else 0.0
            if sig.action == Action.SELL:
                if sig.target_weight is not None:
                    t = float(sig.target_weight)
                elif w0 > 0:
                    frac = 1.0 if sig.fraction is None else min(max(float(sig.fraction), 0.0), 1.0)
                    t = w0 * (1.0 - frac)
                else:  # a short opens only with a negative target_weight
                    decision.rejected.append((sig, "nothing to sell" if pos is None else "a short is reduced by buying"))
                    continue
                capped = t < -m.max_short
                t = max(t, -m.max_short)
                if t >= w0:
                    decision.rejected.append((sig, f"short limit ({m.max_short:g})" if capped else "already at target"))
                    continue
            elif sig.action == Action.BUY:
                if sig.target_weight is not None:
                    t = float(sig.target_weight)
                elif w0 < 0:
                    t = 0.0  # a buy without a target covers the whole short
                elif pos is None:
                    t = self.size
                else:
                    decision.rejected.append((sig, "already held"))
                    continue
                t = min(t, m.max_gross)
                if t <= w0:
                    decision.rejected.append((sig, "already at target"))
                    continue
            else:
                continue
            seen.add(sig.symbol)
            moves.append((sig, float(px), pos, w0, t))

        # first the part of each move that lowers exposure (closing or trimming a long or a short) ...
        lower: list[PlannedOrder] = []
        adds: list[tuple[Signal, float, bool, float, float, bool]] = []  # signal, price, new, from, to, lowered too
        still_open = set(held)
        gross_v = long_v + short_v
        for sig, px, pos, w0, t in moves:
            sym = sig.symbol
            crosses = w0 * t < 0 or t == 0
            lowered = False
            if pos is not None and (crosses or abs(t) < abs(w0)):
                n = abs(pos.qty)
                if crosses:
                    qty = n
                elif abs(w0 - t) < self.min_trade:
                    decision.rejected.append((sig, "already at target"))
                    continue
                else:
                    qty = math.floor(n * (1.0 - t / w0))
                if qty <= 0:
                    decision.rejected.append((sig, "trade too small"))
                    continue
                is_long = pos.qty > 0
                tag = ("exit" if crosses else "trim") if is_long else ("cover" if crosses else "cover_trim")
                lower.append(PlannedOrder(sym, Side.SELL if is_long else Side.BUY, qty, px, None, sig.reason or tag, tag,
                                          strength=float(sig.strength)))
                gross_v -= qty * px
                lowered = True
                if crosses:
                    still_open.discard(sym)
            if t != 0 and (pos is None or crosses or abs(t) > abs(w0)):
                new = pos is None or crosses
                adds.append((sig, px, new, 0.0 if new else w0, t, lowered))

        # ... then the part that adds to it, strongest first, within the gross limit and max_positions
        room = m.max_gross * equity - gross_v
        open_count = len(still_open)
        raised: list[PlannedOrder] = []
        for sig, px, new, start, t, lowered in sorted(adds, key=lambda a: -float(a[0].strength or 0.0)):
            if abs(t - start) < self.min_trade:
                if not lowered:
                    decision.rejected.append((sig, "already at target"))
                continue
            if new and open_count >= self.max_positions:
                decision.rejected.append((sig, f"max positions ({self.max_positions})"))
                continue
            value = min(abs(t - start) * equity, room)
            qty = math.floor(value / px) if value > 0 else 0
            if qty <= 0:
                decision.rejected.append((sig, f"gross exposure limit ({m.max_gross:g})"))
                continue
            short = t < 0
            tag = ("short" if new else "short_add") if short else ("entry" if new else "add")
            raised.append(PlannedOrder(sig.symbol, Side.SELL if short else Side.BUY, qty, px, None, sig.reason or tag,
                                       tag, strength=float(sig.strength), target_weight=t, setups=list(sig.setups)))
            room -= qty * px
            if new:
                open_count += 1
        decision.orders = lower + raised
        decision.budget = {"equity": equity, "gross_after": m.max_gross - room / equity,
                           "buying_power_after": room / equity}
        return decision

    def _forced(self, signals: list[Signal], held: dict[str, Any], prices: dict[str, float], equity: float,
                need: float) -> RiskDecision:
        """A margin call (cut every position by the same share) or, at zero equity, closing everything."""
        decision = RiskDecision()
        keep = self.margin.keep_after_call(equity, need)
        if equity > 0:
            tag = "margin_call"
            why = (f"margin call: maintenance requirement {need:,.0f} above equity {equity:,.0f}, every position cut "
                   f"to {keep:.0%}")
        else:
            tag, why = "wiped_out", "equity at or below zero: everything closed, trading stops"
        for sym, p in sorted(held.items()):
            n = abs(p.qty)
            qty = n - math.floor(n * keep)
            if qty <= 0:
                continue
            px = prices.get(sym) or p.price
            decision.orders.append(PlannedOrder(sym, Side.SELL if p.qty > 0 else Side.BUY, qty, float(px), None, why,
                                                tag))
        decision.rejected.extend((sig, tag.replace("_", " ")) for sig in signals)
        decision.notes.append(why)
        decision.budget = {"equity": equity, "maintenance_requirement": need, "kept": keep}
        return decision
