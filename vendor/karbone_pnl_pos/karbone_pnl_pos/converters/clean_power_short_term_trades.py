#!/usr/bin/env python3
"""
Power Short-Term Trading: ISO Bid Records to Standardized Format Converter

OVERVIEW
--------
This module provides a configuration-driven framework for converting ISO rolling bid 
records into standardized trade formats suitable for PnL calculations. The architecture
eliminates code duplication by using a single generic processing engine that adapts to
each ISO's specific requirements through configuration.

SUPPORTED ISOs
--------------
- NYISO (New York ISO): Hour shift enabled, direct zone naming
- PJM: No hour shift, zone-to-alias mapping
- MISO: No hour shift, zone-to-alias mapping

ARCHITECTURE
------------
The module uses a three-layer architecture:

1. Configuration Layer (constants.py):
   - ISO_CONFIG dictionary defines ISO-specific column names, hour shift behavior,
     zone mappings, and desk assignments
   - Each ISO's configuration is self-contained and easily maintainable

2. Generic Processing Layer:
   - process_trades_df_generic(): Converts raw bid records to standardized trades
   - process_marks_df_generic(): Converts raw spot pricing to standardized marks
   - convert_iso_bid_records(): Main conversion engine that orchestrates the process

3. Compatibility Layer:
   - convert_nyiso_bid_records(), convert_pjm_bid_records(), convert_miso_bid_records()
   - Legacy function names maintained as thin wrappers for backward compatibility

CONVERSION PROCESS
------------------
For each ISO, the conversion follows these steps:

1. Data Loading:
   - Load bid records CSV (trades with cleared quantities)
   - Load spot pricing CSV (settlement prices)

2. Trade Processing:
   - Parse dates using vectorized operations for performance
   - Handle hour conventions (shift for NYISO, direct for PJM/MISO)
   - Map zones to product names (direct or via alias mapping)
   - Clean and normalize quantities and prices

3. Price Matching:
   - Join trades with marks by date, product, and vintage (hour)
   - Creates complete pricing for position valuation

4. Trade Lifecycle:
   - Entry trades: Original positions from bid records (price = 0)

   - Closure trades: Opposite positions with matched settlement prices
   - Result: Complete trade pairs showing P&L for each position

HOUR SHIFT BEHAVIOR
-------------------
Different ISOs use different hour conventions:

- NYISO: Requires 1-hour shift for hour-ending convention
  * Hour 0 in data â†’ HE01 (Hour Ending 1)
  * Hour 23 in data â†’ HE24 (Hour Ending 24)
  * Hour 23 triggers date rollover to next day

- PJM/MISO: Already in hour-ending format, no shift needed
  * Hour 0 in data â†’ HE01
  * Hour 23 in data â†’ HE24
  * No date rollover required

ZONE MAPPING
------------
ISOs may use different zone naming conventions:

- NYISO: Uses zone names directly as product identifiers
  * Zone "ZONE A" â†’ Product "ZONE A - DART"

- PJM: Maps zone codes to standardized aliases (PJM_ZONE_TO_ALIAS)
  * Zone "DOMINION" â†’ Product "DOM - DART"
  * Zone "DPL_RES_AGG" â†’ Product "DPL - DART"

- MISO: Maps zone hubs to standardized aliases (MISO_ZONE_TO_ALIAS)
  * Zone "ARKANSAS_HUB" â†’ Product "Ark Hub - DART"
  * Zone "ILLINOIS_HUB" â†’ Product "Illinois Hub - DART"

OUTPUT FORMAT
-------------
All conversions produce standardized trade DataFrames with these columns:
- date: Trade date (YYYY-MM-DD)
- product: Product name with " - DART" suffix
- vintage: Hour-ending identifier (HE01 through HE24)
- price: Settlement price (0 for entries, matched price for closures)
- quantity: Position size (positive = long, negative = short)
- desk: Trading desk (power_short_term for all ISOs)
- portfolio: ISO identifier (nyiso, pjm, miso, caiso)
- is_exchange_settled: FALSE (OTC trading)
- is_physically_settled: FALSE (financial settlement)
- is_fee: FALSE (not a fee record)
- fee_type: 'regular'
- currency: 'usd'

ADDING NEW ISOs
---------------
To add support for a new ISO:

1. Add column name constants to constants.py:
   - {ISO}_BID_COL_* for bid record columns
   - {ISO}_SPOT_COL_* for spot pricing columns
   - {ISO}_BID_REQUIRED_COLUMNS list
   - {ISO}_SPOT_REQUIRED_COLUMNS list

2. Add zone mapping if needed:
   - {ISO}_ZONE_TO_ALIAS dictionary (or None for direct naming)

3. Add configuration to ISO_CONFIG in constants.py:
   - Set hour_shift flag (True/False)
   - Define column mappings
   - Specify zone_mapping (dict or None)
   - Set desk assignment

4. Create wrapper function (optional):
   - def convert_{iso}_bid_records(...):
   -     return convert_iso_bid_records('{iso}', ...)

No changes to processing logic are needed - the generic functions handle everything.

USAGE EXAMPLES
--------------
Using ISO-specific wrapper functions (recommended for readability):

    from clean_power_short_term_trades import (
        convert_nyiso_bid_records,
        convert_pjm_bid_records,
        convert_miso_bid_records
    )
    
    # NYISO conversion
    nyiso_df = convert_nyiso_bid_records(
        trades_input_file='nyiso_bid_records.csv',
        marks_input_file='nyiso_spot_pricing.csv',
        min_trade_date='2024-01-01'
    )
    
    # PJM conversion
    pjm_df = convert_pjm_bid_records(
        trades_input_file='pjm_bid_records.csv',
        marks_input_file='pjm_spot_pricing.csv',
        min_trade_date='2024-01-01'
    )
    
    # MISO conversion
    miso_df = convert_miso_bid_records(
        trades_input_file='miso_bid_records.csv',
        marks_input_file='miso_spot_pricing.csv',
        min_trade_date='2024-01-01'
    )

Using generic function directly (useful for dynamic ISO selection):

    from clean_power_short_term_trades import convert_iso_bid_records
    
    iso = 'pjm'  # Could be from configuration
    df = convert_iso_bid_records(
        iso_code=iso,
        trades_input_file=f'{iso}_bid_records.csv',
        marks_input_file=f'{iso}_spot_pricing.csv',
        min_trade_date='2024-01-01'
    )

PERFORMANCE OPTIMIZATIONS
-------------------------
The module uses vectorized pandas operations throughout:
- vectorized_parse_dates(): Fast date parsing using pd.to_datetime
- vectorized_clean_quantity(): Bulk quantity cleaning and conversion
- vectorized_clean_price(): Bulk price cleaning and normalization
- vectorized_hour_to_vintage(): Fast hour-to-vintage conversion
- vectorized_process_hour_shift(): Efficient hour shifting with date rollover

These optimizations provide significant speedups over row-by-row processing,
especially important for large datasets with thousands of hourly records.

TECHNICAL NOTES
---------------
- All date/time handling uses pandas datetime for consistency
- Zone mappings use .map().fillna() pattern for safe fallback to original values
- Hour shifts use modulo arithmetic with proper date rollover handling
- Empty DataFrames are returned on errors (fail gracefully)
- Validation occurs at multiple stages (file load, column check, data quality)

DEPENDENCIES
------------
- pandas: DataFrame operations and vectorized processing
- numpy: Numeric operations
- constants: Configuration and ISO-specific mappings
- converters.utils: Shared utility functions for data cleaning
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from karbone_pnl_pos.utils import constants
from karbone_pnl_pos.converters.utils import (
    clean_price_string,
    clean_quantity,
    convert_hour_to_he_vintage,
    parse_date,
)

# Module logger
logger = logging.getLogger('pnl.' + __name__)


# =============================================================================
# Vectorized Helper Functions for Performance
# =============================================================================

def vectorized_parse_dates(series: pd.Series) -> pd.Series:
    """
    Vectorized date parsing using pd.to_datetime with fallback to element-wise.
    Significantly faster than apply(parse_date) for uniform date formats.
    """
    # Try vectorized parsing first (handles most cases)
    try:
        dates = pd.to_datetime(series, format='mixed', errors='coerce')
        return dates.dt.strftime('%Y-%m-%d').where(dates.notna(), None)
    except Exception:
        logger.debug("Vectorized date parsing failed, falling back to element-wise parsing")
        # Fallback to original method only if vectorized fails
        return series.apply(parse_date)


def vectorized_clean_quantity(series: pd.Series) -> pd.Series:
    """
    Vectorized quantity cleaning - removes commas and converts to float.
    Much faster than apply(clean_quantity).
    """
    # Handle already numeric values
    if pd.api.types.is_numeric_dtype(series):
        return series.fillna(0.0).astype(float)
    
    # For string values, use vectorized string operations
    cleaned = series.fillna('').astype(str).str.replace(',', '', regex=False).str.strip()
    return pd.to_numeric(cleaned, errors='coerce').fillna(0.0)


def vectorized_clean_price(series: pd.Series) -> pd.Series:
    """
    Vectorized price cleaning - removes $, commas, handles parentheses for negatives.
    Much faster than apply(clean_price_string).
    """
    # Handle already numeric values
    if pd.api.types.is_numeric_dtype(series):
        return series.fillna(0.0).astype(float)
    
    # Convert to string and clean
    cleaned = series.fillna('').astype(str)
    cleaned = cleaned.str.replace('$', '', regex=False)
    cleaned = cleaned.str.replace(',', '', regex=False)
    cleaned = cleaned.str.strip()
    
    # Handle parenthetical negatives: (100) -> -100
    # Use vectorized string operations instead of apply
    mask_parens = cleaned.str.match(r'^\(.*\)$', na=False)
    if mask_parens.any():
        # Extract content between parentheses and add negative sign
        cleaned_parens = '-' + cleaned.str[1:-1]
        cleaned = cleaned.where(~mask_parens, cleaned_parens)
    
    return pd.to_numeric(cleaned, errors='coerce').fillna(0.0)


def vectorized_hour_to_vintage(hours: pd.Series, one_based: bool = False) -> pd.Series:
    """
    Vectorized hour to HE## vintage conversion.
    Much faster than apply(convert_hour_to_he_vintage).

    Args:
        hours: Series of hour values.
        one_based: If True, hours are already 1-24 (no +1 needed).
                   If False (default), hours are 0-23 and +1 is applied.
    """
    # Convert to numeric, coerce errors to NaN
    hours_numeric = pd.to_numeric(hours, errors='coerce')

    # Add 1 only for 0-based hours
    hour_ending = hours_numeric if one_based else hours_numeric + 1

    # Format as HE## with zero padding
    return hour_ending.apply(lambda x: f'HE{int(x):02d}' if pd.notna(x) and 1 <= x <= 24 else None)


def vectorized_process_hour_shift(df: pd.DataFrame, date_col: str, hour_col: str) -> pd.DataFrame:
    """
    Vectorized processing of hour shifts with date rollover.
    Replaces slow row-by-row apply for hour shifting logic.
    
    Returns df with 'vintage' and updated date_col.
    """
    df = df.copy()
    
    # Convert hours to numeric
    hours = pd.to_numeric(df[hour_col], errors='coerce').fillna(0).astype(int)
    
    # Shift hour by 1
    adjusted_hours = hours + 1
    
    # Identify rows where hour rolls over (>= 24)
    rollover_mask = adjusted_hours >= 24
    
    # Adjust hours modulo 24
    adjusted_hours = adjusted_hours % 24
    
    # Convert adjusted hours to vintage
    df['vintage'] = (adjusted_hours + 1).apply(lambda x: f'HE{int(x):02d}')
    
    # Handle date rollover for rolled-over hours
    if rollover_mask.any():
        dates = pd.to_datetime(df[date_col], errors='coerce')
        dates.loc[rollover_mask] = dates.loc[rollover_mask] + pd.Timedelta(days=1)
        df[date_col] = dates.dt.strftime('%Y-%m-%d').where(dates.notna(), df[date_col])
    
    return df


# =============================================================================
# Original Functions (updated to use vectorized operations)
# =============================================================================

def load_csv_file(file_path: str, file_description: str) -> Optional[pd.DataFrame]:
    """Load a CSV file with standard error handling."""
    if not os.path.exists(file_path):
        logger.error(f"{file_description} file not found: {file_path}")
        return None

    try:
        df = pd.read_csv(file_path)
        logger.debug(f"Loaded {len(df)} rows from {os.path.basename(file_path)}")
        return df if not df.empty else None
    except FileNotFoundError:
        logger.error(f"{file_description} file not found: {file_path}")
    except pd.errors.ParserError as e:
        logger.error(f"Error parsing CSV file {file_path}: {e}")
    except pd.errors.EmptyDataError:
        logger.warning(f"{file_description} file is empty: {file_path}")
    return None


def process_hour_and_date(date_str: str, hour, shift_hour: bool = False) -> Tuple[Optional[str], str]:
    """
    Process hour value to vintage, optionally shifting by 1 hour.
    
    Args:
        date_str: Date string in YYYY-MM-DD format
        hour: Hour value (0-23)
        shift_hour: If True, add 1 hour and handle date rollover
        
    Returns:
        Tuple of (vintage, date_str) where date may be updated if hour rolls over
    """
    try:
        if isinstance(hour, str):
            hour = int(hour.strip())
        
        if not shift_hour:
            return convert_hour_to_he_vintage(hour), date_str
        
        # Shift hour by 1
        adjusted_hour = hour + 1
        
        if adjusted_hour >= 24 and date_str and not pd.isna(date_str):
            adjusted_hour = adjusted_hour % 24
            try:
                date_obj = datetime.strptime(date_str, '%Y-%m-%d')
                new_date_str = (date_obj + timedelta(days=1)).strftime('%Y-%m-%d')
                return convert_hour_to_he_vintage(adjusted_hour), new_date_str
            except (ValueError, TypeError):
                pass
        
        return convert_hour_to_he_vintage(adjusted_hour % 24), date_str
    except (ValueError, TypeError):
        return None, date_str


def process_trades_df_generic(df: pd.DataFrame, iso_config: dict) -> pd.DataFrame:
    """
    Generic trades processing function that works for any ISO.
    
    Uses the provided ISO configuration to determine column names, hour shift behavior,
    zone mapping, and desk assignment. This replaces the ISO-specific functions.
    
    Args:
        df: Raw trades DataFrame from bid records file
        iso_config: ISO configuration dictionary from constants.ISO_CONFIG
        
    Returns:
        Processed DataFrame with standardized columns
    """
    df = df.copy().fillna('')
    
    # Extract column names from config
    bid_cols = iso_config['bid_cols']
    
    # Vectorized date parsing
    df['date'] = vectorized_parse_dates(df[bid_cols['datetime']])
    
    # Handle hour conversion based on ISO configuration
    one_based = iso_config.get('hour_is_one_based', False)
    if iso_config['hour_shift']:
        # Apply hour shift with date rollover (e.g., NYISO)
        df = vectorized_process_hour_shift(df, 'date', bid_cols['hour'])
    else:
        # Direct hour to vintage conversion without shift (e.g., PJM, MISO, CAISO)
        df['vintage'] = vectorized_hour_to_vintage(df[bid_cols['hour']], one_based=one_based)
    
    # Handle zone to product mapping
    zones = df[bid_cols['zone']].astype(str).str.strip()
    if iso_config['zone_mapping']:
        # Map zones to aliases using provided mapping (e.g., PJM, MISO)
        aliases = zones.map(iso_config['zone_mapping']).fillna(zones)
        df['product'] = aliases + ' - DART'
    else:
        # Use zones directly (e.g., NYISO)
        df['product'] = zones + ' - DART'
    
    # Vectorized quantity cleaning
    df['quantity'] = vectorized_clean_quantity(df[bid_cols['clear_mw']])
    
    # Set standardized fields from configuration
    df['desk'] = iso_config['desk']
    df['portfolio'] = iso_config.get('portfolio', '')
    df['strategy'] = ''
    df['is_exchange_settled'] = constants.EXCHANGE_FALSE
    df['is_physically_settled'] = constants.SETTLEMENT_FINANCIAL
    df['is_fee'] = False
    df['fee_type'] = constants.FEE_TYPE_REGULAR
    df['currency'] = constants.DEFAULT_CURRENCY
    df['price'] = 0.0
    
    return df


def process_trades_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Legacy NYISO-specific trades processing function.
    Maintained for backward compatibility. Uses generic function internally.
    """
    return process_trades_df_generic(df, constants.ISO_CONFIG['nyiso'])


