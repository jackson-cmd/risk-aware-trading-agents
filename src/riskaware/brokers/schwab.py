"""Charles Schwab Trader API through schwab-py. Live accounts only (Schwab has no paper API).

Create an app at developer.schwab.com, then set SCHWAB_API_KEY, SCHWAB_APP_SECRET and
optionally SCHWAB_CALLBACK_URL (default https://127.0.0.1:8182). The first connect
opens a browser for the OAuth login and stores a token file (default
~/.riskaware/schwab_token.json); tokens are refreshed automatically after that.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import RISKAWARE_HOME
from ..types import AccountSnapshot, OrderRequest, OrderResult, OrderType, Position, Quote, Side, TimeInForce
from .base import Broker, BrokerError, MissingCredentials, MissingDependency, env_first, map_status
from .rest import as_list, num


class SchwabBroker(Broker):
    name = "schwab"
    has_paper = False

    def __init__(self, token_path: str | None = None, account_number: str | None = None,
                 callback_url: str | None = None, interactive: bool = True, **options: Any):
        super().__init__(**options)
        self.token_path = Path(token_path).expanduser() if token_path else RISKAWARE_HOME / "schwab_token.json"
        self.account_number = account_number or env_first("SCHWAB_ACCOUNT_NUMBER")
        self.callback_url = callback_url or env_first("SCHWAB_CALLBACK_URL") or "https://127.0.0.1:8182"
        self.interactive = interactive
        self.client = None
        self.account_hash: str | None = None

    @property
    def is_paper(self) -> bool:
        return False

    def connect(self) -> None:
        try:
            from schwab import auth
        except ImportError as exc:
            raise MissingDependency("Schwab", "schwab-py", "schwab") from exc
        key, secret = env_first("SCHWAB_API_KEY", "SCHWAB_APP_KEY"), env_first("SCHWAB_APP_SECRET")
        if not key or not secret:
            raise MissingCredentials("Schwab", ["SCHWAB_API_KEY", "SCHWAB_APP_SECRET"])
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.client = auth.easy_client(key, secret, self.callback_url, str(self.token_path),
                                       interactive=self.interactive)
        r = self.client.get_account_numbers()
        r.raise_for_status()
        accounts = r.json()
        if not accounts:
            raise BrokerError("Schwab: no linked accounts")
        chosen = next((a for a in accounts if a.get("accountNumber") == self.account_number), accounts[0])
        self.account_number = chosen["accountNumber"]
        self.account_hash = chosen["hashValue"]

    def _c(self):
        if self.client is None:
            self.connect()
        return self.client

    def _account_json(self, positions: bool = False) -> dict[str, Any]:
        c = self._c()
        fields = [c.Account.Fields.POSITIONS] if positions else None
        r = c.get_account(self.account_hash, fields=fields)
        r.raise_for_status()
        return r.json().get("securitiesAccount", {})

    def account(self) -> AccountSnapshot:
        acct = self._account_json()
        bal = acct.get("currentBalances") or {}
        cash = num(bal.get("cashBalance"), num(bal.get("cashAvailableForTrading"), num(bal.get("availableFunds"))))
        return AccountSnapshot(broker=self.name, account_id=str(self.account_number),
                               equity=num(bal.get("liquidationValue"), num(bal.get("equity"))), cash=cash,
                               buying_power=num(bal.get("buyingPower"), num(bal.get("cashAvailableForTrading"), cash)),
                               is_paper=False)

    def positions(self) -> list[Position]:
        out = []
        for p in as_list(self._account_json(positions=True).get("positions")):
            inst = p.get("instrument") or {}
            if inst.get("assetType") not in ("EQUITY", "COLLECTIVE_INVESTMENT", "ETF", None):
                continue
            qty = num(p.get("longQuantity")) - num(p.get("shortQuantity"))
            if not qty:
                continue
            mv = num(p.get("marketValue"))
            out.append(Position(symbol=str(inst.get("symbol", "")).replace("/", "-").replace(".", "-"), qty=qty,
                                avg_cost=num(p.get("averagePrice")), market_price=mv / qty if qty else None))
        return out

    def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        if not symbols:
            return {}
        r = self._c().get_quotes([s.replace("-", "/") for s in symbols])
        r.raise_for_status()
        data = r.json()
        out = {}
        for s in symbols:
            q = (data.get(s.replace("-", "/")) or data.get(s) or {}).get("quote") or {}
            if q:
                out[s] = Quote(symbol=s, bid=num(q.get("bidPrice"), None), ask=num(q.get("askPrice"), None),
                               last=num(q.get("lastPrice"), None))
        return out

    def place_order(self, order: OrderRequest) -> OrderResult:
        from schwab.orders import equities as eq
        from schwab.orders.common import Duration
        from schwab.utils import Utils

        sym = order.symbol.replace("-", "/")
        qty = int(order.qty)
        if qty <= 0:
            raise BrokerError("Schwab equity orders need whole shares")
        if order.order_type == OrderType.LIMIT:
            if order.limit_price is None:
                raise BrokerError("limit order without a limit price")
            fn = eq.equity_buy_limit if order.side == Side.BUY else eq.equity_sell_limit
            spec = fn(sym, qty, f"{order.limit_price:.2f}")
        else:
            fn = eq.equity_buy_market if order.side == Side.BUY else eq.equity_sell_market
            spec = fn(sym, qty)
        if order.time_in_force == TimeInForce.GTC:
            spec.set_duration(Duration.GOOD_TILL_CANCEL)
        c = self._c()
        r = c.place_order(self.account_hash, spec)
        if r.status_code not in (200, 201):
            raise BrokerError(f"Schwab rejected the order: HTTP {r.status_code} {r.text[:200]}")
        order_id = Utils(c, self.account_hash).extract_order_id(r)
        return OrderResult(order_id=str(order_id), client_order_id=order.client_order_id, symbol=order.symbol,
                           side=order.side, qty=qty, status=map_status("pending"), limit_price=order.limit_price)

    def cancel_order(self, order_id: str) -> None:
        r = self._c().cancel_order(order_id, self.account_hash)
        if r.status_code not in (200, 204):
            raise BrokerError(f"Schwab cancel failed: HTTP {r.status_code}")

    def open_orders(self) -> list[OrderResult]:
        c = self._c()
        r = c.get_orders_for_account(self.account_hash, status=c.Order.Status.WORKING)
        r.raise_for_status()
        out = []
        for o in r.json() or []:
            leg = (o.get("orderLegCollection") or [{}])[0]
            out.append(OrderResult(
                order_id=str(o.get("orderId")), client_order_id=None,
                symbol=str((leg.get("instrument") or {}).get("symbol", "")).replace("/", "-"),
                side=Side.BUY if str(leg.get("instruction", "")).startswith("BUY") else Side.SELL,
                qty=num(o.get("quantity")), status=map_status(o.get("status")),
                filled_qty=num(o.get("filledQuantity")), limit_price=num(o.get("price"), None), raw=o))
        return out
