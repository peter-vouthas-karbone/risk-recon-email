"""Parked full-report composers — all three checks. Activate when ready."""
from __future__ import annotations

from karbone_pnl_pos.reporting.html_builder import _render_document

from daily_recon.config import MAX_TABLE_ROWS_IN_EMAIL
from daily_recon.report.html_compose import (
    ReconReportData,
    _exceptions_of,
    _footer,
    _section_position_break,
)
from daily_recon.report.modules import (
    render_check_strip,
    render_empty_module,
    render_exception_table,
    render_header_band,
    render_hero,
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


def compose_html_full(data: ReconReportData) -> str:
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


def compose_subject_full(data: ReconReportData) -> str:
    total = sum(data.counts.values())
    if total == 0:
        return f"[RINs Recon] {data.business_date.isoformat()} — clean"
    failed = sum(1 for v in data.counts.values() if v > 0)
    noun = "check" if failed == 1 else "checks"
    return f"[RINs Recon] {data.business_date.isoformat()} — {total} exceptions across {failed} {noun}"
