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
