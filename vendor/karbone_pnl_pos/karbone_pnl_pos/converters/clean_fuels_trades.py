#!/usr/bin/env python3
"""
RINs Tradesheet to Standardized Format Converter.

This module converts raw RINs tradesheet data into a standardized format suitable
for PnL calculations. It handles data cleaning, normalization, fee extraction,
and formatting using vectorized pandas operations for performance.

The module supports:
- Multiple tradesheet file processing
- Broker fee extraction
- Product-specific fee calculation from fee schedules
- Date filtering
- Exchange settlement detection

Usage:
    # As a module
    from clean_fuels_trades import convert_multiple_rins_tradesheets
    trades_df, fees_df = convert_multiple_rins_tradesheets(
        input_files=['tradesheet.csv'],
        output_file='output.csv',
        min_trade_date='2024-01-01'
    )

    # As a CLI tool
    python clean_fuels_trades.py --input-file trades.csv --output-file output.csv
"""

import argparse
import logging
import os
from typing import List, Optional, Tuple

import pandas as pd

from karbone_pnl_pos.utils import constants
from karbone_pnl_pos.utils.config_loader import (
    get_broker_skip_phrases,
    get_config,
    get_fee_schedule_path,
    get_min_trade_date,
    get_results_dir,
    get_rins_tradesheet_path,
)
from karbone_pnl_pos.converters.utils import (
    clean_price_string,
    clean_quantity,
    convert_fees_to_pnl_format,
    determine_desk,
    determine_exchange,
    determine_settlement,
    normalize_product,
    parse_date,
)

# Module logger
logger = logging.getLogger('pnl.' + __name__)


def should_skip_for_broker_notes(notes: Optional[str]) -> bool:
    """
    Check if a tradesheet row should be skipped for regular trade processing.

    Rows with broker-related notes are skipped from regular trade processing
    because they represent broker fees, which are handled separately.

    Args:
        notes: Notes field value from the tradesheet row.

    Returns:
        True if the row should be skipped, False otherwise.
    """
    if pd.isna(notes):
        return False
    notes_lower = str(notes).lower()
    broker_phrases = get_broker_skip_phrases()
    return any(phrase in notes_lower for phrase in broker_phrases)


