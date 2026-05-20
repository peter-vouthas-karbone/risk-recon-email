#!/usr/bin/env python3
"""
Price Data Loader.

Loads, filters, and standardizes price data from all sources into a single
combined DataFrame. All filtering to the traded product universe happens here,
at read-time, so downstream processing never sees rows for untouched products.

Sources: main RIN prices, GOO, BOHO, NYISO/PJM/MISO/CAISO spot prices,
         forward curves, ERCOT ancillary.
"""

import logging
import os
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Optional, Set, Tuple

import pandas as pd

from karbone_pnl_pos.utils import constants
from karbone_pnl_pos.converters.utils import clean_price_string, convert_hour_to_he_vintage, parse_date
from karbone_pnl_pos.prices.forward_curve_loader import ForwardCurveLoader

logger = logging.getLogger('pnl.' + __name__)

_DART_SUFFIX = ' - DART'
# Threshold above which ISO spot files are read in chunks to bound peak memory
_ISO_SPOT_CHUNK_SIZE = 500_000
_ISO_SPOT_CHUNK_THRESHOLD_BYTES = 200_000_000


@dataclass(frozen=True)
class PriceUniverse:
    """
    Complete set of products and date range needed to price a set of trades.

    Derived from converted trades before any price files are opened so that
    each loader can filter at read-time.
    """
    # traded âˆª base products (e.g. MCC from MCC+3) âˆª alias sources (e.g. HNG from H)
    products: Set[str]
    raw_traded_products: Set[str]
    min_date: pd.Timestamp   # min trade date âˆ’ 30 days
    max_date: pd.Timestamp   # max trade date + 30 days

    @classmethod
    def from_trades(cls, trades_df: pd.DataFrame) -> "PriceUniverse":
        """Derive the price universe from a converted trades DataFrame."""
        raw = {p for p in trades_df['product'].unique() if pd.notna(p)}

        base: Set[str] = set()
        for p in raw:
            m = constants.ADJUSTED_PRODUCT_PATTERN.match(str(p))
            if m:
                base.add(m.group(1))

        aliases: Set[str] = {
            constants.PRICING_PRODUCT_ALIASES[p]
            for p in raw
            if p in constants.PRICING_PRODUCT_ALIASES
        }

        dates = pd.to_datetime(trades_df['date'])
        return cls(
            products=raw | base | aliases,
            raw_traded_products=raw,
            min_date=dates.min() - timedelta(days=30),
            max_date=dates.max() + timedelta(days=30),
        )


def load_all_prices(
    config: Any,
    universe: PriceUniverse,
) -> Tuple[pd.DataFrame, Set[Tuple[str, str]]]:
    """
    Load, filter, and combine all price sources for the given universe.

    Args:
        config: Config object from config_loader.get_config().
        universe: PriceUniverse derived from converted trades.

    Returns:
        Tuple of (combined_prices_df, forward_curve_vintages).
        combined_prices_df columns: [date, product, vintage, price].
        forward_curve_vintages: set of (product, vintage) exempt from modeling.

    Raises:
        FileNotFoundError: If the main RIN prices file is missing.
    """
    all_dfs = []

    # 1. Main RIN prices (required)
    rin_path = config.get_path('rin_prices')
    all_dfs.append(_load_main_rin(rin_path, universe))

    # 2. GOO prices (optional)
    goo_path = config.get_path('goo_prices')
    if goo_path and os.path.exists(goo_path):
        df = _load_goo(goo_path, universe)
        if df is not None and not df.empty:
            all_dfs.append(df)

    # 3. BOHO prices (optional)
    boho_path = config.get_path('boho_prices')
    if boho_path and os.path.exists(boho_path):
        df = _load_boho(boho_path, universe)
        if df is not None and not df.empty:
            all_dfs.append(df)

    # 4. ISO spot prices (all optional)
    for iso in ('nyiso', 'pjm', 'miso', 'caiso'):
        path = config.get_path(f'{iso}_spot_pricing')
        if path and os.path.exists(path):
            df = _load_iso_spot(path, iso, universe)
            if df is not None and not df.empty:
                all_dfs.append(df)

    # 5. Forward curves + ERCOT ancillary
    curve_paths = {
        'pjm': config.get_path('pjm_forward_curve'),
        'nyiso': config.get_path('nyiso_forward_curve'),
        'ercot': config.get_path('ercot_forward_curve'),
        'nepool': config.get_path('nepool_forward_curve'),
        'caiso': config.get_path('caiso_forward_curve'),
        'miso': config.get_path('miso_forward_curve'),
        'gas': config.get_path('gas_forward_curve'),
    }
    ancillary_path = config.get_path('ercot_ancillary_prices')
    fc_df, fc_vintages = _load_forward_curves(curve_paths, ancillary_path, universe)
    if fc_df is not None and not fc_df.empty:
        all_dfs.append(fc_df)

    # Combine and dedup, keeping the last (most recent) price per (date, product, vintage)
    combined = pd.concat(all_dfs, ignore_index=True)
    logger.info(f"Combined total before deduplication: {len(combined)} price records")
    combined = combined.sort_values(['date', 'product', 'vintage'])
    combined = combined.drop_duplicates(subset=['date', 'product', 'vintage'], keep='last')
    logger.info(f"Final combined price dataset: {len(combined)} records")

    return combined, fc_vintages


