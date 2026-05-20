# RINs Daily Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic Python pipeline that runs daily at 06:00 (Mon–Sat), reconciles the FO Fuels tradesheet against the MO RINs tradesheet across four checks, and emails one report styled to match the existing PnL report aesthetic.

**Architecture:** A new package `src/daily_recon/` orchestrates two vendored packages — `karbone_recon` (staging, drift, cross-recon) and `karbone_pnl_pos` (theme, HTML helpers). New code: running-position computation, four deterministic checks, keyring-based SMTP mailer, HTML/text report composer reusing the PnL theme. Persisted in the same `recon.duckdb` under `pos_*` tables.

**Tech Stack:** Python 3.11+, pandas, duckdb, keyring, smtplib (stdlib), pytest. No new heavy deps. No LLMs in the run.

**Spec:** `docs/superpowers/specs/2026-05-20-rins-daily-recon-design.md`
**Visual reference:** `docs/superpowers/specs/previews/recon-email-mockup.html`

---

## File Layout (created in this plan)

```
claude-daily-recon/
├── pyproject.toml                 # Task 1
├── daily_run.py                   # Task 17
├── README.md                      # Task 18
├── src/daily_recon/
│   ├── __init__.py                # Task 1
│   ├── config.py                  # Task 4
│   ├── persistence.py             # Task 5
│   ├── positions.py               # Task 6
│   ├── checks/
│   │   ├── __init__.py            # Task 7
│   │   ├── trade_drift.py         # Task 7
│   │   ├── historical_position.py # Task 8
│   │   ├── position_equality.py   # Task 9
│   │   └── prior_day_trades.py    # Task 10
│   ├── mailer.py                  # Task 11
│   ├── report/
│   │   ├── __init__.py            # Task 12
│   │   ├── modules.py             # Task 13
│   │   ├── html_compose.py        # Task 14
│   │   └── text_compose.py        # Task 14
│   └── pipeline.py                # Task 15
├── vendor/
│   ├── karbone_recon/             # Task 2
│   └── karbone_pnl_pos/           # Task 3
└── tests/
    ├── conftest.py                # Task 1
    ├── fixtures/                  # populated per-task
    ├── test_persistence.py        # Task 5
    ├── test_positions.py          # Task 6
    ├── test_check_trade_drift.py  # Task 7
    ├── test_check_hist_pos.py     # Task 8
    ├── test_check_pos_eq.py       # Task 9
    ├── test_check_t1_trades.py    # Task 10
    ├── test_mailer.py             # Task 11
    ├── test_report_modules.py     # Task 13
    ├── test_report_compose.py     # Task 14
    └── test_pipeline_integration.py # Task 16
```

---

## Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `src/daily_recon/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `.gitignore`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "daily-recon"
version = "0.1.0"
description = "Daily FO/MO RINs reconciliation pipeline"
requires-python = ">=3.11"
dependencies = [
    "pandas>=2.0",
    "duckdb>=0.10",
    "keyring>=24.0",
    "karbone-recon",
    "karbone-pnl-pos",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-cov>=4.0"]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --strict-markers"

[tool.uv.sources]
karbone-recon = { path = "vendor/karbone_recon", editable = true }
karbone-pnl-pos = { path = "vendor/karbone_pnl_pos", editable = true }
```

- [ ] **Step 2: Create `src/daily_recon/__init__.py`**

```python
"""Daily FO/MO RINs reconciliation pipeline."""

__version__ = "0.1.0"
```

- [ ] **Step 3: Create `tests/__init__.py`**

```python
```

- [ ] **Step 4: Create `tests/conftest.py`**

```python
"""Shared pytest fixtures."""

from pathlib import Path

import duckdb
import pytest


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Return a path to a fresh empty DuckDB file in a tmp dir."""
    return tmp_path / "test.duckdb"


@pytest.fixture
def tmp_data_root(tmp_path: Path) -> Path:
    """Return a tmp data root with incoming/, archive/ subdirs."""
    root = tmp_path / "data"
    (root / "incoming").mkdir(parents=True)
    (root / "archive").mkdir(parents=True)
    return root
```

- [ ] **Step 5: Create `.gitignore`**

```
__pycache__/
*.pyc
.pytest_cache/
.coverage
*.duckdb
_extracted/
output/
data/incoming/*
data/archive/*
!data/incoming/.gitkeep
!data/archive/.gitkeep
.venv/
```

- [ ] **Step 6: Commit**

```bash
git init
git add pyproject.toml src/daily_recon/__init__.py tests/__init__.py tests/conftest.py .gitignore
git commit -m "chore: scaffold daily_recon package"
```

---

## Task 2: Vendor the `recon/` project as `karbone_recon`

The existing recon project uses bare sibling imports (`from audit import ...`). Vendoring requires renaming the package root and rewriting imports to absolute form so it can be installed alongside the pnl-pos vendor without namespace collisions.

**Files:**
- Create: `vendor/karbone_recon/pyproject.toml`
- Create: `vendor/karbone_recon/karbone_recon/__init__.py`
- Create: `vendor/karbone_recon/karbone_recon/*.py` (copied + rewritten from `_extracted/recon/src/*.py`)
- Create: `vendor/karbone_recon/data/counterparty_mapping.csv` (copy from existing recon project's data/, if present)

- [ ] **Step 1: Copy source tree into the vendor location**

```powershell
$src = "_extracted/recon/src"
$dst = "vendor/karbone_recon/karbone_recon"
New-Item -ItemType Directory -Force $dst | Out-Null
Get-ChildItem $src -Filter "*.py" | Where-Object { $_.FullName -notlike "*__pycache__*" } | ForEach-Object {
    Copy-Item $_.FullName -Destination $dst
}
```

- [ ] **Step 2: Add `__init__.py` to the package**

```python
# vendor/karbone_recon/karbone_recon/__init__.py
"""Karbone tradesheet reconciliation — vendored."""
```

- [ ] **Step 3: Rewrite imports — bare sibling imports → absolute `karbone_recon.*`**

Run this exact PowerShell rewrite across every `.py` file in `vendor/karbone_recon/karbone_recon/`:

```powershell
$modules = "archive","audit","db","ingest","mappings","reconcile","report","stage"
Get-ChildItem "vendor/karbone_recon/karbone_recon" -Filter "*.py" | ForEach-Object {
    $text = Get-Content $_.FullName -Raw
    foreach ($m in $modules) {
        $text = $text -replace "(?m)^from $m import", "from karbone_recon.$m import"
        $text = $text -replace "(?m)^import $m\b", "import karbone_recon.$m as $m"
    }
    Set-Content $_.FullName -Value $text -Encoding utf8
}
```

- [ ] **Step 4: Create `vendor/karbone_recon/pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "karbone-recon"
version = "0.1.0"
description = "Vendored copy of the Karbone tradesheet reconciliation project."
requires-python = ">=3.11"
dependencies = ["pandas>=2.0", "duckdb>=0.10"]

[tool.setuptools.packages.find]
include = ["karbone_recon*"]
```

- [ ] **Step 5: Install both vendor packages and the main project (editable)**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ./vendor/karbone_recon
pip install -e .
```

Expected: install succeeds. (Vendor for pnl_pos is added in Task 3 — main install will fail until then. That's OK; we'll verify after Task 3.)

- [ ] **Step 6: Write a smoke import test**

`tests/test_vendor_imports.py`:
```python
"""Vendor packages must be importable under their renamed namespaces."""


def test_karbone_recon_modules_importable():
    from karbone_recon import archive, audit, db, ingest, mappings, reconcile, report, stage  # noqa: F401
```

- [ ] **Step 7: Commit**

```bash
git add vendor/karbone_recon tests/test_vendor_imports.py
git commit -m "vendor: import recon as karbone_recon package"
```

---

## Task 3: Vendor the `pnl-pos/` project as `karbone_pnl_pos`

This project uses `from src.x.y import ...` style. We rename `src/` to `karbone_pnl_pos/` and rewrite imports.

**Files:**
- Create: `vendor/karbone_pnl_pos/pyproject.toml`
- Create: `vendor/karbone_pnl_pos/karbone_pnl_pos/...` (whole tree)

- [ ] **Step 1: Copy the full source tree, excluding caches**

```powershell
$src = "_extracted/pnl-pos/src"
$dst = "vendor/karbone_pnl_pos/karbone_pnl_pos"
New-Item -ItemType Directory -Force $dst | Out-Null
robocopy $src $dst /E /XD __pycache__ | Out-Null
```

- [ ] **Step 2: Rewrite imports — `from src.` → `from karbone_pnl_pos.`**

```powershell
Get-ChildItem "vendor/karbone_pnl_pos/karbone_pnl_pos" -Recurse -Filter "*.py" | ForEach-Object {
    $text = Get-Content $_.FullName -Raw
    $text = $text -replace "(?m)^from src\.", "from karbone_pnl_pos."
    $text = $text -replace "(?m)^import src\.", "import karbone_pnl_pos."
    Set-Content $_.FullName -Value $text -Encoding utf8
}
```

- [ ] **Step 3: Ensure top-level `__init__.py` exists**

```python
# vendor/karbone_pnl_pos/karbone_pnl_pos/__init__.py
"""Karbone PnL & Position project — vendored."""
```

- [ ] **Step 4: Create `vendor/karbone_pnl_pos/pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "karbone-pnl-pos"
version = "0.1.0"
description = "Vendored copy of the Karbone PnL & Position project."
requires-python = ">=3.11"
dependencies = ["pandas>=2.0"]

[tool.setuptools.packages.find]
include = ["karbone_pnl_pos*"]
```

- [ ] **Step 5: Install and run the vendor smoke test**

```powershell
pip install -e ./vendor/karbone_pnl_pos
pip install -e .
```

Append to `tests/test_vendor_imports.py`:
```python
def test_karbone_pnl_pos_theme_importable():
    from karbone_pnl_pos.reporting.theme import THEME, NUM_FONT, SANS_FONT
    assert THEME.accent == "#0a2540"
    assert "IBM Plex" in SANS_FONT


def test_karbone_pnl_pos_html_helpers_importable():
    from karbone_pnl_pos.reporting.html_builder import (
        _s, _td, _tr, _table, _render_module_header, _render_subtotal_row,
    )
    # Smoke check: helpers produce strings
    assert _td("hi", "color:red").startswith("<td")
```

Run: `pytest tests/test_vendor_imports.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add vendor/karbone_pnl_pos tests/test_vendor_imports.py
git commit -m "vendor: import pnl-pos as karbone_pnl_pos package"
```

---

## Task 4: `config.py` — constants and paths

**Files:**
- Create: `src/daily_recon/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

`tests/test_config.py`:
```python
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
```

- [ ] **Step 2: Run the test — expect ImportError**

Run: `pytest tests/test_config.py -v`
Expected: ERROR (`daily_recon.config` missing).

- [ ] **Step 3: Implement `config.py`**

```python
"""Configuration constants for the daily_recon pipeline.

All tunables live here. No env vars, no YAML — one file, one place to change.
"""
from pathlib import Path

# Project root: this file is at src/daily_recon/config.py
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Filesystem layout
DATA_ROOT = PROJECT_ROOT / "data"
OUTPUT_ROOT = PROJECT_ROOT / "output"
DUCKDB_PATH = PROJECT_ROOT / "recon.duckdb"

# Numeric tolerance for both volume and price comparisons.
TOLERANCE = 1e-6

# Email
EMAIL_RECIPIENTS = ["peter.vouthas@karbone.com"]
EMAIL_CC: list[str] = []
EMAIL_SENDER = "peter.vouthas@karbone.com"

# SMTP — Microsoft 365 defaults
SMTP_HOST = "smtp.office365.com"
SMTP_PORT = 587
SMTP_STARTTLS = True
SMTP_RETRY_DELAYS_SEC = (2, 8, 32)

# Keyring credential lookup
KEYRING_SERVICE = "karbone_recon_smtp"
KEYRING_USERNAME = "peter.vouthas@karbone.com"

# Report formatting
MAX_TABLE_ROWS_IN_EMAIL = 50
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `pytest tests/test_config.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/daily_recon/config.py tests/test_config.py
git commit -m "feat(config): add module constants and paths"
```

---

## Task 5: `persistence.py` — `pos_*` DuckDB tables

**Files:**
- Create: `src/daily_recon/persistence.py`
- Create: `tests/test_persistence.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_persistence.py
from datetime import date, datetime
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from daily_recon.persistence import (
    PosRunRecord,
    create_pos_schema,
    insert_exceptions,
    insert_running_positions,
    insert_run,
    latest_successful_run_id,
)


@pytest.fixture
def conn(tmp_db: Path):
    c = duckdb.connect(str(tmp_db))
    create_pos_schema(c)
    yield c
    c.close()


def test_create_pos_schema_is_idempotent(conn):
    create_pos_schema(conn)  # second call must not error
    tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
    assert {"pos_runs", "pos_running_position", "pos_exceptions"} <= tables


def test_insert_run_and_lookup(conn):
    rid = "2026-05-20T060000"
    insert_run(conn, PosRunRecord(
        run_id=rid, business_date=date(2026, 5, 20),
        started_at=datetime(2026, 5, 20, 6, 0, 0),
        finished_at=datetime(2026, 5, 20, 6, 0, 30),
        status="success",
    ))
    assert latest_successful_run_id(conn, exclude=None) == rid
    assert latest_successful_run_id(conn, exclude=rid) is None


def test_insert_running_positions_roundtrip(conn):
    rid = "2026-05-20T060000"
    insert_run(conn, PosRunRecord(
        run_id=rid, business_date=date(2026, 5, 20),
        started_at=datetime.now(), finished_at=datetime.now(), status="success",
    ))
    df = pd.DataFrame({
        "source": ["mo", "fo"],
        "business_date": [date(2026, 5, 20), date(2026, 5, 20)],
        "product_canonical": ["D3 RIN", "D3 RIN"],
        "vintage_canonical": ["2024", "2024"],
        "position": [100.0, 100.0],
    })
    insert_running_positions(conn, rid, df)
    out = conn.execute("SELECT source, position FROM pos_running_position ORDER BY source").df()
    assert list(out["source"]) == ["fo", "mo"]
    assert list(out["position"]) == [100.0, 100.0]


def test_insert_exceptions_stores_payload_json(conn):
    rid = "2026-05-20T060000"
    insert_run(conn, PosRunRecord(
        run_id=rid, business_date=date(2026, 5, 20),
        started_at=datetime.now(), finished_at=datetime.now(), status="success",
    ))
    rows = [{
        "check_id": "position_break",
        "severity": "error",
        "key": {"date": "2026-05-19", "product": "D3 RIN", "vintage": "2024"},
        "payload": {"mo": 100, "fo": 90, "delta": 10},
    }]
    insert_exceptions(conn, rid, rows)
    out = conn.execute(
        "SELECT check_id, payload_json FROM pos_exceptions"
    ).fetchall()
    assert len(out) == 1
    assert out[0][0] == "position_break"
    assert '"delta": 10' in out[0][1]
```

- [ ] **Step 2: Run the test — expect ImportError**

Run: `pytest tests/test_persistence.py -v`
Expected: ERROR.

- [ ] **Step 3: Implement `persistence.py`**

```python
"""DuckDB persistence for daily_recon's pos_* tables.

These tables sit alongside karbone_recon's tables in the same database file.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Optional

import duckdb
import pandas as pd


@dataclass(frozen=True)
class PosRunRecord:
    run_id: str
    business_date: date
    started_at: datetime
    finished_at: datetime
    status: str  # 'success' | 'failed' | 'partial'


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pos_runs (
    run_id          TEXT PRIMARY KEY,
    business_date   DATE NOT NULL,
    started_at      TIMESTAMP NOT NULL,
    finished_at     TIMESTAMP,
    status          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pos_running_position (
    run_id              TEXT NOT NULL,
    source              TEXT NOT NULL,
    business_date       DATE NOT NULL,
    product_canonical   TEXT,
    vintage_canonical   TEXT,
    position            DOUBLE NOT NULL
);

CREATE TABLE IF NOT EXISTS pos_exceptions (
    run_id        TEXT NOT NULL,
    check_id      TEXT NOT NULL,
    severity      TEXT NOT NULL,
    key_json      TEXT NOT NULL,
    payload_json  TEXT NOT NULL
);
"""


def create_pos_schema(conn: duckdb.DuckDBPyConnection) -> None:
    for stmt in _SCHEMA_SQL.strip().split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)


def insert_run(conn: duckdb.DuckDBPyConnection, rec: PosRunRecord) -> None:
    conn.execute(
        "INSERT INTO pos_runs VALUES (?, ?, ?, ?, ?)",
        [rec.run_id, rec.business_date, rec.started_at, rec.finished_at, rec.status],
    )


def latest_successful_run_id(
    conn: duckdb.DuckDBPyConnection, exclude: Optional[str]
) -> Optional[str]:
    row = conn.execute(
        """
        SELECT run_id FROM pos_runs
        WHERE status = 'success' AND (? IS NULL OR run_id <> ?)
        ORDER BY finished_at DESC NULLS LAST, started_at DESC
        LIMIT 1
        """,
        [exclude, exclude],
    ).fetchone()
    return row[0] if row else None


def insert_running_positions(
    conn: duckdb.DuckDBPyConnection, run_id: str, df: pd.DataFrame
) -> None:
    if df.empty:
        return
    work = df.copy()
    work.insert(0, "run_id", run_id)
    conn.register("_pos_buf", work)
    conn.execute("INSERT INTO pos_running_position SELECT * FROM _pos_buf")
    conn.unregister("_pos_buf")


def insert_exceptions(
    conn: duckdb.DuckDBPyConnection, run_id: str, rows: Iterable[dict]
) -> None:
    payload = []
    for r in rows:
        payload.append([
            run_id, r["check_id"], r.get("severity", "error"),
            json.dumps(r["key"], default=str, sort_keys=True),
            json.dumps(r["payload"], default=str),
        ])
    if not payload:
        return
    conn.executemany(
        "INSERT INTO pos_exceptions VALUES (?, ?, ?, ?, ?)",
        payload,
    )
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `pytest tests/test_persistence.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/daily_recon/persistence.py tests/test_persistence.py
git commit -m "feat(persistence): add pos_runs, pos_running_position, pos_exceptions tables"
```

---

## Task 6: `positions.py` — running position from legs

**Files:**
- Create: `src/daily_recon/positions.py`
- Create: `tests/test_positions.py`

The function takes a leg-level DataFrame (already signed: positive = buy, negative = sell), aggregates by `(business_date, product_canonical, vintage_canonical)`, sums, then cumulative-sums sorted by date within each group. Missing dates are NOT densified — only dates with activity appear.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_positions.py
from datetime import date

import pandas as pd
import pytest

from daily_recon.positions import compute_running_position


def _legs(rows):
    return pd.DataFrame(rows, columns=["business_date", "product_canonical", "vintage_canonical", "quantity"])


def test_empty_input_returns_empty():
    df = _legs([])
    out = compute_running_position(df)
    assert out.empty
    assert list(out.columns) == ["business_date", "product_canonical", "vintage_canonical", "position"]


def test_single_group_cumsum():
    df = _legs([
        (date(2026, 5, 18), "D3 RIN", "2024", 100.0),
        (date(2026, 5, 19), "D3 RIN", "2024", -30.0),
        (date(2026, 5, 20), "D3 RIN", "2024", 50.0),
    ])
    out = compute_running_position(df)
    assert list(out["position"]) == [100.0, 70.0, 120.0]


def test_same_date_aggregates_first_then_cumsum():
    df = _legs([
        (date(2026, 5, 18), "D3 RIN", "2024", 100.0),
        (date(2026, 5, 18), "D3 RIN", "2024", -40.0),
        (date(2026, 5, 19), "D3 RIN", "2024", 10.0),
    ])
    out = compute_running_position(df)
    assert list(out["business_date"]) == [date(2026, 5, 18), date(2026, 5, 19)]
    assert list(out["position"]) == [60.0, 70.0]


def test_groups_are_independent():
    df = _legs([
        (date(2026, 5, 18), "D3 RIN", "2024", 100.0),
        (date(2026, 5, 18), "D4 RIN", "2024", 50.0),
        (date(2026, 5, 19), "D3 RIN", "2024", -30.0),
        (date(2026, 5, 19), "D3 RIN", "2025", 1.0),
    ])
    out = compute_running_position(df).sort_values(
        ["product_canonical", "vintage_canonical", "business_date"]
    ).reset_index(drop=True)
    assert list(out["position"]) == [100.0, 70.0, 1.0, 50.0]


def test_null_product_or_vintage_rows_dropped():
    df = _legs([
        (date(2026, 5, 18), None, "2024", 100.0),
        (date(2026, 5, 18), "D3 RIN", None, 100.0),
        (date(2026, 5, 18), "D3 RIN", "2024", 5.0),
    ])
    out = compute_running_position(df)
    assert len(out) == 1
    assert out.iloc[0]["position"] == 5.0


def test_zero_quantity_rows_kept():
    df = _legs([
        (date(2026, 5, 18), "D3 RIN", "2024", 0.0),
        (date(2026, 5, 19), "D3 RIN", "2024", 5.0),
    ])
    out = compute_running_position(df)
    assert list(out["position"]) == [0.0, 5.0]
```

- [ ] **Step 2: Run the test — expect ImportError**

Run: `pytest tests/test_positions.py -v`
Expected: ERROR.

- [ ] **Step 3: Implement `positions.py`**

```python
"""Running position computation from leg-level data.

Input legs must have signed quantity (positive = buy, negative = sell).
Output is one row per (date, product, vintage) with cumulative position.
"""
from __future__ import annotations

import pandas as pd

_OUT_COLS = ["business_date", "product_canonical", "vintage_canonical", "position"]


def compute_running_position(legs: pd.DataFrame) -> pd.DataFrame:
    """Compute cumulative position by (date, product, vintage)."""
    if legs.empty:
        return pd.DataFrame(columns=_OUT_COLS)

    df = legs[legs["product_canonical"].notna() & legs["vintage_canonical"].notna()].copy()
    if df.empty:
        return pd.DataFrame(columns=_OUT_COLS)

    daily = (
        df.groupby(
            ["product_canonical", "vintage_canonical", "business_date"],
            dropna=False,
            as_index=False,
        )["quantity"]
        .sum()
    )
    daily = daily.sort_values(
        ["product_canonical", "vintage_canonical", "business_date"]
    ).reset_index(drop=True)
    daily["position"] = (
        daily.groupby(["product_canonical", "vintage_canonical"])["quantity"].cumsum()
    )
    return daily[_OUT_COLS]
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `pytest tests/test_positions.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/daily_recon/positions.py tests/test_positions.py
git commit -m "feat(positions): compute running position by (date, product, vintage)"
```

---

## Task 7: `checks/trade_drift.py` — Check 1 wrapper

This check delegates to `karbone_recon.audit`. We read its already-computed drift output from DuckDB and convert it into our normalized exception rows.

**Files:**
- Create: `src/daily_recon/checks/__init__.py`
- Create: `src/daily_recon/checks/trade_drift.py`
- Create: `tests/test_check_trade_drift.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_check_trade_drift.py
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from daily_recon.checks.trade_drift import collect_trade_drift_exceptions


@pytest.fixture
def conn(tmp_db: Path):
    c = duckdb.connect(str(tmp_db))
    c.execute("""
        CREATE TABLE mo_trade_drift (
            run_id TEXT, trade_date DATE, side TEXT, counterparty_canonical TEXT,
            product_canonical TEXT, vintage_canonical TEXT,
            change_type TEXT, prior_volume DOUBLE, current_volume DOUBLE,
            prior_wap DOUBLE, current_wap DOUBLE
        )
    """)
    c.execute("""
        CREATE TABLE fuels_trade_drift (
            run_id TEXT, trade_date DATE, side TEXT, counterparty_canonical TEXT,
            product_canonical TEXT, vintage_canonical TEXT,
            change_type TEXT, prior_volume DOUBLE, current_volume DOUBLE,
            prior_wap DOUBLE, current_wap DOUBLE
        )
    """)
    yield c
    c.close()


def test_new_trade_is_suppressed(conn):
    conn.execute(
        "INSERT INTO mo_trade_drift VALUES "
        "('R1','2026-05-20','Buy','Air Liquide','D3 RIN','2024',"
        "'new_trade',NULL,100,NULL,2.5)"
    )
    rows = collect_trade_drift_exceptions(conn, run_id="R1")
    assert rows == []


def test_modified_removed_added_become_exceptions(conn):
    conn.executemany(
        "INSERT INTO mo_trade_drift VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("R1", "2026-05-19", "Buy", "Air Liquide", "D3 RIN", "2024",
             "modified_trade", 100.0, 100.0, 2.44, 2.45),
            ("R1", "2026-05-18", "Sell", "Trillium", "D3 RIN", "2024",
             "removed_trade", 50.0, None, 2.63, None),
        ],
    )
    conn.execute(
        "INSERT INTO fuels_trade_drift VALUES "
        "('R1','2026-05-18','Buy','Mercuria','D5 RIN','2025',"
        "'added_trade',NULL,75,NULL,1.82)"
    )
    rows = collect_trade_drift_exceptions(conn, run_id="R1")
    assert len(rows) == 3
    sources = sorted(r["payload"]["source"] for r in rows)
    assert sources == ["fo", "mo", "mo"]
    types = sorted(r["payload"]["change_type"] for r in rows)
    assert types == ["added_trade", "modified_trade", "removed_trade"]
    for r in rows:
        assert r["check_id"] == "trade_drift"
        assert r["severity"] == "error"
```

- [ ] **Step 2: Run the test — expect ImportError**

Run: `pytest tests/test_check_trade_drift.py -v`
Expected: ERROR.

- [ ] **Step 3: Create empty `checks/__init__.py`**

```python
# src/daily_recon/checks/__init__.py
"""Deterministic recon checks."""
```

- [ ] **Step 4: Implement `checks/trade_drift.py`**

```python
"""Check 1 — trade-level drift since prior run.

Reuses karbone_recon's mo_trade_drift / fuels_trade_drift output tables.
The 'new_trade' classification is informational and suppressed.
"""
from __future__ import annotations

import duckdb

_SUPPRESS_CHANGE_TYPES = {"new_trade"}


def collect_trade_drift_exceptions(
    conn: duckdb.DuckDBPyConnection, run_id: str
) -> list[dict]:
    rows: list[dict] = []
    for source, table in (("mo", "mo_trade_drift"), ("fo", "fuels_trade_drift")):
        df = conn.execute(
            f"""
            SELECT trade_date, side, counterparty_canonical, product_canonical,
                   vintage_canonical, change_type, prior_volume, current_volume,
                   prior_wap, current_wap
            FROM {table}
            WHERE run_id = ?
            """,
            [run_id],
        ).df()
        for r in df.to_dict(orient="records"):
            if r["change_type"] in _SUPPRESS_CHANGE_TYPES:
                continue
            rows.append({
                "check_id": "trade_drift",
                "severity": "error",
                "key": {
                    "source": source,
                    "trade_date": r["trade_date"],
                    "side": r["side"],
                    "counterparty": r["counterparty_canonical"],
                    "product": r["product_canonical"],
                    "vintage": r["vintage_canonical"],
                },
                "payload": {
                    "source": source,
                    "change_type": r["change_type"],
                    "prior_volume": r["prior_volume"],
                    "current_volume": r["current_volume"],
                    "prior_wap": r["prior_wap"],
                    "current_wap": r["current_wap"],
                },
            })
    return rows
```

- [ ] **Step 5: Run the test — expect PASS**

Run: `pytest tests/test_check_trade_drift.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add src/daily_recon/checks/__init__.py src/daily_recon/checks/trade_drift.py tests/test_check_trade_drift.py
git commit -m "feat(checks): trade_drift wraps karbone_recon drift output"
```

---

## Task 8: `checks/historical_position.py` — Check 2

Compares the current run's running positions against the prior run's for all dates `< T-1`. Any difference > `TOLERANCE` produces an exception.

**Files:**
- Create: `src/daily_recon/checks/historical_position.py`
- Create: `tests/test_check_hist_pos.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_check_hist_pos.py
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from daily_recon.checks.historical_position import (
    collect_historical_position_drift_exceptions,
)
from daily_recon.persistence import (
    PosRunRecord, create_pos_schema, insert_run, insert_running_positions,
)


def _seed(conn, run_id, business_date, rows):
    insert_run(conn, PosRunRecord(
        run_id=run_id, business_date=business_date,
        started_at=None, finished_at=None, status="success",
    ))
    df = pd.DataFrame(rows, columns=[
        "source", "business_date", "product_canonical", "vintage_canonical", "position"
    ])
    insert_running_positions(conn, run_id, df)


@pytest.fixture
def conn(tmp_db: Path):
    c = duckdb.connect(str(tmp_db))
    create_pos_schema(c)
    yield c
    c.close()


def test_returns_empty_when_no_prior_run(conn):
    _seed(conn, "R1", date(2026, 5, 20), [
        ("mo", date(2026, 5, 19), "D3 RIN", "2024", 100.0),
    ])
    rows = collect_historical_position_drift_exceptions(
        conn, current_run_id="R1", prior_run_id=None, business_date=date(2026, 5, 20)
    )
    assert rows == []


def test_detects_past_position_change(conn):
    _seed(conn, "R0", date(2026, 5, 19), [
        ("mo", date(2026, 5, 17), "D3 RIN", "2024", 100.0),
        ("mo", date(2026, 5, 18), "D3 RIN", "2024", 150.0),
    ])
    _seed(conn, "R1", date(2026, 5, 20), [
        ("mo", date(2026, 5, 17), "D3 RIN", "2024", 120.0),   # past day changed
        ("mo", date(2026, 5, 18), "D3 RIN", "2024", 150.0),   # unchanged
    ])
    rows = collect_historical_position_drift_exceptions(
        conn, current_run_id="R1", prior_run_id="R0", business_date=date(2026, 5, 20)
    )
    assert len(rows) == 1
    p = rows[0]["payload"]
    assert p["delta"] == 20.0
    assert p["prior_position"] == 100.0
    assert p["current_position"] == 120.0


def test_dates_at_or_after_T_minus_1_are_ignored(conn):
    # T = 2026-05-20, T-1 = 2026-05-19. Only dates < T-1 are inspected.
    _seed(conn, "R0", date(2026, 5, 19), [
        ("mo", date(2026, 5, 19), "D3 RIN", "2024", 100.0),
    ])
    _seed(conn, "R1", date(2026, 5, 20), [
        ("mo", date(2026, 5, 19), "D3 RIN", "2024", 200.0),  # T-1, skipped
    ])
    rows = collect_historical_position_drift_exceptions(
        conn, current_run_id="R1", prior_run_id="R0", business_date=date(2026, 5, 20)
    )
    assert rows == []


def test_below_tolerance_is_ignored(conn):
    _seed(conn, "R0", date(2026, 5, 19), [
        ("mo", date(2026, 5, 17), "D3 RIN", "2024", 100.0),
    ])
    _seed(conn, "R1", date(2026, 5, 20), [
        ("mo", date(2026, 5, 17), "D3 RIN", "2024", 100.0 + 1e-9),
    ])
    rows = collect_historical_position_drift_exceptions(
        conn, current_run_id="R1", prior_run_id="R0", business_date=date(2026, 5, 20)
    )
    assert rows == []
```

- [ ] **Step 2: Run the test — expect ImportError**

Run: `pytest tests/test_check_hist_pos.py -v`
Expected: ERROR.

- [ ] **Step 3: Implement `checks/historical_position.py`**

```python
"""Check 2 — historical position drift.

For every (source, product, vintage, date<T-1), compare today's running position
to the prior run's. Any |delta| > TOLERANCE → exception.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import duckdb

from daily_recon.config import TOLERANCE


def collect_historical_position_drift_exceptions(
    conn: duckdb.DuckDBPyConnection,
    current_run_id: str,
    prior_run_id: Optional[str],
    business_date: date,
) -> list[dict]:
    if prior_run_id is None:
        return []
    cutoff = business_date - timedelta(days=1)  # exclusive — only dates strictly before T-1

    df = conn.execute(
        """
        WITH cur AS (
            SELECT source, business_date, product_canonical, vintage_canonical, position
            FROM pos_running_position
            WHERE run_id = ? AND business_date < ?
        ),
        prv AS (
            SELECT source, business_date, product_canonical, vintage_canonical, position
            FROM pos_running_position
            WHERE run_id = ? AND business_date < ?
        )
        SELECT
            COALESCE(cur.source, prv.source) AS source,
            COALESCE(cur.business_date, prv.business_date) AS business_date,
            COALESCE(cur.product_canonical, prv.product_canonical) AS product,
            COALESCE(cur.vintage_canonical, prv.vintage_canonical) AS vintage,
            COALESCE(cur.position, 0.0) AS current_position,
            COALESCE(prv.position, 0.0) AS prior_position
        FROM cur FULL OUTER JOIN prv USING (source, business_date, product_canonical, vintage_canonical)
        """,
        [current_run_id, cutoff, prior_run_id, cutoff],
    ).df()

    rows: list[dict] = []
    for r in df.to_dict(orient="records"):
        delta = float(r["current_position"]) - float(r["prior_position"])
        if abs(delta) <= TOLERANCE:
            continue
        rows.append({
            "check_id": "historical_position_drift",
            "severity": "error",
            "key": {
                "source": r["source"],
                "date": r["business_date"],
                "product": r["product"],
                "vintage": r["vintage"],
            },
            "payload": {
                "source": r["source"],
                "business_date": r["business_date"],
                "product": r["product"],
                "vintage": r["vintage"],
                "prior_position": float(r["prior_position"]),
                "current_position": float(r["current_position"]),
                "delta": delta,
            },
        })
    return rows
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `pytest tests/test_check_hist_pos.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/daily_recon/checks/historical_position.py tests/test_check_hist_pos.py
git commit -m "feat(checks): detect historical position drift vs prior run"
```

---

## Task 9: `checks/position_equality.py` — Check 3

For every (date, product, vintage), MO position must equal FO position within tolerance.

**Files:**
- Create: `src/daily_recon/checks/position_equality.py`
- Create: `tests/test_check_pos_eq.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_check_pos_eq.py
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from daily_recon.checks.position_equality import (
    collect_position_equality_exceptions,
)
from daily_recon.persistence import (
    PosRunRecord, create_pos_schema, insert_run, insert_running_positions,
)


def _seed(conn, run_id, rows):
    insert_run(conn, PosRunRecord(
        run_id=run_id, business_date=date(2026, 5, 20),
        started_at=None, finished_at=None, status="success",
    ))
    df = pd.DataFrame(rows, columns=[
        "source", "business_date", "product_canonical", "vintage_canonical", "position",
    ])
    insert_running_positions(conn, run_id, df)


@pytest.fixture
def conn(tmp_db: Path):
    c = duckdb.connect(str(tmp_db))
    create_pos_schema(c)
    yield c
    c.close()


def test_equal_positions_produce_no_exceptions(conn):
    _seed(conn, "R1", [
        ("mo", date(2026, 5, 19), "D3 RIN", "2024", 100.0),
        ("fo", date(2026, 5, 19), "D3 RIN", "2024", 100.0),
    ])
    assert collect_position_equality_exceptions(conn, run_id="R1") == []


def test_break_when_mo_exceeds_fo(conn):
    _seed(conn, "R1", [
        ("mo", date(2026, 5, 19), "D3 RIN", "2024", 100.0),
        ("fo", date(2026, 5, 19), "D3 RIN", "2024", 90.0),
    ])
    rows = collect_position_equality_exceptions(conn, run_id="R1")
    assert len(rows) == 1
    p = rows[0]["payload"]
    assert p["mo_position"] == 100.0
    assert p["fo_position"] == 90.0
    assert p["delta"] == 10.0


def test_missing_source_treated_as_zero(conn):
    _seed(conn, "R1", [
        ("mo", date(2026, 5, 19), "D3 RIN", "2024", 50.0),
    ])
    rows = collect_position_equality_exceptions(conn, run_id="R1")
    assert len(rows) == 1
    p = rows[0]["payload"]
    assert p["fo_position"] == 0.0


def test_below_tolerance_ignored(conn):
    _seed(conn, "R1", [
        ("mo", date(2026, 5, 19), "D3 RIN", "2024", 100.0),
        ("fo", date(2026, 5, 19), "D3 RIN", "2024", 100.0 + 1e-9),
    ])
    assert collect_position_equality_exceptions(conn, run_id="R1") == []
```

- [ ] **Step 2: Run the test — expect ImportError**

Run: `pytest tests/test_check_pos_eq.py -v`
Expected: ERROR.

- [ ] **Step 3: Implement `checks/position_equality.py`**

```python
"""Check 3 — MO vs FO running position equality across all dates."""
from __future__ import annotations

import duckdb

from daily_recon.config import TOLERANCE


def collect_position_equality_exceptions(
    conn: duckdb.DuckDBPyConnection, run_id: str
) -> list[dict]:
    df = conn.execute(
        """
        WITH mo AS (
            SELECT business_date, product_canonical, vintage_canonical, position
            FROM pos_running_position WHERE run_id = ? AND source = 'mo'
        ),
        fo AS (
            SELECT business_date, product_canonical, vintage_canonical, position
            FROM pos_running_position WHERE run_id = ? AND source = 'fo'
        )
        SELECT
            COALESCE(mo.business_date, fo.business_date) AS business_date,
            COALESCE(mo.product_canonical, fo.product_canonical) AS product,
            COALESCE(mo.vintage_canonical, fo.vintage_canonical) AS vintage,
            COALESCE(mo.position, 0.0) AS mo_position,
            COALESCE(fo.position, 0.0) AS fo_position
        FROM mo FULL OUTER JOIN fo
        USING (business_date, product_canonical, vintage_canonical)
        """,
        [run_id, run_id],
    ).df()

    rows: list[dict] = []
    for r in df.to_dict(orient="records"):
        delta = float(r["mo_position"]) - float(r["fo_position"])
        if abs(delta) <= TOLERANCE:
            continue
        rows.append({
            "check_id": "position_break",
            "severity": "error",
            "key": {
                "date": r["business_date"],
                "product": r["product"],
                "vintage": r["vintage"],
            },
            "payload": {
                "business_date": r["business_date"],
                "product": r["product"],
                "vintage": r["vintage"],
                "mo_position": float(r["mo_position"]),
                "fo_position": float(r["fo_position"]),
                "delta": delta,
            },
        })
    return rows
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `pytest tests/test_check_pos_eq.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/daily_recon/checks/position_equality.py tests/test_check_pos_eq.py
git commit -m "feat(checks): MO vs FO running position equality"
```

---

## Task 10: `checks/prior_day_trades.py` — Check 4

Reads `karbone_recon`'s `cross_recon` output filtered to `trade_date = T-1`. Anything not `matched` is an exception.

**Files:**
- Create: `src/daily_recon/checks/prior_day_trades.py`
- Create: `tests/test_check_t1_trades.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_check_t1_trades.py
from datetime import date
from pathlib import Path

import duckdb
import pytest

from daily_recon.checks.prior_day_trades import collect_prior_day_trade_exceptions


@pytest.fixture
def conn(tmp_db: Path):
    c = duckdb.connect(str(tmp_db))
    c.execute("""
        CREATE TABLE cross_recon (
            run_id TEXT, status TEXT, trade_date DATE, side TEXT,
            counterparty_canonical TEXT, product_canonical TEXT,
            vintage_canonical TEXT, delivery_match_date DATE,
            mo_volume DOUBLE, fuels_volume DOUBLE,
            mo_wap DOUBLE, fuels_wap DOUBLE
        )
    """)
    yield c
    c.close()


def test_matched_rows_are_suppressed(conn):
    conn.execute(
        "INSERT INTO cross_recon VALUES "
        "('R1','matched','2026-05-19','Buy','Air Liquide','D3 RIN','2024',"
        "'2026-05-20',100,100,2.5,2.5)"
    )
    assert collect_prior_day_trade_exceptions(
        conn, run_id="R1", business_date=date(2026, 5, 20)
    ) == []


def test_only_T_minus_1_rows_considered(conn):
    # Same-day buckets (T) and old-day buckets (T-3) must be filtered out.
    conn.executemany(
        "INSERT INTO cross_recon VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("R1", "price_break", date(2026, 5, 20), "Buy", "A", "D3 RIN", "2024",
             date(2026, 5, 25), 100, 100, 2.5, 2.4),  # today, ignored
            ("R1", "volume_break", date(2026, 5, 17), "Sell", "B", "D3 RIN", "2024",
             date(2026, 5, 25), 100, 90, 2.5, 2.5),   # old, ignored
            ("R1", "mo_only", date(2026, 5, 19), "Buy", "C", "D3 RIN", "2024",
             date(2026, 5, 25), 50, None, 1.8, None),  # T-1, counted
        ],
    )
    rows = collect_prior_day_trade_exceptions(
        conn, run_id="R1", business_date=date(2026, 5, 20)
    )
    assert len(rows) == 1
    assert rows[0]["payload"]["status"] == "mo_only"
    assert rows[0]["payload"]["counterparty"] == "C"


