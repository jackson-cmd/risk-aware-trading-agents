"""Per-account position memory that brokers do not keep: entry dates, tranches, targets, trims.

Stored as JSON in ~/.riskaware/state/<broker>-<account>.json. Orders are recorded as
pending when submitted and applied once the broker reports them filled.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from . import marketcal as mc
from .config import RISKAWARE_HOME
from .types import OrderStatus, Position


@dataclass
class PositionMeta:
    first_entry: str
    last_entry: str
    last_entry_price: float
    tranches: int = 1
    target_weight: float | None = None
    last_trim: str | None = None
    last_trim_price: float | None = None
    peak_price: float = 0.0


class StateStore:
    def __init__(self, broker: str, account: str, root: Path | None = None):
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in f"{broker}-{account}")
        self.path = (root or RISKAWARE_HOME / "state") / f"{safe}.json"
        self.positions: dict[str, PositionMeta] = {}
        self.pending: list[dict[str, Any]] = []
        self.peak_equity = 0.0
        self.load()

    def load(self) -> None:
        if not self.path.is_file():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.positions = {k: PositionMeta(**v) for k, v in data.get("positions", {}).items()}
        self.pending = data.get("pending", [])
        self.peak_equity = float(data.get("peak_equity", 0.0))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"positions": {k: asdict(v) for k, v in self.positions.items()}, "pending": self.pending,
                "peak_equity": self.peak_equity}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def add_pending(self, order_id: str, symbol: str, side: str, tag: str, limit_price: float | None,
                    target_weight: float | None, day: date) -> None:
        self.pending.append({"order_id": order_id, "symbol": symbol, "side": side, "tag": tag,
                             "limit_price": limit_price, "target_weight": target_weight, "day": day.isoformat()})

    def reconcile(self, positions: list[Position], get_status, today: date) -> list[str]:
        """Apply filled pending orders, drop dead ones, and adopt positions opened outside the agent."""
        notes = []
        still = []
        for p in self.pending:
            status = None
            try:
                res = get_status(p["order_id"])
                status = res.status if res is not None else None
                fill_px = res.avg_fill_price if res is not None else None
            except Exception:
                fill_px = None
            if status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
                self._apply(p, fill_px or p.get("limit_price") or 0.0)
                if status == OrderStatus.PARTIALLY_FILLED:
                    still.append(p)
            elif status in (OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED):
                notes.append(f"order {p['order_id']} {p['symbol']} {status.value.lower()}")
            elif date.fromisoformat(p["day"]) < today and status is None:
                notes.append(f"order {p['order_id']} {p['symbol']}: status unknown, dropped")
            else:
                still.append(p)
        self.pending = still
        held = {p.symbol: p for p in positions if p.qty > 0}
        for sym in list(self.positions):
            if sym not in held:
                del self.positions[sym]
        for sym, pos in held.items():
            if sym not in self.positions:
                start = mc.add_trading_days(today, -20).isoformat()
                self.positions[sym] = PositionMeta(first_entry=start, last_entry=start,
                                                   last_entry_price=pos.avg_cost, tranches=1,
                                                   peak_price=pos.market_price or pos.avg_cost)
                notes.append(f"adopted existing position {sym}")
            meta = self.positions[sym]
            if pos.market_price:
                meta.peak_price = max(meta.peak_price, pos.market_price)
        return notes

    def _apply(self, p: dict[str, Any], price: float) -> None:
        sym, day = p["symbol"], p["day"]
        meta = self.positions.get(sym)
        if p["side"] == "BUY":
            if meta is None:
                self.positions[sym] = PositionMeta(first_entry=day, last_entry=day, last_entry_price=price, tranches=1,
                                                   target_weight=p.get("target_weight"), peak_price=price)
            else:
                meta.tranches += 1
                meta.last_entry = day
                meta.last_entry_price = price
                meta.target_weight = meta.target_weight or p.get("target_weight")
        elif meta is not None:
            meta.last_trim = day
            meta.last_trim_price = price