def extract_broker_fees_from_tradesheet(
    df: pd.DataFrame,
    min_trade_date: Optional[str] = None
) -> pd.DataFrame:
    """
    Extract broker fee records from the tradesheet using vectorized operations.

    This function filters for rows where the 'Notes' column indicates a broker fee,
    then processes both the buy-side and sell-side columns to create separate
    fee records. Supports both positive fees (expenses) and negative fees (credits).

    Credits can be specified by:
    - Negative charge values directly
    - 0 purchase price with positive sale price (creates a credit using sale price)

    Args:
        df: Raw tradesheet DataFrame.
        min_trade_date: Minimum trade date filter in 'YYYY-MM-DD' format.

    Returns:
        DataFrame containing broker fee records with columns:
        date, fee_type, fee_quantity, charge, fee_amount, desk.
    """
    # Filter to broker fee records using vectorized string operations
    broker_mask = df[constants.COL_NOTES].str.contains('broker', case=False, na=False)
    broker_df = df[broker_mask].copy()

    if broker_df.empty:
        return pd.DataFrame()

    # Determine desk for all broker records at once
    broker_df['desk'] = broker_df[constants.COL_POSITION_TYPE].apply(determine_desk)

    # Process buy side (seller information) - vectorized operations
    buy_date = broker_df[constants.COL_DATE].apply(parse_date)
    buy_volume = broker_df[constants.COL_VOLUME].apply(clean_quantity)
    buy_price = broker_df[constants.COL_PRICE].apply(clean_price_string)

    # Process sell side (buyer information) - vectorized operations
    sell_date = broker_df[constants.COL_DATE1].apply(parse_date)
    sell_volume = broker_df[constants.COL_VOLUME1].apply(clean_quantity)
    sell_price = broker_df[constants.COL_PRICE1].apply(clean_price_string)

    # Handle special case: 0 purchase price + positive sale price = credit
    # Create credit records from sell side when buy price is 0
    credit_mask = (buy_price == 0) & (sell_price > 0) & (sell_volume > 0)
    credit_fees_df = pd.DataFrame()
    if credit_mask.any():
        credit_records = broker_df[credit_mask]
        credit_fees_df = pd.DataFrame({
            'date': credit_records[constants.COL_DATE1].apply(parse_date),
            'fee_type': constants.FEE_TYPE_BROKER,
            'fee_quantity': credit_records[constants.COL_VOLUME1].apply(clean_quantity),
            'charge': -credit_records[constants.COL_PRICE1].apply(clean_price_string),  # Negative for credit
            'fee_amount': -credit_records[constants.COL_VOLUME1].apply(clean_quantity) *
                          credit_records[constants.COL_PRICE1].apply(clean_price_string),  # Negative for credit
            'desk': credit_records['desk'],
            'portfolio': '',
            'strategy': '',
        })

    # Create buy fees DataFrame
    # Note: Credit cases (buy_price = 0) will be filtered out by validation (charge != 0)
    buy_fees_df = pd.DataFrame({
        'date': buy_date,
        'fee_type': constants.FEE_TYPE_BROKER,
        'fee_quantity': buy_volume,
        'charge': buy_price,
        'fee_amount': buy_volume * buy_price,
        'desk': broker_df['desk'],
        'portfolio': '',
        'strategy': '',
    })

    # Create sell fees DataFrame
    # Exclude credit cases to avoid double-counting (they're handled separately above)
    if credit_mask.any():
        non_credit_mask = ~credit_mask
        sell_fees_df = pd.DataFrame({
            'date': sell_date[non_credit_mask],
            'fee_type': constants.FEE_TYPE_BROKER,
            'fee_quantity': sell_volume[non_credit_mask],
            'charge': sell_price[non_credit_mask],
            'fee_amount': (sell_volume * sell_price)[non_credit_mask],
            'desk': broker_df['desk'][non_credit_mask],
            'portfolio': '',
            'strategy': '',
        })
    else:
        sell_fees_df = pd.DataFrame({
            'date': sell_date,
            'fee_type': constants.FEE_TYPE_BROKER,
            'fee_quantity': sell_volume,
            'charge': sell_price,
            'fee_amount': sell_volume * sell_price,
            'desk': broker_df['desk'],
            'portfolio': '',
            'strategy': '',
        })

    # Combine buy, sell, and credit fees
    fee_dfs = [buy_fees_df, sell_fees_df]
    if not credit_fees_df.empty:
        fee_dfs.append(credit_fees_df)
    all_fees_df = pd.concat(fee_dfs, ignore_index=True)

    # Filter out invalid rows (missing dates, zero volumes, zero charges)
    # Allow negative values for credits
    valid_mask = (
        all_fees_df['date'].notna() &
        (all_fees_df['fee_quantity'] != 0) &  # Allow negative quantities
        (all_fees_df['charge'] != 0)  # Allow negative charges (credits)
    )
    all_fees_df = all_fees_df[valid_mask]

    # Apply date filter if specified
    if min_trade_date:
        date_mask = all_fees_df['date'] >= min_trade_date
        all_fees_df = all_fees_df[date_mask]

    return all_fees_df.reset_index(drop=True)


def load_fee_schedule(fee_schedule_file: str) -> pd.DataFrame:
    """
    Load the product fee schedule from a CSV file.

    Args:
        fee_schedule_file: Path to the fee schedule CSV file.

    Returns:
        DataFrame containing the fee schedule, or empty DataFrame on error.
    """
    try:
        return pd.read_csv(fee_schedule_file)
    except FileNotFoundError:
        logger.warning(f"Fee schedule file not found: {fee_schedule_file}")
        return pd.DataFrame()
    except pd.errors.ParserError as e:
        logger.warning(f"Could not parse fee schedule CSV file {fee_schedule_file}: {e}")
        return pd.DataFrame()
    except pd.errors.EmptyDataError:
        logger.warning(f"Fee schedule file is empty: {fee_schedule_file}")
        return pd.DataFrame()


