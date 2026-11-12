"""Public.com Individual API (REST). Live accounts only: Public has no paper environment.

Environment variables: PUBLIC_API_SECRET (the secret key from Public's API settings page),
optional PUBLIC_ACCOUNT_ID. The secret is exchanged for a short-lived access token.
Orders are asynchronous: placement returns an id, the status is read afterwards.
Public's API terms allow personal, non-commercial use only.
"""

from __future__ import annotations

import time
from typing import Any

from ..types import AccountSnapshot, OrderRequest, OrderResult, OrderType, Position, Quote, Side
from .base import Broker, BrokerError, MissingCredentials, env_first, fmt_price, map_status, uuid_from
from .rest import RestClient, as_list, num

BASE_URL = "https://api.public.com"


class PublicBroker(Broker):
    name = "public"
    has_paper = False
    supports_fractional = True

    def __init__(self, account_id: str | None = None, secret_env: str = "PUBLIC_API_SECRET",
                 token_minutes: int = 60, base_url: str = BASE_URL, **options: Any):
        super().__init__(**options)
        self.secret = env_first(secret_env, "PUBLIC_SECRET_KEY")
        self.account_id = account_id or env_first("PUBLIC_ACCOUNT_ID")
        self.token_minutes = token_minutes
        self.client = RestClient(base_url, {"Content-Type": "application/json", "Accept": "application/json"})
        self._token_expiry = 0.0
        self._secret_env = secret_env

    @property
    def is_paper(self) -> bool:
        return False

    def _auth(self) -> None:
        if not self.secret:
            raise MissingCredentials("Public", [self._secret_env])
        if time.time() < self._token_expiry - 60:
            return
        data = self.client.post("/userapiauthservice/personal/access-tokens",
                                json={"validityInMinutes": self.token_minutes, "secret": self.secret})
        token = (data or {}).get("accessToken")
        if not token:
            raise BrokerError("Public: no accessToken in the auth response")
        self.client.session.headers["Authorization"] = f"Bearer {token}"
        self._token_expiry = time.time() + self.token_minutes * 60

    def connect(self) -> None:
        self._auth()
        if not self.account_id:
            accounts = as_list((self.client.get("/userapigateway/trading/account") or {}).get("accounts"))
            brokerage = [a for a in accounts if a.get("accountType") == "BROKERAGE"] or accounts
            if not brokerage:
                raise BrokerError("Public: no accounts returned")
            self.account_id = brokerage[0]["accountId"]

    def _portfolio(self) -> dict[str, Any]:
        self.connect()
        return self.client.get(f"/userapigateway/trading/{self.account_id}/portfolio/v2") or {}

    def account(self) -> AccountSnapshot:
        pf = self._portfolio()
        equity_items = as_list(pf.get("equity"))
        total = num(pf.get("totalAccountValue"), 0.0) or sum(num(e.get("value")) for e in equity_items)
        cash = pf.get("cash")
        if cash is None:
            cash = next((e.get("value") for e in equity_items if e.get("type") == "CASH"), None)
        bp = pf.get("buyingPower") or {}
        return AccountSnapshot(broker=self.name, account_id=str(self.account_id), equity=total,
                               cash=num(cash, num(bp.get("cashOnlyBuyingPower"))),
                               buying_power=num(bp.get("buyingPower"), num(bp.get("cashOnlyBuyingPower"))),
                               is_paper=False)

    def positions(self) -> list[Position]:
        out = []
        for p in as_list(self._portfolio().get("positions")):
            inst = p.get("instrument") or {}
            if inst.get("type", "EQUITY") != "EQUITY":
                continue
            last = (p.get("lastPrice") or {}).get("lastPrice")
            cost = p.get("costBasis") or {}
            out.append(Position(symbol=inst.get("symbol", ""), qty=num(p.get("quantity")),
                                avg_cost=num(cost.get("unitCost")), market_price=num(last, None)))
        return out

    def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        if not symbols:
            return {}
        self.connect()
        body = {"instruments": [{"symbol": s, "type": "EQUITY"} for s in symbols]}
        data = self.client.post(f"/userapigateway/marketdata/{self.account_id}/quotes", json=body) or {}
        out = {}
        for q in as_list(data.get("quotes")):
            sym = (q.get("instrument") or {}).get("symbol")
            if not sym or (q.get("outcome") not in (None, "SUCCESS")):
                continue
            out[sym] = Quote(symbol=sym, bid=num(q.get("bid"), None), ask=num(q.get("ask"), None),
                             last=num(q.get("last"), None))
        return out

    def _result(self, o: dict[str, Any], fallback: OrderRequest | None = None) -> OrderResult:
        inst = o.get("instrument") or {}
        side = o.get("side") or o.get("orderSide") or (fallback.side.value if fallback else "BUY")
        return OrderResult(
            order_id=o.get("orderId", ""), client_order_id=o.get("orderId"),
            symbol=inst.get("symbol") or (fallback.symbol if fallback else ""),
            side=Side.BUY if str(side).upper() == "BUY" else Side.SELL,
            qty=num(o.get("quantity"), fallback.qty if fallback else 0.0),
            status=map_status(o.get("status") or "PENDING"), filled_qty=num(o.get("filledQuantity")),
            avg_fill_price=num(o.get("averagePrice"), None), limit_price=num(o.get("limitPrice"), None), raw=o,
        )

    def place_order(self, order: OrderRequest) -> OrderResult:
        self.connect()
        order_id = uuid_from(order.client_order_id) if order.client_order_id else uuid_from(f"{time.time_ns()}")
        body: dict[str, Any] = {
            "orderId": order_id,
            "instrument": {"symbol": order.symbol, "type": "EQUITY"},
            "orderSide": order.side.value,
            "orderType": "LIMIT" if order.order_type == OrderType.LIMIT else "MARKET",
            "expiration": {"timeInForce": "DAY" if order.time_in_force.value == "DAY" else "GTD"},
            "quantity": f"{order.qty:g}",
        }
        if order.order_type == OrderType.LIMIT:
            if order.limit_price is None:
                raise BrokerError("limit order without a limit price")
            body["limitPrice"] = fmt_price(order.limit_price)
        if order.extended_hours:
            body["equityMarketSession"] = "EXTENDED"
        resp = self.client.post(f"/userapigateway/trading/{self.account_id}/order", json=body) or {}
        rid = resp.get("orderId", order_id)
        return OrderResult(order_id=rid, client_order_id=order.client_order_id, symbol=order.symbol, side=order.side,
                           qty=order.qty, status=map_status("PENDING"), limit_price=order.limit_price, raw=resp)

    def get_order(self, order_id: str) -> OrderResult | None:
        self.connect()
        o = self.client.get(f"/userapigateway/trading/{self.account_id}/order/{order_id}")
        return self._result(o) if o else None

    def cancel_order(self, order_id: str) -> None:
        self.connect()
        self.client.delete(f"/userapigateway/trading/{self.account_id}/order/{order_id}")

    def open_orders(self) -> list[OrderResult]:
        orders = as_list(self._portfolio().get("orders"))
        live = [o for o in orders if map_status(o.get("status")).value in ("NEW", "PENDING", "PARTIALLY_FILLED")]
        return [self._result(o) for o in live]
