"""Configuration constants for the daily_recon pipeline.

All tunables live here. No env vars, no YAML — one file, one place to change.
"""
from datetime import date
from pathlib import Path

# Project root: this file is at src/daily_recon/config.py
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Filesystem layout
DATA_ROOT = PROJECT_ROOT / "data"
OUTPUT_ROOT = PROJECT_ROOT / "output"
DUCKDB_PATH = PROJECT_ROOT / "recon.duckdb"

# Numeric tolerance for both volume and price comparisons.
TOLERANCE = 1e-6

POSITION_BREAK_LOOKBACK_DAYS = 7

# Position exceptions (breaks, historical drift) for business_date before this
# date are suppressed — pre-live data is expected to be incomplete.
DESYNC_CUTOFF_DATE = date(2026, 1, 1)

# Email
EMAIL_RECIPIENTS = ["peter.vouthas@karbone.com"]
EMAIL_CC: list[str] = []
EMAIL_SENDER = "peter.vouthas@karbone.com"

# SMTP — Gmail defaults
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_STARTTLS = True
SMTP_RETRY_DELAYS_SEC = (2, 8, 32)

# Keyring credential lookup
KEYRING_SERVICE = "karbone_recon_smtp"
KEYRING_USERNAME = "peter.vouthas@karbone.com"

# Report formatting
MAX_TABLE_ROWS_IN_EMAIL = 50

# Source tradesheets (read-only; the pipeline never modifies these)
POSITIONS_DIR = Path(r"G:\Shared drives\KarboneRisk\Data\Positions")
MO_SOURCE_PATH = POSITIONS_DIR / "rins_filtered_tradesheet.csv"
FUELS_SOURCE_PATH = POSITIONS_DIR / "Fuels_Tradesheet_Cpty.csv"
