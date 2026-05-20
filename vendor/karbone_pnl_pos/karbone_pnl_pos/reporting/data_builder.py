"""
Builds the nested report-data dict required by html_builder.render().
Bridges PnL DataFrames (from PnLReporter) to the schema defined in design/data.js.
"""
import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING, Dict, List, Optional

import pandas as pd

from karbone_pnl_pos.utils import constants
from karbone_pnl_pos.utils.trading_calendar import TradingCalendar

if TYPE_CHECKING:
    from src.core.reporting import PnLReporter

logger = logging.getLogger('pnl.' + __name__)

_DESK_ORDER = ['power_forward', 'power_ancillary', 'fuels', 'mgmt', 'power_short_term', 'europe']


def _get_last_n_trading_days(calendar: TradingCalendar, as_of: date, n: int) -> List[date]:
    """Return the last n trading days ending on (and including) as_of, in chronological order."""
    days: List[date] = []
    d = as_of
    # Walk back at most n * 3 calendar days to collect n trading days
    for _ in range(n * 3):
        if len(days) >= n:
            break
        if calendar.is_trading_day(d):
            days.append(d)
        d -= timedelta(days=1)
    return sorted(days)


def _build_30d_sparklines(
    pnl_df: pd.DataFrame,
    as_of: date,
    calendar: TradingCalendar,
    desk_keys: List[str],
) -> tuple:
    """Return (sparkData dict, sparkDates list) for the 30-day trailing window."""
    dates = _get_last_n_trading_days(calendar, as_of, 30)
    if not dates:
        empty = {k: [] for k in desk_keys}
        return empty, []

    df = pnl_df.copy()
    df['_date'] = pd.to_datetime(df['date']).dt.date
    df_window = df[df['_date'].isin(set(dates))]

    if df_window.empty:
        empty = {k: [0.0] * len(dates) for k in desk_keys}
        return empty, [d.isoformat() for d in dates]

    pivot = (
        df_window
        .groupby(['_date', 'desk'])['usd_pnl_trading']
        .sum()
        .unstack(fill_value=0.0)
    )
    pivot = pivot.reindex(dates, fill_value=0.0)

    spark_data: Dict[str, List[float]] = {}
    for key in desk_keys:
        if key in pivot.columns:
            spark_data[key] = [float(v) for v in pivot[key].tolist()]
        else:
            spark_data[key] = [0.0] * len(dates)

    spark_dates = [d.isoformat() for d in dates]
    return spark_data, spark_dates


