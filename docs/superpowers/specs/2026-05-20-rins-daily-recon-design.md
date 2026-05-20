# RINs Daily Reconciliation — Design

**Date:** 2026-05-20
**Author:** Peter Vouthas
**Status:** Draft for review

## 1. Purpose

Replace the daily manual reconciliation between the Front Office (FO) Fuels tradesheet and the Mid Office (MO) RINs tradesheet with a scheduled, deterministic pipeline that runs Monday–Saturday at 06:00 and emails a single report of breaks. The RINs product line is the proof of concept; the design must be reusable for other product/desk reconciliations later.

## 2. Goals and Non-Goals

**Goals**

- Deterministic, repeatable run: identical inputs produce identical outputs.
- One email per day summarizing all break categories with drill-down CSV attachments.
- Reuse existing code in `recon/` and `pnl-pos/` instead of reimplementing staging, position calculation, or SMTP.
- Fail loudly: failures surface via a clearly-labeled failure email and a non-zero exit code.

**Non-Goals (for v1)**

- LLM enrichment of the report. Will be evaluated after we see live output. Spec must not block that future addition.
- Other desks/products (Power, LCFS, OCFP, etc.). Code must be structured so they can be added without rewriting the orchestrator.
- Web UI or dashboard. Output is email + CSV files on disk only.
- Automatic resolution of breaks. We surface them; humans resolve them.

## 3. Inputs and Outputs

### Inputs (in `data/incoming/` at run time)

- One **MO** CSV identified by a `Trade ID` header. Format matches `rins_tradesheet_no_rng.csv`: dual-leg rows (seller columns + buyer columns on one line), with a `Position Type` column whose `Position` rows must be filtered out.
- One **FO** CSV identified by a `Trade Number` header. Format matches `Fuels_Tradesheet.csv`: flat one-leg-per-row with a `Buy/Sell` column.
- Reference data in `data/counterparty_mapping.csv` (carried over from `recon/`): `Legal Name`, `Tradesheet Vendor Name`, `Tradesheet Customer Name`, `Fuels TS Name`.

### Outputs

- Email to `peter.vouthas@karbone.com`. Recipient list is configurable.
- `output/<YYYY-MM-DD>/` directory with:
  - `summary.json` — counts per check, run metadata.
  - `historical_position_drift.csv` — exceptions from Check 2.
  - `position_breaks.csv` — exceptions from Check 3.
  - `prior_day_trade_breaks.csv` — exceptions from Check 4.
  - `trade_drift.csv` — exceptions from Check 1.
  - `mo_running_position.csv`, `fo_running_position.csv` — full position series for audit.
