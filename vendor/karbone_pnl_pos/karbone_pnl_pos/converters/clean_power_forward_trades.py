#!/usr/bin/env python3
"""
Power Forward Tradesheet to Standardized Format Converter.

This module converts raw Power forward tradesheet data into a standardized format suitable
for PnL calculations. It handles data cleaning, normalization, fee extraction,
and formatting for power forward trading positions using vectorized operations.

The module supports:
- Single power forward tradesheet file processing
- Broker fee extraction
- Date filtering
- Exchange settlement detection based on platform

Usage:
    # As a module
    from clean_power_forward_trades import convert_power_tradesheet
    trades_df, fees_df = convert_power_tradesheet(
        input_file='power_trades.csv',
        output_file='output.csv',
        min_trade_date='2024-01-01'
    )

    # As a CLI tool
    python clean_power_forward_trades.py --input-file power.csv --output-file output.csv
"""

import argparse
import logging
import os
import re
from typing import Optional, Tuple

import pandas as pd

from karbone_pnl_pos.utils import constants
from karbone_pnl_pos.utils.config_loader import (
    get_config,
    get_min_trade_date,
    get_results_dir,
)
from karbone_pnl_pos.converters.utils import (
    clean_price_string,
    clean_quantity,
    convert_fees_to_pnl_format,
    convert_hour_to_he_vintage,
    determine_settlement,
    parse_date,
)

# Module logger
logger = logging.getLogger('pnl.' + __name__)


def parse_vintage_to_yyyy_mm(vintage_str: Optional[str]) -> Optional[str]:
    """
    Parse vintage from formats like "Jan 2025" into YYYY-MM format.

    Supports:
    - Month abbreviation + year (e.g., "Jan 2025" -> "2025-01")
    - Full month name + year (e.g., "January 2025" -> "2025-01")
    - Already formatted YYYY-MM (passed through)

    Args:
        vintage_str: Raw vintage string from the tradesheet.

    Returns:
        Vintage in 'YYYY-MM' format, or None if parsing fails.

    Examples:
        >>> parse_vintage_to_yyyy_mm("Jan 2025")
        '2025-01'
        >>> parse_vintage_to_yyyy_mm("2025-01")
        '2025-01'
    """
    if pd.isna(vintage_str) or str(vintage_str).strip() == '':
        return None

    vintage_str = str(vintage_str).strip()

    # Try to parse "Month YYYY" format (e.g., "Jan 2025")
    match = re.match(r'^(\w+)\s+(\d{4})$', vintage_str, re.IGNORECASE)
    if match:
        month_str = match.group(1).lower()
        year_str = match.group(2)

        if month_str in constants.MONTH_NAME_TO_NUMBER:
            return f"{year_str}-{constants.MONTH_NAME_TO_NUMBER[month_str]}"

    # If already in YYYY-MM format, return as-is
    if re.match(r'^\d{4}-\d{2}$', vintage_str):
        return vintage_str

    return None


def determine_exchange_from_platform(platform: Optional[str]) -> str:
    """
    Determine if a trade is an exchange trade based on the platform.

    ICE platform trades are considered exchange trades.

    Args:
        platform: Trading platform name (e.g., "ICE", "OTC").

    Returns:
        'TRUE' if ICE platform, 'FALSE' otherwise.
    """
    if pd.isna(platform):
        return constants.EXCHANGE_FALSE

    platform_str = str(platform).strip()
    is_ice = (platform_str.upper() == constants.PLATFORM_ICE)
    return constants.EXCHANGE_TRUE if is_ice else constants.EXCHANGE_FALSE


def is_broker_fee(ice_equiv: Optional[str]) -> bool:
    """
    Check if a row represents a broker fee based on the ICE Equiv column.

    Args:
        ice_equiv: Value from the ICE Equiv column.

    Returns:
        True if the row represents a broker fee, False otherwise.
    """
    if pd.isna(ice_equiv):
        return False
    ice_equiv_str = str(ice_equiv).strip()
    return ice_equiv_str == constants.POWER_BROKER_INDICATOR


def normalize_power_product(ice_equiv: Optional[str]) -> str:
    """
    Normalize product names from the ICE Equiv column.

    Args:
        ice_equiv: Value from the ICE Equiv column.

    Returns:
        Normalized product name, or empty string if invalid.
    """
    if pd.isna(ice_equiv) or ice_equiv == '':
        return ''

    return str(ice_equiv).strip()


