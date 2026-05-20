# tests/test_pipeline_integration.py
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path

import duckdb
import pytest

from daily_recon import config
from daily_recon.pipeline import run_pipeline


class RecordingMailer:
    def __init__(self):
        self.sent: list[EmailMessage] = []

    def send(self, msg: EmailMessage) -> None:
        self.sent.append(msg)


def _create_upstream_tables(conn):
    conn.execute("""
        CREATE TABLE mo_legs (
            run_id TEXT, trade_date DATE, side TEXT, counterparty_canonical TEXT,
            product_canonical TEXT, vintage_canonical TEXT,
            quantity DOUBLE, price DOUBLE, delivery_match_date DATE
        )
    """)
    conn.execute("""
        CREATE TABLE fuels_lines (
            run_id TEXT, trade_date DATE, side TEXT, counterparty_canonical TEXT,
            product_canonical TEXT, vintage_canonical TEXT,
            quantity DOUBLE, price DOUBLE, delivery_match_date DATE
        )
    """)
    conn.execute("""
        CREATE TABLE mo_trade_drift (
            run_id TEXT, trade_date DATE, side TEXT, counterparty_canonical TEXT,
            product_canonical TEXT, vintage_canonical TEXT,
            change_type TEXT, prior_volume DOUBLE, current_volume DOUBLE,
            prior_wap DOUBLE, current_wap DOUBLE
        )
    """)
    conn.execute("""
        CREATE TABLE fuels_trade_drift (
            run_id TEXT, trade_date DATE, side TEXT, counterparty_canonical TEXT,
            product_canonical TEXT, vintage_canonical TEXT,
            change_type TEXT, prior_volume DOUBLE, current_volume DOUBLE,
            prior_wap DOUBLE, current_wap DOUBLE
        )
    """)
    conn.execute("""
        CREATE TABLE cross_recon (
            run_id TEXT, recon_status TEXT, trade_date DATE, side TEXT,
            counterparty_canonical TEXT, product_canonical TEXT,
            vintage_canonical TEXT,
            mo_volume DOUBLE, fuels_volume DOUBLE,
            mo_wap DOUBLE, fuels_wap DOUBLE
        )
    """)


def _seed_day1(conn, run_id):
    conn.execute(
        "INSERT INTO mo_legs VALUES (?, ?, 'Buy', 'Air Liquide', 'D3 RIN', '2024', 100, 2.5, '2026-05-25')",
        [run_id, date(2026, 5, 18)],
    )
    conn.execute(
        "INSERT INTO fuels_lines VALUES (?, ?, 'Buy', 'Air Liquide', 'D3 RIN', '2024', 100, 2.5, '2026-05-25')",
        [run_id, date(2026, 5, 18)],
    )
    conn.execute(
        "INSERT INTO cross_recon VALUES (?, 'matched', ?, 'Buy', 'Air Liquide', 'D3 RIN', '2024', 100, 100, 2.5, 2.5)",
        [run_id, date(2026, 5, 18)],
    )


