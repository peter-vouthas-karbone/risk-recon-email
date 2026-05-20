#!/usr/bin/env python3
"""
Day-aware runner for the PnL pipeline.

Reporting schedule (all times New York):
  Sunday   â†’ no report (skip)
  Monday   â†’ no report (skip)
  Tuesday  â†’ run with email; covers Saturday + Sunday + Monday
  Wednesdayâ†’ run with email; covers Tuesday
  Thursday â†’ run with email; covers Wednesday
  Friday   â†’ run with email; covers Thursday
  Saturday â†’ run with email; covers Friday
"""

from __future__ import annotations

import logging
import subprocess
import sys
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore

from karbone_pnl_pos.utils.logging_utils import init_logging

# Module logger - will be configured by init_logging() in entry point
logger = logging.getLogger('pnl.' + __name__)

# Days on which no report is sent (0=Monday, 6=Sunday)
_NO_REPORT_DAYS = {6, 0}  # Sunday, Monday


def main() -> int:
    # Initialize logging first
    init_logging(log_prefix='scheduler')

    try:
        ny_tz = ZoneInfo('America/New_York')
        now_ny = datetime.now(ny_tz)
        weekday = now_ny.weekday()  # 0=Monday â€¦ 6=Sunday

        if weekday in _NO_REPORT_DAYS:
            logger.info(
                f"No report scheduled for {now_ny:%A} ({now_ny:%Y-%m-%d} NY). Skipping."
            )
            return 0

        logger.info(f"Running pipeline with email ({now_ny:%A} {now_ny:%Y-%m-%d} NY).")
        cmd = [sys.executable, "-m", "src.runners.run_pnl_pipeline", "--send-email"]
        return subprocess.run(cmd).returncode

    except KeyboardInterrupt:
        logger.warning("Scheduler interrupted by user.")
        return 1
    except Exception:
        logger.exception("Unexpected error in scheduler")
        return 1


if __name__ == "__main__":
    sys.exit(main())


