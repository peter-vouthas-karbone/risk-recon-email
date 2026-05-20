# tests/test_check_trade_drift.py
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from daily_recon.checks.trade_drift import collect_trade_drift_exceptions


@pytest.fixture
def conn(tmp_db: Path):
    c = duckdb.connect(str(tmp_db))
    c.execute("""
        CREATE TABLE mo_trade_drift (
            run_id TEXT, trade_date DATE, side TEXT, counterparty_canonical TEXT,
            product_canonical TEXT, vintage_canonical TEXT,
            change_type TEXT, prior_volume DOUBLE, current_volume DOUBLE,
            prior_wap DOUBLE, current_wap DOUBLE
        )
    """)
    c.execute("""
        CREATE TABLE fuels_trade_drift (
            run_id TEXT, trade_date DATE, side TEXT, counterparty_canonical TEXT,
            product_canonical TEXT, vintage_canonical TEXT,
            change_type TEXT, prior_volume DOUBLE, current_volume DOUBLE,
            prior_wap DOUBLE, current_wap DOUBLE
        )
    """)
    yield c
    c.close()


def test_new_trade_is_suppressed(conn):
    conn.execute(
        "INSERT INTO mo_trade_drift VALUES "
        "('R1','2026-05-20','Buy','Air Liquide','D3 RIN','2024',"
        "'new_trade',NULL,100,NULL,2.5)"
    )
    rows = collect_trade_drift_exceptions(conn, run_id="R1")
    assert rows == []


def test_modified_removed_added_become_exceptions(conn):
    conn.executemany(
        "INSERT INTO mo_trade_drift VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("R1", "2026-05-19", "Buy", "Air Liquide", "D3 RIN", "2024",
             "modified_trade", 100.0, 100.0, 2.44, 2.45),
            ("R1", "2026-05-18", "Sell", "Trillium", "D3 RIN", "2024",
             "removed_trade", 50.0, None, 2.63, None),
        ],
    )
    conn.execute(
        "INSERT INTO fuels_trade_drift VALUES "
        "('R1','2026-05-18','Buy','Mercuria','D5 RIN','2025',"
        "'added_trade',NULL,75,NULL,1.82)"
    )
    rows = collect_trade_drift_exceptions(conn, run_id="R1")
    assert len(rows) == 3
    sources = sorted(r["payload"]["source"] for r in rows)
    assert sources == ["fo", "mo", "mo"]
    types = sorted(r["payload"]["change_type"] for r in rows)
    assert types == ["added_trade", "modified_trade", "removed_trade"]
    for r in rows:
        assert r["check_id"] == "trade_drift"
        assert r["severity"] == "error"