def calculate_product_fees(
    trades_df: pd.DataFrame,
    fee_schedule_df: pd.DataFrame,
    min_trade_date: Optional[str] = None
) -> pd.DataFrame:
    """
    Calculate product-specific fees for exchange-settled trades.

    This function uses an optimized `pd.merge` operation to join trade volumes
    with the fee schedule, replacing inefficient nested loops. It calculates fees
    based on the total traded volume for each product.

    Args:
        trades_df: DataFrame containing standardized trade records.
        fee_schedule_df: DataFrame containing fee schedule with 'product' and 'charge'.
        min_trade_date: Minimum trade date filter in 'YYYY-MM-DD' format.

    Returns:
        DataFrame containing calculated product fees.
    """
    if fee_schedule_df.empty:
        return pd.DataFrame()

    # Filter to only exchange trades (where is_exchange_settled == 'TRUE')
    exchange_trades = trades_df[trades_df['is_exchange_settled'] == constants.EXCHANGE_TRUE].copy()

    if exchange_trades.empty:
        return pd.DataFrame()

    # Apply date filter if specified
    if min_trade_date:
        exchange_trades = exchange_trades[exchange_trades['date'] >= min_trade_date]

    # Group by date, product, vintage, desk to calculate total volume
    trade_volumes = exchange_trades.groupby(['date', 'product', 'vintage', 'desk']).agg({
        'quantity': 'sum'
    }).reset_index()

    # Take absolute value of quantity for fee calculation
    trade_volumes['fee_quantity'] = trade_volumes['quantity'].abs()

    # Merge with fee schedule using optimized pd.merge operation
    merged_df = pd.merge(
        trade_volumes,
        fee_schedule_df,
        on='product',
        how='inner'
    )

    # Calculate fee amounts in one vectorized operation
    merged_df['fee_amount'] = merged_df['charge'] * merged_df['fee_quantity']

    # Add portfolio/strategy defaults (fuels has no portfolio/strategy)
    merged_df['portfolio'] = ''
    merged_df['strategy'] = ''

    # Select and return the required columns in the correct order
    result_df = merged_df[[
        'date', 'product', 'vintage', 'fee_type',
        'fee_quantity', 'charge', 'fee_amount', 'desk', 'portfolio', 'strategy'
    ]]

    return result_df