def test_all_non_matched_statuses_exposed(conn):
    for s in ("price_break", "volume_break", "mo_only", "fo_only"):
        conn.execute(
            "INSERT INTO cross_recon VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ["R1", s, date(2026, 5, 19), "Buy", s, "D3 RIN", "2024",
             date(2026, 5, 25), 1, 2, 3, 4],
        )
    rows = collect_prior_day_trade_exceptions(
        conn, run_id="R1", business_date=date(2026, 5, 20)
    )
    assert sorted(r["payload"]["status"] for r in rows) == [
        "fo_only", "mo_only", "price_break", "volume_break",
    ]
```

- [ ] **Step 2: Run the test — expect ImportError**

Run: `pytest tests/test_check_t1_trades.py -v`
Expected: ERROR.

- [ ] **Step 3: Implement `checks/prior_day_trades.py`**

```python
"""Check 4 — prior-day trade match (T-1) via karbone_recon.cross_recon."""
from __future__ import annotations

from datetime import date, timedelta

import duckdb


def collect_prior_day_trade_exceptions(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    business_date: date,
) -> list[dict]:
    target = business_date - timedelta(days=1)
    df = conn.execute(
        """
        SELECT status, trade_date, side, counterparty_canonical, product_canonical,
               vintage_canonical, delivery_match_date,
               mo_volume, fuels_volume, mo_wap, fuels_wap
        FROM cross_recon
        WHERE run_id = ? AND trade_date = ? AND status <> 'matched'
        """,
        [run_id, target],
    ).df()

    rows: list[dict] = []
    for r in df.to_dict(orient="records"):
        rows.append({
            "check_id": "prior_day_trades",
            "severity": "error",
            "key": {
                "trade_date": r["trade_date"],
                "side": r["side"],
                "counterparty": r["counterparty_canonical"],
                "product": r["product_canonical"],
                "vintage": r["vintage_canonical"],
                "delivery": r["delivery_match_date"],
            },
            "payload": {
                "status": r["status"],
                "trade_date": r["trade_date"],
                "side": r["side"],
                "counterparty": r["counterparty_canonical"],
                "product": r["product_canonical"],
                "vintage": r["vintage_canonical"],
                "delivery": r["delivery_match_date"],
                "mo_volume": r["mo_volume"],
                "fo_volume": r["fuels_volume"],
                "mo_wap": r["mo_wap"],
                "fo_wap": r["fuels_wap"],
            },
        })
    return rows
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `pytest tests/test_check_t1_trades.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/daily_recon/checks/prior_day_trades.py tests/test_check_t1_trades.py
git commit -m "feat(checks): prior-day trade match via cross_recon"
```

