"""DuckDB persistence for daily_recon's pos_* tables.

These tables sit alongside karbone_recon's tables in the same database file.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Optional

import duckdb
import pandas as pd


@dataclass(frozen=True)
class PosRunRecord:
    run_id: str
    business_date: date
    started_at: datetime
    finished_at: datetime
    status: str  # 'success' | 'failed' | 'partial'


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pos_runs (
    run_id          TEXT PRIMARY KEY,
    business_date   DATE NOT NULL,
    started_at      TIMESTAMP NOT NULL,
    finished_at     TIMESTAMP,
    status          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pos_running_position (
    run_id              TEXT NOT NULL,
    source              TEXT NOT NULL,
    business_date       DATE NOT NULL,
    product_canonical   TEXT,
    vintage_canonical   TEXT,
    position            DOUBLE NOT NULL
);

CREATE TABLE IF NOT EXISTS pos_exceptions (
    run_id        TEXT NOT NULL,
    check_id      TEXT NOT NULL,
    severity      TEXT NOT NULL,
    key_json      TEXT NOT NULL,
    payload_json  TEXT NOT NULL
);
"""


def create_pos_schema(conn: duckdb.DuckDBPyConnection) -> None:
    for stmt in _SCHEMA_SQL.strip().split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)


def insert_run(conn: duckdb.DuckDBPyConnection, rec: PosRunRecord) -> None:
    conn.execute(
        "INSERT INTO pos_runs VALUES (?, ?, ?, ?, ?)",
        [rec.run_id, rec.business_date, rec.started_at, rec.finished_at, rec.status],
    )


def latest_successful_run_id(
    conn: duckdb.DuckDBPyConnection, exclude: Optional[str]
) -> Optional[str]:
    row = conn.execute(
        """
        SELECT run_id FROM pos_runs
        WHERE status = 'success' AND (? IS NULL OR run_id <> ?)
        ORDER BY finished_at DESC NULLS LAST, started_at DESC
        LIMIT 1
        """,
        [exclude, exclude],
    ).fetchone()
    return row[0] if row else None


def insert_running_positions(
    conn: duckdb.DuckDBPyConnection, run_id: str, df: pd.DataFrame
) -> None:
    if df.empty:
        return
    work = df.copy()
    work.insert(0, "run_id", run_id)
    conn.register("_pos_buf", work)
    conn.execute("INSERT INTO pos_running_position SELECT * FROM _pos_buf")
    conn.unregister("_pos_buf")


def insert_exceptions(
    conn: duckdb.DuckDBPyConnection, run_id: str, rows: Iterable[dict]
) -> None:
    payload = []
    for r in rows:
        payload.append([
            run_id, r["check_id"], r.get("severity", "error"),
            json.dumps(r["key"], default=str, sort_keys=True),
            json.dumps(r["payload"], default=str),
        ])
    if not payload:
        return
    conn.executemany(
        "INSERT INTO pos_exceptions VALUES (?, ?, ?, ?, ?)",
        payload,
    )
