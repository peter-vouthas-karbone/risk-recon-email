#!/usr/bin/env python3
"""
Application Constants for RINs PnL Analysis System.

This module contains all application-level constants used throughout the codebase.
These include column names, magic strings, business logic mappings, regex patterns,
and standardized values.

All hardcoded values should be defined here to maintain a single source of truth.
"""

import re
from typing import Dict, List, Pattern


# =============================================================================
# DataFrame Column Names (Fuels Tradesheet)
# =============================================================================
COL_SELLER: str = 'Seller'
COL_BUYER: str = 'Buyer'
COL_NOTES: str = 'Notes'
COL_PRODUCT: str = 'Product'
COL_VINTAGE: str = 'Vintage'
COL_POSITION_TYPE: str = 'Position Type'
COL_PLATFORM: str = 'Platform'
COL_DATE: str = 'Date'
COL_DATE1: str = 'Date.1'
COL_VOLUME: str = 'Volume'
COL_VOLUME1: str = 'Volume.1'
COL_PRICE: str = 'Price'
COL_PRICE1: str = 'Price.1'


# =============================================================================
# Power Tradesheet Column Names
# =============================================================================
POWER_COL_DATE: str = 'Date'
POWER_COL_SELLER: str = 'Seller'
POWER_COL_VOLUME: str = 'Volume'
POWER_COL_PRICE: str = 'Price (MWh)'
POWER_COL_DATE1: str = 'Date.1'
POWER_COL_BUYER: str = 'Buyer'
POWER_COL_VOLUME1: str = 'Volume.1'
POWER_COL_PRICE1: str = 'Price (MWh).1'
POWER_COL_PRODUCT: str = 'Product'
POWER_COL_VINTAGE: str = 'Vintage'
POWER_COL_ICE_EQUIV: str = 'ICE Equiv'
POWER_COL_PLATFORM: str = 'Platform'
POWER_COL_PORTFOLIO: str = 'Portfolio'
POWER_COL_STRATEGY: str = 'Strategy'


# =============================================================================
# Magic Strings for Business Logic
# =============================================================================
ICE_COUNTERPARTY: str = "ICE U.S. OTC Commodity Markets LLC"
SHORT_POSITION_STR: str = "SHORT POSITION"
LONG_POSITION_STR: str = "LONG POSITION"
FEE_TYPE_REGULAR: str = 'regular'
FEE_TYPE_BROKER: str = 'broker'


# =============================================================================
# Desk Mappings
# =============================================================================
DESK_MAP: Dict[str, str] = {'rng desk': 'rng', 'management': 'mgmt'}
DEFAULT_DESK: str = 'fuels'
POWER_DESK: str = 'power_forward'
POWER_SHORT_TERM_DESK: str = 'power_short_term'
POWER_ANCILLARY_DESK: str = 'power_ancillary'
ANCILLARY_STRATEGY: str = 'Ancillary'


# =============================================================================
# Power Tradesheet Constants
# =============================================================================
POWER_BROKER_INDICATOR: str = 'Broker'


# =============================================================================
# Standardized Boolean String Values
# =============================================================================
EXCHANGE_TRUE: str = 'TRUE'
EXCHANGE_FALSE: str = 'FALSE'


# =============================================================================
# Date Formats
# =============================================================================
DATE_FORMATS: List[str] = ['%m/%d/%Y', '%Y-%m-%d', '%d/%m/%Y']

# Standard date format strings used throughout the application
DATE_FORMAT_STANDARD: str = '%Y-%m-%d'
DATE_FORMAT_TIMESTAMP: str = '%Y%m%d_%H%M%S'  # Timestamp format: YYYYMMDD_HHMMSS


# =============================================================================
# Final Output Columns
# =============================================================================
FINAL_OUTPUT_COLUMNS: List[str] = [
    'date', 'product', 'vintage', 'price', 'quantity', 'desk', 'portfolio', 'strategy',
    'is_exchange_settled', 'is_physically_settled', 'is_fee', 'fee_type', 'currency'
]


