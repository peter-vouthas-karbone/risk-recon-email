#!/usr/bin/env python3
"""
Position Calculator.

This module computes cumulative positions from converted trades data. It calculates
end-of-day positions by summing trade quantities grouped by desk, product, and vintage.

The position calculation is simpler than PnL calculation as it doesn't require:
- Price data
- Mark-to-market valuation
- Cost basis tracking

It only needs to track the cumulative quantity for each unique combination of
desk/product/vintage over time.

Usage:
    from calculate_pos import PositionCalculator
    
    calculator = PositionCalculator()
    position_df = calculator.calculate_positions(converted_trades_df)
"""

import logging
from typing import Optional

import pandas as pd

from karbone_pnl_pos.utils import constants

# Module logger
logger = logging.getLogger('pnl.' + __name__)


class PositionCalculator:
    """
    Calculates cumulative positions from converted trades data.
    
    This class implements the business logic for position calculations without
    any orchestration or I/O operations. It takes a standardized trades DataFrame
    and produces a position DataFrame showing end-of-day positions for each
    desk/product/vintage combination.
    
    The calculation is straightforward:
    - Group trades by desk, product, vintage
    - Sort by date within each group
    - Calculate cumulative sum of quantities
    - The cumulative sum at each date represents the position at end of that day
    """
    
    def __init__(self) -> None:
        """Initialize the position calculator."""
        pass
    
    def calculate_positions(
        self,
        converted_trades_df: pd.DataFrame,
        include_zero_positions: bool = False
    ) -> pd.DataFrame:
        """
        Calculate cumulative positions from converted trades.
        
        This method:
        1. Validates the input DataFrame has required columns
        2. Filters out fees and rows with empty product columns
        3. Aggregates by date/desk/portfolio/strategy/product/vintage (sums quantities per day)
        4. Calculates cumulative sum to get end-of-day positions
        5. Optionally filters out zero positions
        
        Args:
            converted_trades_df: DataFrame with standardized trade records.
                                Must have columns: date, desk, product, vintage, quantity
            include_zero_positions: If True, includes rows where position is zero.
                                   If False, filters them out (default).
        
        Returns:
            DataFrame with columns: date, desk, strategy, product, vintage, position
            One row per date/desk/strategy/product/vintage combination.
            Strategy column is empty string for all desks; portfolio column contains
            the ISO identifier (nyiso, pjm, miso, caiso) for power_short_term desk trades.
            
        Raises:
            ValueError: If required columns are missing from input DataFrame.
        """
        if converted_trades_df.empty:
            logger.warning("Empty trades DataFrame provided to position calculator")
            return self._create_empty_position_df()
        
        # Validate required columns
        required_columns = ['date', 'desk', 'product', 'vintage', 'quantity']
        missing_columns = [col for col in required_columns if col not in converted_trades_df.columns]
        
        if missing_columns:
            raise ValueError(
                f"Missing required columns in converted_trades_df: {missing_columns}. "
                f"Required: {required_columns}"
            )
        
        logger.info(f"Calculating positions from {len(converted_trades_df)} trades...")
        
        # Create a working copy with all columns we might need
        df = converted_trades_df.copy()
        
        # Filter out fees
        df = self._filter_fees(df)
        logger.info(f"After filtering fees: {len(df)} trades")

        # Filter out rows with empty product column
        df = self._filter_empty_products(df)
        logger.info(f"After filtering empty products: {len(df)} trades")

        if df.empty:
            logger.warning("No trades remaining after filtering")
            return self._create_empty_position_df()
        
        # Initialize portfolio and strategy columns if they don't exist
        if 'portfolio' not in df.columns:
            df['portfolio'] = ''
        if 'strategy' not in df.columns:
            df['strategy'] = ''

        # Keep required columns plus portfolio and strategy
        columns_to_keep = required_columns + ['portfolio', 'strategy']
        df = df[[col for col in columns_to_keep if col in df.columns]].copy()
        
        # Ensure date is in datetime format for proper sorting
        df['date'] = pd.to_datetime(df['date'])
        
        # Step 1: Aggregate by date/desk/portfolio/strategy/product/vintage to get daily net quantities
        # This collapses multiple trades on the same day for the same combination
        daily_agg = df.groupby(
            ['date', 'desk', 'portfolio', 'strategy', 'product', 'vintage'],
            as_index=False
        ).agg({'quantity': 'sum'})

        logger.info(f"After aggregating by date/desk/portfolio/strategy/product/vintage: {len(daily_agg)} daily records")

        # Step 2: Sort by desk, portfolio, strategy, product, vintage, then date
        daily_agg = daily_agg.sort_values(['desk', 'portfolio', 'strategy', 'product', 'vintage', 'date'])

        # Step 3: Calculate cumulative sum within each desk/portfolio/strategy/product/vintage group
        # Reset index to ensure proper grouping for cumsum
        daily_agg = daily_agg.reset_index(drop=True)
        daily_agg['position'] = daily_agg.groupby(
            ['desk', 'portfolio', 'strategy', 'product', 'vintage'],
            group_keys=False
        )['quantity'].cumsum()

        # Select final columns
        position_df = daily_agg[['date', 'desk', 'portfolio', 'strategy', 'product', 'vintage', 'position']].copy()
        
        # Step 4: Forward-fill positions for all dates between trades
        # This ensures we have a record for every day with a non-zero position, not just days with trades
        if not position_df.empty:
            max_date = position_df['date'].max()
            min_date = position_df['date'].min()
            
            # Create complete date range
            all_dates = pd.date_range(start=min_date, end=max_date, freq='D')
            
            # For each desk/strategy/product/vintage combination, forward-fill positions
            expanded_positions = []
            
            for (desk, portfolio, strategy, product, vintage), group in position_df.groupby(
                ['desk', 'portfolio', 'strategy', 'product', 'vintage']
            ):
                # Create a DataFrame with all dates
                date_df = pd.DataFrame({'date': all_dates})

                # Merge with group to get positions on trade dates
                merged = date_df.merge(
                    group[['date', 'position']],
                    on='date',
                    how='left'
                )

                # Forward-fill positions (carry last known position forward)
                # Only forward-fill from the first trade date (don't backfill before first trade)
                merged['position'] = merged['position'].ffill()

                # Add back the grouping columns
                merged['desk'] = desk
                merged['portfolio'] = portfolio
                merged['strategy'] = strategy
                merged['product'] = product
                merged['vintage'] = vintage

                # Only keep rows where position is not null (i.e., from first trade date onwards)
                merged = merged[merged['position'].notna()]

                expanded_positions.append(merged)

            if expanded_positions:
                position_df = pd.concat(expanded_positions, ignore_index=True)
                position_df = position_df[['date', 'desk', 'portfolio', 'strategy', 'product', 'vintage', 'position']]
                logger.info(f"Expanded positions to all dates: {len(position_df)} records (was {len(daily_agg)} trade-date records)")
        
        # Step 5: Ensure latest positions are reported on the latest date in dataset
        # This handles cases where there are no trades on the latest date but positions should still be shown
        if not position_df.empty:
            max_date = position_df['date'].max()
            max_date_str = max_date.strftime('%Y-%m-%d')
            
            # Debug: Check LCFS in position_df before processing
            lcfs_in_df = position_df[(position_df['desk'] == 'rin') & (position_df['product'] == 'LCFS')]
            logger.info(f"LCFS positions in position_df before latest update: {len(lcfs_in_df)} rows")
            if not lcfs_in_df.empty:
                lcfs_vintages = lcfs_in_df['vintage'].unique()
                logger.info(f"  LCFS vintages: {sorted(lcfs_vintages)}")
            
            # Get the latest position for each desk/strategy/product/vintage combination
            # Sort by date first to ensure we get the most recent position
            position_df_sorted = position_df.sort_values('date')
            
            # Get the last row for each group - this preserves all columns
            latest_positions = position_df_sorted.groupby(
                ['desk', 'portfolio', 'strategy', 'product', 'vintage'],
                group_keys=False,
                as_index=False
            ).tail(1).reset_index(drop=True)
            
            logger.info(f"latest_positions columns: {latest_positions.columns.tolist()}")
            logger.info(f"latest_positions shape: {latest_positions.shape}")
            if not latest_positions.empty:
                logger.info(f"Sample latest_positions row: desk={latest_positions.iloc[0]['desk']}, product={latest_positions.iloc[0]['product']}")
            
            # Debug: Log what we found
            logger.info(f"Found {len(latest_positions)} unique combinations to update to {max_date_str}")
            lcfs_latest = latest_positions[(latest_positions['desk'] == 'rin') & (latest_positions['product'] == 'LCFS')]
            logger.info(f"  Including {len(lcfs_latest)} LCFS combinations for rin desk")
            if not lcfs_latest.empty:
                for _, row in lcfs_latest.iterrows():
                    logger.info(f"    LCFS {row['vintage']}: current date {row['date'].strftime('%Y-%m-%d')}, position {row['position']}")
            
            # Update dates to max_date for latest positions
            latest_positions['date'] = max_date
            logger.info(f"Updated {len(latest_positions)} positions to date {max_date_str}")
            
            # Remove any existing rows for max_date (to avoid duplicates)
            # Convert max_date to same type as position_df['date'] for comparison
            rows_before_remove = len(position_df)
            position_df = position_df[position_df['date'] != max_date]
            rows_after_remove = len(position_df)
            removed_count = rows_before_remove - rows_after_remove
            logger.info(f"Removed {removed_count} existing rows for {max_date_str}")
            
            # Append the latest positions with updated date
            position_df = pd.concat([position_df, latest_positions], ignore_index=True)
            rows_after_add = len(position_df)
            logger.info(f"Added {len(latest_positions)} latest positions, total rows now: {rows_after_add}")
            
            # Verify LCFS was added
            lcfs_check = position_df[(position_df['desk'] == 'rin') & (position_df['product'] == 'LCFS')]
            lcfs_on_max = lcfs_check[lcfs_check['date'] == max_date] if 'date' in lcfs_check.columns else pd.DataFrame()
            if 'date' in position_df.columns:
                position_df_dt_check = position_df.copy()
                position_df_dt_check['date'] = pd.to_datetime(position_df_dt_check['date'])
                lcfs_on_max = position_df_dt_check[
                    (position_df_dt_check['desk'] == 'rin') & 
                    (position_df_dt_check['product'] == 'LCFS') &
                    (position_df_dt_check['date'] == max_date)
                ]
            logger.info(f"LCFS positions on {max_date_str} after concat: {len(lcfs_on_max)} rows")
            
            # Debug: Check LCFS positions after update (only if columns exist)
            if all(col in position_df.columns for col in ['desk', 'product', 'vintage', 'position', 'date']):
                position_df_dt = position_df.copy()
                position_df_dt['date'] = pd.to_datetime(position_df_dt['date'])
                lcfs_after = position_df_dt[
                    (position_df_dt['desk'] == 'rin') & 
                    (position_df_dt['product'] == 'LCFS') &
                    (position_df_dt['date'] == max_date)
                ]
                if not lcfs_after.empty:
                    logger.info(f"LCFS positions on {max_date_str} after update:")
                    for _, row in lcfs_after.iterrows():
                        logger.info(f"  {row['vintage']}: {row['position']}")
            
            logger.info(f"Added latest positions for {len(latest_positions)} combinations on {max_date_str}")
        
        # Columns are already in correct order: date, desk, strategy, product, vintage, position
        
        # Convert date back to string format
        position_df['date'] = position_df['date'].dt.strftime('%Y-%m-%d')
        
        # Filter zero positions if requested (default is False, so filter by default)
        logger.info(f"include_zero_positions parameter: {include_zero_positions}")
        if not include_zero_positions:
            original_count = len(position_df)
            position_df = self.filter_zero_positions(position_df)
            filtered_count = original_count - len(position_df)
            if filtered_count > 0:
                logger.info(f"Filtered out {filtered_count} zero positions")
        else:
            logger.info("Zero positions will be included in output")
        
        # Sort final output
        position_df = position_df.sort_values(['date', 'desk', 'portfolio', 'strategy', 'product', 'vintage'])
        position_df = position_df.reset_index(drop=True)
        
        logger.info(
            f"Generated {len(position_df)} position records "
            f"({position_df['desk'].nunique()} desks, "
            f"{position_df['product'].nunique()} products, "
            f"{position_df['vintage'].nunique()} vintages)"
        )
        
        return position_df
    
    def _filter_fees(self, trades_df: pd.DataFrame) -> pd.DataFrame:
        """
        Filter out fee trades from the DataFrame.
        
        Identifies fees using multiple methods:
        - is_fee column (if present)
        - fee_type column (if not 'regular', 'none', '', or 'nan')
        - Product names containing fee keywords (broker, nfa, clearing, commission)
        
        Args:
            trades_df: Trades DataFrame.
        
        Returns:
            DataFrame with fee trades removed.
        """
        if trades_df.empty:
            return trades_df
        
        original_count = len(trades_df)
        
        # Start with all rows included
        fee_mask = pd.Series([False] * len(trades_df), index=trades_df.index)
        
        # Check is_fee column
        # Note: is_fee may be boolean True/False OR string 'TRUE'/'FALSE' (from constants)
        if 'is_fee' in trades_df.columns:
            is_fee_col = trades_df['is_fee']
            # Handle both boolean and string values
            # Convert to string first to handle all cases uniformly
            is_fee_str = is_fee_col.astype(str).str.upper().str.strip()
            is_fee_bool = (
                (is_fee_str == 'TRUE') |
                (is_fee_str == 'T') |
                (is_fee_col == True)  # noqa: E712 - explicit comparison needed for boolean
            )
            fee_mask = fee_mask | is_fee_bool.fillna(False)
        
        # Check fee_type column
        if 'fee_type' in trades_df.columns:
            fee_type_mask = (
                (trades_df['fee_type'].astype(str).str.lower() != 'regular') &
                (trades_df['fee_type'].astype(str).str.lower() != 'none') &
                (trades_df['fee_type'].astype(str).str.lower() != '') &
                (trades_df['fee_type'].astype(str).str.lower() != 'nan')
            )
            fee_mask = fee_mask | fee_type_mask
        
        # Check product names for fee keywords
        if 'product' in trades_df.columns:
            product_mask = trades_df['product'].astype(str).str.lower().str.contains(
                '|'.join(constants.FEE_KEYWORDS), na=False, regex=True
            )
            fee_mask = fee_mask | product_mask
        
        # Filter out fee trades (keep non-fee trades)
        result_df = trades_df[~fee_mask].copy()
        
        filtered_count = original_count - len(result_df)
        if filtered_count > 0:
            logger.info(f"Filtered out {filtered_count} fee trades")
        
        return result_df
    
    def _filter_empty_products(self, trades_df: pd.DataFrame) -> pd.DataFrame:
        """
        Filter out rows with empty, null, or NaN product column values.
        
        Args:
            trades_df: Trades DataFrame.
        
        Returns:
            DataFrame with empty product rows removed.
        """
        if trades_df.empty:
            return trades_df
        
        if 'product' not in trades_df.columns:
            return trades_df
        
        original_count = len(trades_df)
        
        # Filter out rows where product is empty, null, or NaN
        # Check for: NaN, None, empty string, or whitespace-only strings
        product_mask = (
            trades_df['product'].notna() &
            (trades_df['product'].astype(str).str.strip() != '') &
            (trades_df['product'].astype(str).str.strip() != 'nan')
        )
        
        result_df = trades_df[product_mask].copy()
        
        filtered_count = original_count - len(result_df)
        if filtered_count > 0:
            logger.info(f"Filtered out {filtered_count} trades with empty product column")
        
        return result_df
    
    def filter_zero_positions(
        self,
        position_df: pd.DataFrame
    ) -> pd.DataFrame:
        """
        Filter out rows where position is zero or very close to zero.
        
        Uses a small epsilon to handle floating point precision issues.
        
        Args:
            position_df: Position DataFrame.
        
        Returns:
            Filtered DataFrame with zero positions removed.
        """
        if position_df.empty:
            return position_df
        
        original_count = len(position_df)
        
        # Filter out positions that are effectively zero (within floating point tolerance)
        position_df = position_df[position_df['position'].abs() > constants.EPSILON].copy()
        
        filtered_count = original_count - len(position_df)
        if filtered_count > 0:
            logger.info(f"Filtered out {filtered_count} zero positions")
        
        return position_df
    
    def get_position_summary(self, position_df: pd.DataFrame) -> dict:
        """
        Get summary statistics for the position DataFrame.
        
        Args:
            position_df: Position DataFrame.
        
        Returns:
            Dictionary with summary statistics.
        """
        if position_df.empty:
            return {
                'total_records': 0,
                'unique_dates': 0,
                'unique_desks': 0,
                'unique_products': 0,
                'unique_vintages': 0,
                'date_range': (None, None),
            }
        
        return {
            'total_records': len(position_df),
            'unique_dates': position_df['date'].nunique(),
            'unique_desks': position_df['desk'].nunique(),
            'unique_products': position_df['product'].nunique(),
            'unique_vintages': position_df['vintage'].nunique(),
            'date_range': (position_df['date'].min(), position_df['date'].max()),
        }
    
    def _create_empty_position_df(self) -> pd.DataFrame:
        """
        Create an empty position DataFrame with correct columns.
        
        Returns:
            Empty DataFrame with position columns.
        """
        return pd.DataFrame(columns=['date', 'desk', 'portfolio', 'strategy', 'product', 'vintage', 'position'])
    
    def validate_results(self, position_df: pd.DataFrame) -> dict:
        """
        Validate the position calculation results.
        
        Checks:
        - No missing values in key columns
        - Dates are in valid format
        - Positions are numeric
        
        Args:
            position_df: Position DataFrame to validate.
        
        Returns:
            Dictionary with validation check results (True/False).
        """
        if position_df.empty:
            return {
                'has_data': False,
                'no_missing_dates': True,
                'no_missing_products': True,
                'no_missing_vintages': True,
                'positions_are_numeric': True,
            }
        
        validations = {
            'has_data': len(position_df) > 0,
            'no_missing_dates': position_df['date'].notna().all(),
            'no_missing_products': position_df['product'].notna().all(),
            'no_missing_vintages': position_df['vintage'].notna().all(),
            'positions_are_numeric': pd.api.types.is_numeric_dtype(position_df['position']),
        }
        
        return validations

