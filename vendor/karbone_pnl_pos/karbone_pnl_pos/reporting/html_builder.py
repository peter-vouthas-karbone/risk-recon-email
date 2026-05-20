"""
HTML builder for the v4-hybrid PnL report.

One Python function per JSX component in design/v4-hybrid.jsx.
Generates Outlook-safe HTML: nested <table> layout, inline styles only,
border-bottom on <td> elements (not <tr>), border-collapse:collapse on data
tables. Sparklines render as inline SVG by default; when a sparkline_dir is
supplied to render(), they are written as PNG files and referenced via
cid: URLs so they survive Gmail's SVG stripping when CID-attached by the
email sender.
"""
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from karbone_pnl_pos.reporting.formatters import (
    fmt_compact,
    fmt_long_date,
    fmt_price,
    fmt_signed_dollar,
    fmt_vol,
)
from karbone_pnl_pos.reporting.sparkline import render_sparkline_png, sparkline_cid, sparkline_svg
from karbone_pnl_pos.reporting.theme import NUM_FONT, SANS_FONT, THEME as T

_PERIODS_ALL = [('daily', 'Daily'), ('wtd', 'WTD'), ('mtd', 'MTD'), ('qtd', 'QTD'), ('ytd', 'YTD')]
_PERIODS_NO_QY = [('daily', 'Daily'), ('wtd', 'WTD'), ('mtd', 'MTD')]

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Low-level helpers
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _s(**kwargs) -> str:
    """Inline style string from kwargs. Underscores â†’ hyphens. None/'' filtered out."""
    return '; '.join(
        f"{k.replace('_', '-')}:{v}"
        for k, v in kwargs.items()
        if v is not None and v != ''
    )


def _td(content: str, style: str = '', **attrs) -> str:
    attr_str = ''.join(f' {k}="{v}"' for k, v in attrs.items())
    s = f' style="{style}"' if style else ''
    return f'<td{s}{attr_str}>{content}</td>'


def _tr(cells: str, style: str = '') -> str:
    s = f' style="{style}"' if style else ''
    return f'<tr{s}>{cells}</tr>'


def _table(body: str, width: str = '100%', style: str = '', collapse: bool = False) -> str:
    """Build a table. collapse=True adds border-collapse:collapse for visible cell borders."""
    parts = []
    if collapse:
        parts.append('border-collapse:collapse')
    if style:
        parts.append(style)
    full_style = '; '.join(parts)
    s = f' style="{full_style}"' if full_style else ''
    return (
        f'<table width="{width}" cellpadding="0" cellspacing="0" border="0"'
        f' role="presentation"{s}>{body}</table>'
    )


def _num_color(v: float) -> str:
    if v > 0:
        return T.pos
    if v < 0:
        return T.neg
    return T.subtle


def _sparkline_html(
    key: str,
    data: List[float],
    width: int,
    height: int,
    show_last_dot: bool,
    sparkline_dir: Optional[Path],
) -> str:
    """
    Return an HTML snippet for a sparkline.

    When `sparkline_dir` is None: inline <svg> (good for standalone HTML viewed
    in a browser). When set: writes a PNG into the directory and returns an
    <img src="cid:..."> tag matching the CID convention used by
    EmailSender._attach_inline_images (stem@karbone).
    """
    if not data:
        return ''
    if sparkline_dir is None:
        return sparkline_svg(data, width=width, height=height, show_last_dot=show_last_dot)

    png_path = sparkline_dir / f'spark_{key}.png'
    render_sparkline_png(
        data, png_path,
        width_px=width, height_px=height, show_last_dot=show_last_dot,
    )
    cid = sparkline_cid(key)
    return (
        f'<img src="cid:{cid}" width="{width}" height="{height}"'
        f' alt="" border="0"'
        f' style="display:block;width:{width}px;height:{height}px;border:0;outline:none;'
        f'text-decoration:none;-ms-interpolation-mode:bicubic">'
    )