# =============================================================================
# Fee Processing Constants
# =============================================================================
BROKER_FEE_COLUMNS: List[str] = [
    'date', 'fee_type', 'fee_quantity', 'charge', 'fee_amount', 'desk', 'portfolio', 'strategy'
]

PRODUCT_FEE_COLUMNS: List[str] = [
    'date', 'product', 'vintage', 'fee_type', 'fee_quantity', 'charge', 'fee_amount', 'desk',
    'portfolio', 'strategy'
]


# =============================================================================
# Settlement Constants
# =============================================================================
PLATFORM_ICE: str = 'ICE'
SETTLEMENT_PHYSICAL: bool = True
SETTLEMENT_FINANCIAL: bool = False


# =============================================================================
# Product Normalization
# =============================================================================
PRODUCT_NORMALIZATIONS: Dict[str, str] = {
    'RTCs': 'RTC'
}
PRODUCT_RIN_SUFFIX: str = ' RIN'
RIN_PRODUCTS: List[str] = ['D3', 'D4', 'D6']

# Map traded products to the product codes used for pricing lookups.
# Example: trades for 'H' should use 'HNG' pricing.
PRICING_PRODUCT_ALIASES: Dict[str, str] = {
    'H': 'HNG'
}


# =============================================================================
# Fee Product Names
# =============================================================================
FEE_PRODUCT_BROKER: str = 'broker'

# Fee keywords used to identify fee trades in product names
FEE_KEYWORDS: List[str] = ['broker', 'nfa', 'clearing', 'commission']


# =============================================================================
# Currency Constants
# =============================================================================
DEFAULT_CURRENCY: str = 'usd'

CURRENCY_CONVERSION_RATES: Dict[str, float] = {
    'usd': 1.0,
    'gbp': 1.32
}


# =============================================================================
# Numeric Constants
# =============================================================================
# Epsilon value for floating point comparisons (used for zero checks)
EPSILON: float = 1e-6


# =============================================================================
# Report Styling Colors
# =============================================================================
class ReportColors:
    """Centralized color definitions for HTML reports."""

    # Primary Colors
    PRIMARY_BLUE: str = "#1e3a5f"
    PRIMARY_GREEN: str = "#2d5016"

    # Neutral Colors
    BACKGROUND: str = "#ffffff"
    BACKGROUND_ALT: str = "#fafafa"
    BACKGROUND_SUBTLE: str = "#f5f5f5"
    TEXT: str = "#1a1a1a"
    TEXT_SECONDARY: str = "#666666"
    TEXT_MUTED: str = "#999999"
    BORDER: str = "#e5e5e5"

    # Semantic Colors
    ERROR: str = "#c41e3a"
    SUCCESS: str = PRIMARY_GREEN
    WARNING: str = "#d97706"


# =============================================================================
# Regex Patterns for Vintage Parsing
# =============================================================================
VINTAGE_REGEX_Q_SPACE: Pattern[str] = re.compile(r"Q(\d)\s+(\d{4})")
VINTAGE_REGEX_Q_APOSTROPHE: Pattern[str] = re.compile(r"Q(\d)'(\d{2})")
VINTAGE_REGEX_MONTH: Pattern[str] = re.compile(r"(\w+)\s+(\d{4})")
VINTAGE_REGEX_BH: Pattern[str] = re.compile(r"BH(\d{2})")
VINTAGE_REGEX_CY: Pattern[str] = re.compile(r"CY(\d{2})")
VINTAGE_REGEX_YEAR: Pattern[str] = re.compile(r"(\d{4})")


# =============================================================================
# Regex Patterns for Product Adjustment (e.g., MCC+3, CPO-2)
# =============================================================================
ADJUSTED_PRODUCT_PATTERN: Pattern[str] = re.compile(r'^([A-Z]+)([+-])(\d+(?:\.\d+)?)$')


