"""Longbridge (LongPort OpenAPI, ``longport`` package). US stocks.

Environment variables: LONGPORT_APP_KEY, LONGPORT_APP_SECRET, LONGPORT_ACCESS_TOKEN.
A Longbridge paper account uses its own access token; set ``paper: true`` when
the token belongs to the paper account so the safety switches treat it as paper.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..types import AccountSnapshot, OrderRequest, OrderResult, OrderType, Position, Quote, Side, TimeInForce
from .base import Broker, BrokerError, MissingCredentials, MissingDependency, env_first, map_status
from .rest import num


def _lp(symbol: str) -> str:
    return symbol.replace("-", ".") + ".US"


def _sym(lp_symbol: str) -> str:
    return lp_symbol[:-3].replace(".", "-") if lp_symbol.endswith(".US") else lp_symbol


class LongbridgeBroker(Broker):
    name = "longbridge"

    def __init__(self, paper: bool = True, **options: Any):
        super().__init__(**options)
        self.paper = bool(paper)
        self.trade = None
        self.quote_ctx = None
        self.lp = None

    @property
    def is_paper(self) -> bool:
        return self.paper

    def connect(self) -> None:
        try:
            from longport import openapi as lp
        except ImportError as exc:
            raise MissingDependency("Longbridge", "longport", "longbridge") from exc
        if not (env_first("LONGPORT_APP_KEY") and env_first("LONGPORT_APP_SECRET") and env_first("LONGPORT_ACCESS_TOKEN")):
            raise MissingCredentials("Longbridge", ["LONGPORT_APP_KEY", "LONGPORT_APP_SECRET", "LONGPORT_ACCESS_TOKEN"])
        self.lp = lp
        factory = getattr(lp.Config, "from_apikey_env", None) or lp.Config.from_env
        cfg = factory()
        self.trade = lp.TradeContext(cfg)
        self.quote_ctx = lp.QuoteContext(cfg)

    def _t(self):
        if self.trade is None:
            self.connect()
        return self.trade

    def account(self) -> AccountSnapshot:
        bals = self._t().account_balance(currency="USD") or []
        if not bals:
            raise BrokerError("Longbridge: empty account balance")
        b = bals[0]
        return AccountSnapshot(broker=self.name, account_id="longbridge", equity=num(getattr(b, "net_assets", None)),
                               cash=num(getattr(b, "total_cash", None)), buying_power=num(getattr(b, "buy_power", None)),
                               currency=str(getattr(b, "currency", "USD")), is_paper=self.is_paper)

    def positions(self) -> list[Position]:
        resp = self._t().stock_positions()
        out = []
        for ch in getattr(resp, "channels", []) or []:
            for p in ch.positions:
                if not str(p.symbol).endswith(".US") or not num(p.quantity):
                    continue
                out.append(Position(symbol=_sym(p.symbol), qty=num(p.quantity), avg_cost=num(p.cost_price)))
        if out:
            q = self.quotes([p.symbol for p in out])
            for p in out:
                if p.symbol in q:
                    p.market_price = q[p.symbol].price
        return out

    def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        if not symbols:
            return {}
        self._t()
        out = {}
        for q in self.quote_ctx.quote([_lp(s) for s in symbols]) or []:
            s = _sym(q.symbol)
            out[s] = Quote(symbol=s, last=num(q.last_done, None))
        return out

    def place_order(self, order: OrderRequest) -> OrderResult:
        t = self._t()
        lp = self.lp
        kwargs: dict[str, Any] = dict(
            symbol=_lp(order.symbol),
            order_type=lp.OrderType.LO if order.order_type == OrderType.LIMIT else lp.OrderType.MO,
            side=lp.OrderSide.Buy if order.side == Side.BUY else lp.OrderSide.Sell,
            submitted_quantity=Decimal(str(int(order.qty))),
            time_in_force=lp.TimeInForceType.GoodTilCanceled if order.time_in_force == TimeInForce.GTC
            else lp.TimeInForceType.Day,
            remark=(order.client_order_id or "")[:64] or None,
        )
        if order.order_type == OrderType.LIMIT:
            if order.limit_price is None:
                raise BrokerError("limit order without a limit price")
            kwargs["submitted_price"] = Decimal(f"{order.limit_price:.2f}")
        try:
            resp = t.submit_order(**kwargs)
        except TypeError:  # older SDKs take an int quantity
            kwargs["submitted_quantity"] = int(order.qty)
            resp = t.submit_order(**kwargs)
        return OrderResult(order_id=str(resp.order_id), client_order_id=order.client_order_id, symbol=order.symbol,
                           side=order.side, qty=order.qty, status=map_status("pending"), limit_price=order.limit_price)

    def cancel_order(self, order_id: str) -> None:
        self._t().cancel_order(order_id)

    def open_orders(self) -> list[OrderResult]:
        out = []
        for o in self._t().today_orders() or []:
            status = str(o.status).split(".")[-1]
            st = map_status(status)
            if st.value not in ("NEW", "PENDING", "PARTIALLY_FILLED"):
                continue
            out.append(OrderResult(order_id=str(o.order_id), client_order_id=getattr(o, "remark", None) or None,
                                   symbol=_sym(o.symbol), side=Side.BUY if "Buy" in str(o.side) else Side.SELL,
                                   qty=num(o.quantity), status=st, filled_qty=num(getattr(o, "executed_quantity", 0)),
                                   limit_price=num(getattr(o, "price", None), None)))
        return out