- DuckDB tables (in the same `recon.duckdb`, prefixed `pos_*` to avoid colliding with `recon/`'s tables):
  - `pos_runs` — one row per run, with `run_id`, `business_date`, `started_at`, `finished_at`, `status`.
  - `pos_running_position` — `(run_id, source, business_date, product_canonical, vintage_canonical, position)`.
  - `pos_exceptions` — `(run_id, check_id, severity, key_json, payload_json)`.

## 4. Architecture

### 4.1 Project layout

```
claude-daily-recon/
├── daily_run.py            # thin entrypoint, no logic
├── pyproject.toml          # declares deps + vendored recon/pnl-pos as packages
├── src/daily_recon/
│   ├── pipeline.py         # orchestrator
│   ├── positions.py        # build running position from leg-level data
│   ├── checks.py           # the 4 deterministic checks
│   ├── report.py           # HTML + text email composition
│   ├── mailer.py           # SMTP via keyring
│   ├── persistence.py      # pos_* DuckDB tables and writers
│   └── config.py           # paths, tolerances, recipient list
├── data/
│   ├── incoming/, archive/
│   ├── rins_tradesheet.csv, fuels_tradesheet.csv  # active files
│   └── counterparty_mapping.csv
├── output/<YYYY-MM-DD>/
├── recon.duckdb
├── vendor/                 # vendored copies of the two existing projects
│   ├── recon/              # imported as `karbone_recon`
│   └── pnl_pos/            # imported as `karbone_pnl_pos`
└── tests/
```

### 4.2 Dependencies

- `pandas`, `duckdb` (already required by `recon/`).
- `keyring` for SMTP credential retrieval.
- `pytest` for tests.

### 4.3 Reused code

| From         | Used for                                                              |
|--------------|-----------------------------------------------------------------------|
| `recon/src/archive.py`   | validate incoming → archive → promote                     |
| `recon/src/ingest.py`    | MO/FO CSV loading                                         |
| `recon/src/stage.py`     | MO leg expansion (the "vectorize MO" step) + FO normalization |
| `recon/src/audit.py`     | Phase-1 trade-level drift (Check 1)                       |
| `recon/src/reconcile.py` | Phase-2 cross-system match, filtered to T-1 (Check 4)     |
| `recon/src/mappings.py`  | counterparty + vintage canonicalization                   |
| `pnl-pos/src/core/calculate_pos.py` | reference algorithm for running positions      |

Positions are recomputed from `mo_legs` and `fuels_lines` (not via the `pnl-pos` converters), because we want the source-of-truth to be the same staged tables that the trade-level checks use.

## 5. Data Flow

```
incoming/MO.csv, incoming/FO.csv
        │
        ▼
[archive + promote]            (reuse recon)
        │
        ▼
[ingest + stage]               (reuse recon: mo_legs, fuels_lines)
        │
        ├──► [Check 1: trade drift since last run]   (reuse recon audit)
        │
        ▼
[build running position]       (positions.py — NEW)
   pos_running_position(source, date, product, vintage, position)
        │
        ├──► [Check 2: historical-position drift vs prior run]
        ├──► [Check 3: MO vs FO position equality across all dates]
        │
        ▼
[Phase-2 cross-recon, filtered to T-1]   (reuse recon reconcile)
        │
        └──► [Check 4: prior-day trade match]
        │
        ▼
[persist exceptions to pos_exceptions]
        │
        ▼
[compose HTML + text report]   (report.py)
        │
        ▼
[send via keyring SMTP]        (mailer.py)
```

## 6. The Four Checks

All comparisons use tolerance `TOL = 1e-6` for both volume and price (matches `recon`'s existing convention).

### Check 1 — Trade-level drift since last run

Pass-through of `recon`'s Phase-1 drift on both MO and FO independently. Output classifications: `added_trade`, `removed_trade`, `modified_trade`. `new_trade` (a trade on today's run with a new `trade_date` that wasn't present before) is informational, not an exception.

### Check 2 — Historical position drift

For each `(source, product_canonical, vintage_canonical)` series, compare today's running position at every date `d < T-1` against the prior run's running position at the same `d`. Any `abs(today - prior) > TOL` → one row in `historical_position_drift.csv` with `(source, product, vintage, date, prior_position, today_position, delta)`.

Rationale: a change to a past-day position means a backdated trade was added/removed/modified or a mapping changed. Either way, ops needs to know.

### Check 3 — MO vs FO position equality

Full outer join of MO and FO running positions on `(business_date, product_canonical, vintage_canonical)`. For every `d ≤ T`, any `abs(mo_position - fo_position) > TOL` → one row in `position_breaks.csv`. Missing series on one side is treated as zero on that side.

### Check 4 — Prior-day trade match

Reuse `recon.reconcile` cross-system matching, restricted to `trade_date = T-1`, on the bucket key `(side, counterparty_canonical, product_canonical, vintage_canonical, delivery_match_date)` with volume and WAP comparison. Statuses `volume_break`, `price_break`, `mo_only`, `fo_only` → exception rows in `prior_day_trade_breaks.csv`. `matched` is suppressed.

## 7. Business Date Logic

- `T` = the run's business date, defaulting to today. CLI flag `--date YYYY-MM-DD` overrides for backfills.
- `T-1` = the **previous business day** as defined by NYSE trading calendar (use `pnl-pos`'s `trading_calendar.py` if exposed; otherwise a simple weekday subtraction with hardcoded US federal holidays loaded once at startup).
- "Prior run" = the most recent row in `pos_runs` with `status = 'success'` and `run_id != current`. This may not be yesterday's run — if the pipeline didn't run on Sunday, Monday compares against Saturday.
- If no prior run exists (first ever run), Check 2 emits zero exceptions and the email notes "first run — no historical drift baseline available."

## 8. Email Report

### 8.0 Visual identity

The recon report must be visually consistent with the existing PnL report so the two read as products of the same shop. Reuse `pnl-pos/src/reporting/theme.py` (`THEME`, `NUM_FONT`, `SANS_FONT`) **by import** — do not duplicate the palette. Reuse the low-level helpers from `pnl-pos/src/reporting/html_builder.py` (`_s`, `_td`, `_tr`, `_table`, `_render_module_header`, `_render_subtotal_row`, `_render_document`) so module headers, subtotal rules, kickers, and the document shell are pixel-identical to the PnL report.

**Locked-in visual elements:**

- **Colors:** Use `THEME.accent` (`#0a2540`) for kickers/section accents and the subtotal top rule. Use `THEME.pos`/`THEME.neg` for severity coloring on numeric deltas, *not* for "exception count" (which is informational, not directional). Page bg `THEME.bg`, card `THEME.card`, divider `THEME.divider`. No new colors introduced.
- **Severity coloring:** A break is reported as a negative delta (`THEME.neg`) when MO < FO and as a positive delta (`THEME.pos`) when MO > FO, mirroring the existing red/green sign convention. Zero-delta historical entries are suppressed entirely; near-zero ties go to `THEME.subtle`.
- **Type:** `SANS_FONT` for everything except numeric cells (volumes, prices, deltas, dates expressed as ISO), which use `NUM_FONT`. Kickers are 10–11px uppercase with `letter-spacing` 1.4–2.0px and `font-weight:700`, matching the PnL kicker style.
- **Layout:** 720px max-width white card on `THEME.bg`, centered, no shadow, `1px solid THEME.divider` border. Module pattern (kicker → title → table) lifted directly from `_render_module_header`. Tables use `border-collapse:collapse` with `border-bottom` on `<td>` elements (Outlook-safe). Padding cadence: `28px 28px 22px` for hero, `0 24px 16px` for module bodies, `11px 4px` for table cells.
- **Header band:** Match the PnL header convention — kicker `"Karbone Risk · Operations"` over the title `"RINs Reconciliation · {long date}"`, with the run id in the right-aligned hint slot.
- **Hero block:** Replace the PnL "+$X" hero with a count-based hero: the total exception count in `NUM_FONT` 52px, weight 500, with `letter-spacing:-1.5px`. Color is `THEME.neg` when > 0, `THEME.pos` when 0, `THEME.accent` otherwise (never grey — this is the headline). Subtext: `"N exceptions across M checks · prior run YYYY-MM-DD"`.
- **Check strip:** Equivalent to the PnL "period strip" — five equal-width cells, one per check, with the count in `NUM_FONT` 18px and a tiny uppercase kicker label below. Cells separated by `1px solid THEME.divider` vertical rules. Background `THEME.wash`. A check with zero exceptions renders the count in `THEME.subtle` and the dash glyph `–`.
- **Exception tables:** Same column header treatment as PnL data tables (9.5px uppercase, 1.4px letter-spacing, muted color, bottom border). Numeric columns right-aligned in `NUM_FONT`. Severity-colored deltas. Each section truncates at `MAX_TABLE_ROWS_IN_EMAIL` and footnotes `"… N more rows in attached CSV"` in `THEME.subtle` 11px italic.
- **Subtotal rows:** Use `_render_subtotal_row` from PnL — tinted `THEME.total_bg` strip capped by `2px solid THEME.total_rule`. Each module that has a count gets a subtotal row showing `"{Check name} · N exceptions"`.
- **Empty state:** When the whole report has zero exceptions, the body collapses to: header band, the hero (showing `0` in `THEME.pos`), the check strip (all dashes), and a single centered line `"All checks clean."` in `SANS_FONT` 13px `THEME.muted`. No tables rendered.

**What we are NOT doing:** no logos, no images other than (potentially) a future sparkline strip showing exception counts over the prior 30 runs. No charts in v1. No colored cell backgrounds in data tables — borders only, matching PnL.

### 8.1 Subject

- Normal: `[RINs Recon] YYYY-MM-DD — N exceptions across M checks`
- Zero exceptions: `[RINs Recon] YYYY-MM-DD — clean`
- Failure: `[RINs Recon] YYYY-MM-DD — FAILED: <one-line reason>`

### 8.2 Body sections (HTML, in this order)

1. **Run header** — business date, run id, prior-run reference date, total exception count.
2. **Summary table** — one row per check with count of exceptions.
3. **Historical position drift** — table of Check 2 rows (truncated at 50, full set in CSV).
4. **MO vs FO position breaks** — table of Check 3 rows (truncated at 50).
5. **Prior-day trade breaks** — table of Check 4 rows (truncated at 50).
6. **Trade drift since last run** — table of Check 1 rows (truncated at 50).
7. **Unmapped counterparties** — list of raw names that didn't map to a Legal Name in either source (passthrough from `recon`).

A plain-text version with the same content (minus tables formatted as fixed-width) is included as a fallback MIME part.

Attachments: all CSVs from `output/<YYYY-MM-DD>/`, even the ones with zero rows (for downstream tooling).

## 9. Credentials and Mailer

- Username: `peter.vouthas@karbone.com` (hardcoded sender; configurable in `config.py` if it ever changes).
- Password: `keyring.get_password('karbone_recon_smtp', 'peter.vouthas@karbone.com')`. Failure to retrieve → fail the run with a clear error.
- SMTP host/port/TLS settings: live in `config.py` as constants. Need user input on actual host/port — defaulting to Microsoft 365 (`smtp.office365.com:587`, STARTTLS) since the email domain is Karbone.com. **Confirm.**
- Failure mode: 3 SMTP send retries with exponential backoff (2s, 8s, 32s). After the third failure, write `output/<date>/email_send_failed.txt` with the traceback and exit non-zero.

## 10. Scheduling

Windows Task Scheduler, on the host where Peter's keyring is available (his workstation or a service account with the credential pre-stored).

- Trigger: daily 06:00 local.
- Days: Monday through Saturday. Sunday is skipped by the trigger, not by the code.
- Action: `python "G:\Shared drives\KarboneRisk\Development\claude-daily-recon\daily_run.py"`.
- "Run whether user is logged on or not" with stored credentials.
- Task Scheduler history is the sole observability channel; the failure email is the user-facing channel.

The code does **not** itself check the day of week — re-runs and manual triggers are always permitted.

## 11. Error Handling

| Failure                                  | Action                                                                                   |
|------------------------------------------|------------------------------------------------------------------------------------------|
| `data/incoming/` empty or wrong shape    | Send FAILED email with reason. Do not archive or modify active files. Exit 2.            |
| Stage / ingest exception                 | Send FAILED email with traceback. Exit 3.                                                |
| Check raises                             | Log, skip the check, continue with the others, mark the check `errored` in the summary.  |
| Keyring lookup returns `None`            | Cannot send email. Write `output/<date>/email_send_failed.txt` with the reason. Exit 4. Task Scheduler history is the failure surface here. |
| SMTP send fails after retries            | Write `email_send_failed.txt`. Exit 5.                                                   |
| Any uncaught exception in `daily_run.py` | Top-level handler writes `crash.log`, attempts a minimal FAILED email, exits 1.          |

Exit codes are distinct so Task Scheduler history shows what went wrong.

## 12. Configuration

`src/daily_recon/config.py` holds all tunables as module constants:

- `TOLERANCE = 1e-6`
- `EMAIL_RECIPIENTS = ["peter.vouthas@karbone.com"]`
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_STARTTLS`
- `KEYRING_SERVICE = "karbone_recon_smtp"`
- `KEYRING_USERNAME = "peter.vouthas@karbone.com"`
- `MAX_TABLE_ROWS_IN_EMAIL = 50`
- `DATA_ROOT`, `OUTPUT_ROOT`, `DUCKDB_PATH`

No YAML, no env vars. One file, one place to change.

## 13. Testing

- **Unit:** `positions.py` (synthetic legs → expected position series; sign convention; multi-vintage; empty input); `checks.py` (each check fed handcrafted divergences → expected exception rows; tolerance edge cases); `report.py` (snapshot test of HTML output against a fixed exception set).
- **Integration:** fixture pair of MO+FO CSVs simulating Day 1 → Day 2 with each break category injected. Run the full pipeline twice in a temp directory and assert:
  - Exception counts match expectations per check.
  - Email body contains expected section headers and counts.
  - CSV outputs contain expected row counts.
- **Smoke test:** `python daily_run.py --dry-run --date 2026-05-19` against the real CSVs in the project root, with email send disabled by a `--no-email` flag. Used for manual validation before scheduling.

## 14. Open Questions

1. SMTP host/port/TLS — confirm Microsoft 365 defaults.
2. Should the report include the **today's** trades (T) as informational at the bottom of the email, or only breaks? (Default: breaks only.)
3. Position key — keep at `(date, product, vintage)` or add `desk` / `strategy` like `pnl-pos` does? (Default: no desk, since RINs is one desk.)
4. CC list for the email — anyone else?

These don't block implementation; defaults above will be used unless overridden.

## 15. Future Work (not in v1)

- LLM enrichment section appended to the email by a separate, optional `enrichment.py` step. The deterministic pipeline never depends on it.
- Additional desks (Power, LCFS, OCFP) — add new `pipeline.py` variants that share `positions.py`, `checks.py`, `report.py`, `mailer.py`.
- A small status dashboard (single HTML file regenerated each run) for trends across days.
