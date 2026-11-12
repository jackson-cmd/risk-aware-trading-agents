"""Interactive Brokers through TWS or IB Gateway, using ib_async.

Start TWS / IB Gateway and enable the API. Paper accounts listen on 7497 (TWS) or
4002 (Gateway); live ones on 7496 / 4001. Options: host, port, client_id,
account, market_data_type (1 live, 3 delayed; delayed needs no subscription).
"""

from __future__ import annotations

import math
from typing import Any

from ..types import AccountSnapshot, OrderRequest, OrderResult, OrderType, Position, Quote, Side
from .base import Broker, BrokerError, MissingDependency, env_first, map_status

PAPER_PORTS = {7497, 4002}


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or v <= 0 else v


class IBKRBroker(Broker):
    name = "ibkr"

    def __init__(self, host: str = "127.0.0.1", port: int | None = None, client_id: int = 17, account: str = "",
                 market_data_type: int = 3, readonly: bool = False, **options: Any):
        super().__init__(**options)
        self.host = host
        self.port = int(port or env_first("IBKR_PORT") or 7497)
        self.client_id = int(client_id)
        self.account_code = account or env_first("IBKR_ACCOUNT") or ""
        self.market_data_type = market_data_type
        self.readonly = readonly
        self.ib = None

    @property
    def is_paper(self) -> bool:
        return self.port in PAPER_PORTS or self.account_code.startswith("DU")

    def connect(self) -> None:
        try:
            from ib_async import IB
        except ImportError as exc:
            raise MissingDependency("Interactive Brokers", "ib_async", "ibkr") from exc
        self.ib = IB()
        try:
            self.ib.connect(self.host, self.port, clientId=self.client_id, readonly=self.readonly,
                            account=self.account_code)
        except Exception as exc:
            raise BrokerError(f"cannot reach TWS/IB Gateway at {self.host}:{self.port}: {exc}") from exc
        self.ib.reqMarketDataType(self.market_data_type)
        if not self.account_code:
            accts = self.ib.managedAccounts()
            self.account_code = accts[0] if accts else ""

    def close(self) -> None:
        if self.ib is not None:
            self.ib.disconnect()

    def _ib(self):
        if self.ib is None:
            self.connect()
        return self.ib

    @staticmethod
    def _contract(symbol: str):
        from ib_async import Stock

        return Stock(symbol.replace("-", " ").replace(".", " "), "SMART", "USD")

    def account(self) -> AccountSnapshot:
        vals = self._ib().accountSummary(self.account_code)
        table: dict[str, float] = {}
        for v in vals:
            if v.currency in ("USD", "BASE", "") and v.tag in ("NetLiquidation", "TotalCashValue", "BuyingPower",
                                                               "AvailableFunds"):
                try:
                    table.setdefault(v.tag, float(v.value))
                except ValueError:
                    continue
        return AccountSnapshot(broker=self.name, account_id=self.account_code, equity=table.get("NetLiquidation", 0.0),
                               cash=table.get("TotalCashValue", 0.0),
                               buying_power=table.get("AvailableFunds", table.get("BuyingPower", 0.0)),
                               is_paper=self.is_paper)

    def positions(self) -> list[Position]:
        out = []
        for item in self._ib().portfolio(self.account_code):
            c = item.contract
            if getattr(c, "secType", "STK") != "STK" or not item.position:
                continue
            out.append(Position(symbol=c.symbol.replace(" ", "-"), qty=float(item.position),
                                avg_cost=float(item.averageCost), market_price=_f(item.marketPrice)))
        return out

    def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        if not symbols:
            return {}
        ib = self._ib()
        contracts = [self._contract(s) for s in symbols]
        ib.qualifyContracts(*contracts)
        out = {}
        for s, t in zip(symbols, ib.reqTickers(*contracts), strict=False):
            last = _f(t.last) or _f(t.close) or _f(t.marketPrice())
            out[s] = Quote(symbol=s, bid=_f(t.bid), ask=_f(t.ask), last=last)
        return out

    def _result(self, trade) -> OrderResult:
        o, st = trade.order, trade.orderStatus
        return OrderResult(order_id=str(o.orderId), client_order_id=o.orderRef or None,
                           symbol=trade.contract.symbol.replace(" ", "-"),
                           side=Side.BUY if o.action == "BUY" else Side.SELL, qty=float(o.totalQuantity),
                           status=map_status(st.status), filled_qty=float(st.filled or 0),
                           avg_fill_price=_f(st.avgFillPrice), limit_price=_f(getattr(o, "lmtPrice", None)),
                           raw={"permId": o.permId, "status": st.status})

    def place_order(self, order: OrderRequest) -> OrderResult:
        from ib_async import LimitOrder, MarketOrder

        ib = self._ib()
        contract = self._contract(order.symbol)
        ib.qualifyContracts(contract)
        action = "BUY" if order.side == Side.BUY else "SELL"
        if order.order_type == OrderType.LIMIT:
            if order.limit_price is None:
                raise BrokerError("limit order without a limit price")
            ib_order = LimitOrder(action, order.qty, round(order.limit_price, 2))
        else:
            ib_order = MarketOrder(action, order.qty)
        ib_order.tif = order.time_in_force.value
        ib_order.outsideRth = order.extended_hours
        if order.client_order_id:
            ib_order.orderRef = order.client_order_id
        if self.account_code:
            ib_order.account = self.account_code
        trade = ib.placeOrder(contract, ib_order)
        ib.sleep(1.0)
        return self._result(trade)

    def cancel_order(self, order_id: str) -> None:
        ib = self._ib()
        for trade in ib.openTrades():
            if str(trade.order.orderId) == str(order_id):
                ib.cancelOrder(trade.order)
                return
        raise BrokerError(f"open order {order_id} not found")

    def open_orders(self) -> list[OrderResult]:
        return [self._result(t) for t in self._ib().openTrades()]
