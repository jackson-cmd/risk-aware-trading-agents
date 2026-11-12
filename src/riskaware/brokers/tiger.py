"""Tiger Brokers OpenAPI (tigeropen). US stocks.

Download tiger_openapi_config.properties from the Tiger developer page and point
``props_path`` at its folder (default ~/.riskaware/tiger). Tiger paper accounts
have their own account id; set ``paper: true`` when you use one so the safety
switches treat it as paper.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import RISKAWARE_HOME
from ..types import AccountSnapshot, OrderRequest, OrderResult, OrderType, Position, Quote, Side
from .base import Broker, BrokerError, MissingDependency, map_status
from .rest import num


class TigerBroker(Broker):
    name = "tiger"

    def __init__(self, props_path: str | None = None, account: str | None = None, paper: bool = True, **options: Any):
        super().__init__(**options)
        self.props_path = Path(props_path).expanduser() if props_path else RISKAWARE_HOME / "tiger"
        self.account_id = account
        self.paper = bool(paper)
        self.trade = None
        self.quote_client = None

    @property
    def is_paper(self) -> bool:
        return self.paper

    def connect(self) -> None:
        try:
            from tigeropen.quote.quote_client import QuoteClient
            from tigeropen.tiger_open_config import TigerOpenClientConfig
            from tigeropen.trade.trade_client import TradeClient
        except ImportError as exc:
            raise MissingDependency("Tiger Brokers", "tigeropen", "tiger") from exc
        if not self.props_path.exists():
            raise BrokerError(f"Tiger config not found: {self.props_path} (download tiger_openapi_config.properties)")
        cfg = TigerOpenClientConfig(props_path=str(self.props_path))
        if self.account_id:
            cfg.account = self.account_id
        self.account_id = cfg.account
        self.trade = TradeClient(cfg)
        self.quote_client = QuoteClient(cfg)

    def _t(self):
        if self.trade is None:
            self.connect()
        return self.trade

    def account(self) -> AccountSnapshot:
        t = self._t()
        assets = t.get_prime_assets(account=self.account_id)
        seg = None
        if assets is not None:
            segs = getattr(assets, "segments", {}) or {}
            seg = segs.get("S") if isinstance(segs, dict) else None
        if seg is None:
            raise BrokerError("Tiger: no securities segment in the account assets")
        return AccountSnapshot(broker=self.name, account_id=str(self.account_id), equity=num(seg.net_liquidation),
                               cash=num(getattr(seg, "cash_balance", None), num(getattr(seg, "available_cash", None))),
                               buying_power=num(seg.buying_power), is_paper=self.is_paper)

    def positions(self) -> list[Position]:
        from tigeropen.common.consts import Market, SecurityType

        out = []
        for p in self._t().get_positions(account=self.account_id, sec_type=SecurityType.STK, market=Market.US) or []:
            if not p.quantity:
                continue
            out.append(Position(symbol=p.contract.symbol.replace(".", "-"), qty=num(p.quantity),
                                avg_cost=num(p.average_cost), market_price=num(p.market_price, None)))
        return out

    def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        if not symbols:
            return {}
        self._t()
        briefs = self.quote_client.get_briefs([s.replace("-", ".") for s in symbols], include_ask_bid=True)
        out = {}
        for b in briefs or []:
            s = b.symbol.replace(".", "-")
            out[s] = Quote(symbol=s, bid=num(b.bid_price, None), ask=num(b.ask_price, None),
                           last=num(b.latest_price, None))
        return out

    def _result(self, o) -> OrderResult:
        return OrderResult(order_id=str(o.id), client_order_id=None, symbol=o.contract.symbol.replace(".", "-"),
                           side=Side.BUY if str(o.action).upper() == "BUY" else Side.SELL, qty=num(o.quantity),
                           status=map_status(getattr(getattr(o, "status", None), "name", str(getattr(o, "status", "")))),
                           filled_qty=num(o.filled), avg_fill_price=num(o.avg_fill_price, None),
                           limit_price=num(o.limit_price, None))

    def place_order(self, order: OrderRequest) -> OrderResult:
        from tigeropen.common.util.contract_utils import stock_contract
        from tigeropen.common.util.order_utils import limit_order, market_order

        t = self._t()
        contract = stock_contract(order.symbol.replace("-", "."), currency="USD")
        action = "BUY" if order.side == Side.BUY else "SELL"
        qty = int(order.qty)
        if order.order_type == OrderType.LIMIT:
            if order.limit_price is None:
                raise BrokerError("limit order without a limit price")
            tg = limit_order(self.account_id, contract, action, qty, round(order.limit_price, 2),
                             time_in_force=order.time_in_force.value)
        else:
            tg = market_order(self.account_id, contract, action, qty, time_in_force=order.time_in_force.value)
        order_id = t.place_order(tg)
        return OrderResult(order_id=str(order_id or tg.id), client_order_id=order.client_order_id, symbol=order.symbol,
                           side=order.side, qty=qty, status=map_status("pending"), limit_price=order.limit_price)

    def cancel_order(self, order_id: str) -> None:
        self._t().cancel_order(account=self.account_id, id=int(order_id))

    def open_orders(self) -> list[OrderResult]:
        from tigeropen.common.consts import Market

        return [self._result(o) for o in self._t().get_open_orders(account=self.account_id, market=Market.US) or []]
