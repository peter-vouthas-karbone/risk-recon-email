#!/usr/bin/env python3
"""
Forward Curve Price Loader.

This module loads and standardizes forward curve price data from various ISOs
(PJM, NYISO, ERCOT, NEPOOL, CAISO, MISO) for integration into the PnL system.

The loader handles:
- Reading CSV files with forward curve data
- Standardizing column names to match the PnL price format
- Filtering by product and date range
- Combining multiple curve sources
- Deduplication of overlapping data

Usage:
    >>> from forward_curve_loader import ForwardCurveLoader
    >>> loader = ForwardCurveLoader()
    >>> df, vintages = loader.load_all_forward_curves({
    ...     'pjm': '/path/to/pjm.csv',
    ...     'nyiso': '/path/to/nyiso.csv'
    ... })
"""

import logging
import os
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

from karbone_pnl_pos.utils import constants


class ForwardCurveLoader:
    """
    Loads forward curve pricing data and standardizes it for the PnL system.

    This class handles reading forward curve CSV files with specific column formats
    and transforms them into the standardized price format used by the PnL calculator.

    Column Mapping:
        - AS_OF_DATE -> date
        - SETTLE -> px (price)
        - EXPIRATION_DATE_YEAR + EXPIRATION_DATE_MONTH -> vintage (e.g., "2025-10")
        - CONTRACT -> product

    Attributes:
        logger: Logger instance for this class.
    """

    def __init__(self) -> None:
        """Initialize the forward curve loader."""
        self.logger = logging.getLogger('pnl.' + __name__)

    def load_forward_curve_file(self, file_path: str) -> Optional[pd.DataFrame]:
        """
        Load a single forward curve price file and standardize the format.

        Args:
            file_path: Path to the forward curve CSV file.

        Returns:
            Standardized DataFrame with columns: date, product, vintage, px.
            Returns None if file doesn't exist, is empty, or is invalid.
        """
        if not os.path.exists(file_path):
            self.logger.warning(f"Forward curve file not found: {file_path}")
            return None

        if os.path.getsize(file_path) == 0:
            self.logger.warning(f"Forward curve file is empty: {file_path}")
            return None

        try:
            df = pd.read_csv(file_path, low_memory=False)

            if df.empty:
                self.logger.warning(f"Forward curve file contains no data: {file_path}")
                return None

            # Validate required columns
            missing_columns = [
                col for col in constants.FORWARD_CURVE_REQUIRED_COLUMNS
                if col not in df.columns
            ]

            if missing_columns:
                self.logger.error(
                    f"Forward curve file {file_path} missing required columns: {missing_columns}"
                )
                return None

            # Standardize the data
            standardized_df = self._standardize_forward_curve_data(df)

            if standardized_df is not None and not standardized_df.empty:
                self.logger.info(
                    f"Loaded {len(standardized_df)} forward curve prices from {file_path}"
                )
                return standardized_df
            else:
                self.logger.warning(f"No valid data after standardization: {file_path}")
                return None

        except pd.errors.ParserError as e:
            self.logger.error(f"Error parsing forward curve file {file_path}: {str(e)}")
            return None
        except pd.errors.EmptyDataError:
            self.logger.warning(f"Forward curve file is empty: {file_path}")
            return None
        except KeyError as e:
            self.logger.error(f"Missing column in forward curve file {file_path}: {str(e)}")
            return None

    def _standardize_forward_curve_data(self, df: pd.DataFrame) -> Optional[pd.DataFrame]:
        """
        Standardize forward curve data to match the PnL system's price format.

        Transforms:
            - AS_OF_DATE -> date (YYYY-MM-DD format)
            - SETTLE -> px (price)
            - EXPIRATION_DATE_YEAR + EXPIRATION_DATE_MONTH -> vintage (YYYY-MM format)
            - CONTRACT -> product

        Args:
            df: Raw forward curve DataFrame.

        Returns:
            Standardized DataFrame or None if standardization fails.
        """
        try:
            standardized_df = df.copy()

            # Map AS_OF_DATE to date
            standardized_df['date'] = pd.to_datetime(
                standardized_df[constants.FORWARD_CURVE_COL_AS_OF_DATE]
            ).dt.strftime('%Y-%m-%d')

            # Map SETTLE to px (price)
            standardized_df['px'] = standardized_df[constants.FORWARD_CURVE_COL_SETTLE].astype(float)

            # Map CONTRACT to product
            standardized_df['product'] = standardized_df[constants.FORWARD_CURVE_COL_CONTRACT].astype(str)

            # Create vintage from EXPIRATION_DATE_YEAR and EXPIRATION_DATE_MONTH
            standardized_df['vintage'] = (
                standardized_df[constants.FORWARD_CURVE_COL_EXPIRATION_YEAR].astype(str) + '-' +
                standardized_df[constants.FORWARD_CURVE_COL_EXPIRATION_MONTH].astype(str).str.zfill(2)
            )

            # Select only the required columns in the correct order
            final_columns = ['date', 'product', 'vintage', 'px']
            standardized_df = standardized_df[final_columns]

            # Remove rows with missing essential data
            standardized_df = standardized_df.dropna(subset=['date', 'product', 'vintage', 'px'])

            # Remove duplicate entries (keep last for each date-product-vintage combination)
            standardized_df = standardized_df.sort_values(['date', 'product', 'vintage'])
            standardized_df = standardized_df.drop_duplicates(
                subset=['date', 'product', 'vintage'],
                keep='last'
            )

            self.logger.info(f"Standardized {len(standardized_df)} forward curve price records")
            self.logger.debug(f"Products: {standardized_df['product'].unique().tolist()}")
            self.logger.debug(f"Vintages: {sorted(standardized_df['vintage'].unique().tolist())}")

            return standardized_df

        except ValueError as e:
            self.logger.error(f"Value error standardizing forward curve data: {str(e)}")
            return None
        except KeyError as e:
            self.logger.error(f"Missing column in forward curve data: {str(e)}")
            return None

    def load_ercot_ancillary_file(
        self,
        file_path: str,
        products_filter: Optional[Set[str]] = None,
        min_date: Optional[pd.Timestamp] = None,
        max_date: Optional[pd.Timestamp] = None,
    ) -> Tuple[Optional[pd.DataFrame], Set[Tuple[str, str]]]:
        """
        Load the ERCOT ancillary prices file (Date, Term, Code, Mark format).

        Column mapping:
            - Date -> date (YYYY-MM-DD)
            - Term -> vintage (YYYY-MM)
            - Code -> product
            - Mark -> px (price)

        Args:
            file_path: Path to the ERCOT ancillary CSV file.
            products_filter: Optional set of product names to filter by.
            min_date: Optional minimum date filter.
            max_date: Optional maximum date filter.

        Returns:
            Tuple of (standardized DataFrame or None, set of (product, vintage) tuples).
        """
        if not os.path.exists(file_path):
            self.logger.warning(f"ERCOT ancillary file not found: {file_path}")
            return None, set()

        if os.path.getsize(file_path) == 0:
            self.logger.warning(f"ERCOT ancillary file is empty: {file_path}")
            return None, set()

        try:
            df = pd.read_csv(file_path, low_memory=False)

            if df.empty:
                self.logger.warning(f"ERCOT ancillary file contains no data: {file_path}")
                return None, set()

            missing_columns = [
                col for col in constants.ERCOT_ANCILLARY_REQUIRED_COLUMNS
                if col not in df.columns
            ]
            if missing_columns:
                self.logger.error(
                    f"ERCOT ancillary file missing required columns: {missing_columns}"
                )
                return None, set()

            result = df.copy()
            result['date'] = pd.to_datetime(
                result[constants.ERCOT_ANCILLARY_COL_DATE], format='mixed'
            ).dt.strftime('%Y-%m-%d')
            result['vintage'] = pd.to_datetime(
                result[constants.ERCOT_ANCILLARY_COL_TERM], format='mixed'
            ).dt.strftime('%Y-%m')
            result['product'] = result[constants.ERCOT_ANCILLARY_COL_CODE].astype(str)
            result['px'] = result[constants.ERCOT_ANCILLARY_COL_MARK].astype(float)

            result = result[['date', 'product', 'vintage', 'px']]
            result = result.dropna(subset=['date', 'product', 'vintage', 'px'])
            result = result.sort_values(['date', 'product', 'vintage'])
            result = result.drop_duplicates(subset=['date', 'product', 'vintage'], keep='last')

            if products_filter is not None:
                before_count = len(result)
                result = result[result['product'].isin(products_filter)]
                filtered_out = before_count - len(result)
                if filtered_out > 0:
                    self.logger.info(
                        f"  ERCOT ancillary: filtered out {filtered_out:,} prices for non-traded products"
                    )

            if min_date is not None and max_date is not None:
                before_count = len(result)
                result_dates = pd.to_datetime(result['date']).dt.date
                result = result[
                    (result_dates >= min_date.date()) &
                    (result_dates <= max_date.date())
                ]
                filtered_out = before_count - len(result)
                if filtered_out > 0:
                    self.logger.info(
                        f"  ERCOT ancillary: filtered out {filtered_out:,} prices outside date range"
                    )

            if result.empty:
                self.logger.warning("ERCOT ancillary file: no data remaining after filtering")
                return None, set()

            vintages = set(zip(result['product'], result['vintage']))
            self.logger.info(
                f"Loaded {len(result)} ERCOT ancillary prices from {file_path} "
                f"({len(vintages)} product-vintage pairs)"
            )
            return result, vintages

        except pd.errors.ParserError as e:
            self.logger.error(f"Error parsing ERCOT ancillary file {file_path}: {str(e)}")
            return None, set()
        except (KeyError, ValueError) as e:
            self.logger.error(f"Error loading ERCOT ancillary file {file_path}: {str(e)}")
            return None, set()

    def load_all_forward_curves(
        self,
        curve_paths: Dict[str, str],
        products_filter: Optional[Set[str]] = None,
        min_date: Optional[pd.Timestamp] = None,
        max_date: Optional[pd.Timestamp] = None
    ) -> Tuple[pd.DataFrame, Set[Tuple[str, str]]]:
        """
        Load and combine all forward curve files with optional filtering.

        Args:
            curve_paths: Dictionary mapping curve names to file paths,
                        e.g., {'pjm': 'path/to/pjm.csv', 'nyiso': 'path/to/nyiso.csv'}.
            products_filter: Optional set of product names to filter by.
            min_date: Optional minimum date to filter by.
            max_date: Optional maximum date to filter by.

        Returns:
            Tuple containing:
            - Combined DataFrame with all forward curve prices.
            - Set of (product, vintage) tuples that are from forward curves.
        """
        all_curves: List[pd.DataFrame] = []
        forward_curve_vintages: Set[Tuple[str, str]] = set()

        for curve_name, file_path in curve_paths.items():
            self.logger.info(f"Loading {curve_name} forward curve from {file_path}")
            curve_df = self.load_forward_curve_file(file_path)

            if curve_df is None or curve_df.empty:
                self.logger.warning(f"Skipping {curve_name} forward curve (no valid data)")
                continue

            # Apply product filter
            if products_filter is not None:
                before_count = len(curve_df)
                curve_df = curve_df[curve_df['product'].isin(products_filter)]
                filtered_out = before_count - len(curve_df)
                if filtered_out > 0:
                    self.logger.info(
                        f"  Filtered out {filtered_out:,} prices for non-traded products"
                    )

            # Apply date filter
            if min_date is not None and max_date is not None:
                before_count = len(curve_df)
                curve_dates = pd.to_datetime(curve_df['date']).dt.date
                curve_df = curve_df[
                    (curve_dates >= min_date.date()) &
                    (curve_dates <= max_date.date())
                ]
                filtered_out = before_count - len(curve_df)
                if filtered_out > 0:
                    self.logger.info(
                        f"  Filtered out {filtered_out:,} prices outside date range"
                    )

            if curve_df.empty:
                self.logger.warning(f"  No data remaining after filtering for {curve_name}")
                continue

            # Add source column for tracking
            curve_df['source'] = curve_name
            all_curves.append(curve_df)

            # Track which vintages come from forward curves (vectorized)
            forward_curve_vintages.update(
                set(zip(curve_df['product'], curve_df['vintage']))
            )

        if not all_curves:
            self.logger.warning("No valid forward curve data loaded")
            return pd.DataFrame(), set()

        # Combine all curves
        combined_df = pd.concat(all_curves, ignore_index=True)

        # Sort and handle duplicates across files
        combined_df = combined_df.sort_values(['date', 'product', 'vintage', 'source'])

        # Before dropping duplicates, save the source info for logging
        total_before = len(combined_df)

        combined_df = combined_df.drop_duplicates(
            subset=['date', 'product', 'vintage'],
            keep='last'
        )

        # Drop the source column for consistency with price format
        combined_df = combined_df.drop(columns=['source'])

        duplicates_removed = total_before - len(combined_df)
        if duplicates_removed > 0:
            self.logger.info(
                f"Removed {duplicates_removed} duplicate records across curve files"
            )

        self.logger.info(f"Total forward curve prices loaded: {len(combined_df)}")
        self.logger.info(f"Forward curve vintages identified: {len(forward_curve_vintages)}")

        return combined_df, forward_curve_vintages

