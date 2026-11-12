"""tastytrade through the tastytrade SDK (OAuth). Options: sandbox (cert environment), account_number.

Environment variables: TASTYTRADE_CLIENT_SECRET (the OAuth app's provider secret) and
TASTYTRADE_REFRESH_TOKEN, plus optional TASTYTRADE_ACCOUNT_NUMBER.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..types import AccountSnapshot, OrderRequest, OrderResult, OrderType, Position, Quote, Side, TimeInForce
from .base import Broker, BrokerError, MissingCredentials, MissingDependency, env_first, map_status
from .rest import num


class TastytradeBroker(Broker):
    name = "tastytrade"

    def __init__(self, sandbox: bool = False, account_number: str | None = None, **options: Any):
        super().__init__(**options)
        self.sandbox = bool(sandbox)
        self.account_number = account_number or env_first("TASTYTRADE_ACCOUNT_NUMBER")
        self.session = None
        self.acct = None

    @property
    def is_paper(self) -> bool:
        return self.sandbox

    def connect(self) -> None:
        try:
            from tastytrade import Account, Session
        except ImportError as exc:
            raise MissingDependency("tastytrade", "tastytrade", "tastytrade") from exc
        secret = env_first("TASTYTRADE_CLIENT_SECRET", "TASTYTRADE_PROVIDER_SECRET")
        refresh = env_first("TASTYTRADE_REFRESH_TOKEN")
        if not secret or not refresh:
            raise MissingCredentials("tastytrade", ["TASTYTRADE_CLIENT_SECRET", "TASTYTRADE_REFRESH_TOKEN"])
        self.session = Session(provider_secret=secret, refresh_token=refresh, is_test=self.sandbox)
        accounts = Account.get(self.session)
        accounts = accounts if isinstance(accounts, list) else [accounts]
        if not accounts:
            raise BrokerError("tastytrade: no accounts")
        self.acct = next((a for a in accounts if a.account_number == self.account_number), accounts[0])
        self.account_number = self.acct.account_number

    def _ready(self) -> None:
        if self.session is None:
            self.connect()

    def account(self) -> AccountSnapshot:
        self._ready()
        b = self.acct.get_balances(self.session)
        return AccountSnapshot(broker=self.name, account_id=str(self.account_number),
                               equity=num(b.net_liquidating_value), cash=num(b.cash_balance),
                               buying_power=num(b.equity_buying_power), is_paper=self.is_paper)

    def positions(self) -> list[Position]:
        self._ready()
        out = []
        for p in self.acct.get_positions(self.session):
            if str(getattr(p.instrument_type, "value", p.instrument_type)) not in ("Equity", "EQUITY"):
                continue
            qty = num(p.quantity) * (-1 if str(p.quantity_direction).lower().startswith("short") else 1)
            mark = num(getattr(p, "mark_price", None), None) or num(getattr(p, "close_price", None), None)
            out.append(Position(symbol=p.symbol.replace("/", "-"), qty=qty, avg_cost=num(p.average_open_price),
                                market_price=mark))
        return out

    def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        self._ready()
        from tastytrade.market_data import get_market_data
        from tastytrade.order import InstrumentType

        out = {}
        for s in symbols:
            try:
                md = get_market_data(self.session, s.replace("-", "/"), InstrumentType.EQUITY)
            except Exception:
                continue
            out[s] = Quote(symbol=s, bid=num(md.bid, None), ask=num(md.ask, None),
                           last=num(md.last, None) or num(md.mark, None))
        return out

    def place_order(self, order: OrderRequest) -> OrderResult:
        self._ready()
        from tastytrade.instruments import Equity
        from tastytrade.order import LimitOrder, MarketOrder, OrderAction, OrderTimeInForce

        equity = Equity.get(self.session, order.symbol.replace("-", "/"))
        action = OrderAction.BUY_TO_OPEN if order.side == Side.BUY else OrderAction.SELL_TO_CLOSE
        leg = equity.build_leg(Decimal(str(order.qty)), action)
        tif = OrderTimeInForce.GTC if order.time_in_force == TimeInForce.GTC else OrderTimeInForce.DAY
        if order.order_type == OrderType.LIMIT:
            if order.limit_price is None:
                raise BrokerError("limit order without a limit price")
            px = Decimal(f"{order.limit_price:.2f}")
            # tastytrade convention: negative price = debit (buy), positive = credit (sell)
            tt_order = LimitOrder(time_in_force=tif, legs=[leg], price=-px if order.side == Side.BUY else px)
        else:
            tt_order = MarketOrder(time_in_force=tif, legs=[leg])
        resp = self.acct.place_order(self.session, tt_order, dry_run=False)
        placed = resp.order
        return OrderResult(order_id=str(placed.id), client_order_id=order.client_order_id, symbol=order.symbol,
                           side=order.side, qty=order.qty, status=map_status(str(getattr(placed.status, "value", placed.status))),
                           limit_price=order.limit_price)

    def cancel_order(self, order_id: str) -> None:
        self._ready()
        self.acct.delete_order(self.session, int(order_id))

    def open_orders(self) -> list[OrderResult]:
        self._ready()
        out = []
        for o in self.acct.get_live_orders(self.session):
            status = str(getattr(o.status, "value", o.status))
            if map_status(status).value not in ("NEW", "PENDING", "PARTIALLY_FILLED"):
                continue
            leg = o.legs[0] if o.legs else None
            action = str(getattr(leg.action, "value", "")) if leg else ""
            out.append(OrderResult(order_id=str(o.id), client_order_id=None, symbol=(o.underlying_symbol or "").replace("/", "-"),
                                   side=Side.BUY if action.startswith("Buy") else Side.SELL, qty=num(o.size),
                                   status=map_status(status), limit_price=abs(num(o.price, 0.0)) or None))
        return out