def _rename_pnl_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Rename {period}_pnl â†’ {period} so keys match data.js schema."""
    return df.rename(columns={
        'daily_pnl': 'daily',
        'wtd_pnl': 'wtd',
        'mtd_pnl': 'mtd',
        'qtd_pnl': 'qtd',
        'ytd_pnl': 'ytd',
    })


def _build_desks(
    summary_table: pd.DataFrame,
    most_recent_pnl: pd.DataFrame,
) -> Dict[str, dict]:
    """Convert per-desk summary_table rows to the data.desks schema."""
    trade_counts: Dict[str, int] = {}
    if not most_recent_pnl.empty:
        trade_counts = most_recent_pnl.groupby('desk').size().to_dict()

    desks: Dict[str, dict] = {}
    st = _rename_pnl_cols(summary_table.copy())
    period_cols = [c for c in ['daily', 'wtd', 'mtd', 'qtd', 'ytd'] if c in st.columns]
    for _, row in st.iterrows():
        key = row['desk']
        entry: dict = {p: float(row.get(p, 0.0)) for p in period_cols}
        entry['trades'] = trade_counts.get(key, 0)
        desks[key] = entry
    return desks


def _build_product_vintage(reporter: 'PnLReporter', pnl_df: pd.DataFrame) -> List[dict]:
    """Build productVintage list from existing _prepare_pnl_breakout logic."""
    df = reporter._prepare_pnl_breakout(pnl_df, 'product_and_vintage')
    if df.empty:
        return []
    df = _rename_pnl_cols(df)
    rows = []
    for _, r in df.iterrows():
        row: dict = {'desk': r['desk'], 'product': r.get('product', ''), 'vintage': r.get('vintage', '')}
        for p in ['daily', 'wtd', 'mtd', 'qtd', 'ytd']:
            if p in r:
                row[p] = float(r[p])
        rows.append(row)
    return rows


def _build_portfolio_strategy(reporter: 'PnLReporter', pnl_df: pd.DataFrame) -> List[dict]:
    """Build deskPortfolioStrategy list from existing _prepare_pnl_breakout logic."""
    # Check if portfolio/strategy columns exist in pnl_df before calling
    if 'portfolio' not in pnl_df.columns or 'strategy' not in pnl_df.columns:
        return []
    df = reporter._prepare_pnl_breakout(pnl_df, 'portfolio_and_strategy')
    if df.empty:
        return []
    df = _rename_pnl_cols(df)
    rows = []
    for _, r in df.iterrows():
        row: dict = {
            'desk': r['desk'],
            'portfolio': r.get('portfolio', '(none)'),
            'strategy': r.get('strategy', '(none)'),
        }
        if not row['portfolio']:
            row['portfolio'] = '(none)'
        if not row['strategy']:
            row['strategy'] = '(none)'
        for p in ['daily', 'wtd', 'mtd', 'qtd', 'ytd']:
            if p in r:
                row[p] = float(r[p])
        rows.append(row)
    return rows


def _build_daily_details(reporter: 'PnLReporter', most_recent_pnl: pd.DataFrame) -> Dict[str, dict]:
    """Build dailyDetails nested dict keyed by desk then 'product|vintage'."""
    detail_df = reporter._prepare_daily_pnl_detail(most_recent_pnl)
    if detail_df.empty:
        return {}

    result: Dict[str, dict] = {}
    for _, r in detail_df.iterrows():
        desk = r['desk']
        product = r.get('product', '')
        vintage = r.get('vintage', '')
        key = f'{product}|{vintage}' if vintage else product
        if desk not in result:
            result[desk] = {}
        result[desk][key] = {
            'product': product,
            'vintage': vintage,
            'position': float(r.get('position', 0.0)),
            'daily': float(r.get('daily_pnl', 0.0)),
            'mark_cd': float(r.get('px_mark_cd', 0.0)),
            'mark_pd': float(r.get('px_mark_pd', 0.0)),
        }
    return result


def build_report_data(
    reporter: 'PnLReporter',
    pnl_df: pd.DataFrame,
    most_recent_pnl: pd.DataFrame,
    summary_table: pd.DataFrame,
    as_of_date,
    calendar: TradingCalendar,
    report_title: Optional[str] = None,
    single_desk: Optional[str] = None,
    generated_ts: Optional[str] = None,
) -> dict:
    """
    Transform PnL DataFrames into the nested dict expected by html_builder.render().
    Matches the schema in design/data.js.
    """
    from datetime import datetime as _dt
    as_of_str = as_of_date.isoformat() if hasattr(as_of_date, 'isoformat') else str(as_of_date)
    ts = generated_ts or _dt.now().strftime('%Y-%m-%d %H:%M:%S')

    # Desks
    desks = _build_desks(summary_table, most_recent_pnl)

    # Filter to single desk when requested
    if single_desk and single_desk in desks:
        desks = {single_desk: desks[single_desk]}

    desk_keys = list(desks.keys())

    # Desk display names
    desk_names = {k: constants.DESK_DISPLAY_NAMES.get(k, k.replace('_', ' ').title())
                  for k in desk_keys}

    # Sparklines
    spark_data, spark_dates = _build_30d_sparklines(pnl_df, as_of_date, calendar, desk_keys)

    # Breakout tables â€” use full pnl_df so period calcs are over the full history
    product_vintage = _build_product_vintage(reporter, pnl_df)
    portfolio_strategy = _build_portfolio_strategy(reporter, pnl_df)
    daily_details = _build_daily_details(reporter, most_recent_pnl)

    # Filter breakout data to single_desk when requested
    if single_desk:
        product_vintage = [r for r in product_vintage if r['desk'] == single_desk]
        portfolio_strategy = [r for r in portfolio_strategy if r['desk'] == single_desk]
        daily_details = {k: v for k, v in daily_details.items() if k == single_desk}

    return {
        'asOf': as_of_str,
        'generated': ts,
        'reportTitle': report_title or 'Daily PnL Report',
        'desks': desks,
        'deskNames': desk_names,
        'sparkData': spark_data,
        'sparkDates': spark_dates,
        'productVintage': product_vintage,
        'deskPortfolioStrategy': portfolio_strategy,
        'dailyDetails': daily_details,
    }

