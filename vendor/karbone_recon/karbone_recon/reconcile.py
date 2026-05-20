"""
Cross-system reconciliation.

  reconcile_cross_system()  â€” MO vs Fuels  â†’ cross_recon
  reconcile_mgmt_vs_fuels() â€” Mgmt vs Fuels â†’ mgmt_cross_recon
  analyze_book_placement()  â€” three-way MO/Mgmt/Fuels â†’ book_analysis

Aggregates source tables into trade buckets using:
  (trade_date, side, counterparty_canonical, product_canonical, vintage_canonical)
then full-outer-joins and classifies each bucket.
"""

from typing import Optional

import duckdb
import numpy as np
import pandas as pd

from karbone_recon.audit import (
    _aggregate_table,
    _approx_equal,
    _nullable_float,
    BUCKET_KEY,
    VOL_TOLERANCE,
    WAP_TOLERANCE,
)


# ---------------------------------------------------------------------------
# Generic two-table reconciliation engine
# ---------------------------------------------------------------------------

def _reconcile_two_tables(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    left_table: str,
    right_table: str,
    dest_table: str,
    left_label: str,
    right_label: str,
) -> dict[str, int]:
    """
    Full-outer-join two aggregated tables and classify each bucket.
    Inserts results into *dest_table*.
    """
    left = _aggregate_table(conn, run_id, left_table)
    right = _aggregate_table(conn, run_id, right_table)

    left = left.rename(columns={
        "total_volume": f"{left_label}_vol",
        "wap": f"{left_label}_wap",
    })
    right = right.rename(columns={
        "total_volume": f"{right_label}_vol",
        "wap": f"{right_label}_wap",
    })

    merged = pd.merge(left, right, on=BUCKET_KEY, how="outer", indicator=True)

    records = []
    lv = f"{left_label}_vol"
    lw = f"{left_label}_wap"
    rv = f"{right_label}_vol"
    rw = f"{right_label}_wap"

    for _, row in merged.iterrows():
        src = row["_merge"]

        if src == "left_only":
            status = f"{left_label}_only"
            rec = _two_table_record(
                run_id, status, row,
                left_vol=row[lv], right_vol=None,
                left_wap=row[lw], right_wap=None,
                left_label=left_label, right_label=right_label,
            )
        elif src == "right_only":
            status = f"{right_label}_only"
            rec = _two_table_record(
                run_id, status, row,
                left_vol=None, right_vol=row[rv],
                left_wap=None, right_wap=row[rw],
                left_label=left_label, right_label=right_label,
            )
        else:
            vol_diff = not _approx_equal(row[lv], row[rv], VOL_TOLERANCE)
            wap_diff = not _approx_equal(row[lw], row[rw], WAP_TOLERANCE)
            if vol_diff and wap_diff:
                status = "volume_and_price_break"
            elif vol_diff:
                status = "volume_break"
            elif wap_diff:
                status = "price_break"
            else:
                status = "matched"
            rec = _two_table_record(
                run_id, status, row,
                left_vol=row[lv], right_vol=row[rv],
                left_wap=row[lw], right_wap=row[rw],
                left_label=left_label, right_label=right_label,
            )
        records.append(rec)

    if records:
        recon_df = pd.DataFrame(records)
        conn.execute(f"INSERT INTO {dest_table} SELECT * FROM recon_df")

    counts: dict[str, int] = {}
    for r in records:
        s = r["recon_status"]
        counts[s] = counts.get(s, 0) + 1
    return counts


