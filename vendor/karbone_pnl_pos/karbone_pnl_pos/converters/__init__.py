#!/usr/bin/env python3
"""
Converters Package.

This package contains trade conversion utilities for transforming raw tradesheet
data into standardized formats for PnL calculations.

Modules:
    base: Protocol definitions for trade converters (interfaces).
    utils: Shared utility functions for data cleaning and normalization.

Usage:
    >>> from converters import clean_price_string, parse_date
    >>> price = clean_price_string("$1,234.56")
    >>> date = parse_date("01/15/2024")
"""

from .base import FeeExtractor, TradeConverter, TradeNormalizer
from .utils import (
    clean_price_string,
    clean_quantity,
    convert_fees_to_pnl_format,
    determine_desk,
    determine_exchange,
    determine_settlement,
    is_before_min_date,
    normalize_product,
    parse_date,
)

__all__ = [
    # Protocols
    "TradeConverter",
    "FeeExtractor",
    "TradeNormalizer",
    # Utility functions
    "clean_price_string",
    "clean_quantity",
    "parse_date",
    "determine_settlement",
    "determine_desk",
    "determine_exchange",
    "normalize_product",
    "is_before_min_date",
    "convert_fees_to_pnl_format",
]
