# tests/test_persistence.py
from datetime import date, datetime
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from daily_recon.persistence import (
    PosRunRecord,
    create_pos_schema,
    insert_exceptions,
    insert_running_positions,
    insert_run,
    latest_successful_run_id,
)


@pytest.fixture
def conn(tmp_db: Path):
    c = duckdb.connect(str(tmp_db))
    create_pos_schema(c)
    yield c
    c.close()


def test_create_pos_schema_is_idempotent(conn):
    create_pos_schema(conn)  # second call must not error
    tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
    assert {"pos_runs", "pos_running_position", "pos_exceptions"} <= tables


def test_insert_run_and_lookup(conn):
    rid = "2026-05-20T060000"
    insert_run(conn, PosRunRecord(
        run_id=rid, business_date=date(2026, 5, 20),
        started_at=datetime(2026, 5, 20, 6, 0, 0),
        finished_at=datetime(2026, 5, 20, 6, 0, 30),
        status="success",
    ))
    assert latest_successful_run_id(conn, exclude=None) == rid
    assert latest_successful_run_id(conn, exclude=rid) is None


def test_insert_running_positions_roundtrip(conn):
    rid = "2026-05-20T060000"
    insert_run(conn, PosRunRecord(
        run_id=rid, business_date=date(2026, 5, 20),
        started_at=datetime.now(), finished_at=datetime.now(), status="success",
    ))
    df = pd.DataFrame({
        "source": ["mo", "fo"],
        "business_date": [date(2026, 5, 20), date(2026, 5, 20)],
        "product_canonical": ["D3 RIN", "D3 RIN"],
        "vintage_canonical": ["2024", "2024"],
        "position": [100.0, 100.0],
    })
    insert_running_positions(conn, rid, df)
    out = conn.execute("SELECT source, position FROM pos_running_position ORDER BY source").df()
    assert list(out["source"]) == ["fo", "mo"]
    assert list(out["position"]) == [100.0, 100.0]


def test_insert_exceptions_stores_payload_json(conn):
    rid = "2026-05-20T060000"
    insert_run(conn, PosRunRecord(
        run_id=rid, business_date=date(2026, 5, 20),
        started_at=datetime.now(), finished_at=datetime.now(), status="success",
    ))
    rows = [{
        "check_id": "position_break",
        "severity": "error",
        "key": {"date": "2026-05-19", "product": "D3 RIN", "vintage": "2024"},
        "payload": {"mo": 100, "fo": 90, "delta": 10},
    }]
    insert_exceptions(conn, rid, rows)
    out = conn.execute(
        "SELECT check_id, payload_json FROM pos_exceptions"
    ).fetchall()
    assert len(out) == 1
    assert out[0][0] == "position_break"
    assert '"delta": 10' in out[0][1]
