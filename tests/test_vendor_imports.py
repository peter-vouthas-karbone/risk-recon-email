"""Vendor packages must be importable under their renamed namespaces."""


def test_karbone_recon_modules_importable():
    from karbone_recon import archive, audit, db, ingest, mappings, reconcile, report, stage  # noqa: F401


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
