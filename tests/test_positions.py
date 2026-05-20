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