def determine_desk_from_product(product: Optional[str]) -> str:
    """
    Determine the desk based on the product name.

    All products go to the power_forward desk. CAISO short-term trades
    are processed separately from bid records files.

    Args:
        product: Product name from the ICE Equiv column.

    Returns:
        Desk name string.
    """
    return constants.POWER_DESK


def extract_broker_fees_from_power_tradesheet(
    df: pd.DataFrame,
    min_trade_date: Optional[str] = None
) -> pd.DataFrame:
    """
    Extract broker fee records from the power tradesheet using vectorized operations.

    Broker fees are identified by having "Broker" in the ICE Equiv column.

    Args:
        df: Raw power tradesheet DataFrame.
        min_trade_date: Minimum trade date filter in 'YYYY-MM-DD' format.

    Returns:
        DataFrame containing broker fee records with columns:
        date, fee_type, fee_quantity, charge, fee_amount, desk.
    """
    # Filter to broker fee records
    broker_mask = df[constants.POWER_COL_ICE_EQUIV].fillna('') == constants.POWER_BROKER_INDICATOR
    broker_df = df[broker_mask].copy()

    if broker_df.empty:
        return pd.DataFrame()

    # Use Product column if available, otherwise default to power_forward
    if constants.POWER_COL_PRODUCT in broker_df.columns:
        broker_products = broker_df[constants.POWER_COL_PRODUCT].apply(normalize_power_product)
        broker_df['desk'] = broker_products.apply(determine_desk_from_product)
    else:
        broker_df['desk'] = constants.POWER_DESK

    broker_df['portfolio'] = (
        broker_df[constants.POWER_COL_PORTFOLIO].fillna('').astype(str)
        if constants.POWER_COL_PORTFOLIO in broker_df.columns
        else ''
    )
    broker_df['strategy'] = (
        broker_df[constants.POWER_COL_STRATEGY].fillna('').astype(str)
        if constants.POWER_COL_STRATEGY in broker_df.columns
        else ''
    )

    # Process buy side (seller information) - vectorized operations
    buy_date = broker_df[constants.POWER_COL_DATE].apply(parse_date)
    buy_volume = broker_df[constants.POWER_COL_VOLUME].apply(clean_quantity)
    buy_price = broker_df[constants.POWER_COL_PRICE].apply(clean_price_string)

    # Create buy fees DataFrame
    buy_fees_df = pd.DataFrame({
        'date': buy_date,
        'fee_type': constants.FEE_TYPE_BROKER,
        'fee_quantity': buy_volume,
        'charge': buy_price,
        'fee_amount': buy_volume * buy_price,
        'desk': broker_df['desk'],
        'portfolio': broker_df['portfolio'],
        'strategy': broker_df['strategy'],
    })

    # Process sell side (buyer information) - vectorized operations
    sell_date = broker_df[constants.POWER_COL_DATE1].apply(parse_date)
    sell_volume = broker_df[constants.POWER_COL_VOLUME1].apply(clean_quantity)
    sell_price = broker_df[constants.POWER_COL_PRICE1].apply(clean_price_string)

    # Create sell fees DataFrame
    sell_fees_df = pd.DataFrame({
        'date': sell_date,
        'fee_type': constants.FEE_TYPE_BROKER,
        'fee_quantity': sell_volume,
        'charge': sell_price,
        'fee_amount': sell_volume * sell_price,
        'desk': broker_df['desk'],
        'portfolio': broker_df['portfolio'],
        'strategy': broker_df['strategy'],
    })

    # Combine buy and sell fees
    all_fees_df = pd.concat([buy_fees_df, sell_fees_df], ignore_index=True)

    # Filter out invalid rows (missing dates, zero volumes/prices)
    valid_mask = (
        all_fees_df['date'].notna() &
        (all_fees_df['fee_quantity'] > 0) &
        (all_fees_df['charge'] > 0)
    )
    all_fees_df = all_fees_df[valid_mask]

    # Reassign Ancillary strategy fees to their own desk
    ancillary_mask = all_fees_df['strategy'].str.strip() == constants.ANCILLARY_STRATEGY
    all_fees_df.loc[ancillary_mask, 'desk'] = constants.POWER_ANCILLARY_DESK

    # Apply date filter if specified
    if min_trade_date:
        date_mask = all_fees_df['date'] >= min_trade_date
        all_fees_df = all_fees_df[date_mask]

    return all_fees_df.reset_index(drop=True)


