"""
Inter-day trade stability audit â€” generic grouped bucket comparison.

For each run, rows from a source table (mo_legs or fuels_lines) are aggregated
into buckets keyed by:
  (trade_date, side, counterparty_canonical, product_canonical, vintage_canonical)

Per bucket we compute:
  - total_volume = SUM(quantity)
  - weighted_avg_price = SUM(quantity * price) / SUM(quantity)  [null/zero-safe]

The current run's buckets are full-outer-joined against the prior run's buckets.
Buckets that appear only in the current run are further classified:
  - new_trade      : trade_date == business_date (first booked today)
  - added_trade    : trade_date < business_date (backdated / retroactive addition)
Buckets that appear only in the prior run  â†’ removed_trade.
Buckets in both runs where volume or WAP differs â†’ modified_trade.
Unchanged buckets produce no output.
"""

from datetime import date
from typing import Optional

import duckdb
import pandas as pd

from karbone_recon.mappings import RECON_EXCLUDED_PRODUCTS


BUCKET_KEY = [
    "trade_date",
    "side",
    "counterparty_canonical",
    "product_canonical",
    "vintage_canonical",
]

WAP_TOLERANCE = 1e-6
VOL_TOLERANCE = 1e-6


def get_prior_run_id(
    conn: duckdb.DuckDBPyConnection,
    current_run_id: str,
) -> Optional[str]:
    """Return the run_id of the most recent run before the current one, or None."""
    result = conn.execute("""
        SELECT run_id
        FROM runs
        WHERE run_id != ?
        ORDER BY ingestion_timestamp DESC
        LIMIT 1
    """, [current_run_id]).fetchone()
    return result[0] if result else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_mo_drift(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    prior_run_id: Optional[str],
    business_date: Optional[date] = None,
) -> int:
    """MO tradesheet day-over-day drift detection."""
    return _detect_drift(conn, run_id, prior_run_id, business_date,
                         source_table="mo_legs",
                         drift_table="mo_trade_drift")


def detect_fuels_drift(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    prior_run_id: Optional[str],
    business_date: Optional[date] = None,
) -> int:
    """Fuels tradesheet day-over-day drift detection."""
    return _detect_drift(conn, run_id, prior_run_id, business_date,
                         source_table="fuels_lines",
                         drift_table="fuels_trade_drift")


def detect_mgmt_drift(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    prior_run_id: Optional[str],
    business_date: Optional[date] = None,
) -> int:
    """Management tradesheet day-over-day drift detection."""
    return _detect_drift(conn, run_id, prior_run_id, business_date,
                         source_table="mgmt_legs",
                         drift_table="mgmt_trade_drift")


# ---------------------------------------------------------------------------
# Shared engine
# ---------------------------------------------------------------------------

def _aggregate_table(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    table_name: str,
) -> pd.DataFrame:
    """
    Aggregate rows from *table_name* for a given run into trade buckets.

    counterparty_canonical falls back to counterparty_raw when NULL so that
    unmapped counterparties still form a deterministic bucket key.
    """
    excluded = ", ".join(f"'{p}'" for p in RECON_EXCLUDED_PRODUCTS)
    df = conn.execute(f"""
        SELECT
            trade_date,
            side,
            COALESCE(counterparty_canonical, counterparty_raw) AS counterparty_canonical,
            COALESCE(product_canonical, product_raw)           AS product_canonical,
            vintage_canonical,
            SUM(quantity)                                       AS total_volume,
            CASE
                WHEN SUM(quantity) IS NULL OR SUM(quantity) = 0 THEN NULL
                ELSE ROUND(SUM(quantity * price) / SUM(quantity), 5)
            END                                                 AS wap
        FROM {table_name}
        WHERE run_id = ?
          AND COALESCE(product_canonical, product_raw) NOT IN ({excluded})
        GROUP BY
            trade_date,
            side,
            COALESCE(counterparty_canonical, counterparty_raw),
            COALESCE(product_canonical, product_raw),
            vintage_canonical
    """, [run_id]).df()
    return df


