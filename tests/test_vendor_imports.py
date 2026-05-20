"""Vendor packages must be importable under their renamed namespaces."""


def test_karbone_recon_modules_importable():
    from karbone_recon import archive, audit, db, ingest, mappings, reconcile, report, stage  # noqa: F401
