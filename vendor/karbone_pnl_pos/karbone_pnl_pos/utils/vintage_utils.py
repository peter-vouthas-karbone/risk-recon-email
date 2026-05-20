#!/usr/bin/env python3
"""
Vintage utilities for chronological sorting across the PnL workflow.
"""

import re
from typing import Tuple

import pandas as pd

from karbone_pnl_pos.utils import constants

_APOSTROPHE_RE = re.compile(r"['â€™Ê¹`â€²]")


def parse_vintage_for_ordering(vintage: str) -> Tuple[int, int]:
    """
    Parse a vintage string into a sortable tuple (year, quarter/month).

    Supports formats such as:
    - "Q1'25"
    - "Q1 2025"
    - "January 2025"
    - "BH25" or "CY25"
    - "2025"

    Returns:
        Tuple of (year, period) for sorting, or (0, 0) if parsing fails.
    """
    try:
        if vintage is None or (isinstance(vintage, float) and pd.isna(vintage)):
            return (0, 0)

        vintage_str = str(vintage).strip()
        # Normalize common apostrophe variants to improve matching (e.g., â€™ -> ')
        vintage_str = _APOSTROPHE_RE.sub("'", vintage_str)

        if vintage_str.isdigit() and len(vintage_str) == 4:
            return (int(vintage_str), 0)

        q_match = constants.VINTAGE_REGEX_Q_APOSTROPHE.match(vintage_str.upper())
        if q_match:
            quarter = int(q_match.group(1))
            year = 2000 + int(q_match.group(2))
            return (year, quarter)

        month_match = constants.VINTAGE_REGEX_MONTH.match(vintage_str)
        if month_match:
            month_name = month_match.group(1).title()
            year = int(month_match.group(2))
            month = constants.MONTH_NAME_TO_INT.get(month_name, 0)
            return (year, month)

        bh_match = constants.VINTAGE_REGEX_BH.match(vintage_str.upper())
        if bh_match:
            year = 2000 + int(bh_match.group(1))
            return (year, 0)

        cy_match = constants.VINTAGE_REGEX_CY.match(vintage_str.upper())
        if cy_match:
            year = 2000 + int(cy_match.group(1))
            return (year, 0)

        year_match = constants.VINTAGE_REGEX_YEAR.search(vintage_str)
        if year_match:
            return (int(year_match.group(1)), 0)

        return (0, 0)

    except (ValueError, AttributeError, TypeError):
        return (0, 0)


