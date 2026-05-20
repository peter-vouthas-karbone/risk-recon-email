#!/usr/bin/env python3
"""
Shared Trades Converter Module.

This module provides a reusable TradesConverter class that handles the conversion
of various tradesheet formats into a standardized trade format. It is used by both
the PnL pipeline and the position pipeline to ensure consistency.

The converter supports:
- Fuels tradesheets (RINs)
- Power forward tradesheets
- Power short-term bid records (NYISO, PJM, MISO)
- Additional manual trades
- Desk and product filtering
- Date filtering

Usage:
    from trades_converter import TradesConverter
    
    converter = TradesConverter(min_trade_date='2024-01-01')
    if converter.validate_inputs():
        converted_trades_df = converter.convert_all_tradesheets()
"""

import logging
import os
from typing import List, Optional, Tuple

import pandas as pd

from karbone_pnl_pos.utils import constants
from karbone_pnl_pos.converters.clean_fuels_trades import convert_multiple_rins_tradesheets

# Module logger
logger = logging.getLogger('pnl.' + __name__)
from karbone_pnl_pos.converters.clean_power_forward_trades import convert_power_tradesheet
from karbone_pnl_pos.converters.clean_power_short_term_trades import (
    convert_nyiso_bid_records,
    convert_pjm_bid_records,
    convert_miso_bid_records,
    convert_caiso_bid_records
)
from karbone_pnl_pos.utils.config_loader import (
    get_additional_trades_path,
    get_config,
    get_enabled_desks,
    get_min_trade_date,
    get_product_blacklist,
)
from karbone_pnl_pos.core.reporting import get_effective_reporting_date


