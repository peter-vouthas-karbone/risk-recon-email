from datetime import date

from daily_recon.report.modules import (
    render_check_strip,
    render_empty_module,
    render_exception_table,
    render_header_band,
    render_hero,
)


def test_header_band_contains_kicker_title_and_run_meta():
    html = render_header_band(
        business_date=date(2026, 5, 20),
        run_id="2026-05-20T060000",
        prior_run_date=date(2026, 5, 19),
    )
    assert "Karbone Risk &middot; Operations" in html or "Karbone Risk · Operations" in html
    assert "RINs Reconciliation" in html
    assert "May 20" in html or "2026-05-20" in html
    assert "2026-05-20T060000" in html


def test_hero_zero_exceptions_is_green():
    html = render_hero(total_exceptions=0, total_checks=4, failed_checks=0)
    assert "#15803d" in html
    assert ">0<" in html


def test_hero_with_exceptions_is_red():
    html = render_hero(total_exceptions=7, total_checks=4, failed_checks=2)
    assert "#b91c1c" in html
    assert ">7<" in html
    assert "2 of 4" in html


def test_check_strip_renders_three_cells():
    counts = {
        "trade_drift": 2,
        "historical_position_drift": 0,
        "position_break": 3,
    }
    html = render_check_strip(counts)
    assert html.count("<td") >= 3
    assert "Trade Drift" in html
    assert "Position Break" in html
    assert "T-1 Trades" not in html
    # Zero-count cell renders the en-dash and uses subtle color.
    assert "#8a97a8" in html


def test_exception_table_truncates_at_max_rows_and_footnotes():
    rows = [{"col_a": f"r{i}", "col_b": i} for i in range(75)]
    cols = [("col_a", "Col A", "text"), ("col_b", "Col B", "num")]
    html = render_exception_table(
        kicker="Test", title="Test table", rows=rows, columns=cols,
        max_rows=50, subtotal_label="Test", subtotal_count=75,
    )
    # 50 data rows expected
    assert html.count("r0") == 1
    assert html.count("r49") == 1
    assert "r50" not in html
    assert "25 more rows" in html
    assert "2px solid #0a2540" in html  # subtotal cap rule


def test_empty_module_renders_centered_message():
    html = render_empty_module(
        kicker="Hist. Position", title="Historical position drift",
        empty_text="No historical position drift detected.",
    )
    assert "No historical position drift detected." in html
