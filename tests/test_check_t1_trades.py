# tests/test_check_t1_trades.py
from datetime import date
from pathlib import Path

import duckdb
import pytest

from daily_recon.checks.prior_day_trades import collect_prior_day_trade_exceptions


@pytest.fixture
def conn(tmp_db: Path):
    c = duckdb.connect(str(tmp_db))
    c.execute("""
        CREATE TABLE cross_recon (
            run_id TEXT, status TEXT, trade_date DATE, side TEXT,
            counterparty_canonical TEXT, product_canonical TEXT,
            vintage_canonical TEXT, delivery_match_date DATE,
            mo_volume DOUBLE, fuels_volume DOUBLE,
            mo_wap DOUBLE, fuels_wap DOUBLE
        )
    """)
    yield c
    c.close()


def test_matched_rows_are_suppressed(conn):
    conn.execute(
        "INSERT INTO cross_recon VALUES "
        "('R1','matched','2026-05-19','Buy','Air Liquide','D3 RIN','2024',"
        "'2026-05-20',100,100,2.5,2.5)"
    )
    assert collect_prior_day_trade_exceptions(
        conn, run_id="R1", business_date=date(2026, 5, 20)
    ) == []


def test_only_T_minus_1_rows_considered(conn):
    # Same-day buckets (T) and old-day buckets (T-3) must be filtered out.
    conn.executemany(
        "INSERT INTO cross_recon VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("R1", "price_break", date(2026, 5, 20), "Buy", "A", "D3 RIN", "2024",
             date(2026, 5, 25), 100, 100, 2.5, 2.4),  # today, ignored
            ("R1", "volume_break", date(2026, 5, 17), "Sell", "B", "D3 RIN", "2024",
             date(2026, 5, 25), 100, 90, 2.5, 2.5),   # old, ignored
            ("R1", "mo_only", date(2026, 5, 19), "Buy", "C", "D3 RIN", "2024",
             date(2026, 5, 25), 50, None, 1.8, None),  # T-1, counted
        ],
    )
    rows = collect_prior_day_trade_exceptions(
        conn, run_id="R1", business_date=date(2026, 5, 20)
    )
    assert len(rows) == 1
    assert rows[0]["payload"]["status"] == "mo_only"
    assert rows[0]["payload"]["counterparty"] == "C"


def test_all_non_matched_statuses_exposed(conn):
    for s in ("price_break", "volume_break", "mo_only", "fo_only"):
        conn.execute(
            "INSERT INTO cross_recon VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ["R1", s, date(2026, 5, 19), "Buy", s, "D3 RIN", "2024",
             date(2026, 5, 25), 1, 2, 3, 4],
        )
    rows = collect_prior_day_trade_exceptions(
        conn, run_id="R1", business_date=date(2026, 5, 20)
    )
    assert sorted(r["payload"]["status"] for r in rows) == [
        "fo_only", "mo_only", "price_break", "volume_break",
    ]
