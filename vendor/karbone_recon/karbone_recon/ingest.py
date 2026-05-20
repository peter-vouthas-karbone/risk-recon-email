"""
Ingest raw CSV files into immutable DuckDB snapshot tables.

For the MO sheet (rins_tradesheet.csv):
  - Reads only the ~16 business-relevant columns by name.
  - Pandas auto-renames duplicate headers (Date→Date/Date.1, Volume→Volume/Volume.1, etc.)
  - Drops rows where Position Type == "Position" (LONG/SHORT POSITION rows).

For the Fuels sheet (fuels_tradesheet.csv):
  - Reads all 31 columns; stores each row as raw JSON with a SHA-256 hash.
"""

import hashlib
import json
from datetime import date, datetime
from pathlib import Path

import duckdb
import pandas as pd

# Columns we care about in the MO sheet (pandas post-rename names for duplicates)
MO_COLUMNS = [
    "Trade ID",
    "Date",          # seller leg trade date
    "Seller",
    "Volume",        # seller leg volume
    "Price",         # seller leg price
    "Shipment In",   # seller leg delivery
    "Date.1",        # buyer leg trade date  (pandas-renamed duplicate)
    "Buyer",
    "Volume.1",      # buyer leg volume
    "Price.1",       # buyer leg price
    "Shipment out",  # buyer leg delivery
    "Product",
    "Vintage",
    "Platform",
    "Notes",
    "Position Type",
]


def _row_hash(d: dict) -> str:
    serialized = json.dumps(d, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()


def load_mo(
    filepath: str | Path,
    run_id: str,
    business_date: date,
    ingestion_timestamp: datetime,
    conn: duckdb.DuckDBPyConnection,
) -> pd.DataFrame:
    """
    Read the MO tradesheet, store immutable raw snapshots, return filtered DataFrame
    with only the relevant columns (after deduplication / position filtering).
    """
    filepath = Path(filepath)

    # Some MO exports have a summary row above the real header row.
    # Try header=0 first; if key columns are missing, retry with header=1.
    raw_df = pd.read_csv(filepath, header=0, low_memory=False, dtype=str)
    if "Trade ID" not in raw_df.columns:
        raw_df = pd.read_csv(filepath, header=1, low_memory=False, dtype=str)

    # Verify the columns we need are present.
    missing = [c for c in MO_COLUMNS if c not in raw_df.columns]
    if missing:
        raise ValueError(
            f"MO file is missing expected columns after pandas rename: {missing}\n"
            f"Actual columns: {list(raw_df.columns)}"
        )

    # Drop position rows before storing snapshots (they are not trades)
    df = raw_df[raw_df["Position Type"].fillna("").str.strip() != "Position"].copy()
    df = df.reset_index(drop=True)

    # Insert raw snapshot rows
    rows = []
    for idx, row in df.iterrows():
        row_dict = row.to_dict()
        rows.append({
            "run_id": run_id,
            "business_date": business_date,
            "ingestion_timestamp": ingestion_timestamp,
            "source_file_name": filepath.name,
            "source_row_number": int(idx) + 2,  # +2: 1-based + header row
            "raw_row_json": json.dumps(row_dict, default=str),
            "row_hash": _row_hash(row_dict),
        })

    if rows:
        snapshot_df = pd.DataFrame(rows)
        conn.execute(
            "INSERT INTO raw_mo_snapshots SELECT * FROM snapshot_df",
        )

    # Return only the columns needed downstream
    return df[MO_COLUMNS].copy()


def load_mgmt(
    filepath: str | Path,
    run_id: str,
    business_date: date,
    ingestion_timestamp: datetime,
    conn: duckdb.DuckDBPyConnection,
) -> pd.DataFrame:
    """
    Read the Management tradesheet (same layout as MO), store immutable raw
    snapshots in raw_mgmt_snapshots, return filtered DataFrame.
    """
    filepath = Path(filepath)

    raw_df = pd.read_csv(filepath, header=0, low_memory=False, dtype=str)
    if "Trade ID" not in raw_df.columns:
        raw_df = pd.read_csv(filepath, header=1, low_memory=False, dtype=str)

    missing = [c for c in MO_COLUMNS if c not in raw_df.columns]
    if missing:
        raise ValueError(
            f"Mgmt file is missing expected columns: {missing}\n"
            f"Actual columns: {list(raw_df.columns)}"
        )

    df = raw_df[raw_df["Position Type"].fillna("").str.strip() != "Position"].copy()
    df = df.reset_index(drop=True)

    rows = []
    for idx, row in df.iterrows():
        row_dict = row.to_dict()
        rows.append({
            "run_id": run_id,
            "business_date": business_date,
            "ingestion_timestamp": ingestion_timestamp,
            "source_file_name": filepath.name,
            "source_row_number": int(idx) + 2,
            "raw_row_json": json.dumps(row_dict, default=str),
            "row_hash": _row_hash(row_dict),
        })

    if rows:
        snapshot_df = pd.DataFrame(rows)
        conn.execute(
            "INSERT INTO raw_mgmt_snapshots SELECT * FROM snapshot_df",
        )

    return df[MO_COLUMNS].copy()


def load_fuels(
    filepath: str | Path,
    run_id: str,
    business_date: date,
    ingestion_timestamp: datetime,
    conn: duckdb.DuckDBPyConnection,
) -> pd.DataFrame:
    """
    Read the Fuels tradesheet, store immutable raw snapshots, return full DataFrame.
    """
    filepath = Path(filepath)
    df = pd.read_csv(filepath, header=0, low_memory=False, dtype=str)
    df.columns = df.columns.str.strip()
    df = df.reset_index(drop=True)

    rows = []
    for idx, row in df.iterrows():
        row_dict = row.to_dict()
        rows.append({
            "run_id": run_id,
            "business_date": business_date,
            "ingestion_timestamp": ingestion_timestamp,
            "source_file_name": filepath.name,
            "source_row_number": int(idx) + 2,
            "raw_row_json": json.dumps(row_dict, default=str),
            "row_hash": _row_hash(row_dict),
        })

    if rows:
        snapshot_df = pd.DataFrame(rows)
        conn.execute(
            "INSERT INTO raw_fuels_snapshots SELECT * FROM snapshot_df",
        )

    return df
