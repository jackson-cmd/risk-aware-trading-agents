"""Alpaca (REST API v2). Paper trading is the default.

Environment variables (first match wins):
  key:    ALPACA_API_KEY, APCA_API_KEY_ID, ALPACA_PAPER_API_KEY
  secret: ALPACA_SECRET_KEY, APCA_API_SECRET_KEY, ALPACA_PAPER_SECRET_KEY
Options: paper (default true), key_env / secret_env to read other variable names,
data_feed ("iex" on the free plan, "sip" with a subscription).
"""

from __future__ import annotations

from typing import Any

from ..types import AccountSnapshot, OrderRequest, OrderResult, OrderType, Position, Quote, Side
from .base import Broker, BrokerError, MissingCredentials, env_first, fmt_price, map_status, parse_ts
from .rest import RestClient, num

PAPER_URL = "https://paper-api.alpaca.markets"
LIVE_URL = "https://api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"


class AlpacaBroker(Broker):
    name = "alpaca"
    supports_fractional = True

    def __init__(self, paper: bool = True, key_env: str | None = None, secret_env: str | None = None,
                 data_feed: str = "iex", base_url: str | None = None, **options: Any):
        super().__init__(**options)
        self.paper = bool(paper)
        key_names = [key_env] if key_env else (
            ["ALPACA_PAPER_API_KEY", "ALPACA_API_KEY", "APCA_API_KEY_ID"] if self.paper
            else ["ALPACA_LIVE_API_KEY", "ALPACA_API_KEY", "APCA_API_KEY_ID"])
        secret_names = [secret_env] if secret_env else (
            ["ALPACA_PAPER_SECRET_KEY", "ALPACA_SECRET_KEY", "APCA_API_SECRET_KEY"] if self.paper
            else ["ALPACA_LIVE_SECRET_KEY", "ALPACA_SECRET_KEY", "APCA_API_SECRET_KEY"])
        self.key = env_first(*[k for k in key_names if k])
        self.secret = env_first(*[k for k in secret_names if k])
        self.base_url = base_url or (PAPER_URL if self.paper else LIVE_URL)
        self.data_feed = data_feed
        self.client: RestClient | None = None
        self._key_names = key_names

    @property
    def is_paper(self) -> bool:
        return "paper-api" in self.base_url

    def connect(self) -> None:
        if not self.key or not self.secret:
            raise MissingCredentials("Alpaca", [self._key_names[0], self._key_names[0].replace("API_KEY", "SECRET_KEY")])
        if self.paper and self.key.startswith("AK"):
            raise BrokerError("paper=true but the key looks like a live key (AK...). Use a paper key (PK...).")
        self.client = RestClient(self.base_url, {"APCA-API-KEY-ID": self.key, "APCA-API-SECRET-KEY": self.secret,
                                                 "Accept": "application/json"})

    def _c(self) -> RestClient:
        if self.client is None:
            self.connect()
        assert self.client is not None
        return self.client

    def account(self) -> AccountSnapshot:
        a = self._c().get("/v2/account")
        return AccountSnapshot(broker=self.name, account_id=str(a.get("account_number") or a.get("id")),
                               equity=num(a.get("equity")), cash=num(a.get("cash")),
                               buying_power=num(a.get("buying_power")), currency=a.get("currency", "USD"),
                               is_paper=self.is_paper)

    def positions(self) -> list[Position]:
        return [Position(symbol=p["symbol"].replace(".", "-"), qty=num(p.get("qty")), avg_cost=num(p.get("avg_entry_price")),
                         market_price=num(p.get("current_price"), None))
                for p in self._c().get("/v2/positions") or []]

    def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        if not symbols:
            return {}
        c = self._c()
        params = {"symbols": ",".join(s.replace("-", ".") for s in symbols), "feed": self.data_feed}
        out: dict[str, Quote] = {}
        q = c.get("/v2/stocks/quotes/latest", params=params, base=DATA_URL) or {}
        t = c.get("/v2/stocks/trades/latest", params=params, base=DATA_URL) or {}
        for s in symbols:
            key = s.replace("-", ".")
            qq = (q.get("quotes") or {}).get(key) or {}
            tt = (t.get("trades") or {}).get(key) or {}
            bid, ask, last = num(qq.get("bp"), None), num(qq.get("ap"), None), num(tt.get("p"), None)
            if bid == 0:
                bid = None
            if ask == 0:
                ask = None
            if bid or ask or last:
                ts = tt.get("t") or qq.get("t")
                out[s] = Quote(symbol=s, bid=bid, ask=ask, last=last, timestamp=parse_ts(ts))
        return out

    def clock(self) -> dict[str, Any]:
        return self._c().get("/v2/clock")

    def _result(self, o: dict[str, Any]) -> OrderResult:
        return OrderResult(
            order_id=o["id"], client_order_id=o.get("client_order_id"), symbol=o.get("symbol", "").replace(".", "-"),
            side=Side.BUY if o.get("side") == "buy" else Side.SELL, qty=num(o.get("qty") or o.get("notional")),
            status=map_status(o.get("status")), filled_qty=num(o.get("filled_qty")),
            avg_fill_price=num(o.get("filled_avg_price"), None), limit_price=num(o.get("limit_price"), None),
            submitted_at=parse_ts(o.get("submitted_at")),
            raw=o,
        )

    def place_order(self, order: OrderRequest) -> OrderResult:
        body: dict[str, Any] = {
            "symbol": order.symbol.replace("-", "."),
            "qty": f"{order.qty:g}",
            "side": "buy" if order.side == Side.BUY else "sell",
            "type": "limit" if order.order_type == OrderType.LIMIT else "market",
            "time_in_force": order.time_in_force.value.lower(),
        }
        if order.order_type == OrderType.LIMIT:
            if order.limit_price is None:
                raise BrokerError("limit order without a limit price")
            body["limit_price"] = fmt_price(order.limit_price)
        if order.client_order_id:
            body["client_order_id"] = order.client_order_id[:48]
        if order.extended_hours:
            body["extended_hours"] = True
        return self._result(self._c().post("/v2/orders", json=body))

    def cancel_order(self, order_id: str) -> None:
        self._c().delete(f"/v2/orders/{order_id}")

    def get_order(self, order_id: str) -> OrderResult | None:
        return self._result(self._c().get(f"/v2/orders/{order_id}"))

    def open_orders(self) -> list[OrderResult]:
        return [self._result(o) for o in self._c().get("/v2/orders", params={"status": "open", "limit": 500}) or []]
