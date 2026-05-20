"""Full 3-check pipeline — parked for later activation."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Optional

import duckdb

from daily_recon import config
from daily_recon.checks.historical_position import (
    collect_historical_position_drift_exceptions,
)
from daily_recon.checks.position_equality import collect_position_equality_exceptions
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
from daily_recon.pipeline import (
    Mailer,
    PipelineResult,
    _build_email,
    _ensure_output_dir,
    _legs_to_positions,
    _new_run_id,
    _write_csv,
    default_incoming_runner,
)
from daily_recon.report.html_compose import (
    ReconReportData,
    compose_html,
    compose_subject,
)
from daily_recon.report.text_compose import compose_text

logger = logging.getLogger(__name__)


def run_pipeline_full(
    *,
    business_date: Optional[date] = None,
    conn: Optional[duckdb.DuckDBPyConnection] = None,
    mailer: Optional[Mailer] = None,
    send_email: bool = True,
    incoming_runner: Optional[callable] = None,
) -> PipelineResult:
    """Execute one daily reconciliation with all three checks.

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

        # Run the three checks.
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

        insert_exceptions(conn, run_id, all_exceptions)

        counts = {
            "trade_drift": 0,
            "historical_position_drift": 0,
            "position_break": 0,
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
