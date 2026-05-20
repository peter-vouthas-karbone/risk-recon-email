#!/usr/bin/env python3
"""
Trading Calendar Module for NYSE Trading Days.

This module provides a centralized trading calendar that excludes weekends
and NYSE holidays. It loads holidays from data/holidays.csv and provides
utility methods for determining trading days and calculating period boundaries.

Example:
    >>> from trading_calendar import TradingCalendar
    >>> calendar = TradingCalendar()
    >>> calendar.is_trading_day(date(2026, 1, 1))  # New Year's Day
    False
    >>> calendar.is_trading_day(date(2026, 1, 2))  # Friday
    True
    >>> calendar.get_previous_trading_day(date(2026, 1, 3))  # Saturday
    datetime.date(2026, 1, 2)
"""

import csv
import logging
import os
from datetime import date, timedelta
from typing import List, Set

logger = logging.getLogger('pnl.' + __name__)

_MAX_HOLIDAY_LOOKBACK_DAYS = 10


# Determine project root to locate holidays file
_PROJECT_ROOT: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DEFAULT_HOLIDAYS_FILE: str = os.path.join(_PROJECT_ROOT, 'data', 'holidays.csv')


class TradingCalendar:
    """
    NYSE Trading Calendar that excludes weekends and holidays.
    
    This class loads holidays from a CSV file and provides methods to:
    - Check if a date is a trading day
    - Get trading days in a date range
    - Find previous/next trading days
    - Calculate week/month/quarter boundaries based on trading days
    
    Attributes:
        holidays: Set of holiday dates loaded from CSV file.
    """
    
    def __init__(self, holidays_file: str = _DEFAULT_HOLIDAYS_FILE) -> None:
        """
        Initialize trading calendar with holidays.
        
        Args:
            holidays_file: Path to CSV file containing holidays.
                          Defaults to data/holidays.csv relative to project root.
        """
        self.holidays: Set[date] = self._load_holidays(holidays_file)
    
    def _load_holidays(self, file_path: str) -> Set[date]:
        """
        Load holidays from CSV file.
        
        Handles empty date fields (e.g., holidays that fall on weekends
        and are not observed).
        
        Args:
            file_path: Path to holidays CSV file.
        
        Returns:
            Set of holiday dates.
        """
        holidays: Set[date] = set()

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    date_str = row.get('date', '').strip()
                    if not date_str:
                        continue
                    try:
                        holidays.add(date.fromisoformat(date_str))
                    except (ValueError, TypeError):
                        continue
        except FileNotFoundError:
            pass
        except (IOError, OSError) as e:
            logger.warning("Could not load holidays from %s: %s", file_path, e)

        return holidays
    
    def is_trading_day(self, dt: date) -> bool:
        """
        Check if a date is a trading day (not weekend and not holiday).
        
        Args:
            dt: Date to check.
        
        Returns:
            True if date is a trading day, False otherwise.
        """
        if dt.weekday() >= 5:
            return False
        
        # Check if holiday
        if dt in self.holidays:
            return False
        
        return True
    
    def is_holiday(self, dt: date) -> bool:
        """
        Check if a date is specifically a holiday (not just a non-trading day).
        
        This distinguishes holidays from weekends. A date can be a holiday
        even if it falls on a weekend.
        
        Args:
            dt: Date to check.
        
        Returns:
            True if date is a holiday, False otherwise.
        """
        return dt in self.holidays
    
    def get_trading_days(self, start_date: date, end_date: date) -> List[date]:
        """
        Get all trading days in a date range (inclusive).
        
        Args:
            start_date: Start date (inclusive).
            end_date: End date (inclusive).
        
        Returns:
            List of trading days in the range, sorted chronologically.
        """
        trading_days: List[date] = []
        current = start_date
        
        while current <= end_date:
            if self.is_trading_day(current):
                trading_days.append(current)
            current += timedelta(days=1)
        
        return trading_days
    
    def get_previous_trading_day(self, dt: date) -> date:
        """
        Get the most recent trading day on or before the given date.
        
        Args:
            dt: Reference date.
        
        Returns:
            Most recent trading day on or before dt.
        """
        current = dt
        # Look back at most 10 days (should cover weekends and long holiday weekends)
        for _ in range(_MAX_HOLIDAY_LOOKBACK_DAYS):
            if self.is_trading_day(current):
                return current
            current -= timedelta(days=1)
        
        # If we haven't found a trading day, return the original date
        # (shouldn't happen in practice, but handle edge case)
        return dt
    
    def get_next_trading_day(self, dt: date) -> date:
        """
        Get the next trading day on or after the given date.
        
        Args:
            dt: Reference date.
        
        Returns:
            Next trading day on or after dt.
        """
        current = dt
        # Look forward at most 10 days (should cover weekends and long holiday weekends)
        for _ in range(_MAX_HOLIDAY_LOOKBACK_DAYS):
            if self.is_trading_day(current):
                return current
            current += timedelta(days=1)
        
        # If we haven't found a trading day, return the original date
        # (shouldn't happen in practice, but handle edge case)
        return dt
    
    def get_week_start(self, dt: date) -> date:
        """
        Get the Monday of the week containing the given date.
        If Monday is not a trading day, returns the previous trading day.
        
        Args:
            dt: Reference date.
        
        Returns:
            Monday of the week, or previous trading day if Monday is a holiday.
        """
        # Get Monday of the week (Monday = 0)
        days_since_monday = dt.weekday()
        monday = dt - timedelta(days=days_since_monday)
        
        # If Monday is a trading day, return it
        if self.is_trading_day(monday):
            return monday
        
        # Otherwise, return the previous trading day
        return self.get_previous_trading_day(monday)
    
    def filter_trading_days(self, dates: List[date]) -> List[date]:
        """
        Filter a list of dates to only include trading days.
        
        Args:
            dates: List of dates to filter.
        
        Returns:
            List of dates that are trading days, maintaining original order.
        """
        return [d for d in dates if self.is_trading_day(d)]

