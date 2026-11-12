"""The broker interface every adapter implements."""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Any

from ..types import AccountSnapshot, OrderRequest, OrderResult, OrderStatus, Position, Quote, Side


class BrokerError(RuntimeError):
    pass


class MissingDependency(BrokerError):
    def __init__(self, broker: str, package: str, extra: str):
        super().__init__(
            f"The {broker} adapter needs the '{package}' package: pip install \"risk-aware-trading-agents[{extra}]\""
        )


class MissingCredentials(BrokerError):
    def __init__(self, broker: str, variables: list[str]):
        super().__init__(f"{broker}: set {', '.join(variables)} in your environment or .env file")


class Broker(ABC):
    """Common interface. Quantities are shares; prices are in the account currency (USD)."""

    name: str = "base"
    supports_fractional: bool = False
    supports_market_orders: bool = True
    has_paper: bool = True

    def __init__(self, **options: Any):
        self.options = options

    # lifecycle ----------------------------------------------------------------
    def connect(self) -> None:  # noqa: B027  (optional hook)
        """Open sessions, log in, check credentials."""

    def close(self) -> None:  # noqa: B027
        """Release connections."""

    def __enter__(self) -> Broker:
        self.connect()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @property
    @abstractmethod
    def is_paper(self) -> bool:
        """True when orders go to a paper/sandbox/simulated account."""

    # reads -------------------------------------------------------------------
    @abstractmethod
    def account(self) -> AccountSnapshot: ...

    @abstractmethod
    def positions(self) -> list[Position]: ...

    @abstractmethod
    def quotes(self, symbols: list[str]) -> dict[str, Quote]: ...

    def quote(self, symbol: str) -> Quote | None:
        return self.quotes([symbol]).get(symbol)

    @abstractmethod
    def open_orders(self) -> list[OrderResult]: ...

    # writes ------------------------------------------------------------------
    @abstractmethod
    def place_order(self, order: OrderRequest) -> OrderResult: ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> None: ...

    def get_order(self, order_id: str) -> OrderResult | None:
        for o in self.open_orders():
            if o.order_id == order_id:
                return o
        return None

    def describe(self) -> str:
        return f"{self.name} ({'paper' if self.is_paper else 'LIVE'})"


def env_first(*names: str) -> str | None:
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return None


def client_order_id(day: date, symbol: str, side: Side, tag: str, salt: str = "") -> str:
    """Deterministic id: the same planned order on the same day maps to the same id,
    so brokers that de-duplicate on it reject an accidental second submission."""
    raw = f"{day.isoformat()}|{symbol}|{side.value}|{tag}|{salt}"
    digest = hashlib.sha1(raw.encode()).hexdigest()[:12]
    return f"rta-{day:%Y%m%d}-{symbol.replace('.', '').replace('-', '')[:6]}-{side.value[0]}-{digest}"


def uuid_from(text: str) -> str:
    """Stable RFC 4122 UUID (version 5) derived from text, for brokers that need a UUID order id."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"risk-aware-trading-agents/{text}"))


def map_status(raw: str | None) -> OrderStatus:
    if not raw:
        return OrderStatus.UNKNOWN
    s = str(raw).lower().replace(" ", "_").replace("-", "_")
    table = {
        "new": OrderStatus.NEW, "accepted": OrderStatus.NEW, "pending_new": OrderStatus.PENDING,
        "submitted": OrderStatus.NEW, "presubmitted": OrderStatus.PENDING, "open": OrderStatus.NEW,
        "live": OrderStatus.NEW, "received": OrderStatus.PENDING, "routed": OrderStatus.NEW, "working": OrderStatus.NEW,
        "queued": OrderStatus.PENDING, "pending": OrderStatus.PENDING, "in_flight": OrderStatus.PENDING,
        "submitting": OrderStatus.PENDING, "waiting_submit": OrderStatus.PENDING, "wait_to_new": OrderStatus.PENDING,
        "partially_filled": OrderStatus.PARTIALLY_FILLED, "partial_filled": OrderStatus.PARTIALLY_FILLED,
        "partialfilled": OrderStatus.PARTIALLY_FILLED, "filled_part": OrderStatus.PARTIALLY_FILLED,
        "filled": OrderStatus.FILLED, "filled_all": OrderStatus.FILLED,
        "canceled": OrderStatus.CANCELED, "cancelled": OrderStatus.CANCELED, "cancelled_all": OrderStatus.CANCELED,
        "cancelled_part": OrderStatus.CANCELED, "apicancelled": OrderStatus.CANCELED,
        "rejected": OrderStatus.REJECTED, "inactive": OrderStatus.REJECTED, "submit_failed": OrderStatus.REJECTED,
        "failed": OrderStatus.REJECTED, "expired": OrderStatus.EXPIRED, "done_for_day": OrderStatus.EXPIRED,
    }
    return table.get(s, OrderStatus.UNKNOWN)


def parse_ts(text: str | None) -> datetime | None:
    """ISO timestamps with any number of fractional digits and a Z or +hh:mm suffix."""
    if not text:
        return None
    s = str(text).strip().replace("Z", "+00:00")
    m = re.match(r"^(.*?T\d{2}:\d{2}:\d{2})(\.\d+)?(.*)$", s)
    if m:
        # Python 3.10 only accepts exactly 6 fractional digits
        frac = "." + m.group(2)[1:7].ljust(6, "0") if m.group(2) else ""
        s = m.group(1) + frac + m.group(3)
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def fmt_price(px: float | None) -> str | None:
    if px is None:
        return None
    return f"{px:.2f}" if px >= 1 else f"{px:.4f}"
