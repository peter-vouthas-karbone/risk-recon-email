"""Section renderers for the daily_recon email report.

Each renderer returns an HTML fragment that fits inside the document shell
provided by karbone_pnl_pos.reporting.html_builder._render_document.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable

from karbone_pnl_pos.reporting.html_builder import (
    _render_module_header,
    _render_subtotal_row,
    _s,
    _table,
    _td,
    _tr,
)
from karbone_pnl_pos.reporting.theme import NUM_FONT, SANS_FONT, THEME as T

_CHECK_LABELS = {
    "trade_drift": "Trade Drift",
    "historical_position_drift": "Hist. Position",
    "position_break": "Position Break",
    "prior_day_trades": "T-1 Trades",
}
_CHECK_ORDER = ["trade_drift", "historical_position_drift", "position_break", "prior_day_trades"]


def render_header_band(business_date: date, run_id: str, prior_run_date: date | None) -> str:
    kicker_st = _s(font_size="10px", color=T.muted, text_transform="uppercase",
                   letter_spacing="1.8px", font_weight="700")
    title_st = _s(font_size="15px", font_weight="600", margin_top="4px", letter_spacing="-0.1px")
    hint_st = _s(font_size="10.5px", color=T.subtle, letter_spacing="0.3px")
    try:
        long_date = business_date.strftime("%A, %B %d, %Y")
    except Exception:
        long_date = str(business_date)
    prior_part = f" · prior {prior_run_date.isoformat()}" if prior_run_date else " · no prior run"
    hint = f"run {run_id}{prior_part}"
    left = _td(
        f'<div style="{kicker_st}">Karbone Risk &middot; Operations</div>'
        f'<div style="{title_st}">RINs Reconciliation &middot; {long_date}</div>'
    )
    right = _td(f'<div style="{hint_st}">{hint}</div>',
                _s(text_align="right", vertical_align="bottom"))
    inner = _table(f"<tbody>{_tr(left + right)}</tbody>")
    outer_st = _s(padding="22px 24px 10px", border_top=f"1px solid {T.divider}", background=T.card)
    return f'<tr><td><div style="{outer_st}">{inner}</div></td></tr>'


def render_hero(total_exceptions: int, total_checks: int, failed_checks: int) -> str:
    color = T.neg if total_exceptions > 0 else T.pos
    kicker_st = _s(font_size="11px", color=T.muted, text_transform="uppercase",
                   letter_spacing="2px", margin_bottom="10px")
    hero_st = _s(font_family=NUM_FONT, font_size="52px", font_weight="500",
                 letter_spacing="-1.5px", color=color, line_height="1")
    subtext_st = _s(font_size="13px", color=T.muted, margin_top="8px")
    sub = f"{failed_checks} of {total_checks} checks failed" if total_exceptions else "All checks clean"
    wrapper_st = _s(padding="28px 28px 22px")
    return (
        f'<tr><td style="{wrapper_st}">'
        f'<div style="{kicker_st}">Exceptions &middot; Today</div>'
        f'<div style="{hero_st}">{total_exceptions}</div>'
        f'<div style="{subtext_st}">{sub}</div>'
        f"</td></tr>"
    )


def render_check_strip(counts: dict[str, int]) -> str:
    cells = ""
    n = len(_CHECK_ORDER)
    for i, key in enumerate(_CHECK_ORDER):
        v = counts.get(key, 0)
        color = T.subtle if v == 0 else T.neg
        text = "–" if v == 0 else str(v)
        border_right = f"1px solid {T.divider}" if i < n - 1 else ""
        label_st = _s(font_size="9.5px", color=T.muted, text_transform="uppercase",
                      letter_spacing="1.6px", font_weight="600")
        value_st = _s(font_family=NUM_FONT, font_size="18px", font_weight="500",
                      margin_top="4px", letter_spacing="-0.2px", color=color)
        cell_st = _s(width=f"{100 // n}%", padding="14px 12px",
                     border_right=border_right, text_align="center")
        cells += _td(
            f'<div style="{label_st}">{_CHECK_LABELS[key]}</div>'
            f'<div style="{value_st}">{text}</div>',
            cell_st,
        )
    strip_st = _s(border_top=f"1px solid {T.divider}",
                  border_bottom=f"1px solid {T.divider}", background=T.wash)
    inner = _table(f"<tbody>{_tr(cells)}</tbody>")
    return f'<tr><td style="{strip_st}">{inner}</td></tr>'


def render_exception_table(
    *,
    kicker: str,
    title: str,
    rows: list[dict],
    columns: list[tuple[str, str, str]],  # (key, header_label, kind in {"text","num","status"})
    max_rows: int,
    subtotal_label: str,
    subtotal_count: int,
    hint: str = "",
) -> str:
    header_html = _render_module_header(kicker, title=title, hint=hint)

    hdr_border = f"1px solid {T.divider}"
    header_cells = ""
    for _, label, kind in columns:
        align = "right" if kind == "num" else "left"
        hdr_st = _s(font_size="9.5px", color=T.muted, text_transform="uppercase",
                    letter_spacing="1.4px", font_weight="600",
                    text_align=align, padding="12px 4px 8px",
                    border_bottom=hdr_border)
        header_cells += _td(label, hdr_st)

    visible_rows = rows[:max_rows]
    body_rows = ""
    for i, row in enumerate(visible_rows):
        border_b = f"1px solid {T.divider}" if i < len(visible_rows) - 1 else ""
        cells = ""
        for key, _label, kind in columns:
            v = row.get(key, "")
            if kind == "num":
                st = _s(text_align="right", font_family=NUM_FONT, font_size="12px",
                        padding="11px 4px", border_bottom=border_b, white_space="nowrap")
                cells += _td("" if v is None else str(v), st)
            elif kind == "status":
                st = _s(font_size="11px", font_weight="700", color=T.neg,
                        text_transform="uppercase", letter_spacing="0.6px",
                        padding="11px 4px", border_bottom=border_b)
                cells += _td(str(v), st)
            else:
                st = _s(font_size="12.5px", font_weight="500",
                        padding="11px 4px", border_bottom=border_b)
                cells += _td("" if v is None else str(v), st)
        body_rows += _tr(cells)

    truncated_html = ""
    if len(rows) > max_rows:
        more = len(rows) - max_rows
        note_st = _s(font_size="11px", font_style="italic", color=T.subtle, padding="10px 4px")
        truncated_html = _tr(
            _td(f"… {more} more rows in attached CSV", note_st, colspan=str(len(columns)))
        )

    table = _table(f"<tbody>{_tr(header_cells)}{body_rows}{truncated_html}</tbody>", collapse=True)

    col_widths = ["100"] * len(columns)
    subtotal_html = _render_subtotal_row(
        f"{subtotal_label} · {subtotal_count} exceptions",
        [None] * (len(columns) - 1),
        col_widths,
        span_label=1,
    )

    inner_st = _s(padding="0 24px 16px")
    return (f"<tr><td>{header_html}</td></tr>"
            f'<tr><td style="{inner_st}">{table}{subtotal_html}</td></tr>')


def render_empty_module(kicker: str, title: str, empty_text: str, hint: str = "") -> str:
    header_html = _render_module_header(kicker, title=title, hint=hint)
    inner_st = _s(padding="0 24px 22px", text_align="center")
    msg_st = _s(font_size="13px", color=T.muted, padding="18px 0")
    return (f"<tr><td>{header_html}</td></tr>"
            f'<tr><td style="{inner_st}"><div style="{msg_st}">{empty_text}</div></td></tr>')