def _detect_drift(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    prior_run_id: Optional[str],
    business_date: Optional[date],
    source_table: str,
    drift_table: str,
) -> int:
    """
    Compare current run's trade buckets to the prior run's trade buckets
    for a given source table. Inserts change records into drift_table.
    Returns the count of change records inserted.
    """
    if prior_run_id is None:
        return 0

    current = _aggregate_table(conn, run_id, source_table)
    prior = _aggregate_table(conn, prior_run_id, source_table)

    current = current.rename(columns={"total_volume": "cur_vol", "wap": "cur_wap"})
    prior   = prior.rename(columns={"total_volume": "pri_vol", "wap": "pri_wap"})

    merged = pd.merge(current, prior, on=BUCKET_KEY, how="outer", indicator=True)

    records = []

    for _, row in merged.iterrows():
        src = row["_merge"]

        if src == "left_only":
            change_type = _classify_new_bucket(row.get("trade_date"), business_date)
            records.append(_record(run_id, prior_run_id, row, change_type,
                                   prior_vol=None, cur_vol=row["cur_vol"],
                                   prior_wap=None, cur_wap=row["cur_wap"]))

        elif src == "right_only":
            records.append(_record(run_id, prior_run_id, row, "removed_trade",
                                   prior_vol=row["pri_vol"], cur_vol=None,
                                   prior_wap=row["pri_wap"], cur_wap=None))

        else:  # both
            vol_changed = not _approx_equal(row["cur_vol"], row["pri_vol"], VOL_TOLERANCE)
            wap_changed = not _approx_equal(row["cur_wap"], row["pri_wap"], WAP_TOLERANCE)
            if vol_changed or wap_changed:
                records.append(_record(run_id, prior_run_id, row, "modified_trade",
                                       prior_vol=row["pri_vol"], cur_vol=row["cur_vol"],
                                       prior_wap=row["pri_wap"], cur_wap=row["cur_wap"]))

    if records:
        drift_df = pd.DataFrame(records)
        conn.execute(f"INSERT INTO {drift_table} SELECT * FROM drift_df")

    return len(records)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _classify_new_bucket(
    trade_date,
    business_date: Optional[date],
) -> str:
    """
    Return 'new_trade' if trade_date matches business_date (booked today),
    otherwise 'added_trade' (backdated / retroactive addition).
    Falls back to 'added_trade' when either value is unavailable.
    """
    if business_date is None or trade_date is None:
        return "added_trade"
    try:
        td = pd.Timestamp(trade_date).date()
        return "new_trade" if td == business_date else "added_trade"
    except Exception:
        return "added_trade"


def _approx_equal(a, b, tol: float) -> bool:
    """True when both are null, or both are numeric and within tol of each other."""
    a_null = a is None or (isinstance(a, float) and pd.isna(a))
    b_null = b is None or (isinstance(b, float) and pd.isna(b))
    if a_null and b_null:
        return True
    if a_null or b_null:
        return False
    return abs(float(a) - float(b)) <= tol


def _record(
    run_id: str,
    prior_run_id: str,
    row: pd.Series,
    change_type: str,
    prior_vol, cur_vol,
    prior_wap, cur_wap,
) -> dict:
    vol_delta = None
    if cur_vol is not None and prior_vol is not None:
        try:
            vol_delta = float(cur_vol) - float(prior_vol)
        except (TypeError, ValueError):
            vol_delta = None

    wap_delta = None
    if cur_wap is not None and prior_wap is not None:
        try:
            wap_delta = float(cur_wap) - float(prior_wap)
        except (TypeError, ValueError):
            wap_delta = None

    return {
        "run_id":                 run_id,
        "prior_run_id":           prior_run_id,
        "change_type":            change_type,
        "trade_date":             row.get("trade_date"),
        "side":                   row.get("side"),
        "counterparty_canonical": row.get("counterparty_canonical"),
        "product_canonical":      row.get("product_canonical"),
        "vintage_canonical":      row.get("vintage_canonical"),
        "prior_volume":           _nullable_float(prior_vol),
        "current_volume":         _nullable_float(cur_vol),
        "volume_delta":           vol_delta,
        "prior_wap":              _nullable_float(prior_wap),
        "current_wap":            _nullable_float(cur_wap),
        "wap_delta":              wap_delta,
    }


def _nullable_float(v) -> Optional[float]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

