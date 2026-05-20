"""
Output CSV generation for Phase 1 and Phase 2.

All outputs go to output/YYYY-MM-DD/ under the project root.
If multiple runs happen on the same day, files are overwritten (last run wins).
"""

from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

OUTPUT_ROOT = Path(__file__).parent.parent / "output"


def _output_dir(business_date: date) -> Path:
    d = OUTPUT_ROOT / str(business_date)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Phase 1 outputs
# ---------------------------------------------------------------------------

def write_mo_legs(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    business_date: date,
) -> Path:
    """All normalized MO legs for this run."""
    df = conn.execute("""
        SELECT
            mo_leg_id,
            mo_trade_group_id,
            leg_type,
            side,
            trade_date,
            counterparty_raw,
            counterparty_canonical,
            product_raw,
            product_canonical,
            vintage_raw,
            vintage_canonical,
            quantity,
            price,
            delivery_raw,
            delivery_match_date
        FROM mo_legs
        WHERE run_id = ?
        ORDER BY mo_trade_group_id, leg_type
    """, [run_id]).df()

    path = _output_dir(business_date) / "mo_legs.csv"
    df.to_csv(path, index=False)
    return path


def write_mo_drift(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    business_date: date,
) -> Path:
    """Grouped MO bucket changes vs prior run (volume and WAP deltas)."""
    df = conn.execute("""
        SELECT
            change_type,
            trade_date,
            side,
            counterparty_canonical,
            product_canonical,
            vintage_canonical,
            prior_volume,
            current_volume,
            volume_delta,
            prior_wap,
            current_wap,
            wap_delta,
            prior_run_id
        FROM mo_trade_drift
        WHERE run_id = ?
        ORDER BY
            CASE change_type
                WHEN 'new_trade'       THEN 1
                WHEN 'modified_trade'  THEN 2
                WHEN 'added_trade'     THEN 3
                WHEN 'removed_trade'   THEN 4
                ELSE 5
            END,
            trade_date, side, counterparty_canonical,
            product_canonical, vintage_canonical
    """, [run_id]).df()

    path = _output_dir(business_date) / "mo_drift.csv"
    df.to_csv(path, index=False)
    return path


def write_fuels_drift(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    business_date: date,
) -> Path:
    """Fuels trade bucket changes vs prior run (volume and WAP deltas)."""
    df = conn.execute("""
        SELECT
            change_type,
            trade_date,
            side,
            counterparty_canonical,
            product_canonical,
            vintage_canonical,
            prior_volume,
            current_volume,
            volume_delta,
            prior_wap,
            current_wap,
            wap_delta,
            prior_run_id
        FROM fuels_trade_drift
        WHERE run_id = ?
        ORDER BY
            CASE change_type
                WHEN 'new_trade'       THEN 1
                WHEN 'modified_trade'  THEN 2
                WHEN 'added_trade'     THEN 3
                WHEN 'removed_trade'   THEN 4
                ELSE 5
            END,
            trade_date, side, counterparty_canonical,
            product_canonical, vintage_canonical
    """, [run_id]).df()

    path = _output_dir(business_date) / "fuels_drift.csv"
    df.to_csv(path, index=False)
    return path


def write_unmapped(
    unmapped_list: list[dict],
    business_date: date,
) -> Path:
    """All unmapped counterparties/products from this run."""
    df = pd.DataFrame(unmapped_list) if unmapped_list else pd.DataFrame(
        columns=["type", "source_system", "raw_value"]
    )
    path = _output_dir(business_date) / "unmapped_values.csv"
    df.to_csv(path, index=False)
    return path


def write_mgmt_legs(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    business_date: date,
) -> Path:
    """All normalized Management legs for this run."""
    df = conn.execute("""
        SELECT
            mgmt_leg_id,
            mgmt_trade_group_id,
            leg_type,
            side,
            trade_date,
            counterparty_raw,
            counterparty_canonical,
            product_raw,
            product_canonical,
            vintage_raw,
            vintage_canonical,
            quantity,
            price,
            delivery_raw,
            delivery_match_date
        FROM mgmt_legs
        WHERE run_id = ?
        ORDER BY mgmt_trade_group_id, leg_type
    """, [run_id]).df()

    path = _output_dir(business_date) / "mgmt_legs.csv"
    df.to_csv(path, index=False)
    return path


