"""Futu / moomoo OpenAPI through the FutuOpenD gateway (futu-api). US stocks.

Run FutuOpenD (or moomoo OpenD) locally and log in there. Options: host, port
(11111), simulate (default true, the paper account), security_firm (FUTUINC for
moomoo US, FUTUSECURITIES for Futu HK, FUTUSG, FUTUAU, ...), acc_id. Real trading
needs the trade password in FUTU_TRADE_PASSWORD (only read when simulate is false).
"""

from __future__ import annotations

from typing import Any

from ..types import AccountSnapshot, OrderRequest, OrderResult, OrderType, Position, Quote, Side
from .base import Broker, BrokerError, MissingCredentials, MissingDependency, env_first, map_status
from .rest import num


def _code(symbol: str) -> str:
    return "US." + symbol.replace("-", ".")


def _sym(code: str) -> str:
    return code.split(".", 1)[1].replace(".", "-") if "." in code else code


class FutuBroker(Broker):
    name = "futu"

    def __init__(self, host: str = "127.0.0.1", port: int = 11111, simulate: bool = True,
                 security_firm: str = "FUTUINC", acc_id: int = 0, **options: Any):
        super().__init__(**options)
        self.host, self.port = host, int(port)
        self.simulate = bool(simulate)
        self.security_firm = security_firm
        self.acc_id = int(acc_id)
        self.trd = None
        self.futu = None

    @property
    def is_paper(self) -> bool:
        return self.simulate

    def connect(self) -> None:
        try:
            import futu
        except ImportError as exc:
            raise MissingDependency("Futu/moomoo", "futu-api", "futu") from exc
        self.futu = futu
        firm = getattr(futu.SecurityFirm, self.security_firm, futu.SecurityFirm.FUTUINC)
        self.trd = futu.OpenSecTradeContext(filter_trdmarket=futu.TrdMarket.US, host=self.host, port=self.port,
                                            security_firm=firm)
        if not self.simulate:
            pwd = env_first("FUTU_TRADE_PASSWORD")
            if not pwd:
                raise MissingCredentials("Futu (real trading)", ["FUTU_TRADE_PASSWORD"])
            ret, data = self.trd.unlock_trade(pwd)
            if ret != futu.RET_OK:
                raise BrokerError(f"Futu unlock failed: {data}")

    def close(self) -> None:
        if self.trd is not None:
            self.trd.close()

    def _env(self):
        if self.trd is None:
            self.connect()
        return self.futu.TrdEnv.SIMULATE if self.simulate else self.futu.TrdEnv.REAL

    def _check(self, ret, data):
        if ret != self.futu.RET_OK:
            raise BrokerError(f"Futu: {data}")
        return data

    def account(self) -> AccountSnapshot:
        env = self._env()
        df = self._check(*self.trd.accinfo_query(trd_env=env, acc_id=self.acc_id, currency="USD"))
        row = df.iloc[0]
        return AccountSnapshot(broker=self.name, account_id=str(self.acc_id or row.get("acc_id", "")),
                               equity=num(row.get("total_assets")), cash=num(row.get("cash")),
                               buying_power=num(row.get("power")), is_paper=self.is_paper)

    def positions(self) -> list[Position]:
        env = self._env()
        df = self._check(*self.trd.position_list_query(trd_env=env, acc_id=self.acc_id))
        out = []
        for _, r in df.iterrows():
            if not str(r["code"]).startswith("US.") or num(r.get("qty")) == 0:
                continue
            out.append(Position(symbol=_sym(r["code"]), qty=num(r.get("qty")), avg_cost=num(r.get("cost_price")),
                                market_price=num(r.get("nominal_price"), None)))
        return out

    def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        if not symbols:
            return {}
        self._env()
        q = self.futu.OpenQuoteContext(host=self.host, port=self.port)
        try:
            df = self._check(*q.get_market_snapshot([_code(s) for s in symbols]))
        finally:
            q.close()
        out = {}
        for _, r in df.iterrows():
            s = _sym(r["code"])
            out[s] = Quote(symbol=s, bid=num(r.get("bid_price"), None), ask=num(r.get("ask_price"), None),
                           last=num(r.get("last_price"), None))
        return out

    def place_order(self, order: OrderRequest) -> OrderResult:
        env = self._env()
        f = self.futu
        side = f.TrdSide.BUY if order.side == Side.BUY else f.TrdSide.SELL
        if order.order_type == OrderType.LIMIT:
            if order.limit_price is None:
                raise BrokerError("limit order without a limit price")
            otype, price = f.OrderType.NORMAL, round(order.limit_price, 2)
        else:
            otype = f.OrderType.MARKET
            q = self.quote(order.symbol)
            price = (q.price if q else 0.0) or 0.0
        df = self._check(*self.trd.place_order(price=price, qty=order.qty, code=_code(order.symbol), trd_side=side,
                                               order_type=otype, trd_env=env, acc_id=self.acc_id,
                                               remark=(order.client_order_id or "")[:64] or None))
        row = df.iloc[0]
        return OrderResult(order_id=str(row["order_id"]), client_order_id=order.client_order_id, symbol=order.symbol,
                           side=order.side, qty=order.qty, status=map_status(str(row.get("order_status", "submitting"))),
                           limit_price=order.limit_price)

    def cancel_order(self, order_id: str) -> None:
        env = self._env()
        self._check(*self.trd.modify_order(self.futu.ModifyOrderOp.CANCEL, order_id, 0, 0, trd_env=env,
                                           acc_id=self.acc_id))

    def open_orders(self) -> list[OrderResult]:
        env = self._env()
        df = self._check(*self.trd.order_list_query(trd_env=env, acc_id=self.acc_id))
        out = []
        for _, r in df.iterrows():
            st = map_status(str(r.get("order_status", "")))
            if st.value not in ("NEW", "PENDING", "PARTIALLY_FILLED"):
                continue
            out.append(OrderResult(order_id=str(r["order_id"]), client_order_id=r.get("remark") or None,
                                   symbol=_sym(r["code"]), side=Side.BUY if "BUY" in str(r["trd_side"]) else Side.SELL,
                                   qty=num(r.get("qty")), status=st, filled_qty=num(r.get("dealt_qty")),
                                   avg_fill_price=num(r.get("dealt_avg_price"), None), limit_price=num(r.get("price"), None)))
        return out
