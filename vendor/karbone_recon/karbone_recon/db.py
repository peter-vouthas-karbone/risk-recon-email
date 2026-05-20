"""
DuckDB connection helper and schema creation.
All tables use CREATE TABLE IF NOT EXISTS so this is safe to call on every run.
"""

import duckdb
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "recon.duckdb"


def get_connection() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(DB_PATH))


def create_schema(conn: duckdb.DuckDBPyConnection) -> None:
    conn.executemany("PRAGMA", []) if False else None  # no-op, just for symmetry
    conn.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            run_id            TEXT PRIMARY KEY,
            business_date     DATE,
            ingestion_timestamp TIMESTAMP,
            mo_source_file    TEXT,
            fuels_source_file TEXT,
            mgmt_source_file  TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS raw_mo_snapshots (
            run_id              TEXT,
            business_date       DATE,
            ingestion_timestamp TIMESTAMP,
            source_file_name    TEXT,
            source_row_number   INTEGER,
            raw_row_json        TEXT,
            row_hash            TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS raw_fuels_snapshots (
            run_id              TEXT,
            business_date       DATE,
            ingestion_timestamp TIMESTAMP,
            source_file_name    TEXT,
            source_row_number   INTEGER,
            raw_row_json        TEXT,
            row_hash            TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS mo_rows (
            run_id              TEXT,
            mo_row_id           TEXT,
            mo_trade_group_id   TEXT,
            source_row_number   INTEGER,
            product_raw         TEXT,
            vintage_raw         TEXT,
            platform            TEXT,
            notes               TEXT,
            row_hash            TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS mo_legs (
            run_id                  TEXT,
            mo_leg_id               TEXT,
            mo_row_id               TEXT,
            mo_trade_group_id       TEXT,
            leg_type                TEXT,   -- seller_leg | buyer_leg
            side                    TEXT,   -- Buy | Sell
            trade_date              DATE,
            counterparty_raw        TEXT,
            counterparty_canonical  TEXT,
            product_raw             TEXT,
            product_canonical       TEXT,
            vintage_raw             TEXT,
            vintage_canonical       TEXT,
            quantity                DOUBLE,
            price                   DOUBLE,
            delivery_raw            TEXT,
            delivery_match_date     DATE,
            row_hash                TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS fuels_lines (
            run_id                  TEXT,
            fuels_line_id           TEXT,
            trade_number            TEXT,
            line_id                 TEXT,
            trade_date              DATE,
            counterparty_raw        TEXT,
            counterparty_canonical  TEXT,
            side                    TEXT,
            product_raw             TEXT,
            product_canonical       TEXT,
            vintage_raw             TEXT,
            vintage_canonical       TEXT,
            quantity                DOUBLE,
            price                   DOUBLE,
            delivery_date           DATE,
            delivery_close          DATE,
            delivery_match_date     DATE,
            open_closed             TEXT,
            status                  TEXT,
            row_hash                TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS mo_trade_drift (
            run_id                  TEXT,
            prior_run_id            TEXT,
            change_type             TEXT,   -- new_trade | added_trade | removed_trade | modified_trade
            trade_date              DATE,
            side                    TEXT,
            counterparty_canonical  TEXT,
            product_canonical       TEXT,
            vintage_canonical       TEXT,
            prior_volume            DOUBLE,
            current_volume          DOUBLE,
            volume_delta            DOUBLE,
            prior_wap               DOUBLE,
            current_wap             DOUBLE,
            wap_delta               DOUBLE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS fuels_trade_drift (
            run_id                  TEXT,
            prior_run_id            TEXT,
            change_type             TEXT,   -- new_trade | added_trade | removed_trade | modified_trade
            trade_date              DATE,
            side                    TEXT,
            counterparty_canonical  TEXT,
            product_canonical       TEXT,
            vintage_canonical       TEXT,
            prior_volume            DOUBLE,
            current_volume          DOUBLE,
            volume_delta            DOUBLE,
            prior_wap               DOUBLE,
            current_wap             DOUBLE,
            wap_delta               DOUBLE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS counterparty_map (
            source_system       TEXT,
            raw_name            TEXT,
            canonical_name      TEXT,
            approval_status     TEXT,   -- approved | pending
            effective_date      DATE,
            UNIQUE(source_system, raw_name)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS product_map (
            raw_product         TEXT PRIMARY KEY,
            canonical_product   TEXT,
            approval_status     TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS vintage_rule_map (
            canonical_product   TEXT PRIMARY KEY,
            use_vintage_flag    BOOLEAN
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS raw_mgmt_snapshots (
            run_id              TEXT,
            business_date       DATE,
            ingestion_timestamp TIMESTAMP,
            source_file_name    TEXT,
            source_row_number   INTEGER,
            raw_row_json        TEXT,
            row_hash            TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS mgmt_rows (
            run_id              TEXT,
            mgmt_row_id         TEXT,
            mgmt_trade_group_id TEXT,
            source_row_number   INTEGER,
            product_raw         TEXT,
            vintage_raw         TEXT,
            platform            TEXT,
            notes               TEXT,
            row_hash            TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS mgmt_legs (
            run_id                  TEXT,
            mgmt_leg_id             TEXT,
            mgmt_row_id             TEXT,
            mgmt_trade_group_id     TEXT,
            leg_type                TEXT,
            side                    TEXT,
            trade_date              DATE,
            counterparty_raw        TEXT,
            counterparty_canonical  TEXT,
            product_raw             TEXT,
            product_canonical       TEXT,
            vintage_raw             TEXT,
            vintage_canonical       TEXT,
            quantity                DOUBLE,
            price                   DOUBLE,
            delivery_raw            TEXT,
            delivery_match_date     DATE,
            row_hash                TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS mgmt_trade_drift (
            run_id                  TEXT,
            prior_run_id            TEXT,
            change_type             TEXT,
            trade_date              DATE,
            side                    TEXT,
            counterparty_canonical  TEXT,
            product_canonical       TEXT,
            vintage_canonical       TEXT,
            prior_volume            DOUBLE,
            current_volume          DOUBLE,
            volume_delta            DOUBLE,
            prior_wap               DOUBLE,
            current_wap             DOUBLE,
            wap_delta               DOUBLE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS mgmt_cross_recon (
            run_id                  TEXT,
            recon_status            TEXT,
            trade_date              DATE,
            side                    TEXT,
            counterparty_canonical  TEXT,
            product_canonical       TEXT,
            vintage_canonical       TEXT,
            mgmt_volume             DOUBLE,
            fuels_volume            DOUBLE,
            volume_delta            DOUBLE,
            mgmt_wap                DOUBLE,
            fuels_wap               DOUBLE,
            wap_delta               DOUBLE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS book_analysis (
            run_id                  TEXT,
            book_flag               TEXT,
            ice_mgmt_flag           BOOLEAN,
            trade_date              DATE,
            side                    TEXT,
            counterparty_canonical  TEXT,
            product_canonical       TEXT,
            vintage_canonical       TEXT,
            mo_volume               DOUBLE,
            mo_wap                  DOUBLE,
            mgmt_volume             DOUBLE,
            mgmt_wap                DOUBLE,
            fuels_volume            DOUBLE,
            fuels_wap               DOUBLE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS cross_recon (
            run_id                  TEXT,
            recon_status            TEXT,
            trade_date              DATE,
            side                    TEXT,
            counterparty_canonical  TEXT,
            product_canonical       TEXT,
            vintage_canonical       TEXT,
            mo_volume               DOUBLE,
            fuels_volume            DOUBLE,
            volume_delta            DOUBLE,
            mo_wap                  DOUBLE,
            fuels_wap               DOUBLE,
            wap_delta               DOUBLE
        )
    """)