def vectorized_convert_power_tradesheet(
    df: pd.DataFrame,
    min_trade_date: Optional[str] = None
) -> pd.DataFrame:
    """
    Convert a power tradesheet DataFrame to standardized trade format using vectorized operations.

    This function replaces row-by-row processing with pandas vectorized operations for
    significant performance gains. It creates separate DataFrames for buy and sell sides,
    applies all filtering logic vectorially, and concatenates the results.

    Args:
        df: Raw power tradesheet DataFrame.
        min_trade_date: Minimum trade date filter in 'YYYY-MM-DD' format.

    Returns:
        DataFrame with standardized trade records matching FINAL_OUTPUT_COLUMNS.
    """
    if df.empty:
        return pd.DataFrame()

    # Create a copy to avoid modifying the original
    df_copy = df.copy()

    # Fill NaN values to avoid issues with string operations
    df_copy = df_copy.fillna('')

    # Common fields processing
    # First normalize products to determine desk assignment
    products = df_copy[constants.POWER_COL_ICE_EQUIV].apply(normalize_power_product)
    common_data = {
        'product': products,
        'vintage': df_copy[constants.POWER_COL_VINTAGE].apply(parse_vintage_to_yyyy_mm),
        'desk': products.apply(determine_desk_from_product),
        'portfolio': (
            df_copy[constants.POWER_COL_PORTFOLIO].astype(str)
            if constants.POWER_COL_PORTFOLIO in df_copy.columns
            else pd.Series('', index=df_copy.index)
        ),
        'strategy': (
            df_copy[constants.POWER_COL_STRATEGY].astype(str)
            if constants.POWER_COL_STRATEGY in df_copy.columns
            else pd.Series('', index=df_copy.index)
        ),
        'is_physically_settled': df_copy[constants.POWER_COL_PLATFORM].apply(determine_settlement),
        'is_exchange_settled': df_copy[constants.POWER_COL_PLATFORM].apply(determine_exchange_from_platform)
    }

    # Create Buy-Side DataFrame
    buys_df = pd.DataFrame(common_data)

    # Add buy-specific fields
    buys_df['date'] = df_copy[constants.POWER_COL_DATE].apply(parse_date)
    buys_df['price'] = df_copy[constants.POWER_COL_PRICE].apply(clean_price_string)
    buys_df['quantity'] = df_copy[constants.POWER_COL_VOLUME].apply(clean_quantity)

    # Filter out broker fee rows and SHORT POSITION rows
    broker_mask = df_copy[constants.POWER_COL_ICE_EQUIV].fillna('') == constants.POWER_BROKER_INDICATOR
    seller_short_mask = (
        df_copy[constants.POWER_COL_SELLER].astype(str).str.strip().str.upper()
        != constants.SHORT_POSITION_STR
    )
    buys_df = buys_df[~broker_mask & seller_short_mask].copy()

    # Filter out invalid buys (no date, zero/negative quantity, no product)
    valid_buy_mask = (
        buys_df['date'].notna() &
        (buys_df['quantity'] > 0) &
        buys_df['product'].notna() &
        (buys_df['product'] != '')
    )
    buys_df = buys_df[valid_buy_mask].copy()

    # Create Sell-Side DataFrame
    sells_df = pd.DataFrame(common_data)

    # Add sell-specific fields
    sells_df['date'] = df_copy[constants.POWER_COL_DATE1].apply(parse_date)
    sells_df['price'] = df_copy[constants.POWER_COL_PRICE1].apply(clean_price_string)
    sells_df['quantity'] = -df_copy[constants.POWER_COL_VOLUME1].apply(clean_quantity)  # Negative for sells

    # Filter out broker fee rows and LONG POSITION rows
    buyer_long_mask = (
        df_copy[constants.POWER_COL_BUYER].astype(str).str.strip().str.upper()
        != constants.LONG_POSITION_STR
    )
    sells_df = sells_df[~broker_mask & buyer_long_mask].copy()

    # Filter out invalid sells (no date, zero/positive quantity, no product)
    valid_sell_mask = (
        sells_df['date'].notna() &
        (sells_df['quantity'] < 0) &
        sells_df['product'].notna() &
        (sells_df['product'] != '')
    )
    sells_df = sells_df[valid_sell_mask].copy()

    # Concatenate Buy and Sell DataFrames
    all_trades = pd.concat([buys_df, sells_df], ignore_index=True)

    if all_trades.empty:
        return pd.DataFrame()

    # Reassign Ancillary strategy trades to their own desk
    ancillary_mask = all_trades['strategy'].str.strip() == constants.ANCILLARY_STRATEGY
    all_trades.loc[ancillary_mask, 'desk'] = constants.POWER_ANCILLARY_DESK

    # Add constant columns
    all_trades['is_fee'] = False
    all_trades['fee_type'] = constants.FEE_TYPE_REGULAR
    all_trades['currency'] = constants.DEFAULT_CURRENCY

    # Apply date filter if specified
    if min_trade_date:
        date_mask = all_trades['date'] >= min_trade_date
        all_trades = all_trades[date_mask]

    # Filter out any remaining rows with zero quantity or no date
    final_mask = (
        all_trades['date'].notna() &
        (all_trades['quantity'] != 0)
    )
    all_trades = all_trades[final_mask]

    # Return only the required columns in the correct order
    return all_trades[constants.FINAL_OUTPUT_COLUMNS].copy()


