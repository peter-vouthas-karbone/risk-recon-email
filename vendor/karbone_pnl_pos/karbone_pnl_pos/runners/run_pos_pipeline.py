#!/usr/bin/env python3
"""
Position File Generation Pipeline.

This module provides the orchestrator for generating position files from tradesheets.
It uses the shared TradesConverter to convert tradesheets and the PositionCalculator
to compute cumulative positions.

The workflow includes:
1. Input validation
2. Tradesheet conversion (fuels and power)
3. Position calculation
4. CSV file generation

Usage:
    # From command line
    python run_pos_pipeline.py
    
    # With custom output path
    python run_pos_pipeline.py --output-file positions_20240115.csv
    
    # As a module
    from run_pos_pipeline import PositionPipeline
    pipeline = PositionPipeline()
    success = pipeline.run()
"""

import argparse
import logging
import os
import sys
from datetime import datetime
from typing import Optional

import pandas as pd

from karbone_pnl_pos.utils import constants
from karbone_pnl_pos.core.calculate_pos import PositionCalculator
from karbone_pnl_pos.utils.config_loader import get_config, get_min_trade_date
from karbone_pnl_pos.utils.logging_utils import init_logging
from karbone_pnl_pos.converters.trades_converter import TradesConverter

# Module logger - will be configured by init_logging() in entry point
logger = logging.getLogger('pnl.' + __name__)


