"""The risk manager: the only place where signals become orders.

Hard rules, in order:
1. Sells: discretionary sells only in RISK_ON / NEUTRAL; emergency exits in any
   regime when the profile allows them. Overweight positions are trimmed back to
   the cap in RISK_ON / NEUTRAL.
2. Buys, strongest first: minimum conviction, drawdown pause, earnings blackout,
   daily limits, tranche rules, position / sector / speculative caps, maximum
   positions, the stock budget and the cash floor (cash + T-bill ETF).
3. Treasury sleeve rebalance and T-bill cash management (no regime limits; they
   are not risk-asset trades).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .config import ExecutionConfig, OptionsConfig, RiskProfile, UniverseConfig
from .sizing import shares_for, target_weight, tranche_weight
from .strategy.base import PortfolioView, StrategyContext
from .types import Action, OptionContract, Regime, Side, Signal
from .universe import sector_of


@dataclass
class PlannedOrder:
    symbol: str
    side: Side
    qty: float
    price: float
    limit_price: float | None
    reason: str
    tag: str
    strength: float = 0.0
    target_weight: float | None = None
    tranche: int | None = None
    setups: list[str] = field(default_factory=list)
    option: OptionContract | None = None  # set for orders that open an option position
    option_key: str | None = None  # set for orders that close an option position
    iv: float | None = None  # modelled implied volatility used to price an option order

    @property
    def is_option(self) -> bool:
        return self.option is not None or self.option_key is not None

    @property
    def value(self) -> float:
        mult = 100 if self.is_option else 1
        return self.qty * mult * (self.limit_price or self.price)


@dataclass
class RiskDecision:
    orders: list[PlannedOrder] = field(default_factory=list)
    rejected: list[tuple[Signal, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    budget: dict[str, float] = field(default_factory=dict)

    def buys(self) -> list[PlannedOrder]:
        return [o for o in self.orders if o.side == Side.BUY]

    def sells(self) -> list[PlannedOrder]:
        return [o for o in self.orders if o.side == Side.SELL]


class RiskManager:
    def __init__(self, profile: RiskProfile, universe: UniverseConfig | None = None,
                 execution: ExecutionConfig | None = None, manage_cash: bool = True,
                 regime_rules: bool = True, options: OptionsConfig | None = None):
        self.p = profile
        self.u = universe or UniverseConfig()
        self.x = execution or ExecutionConfig()
        self.opt = options or OptionsConfig()
        self.manage_cash = manage_cash
        self.regime_rules = regime_rules

    # ---------------------------------------------------------------- helpers
    def _limit(self, side: Side, price: float) -> float | None:
        if self.x.order_type == "MARKET":
            return None
        off = self.x.limit_offset
        px = price * (1 + off) if side == Side.BUY else price * (1 - off)
        return round(px, 2) if px >= 1 else round(px, 4)

    def is_speculative(self, sym: str, ctx: StrategyContext) -> bool:
        if sym in self.u.speculative_symbols:
            return True
        if ctx.is_speculative(sym):
            return True
        row = ctx.row(sym)
        if row is None:
            return False
        vol = row.get("vol60")
        close = row.get("close")
        if not math.isnan(close) and close < self.p.speculative_min_price:
            return True
        return not math.isnan(vol) and vol >= self.p.speculative_vol

    def _sector(self, sym: str) -> str:
        return sector_of(sym, self.u.sectors)

    # ---------------------------------------------------------------- main
    def plan(self, signals: list[Signal], ctx: StrategyContext, prices: dict[str, float]) -> RiskDecision:
        p, pf = self.p, ctx.portfolio
        regime = ctx.market.regime
        decision = RiskDecision()
        equity = pf.equity
        if equity <= 0:
            decision.notes.append("account equity is zero")
            return decision

        cash = pf.cash  # raw cash
        cash_eq = pf.cash_equivalents
        collateral = pf.option_collateral  # cash promised to written puts: not free, not part of the floor
        floor_value = p.cash_floor * equity
        option_signals = [s for s in signals if s.is_option]
        signals = [s for s in signals if not s.is_option]
        weights = {s: pos.value / equity for s, pos in pf.positions.items()}
        sector_w: dict[str, float] = {}
        for s, w in weights.items():
            sector_w[self._sector(s)] = sector_w.get(self._sector(s), 0.0) + w
        spec_names = {s for s in pf.positions if self.is_speculative(s, ctx)}
        spec_w = sum(weights[s] for s in spec_names)
        stock_w = sum(weights.values()) + collateral / equity  # written puts can turn into stock
        n_positions = len(pf.positions)
        max_stock = p.max_equity_exposure
        decision.budget = {"equity": equity, "cash_floor_value": floor_value, "stock_exposure": stock_w,
                           "max_stock_exposure": max_stock, "cash_ratio": pf.cash_ratio}

        sells = [s for s in signals if s.action == Action.SELL]
        buys = sorted((s for s in signals if s.action == Action.BUY), key=lambda s: -s.strength)

        # 1. sells ------------------------------------------------------------
        sold_qty: dict[str, float] = {}
        for sig in sells:
            pos = pf.positions.get(sig.symbol)
            if pos is None or pos.qty <= 0:
                decision.rejected.append((sig, "no position to sell"))
                continue
            allowed = (not self.regime_rules) or regime.allows_discretionary_sells or (
                sig.emergency and p.allow_emergency_exits)
            if not allowed:
                decision.rejected.append((sig, f"{regime.value}: hold, no selling (only emergency exits)"))
                continue
            frac = 1.0 if sig.fraction is None else max(0.0, min(1.0, sig.fraction))
            if frac >= 0.999:
                qty = pos.qty
            elif self.x.allow_fractional:
                qty = round(pos.qty * frac, 4)
            else:
                qty = max(1.0, math.floor(pos.qty * frac)) if pos.qty >= 1 else pos.qty
            qty = min(qty, pos.qty - pf.covered_shares(sig.symbol))
            if qty <= 0:
                why = "shares are covered by written calls" if pf.covered_shares(sig.symbol) else "position too small to trim"
                decision.rejected.append((sig, why))
                continue
            price = prices.get(sig.symbol, pos.price)
            tag = sig.details.get("tag") or (sig.setups[0] if sig.setups else "exit")
            decision.orders.append(PlannedOrder(sig.symbol, Side.SELL, qty, price, self._limit(Side.SELL, price),
                                                sig.reason, tag, sig.strength, setups=sig.setups))
            sold_qty[sig.symbol] = qty
            proceeds = qty * price
            cash += proceeds
            delta_w = proceeds / equity
            weights[sig.symbol] = weights.get(sig.symbol, 0.0) - delta_w
            sector_w[self._sector(sig.symbol)] -= delta_w
            stock_w -= delta_w
            if sig.symbol in spec_names:
                spec_w -= delta_w
            if qty >= pos.qty:
                n_positions -= 1

        # overweight trims (discretionary)
        if (not self.regime_rules) or regime.allows_discretionary_sells:
            for sym, pos in pf.positions.items():
                if sym in sold_qty:
                    continue
                w = weights.get(sym, 0.0)
                if w > p.max_position * 1.5:
                    excess = (w - p.max_position) * equity
                    qty = min(shares_for(excess, pos.price, self.x.allow_fractional),
                              pos.qty - pf.covered_shares(sym))
                    if qty > 0:
                        price = prices.get(sym, pos.price)
                        decision.orders.append(PlannedOrder(sym, Side.SELL, qty, price, self._limit(Side.SELL, price),
                                                            f"trim overweight position ({w:.1%} > {p.max_position:.0%} cap)",
                                                            "cap_trim"))
                        cash += qty * price
                        dw = qty * price / equity
                        weights[sym] -= dw
                        sector_w[self._sector(sym)] -= dw
                        stock_w -= dw

        # restore the cash floor when rising prices pushed it below (sell high, never in a falling market)
        cash_total = cash + cash_eq - collateral
        shortfall = floor_value - cash_total
        if shortfall > 0.02 * equity and ((not self.regime_rules) or regime.allows_discretionary_sells):
            need = shortfall + 0.01 * equity
            ranked = sorted(((s, pos) for s, pos in pf.positions.items() if s not in sold_qty and pos.qty > 0),
                            key=lambda kv: -kv[1].gain)
            for sym, pos in ranked:
                if need <= 0:
                    break
                price = prices.get(sym, pos.price)
                take = min(need, pos.value * 0.5)
                qty = shares_for(take, price, self.x.allow_fractional)
                if qty <= 0 and pos.qty >= 1:
                    qty = 1.0
                qty = min(qty, pos.qty - pf.covered_shares(sym))
                if qty <= 0:
                    continue
                decision.orders.append(PlannedOrder(sym, Side.SELL, qty, price, self._limit(Side.SELL, price),
                                                    f"restore the {p.cash_floor:.0%} cash floor (trim the biggest winner)",
                                                    "floor_restore"))
                sold_qty[sym] = sold_qty.get(sym, 0.0) + qty
                proceeds = qty * price
                cash += proceeds
                need -= proceeds
                dw = proceeds / equity
                weights[sym] = weights.get(sym, 0.0) - dw
                sector_w[self._sector(sym)] -= dw
                stock_w -= dw
                if sym in spec_names:
                    spec_w -= dw
            decision.notes.append(f"cash + T-bills were {cash_total / equity:.1%}, below the {p.cash_floor:.0%} floor")

        # 2. buys -------------------------------------------------------------
        if p.drawdown_pause is not None and pf.drawdown >= p.drawdown_pause and buys:
            decision.notes.append(f"new buys paused: portfolio drawdown {pf.drawdown:.0%} >= {p.drawdown_pause:.0%}")
            for sig in buys:
                decision.rejected.append((sig, "drawdown pause"))
            buys = []
        new_today = buys_today = 0
        for sig in buys:
            sym = sig.symbol
            if sym in sold_qty:
                decision.rejected.append((sig, "sold today"))
                continue
            if sig.strength < p.min_conviction:
                decision.rejected.append((sig, f"conviction {sig.strength:.2f} < {p.min_conviction:.2f}"))
                continue
            price = prices.get(sym)
            if price is None or price <= 0 or math.isnan(price):
                decision.rejected.append((sig, "no price"))
                continue
            dte = ctx.days_to_earnings(sym)
            if dte is not None and 0 <= dte <= p.earnings_blackout_days:
                decision.rejected.append((sig, f"earnings in {dte} trading days"))
                continue
            if buys_today >= p.max_buys_per_day:
                decision.rejected.append((sig, "daily buy limit"))
                continue
            pos = pf.positions.get(sym)
            row = ctx.row(sym)
            atr = row.get("atr14") if row is not None else math.nan
            atr_pct = row.get("atr_pct") if row is not None else math.nan
            ignore_tranche_rules = bool(sig.details.get("ignore_tranche_rules"))
            if pos is None:
                if new_today >= p.max_new_positions_per_day:
                    decision.rejected.append((sig, "daily new-position limit"))
                    continue
                if n_positions >= p.max_positions:
                    decision.rejected.append((sig, f"max positions ({p.max_positions})"))
                    continue
                full = sig.target_weight or target_weight(sig.strength, p, regime, atr_pct)
                k = 0
            else:
                full = pos.target_weight or sig.target_weight or target_weight(sig.strength, p, regime, atr_pct)
                built = weights.get(sym, 0.0) / full if full > 0 else 1.0
                if built >= 0.95 or (ignore_tranche_rules and pos.tranches >= len(p.tranches)):
                    decision.rejected.append((sig, "position fully built"))
                    continue
                # after a trim the position can be rebuilt: size by what is missing, not by tranche count
                k = min(pos.tranches, len(p.tranches) - 1)
                if not ignore_tranche_rules:
                    if pos.days_since_entry < p.tranche_min_gap_days:
                        decision.rejected.append((sig, f"last entry {pos.days_since_entry} days ago"))
                        continue
                    deeper = (not math.isnan(atr)) and pos.last_entry_price is not None and \
                        price <= pos.last_entry_price - p.tranche_min_drop_atr * atr
                    pyramid = regime in (Regime.RISK_ON, Regime.NEUTRAL) and pos.gain > 0
                    if not (deeper or pyramid):
                        decision.rejected.append((sig, "add-on needs a deeper support or a winning position"))
                        continue
            cur_w = weights.get(sym, 0.0)
            add_w = tranche_weight(full, p, k) if pos is None else min(tranche_weight(full, p, k), full - cur_w)
            spec = self.is_speculative(sym, ctx)
            sector = self._sector(sym)
            caps = [
                ("position cap", p.max_position - cur_w),
                ("sector cap", p.max_sector - sector_w.get(sector, 0.0)),
                ("stock budget", max_stock - stock_w),
                ("cash floor", (cash + cash_eq - collateral - floor_value) / equity),
            ]
            if spec:
                if pos is None and len(spec_names) >= p.speculative_max_names:
                    decision.rejected.append((sig, "speculative sleeve full (names)"))
                    continue
                caps.append(("speculative sleeve", p.speculative_sleeve - spec_w))
            limit_name, room = min(caps, key=lambda c: c[1])
            alloc_w = min(add_w, room)
            value = alloc_w * equity
            if value < p.min_order_value:
                why = f"{limit_name} reached" if room < add_w else "order below minimum size"
                decision.rejected.append((sig, why))
                continue
            limit_px = self._limit(Side.BUY, price)
            qty = shares_for(value, limit_px or price, self.x.allow_fractional)
            if qty <= 0:
                decision.rejected.append((sig, "one share costs more than the allowed size"))
                continue
            cost = qty * (limit_px or price)
            tag = "tranche" if pos is not None else "entry"
            reason = sig.reason or ", ".join(sig.setups)
            if alloc_w < add_w:
                reason += f" (trimmed by {limit_name})"
            decision.orders.append(PlannedOrder(sym, Side.BUY, qty, price, limit_px, reason, tag, sig.strength,
                                                target_weight=full, tranche=k + 1, setups=sig.setups))
            buys_today += 1
            if pos is None:
                new_today += 1
                n_positions += 1
            dw = cost / equity
            cash -= cost
            weights[sym] = cur_w + dw
            sector_w[sector] = sector_w.get(sector, 0.0) + dw
            stock_w += dw
            if spec:
                spec_w += dw
                spec_names.add(sym)

        # 3. options --------------------------------------------------------------
        if option_signals:
            sold_now: dict[str, float] = {}
            for order in decision.orders:
                if order.side == Side.SELL and not order.is_option:
                    sold_now[order.symbol] = sold_now.get(order.symbol, 0.0) + order.qty
            cash, collateral, stock_w = self._plan_options(option_signals, ctx, decision, cash, cash_eq, collateral,
                                                           equity, stock_w, floor_value, max_stock, sold_now)

        # 4. treasury sleeve and T-bill cash management ------------------------
        if self.manage_cash:
            self._treasury_and_cash(ctx, prices, decision, cash - collateral, cash_eq, equity)
        decision.budget["stock_exposure_after"] = stock_w
        return decision

    def _plan_options(self, signals: list[Signal], ctx: StrategyContext, decision: RiskDecision, cash: float,
                      cash_eq: float, collateral: float, equity: float, stock_w: float, floor_value: float,
                      max_stock: float, sold_now: dict[str, float] | None = None) -> tuple[float, float, float]:
        """Option exits are always allowed (options expire); opens must fit the premium sleeve,
        the per-trade cap, the put collateral cap, the stock budget and the cash floor."""
        from .options.pricing import choose_contract, model_iv

        p, pf, o = self.p, ctx.portfolio, self.opt
        long_value = pf.long_option_value
        planned_calls: dict[str, int] = {}
        for sig in (s for s in signals if s.option_key):
            op = pf.options.get(sig.option_key)
            if op is None:
                decision.rejected.append((sig, "no such option position"))
                continue
            side = Side.SELL if op.is_long else Side.BUY
            decision.orders.append(PlannedOrder(op.underlying, side, abs(op.contracts), op.mark, None, sig.reason,
                                                "option_close", sig.strength, setups=sig.setups, option_key=op.key))
            if op.is_long:
                cash += op.value
                long_value -= op.value
            else:
                cash += op.value  # value is negative: buying back costs cash
                if op.right == "P":
                    collateral -= op.strike * 100 * abs(op.contracts)
                    stock_w -= op.strike * 100 * abs(op.contracts) / equity
        opens = sorted((s for s in signals if s.option is not None and not s.option_key), key=lambda s: -s.strength)
        for sig in opens:
            spec = sig.option
            if sig.strength < p.min_conviction:
                decision.rejected.append((sig, f"conviction {sig.strength:.2f} < {p.min_conviction:.2f}"))
                continue
            row = ctx.row(sig.symbol)
            spot = row.get("close") if row is not None else math.nan
            if row is None or math.isnan(spot) or spot <= 0:
                decision.rejected.append((sig, "no price"))
                continue
            iv = model_iv(sig.symbol, row.get("vol20"), row.get("vol60"), ctx.market.vix, o.iv_premium)
            contract, price, _delta = choose_contract(sig.symbol, spec, spot, ctx.day, iv, ctx.market.risk_free)
            if price < o.min_premium:
                decision.rejected.append((sig, "option premium too small"))
                continue
            free = cash + cash_eq - collateral - floor_value
            if spec.side == Side.BUY:
                if p.options_sleeve <= 0:
                    decision.rejected.append((sig, f"the {p.name} profile does not buy options"))
                    continue
                budget = min(p.options_per_trade * p.size_multiplier(ctx.market.regime) * equity,
                             p.options_sleeve * equity - long_value, free)
                unit = price * (1 + o.half_spread) * 100 + o.commission_per_contract
                n = int(budget // unit) if budget > 0 else 0
                n = min(n, spec.contracts) if spec.contracts else n
                if n < 1:
                    decision.rejected.append((sig, "options premium budget used up"))
                    continue
                cost = n * unit
                cash -= cost
                long_value += cost
            elif spec.right == "P":
                per = contract.strike * 100
                room = min(p.short_put_collateral_max * equity - collateral, free, (max_stock - stock_w) * equity,
                           p.max_position * equity)
                n = int(room // per) if room > 0 else 0
                n = min(n, spec.contracts) if spec.contracts else n
                if n < 1:
                    decision.rejected.append((sig, "no room to secure another put"))
                    continue
                collateral += n * per
                stock_w += n * per / equity
                cash += n * (price * (1 - o.half_spread) * 100 - o.commission_per_contract)
            else:
                pos = pf.positions.get(sig.symbol)
                held = int(pos.qty - (sold_now or {}).get(sig.symbol, 0.0)) if pos else 0
                uncovered = held - pf.covered_shares(sig.symbol) - planned_calls.get(sig.symbol, 0) * 100
                n = min(spec.contracts or 10**6, uncovered // 100)
                if n < 1:
                    decision.rejected.append((sig, "a covered call needs 100 uncovered shares per contract"))
                    continue
                planned_calls[sig.symbol] = planned_calls.get(sig.symbol, 0) + n
                cash += n * (price * (1 - o.half_spread) * 100 - o.commission_per_contract)
            decision.orders.append(PlannedOrder(sig.symbol, spec.side, n, price, None, sig.reason, "option_open",
                                                sig.strength, setups=sig.setups, option=contract, iv=iv))
        return cash, collateral, stock_w

    def _treasury_and_cash(self, ctx: StrategyContext, prices: dict[str, float], decision: RiskDecision,
                           cash: float, cash_eq: float, equity: float) -> None:
        p, u = self.p, self.u
        pf: PortfolioView = ctx.portfolio
        tsym = u.treasury_symbol
        if p.treasury_sleeve > 0 and tsym and prices.get(tsym):
            tprice = prices[tsym]
            target = p.treasury_sleeve * equity
            diff = target - pf.treasury_value
            if abs(diff) > p.treasury_band * equity and abs(diff) >= p.min_order_value:
                side = Side.BUY if diff > 0 else Side.SELL
                if side == Side.BUY:
                    diff = min(diff, max(0.0, cash + cash_eq - p.cash_floor * equity))
                qty = shares_for(abs(diff), tprice, self.x.allow_fractional)
                if qty > 0:
                    decision.orders.append(PlannedOrder(tsym, side, qty, tprice, self._limit(side, tprice),
                                                        f"treasury sleeve to {p.treasury_sleeve:.0%}", "treasury_rebalance"))
                    cash += -qty * tprice if side == Side.BUY else qty * tprice
        cv = u.cash_vehicle
        if not cv or not prices.get(cv) or ctx.mode == "backtest":
            return
        cvp = prices[cv]
        buffer = p.cash_buffer * equity
        if cash < buffer and cash_eq > 0:
            need = min(buffer - cash, cash_eq)
            qty = math.ceil(need / cvp) if not self.x.allow_fractional else round(need / cvp, 4)
            qty = min(qty, cash_eq / cvp)
            if qty * cvp >= p.min_order_value:
                decision.orders.insert(0, PlannedOrder(cv, Side.SELL, float(math.floor(qty) if not self.x.allow_fractional else qty),
                                                       cvp, self._limit(Side.SELL, cvp),
                                                       "fund buys from the T-bill reserve", "cash_management"))
        elif cash > buffer + max(0.01 * equity, p.min_order_value):
            excess = cash - buffer
            qty = shares_for(excess, cvp * (1 + self.x.limit_offset), self.x.allow_fractional)
            if qty > 0:
                decision.orders.append(PlannedOrder(cv, Side.BUY, qty, cvp, self._limit(Side.BUY, cvp),
                                                    "park idle cash in T-bills", "cash_management"))
