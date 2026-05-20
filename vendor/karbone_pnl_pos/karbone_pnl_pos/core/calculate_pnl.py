#!/usr/bin/env python3
"""
Daily PnL Calculator.

This module computes daily Profit and Loss (PnL) with detailed inventory tracking,
mark-to-market valuation, and trade decomposition. It implements the core financial
logic for the RINs trading system.

Key Features:
- Multi-currency support with USD conversion
- Vintage price modeling via linear extrapolation
- Forward-fill for missing market prices
- Trade price fallback for mark-to-market
- Adjusted product price generation (e.g., MCC+3, CPO-2)

Assumptions:
- Buy quantities are positive
- Sell quantities are negative

Usage:
    >>> from calculate_pnl import DailyPnLCalculator
    >>> calculator = DailyPnLCalculator()
    >>> calculator.load_data(trades_df, prices_df)
    >>> pnl_df = calculator.compute_daily_pnl()
    >>> rounded_df = calculator.get_rounded_results(pnl_df)
"""

import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd

from karbone_pnl_pos.utils import constants
from karbone_pnl_pos.utils.config_loader import get_config, get_pnl_defaults
from karbone_pnl_pos.utils.vintage_utils import parse_vintage_for_ordering

_RE_VINTAGE_YM = re.compile(r'^(\d{4})[-/](\d{1,2})$')
_RE_VINTAGE_MY = re.compile(r'^([a-zA-Z]+)[-\s](\d{2,4})$', re.IGNORECASE)

# Module logger
logger = logging.getLogger('pnl.' + __name__)


@dataclass
class GroupPnLState:
    """
    Explicit state container for PnL calculation within a trade group.

    This dataclass replaces hidden instance variables that were previously
    tracked on the calculator instance. Making state explicit improves
    testability and clarity.

    Attributes:
        qty_start: Starting position quantity for the current day.
        prior_mark_source: Source of the previous day's mark price.
        prior_px: Previous day's mark price.
    """

    qty_start: float = 0.0
    prior_mark_source: Optional[str] = None
    prior_px: Optional[float] = None


