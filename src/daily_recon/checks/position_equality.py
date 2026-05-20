"""Check 3 — MO vs FO running position equality across all dates."""
from __future__ import annotations

import duckdb

from daily_recon.config import DESYNC_CUTOFF_DATE, TOLERANCE


def collect_position_equality_exceptions(
    conn: duckdb.DuckDBPyConnection, run_id: str
) -> list[dict]:
    df = conn.execute(
        """
        WITH mo AS (
            SELECT business_date, product_canonical, vintage_canonical, position
            FROM pos_running_position WHERE run_id = ? AND source = 'mo'
        ),
        fo AS (
            SELECT business_date, product_canonical, vintage_canonical, position
            FROM pos_running_position WHERE run_id = ? AND source = 'fo'
        )
        SELECT * FROM (
            SELECT
                COALESCE(mo.business_date, fo.business_date) AS business_date,
                COALESCE(mo.product_canonical, fo.product_canonical) AS product,
                COALESCE(mo.vintage_canonical, fo.vintage_canonical) AS vintage,
                COALESCE(mo.position, 0.0) AS mo_position,
                COALESCE(fo.position, 0.0) AS fo_position
            FROM mo FULL OUTER JOIN fo
            USING (business_date, product_canonical, vintage_canonical)
        ) WHERE business_date >= ?
        """,
        [run_id, run_id, DESYNC_CUTOFF_DATE],
    ).df()

    rows: list[dict] = []
    for r in df.to_dict(orient="records"):
        delta = float(r["mo_position"]) - float(r["fo_position"])
        if abs(delta) <= TOLERANCE:
            continue
        rows.append({
            "check_id": "position_break",
            "severity": "error",
            "key": {
                "date": r["business_date"],
                "product": r["product"],
                "vintage": r["vintage"],
            },
            "payload": {
                "business_date": r["business_date"],
                "product": r["product"],
                "vintage": r["vintage"],
                "mo_position": float(r["mo_position"]),
                "fo_position": float(r["fo_position"]),
                "delta": delta,
            },
        })
    return rows
