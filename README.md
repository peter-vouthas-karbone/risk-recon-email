# Karbone Daily Reconciliation (RINs)

Deterministic daily pipeline that reconciles the FO Fuels tradesheet against the MO RINs tradesheet across four checks and emails a single report.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ./vendor/karbone_recon
pip install -e ./vendor/karbone_pnl_pos
pip install -e .[dev]
```

Store the SMTP password in the OS keyring (run once):

```powershell
python -c "import keyring; keyring.set_password('karbone_recon_smtp','peter.vouthas@karbone.com', 'PASSWORD_HERE')"
```

## Daily Run

```powershell
python daily_run.py                    # business date = today
python daily_run.py --date 2026-05-20  # explicit business date
python daily_run.py --no-email         # write outputs but skip sending
```

Drop incoming MO and FO CSVs into `data/incoming/` before running.

Outputs land in `output/<business-date>/` plus the SMTP report.

## Scheduling

Windows Task Scheduler — daily 06:00, Mon–Sat:

1. Create Basic Task → "Karbone Daily Recon"
2. Trigger: Weekly on Mon–Sat at 06:00
3. Action: Start a program
   - Program: `C:\Path\To\claude-daily-recon\.venv\Scripts\python.exe`
   - Arguments: `daily_run.py`
   - Start in: `C:\Path\To\claude-daily-recon` (absolute path)
4. Settings → "Run whether user is logged on or not" with stored credentials so the keyring is accessible.

## Exit Codes

| Code | Meaning                              |
|------|--------------------------------------|
| 0    | Success                              |
| 1    | Uncaught exception (see crash log)   |
| 2    | Incoming files missing/malformed     |
| 3    | Stage / ingest failure               |
| 4    | Keyring credential missing           |
| 5    | SMTP failed after all retries        |

## Tests

```powershell
pytest -v
```

## Project Layout

```
claude-daily-recon/
├── daily_run.py            # CLI entrypoint
├── src/daily_recon/
│   ├── config.py           # constants, paths, tolerances
│   ├── persistence.py      # pos_* DuckDB tables
│   ├── positions.py        # running position from legs
│   ├── checks/             # 4 deterministic checks
│   ├── mailer.py           # SMTP via keyring
│   ├── report/             # HTML + text composers
│   └── pipeline.py         # orchestrator
├── vendor/
│   ├── karbone_recon/      # vendored Karbone tradesheet reconciliation
│   └── karbone_pnl_pos/    # vendored Karbone PnL & position (theme, helpers)
└── tests/                  # pytest suite
```
