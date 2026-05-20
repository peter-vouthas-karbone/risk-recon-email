# CLAUDE.md

Guidance for Claude Code sessions in this repository.

## What this is

A deterministic daily pipeline that reconciles the FO Fuels tradesheet against the MO RINs tradesheet across four checks and emails a single report. Scheduled Mon–Sat 06:00 via Windows Task Scheduler.

## Quick commands

```powershell
.venv\Scripts\Activate.ps1
pytest -v                              # full suite (should be 43 passed)
python daily_run.py --no-email         # exercise pipeline without sending
python daily_run.py --date 2026-05-20  # backfill a specific date
```

Generate a rendered email preview from the composer:
```powershell
python -c "from daily_recon.report.html_compose import ReconReportData, compose_html; ..."
```
(See `docs/superpowers/specs/previews/recon-email-actual.html` for an example.)

## Layout

- `src/daily_recon/` — new code (config, persistence, positions, checks/, mailer, report/, pipeline)
- `vendor/karbone_recon/` — vendored copy of the Karbone tradesheet reconciliation project, imports rewritten from bare siblings to `karbone_recon.*`
- `vendor/karbone_pnl_pos/` — vendored copy of the Karbone PnL & position project, imports rewritten from `src.*` to `karbone_pnl_pos.*`
- `docs/superpowers/specs/2026-05-20-rins-daily-recon-design.md` — the design spec
- `docs/superpowers/plans/2026-05-20-rins-daily-recon.md` — the implementation plan
- `docs/superpowers/specs/previews/` — design mockup + live-rendered email preview

## Conventions

- **TDD throughout.** New behavior gets a failing test first, then the minimum code to pass.
- **Tolerance:** `1e-6` for both volume and price comparisons. Constant lives in `src/daily_recon/config.py`.
- **One config file.** No env vars, no YAML. Edit `src/daily_recon/config.py`.
- **Theme reuse, not duplication.** Import `karbone_pnl_pos.reporting.theme.THEME` and the helpers from `html_builder` — do not introduce new colors or fonts. The recon report must read as a sibling of the PnL report.
- **Exception payloads must include every field the report column reads.** When adding a new check or extending an existing one, make sure the `payload` dict in the check covers the columns defined in `src/daily_recon/report/html_compose.py:_section_*`.
- **Commits are small and atomic.** One logical change per commit, conventional-commit-style subject (`feat(...)`, `fix(...)`, `test(...)`, `docs:`, `chore:`, `vendor:`).

## Known stubs / follow-ups

- `default_incoming_runner` in `src/daily_recon/pipeline.py` raises `NotImplementedError`. Tests inject a fake `incoming_runner`. Real wiring to `karbone_recon.{archive,ingest,stage,audit,reconcile}` is the next concrete TODO — needs to be exercised against real CSVs in `data/incoming/`.
- The keyring credential is per-host; whoever runs the scheduled task must have set it via `keyring.set_password('karbone_recon_smtp', 'peter.vouthas@karbone.com', '<password>')` on that machine.

## Gotchas

- `_new_run_id` uses microseconds, not just seconds — two runs in the same second would otherwise collide on `pos_runs.run_id`.
- `karbone_pnl_pos.reporting.sparkline` lazy-imports matplotlib/numpy inside the PNG renderer. Don't move those imports back to module top.
- Vendored project imports were rewritten mechanically. If you add new modules under either vendor, follow the same prefixed-absolute pattern (`from karbone_recon.x import ...`).
- Position equality and historical-position drift consume `pos_running_position` — they're seeded by `_legs_to_positions` in the pipeline, which reads `mo_legs.trade_date` AS `business_date`. The "business date" of a position is the trade date.

## When extending to a new desk/product

Same skeleton: a check module per check type, a section renderer per check, a payload dict that mirrors the report columns. Most of the existing code is product-agnostic; the parts that aren't (kicker labels, section ordering) live in `src/daily_recon/report/modules.py` and `html_compose.py`.
