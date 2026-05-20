"""End-to-end orchestrator for one daily run (position breaks only)."""
from __future__ import annotations

import csv
import json
import logging
import sys
import traceback
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Optional, Protocol

import duckdb
import pandas as pd

from daily_recon import config
from daily_recon.checks.position_equality import collect_position_equality_exceptions
from daily_recon.mailer import KeyringSMTPMailer
from daily_recon.persistence import (
    PosRunRecord,
    create_pos_schema,
    insert_exceptions,
    insert_run,
    insert_running_positions,
    latest_successful_run_id,
)
from daily_recon.positions import compute_running_position
from daily_recon.report.html_compose import (
    ReconReportData,
    compose_html,
    compose_subject,
)
from daily_recon.report.text_compose import compose_text

logger = logging.getLogger(__name__)


class Mailer(Protocol):
    def send(self, msg: EmailMessage) -> None: ...


@dataclass
class PipelineResult:
    run_id: str
    status: str           # 'success' | 'failed'
    exception_count: int
    counts: dict[str, int]
    output_dir: Path


def _new_run_id(now: datetime) -> str:
    return now.strftime("%Y-%m-%dT%H%M%S%f")


def _ensure_output_dir(business_date: date) -> Path:
    d = config.OUTPUT_ROOT / business_date.isoformat()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def _legs_to_positions(conn, run_id: str, business_date: date) -> pd.DataFrame:
    """Build pos_running_position rows for the current run from mo_legs + fuels_lines."""
    mo = conn.execute(
        """
        SELECT trade_date AS business_date, product_canonical, vintage_canonical, quantity
        FROM mo_legs WHERE run_id = ?
        """,
        [run_id],
    ).df()
    fo = conn.execute(
        """
        SELECT trade_date AS business_date, product_canonical, vintage_canonical, quantity
        FROM fuels_lines WHERE run_id = ?
        """,
        [run_id],
    ).df()
    mo_pos = compute_running_position(mo)
    fo_pos = compute_running_position(fo)
    mo_pos.insert(0, "source", "mo")
    fo_pos.insert(0, "source", "fo")
    return pd.concat([mo_pos, fo_pos], ignore_index=True)


def _build_email(subject: str, html: str, text: str, attachments: list[Path]) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = config.EMAIL_SENDER
    msg["To"] = ", ".join(config.EMAIL_RECIPIENTS)
    if config.EMAIL_CC:
        msg["Cc"] = ", ".join(config.EMAIL_CC)
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    for p in attachments:
        if p.exists():
            data = p.read_bytes()
            msg.add_attachment(data, maintype="text", subtype="csv", filename=p.name)
    return msg


def run_pipeline(
    *,
    business_date: Optional[date] = None,
    conn: Optional[duckdb.DuckDBPyConnection] = None,
    mailer: Optional[Mailer] = None,
    send_email: bool = True,
    incoming_runner: Optional[callable] = None,
) -> PipelineResult:
    """Execute one daily reconciliation.

    `incoming_runner` is the callable that performs karbone_recon's
    archive->ingest->stage->audit->reconcile sequence and returns the new run_id.
    Tests inject a fake; in production, pass `default_incoming_runner`.
    """
    started_at = datetime.now()
    business_date = business_date or started_at.date()
    run_id = _new_run_id(started_at)
    out_dir = _ensure_output_dir(business_date)

    owned_conn = conn is None
    if owned_conn:
        conn = duckdb.connect(str(config.DUCKDB_PATH))
    try:
        create_pos_schema(conn)

        if incoming_runner is None:
            incoming_runner = default_incoming_runner
        upstream_run_id = incoming_runner(conn, business_date)

        # Build running positions and persist them under our own run id.
        prior_run_id = latest_successful_run_id(conn, exclude=run_id)
        positions_df = _legs_to_positions(conn, upstream_run_id, business_date)

        insert_run(conn, PosRunRecord(
            run_id=run_id, business_date=business_date,
            started_at=started_at, finished_at=None, status="success",
        ))
        insert_running_positions(conn, run_id, positions_df)

        prior_business_date = (
            conn.execute(
                "SELECT business_date FROM pos_runs WHERE run_id = ?", [prior_run_id]
            ).fetchone()[0]
            if prior_run_id else None
        )

        # Run position-equality check for the last 7 days.
        date_from = max(
            config.DESYNC_CUTOFF_DATE,
            business_date - timedelta(days=config.POSITION_BREAK_LOOKBACK_DAYS - 1),
        )
        all_exceptions = collect_position_equality_exceptions(conn, run_id=run_id, date_from=date_from)

        insert_exceptions(conn, run_id, all_exceptions)

        counts = {"position_break": sum(1 for e in all_exceptions if e["check_id"] == "position_break")}

        # Write per-check CSVs.
        _write_csv(out_dir / "position_breaks.csv", [e["payload"] for e in all_exceptions if e["check_id"] == "position_break"])
        positions_df.to_csv(out_dir / "running_position.csv", index=False)
        (out_dir / "summary.json").write_text(json.dumps({
            "run_id": run_id,
            "business_date": business_date.isoformat(),
            "prior_run_id": prior_run_id,
            "counts": counts,
            "total_exceptions": sum(counts.values()),
        }, indent=2))

        # Compose and send the report.
        mo_leg_count = conn.execute(
            "SELECT COUNT(*) FROM mo_legs WHERE run_id = ?", [upstream_run_id]
        ).fetchone()[0]
        fo_line_count = conn.execute(
            "SELECT COUNT(*) FROM fuels_lines WHERE run_id = ?", [upstream_run_id]
        ).fetchone()[0]

        data = ReconReportData(
            business_date=business_date,
            run_id=run_id,
            prior_run_date=prior_business_date,
            mo_leg_count=mo_leg_count,
            fo_line_count=fo_line_count,
            counts=counts,
            exceptions=all_exceptions,
        )
        subject = compose_subject(data)
        html = compose_html(data)
        text = compose_text(data)
        (out_dir / "email.html").write_text(html, encoding="utf-8")
        (out_dir / "email.txt").write_text(text, encoding="utf-8")

        if send_email:
            attachments = [out_dir / "position_breaks.csv"]
            msg = _build_email(subject, html, text, attachments)
            (mailer or KeyringSMTPMailer()).send(msg)

        conn.execute(
            "UPDATE pos_runs SET finished_at = ?, status = 'success' WHERE run_id = ?",
            [datetime.now(), run_id],
        )

        return PipelineResult(
            run_id=run_id, status="success",
            exception_count=sum(counts.values()),
            counts=counts, output_dir=out_dir,
        )
    finally:
        if owned_conn:
            conn.close()