def _period_value_cell(v: float, is_daily: bool = False, border_b: str = '') -> str:
    """Single numeric value cell. border_b applied directly to the <td>."""
    color = _num_color(v)
    weight = 'bold' if is_daily else 'normal'
    text = 'â€“' if round(v) == 0 else fmt_signed_dollar(v)
    st = _s(text_align='right', font_family=NUM_FONT, font_size='12px',
            color=color, font_weight=weight, white_space='nowrap', padding='0 4px',
            border_bottom=border_b)
    return _td(text, st)


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Module header  (ModuleHeader â€” v4-hybrid.jsx:158)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _render_module_header(kicker: str, title: str = '', hint: str = '') -> str:
    kicker_st = _s(font_size='10px', color=T.muted, text_transform='uppercase',
                   letter_spacing='1.8px', font_weight='700')
    kicker_html = f'<div style="{kicker_st}">{kicker}</div>'

    title_html = ''
    if title:
        title_st = _s(font_size='15px', font_weight='600', margin_top='4px',
                      letter_spacing='-0.1px')
        title_html = f'<div style="{title_st}">{title}</div>'

    hint_html = ''
    if hint:
        hint_st = _s(font_size='10.5px', color=T.subtle, letter_spacing='0.3px')
        hint_html = f'<div style="{hint_st}">{hint}</div>'

    left_cell = _td(kicker_html + title_html)
    right_st = _s(text_align='right', vertical_align='bottom')
    right_cell = _td(hint_html, right_st) if hint else '<td></td>'

    inner = _table(f'<tbody>{_tr(left_cell + right_cell)}</tbody>')
    outer_st = _s(padding='22px 24px 10px', border_top=f'1px solid {T.divider}',
                  background=T.card)
    return f'<div style="{outer_st}">{inner}</div>'


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Subtotal row  (SubtotalRow â€” v4-hybrid.jsx:464)
# border-top applied to each <td>, not the <tr>
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _render_subtotal_row(label: str, values: List[Optional[float]],
                         col_widths: List[str], span_label: int = 1) -> str:
    tr_st = _s(background=T.total_bg)
    border_top = f'2px solid {T.total_rule}'

    label_st = _s(font_size='11px', font_weight='700', color=T.accent,
                  text_transform='uppercase', letter_spacing='1.2px',
                  padding='10px 8px 10px 4px', border_top=border_top)
    cells = f'<td colspan="{span_label}" style="{label_st}">{label}</td>'

    for v in values:
        if v is None:
            null_st = _s(text_align='right', font_family=NUM_FONT, font_size='13px',
                         font_weight='700', padding='10px 4px', border_top=border_top)
            cells += _td('', null_st)
        else:
            color = T.pos if v >= 0 else T.neg
            val_st = _s(text_align='right', font_family=NUM_FONT, font_size='13px',
                        font_weight='700', color=color, letter_spacing='-0.1px',
                        padding='10px 4px', border_top=border_top)
            cells += _td(fmt_signed_dollar(v), val_st)

    col_tds = ''.join(f'<td width="{w}"></td>' for w in col_widths)
    col_row = f'<tr style="height:0">{col_tds}</tr>'
    return _table(f'<tbody>{col_row}{_tr(cells, tr_st)}</tbody>', collapse=True)


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Summary hero  (SummaryHero â€” v4-hybrid.jsx:129)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _render_summary_hero(report_data: dict, periods: List[Tuple[str, str]],
                         is_single: bool, firm_series: List[float],
                         sparkline_dir: Optional[Path] = None) -> str:
    desks = report_data['desks']
    daily_total = sum(d.get('daily', 0) for d in desks.values())

    denom_key = 'ytd' if any(p == 'ytd' for p, _ in periods) else 'mtd'
    denom = sum(d.get(denom_key, 0) for d in desks.values())
    share_pct = (daily_total / denom * 100) if denom != 0 else 0.0

    positive_count = sum(1 for d in desks.values() if d.get('daily', 0) > 0)
    total_count = len(desks)

    color = T.pos if daily_total >= 0 else T.neg
    sign = '+' if daily_total >= 0 else 'âˆ’'
    hero_text = f'{sign}${abs(round(daily_total)):,}'

    kicker = 'Desk P&amp;L Â· Today' if is_single else 'Trading P&amp;L Â· Today'
    share_sign = '+' if share_pct >= 0 else ''
    denom_label = 'YTD' if denom_key == 'ytd' else 'MTD'
    subject_label = 'desk' if not is_single else 'portfolio'
    positive_str = (f' &middot; {positive_count} of {total_count} {subject_label}s positive'
                    if total_count > 0 else '')
    subtext = f'{share_sign}{share_pct:.1f}% of {denom_label}{positive_str}'

    kicker_st = _s(font_size='11px', color=T.muted, text_transform='uppercase',
                   letter_spacing='2px', margin_bottom='10px')
    kicker_div = f'<div style="{kicker_st}">{kicker}</div>'

    hero_st = _s(font_family=NUM_FONT, font_size='52px', font_weight='500',
                 letter_spacing='-1.5px', color=color, line_height='1')
    hero_div = f'<div style="{hero_st}">{hero_text}</div>'

    spark_html = _sparkline_html(
        'firm', firm_series, width=110, height=30,
        show_last_dot=True, sparkline_dir=sparkline_dir,
    )
    spark_label_st = _s(font_size='10px', color=T.subtle, margin_top='2px',
                        letter_spacing='0.4px', text_transform='uppercase')
    spark_label = f'<div style="{spark_label_st}">30-day</div>'

    left_st = _s(vertical_align='bottom')
    right_st = _s(vertical_align='bottom', padding_left='16px', padding_bottom='6px')
    hero_row = _tr(_td(hero_div, left_st) + _td(spark_html + spark_label, right_st))

    subtext_st = _s(font_size='13px', color=T.muted, margin_top='8px')
    subtext_row = _tr(_td(f'<div style="{subtext_st}">{subtext}</div>'))

    inner = _table(f'<tbody>{hero_row}{subtext_row}</tbody>')
    wrapper_st = _s(padding='28px 28px 22px')
    return f'<tr><td style="{wrapper_st}">{kicker_div}{inner}</td></tr>'


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Period strip  (v4-hybrid.jsx:79)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _render_period_strip(totals: Dict[str, float], periods: List[Tuple[str, str]]) -> str:
    n = len(periods)
    cell_pct = f'{100 // n}%'
    cells = ''
    for i, (p, label) in enumerate(periods):
        v = totals.get(p, 0.0)
        color = T.pos if v >= 0 else T.neg
        sign = '+' if v >= 0 else 'âˆ’'
        text = f'{sign}${fmt_compact(abs(v))}'
        border_right = f'1px solid {T.divider}' if i < n - 1 else ''

        label_st = _s(font_size='9.5px', color=T.muted, text_transform='uppercase',
                      letter_spacing='1.6px', font_weight='600')
        label_div = f'<div style="{label_st}">{label}</div>'

        value_st = _s(font_family=NUM_FONT, font_size='18px', font_weight='500',
                      margin_top='4px', letter_spacing='-0.2px', color=color)
        value_div = f'<div style="{value_st}">{text}</div>'

        cell_st = _s(width=cell_pct, padding='14px 12px', border_right=border_right,
                     text_align='center')
        cells += _td(label_div + value_div, cell_st)

    strip_st = _s(border_top=f'1px solid {T.divider}',
                  border_bottom=f'1px solid {T.divider}',
                  background=T.wash)
    inner = _table(f'<tbody>{_tr(cells)}</tbody>')
    return f'<tr><td style="{strip_st}">{inner}</td></tr>'


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# By Desk module  (ModuleByDesk â€” v4-hybrid.jsx:172)
# border-bottom on each <td>; border-collapse:collapse on table
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _render_module_by_desk(report_data: dict, periods: List[Tuple[str, str]],
                           sparkline_dir: Optional[Path] = None) -> str:
    desks_dict = report_data['desks']
    desk_names = report_data['deskNames']
    spark_data = report_data['sparkData']
    sorted_desks = sorted(desks_dict.items(), key=lambda kv: kv[1].get('daily', 0), reverse=True)

    col_widths = ['140', '130'] + ['72'] * len(periods)
    hdr_border = f'1px solid {T.divider}'

    # Header row â€” border on each <td>
    hdr_l_st = _s(font_size='9.5px', color=T.muted, text_transform='uppercase',
                  letter_spacing='1.4px', font_weight='600', padding='12px 4px 8px 4px',
                  border_bottom=hdr_border)
    hdr_sp_st = _s(font_size='9.5px', color=T.muted, text_transform='uppercase',
                   letter_spacing='1.4px', font_weight='600', padding='12px 4px 8px',
                   border_bottom=hdr_border)
    hdr_n_st = _s(font_size='9.5px', color=T.muted, text_transform='uppercase',
                  letter_spacing='1.4px', font_weight='600', text_align='right',
                  padding='12px 4px 8px', border_bottom=hdr_border)

    header_cells = (_td('Desk', hdr_l_st) + _td('30-day', hdr_sp_st)
                    + ''.join(_td(label, hdr_n_st) for _, label in periods))
    header_row = _tr(header_cells)

    # Data rows â€” border-bottom on each <td>
    data_rows = ''
    for i, (key, desk) in enumerate(sorted_desks):
        name = desk_names.get(key, key)
        series = spark_data.get(key, [])
        spark_html = _sparkline_html(
            key, series, width=120, height=26,
            show_last_dot=True, sparkline_dir=sparkline_dir,
        )

        border_b = f'1px solid {T.divider}' if i < len(sorted_desks) - 1 else ''
        name_st = _s(font_size='13px', font_weight='500', padding='11px 4px',
                     border_bottom=border_b)
        spark_st = _s(padding='11px 4px', vertical_align='middle', border_bottom=border_b)
        period_cells = ''.join(
            _period_value_cell(desk.get(p, 0.0), p == 'daily', border_b=border_b)
            for p, _ in periods
        )
        data_rows += _tr(_td(name, name_st) + _td(spark_html, spark_st) + period_cells)

    totals_values = [sum(d.get(p, 0) for d in desks_dict.values()) for p, _ in periods]
    subtotal = _render_subtotal_row('Firm Total', totals_values, col_widths, span_label=2)

    col_row = '<tr style="height:0">' + ''.join(f'<td width="{w}"></td>' for w in col_widths) + '</tr>'
    table_html = _table(f'<tbody>{col_row}{header_row}{data_rows}</tbody>', collapse=True)

    inner_st = _s(padding='0 24px 16px')
    header_html = _render_module_header('By Desk', hint='Ranked by today')
    return (f'<tr><td>{header_html}</td></tr>'
            f'<tr><td style="{inner_st}">{table_html}{subtotal}</td></tr>')


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Portfolio Ã— Strategy module  (ModulePortfolioStrategy â€” v4-hybrid.jsx:225)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _render_module_portfolio_strategy(report_data: dict, periods: List[Tuple[str, str]],
                                      single_desk: Optional[str]) -> str:
    rows_all = report_data.get('deskPortfolioStrategy', [])
    desk_names = report_data['deskNames']
    desks_order = list(report_data['desks'].keys())

    by_desk: Dict[str, list] = {}
    for r in rows_all:
        by_desk.setdefault(r['desk'], []).append(r)

    desk_keys = [k for k in desks_order if by_desk.get(k)]
    if not desk_keys:
        return ''

    title = ('P&amp;L by Portfolio &amp; Strategy' if single_desk
             else 'P&amp;L by Desk Â· Portfolio Â· Strategy')
    header = _render_module_header('Portfolio &times; Strategy', title=title)

    col_widths = ['200', '180'] + ['72'] * len(periods)
    col_row = ('<tr style="height:0">'
               + ''.join(f'<td width="{w}"></td>' for w in col_widths) + '</tr>')

    hdr_border = f'1px solid {T.divider}'
    hdr_l_st = _s(font_size='9.5px', color=T.muted, text_transform='uppercase',
                  letter_spacing='1.4px', font_weight='600', padding='12px 4px 8px 4px',
                  border_bottom=hdr_border)
    hdr_r_st = _s(font_size='9.5px', color=T.muted, text_transform='uppercase',
                  letter_spacing='1.4px', font_weight='600', text_align='right',
                  padding='12px 4px 8px', border_bottom=hdr_border)
    header_cells = (_td('Portfolio', hdr_l_st) + _td('Strategy', hdr_l_st)
                    + ''.join(_td(label, hdr_r_st) for _, label in periods))
    hdr_row = _tr(header_cells)

    np_ = len(periods)
    content_rows = ''
    for desk_key in desk_keys:
        desk_rows = sorted(by_desk[desk_key],
                           key=lambda r: abs(r.get('daily', 0)), reverse=True)
        subtotal = {p: sum(r.get(p, 0) for r in by_desk[desk_key]) for p, _ in periods}

        if not single_desk:
            desk_label = desk_names.get(desk_key, desk_key)
            dk_st = _s(padding='14px 4px 6px', font_size='11px', font_weight='700',
                       color=T.accent, letter_spacing='0.6px', text_transform='uppercase')
            content_rows += _tr(_td(desk_label, dk_st, colspan=str(2 + np_)))

        for i, r in enumerate(desk_rows):
            border_b = f'1px solid {T.divider}' if i < len(desk_rows) - 1 else ''
            port = r.get('portfolio') or '(none)'
            strat = r.get('strategy') or '(none)'
            port_st = _s(font_size='12.5px', font_weight='500', padding='11px 4px',
                         border_bottom=border_b)
            strat_st = _s(font_size='12px', color=T.muted, padding='11px 4px',
                          border_bottom=border_b)
            port_cell = _td('â€”' if port == '(none)' else port, port_st)
            strat_cell = _td('â€”' if strat == '(none)' else strat, strat_st)
            p_cells = ''.join(
                _period_value_cell(r.get(p, 0), p == 'daily', border_b=border_b)
                for p, _ in periods
            )
            content_rows += _tr(port_cell + strat_cell + p_cells)

        total_label = 'Total' if single_desk else f'{desk_names.get(desk_key, desk_key)} total'
        sub_html = _render_subtotal_row(total_label,
                                        [subtotal[p] for p, _ in periods],
                                        col_widths, span_label=2)
        content_rows += f'<tr><td colspan="{2 + np_}">{sub_html}</td></tr>'

    tbody = f'<tbody>{col_row}{hdr_row}{content_rows}</tbody>'
    inner_st = _s(padding='0 24px 0')
    return (f'<tr><td>{header}</td></tr>'
            f'<tr><td style="{inner_st}">{_table(tbody, collapse=True)}</td></tr>')


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Product Ã— Vintage module  (ModuleProductVintage â€” v4-hybrid.jsx:301)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _render_module_product_vintage(report_data: dict, periods: List[Tuple[str, str]],
                                   single_desk: Optional[str]) -> str:
    rows_all = report_data.get('productVintage', [])
    desk_names = report_data['deskNames']
    desks_order = list(report_data['desks'].keys())

    by_desk: Dict[str, list] = {}
    for r in rows_all:
        by_desk.setdefault(r['desk'], []).append(r)

    desk_keys = [k for k in desks_order if by_desk.get(k)]
    if not desk_keys:
        return ''

    header = _render_module_header('Product &times; Vintage',
                                   title='P&amp;L by Product and Vintage')

    col_widths = ['200', '120'] + ['72'] * len(periods)
    col_row = ('<tr style="height:0">'
               + ''.join(f'<td width="{w}"></td>' for w in col_widths) + '</tr>')

    hdr_border = f'1px solid {T.divider}'
    hdr_l_st = _s(font_size='9.5px', color=T.muted, text_transform='uppercase',
                  letter_spacing='1.4px', font_weight='600', padding='12px 4px 8px 4px',
                  border_bottom=hdr_border)
    hdr_r_st = _s(font_size='9.5px', color=T.muted, text_transform='uppercase',
                  letter_spacing='1.4px', font_weight='600', text_align='right',
                  padding='12px 4px 8px', border_bottom=hdr_border)
    header_cells = (_td('Product', hdr_l_st) + _td('Vintage', hdr_l_st)
                    + ''.join(_td(label, hdr_r_st) for _, label in periods))
    hdr_row = _tr(header_cells)

    np_ = len(periods)
    content_rows = ''
    for desk_key in desk_keys:
        all_rows = by_desk[desk_key]
        filtered = [r for r in all_rows
                    if abs(r.get('daily', 0)) > 100 or abs(r.get('mtd', 0)) > 1000]
        filtered.sort(key=lambda r: abs(r.get('daily', 0)), reverse=True)
        desk_rows = filtered[:16]
        if not desk_rows:
            continue
        subtotal = {p: sum(r.get(p, 0) for r in all_rows) for p, _ in periods}

        if not single_desk:
            desk_label = desk_names.get(desk_key, desk_key)
            dk_st = _s(padding='14px 4px 6px', font_size='11px', font_weight='700',
                       color=T.accent, letter_spacing='0.6px', text_transform='uppercase')
            content_rows += _tr(_td(desk_label, dk_st, colspan=str(2 + np_)))

        for i, r in enumerate(desk_rows):
            border_b = f'1px solid {T.divider}' if i < len(desk_rows) - 1 else ''
            product_st = _s(font_size='12.5px', font_weight='500', padding='11px 4px',
                            border_bottom=border_b)
            vintage_st = _s(font_size='12px', color=T.muted, font_family=NUM_FONT,
                            padding='11px 4px', border_bottom=border_b)
            product_cell = _td(r.get('product', ''), product_st)
            vintage_cell = _td(r.get('vintage', '') or 'â€”', vintage_st)
            p_cells = ''.join(
                _period_value_cell(r.get(p, 0), p == 'daily', border_b=border_b)
                for p, _ in periods
            )
            content_rows += _tr(product_cell + vintage_cell + p_cells)

        total_label = 'Total' if single_desk else f'{desk_names.get(desk_key, desk_key)} total'
        sub_html = _render_subtotal_row(total_label,
                                        [subtotal[p] for p, _ in periods],
                                        col_widths, span_label=2)
        content_rows += f'<tr><td colspan="{2 + np_}">{sub_html}</td></tr>'

    if not content_rows:
        return ''

    tbody = f'<tbody>{col_row}{hdr_row}{content_rows}</tbody>'
    inner_st = _s(padding='0 24px 0')
    return (f'<tr><td>{header}</td></tr>'
            f'<tr><td style="{inner_st}">{_table(tbody, collapse=True)}</td></tr>')


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Daily Detail module  (ModuleDailyDetail â€” v4-hybrid.jsx:381)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _render_module_daily_detail(report_data: dict, single_desk: Optional[str]) -> str:
    detail = report_data.get('dailyDetails', {})
    desk_names = report_data['deskNames']
    desks_order = list(report_data['desks'].keys())

    desk_keys = [k for k in desks_order if detail.get(k)]
    if not desk_keys:
        return ''

    header = _render_module_header('Daily Detail',
                                   title='Daily P&amp;L Â· Marks as of close',
                                   hint='Mark = current &rarr; prior')

    col_widths = ['180', '100', '100', '100', '160']
    col_row = ('<tr style="height:0">'
               + ''.join(f'<td width="{w}"></td>' for w in col_widths) + '</tr>')

    hdr_border = f'1px solid {T.divider}'
    hdr_l_st = _s(font_size='9.5px', color=T.muted, text_transform='uppercase',
                  letter_spacing='1.4px', font_weight='600', padding='12px 4px 8px 4px',
                  border_bottom=hdr_border)
    hdr_r_st = _s(font_size='9.5px', color=T.muted, text_transform='uppercase',
                  letter_spacing='1.4px', font_weight='600', text_align='right',
                  padding='12px 4px 8px', border_bottom=hdr_border)
    hdr_mark_st = _s(font_size='9.5px', color=T.muted, text_transform='uppercase',
                     letter_spacing='1.4px', font_weight='600',
                     padding='12px 4px 8px 16px', border_bottom=hdr_border)
    header_cells = (_td('Product', hdr_l_st) + _td('Vintage', hdr_l_st)
                    + _td('Position', hdr_r_st) + _td('Daily', hdr_r_st)
                    + _td('Mark Â· current / prior', hdr_mark_st))
    hdr_row = _tr(header_cells)

    content_rows = ''
    for desk_key in desk_keys:
        rows = [r for r in detail[desk_key].values()
                if r.get('position', 0) != 0 or r.get('daily', 0) != 0]
        rows.sort(key=lambda r: abs(r.get('daily', 0)), reverse=True)
        rows = rows[:20]
        if not rows:
            continue
        subtotal_daily = sum(r.get('daily', 0) for r in rows)

        if not single_desk:
            desk_label = desk_names.get(desk_key, desk_key)
            dk_st = _s(padding='14px 4px 6px', font_size='11px', font_weight='700',
                       color=T.accent, letter_spacing='0.6px', text_transform='uppercase')
            content_rows += _tr(_td(desk_label, dk_st, colspan='5'))

        for i, r in enumerate(rows):
            border_b = f'1px solid {T.divider}' if i < len(rows) - 1 else ''

            mark_cd = r.get('mark_cd', 0.0)
            mark_pd = r.get('mark_pd', 0.0)
            dec = 2 if mark_cd >= 50 else 4
            if mark_cd > mark_pd:
                arrow, mark_color = '&#9650;', T.pos
            elif mark_cd < mark_pd:
                arrow, mark_color = '&#9660;', T.neg
            else:
                arrow, mark_color = '&middot;', T.subtle

            pos_val = r.get('position', 0.0)
            daily_val = r.get('daily', 0.0)
            daily_color = T.subtle if round(daily_val) == 0 else (T.pos if daily_val >= 0 else T.neg)
            daily_text = ('â€“' if round(daily_val) == 0
                          else ('+' if daily_val >= 0 else 'âˆ’') + '$' + fmt_compact(abs(daily_val)))

            cd_st = _s(color=T.ink, font_weight='500')
            arr_st = _s(color=mark_color, font_size='10px')
            pd_st = _s(color=T.subtle, font_size='11px')
            mark_html = (f'<span style="{cd_st}">{fmt_price(mark_cd, dec)}</span>'
                         f'&nbsp;<span style="{arr_st}">{arrow}</span>&nbsp;'
                         f'<span style="{pd_st}">{fmt_price(mark_pd, dec)}</span>')

            pos_color = T.neg if pos_val < 0 else T.ink
            prod_st = _s(font_size='12.5px', font_weight='500', padding='11px 4px',
                         border_bottom=border_b)
            vint_st = _s(font_size='12px', color=T.muted, font_family=NUM_FONT,
                         padding='11px 4px', border_bottom=border_b)
            pos_st = _s(text_align='right', font_family=NUM_FONT, font_size='12px',
                        color=pos_color, padding='11px 4px', border_bottom=border_b)
            day_st = _s(text_align='right', font_family=NUM_FONT, font_size='12px',
                        color=daily_color, font_weight='500', padding='11px 4px',
                        border_bottom=border_b)
            mark_td_st = _s(font_family=NUM_FONT, font_size='11.5px',
                            padding='11px 4px 11px 16px', border_bottom=border_b)

            cells = (
                _td(r.get('product', ''), prod_st)
                + _td(r.get('vintage', '') or 'â€”', vint_st)
                + _td(fmt_vol(pos_val), pos_st)
                + _td(daily_text, day_st)
                + _td(mark_html, mark_td_st)
            )
            content_rows += _tr(cells)

        total_label = ('Daily total' if single_desk
                       else f'{desk_names.get(desk_key, desk_key)} daily total')
        # span_label=3 puts the label across Product+Vintage+Position so the
        # value lands in the Daily column, aligned with the "DAILY" header.
        sub_html = _render_subtotal_row(total_label, [subtotal_daily, None],
                                        col_widths, span_label=3)
        content_rows += f'<tr><td colspan="5">{sub_html}</td></tr>'

    if not content_rows:
        return ''

    tbody = f'<tbody>{col_row}{hdr_row}{content_rows}</tbody>'
    inner_st = _s(padding='0 24px 0')
    return (f'<tr><td>{header}</td></tr>'
            f'<tr><td style="{inner_st}">{_table(tbody, collapse=True)}</td></tr>')


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Document shell
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _render_document(inner_rows: str) -> str:
    body_st = _s(margin='0', padding='24px', background=T.bg,
                 font_family=SANS_FONT, font_size='13px', color=T.ink)
    card_st = _s(background=T.card, border=f'1px solid {T.divider}', overflow='hidden')

    mso_open = '<!--[if mso]><table width="720" align="center"><tr><td><![endif]-->'
    mso_close = '<!--[if mso]></td></tr></table><![endif]-->'

    card_table = _table(f'<tbody>{inner_rows}</tbody>', width='720', style=card_st)
    max_div_st = _s(max_width='720px', margin='0 auto')
    center_div = f'<div style="{max_div_st}">{mso_open}{card_table}{mso_close}</div>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<!--[if mso]><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml><![endif]-->
