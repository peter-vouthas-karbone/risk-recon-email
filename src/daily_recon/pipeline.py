"""End-to-end orchestrator for one daily run."""
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
from daily_recon.checks.historical_position import (
    collect_historical_position_drift_exceptions,
)
from daily_recon.checks.position_equality import collect_position_equality_exceptions
from daily_recon.checks.prior_day_trades import collect_prior_day_trade_exceptions
from daily_recon.checks.trade_drift import collect_trade_drift_exceptions
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
    return now.strftime("%Y-%m-%dT%H%M%S")


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

        # Run the four checks.
        all_exceptions: list[dict] = []
        all_exceptions.extend(collect_trade_drift_exceptions(conn, upstream_run_id))
        prior_business_date = (
            conn.execute(
                "SELECT business_date FROM pos_runs WHERE run_id = ?", [prior_run_id]
            ).fetchone()[0]
            if prior_run_id else None
        )
        all_exceptions.extend(collect_historical_position_drift_exceptions(
            conn, current_run_id=run_id, prior_run_id=prior_run_id,
            business_date=business_date,
        ))
        all_exceptions.extend(collect_position_equality_exceptions(conn, run_id=run_id))
        all_exceptions.extend(collect_prior_day_trade_exceptions(
            conn, run_id=upstream_run_id, business_date=business_date,
        ))

        insert_exceptions(conn, run_id, all_exceptions)

        counts = {
            "trade_drift": 0,
            "historical_position_drift": 0,
            "position_break": 0,
            "prior_day_trades": 0,
        }
        for e in all_exceptions:
            counts[e["check_id"]] = counts.get(e["check_id"], 0) + 1

        # Write per-check CSVs.
        by_check: dict[str, list[dict]] = {k: [] for k in counts}
        for e in all_exceptions:
            by_check[e["check_id"]].append(e["payload"])
        _write_csv(out_dir / "trade_drift.csv", by_check["trade_drift"])
        _write_csv(out_dir / "historical_position_drift.csv", by_check["historical_position_drift"])
        _write_csv(out_dir / "position_breaks.csv", by_check["position_break"])
        _write_csv(out_dir / "prior_day_trade_breaks.csv", by_check["prior_day_trades"])
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
            attachments = [
                out_dir / "trade_drift.csv",
                out_dir / "historical_position_drift.csv",
                out_dir / "position_breaks.csv",
                out_dir / "prior_day_trade_breaks.csv",
            ]
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
    raise NotImplementedError(
        "default_incoming_runner is a stub. The real wiring to karbone_recon's "
        "archive/ingest/stage/audit/reconcile functions will be done once the actual "
        "CSV ingestion paths are confirmed. Tests inject a fake incoming_runner."
    )
