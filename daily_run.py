#!/usr/bin/env python3
"""Daily reconciliation entrypoint.

Usage:
    python daily_run.py
    python daily_run.py --date 2026-05-20
    python daily_run.py --no-email
"""
from __future__ import annotations

import argparse
import logging
import sys
import traceback
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path

from daily_recon import config
from daily_recon.mailer import KeyringSMTPMailer, MailerCredentialError
from daily_recon.pipeline import run_pipeline


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Daily FO/MO RINs reconciliation")
    p.add_argument("--date", help="Business date YYYY-MM-DD; default = today")
    p.add_argument("--no-email", action="store_true", help="Skip email send (still writes outputs)")
    return p.parse_args(argv)


def _emergency_email(reason: str, tb: str) -> None:
    """Best-effort FAILED email; swallows any error from the mailer itself."""
    try:
        msg = EmailMessage()
        msg["From"] = config.EMAIL_SENDER
        msg["To"] = ", ".join(config.EMAIL_RECIPIENTS)
        msg["Subject"] = f"[RINs Recon] {date.today().isoformat()} — FAILED: {reason[:80]}"
        body = f"Reason: {reason}\n\nTraceback:\n{tb}"
        msg.set_content(body)
        KeyringSMTPMailer().send(msg)
    except Exception:
        logging.exception("Could not send emergency FAILED email")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parse_args(argv or sys.argv[1:])
    business_date = (
        datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()
    )

    try:
        result = run_pipeline(business_date=business_date, send_email=not args.no_email)
        print(f"Run {result.run_id}: {result.exception_count} exceptions -> {result.output_dir}")
        return 0
    except MailerCredentialError as e:
        logging.error("Keyring credential missing: %s", e)
        (config.OUTPUT_ROOT / "email_send_failed.txt").write_text(str(e), encoding="utf-8")
        return 4
    except Exception as e:
        tb = traceback.format_exc()
        logging.exception("Pipeline crashed")
        crash = config.OUTPUT_ROOT / f"crash-{datetime.now():%Y%m%dT%H%M%S}.log"
        crash.parent.mkdir(parents=True, exist_ok=True)
        crash.write_text(tb, encoding="utf-8")
        _emergency_email(str(e), tb)
        return 1


if __name__ == "__main__":
    sys.exit(main())