---

## Task 11: `mailer.py` — keyring SMTP

Three retries with exponential backoff. Failure modes: missing keyring → raise; SMTP raise after retries → raise. The pipeline catches and handles.

**Files:**
- Create: `src/daily_recon/mailer.py`
- Create: `tests/test_mailer.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mailer.py
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
```

- [ ] **Step 2: Run the test — expect ImportError**

Run: `pytest tests/test_mailer.py -v`
Expected: ERROR.

- [ ] **Step 3: Implement `mailer.py`**

```python
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
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `pytest tests/test_mailer.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/daily_recon/mailer.py tests/test_mailer.py
git commit -m "feat(mailer): keyring-backed SMTP with retry"
```

---

## Task 12: `report/__init__.py` and shared helpers

The report package wraps `karbone_pnl_pos.reporting.html_builder`'s helpers and adds recon-specific modules. The composer assembles a list of section modules (hero, check strip, exception tables, footer) into the final HTML using `_render_document`-style shell.

**Files:**
- Create: `src/daily_recon/report/__init__.py`

- [ ] **Step 1: Write the file**

```python
# src/daily_recon/report/__init__.py
"""Email report composition for daily_recon.

Reuses karbone_pnl_pos.reporting.theme + html_builder low-level helpers
to ensure the recon report is visually consistent with the PnL report.
"""
```

- [ ] **Step 2: Commit**

```bash
git add src/daily_recon/report/__init__.py
git commit -m "feat(report): scaffold report package"
```

---

## Task 13: `report/modules.py` — module renderers

Five renderers: `render_header_band`, `render_hero`, `render_check_strip`, `render_exception_table`, `render_empty_module`. Each returns a fragment compatible with the document shell.

**Files:**
- Create: `src/daily_recon/report/modules.py`
- Create: `tests/test_report_modules.py`
- Create: `tests/fixtures/__init__.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_report_modules.py
from datetime import date

