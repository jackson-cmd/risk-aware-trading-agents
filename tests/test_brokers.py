import json

import pytest

from riskaware.brokers import BROKERS, BrokerError, make_broker
from riskaware.brokers.base import client_order_id, map_status, parse_ts, uuid_from
from riskaware.brokers.paper import PaperBroker
from riskaware.types import OrderRequest, OrderStatus, OrderType, Side
from tests.conftest import fake_quotes


@pytest.fixture
def paper(tmp_path):
    b = PaperBroker(account_file=tmp_path / "acct.json", starting_cash=10_000,
                    quote_fn=fake_quotes({"AAA": 100.0, "BBB": 50.0}))
    b.connect()
    return b


def test_paper_fill_and_persistence(paper, tmp_path):
    r = paper.place_order(OrderRequest("AAA", Side.BUY, 10, OrderType.LIMIT, 101.0, client_order_id="c1"))
    assert r.status == OrderStatus.FILLED and r.avg_fill_price <= 101.0
    assert paper.account().cash == pytest.approx(10_000 - 10 * r.avg_fill_price)
    again = PaperBroker(account_file=tmp_path / "acct.json", quote_fn=fake_quotes({"AAA": 100.0}))
    again.connect()
    assert again.positions()[0].qty == 10


def test_paper_resting_cancel_and_duplicates(paper):
    r = paper.place_order(OrderRequest("AAA", Side.BUY, 5, OrderType.LIMIT, 90.0, client_order_id="c2"))
    assert r.status == OrderStatus.NEW and len(paper.open_orders()) == 1
    paper.cancel_order(r.order_id)
    assert paper.open_orders() == []
    with pytest.raises(BrokerError):
        paper.place_order(OrderRequest("AAA", Side.BUY, 1, OrderType.LIMIT, 90.0, client_order_id="c2"))


def test_paper_rejects_oversell_and_overspend(paper):
    r = paper.place_order(OrderRequest("BBB", Side.SELL, 1, OrderType.LIMIT, 49.0))
    assert r.status == OrderStatus.REJECTED
    r2 = paper.place_order(OrderRequest("AAA", Side.BUY, 1000, OrderType.MARKET))
    assert r2.status == OrderStatus.REJECTED and "cash" in r2.message


def test_client_order_ids_are_deterministic():
    from datetime import date

    a = client_order_id(date(2026, 1, 5), "BRK-B", Side.BUY, "entry1")
    assert a == client_order_id(date(2026, 1, 5), "BRK-B", Side.BUY, "entry1")
    assert a != client_order_id(date(2026, 1, 6), "BRK-B", Side.BUY, "entry1")
    assert len(a) <= 48 and uuid_from("x") == uuid_from("x")


def test_status_and_timestamp_parsing():
    assert map_status("partially_filled") == OrderStatus.PARTIALLY_FILLED
    assert map_status("Filled") == OrderStatus.FILLED and map_status("CANCELLED") == OrderStatus.CANCELED
    assert parse_ts("2026-11-11T23:15:25.1137638Z").microsecond == 113763
    assert parse_ts("2026-11-11T18:15:25.1-05:00").microsecond == 100000
    assert parse_ts("2026-11-11T23:15:25Z").tzinfo is not None


def test_registry_builds_every_rest_adapter_without_network():
    for name in ("paper", "alpaca", "public", "tradier"):
        assert make_broker(name).name == name
    assert set(BROKERS) >= {"ibkr", "schwab", "tastytrade", "futu", "tiger", "longbridge"}
    with pytest.raises(BrokerError):
        make_broker("nope")


class FakeResponse:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)
        self.content = self.text.encode()

    def json(self):
        return self._payload


def install_fake_http(monkeypatch, routes):
    calls = []

    def fake_request(self, method, url, params=None, json=None, data=None, timeout=None):
        calls.append({"method": method, "url": url, "params": params, "json": json, "data": data,
                      "headers": dict(self.headers)})
        for (m, frag), resp in routes.items():
            if m == method and frag in url:
                return FakeResponse(*resp) if isinstance(resp, tuple) else FakeResponse(200, resp)
        return FakeResponse(404, {"message": "no route"})

    monkeypatch.setattr("requests.Session.request", fake_request)
    return calls


