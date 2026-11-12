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
CREATE TABLE IF NOT EXISTS llm_reviews (
  run_id INTEGER, ts TEXT, symbol TEXT, provider TEXT, model TEXT, strength_before REAL, adjustment REAL,
  veto INTEGER, veto_reason TEXT, bull TEXT, bear TEXT, summary TEXT, system_prompt TEXT, prompt TEXT, raw TEXT,
  error TEXT, latency_ms INTEGER, usage TEXT
);
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

    def log_llm_review(self, run_id: int, rec: Any) -> None:
        """Store one LLM review (riskaware.llm.ReviewRecord): the prompt, the raw reply and what it changed."""
        v = rec.verdict
        self.db.execute(
            "INSERT INTO llm_reviews VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, datetime.now(timezone.utc).isoformat(), rec.symbol, rec.provider, rec.model, rec.strength_before,
             v.adjustment if v else None, int(v.veto) if v else None, v.veto_reason if v else None,
             v.bull_case if v else None, v.bear_case if v else None, v.summary if v else None, rec.system,
             rec.prompt, rec.raw, rec.error, rec.latency_ms, json.dumps(rec.usage or {}, default=str)))

    def llm_reviews(self, limit: int = 50, symbol: str | None = None, run_id: int | None = None
                    ) -> list[dict[str, Any]]:
        where, args = [], []
        if symbol:
            where.append("symbol = ?")
            args.append(symbol.upper())
        if run_id is not None:
            where.append("run_id = ?")
            args.append(run_id)
        sql = "SELECT * FROM llm_reviews" + (" WHERE " + " AND ".join(where) if where else "")
        cur = self.db.execute(sql + " ORDER BY rowid DESC LIMIT ?", (*args, limit))
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]

    def recent_decisions(self, limit: int = 200, symbol: str | None = None) -> list[dict[str, Any]]:
        """Decisions with the time of their run, newest first."""
        sql = ("SELECT d.run_id, r.ts, d.symbol, d.action, d.strength, d.accepted, d.reject_reason, d.reason, "
               "d.setups FROM decisions d JOIN runs r ON r.id = d.run_id")
        args: list[Any] = []
        if symbol:
            sql += " WHERE d.symbol = ?"
            args.append(symbol.upper())
        cur = self.db.execute(sql + " ORDER BY d.run_id DESC, d.strength DESC LIMIT ?", (*args, limit))
        names = [d[0] for d in cur.description]
        rows = [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
        for r in rows:
            try:
                r["setups"] = json.loads(r["setups"] or "[]")
            except ValueError:
                r["setups"] = []
        return rows

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
