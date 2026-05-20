"""SMTP mailer that pulls credentials from the OS keyring."""
from __future__ import annotations

import logging
import smtplib
import time
from email.message import EmailMessage

import keyring

from daily_recon.config import (
    KEYRING_SERVICE,
    KEYRING_USERNAME,
    SMTP_HOST,
    SMTP_PORT,
    SMTP_RETRY_DELAYS_SEC,
    SMTP_STARTTLS,
)

logger = logging.getLogger(__name__)


class MailerCredentialError(RuntimeError):
    """Raised when the keyring returns no password for the configured service/user."""


class KeyringSMTPMailer:
    """Send an EmailMessage via SMTP using a password stored in the OS keyring."""

    def __init__(
        self,
        host: str = SMTP_HOST,
        port: int = SMTP_PORT,
        starttls: bool = SMTP_STARTTLS,
        retry_delays: tuple[int, ...] = SMTP_RETRY_DELAYS_SEC,
        username: str = KEYRING_USERNAME,
        keyring_service: str = KEYRING_SERVICE,
    ) -> None:
        self._host = host
        self._port = port
        self._starttls = starttls
        self._retry_delays = retry_delays
        self._username = username
        self._service = keyring_service

    def send(self, msg: EmailMessage) -> None:
        password = keyring.get_password(self._service, self._username)
        if not password:
            raise MailerCredentialError(
                f"No password in keyring for service={self._service!r} user={self._username!r}"
            )

        attempt = 0
        last_exc: Exception | None = None
        for delay in (0, *self._retry_delays):
            if delay:
                logger.info("Retrying SMTP send in %ss (attempt %s)", delay, attempt)
                time.sleep(delay)
            attempt += 1
            try:
                smtp = smtplib.SMTP(self._host, self._port, timeout=30)
                try:
                    smtp.ehlo()
                    if self._starttls:
                        smtp.starttls()
                        smtp.ehlo()
                    smtp.login(self._username, password)
                    smtp.send_message(msg)
                    return
                finally:
                    try:
                        smtp.quit()
                    except smtplib.SMTPException:
                        pass
            except smtplib.SMTPException as e:
                last_exc = e
                logger.warning("SMTP send failed on attempt %s: %s", attempt, e)
        assert last_exc is not None
        raise last_exc
