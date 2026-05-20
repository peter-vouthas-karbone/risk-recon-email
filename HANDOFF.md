# Handoff — RINs Daily Reconciliation

**Date:** 2026-05-20
**Owner:** Peter Vouthas

## What was built

A deterministic Python pipeline that runs Mon–Sat at 06:00, reconciles the FO Fuels tradesheet against the MO RINs tradesheet across four checks, and emails one report styled to match the existing PnL report.

The four checks:

1. **Trade Drift** — added/removed/modified trades since the prior run, per system. `new_trade` is suppressed.
2. **Historical Position Drift** — running position by (date, product, vintage) for dates < T-1, compared against the prior run. Catches backdated changes.
3. **Position Break** — MO running position vs FO running position across all dates.
4. **Prior-Day Trades** — MO vs FO cross-system match restricted to `trade_date = T-1`. Anything not `matched` is an exception.

## Repository state

- **22 commits** on `master`, one logical commit per task plus follow-up fixes.
- **43 tests passing.** Unit coverage for every check (tolerance edges, missing prior run, missing source, etc.), the persistence layer, the mailer (with retry/credential paths mocked), the report renderers, the composers, and a two-day integration test exercising all four checks plus the empty-state day.
- Spec and plan are committed under `docs/superpowers/`.
- A live-rendered email preview is at `docs/superpowers/specs/previews/recon-email-actual.html`.

## How to verify locally

```powershell
cd "G:\Shared drives\KarboneRisk\Development\claude-daily-recon"
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ./vendor/karbone_recon
pip install -e ./vendor/karbone_pnl_pos
pip install -e .[dev]
pytest -v
```

Expected: 43 passed.

## What still needs to be done before this can run for real

These are tracked in the spec as v1.1 / production-readiness items, not bugs.

1. **Wire `default_incoming_runner` to karbone_recon.** Currently a `NotImplementedError` stub in `src/daily_recon/pipeline.py`. The function must:
   - Run `karbone_recon.archive` validate → archive → promote on the `data/incoming/` drop zone.
   - Run `karbone_recon.ingest.load_mo` and `load_fuels`, then `stage.expand_mo_legs` and `stage_fuels`.
   - Run `karbone_recon.audit` for Phase-1 drift.
   - Run `karbone_recon.reconcile.reconcile_cross_system` for Phase-2 cross-recon.
   - Return the upstream `run_id`.

   The function signatures inside karbone_recon haven't been verified against the names assumed in the original plan — read the vendored code under `vendor/karbone_recon/karbone_recon/` and adapt.

2. **Store the SMTP password in the keyring** on the host that will run the scheduled task:
   ```powershell
   python -c "import keyring; keyring.set_password('karbone_recon_smtp', 'peter.vouthas@karbone.com', '<the password>')"
   ```
   Karbone email is on Microsoft 365, so an app password may be required if MFA is enforced on the mailbox.

3. **Confirm SMTP host/port.** Defaults assumed: `smtp.office365.com:587` with STARTTLS. If Karbone uses a different SMTP relay, update `SMTP_HOST` / `SMTP_PORT` / `SMTP_STARTTLS` in `src/daily_recon/config.py`.

4. **Set up Windows Task Scheduler** per the steps in `README.md`. Run as a user/service account whose keyring holds the SMTP password.

5. **End-to-end dry run.** With real `Fuels_Tradesheet.csv` and `rins_tradesheet_no_rng.csv` in `data/incoming/`, run `python daily_run.py --no-email --date <today>` once to surface any data-shape issues; then run with `--no-email` removed against a personal recipient before pointing it at production.

## Open questions captured in the spec (defaults applied unless overridden)

- SMTP host/port — defaulting to `smtp.office365.com:587`.
- Include today's (T) trades as informational in the email — defaulting to breaks-only.
- Position key includes desk/strategy — defaulting to `(date, product, vintage)` only.
- CC list — empty.

## Future enhancements (explicitly out of scope for v1)

- **LLM enrichment.** Decision deferred until we see live output. Spec section 15 outlines an optional `enrichment.py` module that would post-process the deterministic exceptions into clusters/categories. Must remain optional and never block the deterministic report.
- **Additional desks/products.** The architecture is product-agnostic; adding Power / LCFS / OCFP would mostly involve new pipeline variants sharing positions/checks/report/mailer.
- **Trend dashboard.** A small standalone HTML showing exception trends across runs.

## Where to look

| Question                              | File                                                                                  |
|---------------------------------------|---------------------------------------------------------------------------------------|
| What was supposed to be built?        | `docs/superpowers/specs/2026-05-20-rins-daily-recon-design.md`                        |
| What's the implementation plan?       | `docs/superpowers/plans/2026-05-20-rins-daily-recon.md`                               |
| What does the email look like?        | `docs/superpowers/specs/previews/recon-email-actual.html` (live), `recon-email-mockup.html` (design) |
| How do I run / schedule it?           | `README.md`                                                                           |
| Conventions for future Claude work?   | `CLAUDE.md`                                                                           |
| Where are the deterministic checks?   | `src/daily_recon/checks/`                                                             |
| Where's the orchestrator?             | `src/daily_recon/pipeline.py`                                                         |
| Where's the visual theme?             | `vendor/karbone_pnl_pos/karbone_pnl_pos/reporting/theme.py`                           |

## Contact

Peter Vouthas · peter.vouthas@karbone.com