def process_marks_df_generic(df: pd.DataFrame, iso_config: dict) -> pd.DataFrame:
    """
    Generic marks/pricing processing function that works for any ISO.
    
    Uses the provided ISO configuration to determine column names for spot pricing data.
    Converts hours to vintages without shifting (marks are always hour-ending).
    
    Args:
        df: Raw marks DataFrame from spot pricing file
        iso_config: ISO configuration dictionary from constants.ISO_CONFIG
        
    Returns:
        Processed DataFrame with date, product, vintage, and price columns
    """
    df = df.copy().fillna('')
    
    # Extract column names from config
    spot_cols = iso_config['spot_cols']
    
    # Vectorized date parsing
    df['date'] = vectorized_parse_dates(df[spot_cols['datetime']])
    
    # Vectorized hour to vintage conversion (no shift for marks - always hour-ending)
    one_based = iso_config.get('hour_is_one_based', False)
    df['vintage'] = vectorized_hour_to_vintage(df[spot_cols['hour']], one_based=one_based)
    
    # Map product names (alias + DART suffix)
    df['product'] = df[spot_cols['alias']].astype(str).str.strip() + ' - DART'
    
    # Vectorized price cleaning
    df['price'] = vectorized_clean_price(df[spot_cols['price']])
    
    return df[['date', 'product', 'vintage', 'price']]


