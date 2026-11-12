"""Broker adapters behind one interface. ``make_broker(name, **options)`` builds one."""

from __future__ import annotations

from typing import Any

from .base import Broker, BrokerError, MissingCredentials, MissingDependency, client_order_id

BROKERS: dict[str, dict[str, Any]] = {
    "paper": {"module": "paper", "cls": "PaperBroker", "label": "Built-in paper account (local file)",
              "paper": "always", "install": "", "env": []},
    "alpaca": {"module": "alpaca", "cls": "AlpacaBroker", "label": "Alpaca", "paper": "yes (default)",
               "install": "", "env": ["ALPACA_API_KEY", "ALPACA_SECRET_KEY"]},
    "public": {"module": "public", "cls": "PublicBroker", "label": "Public.com", "paper": "no (live only)",
               "install": "", "env": ["PUBLIC_API_SECRET"]},
    "tradier": {"module": "tradier", "cls": "TradierBroker", "label": "Tradier", "paper": "yes (sandbox)",
                "install": "", "env": ["TRADIER_TOKEN", "TRADIER_ACCOUNT_ID"]},
    "ibkr": {"module": "ibkr", "cls": "IBKRBroker", "label": "Interactive Brokers (TWS / IB Gateway)",
             "paper": "yes (paper account)", "install": "ibkr", "env": []},
    "schwab": {"module": "schwab", "cls": "SchwabBroker", "label": "Charles Schwab", "paper": "no",
               "install": "schwab", "env": ["SCHWAB_API_KEY", "SCHWAB_APP_SECRET"]},
    "tastytrade": {"module": "tastytrade", "cls": "TastytradeBroker", "label": "tastytrade",
                   "paper": "yes (cert sandbox)", "install": "tastytrade",
                   "env": ["TASTYTRADE_CLIENT_SECRET", "TASTYTRADE_REFRESH_TOKEN"]},
    "futu": {"module": "futu", "cls": "FutuBroker", "label": "Futu / moomoo (OpenD)", "paper": "yes (simulate)",
             "install": "futu", "env": []},
    "tiger": {"module": "tiger", "cls": "TigerBroker", "label": "Tiger Brokers", "paper": "yes (paper account)",
              "install": "tiger", "env": []},
    "longbridge": {"module": "longbridge", "cls": "LongbridgeBroker", "label": "Longbridge (LongPort)",
                   "paper": "yes (paper account)", "install": "longbridge",
                   "env": ["LONGPORT_APP_KEY", "LONGPORT_APP_SECRET", "LONGPORT_ACCESS_TOKEN"]},
}


def make_broker(name: str, **options: Any) -> Broker:
    import importlib

    key = name.lower().strip()
    if key in ("moomoo",):
        key = "futu"
    if key in ("ib", "interactive_brokers"):
        key = "ibkr"
    if key not in BROKERS:
        raise BrokerError(f"unknown broker '{name}'. Supported: {', '.join(BROKERS)}")
    spec = BROKERS[key]
    module = importlib.import_module(f".{spec['module']}", __name__)
    cls = getattr(module, spec["cls"])
    return cls(**options)


__all__ = ["BROKERS", "Broker", "BrokerError", "MissingCredentials", "MissingDependency", "client_order_id",
           "make_broker"]
