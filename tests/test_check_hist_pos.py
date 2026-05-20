from datetime import date, datetime
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from daily_recon.checks.historical_position import (
    collect_historical_position_drift_exceptions,
)
from daily_recon.persistence import (
    PosRunRecord, create_pos_schema, insert_run, insert_running_positions,
)


def _seed(conn, run_id, business_date, rows):
    insert_run(conn, PosRunRecord(
        run_id=run_id, business_date=business_date,
        started_at=datetime(2026, 1, 1), finished_at=None, status="success",
    ))
    df = pd.DataFrame(rows, columns=[
        "source", "business_date", "product_canonical", "vintage_canonical", "position"
    ])
    insert_running_positions(conn, run_id, df)


@pytest.fixture
def conn(tmp_db: Path):
    c = duckdb.connect(str(tmp_db))
    create_pos_schema(c)
    yield c
    c.close()


def test_returns_empty_when_no_prior_run(conn):
    _seed(conn, "R1", date(2026, 5, 20), [
        ("mo", date(2026, 5, 19), "D3 RIN", "2024", 100.0),
    ])
    rows = collect_historical_position_drift_exceptions(
        conn, current_run_id="R1", prior_run_id=None, business_date=date(2026, 5, 20)
    )
    assert rows == []


def test_detects_past_position_change(conn):
    _seed(conn, "R0", date(2026, 5, 19), [
        ("mo", date(2026, 5, 17), "D3 RIN", "2024", 100.0),
        ("mo", date(2026, 5, 18), "D3 RIN", "2024", 150.0),
    ])
    _seed(conn, "R1", date(2026, 5, 20), [
        ("mo", date(2026, 5, 17), "D3 RIN", "2024", 120.0),   # past day changed
        ("mo", date(2026, 5, 18), "D3 RIN", "2024", 150.0),   # unchanged
    ])
    rows = collect_historical_position_drift_exceptions(
        conn, current_run_id="R1", prior_run_id="R0", business_date=date(2026, 5, 20)
    )
    assert len(rows) == 1
    p = rows[0]["payload"]
    assert p["delta"] == 20.0
    assert p["prior_position"] == 100.0
    assert p["current_position"] == 120.0


def test_dates_at_or_after_T_minus_1_are_ignored(conn):
    # T = 2026-05-20, T-1 = 2026-05-19. Only dates < T-1 are inspected.
    _seed(conn, "R0", date(2026, 5, 19), [
        ("mo", date(2026, 5, 19), "D3 RIN", "2024", 100.0),
    ])
    _seed(conn, "R1", date(2026, 5, 20), [
        ("mo", date(2026, 5, 19), "D3 RIN", "2024", 200.0),  # T-1, skipped
    ])
    rows = collect_historical_position_drift_exceptions(
        conn, current_run_id="R1", prior_run_id="R0", business_date=date(2026, 5, 20)
    )
    assert rows == []


def test_breaks_before_cutoff_are_suppressed(conn):
    _seed(conn, "R0", date(2026, 5, 19), [
        ("mo", date(2025, 12, 31), "D3 RIN", "2024", 100.0),
    ])
    _seed(conn, "R1", date(2026, 5, 20), [
        ("mo", date(2025, 12, 31), "D3 RIN", "2024", 120.0),
    ])
    rows = collect_historical_position_drift_exceptions(
        conn, current_run_id="R1", prior_run_id="R0", business_date=date(2026, 5, 20)
    )
    assert rows == []


def test_below_tolerance_is_ignored(conn):
    _seed(conn, "R0", date(2026, 5, 19), [
        ("mo", date(2026, 5, 17), "D3 RIN", "2024", 100.0),
    ])
    _seed(conn, "R1", date(2026, 5, 20), [
        ("mo", date(2026, 5, 17), "D3 RIN", "2024", 100.0 + 1e-9),
    ])
    rows = collect_historical_position_drift_exceptions(
        conn, current_run_id="R1", prior_run_id="R0", business_date=date(2026, 5, 20)
    )
    assert rows == []
