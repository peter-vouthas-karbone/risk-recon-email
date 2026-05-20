#!/usr/bin/env python3
"""
Reporting Module for PnL Analysis.

This module handles generation of text and HTML reports for PnL analysis results.
It provides both summary reports and position reports with customizable
formatting and multi-period PnL calculations (daily, WTD, MTD, QTD, YTD).

Usage:
    >>> from reporting import PnLReporter
    >>> reporter = PnLReporter(
    ...     pnl_df=pnl_dataframe,
    ...     results=workflow_results,
    ...     min_trade_date='2024-01-01',
    ...     summary_report_path='/path/to/summary.txt',
    ...     html_report_path='/path/to/report.html'
    ... )
    >>> reporter.generate_summary_report()
    >>> reporter.generate_html_report()
"""

import logging
import os
import re
from datetime import date, datetime, timedelta, time
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

try:
    from zoneinfo import ZoneInfo
except ImportError:
    # Fallback for Python < 3.9
    from backports.zoneinfo import ZoneInfo  # type: ignore

from karbone_pnl_pos.utils import constants
from karbone_pnl_pos.utils.config_loader import get_results_dir
from karbone_pnl_pos.utils.trading_calendar import TradingCalendar
from karbone_pnl_pos.utils.vintage_utils import parse_vintage_for_ordering

# Module logger
logger = logging.getLogger('pnl.' + __name__)


def get_last_report_date() -> Optional[date]:
    """
    Get the date of the last successfully sent report.
    
    This is used to determine the start date for Daily PnL calculations.
    Daily PnL includes all PnL since the last report was sent.
    
    Returns:
        The date of the last report, or None if no report has been sent yet.
    """
    results_dir = get_results_dir()
    last_report_file = os.path.join(results_dir, 'last_report_date.txt')
    
    if not os.path.exists(last_report_file):
        return None
    
    try:
        with open(last_report_file, 'r', encoding='utf-8') as f:
            date_str = f.read().strip()
            if date_str:
                return datetime.strptime(date_str, '%Y-%m-%d').date()
    except (ValueError, OSError) as e:
        logger.warning(f"Could not read last report date from {last_report_file}: {e}")
        return None
    
    return None


def save_last_report_date(report_date: date) -> bool:
    """
    Save the date of the last successfully sent report.
    
    Args:
        report_date: The date of the report that was just sent.
        
    Returns:
        True if the date was saved successfully, False otherwise.
    """
    results_dir = get_results_dir()
    os.makedirs(results_dir, exist_ok=True)
    last_report_file = os.path.join(results_dir, 'last_report_date.txt')
    
    try:
        with open(last_report_file, 'w', encoding='utf-8') as f:
            f.write(report_date.strftime('%Y-%m-%d'))
        logger.info(f"Saved last report date: {report_date}")
        return True
    except OSError as e:
        logger.error(f"Could not save last report date to {last_report_file}: {e}")
        return False


def get_effective_reporting_date() -> date:
    """
    Get the effective date for reporting purposes.
    
    For reporting, "today" is not considered until 5pm New York time.
    Before 5pm NY time, the prior day is used as the effective date.
    This ensures morning PnL reports show the prior day's data.
    
    Returns:
        The effective date to use for filtering and reporting.
    """
    ny_tz = ZoneInfo('America/New_York')
    ny_now = datetime.now(ny_tz)
    
    # If it's before 5pm NY time, use yesterday
    cutoff_time = time(17, 0)  # 5:00 PM
    if ny_now.time() < cutoff_time:
        return (ny_now.date() - timedelta(days=1))
    else:
        return ny_now.date()


def get_daily_pnl_date_range(day_run: date, calendar: TradingCalendar) -> List[date]:
    """
    Deterministically calculate which dates should be included in Daily PnL.
    
    This implements the deterministic procedure for Daily PnL date inclusion:
    - Weekend rollup rules (Sat includes Fri+Sat, Sun includes Fri+Sat+Sun)
    - Holiday deferral rules (holidays don't produce standalone reports)
    - "Holiday steals the prior day" catch-up rule
    - Cascading consecutive-holiday handling
    
    Args:
        day_run: The effective reporting date (using 5pm NY boundary)
        calendar: TradingCalendar instance for holiday checking
        
    Returns:
        List of dates to include in Daily PnL, in chronological order
    """
    dates_to_include: List[date] = []
    
    # Step 1: Check if day_run is a holiday
    is_day_run_holiday = calendar.is_holiday(day_run)
    
    if is_day_run_holiday:
        # Step 2a: Holiday handling (roll up through the holiday itself).
        # Include the holiday chain ending on day_run, plus the correct prior block
        # (Mon holiday => include Fri-Sun before it).
        baseline_dates: List[date] = []
        holiday_chain: List[date] = [day_run]
        current_date = day_run - timedelta(days=1)
        max_lookback = 20

        for _ in range(max_lookback):
            if current_date < day_run - timedelta(days=max_lookback):
                break
            if calendar.is_holiday(current_date):
                holiday_chain.insert(0, current_date)
                current_date -= timedelta(days=1)
            else:
                break

        h_start = holiday_chain[0]
        h_start_weekday = h_start.weekday()  # Monday=0, Friday=4, Saturday=5, Sunday=6
        if h_start_weekday == 0:  # Monday holiday
            sunday = h_start - timedelta(days=1)
            saturday = h_start - timedelta(days=2)
            friday = h_start - timedelta(days=3)
            baseline_dates = [friday, saturday, sunday] + holiday_chain
        else:
            prior_day = h_start - timedelta(days=1)
            baseline_dates = [prior_day] + holiday_chain

        # Remove duplicates while preserving order
        seen = set()
        for d in baseline_dates:
            if d not in seen:
                seen.add(d)
                dates_to_include.append(d)
        dates_to_include.sort()
        return dates_to_include

    # Step 2: Deterministic baseline weekend/weekday rules
    # Always use deterministic logic based on the current date only.
    baseline_dates: List[date] = []
    if not is_day_run_holiday:
        day_of_week = day_run.weekday()  # Monday=0, Friday=4, Saturday=5, Sunday=6

        if day_of_week == 0:  # Monday: roll up Sat+Sun+Mon (no reports issued Sun or Mon)
            saturday = day_run - timedelta(days=2)
            sunday = day_run - timedelta(days=1)
            baseline_dates = [saturday, sunday, day_run]
        elif day_of_week < 5:  # Tuesday through Friday
            baseline_dates = [day_run]
        elif day_of_week == 5:  # Saturday
            # Include Friday and Saturday (per weekend rollup rule)
            friday = day_run - timedelta(days=1)
            baseline_dates = [friday, day_run]
        elif day_of_week == 6:  # Sunday
            # Include Friday, Saturday, and Sunday (per weekend rollup rule)
            saturday = day_run - timedelta(days=1)
            friday = day_run - timedelta(days=2)
            baseline_dates = [friday, saturday, day_run]
    
    # Step 3: Find deferred holiday chain (most recent contiguous holidays ending before day_run)
    deferred_holidays: List[date] = []
    current_date = day_run - timedelta(days=1)
    max_lookback = 20  # Look back up to 20 days to find holiday chain
    
    for _ in range(max_lookback):
        if current_date < day_run - timedelta(days=max_lookback):
            break
        
        if calendar.is_holiday(current_date):
            deferred_holidays.insert(0, current_date)  # Insert at beginning to maintain chronological order
            current_date -= timedelta(days=1)
        else:
            # Stop if we hit a non-holiday (end of contiguous chain)
            break
    
    # Step 4: Construct deferred block if holidays were found
    deferred_block: List[date] = []
    
    if deferred_holidays:
        h_start = deferred_holidays[0]  # First holiday in chain
        h_start_weekday = h_start.weekday()  # Monday=0, Friday=4, Saturday=5, Sunday=6
        
        # Determine prior day block
        if h_start_weekday == 0:  # Monday holiday
            # Prior block is the entire weekend [Fri, Sat, Sun] before that Monday
            sunday = h_start - timedelta(days=1)
            saturday = h_start - timedelta(days=2)
            friday = h_start - timedelta(days=3)
            prior_block = [friday, saturday, sunday]
        else:
            # Prior block is just the day immediately before H_start
            prior_day = h_start - timedelta(days=1)
            prior_block = [prior_day]
        
        # Deferred block = prior block + all holidays in chain
        deferred_block = prior_block + deferred_holidays
    
    # Step 5: Combine deferred block (if any) + baseline, remove duplicates, sort
    all_dates = deferred_block + baseline_dates
    
    # Remove duplicates while preserving order
    seen = set()
    dates_to_include = []
    for d in all_dates:
        if d not in seen:
            seen.add(d)
            dates_to_include.append(d)
    
    # Sort chronologically
    dates_to_include.sort()
    
    return dates_to_include