# ---------------------------------------------------------------------------
# Private loaders â€” each returns a DataFrame with columns [date, product, vintage, price]
# ---------------------------------------------------------------------------

def _load_main_rin(path: str, universe: PriceUniverse) -> pd.DataFrame:
    logger.info(f"Loading main RIN prices from {path}")
    df = pd.read_csv(path)
    logger.info(f"Loaded {len(df)} records from main RIN prices file")

    if universe.products and 'product' in df.columns:
        df = df[df['product'].isin(universe.products)]
    if 'date' in df.columns:
        df = _filter_by_date_str(df, 'date', universe)

    logger.info(f"Main RIN prices after filtering: {len(df)} records")
    return df


def _load_goo(path: str, universe: PriceUniverse) -> Optional[pd.DataFrame]:
    try:
        logger.info(f"Loading GOO prices from {path}")
        df = pd.read_csv(path)
        logger.info(f"Loaded {len(df)} records from GOO prices file")

        missing = [c for c in constants.GOO_REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            logger.error(f"GOO prices file missing required columns: {missing}")
            return None

        df = df[['date', 'product', 'vintage', 'mid']].copy()
        df = df.rename(columns={'mid': 'price'})
        df = df.dropna(subset=['price'])
        df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')

        if universe.products:
            df = df[df['product'].isin(universe.products)]
        df = _filter_by_date_str(df, 'date', universe)

        logger.info(f"GOO prices after filtering: {len(df)} records")
        logger.info(f"GOO products: {df['product'].unique().tolist()}")
        return df

    except pd.errors.ParserError as e:
        logger.warning(f"Error parsing GOO prices file: {e}")
        return None


def _load_boho(path: str, universe: PriceUniverse) -> Optional[pd.DataFrame]:
    try:
        logger.info(f"Loading BOHO prices from {path}")
        df = pd.read_csv(path)
        logger.info(f"Loaded {len(df)} records from BOHO prices file")

        missing = [c for c in constants.BOHO_REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            logger.error(f"BOHO prices file missing required columns: {missing}")
            return None

        df = df[['date', 'product', 'vintage', 'price']].copy()
        df = df.dropna(subset=['price'])
        df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')

        if universe.products:
            df = df[df['product'].isin(universe.products)]
        df = _filter_by_date_str(df, 'date', universe)

        logger.info(f"BOHO prices after filtering: {len(df)} records")
        logger.info(f"BOHO products: {df['product'].unique().tolist()}")
        return df

    except pd.errors.ParserError as e:
        logger.warning(f"Error parsing BOHO prices file: {e}")
        return None


def _load_iso_spot(path: str, iso: str, universe: PriceUniverse) -> Optional[pd.DataFrame]:
    """
    Load and filter an ISO spot price CSV (nyiso, pjm, miso, or caiso).

    All four ISOs share the same raw schema: DATETIME, Hour, Alias, DART_Price.
    Standardized products are '{Alias.strip()} - DART'.  We filter on the raw
    Alias column before the expensive per-row transforms run.
    """
    iso_cfg = constants.ISO_CONFIG.get(iso, {})
    spot_cols = iso_cfg.get('spot_cols', {})
    required_cols = iso_cfg.get('required_spot_cols', [])

    datetime_col = spot_cols.get('datetime', 'DATETIME')
    hour_col = spot_cols.get('hour', 'Hour')
    alias_col = spot_cols.get('alias', 'Alias')
    price_col = spot_cols.get('price', 'DART_Price')
    usecols = [datetime_col, hour_col, alias_col, price_col]

    # Reverse the DART suffix to get the raw Alias values we want
    wanted_aliases: Optional[Set[str]] = None
    if universe.products:
        wanted_aliases = {
            p[: -len(_DART_SUFFIX)] for p in universe.products if p.endswith(_DART_SUFFIX)
        }
        if not wanted_aliases:
            logger.info(f"No {iso.upper()} DART products in universe, skipping {path}")
            return None

    try:
        logger.info(f"Loading {iso.upper()} spot prices from {path}")

        if os.path.getsize(path) > _ISO_SPOT_CHUNK_THRESHOLD_BYTES:
            logger.info(f"Using chunked reading for large {iso.upper()} file")
            chunks = []
            for chunk in pd.read_csv(path, usecols=usecols, chunksize=_ISO_SPOT_CHUNK_SIZE):
                if wanted_aliases is not None:
                    chunk = chunk[chunk[alias_col].astype(str).str.strip().isin(wanted_aliases)]
                chunk = _filter_raw_datetimes(chunk, datetime_col, universe)
                if not chunk.empty:
                    chunks.append(chunk)
            if not chunks:
                logger.info(f"No {iso.upper()} spot prices matched universe after filtering")
                return None
            raw_df = pd.concat(chunks, ignore_index=True)
        else:
            raw_df = pd.read_csv(path, usecols=usecols)
            if wanted_aliases is not None:
                raw_df = raw_df[raw_df[alias_col].astype(str).str.strip().isin(wanted_aliases)]
            raw_df = _filter_raw_datetimes(raw_df, datetime_col, universe)

        logger.info(f"Loaded {len(raw_df)} {iso.upper()} rows after pre-filter")

        missing = [c for c in required_cols if c not in raw_df.columns]
        if missing:
            logger.error(f"{iso.upper()} spot prices file missing required columns: {missing}")
            return None

        if raw_df.empty:
            return pd.DataFrame()

        raw_df = raw_df.copy()
        raw_df['date'] = raw_df[datetime_col].apply(parse_date)

        if iso == 'caiso':
            # CAISO Hour is 1-based; Hour=0 boundary rows are dropped via None
            raw_df['vintage'] = raw_df[hour_col].apply(
                lambda h: f'HE{int(h):02d}' if pd.notna(h) and 1 <= int(h) <= 24 else None
            )
        else:
            raw_df['vintage'] = raw_df[hour_col].apply(convert_hour_to_he_vintage)

        raw_df['product'] = raw_df[alias_col].astype(str).str.strip() + _DART_SUFFIX
        raw_df['price'] = raw_df[price_col].apply(clean_price_string)

        result = raw_df[['date', 'product', 'vintage', 'price']].dropna(
            subset=['date', 'product', 'vintage', 'price']
        )

        logger.info(f"Standardized {iso.upper()} spot prices: {len(result)} DART records")
        logger.info(f"{iso.upper()} products: {sorted(result['product'].unique().tolist()[:10])}")
        logger.info(f"{iso.upper()} vintages: {sorted(result['vintage'].unique().tolist())}")
        return result

    except pd.errors.ParserError as e:
        logger.warning(f"Error parsing {iso.upper()} spot prices file: {e}")
        return None
    except KeyError as e:
        logger.error(f"Error standardizing {iso.upper()} spot prices: {e}")
        return None
    except ValueError as e:
        logger.error(f"Value error standardizing {iso.upper()} spot prices: {e}")
        return None


def _load_forward_curves(
    curve_paths: dict,
    ancillary_path: str,
    universe: PriceUniverse,
) -> Tuple[Optional[pd.DataFrame], Set[Tuple[str, str]]]:
    """Load forward curves and ERCOT ancillary prices, both filtered to universe."""
    products_filter = universe.products if universe.products else None
    min_date = universe.min_date
    max_date = universe.max_date

    if products_filter:
        logger.info(f"Filtering forward curves to {len(products_filter)} traded products")
    logger.info(f"Date range: {min_date.date()} to {max_date.date()}")

    try:
        loader = ForwardCurveLoader()

        logger.info(f"Loading forward curve prices from {len(curve_paths)} curve files...")
        fc_df, fc_vintages = loader.load_all_forward_curves(
            curve_paths,
            products_filter=products_filter,
            min_date=min_date,
            max_date=max_date,
        )

        # Exclude ERCOT ancillary products â€” they come from the dedicated ancillary file
        ancillary_set = set(constants.ERCOT_ANCILLARY_PRODUCTS)
        if fc_df is not None and not fc_df.empty:
            before = len(fc_df)
            fc_df = fc_df[~fc_df['product'].isin(ancillary_set)]
            removed = before - len(fc_df)
            if removed > 0:
                logger.info(
                    f"Removed {removed} ERCOT ancillary rows from forward curve "
                    "(will be replaced by ancillary file)"
                )
            fc_vintages = {(p, v) for (p, v) in fc_vintages if p not in ancillary_set}

        # Load ERCOT ancillary prices
        logger.info(f"Loading ERCOT ancillary prices from {ancillary_path}...")
        anc_df, anc_vintages = loader.load_ercot_ancillary_file(
            ancillary_path,
            products_filter=products_filter,
            min_date=min_date,
            max_date=max_date,
        )

        if anc_df is not None and not anc_df.empty:
            if fc_df is not None and not fc_df.empty:
                fc_df = pd.concat([fc_df, anc_df], ignore_index=True)
            else:
                fc_df = anc_df
            fc_vintages = fc_vintages | anc_vintages
            logger.info(
                f"Added {len(anc_df)} ERCOT ancillary prices "
                f"({len(anc_vintages)} product-vintage pairs)"
            )

        if fc_df is not None and not fc_df.empty:
            fc_df = fc_df.rename(columns={'px': 'price'})
            fc_df['product'] = fc_df['product'].astype('category')
            fc_df['vintage'] = fc_df['vintage'].astype('category')
            logger.info(f"Successfully loaded {len(fc_df)} forward curve prices")
            logger.info(
                f"Identified {len(fc_vintages)} forward curve vintages "
                "for exemption from modeling"
            )
            return fc_df, fc_vintages

        logger.info("No forward curve prices loaded")
        return None, set()

    except OSError as e:
        logger.warning(f"Error loading forward curve prices: {e}")
        logger.warning("Continuing without forward curve prices")
        return None, set()


# ---------------------------------------------------------------------------
# Date-range filter utilities
# ---------------------------------------------------------------------------

def _filter_by_date_str(df: pd.DataFrame, col: str, universe: PriceUniverse) -> pd.DataFrame:
    """Filter rows with a YYYY-MM-DD string date column to [min_date, max_date]."""
    min_s = str(universe.min_date.date())
    max_s = str(universe.max_date.date())
    return df[df[col].astype(str).str[:10].between(min_s, max_s)]


def _filter_raw_datetimes(
    df: pd.DataFrame, datetime_col: str, universe: PriceUniverse
) -> pd.DataFrame:
    """Filter rows with a DATETIME column (may include time) to [min_date, max_date]."""
    min_s = str(universe.min_date.date())
    max_s = str(universe.max_date.date())
    return df[df[datetime_col].astype(str).str[:10].between(min_s, max_s)]

