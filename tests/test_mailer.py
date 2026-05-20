from email.message import EmailMessage
from unittest.mock import MagicMock, patch

import pytest

from daily_recon.mailer import KeyringSMTPMailer, MailerCredentialError


def _msg() -> EmailMessage:
    m = EmailMessage()
    m["From"] = "a@b"
    m["To"] = "c@d"
    m["Subject"] = "x"
    m.set_content("body")
    return m


def test_missing_credential_raises():
    with patch("daily_recon.mailer.keyring.get_password", return_value=None):
        m = KeyringSMTPMailer()
        with pytest.raises(MailerCredentialError):
            m.send(_msg())


def test_send_calls_smtp_starttls_login_sendmessage_quit():
    fake_smtp = MagicMock()
    with patch("daily_recon.mailer.keyring.get_password", return_value="pw"), \
         patch("daily_recon.mailer.smtplib.SMTP", return_value=fake_smtp) as smtp_ctor:
        m = KeyringSMTPMailer()
        m.send(_msg())
    smtp_ctor.assert_called_once()
    fake_smtp.starttls.assert_called_once()
    fake_smtp.login.assert_called_once()
    fake_smtp.send_message.assert_called_once()
    fake_smtp.quit.assert_called_once()


def test_retries_on_transient_failure_then_succeeds():
    import smtplib
    fake_smtp = MagicMock()
    fake_smtp.send_message.side_effect = [
        smtplib.SMTPException("boom"),
        smtplib.SMTPException("boom2"),
        None,
    ]
    with patch("daily_recon.mailer.keyring.get_password", return_value="pw"), \
         patch("daily_recon.mailer.smtplib.SMTP", return_value=fake_smtp), \
         patch("daily_recon.mailer.time.sleep") as sleep:
        m = KeyringSMTPMailer()
        m.send(_msg())
    assert fake_smtp.send_message.call_count == 3
    assert sleep.call_count == 2  # one sleep between each retry pair


def test_raises_after_all_retries():
    import smtplib
    fake_smtp = MagicMock()
    fake_smtp.send_message.side_effect = smtplib.SMTPException("boom")
    with patch("daily_recon.mailer.keyring.get_password", return_value="pw"), \
         patch("daily_recon.mailer.smtplib.SMTP", return_value=fake_smtp), \
         patch("daily_recon.mailer.time.sleep"):
        m = KeyringSMTPMailer()
        with pytest.raises(smtplib.SMTPException):
            m.send(_msg())
