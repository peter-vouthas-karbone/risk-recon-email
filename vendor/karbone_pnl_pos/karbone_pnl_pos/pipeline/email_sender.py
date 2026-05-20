#!/usr/bin/env python3
"""
Email Sender for PnL Pipeline.

This module handles sending email notifications for PnL reports.
It supports both summary reports and position (volumetric exposure) reports.

The sender follows the Single Responsibility Principle by focusing solely
on email delivery, separate from report generation and workflow orchestration.

Security Note:
    Email credentials should be provided via environment variables
    (EMAIL_USERNAME, EMAIL_PASSWORD) rather than configuration files.

Usage:
    >>> from pipeline.email_sender import EmailSender
    >>> sender = EmailSender()
    >>> if sender.is_enabled():
    ...     sender.send_summary_report(
    ...         html_report_path='/path/to/report.html',
    ...         text_report_path='/path/to/report.txt',
    ...         most_recent_date='2024-01-15',
    ...         total_pnl=50000.0
    ...     )
"""

import logging
import os
import smtplib
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from karbone_pnl_pos.utils.config_loader import get_email_config

# Module logger
logger = logging.getLogger('pnl.' + __name__)


class EmailSender:
    """
    Sends email notifications for PnL reports.

    This class handles:
    - SMTP connection and authentication
    - HTML email formatting
    - Sending to multiple recipients
    - Error handling and logging

    Attributes:
        config: Email configuration dictionary.
    """

    def __init__(self, email_config: Optional[Dict[str, Any]] = None) -> None:
        """
        Initialize email sender with configuration.

        Args:
            email_config: Email configuration dict. If None, loads from config.
        """
        self.config: Dict[str, Any] = email_config or get_email_config()

    def is_enabled(self) -> bool:
        """
        Check if email notifications are enabled.

        Returns:
            True if email notifications are enabled in configuration.
        """
        return self.config.get('enabled', False)

    def send_summary_report(
        self,
        html_report_path: str,
        text_report_path: str,
        most_recent_date: str,
        total_pnl: float = 0.0,
    ) -> bool:
        """
        Send the summary report via email.

        Args:
            html_report_path: Path to HTML report file.
            text_report_path: Path to text report file (fallback).
            most_recent_date: Most recent trade date for subject line.
            total_pnl: Total PnL value for email body.

        Returns:
            True if email sent successfully, False otherwise.
        """
        if not self.is_enabled():
            logger.info("Email notifications are disabled in configuration")
            return True

        logger.info("Sending summary email notification...")

        summary_config = self.config.get('summary_report', self.config)
        if not summary_config.get('enabled', True):
            logger.info("Summary email disabled in configuration")
            return True

        to_addresses = self._normalize_recipients(summary_config.get('to_addresses', []))
        if not to_addresses:
            to_addresses = self._normalize_recipients(summary_config.get('to_address', ''))

        cc_addresses = self._normalize_recipients(summary_config.get('cc_addresses', []))
        if not cc_addresses:
            cc_addresses = self._normalize_recipients(summary_config.get('cc_address', ''))

        if not self._validate_credentials() or not to_addresses:
            logger.error("Email configuration incomplete")
            return False

        subject_template = summary_config.get(
            'subject',
            'Karbone Risk Daily PnL Summary Report - {date}'
        )
        subject = subject_template.format(date=most_recent_date)

        # Load HTML body
        html_body = self._load_report_content(
            html_report_path,
            text_report_path,
            most_recent_date,
            total_pnl
        )

        success = self._send_email(to_addresses, subject, html_body, cc_addresses=cc_addresses)
        if success:
            recipient_count = len(to_addresses)
            cc_count = len(cc_addresses)
            if cc_count > 0:
                logger.info(f"Email sent successfully to {recipient_count} recipient(s) and {cc_count} CC recipient(s)")
            else:
                logger.info(f"Email sent successfully to {recipient_count} recipient(s)")
        return success

    def send_position_report(
        self,
        html_report_path: str,
        most_recent_date: str,
        intro_text: str = "",
    ) -> bool:
        """
        Send the position (volumetric exposure) report via email.

        Args:
            html_report_path: Path to HTML position report.
            most_recent_date: Most recent trade date for subject line.
            intro_text: Intro text for the report.

        Returns:
            True if email sent successfully, False otherwise.
        """
        if not self.is_enabled():
            logger.info("Email notifications are disabled in configuration")
            return True

        position_config = self.config.get('position_report', {})
        if not position_config.get('enabled', False):
            logger.info("Position report email disabled in configuration")
            return True

        to_addresses = self._normalize_recipients(position_config.get('to_addresses', []))
        if not to_addresses:
            to_addresses = self._normalize_recipients(position_config.get('to_address', ''))

        cc_addresses = self._normalize_recipients(position_config.get('cc_addresses', []))
        if not cc_addresses:
            cc_addresses = self._normalize_recipients(position_config.get('cc_address', ''))

        if not to_addresses:
            logger.warning("Position report email enabled but no recipients configured")
            return True

        subject_template = position_config.get(
            'subject',
            'Karbone Volumetric Exposure Report - {date}'
        )
        subject = subject_template.format(date=most_recent_date)

        # Load HTML body
        html_body = ""
        if os.path.exists(html_report_path):
            try:
                with open(html_report_path, 'r', encoding='utf-8') as f:
                    html_body = f.read()
            except OSError as e:
                logger.warning(f"Could not read position report: {e}")

        if not html_body:
            html_body = f"<p>{intro_text}</p>"

        success = self._send_email(to_addresses, subject, html_body, cc_addresses=cc_addresses)
        if success:
            recipient_list = ', '.join(to_addresses)
            if cc_addresses:
                recipient_list += f" (CC: {', '.join(cc_addresses)})"
            logger.info(f"Position email sent to {recipient_list}")
        return success

    # ------------------------------------------------------------------ #
    # Multi-report helpers
    # ------------------------------------------------------------------ #

    def send_html_file(
        self,
        to_addresses: List[str],
        subject: str,
        html_report_path: str,
        fallback_body: str = "",
        cc_addresses: Optional[List[str]] = None
    ) -> bool:
        """
        Send a single HTML file to specified recipients.

        Args:
            to_addresses: List of email recipients.
            subject: Email subject.
            html_report_path: Path to HTML report file.
            fallback_body: Optional fallback HTML body if file missing/unreadable.
            cc_addresses: Optional list of CC email recipients.
        """
        if not self._validate_credentials():
            logger.error("Email credentials are not configured")
            return False

        if not to_addresses:
            logger.error("No recipients provided for report email")
            return False

        html_body = fallback_body
        if os.path.exists(html_report_path):
            try:
                with open(html_report_path, 'r', encoding='utf-8') as f:
                    html_body = f.read()
            except OSError as e:
                logger.warning(f"Could not read report file {html_report_path}: {e}")
        else:
            logger.warning(f"Report file not found: {html_report_path}")

        if not html_body:
            logger.error("No email body available to send")
            return False

        # Locate per-report sparkline images directory: <html_stem>_images/
        # next to the HTML file. core.reporting writes one such subdirectory
        # per generated report so different desk reports don't share or
        # overwrite each other's PNGs.
        images_dir = None
        if html_report_path and os.path.exists(html_report_path):
            html_path_obj = Path(html_report_path)
            candidate = html_path_obj.parent / f'{html_path_obj.stem}_images'
            if candidate.is_dir():
                images_dir = candidate

        return self._send_email(
            to_addresses=to_addresses,
            subject=subject,
            html_body=html_body,
            cc_addresses=cc_addresses or [],
            images_dir=images_dir,
        )

    def send_reports_from_settings(
        self,
        pnl_reports: List[Dict[str, Any]],
        position_reports: List[Dict[str, Any]],
        generated: Dict[str, List[Dict[str, Any]]],
        report_date: str
    ) -> bool:
        """
        Send all enabled PnL and position reports based on settings.yaml structure.

        Args:
            pnl_reports: Config list under email.pnl_reports.
            position_reports: Config list under email.position_reports.
            generated: Dict returned by reporter.generate_reports_from_settings.
            report_date: Date string for subject formatting.
        """
        if not self.is_enabled():
            logger.info("Email notifications are disabled in configuration")
            return True

        all_good = True

        # Send PnL reports
        for cfg, gen in zip(pnl_reports, generated.get('pnl_reports', [])):
            if not cfg.get('enabled'):
                continue
            if not gen.get('generated'):
                logger.warning(
                    "PnL report '%s' not generated; skipping email",
                    cfg.get('name', 'unnamed')
                )
                all_good = False
                continue

            to_addresses = self._normalize_recipients(cfg.get('to_addresses', []))
            cc_addresses = self._normalize_recipients(cfg.get('cc_addresses', []))
            subject_tpl = cfg.get('subject', 'Karbone Daily PnL Summary Report - {date}')
            subject = subject_tpl.format(date=report_date)
            html_path = gen.get('html_report_path', '')

            success = self.send_html_file(
                to_addresses=to_addresses,
                subject=subject,
                html_report_path=html_path,
                cc_addresses=cc_addresses if cc_addresses else None
            )
            all_good = all_good and success

        # Send Position reports
        for cfg, gen in zip(position_reports, generated.get('position_reports', [])):
            if not cfg.get('enabled'):
                continue
            if not gen.get('generated'):
                logger.warning(
                    "Position report '%s' not generated; skipping email",
                    cfg.get('name', 'unnamed')
                )
                all_good = False
                continue

            to_addresses = self._normalize_recipients(cfg.get('to_addresses', []))
            cc_addresses = self._normalize_recipients(cfg.get('cc_addresses', []))
            subject_tpl = cfg.get('subject', 'Karbone Daily Position Report - {date}')
            subject = subject_tpl.format(date=report_date)
            html_path = gen.get('position_report_path', '')

            success = self.send_html_file(
                to_addresses=to_addresses,
                subject=subject,
                html_report_path=html_path,
                cc_addresses=cc_addresses if cc_addresses else None
            )
            all_good = all_good and success

        return all_good

    def _normalize_recipients(
        self,
        addresses: Union[str, List[str], None]
    ) -> List[str]:
        """
        Normalize email recipient configuration into a list of addresses.

        Args:
            addresses: Email addresses as string (comma-separated) or list.

        Returns:
            List of cleaned email addresses.
        """
        if addresses is None:
            return []
        if isinstance(addresses, str):
            return [addr.strip() for addr in addresses.split(',') if addr.strip()]
        if isinstance(addresses, list):
            return [
                addr.strip() for addr in addresses
                if isinstance(addr, str) and addr.strip()
            ]
        return []

    def _validate_credentials(self) -> bool:
        """
        Check if email credentials are configured.

        Returns:
            True if username and password are present, False otherwise.
        """
        username = self.config.get('username', '')
        password = self.config.get('password', '')
        return bool(username and password)

    def _load_report_content(
        self,
        html_path: str,
        text_path: str,
        date: str,
        total_pnl: float
    ) -> str:
        """
        Load report content from file or generate fallback.

        Priority:
        1. HTML file
        2. Text file (wrapped in <pre> tags)
        3. Template from configuration

        Args:
            html_path: Path to HTML report file.
            text_path: Path to text report file.
            date: Date for template formatting.
            total_pnl: Total PnL for template formatting.

        Returns:
            HTML content string.
        """
        body_template = self.config.get('body', 'Daily PnL Summary Report')

        if os.path.exists(html_path):
            try:
                with open(html_path, 'r', encoding='utf-8') as f:
                    return f.read()
            except OSError as e:
                logger.warning(f"Could not read HTML report: {e}")

        if os.path.exists(text_path):
            try:
                with open(text_path, 'r', encoding='utf-8') as f:
                    return f"<pre>{f.read()}</pre>"
            except OSError as e:
                logger.warning(f"Could not read text report: {e}")

        return f"<p>{body_template.format(date=date, total_pnl=f'${total_pnl:,.2f}')}</p>"

    def _attach_inline_images(self, msg: MIMEMultipart, images_dir: Optional[Path]) -> None:
        """Attach every PNG in images_dir as a related inline image with CID <stem@karbone>."""
        if images_dir is None or not images_dir.is_dir():
            return
        for png in sorted(images_dir.glob('*.png')):
            try:
                with open(png, 'rb') as f:
                    img = MIMEImage(f.read(), _subtype='png')
                img.add_header('Content-ID', f'<{png.stem}@karbone>')
                img.add_header('Content-Disposition', 'inline', filename=png.name)
                msg.attach(img)
            except OSError as e:
                logger.warning("Could not attach sparkline image %s: %s", png.name, e)

    def _send_email(
        self,
        to_addresses: List[str],
        subject: str,
        html_body: str,
        cc_addresses: Optional[List[str]] = None,
        images_dir: Optional[Path] = None,
    ) -> bool:
        """
        Send an HTML email using SMTP.

        Args:
            to_addresses: List of recipient email addresses.
            subject: Email subject line.
            html_body: HTML content for email body.
            cc_addresses: Optional list of CC email addresses.

        Returns:
            True if successful, False otherwise.
        """
        smtp_server = self.config.get('smtp_server', 'smtp.gmail.com')
        smtp_port = self.config.get('smtp_port', 587)
        username = self.config.get('username', '')
        password = self.config.get('password', '')
        from_address = self.config.get('from_address', username)

        if cc_addresses is None:
            cc_addresses = []

        try:
            # Outer 'related' wrapper allows inline CID image references
            msg = MIMEMultipart('related')
            msg['From'] = from_address
            msg['To'] = ', '.join(to_addresses)
            if cc_addresses:
                msg['Cc'] = ', '.join(cc_addresses)
            msg['Subject'] = subject

            # 'alternative' inner part holds the HTML body
            alt = MIMEMultipart('alternative')
            alt.attach(MIMEText(html_body, 'html'))
            msg.attach(alt)

            # Attach sparkline PNGs as inline CID images
            self._attach_inline_images(msg, images_dir)

            # Extract just email addresses for sendmail (display names are for headers only)
            def extract_email(addr: str) -> str:
                """Extract email address from 'Name <email>' format."""
                _, email = parseaddr(addr)
                return email if email else addr

            all_recipients = [extract_email(addr) for addr in to_addresses + cc_addresses]

            server = smtplib.SMTP(smtp_server, smtp_port)
            server.starttls()
            server.login(username, password)
            server.sendmail(from_address, all_recipients, msg.as_string())
            server.quit()
            return True

        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"SMTP authentication failed: {e}")
            return False
        except smtplib.SMTPConnectError as e:
            logger.error(f"Failed to connect to SMTP server: {e}")
            return False
        except smtplib.SMTPException as e:
            logger.error(f"SMTP Error sending email: {e}")
            return False
        except OSError as e:
            logger.error(f"Network error sending email: {e}")
            return False
        except Exception:
            logger.exception("Unexpected error sending email")
            return False