# =============================================================================
# Column Management Constants
# =============================================================================
REQUIRED_CORE_COLUMNS: List[str] = [
    'date', 'product', 'vintage', 'px', 'qty', 'desk', 'portfolio', 'strategy',
    'is_exchange_settled', 'is_physically_settled', 'currency'
]
OPTIONAL_COLUMNS: List[str] = ['fee_type']
REQUIRED_PRICE_COLUMNS: List[str] = ['date', 'product', 'vintage', 'px']


# =============================================================================
# Column Mappings
# =============================================================================
TRADE_COLUMN_MAPPINGS: Dict[str, str] = {
    'price': 'px',
    'quantity': 'qty'
}

PRICE_COLUMN_MAPPINGS: Dict[str, str] = {
    'price': 'px'
}


# =============================================================================
# Month Name to Number Mapping (for vintage parsing)
# =============================================================================
MONTH_NAME_TO_NUMBER: Dict[str, str] = {
    'jan': '01', 'january': '01',
    'feb': '02', 'february': '02',
    'mar': '03', 'march': '03',
    'apr': '04', 'april': '04',
    'may': '05',
    'jun': '06', 'june': '06',
    'jul': '07', 'july': '07',
    'aug': '08', 'august': '08',
    'sep': '09', 'september': '09',
    'oct': '10', 'october': '10',
    'nov': '11', 'november': '11',
    'dec': '12', 'december': '12'
}

MONTH_NAME_TO_INT: Dict[str, int] = {
    'January': 1, 'February': 2, 'March': 3, 'April': 4,
    'May': 5, 'June': 6, 'July': 7, 'August': 8,
    'September': 9, 'October': 10, 'November': 11, 'December': 12
}


# =============================================================================
# Forward Curve Column Names
# =============================================================================
FORWARD_CURVE_COL_AS_OF_DATE: str = 'AS_OF_DATE'
FORWARD_CURVE_COL_SETTLE: str = 'SETTLE'
FORWARD_CURVE_COL_EXPIRATION_YEAR: str = 'EXPIRATION_DATE_YEAR'
FORWARD_CURVE_COL_EXPIRATION_MONTH: str = 'EXPIRATION_DATE_MONTH'
FORWARD_CURVE_COL_CONTRACT: str = 'CONTRACT'

FORWARD_CURVE_REQUIRED_COLUMNS: List[str] = [
    FORWARD_CURVE_COL_AS_OF_DATE,
    FORWARD_CURVE_COL_SETTLE,
    FORWARD_CURVE_COL_EXPIRATION_YEAR,
    FORWARD_CURVE_COL_EXPIRATION_MONTH,
    FORWARD_CURVE_COL_CONTRACT
]


# =============================================================================
# ERCOT Ancillary Prices Column Names
# =============================================================================
ERCOT_ANCILLARY_COL_DATE: str = 'Date'
ERCOT_ANCILLARY_COL_TERM: str = 'Term'
ERCOT_ANCILLARY_COL_CODE: str = 'Code'
ERCOT_ANCILLARY_COL_MARK: str = 'Mark'

ERCOT_ANCILLARY_REQUIRED_COLUMNS: List[str] = [
    ERCOT_ANCILLARY_COL_DATE,
    ERCOT_ANCILLARY_COL_TERM,
    ERCOT_ANCILLARY_COL_CODE,
    ERCOT_ANCILLARY_COL_MARK,
]

# Products sourced from the ERCOT ancillary file (excluded from ERCOT forward curve)
ERCOT_ANCILLARY_PRODUCTS: List[str] = ['ENS', 'ECR', 'ECY', 'ERK', 'ERD']


# =============================================================================
# GOO Prices Required Columns
# =============================================================================
GOO_REQUIRED_COLUMNS: List[str] = ['date', 'product', 'vintage', 'mid']

# =============================================================================
# BOHO Prices Required Columns
# =============================================================================
BOHO_REQUIRED_COLUMNS: List[str] = ['date', 'product', 'vintage', 'price']