from daily_recon.report.modules import (
    render_check_strip,
    render_empty_module,
    render_exception_table,
    render_header_band,
    render_hero,
)


def test_header_band_contains_kicker_title_and_run_meta():
    html = render_header_band(
        business_date=date(2026, 5, 20),
        run_id="2026-05-20T060000",
        prior_run_date=date(2026, 5, 19),
    )
    assert "Karbone Risk &middot; Operations" in html or "Karbone Risk · Operations" in html
    assert "RINs Reconciliation" in html
    assert "May 20" in html or "2026-05-20" in html
    assert "2026-05-20T060000" in html


def test_hero_zero_exceptions_is_green():
    html = render_hero(total_exceptions=0, total_checks=4, failed_checks=0)
    assert "#15803d" in html
    assert ">0<" in html


def test_hero_with_exceptions_is_red():
    html = render_hero(total_exceptions=7, total_checks=4, failed_checks=2)
    assert "#b91c1c" in html
    assert ">7<" in html
    assert "2 of 4" in html


def test_check_strip_renders_four_cells():
    counts = {
        "trade_drift": 2,
        "historical_position_drift": 0,
        "position_break": 3,
        "prior_day_trades": 2,
    }
    html = render_check_strip(counts)
    assert html.count("<td") >= 4
    assert "Trade Drift" in html
    assert "Position Break" in html
    assert "T-1 Trades" in html
    # Zero-count cell renders the en-dash and uses subtle color.
    assert "#8a97a8" in html


def test_exception_table_truncates_at_max_rows_and_footnotes():
    rows = [{"col_a": f"r{i}", "col_b": i} for i in range(75)]
    cols = [("col_a", "Col A", "text"), ("col_b", "Col B", "num")]
    html = render_exception_table(
        kicker="Test", title="Test table", rows=rows, columns=cols,
        max_rows=50, subtotal_label="Test", subtotal_count=75,
    )
    # 50 data rows expected
    assert html.count("r0") == 1
    assert html.count("r49") == 1
    assert "r50" not in html
    assert "25 more rows" in html
    assert "2px solid #0a2540" in html  # subtotal cap rule


def test_empty_module_renders_centered_message():
    html = render_empty_module(
        kicker="Hist. Position", title="Historical position drift",
        empty_text="No historical position drift detected.",
    )
    assert "No historical position drift detected." in html
