"""Check 4 — prior-day trade match (T-1) via karbone_recon.cross_recon."""
from __future__ import annotations

from datetime import date, timedelta

import duckdb


def collect_prior_day_trade_exceptions(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    business_date: date,
) -> list[dict]:
    target = business_date - timedelta(days=1)
    df = conn.execute(
        """
        SELECT status, trade_date, side, counterparty_canonical, product_canonical,
               vintage_canonical, delivery_match_date,
               mo_volume, fuels_volume, mo_wap, fuels_wap
        FROM cross_recon
        WHERE run_id = ? AND trade_date = ? AND status <> 'matched'
        """,
        [run_id, target],
    ).df()

    rows: list[dict] = []
    for r in df.to_dict(orient="records"):
        rows.append({
            "check_id": "prior_day_trades",
            "severity": "error",
            "key": {
                "trade_date": r["trade_date"],
                "side": r["side"],
                "counterparty": r["counterparty_canonical"],
                "product": r["product_canonical"],
                "vintage": r["vintage_canonical"],
                "delivery": r["delivery_match_date"],
            },
            "payload": {
                "status": r["status"],
                "trade_date": r["trade_date"],
                "side": r["side"],
                "counterparty": r["counterparty_canonical"],
                "product": r["product_canonical"],
                "vintage": r["vintage_canonical"],
                "delivery": r["delivery_match_date"],
                "mo_volume": r["mo_volume"],
                "fo_volume": r["fuels_volume"],
                "mo_wap": r["mo_wap"],
                "fo_wap": r["fuels_wap"],
            },
        })
    return rows
