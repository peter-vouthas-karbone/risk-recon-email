#!/usr/bin/env python3
"""
Production PnL Analysis Workflow.

This module provides the complete end-to-end workflow for converting tradesheets,
computing daily PnL, and generating reports with validation. It orchestrates all
components of the PnL system.

The workflow includes:
1. Input validation
2. Tradesheet conversion (fuels and power)
3. Price data loading and combination
4. PnL calculation
5. Report generation (text and HTML)
6. Email notifications (optional)

Usage:
    # From command line
    python run_pnl_pipeline.py --send-email

    # As a module
    from run_pnl_pipeline import ProductionPnLWorkflow
    workflow = ProductionPnLWorkflow(send_email=True)
    success = workflow.run()
"""

import argparse
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd

try:
    from zoneinfo import ZoneInfo
except ImportError:
    # Fallback for Python < 3.9
    from backports.zoneinfo import ZoneInfo  # type: ignore

from karbone_pnl_pos.utils import constants
from karbone_pnl_pos.core.calculate_pnl import DailyPnLCalculator
from karbone_pnl_pos.utils.config_loader import (
    get_config,
    get_email_config,
    get_granularity_config,
    get_min_trade_date,
)
from karbone_pnl_pos.prices.load_prices import PriceUniverse, load_all_prices
from karbone_pnl_pos.prices.prepare_ercot_ancillary import prepare_ercot_ancillary
from karbone_pnl_pos.utils.logging_utils import init_logging
from karbone_pnl_pos.pipeline.email_sender import EmailSender
from karbone_pnl_pos.core.reporting import PnLReporter, get_effective_reporting_date, save_last_report_date
from karbone_pnl_pos.converters.trades_converter import TradesConverter

# Module logger - will be configured by init_logging() in entry point
logger = logging.getLogger('pnl.' + __name__)


@dataclass
class WorkflowResults:
    """
    Structured container for workflow results with type safety.

    Attributes:
        trades_converted: Total number of trades converted from tradesheets.
        trades_loaded: Number of trades loaded into calculator.
        prices_loaded: Number of price records loaded.
        pnl_records: Number of PnL records generated.
        date_range: Tuple of (min_date, max_date) for PnL data.
        products: Number of unique products.
        vintages: Number of unique vintages.
        desks: Number of unique desks.
        total_pnl: Total trading PnL in USD.
        mtm_pnl: Mark-to-market PnL component.
        buy_pnl: Buy trade PnL component.
        sell_pnl: Sell trade PnL component.
        modeled_prices: Number of prices generated via modeling.
        validation_results: Dictionary of validation check results.
    """

    trades_converted: int = 0
    trades_loaded: int = 0
    prices_loaded: int = 0
    pnl_records: int = 0
    date_range: Tuple[Any, Any] = (None, None)
    products: int = 0
    vintages: int = 0
    desks: int = 0
    total_pnl: float = 0.0
    mtm_pnl: float = 0.0
    buy_pnl: float = 0.0
    sell_pnl: float = 0.0
    modeled_prices: int = 0
    validation_results: Dict[str, bool] = field(default_factory=dict)