```

- [ ] **Step 2: Create `tests/fixtures/__init__.py`**

```python
```

- [ ] **Step 3: Run the test — expect ImportError**

Run: `pytest tests/test_report_modules.py -v`
Expected: ERROR.

- [ ] **Step 4: Implement `report/modules.py`**

```python
"""Section renderers for the daily_recon email report.

Each renderer returns an HTML fragment that fits inside the document shell
provided by karbone_pnl_pos.reporting.html_builder._render_document.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable

from karbone_pnl_pos.reporting.html_builder import (
    _render_module_header,
    _render_subtotal_row,
    _s,
    _table,
    _td,
    _tr,
)
from karbone_pnl_pos.reporting.theme import NUM_FONT, SANS_FONT, THEME as T

_CHECK_LABELS = {
    "trade_drift": "Trade Drift",
    "historical_position_drift": "Hist. Position",
    "position_break": "Position Break",
    "prior_day_trades": "T-1 Trades",
}
_CHECK_ORDER = ["trade_drift", "historical_position_drift", "position_break", "prior_day_trades"]


def _fmt_long_date(d: date) -> str:
    return d.strftime("%A, %B %-d, %Y") if hasattr(d, "strftime") else str(d)


def render_header_band(business_date: date, run_id: str, prior_run_date: date | None) -> str:
    kicker_st = _s(font_size="10px", color=T.muted, text_transform="uppercase",
                   letter_spacing="1.8px", font_weight="700")
    title_st = _s(font_size="15px", font_weight="600", margin_top="4px", letter_spacing="-0.1px")
    hint_st = _s(font_size="10.5px", color=T.subtle, letter_spacing="0.3px")
    try:
        long_date = business_date.strftime("%A, %B %d, %Y")
    except Exception:
        long_date = str(business_date)
    prior_part = f" · prior {prior_run_date.isoformat()}" if prior_run_date else " · no prior run"
    hint = f"run {run_id}{prior_part}"
    left = _td(
        f'<div style="{kicker_st}">Karbone Risk · Operations</div>'
        f'<div style="{title_st}">RINs Reconciliation · {long_date}</div>'
    )
    right = _td(f'<div style="{hint_st}">{hint}</div>',
                _s(text_align="right", vertical_align="bottom"))
    inner = _table(f"<tbody>{_tr(left + right)}</tbody>")
    outer_st = _s(padding="22px 24px 10px", border_top=f"1px solid {T.divider}", background=T.card)
    return f'<tr><td><div style="{outer_st}">{inner}</div></td></tr>'


def render_hero(total_exceptions: int, total_checks: int, failed_checks: int) -> str:
    color = T.neg if total_exceptions > 0 else T.pos
    kicker_st = _s(font_size="11px", color=T.muted, text_transform="uppercase",
                   letter_spacing="2px", margin_bottom="10px")
    hero_st = _s(font_family=NUM_FONT, font_size="52px", font_weight="500",
                 letter_spacing="-1.5px", color=color, line_height="1")
    subtext_st = _s(font_size="13px", color=T.muted, margin_top="8px")
    sub = f"{failed_checks} of {total_checks} checks failed" if total_exceptions else "All checks clean"
    wrapper_st = _s(padding="28px 28px 22px")
    return (
        f'<tr><td style="{wrapper_st}">'
        f'<div style="{kicker_st}">Exceptions · Today</div>'
        f'<div style="{hero_st}">{total_exceptions}</div>'
        f'<div style="{subtext_st}">{sub}</div>'
        f"</td></tr>"
    )


def render_check_strip(counts: dict[str, int]) -> str:
    cells = ""
    n = len(_CHECK_ORDER)
    for i, key in enumerate(_CHECK_ORDER):
        v = counts.get(key, 0)
        color = T.subtle if v == 0 else T.neg
        text = "–" if v == 0 else str(v)
        border_right = f"1px solid {T.divider}" if i < n - 1 else ""
        label_st = _s(font_size="9.5px", color=T.muted, text_transform="uppercase",
                      letter_spacing="1.6px", font_weight="600")
        value_st = _s(font_family=NUM_FONT, font_size="18px", font_weight="500",
                      margin_top="4px", letter_spacing="-0.2px", color=color)
        cell_st = _s(width=f"{100 // n}%", padding="14px 12px",
                     border_right=border_right, text_align="center")
        cells += _td(
            f'<div style="{label_st}">{_CHECK_LABELS[key]}</div>'
            f'<div style="{value_st}">{text}</div>',
            cell_st,
        )
    strip_st = _s(border_top=f"1px solid {T.divider}",
                  border_bottom=f"1px solid {T.divider}", background=T.wash)
    inner = _table(f"<tbody>{_tr(cells)}</tbody>")
    return f'<tr><td style="{strip_st}">{inner}</td></tr>'


def render_exception_table(
    *,
    kicker: str,
    title: str,
    rows: list[dict],
    columns: list[tuple[str, str, str]],  # (key, header_label, kind in {"text","num","status"})
    max_rows: int,
    subtotal_label: str,
    subtotal_count: int,
    hint: str = "",
) -> str:
    header_html = _render_module_header(kicker, title=title, hint=hint)

    hdr_border = f"1px solid {T.divider}"
    header_cells = ""
    for _, label, kind in columns:
        align = "right" if kind == "num" else "left"
        hdr_st = _s(font_size="9.5px", color=T.muted, text_transform="uppercase",
                    letter_spacing="1.4px", font_weight="600",
                    text_align=align, padding="12px 4px 8px",
                    border_bottom=hdr_border)
        header_cells += _td(label, hdr_st)

    visible_rows = rows[:max_rows]
    body_rows = ""
    for i, row in enumerate(visible_rows):
        border_b = f"1px solid {T.divider}" if i < len(visible_rows) - 1 else ""
        cells = ""
        for key, _label, kind in columns:
            v = row.get(key, "")
            if kind == "num":
                st = _s(text_align="right", font_family=NUM_FONT, font_size="12px",
                        padding="11px 4px", border_bottom=border_b, white_space="nowrap")
                cells += _td("" if v is None else str(v), st)
            elif kind == "status":
                st = _s(font_size="11px", font_weight="700", color=T.neg,
                        text_transform="uppercase", letter_spacing="0.6px",
                        padding="11px 4px", border_bottom=border_b)
                cells += _td(str(v), st)
            else:
                st = _s(font_size="12.5px", font_weight="500",
                        padding="11px 4px", border_bottom=border_b)
                cells += _td("" if v is None else str(v), st)
        body_rows += _tr(cells)

    truncated_html = ""
    if len(rows) > max_rows:
        more = len(rows) - max_rows
        note_st = _s(font_size="11px", font_style="italic", color=T.subtle, padding="10px 4px")
        truncated_html = _tr(_td(f"… {more} more rows in attached CSV",
                                 note_st + f"; text-align:left", colspan=str(len(columns))))

    table = _table(f"<tbody>{_tr(header_cells)}{body_rows}{truncated_html}</tbody>", collapse=True)

    col_widths = ["100"] * len(columns)
    subtotal_html = _render_subtotal_row(
        f"{subtotal_label} · {subtotal_count} exceptions",
        [None] * (len(columns) - 1),
        col_widths,
        span_label=1,
    )

    inner_st = _s(padding="0 24px 16px")
    return (f"<tr><td>{header_html}</td></tr>"
            f'<tr><td style="{inner_st}">{table}{subtotal_html}</td></tr>')


def render_empty_module(kicker: str, title: str, empty_text: str, hint: str = "") -> str:
    header_html = _render_module_header(kicker, title=title, hint=hint)
    inner_st = _s(padding="0 24px 22px", text_align="center")
    msg_st = _s(font_size="13px", color=T.muted, padding="18px 0")
    return (f"<tr><td>{header_html}</td></tr>"
            f'<tr><td style="{inner_st}"><div style="{msg_st}">{empty_text}</div></td></tr>')
```

- [ ] **Step 5: Run the test — expect PASS**

Run: `pytest tests/test_report_modules.py -v`
Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add src/daily_recon/report/modules.py tests/test_report_modules.py tests/fixtures/__init__.py
git commit -m "feat(report): module renderers using pnl-pos theme"
```

---

## Task 14: `report/html_compose.py` + `report/text_compose.py`

Top-level composers that take a `ReconReportData` dataclass and produce the final HTML and plain-text bodies.

**Files:**
- Create: `src/daily_recon/report/html_compose.py`
- Create: `src/daily_recon/report/text_compose.py`
- Create: `tests/test_report_compose.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_report_compose.py
from datetime import date

from daily_recon.report.html_compose import ReconReportData, compose_html
from daily_recon.report.text_compose import compose_text


def _data(exceptions):
    return ReconReportData(
        business_date=date(2026, 5, 20),
        run_id="2026-05-20T060000",
        prior_run_date=date(2026, 5, 19),
        mo_leg_count=1284,
        fo_line_count=906,
        counts={
            "trade_drift": sum(1 for e in exceptions if e["check_id"] == "trade_drift"),
            "historical_position_drift": sum(1 for e in exceptions if e["check_id"] == "historical_position_drift"),
            "position_break": sum(1 for e in exceptions if e["check_id"] == "position_break"),
            "prior_day_trades": sum(1 for e in exceptions if e["check_id"] == "prior_day_trades"),
        },
        exceptions=exceptions,
    )


def test_html_zero_exception_path():
    html = compose_html(_data([]))
    assert "<!DOCTYPE html>" in html
    assert "All checks clean" in html


def test_html_with_each_check_section():
    excs = [
        {"check_id": "position_break", "key": {}, "payload": {
            "business_date": "2026-05-19", "product": "D3 RIN", "vintage": "2024",
            "mo_position": 100, "fo_position": 90, "delta": 10,
        }},
        {"check_id": "prior_day_trades", "key": {}, "payload": {
            "status": "price_break", "counterparty": "Mercuria", "side": "Sell",
            "product": "D3 RIN", "vintage": "2024",
            "mo_volume": 100, "fo_volume": 100, "mo_wap": 2.5, "fo_wap": 2.49,
        }},
        {"check_id": "trade_drift", "key": {}, "payload": {
            "source": "mo", "change_type": "modified_trade",
            "counterparty": "Air Liquide", "product": "D3 RIN", "vintage": "2024",
            "prior_volume": 100, "current_volume": 100, "prior_wap": 2.44, "current_wap": 2.45,
        }},
    ]
    html = compose_html(_data(excs))
    # Section kickers appear
    assert "Position Break" in html
    assert "T-1 Trades" in html
    assert "Trade Drift" in html
    # Empty check renders empty state
    assert "No historical position drift" in html
    # Theme tokens present
    assert "#0a2540" in html


def test_text_compose_contains_section_counts():
    excs = [{"check_id": "position_break", "key": {}, "payload": {
        "business_date": "2026-05-19", "product": "D3 RIN", "vintage": "2024",
        "mo_position": 100, "fo_position": 90, "delta": 10,
    }}]
    text = compose_text(_data(excs))
    assert "RINs Reconciliation" in text
    assert "2026-05-20" in text
    assert "Position Break: 1" in text
    assert "Trade Drift: 0" in text


def test_subject_helper():
    from daily_recon.report.html_compose import compose_subject
    assert compose_subject(_data([])) == "[RINs Recon] 2026-05-20 — clean"
    excs = [{"check_id": "position_break", "key": {}, "payload": {}}] * 3
    assert compose_subject(_data(excs)) == "[RINs Recon] 2026-05-20 — 3 exceptions across 1 check"
    excs = (
        [{"check_id": "position_break", "key": {}, "payload": {}}] * 2
        + [{"check_id": "trade_drift", "key": {}, "payload": {}}]
    )
    assert compose_subject(_data(excs)) == "[RINs Recon] 2026-05-20 — 3 exceptions across 2 checks"
```

- [ ] **Step 2: Run the test — expect ImportError**

Run: `pytest tests/test_report_compose.py -v`
Expected: ERROR.

- [ ] **Step 3: Implement `report/html_compose.py`**

```python
"""Top-level HTML composer for the daily_recon report."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from karbone_pnl_pos.reporting.html_builder import _render_document
from karbone_pnl_pos.reporting.theme import SANS_FONT, THEME as T

from daily_recon.config import MAX_TABLE_ROWS_IN_EMAIL
from daily_recon.report.modules import (
    render_check_strip,
    render_empty_module,
    render_exception_table,
    render_header_band,
    render_hero,
)


@dataclass
class ReconReportData:
    business_date: date
    run_id: str
    prior_run_date: Optional[date]
    mo_leg_count: int
    fo_line_count: int
    counts: dict[str, int]
    exceptions: list[dict] = field(default_factory=list)


