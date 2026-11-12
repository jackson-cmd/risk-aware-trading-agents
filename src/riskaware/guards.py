"""Safety guards around order submission."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from . import marketcal as mc
from .config import RISKAWARE_HOME, AppConfig
from .risk import PlannedOrder
from .types import Quote, Side

HALT_FILE = RISKAWARE_HOME / "HALT"


class GuardError(RuntimeError):
    pass


def halt(reason: str = "manual halt") -> None:
    HALT_FILE.parent.mkdir(parents=True, exist_ok=True)
    HALT_FILE.write_text(reason, encoding="utf-8")


def resume() -> bool:
    if HALT_FILE.exists():
        HALT_FILE.unlink()
        return True
    return False


def is_halted() -> str | None:
    return HALT_FILE.read_text(encoding="utf-8") if HALT_FILE.exists() else None


@dataclass
class GuardResult:
    approved: list[PlannedOrder] = field(default_factory=list)
    blocked: list[tuple[PlannedOrder, str]] = field(default_factory=list)


class Guards:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self.x = cfg.execution

    def preflight(self, mode: str, broker_is_paper: bool, allow_live: bool, today: date | None = None) -> None:
        """Raise GuardError if this run must not place orders at all."""
        today = today or mc.now_ny().date()
        if mode == "dry-run":
            return
        reason = is_halted()
        if reason is not None:
            raise GuardError(f"trading is halted ({reason.strip() or 'kill switch'}); run `riskaware resume` to re-enable")
        if self.x.require_market_day and not mc.is_trading_day(today):
            raise GuardError(f"{today} is not a trading day; nothing to do (use --dry-run to preview)")
        if mode == "paper" and not broker_is_paper:
            raise GuardError("execution.mode is 'paper' but this broker connection is LIVE. "
                             "Use a paper account, or set execution.mode: live and pass --live.")
        if mode == "live":
            if self.x.mode != "live":
                raise GuardError("live trading needs `execution.mode: live` in the config file")
            if not allow_live:
                raise GuardError("live trading needs the --live flag")

    def check(self, orders: list[PlannedOrder], quotes: dict[str, Quote], equity: float,
              max_position: float, seen: set[str] | None = None) -> GuardResult:
        out = GuardResult()
        cap = self.x.max_order_value or max_position * equity * 1.05
        ordered = sorted(orders, key=lambda o: 0 if o.side == Side.SELL else 1)
        for o in ordered:
            if len(out.approved) >= self.x.max_orders_per_run:
                out.blocked.append((o, f"max {self.x.max_orders_per_run} orders per run"))
                continue
            if o.qty <= 0:
                out.blocked.append((o, "zero quantity"))
                continue
            q = quotes.get(o.symbol)
            ref = q.price if q else None
            if ref is None or ref <= 0:
                ref = o.price
            if o.limit_price is not None and ref:
                dev = abs(o.limit_price / ref - 1)
                if dev > self.x.price_band:
                    out.blocked.append((o, f"limit {o.limit_price:.2f} is {dev:.1%} from the quote {ref:.2f}"))
                    continue
            if o.side == Side.BUY and o.tag not in ("cash_management", "treasury_rebalance") and o.value > cap:
                out.blocked.append((o, f"order value ${o.value:,.0f} above the ${cap:,.0f} cap"))
                continue
            out.approved.append(o)
        return out
