"""Built-in paper broker: a local simulated account stored in a JSON file.

Prices come from a quote function (Yahoo Finance by default). Marketable limit
orders and market orders fill immediately at the quote with slippage; other
limit orders rest until a later run finds the price inside the limit or the day
ends. No account or API key needed.
"""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ..config import RISKAWARE_HOME
from ..types import AccountSnapshot, OrderRequest, OrderResult, OrderStatus, OrderType, Position, Quote, Side
from .base import Broker, BrokerError


def yahoo_quotes(symbols: list[str]) -> dict[str, Quote]:
    """Latest prices from Yahoo Finance (delayed during the session)."""
    import yfinance as yf

    out: dict[str, Quote] = {}
    if not symbols:
        return out
    try:
        df = yf.download(symbols, period="5d", interval="1d", auto_adjust=False, progress=False,
                         group_by="ticker", threads=True)
    except Exception:
        return out
    for s in symbols:
        try:
            sub = df[s] if s in df.columns.get_level_values(0) else df
            last = float(sub["Close"].dropna().iloc[-1])
            out[s] = Quote(symbol=s, last=last, bid=last, ask=last, timestamp=datetime.now(timezone.utc))
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return out


class PaperBroker(Broker):
    name = "paper"
    supports_fractional = True

    def __init__(self, account_file: str | Path | None = None, starting_cash: float = 100_000.0,
                 slippage_bps: float = 5.0, commission_per_share: float = 0.0,
                 quote_fn: Callable[[list[str]], dict[str, Quote]] | None = None, account_name: str = "default",
                 **options: Any):
        super().__init__(**options)
        self.path = Path(account_file).expanduser() if account_file else RISKAWARE_HOME / "paper" / f"{account_name}.json"
        self.starting_cash = float(starting_cash)
        self.slippage = slippage_bps / 10_000
        self.commission = commission_per_share
        self.quote_fn = quote_fn or yahoo_quotes
        self.state: dict[str, Any] = {}

    @property
    def is_paper(self) -> bool:
        return True

    # persistence ----------------------------------------------------------------
    def connect(self) -> None:
        if self.path.is_file():
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self.state = {"cash": self.starting_cash, "positions": {}, "orders": [], "fills": [],
                          "created": datetime.now(timezone.utc).isoformat()}
            self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2, default=str), encoding="utf-8")
        tmp.replace(self.path)

    def reset(self, cash: float | None = None) -> None:
        self.state = {"cash": float(cash or self.starting_cash), "positions": {}, "orders": [], "fills": [],
                      "created": datetime.now(timezone.utc).isoformat()}
        self._save()

    def _ensure(self) -> None:
        if not self.state:
            self.connect()

    # reads -------------------------------------------------------------------------
    def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        return self.quote_fn(list(dict.fromkeys(symbols)))

    def positions(self) -> list[Position]:
        self._ensure()
        self._sweep()
        syms = list(self.state["positions"])
        q = self.quotes(syms) if syms else {}
        out = []
        for s, p in self.state["positions"].items():
            px = q[s].price if s in q and q[s].price else p["avg_cost"]
            out.append(Position(symbol=s, qty=p["qty"], avg_cost=p["avg_cost"], market_price=px))
        return out

    def account(self) -> AccountSnapshot:
        self._ensure()
        pos = self.positions()
        equity = self.state["cash"] + sum(p.market_value for p in pos)
        reserved = sum(o["qty"] * (o.get("limit_price") or 0) for o in self.state["orders"]
                       if o["status"] == "NEW" and o["side"] == "BUY")
        return AccountSnapshot(broker=self.name, account_id=self.path.stem, equity=equity, cash=self.state["cash"],
                               buying_power=self.state["cash"] - reserved, is_paper=True)

    def open_orders(self) -> list[OrderResult]:
        self._ensure()
        self._sweep()
        return [self._result(o) for o in self.state["orders"] if o["status"] == "NEW"]

    # writes ------------------------------------------------------------------------
    def place_order(self, order: OrderRequest) -> OrderResult:
        self._ensure()
        if order.qty <= 0:
            raise BrokerError("quantity must be positive")
        if order.client_order_id and any(o.get("client_order_id") == order.client_order_id for o in self.state["orders"]):
            raise BrokerError(f"duplicate client_order_id {order.client_order_id}")
        rec = {
            "id": uuid.uuid4().hex[:12], "client_order_id": order.client_order_id, "symbol": order.symbol,
            "side": order.side.value, "qty": float(order.qty), "type": order.order_type.value,
            "limit_price": order.limit_price, "status": "NEW", "filled_qty": 0.0, "avg_fill_price": None,
            "submitted": datetime.now(timezone.utc).isoformat(), "day": date.today().isoformat(), "tag": order.tag,
        }
        if order.side == Side.SELL:
            held = self.state["positions"].get(order.symbol, {}).get("qty", 0.0)
            if order.qty > held + 1e-9:
                rec["status"] = "REJECTED"
                rec["message"] = f"insufficient shares: have {held}, selling {order.qty}"
                self.state["orders"].append(rec)
                self._save()
                return self._result(rec)
        self.state["orders"].append(rec)
        q = self.quotes([order.symbol]).get(order.symbol)
        if q is not None and q.price:
            self._try_fill(rec, q)
        self._save()
        return self._result(rec)

    def cancel_order(self, order_id: str) -> None:
        self._ensure()
        for o in self.state["orders"]:
            if o["id"] == order_id and o["status"] == "NEW":
                o["status"] = "CANCELED"
        self._save()

    def get_order(self, order_id: str) -> OrderResult | None:
        self._ensure()
        for o in self.state["orders"]:
            if o["id"] == order_id:
                return self._result(o)
        return None

    # internals ---------------------------------------------------------------------
    def _sweep(self) -> None:
        """Expire DAY orders from earlier days, try to fill resting orders at current quotes."""
        today = date.today().isoformat()
        changed = False
        resting = [o for o in self.state["orders"] if o["status"] == "NEW"]
        for o in resting:
            if o["day"] < today:
                o["status"] = "EXPIRED"
                changed = True
        resting = [o for o in resting if o["status"] == "NEW"]
        if resting:
            q = self.quotes([o["symbol"] for o in resting])
            for o in resting:
                if o["symbol"] in q and q[o["symbol"]].price:
                    changed |= self._try_fill(o, q[o["symbol"]])
        if changed:
            self._save()

    def _try_fill(self, rec: dict[str, Any], q: Quote) -> bool:
        side = Side(rec["side"])
        ref = (q.ask or q.price) if side == Side.BUY else (q.bid or q.price)
        if ref is None or ref <= 0:
            return False
        px = ref * (1 + self.slippage) if side == Side.BUY else ref * (1 - self.slippage)
        lim = rec.get("limit_price")
        if rec["type"] == OrderType.LIMIT.value and lim is not None:
            if side == Side.BUY and ref > lim:
                return False
            if side == Side.SELL and ref < lim:
                return False
            px = min(px, lim) if side == Side.BUY else max(px, lim)
        qty = rec["qty"]
        fee = self.commission * qty
        if side == Side.BUY:
            cost = qty * px + fee
            if cost > self.state["cash"] + 1e-6:
                rec["status"] = "REJECTED"
                rec["message"] = "insufficient cash"
                return True
            self.state["cash"] -= cost
            pos = self.state["positions"].setdefault(rec["symbol"], {"qty": 0.0, "avg_cost": 0.0})
            new_qty = pos["qty"] + qty
            pos["avg_cost"] = (pos["avg_cost"] * pos["qty"] + px * qty) / new_qty
            pos["qty"] = new_qty
        else:
            pos = self.state["positions"].get(rec["symbol"])
            if not pos or pos["qty"] + 1e-9 < qty:
                rec["status"] = "REJECTED"
                rec["message"] = "insufficient shares"
                return True
            self.state["cash"] += qty * px - fee
            pos["qty"] -= qty
            if pos["qty"] <= 1e-9:
                del self.state["positions"][rec["symbol"]]
        rec["status"] = "FILLED"
        rec["filled_qty"] = qty
        rec["avg_fill_price"] = round(px, 4)
        self.state["fills"].append({"order": rec["id"], "symbol": rec["symbol"], "side": rec["side"], "qty": qty,
                                    "price": px, "time": datetime.now(timezone.utc).isoformat()})
        return True

    def _result(self, rec: dict[str, Any]) -> OrderResult:
        return OrderResult(
            order_id=rec["id"], client_order_id=rec.get("client_order_id"), symbol=rec["symbol"],
            side=Side(rec["side"]), qty=rec["qty"], status=OrderStatus(rec["status"]),
            filled_qty=rec.get("filled_qty", 0.0), avg_fill_price=rec.get("avg_fill_price"),
            limit_price=rec.get("limit_price"), message=rec.get("message", ""), raw=rec,
        )

    @staticmethod
    def _isnan(x: float | None) -> bool:
        return x is None or (isinstance(x, float) and math.isnan(x))