def _two_table_record(
    run_id: str,
    recon_status: str,
    row: pd.Series,
    left_vol, right_vol,
    left_wap, right_wap,
    left_label: str,
    right_label: str,
) -> dict:
    vol_delta = None
    if left_vol is not None and right_vol is not None:
        try:
            vol_delta = float(left_vol) - float(right_vol)
        except (TypeError, ValueError):
            pass

    wap_delta = None
    if left_wap is not None and right_wap is not None:
        try:
            wap_delta = float(left_wap) - float(right_wap)
        except (TypeError, ValueError):
            pass

    return {
        "run_id":                 run_id,
        "recon_status":           recon_status,
        "trade_date":             row.get("trade_date"),
        "side":                   row.get("side"),
        "counterparty_canonical": row.get("counterparty_canonical"),
        "product_canonical":      row.get("product_canonical"),
        "vintage_canonical":      row.get("vintage_canonical"),
        f"{left_label}_volume":   _nullable_float(left_vol),
        f"{right_label}_volume":  _nullable_float(right_vol),
        "volume_delta":           vol_delta,
        f"{left_label}_wap":      _nullable_float(left_wap),
        f"{right_label}_wap":     _nullable_float(right_wap),
        "wap_delta":              wap_delta,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def reconcile_cross_system(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
) -> dict[str, int]:
    """MO vs Fuels â†’ cross_recon."""
    return _reconcile_two_tables(
        conn, run_id,
        left_table="mo_legs", right_table="fuels_lines",
        dest_table="cross_recon",
        left_label="mo", right_label="fuels",
    )


def reconcile_mgmt_vs_fuels(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
) -> dict[str, int]:
    """Management vs Fuels â†’ mgmt_cross_recon."""
    return _reconcile_two_tables(
        conn, run_id,
        left_table="mgmt_legs", right_table="fuels_lines",
        dest_table="mgmt_cross_recon",
        left_label="mgmt", right_label="fuels",
    )


# ---------------------------------------------------------------------------
# Three-way book analysis
# ---------------------------------------------------------------------------

ICE_COUNTERPARTIES = {
    "ICE U.S. OTC Commodity Markets, LLC",
    "ICE U.S. OTC Commodity Markets",
}

ICE_WAP_TOLERANCE = 0.01


def analyze_book_placement(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
) -> dict[str, int]:
    """
    Three-way join of MO, Management, and Fuels trade buckets.

    Classifies each bucket by which books it appears on and flags likely
    ICE management trades in the Fuels data.
    """
    mo = _aggregate_table(conn, run_id, "mo_legs")
    mgmt = _aggregate_table(conn, run_id, "mgmt_legs")
    fuels = _aggregate_table(conn, run_id, "fuels_lines")

    for df, prefix in [(mo, "mo"), (mgmt, "mgmt"), (fuels, "fuels")]:
        df.rename(columns={
            "total_volume": f"{prefix}_vol",
            "wap": f"{prefix}_wap",
        }, inplace=True)

    # Successive outer joins
    merged = pd.merge(mo, mgmt, on=BUCKET_KEY, how="outer", indicator=True)
    merged["_merge_1"] = merged["_merge"]
    merged.drop(columns=["_merge"], inplace=True)

    merged = pd.merge(merged, fuels, on=BUCKET_KEY, how="outer", indicator=True)
    merged["_merge_2"] = merged["_merge"]
    merged.drop(columns=["_merge"], inplace=True)

    # Build set of ICE wash pairs in Fuels for flagging
    ice_wash_keys = _detect_ice_wash_pairs(fuels)

    records = []
    for _, row in merged.iterrows():
        has_mo = _not_null(row.get("mo_vol"))
        has_mgmt = _not_null(row.get("mgmt_vol"))
        has_fuels = _not_null(row.get("fuels_vol"))

        if has_mo and has_mgmt and has_fuels:
            flag = "all_three"
        elif has_mo and has_fuels:
            flag = "mo_and_fuels"
        elif has_mgmt and has_fuels:
            flag = "mgmt_and_fuels"
        elif has_mo and has_mgmt:
            flag = "mo_and_mgmt"
        elif has_mo:
            flag = "mo_only"
        elif has_mgmt:
            flag = "mgmt_only"
        else:
            flag = "fuels_only"

        bkey = (
            row.get("trade_date"),
            row.get("counterparty_canonical"),
            row.get("product_canonical"),
            row.get("vintage_canonical"),
        )
        ice_flag = bkey in ice_wash_keys

        records.append({
            "run_id":                 run_id,
            "book_flag":              flag,
            "ice_mgmt_flag":          ice_flag,
            "trade_date":             row.get("trade_date"),
            "side":                   row.get("side"),
            "counterparty_canonical": row.get("counterparty_canonical"),
            "product_canonical":      row.get("product_canonical"),
            "vintage_canonical":      row.get("vintage_canonical"),
            "mo_volume":              _nullable_float(row.get("mo_vol")),
            "mo_wap":                 _nullable_float(row.get("mo_wap")),
            "mgmt_volume":            _nullable_float(row.get("mgmt_vol")),
            "mgmt_wap":              _nullable_float(row.get("mgmt_wap")),
            "fuels_volume":           _nullable_float(row.get("fuels_vol")),
            "fuels_wap":              _nullable_float(row.get("fuels_wap")),
        })

    if records:
        analysis_df = pd.DataFrame(records)
        conn.execute("INSERT INTO book_analysis SELECT * FROM analysis_df")

    counts: dict[str, int] = {}
    for r in records:
        f = r["book_flag"]
        counts[f] = counts.get(f, 0) + 1
    return counts


def _not_null(v) -> bool:
    if v is None:
        return False
    if isinstance(v, float) and (pd.isna(v) or np.isnan(v)):
        return False
    return True


def _detect_ice_wash_pairs(fuels_df: pd.DataFrame) -> set[tuple]:
    """
    Find Fuels buckets where ICE appears as counterparty on both Buy and Sell
    for the same (trade_date, product, vintage) with approximately equal WAP.
    Returns a set of (trade_date, counterparty, product, vintage) keys that
    belong to likely ICE management wash trades.
    """
    if fuels_df.empty:
        return set()

    ice = fuels_df[
        fuels_df["counterparty_canonical"].isin(ICE_COUNTERPARTIES)
    ].copy()

    if ice.empty:
        return set()

    join_key = ["trade_date", "product_canonical", "vintage_canonical"]
    buys = ice[ice["side"] == "Buy"][join_key + ["fuels_vol", "fuels_wap"]]
    sells = ice[ice["side"] == "Sell"][join_key + ["fuels_vol", "fuels_wap"]]

    if buys.empty or sells.empty:
        return set()

    pairs = pd.merge(buys, sells, on=join_key, suffixes=("_buy", "_sell"))

    result = set()
    for _, p in pairs.iterrows():
        buy_wap = abs(p["fuels_wap_buy"]) if _not_null(p.get("fuels_wap_buy")) else None
        sell_wap = abs(p["fuels_wap_sell"]) if _not_null(p.get("fuels_wap_sell")) else None
        if buy_wap is not None and sell_wap is not None:
            if abs(buy_wap - sell_wap) <= ICE_WAP_TOLERANCE:
                for cp in ICE_COUNTERPARTIES:
                    result.add((
                        p["trade_date"],
                        cp,
                        p["product_canonical"],
                        p["vintage_canonical"],
                    ))
    return result