class TradesConverter:
    """
    Handles conversion of various tradesheet formats into standardized trade format.
    
    This class extracts and encapsulates the tradesheet conversion logic that was
    previously embedded in ProductionPnLWorkflow, making it reusable by multiple
    pipelines (PnL, position, etc.).
    
    Attributes:
        config: Configuration loader instance.
        min_trade_date: Minimum date filter for trades.
        tradesheet_files: List of valid fuels tradesheet file paths.
        has_power_tradesheet: Whether a power tradesheet exists.
    """
    
    def __init__(self, min_trade_date: Optional[str] = None) -> None:
        """
        Initialize the trades converter.
        
        Args:
            min_trade_date: Minimum date for trades (YYYY-MM-DD format).
                          If None, uses value from configuration.
        """
        self.config = get_config()
        self.min_trade_date = min_trade_date or get_min_trade_date()
        self.tradesheet_files: List[str] = []
        self.has_power_tradesheet: bool = False
        self.has_management_power_tradesheet: bool = False
        
        # Load input file paths from configuration
        self.input_rins_tradesheet = self.config.get_path('rins_tradesheet')
        self.input_management_tradesheet = self.config.get_path('management_rins_tradesheet')
        self.input_power_tradesheet = self.config.get_path('power_tradesheet')
        self.input_management_power_tradesheet = self.config.get_path('management_power_tradesheet')
    
    def validate_inputs(self) -> bool:
        """
        Validate the existence and non-emptiness of all required input files.
        
        Note: This only validates tradesheet files, not price files (which are only
        needed for PnL calculation, not position calculation).
        
        Returns:
            True if all required inputs are valid, False otherwise.
        """
        logger.info("Validating input files...")
        
        tradesheet_files = []
        
        # Check RINs tradesheet (optional)
        if self.input_rins_tradesheet and os.path.exists(self.input_rins_tradesheet):
            if os.path.getsize(self.input_rins_tradesheet) > 0:
                tradesheet_files.append(self.input_rins_tradesheet)
                logger.info(f"Found RINs tradesheet: {self.input_rins_tradesheet}")
            else:
                logger.warning(f"RINs tradesheet is empty: {self.input_rins_tradesheet}")
        
        # Check management tradesheet (optional)
        if self.input_management_tradesheet and os.path.exists(self.input_management_tradesheet):
            if os.path.getsize(self.input_management_tradesheet) > 0:
                tradesheet_files.append(self.input_management_tradesheet)
                logger.info(f"Found management tradesheet: {self.input_management_tradesheet}")
            else:
                logger.warning(f"Management tradesheet is empty: {self.input_management_tradesheet}")
        
        # Check power tradesheet (optional)
        has_power = False
        if self.input_power_tradesheet and os.path.exists(self.input_power_tradesheet):
            if os.path.getsize(self.input_power_tradesheet) > 0:
                has_power = True
                logger.info(f"Found power tradesheet: {self.input_power_tradesheet}")
            else:
                logger.warning(f"Power tradesheet is empty: {self.input_power_tradesheet}")
        
        # Check management power tradesheet (optional)
        has_management_power = False
        if self.input_management_power_tradesheet and os.path.exists(self.input_management_power_tradesheet):
            if os.path.getsize(self.input_management_power_tradesheet) > 0:
                has_management_power = True
                logger.info(f"Found management power tradesheet: {self.input_management_power_tradesheet}")
            else:
                logger.warning(f"Management power tradesheet is empty: {self.input_management_power_tradesheet}")
        
        # At least one tradesheet is required
        if not tradesheet_files and not has_power and not has_management_power:
            logger.error("No valid tradesheet files found")
            return False
        
        self.tradesheet_files = tradesheet_files
        self.has_power_tradesheet = has_power
        self.has_management_power_tradesheet = has_management_power
        
        logger.info(f"Found {len(tradesheet_files)} valid fuels tradesheet file(s)")
        if has_power:
            logger.info("Found power tradesheet")
        if has_management_power:
            logger.info("Found management power tradesheet")
        
        return True
    
    def convert_all_tradesheets(
        self,
        save_to_file: Optional[str] = None
    ) -> pd.DataFrame:
        """
        Convert all tradesheets into a standardized trade format.
        
        This method orchestrates the conversion of all tradesheet types:
        - Fuels tradesheets (RINs)
        - Power tradesheet
        - Management power tradesheet
        - NYISO bid records
        - PJM bid records
        - MISO bid records
        - Additional manual trades
        
        Then applies filters (desk, product, date) and returns the result.
        
        Args:
            save_to_file: Optional path to save the converted trades CSV.
        
        Returns:
            DataFrame with standardized trade records.
        
        Raises:
            ValueError: If no trades are converted from any source.
        """
        all_trades: List[pd.DataFrame] = []
        all_fees: List[pd.DataFrame] = []
        
        # Convert fuels tradesheets
        if self.tradesheet_files:
            logger.info("Converting fuels tradesheets to trades format...")
            
            try:
                # Use temp output file for fuels conversion
                temp_output = save_to_file or 'temp_converted_trades.csv'
                
                trades_df, fees_df = convert_multiple_rins_tradesheets(
                    self.tradesheet_files,
                    temp_output,
                    self.min_trade_date,
                    include_fees=True
                )
                
                if not trades_df.empty:
                    all_trades.append(trades_df)
                    logger.info(f"Converted {len(trades_df)} fuels trade records")
                
                if not fees_df.empty:
                    all_fees.append(fees_df)
                
                # Clean up temp file if we created one
                if not save_to_file and os.path.exists(temp_output):
                    os.remove(temp_output)
                    
            except pd.errors.ParserError as e:
                logger.error(f"Error parsing fuels tradesheets: {e}")
                raise
            except KeyError as e:
                logger.error(f"Missing column in fuels tradesheets: {e}")
                raise
        
        # Convert power tradesheet
        if self.has_power_tradesheet:
            logger.info("Converting power tradesheet to trades format...")
            
            try:
                power_output = 'temp_power_trades.csv'
                
                power_trades_df, power_fees_df = convert_power_tradesheet(
                    self.input_power_tradesheet,
                    power_output,
                    self.min_trade_date,
                    include_fees=True
                )
                
                if not power_trades_df.empty:
                    all_trades.append(power_trades_df)
                    logger.info(f"Converted {len(power_trades_df)} power trade records")
                
                if not power_fees_df.empty:
                    all_fees.append(power_fees_df)
                
                if os.path.exists(power_output):
                    os.remove(power_output)
                    
            except pd.errors.ParserError as e:
                logger.error(f"Error parsing power tradesheet: {e}")
            except KeyError as e:
                logger.error(f"Missing column in power tradesheet: {e}")
        
        # Convert management power tradesheet
        if self.has_management_power_tradesheet:
            logger.info("Converting management power tradesheet to trades format...")
            
            try:
                mgmt_power_output = 'temp_mgmt_power_trades.csv'
                
                mgmt_power_trades_df, mgmt_power_fees_df = convert_power_tradesheet(
                    self.input_management_power_tradesheet,
                    mgmt_power_output,
                    self.min_trade_date,
                    include_fees=True
                )
                
                if not mgmt_power_trades_df.empty:
                    # Override desk to 'mgmt' for all management power trades
                    mgmt_power_trades_df['desk'] = 'mgmt'
                    all_trades.append(mgmt_power_trades_df)
                    logger.info(f"Converted {len(mgmt_power_trades_df)} management power trade records")
                
                if not mgmt_power_fees_df.empty:
                    # Override desk to 'mgmt' for all management power fees
                    mgmt_power_fees_df['desk'] = 'mgmt'
                    all_fees.append(mgmt_power_fees_df)
                
                if os.path.exists(mgmt_power_output):
                    os.remove(mgmt_power_output)
                    
            except pd.errors.ParserError as e:
                logger.error(f"Error parsing management power tradesheet: {e}")
            except KeyError as e:
                logger.error(f"Missing column in management power tradesheet: {e}")
        
        # Convert NYISO bid records
        nyiso_bid_records = self.config.get_path('nyiso_bid_records')
        nyiso_spot_pricing = self.config.get_path('nyiso_spot_pricing')
        if nyiso_bid_records and os.path.exists(nyiso_bid_records):
            if not nyiso_spot_pricing or not os.path.exists(nyiso_spot_pricing):
                logger.warning("NYISO bid records found but marks file (nyiso_spot_pricing) not found or missing")
            else:
                logger.info("Converting NYISO bid records to trades format...")
                try:
                    nyiso_trades_df = convert_nyiso_bid_records(
                        nyiso_bid_records,
                        nyiso_spot_pricing,
                        self.min_trade_date
                    )
                    
                    if not nyiso_trades_df.empty:
                        all_trades.append(nyiso_trades_df)
                        logger.info(f"Converted {len(nyiso_trades_df)} NYISO bid record trades")
                except pd.errors.ParserError as e:
                    logger.error(f"Error parsing NYISO bid records: {e}")
                except KeyError as e:
                    logger.error(f"Missing column in NYISO bid records: {e}")
                except Exception as e:
                    raise
        
        # Convert PJM bid records
        pjm_bid_records = self.config.get_path('pjm_bid_records')
        pjm_spot_pricing = self.config.get_path('pjm_spot_pricing')
        if pjm_bid_records and os.path.exists(pjm_bid_records):
            if not pjm_spot_pricing or not os.path.exists(pjm_spot_pricing):
                logger.warning("PJM bid records found but marks file (pjm_spot_pricing) not found or missing")
            else:
                logger.info("Converting PJM bid records to trades format...")
                try:
                    pjm_trades_df = convert_pjm_bid_records(
                        pjm_bid_records,
                        pjm_spot_pricing,
                        self.min_trade_date
                    )
                    
                    if not pjm_trades_df.empty:
                        all_trades.append(pjm_trades_df)
                        logger.info(f"Converted {len(pjm_trades_df)} PJM bid record trades")
                except pd.errors.ParserError as e:
                    logger.error(f"Error parsing PJM bid records: {e}")
                except KeyError as e:
                    logger.error(f"Missing column in PJM bid records: {e}")
                except Exception as e:
                    raise
        
        # Convert MISO bid records
        miso_bid_records = self.config.get_path('miso_bid_records')
        miso_spot_pricing = self.config.get_path('miso_spot_pricing')
        if miso_bid_records and os.path.exists(miso_bid_records):
            if not miso_spot_pricing or not os.path.exists(miso_spot_pricing):
                logger.warning("MISO bid records found but marks file (miso_spot_pricing) not found or missing")
            else:
                logger.info("Converting MISO bid records to trades format...")
                try:
                    miso_trades_df = convert_miso_bid_records(
                        miso_bid_records,
                        miso_spot_pricing,
                        self.min_trade_date
                    )

                    if not miso_trades_df.empty:
                        all_trades.append(miso_trades_df)
                        logger.info(f"Converted {len(miso_trades_df)} MISO bid record trades")
                except pd.errors.ParserError as e:
                    logger.error(f"Error parsing MISO bid records: {e}")
                except KeyError as e:
                    logger.error(f"Missing column in MISO bid records: {e}")
                except Exception as e:
                    raise

        # Convert CAISO bid records
        caiso_bid_records = self.config.get_path('caiso_bid_records')
        caiso_spot_pricing = self.config.get_path('caiso_spot_pricing')
        if caiso_bid_records and os.path.exists(caiso_bid_records):
            if not caiso_spot_pricing or not os.path.exists(caiso_spot_pricing):
                logger.warning("CAISO bid records found but marks file (caiso_spot_pricing) not found or missing")
            else:
                logger.info("Converting CAISO bid records to trades format...")
                try:
                    caiso_trades_df = convert_caiso_bid_records(
                        caiso_bid_records,
                        caiso_spot_pricing,
                        self.min_trade_date
                    )

                    if not caiso_trades_df.empty:
                        all_trades.append(caiso_trades_df)
                        logger.info(f"Converted {len(caiso_trades_df)} CAISO bid record trades")
                except pd.errors.ParserError as e:
                    logger.error(f"Error parsing CAISO bid records: {e}")
                except KeyError as e:
                    logger.error(f"Missing column in CAISO bid records: {e}")
                except Exception as e:
                    raise
        
        if not all_trades:
            raise ValueError("No trades converted from any source")
        
        converted_trades_df = pd.concat(all_trades, ignore_index=True)
        
        # Store fee information for reporting
        fees_df = pd.DataFrame()
        if all_fees:
            fees_df = pd.concat(all_fees, ignore_index=True)
        
        # Count trade types
        if not converted_trades_df.empty:
            is_fee_col = converted_trades_df.get('is_fee', pd.Series([False]))
            fee_trades = converted_trades_df[is_fee_col == True]
            regular_trades = converted_trades_df[is_fee_col != True]
            
            logger.info(f"Regular trades: {len(regular_trades)}")
            logger.info(f"Fee trades: {len(fee_trades)}")
        
        # Verify date filtering
        converted_trades_df['date'] = pd.to_datetime(converted_trades_df['date'])
        cutoff_date = pd.to_datetime(self.min_trade_date)
        early_trades = converted_trades_df[converted_trades_df['date'] < cutoff_date]
        
        if len(early_trades) > 0:
            logger.warning(f"Found {len(early_trades)} trades before {self.min_trade_date}")
        
        logger.info(f"Total output: {len(converted_trades_df)} trade records")
        logger.info(
            f"Date range: {converted_trades_df['date'].min()} "
            f"to {converted_trades_df['date'].max()}"
        )
        
        # Convert date back to string format for consistency
        converted_trades_df['date'] = converted_trades_df['date'].dt.strftime('%Y-%m-%d')
        
        # Save if requested
        if save_to_file:
            converted_trades_df.to_csv(save_to_file, index=False)
            logger.info(f"Saved combined trades to {save_to_file}")
        
        # Load and merge additional trades
        converted_trades_df = self._load_and_merge_additional_trades(converted_trades_df)
        
        # Apply filters
        converted_trades_df = self.apply_filters(converted_trades_df)
        
        return converted_trades_df
    
    def _load_and_merge_additional_trades(
        self,
        converted_trades_df: pd.DataFrame
    ) -> pd.DataFrame:
        """
        Load additional trades from CSV file and merge with converted trades.
        
        Args:
            converted_trades_df: Existing converted trades DataFrame.
        
        Returns:
            DataFrame with additional trades merged in.
        """
        additional_trades_path = get_additional_trades_path()
        
        if not os.path.exists(additional_trades_path):
            logger.info(f"No additional trades file found at {additional_trades_path}")
            return converted_trades_df
        
        try:
            logger.info(f"Loading additional trades from {additional_trades_path}")
            
            additional_df = pd.read_csv(additional_trades_path)
            
            if additional_df.empty:
                logger.info("Additional trades file is empty")
                return converted_trades_df
            
            logger.info(f"Loaded {len(additional_df)} additional trade records")
            
            standardized_additional = self._standardize_additional_trades(additional_df)
            
            if standardized_additional.empty:
                logger.warning("No valid additional trades after standardization")
                return converted_trades_df
            
            # Apply date filter
            if self.min_trade_date:
                cutoff_date = pd.to_datetime(self.min_trade_date)
                standardized_additional['date'] = pd.to_datetime(standardized_additional['date'])
                before_cutoff = standardized_additional[
                    standardized_additional['date'] < cutoff_date
                ]
                if len(before_cutoff) > 0:
                    logger.info(
                        f"Filtering out {len(before_cutoff)} additional trades "
                        f"before {self.min_trade_date}"
                    )
                    standardized_additional = standardized_additional[
                        standardized_additional['date'] >= cutoff_date
                    ]
                standardized_additional['date'] = (
                    standardized_additional['date'].dt.strftime('%Y-%m-%d')
                )
            
            # Merge
            original_count = len(converted_trades_df)
            converted_trades_df = pd.concat(
                [converted_trades_df, standardized_additional],
                ignore_index=True
            )
            
            added_count = len(standardized_additional)
            
            logger.info(f"Added {added_count} additional trades")
            logger.info(
                f"Total trades after merge: {len(converted_trades_df)} (was {original_count})"
            )
            
            return converted_trades_df
            
        except FileNotFoundError:
            logger.warning(f"Additional trades file not found: {additional_trades_path}")
            return converted_trades_df
        except pd.errors.ParserError as e:
            logger.error(f"Error parsing additional trades CSV: {e}")
            return converted_trades_df
    
    def _standardize_additional_trades(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Standardize additional trades DataFrame to match converted trades format.
        
        Args:
            df: Raw additional trades DataFrame.
        
        Returns:
            DataFrame with standardized column names and data types.
        """
        if df.empty:
            return pd.DataFrame()
        
        try:
            standardized_df = df.copy()
            
            # Clean quantity column
            if 'quantity' in standardized_df.columns:
                standardized_df['quantity'] = (
                    standardized_df['quantity']
                    .astype(str)
                    .str.replace(',', '')
                    .astype(float)
                )
            
            # Parse dates
            if 'date' in standardized_df.columns:
                standardized_df['date'] = (
                    pd.to_datetime(standardized_df['date']).dt.strftime('%Y-%m-%d')
                )
            
            # Ensure required columns with defaults from constants
            for col, default_value in constants.ADDITIONAL_TRADES_REQUIRED_COLUMNS.items():
                if col not in standardized_df.columns:
                    standardized_df[col] = default_value
            
            # Select columns in correct order
            final_columns = list(constants.ADDITIONAL_TRADES_REQUIRED_COLUMNS.keys())
            standardized_df = standardized_df[final_columns]
            
            # Filter invalid rows
            essential_mask = (
                standardized_df['date'].notna() &
                standardized_df['product'].notna() &
                standardized_df['vintage'].notna() &
                (standardized_df['quantity'] != 0)
            )
            
            standardized_df = standardized_df[essential_mask]
            
            logger.info(f"Standardized {len(standardized_df)} additional trades")
            
            return standardized_df
            
        except KeyError as e:
            logger.error(f"Missing column standardizing additional trades: {e}")
            return pd.DataFrame()
    
    def apply_filters(self, trades_df: pd.DataFrame) -> pd.DataFrame:
        """
        Apply desk and product filters to the converted trades DataFrame.
        
        Filters out trades that don't match enabled desks or are in the product blacklist.
        Also filters out trades with future dates (dates > today).
        
        Args:
            trades_df: Converted trades DataFrame.
        
        Returns:
            Filtered DataFrame.
        """
        if trades_df.empty:
            return trades_df
        
        original_count = len(trades_df)
        logger.info("Applying desk and product filters...")
        
        # Filter out future-dated trades first (using effective reporting date)
        effective_date = get_effective_reporting_date()
        trades_df = trades_df.copy()  # Avoid SettingWithCopyWarning
        trades_df['date'] = pd.to_datetime(trades_df['date'])
        future_trades = trades_df[
            trades_df['date'].dt.date > effective_date
        ]
        if len(future_trades) > 0:
            logger.info(
                f"Filtering out {len(future_trades)} trades with future dates (after {effective_date})"
            )
            trades_df = trades_df[
                trades_df['date'].dt.date <= effective_date
            ].copy()
        # Convert back to string format for consistency
        trades_df['date'] = trades_df['date'].dt.strftime('%Y-%m-%d')
        
        enabled_desks = get_enabled_desks()
        product_blacklist = get_product_blacklist()
        
        if enabled_desks:
            logger.info(f"Filtering to enabled desks: {enabled_desks}")
            trades_df = trades_df[
                trades_df['desk'].isin(enabled_desks)
            ]
            logger.info(f"Filtered to {len(trades_df)} trades from enabled desks")
        else:
            logger.debug("No desk filter specified - including all desks")
        
        if product_blacklist:
            logger.info(f"Excluding blacklisted products: {product_blacklist}")
            before_blacklist = len(trades_df)
            trades_df = trades_df[
                ~trades_df['product'].isin(product_blacklist)
            ]
            filtered_count = before_blacklist - len(trades_df)
            if filtered_count > 0:
                logger.info(f"Excluded {filtered_count} trades with blacklisted products")
        else:
            logger.debug("No product blacklist specified - including all products")
        
        final_count = len(trades_df)
        filtered_total = original_count - final_count
        if filtered_total > 0:
            logger.info(
                f"Total trades after filtering: {final_count} (filtered out {filtered_total})"
            )
        
        return trades_df