def compose_subject(data: ReconReportData) -> str:
    total = sum(data.counts.values())
    if total == 0:
        return f"[RINs Recon] {data.business_date.isoformat()} — clean"
    failed = sum(1 for v in data.counts.values() if v > 0)
    noun = "check" if failed == 1 else "checks"
    return f"[RINs Recon] {data.business_date.isoformat()} — {total} exceptions across {failed} {noun}"


def _exceptions_of(data: ReconReportData, check_id: str) -> list[dict]:
    return [e["payload"] for e in data.exceptions if e["check_id"] == check_id]


def _section_position_break(data: ReconReportData) -> str:
    rows = _exceptions_of(data, "position_break")
    if not rows:
        return render_empty_module(
            kicker="Position Break",
            title="MO vs FO running position · all dates",
            empty_text="No MO vs FO position breaks detected.",
            hint="tolerance 1e-6",
        )
    cols = [
        ("business_date", "Date", "text"),
        ("product", "Product", "text"),
        ("vintage", "Vintage", "text"),
        ("mo_position", "MO", "num"),
        ("fo_position", "FO", "num"),
        ("delta", "Δ", "num"),
    ]
    return render_exception_table(
        kicker="Position Break",
        title="MO vs FO running position · all dates",
        rows=rows, columns=cols,
        max_rows=MAX_TABLE_ROWS_IN_EMAIL,
        subtotal_label="Position Break",
        subtotal_count=len(rows),
        hint="tolerance 1e-6",
    )


def _section_prior_day_trades(data: ReconReportData) -> str:
    rows = _exceptions_of(data, "prior_day_trades")
    if not rows:
        return render_empty_module(
            kicker="T-1 Trades",
            title="Prior-day trade match",
            empty_text="No prior-day trade breaks detected.",
        )
    cols = [
        ("counterparty", "Counterparty", "text"),
        ("side", "Side", "text"),
        ("product", "Product", "text"),
        ("vintage", "Vint.", "text"),
        ("status", "Status", "status"),
        ("mo_volume", "MO vol", "num"),
        ("fo_volume", "FO vol", "num"),
    ]
    return render_exception_table(
        kicker="T-1 Trades",
        title="Prior-day trade match",
        rows=rows, columns=cols,
        max_rows=MAX_TABLE_ROWS_IN_EMAIL,
        subtotal_label="T-1 Trades",
        subtotal_count=len(rows),
    )


def _section_trade_drift(data: ReconReportData) -> str:
    rows = _exceptions_of(data, "trade_drift")
    if not rows:
        return render_empty_module(
            kicker="Trade Drift",
            title="Changes since prior run · per system",
            empty_text="No trade drift detected since the prior run.",
            hint="new_trade suppressed",
        )
    cols = [
        ("source", "Source", "text"),
        ("counterparty", "Counterparty", "text"),
        ("change_type", "Change", "status"),
        ("product", "Product", "text"),
        ("vintage", "Vintage", "text"),
        ("prior_volume", "Prior vol", "num"),
        ("current_volume", "Current vol", "num"),
    ]
    return render_exception_table(
        kicker="Trade Drift",
        title="Changes since prior run · per system",
        rows=rows, columns=cols,
        max_rows=MAX_TABLE_ROWS_IN_EMAIL,
        subtotal_label="Trade Drift",
        subtotal_count=len(rows),
        hint="new_trade suppressed",
    )


def _section_historical_position(data: ReconReportData) -> str:
    rows = _exceptions_of(data, "historical_position_drift")
    if not rows:
        return render_empty_module(
            kicker="Hist. Position",
            title="Historical position drift · dates < T-1",
            empty_text="No historical position drift detected.",
            hint="tolerance 1e-6",
        )
    cols = [
        ("source", "Source", "text"),
        ("business_date", "Date", "text"),
        ("product", "Product", "text"),
        ("vintage", "Vintage", "text"),
        ("prior_position", "Prior", "num"),
        ("current_position", "Current", "num"),
        ("delta", "Δ", "num"),
    ]
    return render_exception_table(
        kicker="Hist. Position",
        title="Historical position drift · dates < T-1",
        rows=rows, columns=cols,
        max_rows=MAX_TABLE_ROWS_IN_EMAIL,
        subtotal_label="Hist. Position",
        subtotal_count=len(rows),
        hint="tolerance 1e-6",
    )


def _footer() -> str:
    from karbone_pnl_pos.reporting.html_builder import _s
    foot_st = _s(border_top=f"1px solid {T.divider}", background=T.wash,
                 padding="14px 24px")
    text_st = _s(font_size="10.5px", color=T.subtle, letter_spacing="0.3px")
    return (f'<tr><td style="{foot_st}">'
            f'<div style="{text_st}">Karbone Risk · Daily Reconciliation</div>'
            f"</td></tr>")


def compose_html(data: ReconReportData) -> str:
    rows = (
        render_header_band(data.business_date, data.run_id, data.prior_run_date)
        + render_hero(
            total_exceptions=sum(data.counts.values()),
            total_checks=len(data.counts),
            failed_checks=sum(1 for v in data.counts.values() if v > 0),
        )
        + render_check_strip(data.counts)
        + _section_position_break(data)
        + _section_prior_day_trades(data)
        + _section_trade_drift(data)
        + _section_historical_position(data)
        + _footer()
    )
    return _render_document(rows)
```

- [ ] **Step 4: Implement `report/text_compose.py`**

```python
"""Plain-text fallback composer."""
from __future__ import annotations

from daily_recon.report.html_compose import ReconReportData

_CHECK_LABELS = {
    "trade_drift": "Trade Drift",
    "historical_position_drift": "Historical Position Drift",
    "position_break": "Position Break",
    "prior_day_trades": "T-1 Trades",
}
_ORDER = ["position_break", "prior_day_trades", "trade_drift", "historical_position_drift"]


def compose_text(data: ReconReportData) -> str:
    total = sum(data.counts.values())
    lines = [
        "Karbone Risk · Operations",
        f"RINs Reconciliation · {data.business_date.isoformat()}",
        f"Run: {data.run_id}  Prior: {data.prior_run_date or 'none'}",
        "",
        f"Total exceptions: {total}",
        "",
        "Counts by check:",
    ]
    for k in _ORDER:
        lines.append(f"  {_CHECK_LABELS[k]}: {data.counts.get(k, 0)}")
    lines.append("")
    if total == 0:
        lines.append("All checks clean.")
    else:
        lines.append("See attached CSVs for full exception detail.")
    return "\n".join(lines)
```

- [ ] **Step 5: Run the test — expect PASS**

Run: `pytest tests/test_report_compose.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/daily_recon/report/html_compose.py src/daily_recon/report/text_compose.py tests/test_report_compose.py
git commit -m "feat(report): compose HTML and text bodies"
```

---

## Task 15: `pipeline.py` — orchestrator

Wires everything together. Designed so tests can inject a `Mailer` (interface = `.send(EmailMessage)`).

**Files:**
- Create: `src/daily_recon/pipeline.py`

- [ ] **Step 1: Implement `pipeline.py`**

```python
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
    archive→ingest→stage→audit→reconcile sequence and returns the new run_id.
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
            from daily_recon.pipeline import default_incoming_runner
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
    """Run karbone_recon's archive→ingest→stage→audit→reconcile chain.

    Returns the upstream run_id corresponding to the staged data.
    """
    from karbone_recon import archive, audit, db, ingest, reconcile, stage  # noqa: F401

    # Use karbone_recon's own connection-side schema setup.
    db.create_schema(conn)
    upstream_run_id = ingest.run_ingest(conn, business_date)
    stage.run_stage(conn, upstream_run_id)
    audit.run_audit(conn, upstream_run_id)
    reconcile.reconcile_cross_system(conn, upstream_run_id)
    return upstream_run_id
```

- [ ] **Step 2: Sanity-check imports compile**

Run: `python -c "from daily_recon import pipeline"`
Expected: no output, exit 0.

(Integration coverage of this module is Task 16. No unit test for pipeline.py itself — it's wiring.)

- [ ] **Step 3: Commit**

```bash
git add src/daily_recon/pipeline.py
git commit -m "feat(pipeline): orchestrate ingest, checks, report, send"
```

---

## Task 16: Integration test against a tiny fixture pair

The fixtures simulate two consecutive runs over the same MO + FO data with known breaks injected on day 2. The test uses a fake `incoming_runner` that loads the fixtures directly into `mo_legs` / `fuels_lines` / drift / cross-recon tables — keeping us decoupled from karbone_recon's internal staging code while still exercising every daily_recon code path.

**Files:**
- Create: `tests/fixtures/day1.py`
- Create: `tests/fixtures/day2.py`
- Create: `tests/test_pipeline_integration.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline_integration.py
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path

import duckdb
import pytest

from daily_recon import config
from daily_recon.pipeline import run_pipeline


class RecordingMailer:
    def __init__(self):
        self.sent: list[EmailMessage] = []

    def send(self, msg: EmailMessage) -> None:
        self.sent.append(msg)


def _create_upstream_tables(conn):
    conn.execute("""
        CREATE TABLE mo_legs (
            run_id TEXT, trade_date DATE, side TEXT, counterparty_canonical TEXT,
            product_canonical TEXT, vintage_canonical TEXT,
            quantity DOUBLE, price DOUBLE, delivery_match_date DATE
        )
    """)
    conn.execute("""
        CREATE TABLE fuels_lines (
            run_id TEXT, trade_date DATE, side TEXT, counterparty_canonical TEXT,
            product_canonical TEXT, vintage_canonical TEXT,
            quantity DOUBLE, price DOUBLE, delivery_match_date DATE
        )
    """)
    conn.execute("""
        CREATE TABLE mo_trade_drift (
            run_id TEXT, trade_date DATE, side TEXT, counterparty_canonical TEXT,
            product_canonical TEXT, vintage_canonical TEXT,
            change_type TEXT, prior_volume DOUBLE, current_volume DOUBLE,
            prior_wap DOUBLE, current_wap DOUBLE
        )
    """)
    conn.execute("""
        CREATE TABLE fuels_trade_drift (
            run_id TEXT, trade_date DATE, side TEXT, counterparty_canonical TEXT,
            product_canonical TEXT, vintage_canonical TEXT,
            change_type TEXT, prior_volume DOUBLE, current_volume DOUBLE,
            prior_wap DOUBLE, current_wap DOUBLE
        )
    """)
    conn.execute("""
        CREATE TABLE cross_recon (
            run_id TEXT, status TEXT, trade_date DATE, side TEXT,
            counterparty_canonical TEXT, product_canonical TEXT,
            vintage_canonical TEXT, delivery_match_date DATE,
            mo_volume DOUBLE, fuels_volume DOUBLE,
            mo_wap DOUBLE, fuels_wap DOUBLE
        )
    """)