def default_incoming_runner(conn: duckdb.DuckDBPyConnection, business_date: date) -> str:
    """Run karbone_recon's archive->ingest->stage->audit->reconcile chain.

    Returns the upstream run_id corresponding to the staged data.
    """
    from karbone_recon.db import create_schema
    from karbone_recon.archive import archive_current_inputs, CANONICAL_MO, CANONICAL_FUELS
    from karbone_recon.ingest import load_mo, load_fuels
    from karbone_recon.stage import expand_mo_legs, stage_fuels
    from karbone_recon.audit import get_prior_run_id, detect_mo_drift, detect_fuels_drift
    from karbone_recon.reconcile import reconcile_cross_system
    from karbone_recon.mappings import (
        load_counterparty_map, load_vintage_rules, build_counterparty_lookup,
    )

    create_schema(conn)

    try:
        load_counterparty_map(conn)
        load_vintage_rules(conn)
    except FileNotFoundError:
        logger.warning("counterparty_mapping.csv not found — running with empty lookup")

    mo_path = config.MO_SOURCE_PATH
    fuels_path = config.FUELS_SOURCE_PATH
    if not mo_path.exists():
        raise FileNotFoundError(f"MO source not found: {mo_path}")
    if not fuels_path.exists():
        raise FileNotFoundError(f"Fuels source not found: {fuels_path}")

    archive_current_inputs(
        business_date,
        mo_path=config.MO_SOURCE_PATH,
        fuels_path=config.FUELS_SOURCE_PATH,
        force=True,
    )

    now = datetime.now()
    upstream_run_id = _new_run_id(now)

    mo_df = load_mo(mo_path, upstream_run_id, business_date, now, conn)
    fuels_df = load_fuels(fuels_path, upstream_run_id, business_date, now, conn)

    vendor_lookup = build_counterparty_lookup(conn, "mo_vendor")
    customer_lookup = build_counterparty_lookup(conn, "mo_customer")
    fuels_lookup = build_counterparty_lookup(conn, "fuels")

    expand_mo_legs(mo_df, upstream_run_id, vendor_lookup, customer_lookup, conn)
    stage_fuels(fuels_df, upstream_run_id, fuels_lookup, conn)

    conn.execute(
        "INSERT INTO runs (run_id, business_date, ingestion_timestamp, "
        "mo_source_file, fuels_source_file, mgmt_source_file) VALUES (?, ?, ?, ?, ?, NULL)",
        [upstream_run_id, business_date, now, mo_path.name, fuels_path.name],
    )

    prior_run_id = get_prior_run_id(conn, upstream_run_id)
    detect_mo_drift(conn, upstream_run_id, prior_run_id, business_date)
    detect_fuels_drift(conn, upstream_run_id, prior_run_id, business_date)
    reconcile_cross_system(conn, upstream_run_id)

    return upstream_run_id