def write_mgmt_drift(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    business_date: date,
) -> Path:
    """Management trade bucket changes vs prior run."""
    df = conn.execute("""
        SELECT
            change_type,
            trade_date,
            side,
            counterparty_canonical,
            product_canonical,
            vintage_canonical,
            prior_volume,
            current_volume,
            volume_delta,
            prior_wap,
            current_wap,
            wap_delta,
            prior_run_id
        FROM mgmt_trade_drift
        WHERE run_id = ?
        ORDER BY
            CASE change_type
                WHEN 'new_trade'       THEN 1
                WHEN 'modified_trade'  THEN 2
                WHEN 'added_trade'     THEN 3
                WHEN 'removed_trade'   THEN 4
                ELSE 5
            END,
            trade_date, side, counterparty_canonical,
            product_canonical, vintage_canonical
    """, [run_id]).df()

    path = _output_dir(business_date) / "mgmt_drift.csv"
    df.to_csv(path, index=False)
    return path


def write_run_summary(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    business_date: date,
    unmapped_count: int,
) -> Path:
    """High-level counts for this run."""
    mo_row_count = conn.execute(
        "SELECT COUNT(*) FROM mo_rows WHERE run_id = ?", [run_id]
    ).fetchone()[0]

    mo_leg_count = conn.execute(
        "SELECT COUNT(*) FROM mo_legs WHERE run_id = ?", [run_id]
    ).fetchone()[0]

    fuels_line_count = conn.execute(
        "SELECT COUNT(*) FROM fuels_lines WHERE run_id = ?", [run_id]
    ).fetchone()[0]

    new_trades = conn.execute(
        "SELECT COUNT(*) FROM mo_trade_drift WHERE run_id = ? AND change_type = 'new_trade'",
        [run_id]
    ).fetchone()[0]

    drift_added = conn.execute(
        "SELECT COUNT(*) FROM mo_trade_drift WHERE run_id = ? AND change_type = 'added_trade'",
        [run_id]
    ).fetchone()[0]

    drift_removed = conn.execute(
        "SELECT COUNT(*) FROM mo_trade_drift WHERE run_id = ? AND change_type = 'removed_trade'",
        [run_id]
    ).fetchone()[0]

    drift_modified = conn.execute(
        "SELECT COUNT(*) FROM mo_trade_drift WHERE run_id = ? AND change_type = 'modified_trade'",
        [run_id]
    ).fetchone()[0]

    fuels_new_trades = conn.execute(
        "SELECT COUNT(*) FROM fuels_trade_drift WHERE run_id = ? AND change_type = 'new_trade'",
        [run_id]
    ).fetchone()[0]

    fuels_drift_added = conn.execute(
        "SELECT COUNT(*) FROM fuels_trade_drift WHERE run_id = ? AND change_type = 'added_trade'",
        [run_id]
    ).fetchone()[0]

    fuels_drift_removed = conn.execute(
        "SELECT COUNT(*) FROM fuels_trade_drift WHERE run_id = ? AND change_type = 'removed_trade'",
        [run_id]
    ).fetchone()[0]

    fuels_drift_modified = conn.execute(
        "SELECT COUNT(*) FROM fuels_trade_drift WHERE run_id = ? AND change_type = 'modified_trade'",
        [run_id]
    ).fetchone()[0]

    recon_matched = conn.execute(
        "SELECT COUNT(*) FROM cross_recon WHERE run_id = ? AND recon_status = 'matched'",
        [run_id]
    ).fetchone()[0]

    recon_volume_breaks = conn.execute(
        "SELECT COUNT(*) FROM cross_recon WHERE run_id = ? AND recon_status = 'volume_break'",
        [run_id]
    ).fetchone()[0]

    recon_price_breaks = conn.execute(
        "SELECT COUNT(*) FROM cross_recon WHERE run_id = ? AND recon_status = 'price_break'",
        [run_id]
    ).fetchone()[0]

    recon_vol_and_price_breaks = conn.execute(
        "SELECT COUNT(*) FROM cross_recon WHERE run_id = ? AND recon_status = 'volume_and_price_break'",
        [run_id]
    ).fetchone()[0]

    recon_mo_only = conn.execute(
        "SELECT COUNT(*) FROM cross_recon WHERE run_id = ? AND recon_status = 'mo_only'",
        [run_id]
    ).fetchone()[0]

    recon_fuels_only = conn.execute(
        "SELECT COUNT(*) FROM cross_recon WHERE run_id = ? AND recon_status = 'fuels_only'",
        [run_id]
    ).fetchone()[0]

    # Management counts (safe — returns 0 if table is empty)
    mgmt_leg_count = conn.execute(
        "SELECT COUNT(*) FROM mgmt_legs WHERE run_id = ?", [run_id]
    ).fetchone()[0]

    mgmt_new_trades = conn.execute(
        "SELECT COUNT(*) FROM mgmt_trade_drift WHERE run_id = ? AND change_type = 'new_trade'",
        [run_id]
    ).fetchone()[0]

    mgmt_drift_added = conn.execute(
        "SELECT COUNT(*) FROM mgmt_trade_drift WHERE run_id = ? AND change_type = 'added_trade'",
        [run_id]
    ).fetchone()[0]

    mgmt_drift_removed = conn.execute(
        "SELECT COUNT(*) FROM mgmt_trade_drift WHERE run_id = ? AND change_type = 'removed_trade'",
        [run_id]
    ).fetchone()[0]

    mgmt_drift_modified = conn.execute(
        "SELECT COUNT(*) FROM mgmt_trade_drift WHERE run_id = ? AND change_type = 'modified_trade'",
        [run_id]
    ).fetchone()[0]

    mgmt_recon_matched = conn.execute(
        "SELECT COUNT(*) FROM mgmt_cross_recon WHERE run_id = ? AND recon_status = 'matched'",
        [run_id]
    ).fetchone()[0]

    mgmt_recon_breaks = conn.execute(
        "SELECT COUNT(*) FROM mgmt_cross_recon WHERE run_id = ? AND recon_status != 'matched'",
        [run_id]
    ).fetchone()[0]

    # Book analysis counts
    def _book_count(flag: str) -> int:
        return conn.execute(
            "SELECT COUNT(*) FROM book_analysis WHERE run_id = ? AND book_flag = ?",
            [run_id, flag]
        ).fetchone()[0]

    book_all_three = _book_count("all_three")
    book_mo_and_fuels = _book_count("mo_and_fuels")
    book_mgmt_and_fuels = _book_count("mgmt_and_fuels")
    book_mo_and_mgmt = _book_count("mo_and_mgmt")
    book_mo_only = _book_count("mo_only")
    book_mgmt_only = _book_count("mgmt_only")
    book_fuels_only = _book_count("fuels_only")

    ice_mgmt_flagged = conn.execute(
        "SELECT COUNT(*) FROM book_analysis WHERE run_id = ? AND ice_mgmt_flag = TRUE",
        [run_id]
    ).fetchone()[0]

    summary = {
        "run_id": [run_id],
        "business_date": [str(business_date)],
        "mo_source_rows": [mo_row_count],
        "mo_legs": [mo_leg_count],
        "mgmt_legs": [mgmt_leg_count],
        "fuels_lines": [fuels_line_count],
        "mo_new_trades": [new_trades],
        "mo_drift_added_trades": [drift_added],
        "mo_drift_removed_trades": [drift_removed],
        "mo_drift_modified_trades": [drift_modified],
        "fuels_new_trades": [fuels_new_trades],
        "fuels_drift_added_trades": [fuels_drift_added],
        "fuels_drift_removed_trades": [fuels_drift_removed],
        "fuels_drift_modified_trades": [fuels_drift_modified],
        "mgmt_new_trades": [mgmt_new_trades],
        "mgmt_drift_added_trades": [mgmt_drift_added],
        "mgmt_drift_removed_trades": [mgmt_drift_removed],
        "mgmt_drift_modified_trades": [mgmt_drift_modified],
        "recon_matched": [recon_matched],
        "recon_volume_breaks": [recon_volume_breaks],
        "recon_price_breaks": [recon_price_breaks],
        "recon_vol_and_price_breaks": [recon_vol_and_price_breaks],
        "recon_mo_only": [recon_mo_only],
        "recon_fuels_only": [recon_fuels_only],
        "mgmt_recon_matched": [mgmt_recon_matched],
        "mgmt_recon_breaks": [mgmt_recon_breaks],
        "book_all_three": [book_all_three],
        "book_mo_and_fuels": [book_mo_and_fuels],
        "book_mgmt_and_fuels": [book_mgmt_and_fuels],
        "book_mo_and_mgmt": [book_mo_and_mgmt],
        "book_mo_only": [book_mo_only],
        "book_mgmt_only": [book_mgmt_only],
        "book_fuels_only": [book_fuels_only],
        "ice_mgmt_flagged": [ice_mgmt_flagged],
        "unmapped_values": [unmapped_count],
    }

    df = pd.DataFrame(summary)
    path = _output_dir(business_date) / "run_summary.csv"
    df.to_csv(path, index=False)
    return path