def _seed_day2(conn, run_id):
    # MO: backdated change on 2026-05-18 (100→120) on vintage 2024.
    # FO: unchanged 2026-05-18 (still 100) — the backdated MO edit was not mirrored.
    # New T-1 sell on 2026-05-19 uses vintage 2025 so it forms its own accumulation
    # bucket; both MO and FO carry -50 volume, so the running position for vintage 2025
    # is equal (-50 == -50) and produces no position_break on that date.
    # The only position_break is vintage 2024 on 2026-05-18 (MO 120 vs FO 100).
    conn.execute(
        "INSERT INTO mo_legs VALUES (?, ?, 'Buy', 'Air Liquide', 'D3 RIN', '2024', 120, 2.5, '2026-05-25')",
        [run_id, date(2026, 5, 18)],
    )
    conn.execute(
        "INSERT INTO mo_legs VALUES (?, ?, 'Sell', 'Mercuria', 'D3 RIN', '2025', -50, 2.6, '2026-05-25')",
        [run_id, date(2026, 5, 19)],
    )
    conn.execute(
        "INSERT INTO fuels_lines VALUES (?, ?, 'Buy', 'Air Liquide', 'D3 RIN', '2024', 100, 2.5, '2026-05-25')",
        [run_id, date(2026, 5, 18)],
    )
    conn.execute(
        "INSERT INTO fuels_lines VALUES (?, ?, 'Sell', 'Mercuria', 'D3 RIN', '2025', -50, 2.55, '2026-05-25')",
        [run_id, date(2026, 5, 19)],
    )
    conn.execute(
        "INSERT INTO cross_recon VALUES (?, 'matched', ?, 'Buy', 'Air Liquide', 'D3 RIN', '2024', 120, 100, 2.5, 2.5)",
        [run_id, date(2026, 5, 18)],
    )
    conn.execute(
        "INSERT INTO cross_recon VALUES (?, 'price_break', ?, 'Sell', 'Mercuria', 'D3 RIN', '2025', -50, -50, 2.6, 2.55)",
        [run_id, date(2026, 5, 19)],
    )
    conn.execute(
        "INSERT INTO mo_trade_drift VALUES (?, ?, 'Buy', 'Air Liquide', 'D3 RIN', '2024', 'modified_trade', 100, 120, 2.5, 2.5)",
        [run_id, date(2026, 5, 18)],
    )


@pytest.fixture
def isolated_paths(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_ROOT", tmp_path / "output")
    monkeypatch.setattr(config, "DUCKDB_PATH", tmp_path / "recon.duckdb")
    config.OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_full_pipeline_day1_then_day2(isolated_paths):
    db_path = config.DUCKDB_PATH

    # ── Day 1 ──
    conn1 = duckdb.connect(str(db_path))
    _create_upstream_tables(conn1)

    def runner_day1(conn, business_date):
        rid = "U-day1"
        _seed_day1(conn, rid)
        return rid

    mailer1 = RecordingMailer()
    result1 = run_pipeline(
        business_date=date(2026, 5, 19),
        conn=conn1, mailer=mailer1, send_email=True,
        incoming_runner=runner_day1,
    )
    conn1.close()
    assert result1.status == "success"
    assert result1.exception_count == 0
    assert len(mailer1.sent) == 1
    assert "clean" in mailer1.sent[0]["Subject"]

    # ── Day 2 ──
    conn2 = duckdb.connect(str(db_path))

    def runner_day2(conn, business_date):
        rid = "U-day2"
        _seed_day2(conn, rid)
        return rid

    mailer2 = RecordingMailer()
    result2 = run_pipeline(
        business_date=date(2026, 5, 20),
        conn=conn2, mailer=mailer2, send_email=True,
        incoming_runner=runner_day2,
    )
    conn2.close()

    assert result2.status == "success"
    # The backdated MO modification on 2026-05-18 (MO 120 vs FO 100) creates
    # a position break — the only check that runs in the stripped pipeline.
    assert result2.counts["position_break"] == 1
    assert "trade_drift" not in result2.counts
    assert "historical_position_drift" not in result2.counts
    assert "prior_day_trades" not in result2.counts
    assert result2.exception_count == 1

    msg = mailer2.sent[0]
    assert "1 position break exception" in msg["Subject"]
    # MIME structure: multipart/mixed → [multipart/alternative, csv]
    # The alternative part contains [text/plain, text/html].
    alternative_part = msg.get_payload()[0]
    html = alternative_part.get_payload()[1].get_content()
    # The position_break row shows the D3 RIN product and 2024 vintage.
    assert "D3 RIN" in html
    assert "2024" in html

    out = result2.output_dir
    assert (out / "summary.json").exists()
    assert (out / "position_breaks.csv").read_text().count("\n") >= 1
    assert not (out / "trade_drift.csv").exists()
    assert not (out / "historical_position_drift.csv").exists()
