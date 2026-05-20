"""Check 1 — trade-level drift since prior run.

Reuses karbone_recon's mo_trade_drift / fuels_trade_drift output tables.
The 'new_trade' classification is informational and suppressed.
"""
from __future__ import annotations

import duckdb

_SUPPRESS_CHANGE_TYPES = {"new_trade"}


def collect_trade_drift_exceptions(
    conn: duckdb.DuckDBPyConnection, run_id: str
) -> list[dict]:
    rows: list[dict] = []
    for source, table in (("mo", "mo_trade_drift"), ("fo", "fuels_trade_drift")):
        df = conn.execute(
            f"""
            SELECT trade_date, side, counterparty_canonical, product_canonical,
                   vintage_canonical, change_type, prior_volume, current_volume,
                   prior_wap, current_wap
            FROM {table}
            WHERE run_id = ?
            """,
            [run_id],
        ).df()
        for r in df.to_dict(orient="records"):
            if r["change_type"] in _SUPPRESS_CHANGE_TYPES:
                continue
            rows.append({
                "check_id": "trade_drift",
                "severity": "error",
                "key": {
                    "source": source,
                    "trade_date": r["trade_date"],
                    "side": r["side"],
                    "counterparty": r["counterparty_canonical"],
                    "product": r["product_canonical"],
                    "vintage": r["vintage_canonical"],
                },
                "payload": {
                    "source": source,
                    "trade_date": r["trade_date"],
                    "side": r["side"],
                    "counterparty": r["counterparty_canonical"],
                    "product": r["product_canonical"],
                    "vintage": r["vintage_canonical"],
                    "change_type": r["change_type"],
                    "prior_volume": r["prior_volume"],
                    "current_volume": r["current_volume"],
                    "prior_wap": r["prior_wap"],
                    "current_wap": r["current_wap"],
                },
            })
    return rows
