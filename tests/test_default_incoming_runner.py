import shutil
from datetime import date

import duckdb
import pytest

import daily_recon.config as config
from daily_recon.pipeline import default_incoming_runner
from daily_recon.persistence import create_pos_schema


MO_HEADER = "Trade ID,Date,Seller,Volume,Price,Shipment In,Date.1,Buyer,Volume.1,Price.1,Shipment out,Product,Vintage,Platform,Notes,Position Type\n"
MO_ROW    = "T001,05/20/2026,Acme Corp,1000.0,$2.50,05/31/2026,05/20/2026,Beta LLC,-1000.0,$2.50,05/31/2026,D6 RIN,2026,EMTS,,\n"

FUELS_HEADER = "Trade Number,Line ID,Open/Closed,Status,Trade Date,Buy/Sell,Product,Vintage,Volume,Price/RIN,Delivery Date,Delivery Close\n"
FUELS_ROW    = "F001,F001-1,Closed,Active,2026-05-20,Sell,D6 RIN,2026,-1000.0,2.50,2026-05-31,2026-05-31\n"


@pytest.fixture
def src_csvs(tmp_path):
    """Create minimal MO + Fuels CSV files and return their paths."""
    mo = tmp_path / "rins_tradesheet_no_rng.csv"
    mo.write_text(MO_HEADER + MO_ROW)
    fuels = tmp_path / "Fuels_Tradesheet.csv"
    fuels.write_text(FUELS_HEADER + FUELS_ROW)
    return mo, fuels


@pytest.fixture
def fresh_conn():
    conn = duckdb.connect()
    from karbone_recon.db import create_schema
    create_schema(conn)
    create_pos_schema(conn)
    yield conn
    conn.close()


def test_raises_when_mo_source_missing(tmp_path, fresh_conn, monkeypatch):
    monkeypatch.setattr(config, "MO_SOURCE_PATH", tmp_path / "does_not_exist.csv")
    monkeypatch.setattr(config, "FUELS_SOURCE_PATH", tmp_path / "also_missing.csv")
    with pytest.raises(FileNotFoundError, match="MO source not found"):
        default_incoming_runner(fresh_conn, date(2026, 5, 20))


def test_populates_mo_legs(src_csvs, fresh_conn, monkeypatch):
    mo_path, fuels_path = src_csvs
    monkeypatch.setattr(config, "MO_SOURCE_PATH", mo_path)
    monkeypatch.setattr(config, "FUELS_SOURCE_PATH", fuels_path)

    rid = default_incoming_runner(fresh_conn, date(2026, 5, 20))

    count = fresh_conn.execute(
        "SELECT COUNT(*) FROM mo_legs WHERE run_id = ?", [rid]
    ).fetchone()[0]
    assert count > 0


def test_populates_fuels_lines(src_csvs, fresh_conn, monkeypatch):
    mo_path, fuels_path = src_csvs
    monkeypatch.setattr(config, "MO_SOURCE_PATH", mo_path)
    monkeypatch.setattr(config, "FUELS_SOURCE_PATH", fuels_path)

    rid = default_incoming_runner(fresh_conn, date(2026, 5, 20))

    count = fresh_conn.execute(
        "SELECT COUNT(*) FROM fuels_lines WHERE run_id = ?", [rid]
    ).fetchone()[0]
    assert count > 0


def test_populates_cross_recon(src_csvs, fresh_conn, monkeypatch):
    mo_path, fuels_path = src_csvs
    monkeypatch.setattr(config, "MO_SOURCE_PATH", mo_path)
    monkeypatch.setattr(config, "FUELS_SOURCE_PATH", fuels_path)

    rid = default_incoming_runner(fresh_conn, date(2026, 5, 20))

    count = fresh_conn.execute(
        "SELECT COUNT(*) FROM cross_recon WHERE run_id = ?", [rid]
    ).fetchone()[0]
    assert count > 0


def test_no_drift_on_first_run(src_csvs, fresh_conn, monkeypatch):
    """With no prior run, drift tables must be empty."""
    mo_path, fuels_path = src_csvs
    monkeypatch.setattr(config, "MO_SOURCE_PATH", mo_path)
    monkeypatch.setattr(config, "FUELS_SOURCE_PATH", fuels_path)

    rid = default_incoming_runner(fresh_conn, date(2026, 5, 20))

    mo_drift = fresh_conn.execute(
        "SELECT COUNT(*) FROM mo_trade_drift WHERE run_id = ?", [rid]
    ).fetchone()[0]
    fuels_drift = fresh_conn.execute(
        "SELECT COUNT(*) FROM fuels_trade_drift WHERE run_id = ?", [rid]
    ).fetchone()[0]
    assert mo_drift == 0
    assert fuels_drift == 0