# =============================================================================
# NYISO Spot Pricing Column Names
# =============================================================================
NYISO_SPOT_COL_DATETIME: str = 'DATETIME'
NYISO_SPOT_COL_HOUR: str = 'Hour'
NYISO_SPOT_COL_ALIAS: str = 'Alias'
NYISO_SPOT_COL_PRICE: str = 'DART_Price'

NYISO_SPOT_REQUIRED_COLUMNS: List[str] = [
    NYISO_SPOT_COL_DATETIME,
    NYISO_SPOT_COL_HOUR,
    NYISO_SPOT_COL_ALIAS,
    NYISO_SPOT_COL_PRICE
]


# =============================================================================
# NYISO Bid Records Column Names
# =============================================================================
NYISO_BID_COL_DATETIME: str = 'DateTime'
NYISO_BID_COL_HOUR: str = 'Hour'
NYISO_BID_COL_ZONE: str = 'Zone'
NYISO_BID_COL_CLEAR_MW: str = 'CLEAR MW'
NYISO_BID_COL_TYPE: str = 'Type'

NYISO_BID_REQUIRED_COLUMNS: List[str] = [
    NYISO_BID_COL_DATETIME,
    NYISO_BID_COL_HOUR,
    NYISO_BID_COL_ZONE,
    NYISO_BID_COL_CLEAR_MW
]


# =============================================================================
# PJM Spot Pricing Column Names
# =============================================================================
PJM_SPOT_COL_DATETIME: str = 'DATETIME'
PJM_SPOT_COL_HOUR: str = 'Hour'
PJM_SPOT_COL_ALIAS: str = 'Alias'
PJM_SPOT_COL_PRICE: str = 'DART_Price'

PJM_SPOT_REQUIRED_COLUMNS: List[str] = [
    PJM_SPOT_COL_DATETIME,
    PJM_SPOT_COL_HOUR,
    PJM_SPOT_COL_ALIAS,
    PJM_SPOT_COL_PRICE
]


# =============================================================================
# PJM Bid Records Column Names
# =============================================================================
PJM_BID_COL_DATETIME: str = 'DateTime'
PJM_BID_COL_HOUR: str = 'Hour'
PJM_BID_COL_ZONE: str = 'Zone'
PJM_BID_COL_CLEAR_MW: str = 'CLEAR MW'
PJM_BID_COL_TYPE: str = 'Type'

PJM_BID_REQUIRED_COLUMNS: List[str] = [
    PJM_BID_COL_DATETIME,
    PJM_BID_COL_HOUR,
    PJM_BID_COL_ZONE,
    PJM_BID_COL_CLEAR_MW
]

# =============================================================================
# PJM Zone to Alias Mapping
# =============================================================================
PJM_ZONE_TO_ALIAS: Dict[str, str] = {
    'DOMINION': 'DOM',
    'DPL_RES_AGG': 'DPL',
    'AECO_RES_AGG': 'AECO',
    'N_ILLINOIS': 'NI',
    'WESTERN': 'West',
    'AEP_DAYTON': 'AD',
    'PSEG_RES_AGG': 'PSEG',
    'IMO': 'IMO',
    'NYIS': 'NYISO',
    'HUDSONTP': 'HTP',
    'EASTERN': 'East'
}


# =============================================================================
# MISO Spot Pricing Column Names
# =============================================================================
MISO_SPOT_COL_DATETIME: str = 'DATETIME'
MISO_SPOT_COL_HOUR: str = 'Hour'
MISO_SPOT_COL_ALIAS: str = 'Alias'
MISO_SPOT_COL_PRICE: str = 'DART_Price'

MISO_SPOT_REQUIRED_COLUMNS: List[str] = [
    MISO_SPOT_COL_DATETIME,
    MISO_SPOT_COL_HOUR,
    MISO_SPOT_COL_ALIAS,
    MISO_SPOT_COL_PRICE
]