def process_marks_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Legacy NYISO-specific marks processing function.
    Maintained for backward compatibility. Uses generic function internally.
    """
    return process_marks_df_generic(df, constants.ISO_CONFIG['nyiso'])


def match_marks_to_trades(trades_df: pd.DataFrame, marks_df: pd.DataFrame) -> pd.DataFrame:
    """Match marks to trades based on date, product, and vintage."""
    trades_subset = trades_df[['date', 'product', 'vintage']]
    marks_subset = marks_df[['date', 'product', 'vintage', 'price']]
    return pd.merge(trades_subset, marks_subset, on=['date', 'product', 'vintage'], how='left')


def convert_iso_bid_records(
    iso_code: str,
    trades_input_file: str,
    marks_input_file: str,
    min_trade_date: Optional[str] = None
) -> pd.DataFrame:
    """
    Generic conversion function for ISO rolling bid records to standardized trade format.
    
    This is the main conversion engine that works for any ISO (NYISO, PJM, MISO, etc.)
    by using configuration-driven processing. The ISO-specific behavior is determined
    by the configuration stored in constants.ISO_CONFIG.
    
    The conversion process:
    1. Loads bid records (trades) and spot pricing (marks) CSV files
    2. Validates that required columns exist
    3. Processes trades: parses dates, handles hour shifts/conversions, maps zones to products
    4. Processes marks: parses dates, converts hours to vintages, extracts pricing
    5. Matches marks to trades by date, product, and vintage
    6. Creates entry trades (original positions) and closure trades (opposite positions with prices)
    7. Concatenates entries and closures for complete trade lifecycle
    
    Args:
        iso_code: ISO identifier ('nyiso', 'pjm', 'miso', etc.)
        trades_input_file: Path to the bid records CSV file
        marks_input_file: Path to the spot pricing CSV file
        min_trade_date: Optional minimum trade date filter in 'YYYY-MM-DD' format
        
    Returns:
        DataFrame with standardized trade records matching FINAL_OUTPUT_COLUMNS.
        Returns empty DataFrame if files are missing, invalid, or no valid trades.
        
    Example:
        >>> df = convert_iso_bid_records('pjm', 'pjm_bids.csv', 'pjm_marks.csv', '2024-01-01')
    """
    # Load ISO configuration
    if iso_code not in constants.ISO_CONFIG:
        logger.error(f"Unknown ISO code '{iso_code}'. Valid codes: {list(constants.ISO_CONFIG.keys())}")
        return pd.DataFrame()
    
    iso_config = constants.ISO_CONFIG[iso_code]
    iso_name = iso_config['name']
    
    # Load files
    trades_df = load_csv_file(trades_input_file, f"{iso_name} bid records")
    if trades_df is None:
        return pd.DataFrame()
    
    marks_df = load_csv_file(marks_input_file, f"{iso_name} marks")
    if marks_df is None:
        return pd.DataFrame()
    
    # Validate required columns
    missing_columns = [
        col for col in iso_config['required_bid_cols']
        if col not in trades_df.columns
    ]
    if missing_columns:
        logger.error(f"{iso_name} bid records file missing required columns: {missing_columns}")
        return pd.DataFrame()
    
    try:
        # Process trades and marks using generic functions
        df_entries = process_trades_df_generic(trades_df, iso_config)
        marks_processed = process_marks_df_generic(marks_df, iso_config)
    except Exception:
        logger.exception(f"Error processing {iso_name} data")
        return pd.DataFrame()
    
    # Filter invalid rows
    valid_mask = (
        df_entries['date'].notna() &
        (df_entries['quantity'] != 0) &
        df_entries['product'].notna() &
        (df_entries['product'] != '')
    )
    df_entries = df_entries[valid_mask].copy()
    
    if df_entries.empty:
        return pd.DataFrame()
    
    # Apply date filter
    if min_trade_date:
        df_entries = df_entries[df_entries['date'] >= min_trade_date]
    
    if df_entries.empty:
        return pd.DataFrame()
    
    # Reset index to ensure alignment after filtering
    df_entries = df_entries.reset_index(drop=True)
    
    # Match marks to trades
    df_matched_prices = match_marks_to_trades(df_entries, marks_processed)
    
    # Create closures (opposite positions with matched prices)
    df_closures = df_entries.copy()
    df_closures['quantity'] = -df_entries['quantity']
    df_closures['price'] = df_matched_prices['price']
    
    # Combine entries and closures
    result_df = pd.concat(
        [
            df_entries[constants.FINAL_OUTPUT_COLUMNS].copy(),
            df_closures[constants.FINAL_OUTPUT_COLUMNS].copy()
        ],
        ignore_index=True
    )
    logger.info(f"Created {len(result_df)} {iso_name} DART trade records")
    
    return result_df


def convert_nyiso_bid_records(
    trades_input_file: str,
    marks_input_file: str,
    min_trade_date: Optional[str] = None
) -> pd.DataFrame:
    """
    Convert NYISO rolling bid records to standardized trade format.
    
    Legacy wrapper function maintained for backward compatibility.
    Uses the generic convert_iso_bid_records() function internally.

    Args:
        trades_input_file: Path to the NYISO bid records CSV file.
        marks_input_file: Path to the NYISO marks CSV file.
        min_trade_date: Minimum trade date filter in 'YYYY-MM-DD' format.

    Returns:
        DataFrame with standardized trade records matching FINAL_OUTPUT_COLUMNS.
    """
    return convert_iso_bid_records('nyiso', trades_input_file, marks_input_file, min_trade_date)


# =============================================================================
# PJM Conversion Functions (no hour shift)
# =============================================================================

def process_pjm_trades_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Legacy PJM-specific trades processing function.
    Maintained for backward compatibility. Uses generic function internally.
    """
    return process_trades_df_generic(df, constants.ISO_CONFIG['pjm'])