class DailyPnLCalculator:
    """
    Computes daily PnL with inventory tracking and mark-to-market valuation.

    This class encapsulates all logic for loading trade and price data,
    preprocessing it into a standardized format, and running a daily PnL
    calculation algorithm. It supports various configuration options for
    handling missing data and modeling prices.

    Attributes:
        forward_fill_allowed: Whether to forward-fill missing prices.
        missing_pnl_policy: Policy for handling missing PnL ('null' or 'zero').
        enable_vintage_modeling: Whether to model missing vintage prices.
        use_trade_price_fallback: Whether to use trade prices as fallback marks.
        trades_df: Preprocessed trades DataFrame.
        prices_df: Preprocessed prices DataFrame.
        px_map: Dictionary mapping (date, product, vintage) to prices.
        calendar: Sorted list of all dates with activity (trades or prices).
        modeled_prices: Dictionary tracking modeled price details.
        trade_price_fallbacks: Dictionary tracking trade price fallback usage.
        trade_price_cache: Cache for trade price lookups.
        forward_curve_vintages: Set of vintages from forward curves.
        adjusted_prices: Dictionary tracking adjusted product prices.
    """

    def __init__(
        self,
        forward_fill_allowed: Optional[bool] = None,
        missing_pnl_policy: Optional[str] = None,
        enable_vintage_modeling: Optional[bool] = None,
        use_trade_price_fallback: Optional[bool] = None,
        desk_overrides: Optional[Dict[str, Dict[str, Any]]] = None,
        include_weighted_avg_prices: Optional[bool] = None,
    ) -> None:
        """
        Initialize the PnL calculator with specified or default configurations.

        Args:
            forward_fill_allowed: If True, forward-fills missing prices within
                a product-vintage series. Falls back to config if None.
            missing_pnl_policy: Policy ('null' or 'zero') for handling PnL on
                days with missing market prices. Falls back to config if None.
            enable_vintage_modeling: If True, models missing vintage prices
                using linear extrapolation. Falls back to config if None.
            use_trade_price_fallback: If True, uses the most recent trade price
                as a fallback mark when no market price is available.
                Falls back to config if None.
            desk_overrides: Optional dictionary mapping desk names to their
                specific PnL algorithm settings. Overrides default settings
                for those desks.
        """
        config_defaults = get_pnl_defaults()

        self.forward_fill_allowed: bool = (
            forward_fill_allowed
            if forward_fill_allowed is not None
            else config_defaults.get('forward_fill_allowed', True)
        )
        self.missing_pnl_policy: str = (
            missing_pnl_policy
            if missing_pnl_policy is not None
            else config_defaults.get('missing_pnl_policy', 'null')
        )
        self.enable_vintage_modeling: bool = (
            enable_vintage_modeling
            if enable_vintage_modeling is not None
            else config_defaults.get('enable_vintage_modeling', True)
        )
        self.use_trade_price_fallback: bool = (
            use_trade_price_fallback
            if use_trade_price_fallback is not None
            else config_defaults.get('use_trade_price_fallback', True)
        )
        
        # Store desk-specific overrides
        self.desk_overrides: Dict[str, Dict[str, Any]] = desk_overrides or {}

        self.include_weighted_avg_prices: bool = (
            include_weighted_avg_prices
            if include_weighted_avg_prices is not None
            else config_defaults.get('include_weighted_avg_prices', False)
        )

        # Load vintage month exclusion configuration
        exclude_config = config_defaults.get('exclude_vintage_month_pnl', {})
        self.exclude_vintage_month_enabled: bool = exclude_config.get('enabled', False)
        self.exclude_vintage_month_products = exclude_config.get('products', 'all')
        
        if self.exclude_vintage_month_enabled:
            if self.exclude_vintage_month_products == 'all':
                logger.info("Vintage month PnL exclusion enabled for all products")
            else:
                logger.info(
                    f"Vintage month PnL exclusion enabled for products: "
                    f"{self.exclude_vintage_month_products}"
                )

        self.trades_df: Optional[pd.DataFrame] = None
        self.prices_df: Optional[pd.DataFrame] = None
        self.px_map: Optional[Dict[Tuple[date, str, str], float]] = None
        self._prices_norm_df: Optional[pd.DataFrame] = None
        self.calendar: Optional[List[date]] = None
        self.modeled_prices: Dict[Tuple[date, str, str], Dict[str, Any]] = {}
        self.trade_price_fallbacks: Dict[Tuple, Dict[str, Any]] = {}
        self.trade_price_cache: Dict[Tuple, Optional[float]] = {}
        self.forward_curve_vintages: Set[Tuple[str, str]] = set()
        self.adjusted_prices: Dict[Tuple[date, str, str], Dict[str, Any]] = {}
    
    def _get_desk_setting(self, desk: str, setting_name: str) -> Any:
        """
        Get a PnL algorithm setting for a specific desk.
        
        Returns desk-specific override if available, otherwise returns default.
        
        Args:
            desk: Trading desk name.
            setting_name: Name of the setting (e.g., 'forward_fill_allowed').
            
        Returns:
            Setting value for the desk.
        """
        if desk in self.desk_overrides and setting_name in self.desk_overrides[desk]:
            return self.desk_overrides[desk][setting_name]
        
        # Return default instance setting
        return getattr(self, setting_name)
    
    def _should_suppress_pnl(self, trade_date: date, product: str, vintage: str) -> bool:
        """
        Check if PnL should be suppressed based on date-vintage year-month matching.
        
        When enabled, suppresses PnL when the trade date is in the vintage month
        or any later month (past months as well as the current month).
        
        Args:
            trade_date: The date of the PnL calculation.
            product: Product name.
            vintage: Vintage string.
            
        Returns:
            True if PnL should be suppressed, False otherwise.
        """
        if not self.exclude_vintage_month_enabled:
            return False
        
        # Check if this product should be excluded
        if self.exclude_vintage_month_products != 'all':
            if isinstance(self.exclude_vintage_month_products, list):
                if product not in self.exclude_vintage_month_products:
                    return False
            else:
                # If it's not 'all' and not a list, don't suppress
                return False
        
        # Extract year-month from trade date
        date_year_month = (trade_date.year, trade_date.month)
        
        # Extract year-month from vintage
        vintage_year_month = self._extract_year_month_from_vintage(vintage)
        
        if vintage_year_month is None:
            # Cannot determine vintage year-month, don't suppress
            return False
        
        # Suppress if date year-month is >= vintage year-month
        # (applies to vintage month and all subsequent months - past and current)
        return date_year_month >= vintage_year_month
    
    def _extract_year_month_from_vintage(self, vintage: str) -> Optional[Tuple[int, int]]:
        """
        Extract year and month from a vintage string.
        
        Handles various vintage formats:
        - Quarter formats: "Q1'25", "Q1 2025" -> (2025, 1) for Q1
        - Year-month formats: "2026-01" -> (2026, 1)
        - Month-year formats: "Jan-26", "January 2026" -> (2026, 1)
        
        For quarterly vintages, returns the first month of the quarter.
        
        Args:
            vintage: Vintage string.
            
        Returns:
            Tuple of (year, month) or None if cannot parse.
        """
        if not vintage or pd.isna(vintage):
            return None

        vintage_str = str(vintage).strip()

        q_match = constants.VINTAGE_REGEX_Q_APOSTROPHE.match(vintage_str)
        if not q_match:
            q_match = constants.VINTAGE_REGEX_Q_SPACE.match(vintage_str)

        if q_match:
            quarter = int(q_match.group(1))
            year = int(q_match.group(2))
            if year < 100:
                year += 2000
            quarter_to_month = {1: 1, 2: 4, 3: 7, 4: 10}
            month = quarter_to_month.get(quarter)
            if month:
                return (year, month)

        ym_match = _RE_VINTAGE_YM.match(vintage_str)
        if ym_match:
            year = int(ym_match.group(1))
            month = int(ym_match.group(2))
            if 1 <= month <= 12:
                return (year, month)

        my_match = _RE_VINTAGE_MY.match(vintage_str)
        if my_match:
            year = int(my_match.group(2))
            if year < 100:
                year += 2000
            month = constants.MONTH_NAME_TO_INT.get(my_match.group(1).title())
            if month:
                return (year, month)

        return None

    def load_data(
        self,
        trades_df: pd.DataFrame,
        prices_df: pd.DataFrame,
        forward_curve_vintages: Optional[Set[Tuple[str, str]]] = None,
        verbose: bool = True
    ) -> None:
        """
        Load, preprocess, and prepare trade and price data for PnL calculation.

        Args:
            trades_df: Raw trades DataFrame.
            prices_df: Raw prices DataFrame.
            forward_curve_vintages: Set of (product, vintage) tuples to exempt
                from vintage modeling.
            verbose: If True, prints progress messages.
        """
        logger.info("Loading and preprocessing data...")

        self.trades_df = trades_df.copy()
        self.prices_df = prices_df.copy()
        self.forward_curve_vintages = forward_curve_vintages or set()

        logger.debug("  Step 1/5: Preprocessing trades...")
        self._preprocess_trades()

        logger.debug("  Step 2/5: Preprocessing prices...")
        self._preprocess_prices()

        logger.debug("  Step 3/5: Generating adjusted product prices...")
        self._generate_adjusted_product_prices()

        logger.debug("  Step 4/5: Building price lookup tables...")
        self._build_price_lookup()

        logger.debug("  Step 5/5: Building calendar and modeling vintages...")
        self._build_calendar()

        logger.info(f"Data loading complete: {len(self.trades_df)} trades and {len(self.prices_df)} price points")
        if self.adjusted_prices:
            logger.info(f"  Generated {len(self.adjusted_prices)} adjusted product prices")
        if self.modeled_prices:
            logger.info(f"  Modeled {len(self.modeled_prices)} vintage prices using linear extrapolation")

    def _preprocess_trades(self) -> None:
        """
        Clean, normalize, and prepare the trades DataFrame for calculation.

        Steps:
        1. Rename columns to standardized schema (e.g., 'price' -> 'px').
        2. Ensure all required columns are present.
        3. Drop rows with null values in essential columns.
        4. Convert date strings to datetime objects.
        5. Sort for chronological processing.
        """
        self.trades_df = self.trades_df.rename(columns=constants.TRADE_COLUMN_MAPPINGS)

        # Initialize portfolio/strategy with empty string if not present
        for col in ('portfolio', 'strategy'):
            if col not in self.trades_df.columns:
                self.trades_df[col] = ''

        required_cols = constants.REQUIRED_CORE_COLUMNS.copy()

        for optional_col in constants.OPTIONAL_COLUMNS:
            if optional_col in self.trades_df.columns:
                self.trades_df[optional_col] = self.trades_df[optional_col].fillna(
                    constants.FEE_TYPE_REGULAR
                )
                required_cols.append(optional_col)
            else:
                self.trades_df[optional_col] = constants.FEE_TYPE_REGULAR
                required_cols.append(optional_col)

        self.trades_df = self.trades_df[required_cols].copy()
        self.trades_df = self.trades_df.dropna(subset=constants.REQUIRED_CORE_COLUMNS)
        self.trades_df['date'] = pd.to_datetime(self.trades_df['date']).dt.date

        self.trades_df = self.trades_df.sort_values([
            'date', 'desk', 'portfolio', 'strategy', 'product', 'vintage',
            'is_exchange_settled', 'is_physically_settled', 'fee_type'
        ])

        logger.debug(f"Preprocessed trades: {len(self.trades_df)} rows")

    def _preprocess_prices(self) -> None:
        """
        Clean, normalize, and prepare the prices DataFrame for lookups.

        Filtering to traded products now happens upstream in load_prices.py before
        any data enters memory.  This method only handles column normalization,
        date conversion, deduplication, and alias materialization.
        """
        self.prices_df = self.prices_df.rename(columns=constants.PRICE_COLUMN_MAPPINGS)
        self.prices_df = self.prices_df[constants.REQUIRED_PRICE_COLUMNS].copy()
        self.prices_df = self.prices_df.dropna(subset=constants.REQUIRED_PRICE_COLUMNS)

        self.prices_df['date'] = pd.to_datetime(self.prices_df['date']).dt.date
        self.prices_df = self.prices_df.sort_values(['date', 'product', 'vintage'])
        self.prices_df = self.prices_df.drop_duplicates(
            subset=['date', 'product', 'vintage'],
            keep='last'
        )

        alias_rows_added = self._apply_pricing_product_aliases()
        if alias_rows_added > 0:
            self.prices_df = self.prices_df.sort_values(['date', 'product', 'vintage'])
            self.prices_df = self.prices_df.drop_duplicates(
                subset=['date', 'product', 'vintage'],
                keep='last'
            )
            logger.debug(f"  Added {alias_rows_added} price records via product aliases")

        self.prices_df['product'] = self.prices_df['product'].astype('category')

        logger.debug(f"Preprocessed prices: {len(self.prices_df)} rows")

    def _apply_pricing_product_aliases(self) -> int:
        """
        Duplicate price rows for alias products (e.g., 'H' uses 'HNG' pricing).

        Returns:
            Number of alias price rows added.
        """
        if not constants.PRICING_PRODUCT_ALIASES:
            return 0

        alias_rows: List[Dict[str, Any]] = []
        existing_keys = set(
            zip(self.prices_df['date'], self.prices_df['product'], self.prices_df['vintage'])
        )

        for alias_product, source_product in constants.PRICING_PRODUCT_ALIASES.items():
            source_rows = self.prices_df[self.prices_df['product'] == source_product]
            if source_rows.empty:
                continue

            candidate = source_rows[['date', 'vintage', 'px']].copy()
            candidate['product'] = alias_product
            keys = list(zip(candidate['date'], candidate['product'], candidate['vintage']))
            mask = [k not in existing_keys for k in keys]
            new_rows = candidate[mask]
            if not new_rows.empty:
                alias_rows.extend(new_rows[['date', 'product', 'vintage', 'px']].to_dict('records'))
                existing_keys.update(k for k, keep in zip(keys, mask) if keep)

        if alias_rows:
            alias_df = pd.DataFrame(alias_rows)
            self.prices_df = pd.concat([self.prices_df, alias_df], ignore_index=True)

        return len(alias_rows)

    def _generate_adjusted_product_prices(self) -> None:
        """
        Generate prices for products with adjustment patterns like ABC+# or ABC-#.

        For products structured as BASE+ADJUSTMENT (e.g., MCC+3, CPO-2):
        1. Identify all unique adjusted products in trades and prices.
        2. Parse the base product and adjustment amount.
        3. Look up prices for the base product.
        4. Create synthetic price records with the adjustment applied.

        Examples:
            - MCC+3: looks up MCC prices and creates MCC+3 prices = MCC + 3.0
            - CPO-2: looks up CPO prices and creates CPO-2 prices = CPO - 2.0
        """
        # Find adjusted products in trades
        adjusted_products_in_trades: Set[str] = set()
        if self.trades_df is not None and 'product' in self.trades_df.columns:
            for product in self.trades_df['product'].unique():
                if pd.notna(product) and constants.ADJUSTED_PRODUCT_PATTERN.match(str(product)):
                    adjusted_products_in_trades.add(str(product))

        # Find adjusted products already in prices
        adjusted_products_in_prices: Set[str] = set()
        if 'product' in self.prices_df.columns:
            for product in self.prices_df['product'].unique():
                if pd.notna(product) and constants.ADJUSTED_PRODUCT_PATTERN.match(str(product)):
                    adjusted_products_in_prices.add(str(product))

        # Only generate for products in trades but not in prices
        adjusted_products_to_generate = adjusted_products_in_trades - adjusted_products_in_prices

        if not adjusted_products_to_generate:
            return

        logger.debug(f"Generating prices for {len(adjusted_products_to_generate)} adjusted products...")

        # Group prices by base product
        base_prices: Dict[str, pd.DataFrame] = {}
        for product in self.prices_df['product'].unique():
            if pd.notna(product):
                base_prices[str(product)] = self.prices_df[
                    self.prices_df['product'] == product
                ].copy()

        new_price_rows: List[Dict[str, Any]] = []

        for adjusted_product in adjusted_products_to_generate:
            match = constants.ADJUSTED_PRODUCT_PATTERN.match(adjusted_product)
            if not match:
                continue

            base_product = match.group(1)
            operator = match.group(2)
            adjustment = float(match.group(3))

            if operator == '-':
                adjustment = -adjustment

            if base_product not in base_prices:
                logger.warning(
                    f"No prices found for base product '{base_product}' "
                    f"(needed for '{adjusted_product}')"
                )
                continue

            base_df = base_prices[base_product]

            for _, row in base_df.iterrows():
                new_price_rows.append({
                    'date': row['date'],
                    'product': adjusted_product,
                    'vintage': row['vintage'],
                    'px': row['px'] + adjustment
                })

                key = (row['date'], adjusted_product, row['vintage'])
                self.adjusted_prices[key] = {
                    'base_product': base_product,
                    'base_price': row['px'],
                    'adjustment': adjustment,
                    'final_price': row['px'] + adjustment
                }

        if new_price_rows:
            new_prices_df = pd.DataFrame(new_price_rows)
            original_count = len(self.prices_df)
            self.prices_df = pd.concat([self.prices_df, new_prices_df], ignore_index=True)
            self.prices_df = self.prices_df.drop_duplicates(
                subset=['date', 'product', 'vintage'],
                keep='last'
            )

            added_count = len(self.prices_df) - original_count
            logger.debug(f"  Generated {added_count} adjusted price records")

            if added_count > 0:
                sample = ', '.join(list(adjusted_products_to_generate)[:5])
                logger.debug(f"  Sample adjusted products: {sample}")

    def _build_price_lookup(self) -> None:
        """
        Create a high-performance price lookup map from the prices DataFrame.

        The map uses (date, product, vintage) tuple as key for O(1) retrieval,
        which is critical for the main PnL calculation loop.

        Also builds self._prices_norm_df â€” a normalized DataFrame used by the
        vectorized PnL path for merge-based price resolution.
        """
        prices_with_norm_vintage = self.prices_df.copy()
        prices_with_norm_vintage['vintage'] = prices_with_norm_vintage['vintage'].apply(
            self._normalize_vintage
        )
        self.px_map = prices_with_norm_vintage.set_index(
            ['date', 'product', 'vintage']
        )['px'].to_dict()

        # Build normalized prices DataFrame for the vectorized path
        prices_norm = prices_with_norm_vintage.rename(columns={'vintage': '_norm_vintage'})
        prices_norm = prices_norm.drop_duplicates(
            subset=['date', 'product', '_norm_vintage'], keep='last'
        )
        prices_norm['_is_modeled'] = False
        self._prices_norm_df: pd.DataFrame = prices_norm[
            ['date', 'product', '_norm_vintage', 'px', '_is_modeled']
        ].copy()

    def _build_calendar(self) -> None:
        """
        Construct a complete calendar from trade and price dates.

        The calendar ensures PnL is calculated for every day with market activity,
        even on days without trades, for accurate mark-to-market PnL.
        
        Includes ALL dates with activity (trades or prices), regardless of whether
        they are traditional trading days. This allows PnL calculation for markets
        that trade on weekends and holidays (e.g., power markets).
        
        Note: Email scheduling still uses trading day logic via run_pnl_scheduler.py.
        """
        trade_dates = set(self.trades_df['date'])
        price_dates = set(self.prices_df['date'])
        all_dates = sorted(trade_dates.union(price_dates))
        
        # Include ALL dates with activity (don't filter to trading days)
        # This allows PnL calculation for power markets that trade on weekends/holidays
        self.calendar = all_dates

        logger.debug(
            f"Calendar: {len(self.calendar)} days "
            f"from {self.calendar[0]} to {self.calendar[-1]}"
        )

        if self.enable_vintage_modeling:
            self._model_missing_vintage_prices()

        # Append modeled prices to the normalized DataFrame for the vectorized path
        if self.modeled_prices:
            modeled_rows = [
                {'date': d, 'product': p, '_norm_vintage': v,
                 'px': info['price'], '_is_modeled': True}
                for (d, p, v), info in self.modeled_prices.items()
            ]
            modeled_df = pd.DataFrame(modeled_rows)
            self._prices_norm_df = pd.concat(
                [self._prices_norm_df, modeled_df], ignore_index=True
            )
            # Modeled prices should not override real prices â€” keep first occurrence
            self._prices_norm_df = self._prices_norm_df.drop_duplicates(
                subset=['date', 'product', '_norm_vintage'], keep='first'
            )

    def _normalize_vintage(self, vintage: str) -> str:
        """
        Normalize various vintage string formats to canonical QX'YY representation.

        Args:
            vintage: Raw vintage string.

        Returns:
            Normalized vintage string.

        Example:
            'Q1 2025' -> "Q1'25"
        """
        if not vintage or pd.isna(vintage):
            return str(vintage) if not pd.isna(vintage) else ''

        vintage_str = str(vintage).strip()

        # Convert space format to apostrophe format
        q_space_match = constants.VINTAGE_REGEX_Q_SPACE.match(vintage_str)
        if q_space_match:
            quarter = q_space_match.group(1)
            year = int(q_space_match.group(2))
            year_2digit = year % 100
            return f"Q{quarter}'{year_2digit:02d}"

        # Apostrophe format already normalized
        q_apostrophe_match = constants.VINTAGE_REGEX_Q_APOSTROPHE.match(vintage_str)
        if q_apostrophe_match:
            return vintage_str

        return vintage_str

    def _model_missing_vintage_prices(self) -> None:
        """
        Model missing vintage prices using linear extrapolation.

        Pre-calculates vintage universes per product for performance,
        then iterates through the calendar to model prices for any
        traded vintage lacking a market price.
        """
        if not self.enable_vintage_modeling:
            return

        logger.debug("Modeling missing vintage prices...")
        modeled_count = 0

        # Get all traded vintages
        traded_vintages: Set[Tuple[str, str]] = set()
        for _, row in self.trades_df.iterrows():
            normalized_vintage = self._normalize_vintage(row['vintage'])
            traded_vintages.add((row['product'], normalized_vintage))

        # Pre-calculate vintage universe per product
        product_to_vintages_map: Dict[str, Set[str]] = {}
        for (d, p, v) in self.px_map.keys():
            if p not in product_to_vintages_map:
                product_to_vintages_map[p] = set()
            product_to_vintages_map[p].add(v)

        for prod, vintage in traded_vintages:
            if prod not in product_to_vintages_map:
                product_to_vintages_map[prod] = set()
            product_to_vintages_map[prod].add(vintage)

        # Pre-sort vintages per product
        product_to_sorted_vintages_map: Dict[str, List[str]] = {}
        for product, vintages in product_to_vintages_map.items():
            product_to_sorted_vintages_map[product] = sorted(
                vintages,
                key=parse_vintage_for_ordering
            )

        # Group prices by date and product
        prices_by_date_product: Dict[date, Dict[str, Dict[str, float]]] = {}
        for (d, p, v), price in self.px_map.items():
            if d not in prices_by_date_product:
                prices_by_date_product[d] = {}
            if p not in prices_by_date_product[d]:
                prices_by_date_product[d][p] = {}
            prices_by_date_product[d][p][v] = price

        for date_key in self.calendar:
            if date_key not in prices_by_date_product:
                continue

            products_for_date = prices_by_date_product[date_key]

            for product, vintage_prices in products_for_date.items():
                all_sorted_vintages = product_to_sorted_vintages_map.get(product, [])

                for i, vintage in enumerate(all_sorted_vintages):
                    if vintage not in vintage_prices:
                        # Skip forward curve vintages
                        if (product, vintage) in self.forward_curve_vintages:
                            continue

                        is_traded = (product, vintage) in traded_vintages

                        modeled_price = self._interpolate_vintage_price(
                            vintage, all_sorted_vintages, vintage_prices, i
                        )

                        if modeled_price is not None:
                            key = (date_key, product, vintage)
                            self.px_map[key] = modeled_price
                            self.modeled_prices[key] = {
                                'price': modeled_price,
                                'method': 'linear_extrapolation',
                                'is_traded': is_traded
                            }
                            modeled_count += 1

        if modeled_count > 0:
            logger.debug(
                f"Modeled {modeled_count} missing vintage prices "
                "using linear extrapolation"
            )

    def _interpolate_vintage_price(
        self,
        target_vintage: str,
        all_vintages: List[str],
        available_prices: Dict[str, float],
        target_index: int
    ) -> Optional[float]:
        """
        Interpolate a missing vintage price using linear extrapolation.

        Formula: P_target = 2 * P_next - P_prior

        Args:
            target_vintage: The vintage needing a price.
            all_vintages: List of all vintages for the product.
            available_prices: Dict of vintage -> price for available prices.
            target_index: Index of target in all_vintages.

        Returns:
            Interpolated price, or None if insufficient data.
        """
        try:
            prior_vintage_1: Optional[str] = None
            prior_vintage_2: Optional[str] = None

            prior_count = 0
            for i in range(target_index - 1, -1, -1):
                if all_vintages[i] in available_prices:
                    if prior_count == 0:
                        prior_vintage_1 = all_vintages[i]
                        prior_count += 1
                    elif prior_count == 1:
                        prior_vintage_2 = all_vintages[i]
                        break

            if prior_vintage_1 and prior_vintage_2:
                more_recent_prior_price = available_prices[prior_vintage_1]
                less_recent_prior_price = available_prices[prior_vintage_2]

                interpolated_price = 2 * more_recent_prior_price - less_recent_prior_price

                if interpolated_price > 0:
                    return interpolated_price

            return None

        except (IndexError, KeyError, TypeError):
            return None

    def get_modeled_price_summary(self) -> Dict[str, Any]:
        """
        Return a detailed summary of all modeled prices for auditing.

        Returns:
            Dictionary with total count, breakdowns by product/vintage, and details.
        """
        if not self.modeled_prices:
            return {}

        summary: Dict[str, Any] = {
            'total_modeled': len(self.modeled_prices),
            'by_product': defaultdict(int),
            'by_vintage': defaultdict(int),
            'details': []
        }

        for (date_key, product, vintage), info in self.modeled_prices.items():
            summary['by_product'][product] += 1
            summary['by_vintage'][vintage] += 1
            summary['details'].append({
                'date': date_key,
                'product': product,
                'vintage': vintage,
                'modeled_price': info['price'],
                'method': info['method']
            })

        return summary

    def get_trade_price_fallback_summary(self) -> Dict[str, Any]:
        """
        Return a detailed summary of all trade price fallbacks for auditing.

        Returns:
            Dictionary with total count, breakdowns, and details.
        """
        if not self.trade_price_fallbacks:
            return {}

        summary: Dict[str, Any] = {
            'total_fallbacks': len(self.trade_price_fallbacks),
            'by_product': defaultdict(int),
            'by_vintage': defaultdict(int),
            'details': []
        }

        for key, info in self.trade_price_fallbacks.items():
            date_key, product, vintage, is_exchange_settled, is_physically_settled = key
            summary['by_product'][product] += 1
            summary['by_vintage'][vintage] += 1
            summary['details'].append({
                'date': date_key,
                'product': product,
                'vintage': vintage,
                'is_exchange_settled': is_exchange_settled,
                'is_physically_settled': is_physically_settled,
                'fallback_price': info['fallback_price'],
                'method': info['method']
            })

        return summary

    def _get_most_recent_trade_price(
        self,
        target_date: date,
        product: str,
        vintage: str,
        is_exchange_settled: Optional[str] = None,
        is_physically_settled: Optional[bool] = None
    ) -> Optional[float]:
        """
        Retrieve the most recent trade price on or before target date.

        Uses caching to avoid redundant DataFrame filtering.

        Args:
            target_date: Date to look back from.
            product: Product name.
            vintage: Vintage string.
            is_exchange_settled: Optional exchange settlement filter.
            is_physically_settled: Optional physical settlement filter.

        Returns:
            Most recent trade price, or None if not found.
        """
        if self.trades_df is None:
            return None

        cache_key = (target_date, product, vintage, is_exchange_settled, is_physically_settled)

        if cache_key in self.trade_price_cache:
            return self.trade_price_cache[cache_key]

        mask = (
            (self.trades_df['product'] == product) &
            (self.trades_df['vintage'] == vintage) &
            (self.trades_df['date'] <= target_date)
        )

        if is_exchange_settled is not None:
            mask = mask & (self.trades_df['is_exchange_settled'] == is_exchange_settled)
        if is_physically_settled is not None:
            mask = mask & (self.trades_df['is_physically_settled'] == is_physically_settled)

        relevant_trades = self.trades_df[mask]

        if len(relevant_trades) == 0:
            self.trade_price_cache[cache_key] = None
            return None

        relevant_trades = relevant_trades.sort_values('date', ascending=False)
        most_recent_trade = relevant_trades.iloc[0]
        price = most_recent_trade['px']

        self.trade_price_cache[cache_key] = price
        return price

    def compute_daily_pnl(self, verbose: bool = True) -> pd.DataFrame:
        """
        Compute the daily PnL for all trade groups.

        Delegates to compute_daily_pnl_vectorized() for performance.
        The legacy per-group loop is preserved in _compute_group_pnl() for
        reference but is no longer called by default.

        Args:
            verbose: If True, logs progress messages.

        Returns:
            DataFrame containing detailed daily PnL decomposition.

        Raises:
            ValueError: If data has not been loaded via load_data().
        """
        return self.compute_daily_pnl_vectorized(verbose=verbose)

    # -------------------------------------------------------------------------
    # Vectorized PnL implementation
    # -------------------------------------------------------------------------

    _GROUP_KEYS: List[str] = [
        'product', 'vintage', 'desk', 'portfolio', 'strategy',
        'is_exchange_settled', 'is_physically_settled', 'fee_type', 'currency'
    ]

    def compute_daily_pnl_vectorized(self, verbose: bool = True) -> pd.DataFrame:
        """
        Vectorized implementation of daily PnL calculation.

        Replaces the per-group, per-day Python loop in compute_daily_pnl() with
        pandas groupby / cumsum / shift / merge operations for a significant
        performance improvement (typically 10-50x on real portfolios).

        The output schema is identical to compute_daily_pnl() except the four
        px_wtd_avg_* columns are not present (they were removed as dead code).

        Args:
            verbose: If True, logs progress messages.

        Returns:
            DataFrame containing detailed daily PnL decomposition.

        Raises:
            ValueError: If data has not been loaded via load_data().
        """
        if self.trades_df is None or self.prices_df is None:
            raise ValueError("Data not loaded. Call load_data() first.")

        logger.info("Computing daily PnL (vectorized)...")

        # Phase 1: aggregate all trades into daily group-level statistics
        trade_aggs = self._vec_aggregate_trades()

        if trade_aggs.empty:
            logger.warning(f"No trade groups found in {len(self.trades_df)} trades")
            return pd.DataFrame()

        # Phase 2: build full date Ã— group grid
        grid = self._vec_build_grid(trade_aggs)

        # Phase 3: cumulative position roll-forward
        grid = self._vec_compute_positions(grid)

        # Phase 4: mark price resolution (full hierarchy)
        grid = self._vec_resolve_marks(grid)

        # Phase 5: PnL component arithmetic
        grid = self._vec_compute_pnl(grid)

        # Phase 5b: weighted average price columns (optional)
        if self.include_weighted_avg_prices:
            grid = self._vec_compute_weighted_avg_prices(grid)

        # Phase 6: PnL suppression (vintage month exclusion)
        grid = self._vec_apply_suppression(grid)

        # Phase 7: filter to rows with activity, format output
        pnl_df = self._vec_filter_and_format(grid)

        pnl_df = self._sort_pnl_dataframe(pnl_df)

        if self.exclude_vintage_month_enabled and 'pnl_suppressed' in pnl_df.columns:
            suppressed_count = pnl_df['pnl_suppressed'].sum()
            if suppressed_count > 0:
                logger.info(
                    f"  Suppressed PnL for {suppressed_count} records "
                    f"where date year-month matched vintage year-month"
                )

        logger.info(
            f"PnL calculation complete (vectorized): "
            f"Generated {len(pnl_df)} daily PnL records"
        )
        return pnl_df

    def _vec_aggregate_trades(self) -> pd.DataFrame:
        """
        Aggregate all trades into daily group-level statistics in a single pass.

        Returns:
            DataFrame with columns: date, <GROUP_KEYS>, qty_buy, ctv_buy,
            qty_sell, ctv_sell, px_wtd_avg_buy, px_wtd_avg_sell,
            net_qty, cash_trade.
        """
        df = self.trades_df.copy()
        df['_ctv'] = df['qty'] * df['px']

        df['_qty_buy'] = df['qty'].clip(lower=0)
        df['_ctv_buy'] = df['_ctv'].where(df['qty'] > 0, 0.0)
        df['_qty_sell'] = df['qty'].clip(upper=0)
        df['_ctv_sell'] = df['_ctv'].where(df['qty'] < 0, 0.0)

        agg_keys = ['date'] + self._GROUP_KEYS
        agg = df.groupby(agg_keys, observed=True).agg(
            qty_buy=('_qty_buy', 'sum'),
            ctv_buy=('_ctv_buy', 'sum'),
            qty_sell=('_qty_sell', 'sum'),
            ctv_sell=('_ctv_sell', 'sum'),
            net_qty=('qty', 'sum'),
            _ctv_sum=('_ctv', 'sum'),
        ).reset_index()

        agg['px_wtd_avg_buy'] = (agg['ctv_buy'] / agg['qty_buy']).where(
            agg['qty_buy'] != 0
        )
        agg['px_wtd_avg_sell'] = (agg['ctv_sell'] / agg['qty_sell']).where(
            agg['qty_sell'] != 0
        )
        agg['cash_trade'] = -agg['_ctv_sum']
        agg = agg.drop(columns=['_ctv_sum'])

        return agg

    def _vec_build_grid(self, trade_aggs: pd.DataFrame) -> pd.DataFrame:
        """
        Build a scaffold DataFrame covering every (calendar date, group) combination.

        Trade statistics are left-joined in; missing days get zero trade activity.
        """
        groups = trade_aggs[self._GROUP_KEYS].drop_duplicates().reset_index(drop=True)
        calendar_df = pd.DataFrame({'date': self.calendar})

        # Cross join: each group Ã— every calendar date
        grid = groups.merge(calendar_df, how='cross')

        # Merge trade aggregates (left join: most cells = no trades)
        merge_keys = ['date'] + self._GROUP_KEYS
        grid = grid.merge(trade_aggs, on=merge_keys, how='left')

        # Fill missing trade fields with zero (NaN = no trades that day)
        fill_cols = ['qty_buy', 'ctv_buy', 'qty_sell', 'ctv_sell', 'net_qty', 'cash_trade']
        grid[fill_cols] = grid[fill_cols].fillna(0.0)
        # px_wtd_avg_buy/sell intentionally left NaN where no trades

        return grid

    def _vec_compute_positions(self, grid: pd.DataFrame) -> pd.DataFrame:
        """Compute qty_start and qty_end via cumsum + shift within each group."""
        grid = grid.sort_values(self._GROUP_KEYS + ['date']).reset_index(drop=True)

        grid['qty_end'] = grid.groupby(
            self._GROUP_KEYS, observed=True
        )['net_qty'].cumsum()

        grid['qty_start'] = grid.groupby(
            self._GROUP_KEYS, observed=True
        )['qty_end'].shift(1, fill_value=0.0)

        return grid

    def _vec_resolve_marks(self, grid: pd.DataFrame) -> pd.DataFrame:
        """
        Populate px_mark_cd (current day) and px_mark_pd (prior day) using the
        full mark hierarchy: desk_mark â†’ vintage_interpolation â†’ forward_fill
        â†’ recent_trade â†’ no_mark.

        Also populates mark_source_cd and mark_source_pd.
        """
        # Normalize vintage for price lookup
        grid['_norm_vintage'] = grid['vintage'].apply(self._normalize_vintage)

        # ---- 4a: merge desk/modeled prices ----
        prices = self._prices_norm_df.copy()
        # Convert product dtype to match grid (may be category)
        prices['product'] = prices['product'].astype(str)
        grid['_product_str'] = grid['product'].astype(str)

        grid = grid.merge(
            prices[['date', 'product', '_norm_vintage', 'px', '_is_modeled']].rename(
                columns={'product': '_product_str', 'px': '_raw_px'}
            ),
            on=['date', '_product_str', '_norm_vintage'],
            how='left'
        )
        grid['_is_modeled'] = grid['_is_modeled'].eq(True)

        # ---- 4b: suppress modeled prices for desks that disable vintage modeling ----
        grid['_enable_vm'] = grid['desk'].map({d: self._get_desk_setting(d, 'enable_vintage_modeling') for d in grid['desk'].unique()})
        suppress_modeled = grid['_is_modeled'] & ~grid['_enable_vm']
        grid.loc[suppress_modeled, '_raw_px'] = float('nan')
        grid.loc[suppress_modeled, '_is_modeled'] = False

        # ---- 4c: determine initial mark source ----
        grid['_mark_source'] = None
        has_price = grid['_raw_px'].notna()
        grid.loc[has_price & grid['_is_modeled'], '_mark_source'] = 'vintage_interpolation'
        grid.loc[has_price & ~grid['_is_modeled'], '_mark_source'] = 'desk_mark'

        # Start building px_mark_cd from raw price
        grid['px_mark_cd'] = grid['_raw_px']

        # ---- 4d: forward fill within groups (where allowed) ----
        grid['_ff_allowed'] = grid['desk'].map({d: self._get_desk_setting(d, 'forward_fill_allowed') for d in grid['desk'].unique()})

        grid['_px_ffilled'] = grid.groupby(
            self._GROUP_KEYS, observed=True
        )['px_mark_cd'].ffill()

        ffill_mask = (
            grid['px_mark_cd'].isna() &
            grid['_ff_allowed'] &
            grid['_px_ffilled'].notna()
        )
        grid.loc[ffill_mask, 'px_mark_cd'] = grid.loc[ffill_mask, '_px_ffilled']
        grid.loc[ffill_mask, '_mark_source'] = 'forward_fill'

        # ---- 4e: recent trade price fallback ----
        grid['_tb_allowed'] = grid['desk'].map({d: self._get_desk_setting(d, 'use_trade_price_fallback') for d in grid['desk'].unique()})

        still_missing = grid['px_mark_cd'].isna() & grid['_tb_allowed']
        if still_missing.any():
            trade_px_lookup = self._vec_build_recent_trade_price_lookup()
            grid = grid.merge(
                trade_px_lookup,
                on=['date', '_product_str', '_norm_vintage',
                    'is_exchange_settled', 'is_physically_settled'],
                how='left'
            )
            fallback_mask = (
                grid['px_mark_cd'].isna() &
                grid['_tb_allowed'] &
                grid['_recent_trade_px'].notna()
            )
            grid.loc[fallback_mask, 'px_mark_cd'] = grid.loc[fallback_mask, '_recent_trade_px']
            grid.loc[fallback_mask, '_mark_source'] = 'recent_trade'

            # Populate self.trade_price_fallbacks for API compatibility
            for row in grid.loc[fallback_mask, ['date', 'product', 'vintage', 'is_exchange_settled', 'is_physically_settled', '_recent_trade_px']].to_dict('records'):
                self.trade_price_fallbacks[
                    (row['date'], row['product'], row['vintage'], row['is_exchange_settled'], row['is_physically_settled'])
                ] = {'fallback_price': row['_recent_trade_px'], 'date': row['date'], 'method': 'most_recent_trade'}

            grid = grid.drop(columns=['_recent_trade_px'])

        # ---- 4f: fee trades override ----
        fee_mask = grid['fee_type'] != constants.FEE_TYPE_REGULAR
        if 'is_fee' in grid.columns:
            fee_mask = fee_mask | (grid['is_fee'] == True)
        fee_mask = fee_mask | grid['product'].astype(str).isin(constants.FEE_KEYWORDS)
        grid.loc[fee_mask, 'px_mark_cd'] = 0.0
        grid.loc[fee_mask, '_mark_source'] = 'fee_exempt'
        grid['_is_fee'] = fee_mask

        # ---- 4g: label remaining NaN as no_mark ----
        grid.loc[grid['px_mark_cd'].isna(), '_mark_source'] = 'no_mark'
        grid['mark_source_cd'] = grid['_mark_source']

        # ---- 4h: prior-day mark (px_mark_pd) via shift ----
        grid['px_mark_pd'] = grid.groupby(
            self._GROUP_KEYS, observed=True
        )['px_mark_cd'].shift(1)
        grid['mark_source_pd'] = grid.groupby(
            self._GROUP_KEYS, observed=True
        )['mark_source_cd'].shift(1)

        # ---- 4i: seed px_mark_pd on first calendar date from pre-calendar prices ----
        first_date = self.calendar[0] if self.calendar else None
        if first_date:
            pre_cal = {
                (p, v): px
                for (d, p, v), px in self.px_map.items()
                if d < first_date
            }
            if pre_cal:
                # Build lookup: for each (product, norm_vintage) take the first
                # pre-calendar price (replicating existing behavior)
                seen: Dict[Tuple[str, str], float] = {}
                for (p, v), px in pre_cal.items():
                    if (p, v) not in seen:
                        seen[(p, v)] = px
                pre_df = pd.DataFrame(
                    [{'_product_str': p, '_norm_vintage': v, '_pre_px': px}
                     for (p, v), px in seen.items()]
                )
                first_mask = grid['date'] == first_date
                grid = grid.merge(pre_df, on=['_product_str', '_norm_vintage'], how='left')
                needs_seed = first_mask & grid['px_mark_pd'].isna() & grid['_pre_px'].notna()
                grid.loc[needs_seed, 'px_mark_pd'] = grid.loc[needs_seed, '_pre_px']
                grid = grid.drop(columns=['_pre_px'])

        # Drop working columns
        drop_cols = [
            '_raw_px', '_is_modeled', '_enable_vm', '_mark_source',
            '_ff_allowed', '_px_ffilled', '_tb_allowed'
        ]
        grid = grid.drop(columns=[c for c in drop_cols if c in grid.columns])

        return grid

    def _vec_build_recent_trade_price_lookup(self) -> pd.DataFrame:
        """
        Build a forward-filled lookup of the most recent trade price per group.

        Returns DataFrame with columns:
            date, _product_str, _norm_vintage,
            is_exchange_settled, is_physically_settled, _recent_trade_px
        """
        trades = self.trades_df.copy()
        trades['_product_str'] = trades['product'].astype(str)
        trades['_norm_vintage'] = trades['vintage'].apply(self._normalize_vintage)

        group_keys = ['_product_str', '_norm_vintage', 'is_exchange_settled', 'is_physically_settled']

        # Last trade price per (group, date)
        last_trade = (
            trades.sort_values('date')
            .groupby(group_keys + ['date'], observed=True)['px']
            .last()
            .reset_index()
            .rename(columns={'px': '_recent_trade_px'})
        )

        # Forward-fill across the full calendar
        groups = last_trade[group_keys].drop_duplicates()
        calendar_df = pd.DataFrame({'date': self.calendar})
        full_grid = groups.merge(calendar_df, how='cross')
        full_grid = full_grid.merge(last_trade, on=group_keys + ['date'], how='left')
        full_grid = full_grid.sort_values(group_keys + ['date'])
        full_grid['_recent_trade_px'] = full_grid.groupby(
            group_keys, observed=True
        )['_recent_trade_px'].ffill()

        return full_grid

    def _vec_compute_pnl(self, grid: pd.DataFrame) -> pd.DataFrame:
        """Compute pnl_mtm, pnl_buy, pnl_sell, pnl_trading as vectorized column ops."""
        both_marks = grid['px_mark_cd'].notna() & grid['px_mark_pd'].notna()
        grid['pnl_mtm'] = float('nan')
        grid.loc[both_marks, 'pnl_mtm'] = (
            grid.loc[both_marks, 'qty_start'] *
            (grid.loc[both_marks, 'px_mark_cd'] - grid.loc[both_marks, 'px_mark_pd'])
        )

        has_buy = (grid['qty_buy'] > 0) & grid['px_mark_cd'].notna() & grid['px_wtd_avg_buy'].notna()
        grid['pnl_buy'] = float('nan')
        grid.loc[has_buy, 'pnl_buy'] = (
            grid.loc[has_buy, 'qty_buy'] *
            (grid.loc[has_buy, 'px_mark_cd'] - grid.loc[has_buy, 'px_wtd_avg_buy'])
        )

        has_sell = (grid['qty_sell'] < 0) & grid['px_mark_cd'].notna() & grid['px_wtd_avg_sell'].notna()
        grid['pnl_sell'] = float('nan')
        grid.loc[has_sell, 'pnl_sell'] = (
            grid.loc[has_sell, 'qty_sell'] *
            (grid.loc[has_sell, 'px_mark_cd'] - grid.loc[has_sell, 'px_wtd_avg_sell'])
        )

        zero_policy = grid['desk'].map({d: self._get_desk_setting(d, 'missing_pnl_policy') for d in grid['desk'].unique()}) == 'zero'
        grid.loc[zero_policy & grid['pnl_mtm'].isna(), 'pnl_mtm'] = 0.0
        grid.loc[zero_policy & grid['pnl_buy'].isna(), 'pnl_buy'] = 0.0
        grid.loc[zero_policy & grid['pnl_sell'].isna(), 'pnl_sell'] = 0.0

        # NaN if ALL components are NaN (min_count=1 behaviour)
        pnl_components = grid[['pnl_mtm', 'pnl_buy', 'pnl_sell']].astype(float)
        grid['pnl_trading'] = pnl_components.sum(axis=1, min_count=1)

        return grid

    def _vec_apply_suppression(self, grid: pd.DataFrame) -> pd.DataFrame:
        """Zero out PnL when trade date year-month >= vintage year-month (if enabled)."""
        if not self.exclude_vintage_month_enabled:
            grid['pnl_suppressed'] = False
            return grid

        # Convert to YYYYMM float for comparison (float to allow NaN for unparseable vintages)
        grid['_date_ym'] = grid['date'].apply(
            lambda d: float(d.year * 100 + d.month)
        )
        _ym_cache = {
            v: (lambda ym: float(ym[0] * 100 + ym[1]) if ym else float('nan'))(
                self._extract_year_month_from_vintage(v)
            )
            for v in grid['vintage'].unique()
        }
        grid['_vintage_ym'] = grid['vintage'].map(_ym_cache)

        has_vintage_ym = grid['_vintage_ym'].notna()
        if self.exclude_vintage_month_products == 'all':
            suppress = has_vintage_ym & (grid['_date_ym'] >= grid['_vintage_ym'].where(has_vintage_ym, 0))
        elif isinstance(self.exclude_vintage_month_products, list):
            suppress = (
                grid['product'].isin(self.exclude_vintage_month_products) &
                has_vintage_ym &
                (grid['_date_ym'] >= grid['_vintage_ym'].where(has_vintage_ym, 0))
            )
        else:
            suppress = pd.Series(False, index=grid.index)

        grid['pnl_suppressed'] = suppress
        for col in ['pnl_mtm', 'pnl_buy', 'pnl_sell', 'pnl_trading']:
            grid.loc[suppress, col] = 0.0

        grid = grid.drop(columns=['_date_ym', '_vintage_ym'])
        return grid

    def _vec_compute_weighted_avg_prices(self, grid: pd.DataFrame) -> pd.DataFrame:
        """
        Compute px_wtd_avg_start and px_wtd_avg_end (running cost basis) for each group.

        px_wtd_avg_buy and px_wtd_avg_sell are already on the grid from
        _vec_aggregate_trades(); this method adds the carry-forward cost basis columns.

        The cost basis rules mirror the old _calculate_end_of_day_wavg_price():
          - If position stays same-sign: blend prior cost basis with today's trades.
          - If position flips sign: reset basis from trades establishing new position.
          - If position ends at zero: basis is None.
        """
        group_keys = self._GROUP_KEYS
        result_rows = []

        for group_vals, grp in grid.groupby(group_keys, observed=True, sort=False):
            grp = grp.sort_values('date')
            px_wtd_avg_end_prev: Optional[float] = None

            for row in grp.itertuples():
                idx = row.Index
                qty_start = row.qty_start
                qty_buy = row.qty_buy
                ctv_buy = row.ctv_buy
                qty_sell = row.qty_sell
                ctv_sell = row.ctv_sell
                net_qty = row.net_qty
                px_wtd_avg_buy = getattr(row, 'px_wtd_avg_buy', None)
                qty_end = qty_start + net_qty

                px_wtd_avg_start = px_wtd_avg_end_prev
                px_wtd_avg_end: Optional[float] = None

                if qty_end != 0:
                    same_sign = (qty_start >= 0 and qty_end >= 0) or (qty_start < 0 and qty_end < 0)
                    if same_sign:
                        if qty_start != 0 and px_wtd_avg_start is not None:
                            ctv_start = qty_start * px_wtd_avg_start
                            if qty_start < 0:
                                if qty_sell < 0:
                                    ctv_basis = ctv_start + ctv_sell
                                    qty_basis = qty_start + qty_sell
                                else:
                                    ctv_basis = ctv_start
                                    qty_basis = qty_start
                            else:
                                if qty_buy > 0 and pd.notna(px_wtd_avg_buy):
                                    ctv_basis = ctv_start + ctv_buy
                                    qty_basis = qty_start + qty_buy
                                else:
                                    ctv_basis = ctv_start
                                    qty_basis = qty_start
                        else:
                            if qty_end < 0 and qty_sell < 0:
                                ctv_basis = ctv_sell
                                qty_basis = qty_sell
                            elif qty_end > 0 and qty_buy > 0 and pd.notna(px_wtd_avg_buy):
                                ctv_basis = px_wtd_avg_buy * qty_buy
                                qty_basis = qty_buy
                            else:
                                ctv_basis = None
                                qty_basis = None
                    else:
                        # Position flip: cost basis from trades establishing new direction
                        if qty_end < 0:
                            if qty_sell < 0:
                                ctv_basis = ctv_sell
                                qty_basis = qty_sell
                            else:
                                ctv_basis = None
                                qty_basis = None
                        else:
                            if qty_buy > 0 and pd.notna(px_wtd_avg_buy):
                                ctv_basis = px_wtd_avg_buy * qty_buy
                                qty_basis = qty_buy
                            else:
                                ctv_basis = None
                                qty_basis = None

                    if ctv_basis is not None and qty_basis is not None and qty_basis != 0:
                        px_wtd_avg_end = ctv_basis / qty_basis

                result_rows.append((idx, px_wtd_avg_start, px_wtd_avg_end))
                px_wtd_avg_end_prev = px_wtd_avg_end

        if result_rows:
            idx_vals, starts, ends = zip(*result_rows)
            grid.loc[list(idx_vals), 'px_wtd_avg_start'] = list(starts)
            grid.loc[list(idx_vals), 'px_wtd_avg_end'] = list(ends)
        else:
            grid['px_wtd_avg_start'] = None
            grid['px_wtd_avg_end'] = None

        grid['px_wtd_avg_start'] = pd.to_numeric(grid['px_wtd_avg_start'], errors='coerce')
        grid['px_wtd_avg_end'] = pd.to_numeric(grid['px_wtd_avg_end'], errors='coerce')

        return grid

    def _vec_filter_and_format(self, grid: pd.DataFrame) -> pd.DataFrame:
        """Filter to rows with trading activity and rename columns to output schema."""
        activity_mask = (
            (grid['qty_start'] != 0) |
            (grid['qty_buy'] != 0) |
            (grid['qty_sell'] != 0)
        )
        result = grid[activity_mask].copy()

        # fee_type: None for regular trades (matching existing output convention)
        result['fee_type'] = result['fee_type'].where(
            result['fee_type'] != constants.FEE_TYPE_REGULAR, other=None
        )

        # Rename is_fee from computed flag
        if '_is_fee' in result.columns:
            result = result.rename(columns={'_is_fee': 'is_fee'})
        else:
            result['is_fee'] = False

        output_cols = [
            'date', 'product', 'vintage', 'desk', 'portfolio', 'strategy',
            'is_exchange_settled', 'is_physically_settled',
            'is_fee', 'fee_type', 'currency',
            'qty_start', 'qty_buy', 'qty_sell', 'qty_end',
            'px_mark_pd', 'px_mark_cd',
            'pnl_mtm', 'pnl_buy', 'pnl_sell', 'pnl_trading', 'pnl_suppressed',
            'net_qty', 'ctv_buy', 'ctv_sell', 'cash_trade',
            'mark_source_pd', 'mark_source_cd',
        ]
        if self.include_weighted_avg_prices:
            output_cols += [
                'px_wtd_avg_buy', 'px_wtd_avg_sell',
                'px_wtd_avg_start', 'px_wtd_avg_end',
            ]
            result['open_unrealized_pnl'] = result['qty_end'] * (result['px_mark_cd'] - result['px_wtd_avg_end'])
            output_cols.append('open_unrealized_pnl')
        # Only keep columns that exist
        output_cols = [c for c in output_cols if c in result.columns]
        return result[output_cols].reset_index(drop=True)

    # -------------------------------------------------------------------------
    # End vectorized PnL implementation
    # -------------------------------------------------------------------------

    def _compute_group_pnl(
        self,
        product: str,
        vintage: str,
        desk: str,
        is_exchange_settled: str,
        is_physically_settled: bool,
        fee_type: str,
        currency: str,
        group_trades: pd.DataFrame
    ) -> List[Dict[str, Any]]:
        """
        Compute the daily PnL series for a single instrument group.

        Args:
            product: Product name.
            vintage: Vintage string.
            desk: Trading desk.
            is_exchange_settled: Exchange settlement flag.
            is_physically_settled: Physical settlement flag.
            fee_type: Type of fee.
            currency: Currency code.
            group_trades: DataFrame of trades for this group.

        Returns:
            List of daily PnL records.
        """
        trades_by_day = self._aggregate_daily_trades(group_trades)
        state = GroupPnLState()

        # Look for price before first calendar date
        first_calendar_date = self.calendar[0] if self.calendar else None
        if first_calendar_date:
            normalized_vintage = self._normalize_vintage(vintage)
            all_price_dates = sorted([
                d for d, p, v in self.px_map.keys()
                if p == product and v == normalized_vintage
            ])
            for d in all_price_dates:
                if d < first_calendar_date:
                    state.prior_px = self.px_map.get((d, product, normalized_vintage))
                    break

        results: List[Dict[str, Any]] = []
        is_fee = self._determine_if_fee_trade(fee_type, group_trades, product)

        for d in self.calendar:
            todays_trades = trades_by_day.get(d, [])
            daily_aggs = self._aggregate_daily_trade_stats(todays_trades)

            marks = self._get_daily_marks_with_state(
                d, product, vintage, desk, is_fee,
                is_exchange_settled, is_physically_settled, state
            )

            pnl_components = self._calculate_pnl_components(
                state.qty_start, daily_aggs, marks
            )

            if self._has_trading_activity(state.qty_start, daily_aggs):
                result_record = self._create_result_record(
                    d, product, vintage, desk, is_exchange_settled,
                    is_physically_settled, is_fee, fee_type, currency,
                    state.qty_start, daily_aggs, marks, pnl_components, todays_trades
                )
                results.append(result_record)

            state.qty_start = state.qty_start + daily_aggs['net_qty']

        return results

    def _aggregate_daily_trade_stats(self, todays_trades: List[Dict]) -> Dict[str, Any]:
        """
        Aggregate daily trade statistics from a list of trades.

        Args:
            todays_trades: List of trade dictionaries.

        Returns:
            Dictionary with qty_buy, ctv_buy, qty_sell, ctv_sell,
            px_wtd_avg_buy, px_wtd_avg_sell, net_qty.
        """
        qty_buy = 0.0
        ctv_buy = 0.0
        qty_sell = 0.0
        ctv_sell = 0.0

        for trade in todays_trades:
            qty = trade['qty']
            px = trade['px']

            if qty > 0:
                qty_buy += qty
                ctv_buy += qty * px
            else:
                qty_sell += qty
                ctv_sell += qty * px

        px_wtd_avg_buy = (ctv_buy / qty_buy) if qty_buy != 0 else None
        px_wtd_avg_sell = (ctv_sell / qty_sell) if qty_sell != 0 else None
        net_qty = sum(trade['qty'] for trade in todays_trades)

        return {
            'qty_buy': qty_buy,
            'ctv_buy': ctv_buy,
            'qty_sell': qty_sell,
            'ctv_sell': ctv_sell,
            'px_wtd_avg_buy': px_wtd_avg_buy,
            'px_wtd_avg_sell': px_wtd_avg_sell,
            'net_qty': net_qty
        }

    def _determine_if_fee_trade(
        self,
        fee_type: str,
        group_trades: pd.DataFrame,
        product: str
    ) -> bool:
        """
        Determine if a trade group represents a fee rather than a regular trade.

        Uses multiple detection methods for robustness:
        1. Check fee_type column (primary).
        2. Check is_fee boolean column.
        3. Fall back to product name patterns.

        Args:
            fee_type: Fee type from trade data.
            group_trades: DataFrame of trades.
            product: Product name.

        Returns:
            True if this is a fee trade, False otherwise.
        """
        if fee_type != constants.FEE_TYPE_REGULAR:
            return True

        if 'is_fee' in group_trades.columns:
            return group_trades['is_fee'].iloc[0] if not group_trades.empty else False

        return product in constants.FEE_KEYWORDS

    def _has_trading_activity(self, qty_start: float, daily_aggs: Dict) -> bool:
        """
        Check if there was any position or trading activity on a given day.

        Args:
            qty_start: Starting position quantity.
            daily_aggs: Aggregated daily trade statistics.

        Returns:
            True if there was activity, False otherwise.
        """
        return (qty_start != 0) or (daily_aggs['qty_buy'] != 0) or (daily_aggs['qty_sell'] != 0)

    def _create_result_record(
        self,
        d: date,
        product: str,
        vintage: str,
        desk: str,
        is_exchange_settled: str,
        is_physically_settled: bool,
        is_fee: bool,
        fee_type: str,
        currency: str,
        qty_start: float,
        daily_aggs: Dict,
        marks: Dict,
        pnl_components: Dict,
        todays_trades: List[Dict]
    ) -> Dict[str, Any]:
        """
        Create a standardized dictionary record for a single day's PnL output.

        Args:
            d: Trade date.
            product: Product name.
            vintage: Vintage string.
            desk: Trading desk.
            is_exchange_settled: Exchange settlement flag.
            is_physically_settled: Physical settlement flag.
            is_fee: Whether this is a fee trade.
            fee_type: Type of fee.
            currency: Currency code.
            qty_start: Starting quantity.
            daily_aggs: Aggregated daily statistics.
            marks: Mark prices and sources.
            pnl_components: PnL component values.
            todays_trades: List of today's trades.

        Returns:
            Dictionary with all PnL output fields.
        """
        # Check if PnL should be suppressed for this date-vintage combination
        pnl_suppressed = self._should_suppress_pnl(d, product, vintage)

        # If suppressed, override PnL values to 0
        if pnl_suppressed:
            pnl_mtm = 0.0
            pnl_buy = 0.0
            pnl_sell = 0.0
            pnl_trading = 0.0
        else:
            pnl_mtm = pnl_components['pnl_mtm']
            pnl_buy = pnl_components['pnl_buy']
            pnl_sell = pnl_components['pnl_sell']
            pnl_trading = pnl_components['pnl_trading']

        qty_end = qty_start + daily_aggs['net_qty']
        px_cd = marks['px_cd']

        return {
            'date': d,
            'product': product,
            'vintage': vintage,
            'desk': desk,
            'is_exchange_settled': is_exchange_settled,
            'is_physically_settled': is_physically_settled,
            'is_fee': is_fee,
            'fee_type': fee_type if fee_type != constants.FEE_TYPE_REGULAR else None,
            'currency': currency,
            'qty_start': qty_start,
            'qty_buy': daily_aggs['qty_buy'],
            'qty_sell': daily_aggs['qty_sell'],
            'qty_end': qty_end,
            'px_mark_pd': marks['px_pd'],
            'px_mark_cd': px_cd,
            'pnl_mtm': pnl_mtm,
            'pnl_buy': pnl_buy,
            'pnl_sell': pnl_sell,
            'pnl_trading': pnl_trading,
            'pnl_suppressed': pnl_suppressed,
            'net_qty': daily_aggs['net_qty'],
            'ctv_buy': daily_aggs['ctv_buy'],
            'ctv_sell': daily_aggs['ctv_sell'],
            'cash_trade': -sum(trade['qty'] * trade['px'] for trade in todays_trades),
            'mark_source_pd': marks['mark_source_pd'],
            'mark_source_cd': marks['mark_source_cd']
        }

    def _aggregate_daily_trades(
        self,
        group_trades: pd.DataFrame
    ) -> Dict[date, List[Dict]]:
        """
        Aggregate trades into a dictionary keyed by date.

        Args:
            group_trades: DataFrame of trades.

        Returns:
            Dictionary mapping dates to lists of trade records.
        """
        trades_by_day: Dict[date, List[Dict]] = {}
        for _, trade in group_trades.iterrows():
            trade_date = trade['date']
            if trade_date not in trades_by_day:
                trades_by_day[trade_date] = []
            trades_by_day[trade_date].append(trade)
        return trades_by_day

    def _get_daily_marks_with_state(
        self,
        d: date,
        product: str,
        vintage: str,
        desk: str,
        is_fee: bool,
        is_exchange_settled: str,
        is_physically_settled: bool,
        state: GroupPnLState
    ) -> Dict[str, Any]:
        """
        Retrieve mark-to-market prices for current and prior day.

        Encapsulates the full lookup hierarchy:
        1. Direct desk mark from price file.
        2. Modeled price via vintage extrapolation (if enabled for desk).
        3. Forward-filled price from previous day (if enabled for desk).
        4. Most recent trade price as fallback (if enabled for desk).

        Args:
            d: Current date.
            product: Product name.
            vintage: Vintage string.
            desk: Trading desk name.
            is_fee: Whether this is a fee trade.
            is_exchange_settled: Exchange settlement flag.
            is_physically_settled: Physical settlement flag.
            state: GroupPnLState (modified in place).

        Returns:
            Dictionary with px_cd, px_pd, mark_source_cd, mark_source_pd.
        """
        if is_fee:
            return {
                'px_cd': 0.0,
                'px_pd': 0.0,
                'mark_source_cd': 'fee_exempt',
                'mark_source_pd': 'fee_exempt'
            }

        # Get desk-specific settings
        forward_fill_allowed = self._get_desk_setting(desk, 'forward_fill_allowed')
        use_trade_price_fallback = self._get_desk_setting(desk, 'use_trade_price_fallback')
        enable_vintage_modeling = self._get_desk_setting(desk, 'enable_vintage_modeling')

        normalized_vintage = self._normalize_vintage(vintage)
        price_key = (d, product, normalized_vintage)
        px_cd = self.px_map.get(price_key)
        mark_source_cd = None
        mark_source_pd = None

        # Check if this is a modeled price
        is_modeled_price = price_key in self.modeled_prices
        
        # Skip modeled prices if desk doesn't allow vintage modeling
        if is_modeled_price and not enable_vintage_modeling:
            px_cd = None

        if px_cd is not None:
            if is_modeled_price and enable_vintage_modeling:
                mark_source_cd = 'vintage_interpolation'
            else:
                mark_source_cd = 'desk_mark'
        else:
            if forward_fill_allowed and state.prior_px is not None:
                px_cd = state.prior_px
                mark_source_cd = 'forward_fill'
            elif use_trade_price_fallback:
                trade_fallback_price = self._get_most_recent_trade_price(
                    d, product, vintage, is_exchange_settled, is_physically_settled
                )
                if trade_fallback_price is not None:
                    px_cd = trade_fallback_price
                    mark_source_cd = 'recent_trade'
                    fallback_key = (d, product, vintage, is_exchange_settled, is_physically_settled)
                    self.trade_price_fallbacks[fallback_key] = {
                        'fallback_price': trade_fallback_price,
                        'date': d,
                        'method': 'most_recent_trade'
                    }
                else:
                    mark_source_cd = 'no_mark'
            else:
                mark_source_cd = 'no_mark'

        px_pd = state.prior_px
        if px_pd is not None:
            mark_source_pd = state.prior_mark_source or 'desk_mark'
        else:
            mark_source_pd = None

        state.prior_px = px_cd
        state.prior_mark_source = mark_source_cd

        return {
            'px_cd': px_cd,
            'px_pd': px_pd,
            'mark_source_cd': mark_source_cd,
            'mark_source_pd': mark_source_pd
        }

    def _calculate_pnl_components(
        self,
        qty_start: float,
        daily_aggs: Dict,
        marks: Dict
    ) -> Dict[str, Optional[float]]:
        """
        Calculate individual PnL components: MTM, buys, and sells.

        Args:
            qty_start: Starting position quantity.
            daily_aggs: Aggregated daily trade statistics.
            marks: Mark prices.

        Returns:
            Dictionary with pnl_mtm, pnl_buy, pnl_sell, pnl_trading.
        """
        qty_buy = daily_aggs['qty_buy']
        qty_sell = daily_aggs['qty_sell']
        px_wtd_avg_buy = daily_aggs['px_wtd_avg_buy']
        px_wtd_avg_sell = daily_aggs['px_wtd_avg_sell']

        px_cd = marks['px_cd']
        px_pd = marks['px_pd']

        pnl_mtm: Optional[float] = None
        pnl_buy: Optional[float] = None
        pnl_sell: Optional[float] = None

        if px_cd is not None and px_pd is not None:
            pnl_mtm = qty_start * (px_cd - px_pd)
        elif self.missing_pnl_policy == 'zero':
            pnl_mtm = 0.0

        if px_cd is not None and qty_buy > 0:
            pnl_buy = qty_buy * (px_cd - px_wtd_avg_buy)
        elif self.missing_pnl_policy == 'zero':
            pnl_buy = 0.0

        if px_cd is not None and qty_sell < 0:
            pnl_sell = qty_sell * (px_cd - px_wtd_avg_sell)
        elif self.missing_pnl_policy == 'zero':
            pnl_sell = 0.0

        pnl_components = [pnl_mtm, pnl_buy, pnl_sell]
        valid_components = [p for p in pnl_components if p is not None]
        pnl_trading = sum(valid_components) if valid_components else None

        return {
            'pnl_mtm': pnl_mtm,
            'pnl_buy': pnl_buy,
            'pnl_sell': pnl_sell,
            'pnl_trading': pnl_trading
        }

    def validate_results(self, pnl_df: pd.DataFrame, verbose: bool = True) -> Dict[str, bool]:
        """
        Perform validation checks on the computed PnL DataFrame.

        Args:
            pnl_df: Computed PnL DataFrame.
            verbose: If True, prints validation results.

        Returns:
            Dictionary mapping check names to pass/fail status.
        """
        logger.info("Performing validation checks...")

        validations: Dict[str, bool] = {}

        validations['qty_continuity'] = self._validate_quantity_continuity(pnl_df)
        validations['pnl_consistency'] = self._validate_pnl_consistency(pnl_df)
        validations['no_negative_prices'] = self._validate_no_negative_prices(pnl_df)

        logger.debug(f"Validation results: {validations}")
        return validations

    def _validate_quantity_continuity(self, pnl_df: pd.DataFrame) -> bool:
        """
        Validate that quantity is continuous from one day to the next.

        Checks that each day's qty_start equals the previous day's qty_end.

        Args:
            pnl_df: PnL DataFrame.

        Returns:
            True if validation passes, False otherwise.
        """
        try:
            groupby_cols = [
                'product', 'vintage', 'desk', 'portfolio', 'strategy',
                'is_exchange_settled', 'is_physically_settled'
            ]
            if 'fee_type' in pnl_df.columns:
                groupby_cols.append('fee_type')
            if 'currency' in pnl_df.columns:
                groupby_cols.append('currency')

            for group_key, group in pnl_df.groupby(groupby_cols):
                group_sorted = group.sort_values('date')

                for i in range(1, len(group_sorted)):
                    prev_date = group_sorted.iloc[i-1]['date']
                    prev_qty_end = group_sorted.iloc[i-1]['qty_end']
                    curr_date = group_sorted.iloc[i]['date']
                    curr_qty_start = group_sorted.iloc[i]['qty_start']

                    if abs(prev_qty_end - curr_qty_start) > constants.EPSILON:
                        group_str = '-'.join([str(k) for k in group_key if k is not None])
                        logger.warning(f"Quantity continuity error for {group_str}:")
                        logger.warning(f"  {prev_date}: qty_end = {prev_qty_end}")
                        logger.warning(f"  {curr_date}: qty_start = {curr_qty_start}")
                        logger.warning(f"  Difference: {abs(prev_qty_end - curr_qty_start)}")
                        return False

            return True
        except Exception:
            logger.exception("Quantity continuity validation failed")
            return False

    def _validate_pnl_consistency(self, pnl_df: pd.DataFrame) -> bool:
        """
        Validate that total trading PnL equals sum of components.

        Args:
            pnl_df: PnL DataFrame.

        Returns:
            True if validation passes, False otherwise.
        """
        try:
            valid_rows = pnl_df.dropna(subset=['pnl_mtm', 'pnl_buy', 'pnl_sell'])

            if len(valid_rows) > 0:
                computed_total = (
                    valid_rows['pnl_mtm'] +
                    valid_rows['pnl_buy'] +
                    valid_rows['pnl_sell']
                )

                differences = abs(computed_total - valid_rows['pnl_trading'])
                max_diff = differences.max()

                if max_diff > constants.EPSILON:
                    logger.warning(f"PnL component sum mismatch, max difference: {max_diff}")
                    return False

            return True
        except Exception:
            logger.exception("PnL consistency validation failed")
            return False

    def _validate_no_negative_prices(self, pnl_df: pd.DataFrame) -> bool:
        """
        Check for any negative prices in the output.

        Negative prices are allowed for all power desks (power_forward and power_short_term)
        since power prices can frequently go negative in electricity markets.

        Args:
            pnl_df: PnL DataFrame.

        Returns:
            True if no negative prices found (excluding power desks), False otherwise.
        """
        try:
            price_cols = ['px_mark_pd', 'px_mark_cd',
                          'px_wtd_avg_buy', 'px_wtd_avg_sell']

            # Filter out power desks - negative prices are allowed for power products
            check_df = pnl_df.copy()
            if 'desk' in check_df.columns:
                power_desks = [constants.POWER_DESK, constants.POWER_SHORT_TERM_DESK, constants.POWER_ANCILLARY_DESK]
                check_df = check_df[~check_df['desk'].isin(power_desks)]

            for col in price_cols:
                if col in check_df.columns:
                    negative_prices = check_df[col] < 0
                    if negative_prices.any():
                        logger.warning(f"Found negative prices in column: {col}")
                        return False

            return True
        except Exception:
            logger.exception("Negative price validation failed")
            return False

    def _round_output_precision(self, pnl_df: pd.DataFrame) -> pd.DataFrame:
        """
        Round all numeric columns to configured precision.

        Args:
            pnl_df: PnL DataFrame.

        Returns:
            DataFrame with rounded values.
        """
        df_rounded = pnl_df.copy()

        config = get_config()
        precision = config.get_precision_config()

        price_precision = precision.get('prices', 5)
        price_cols = ['px_mark_pd', 'px_mark_cd',
                      'px_wtd_avg_buy', 'px_wtd_avg_sell',
                      'px_wtd_avg_start', 'px_wtd_avg_end']
        for col in price_cols:
            if col in df_rounded.columns:
                df_rounded[col] = df_rounded[col].round(price_precision)
            usd_col = f"usd_{col}"
            if usd_col in df_rounded.columns:
                df_rounded[usd_col] = df_rounded[usd_col].round(price_precision)

        pnl_precision = precision.get('pnl', 2)
        pnl_cols = ['pnl_mtm', 'pnl_buy', 'pnl_sell', 'pnl_trading', 'open_unrealized_pnl']
        for col in pnl_cols:
            if col in df_rounded.columns:
                df_rounded[col] = df_rounded[col].round(pnl_precision)
            usd_col = f"usd_{col}"
            if usd_col in df_rounded.columns:
                df_rounded[usd_col] = df_rounded[usd_col].round(pnl_precision)

        ctv_precision = precision.get('countervalues', 2)
        ctv_cols = ['ctv_buy', 'ctv_sell', 'cash_trade']
        for col in ctv_cols:
            if col in df_rounded.columns:
                df_rounded[col] = df_rounded[col].round(ctv_precision)
            usd_col = f"usd_{col}"
            if usd_col in df_rounded.columns:
                df_rounded[usd_col] = df_rounded[usd_col].round(ctv_precision)

        qty_precision = precision.get('quantities', 2)
        qty_cols = ['qty_start', 'qty_buy', 'qty_sell', 'qty_end', 'net_qty']
        for col in qty_cols:
            if col in df_rounded.columns:
                df_rounded[col] = df_rounded[col].round(qty_precision)

        return df_rounded

    def get_rounded_results(self, pnl_df: pd.DataFrame) -> pd.DataFrame:
        """
        Return the final PnL DataFrame with proper rounding and sorting.

        Args:
            pnl_df: Raw PnL DataFrame.

        Returns:
            Rounded and sorted DataFrame.
        """
        pnl_df = self._add_converted_currency_columns(pnl_df)
        df_rounded = self._round_output_precision(pnl_df)
        df_rounded = self._sort_pnl_dataframe(df_rounded)
        df_rounded = self._add_year_month_day_columns(df_rounded)

        return df_rounded

    def get_rounded_pricing_data(self) -> pd.DataFrame:
        """
        Return the final pricing DataFrame with proper rounding and sorting.

        Returns:
            Rounded and sorted pricing DataFrame.
        """
        if self.prices_df is None:
            return pd.DataFrame()

        pricing_df = self.prices_df.copy()

        if 'currency' not in pricing_df.columns:
            pricing_df['currency'] = constants.DEFAULT_CURRENCY

        pricing_df = self._add_converted_currency_columns(pricing_df)
        df_rounded = self._round_pricing_precision(pricing_df)
        df_rounded = self._sort_pricing_dataframe(df_rounded)
        df_rounded = self._add_year_month_day_columns(df_rounded)

        return df_rounded

    def _round_pricing_precision(self, pricing_df: pd.DataFrame) -> pd.DataFrame:
        """
        Round pricing DataFrame columns to configured precision.

        Args:
            pricing_df: Pricing DataFrame.

        Returns:
            DataFrame with rounded values.
        """
        df_rounded = pricing_df.copy()

        config = get_config()
        precision = config.get_precision_config()

        price_precision = precision.get('prices', 5)
        price_cols = ['px', 'price']
        for col in price_cols:
            if col in df_rounded.columns:
                df_rounded[col] = df_rounded[col].round(price_precision)
            usd_col = f"usd_{col}"
            if usd_col in df_rounded.columns:
                df_rounded[usd_col] = df_rounded[usd_col].round(price_precision)

        return df_rounded

    def _sort_pricing_dataframe(self, pricing_df: pd.DataFrame) -> pd.DataFrame:
        """
        Apply standardized multi-level sort to the pricing DataFrame.

        Sort Order: date, product, vintage (chronological).

        Args:
            pricing_df: Pricing DataFrame.

        Returns:
            Sorted DataFrame.
        """
        if pricing_df.empty:
            return pricing_df

        pricing_df['_vintage_sort'] = pricing_df['vintage'].apply(parse_vintage_for_ordering)

        pricing_df = pricing_df.sort_values([
            'date',
            'product',
            '_vintage_sort',
            'vintage'
        ])

        pricing_df = pricing_df.drop(columns=['_vintage_sort'])

        return pricing_df

    def _sort_pnl_dataframe(self, pnl_df: pd.DataFrame) -> pd.DataFrame:
        """
        Apply standardized multi-level sort to the PnL DataFrame.

        Sort Order:
        1. date (chronological)
        2. desk (alphabetical)
        3. product (alphabetical)
        4. vintage (chronological)
        5. is_exchange_settled ('FALSE' first)
        6. fee_type ('regular' first)

        Args:
            pnl_df: PnL DataFrame.

        Returns:
            Sorted DataFrame.
        """
        if pnl_df.empty:
            return pnl_df

        pnl_df['_exchange_sort'] = pnl_df['is_exchange_settled'].map({
            constants.EXCHANGE_FALSE: 0,
            constants.EXCHANGE_TRUE: 1
        })

        pnl_df['_fee_sort'] = pnl_df['fee_type'].apply(
            lambda x: 0 if x is None or x == constants.FEE_TYPE_REGULAR else 1
        )

        pnl_df = pnl_df.sort_values([
            'date',
            'desk',
            'portfolio',
            'strategy',
            'product',
            'vintage',
            'currency',
            '_exchange_sort',
            '_fee_sort',
            'fee_type'
        ])

        pnl_df = pnl_df.drop(columns=['_exchange_sort', '_fee_sort'])

        return pnl_df

    def _add_year_month_day_columns(self, pnl_df: pd.DataFrame) -> pd.DataFrame:
        """
        Add year, month, and day columns derived from the date column.

        Args:
            pnl_df: DataFrame with date column.

        Returns:
            DataFrame with year, month, day columns inserted after date.
        """
        year = pd.to_datetime(pnl_df['date']).dt.year
        month = pd.to_datetime(pnl_df['date']).dt.month
        day = pd.to_datetime(pnl_df['date']).dt.day

        insert_pos = 1 if 'date' in pnl_df.columns else 0
        pnl_df.insert(insert_pos, 'day', day)
        pnl_df.insert(insert_pos, 'month', month)
        pnl_df.insert(insert_pos, 'year', year)
        return pnl_df

    def _add_converted_currency_columns(self, pnl_df: pd.DataFrame) -> pd.DataFrame:
        """
        Add USD equivalents for specified columns using configured rates.

        Args:
            pnl_df: DataFrame with currency column.

        Returns:
            DataFrame with usd_ prefixed columns added.
        """
        pnl_df['_conv_rate'] = pnl_df['currency'].map(
            constants.CURRENCY_CONVERSION_RATES
        ).fillna(1.0)

        columns_to_convert = [
            'px_mark_pd', 'px_mark_cd',
            'px_wtd_avg_buy', 'px_wtd_avg_sell', 'px_wtd_avg_start', 'px_wtd_avg_end',
            'pnl_mtm', 'pnl_buy', 'pnl_sell', 'pnl_trading', 'open_unrealized_pnl',
            'ctv_buy', 'ctv_sell', 'cash_trade'
        ]

        for col in columns_to_convert:
            if col in pnl_df.columns:
                usd_col_name = f'usd_{col}'
                pnl_df[usd_col_name] = pnl_df[col] * pnl_df['_conv_rate']

        pnl_df = pnl_df.drop(columns=['_conv_rate'])

        # Zero out USD PnL columns when pnl_suppressed is True
        if 'pnl_suppressed' in pnl_df.columns:
            suppressed_mask = pnl_df['pnl_suppressed'] == True
            pnl_usd_cols = ['usd_pnl_mtm', 'usd_pnl_buy', 'usd_pnl_sell', 'usd_pnl_trading']
            for col in pnl_usd_cols:
                if col in pnl_df.columns:
                    pnl_df.loc[suppressed_mask, col] = 0.0

        # Move wtd avg + open_unrealized_pnl columns to the rightmost positions
        wtd_avg_tail = [c for c in [
            'px_wtd_avg_buy', 'px_wtd_avg_sell', 'px_wtd_avg_start',
            'px_wtd_avg_end', 'open_unrealized_pnl',
            'usd_px_wtd_avg_buy', 'usd_px_wtd_avg_sell', 'usd_px_wtd_avg_start',
            'usd_px_wtd_avg_end', 'usd_open_unrealized_pnl'
        ] if c in pnl_df.columns]
        if wtd_avg_tail:
            other_cols = [c for c in pnl_df.columns if c not in wtd_avg_tail]
            pnl_df = pnl_df[other_cols + wtd_avg_tail]

        return pnl_df