def _seed_day1(conn, run_id):
    # MO has a buy-leg of 100 of D3 RIN 2024 on 2026-05-18.
    conn.execute(
        "INSERT INTO mo_legs VALUES (?, ?, 'Buy', 'Air Liquide', 'D3 RIN', '2024', 100, 2.5, '2026-05-25')",
        [run_id, date(2026, 5, 18)],
    )
    conn.execute(
        "INSERT INTO fuels_lines VALUES (?, ?, 'Buy', 'Air Liquide', 'D3 RIN', '2024', 100, 2.5, '2026-05-25')",
        [run_id, date(2026, 5, 18)],
    )
    conn.execute(
        "INSERT INTO cross_recon VALUES (?, 'matched', ?, 'Buy', 'Air Liquide', 'D3 RIN', '2024', '2026-05-25', 100, 100, 2.5, 2.5)",
        [run_id, date(2026, 5, 18)],
    )


def _seed_day2(conn, run_id):
    # MO: same 2026-05-18 buy of 100 PLUS a new T-1 sell of 50 (D3 RIN 2024).
    # FO: same 2026-05-18 buy of 100 PLUS the same T-1 sell of 50 but at a DIFFERENT price → price_break.
    # Also: a backdated MO modification on 2026-05-18 — qty changes 100 → 120 → historical_position_drift.
    conn.execute(
        "INSERT INTO mo_legs VALUES (?, ?, 'Buy', 'Air Liquide', 'D3 RIN', '2024', 120, 2.5, '2026-05-25')",
        [run_id, date(2026, 5, 18)],
    )
    conn.execute(
        "INSERT INTO mo_legs VALUES (?, ?, 'Sell', 'Mercuria', 'D3 RIN', '2024', -50, 2.6, '2026-05-25')",
        [run_id, date(2026, 5, 19)],
    )
    conn.execute(
        "INSERT INTO fuels_lines VALUES (?, ?, 'Buy', 'Air Liquide', 'D3 RIN', '2024', 100, 2.5, '2026-05-25')",
        [run_id, date(2026, 5, 18)],
    )
    conn.execute(
        "INSERT INTO fuels_lines VALUES (?, ?, 'Sell', 'Mercuria', 'D3 RIN', '2024', -50, 2.55, '2026-05-25')",
        [run_id, date(2026, 5, 19)],
    )
    # Cross-recon: the T-1 sell mismatches on price.
    conn.execute(
        "INSERT INTO cross_recon VALUES (?, 'matched', ?, 'Buy', 'Air Liquide', 'D3 RIN', '2024', '2026-05-25', 120, 100, 2.5, 2.5)",
        [run_id, date(2026, 5, 18)],
    )
    conn.execute(
        "INSERT INTO cross_recon VALUES (?, 'price_break', ?, 'Sell', 'Mercuria', 'D3 RIN', '2024', '2026-05-25', -50, -50, 2.6, 2.55)",
        [run_id, date(2026, 5, 19)],
    )
    # Drift: MO has a modified_trade on 2026-05-18 (qty 100→120).
    conn.execute(
        "INSERT INTO mo_trade_drift VALUES (?, ?, 'Buy', 'Air Liquide', 'D3 RIN', '2024', 'modified_trade', 100, 120, 2.5, 2.5)",
        [run_id, date(2026, 5, 18)],
    )


@pytest.fixture
def isolated_paths(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_ROOT", tmp_path / "output")
    monkeypatch.setattr(config, "DUCKDB_PATH", tmp_path / "recon.duckdb")
    config.OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_full_pipeline_day1_then_day2(isolated_paths):
    db_path = config.DUCKDB_PATH

    # ── Day 1 ────────────────────────────────────────────────────────────
    conn1 = duckdb.connect(str(db_path))
    _create_upstream_tables(conn1)

    def runner_day1(conn, business_date):
        rid = "U-day1"
        _seed_day1(conn, rid)
        return rid

    mailer1 = RecordingMailer()
    result1 = run_pipeline(
        business_date=date(2026, 5, 19),
        conn=conn1, mailer=mailer1, send_email=True,
        incoming_runner=runner_day1,
    )
    conn1.close()
    assert result1.status == "success"
    assert result1.exception_count == 0
    assert len(mailer1.sent) == 1
    assert "clean" in mailer1.sent[0]["Subject"]

    # ── Day 2 ────────────────────────────────────────────────────────────
    conn2 = duckdb.connect(str(db_path))

    def runner_day2(conn, business_date):
        rid = "U-day2"
        _seed_day2(conn, rid)
        return rid

    mailer2 = RecordingMailer()
    result2 = run_pipeline(
        business_date=date(2026, 5, 20),
        conn=conn2, mailer=mailer2, send_email=True,
        incoming_runner=runner_day2,
    )
    conn2.close()

    assert result2.status == "success"
    # Expect: 1 trade_drift + 1 historical_position_drift + 0 position_break + 1 prior_day_trades
    assert result2.counts["trade_drift"] == 1
    assert result2.counts["historical_position_drift"] == 1
    assert result2.counts["position_break"] == 0
    assert result2.counts["prior_day_trades"] == 1
    assert result2.exception_count == 3

    msg = mailer2.sent[0]
    assert "3 exceptions across 3 checks" in msg["Subject"]
    html = msg.get_payload()[1].get_content()
    assert "Air Liquide" in html
    assert "Mercuria" in html
    assert "price_break" in html or "PRICE_BREAK" in html

    out = result2.output_dir
    assert (out / "summary.json").exists()
    assert (out / "trade_drift.csv").read_text().count("\n") >= 1
    assert (out / "prior_day_trade_breaks.csv").read_text().count("\n") >= 1
```

- [ ] **Step 2: Run the test — expect failures**

Run: `pytest tests/test_pipeline_integration.py -v`
Expected: FAIL (path issues, edge cases). Fix until green. Common fixes:
- `_legs_to_positions` reading `trade_date` column — make sure the test seed columns match.
- `pos_runs.business_date` foreign-key absence — none in this schema, should be fine.

- [ ] **Step 3: Run full test suite**

Run: `pytest -v`
Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_pipeline_integration.py
git commit -m "test(pipeline): two-day integration covering all four checks"
```

---

## Task 17: `daily_run.py` — entrypoint with CLI flags

**Files:**
- Create: `daily_run.py`

- [ ] **Step 1: Implement `daily_run.py`**

```python
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
        print(f"Run {result.run_id}: {result.exception_count} exceptions → {result.output_dir}")
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
```

- [ ] **Step 2: Smoke-check it parses args**

Run: `python daily_run.py --help`
Expected: argparse help output, exit 0.

- [ ] **Step 3: Commit**

```bash
git add daily_run.py
git commit -m "feat(cli): daily_run entrypoint with --date and --no-email"
```

---

## Task 18: README + Task Scheduler setup notes

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write the README**

```markdown
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
2. Trigger: Daily 06:00, recur every 1 day; under Advanced Settings → Repeat → days Monday–Saturday (or set Trigger: Weekly on Mon–Sat)
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
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: setup, daily run, scheduling, exit codes"
```

---

## Final Step: Full suite verification

- [ ] **Step 1: Run the entire test suite**

Run: `pytest -v --tb=short`
Expected: all tests pass.

- [ ] **Step 2: Smoke-import everything from a fresh Python**

```powershell
python -c "from daily_recon.pipeline import run_pipeline; from daily_recon.mailer import KeyringSMTPMailer; from daily_recon.report.html_compose import compose_html; print('OK')"
```

Expected: `OK`.

- [ ] **Step 3: Confirm git log is clean and linear**

Run: `git log --oneline`
Expected: one commit per task, no merge commits.

---

## Self-Review (run after writing this plan)

**Spec coverage:**

- §2 deterministic pipeline → Tasks 5–10, 15 ✓
- §3 outputs (CSVs + summary.json + DuckDB tables) → Task 15 (`_write_csv` + `summary.json`) ✓
- §4.1 project layout → Tasks 1, 2, 3, 4, 5, 6, 7, 11, 12, 13, 14, 15, 17 ✓
- §4.3 reused code → Tasks 2, 3, 15 (incoming_runner) ✓
- §5 data flow → Task 15 ✓
- §6 four checks → Tasks 7, 8, 9, 10 ✓
- §7 business-date logic (T-1 boundary, prior-run lookup) → Tasks 8, 10, 15 (uses `latest_successful_run_id`) ✓
- §8 email report (subject, sections, attachments, visual identity) → Tasks 12, 13, 14, 15 ✓
- §9 keyring SMTP + retry → Task 11 ✓
- §10 scheduling → Task 18 ✓
- §11 error handling (exit codes) → Task 17 (matches the spec's exit-code table) ✓
- §12 configuration → Task 4 ✓
- §13 testing — unit + integration → Tasks 4–14 (unit) and 16 (integration) ✓

**Placeholder scan:** No `TBD`, no "add error handling", no "similar to Task N" — every step has the actual code.

**Type consistency check:**
- `PosRunRecord` defined in Task 5, used in Tasks 8, 9, 15 — same field names ✓
- `Mailer` Protocol defined in Task 15 — `RecordingMailer.send` in Task 16 matches its signature ✓
- `compute_running_position` columns (`business_date, product_canonical, vintage_canonical, position`) — consumed in Task 15's `_legs_to_positions` and stored via `insert_running_positions` defined in Task 5 ✓
- `ReconReportData` fields (`counts`, `exceptions`, etc.) — used in Tasks 14 and 15 with the same names ✓
- Check IDs (`trade_drift`, `historical_position_drift`, `position_break`, `prior_day_trades`) — consistent across Tasks 7–10, 13, 14, 15 ✓
