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