</head>
<body style="{body_st}">
{center_div}
</body>
</html>"""


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Public entry point
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def render(
    report_data: dict,
    sparkline_dir: Optional[Path] = None,
    density: str = 'comfortable',
    include_qt_ytd: bool = True,
    modules: Optional[List[str]] = None,
    single_desk: Optional[str] = None,
) -> str:
    """
    Render the v4-hybrid PnL report as an Outlook-safe HTML string.

    Sparkline mode is controlled by `sparkline_dir`:
      - None: inline <svg> tags (good for standalone HTML viewed in a browser;
              Gmail strips these so use only for local viewing or non-Gmail clients).
      - Path: writes one PNG per sparkline into the directory (`spark_firm.png`
              plus `spark_{desk_key}.png`) and emits `<img src="cid:...">` tags.
              EmailSender._attach_inline_images picks every PNG up automatically
              and attaches it with Content-ID `<{stem}@karbone>`, which Gmail
              renders correctly. No external network requests; image bytes ride
              inside the MIME envelope.

    Row borders are applied to each <td> element (not <tr>) with border-collapse:collapse
    on the parent table.
    """
    if modules is None:
        modules = ['portfolio_strategy', 'product_vintage', 'daily_detail']
    if sparkline_dir is not None:
        sparkline_dir.mkdir(parents=True, exist_ok=True)

    periods = _PERIODS_ALL if include_qt_ytd else _PERIODS_NO_QY
    desks = report_data['desks']
    desk_names = report_data['deskNames']
    as_of = report_data.get('asOf', '')
    is_single = bool(single_desk and single_desk in desks)

    # Firm-level sparkline: sum of all desk series per day
    spark_dates = report_data.get('sparkDates', [])
    spark_data_all = report_data.get('sparkData', {})
    n = len(spark_dates)
    firm_series: List[float] = (
        [sum(spark_data_all.get(dk, [0.0] * n)[i] for dk in spark_data_all)
         for i in range(n)]
        if n > 0 else []
    )

    totals = {p: sum(d.get(p, 0) for d in desks.values()) for p, _ in periods}

    # â”€â”€ Header band â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if is_single:
        kicker = f'Karbone Risk Â· {desk_names.get(single_desk, single_desk)} Desk'
        title = f'{desk_names.get(single_desk, single_desk)} Â· Daily P&amp;L Â· {fmt_long_date(as_of)}'
    else:
        kicker = 'Karbone Risk'
        title = f'Daily P&amp;L Â· {fmt_long_date(as_of)}'

    kicker_st = _s(font_size='10px', letter_spacing='2.5px', opacity='0.7',
                   text_transform='uppercase', margin_bottom='3px')
    kicker_div = f'<div style="{kicker_st}">{kicker}</div>'
    title_st = _s(font_size='17px', font_weight='500', letter_spacing='-0.2px')
    title_div = f'<div style="{title_st}">{title}</div>'
    header_band_st = _s(background=T.accent, color='#ffffff', padding='18px 24px')
    header_row = f'<tr><td style="{header_band_st}">{kicker_div}{title_div}</td></tr>'

    # â”€â”€ Assembled sections â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    hero_row = _render_summary_hero(report_data, periods, is_single, firm_series,
                                    sparkline_dir=sparkline_dir)
    strip_row = _render_period_strip(totals, periods)
    by_desk_rows = ('' if is_single
                    else _render_module_by_desk(report_data, periods,
                                                sparkline_dir=sparkline_dir))

    sd = single_desk if is_single else None
    ps_rows = (_render_module_portfolio_strategy(report_data, periods, sd)
               if 'portfolio_strategy' in modules else '')
    pv_rows = (_render_module_product_vintage(report_data, periods, sd)
               if 'product_vintage' in modules else '')
    dd_rows = (_render_module_daily_detail(report_data, sd)
               if 'daily_detail' in modules else '')

    # â”€â”€ Footer â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    generated = report_data.get('generated', '')
    meta_line = (f'USD figures, trading P&amp;L only. '
                 f'Generated {generated} &middot; Karbone Risk')
    confidentiality = (
        'The information contained in this e-mail transmittal is privileged and confidential '
        'and intended for the addressee only. If you are neither the intended recipient nor '
        'the employee or agent responsible for delivering this e-mail to the intended '
        'recipient, any disclosure of this information in any way or taking of any action '
        'in reliance on this information is strictly prohibited. If you have received this '
        'e-mail in error, please notify the person transmitting the information immediately.'
    )
    meta_st = _s(font_size='11px', color=T.muted, line_height='1.5',
                 margin_bottom='10px')
    conf_st = _s(font_size='10px', color=T.subtle, line_height='1.6')
    footer_inner = (f'<div style="{meta_st}">{meta_line}</div>'
                    f'<div style="{conf_st}">{confidentiality}</div>')
    footer_st = _s(padding='14px 24px 18px', background=T.wash,
                   border_top=f'1px solid {T.divider}')
    footer_row = f'<tr><td style="{footer_st}">{footer_inner}</td></tr>'

    inner_rows = (header_row + hero_row + strip_row
                  + by_desk_rows + ps_rows + pv_rows + dd_rows
                  + footer_row)
    return _render_document(inner_rows)

