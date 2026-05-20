#!/usr/bin/env python3
"""
Shared Utilities for Trade Converters.

This module contains common functions used by both fuels and power trade converters.
These functions handle data cleaning, parsing, and standardization of trade data.

Functions in this module are designed to be pure and stateless, making them
easily testable and reusable across different converter implementations.
"""

from datetime import datetime
from typing import Dict, Optional, Union

import pandas as pd

from karbone_pnl_pos.utils import constants


def clean_price_string(price_str: Union[str, float, None]) -> float:
    """
    Parse price string, handling currency symbols and parenthetical negatives.

    Cleans a price string by removing currency symbols and commas, and converts
    it to a float. Handles negative values represented by parentheses,
    e.g., '($1.23)' -> -1.23.

    Args:
        price_str: Raw price string (e.g., "$1,234.56", "($100)") or numeric value.

    Returns:
        Parsed float value, or 0.0 if invalid or empty.

    Examples:
        >>> clean_price_string("$1,234.56")
        1234.56
        >>> clean_price_string("($100)")
        -100.0
        >>> clean_price_string(None)
        0.0
    """
    if pd.isna(price_str) or price_str == '':
        return 0.0

    # Remove dollar signs and convert to string
    cleaned = str(price_str).replace('$', '').replace(',', '').strip()

    # Handle parentheses for negative values
    if cleaned.startswith('(') and cleaned.endswith(')'):
        cleaned = '-' + cleaned[1:-1]

    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return 0.0


def clean_quantity(qty_str: Union[str, float, None]) -> float:
    """
    Clean a quantity string by removing commas and convert to float.

    Args:
        qty_str: Raw quantity string (e.g., "1,000", "500") or numeric value.

    Returns:
        Parsed float value, or 0.0 if invalid or empty.

    Examples:
        >>> clean_quantity("1,000")
        1000.0
        >>> clean_quantity(500)
        500.0
    """
    if pd.isna(qty_str) or qty_str == '':
        return 0.0

    # Convert to string and remove commas
    cleaned = str(qty_str).replace(',', '').strip()

    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return 0.0


def parse_date(date_str: Union[str, None]) -> Optional[str]:
    """
    Parse a date string from various common formats into standardized 'YYYY-MM-DD'.

    Supports multiple input formats including:
    - MM/DD/YYYY (US format)
    - YYYY-MM-DD (ISO format)
    - DD/MM/YYYY (European format)
    - YYYY-MM-DD HH:MM:SS (datetime format - extracts date part)

    Args:
        date_str: Raw date string in any supported format.

    Returns:
        Standardized date string in 'YYYY-MM-DD' format, or None if invalid.

    Examples:
        >>> parse_date("01/15/2024")
        '2024-01-15'
        >>> parse_date("2024-01-15")
        '2024-01-15'
        >>> parse_date("2024-01-15 02:00:00")
        '2024-01-15'
    """
    if pd.isna(date_str) or date_str == '':
        return None

    date_str_clean = str(date_str).strip()
    
    # First try parsing as datetime (handles YYYY-MM-DD HH:MM:SS format)
    # Common datetime formats
    datetime_formats = [
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d %H:%M:%S.%f',
        '%m/%d/%Y %H:%M:%S',
        '%d/%m/%Y %H:%M:%S',
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%dT%H:%M:%S.%f'
    ]
    
    for fmt in datetime_formats:
        try:
            dt = datetime.strptime(date_str_clean, fmt)
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            continue
    
    # If datetime parsing fails, try date-only formats
    for fmt in constants.DATE_FORMATS:
        try:
            dt = datetime.strptime(date_str_clean, fmt)
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            continue
    
    # Last resort: try to extract date from string if it starts with YYYY-MM-DD
    # This handles cases like "2025-07-31 02:00:00" by taking first 10 chars
    if len(date_str_clean) >= 10:
        try:
            date_part = date_str_clean[:10]
            dt = datetime.strptime(date_part, '%Y-%m-%d')
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            pass
    
    return None


