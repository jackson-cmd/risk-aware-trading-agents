"""Tradier brokerage API (REST). Options: sandbox (default true), account_id.

Environment variables: TRADIER_TOKEN (or TRADIER_SANDBOX_TOKEN), TRADIER_ACCOUNT_ID.
"""

from __future__ import annotations

import re
from typing import Any

from ..types import AccountSnapshot, OrderRequest, OrderResult, OrderType, Position, Quote, Side
from .base import Broker, BrokerError, MissingCredentials, env_first, fmt_price, map_status
from .rest import RestClient, as_list, num

LIVE_URL = "https://api.tradier.com/v1"
SANDBOX_URL = "https://sandbox.tradier.com/v1"


class TradierBroker(Broker):
    name = "tradier"

    def __init__(self, sandbox: bool = True, account_id: str | None = None, token_env: str | None = None,
                 **options: Any):
        super().__init__(**options)
        self.sandbox = bool(sandbox)
        names = [token_env] if token_env else (["TRADIER_SANDBOX_TOKEN", "TRADIER_TOKEN"] if self.sandbox
                                                else ["TRADIER_TOKEN"])
        self.token = env_first(*names)
        self.account_id = account_id or env_first("TRADIER_ACCOUNT_ID")
        self.base_url = SANDBOX_URL if self.sandbox else LIVE_URL
        self.client: RestClient | None = None
        self._names = names

    @property
    def is_paper(self) -> bool:
        return self.sandbox

    def connect(self) -> None:
        if not self.token:
            raise MissingCredentials("Tradier", self._names)
        self.client = RestClient(self.base_url, {"Authorization": f"Bearer {self.token}", "Accept": "application/json"})
        if not self.account_id:
            prof = (self.client.get("/user/profile") or {}).get("profile") or {}
            accounts = as_list(prof.get("account"))
            if not accounts:
                raise BrokerError("Tradier: no account in the profile")
            self.account_id = accounts[0]["account_number"]

    def _c(self) -> RestClient:
        if self.client is None:
            self.connect()
        assert self.client is not None
        return self.client

    def account(self) -> AccountSnapshot:
        b = (self._c().get(f"/accounts/{self.account_id}/balances") or {}).get("balances") or {}
        cash_block = b.get("cash") or {}
        margin = b.get("margin") or {}
        bp = num(margin.get("stock_buying_power"), num(cash_block.get("cash_available"), num(b.get("total_cash"))))
        return AccountSnapshot(broker=self.name, account_id=str(self.account_id), equity=num(b.get("total_equity")),
                               cash=num(b.get("total_cash")), buying_power=bp, is_paper=self.is_paper)

    def positions(self) -> list[Position]:
        data = (self._c().get(f"/accounts/{self.account_id}/positions") or {}).get("positions")
        items = as_list(data.get("position") if isinstance(data, dict) else data)
        out = []
        for p in items:
            qty = num(p.get("quantity"))
            out.append(Position(symbol=p["symbol"], qty=qty, avg_cost=num(p.get("cost_basis")) / qty if qty else 0.0))
        if out:
            q = self.quotes([p.symbol for p in out])
            for p in out:
                if p.symbol in q:
                    p.market_price = q[p.symbol].price
        return out

    def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        if not symbols:
            return {}
        data = (self._c().get("/markets/quotes", params={"symbols": ",".join(symbols)}) or {}).get("quotes") or {}
        out = {}
        for q in as_list(data.get("quote") if isinstance(data, dict) else None):
            out[q["symbol"]] = Quote(symbol=q["symbol"], bid=num(q.get("bid"), None), ask=num(q.get("ask"), None),
                                     last=num(q.get("last"), None))
        return out

    def _result(self, o: dict[str, Any]) -> OrderResult:
        return OrderResult(order_id=str(o.get("id")), client_order_id=o.get("tag"), symbol=o.get("symbol", ""),
                           side=Side.BUY if str(o.get("side", "")).startswith("buy") else Side.SELL,
                           qty=num(o.get("quantity")), status=map_status(o.get("status")),
                           filled_qty=num(o.get("exec_quantity")), avg_fill_price=num(o.get("avg_fill_price"), None),
                           limit_price=num(o.get("price"), None), raw=o)

    def place_order(self, order: OrderRequest) -> OrderResult:
        form: dict[str, Any] = {
            "class": "equity", "symbol": order.symbol.replace("-", "."),
            "side": "buy" if order.side == Side.BUY else "sell", "quantity": f"{order.qty:g}",
            "type": "limit" if order.order_type == OrderType.LIMIT else "market",
            "duration": order.time_in_force.value.lower(),
        }
        if order.order_type == OrderType.LIMIT:
            if order.limit_price is None:
                raise BrokerError("limit order without a limit price")
            form["price"] = fmt_price(order.limit_price)
        if order.client_order_id:
            form["tag"] = re.sub(r"[^A-Za-z0-9-]", "-", order.client_order_id)[:255]
        resp = (self._c().post(f"/accounts/{self.account_id}/orders", data=form) or {}).get("order") or {}
        if not resp.get("id"):
            raise BrokerError(f"Tradier rejected the order: {resp}")
        return OrderResult(order_id=str(resp["id"]), client_order_id=order.client_order_id, symbol=order.symbol,
                           side=order.side, qty=order.qty, status=map_status(resp.get("status") or "pending"),
                           limit_price=order.limit_price, raw=resp)

    def cancel_order(self, order_id: str) -> None:
        self._c().delete(f"/accounts/{self.account_id}/orders/{order_id}")

    def open_orders(self) -> list[OrderResult]:
        data = (self._c().get(f"/accounts/{self.account_id}/orders") or {}).get("orders")
        items = as_list(data.get("order") if isinstance(data, dict) else data)
        return [self._result(o) for o in items if map_status(o.get("status")).value in ("NEW", "PENDING", "PARTIALLY_FILLED")]
