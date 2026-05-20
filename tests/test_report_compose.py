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
        },
        exceptions=exceptions,
    )


def test_html_zero_exception_path():
    html = compose_html(_data([]))
    assert "<!DOCTYPE html>" in html
    assert "No MO vs FO position breaks detected" in html


def test_html_with_position_break_section():
    excs = [
        {"check_id": "position_break", "key": {}, "payload": {
            "business_date": "2026-05-19", "product": "D3 RIN", "vintage": "2024",
            "mo_position": 100, "fo_position": 90, "delta": 10,
        }},
    ]
    html = compose_html(_data(excs))
    assert "Position Break" in html
    assert "#0a2540" in html


def test_text_compose_contains_section_counts():
    excs = [{"check_id": "position_break", "key": {}, "payload": {
        "business_date": "2026-05-19", "product": "D3 RIN", "vintage": "2024",
        "mo_position": 100, "fo_position": 90, "delta": 10,
    }}]
    text = compose_text(_data(excs))
    assert "RINs Reconciliation" in text
    assert "2026-05-20" in text
    assert "Position breaks: 1" in text


def test_subject_helper():
    from daily_recon.report.html_compose import compose_subject
    assert compose_subject(_data([])) == "[RINs Recon] 2026-05-20 — clean"
    excs = [{"check_id": "position_break", "key": {}, "payload": {}}]
    assert compose_subject(_data(excs)) == "[RINs Recon] 2026-05-20 — 1 position break exception"
    excs = [{"check_id": "position_break", "key": {}, "payload": {}}] * 3
    assert compose_subject(_data(excs)) == "[RINs Recon] 2026-05-20 — 3 position break exceptions"
