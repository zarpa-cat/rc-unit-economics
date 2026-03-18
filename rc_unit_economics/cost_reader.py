"""Read operation costs from billing meter audit log (SQLite)."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from .models import OperationCost


def _parse_ts(val: str | None) -> datetime:
    if not val:
        return datetime.utcnow()
    try:
        return datetime.fromisoformat(val)
    except ValueError:
        return datetime.utcnow()


class CostReader:
    """Read operation costs from an agent-billing-meter audit log."""

    def __init__(self, db_path: str, usd_per_credit: float = 0.001) -> None:
        self._db_path = db_path
        self._usd_per_credit = usd_per_credit

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _table_exists(self, conn: sqlite3.Connection) -> bool:
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='audit_log'")
        return cur.fetchone() is not None

    def load_all(self) -> list[OperationCost]:
        """Load all operation cost records from the audit log."""
        if not Path(self._db_path).exists():
            return []
        conn = self._connect()
        try:
            if not self._table_exists(conn):
                return []
            rows = conn.execute(
                "SELECT subscriber_id, operation, amount, timestamp FROM audit_log ORDER BY timestamp"
            ).fetchall()
            return [
                OperationCost(
                    subscriber_id=row["subscriber_id"],
                    operation=row["operation"],
                    amount=row["amount"],
                    timestamp=_parse_ts(row["timestamp"]),
                    usd_per_credit=self._usd_per_credit,
                )
                for row in rows
            ]
        finally:
            conn.close()

    def load_for_subscriber(self, subscriber_id: str) -> list[OperationCost]:
        """Load cost records for a specific subscriber."""
        if not Path(self._db_path).exists():
            return []
        conn = self._connect()
        try:
            if not self._table_exists(conn):
                return []
            rows = conn.execute(
                "SELECT subscriber_id, operation, amount, timestamp FROM audit_log "
                "WHERE subscriber_id = ? ORDER BY timestamp",
                (subscriber_id,),
            ).fetchall()
            return [
                OperationCost(
                    subscriber_id=row["subscriber_id"],
                    operation=row["operation"],
                    amount=row["amount"],
                    timestamp=_parse_ts(row["timestamp"]),
                    usd_per_credit=self._usd_per_credit,
                )
                for row in rows
            ]
        finally:
            conn.close()

    def known_subscriber_ids(self) -> list[str]:
        """Return all unique subscriber IDs in the audit log."""
        if not Path(self._db_path).exists():
            return []
        conn = self._connect()
        try:
            if not self._table_exists(conn):
                return []
            rows = conn.execute("SELECT DISTINCT subscriber_id FROM audit_log").fetchall()
            return [row["subscriber_id"] for row in rows]
        finally:
            conn.close()