class PnLReporter:
    """
    Handles generation of PnL reports in various formats.

    This class generates:
    - Text summary reports with key metrics
    - HTML reports for email distribution
    - Volumetric exposure reports

    Attributes:
        pnl_df: DataFrame containing PnL calculations.
        results: WorkflowResults object with summary statistics.
        min_trade_date: Minimum trade date filter.
        summary_report_path: Path to save text summary report.
        html_report_path: Path to save HTML report.
        position_report_path: Path to save position report.
    """

    _PERIOD_LABELS: Dict[str, str] = {
        'daily': 'Daily',
        'wtd': 'WTD',
        'mtd': 'MTD',
        'qtd': 'QTD',
        'ytd': 'YTD'
    }

    def __init__(
        self,
        pnl_df: pd.DataFrame,
        results: Any,
        min_trade_date: str,
        summary_report_path: str,
        html_report_path: str,
        position_report_path: Optional[str] = None,
        granularity: Optional[List[str]] = None,
        converted_trades_df: Optional[pd.DataFrame] = None
    ) -> None:
        """
        Initialize the reporter with PnL data and configuration.

        Args:
            pnl_df: DataFrame containing PnL calculations.
            results: WorkflowResults object with summary statistics.
            min_trade_date: Minimum trade date filter.
            summary_report_path: Path to save text summary report.
            html_report_path: Path to save HTML report.
            position_report_path: Path to save position report.
            granularity: List of enabled time periods (e.g., ['daily', 'wtd', 'mtd', 'qtd', 'ytd']).
                        Defaults to all periods if not provided.
            converted_trades_df: Optional DataFrame containing converted trades. Used to determine
                               most recent trade dates by desk. If not provided, will attempt to
                               load from the most recent converted trades CSV file.
        """
        self.pnl_df = pnl_df
        self.results = results
        self.min_trade_date = min_trade_date
        self.summary_report_path = summary_report_path
        self.html_report_path = html_report_path
        self.position_report_path = position_report_path or html_report_path
        self.converted_trades_df = converted_trades_df
        
        # Set granularity - default to all periods if not provided
        if granularity is None:
            from src.utils.config_loader import get_granularity_config
            granularity = get_granularity_config()
        
        # Validate and store granularity
        valid_periods = ['daily', 'wtd', 'mtd', 'qtd', 'ytd']
        self.granularity = [p for p in granularity if p in valid_periods] or valid_periods

    # ------------------------------------------------------------------ #
    # Helpers for multi-report generation
    # ------------------------------------------------------------------ #

    def _get_period_column_name(self, period: str) -> str:
        """
        Map period name to DataFrame column name.

        Args:
            period: Period name ('daily', 'wtd', 'mtd', 'qtd', 'ytd').

        Returns:
            Column name (e.g., 'daily_pnl', 'wtd_pnl').
        """
        period_map = {
            'daily': 'daily_pnl',
            'wtd': 'wtd_pnl',
            'mtd': 'mtd_pnl',
            'qtd': 'qtd_pnl',
            'ytd': 'ytd_pnl'
        }
        return period_map.get(period, f'{period}_pnl')

    def _is_period_enabled(self, period: str) -> bool:
        """
        Check if a time period is enabled in granularity configuration.

        Args:
            period: Period name ('daily', 'wtd', 'mtd', 'qtd', 'ytd').

        Returns:
            True if period is enabled, False otherwise.
        """
        return period in self.granularity

    def _get_enabled_periods(self) -> List[str]:
        """
        Get list of enabled periods in standard order.

        Returns:
            List of enabled period names in order: daily, wtd, mtd, qtd, ytd.
        """
        standard_order = ['daily', 'wtd', 'mtd', 'qtd', 'ytd']
        return [p for p in standard_order if p in self.granularity]

    def _normalize_desks_filter(self, desks: Any) -> Optional[List[str]]:
        """
        Normalize desk filter values from configuration.

        Args:
            desks: Value from settings (string, list, etc.).

        Returns:
            List of lowercase desk names to filter on, or None to skip filtering.
        """
        if desks is None:
            return None

        if isinstance(desks, str):
            desks = desks.strip()
            if desks.lower() == 'all' or desks == '':
                return None
            return [desk.strip().lower() for desk in desks.split(',') if desk.strip()]

        if isinstance(desks, (list, tuple, set)):
            cleaned = [str(desk).strip().lower() for desk in desks if str(desk).strip()]
            return cleaned or None

        return None

    def _filter_df_by_desks(
        self,
        pnl_df: Optional[pd.DataFrame],
        desks_filter: Optional[List[str]]
    ) -> pd.DataFrame:
        """
        Filter a PnL DataFrame by desks (case-insensitive).

        Args:
            pnl_df: Source PnL DataFrame.
            desks_filter: List of desk codes to include. None means no filter.

        Returns:
            Filtered DataFrame (or empty DataFrame if no data matches).
        """
        if pnl_df is None or pnl_df.empty:
            return pd.DataFrame()

        if not desks_filter:
            return pnl_df.copy()

        df = pnl_df.copy()
        df['__desk_lower'] = df['desk'].astype(str).str.lower()
        filtered = df[df['__desk_lower'].isin(desks_filter)].drop(columns=['__desk_lower'])
        return filtered

    def _build_results_snapshot(self, pnl_df: pd.DataFrame) -> SimpleNamespace:
        """
        Build a lightweight results object scoped to a filtered DataFrame.

        Args:
            pnl_df: Filtered PnL DataFrame.

        Returns:
            SimpleNamespace with minimal attributes required by report writers.
        """
        if pnl_df is None or pnl_df.empty:
            return SimpleNamespace(
                pnl_records=0,
                date_range=(None, None),
                products=0,
                vintages=0,
                desks=0,
                validation_results=getattr(self.results, 'validation_results', {})
            )

        return SimpleNamespace(
            pnl_records=len(pnl_df),
            date_range=(pnl_df['date'].min(), pnl_df['date'].max()),
            products=pnl_df['product'].nunique(),
            vintages=pnl_df['vintage'].nunique(),
            desks=pnl_df['desk'].nunique(),
            validation_results=getattr(self.results, 'validation_results', {})
        )

    def _slugify_report_name(self, name: str) -> str:
        """
        Create a filesystem-friendly slug for report filenames.
        """
        safe_name = name.strip().lower().replace(' ', '_')
        safe_name = ''.join(ch if ch.isalnum() or ch in ('_', '-') else '_' for ch in safe_name)
        return safe_name or 'report'

    def _get_most_recent_trade_date_by_desk(self, pnl_df: pd.DataFrame) -> Dict[str, str]:
        """
        Calculate the most recent trade date for each desk using converted trades.
        
        Uses the converted trades DataFrame (actual trades) rather than the PnL DataFrame
        (which may include dates from mark-to-market calculations or price data).
        
        Args:
            pnl_df: DataFrame containing PnL calculations (not used, kept for compatibility).
            
        Returns:
            Dictionary mapping desk names to their most recent trade date strings (YYYY-MM-DD format).
        """
        # Try to use converted_trades_df if available
        trades_df = self.converted_trades_df
        
        # If not available, try to load from the most recent converted trades CSV file
        if trades_df is None or trades_df.empty:
            trades_df = self._load_most_recent_converted_trades()
        
        if trades_df is None or trades_df.empty:
            return {}
        
        # Filter out future-dated trades (using effective reporting date)
        effective_date = get_effective_reporting_date()
        trades_df_copy = trades_df.copy()
        trades_df_copy['date'] = pd.to_datetime(trades_df_copy['date'])
        trades_df_copy = trades_df_copy[trades_df_copy['date'].dt.date <= effective_date]
        
        if trades_df_copy.empty:
            return {}
        
        # Group by desk and find the maximum date for each desk
        desk_dates = trades_df_copy.groupby('desk')['date'].max()
        
        # Convert to dictionary with formatted date strings
        result = {}
        for desk, max_date in desk_dates.items():
            if isinstance(max_date, pd.Timestamp):
                result[desk] = max_date.strftime('%Y-%m-%d')
            elif hasattr(max_date, 'strftime'):
                result[desk] = max_date.strftime('%Y-%m-%d')
            else:
                result[desk] = str(max_date)
        
        return result

    def _load_most_recent_converted_trades(self) -> Optional[pd.DataFrame]:
        """
        Load the most recent converted trades CSV file.
        
        Returns:
            DataFrame containing converted trades, or None if file not found.
        """
        results_dir = get_results_dir()
        csv_dir = os.path.join(results_dir, 'csv')
        
        if not os.path.exists(csv_dir):
            return None
        
        # Find the most recent converted_trades CSV file
        pattern = 'converted_trades_*.csv'
        matching_files = []
        for filename in os.listdir(csv_dir):
            if filename.startswith('converted_trades_') and filename.endswith('.csv'):
                filepath = os.path.join(csv_dir, filename)
                matching_files.append((os.path.getmtime(filepath), filepath))
        
        if not matching_files:
            return None
        
        # Get the most recent file
        matching_files.sort(reverse=True)
        most_recent_file = matching_files[0][1]
        
        try:
            trades_df = pd.read_csv(most_recent_file)
            logger.info(f"Loaded converted trades from {most_recent_file}")
            return trades_df
        except (OSError, pd.errors.ParserError) as e:
            logger.warning(f"Could not load converted trades from {most_recent_file}: {e}")
            return None

    def get_most_recent_day_pnl(self, pnl_df: pd.DataFrame) -> pd.DataFrame:
        """
        Get all PnL for dates that should be included in Daily PnL.
        
        Uses deterministic date range calculation based on:
        - Weekend rollup rules (Sat includes Fri+Sat, Sun includes Fri+Sat+Sun)
        - Holiday deferral rules (holidays don't produce standalone reports)
        - "Holiday steals the prior day" catch-up rule
        - Cascading consecutive-holiday handling
        
        Excludes future-dated trades (dates > today) to ensure the most recent
        trade date never reflects a future date.

        Args:
            pnl_df: DataFrame containing PnL calculations.

        Returns:
            DataFrame with all PnL for dates in the Daily PnL range, sorted by desk/product/vintage.
        """
        if pnl_df.empty:
            return pd.DataFrame()

        # Filter out future-dated trades (using effective reporting date)
        effective_date = get_effective_reporting_date()
        pnl_df_copy = pnl_df.copy()
        
        # Store original date format
        original_date_dtype = pnl_df_copy['date'].dtype
        
        # Convert to datetime for filtering
        pnl_df_copy['date'] = pd.to_datetime(pnl_df_copy['date'])
        pnl_df_copy = pnl_df_copy[pnl_df_copy['date'].dt.date <= effective_date]
        
        if pnl_df_copy.empty:
            return pd.DataFrame()

        # Get the deterministic date range for Daily PnL
        calendar = TradingCalendar()
        dates_to_include = get_daily_pnl_date_range(effective_date, calendar)
        
        if not dates_to_include:
            # No dates to include (e.g., day_run is a holiday with no deferred holidays)
            return pd.DataFrame()
        
        # Filter to only include dates in the calculated range
        recent_pnl = pnl_df_copy[
            pnl_df_copy['date'].dt.date.isin(dates_to_include)
        ].copy()
        
        if recent_pnl.empty:
            return pd.DataFrame()
        
        # Restore original date format
        if pd.api.types.is_object_dtype(original_date_dtype) or isinstance(original_date_dtype, str):
            # If original was string, convert back to string
            recent_pnl['date'] = recent_pnl['date'].dt.strftime('%Y-%m-%d')
        elif hasattr(original_date_dtype, 'type') and original_date_dtype.type == date:
            # If original was date, convert back to date
            recent_pnl['date'] = recent_pnl['date'].dt.date
        
        recent_pnl['_vintage_sort'] = recent_pnl['vintage'].apply(parse_vintage_for_ordering)
        recent_pnl = recent_pnl.sort_values(
            ['desk', 'product', '_vintage_sort', 'vintage']
        ).drop(columns=['_vintage_sort'])

        return recent_pnl

    def calculate_pnl_by_year_desk(self, pnl_df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate total PnL by year and desk from the PnL DataFrame.

        Args:
            pnl_df: DataFrame containing PnL calculations.

        Returns:
            DataFrame with PnL aggregated by year and desk, sorted by year (desc) then desk.
        """
        if pnl_df.empty:
            return pd.DataFrame()

        pnl_df_copy = pnl_df.copy()
        pnl_df_copy['year'] = pd.to_datetime(pnl_df_copy['date']).dt.year

        pnl_by_year_desk = pnl_df_copy.groupby(
            ['year', 'desk']
        )['usd_pnl_trading'].sum().reset_index()

        pnl_by_year_desk = pnl_by_year_desk.sort_values(
            ['year', 'desk'],
            ascending=[False, True]
        )

        return pnl_by_year_desk

    def calculate_wtd_pnl(self, pnl_df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate Week-to-Date PnL by desk from the PnL DataFrame.

        Uses the latest trading date from the PnL data to determine week boundaries,
        not the current system date.

        Args:
            pnl_df: DataFrame containing PnL calculations.

        Returns:
            DataFrame with WTD PnL aggregated by desk.
        """
        if pnl_df.empty:
            return pd.DataFrame()

        pnl_df_copy = pnl_df.copy()
        pnl_df_copy['date'] = pd.to_datetime(pnl_df_copy['date'])
        
        # Get latest date from PnL data and find prior trading day
        latest_date = pnl_df_copy['date'].max().date()
        calendar = TradingCalendar()
        prior_trading_day = calendar.get_previous_trading_day(latest_date)
        
        # Get week start based on prior trading day
        week_start = calendar.get_week_start(prior_trading_day)
        week_start_dt = pd.Timestamp(week_start)

        wtd_data = pnl_df_copy[
            (pnl_df_copy['date'] >= week_start_dt) &
            (pnl_df_copy['date'].dt.date <= prior_trading_day)
        ]

        if wtd_data.empty:
            return pd.DataFrame()

        wtd_by_desk = wtd_data.groupby('desk')['usd_pnl_trading'].sum().reset_index()
        wtd_by_desk.columns = ['desk', 'wtd_pnl']

        return wtd_by_desk

    def calculate_mtd_pnl(self, pnl_df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate Month-to-Date PnL by desk from the PnL DataFrame.

        Uses the latest trading date from the PnL data to determine month boundaries,
        not the current system date.

        Args:
            pnl_df: DataFrame containing PnL calculations.

        Returns:
            DataFrame with MTD PnL aggregated by desk.
        """
        if pnl_df.empty:
            return pd.DataFrame()

        pnl_df_copy = pnl_df.copy()
        pnl_df_copy['date'] = pd.to_datetime(pnl_df_copy['date'])
        
        # Get latest date from PnL data and find prior trading day
        latest_date = pnl_df_copy['date'].max().date()
        calendar = TradingCalendar()
        prior_trading_day = calendar.get_previous_trading_day(latest_date)
        
        prior_year = prior_trading_day.year
        prior_month = prior_trading_day.month
        prior_trading_day_dt = pd.Timestamp(prior_trading_day)

        mtd_data = pnl_df_copy[
            (pnl_df_copy['date'].dt.year == prior_year) &
            (pnl_df_copy['date'].dt.month == prior_month) &
            (pnl_df_copy['date'].dt.date <= prior_trading_day)
        ]

        if mtd_data.empty:
            return pd.DataFrame()

        mtd_by_desk = mtd_data.groupby('desk')['usd_pnl_trading'].sum().reset_index()
        mtd_by_desk.columns = ['desk', 'mtd_pnl']

        return mtd_by_desk

    def calculate_qtd_pnl(self, pnl_df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate Quarter-to-Date PnL by desk from the PnL DataFrame.

        Uses the latest trading date from the PnL data to determine quarter boundaries,
        not the current system date.

        Args:
            pnl_df: DataFrame containing PnL calculations.

        Returns:
            DataFrame with QTD PnL aggregated by desk.
        """
        if pnl_df.empty:
            return pd.DataFrame()

        pnl_df_copy = pnl_df.copy()
        pnl_df_copy['date'] = pd.to_datetime(pnl_df_copy['date'])
        
        # Get latest date from PnL data and find prior trading day
        latest_date = pnl_df_copy['date'].max().date()
        calendar = TradingCalendar()
        prior_trading_day = calendar.get_previous_trading_day(latest_date)
        
        prior_year = prior_trading_day.year
        prior_quarter = (prior_trading_day.month - 1) // 3 + 1
        prior_trading_day_dt = pd.Timestamp(prior_trading_day)

        pnl_df_copy['quarter'] = pnl_df_copy['date'].dt.quarter

        qtd_data = pnl_df_copy[
            (pnl_df_copy['date'].dt.year == prior_year) &
            (pnl_df_copy['quarter'] == prior_quarter) &
            (pnl_df_copy['date'].dt.date <= prior_trading_day)
        ]

        if qtd_data.empty:
            return pd.DataFrame()

        qtd_by_desk = qtd_data.groupby('desk')['usd_pnl_trading'].sum().reset_index()
        qtd_by_desk.columns = ['desk', 'qtd_pnl']

        return qtd_by_desk

    def _calculate_pnl_by_period_and_group(
        self,
        pnl_df: pd.DataFrame,
        group_by_cols: List[str]
    ) -> Dict[str, pd.DataFrame]:
        """
        Calculate PnL by all time periods (Daily, WTD, MTD, QTD, YTD) with additional grouping.
        
        Args:
            pnl_df: DataFrame containing PnL calculations.
            group_by_cols: List of columns to group by (e.g., ['product'], ['vintage'], ['product', 'vintage']).
            
        Returns:
            Dictionary with keys 'daily', 'wtd', 'mtd', 'qtd', 'ytd', each containing a DataFrame
            with the grouped PnL results.
        """
        if pnl_df.empty:
            empty_df = pd.DataFrame(columns=group_by_cols + ['pnl'])
            return {
                'daily': empty_df.copy(),
                'wtd': empty_df.copy(),
                'mtd': empty_df.copy(),
                'qtd': empty_df.copy(),
                'ytd': empty_df.copy()
            }
        
        pnl_df_copy = pnl_df.copy()
        pnl_df_copy['date'] = pd.to_datetime(pnl_df_copy['date'])
        
        # Filter out future-dated trades to match get_most_recent_day_pnl logic
        # Use effective reporting date (5pm NY time cutoff)
        effective_date = get_effective_reporting_date()
        pnl_df_copy = pnl_df_copy[pnl_df_copy['date'].dt.date <= effective_date]
        
        if pnl_df_copy.empty:
            empty_df = pd.DataFrame(columns=group_by_cols + ['pnl'])
            return {
                'daily': empty_df.copy(),
                'wtd': empty_df.copy(),
                'mtd': empty_df.copy(),
                'qtd': empty_df.copy(),
                'ytd': empty_df.copy()
            }
        
        # Get latest date from filtered PnL data (actual most recent date, not prior trading day)
        latest_date = pnl_df_copy['date'].max().date()
        calendar = TradingCalendar()
        prior_trading_day = calendar.get_previous_trading_day(latest_date)
        prior_trading_day_dt = pd.Timestamp(prior_trading_day)
        
        prior_year = prior_trading_day.year
        prior_month = prior_trading_day.month
        prior_quarter = (prior_trading_day.month - 1) // 3 + 1
        
        # Get week start based on prior trading day
        week_start = calendar.get_week_start(prior_trading_day)
        week_start_dt = pd.Timestamp(week_start)
        
        # Daily PnL - uses deterministic date range calculation
        # This matches the logic in get_most_recent_day_pnl()
        dates_to_include = get_daily_pnl_date_range(effective_date, calendar)
        
        # Daily PnL
        # Start with an empty DataFrame that already has the expected columns so that
        # downstream merge operations (which always merge on group_by_cols) never fail
        # with KeyError when a particular period has no rows.
        daily = pd.DataFrame(columns=group_by_cols + ['daily_pnl'])
        if dates_to_include:
            daily_pnl_data = pnl_df_copy[
                pnl_df_copy['date'].dt.date.isin(dates_to_include)
            ].copy()
            if not daily_pnl_data.empty:
                daily = daily_pnl_data.groupby(group_by_cols)['usd_pnl_trading'].sum().reset_index()
                daily.columns = group_by_cols + ['daily_pnl']
        
        # WTD PnL
        wtd_data = pnl_df_copy[
            (pnl_df_copy['date'] >= week_start_dt) &
            (pnl_df_copy['date'].dt.date <= prior_trading_day)
        ]
        # Initialize with expected columns to keep merge keys consistent even when empty
        wtd = pd.DataFrame(columns=group_by_cols + ['wtd_pnl'])
        if not wtd_data.empty:
            wtd = wtd_data.groupby(group_by_cols)['usd_pnl_trading'].sum().reset_index()
            wtd.columns = group_by_cols + ['wtd_pnl']
        
        # MTD PnL
        mtd_data = pnl_df_copy[
            (pnl_df_copy['date'].dt.year == prior_year) &
            (pnl_df_copy['date'].dt.month == prior_month) &
            (pnl_df_copy['date'].dt.date <= prior_trading_day)
        ]
        # Initialize with expected columns to keep merge keys consistent even when empty
        mtd = pd.DataFrame(columns=group_by_cols + ['mtd_pnl'])
        if not mtd_data.empty:
            mtd = mtd_data.groupby(group_by_cols)['usd_pnl_trading'].sum().reset_index()
            mtd.columns = group_by_cols + ['mtd_pnl']
        
        # QTD PnL
        pnl_df_copy['quarter'] = pnl_df_copy['date'].dt.quarter
        qtd_data = pnl_df_copy[
            (pnl_df_copy['date'].dt.year == prior_year) &
            (pnl_df_copy['quarter'] == prior_quarter) &
            (pnl_df_copy['date'].dt.date <= prior_trading_day)
        ]
        # Initialize with expected columns to keep merge keys consistent even when empty
        qtd = pd.DataFrame(columns=group_by_cols + ['qtd_pnl'])
        if not qtd_data.empty:
            qtd = qtd_data.groupby(group_by_cols)['usd_pnl_trading'].sum().reset_index()
            qtd.columns = group_by_cols + ['qtd_pnl']
        
        # YTD PnL
        ytd_data = pnl_df_copy[
            (pnl_df_copy['date'].dt.year == prior_year) &
            (pnl_df_copy['date'].dt.date <= prior_trading_day)
        ]
        # Initialize with expected columns to keep merge keys consistent even when empty
        ytd = pd.DataFrame(columns=group_by_cols + ['ytd_pnl'])
        if not ytd_data.empty:
            ytd = ytd_data.groupby(group_by_cols)['usd_pnl_trading'].sum().reset_index()
            ytd.columns = group_by_cols + ['ytd_pnl']
        
        return {
            'daily': daily,
            'wtd': wtd,
            'mtd': mtd,
            'qtd': qtd,
            'ytd': ytd
        }

    def _prepare_pnl_breakout(
        self,
        pnl_df: pd.DataFrame,
        breakout_type: str
    ) -> pd.DataFrame:
        """
        Prepare PnL breakout data grouped by product, vintage, or both.
        
        Args:
            pnl_df: DataFrame containing PnL calculations.
            breakout_type: One of 'product', 'vintage', or 'product_and_vintage'.
            
        Returns:
            DataFrame with columns: [grouping_cols, daily_pnl, wtd_pnl, mtd_pnl, qtd_pnl, ytd_pnl]
        """
        _valid_breakout_types = [
            'product', 'vintage', 'product_and_vintage',
            'portfolio', 'strategy', 'portfolio_and_strategy',
        ]
        if breakout_type not in _valid_breakout_types:
            return pd.DataFrame()

        # Determine grouping columns (include desk for sorting and header display)
        if breakout_type == 'product':
            group_by_cols = ['desk', 'product']
        elif breakout_type == 'vintage':
            group_by_cols = ['desk', 'vintage']
        elif breakout_type == 'product_and_vintage':
            group_by_cols = ['desk', 'product', 'vintage']
        elif breakout_type == 'portfolio':
            group_by_cols = ['desk', 'portfolio']
        elif breakout_type == 'strategy':
            group_by_cols = ['desk', 'strategy']
        else:  # portfolio_and_strategy
            group_by_cols = ['desk', 'portfolio', 'strategy']
        
        pnl_df_for_grouping = pnl_df.copy()
        fee_mask = self._detect_fees(pnl_df_for_grouping)

        if 'vintage' in group_by_cols and fee_mask.any():
            self._apply_fee_vintage_collapse(pnl_df_for_grouping, fee_mask)
        
        # Ensure all grouping columns have valid values (fill NaN/None to prevent exclusion from groupby)
        for col in group_by_cols:
            if col in pnl_df_for_grouping.columns:
                # Fill NaN/None with empty string (for vintage) or placeholder (for product/desk)
                if col == 'vintage':
                    pnl_df_for_grouping[col] = pnl_df_for_grouping[col].fillna('')
                elif col == 'desk':
                    pnl_df_for_grouping[col] = pnl_df_for_grouping[col].fillna('Unknown')
                else:
                    pnl_df_for_grouping[col] = pnl_df_for_grouping[col].fillna('Unknown')
        
        # Calculate PnL by all periods with grouping
        period_data = self._calculate_pnl_by_period_and_group(pnl_df_for_grouping, group_by_cols)
        
        # Start with first enabled period as base, or daily if enabled
        result = pd.DataFrame()
        if self._is_period_enabled('daily'):
            result = period_data['daily'].copy()
        elif self._is_period_enabled('wtd') and not period_data['wtd'].empty:
            result = period_data['wtd'].copy()
        elif self._is_period_enabled('mtd') and not period_data['mtd'].empty:
            result = period_data['mtd'].copy()
        elif self._is_period_enabled('qtd') and not period_data['qtd'].empty:
            result = period_data['qtd'].copy()
        elif self._is_period_enabled('ytd') and not period_data['ytd'].empty:
            result = period_data['ytd'].copy()
        else:
            # Fallback: use daily even if not enabled (shouldn't happen with default config)
            result = period_data['daily'].copy() if not period_data['daily'].empty else pd.DataFrame(columns=group_by_cols)
        
        # Merge other enabled periods (skip daily since it's the base)
        if self._is_period_enabled('wtd'):
            if not period_data['wtd'].empty:
                result = result.merge(period_data['wtd'], on=group_by_cols, how='outer')
            else:
                result['wtd_pnl'] = 0.0
        
        if self._is_period_enabled('mtd'):
            if not period_data['mtd'].empty:
                result = result.merge(period_data['mtd'], on=group_by_cols, how='outer')
            else:
                result['mtd_pnl'] = 0.0
        
        if self._is_period_enabled('qtd'):
            if not period_data['qtd'].empty:
                result = result.merge(period_data['qtd'], on=group_by_cols, how='outer')
            else:
                result['qtd_pnl'] = 0.0
        
        if self._is_period_enabled('ytd'):
            if not period_data['ytd'].empty:
                result = result.merge(period_data['ytd'], on=group_by_cols, how='outer')
            else:
                result['ytd_pnl'] = 0.0
        
        # Fill NaN values with 0
        result = result.fillna(0)
        
        # Sort results by desk first (using display name), then by grouping columns
        result['desk_display'] = result['desk'].apply(self._format_desk_name)
        if 'vintage' in group_by_cols:
            # Add vintage sort column for proper ordering
            result['_vintage_sort'] = result['vintage'].apply(parse_vintage_for_ordering)
            if 'product' in group_by_cols:
                result = result.sort_values(['desk_display', 'product', '_vintage_sort', 'vintage'], na_position='last')
            else:
                result = result.sort_values(['desk_display', '_vintage_sort', 'vintage'], na_position='last')
            result = result.drop(columns=['_vintage_sort', 'desk_display'])
        elif 'product' in group_by_cols:
            result = result.sort_values(['desk_display', 'product'], na_position='last')
            result = result.drop(columns=['desk_display'])
        elif 'portfolio' in group_by_cols and 'strategy' in group_by_cols:
            result = result.sort_values(['desk_display', 'portfolio', 'strategy'], na_position='last')
            result = result.drop(columns=['desk_display'])
        elif 'portfolio' in group_by_cols:
            result = result.sort_values(['desk_display', 'portfolio'], na_position='last')
            result = result.drop(columns=['desk_display'])
        elif 'strategy' in group_by_cols:
            result = result.sort_values(['desk_display', 'strategy'], na_position='last')
            result = result.drop(columns=['desk_display'])
        else:
            result = result.sort_values('desk_display', na_position='last')
            result = result.drop(columns=['desk_display'])
        
        return result

    def generate_reports_from_settings(
        self,
        pnl_reports: Optional[List[Dict[str, Any]]] = None,
        position_reports: Optional[List[Dict[str, Any]]] = None,
        results_dir: Optional[str] = None,
        timestamp: Optional[str] = None
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Generate PnL and position reports based on settings.yaml structure.

        Args:
            pnl_reports: List of PnL report configurations under email.pnl_reports.
            position_reports: List of position report configs under email.position_reports.
            results_dir: Directory to write output files. Defaults to current summary dir.
            timestamp: Optional timestamp suffix for filenames. Defaults to now.

        Returns:
            Dictionary summarizing generated report file paths and statuses.
        """
        pnl_reports = pnl_reports or []
        position_reports = position_reports or []
        timestamp = timestamp or datetime.now().strftime('%Y%m%d_%H%M%S')
        base_results_dir = results_dir or os.path.dirname(self.summary_report_path) or '.'
        os.makedirs(base_results_dir, exist_ok=True)
        
        # Create subdirectories for organized output
        html_subdir = os.path.join(base_results_dir, 'html')
        txt_subdir = os.path.join(base_results_dir, 'txt')
        os.makedirs(html_subdir, exist_ok=True)
        os.makedirs(txt_subdir, exist_ok=True)

        generated: Dict[str, List[Dict[str, Any]]] = {
            'pnl_reports': [],
            'position_reports': []
        }

        for report_cfg in pnl_reports:
            if not report_cfg or not report_cfg.get('enabled', False):
                continue

            desks_filter = self._normalize_desks_filter(report_cfg.get('desks', 'all'))
            filtered_df = self._filter_df_by_desks(self.pnl_df, desks_filter)

            if filtered_df.empty and desks_filter:
                logger.info(
                    "Skipping PnL report '%s' - no data for desks filter %s",
                    report_cfg.get('name', 'unnamed'),
                    desks_filter
                )
                generated['pnl_reports'].append({
                    'name': report_cfg.get('name', 'unnamed'),
                    'desks': report_cfg.get('desks', 'all'),
                    'generated': False,
                    'reason': 'No data after desk filter'
                })
                continue

            subset_results = self._build_results_snapshot(filtered_df)
            report_name = report_cfg.get('name', 'pnl_report')
            slug = self._slugify_report_name(report_name)

            summary_path = os.path.join(txt_subdir, f"{slug}_summary_{timestamp}.txt")
            html_path = os.path.join(html_subdir, f"{slug}_pnl_{timestamp}.html")
            position_path = os.path.join(html_subdir, f"{slug}_position_{timestamp}.html")

            # Get granularity from report config or use global default
            report_granularity = report_cfg.get('granularity', None)
            if report_granularity is None:
                from src.utils.config_loader import get_granularity_config
                report_granularity = get_granularity_config()

            subset_reporter = PnLReporter(
                pnl_df=filtered_df,
                results=subset_results,
                min_trade_date=self.min_trade_date,
                summary_report_path=summary_path,
                html_report_path=html_path,
                position_report_path=position_path,
                granularity=report_granularity,
                converted_trades_df=self.converted_trades_df
            )

            # Extract tables configuration (None = show all non-breakout sections)
            tables_cfg = report_cfg.get('tables', None)

            # Derive breakout type from the tables list â€” the first entry that matches a
            # known breakout keyword is used; all other entries are structural table keys.
            _breakout_types = {
                'product', 'vintage', 'product_and_vintage',
                'portfolio', 'strategy', 'portfolio_and_strategy',
            }
            breakout_type = None
            if tables_cfg:
                for _t in tables_cfg:
                    if _t.lower() in _breakout_types:
                        breakout_type = _t.lower()
                        break

            # Extract report title from subject line (remove date part and brackets)
            subject_template = report_cfg.get('subject', 'Karbone Daily PnL Summary Report - {date}')
            # Remove the " - {date}" part to get just the title, then remove brackets
            report_title = subject_template.replace(' - {date}', '').strip()
            report_title = report_title.replace('[', '').replace(']', '').strip()

            summary_ok = subset_reporter.generate_summary_report()
            html_ok = subset_reporter.generate_html_report(breakout_type=breakout_type, report_title=report_title, tables=tables_cfg)

            generated['pnl_reports'].append({
                'name': report_name,
                'desks': report_cfg.get('desks', 'all'),
                'summary_report_path': summary_path,
                'html_report_path': html_path,
                'generated': bool(summary_ok and html_ok)
            })

        for report_cfg in position_reports:
            if not report_cfg or not report_cfg.get('enabled', False):
                continue

            desks_filter = self._normalize_desks_filter(report_cfg.get('desks', 'all'))
            filtered_df = self._filter_df_by_desks(self.pnl_df, desks_filter)

            if filtered_df.empty and desks_filter:
                logger.info(
                    "Skipping position report '%s' - no data for desks filter %s",
                    report_cfg.get('name', 'unnamed'),
                    desks_filter
                )
                generated['position_reports'].append({
                    'name': report_cfg.get('name', 'unnamed'),
                    'desks': report_cfg.get('desks', 'all'),
                    'generated': False,
                    'reason': 'No data after desk filter'
                })
                continue

            subset_results = self._build_results_snapshot(filtered_df)
            report_name = report_cfg.get('name', 'position_report')
            slug = self._slugify_report_name(report_name)
            position_path = os.path.join(html_subdir, f"{slug}_position_{timestamp}.html")

            subset_reporter = PnLReporter(
                pnl_df=filtered_df,
                results=subset_results,
                min_trade_date=self.min_trade_date,
                summary_report_path=self.summary_report_path,
                html_report_path=self.html_report_path,
                position_report_path=position_path,
                converted_trades_df=self.converted_trades_df
            )

            intro_text = report_cfg.get('intro_text', '')
            # Extract report title from subject line (remove date part and brackets)
            subject_template = report_cfg.get('subject', 'Karbone Daily Position Report - {date}')
            # Remove the " - {date}" part to get just the title, then remove brackets
            report_title = subject_template.replace(' - {date}', '').strip()
            report_title = report_title.replace('[', '').replace(']', '').strip()
            
            position_ok = subset_reporter.generate_position_html_report(intro_text=intro_text, report_title=report_title)

            generated['position_reports'].append({
                'name': report_name,
                'desks': report_cfg.get('desks', 'all'),
                'position_report_path': position_path,
                'generated': bool(position_ok)
            })

        return generated

    def generate_summary_report(self) -> bool:
        """
        Generate a data-driven, comprehensive text summary of the workflow.

        The report includes summaries of input data, PnL results, financial
        figures, validation checks, and output file locations.

        Returns:
            True if report generated successfully, False otherwise.
        """
        logger.info("Generating summary report...")

        try:
            with open(self.summary_report_path, 'w', encoding='utf-8') as f:
                f.write("=" * 80 + "\n")
                f.write("Karbone Daily PnL Summary Report\n")
                f.write("=" * 80 + "\n")
                f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("\n")

                # Most recent day PnL (only if daily is enabled)
                if self._is_period_enabled('daily') and not self.pnl_df.empty:
                    most_recent_pnl = self.get_most_recent_day_pnl(self.pnl_df)
                    if not most_recent_pnl.empty:
                        most_recent_date = most_recent_pnl['date'].iloc[0]
                        total_recent_pnl = most_recent_pnl['usd_pnl_trading'].sum()

                        f.write("DAILY PnL BY DESK\n")
                        f.write("-" * 40 + "\n")
                        f.write(f"Date: {most_recent_date}\n")
                        f.write(f"Total Daily PnL: ${total_recent_pnl:,.2f}\n")

                        recent_by_desk = most_recent_pnl.groupby(
                            'desk'
                        )['usd_pnl_trading'].sum().reset_index()
                        for _, row in recent_by_desk.iterrows():
                            desk_name = self._format_desk_name(row['desk'])
                            f.write(f"{desk_name}: ${row['usd_pnl_trading']:,.2f}\n")
                        f.write("\n")

                # YTD PnL by desk (only if ytd is enabled)
                if self._is_period_enabled('ytd') and not self.pnl_df.empty:
                    pnl_by_year_desk = self.calculate_pnl_by_year_desk(self.pnl_df)
                    if not pnl_by_year_desk.empty:
                        current_year = datetime.now().year
                        ytd_pnl = pnl_by_year_desk[pnl_by_year_desk['year'] == current_year]
                        if not ytd_pnl.empty:
                            f.write("YTD PnL BY DESK\n")
                            f.write("-" * 40 + "\n")
                            total_ytd_pnl = ytd_pnl['usd_pnl_trading'].sum()
                            f.write(f"Total YTD PnL: ${total_ytd_pnl:,.2f}\n")
                            for _, row in ytd_pnl.iterrows():
                                desk_name = self._format_desk_name(row['desk'])
                                f.write(f"{desk_name}: ${row['usd_pnl_trading']:,.2f}\n")
                            f.write("\n")

                # PnL analysis results
                f.write("PnL ANALYSIS RESULTS\n")
                f.write("-" * 40 + "\n")
                f.write(f"PnL Records Generated: {self.results.pnl_records:,}\n")
                date_range_str = (
                    f"{self.results.date_range[0]} to {self.results.date_range[1]}"
                    if self.results.date_range[0] is not None
                    else "N/A"
                )
                f.write(f"Date Range: {date_range_str}\n")
                f.write(f"Products: {self.results.products}\n")
                f.write(f"Vintages: {self.results.vintages}\n")
                f.write(f"Desks: {self.results.desks}\n")
                f.write("\n")

                # Validation results
                f.write("VALIDATION RESULTS\n")
                f.write("-" * 40 + "\n")
                for check, result in self.results.validation_results.items():
                    f.write(f"{check}: {'PASS' if result else 'FAIL'}\n")
                f.write("\n")

                # Settings
                f.write("SETTINGS\n")
                f.write("-" * 40 + "\n")
                f.write(f"Analysis Date Filter: Trades >= {self.min_trade_date}\n")
                f.write("\n")

                f.write("=" * 80 + "\n")
                f.write("Analysis completed successfully.\n")
                f.write("=" * 80 + "\n")

            logger.info(f"Summary report saved to {self.summary_report_path}")
            return True

        except FileNotFoundError as e:
            logger.error(f"Output directory not found for summary report: {e}")
            return False
        except PermissionError as e:
            logger.error(f"Permission denied writing summary report: {e}")
            return False
        except OSError as e:
            logger.error(f"OS error writing summary report: {e}")
            return False

    def generate_html_report(self, breakout_type: Optional[str] = None, report_title: Optional[str] = None, tables: Optional[List[str]] = None) -> bool:
        """
        Generate a nicely formatted HTML report for email distribution.

        The report includes a table with desk | daily | WTD | MTD | QTD | YTD.
        Optionally includes a breakout table by product, vintage, portfolio, strategy, or combinations.

        Args:
            breakout_type: Optional breakout type ('product', 'vintage', 'product_and_vintage',
                'portfolio', 'strategy', or 'portfolio_and_strategy').
            report_title: Optional custom title for the report. Defaults to "Karbone Daily PnL Summary Report".
            tables: Optional list of sections to include. Defaults to all structural sections when None.
                Structural keys: 'desk_summary', 'daily_detail'.
                Breakout key: one of 'product', 'vintage', 'product_and_vintage',
                'portfolio', 'strategy', 'portfolio_and_strategy' â€” presence triggers the breakout table.

        Returns:
            True if report generated successfully, False otherwise.
        """
        logger.info("Generating HTML report...")

        try:
            if self.pnl_df.empty:
                logger.error("No PnL data available for HTML report generation")
                return False

            # Calculate all required metrics (only for enabled periods)
            most_recent_pnl = self.get_most_recent_day_pnl(self.pnl_df)
            
            # Start with desk column
            summary_table = pd.DataFrame()
            if not most_recent_pnl.empty:
                # Get unique desks
                desks = most_recent_pnl['desk'].unique()
                summary_table = pd.DataFrame({'desk': desks})
            else:
                summary_table = pd.DataFrame(columns=['desk'])

            # Add enabled periods
            if self._is_period_enabled('daily'):
                daily_by_desk = pd.DataFrame()
                if not most_recent_pnl.empty:
                    daily_by_desk = most_recent_pnl.groupby(
                        'desk'
                    )['usd_pnl_trading'].sum().reset_index()
                    daily_by_desk.columns = ['desk', 'daily_pnl']
                
                if not daily_by_desk.empty:
                    summary_table = summary_table.merge(daily_by_desk, on='desk', how='outer')
                else:
                    summary_table['daily_pnl'] = 0.0

            if self._is_period_enabled('wtd'):
                wtd_by_desk = self.calculate_wtd_pnl(self.pnl_df)
                if not wtd_by_desk.empty:
                    summary_table = summary_table.merge(wtd_by_desk, on='desk', how='outer')
                else:
                    summary_table['wtd_pnl'] = 0.0

            if self._is_period_enabled('mtd'):
                mtd_by_desk = self.calculate_mtd_pnl(self.pnl_df)
                if not mtd_by_desk.empty:
                    summary_table = summary_table.merge(mtd_by_desk, on='desk', how='outer')
                else:
                    summary_table['mtd_pnl'] = 0.0

            if self._is_period_enabled('qtd'):
                qtd_by_desk = self.calculate_qtd_pnl(self.pnl_df)
                if not qtd_by_desk.empty:
                    summary_table = summary_table.merge(qtd_by_desk, on='desk', how='outer')
                else:
                    summary_table['qtd_pnl'] = 0.0

            if self._is_period_enabled('ytd'):
                # Get latest date from PnL data and find prior trading day
                if not self.pnl_df.empty:
                    pnl_df_copy = self.pnl_df.copy()
                    pnl_df_copy['date'] = pd.to_datetime(pnl_df_copy['date'])
                    latest_date = pnl_df_copy['date'].max().date()
                    calendar = TradingCalendar()
                    prior_trading_day = calendar.get_previous_trading_day(latest_date)
                    prior_year = prior_trading_day.year
                else:
                    prior_year = datetime.now().year
                
                pnl_by_year_desk = self.calculate_pnl_by_year_desk(self.pnl_df)
                ytd_by_desk = pd.DataFrame()
                if not pnl_by_year_desk.empty:
                    ytd_by_desk = pnl_by_year_desk[
                        pnl_by_year_desk['year'] == prior_year
                    ][['desk', 'usd_pnl_trading']]
                    ytd_by_desk.columns = ['desk', 'ytd_pnl']
                
                if not ytd_by_desk.empty:
                    summary_table = summary_table.merge(ytd_by_desk, on='desk', how='outer')
                else:
                    summary_table['ytd_pnl'] = 0.0

            summary_table = summary_table.fillna(0)
            summary_table = summary_table.sort_values('desk')

            # Determine report date (effective as-of date)
            as_of_date = get_effective_reporting_date()

            # Determine single-desk mode: when the filtered df contains exactly one desk
            unique_desks = list(summary_table['desk'].unique())
            single_desk = unique_desks[0] if len(unique_desks) == 1 else None

            # Determine which modules to render based on tables config
            modules = self._modules_from_tables_cfg(tables)

            # Build the report data dict (bridges DataFrames â†’ nested dict)
            from src.reporting.data_builder import build_report_data
            from src.reporting.html_builder import render as render_html
            from datetime import datetime as _dt
            from pathlib import Path

            calendar = TradingCalendar()
            report_data = build_report_data(
                reporter=self,
                pnl_df=self.pnl_df,
                most_recent_pnl=most_recent_pnl,
                summary_table=summary_table,
                as_of_date=as_of_date,
                calendar=calendar,
                report_title=report_title,
                single_desk=single_desk,
                generated_ts=_dt.now().strftime('%Y-%m-%d %H:%M:%S'),
            )

            include_qt_ytd = (self._is_period_enabled('qtd') and
                              self._is_period_enabled('ytd'))
            # Sparkline PNGs live in a per-report subdirectory named
            # <html_stem>_images/. EmailSender._attach_inline_images walks this
            # directory and attaches each PNG with CID <stem@karbone>;
            # html_builder emits matching <img src="cid:...">. The per-report
            # directory keeps each report's spark_firm.png and per-desk PNGs
            # isolated â€” a shared images/ folder would let later renders
            # overwrite earlier ones and would attach every PNG to every email
            # (e.g. power_short_term graph in the fuels report).
            _html_path = Path(self.html_report_path)
            images_dir = _html_path.parent / f'{_html_path.stem}_images'
            html_content = render_html(
                report_data,
                sparkline_dir=images_dir,
                include_qt_ytd=include_qt_ytd,
                modules=modules,
                single_desk=single_desk,
            )

            with open(self.html_report_path, 'w', encoding='utf-8') as f:
                f.write(html_content)

            logger.info(f"HTML report saved to {self.html_report_path}")
            return True

        except OSError:
            logger.exception("Error generating HTML report")
            return False

    def _modules_from_tables_cfg(self, tables: Optional[List[str]]) -> List[str]:
        """Map a tables config list to v4-hybrid module keys."""
        if tables is None:
            return ['portfolio_strategy', 'product_vintage', 'daily_detail']
        modules = []
        tables_lower = {t.lower() for t in tables}
        if any(k in tables_lower for k in ('portfolio_and_strategy', 'portfolio', 'strategy')):
            modules.append('portfolio_strategy')
        if any(k in tables_lower for k in ('product_and_vintage', 'product', 'vintage')):
            modules.append('product_vintage')
        if 'daily_detail' in tables_lower:
            modules.append('daily_detail')
        # Fallback: if only desk_summary was listed, show no breakout modules
        return modules

    def _format_long_date(self, d: date) -> str:
        return f"{d.strftime('%B')} {d.day}, {d.year}"

    def _detect_fees(self, df: pd.DataFrame) -> pd.Series:
        """Return boolean mask identifying fee rows via is_fee, fee_type, and product keywords."""
        fee_mask = pd.Series(False, index=df.index)
        if 'is_fee' in df.columns:
            fee_mask = fee_mask | df['is_fee'].fillna(False).astype(bool)
        if 'fee_type' in df.columns:
            fee_type_lower = df['fee_type'].astype(str).str.lower()
            fee_mask = fee_mask | (
                (fee_type_lower != 'regular') &
                (fee_type_lower != 'none') &
                (fee_type_lower != '') &
                (fee_type_lower != 'nan')
            )
        fee_mask = fee_mask | df['product'].astype(str).str.lower().str.contains(
            '|'.join(constants.FEE_KEYWORDS), na=False, regex=True
        )
        return fee_mask

    def _apply_fee_vintage_collapse(self, df: pd.DataFrame, fee_mask: pd.Series) -> None:
        """Collapse vintage to year for fee rows in-place (e.g. '01-2025' -> '2025')."""
        df.loc[fee_mask, 'vintage'] = df.loc[fee_mask, 'vintage'].apply(
            lambda v: self._extract_year_from_vintage(v) or ''
        )

    def _format_notional(self, value: float) -> str:
        """
        Format notional value as integer without decimals, using parentheses for negatives.
        Returns "-" for zero values.

        Args:
            value: Numeric value to format.

        Returns:
            Formatted string (e.g., "1,234", "(1,234)", or "-" for zero).
        """
        abs_value = abs(value)
        rounded = int(round(abs_value))
        if rounded == 0:
            return "-"
        formatted = f"{rounded:,}"
        if value < 0:
            return f"({formatted})"
        return formatted

    def _format_notional_with_dollar(self, value: float) -> str:
        """
        Format notional value with a left-anchored dollar sign and right-aligned number.
        Returns "-" for zero values.

        Args:
            value: Numeric value to format.

        Returns:
            HTML string with spans to align "$" left and digits right (e.g.,
            "<span ...>$</span><span ...>1,234</span>" or "($ ... )").
        """
        abs_value = abs(value)
        rounded = int(round(abs_value))
        left = "$"
        formatted = f"{rounded:,}"

        if rounded == 0:
            right = "-"
        elif value < 0:
            right = f"({formatted})"
        else:
            right = formatted

        return (
            f"<span style='float:left;'>{left}</span>"
            f"<span style='float:right;'>{right}</span>"
        )

    def _format_price(self, value: float, decimals: int = 4) -> str:
        """
        Format price value with specified decimal places, using parentheses for negatives.
        Returns "-" for NaN values.

        Args:
            value: Numeric value to format.
            decimals: Number of decimal places (default 4).

        Returns:
            Formatted string (e.g., "1,234.5678", "(1,234.5678)", or "-" for NaN).
        """
        if pd.isna(value):
            return "-"
        
        abs_value = abs(value)
        formatted = f"{abs_value:,.{decimals}f}"
        
        if value < 0:
            return f"({formatted})"
        return formatted

    def _format_price_with_dollar(self, value: float, decimals: int = 4) -> str:
        """
        Format price value with a left-anchored dollar sign and right-aligned number.
        Returns "-" for NaN values.

        Args:
            value: Numeric value to format.
            decimals: Number of decimal places (default 4).

        Returns:
            HTML string with spans to align "$" left and digits right (e.g.,
            "<span ...>$</span><span ...>1,234.5678</span>" or "($ ... )").
        """
        if pd.isna(value):
            return "-"
        
        left = "$"
        abs_value = abs(value)
        formatted = f"{abs_value:,.{decimals}f}"

        if value < 0:
            right = f"({formatted})"
        else:
            right = formatted

        return (
            f"<span style='float:left;'>{left}</span>"
            f"<span style='float:right;'>{right}</span>"
        )

    def _format_desk_name(self, desk: str) -> str:
        """
        Map desk codes to display names using constants.

        Args:
            desk: Desk code (e.g., "europe", "mgmt", "power_forward", "fuels").

        Returns:
            Formatted desk name (e.g., "Europe", "Management", "Power Forward", "Fuels").
        """
        return constants.DESK_DISPLAY_NAMES.get(desk.lower(), desk.title())
    
    def _format_product_name(self, product: Optional[str]) -> str:
        """
        Format product name for display, capitalizing "Broker" when present.

        Args:
            product: Product name string.

        Returns:
            Formatted product name with "Broker" capitalized.
        """
        if not product or pd.isna(product):
            return "N/A"
        product_str = str(product)
        # Capitalize "Broker" (case-insensitive replacement)
        product_str = re.sub(r'\b(broker)\b', 'Broker', product_str, flags=re.IGNORECASE)
        return product_str
    
    def _extract_year_from_vintage(self, vintage: Optional[str]) -> Optional[str]:
        """
        Extract year from vintage string (e.g., "01-2025" -> "2025", "02-2025" -> "2025").
        
        Args:
            vintage: Vintage string in format like "01-2025" or "02-2025".
            
        Returns:
            Year string (e.g., "2025") or None if vintage is invalid/None.
        """
        if not vintage or pd.isna(vintage):
            return None
        vintage_str = str(vintage).strip()
        # Try to extract year from formats like "01-2025", "02-2025", etc.
        # Look for pattern: digits-dash-digits (year is after the dash)
        match = re.search(r'-\s*(\d{4})\b', vintage_str)
        if match:
            return match.group(1)
        # If no match, try to find 4-digit year anywhere
        match = re.search(r'\b(\d{4})\b', vintage_str)
        if match:
            return match.group(1)
        # If still no match, return the original vintage
        return vintage_str

    def _format_volume(self, value: Optional[float]) -> str:
        """
        Format volume values as rounded integers with thousands separators.
        Returns "-" for zero values.

        Args:
            value: Volume value to format.

        Returns:
            Formatted string with commas, or "-" for zero.
        """
        if value is None or pd.isna(value):
            return "-"
        rounded = int(round(value))
        if rounded == 0:
            return "-"
        formatted = f"{abs(rounded):,}"
        return f"({formatted})" if value < 0 else formatted

    def _format_percent(self, value: Optional[float], decimals: int = 2) -> str:
        """
        Format percentage values.

        Args:
            value: Percentage value to format (e.g., 15.5 for 15.5%).
            decimals: Number of decimal places.

        Returns:
            Formatted string with % sign, or "N/A" if None.
        """
        if value is None or pd.isna(value):
            return "N/A"
        formatted = f"{abs(value):.{decimals}f}%"
        return f"({formatted})" if value < 0 else formatted

    def _build_base_styles(self) -> str:
        """
        Centralized CSS shared by Daily PnL and Daily Position reports.
        """
        Colors = constants.ReportColors
        return f"""
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: {Colors.BACKGROUND_ALT};
            color: {Colors.TEXT};
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background-color: {Colors.BACKGROUND};
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.05);
            border: 1px solid {Colors.BORDER};
        }}
        .header {{
            text-align: center;
            margin-bottom: 30px;
            border-bottom: 3px solid {Colors.PRIMARY_BLUE};
            padding-bottom: 20px;
        }}
        .header h1 {{
            color: {Colors.TEXT};
            margin: 0 0 10px 0;
            font-size: 28px;
        }}
        .subtitle {{
            color: {Colors.TEXT_SECONDARY};
            font-size: 14px;
        }}
        .table-wrapper {{
            width: 100%;
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
        }}
        table.report-table {{
            width: 100%;
            border-collapse: collapse;
            margin: 20px 0;
            background-color: {Colors.BACKGROUND};
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
            border: 1px solid {Colors.BORDER};
            box-sizing: border-box;
        }}
        table.report-table th {{
            background-color: {Colors.PRIMARY_BLUE};
            color: {Colors.BACKGROUND};
            padding: 15px;
            text-align: left;
            font-weight: bold;
            text-transform: uppercase;
            font-size: 12px;
            letter-spacing: 1px;
        }}
        table.report-table td {{
            padding: 12px;
            border-bottom: 1px solid {Colors.BORDER};
        }}
        table.report-table tfoot td {{
            background-color: {Colors.BACKGROUND_SUBTLE};
            font-weight: bold;
            padding: 15px;
            border-top: 2px solid {Colors.PRIMARY_BLUE};
        }}
        .text-left {{ text-align: left; }}
        .text-right {{ text-align: right; }}
        .text-center {{ text-align: center; }}
        table.report-table th.text-center {{ text-align: center; }}
        table.report-table th.text-right {{ text-align: right; }}
        .text-muted {{ color: {Colors.TEXT_SECONDARY}; }}
        .bold {{ font-weight: bold; }}
        .numeric-positive {{ color: {Colors.PRIMARY_GREEN}; }}
        .numeric-negative {{ color: {Colors.ERROR}; }}
        .subheader-row td {{
            background-color: {Colors.BACKGROUND_SUBTLE};
            font-weight: bold;
            border-bottom: 1px solid {Colors.BORDER};
        }}
        .border-right-strong {{ border-right: 2px solid {Colors.BORDER}; }}
        .footnote {{
            margin-top: 20px;
            padding-top: 15px;
            border-top: 1px solid {Colors.BORDER};
            color: {Colors.TEXT_SECONDARY};
            font-size: 11px;
            font-style: italic;
        }}
        .footer {{
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid {Colors.BORDER};
            text-align: center;
            color: {Colors.TEXT_SECONDARY};
            font-size: 12px;
        }}
        @media (max-width: 768px) {{
            body {{
                padding: 10px;
            }}
            .container {{
                padding: 15px;
                border-radius: 4px;
            }}
            .header h1 {{
                font-size: 20px;
            }}
            .subtitle {{
                font-size: 12px;
            }}
            h2 {{
                font-size: 16px;
            }}
            table.report-table {{
                font-size: 12px;
                min-width: 700px;
            }}
            table.report-table th {{
                padding: 10px 6px;
                font-size: 10px;
                letter-spacing: 0.5px;
            }}
            table.report-table td,
            table.report-table tfoot td {{
                padding: 8px 6px !important;
            }}
            .footnote {{
                font-size: 10px;
            }}
            .footer {{
                font-size: 11px;
            }}
        }}
        @media (max-width: 480px) {{
            body {{
                padding: 5px;
            }}
            .container {{
                padding: 10px;
            }}
            .header h1 {{
                font-size: 18px;
            }}
            table.report-table {{
                font-size: 11px;
            }}
            table.report-table th {{
                padding: 8px 4px;
                font-size: 9px;
            }}
            table.report-table td,
            table.report-table tfoot td {{
                padding: 6px 4px !important;
            }}
        }}
        """

    def _get_currency_rate(self, currency: Optional[str]) -> float:
        """
        Return the USD conversion rate for the given currency code.

        Args:
            currency: Currency code (e.g., 'usd', 'gbp').

        Returns:
            Conversion rate to USD.
        """
        if not currency:
            currency = constants.DEFAULT_CURRENCY
        currency = str(currency).lower()
        return constants.CURRENCY_CONVERSION_RATES.get(
            currency,
            constants.CURRENCY_CONVERSION_RATES.get(constants.DEFAULT_CURRENCY, 1.0)
        )

    def _prepare_volume_exposures(self, most_recent_pnl: pd.DataFrame) -> pd.DataFrame:
        """
        Build a summary of exposure volumes per desk/product/vintage.

        Aggregates prior day qty, current day qty, and computes change and
        notional (current qty * current mark), converted to USD.

        Args:
            most_recent_pnl: Most recent day's PnL data.

        Returns:
            DataFrame with exposure volumes.
        """
        if most_recent_pnl.empty:
            return pd.DataFrame(columns=[
                'desk', 'product', 'vintage', 'change_qty',
                'current_day_qty', 'notional_value'
            ])

        exposures = most_recent_pnl.copy()
        
        if 'is_fee' in exposures.columns:
            exposures = exposures[~exposures['is_fee'].fillna(False)]

        for col in ['qty_start', 'qty_end', 'px_mark_cd']:
            if col not in exposures.columns:
                exposures[col] = 0.0

        # Handle fee vintage collapsing before date splitting
        # Check if fee_type column exists to identify all fees
        has_fee_type = 'fee_type' in exposures.columns
        
        # Identify all fees (broker, NFA, clearing, commission, etc.)
        if has_fee_type:
            fee_mask = (
                exposures['fee_type'].astype(str).str.lower() != 'regular'
            ) & (
                exposures['fee_type'].astype(str).str.lower() != 'none'
            ) & (
                exposures['fee_type'].astype(str).str.lower() != ''
            ) & (
                exposures['fee_type'].astype(str).str.lower() != 'nan'
            )
        else:
            # Fallback: check if product contains fee-related keywords
            fee_mask = exposures['product'].astype(str).str.lower().str.contains(
                '|'.join(constants.FEE_KEYWORDS), na=False, regex=True
            )
        
        if fee_mask.any():
            self._apply_fee_vintage_collapse(exposures, fee_mask)

        # Ensure grouping columns have valid values
        exposures['product'] = exposures['product'].fillna('Unknown')
        exposures['vintage'] = exposures['vintage'].fillna('')

        # Get earliest and latest dates from the date range
        if 'date' in exposures.columns:
            exposures['_date_dt'] = pd.to_datetime(exposures['date'])
            earliest_date = exposures['_date_dt'].min()
            latest_date = exposures['_date_dt'].max()
            
            # Get prior day qty from earliest date (use qty_start, fallback to qty_end if qty_start not available)
            earliest_data = exposures[exposures['_date_dt'] == earliest_date].copy()
            earliest_data['prior_day_qty'] = earliest_data['qty_start'].fillna(
                earliest_data['qty_end'].fillna(0.0)
            ).astype(float)
            
            # Get current day qty from latest date
            latest_data = exposures[exposures['_date_dt'] == latest_date].copy()
            latest_data['current_day_qty'] = latest_data['qty_end'].fillna(0.0).astype(float)
            latest_data['px_mark_cd'] = latest_data['px_mark_cd'].fillna(0.0).astype(float)
            latest_data['px_mark_pd'] = latest_data.get('px_mark_pd', latest_data['px_mark_cd']).fillna(0.0).astype(float)
            
            # Group prior data by desk/product/vintage
            prior_grouped = earliest_data.groupby(
                ['desk', 'product', 'vintage'],
                dropna=False,
                as_index=False
            ).agg({
                'prior_day_qty': 'sum'
            })
            
            # Group current data by desk/product/vintage
            # Include currency if available (use first value since it should be consistent per product)
            agg_dict = {
                'current_day_qty': 'sum',
                'px_mark_cd': 'mean',
                'px_mark_pd': 'mean'
            }
            if 'currency' in latest_data.columns:
                agg_dict['currency'] = 'first'
            
            current_grouped = latest_data.groupby(
                ['desk', 'product', 'vintage'],
                dropna=False,
                as_index=False
            ).agg(agg_dict)
            
            # Merge prior and current
            exposures = prior_grouped.merge(
                current_grouped,
                on=['desk', 'product', 'vintage'],
                how='outer'
            )
            
            # Fill NaN values
            exposures['prior_day_qty'] = exposures['prior_day_qty'].fillna(0.0)
            exposures['current_day_qty'] = exposures['current_day_qty'].fillna(0.0)
            exposures['px_mark_cd'] = exposures['px_mark_cd'].fillna(0.0)
            exposures['px_mark_pd'] = exposures['px_mark_pd'].fillna(0.0)
            
            # Calculate change as difference between current and prior
            exposures['change_qty'] = exposures['current_day_qty'] - exposures['prior_day_qty']
            
            # Calculate notional values
            exposures['notional_value'] = exposures['current_day_qty'] * exposures['px_mark_cd']
            
            # Clean up temporary column
            exposures = exposures.drop(columns=['_date_dt'], errors='ignore')
        else:
            # Fallback if no date column (shouldn't happen, but handle gracefully)
            exposures['prior_day_qty'] = exposures['qty_start'].fillna(0.0).astype(float)
            exposures['current_day_qty'] = exposures['qty_end'].fillna(0.0).astype(float)
            exposures['px_mark_cd'] = exposures['px_mark_cd'].fillna(0.0).astype(float)
            exposures['px_mark_pd'] = exposures.get('px_mark_pd', exposures['px_mark_cd']).fillna(0.0).astype(float)
            exposures['change_qty'] = exposures['current_day_qty'] - exposures['prior_day_qty']
            exposures['notional_value'] = exposures['current_day_qty'] * exposures['px_mark_cd']

        # Handle currency conversion
        if 'currency' not in exposures.columns:
            exposures['currency'] = constants.DEFAULT_CURRENCY

        exposures['currency'] = (
            exposures['currency']
            .fillna(constants.DEFAULT_CURRENCY)
            .astype(str)
            .str.lower()
        )
        exposures['conversion_rate'] = exposures['currency'].apply(self._get_currency_rate)
        exposures['notional_value_usd'] = exposures['notional_value'] * exposures['conversion_rate']

        # Filter to rows with material positions
        exposures = exposures[
            (exposures['prior_day_qty'].abs() > constants.EPSILON) |
            (exposures['current_day_qty'].abs() > constants.EPSILON)
        ]

        if exposures.empty:
            return pd.DataFrame(columns=[
                'desk', 'product', 'vintage', 'change_qty',
                'current_day_qty', 'notional_value'
            ])
        
        # Data is already grouped by desk/product/vintage, so use directly
        grouped = exposures.copy()
        grouped['notional_value'] = grouped['notional_value_usd'].abs()
        
        # Calculate percent change: |daily change| / |prior day position|
        grouped['percent_change'] = (
            grouped['change_qty'].abs() / grouped['prior_day_qty'].abs()
        )
        grouped.loc[grouped['prior_day_qty'].abs() < constants.EPSILON, 'percent_change'] = None
        grouped.loc[grouped['percent_change'] == float('inf'), 'percent_change'] = None
        grouped.loc[grouped['percent_change'] == -float('inf'), 'percent_change'] = None
        
        # Calculate percent of desk exposure (notional value as % of total desk notional)
        desk_totals = grouped.groupby('desk')['notional_value'].transform('sum')
        grouped['percent_of_desk_exposure'] = (
            grouped['notional_value'] / desk_totals * 100
        )
        grouped.loc[desk_totals.abs() < constants.EPSILON, 'percent_of_desk_exposure'] = None
        grouped.loc[grouped['percent_of_desk_exposure'] == float('inf'), 'percent_of_desk_exposure'] = None
        grouped.loc[grouped['percent_of_desk_exposure'] == -float('inf'), 'percent_of_desk_exposure'] = None

        # Sort by display name so desk sections appear alphabetically as rendered
        grouped['_vintage_sort'] = grouped['vintage'].apply(parse_vintage_for_ordering)
        grouped['desk_display'] = grouped['desk'].apply(self._format_desk_name)
        grouped = grouped.sort_values(
            ['desk_display', 'product', '_vintage_sort', 'vintage'],
            na_position='last'
        )

        grouped = grouped.drop(columns=['desk_display', '_vintage_sort'])

        return grouped[[
            'desk', 'product', 'vintage', 'change_qty',
            'current_day_qty', 'prior_day_qty', 'notional_value',
            'percent_change', 'percent_of_desk_exposure', 'px_mark_cd'
        ]]

    def _build_desk_notional_summary_table(
        self,
        exposures: pd.DataFrame,
        most_recent_date: str
    ) -> str:
        """
        Build a desk-level summary table showing current notional exposure.

        Args:
            exposures: DataFrame produced by _prepare_volume_exposures.
            most_recent_date: Date string for the most recent day.

        Returns:
            HTML string for the desk summary table.
        """
        if exposures.empty:
            rows = f"""
                <tr>
                    <td colspan="2" class="text-center text-muted">
                        No exposure data available for {most_recent_date}.
                    </td>
                </tr>
            """
            totals_row = ""
        else:
            # Aggregate by desk
            desk_summary = exposures.groupby('desk', as_index=False).agg({
                'notional_value': 'sum'
            })
            
            rows = ""
            total_current = 0.0
            
            for _, row in desk_summary.iterrows():
                desk_name = self._format_desk_name(row['desk'])
                total_current_notional = row['notional_value']
                total_current_class = (
                    "numeric-positive" if total_current_notional >= 0 else "numeric-negative"
                )
                total_current += total_current_notional

                rows += f"""
                    <tr>
                        <td class="text-left bold">{desk_name}</td>
                        <td class="text-right {total_current_class}">
                            {self._format_notional(total_current_notional)}</td>
                    </tr>
                """
            
            # Add totals row
            total_current_class = "numeric-positive" if total_current >= 0 else "numeric-negative"
            totals_row = f"""
                <tfoot>
                    <tr>
                        <td class="text-left bold">TOTAL</td>
                        <td class="text-right bold {total_current_class}">
                            {self._format_notional(total_current)}</td>
                    </tr>
                </tfoot>
            """

        table_html = f"""
            <table class="report-table" style="table-layout: fixed; width: 100%;">
                <thead>
                    <tr>
                        <th class="text-left">Desk</th>
                        <th class="text-right">Current</th>
                    </tr>
                </thead>
                <tbody>
                    {rows}
                </tbody>
                {totals_row}
            </table>
        """

        return table_html

    def _build_volume_exposure_table_html(
        self,
        most_recent_pnl: pd.DataFrame,
        most_recent_date: str
    ) -> Tuple[str, pd.DataFrame]:
        """
        Build the HTML table snippet for volumetric exposures.
        
        Includes: Desk, Product, Vintage, Daily Change, Current Position,
        Current Notional Value, Percent of Desk Exposure. Grouped by desk,
        with summary rows showing Total Long Exposure, Total Short Exposure,
        and Net Exposure.

        Args:
            most_recent_pnl: Most recent day's PnL data.
            most_recent_date: Date string for the most recent day.

        Returns:
            Tuple containing the HTML string for the table and the exposures DataFrame.
        """
        exposures = self._prepare_volume_exposures(most_recent_pnl)

        if exposures.empty:
            rows = f"""
                <tr>
                    <td colspan="7" class="text-center text-muted">
                        No material volume exposures for {most_recent_date}.
                    </td>
                </tr>
            """
            summary_rows = ""
        else:
            rows = ""
            current_desk = None
            total_long_exposure = 0.0
            total_short_exposure = 0.0
            gross_exposure = 0.0

            for _, row in exposures.iterrows():
                desk = row['desk']
                desk_name = self._format_desk_name(desk)
                product = self._format_product_name(row['product'])
                # Leave vintage blank if empty, don't show "N/A"
                vintage_val = row['vintage']
                vintage = '' if (not vintage_val or pd.isna(vintage_val) or str(vintage_val).strip() == '') else str(vintage_val)
                
                # Volumetric values
                prior_vol = self._format_volume(row['prior_day_qty'])
                change_vol = self._format_volume(row['change_qty'])
                current_vol = self._format_volume(row['current_day_qty'])
                
                # Current notional values - use dollar sign for first row in each desk subsection
                if current_desk != desk:
                    current_notional = self._format_notional_with_dollar(row['notional_value'])
                else:
                    current_notional = self._format_notional(row['notional_value'])
                
                pct_desk = self._format_percent(row['percent_of_desk_exposure'])

                # Add desk subheader row if new desk
                if current_desk != desk:
                    rows += f"""
                        <tr class="subheader-row">
                            <td colspan="7" class="text-left">
                                {desk_name}
                            </td>
                        </tr>
                    """
                    current_desk = desk

                # Track exposures for summary
                current_qty = row['current_day_qty']
                current_notional_val = row['notional_value']
                if current_qty > 0:
                    total_long_exposure += current_notional_val
                elif current_qty < 0:
                    total_short_exposure += abs(current_notional_val)
                gross_exposure += current_notional_val

                change_vol_class = "numeric-positive" if row['change_qty'] >= 0 else "numeric-negative"

                rows += f"""
                    <tr>
                        <td class="text-left">{product}</td>
                        <td class="text-left border-right-strong">{vintage}</td>
                        <td class="text-right">{prior_vol}</td>
                        <td class="text-right {change_vol_class}">{change_vol}</td>
                        <td class="text-right border-right-strong">{current_vol}</td>
                        <td class="text-right">{current_notional}</td>
                        <td class="text-right">{pct_desk}</td>
                    </tr>
                """

            # Summary rows
            gross_class = "numeric-positive" if gross_exposure >= 0 else "numeric-negative"
            summary_rows = f"""
                <tfoot>
                    <tr>
                        <td colspan="5" class="text-left bold">Total Long Exposure</td>
                        <td class="text-right bold">{self._format_notional_with_dollar(total_long_exposure)}</td>
                        <td></td>
                    </tr>
                    <tr>
                        <td colspan="5" class="text-left bold">Total Short Exposure</td>
                        <td class="text-right bold">{self._format_notional_with_dollar(total_short_exposure)}</td>
                        <td></td>
                    </tr>
                    <tr>
                        <td colspan="5" class="text-left bold">Gross Exposure</td>
                        <td class="text-right bold {gross_class}">
                            {self._format_notional_with_dollar(gross_exposure)}</td>
                        <td></td>
                    </tr>
                </tfoot>
            """

        table_html = f"""
            <table class="report-table" style="table-layout: fixed; width: 100%;">
                <thead>
                    <tr>
                        <th colspan="2" class="text-center border-right-strong"></th>
                        <th colspan="3" class="text-center border-right-strong">Volumetric Exposure</th>
                        <th colspan="2" class="text-center">Notional Value</th>
                    </tr>
                    <tr>
                        <th class="text-left">Product</th>
                        <th class="text-left border-right-strong">Vintage</th>
                        <th class="text-center">Prior</th>
                        <th class="text-center">Change</th>
                        <th class="text-center border-right-strong">Current</th>
                        <th class="text-center">Current</th>
                        <th class="text-center">% of Desk</th>
                    </tr>
                </thead>
                <tbody>
                    {rows}
                </tbody>
                {summary_rows}
            </table>
        """

        return table_html, exposures

    def _build_nonzero_change_table_html(
        self,
        exposures: pd.DataFrame,
        most_recent_date: str
    ) -> str:
        """
        Build an HTML table showing only products with non-zero daily volume changes.
        
        Shows: Product, Vintage, Volumetric Exposure (Prior/Change/Current),
        Notional Value (Current/% of Desk). Grouped by desk.

        Args:
            exposures: DataFrame produced by _prepare_volume_exposures.
            most_recent_date: Date string for the most recent day.

        Returns:
            HTML string for the non-zero change table (or a message if none).
        """
        if exposures.empty:
            rows = f"""
                <tr>
                    <td colspan="7" class="text-center text-muted">
                        No products with a non-zero change in volume for {most_recent_date}.
                    </td>
                </tr>
            """
        else:
            # Filter to rows where the daily change is non-zero
            nonzero = exposures[exposures['change_qty'].abs() > constants.EPSILON].copy()

            if nonzero.empty:
                rows = f"""
                    <tr>
                        <td colspan="7" class="text-center text-muted">
                            No products with a non-zero change in volume for {most_recent_date}.
                        </td>
                    </tr>
                """
            else:
                # Sort desks alphabetically by display name (to match Current Position Detail),
                # then by largest absolute daily change and notional value.
                nonzero['desk_display'] = nonzero['desk'].apply(self._format_desk_name)
                nonzero['_abs_change'] = nonzero['change_qty'].abs()
                nonzero = nonzero.sort_values(
                    ['desk_display', '_abs_change', 'notional_value'],
                    ascending=[True, False, False]
                )
                nonzero = nonzero.drop(columns=['_abs_change', 'desk_display'])

                rows = ""
                current_desk = None

                for _, row in nonzero.iterrows():
                    desk = row['desk']
                    desk_name = self._format_desk_name(desk)
                    product = self._format_product_name(row['product'])
                    # Leave vintage blank if empty, don't show "N/A"
                    vintage_val = row['vintage']
                    vintage = '' if (not vintage_val or pd.isna(vintage_val) or str(vintage_val).strip() == '') else str(vintage_val)
                    
                    # Volumetric values
                    prior_vol = self._format_volume(row['prior_day_qty'])
                    change_vol = self._format_volume(row['change_qty'])
                    current_vol = self._format_volume(row['current_day_qty'])
                    
                    # Current notional values - use dollar sign for first row in each desk subsection
                    if current_desk != desk:
                        current_notional = self._format_notional_with_dollar(row['notional_value'])
                    else:
                        current_notional = self._format_notional(row['notional_value'])
                    
                    pct_desk = self._format_percent(row['percent_of_desk_exposure'])

                    change_vol_class = "numeric-positive" if row['change_qty'] >= 0 else "numeric-negative"

                    # Add desk subheader row if new desk
                    if current_desk != desk:
                        rows += f"""
                            <tr class="subheader-row">
                                <td colspan="7" class="text-left">
                                    {desk_name}
                                </td>
                            </tr>
                        """
                        current_desk = desk

                    rows += f"""
                        <tr>
                            <td class="text-left">{product}</td>
                            <td class="text-left border-right-strong">{vintage}</td>
                            <td class="text-right">{prior_vol}</td>
                            <td class="text-right {change_vol_class}">{change_vol}</td>
                            <td class="text-right border-right-strong">{current_vol}</td>
                            <td class="text-right">{current_notional}</td>
                            <td class="text-right">{pct_desk}</td>
                        </tr>
                    """

        table_html = f"""
            <table class="report-table" style="table-layout: fixed; width: 100%;">
                <thead>
                    <tr>
                        <th colspan="2" class="text-center border-right-strong"></th>
                        <th colspan="3" class="text-center border-right-strong">Volumetric Exposure</th>
                        <th colspan="2" class="text-center">Notional Value</th>
                    </tr>
                    <tr>
                        <th class="text-left">Product</th>
                        <th class="text-left border-right-strong">Vintage</th>
                        <th class="text-center">Prior</th>
                        <th class="text-center">Change</th>
                        <th class="text-center border-right-strong">Current</th>
                        <th class="text-center">Current</th>
                        <th class="text-center">% of Desk</th>
                    </tr>
                </thead>
                <tbody>
                    {rows}
                </tbody>
            </table>
        """

        return table_html

    def _build_pnl_breakout_table_html(
        self,
        breakout_df: pd.DataFrame,
        breakout_type: str,
        most_recent_date: str
    ) -> str:
        """
        Build HTML table for PnL breakout by product, vintage, or both.
        
        Args:
            breakout_df: DataFrame from _prepare_pnl_breakout with PnL by grouping.
            breakout_type: One of 'product', 'vintage', or 'product_and_vintage'.
            most_recent_date: Date string for the most recent day.
            
        Returns:
            HTML string for the breakout table.
        """
        # Get enabled periods
        enabled_periods = self._get_enabled_periods()
        period_labels = self._PERIOD_LABELS
        
        _two_col_breakouts = {'product_and_vintage', 'portfolio_and_strategy'}
        if breakout_df.empty:
            if breakout_type == 'product':
                heading = "Product"
            elif breakout_type == 'vintage':
                heading = "Vintage"
            elif breakout_type == 'portfolio':
                heading = "Portfolio"
            elif breakout_type == 'strategy':
                heading = "Strategy"
            elif breakout_type == 'portfolio_and_strategy':
                heading = "Portfolio and Strategy"
            else:
                heading = "Product and Vintage"

            num_cols = (2 if breakout_type in _two_col_breakouts else 1) + len(enabled_periods)
            rows = f"""
                <tr>
                    <td colspan="{num_cols}" class="text-center text-muted">
                        No PnL data available for {heading} breakdown on {most_recent_date}.
                    </td>
                </tr>
            """
            totals_row = ""
        else:
            # Calculate totals for enabled periods only
            totals = {}
            for period in enabled_periods:
                col_name = self._get_period_column_name(period)
                totals[period] = breakout_df[col_name].sum() if col_name in breakout_df.columns else 0.0
            
            rows = ""
            first_row = True
            current_desk = None
            for _, row in breakout_df.iterrows():
                desk = row['desk']
                desk_name = self._format_desk_name(desk)
                
                # Add desk subheader row if new desk
                if current_desk != desk:
                    num_cols = (2 if breakout_type in _two_col_breakouts else 1) + len(enabled_periods)
                    rows += f"""
                        <tr class="subheader-row">
                            <td colspan="{num_cols}" class="text-left">
                                {desk_name}
                            </td>
                        </tr>
                    """
                    current_desk = desk
                    first_row = True  # Reset first_row for new desk section
                
                # Determine grouping columns and values
                if breakout_type == 'product':
                    product = self._format_product_name(row['product'])
                    vintage = None
                elif breakout_type == 'vintage':
                    product = None
                    vintage_val = row['vintage']
                    vintage = '' if (not vintage_val or pd.isna(vintage_val) or str(vintage_val).strip() == '') else str(vintage_val)
                elif breakout_type == 'product_and_vintage':
                    product = self._format_product_name(row['product'])
                    vintage_val = row['vintage']
                    vintage = '' if (not vintage_val or pd.isna(vintage_val) or str(vintage_val).strip() == '') else str(vintage_val)
                elif breakout_type == 'portfolio':
                    portfolio_val = row['portfolio']
                    col1 = '' if (not portfolio_val or pd.isna(portfolio_val) or str(portfolio_val).strip() == '') else str(portfolio_val)
                    col2 = None
                elif breakout_type == 'strategy':
                    strategy_val = row['strategy']
                    col1 = 'N/A' if (not strategy_val or pd.isna(strategy_val) or str(strategy_val).strip() in ('', 'Unknown')) else str(strategy_val)
                    col2 = None
                else:  # portfolio_and_strategy
                    portfolio_val = row['portfolio']
                    strategy_val = row['strategy']
                    col1 = '' if (not portfolio_val or pd.isna(portfolio_val) or str(portfolio_val).strip() == '') else str(portfolio_val)
                    col2 = 'N/A' if (not strategy_val or pd.isna(strategy_val) or str(strategy_val).strip() in ('', 'Unknown')) else str(strategy_val)
                
                # Apply formatting (dollar sign for first row of each desk section)
                fmt = self._format_notional_with_dollar if first_row else self._format_notional
                
                # Build period cells for enabled periods
                period_cells = ""
                for period in enabled_periods:
                    col_name = self._get_period_column_name(period)
                    pnl_value = row.get(col_name, 0.0)
                    pnl_class = "numeric-positive" if pnl_value >= 0 else "numeric-negative"
                    # YTD gets bold styling
                    bold_class = "bold " if period == 'ytd' else ""
                    period_cells += f'<td class="text-right {bold_class}{pnl_class}">{fmt(pnl_value)}</td>'
                
                # Build row HTML
                if breakout_type == 'product':
                    rows += f"""
                        <tr>
                            <td class="text-left">{product}</td>
                            {period_cells}
                        </tr>
                    """
                elif breakout_type == 'vintage':
                    rows += f"""
                        <tr>
                            <td class="text-left">{vintage}</td>
                            {period_cells}
                        </tr>
                    """
                elif breakout_type == 'product_and_vintage':
                    rows += f"""
                        <tr>
                            <td class="text-left">{product}</td>
                            <td class="text-left border-right-strong">{vintage}</td>
                            {period_cells}
                        </tr>
                    """
                elif breakout_type in ('portfolio', 'strategy'):
                    rows += f"""
                        <tr>
                            <td class="text-left">{col1}</td>
                            {period_cells}
                        </tr>
                    """
                else:  # portfolio_and_strategy
                    rows += f"""
                        <tr>
                            <td class="text-left">{col1}</td>
                            <td class="text-left border-right-strong">{col2}</td>
                            {period_cells}
                        </tr>
                    """
                
                first_row = False
            
            # Build totals row
            total_cells = ""
            for period in enabled_periods:
                total_value = totals[period]
                total_class = "numeric-positive" if total_value >= 0 else "numeric-negative"
                bold_class = "bold " if period == 'ytd' else ""
                total_cells += f'<td class="text-right {bold_class}{total_class}">{self._format_notional_with_dollar(total_value)}</td>'
            
            if breakout_type in _two_col_breakouts:
                totals_row = f"""
                    <tfoot>
                        <tr>
                            <td colspan="2" class="text-left bold">TOTAL</td>
                            {total_cells}
                        </tr>
                    </tfoot>
                """
            else:
                totals_row = f"""
                    <tfoot>
                        <tr>
                            <td class="text-left bold">TOTAL</td>
                            {total_cells}
                        </tr>
                    </tfoot>
                """
        
        # Build table header and column widths based on breakout type and enabled periods
        num_grouping_cols = 2 if breakout_type in _two_col_breakouts else 1
        num_pnl_cols = len(enabled_periods)
        total_cols = num_grouping_cols + num_pnl_cols
        col_width = f"{100.0 / total_cols:.2f}%"

        # Build header columns
        if breakout_type == 'product':
            header_cols = '<th class="text-left">Product</th>'
        elif breakout_type == 'vintage':
            header_cols = '<th class="text-left">Vintage</th>'
        elif breakout_type == 'product_and_vintage':
            header_cols = '<th class="text-left">Product</th><th class="text-left border-right-strong">Vintage</th>'
        elif breakout_type == 'portfolio':
            header_cols = '<th class="text-left">Portfolio</th>'
        elif breakout_type == 'strategy':
            header_cols = '<th class="text-left">Strategy</th>'
        else:  # portfolio_and_strategy
            header_cols = '<th class="text-left">Portfolio</th><th class="text-left border-right-strong">Strategy</th>'
        
        for period in enabled_periods:
            header_cols += f'<th class="text-right">{period_labels[period]}</th>'
        
        # Build colgroup
        colgroup_cols = ""
        for _ in range(total_cols):
            colgroup_cols += f'<col style="width: {col_width};">'
        colgroup = f'<colgroup>{colgroup_cols}</colgroup>'
        
        table_html = f"""
            <table class="report-table" style="table-layout: fixed; width: 100%;">
                {colgroup}
                <thead>
                    <tr>
                        {header_cols}
                    </tr>
                </thead>
                <tbody>
                    {rows}
                </tbody>
                {totals_row}
            </table>
        """
        
        return table_html

    def _prepare_daily_pnl_detail(self, most_recent_pnl: pd.DataFrame) -> pd.DataFrame:
        """
        Prepare daily PnL detail data grouped by product and vintage.
        
        Includes daily PnL, prior day mark, and current day mark prices.
        Handles fees by collapsing vintage to year (similar to breakout logic).
        
        Args:
            most_recent_pnl: Most recent day's PnL data.
            
        Returns:
            DataFrame with columns: product, vintage, daily_pnl, position, px_mark_pd, px_mark_cd
        """
        if most_recent_pnl.empty:
            return pd.DataFrame(columns=['desk', 'product', 'vintage', 'daily_pnl', 'position', 'px_mark_pd', 'px_mark_cd'])
        
        pnl_df_for_grouping = most_recent_pnl.copy()
        
        fee_mask = self._detect_fees(pnl_df_for_grouping)
        if fee_mask.any():
            self._apply_fee_vintage_collapse(pnl_df_for_grouping, fee_mask)
        
        # Ensure grouping columns have valid values
        pnl_df_for_grouping['product'] = pnl_df_for_grouping['product'].fillna('Unknown')
        pnl_df_for_grouping['vintage'] = pnl_df_for_grouping['vintage'].fillna('')
        
        # Convert date to datetime if needed to find most recent date
        if 'date' in pnl_df_for_grouping.columns:
            pnl_df_for_grouping['_date_dt'] = pd.to_datetime(pnl_df_for_grouping['date'])
            most_recent_date = pnl_df_for_grouping['_date_dt'].max()
            # Filter to only most recent date for position and mark prices
            current_date_data = pnl_df_for_grouping[pnl_df_for_grouping['_date_dt'] == most_recent_date].copy()
        else:
            current_date_data = pnl_df_for_grouping.copy()
        
        # Calculate daily PnL from all dates (sum across the date range)
        daily_pnl_grouped = pnl_df_for_grouping.groupby(
            ['desk', 'product', 'vintage'],
            dropna=False,
            as_index=False
        ).agg({
            'usd_pnl_trading': 'sum'
        })
        
        # Get position and mark prices from current date only, excluding fee rows
        # (fee rows have px_mark_pd/cd = 0.0 and no real position, so including them
        # would dilute the averaged mark prices and inflate qty_end)
        position_grouped = current_date_data[~fee_mask].groupby(
            ['desk', 'product', 'vintage'],
            dropna=False,
            as_index=False
        ).agg({
            'qty_end': 'sum',  # Sum end-of-day positions (from current date only)
            'px_mark_pd': 'mean',  # Average prior day mark (from current date only)
            'px_mark_cd': 'mean'   # Average current day mark (from current date only)
        })
        
        # Merge daily PnL (from all dates) with position/marks (from current date only)
        grouped = daily_pnl_grouped.merge(
            position_grouped,
            on=['desk', 'product', 'vintage'],
            how='outer'
        )
        
        # Fill NaN values that might occur from the merge
        grouped['usd_pnl_trading'] = grouped['usd_pnl_trading'].fillna(0.0)
        grouped['qty_end'] = grouped['qty_end'].fillna(0.0)
        grouped['px_mark_pd'] = grouped['px_mark_pd'].fillna(0.0)
        grouped['px_mark_cd'] = grouped['px_mark_cd'].fillna(0.0)
        
        grouped.columns = ['desk', 'product', 'vintage', 'daily_pnl', 'position', 'px_mark_pd', 'px_mark_cd']
        
        # Clean up temporary column if it was added
        if '_date_dt' in pnl_df_for_grouping.columns:
            pnl_df_for_grouping.drop(columns=['_date_dt'], inplace=True)
        
        # Sort by desk first (using display name), then by product and vintage
        grouped['desk_display'] = grouped['desk'].apply(self._format_desk_name)
        grouped['_vintage_sort'] = grouped['vintage'].apply(parse_vintage_for_ordering)
        grouped = grouped.sort_values(['desk_display', 'product', '_vintage_sort', 'vintage'], na_position='last')
        grouped = grouped.drop(columns=['_vintage_sort', 'desk_display'])
        
        return grouped

    def _build_daily_pnl_detail_table_html(
        self,
        detail_df: pd.DataFrame,
        most_recent_date: str
    ) -> str:
        """
        Build HTML table for Daily PnL Detail.
        
        Shows: Product, Vintage, Position, Daily PnL, Prior Day Mark, Current Day Mark.
        
        Args:
            detail_df: DataFrame from _prepare_daily_pnl_detail.
            most_recent_date: Date string for the most recent day.
            
        Returns:
            HTML string for the detail table.
        """
        if detail_df.empty:
            rows = f"""
            <tr>
                <td colspan="6" class="text-center text-muted">
                    No daily PnL detail data available for {most_recent_date}.
                </td>
            </tr>
            """
            totals_row = ""
        else:
            # Calculate totals
            total_daily = detail_df['daily_pnl'].sum()
            
            rows = ""
            first_row = True
            current_desk = None
            for _, row in detail_df.iterrows():
                desk = row['desk']
                desk_name = self._format_desk_name(desk)
                
                # Add desk subheader row if new desk
                if current_desk != desk:
                    rows += f"""
                        <tr class="subheader-row">
                            <td colspan="6" class="text-left">
                                {desk_name}
                            </td>
                        </tr>
                    """
                    current_desk = desk
                    first_row = True  # Reset first_row for new desk section
                
                product = self._format_product_name(row['product'])
                vintage_val = row['vintage']
                vintage = '' if (not vintage_val or pd.isna(vintage_val) or str(vintage_val).strip() == '') else str(vintage_val)
                
                position = row.get('position', 0.0)
                daily_pnl = row.get('daily_pnl', 0.0)
                px_mark_pd = row.get('px_mark_pd', 0.0)
                px_mark_cd = row.get('px_mark_cd', 0.0)
                
                # Format values (dollar sign for first row of each desk section)
                fmt = self._format_notional_with_dollar if first_row else self._format_notional
                fmt_price = self._format_price_with_dollar if first_row else self._format_price
                fmt_volume = self._format_volume
                daily_class = "numeric-positive" if daily_pnl >= 0 else "numeric-negative"
                position_class = "numeric-positive" if position >= 0 else "numeric-negative"
                
                # Format mark prices (show 4 decimal places, using same dollar formatting style)
                mark_pd_str = fmt_price(px_mark_pd)
                mark_cd_str = fmt_price(px_mark_cd)
                
                rows += f"""
                    <tr>
                        <td class="text-left">{product}</td>
                        <td class="text-left border-right-strong">{vintage}</td>
                        <td class="text-right {position_class}">{fmt_volume(position)}</td>
                        <td class="text-right {daily_class}">{fmt(daily_pnl)}</td>
                        <td class="text-right">{mark_cd_str}</td>
                        <td class="text-right">{mark_pd_str}</td>
                    </tr>
                """
                first_row = False
            
            # Build totals row
            total_daily_class = "numeric-positive" if total_daily >= 0 else "numeric-negative"
            totals_row = f"""
                <tfoot>
                    <tr>
                        <td colspan="3" class="text-left bold">TOTAL</td>
                        <td class="text-right bold {total_daily_class}">
                            {self._format_notional_with_dollar(total_daily)}</td>
                        <td colspan="2"></td>
                    </tr>
                </tfoot>
            """
        
        # Column widths: Product and Vintage maintain 20% each, remaining 4 columns share 60% (15% each)
        product_vintage_width = "20%"
        remaining_col_width = "15%"  # 60% / 4 columns
        
        table_html = f"""
            <table class="report-table" style="table-layout: fixed; width: 100%;">
                <colgroup>
                    <col style="width: {product_vintage_width};">
                    <col style="width: {product_vintage_width};">
                    <col style="width: {remaining_col_width};">
                    <col style="width: {remaining_col_width};">
                    <col style="width: {remaining_col_width};">
                    <col style="width: {remaining_col_width};">
                </colgroup>
                <thead>
                    <tr>
                        <th class="text-left">Product</th>
                        <th class="text-left border-right-strong">Vintage</th>
                        <th class="text-right">Position</th>
                        <th class="text-right">Daily PnL</th>
                        <th class="text-right">Current Day Mark</th>
                        <th class="text-right">Prior Day Mark</th>
                    </tr>
                </thead>
                <tbody>
                    {rows}
                </tbody>
                {totals_row}
            </table>
        """
        
        return table_html

    def _build_daily_pnl_detail_section(
        self,
        most_recent_pnl: pd.DataFrame,
        most_recent_date: str
    ) -> str:
        """
        Build the Daily PnL Detail section for the HTML report.
        
        Args:
            most_recent_pnl: Most recent day's PnL data.
            most_recent_date: Date string for the most recent day.
            
        Returns:
            HTML string for the Daily PnL Detail section.
        """
        if most_recent_pnl.empty:
            return ""
        
        detail_df = self._prepare_daily_pnl_detail(most_recent_pnl)
        detail_table_html = self._build_daily_pnl_detail_table_html(detail_df, most_recent_date)
        
        return f"""
                <h2 style="margin-top: 30px;">Daily PnL Detail</h2>
                <div class="table-wrapper">
                {detail_table_html}
                </div>
        """

    def _build_html_content(
        self,
        summary_table: pd.DataFrame,
        most_recent_pnl: pd.DataFrame,
        breakout_type: Optional[str] = None,
        report_title: Optional[str] = None,
        tables: Optional[List[str]] = None
    ) -> str:
        """
        Build the HTML content for the report.

        Args:
            summary_table: DataFrame with desk, daily_pnl, wtd_pnl, mtd_pnl, qtd_pnl, ytd_pnl.
            most_recent_pnl: DataFrame with the most recent day's PnL data.
            breakout_type: Optional breakout type ('product', 'vintage', 'product_and_vintage',
                'portfolio', 'strategy', or 'portfolio_and_strategy').
            report_title: Optional custom title for the report. Defaults to "Karbone Daily PnL Summary Report".
            tables: Optional list of sections to include. Defaults to all structural sections when None.
                Structural keys: 'desk_summary', 'daily_detail'. Breakout type (if any) is
                identified by the presence of a breakout keyword in this list.

        Returns:
            HTML string for the complete report.
        """
        _all_tables = {'desk_summary', 'daily_detail'}
        tables_set = _all_tables if tables is None else {t.lower() for t in tables}

        report_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Calculate most recent trade date per desk, but only for desks in this report
        all_desk_dates = self._get_most_recent_trade_date_by_desk(self.pnl_df)
        
        # Filter to only desks that are in the summary_table (i.e., desks in this report)
        desks_in_report = set(summary_table['desk'].unique()) if not summary_table.empty else set()
        desk_dates = {desk: date_str for desk, date_str in all_desk_dates.items() if desk in desks_in_report}
        
        # Build desk-specific date display
        if desk_dates:
            # Sort desks by display name for consistent ordering
            desk_date_items = []
            for desk in sorted(desk_dates.keys(), key=lambda d: self._format_desk_name(d)):
                desk_display = self._format_desk_name(desk)
                date_str = desk_dates[desk]
                desk_date_items.append(f"{desk_display}: {date_str}")
            most_recent_date_display = " | ".join(desk_date_items)
        else:
            most_recent_date_display = 'N/A'
        
        if not most_recent_pnl.empty:
            # Convert to datetime and filter out future dates
            most_recent_pnl_copy = most_recent_pnl.copy()
            most_recent_pnl_copy['date'] = pd.to_datetime(most_recent_pnl_copy['date'])
            effective_date = get_effective_reporting_date()
            most_recent_pnl_copy = most_recent_pnl_copy[
                most_recent_pnl_copy['date'].dt.date <= effective_date
            ]
            if not most_recent_pnl_copy.empty:
                most_recent_date = most_recent_pnl_copy['date'].max()
                # Convert to string if it's a datetime object
                if isinstance(most_recent_date, pd.Timestamp):
                    most_recent_date = most_recent_date.strftime('%Y-%m-%d')
                elif hasattr(most_recent_date, 'strftime'):
                    most_recent_date = most_recent_date.strftime('%Y-%m-%d')
            else:
                most_recent_date = 'N/A'
        else:
            most_recent_date = 'N/A'
        Colors = constants.ReportColors
        
        # Prepare breakout table if a breakout type was specified
        breakout_table_html = ""
        breakout_heading = ""
        if breakout_type and breakout_type != 'none':
            breakout_df = self._prepare_pnl_breakout(self.pnl_df, breakout_type)
            breakout_table_html = self._build_pnl_breakout_table_html(
                breakout_df, breakout_type, most_recent_date
            )
            
            # Determine heading text
            if breakout_type == 'product':
                breakout_heading = "PnL by Product"
            elif breakout_type == 'vintage':
                breakout_heading = "PnL by Vintage"
            elif breakout_type == 'product_and_vintage':
                breakout_heading = "PnL by Product and Vintage"
            elif breakout_type == 'portfolio':
                breakout_heading = "PnL by Portfolio"
            elif breakout_type == 'strategy':
                breakout_heading = "PnL by Strategy"
            else:  # portfolio_and_strategy
                breakout_heading = "PnL by Portfolio and Strategy"

        # Ensure desks are displayed alphabetically by display name
        summary_table = summary_table.copy()
        summary_table['desk_display'] = summary_table['desk'].apply(self._format_desk_name)
        summary_table = summary_table.sort_values('desk_display')

        # Get enabled periods
        enabled_periods = self._get_enabled_periods()
        period_labels = self._PERIOD_LABELS
        
        # Calculate totals for enabled periods only
        totals = {}
        for period in enabled_periods:
            col_name = self._get_period_column_name(period)
            totals[period] = summary_table[col_name].sum() if col_name in summary_table.columns else 0.0

        # Check if there's only one desk (hide footer in that case)
        show_footer = len(summary_table) > 1

        # Build table rows (first row uses dollar-aligned formatting to mirror position report)
        table_rows = ""
        first_row = True
        for _, row in summary_table.iterrows():
            desk_name = row['desk_display']
            fmt = self._format_notional_with_dollar if first_row else self._format_notional
            
            # Build period cells for enabled periods
            period_cells = ""
            for period in enabled_periods:
                col_name = self._get_period_column_name(period)
                pnl_value = row.get(col_name, 0.0)
                pnl_class = "numeric-positive" if pnl_value >= 0 else "numeric-negative"
                # YTD gets bold styling
                bold_class = "bold " if period == 'ytd' else ""
                period_cells += f'<td class="text-right {bold_class}{pnl_class}">{fmt(pnl_value)}</td>'
            
            table_rows += f"""
                <tr>
                    <td class="text-left">{desk_name}</td>
                    {period_cells}
                </tr>
            """
            first_row = False

        styles = self._build_base_styles()
        
        # Build total classes and cells
        total_cells = ""
        for period in enabled_periods:
            total_value = totals[period]
            total_class = "numeric-positive" if total_value >= 0 else "numeric-negative"
            bold_class = "bold " if period == 'ytd' else ""
            total_cells += f'<td class="text-right {bold_class}{total_class}">{self._format_notional_with_dollar(total_value)}</td>'
        
        # Adjust desk column width based on number of enabled periods and breakout type
        num_pnl_cols = len(enabled_periods)
        if breakout_type == 'product_and_vintage':
            # Desk = 2 units, N PnL columns = N units, total = N+2 units
            desk_col_width = f"{200.0 / (num_pnl_cols + 2):.3f}%"
            pnl_col_width = f"{100.0 / (num_pnl_cols + 2):.3f}%"
        else:
            # Desk = 1 unit, N PnL columns = N units, total = N+1 units
            desk_col_width = f"{100.0 / (num_pnl_cols + 1):.3f}%"
            pnl_col_width = f"{100.0 / (num_pnl_cols + 1):.3f}%"

        # Use provided title or default
        title = report_title or "Karbone Daily PnL Summary Report"
        # For HTML title tag, use a shorter version without brackets if present
        html_title = title.replace("[", "").replace("]", "").strip()

        # Determine the Daily PnL date range used for this report
        daily_range_subtitle = ""
        effective_date = get_effective_reporting_date()
        calendar = TradingCalendar()
        daily_dates = get_daily_pnl_date_range(effective_date, calendar)
        if daily_dates:
            start_date = min(daily_dates)
            end_date = max(daily_dates)

            _fmt_long = self._format_long_date

            if start_date == end_date:
                daily_range_subtitle = f"Includes PnL for {_fmt_long(start_date)}"
            else:
                daily_range_subtitle = (
                    f"Includes PnL from {_fmt_long(start_date)} to {_fmt_long(end_date)}"
                )
        
        # Pre-build optional table sections so the f-string stays readable
        if 'desk_summary' in tables_set:
            _footer_html = f'<tfoot><tr><td class="text-left">TOTAL</td>{total_cells}</tr></tfoot>' if show_footer else ''
            _colgroup = f'<col style="width: {desk_col_width};">' + ''.join(
                f'<col style="width: {pnl_col_width};">' for _ in enabled_periods
            )
            _header_cells = '<th class="text-left">Desk</th>' + ''.join(
                f'<th class="text-right">{period_labels[p]}</th>' for p in enabled_periods
            )
            desk_summary_html = (
                f'<h2 style="margin-top: 30px;">PnL by Desk</h2>'
                f'<div class="table-wrapper">'
                f'<table class="report-table" style="table-layout: fixed; width: 100%;">'
                f'<colgroup>{_colgroup}</colgroup>'
                f'<thead><tr>{_header_cells}</tr></thead>'
                f'<tbody>{table_rows}</tbody>'
                f'{_footer_html}'
                f'</table></div>'
            )
        else:
            desk_summary_html = ''

        if 'daily_detail' in tables_set and self._is_period_enabled('daily'):
            daily_detail_html = self._build_daily_pnl_detail_section(most_recent_pnl, most_recent_date)
        else:
            daily_detail_html = ''

        # Build HTML
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>{html_title}</title>
            <style>
                {styles}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>{title}</h1>
                    <div class="subtitle">Generated: {report_date}</div>
                    {f'<div class="subtitle">{daily_range_subtitle}</div>' if daily_range_subtitle else ''}
                </div>

                {desk_summary_html}
                {f'<h2 style="margin-top: 30px;">{breakout_heading}</h2><div class="table-wrapper">{breakout_table_html}</div>' if breakout_table_html else ''}

                {daily_detail_html}

                <div class="footnote">
                    <p style="margin: 0;">
                        1. All figures are in USD. Foreign currencies are converted to USD.
                    </p>
                </div>

                <div class="footnote" style="margin-top: 15px; border-top: 1px solid {constants.ReportColors.BORDER}; padding-top: 15px;">
                    <p style="margin: 0; font-weight: bold;">Confidentiality Notice:</p>
                    <p style="margin: 5px 0 0 0;">
                        The information contained in this e-mail transmittal is privileged and confidential and intended for the addressee only. If you are neither the intended recipient nor the employee or agent responsible for delivering this e-mail to the intended recipient, any disclosure of this information in any way or taking of any action in reliance on this information is strictly prohibited. If you have received this e-mail in error, please notify the person transmitting the information immediately.
                    </p>
                </div>

                <div class="footer text-muted">
                    <p>This is an automated report generated by the Karbone Risk team.</p>
                    <p>For questions or issues, please
                        <a href="mailto:riskteam@karbone.com">contact us</a>
                    </p>
                </div>
            </div>
        </body>
        </html>
        """

        return html

    def generate_volume_report_html(self, intro_text: Optional[str] = None, report_title: Optional[str] = None) -> str:
        """
        Generate a standalone HTML page containing the volumetric exposure table.

        Args:
            intro_text: Optional introductory copy to appear above the table.
            report_title: Optional custom title for the report. Defaults to "Karbone Daily Position Report".

        Returns:
            HTML string for the volume exposure report.
        """
        most_recent_pnl = self.get_most_recent_day_pnl(self.pnl_df)
        
        # Calculate most recent trade date per desk, but only for desks in this report
        all_desk_dates = self._get_most_recent_trade_date_by_desk(self.pnl_df)
        
        # Filter to only desks that are in most_recent_pnl (i.e., desks in this report)
        desks_in_report = set(most_recent_pnl['desk'].unique()) if not most_recent_pnl.empty else set()
        desk_dates = {desk: date_str for desk, date_str in all_desk_dates.items() if desk in desks_in_report}
        
        # Build desk-specific date display
        if desk_dates:
            # Sort desks by display name for consistent ordering
            desk_date_items = []
            for desk in sorted(desk_dates.keys(), key=lambda d: self._format_desk_name(d)):
                desk_display = self._format_desk_name(desk)
                date_str = desk_dates[desk]
                desk_date_items.append(f"{desk_display}: {date_str}")
            most_recent_date_display = " | ".join(desk_date_items)
        else:
            most_recent_date_display = 'N/A'
        
        if not most_recent_pnl.empty:
            # Convert to datetime and filter out future dates
            most_recent_pnl_copy = most_recent_pnl.copy()
            most_recent_pnl_copy['date'] = pd.to_datetime(most_recent_pnl_copy['date'])
            effective_date = get_effective_reporting_date()
            most_recent_pnl_copy = most_recent_pnl_copy[
                most_recent_pnl_copy['date'].dt.date <= effective_date
            ]
            if not most_recent_pnl_copy.empty:
                most_recent_date = most_recent_pnl_copy['date'].max()
                # Convert to string if it's a datetime object
                if isinstance(most_recent_date, pd.Timestamp):
                    most_recent_date = most_recent_date.strftime('%Y-%m-%d')
                elif hasattr(most_recent_date, 'strftime'):
                    most_recent_date = most_recent_date.strftime('%Y-%m-%d')
            else:
                most_recent_date = 'N/A'
        else:
            most_recent_date = 'N/A'
        report_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        styles = self._build_base_styles()
        Colors = constants.ReportColors

        volume_table_html, exposures = self._build_volume_exposure_table_html(
            most_recent_pnl,
            most_recent_date
        )
        nonzero_change_table_html = self._build_nonzero_change_table_html(
            exposures,
            most_recent_date
        )
        intro_html = f"""
            <div class="text-muted" style="margin-bottom: 10px;">
                {intro_text}
            </div>
        """ if intro_text else ""

        # Use provided title or default
        title = report_title or "Karbone Daily Position Report"
        # For HTML title tag, use a shorter version without brackets if present
        html_title = title.replace("[", "").replace("]", "").strip()

        # Determine the Daily PnL date range used for this volume report
        daily_range_subtitle = ""
        effective_date = get_effective_reporting_date()
        calendar = TradingCalendar()
        daily_dates = get_daily_pnl_date_range(effective_date, calendar)
        if daily_dates:
            start_date = min(daily_dates)
            end_date = max(daily_dates)

            _fmt_long = self._format_long_date

            if start_date == end_date:
                daily_range_subtitle = f"Includes PnL for {_fmt_long(start_date)}"
            else:
                daily_range_subtitle = (
                    f"Includes PnL from {_fmt_long(start_date)} to {_fmt_long(end_date)}"
                )

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>{html_title}</title>
            <style>
                {styles}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>{title}</h1>
                    <div class="subtitle">Generated: {report_date}</div>
                    {f'<div class="subtitle">{daily_range_subtitle}</div>' if daily_range_subtitle else ''}
                </div>
                {intro_html}
                
                <h2 style="margin-top: 10px;">
                    Changes in Volumetric Exposure
                </h2>
                <div class="table-wrapper">
                {nonzero_change_table_html}
                </div>
                
                <h2 style="margin-top: 30px;">
                    Current Position Detail
                </h2>
                <div class="table-wrapper">
                {volume_table_html}
                </div>
                <div class="footnote" style="margin-top: 10px;">
                    <p style="margin: 0;">
                        1. All notional value figures are in USD.
                        Foreign currencies are converted to USD.
                    </p>
                    <p style="margin: 0;">
                        2. All volume figures are rounded to the nearest integer.
                    </p>
                </div>
                <div class="footnote" style="margin-top: 15px; border-top: 1px solid {Colors.BORDER}; padding-top: 15px;">
                    <p style="margin: 0; font-weight: bold;">Confidentiality Notice:</p>
                    <p style="margin: 5px 0 0 0;">
                        The information contained in this e-mail transmittal is privileged and confidential and intended for the addressee only. If you are neither the intended recipient nor the employee or agent responsible for delivering this e-mail to the intended recipient, any disclosure of this information in any way or taking of any action in reliance on this information is strictly prohibited. If you have received this e-mail in error, please notify the person transmitting the information immediately.
                    </p>
                </div>
                <div class="footer text-muted">
                    <p>This is an automated report generated by the Karbone Risk team.</p>
                    <p>For questions or issues, please
                        <a href="mailto:riskteam@karbone.com">contact us</a>.
                    </p>
                </div>
            </div>
        </body>
        </html>
        """

        return html

    def generate_position_html_report(self, intro_text: Optional[str] = None, report_title: Optional[str] = None) -> bool:
        """
        Write the position report to the configured file.

        Args:
            intro_text: Optional introductory text for the report.
            report_title: Optional custom title for the report. Defaults to "Karbone Daily Position Report".

        Returns:
            True if report generated successfully, False otherwise.
        """
        logger.info("Generating volumetric exposure HTML report...")

        if not self.position_report_path:
            logger.error("Position report path is not configured")
            return False

        try:
            html_content = self.generate_volume_report_html(intro_text=intro_text, report_title=report_title)
            with open(self.position_report_path, 'w', encoding='utf-8') as f:
                f.write(html_content)
            logger.info(f"Volumetric exposure report saved to {self.position_report_path}")
            return True
        except OSError:
            logger.exception("Error generating position report")
            return False