def determine_settlement(platform: Union[str, None]) -> bool:
    """
    Determine if a trade is physically settled based on the trading platform.

    ICE trades are considered financially settled; all others are physical by default.

    Args:
        platform: Trading platform name (e.g., "ICE", "OTC").

    Returns:
        True if physically settled, False if financially settled.
    """
    if pd.isna(platform):
        return constants.SETTLEMENT_PHYSICAL  # Default to physically settled

    platform_str = str(platform).strip()
    if platform_str.upper() == constants.PLATFORM_ICE:
        return constants.SETTLEMENT_FINANCIAL  # Financial settlement = not physically settled
    else:
        return constants.SETTLEMENT_PHYSICAL  # Physical settlement = physically settled


def convert_fees_to_pnl_format(
    fees_df: pd.DataFrame,
    desk: str = constants.DEFAULT_DESK
) -> pd.DataFrame:
    """
    Convert a DataFrame of calculated fees into standardized PnL trade format.

    For each fee, this function creates two synthetic, offsetting trades:
    1. A "buy" trade representing the fee as an expense (price = charge, quantity > 0).
    2. A "sell" trade to zero out the quantity impact, ensuring fees do not affect
       the net position of any instrument (price = 0, quantity < 0).

    This allows fees to be processed as cash flows within the PnL system.

    Args:
        fees_df: DataFrame containing fee records with columns:
                 date, fee_type, fee_quantity, charge, desk, and optionally
                 product and vintage.
        desk: Default desk name if not specified in fees_df.

    Returns:
        DataFrame with synthetic trade records in PnL format matching
        FINAL_OUTPUT_COLUMNS in constants.py.
    """
    if fees_df.empty:
        return pd.DataFrame()

    pnl_rows = []

    for _, fee_row in fees_df.iterrows():
        date = fee_row['date']
        fee_type = fee_row['fee_type']
        fee_quantity = fee_row['fee_quantity']
        charge = fee_row['charge']
        row_desk = fee_row.get('desk', desk)

        # Handle product and vintage based on fee type
        if fee_type == constants.FEE_TYPE_BROKER:
            # Broker fees: no specific product/vintage granularity
            product = constants.FEE_PRODUCT_BROKER
            # Determine vintage as month when fee was incurred
            try:
                date_obj = datetime.strptime(date, '%Y-%m-%d')
                vintage = date_obj.strftime('%Y-%m')  # YYYY-MM format
            except (ValueError, TypeError):
                vintage = date[:7] if len(str(date)) >= 7 else str(date)
            exchange_flag = constants.EXCHANGE_FALSE
            settlement_type = constants.SETTLEMENT_PHYSICAL
        else:
            # Exchange-related fees: use actual product and vintage
            product = fee_row.get('product', fee_type)
            vintage = fee_row.get('vintage')
            if vintage is None:
                try:
                    date_obj = datetime.strptime(date, '%Y-%m-%d')
                    vintage = date_obj.strftime('%Y-%m')
                except (ValueError, TypeError):
                    vintage = date[:7] if len(str(date)) >= 7 else str(date)
            exchange_flag = constants.EXCHANGE_TRUE
            settlement_type = constants.SETTLEMENT_FINANCIAL

        # Create base dictionary with all shared fields
        base_pnl_record: Dict[str, object] = {
            'date': date,
            'product': product,
            'vintage': vintage,
            'desk': row_desk,
            'portfolio': fee_row.get('portfolio', ''),
            'strategy': fee_row.get('strategy', ''),
            'is_exchange_settled': exchange_flag,
            'is_physically_settled': settlement_type,
            'is_fee': True,
            'fee_type': fee_type,
            'currency': constants.DEFAULT_CURRENCY
        }

        # Create synthetic buy record for fees (represents the fee expense)
        buy_record = base_pnl_record.copy()
        buy_record.update({
            'price': charge,
            'quantity': fee_quantity
        })
        pnl_rows.append(buy_record)

        # Create offsetting sell record (net zero impact on position)
        sell_record = base_pnl_record.copy()
        sell_record.update({
            'price': 0.0,
            'quantity': -fee_quantity
        })
        pnl_rows.append(sell_record)

    return pd.DataFrame(pnl_rows)


