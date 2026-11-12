"""Shared plumbing for REST broker adapters."""

from __future__ import annotations

import logging
from typing import Any

import requests

from .base import BrokerError

log = logging.getLogger(__name__)


class RestClient:
    def __init__(self, base_url: str, headers: dict[str, str] | None = None, timeout: float = 20.0,
                 session: requests.Session | None = None):
        self.base = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.session.headers.update(headers or {})
        self.timeout = timeout

    def request(self, method: str, path: str, *, params: dict[str, Any] | None = None,
                json: Any = None, data: dict[str, Any] | None = None, base: str | None = None,
                ok_statuses: tuple[int, ...] = (200, 201, 202, 204)) -> Any:
        url = (base or self.base).rstrip("/") + path
        try:
            r = self.session.request(method, url, params=params, json=json, data=data, timeout=self.timeout)
        except requests.RequestException as exc:
            raise BrokerError(f"{method} {path}: {exc}") from exc
        if r.status_code not in ok_statuses:
            detail = r.text[:300].replace("\n", " ")
            raise BrokerError(f"{method} {path}: HTTP {r.status_code} {detail}")
        if r.status_code == 204 or not r.content:
            return None
        try:
            return r.json()
        except ValueError:
            return r.text

    def get(self, path: str, **kw: Any) -> Any:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw: Any) -> Any:
        return self.request("POST", path, **kw)

    def delete(self, path: str, **kw: Any) -> Any:
        return self.request("DELETE", path, **kw)


def as_list(x: Any) -> list[Any]:
    """Some APIs return a single object instead of a one-item list, or the string 'null' for none."""
    if x is None or x == "null":
        return []
    return x if isinstance(x, list) else [x]


def num(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default
