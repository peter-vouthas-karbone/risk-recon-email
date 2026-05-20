"""
Prepare the combined ERCOT ancillary price file for the PnL pipeline.

Merges historical ancillary marks from the ERCOT forward curve dataset
(terms through 2025-12-31) with forward-looking marks from the Karbone
ancillary file (terms from 2026-01-01 onward), writing the result to the
production ancillary price file consumed by the pipeline.
"""

import logging
import os

import pandas as pd

logger = logging.getLogger('pnl.' + __name__)

# Ancillary product codes recognised by the pipeline
ANCILLARY_PRODUCTS = {'ENS', 'ECR', 'ECY', 'ERK', 'ERD'}

# Split date: fwd-curve rows with mark date on or before this date are kept;
# Karbone rows with mark date on or after this date are kept.
FWD_CURVE_CUTOFF = pd.Timestamp('2025-12-31')
KARBONE_CUTOFF = pd.Timestamp('2026-01-01')

# Column mapping from ercot_forward_curve_data.csv to the ancillary schema
_FWD_CURVE_COL_MAP = {
    'AS_OF_DATE': 'Date',
    'EXPIRATION_DATE': 'Term',
    'CONTRACT': 'Code',
    'SETTLE': 'Mark',
}


def prepare_ercot_ancillary(
    ercot_fwd_curve_path: str,
    karbone_source_path: str,
    output_path: str,
) -> bool:
    """
    Combine ERCOT forward curve (mark dates <= 2025-12-31) and Karbone ancillary
    (mark dates >= 2026-01-01) into a single production-ready CSV.

    Args:
        ercot_fwd_curve_path: Path to ercot_forward_curve_data.csv.
        karbone_source_path:  Path to the source Karbone ancillary CSV
                              (Date, Term, Code, Mark columns).
        output_path:          Destination path for the combined output file.

    Returns:
        True on success, False if any required input is missing or unreadable.
    """
    # --- input validation ---------------------------------------------------
    for label, path in [
        ('ERCOT forward curve', ercot_fwd_curve_path),
        ('Karbone ancillary source', karbone_source_path),
    ]:
        if not os.path.exists(path):
            logger.error(f"prepare_ercot_ancillary: {label} file not found: {path}")
            return False
        if os.path.getsize(path) == 0:
            logger.error(f"prepare_ercot_ancillary: {label} file is empty: {path}")
            return False

    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.isdir(output_dir):
        logger.error(
            f"prepare_ercot_ancillary: output directory does not exist: {output_dir}"
        )
        return False

    try:
        # --- ERCOT forward curve (mark date <= 2025-12-31) ----------------
        logger.info(f"Loading ERCOT forward curve from: {ercot_fwd_curve_path}")
        ercot_df = pd.read_csv(
            ercot_fwd_curve_path,
            usecols=list(_FWD_CURVE_COL_MAP.keys()),
        )

        ercot_df = ercot_df.rename(columns=_FWD_CURVE_COL_MAP)
        ercot_df['Date'] = pd.to_datetime(ercot_df['Date'], format='mixed')
        ercot_df['Term'] = pd.to_datetime(ercot_df['Term'], format='mixed')

        # Keep only ancillary product codes with mark date through end of 2025
        ercot_df = ercot_df[ercot_df['Code'].isin(ANCILLARY_PRODUCTS)]
        ercot_df = ercot_df[ercot_df['Date'] <= FWD_CURVE_CUTOFF]
        ercot_df['Term'] = ercot_df['Term'].dt.strftime('%Y-%m-%d')
        logger.info(
            f"  ERCOT forward curve: {len(ercot_df)} ancillary rows "
            f"with mark date <= {FWD_CURVE_CUTOFF.date()}"
        )

        # --- Karbone ancillary (forward-looking, >= 2026-01-01) -------------
        logger.info(f"Loading Karbone ancillary source from: {karbone_source_path}")
        karbone_df = pd.read_csv(karbone_source_path)

        required_cols = {'Date', 'Term', 'Code', 'Mark'}
        missing = required_cols - set(karbone_df.columns)
        if missing:
            logger.error(
                f"prepare_ercot_ancillary: Karbone source missing columns: {missing}"
            )
            return False

        karbone_df['Date'] = pd.to_datetime(karbone_df['Date'], format='mixed')
        karbone_df['Term'] = pd.to_datetime(karbone_df['Term'], format='mixed')
        karbone_df = karbone_df[karbone_df['Date'] >= KARBONE_CUTOFF]
        karbone_df['Term'] = karbone_df['Term'].dt.strftime('%Y-%m-%d')
        logger.info(
            f"  Karbone ancillary: {len(karbone_df)} rows "
            f"with mark date >= {KARBONE_CUTOFF.date()}"
        )

        # --- combine and write ----------------------------------------------
        combined = pd.concat(
            [ercot_df[['Date', 'Term', 'Code', 'Mark']],
             karbone_df[['Date', 'Term', 'Code', 'Mark']]],
            ignore_index=True,
        )

        combined.to_csv(output_path, index=False)
        logger.info(
            f"prepare_ercot_ancillary: wrote {len(combined)} rows to {output_path}"
        )
        return True

    except (OSError, pd.errors.ParserError, KeyError, ValueError) as exc:
        logger.error(f"prepare_ercot_ancillary: failed with error: {exc}")
        return False