def convert_power_tradesheet(
    input_file: str,
    output_file: str,
    min_trade_date: Optional[str] = None,
    include_fees: Optional[bool] = None
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Orchestrate the end-to-end conversion of a power tradesheet file.

    This function manages the entire workflow:
    1. Reads the input file.
    2. Converts all regular trades.
    3. Extracts broker fees.
    4. Converts all fees to the standard PnL format.
    5. Combines regular trades and fee trades.
    6. Sorts the final output and saves it to a CSV file.

    Args:
        input_file: Path to the input power tradesheet CSV file.
        output_file: Path for the output CSV file.
        min_trade_date: Minimum trade date filter in 'YYYY-MM-DD' format.
        include_fees: Whether to include fee processing.

    Returns:
        Tuple of (trades_df, fees_df) where:
        - trades_df: Combined trade and fee records.
        - fees_df: Raw fee records before conversion.
    """
    # Use configuration defaults if not provided
    config = get_config()
    if min_trade_date is None:
        min_trade_date = get_min_trade_date()
    if include_fees is None:
        include_fees = config.get('conversion.include_fees', True)

    logger.info(f"Processing power tradesheet: {os.path.basename(input_file)}")

    if min_trade_date:
        logger.info(f"Will filter out trades before: {min_trade_date}")

    # Read the CSV file
    if not os.path.exists(input_file):
        logger.error(f"Power tradesheet file not found: {input_file}")
        return pd.DataFrame(), pd.DataFrame()

    try:
        df = pd.read_csv(input_file)
        logger.debug(f"Loaded {len(df)} rows from {os.path.basename(input_file)}")
    except FileNotFoundError:
        logger.error(f"Power tradesheet file not found: {input_file}")
        return pd.DataFrame(), pd.DataFrame()
    except pd.errors.ParserError as e:
        logger.error(f"Error parsing CSV file {input_file}: {e}")
        return pd.DataFrame(), pd.DataFrame()
    except pd.errors.EmptyDataError:
        logger.warning(f"Power tradesheet file is empty: {input_file}")
        return pd.DataFrame(), pd.DataFrame()

    # Convert trades using fully vectorized approach
    try:
        trades_df = vectorized_convert_power_tradesheet(df, min_trade_date)
        regular_trade_count = len(trades_df)
        logger.info(f"  Converted {len(df)} rows into {regular_trade_count} regular trades")
    except KeyError as e:
        logger.error(f"Missing required column in {input_file}: {e}")
        return pd.DataFrame(), pd.DataFrame()

    # Extract broker fees
    all_fees = pd.DataFrame()
    if include_fees:
        logger.info(f"Processing fees for {os.path.basename(input_file)}...")
        broker_fees = extract_broker_fees_from_power_tradesheet(df, min_trade_date)
        logger.debug(f"Extracted {len(broker_fees)} broker fee records")

        if not broker_fees.empty:
            all_fees = broker_fees

            # Convert fees to PnL format and append to trades
            fee_trades = convert_fees_to_pnl_format(all_fees)
            if not fee_trades.empty:
                logger.debug(f"Converting {len(fee_trades)} fee records to trade format")

                # Ensure correct column order for fee trades
                fee_trades = fee_trades[constants.FINAL_OUTPUT_COLUMNS]

                # Combine with regular trades
                if not trades_df.empty:
                    # Add missing columns to regular trades if they don't exist
                    if 'is_fee' not in trades_df.columns:
                        trades_df['is_fee'] = False
                    if 'fee_type' not in trades_df.columns:
                        trades_df['fee_type'] = constants.FEE_TYPE_REGULAR
                    if 'currency' not in trades_df.columns:
                        trades_df['currency'] = constants.DEFAULT_CURRENCY
                    trades_df = trades_df[constants.FINAL_OUTPUT_COLUMNS]
                    final_trades_df = pd.concat([trades_df, fee_trades], ignore_index=True)
                else:
                    final_trades_df = fee_trades

                trades_df = final_trades_df

    # Create output DataFrame
    if not trades_df.empty:
        # Ensure correct column order
        if 'is_fee' not in trades_df.columns:
            trades_df['is_fee'] = False
        if 'fee_type' not in trades_df.columns:
            trades_df['fee_type'] = constants.FEE_TYPE_REGULAR
        if 'currency' not in trades_df.columns:
            trades_df['currency'] = constants.DEFAULT_CURRENCY
        trades_df = trades_df[constants.FINAL_OUTPUT_COLUMNS]

        # Convert date column to datetime for proper sorting
        trades_df['date'] = pd.to_datetime(trades_df['date'])

        # Create sorting keys
        trades_df['_exchange_sort'] = trades_df['is_exchange_settled'].map({
            constants.EXCHANGE_FALSE: 0,
            constants.EXCHANGE_TRUE: 1
        })
        trades_df['_fee_sort'] = trades_df['fee_type'].apply(
            lambda x: 0 if x is None or x == constants.FEE_TYPE_REGULAR else 1
        )

        # Sort by all specified columns
        trades_df = trades_df.sort_values([
            'date',
            'desk',
            'product',
            'vintage',
            'currency',
            '_exchange_sort',
            '_fee_sort',
            'fee_type'
        ])

        # Drop the temporary sorting columns
        trades_df = trades_df.drop(columns=['_exchange_sort', '_fee_sort'])

        # Convert date back to string format for output
        trades_df['date'] = trades_df['date'].dt.strftime('%Y-%m-%d')

        # Save to CSV
        trades_df.to_csv(output_file, index=False)
        logger.info(f"Conversion completed:")
        logger.info(f"- Converted {len(df)} tradesheet rows into {regular_trade_count} regular trades")
        logger.info(f"- Added {len(trades_df) - regular_trade_count} fee-related trade records")
        logger.info(f"- Total output: {len(trades_df)} trade records")
        logger.info(f"Output saved to: {output_file}")

        # Show sample of output (debug level)
        logger.debug(f"Sample of converted records:\n{trades_df.head(10).to_string(index=False)}")

        # Show fee summary if fees were processed
        if include_fees and not all_fees.empty:
            fee_summary = all_fees.groupby('fee_type').agg({
                'fee_amount': ['count', 'sum']
            }).round(2)
            logger.debug(f"Fee summary:\n{fee_summary}")

    else:
        logger.warning("No valid trades found to convert")
        trades_df = pd.DataFrame()

    return trades_df, all_fees


def main() -> None:
    """
    Main entry point for command-line execution.

    Handles argument parsing and invokes the main conversion workflow.
    """
    parser = argparse.ArgumentParser(
        description="Convert Power forward tradesheet to a standardized trades format.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    # Get configuration defaults
    config = get_config()
    default_input = config.get_path('power_tradesheet')
    if not default_input:
        default_input = "power_tradesheet.csv"
    default_output_dir = get_results_dir()
    default_output_path = os.path.join(default_output_dir, 'converted_power_trades.csv')
    default_min_date = get_min_trade_date()

    parser.add_argument(
        '--input-file',
        dest='input_file',
        type=str,
        default=default_input,
        help=f"Input Power tradesheet CSV file.\n(default: {default_input})"
    )
    parser.add_argument(
        '--output-file',
        dest='output_file',
        type=str,
        default=default_output_path,
        help=f"Output standardized trades CSV file.\n(default: {default_output_path})"
    )
    parser.add_argument(
        '--min-trade-date',
        dest='min_trade_date',
        type=str,
        default=default_min_date,
        help=f"Minimum trade date to include (YYYY-MM-DD).\n(default: {default_min_date})"
    )
    parser.add_argument(
        '--no-fees',
        action='store_true',
        help="Disable all fee processing and do not include fees in the output."
    )

    args = parser.parse_args()

    # Ensure output directory exists
    output_dir = os.path.dirname(args.output_file)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    logger.info("Starting Power Forward Tradesheet Conversion")
    logger.info("-" * 40)
    logger.info(f"  Input file:   {args.input_file}")
    logger.info(f"  Output file:  {args.output_file}")
    logger.info(f"  Min date:     {args.min_trade_date}")
    logger.info(f"  Include fees: {not args.no_fees}")
    logger.info("-" * 40)

    convert_power_tradesheet(
        input_file=args.input_file,
        output_file=args.output_file,
        min_trade_date=args.min_trade_date,
        include_fees=not args.no_fees
    )


if __name__ == "__main__":
    main()