def determine_desk(
    position_type: Union[str, None],
    desk_map: Optional[Dict[str, str]] = None
) -> str:
    """
    Determine the trading desk based on the 'Position Type' field.

    Args:
        position_type: Raw position type string from tradesheet.
        desk_map: Optional custom mapping; defaults to constants.DESK_MAP.

    Returns:
        Normalized desk name.
    """
    if desk_map is None:
        desk_map = constants.DESK_MAP

    if pd.isna(position_type) or str(position_type).strip() == '':
        return constants.DEFAULT_DESK

    position_str = str(position_type).strip().lower()
    return desk_map.get(position_str, constants.DEFAULT_DESK)


def determine_exchange(seller: Union[str, None], buyer: Union[str, None]) -> str:
    """
    Determine if a trade is an exchange trade by checking if ICE is a counterparty.

    Args:
        seller: Seller name from tradesheet.
        buyer: Buyer name from tradesheet.

    Returns:
        'TRUE' if ICE is a counterparty, 'FALSE' otherwise.
    """
    seller_str = str(seller).strip() if not pd.isna(seller) else ''
    buyer_str = str(buyer).strip() if not pd.isna(buyer) else ''

    is_ice = (seller_str == constants.ICE_COUNTERPARTY or buyer_str == constants.ICE_COUNTERPARTY)
    return constants.EXCHANGE_TRUE if is_ice else constants.EXCHANGE_FALSE


def normalize_product(product: Union[str, None]) -> str:
    """
    Normalize product names to a consistent format.

    Handles:
    - Removing ' RIN' suffix from D-series products (e.g., 'D3 RIN' -> 'D3')
    - Standardizing plural forms (e.g., 'RTCs' -> 'RTC')

    Args:
        product: Raw product name from tradesheet.

    Returns:
        Normalized product name.
    """
    if not product or pd.isna(product):
        return str(product) if not pd.isna(product) else ''

    product_str = str(product).strip()

    # Remove " RIN" suffix for D3, D4, D6 products
    if product_str.endswith(constants.PRODUCT_RIN_SUFFIX):
        base_product = product_str.replace(constants.PRODUCT_RIN_SUFFIX, '')
        if base_product in constants.RIN_PRODUCTS:
            return base_product

    # Apply standard normalizations
    if product_str in constants.PRODUCT_NORMALIZATIONS:
        return constants.PRODUCT_NORMALIZATIONS[product_str]

    return product_str


def is_before_min_date(date_str: Optional[str], min_date: Optional[str]) -> bool:
    """
    Check if a given date string is chronologically before the minimum trade date.

    Args:
        date_str: Date string in 'YYYY-MM-DD' format.
        min_date: Minimum date string in 'YYYY-MM-DD' format.

    Returns:
        True if date_str < min_date, False otherwise.
    """
    if not date_str or not min_date:
        return False
    return date_str < min_date


def convert_hour_to_he_vintage(hour: Union[int, str]) -> str:
    """
    Convert integer hour (0-23) to "HE##" format with zero-padding (hour ending).

    Converts hour values to NYISO vintage format where hours are represented
    as "HE01", "HE02", ..., "HE24" with zero-padding. The input hour (0-23)
    is converted to hour ending format by adding 1 (so hour 0 becomes HE01,
    hour 23 becomes HE24).

    Args:
        hour: Integer hour (0-23) or string representation of hour.

    Returns:
        Vintage string in "HE##" format (e.g., "HE01", "HE24").

    Examples:
        >>> convert_hour_to_he_vintage(0)
        'HE01'
        >>> convert_hour_to_he_vintage(23)
        'HE24'
        >>> convert_hour_to_he_vintage("1")
        'HE02'
    """
    try:
        # Convert to int if string
        if isinstance(hour, str):
            hour = int(hour.strip())
        
        # Validate range (0-23 for standard hour format)
        if not (0 <= hour <= 23):
            raise ValueError(f"Hour must be between 0 and 23, got {hour}")
        
        # Convert to hour ending format by adding 1 (0->1, 1->2, ..., 23->24)
        hour_ending = hour + 1
        
        # Format with zero-padding
        return f"HE{hour_ending:02d}"
    
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid hour value: {hour}") from e
