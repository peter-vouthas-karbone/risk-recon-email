from datetime import date, datetime
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from daily_recon.checks.position_equality import (
    collect_position_equality_exceptions,
)
from daily_recon.persistence import (
    PosRunRecord, create_pos_schema, insert_run, insert_running_positions,
)


def _seed(conn, run_id, rows):
    insert_run(conn, PosRunRecord(
        run_id=run_id, business_date=date(2026, 5, 20),
        started_at=datetime(2026, 1, 1), finished_at=None, status="success",
    ))
    df = pd.DataFrame(rows, columns=[
        "source", "business_date", "product_canonical", "vintage_canonical", "position",
    ])
    insert_running_positions(conn, run_id, df)


@pytest.fixture
def conn(tmp_db: Path):
    c = duckdb.connect(str(tmp_db))
    create_pos_schema(c)
    yield c
    c.close()


def test_equal_positions_produce_no_exceptions(conn):
    _seed(conn, "R1", [
        ("mo", date(2026, 5, 19), "D3 RIN", "2024", 100.0),
        ("fo", date(2026, 5, 19), "D3 RIN", "2024", 100.0),
    ])
    assert collect_position_equality_exceptions(conn, run_id="R1") == []


def test_break_when_mo_exceeds_fo(conn):
    _seed(conn, "R1", [
        ("mo", date(2026, 5, 19), "D3 RIN", "2024", 100.0),
        ("fo", date(2026, 5, 19), "D3 RIN", "2024", 90.0),
    ])
    rows = collect_position_equality_exceptions(conn, run_id="R1")
    assert len(rows) == 1
    p = rows[0]["payload"]
    assert p["mo_position"] == 100.0
    assert p["fo_position"] == 90.0
    assert p["delta"] == 10.0


def test_missing_source_treated_as_zero(conn):
    _seed(conn, "R1", [
        ("mo", date(2026, 5, 19), "D3 RIN", "2024", 50.0),
    ])
    rows = collect_position_equality_exceptions(conn, run_id="R1")
    assert len(rows) == 1
    p = rows[0]["payload"]
    assert p["fo_position"] == 0.0


def test_breaks_before_cutoff_are_suppressed(conn):
    _seed(conn, "R1", [
        ("mo", date(2025, 12, 31), "D3 RIN", "2024", 100.0),
        ("fo", date(2025, 12, 31), "D3 RIN", "2024", 90.0),
    ])
    assert collect_position_equality_exceptions(conn, run_id="R1") == []


def test_below_tolerance_ignored(conn):
    _seed(conn, "R1", [
        ("mo", date(2026, 5, 19), "D3 RIN", "2024", 100.0),
        ("fo", date(2026, 5, 19), "D3 RIN", "2024", 100.0 + 1e-9),
    ])
    assert collect_position_equality_exceptions(conn, run_id="R1") == []