# =============================================================================
# MISO Bid Records Column Names
# =============================================================================
MISO_BID_COL_DATETIME: str = 'DateTime'
MISO_BID_COL_HOUR: str = 'Hour'
MISO_BID_COL_ZONE: str = 'Zone'
MISO_BID_COL_CLEAR_MW: str = 'CLEAR MW'
MISO_BID_COL_TYPE: str = 'Type'

MISO_BID_REQUIRED_COLUMNS: List[str] = [
    MISO_BID_COL_DATETIME,
    MISO_BID_COL_HOUR,
    MISO_BID_COL_ZONE,
    MISO_BID_COL_CLEAR_MW
]


# =============================================================================
# MISO Zone to Alias Mapping
# =============================================================================
MISO_ZONE_TO_ALIAS: Dict[str, str] = {
    'ARKANSAS_HUB': 'Ark Hub',
    'ILLINOIS_HUB': 'Illinois Hub',
    'INDIANA_HUB': 'Indy Hub',
    'LOUISIANA_HUB': 'LA Hub',
    'MISSISSIPPI_HUB': 'Miss Hub',
    'MICHIGAN_HUB': 'Mich Hub',
    'MINNESOTA_HUB': 'Minn Hub',
    'TEXAS_HUB': 'Texas Hub'
}


# =============================================================================
# CAISO Spot Pricing Column Names
# =============================================================================
CAISO_SPOT_COL_DATETIME: str = 'DATETIME'
CAISO_SPOT_COL_HOUR: str = 'Hour'
CAISO_SPOT_COL_ALIAS: str = 'Alias'
CAISO_SPOT_COL_PRICE: str = 'DART_Price'

CAISO_SPOT_REQUIRED_COLUMNS: List[str] = [
    CAISO_SPOT_COL_DATETIME,
    CAISO_SPOT_COL_HOUR,
    CAISO_SPOT_COL_ALIAS,
    CAISO_SPOT_COL_PRICE
]


# =============================================================================
# CAISO Bid Records Column Names
# =============================================================================
CAISO_BID_COL_DATETIME: str = 'DATETIME'
CAISO_BID_COL_HOUR: str = 'HOURENDING'
CAISO_BID_COL_ZONE: str = 'hub'
CAISO_BID_COL_CLEAR_MW: str = 'CLEAR_MW'

CAISO_BID_REQUIRED_COLUMNS: List[str] = [
    CAISO_BID_COL_DATETIME,
    CAISO_BID_COL_HOUR,
    CAISO_BID_COL_ZONE,
    CAISO_BID_COL_CLEAR_MW
]


