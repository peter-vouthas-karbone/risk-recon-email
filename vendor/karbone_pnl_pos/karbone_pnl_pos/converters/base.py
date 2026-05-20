#!/usr/bin/env python3
"""
Base Protocols for Trade Converters.

This module defines the interface contracts (Protocols) that trade converters
should implement. These protocols enable dependency injection, polymorphic
usage, and easier testing through type-safe abstractions.

Note:
    These protocols define the expected interfaces but are not currently
    implemented by any concrete classes. The functional approach in
    clean_fuels_trades.py and clean_power_forward_trades.py is preferred for now.
    Consider implementing these protocols if a class-based approach becomes
    necessary for extensibility.
"""

from typing import Optional, Protocol, Tuple

import pandas as pd


class TradeConverter(Protocol):
    """
    Protocol defining the interface for trade converters.

    All trade converters (fuels, power, etc.) should implement this interface
    to ensure consistent behavior and enable polymorphic usage.

    Example:
        class FuelsTradeConverter:
            def convert(
                self,
                input_df: pd.DataFrame,
                min_trade_date: Optional[str] = None,
                include_fees: bool = True,
            ) -> Tuple[pd.DataFrame, pd.DataFrame]:
                # Implementation here
                ...
    """

    def convert(
        self,
        input_df: pd.DataFrame,
        min_trade_date: Optional[str] = None,
        include_fees: bool = True,
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Convert raw tradesheet data to standardized trade format.

        Args:
            input_df: Raw tradesheet DataFrame containing unprocessed trade data.
            min_trade_date: Minimum trade date filter in 'YYYY-MM-DD' format.
                           Trades before this date are excluded.
            include_fees: Whether to include fee processing in the conversion.

        Returns:
            A tuple of (trades_df, fees_df) where:
            - trades_df: Standardized trade records with columns matching
                        FINAL_OUTPUT_COLUMNS in constants.py.
            - fees_df: Extracted fee records with fee-specific columns.
        """
        ...


class FeeExtractor(Protocol):
    """
    Protocol defining the interface for fee extraction.

    Fee extractors handle the identification and extraction of various
    fee types (broker fees, exchange fees, etc.) from tradesheets.
    """

    def extract_fees(
        self,
        df: pd.DataFrame,
        min_trade_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Extract fee records from tradesheet data.

        Args:
            df: Raw tradesheet DataFrame.
            min_trade_date: Minimum trade date filter in 'YYYY-MM-DD' format.

        Returns:
            DataFrame containing extracted fee records with columns:
            - date: Fee date in 'YYYY-MM-DD' format.
            - fee_type: Type of fee ('broker', 'exchange', etc.).
            - fee_quantity: Volume/quantity for fee calculation.
            - charge: Per-unit charge amount.
            - fee_amount: Total fee amount (fee_quantity * charge).
            - desk: Trading desk identifier.
        """
        ...


class TradeNormalizer(Protocol):
    """
    Protocol for trade data normalization.

    Normalizers handle the standardization of product names,
    vintage formats, and other trade attributes to ensure consistency
    across the system.
    """

    def normalize(self, value: str) -> str:
        """
        Normalize a single value according to specific rules.

        Args:
            value: Raw value to normalize.

        Returns:
            Normalized value as a string.
        """
        ...
