from datetime import date

from daily_recon.report.html_compose import ReconReportData, compose_html
from daily_recon.report.text_compose import compose_text


def _data(exceptions):
    return ReconReportData(
        business_date=date(2026, 5, 20),
        run_id="2026-05-20T060000",
        prior_run_date=date(2026, 5, 19),
        mo_leg_count=1284,
        fo_line_count=906,
        counts={
            "trade_drift": sum(1 for e in exceptions if e["check_id"] == "trade_drift"),
            "historical_position_drift": sum(1 for e in exceptions if e["check_id"] == "historical_position_drift"),
            "position_break": sum(1 for e in exceptions if e["check_id"] == "position_break"),
            "prior_day_trades": sum(1 for e in exceptions if e["check_id"] == "prior_day_trades"),
        },
        exceptions=exceptions,
    )


def test_html_zero_exception_path():
    html = compose_html(_data([]))
    assert "<!DOCTYPE html>" in html
    assert "All checks clean" in html


def test_html_with_each_check_section():
    excs = [
        {"check_id": "position_break", "key": {}, "payload": {
            "business_date": "2026-05-19", "product": "D3 RIN", "vintage": "2024",
            "mo_position": 100, "fo_position": 90, "delta": 10,
        }},
        {"check_id": "prior_day_trades", "key": {}, "payload": {
            "status": "price_break", "counterparty": "Mercuria", "side": "Sell",
            "product": "D3 RIN", "vintage": "2024",
            "mo_volume": 100, "fo_volume": 100, "mo_wap": 2.5, "fo_wap": 2.49,
        }},
        {"check_id": "trade_drift", "key": {}, "payload": {
            "source": "mo", "change_type": "modified_trade",
            "counterparty": "Air Liquide", "product": "D3 RIN", "vintage": "2024",
            "prior_volume": 100, "current_volume": 100, "prior_wap": 2.44, "current_wap": 2.45,
        }},
    ]
    html = compose_html(_data(excs))
    # Section kickers appear
    assert "Position Break" in html
    assert "T-1 Trades" in html
    assert "Trade Drift" in html
    # Empty check renders empty state
    assert "No historical position drift" in html
    # Theme tokens present
    assert "#0a2540" in html


def test_text_compose_contains_section_counts():
    excs = [{"check_id": "position_break", "key": {}, "payload": {
        "business_date": "2026-05-19", "product": "D3 RIN", "vintage": "2024",
        "mo_position": 100, "fo_position": 90, "delta": 10,
    }}]
    text = compose_text(_data(excs))
    assert "RINs Reconciliation" in text
    assert "2026-05-20" in text
    assert "Position Break: 1" in text
    assert "Trade Drift: 0" in text


def test_subject_helper():
    from daily_recon.report.html_compose import compose_subject
    assert compose_subject(_data([])) == "[RINs Recon] 2026-05-20 — clean"
    excs = [{"check_id": "position_break", "key": {}, "payload": {}}] * 3
    assert compose_subject(_data(excs)) == "[RINs Recon] 2026-05-20 — 3 exceptions across 1 check"
    excs = (
        [{"check_id": "position_break", "key": {}, "payload": {}}] * 2
        + [{"check_id": "trade_drift", "key": {}, "payload": {}}]
    )
    assert compose_subject(_data(excs)) == "[RINs Recon] 2026-05-20 — 3 exceptions across 2 checks"