# ---------------------------------------------------------------------------
# Cross-run aggregate outputs
# ---------------------------------------------------------------------------

def write_exceptions_by_day(conn: duckdb.DuckDBPyConnection) -> Path:
    """Exception counts (drift excluding new_trade) per business date and source system."""
    df = conn.execute("""
        SELECT
            r.business_date,
            d.source_system,
            d.change_type AS exception_type,
            COUNT(*) AS count
        FROM (
            SELECT run_id, change_type, 'mo' AS source_system
            FROM mo_trade_drift
            WHERE change_type != 'new_trade'
            UNION ALL
            SELECT run_id, change_type, 'fuels' AS source_system
            FROM fuels_trade_drift
            WHERE change_type != 'new_trade'
            UNION ALL
            SELECT run_id, change_type, 'mgmt' AS source_system
            FROM mgmt_trade_drift
            WHERE change_type != 'new_trade'
        ) d
        JOIN runs r ON r.run_id = d.run_id
        GROUP BY r.business_date, d.source_system, d.change_type
        ORDER BY r.business_date, d.source_system, d.change_type
    """).df()

    path = OUTPUT_ROOT / "exceptions_by_day.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path


# ---------------------------------------------------------------------------
# Phase 2 outputs
# ---------------------------------------------------------------------------

