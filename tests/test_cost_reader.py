"""Tests for CostReader."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from rc_unit_economics.cost_reader import CostReader


def _create_audit_db(path: Path) -> None:
    """Create a minimal audit log DB matching agent-billing-meter schema."""
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY,
            subscriber_id TEXT NOT NULL,
            operation TEXT NOT NULL,
            amount INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            success INTEGER DEFAULT 1
        )
    """)
    conn.executemany(
        "INSERT INTO audit_log (subscriber_id, operation, amount, timestamp) VALUES (?, ?, ?, ?)",
        [
            ("user_1", "generate_report", 10, "2026-03-01T10:00:00"),
            ("user_1", "summarize", 5, "2026-03-05T12:00:00"),
            ("user_2", "generate_report", 10, "2026-03-02T09:00:00"),
            ("user_2", "export_pdf", 20, "2026-03-10T15:00:00"),
            ("user_3", "generate_report", 10, "2026-03-03T08:00:00"),
        ],
    )
    conn.commit()
    conn.close()


class TestCostReader:
    def test_load_all(self, tmp_path: Path) -> None:
        db = tmp_path / "audit.db"
        _create_audit_db(db)
        reader = CostReader(str(db), usd_per_credit=0.001)
        ops = reader.load_all()
        assert len(ops) == 5

    def test_load_all_amounts(self, tmp_path: Path) -> None:
        db = tmp_path / "audit.db"
        _create_audit_db(db)
        reader = CostReader(str(db), usd_per_credit=0.001)
        ops = reader.load_all()
        total_credits = sum(o.amount for o in ops)
        assert total_credits == 55  # 10+5+10+20+10

    def test_load_all_usd_conversion(self, tmp_path: Path) -> None:
        db = tmp_path / "audit.db"
        _create_audit_db(db)
        reader = CostReader(str(db), usd_per_credit=0.01)  # $0.01/credit
        ops = reader.load_all()
        assert ops[0].usd_cost == pytest.approx(0.10)  # 10 credits × $0.01

    def test_load_for_subscriber(self, tmp_path: Path) -> None:
        db = tmp_path / "audit.db"
        _create_audit_db(db)
        reader = CostReader(str(db))
        ops = reader.load_for_subscriber("user_1")
        assert len(ops) == 2
        assert all(o.subscriber_id == "user_1" for o in ops)

    def test_load_for_missing_subscriber(self, tmp_path: Path) -> None:
        db = tmp_path / "audit.db"
        _create_audit_db(db)
        reader = CostReader(str(db))
        ops = reader.load_for_subscriber("user_999")
        assert ops == []

    def test_known_subscriber_ids(self, tmp_path: Path) -> None:
        db = tmp_path / "audit.db"
        _create_audit_db(db)
        reader = CostReader(str(db))
        ids = reader.known_subscriber_ids()
        assert set(ids) == {"user_1", "user_2", "user_3"}

    def test_missing_db_returns_empty(self, tmp_path: Path) -> None:
        reader = CostReader(str(tmp_path / "nonexistent.db"))
        assert reader.load_all() == []
        assert reader.load_for_subscriber("x") == []
        assert reader.known_subscriber_ids() == []

    def test_empty_db_no_table(self, tmp_path: Path) -> None:
        db = tmp_path / "empty.db"
        conn = sqlite3.connect(str(db))
        conn.close()
        reader = CostReader(str(db))
        assert reader.load_all() == []

    def test_operations_ordered_by_timestamp(self, tmp_path: Path) -> None:
        db = tmp_path / "audit.db"
        _create_audit_db(db)
        reader = CostReader(str(db))
        ops = reader.load_for_subscriber("user_1")
        assert ops[0].timestamp < ops[1].timestamp

    def test_usd_per_credit_propagated(self, tmp_path: Path) -> None:
        db = tmp_path / "audit.db"
        _create_audit_db(db)
        reader = CostReader(str(db), usd_per_credit=0.005)
        ops = reader.load_all()
        for op in ops:
            assert op.usd_per_credit == 0.005