# =============================================================================
# ISO Configuration for Power Short-Term Trading
# =============================================================================
# Centralized configuration for each ISO to support generic processing
# Each ISO config contains: column mappings, hour shift setting, zone mapping,
# desk assignment, and display name
ISO_CONFIG: Dict[str, Dict] = {
    'nyiso': {
        'name': 'NYISO',
        'desk': POWER_SHORT_TERM_DESK,
        'portfolio': 'NYISO',
        'hour_shift': True,  # NYISO requires 1-hour shift for hour-ending convention
        'zone_mapping': None,  # NYISO uses zones directly as product names
        'bid_cols': {
            'datetime': NYISO_BID_COL_DATETIME,
            'hour': NYISO_BID_COL_HOUR,
            'zone': NYISO_BID_COL_ZONE,
            'clear_mw': NYISO_BID_COL_CLEAR_MW
        },
        'spot_cols': {
            'datetime': NYISO_SPOT_COL_DATETIME,
            'hour': NYISO_SPOT_COL_HOUR,
            'alias': NYISO_SPOT_COL_ALIAS,
            'price': NYISO_SPOT_COL_PRICE
        },
        'required_bid_cols': NYISO_BID_REQUIRED_COLUMNS,
        'required_spot_cols': NYISO_SPOT_REQUIRED_COLUMNS
    },
    'pjm': {
        'name': 'PJM',
        'desk': POWER_SHORT_TERM_DESK,
        'portfolio': 'PJM',
        'hour_shift': False,  # PJM does not require hour shift
        'zone_mapping': PJM_ZONE_TO_ALIAS,
        'bid_cols': {
            'datetime': PJM_BID_COL_DATETIME,
            'hour': PJM_BID_COL_HOUR,
            'zone': PJM_BID_COL_ZONE,
            'clear_mw': PJM_BID_COL_CLEAR_MW
        },
        'spot_cols': {
            'datetime': PJM_SPOT_COL_DATETIME,
            'hour': PJM_SPOT_COL_HOUR,
            'alias': PJM_SPOT_COL_ALIAS,
            'price': PJM_SPOT_COL_PRICE
        },
        'required_bid_cols': PJM_BID_REQUIRED_COLUMNS,
        'required_spot_cols': PJM_SPOT_REQUIRED_COLUMNS
    },
    'miso': {
        'name': 'MISO',
        'desk': POWER_SHORT_TERM_DESK,
        'portfolio': 'MISO',
        'hour_shift': False,  # MISO does not require hour shift
        'zone_mapping': MISO_ZONE_TO_ALIAS,
        'bid_cols': {
            'datetime': MISO_BID_COL_DATETIME,
            'hour': MISO_BID_COL_HOUR,
            'zone': MISO_BID_COL_ZONE,
            'clear_mw': MISO_BID_COL_CLEAR_MW
        },
        'spot_cols': {
            'datetime': MISO_SPOT_COL_DATETIME,
            'hour': MISO_SPOT_COL_HOUR,
            'alias': MISO_SPOT_COL_ALIAS,
            'price': MISO_SPOT_COL_PRICE
        },
        'required_bid_cols': MISO_BID_REQUIRED_COLUMNS,
        'required_spot_cols': MISO_SPOT_REQUIRED_COLUMNS
    },
    'caiso': {
        'name': 'CAISO',
        'desk': POWER_SHORT_TERM_DESK,
        'portfolio': 'CAISO',
        'hour_shift': False,  # CAISO does not require hour shift
        'hour_is_one_based': True,  # HOURENDING column is already 1-24, no +1 needed
        'zone_mapping': None,  # CAISO uses zones directly as product names
        'bid_cols': {
            'datetime': CAISO_BID_COL_DATETIME,
            'hour': CAISO_BID_COL_HOUR,
            'zone': CAISO_BID_COL_ZONE,
            'clear_mw': CAISO_BID_COL_CLEAR_MW
        },
        'spot_cols': {
            'datetime': CAISO_SPOT_COL_DATETIME,
            'hour': CAISO_SPOT_COL_HOUR,
            'alias': CAISO_SPOT_COL_ALIAS,
            'price': CAISO_SPOT_COL_PRICE
        },
        'required_bid_cols': CAISO_BID_REQUIRED_COLUMNS,
        'required_spot_cols': CAISO_SPOT_REQUIRED_COLUMNS
    }
}


# =============================================================================
# Additional Trades Required Columns
# =============================================================================
ADDITIONAL_TRADES_REQUIRED_COLUMNS: Dict[str, object] = {
    'date': None,
    'product': '',
    'vintage': '',
    'price': 0.0,
    'quantity': 0.0,
    'desk': 'unknown',
    'portfolio': '',
    'strategy': '',
    'is_exchange_settled': EXCHANGE_FALSE,
    'is_physically_settled': True,
    'is_fee': False,
    'fee_type': FEE_TYPE_REGULAR,
    'currency': DEFAULT_CURRENCY
}

# =============================================================================
# Reporting Defaults
# =============================================================================
DEFAULT_REPORT_RECIPIENTS: List[str] = ['Peter Vouthas <peter.vouthas@karbone.com>']


# =============================================================================
# Desk Display Name Mappings
# =============================================================================
DESK_DISPLAY_NAMES: Dict[str, str] = {
    'europe': 'Europe',
    'mgmt': 'Management',
    'power_forward': 'Power Forward',
    'power_short_term': 'Power Short Term',
    'power_ancillary': 'Power Ancillary',
    'fuels': 'Fuels'
}
