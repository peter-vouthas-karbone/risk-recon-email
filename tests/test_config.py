from pathlib import Path

from daily_recon import config


def test_constants_present_and_sane():
    assert config.TOLERANCE == 1e-6
    assert "peter.vouthas@karbone.com" in config.EMAIL_RECIPIENTS
    assert config.SMTP_HOST == "smtp.office365.com"
    assert config.SMTP_PORT == 587
    assert config.SMTP_STARTTLS is True
    assert config.KEYRING_SERVICE == "karbone_recon_smtp"
    assert config.KEYRING_USERNAME == "peter.vouthas@karbone.com"
    assert config.MAX_TABLE_ROWS_IN_EMAIL == 50


def test_paths_are_absolute():
    assert isinstance(config.DATA_ROOT, Path)
    assert config.DATA_ROOT.is_absolute()
    assert config.OUTPUT_ROOT.is_absolute()
    assert config.DUCKDB_PATH.is_absolute()
    assert config.DUCKDB_PATH.suffix == ".duckdb"