class PositionPipeline:
    """
    Orchestrates the position file generation workflow.
    
    This class manages all steps of the position workflow including:
    - Input file validation
    - Tradesheet conversion (using shared TradesConverter)
    - Position calculation (using PositionCalculator)
    - CSV file generation
    
    Attributes:
        config: Configuration loader instance.
        min_trade_date: Minimum date filter for trades.
        timestamp: Timestamp string for output files.
        converter: TradesConverter instance.
        calculator: PositionCalculator instance.
        converted_trades_df: Converted trades DataFrame.
        position_df: Calculated positions DataFrame.
    """
    
    def __init__(
        self,
        min_trade_date: Optional[str] = None,
        output_file: Optional[str] = None
    ) -> None:
        """
        Initialize the position pipeline.
        
        Args:
            min_trade_date: Minimum date for trades (YYYY-MM-DD format).
                          For positions, this is ignored - we need ALL trades
                          to calculate accurate life-to-date cumulative positions.
            output_file: Custom output file path. If None, uses default location.
        """
        self.config = get_config()
        self.min_trade_date = min_trade_date or get_min_trade_date()
        self.timestamp = datetime.now().strftime(constants.DATE_FORMAT_TIMESTAMP)
        
        # Initialize components
        self.converter = TradesConverter(min_trade_date=self.min_trade_date)
        self.calculator = PositionCalculator()
        
        # Data containers
        self.converted_trades_df: Optional[pd.DataFrame] = None
        self.position_df: Optional[pd.DataFrame] = None
        
        # Set up output paths
        results_dir = self.config.get_path('results')
        os.makedirs(results_dir, exist_ok=True)
        
        csv_dir = os.path.join(results_dir, 'csv')
        os.makedirs(csv_dir, exist_ok=True)
        
        # Output file path
        if output_file:
            self.output_file = output_file
        else:
            # Use default naming convention from config
            positions_template = self.config.get_template('positions')
            self.output_file = os.path.join(
                csv_dir,
                positions_template.format(timestamp=self.timestamp)
            )
        
        # Also save converted trades for reference
        converted_trades_pos_template = self.config.get_template('converted_trades_pos')
        self.converted_trades_file = os.path.join(
            csv_dir,
            converted_trades_pos_template.format(timestamp=self.timestamp)
        )
    
    def validate_inputs(self) -> bool:
        """
        Validate the existence and non-emptiness of all required input files.
        
        Returns:
            True if all required inputs are valid, False otherwise.
        """
        return self.converter.validate_inputs()
    
    def convert_tradesheets(self) -> bool:
        """
        Convert raw tradesheets into a standardized trade format.
        
        Returns:
            True if conversion successful, False otherwise.
        """
        try:
            self.converted_trades_df = self.converter.convert_all_tradesheets(
                save_to_file=self.converted_trades_file
            )
            
            if self.converted_trades_df.empty:
                logger.error("No trades in converted DataFrame")
                return False
            
            logger.info(f"Successfully converted {len(self.converted_trades_df)} trades")
            return True
            
        except Exception:
            logger.exception("Error during tradesheet conversion")
            return False
    
    def calculate_positions(self, include_zero_positions: bool = False) -> bool:
        """
        Calculate cumulative positions from converted trades.
        
        Args:
            include_zero_positions: Whether to include zero positions in output.
        
        Returns:
            True if calculation successful, False otherwise.
        """
        try:
            logger.info(f"Calculating positions from {len(self.converted_trades_df)} trades...")
            
            self.position_df = self.calculator.calculate_positions(
                self.converted_trades_df,
                include_zero_positions=include_zero_positions
            )
            
            if self.position_df.empty:
                logger.warning(f"No positions generated from {len(self.converted_trades_df)} input trades")
                return False
            
            # Validate results
            validations = self.calculator.validate_results(self.position_df)
            
            if not all(validations.values()):
                logger.warning("Some validation checks failed:")
                for check, result in validations.items():
                    if not result:
                        logger.warning(f"  Failed: {check}")
            else:
                logger.info("All validation checks passed")
            
            # Get and log summary statistics
            summary = self.calculator.get_position_summary(self.position_df)
            
            logger.info(f"Position calculation completed: {summary['total_records']} records")
            logger.debug(f"  Unique dates: {summary['unique_dates']}")
            logger.debug(f"  Unique desks: {summary['unique_desks']}")
            logger.debug(f"  Unique products: {summary['unique_products']}")
            logger.debug(f"  Unique vintages: {summary['unique_vintages']}")
            logger.debug(f"  Date range: {summary['date_range'][0]} to {summary['date_range'][1]}")
            
            return True
            
        except Exception:
            logger.exception("Error during position calculation")
            return False
    
    def save_position_file(self) -> bool:
        """
        Save the position DataFrame to CSV file.
        
        Saves two copies:
        1. Timestamped file in results/csv directory
        2. Latest copy to the shared positions directory
        
        Returns:
            True if save successful, False otherwise.
        """
        try:
            if self.position_df is None or self.position_df.empty:
                logger.error("No position data to save")
                return False
            
            # Ensure portfolio and strategy columns are empty string not NaN
            self.position_df['portfolio'] = self.position_df['portfolio'].fillna('')
            self.position_df['strategy'] = self.position_df['strategy'].fillna('')
            
            # Save timestamped copy to results directory
            self.position_df.to_csv(self.output_file, index=False)
            logger.info(f"Position file saved to: {self.output_file}")
            
            # Save latest copy to shared positions directory
            latest_positions_path = self.config.get_path('latest_positions')
            if latest_positions_path:
                latest_positions_dir = os.path.dirname(latest_positions_path)
                
                # Ensure directory exists
                os.makedirs(latest_positions_dir, exist_ok=True)
                
                # Save latest copy
                self.position_df.to_csv(latest_positions_path, index=False)
                logger.info(f"Latest position file saved to: {latest_positions_path}")
            else:
                logger.warning("Latest positions path not configured, skipping latest copy save")

            # Save Fuels (RIN desk only) positions to FuelsRisk
            fuels_positions_path = self.config.get_path('fuels_positions')
            if fuels_positions_path:
                fuels_positions_dir = os.path.dirname(fuels_positions_path)
                os.makedirs(fuels_positions_dir, exist_ok=True)
                rin_df = self.position_df[self.position_df['desk'] == constants.DEFAULT_DESK]
                rin_df.to_csv(fuels_positions_path, index=False)
                logger.info(f"Fuels (RIN desk) position file saved to: {fuels_positions_path} ({len(rin_df)} records)")
            
            # Save Power (power_forward desk only) positions to PowerRisk
            power_positions_path = self.config.get_path('power_positions')
            if power_positions_path:
                power_positions_dir = os.path.dirname(power_positions_path)
                os.makedirs(power_positions_dir, exist_ok=True)
                power_df = self.position_df[self.position_df['desk'] == constants.POWER_DESK]
                power_df.to_csv(power_positions_path, index=False)
                logger.info(f"Power (power_forward desk) position file saved to: {power_positions_path} ({len(power_df)} records)")

            # Show sample of output
            logger.debug("Sample of position records:")
            sample_df = self.position_df.head(10)
            for _, row in sample_df.iterrows():
                logger.debug(
                    f"  {row['date']} | {row['desk']:20s} | {row['product']:15s} | "
                    f"{row['vintage']:10s} | {row['position']:>10.2f}"
                )
            
            return True
            
        except Exception:
            logger.exception("Error saving position file")
            return False
    
    def run(self, include_zero_positions: bool = False) -> bool:
        """
        Execute the full position generation workflow.
        
        Sequence:
        1. Input validation
        2. Tradesheet conversion
        3. Position calculation
        4. CSV file generation
        
        Args:
            include_zero_positions: Whether to include zero positions in output.
        
        Returns:
            True if workflow completed successfully, False otherwise.
        """
        logger.info("Starting Position File Generation Pipeline")
        logger.info("=" * 60)
        
        # Step 1: Validate inputs
        if not self.validate_inputs():
            logger.error("Pipeline failed at input validation")
            return False
        
        # Step 2: Convert tradesheets
        if not self.convert_tradesheets():
            logger.error("Pipeline failed at tradesheet conversion")
            return False
        
        # Step 3: Calculate positions
        if not self.calculate_positions(include_zero_positions=include_zero_positions):
            logger.error("Pipeline failed at position calculation")
            return False
        
        # Step 4: Save position file
        if not self.save_position_file():
            logger.error("Pipeline failed at file save")
            return False
        
        # Success
        logger.info("=" * 60)
        logger.info("POSITION PIPELINE COMPLETED SUCCESSFULLY")
        logger.info("=" * 60)
        logger.info("Files saved:")
        logger.info(f"  - Converted Trades: {self.converted_trades_file}")
        logger.info(f"  - Positions (timestamped): {self.output_file}")
        latest_positions_path = self.config.get_path('latest_positions')
        if latest_positions_path:
            logger.info(f"  - Positions (latest): {latest_positions_path}")
        fuels_positions_path = self.config.get_path('fuels_positions')
        if fuels_positions_path:
            logger.info(f"  - Fuels positions (RIN desk): {fuels_positions_path}")
        power_positions_path = self.config.get_path('power_positions')
        if power_positions_path:
            logger.info(f"  - Power positions (power_forward desk): {power_positions_path}")

        return True