def test_alpaca_adapter(monkeypatch):
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "PKTEST")
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", "secret")
    order = {"id": "o1", "client_order_id": "c1", "symbol": "BRK.B", "side": "buy", "qty": "2", "status": "accepted",
             "filled_qty": "0", "limit_price": "450.10", "submitted_at": "2026-11-11T23:15:25.1137638Z"}
    calls = install_fake_http(monkeypatch, {
        ("GET", "/v2/account"): {"account_number": "PA1", "equity": "1000", "cash": "400", "buying_power": "800",
                                 "currency": "USD"},
        ("GET", "/v2/positions"): [{"symbol": "BRK.B", "qty": "3", "avg_entry_price": "400", "current_price": "450"}],
        ("POST", "/v2/orders"): order,
        ("GET", "/quotes/latest"): {"quotes": {"BRK.B": {"bp": 449.9, "ap": 450.2, "t": "2026-11-11T20:59:59Z"}}},
        ("GET", "/trades/latest"): {"trades": {"BRK.B": {"p": 450.0, "t": "2026-11-11T20:59:59Z"}}},
    })
    b = make_broker("alpaca", paper=True)
    assert b.is_paper
    assert b.account().equity == 1000.0
    assert b.positions()[0].symbol == "BRK-B"
    q = b.quotes(["BRK-B"])["BRK-B"]
    assert q.bid == 449.9 and q.last == 450.0
    r = b.place_order(OrderRequest("BRK-B", Side.BUY, 2, OrderType.LIMIT, 450.1, client_order_id="c1"))
    sent = [c for c in calls if c["method"] == "POST"][0]
    assert sent["json"]["symbol"] == "BRK.B" and sent["json"]["limit_price"] == "450.10"
    assert sent["headers"]["APCA-API-KEY-ID"] == "PKTEST" and "paper-api" in sent["url"]
    assert r.status == OrderStatus.NEW and r.symbol == "BRK-B"


def test_alpaca_refuses_live_key_in_paper_mode(monkeypatch):
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "AKLIVE")
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", "s")
    with pytest.raises(BrokerError):
        make_broker("alpaca", paper=True).connect()


def test_public_adapter(monkeypatch):
    monkeypatch.setenv("PUBLIC_API_SECRET", "sek")
    calls = install_fake_http(monkeypatch, {
        ("POST", "/personal/access-tokens"): {"accessToken": "tok"},
        ("GET", "/trading/account"): {"accounts": [{"accountId": "A1", "accountType": "BROKERAGE"}]},
        ("GET", "/portfolio/v2"): {"buyingPower": {"cashOnlyBuyingPower": "500", "buyingPower": "500"},
                                   "equity": [{"type": "CASH", "value": "500"}, {"type": "STOCK", "value": "1500"}],
                                   "positions": [{"instrument": {"symbol": "AAPL", "type": "EQUITY"}, "quantity": "5",
                                                  "lastPrice": {"lastPrice": "300"},
                                                  "costBasis": {"unitCost": "250"}}],
                                   "orders": []},
        ("POST", "/quotes"): {"quotes": [{"instrument": {"symbol": "AAPL", "type": "EQUITY"}, "outcome": "SUCCESS",
                                          "last": "300.5", "bid": "300.4", "ask": "300.6"}]},
        ("POST", "/order"): {"orderId": "ignored-echo"},
    })
    b = make_broker("public")
    assert not b.is_paper
    a = b.account()
    assert a.equity == 2000.0 and a.cash == 500.0 and b.account_id == "A1"
    assert b.positions()[0].avg_cost == 250.0
    assert b.quotes(["AAPL"])["AAPL"].ask == 300.6
    b.place_order(OrderRequest("AAPL", Side.BUY, 1, OrderType.LIMIT, 300.6, client_order_id="c9"))
    body = [c for c in calls if c["method"] == "POST" and c["url"].endswith("/order")][0]["json"]
    assert body["orderSide"] == "BUY" and body["orderType"] == "LIMIT" and body["limitPrice"] == "300.60"
    assert body["instrument"] == {"symbol": "AAPL", "type": "EQUITY"} and body["expiration"] == {"timeInForce": "DAY"}
    assert body["orderId"] == uuid_from("c9")  # deterministic: a resend is de-duplicated by Public
    assert any(c["headers"].get("Authorization") == "Bearer tok" for c in calls if "portfolio" in c["url"])


def test_tradier_adapter(monkeypatch):
    monkeypatch.setenv("TRADIER_SANDBOX_TOKEN", "t")
    monkeypatch.setenv("TRADIER_ACCOUNT_ID", "VA1")
    calls = install_fake_http(monkeypatch, {
        ("GET", "/balances"): {"balances": {"total_equity": 5000, "total_cash": 1000, "margin": {"stock_buying_power": 2000}}},
        ("GET", "/positions"): {"positions": {"position": {"symbol": "MSFT", "quantity": 2, "cost_basis": 800}}},
        ("GET", "/markets/quotes"): {"quotes": {"quote": {"symbol": "MSFT", "last": 410, "bid": 409.9, "ask": 410.1}}},
        ("POST", "/orders"): {"order": {"id": 77, "status": "ok"}},
        ("GET", "/orders"): {"orders": "null"},
    })
    b = make_broker("tradier", sandbox=True)
    assert b.is_paper and b.account().buying_power == 2000
    p = b.positions()[0]
    assert p.avg_cost == 400 and p.market_price == 410
    r = b.place_order(OrderRequest("MSFT", Side.SELL, 1, OrderType.LIMIT, 409.0, client_order_id="rta_x.1"))
    form = [c for c in calls if c["method"] == "POST"][0]["data"]
    assert form["side"] == "sell" and form["price"] == "409.00" and form["tag"] == "rta-x-1"
    assert r.order_id == "77" and b.open_orders() == []
