"""SQLite journal: every run, decision and order, for audits and the dashboard."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import RISKAWARE_HOME

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, mode TEXT, broker TEXT, account TEXT, strategy TEXT,
  regime TEXT, equity REAL, cash REAL, stock_exposure REAL, fear_greed REAL, vix REAL, notes TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
  run_id INTEGER, symbol TEXT, action TEXT, strength REAL, reason TEXT, setups TEXT,
  accepted INTEGER, reject_reason TEXT
);
CREATE TABLE IF NOT EXISTS orders (
  run_id INTEGER, ts TEXT, client_order_id TEXT, broker_order_id TEXT, symbol TEXT, side TEXT, qty REAL,
  limit_price REAL, tag TEXT, status TEXT, message TEXT, reason TEXT
);
CREATE INDEX IF NOT EXISTS orders_coid ON orders(client_order_id);
"""


class Journal:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path).expanduser() if path else RISKAWARE_HOME / "journal.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def start_run(self, **fields: Any) -> int:
        fields.setdefault("ts", datetime.now(timezone.utc).isoformat())
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        cur = self.db.execute(f"INSERT INTO runs ({cols}) VALUES ({marks})", list(fields.values()))
        self.db.commit()
        return int(cur.lastrowid)

    def log_decision(self, run_id: int, symbol: str, action: str, strength: float, reason: str,
                     setups: list[str], accepted: bool, reject_reason: str = "") -> None:
        self.db.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?)",
                        (run_id, symbol, action, strength, reason, json.dumps(setups), int(accepted), reject_reason))

    def log_order(self, run_id: int, client_order_id: str | None, broker_order_id: str | None, symbol: str,
                  side: str, qty: float, limit_price: float | None, tag: str, status: str, message: str = "",
                  reason: str = "") -> None:
        self.db.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (run_id, datetime.now(timezone.utc).isoformat(), client_order_id, broker_order_id, symbol,
                         side, qty, limit_price, tag, status, message, reason))

    def commit(self) -> None:
        self.db.commit()

    def seen_client_order_id(self, coid: str) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM orders WHERE client_order_id = ? AND status NOT IN ('REJECTED','DRY_RUN','SKIPPED','ERROR') LIMIT 1",
            (coid,)).fetchone()
        return row is not None

    def recent_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        cur = self.db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]

    def recent_orders(self, limit: int = 100) -> list[dict[str, Any]]:
        cur = self.db.execute("SELECT * FROM orders ORDER BY rowid DESC LIMIT ?", (limit,))
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