def main() -> None:
    """Main entry point for running the position pipeline from the command line."""
    # Initialize logging first
    init_logging(log_prefix='pos')
    
    parser = argparse.ArgumentParser(
        description="Position File Generator - Convert tradesheets to position file"
    )
    parser.add_argument(
        '--output-file',
        type=str,
        default=None,
        help='Custom output file path for positions CSV'
    )
    parser.add_argument(
        '--min-trade-date',
        type=str,
        default=None,
        help='Minimum trade date filter (YYYY-MM-DD format)'
    )
    parser.add_argument(
        '--include-zero-positions',
        action='store_true',
        help='Include zero positions in output (default: excluded)'
    )
    
    args = parser.parse_args()
    
    logger.info("Position File Generation Pipeline")
    logger.info("=" * 50)
    
    if args.min_trade_date:
        logger.info(f"Using custom min_trade_date: {args.min_trade_date}")
    
    if args.output_file:
        logger.info(f"Using custom output file: {args.output_file}")
    
    if args.include_zero_positions:
        logger.info("Zero positions will be included in output")
    else:
        logger.info("Zero positions will be excluded from output (default)")
    
    pipeline = PositionPipeline(
        min_trade_date=args.min_trade_date,
        output_file=args.output_file
    )
    
    try:
        include_zero = args.include_zero_positions
        success = pipeline.run(include_zero_positions=include_zero)
        
        if success:
            logger.info("Position file generated successfully!")
        else:
            logger.error("Position generation failed. Check the logs above.")
            sys.exit(1)
    
    except KeyboardInterrupt:
        logger.warning("Position generation interrupted by user.")
        sys.exit(1)
    except Exception:
        logger.exception("Unexpected error")
        sys.exit(1)


if __name__ == "__main__":
    main()