class ProductionPnLWorkflow:
    """
    Manages the end-to-end PnL analysis workflow.

    This class orchestrates all steps of the PnL workflow including:
    - Input file validation
    - Tradesheet conversion
    - Price data loading
    - PnL calculation
    - Report generation
    - Email notifications

    Attributes:
        config: Configuration loader instance.
        min_trade_date: Minimum date filter for trades.
        send_email: Whether to send email notifications.
        timestamp: Timestamp string for output files.
        results: WorkflowResults container.
        reporter: PnLReporter instance (set after PnL computation).
    """

    def __init__(
        self,
        min_trade_date: Optional[str] = None,
        send_email: bool = False,
        include_weighted_avg_prices: Optional[bool] = None,
    ) -> None:
        """
        Initialize the workflow.

        Args:
            min_trade_date: Minimum date for trades (YYYY-MM-DD format).
                          If None, uses value from configuration.
            send_email: Whether to send email notifications after completion.
            include_weighted_avg_prices: Override config flag for outputting
                px_wtd_avg_buy/sell/start/end columns. None defers to config.
        """
        self.config = get_config()
        self.min_trade_date = min_trade_date or get_min_trade_date()
        self.send_email = send_email
        self.include_weighted_avg_prices = include_weighted_avg_prices
        self.timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.results = WorkflowResults()
        self.reporter: Optional[PnLReporter] = None
        self.forward_curve_vintages: Set[Tuple[str, str]] = set()
        self.converted_trades_df: Optional[pd.DataFrame] = None
        self.pnl_df: Optional[pd.DataFrame] = None
        
        # Initialize shared trades converter
        self.trades_converter = TradesConverter(min_trade_date=self.min_trade_date)

        self.results_dir = self.config.get_path('results')

        os.makedirs(self.results_dir, exist_ok=True)

        # Create subdirectories for organized output
        self.csv_dir = os.path.join(self.results_dir, 'csv')
        self.html_dir = os.path.join(self.results_dir, 'html')
        self.txt_dir = os.path.join(self.results_dir, 'txt')
        
        os.makedirs(self.csv_dir, exist_ok=True)
        os.makedirs(self.html_dir, exist_ok=True)
        os.makedirs(self.txt_dir, exist_ok=True)

        # Set up output file paths
        self.converted_trades = os.path.join(
            self.csv_dir,
            self.config.get_template('converted_trades').format(timestamp=self.timestamp)
        )
        self.pnl_results = os.path.join(
            self.csv_dir,
            self.config.get_template('daily_pnl_results').format(timestamp=self.timestamp)
        )
        self.pricing_data = os.path.join(
            self.csv_dir,
            self.config.get_template('pricing_data').format(timestamp=self.timestamp)
        )
        self.summary_report = os.path.join(
            self.txt_dir,
            self.config.get_template('summary_report').format(timestamp=self.timestamp)
        )
        self.html_report = os.path.join(
            self.html_dir,
            f'pnl_report_{self.timestamp}.html'
        )
        self.position_html_report = os.path.join(
            self.html_dir,
            f'volumetric_exposure_report_{self.timestamp}.html'
        )

    def validate_inputs(self) -> bool:
        """
        Validate the existence and non-emptiness of all required input files.

        Returns:
            True if all required inputs are valid, False otherwise.
        """
        if not self.trades_converter.validate_inputs():
            return False

        rin_prices = self.config.get_path('rin_prices')
        if not os.path.exists(rin_prices):
            logger.error(f"Missing required file: {rin_prices}")
            return False
        rin_size = os.path.getsize(rin_prices)
        if rin_size == 0:
            logger.error(f"File is empty: {rin_prices}")
            return False
        logger.info(f"File size: {rin_size:,} bytes - {rin_prices}")

        return True

    def convert_tradesheet(self) -> bool:
        """
        Convert raw tradesheets into a standardized trade format.

        Uses the shared TradesConverter to handle all tradesheet types.
        Filters trades by date, processes associated fees, and stores the
        resulting DataFrame in memory for subsequent steps.

        Returns:
            True if conversion successful, False otherwise.
        """
        try:
            # Use shared converter to convert all tradesheets
            self.converted_trades_df = self.trades_converter.convert_all_tradesheets(
                save_to_file=self.converted_trades
            )
            
            if self.converted_trades_df.empty:
                logger.error("No trades converted from any source")
                return False

            self.results.trades_converted = len(self.converted_trades_df)
            return True
            
        except Exception:
            logger.exception("Error during tradesheet conversion")
            return False

    def compute_pnl(self) -> bool:
        """
        Compute daily PnL using the in-memory converted trades DataFrame.

        Loads market prices, calculates PnL, validates results, and saves output.

        Returns:
            True if computation successful, False otherwise.
        """
        logger.info(f"Computing daily PnL for {len(self.converted_trades_df)} trades...")

        try:
            pnl_config = self.config.get_pnl_config()
            desk_overrides = pnl_config.get('desk_overrides', {})
            calculator = DailyPnLCalculator(
                forward_fill_allowed=pnl_config.get('forward_fill_allowed', True),
                missing_pnl_policy=pnl_config.get('missing_pnl_policy', 'null'),
                enable_vintage_modeling=pnl_config.get('enable_vintage_modeling', True),
                use_trade_price_fallback=pnl_config.get('use_trade_price_fallback', True),
                desk_overrides=desk_overrides,
                include_weighted_avg_prices=self.include_weighted_avg_prices,
            )

            universe = PriceUniverse.from_trades(self.converted_trades_df)
            prices_df, self.forward_curve_vintages = load_all_prices(self.config, universe)

            calculator.load_data(
                self.converted_trades_df,
                prices_df,
                forward_curve_vintages=self.forward_curve_vintages
            )
            self.results.trades_loaded = len(calculator.trades_df)
            self.results.prices_loaded = len(calculator.prices_df)

            pnl_df = calculator.compute_daily_pnl()

            if len(pnl_df) == 0:
                logger.error("No PnL records generated")
                return False

            validations = calculator.validate_results(pnl_df)
            self.results.validation_results = validations

            if not all(validations.values()):
                failed_count = sum(1 for v in validations.values() if not v)
                logger.warning(f"Some validation checks failed ({failed_count}/{len(validations)})")
                for check, result in validations.items():
                    if not result:
                        logger.warning(f"  Failed: {check}")
            else:
                logger.info("All validation checks passed")

            pnl_df_rounded = calculator.get_rounded_results(pnl_df)
            pnl_df_rounded.to_csv(self.pnl_results, index=False)
            logger.info(f"PnL file saved to: {self.pnl_results}")

            latest_pnl_path = self.config.get_path('latest_pnl')
            if latest_pnl_path:
                os.makedirs(os.path.dirname(latest_pnl_path), exist_ok=True)
                pnl_df_rounded.to_csv(latest_pnl_path, index=False)
                logger.info(f"Latest PnL file saved to: {latest_pnl_path}")
            else:
                logger.warning("Latest PnL path not configured, skipping latest copy save")

            pricing_df_rounded = calculator.get_rounded_pricing_data()
            pricing_df_rounded.to_csv(self.pricing_data, index=False)

            self.pnl_df = pnl_df_rounded

            # Store statistics
            self.results.pnl_records = len(pnl_df)
            self.results.date_range = (pnl_df['date'].min(), pnl_df['date'].max())
            self.results.products = pnl_df['product'].nunique()
            self.results.vintages = pnl_df['vintage'].nunique()
            self.results.desks = pnl_df['desk'].nunique()
            self.results.total_pnl = pnl_df['usd_pnl_trading'].sum()
            self.results.mtm_pnl = pnl_df['pnl_mtm'].sum()
            self.results.buy_pnl = pnl_df['pnl_buy'].sum()
            self.results.sell_pnl = pnl_df['pnl_sell'].sum()

            modeled_summary = calculator.get_modeled_price_summary()
            self.results.modeled_prices = modeled_summary.get('total_modeled', 0)

            logger.info(f"Generated {len(pnl_df)} PnL records")
            logger.info(f"Total Trading PnL: ${self.results.total_pnl:,.2f}")

            # Log PnL by year/desk
            pnl_by_year_desk = self._calculate_pnl_by_year_desk(pnl_df_rounded)
            if not pnl_by_year_desk.empty:
                logger.debug("=== PnL by Year by Desk ===")
                for _, row in pnl_by_year_desk.iterrows():
                    logger.debug(
                        f"  {int(row['year'])} - {row['desk']}: "
                        f"${row['usd_pnl_trading']:,.2f}"
                    )

            # Log most recent day PnL
            most_recent_pnl = self._get_most_recent_day_pnl(pnl_df_rounded)
            if not most_recent_pnl.empty:
                most_recent_date = most_recent_pnl['date'].iloc[0]
                total_recent_pnl = most_recent_pnl['usd_pnl_trading'].sum()
                logger.debug(f"=== Most Recent Day PnL ({most_recent_date}) ===")
                logger.debug(f"  Total: ${total_recent_pnl:,.2f}")

                recent_by_desk = most_recent_pnl.groupby('desk')['usd_pnl_trading'].sum().reset_index()
                for _, row in recent_by_desk.iterrows():
                    logger.debug(f"  {row['desk']}: ${row['usd_pnl_trading']:,.2f}")

            return True

        except FileNotFoundError as e:
            logger.error(f"Input file not found: {e}")
            return False
        except PermissionError as e:
            logger.error(f"Permission denied: {e}")
            return False
        except pd.errors.EmptyDataError as e:
            logger.error(f"Empty data encountered: {e}")
            return False
        except pd.errors.ParserError as e:
            logger.error(f"Data parsing error: {e}")
            return False
        except KeyError as e:
            logger.error(f"Missing required column: {e}")
            return False
        except ValueError as e:
            logger.error(f"Data validation error: {e}")
            return False

    def _calculate_pnl_by_year_desk(self, pnl_df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate total PnL by year and desk.

        Args:
            pnl_df: DataFrame containing PnL calculations.

        Returns:
            DataFrame with PnL aggregated by year and desk.
        """
        if pnl_df.empty:
            return pd.DataFrame()

        year = pd.to_datetime(pnl_df['date']).dt.year.rename('year')
        pnl_by_year_desk = (
            pnl_df.groupby([year, 'desk'])['usd_pnl_trading']
            .sum()
            .reset_index()
            .sort_values(['year', 'desk'], ascending=[False, True])
        )
        return pnl_by_year_desk

    def _get_most_recent_day_pnl(self, pnl_df: pd.DataFrame) -> pd.DataFrame:
        """
        Get the most recent day's PnL.

        Args:
            pnl_df: DataFrame containing PnL calculations.

        Returns:
            DataFrame with the most recent day's PnL data.
        """
        if pnl_df.empty:
            return pd.DataFrame()

        most_recent_date = pnl_df['date'].max()
        return pnl_df[pnl_df['date'] == most_recent_date].sort_values(['desk', 'product', 'vintage'])

    def _extract_positions_from_pnl(self) -> bool:
        """
        Extract end-of-day positions from the computed PnL DataFrame and save position files.

        Reads qty_end from self.pnl_df, renames it to position, and saves the same
        output files that the standalone position pipeline produces. The power_short_term
        desk is excluded because those records are not tracked in position files.

        Returns:
            True if extraction and save succeeded, False otherwise.
        """
        try:
            if self.pnl_df is None or self.pnl_df.empty:
                logger.error("No PnL data available to extract positions from")
                return False

            pos_df = (
                self.pnl_df[self.pnl_df['desk'] != constants.POWER_SHORT_TERM_DESK]
                [['date', 'desk', 'portfolio', 'strategy', 'product', 'vintage', 'qty_end']]
                .rename(columns={'qty_end': 'position'})
                .copy()
            )

            pos_df = pos_df[pos_df['position'].abs() > constants.EPSILON]
            pos_df['portfolio'] = pos_df['portfolio'].fillna('')
            pos_df['strategy'] = pos_df['strategy'].fillna('')
            pos_df = pos_df.sort_values(
                ['date', 'desk', 'portfolio', 'strategy', 'product', 'vintage']
            ).reset_index(drop=True)

            positions_template = self.config.get_template('positions')
            positions_file = os.path.join(
                self.csv_dir,
                positions_template.format(timestamp=self.timestamp)
            )
            pos_df.to_csv(positions_file, index=False)
            logger.info(f"Position file saved to: {positions_file}")

            latest_positions_path = self.config.get_path('latest_positions')
            if latest_positions_path:
                os.makedirs(os.path.dirname(latest_positions_path), exist_ok=True)
                pos_df.to_csv(latest_positions_path, index=False)
                logger.info(f"Latest position file saved to: {latest_positions_path}")

            fuels_positions_path = self.config.get_path('fuels_positions')
            if fuels_positions_path:
                os.makedirs(os.path.dirname(fuels_positions_path), exist_ok=True)
                rin_df = pos_df[pos_df['desk'] == constants.DEFAULT_DESK]
                rin_df.to_csv(fuels_positions_path, index=False)
                logger.info(
                    f"Fuels position file saved to: {fuels_positions_path} ({len(rin_df)} records)"
                )

            power_positions_path = self.config.get_path('power_positions')
            if power_positions_path:
                os.makedirs(os.path.dirname(power_positions_path), exist_ok=True)
                power_df = pos_df[pos_df['desk'] == constants.POWER_DESK]
                power_df.to_csv(power_positions_path, index=False)
                logger.info(
                    f"Power position file saved to: {power_positions_path} ({len(power_df)} records)"
                )

            logger.info(
                f"Positions extracted: {len(pos_df)} records "
                f"({pos_df['desk'].nunique()} desks, "
                f"{pos_df['product'].nunique()} products)"
            )
            return True

        except Exception:
            logger.exception("Error extracting positions from PnL data")
            return False

    def send_email_notifications_from_settings(
        self,
        generated_reports: Dict[str, List[Dict[str, Any]]],
        email_config: Dict[str, Any]
    ) -> bool:
        """
        Send emails for all enabled PnL and position reports defined in settings.yaml.
        """
        if not self.send_email:
            logger.info("Email notifications not requested via command line")
            return True

        email_sender = EmailSender(email_config=email_config)

        if not email_sender.is_enabled():
            logger.info("Email notifications are disabled in configuration")
            return True

        report_date = (
            str(self.results.date_range[1])
            if self.results.date_range and self.results.date_range[1]
            else datetime.now().strftime('%Y-%m-%d')
        )

        pnl_reports_cfg = email_config.get('pnl_reports', [])
        position_reports_cfg = email_config.get('position_reports', [])

        if not pnl_reports_cfg and not position_reports_cfg:
            logger.info("No pnl_reports/position_reports configured; skipping email sends")
            return True

        success = email_sender.send_reports_from_settings(
            pnl_reports=pnl_reports_cfg,
            position_reports=position_reports_cfg,
            generated=generated_reports,
            report_date=report_date
        )

        if success:
            logger.info("All configured report emails sent successfully")
            try:
                report_date_obj = datetime.strptime(report_date.split()[0], '%Y-%m-%d').date()
                save_last_report_date(report_date_obj)
            except (ValueError, AttributeError, TypeError) as e:
                logger.warning(f"Could not save last report date '{report_date}': {e}. Using effective reporting date.")
                try:
                    save_last_report_date(get_effective_reporting_date())
                except Exception as e2:
                    logger.error(f"Failed to save effective reporting date as fallback: {e2}")
        else:
            logger.warning("Some report emails failed to send")
        return success

    def run(self) -> bool:
        """
        Execute the full PnL analysis workflow in sequential order.

        Sequence:
        1. Input validation
        2. Tradesheet conversion
        3. PnL computation
        4. Summary report generation
        5. HTML report generation
        6. Email notifications (optional)
        7. Position file generation

        Returns:
            True if workflow completed successfully, False otherwise.
        """
        logger.info("Starting Production PnL Analysis Workflow")
        logger.info("=" * 60)

        # Step 1: Validate inputs
        if not self.validate_inputs():
            logger.error("Workflow failed at input validation")
            return False

        # Step 2: Convert tradesheet
        if not self.convert_tradesheet():
            logger.error("Workflow failed at tradesheet conversion")
            return False

        # Step 2b: Build combined ERCOT ancillary price file before marks are loaded
        ercot_fwd_path = self.config.get_path('ercot_forward_curve')
        karbone_src_path = self.config.get_path('ercot_ancillary_karbone_source')
        ancillary_out_path = self.config.get_path('ercot_ancillary_prices')
        if not prepare_ercot_ancillary(ercot_fwd_path, karbone_src_path, ancillary_out_path):
            logger.warning(
                "ERCOT ancillary preparation failed — pipeline will attempt to "
                "continue with any existing ancillary price file"
            )

        # Step 3: Compute PnL
        if not self.compute_pnl():
            logger.error("Workflow failed at PnL computation")
            return False

        # Step 4: Generate reports
        granularity = get_granularity_config()
        self.reporter = PnLReporter(
            pnl_df=self.pnl_df,
            results=self.results,
            min_trade_date=self.min_trade_date,
            summary_report_path=self.summary_report,
            html_report_path=self.html_report,
            position_report_path=self.position_html_report,
            granularity=granularity,
            converted_trades_df=self.converted_trades_df
        )

        email_config = get_email_config()
        pnl_reports_cfg = email_config.get('pnl_reports', [])
        position_reports_cfg = email_config.get('position_reports', [])

        if not self.reporter.generate_summary_report():
            logger.error("Workflow failed at report generation")
            return False

        # Step 5: Generate HTML report
        if not self.reporter.generate_html_report():
            logger.warning("HTML report generation failed, but continuing with workflow")

        if not self.reporter.generate_position_html_report():
            logger.warning("Volumetric exposure report generation failed, but continuing")

        # Generate per-config reports and send emails (new settings structure)
        generated_reports = self.reporter.generate_reports_from_settings(
            pnl_reports=pnl_reports_cfg,
            position_reports=position_reports_cfg,
            results_dir=self.results_dir,
            timestamp=self.timestamp
        )

        # Step 6: Send email notifications
        if not self.send_email_notifications_from_settings(generated_reports, email_config):
            logger.warning("Some email notifications failed, but continuing with workflow completion")

        # Step 7: Extract positions from PnL data and save position files
        if not self._extract_positions_from_pnl():
            logger.warning("Position extraction failed, but PnL workflow completed successfully")

        # Success
        logger.info("=" * 60)
        logger.info("PRODUCTION WORKFLOW COMPLETED SUCCESSFULLY")
        logger.info("=" * 60)
        logger.info("Results saved to:")
        logger.info(f"  - Trades: {self.converted_trades}")
        logger.info(f"  - PnL (timestamped): {self.pnl_results}")
        latest_pnl_path = self.config.get_path('latest_pnl')
        if latest_pnl_path:
            logger.info(f"  - PnL (latest): {latest_pnl_path}")
        logger.info(f"  - Pricing: {self.pricing_data}")
        logger.info(f"  - Text Report: {self.summary_report}")
        logger.info(f"  - HTML Report: {self.html_report}")

        return True


def main() -> None:
    """Main entry point for running the workflow from the command line."""
    # Initialize logging first
    init_logging(log_prefix='pnl')
    
    parser = argparse.ArgumentParser(
        description="RINs Daily PnL Analysis - Production Workflow"
    )
    parser.add_argument(
        '--send-email',
        action='store_true',
        help='Send email notification with summary report after completion'
    )
    parser.add_argument(
        '--wtd-avg-prices',
        action='store_true',
        default=None,
        help='Include px_wtd_avg_buy/sell/start/end columns in output (overrides config)'
    )
    parser.add_argument(
        '--no-wtd-avg-prices',
        action='store_false',
        dest='wtd_avg_prices',
        help='Exclude px_wtd_avg_buy/sell/start/end columns from output (overrides config)'
    )
    parser.set_defaults(wtd_avg_prices=None)

    args = parser.parse_args()

    logger.info("RINs Daily PnL Analysis - Production Workflow")
    logger.info("=" * 50)

    if args.send_email:
        logger.info("Email notifications enabled via --send-email flag")
    if args.wtd_avg_prices is not None:
        state = "enabled" if args.wtd_avg_prices else "disabled"
        logger.info(f"Weighted average price columns {state} via CLI flag")

    workflow = ProductionPnLWorkflow(
        send_email=args.send_email,
        include_weighted_avg_prices=args.wtd_avg_prices,
    )

    try:
        success = workflow.run()
        if success:
            logger.info("Analysis completed successfully!")
            if args.send_email:
                logger.info("Email notification sent.")
        else:
            logger.error("Analysis failed. Check the logs above.")
            sys.exit(1)

    except KeyboardInterrupt:
        logger.warning("Analysis interrupted by user.")
        sys.exit(1)
    except Exception:
        logger.exception("Unexpected error")
        sys.exit(1)


if __name__ == "__main__":
    main()

