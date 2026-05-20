"""Top-level HTML composer for the daily_recon report."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from karbone_pnl_pos.reporting.html_builder import _render_document
from karbone_pnl_pos.reporting.theme import THEME as T

from daily_recon.config import MAX_TABLE_ROWS_IN_EMAIL
from daily_recon.report.modules import (
    render_empty_module,
    render_exception_table,
    render_header_band,
    render_hero,
)


@dataclass
class ReconReportData:
    business_date: date
    run_id: str
    prior_run_date: Optional[date]
    mo_leg_count: int
    fo_line_count: int
    counts: dict[str, int]
    exceptions: list[dict] = field(default_factory=list)


def compose_subject(data: ReconReportData) -> str:
    total = data.counts.get("position_break", 0)
    if total == 0:
        return f"[RINs Recon] {data.business_date.isoformat()} — clean"
    noun = "exception" if total == 1 else "exceptions"
    return f"[RINs Recon] {data.business_date.isoformat()} — {total} position break {noun}"


def _exceptions_of(data: ReconReportData, check_id: str) -> list[dict]:
    return [e["payload"] for e in data.exceptions if e["check_id"] == check_id]


def _section_position_break(data: ReconReportData) -> str:
    rows = _exceptions_of(data, "position_break")
    if not rows:
        return render_empty_module(
            kicker="Position Break",
            title="MO vs FO running position · all dates",
            empty_text="No MO vs FO position breaks detected.",
            hint="tolerance 1e-6",
        )
    cols = [
        ("business_date", "Date", "text"),
        ("product", "Product", "text"),
        ("vintage", "Vintage", "text"),
        ("mo_position", "MO", "num"),
        ("fo_position", "FO", "num"),
        ("delta", "Δ", "num"),
    ]
    return render_exception_table(
        kicker="Position Break",
        title="MO vs FO running position · all dates",
        rows=rows, columns=cols,
        max_rows=MAX_TABLE_ROWS_IN_EMAIL,
        subtotal_label="Position Break",
        subtotal_count=len(rows),
        hint="tolerance 1e-6",
    )


def _footer() -> str:
    from karbone_pnl_pos.reporting.html_builder import _s
    foot_st = _s(border_top=f"1px solid {T.divider}", background=T.wash,
                 padding="14px 24px")
    text_st = _s(font_size="10.5px", color=T.subtle, letter_spacing="0.3px")
    return (f'<tr><td style="{foot_st}">'
            f'<div style="{text_st}">Karbone Risk · Daily Reconciliation</div>'
            f"</td></tr>")


def compose_html(data: ReconReportData) -> str:
    n = data.counts.get("position_break", 0)
    rows = (
        render_header_band(data.business_date, data.run_id, data.prior_run_date)
        + render_hero(
            total_exceptions=n,
            total_checks=1,
            failed_checks=1 if n > 0 else 0,
        )
        + _section_position_break(data)
        + _footer()
    )
    return _render_document(rows)