def write_cross_recon(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    business_date: date,
) -> Path:
    """Cross-system MO vs Fuels reconciliation results."""
    df = conn.execute("""
        SELECT
            recon_status,
            trade_date,
            side,
            counterparty_canonical,
            product_canonical,
            vintage_canonical,
            mo_volume,
            fuels_volume,
            volume_delta,
            mo_wap,
            fuels_wap,
            wap_delta
        FROM cross_recon
        WHERE run_id = ?
        ORDER BY
            CASE recon_status
                WHEN 'volume_and_price_break' THEN 1
                WHEN 'volume_break'           THEN 2
                WHEN 'price_break'            THEN 3
                WHEN 'mo_only'                THEN 4
                WHEN 'fuels_only'             THEN 5
                WHEN 'matched'                THEN 6
                ELSE 7
            END,
            trade_date, side, counterparty_canonical,
            product_canonical, vintage_canonical
    """, [run_id]).df()

    path = _output_dir(business_date) / "cross_recon.csv"
    df.to_csv(path, index=False)
    return path


def write_mgmt_cross_recon(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    business_date: date,
) -> Path:
    """Cross-system Management vs Fuels reconciliation results."""
    df = conn.execute("""
        SELECT
            recon_status,
            trade_date,
            side,
            counterparty_canonical,
            product_canonical,
            vintage_canonical,
            mgmt_volume,
            fuels_volume,
            volume_delta,
            mgmt_wap,
            fuels_wap,
            wap_delta
        FROM mgmt_cross_recon
        WHERE run_id = ?
        ORDER BY
            CASE recon_status
                WHEN 'volume_and_price_break' THEN 1
                WHEN 'volume_break'           THEN 2
                WHEN 'price_break'            THEN 3
                WHEN 'mgmt_only'              THEN 4
                WHEN 'fuels_only'             THEN 5
                WHEN 'matched'                THEN 6
                ELSE 7
            END,
            trade_date, side, counterparty_canonical,
            product_canonical, vintage_canonical
    """, [run_id]).df()

    path = _output_dir(business_date) / "mgmt_cross_recon.csv"
    df.to_csv(path, index=False)
    return path


def write_book_analysis(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    business_date: date,
) -> Path:
    """Three-way book placement analysis."""
    df = conn.execute("""
        SELECT
            book_flag,
            ice_mgmt_flag,
            trade_date,
            side,
            counterparty_canonical,
            product_canonical,
            vintage_canonical,
            mo_volume,
            mo_wap,
            mgmt_volume,
            mgmt_wap,
            fuels_volume,
            fuels_wap
        FROM book_analysis
        WHERE run_id = ?
        ORDER BY
            CASE book_flag
                WHEN 'all_three'      THEN 1
                WHEN 'mo_and_mgmt'    THEN 2
                WHEN 'fuels_only'     THEN 3
                WHEN 'mo_only'        THEN 4
                WHEN 'mgmt_only'      THEN 5
                WHEN 'mo_and_fuels'   THEN 6
                WHEN 'mgmt_and_fuels' THEN 7
                ELSE 8
            END,
            ice_mgmt_flag DESC,
            trade_date, side, counterparty_canonical,
            product_canonical, vintage_canonical
    """, [run_id]).df()

    path = _output_dir(business_date) / "book_analysis.csv"
    df.to_csv(path, index=False)
    return path
