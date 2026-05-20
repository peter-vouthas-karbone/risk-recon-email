"""Check 2 — historical position drift.

For every (source, product, vintage, date<T-1), compare today's running position
to the prior run's. Any |delta| > TOLERANCE → exception.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import duckdb

from daily_recon.config import DESYNC_CUTOFF_DATE, TOLERANCE


def collect_historical_position_drift_exceptions(
    conn: duckdb.DuckDBPyConnection,
    current_run_id: str,
    prior_run_id: Optional[str],
    business_date: date,
) -> list[dict]:
    if prior_run_id is None:
        return []
    cutoff = business_date - timedelta(days=1)  # exclusive — only dates strictly before T-1

    df = conn.execute(
        """
        WITH cur AS (
            SELECT source, business_date, product_canonical, vintage_canonical, position
            FROM pos_running_position
            WHERE run_id = ? AND business_date < ? AND business_date >= ?
        ),
        prv AS (
            SELECT source, business_date, product_canonical, vintage_canonical, position
            FROM pos_running_position
            WHERE run_id = ? AND business_date < ? AND business_date >= ?
        )
        SELECT
            COALESCE(cur.source, prv.source) AS source,
            COALESCE(cur.business_date, prv.business_date) AS business_date,
            COALESCE(cur.product_canonical, prv.product_canonical) AS product,
            COALESCE(cur.vintage_canonical, prv.vintage_canonical) AS vintage,
            COALESCE(cur.position, 0.0) AS current_position,
            COALESCE(prv.position, 0.0) AS prior_position
        FROM cur FULL OUTER JOIN prv USING (source, business_date, product_canonical, vintage_canonical)
        """,
        [current_run_id, cutoff, DESYNC_CUTOFF_DATE, prior_run_id, cutoff, DESYNC_CUTOFF_DATE],
    ).df()

    rows: list[dict] = []
    for r in df.to_dict(orient="records"):
        delta = float(r["current_position"]) - float(r["prior_position"])
        if abs(delta) <= TOLERANCE:
            continue
        rows.append({
            "check_id": "historical_position_drift",
            "severity": "error",
            "key": {
                "source": r["source"],
                "date": r["business_date"],
                "product": r["product"],
                "vintage": r["vintage"],
            },
            "payload": {
                "source": r["source"],
                "business_date": r["business_date"],
                "product": r["product"],
                "vintage": r["vintage"],
                "prior_position": float(r["prior_position"]),
                "current_position": float(r["current_position"]),
                "delta": delta,
            },
        })
    return rows