def vectorized_convert_tradesheet(
    df: pd.DataFrame,
    min_trade_date: Optional[str] = None
) -> pd.DataFrame:
    """
    Convert a tradesheet DataFrame to standardized trade format using vectorized operations.

    This function replaces row-by-row processing with pandas vectorized operations for
    significant performance gains. It creates separate DataFrames for buy and sell sides,
    applies all filtering logic vectorially, and concatenates the results.

    Args:
        df: Raw tradesheet DataFrame.
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
    common_data = {
        'product': df_copy[constants.COL_PRODUCT].apply(normalize_product),
        'vintage': df_copy[constants.COL_VINTAGE].astype(str).str.strip(),
        'desk': df_copy[constants.COL_POSITION_TYPE].apply(determine_desk),
        'portfolio': pd.Series('', index=df_copy.index),
        'strategy': pd.Series('', index=df_copy.index),
        'is_physically_settled': df_copy[constants.COL_PLATFORM].apply(determine_settlement),
        'is_exchange_settled': df_copy.apply(
            lambda row: determine_exchange(row[constants.COL_SELLER], row[constants.COL_BUYER]),
            axis=1
        )
    }

    # Create Buy-Side DataFrame
    buys_df = pd.DataFrame(common_data)

    # Add buy-specific fields
    buys_df['date'] = df_copy[constants.COL_DATE].apply(parse_date)
    buys_df['price'] = df_copy[constants.COL_PRICE].apply(clean_price_string)
    buys_df['quantity'] = df_copy[constants.COL_VOLUME].apply(clean_quantity)

    # Create masks from the original DataFrame to avoid reindexing issues
    broker_mask = df_copy[constants.COL_NOTES].str.contains('broker', case=False, na=False)
    seller_short_mask = (
        df_copy[constants.COL_SELLER].astype(str).str.strip().str.upper()
        != constants.SHORT_POSITION_STR
    )

    # Filter out broker notes rows and SHORT POSITION rows
    buys_df = buys_df[~broker_mask & seller_short_mask].copy()

    # Filter out invalid buys (no date, zero/negative quantity)
    valid_buy_mask = (
        buys_df['date'].notna() &
        (buys_df['quantity'] > 0)
    )
    buys_df = buys_df[valid_buy_mask].copy()

    # Create Sell-Side DataFrame
    sells_df = pd.DataFrame(common_data)

    # Add sell-specific fields
    sells_df['date'] = df_copy[constants.COL_DATE1].apply(parse_date)
    sells_df['price'] = df_copy[constants.COL_PRICE1].apply(clean_price_string)
    sells_df['quantity'] = -df_copy[constants.COL_VOLUME1].apply(clean_quantity)  # Negative for sells

    # Filter out broker notes rows and LONG POSITION rows
    buyer_long_mask = (
        df_copy[constants.COL_BUYER].astype(str).str.strip().str.upper()
        != constants.LONG_POSITION_STR
    )
    sells_df = sells_df[~broker_mask & buyer_long_mask].copy()

    # Filter out invalid sells (no date, zero/positive quantity)
    valid_sell_mask = (
        sells_df['date'].notna() &
        (sells_df['quantity'] < 0)
    )
    sells_df = sells_df[valid_sell_mask].copy()

    # Concatenate Buy and Sell DataFrames
    all_trades = pd.concat([buys_df, sells_df], ignore_index=True)

    if all_trades.empty:
        return pd.DataFrame()

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


def convert_multiple_rins_tradesheets(
    input_files: List[str],
    output_file: str,
    min_trade_date: Optional[str] = None,
    fee_schedule_file: Optional[str] = None,
    include_fees: Optional[bool] = None
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Orchestrate the end-to-end conversion of one or more RINs tradesheet files.

    This function manages the entire workflow:
    1. Reads and concatenates multiple input files.
    2. Converts all regular trades.
    3. Extracts broker fees.
    4. Calculates product-specific fees.
    5. Converts all fees to the standard PnL format.
    6. Combines regular trades and fee trades.
    7. Sorts the final output and saves it to a CSV file.

    Args:
        input_files: List of paths to input tradesheet CSV files.
        output_file: Path for the output CSV file.
        min_trade_date: Minimum trade date filter in 'YYYY-MM-DD' format.
        fee_schedule_file: Path to fee schedule CSV file.
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
    if fee_schedule_file is None:
        fee_schedule_file = get_fee_schedule_path()
    if include_fees is None:
        include_fees = config.get('conversion.include_fees', True)

    logger.info(f"Processing {len(input_files)} tradesheet files:")
    for i, file_path in enumerate(input_files, 1):
        logger.debug(f"  {i}. {file_path}")

    if min_trade_date:
        logger.info(f"Will filter out trades before: {min_trade_date}")

    # Process each tradesheet file
    all_trades: List[dict] = []
    all_broker_fees: List[pd.DataFrame] = []
    total_processed = 0
    total_skipped = 0

    for input_file in input_files:
        if not os.path.exists(input_file):
            logger.warning(f"Tradesheet file not found: {input_file}")
            continue

        logger.info(f"Processing: {os.path.basename(input_file)}")

        # Read the CSV file
        try:
            df = pd.read_csv(input_file)
            logger.debug(f"Loaded {len(df)} rows from {os.path.basename(input_file)}")
        except FileNotFoundError:
            logger.error(f"Tradesheet file not found: {input_file}")
            continue
        except pd.errors.ParserError as e:
            logger.error(f"Error parsing CSV file {input_file}: {e}")
            continue
        except pd.errors.EmptyDataError:
            logger.warning(f"Tradesheet file is empty: {input_file}")
            continue

        # Convert trades using fully vectorized approach
        try:
            file_trades_df = vectorized_convert_tradesheet(df, min_trade_date)
            file_trades = file_trades_df.to_dict('records') if not file_trades_df.empty else []

            # Calculate statistics
            original_row_count = len(df)
            converted_trade_count = len(file_trades)
            file_processed = original_row_count - (original_row_count - len(file_trades_df))
            file_skipped = original_row_count - file_processed

            all_trades.extend(file_trades)
            total_processed += file_processed
            total_skipped += file_skipped

            logger.info(f"  Converted {file_processed} rows into {converted_trade_count} trades")
            logger.debug(f"  Skipped {file_skipped} rows")

        except KeyError as e:
            logger.error(f"Missing required column in {input_file}: {e}")
            logger.warning(f"Skipping file: {input_file}")
            total_skipped += len(df)
            continue

        # Extract broker fees from this tradesheet
        if include_fees:
            file_broker_fees = extract_broker_fees_from_tradesheet(df, min_trade_date)
            all_broker_fees.append(file_broker_fees)
            logger.debug(f"  Extracted {len(file_broker_fees)} broker fee records")

    # Create combined trades DataFrame
    trades_df = pd.DataFrame(all_trades) if all_trades else pd.DataFrame()

    # Process fees if requested
    all_fees = pd.DataFrame()
    if include_fees and (all_broker_fees or not trades_df.empty):
        logger.info("Processing fees...")

        # Combine broker fees from all tradesheets
        combined_broker_fees = pd.DataFrame()
        if all_broker_fees:
            broker_dfs = [df for df in all_broker_fees if not df.empty]
            if broker_dfs:
                combined_broker_fees = pd.concat(broker_dfs, ignore_index=True)

        logger.debug(f"Total broker fee records from all tradesheets: {len(combined_broker_fees)}")

        # Calculate product fees for exchange trades
        fee_schedule_df = load_fee_schedule(fee_schedule_file)
        product_fees = pd.DataFrame()
        if not trades_df.empty and not fee_schedule_df.empty:
            product_fees = calculate_product_fees(trades_df, fee_schedule_df, min_trade_date)
            logger.debug(f"Calculated {len(product_fees)} product fee records from fee schedule")

        # Combine all fees
        fee_dfs = [df for df in [combined_broker_fees, product_fees] if not df.empty]
        if fee_dfs:
            all_fees = pd.concat(fee_dfs, ignore_index=True)
            logger.debug(f"Total fees: {len(all_fees)} records")

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
        logger.info(f"- Total tradesheet files processed: {len([f for f in input_files if os.path.exists(f)])}")
        logger.info(f"- Converted {total_processed} tradesheet rows into {len(all_trades)} regular trades")
        logger.info(f"- Added {len(trades_df) - len(all_trades)} fee-related trade records")
        logger.info(f"- Total output: {len(trades_df)} trade records")
        logger.debug(f"- Skipped {total_skipped} rows (broker notes, date filters, etc.)")
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
        description="Convert RINs tradesheet to a standardized trades format.",
        formatter_class=argparse.RawTextHelpFormatter
    )

    # Get configuration defaults
    config = get_config()
    default_input = get_rins_tradesheet_path()
    default_output_dir = get_results_dir()
    default_output_path = os.path.join(
        default_output_dir,
        config.get('paths.converted_trades', 'converted_trades.csv')
    )
    default_min_date = get_min_trade_date()
    default_fee_schedule = get_fee_schedule_path()

    parser.add_argument(
        '--input-file',
        dest='input_file',
        type=str,
        default=default_input,
        help=f"Input RINs tradesheet CSV file.\n(default: {default_input})"
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
        '--fee-schedule-file',
        dest='fee_schedule_file',
        type=str,
        default=default_fee_schedule,
        help=f"Fee schedule CSV file for calculating product-based fees.\n(default: {default_fee_schedule})"
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

    logger.info("Starting RINs Tradesheet Conversion")
    logger.info("-" * 40)
    logger.info(f"  Input file:   {args.input_file}")
    logger.info(f"  Output file:  {args.output_file}")
    logger.info(f"  Min date:     {args.min_trade_date}")
    logger.info(f"  Fee schedule: {args.fee_schedule_file}")
    logger.info(f"  Include fees: {not args.no_fees}")
    logger.info("-" * 40)

    convert_multiple_rins_tradesheets(
        input_files=[args.input_file],
        output_file=args.output_file,
        min_trade_date=args.min_trade_date,
        fee_schedule_file=args.fee_schedule_file,
        include_fees=not args.no_fees
    )


if __name__ == "__main__":
    main()

