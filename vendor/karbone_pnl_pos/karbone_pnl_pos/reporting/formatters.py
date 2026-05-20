"""
Number and date formatting utilities for the v4-hybrid PnL report.
Port of design/utils.jsx formatter functions.
"""
from datetime import date as _date
from typing import Optional


def fmt_usd(v: Optional[float], dollar: bool = False, dash: bool = True) -> str:
    """Format a dollar value. Negatives in parens. Zero returns '–' when dash=True."""
    if v is None:
        return '–'
    rounded = round(v)
    if dash and rounded == 0:
        return '–'
    abv = abs(rounded)
    s = f'{abv:,}'
    wrapped = f'({s})' if v < 0 else s
    return f'${wrapped}' if dollar else wrapped


def fmt_compact(v: Optional[float]) -> str:
    """Compact K/M abbreviation: 1,234,567 → '1.2M', 12345 → '12.3K', 999 → '999'."""
    if v is None:
        return '–'
    abv = abs(v)
    if abv >= 1_000_000:
        suffix = f'{v / 1_000_000:.1f}' if abv < 10_000_000 else f'{v / 1_000_000:.1f}'
        return f'{suffix}M'
    if abv >= 1_000:
        suffix = f'{v / 1_000:.0f}' if abv >= 100_000 else f'{v / 1_000:.1f}'
        return f'{suffix}K'
    return str(round(v))


def fmt_compact_signed(v: Optional[float]) -> str:
    """Compact with explicit +/− sign prefix."""
    if v is None:
        return '–'
    sign = '−' if v < 0 else ('+' if v > 0 else '')
    return sign + fmt_compact(abs(v))


def fmt_price(v: Optional[float], decimals: int = 4) -> str:
    """Price with fixed decimal places; NaN/None returns '–'."""
    if v is None:
        return '–'
    try:
        return f'{v:,.{decimals}f}'
    except (TypeError, ValueError):
        return '–'


def fmt_vol(v: Optional[float]) -> str:
    """Volume: integer with commas, negatives in parens, zero '–'.
    Positive values get a trailing non-breaking space so digits align with closing paren."""
    if v is None or round(v) == 0:
        return '–'
    abv = abs(round(v))
    s = f'{abv:,}'
    return f'({s})' if v < 0 else f'{s} '


def fmt_long_date(s: str) -> str:
    """'2026-05-13' → 'Wednesday, May 13, 2026'. Windows-safe (no %-d)."""
    try:
        d = _date.fromisoformat(s)
        return f"{d.strftime('%A, %B')} {d.day}, {d.year}"
    except (ValueError, AttributeError):
        return s


def fmt_signed_dollar(v: Optional[float]) -> str:
    """'+$1,234' / '−$1,234' / '–'. Uses Unicode minus (U+2212) for negatives."""
    if v is None or round(v) == 0:
        return '–'
    sign = '−' if v < 0 else '+'
    return f'{sign}${fmt_compact(abs(v))}'
