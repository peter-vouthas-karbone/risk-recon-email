"""Top-level HTML composer for the daily_recon report."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from karbone_pnl_pos.reporting.html_builder import _render_document
from karbone_pnl_pos.reporting.theme import SANS_FONT, THEME as T

from daily_recon.config import MAX_TABLE_ROWS_IN_EMAIL
from daily_recon.report.modules import (
    render_check_strip,
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
    total = sum(data.counts.values())
    if total == 0:
        return f"[RINs Recon] {data.business_date.isoformat()} — clean"
    failed = sum(1 for v in data.counts.values() if v > 0)
    noun = "check" if failed == 1 else "checks"
    return f"[RINs Recon] {data.business_date.isoformat()} — {total} exceptions across {failed} {noun}"


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


def _section_prior_day_trades(data: ReconReportData) -> str:
    rows = _exceptions_of(data, "prior_day_trades")
    if not rows:
        return render_empty_module(
            kicker="T-1 Trades",
            title="Prior-day trade match",
            empty_text="No prior-day trade breaks detected.",
        )
    cols = [
        ("counterparty", "Counterparty", "text"),
        ("side", "Side", "text"),
        ("product", "Product", "text"),
        ("vintage", "Vint.", "text"),
        ("status", "Status", "status"),
        ("mo_volume", "MO vol", "num"),
        ("fo_volume", "FO vol", "num"),
    ]
    return render_exception_table(
        kicker="T-1 Trades",
        title="Prior-day trade match",
        rows=rows, columns=cols,
        max_rows=MAX_TABLE_ROWS_IN_EMAIL,
        subtotal_label="T-1 Trades",
        subtotal_count=len(rows),
    )


def _section_trade_drift(data: ReconReportData) -> str:
    rows = _exceptions_of(data, "trade_drift")
    if not rows:
        return render_empty_module(
            kicker="Trade Drift",
            title="Changes since prior run · per system",
            empty_text="No trade drift detected since the prior run.",
            hint="new_trade suppressed",
        )
    cols = [
        ("source", "Source", "text"),
        ("counterparty", "Counterparty", "text"),
        ("change_type", "Change", "status"),
        ("product", "Product", "text"),
        ("vintage", "Vintage", "text"),
        ("prior_volume", "Prior vol", "num"),
        ("current_volume", "Current vol", "num"),
    ]
    return render_exception_table(
        kicker="Trade Drift",
        title="Changes since prior run · per system",
        rows=rows, columns=cols,
        max_rows=MAX_TABLE_ROWS_IN_EMAIL,
        subtotal_label="Trade Drift",
        subtotal_count=len(rows),
        hint="new_trade suppressed",
    )


def _section_historical_position(data: ReconReportData) -> str:
    rows = _exceptions_of(data, "historical_position_drift")
    if not rows:
        return render_empty_module(
            kicker="Hist. Position",
            title="Historical position drift · dates < T-1",
            empty_text="No historical position drift detected.",
            hint="tolerance 1e-6",
        )
    cols = [
        ("source", "Source", "text"),
        ("business_date", "Date", "text"),
        ("product", "Product", "text"),
        ("vintage", "Vintage", "text"),
        ("prior_position", "Prior", "num"),
        ("current_position", "Current", "num"),
        ("delta", "Δ", "num"),
    ]
    return render_exception_table(
        kicker="Hist. Position",
        title="Historical position drift · dates < T-1",
        rows=rows, columns=cols,
        max_rows=MAX_TABLE_ROWS_IN_EMAIL,
        subtotal_label="Hist. Position",
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
    rows = (
        render_header_band(data.business_date, data.run_id, data.prior_run_date)
        + render_hero(
            total_exceptions=sum(data.counts.values()),
            total_checks=len(data.counts),
            failed_checks=sum(1 for v in data.counts.values() if v > 0),
        )
        + render_check_strip(data.counts)
        + _section_position_break(data)
        + _section_trade_drift(data)
        + _section_historical_position(data)
        + _footer()
    )
    return _render_document(rows)