def process_pjm_marks_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Legacy PJM-specific marks processing function.
    Maintained for backward compatibility. Uses generic function internally.
    """
    return process_marks_df_generic(df, constants.ISO_CONFIG['pjm'])


def convert_pjm_bid_records(
    trades_input_file: str,
    marks_input_file: str,
    min_trade_date: Optional[str] = None
) -> pd.DataFrame:
    """
    Convert PJM rolling bid records to standardized trade format.
    
    Legacy wrapper function maintained for backward compatibility.
    Uses the generic convert_iso_bid_records() function internally.

    Args:
        trades_input_file: Path to the PJM bid records CSV file.
        marks_input_file: Path to the PJM marks CSV file.
        min_trade_date: Minimum trade date filter in 'YYYY-MM-DD' format.

    Returns:
        DataFrame with standardized trade records matching FINAL_OUTPUT_COLUMNS.
    """
    return convert_iso_bid_records('pjm', trades_input_file, marks_input_file, min_trade_date)


def convert_miso_bid_records(
    trades_input_file: str,
    marks_input_file: str,
    min_trade_date: Optional[str] = None
) -> pd.DataFrame:
    """
    Convert MISO rolling bid records to standardized trade format.

    MISO does not require a 1-hour shift for hour ending convention and uses
    zone-to-alias mapping similar to PJM.

    Args:
        trades_input_file: Path to the MISO bid records CSV file.
        marks_input_file: Path to the MISO marks CSV file.
        min_trade_date: Minimum trade date filter in 'YYYY-MM-DD' format.

    Returns:
        DataFrame with standardized trade records matching FINAL_OUTPUT_COLUMNS.
    """
    return convert_iso_bid_records('miso', trades_input_file, marks_input_file, min_trade_date)


def convert_caiso_bid_records(
    trades_input_file: str,
    marks_input_file: str,
    min_trade_date: Optional[str] = None
) -> pd.DataFrame:
    """
    Convert CAISO rolling bid records to standardized trade format.

    CAISO does not require a 1-hour shift for hour ending convention and uses
    zones directly as product names (no zone-to-alias mapping).

    Args:
        trades_input_file: Path to the CAISO bid records CSV file.
        marks_input_file: Path to the CAISO marks CSV file.
        min_trade_date: Minimum trade date filter in 'YYYY-MM-DD' format.

    Returns:
        DataFrame with standardized trade records matching FINAL_OUTPUT_COLUMNS.
    """
    return convert_iso_bid_records('caiso', trades_input_file, marks_input_file, min_trade_date)

