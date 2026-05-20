"""
Canonical staging layer.

expand_mo_legs():
  Takes the filtered MO DataFrame (from ingest.load_mo) and expands each row
  into up to two legs (seller leg + buyer leg), cleaning dates and numerics.
  Inserts into mo_rows and mo_legs tables.

stage_fuels():
  Normalizes the Fuels DataFrame, derives delivery_match_date, and inserts
  into fuels_lines.
"""

import hashlib
import json
import re
from datetime import date
from typing import Optional

import duckdb
import pandas as pd

IGNORED_COUNTERPARTIES = {"LONG POSITION", "SHORT POSITION"}

PRODUCT_CANONICAL_MAP = {
    "CA LCFS": "LCFS",
}

# Products whose vintage_canonical is derived from the delivery quarter rather
# than taken from the raw vintage field.
DELIVERY_QUARTER_PRODUCTS = {"LCFS", "OCFP"}


# ---------------------------------------------------------------------------
# Cleaning utilities
# ---------------------------------------------------------------------------

def clean_numeric(s, decimals: int = 4) -> Optional[float]:
    """
    Parse numeric strings like '$2.4400', '52,772', '(52,772)', '$ (2.44)', '$0.00'.
    Parentheses indicate negative values.  Result is rounded to *decimals* places
    (default 4) to prevent floating-point imprecision from propagating downstream.
    Returns float or None.
    """
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    s = str(s).strip()
    if not s or s.lower() == "nan":
        return None
    s = re.sub(r"[\$,\s]", "", s)
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    try:
        v = float(s)
        return round(-v if negative else v, decimals)
    except ValueError:
        return None


def canonicalize_product(raw: Optional[str]) -> Optional[str]:
    """Map a raw product name to its canonical form."""
    if raw is None:
        return None
    return PRODUCT_CANONICAL_MAP.get(raw, raw)


def delivery_to_quarter(d: Optional[date]) -> Optional[str]:
    """
    Convert a delivery date to a quarter string in the format Q{n}'{YY}.
    e.g. 2025-04-10 -> "Q2'25", 2026-01-05 -> "Q1'26"
    Returns None when d is None.
    """
    if d is None:
        return None
    quarter = (d.month - 1) // 3 + 1
    return f"Q{quarter}'{d.strftime('%y')}"


def clean_date(s) -> Optional[date]:
    """Parse MM/DD/YYYY dates. Returns datetime.date or None."""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    s = str(s).strip()
    if not s or s.lower() == "nan":
        return None
    try:
        return pd.to_datetime(s, format="%m/%d/%Y").date()
    except Exception:
        # Try generic parse as fallback
        try:
            return pd.to_datetime(s).date()
        except Exception:
            return None


def _leg_hash(d: dict) -> str:
    return hashlib.sha256(
        json.dumps(d, sort_keys=True, default=str).encode()
    ).hexdigest()


# ---------------------------------------------------------------------------
# Generic tradesheet leg expansion (shared by MO and Management)
# ---------------------------------------------------------------------------

def _expand_tradesheet_legs(
    df: pd.DataFrame,
    run_id: str,
    vendor_lookup: dict[str, str],
    customer_lookup: dict[str, str],
    id_prefix: str,
    vendor_source_system: str,
    customer_source_system: str,
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Expand each row in a seller/buyer tradesheet into up to two legs.

    Returns (row_records, leg_records, unmapped) — caller inserts into the
    appropriate database tables.
    """
    row_records: list[dict] = []
    leg_records: list[dict] = []
    unmapped: list[dict] = []

    row_id_key = f"{id_prefix}_row_id"
    leg_id_key = f"{id_prefix}_leg_id"
    group_id_key = f"{id_prefix}_trade_group_id"

    for row_num, (_, row) in enumerate(df.iterrows()):
        source_row_number = row_num + 2
        this_row_id = f"{run_id}_{source_row_number}"
        product_raw = str(row.get("Product", "")).strip()
        if not product_raw or product_raw.lower() == "nan":
            continue
        vintage_raw = str(row.get("Vintage", "")).strip() or None
        platform = str(row.get("Platform", "")).strip() or None
        notes = str(row.get("Notes", "")).strip() or None

        row_hash = _leg_hash({
            "source_row_number": source_row_number,
            "Product": product_raw,
            "Vintage": vintage_raw,
        })

        row_records.append({
            "run_id": run_id,
            row_id_key: this_row_id,
            group_id_key: this_row_id,
            "source_row_number": source_row_number,
            "product_raw": product_raw,
            "vintage_raw": vintage_raw,
            "platform": platform,
            "notes": notes,
            "row_hash": row_hash,
        })

        # ---- Seller leg (side=Buy) ----
        seller_raw = str(row.get("Seller", "")).strip() or None
        if seller_raw and seller_raw.upper() not in IGNORED_COUNTERPARTIES:
            seller_canonical = vendor_lookup.get(seller_raw)
            if seller_canonical is None:
                unmapped.append({
                    "type": "counterparty",
                    "source_system": vendor_source_system,
                    "raw_value": seller_raw,
                })
            trade_date = clean_date(row.get("Date"))
            quantity = clean_numeric(row.get("Volume"))
            price = clean_numeric(row.get("Price"))
            price = -abs(price) if price is not None else None
            delivery_raw = str(row.get("Shipment In", "")).strip() or None
            delivery_match_date = clean_date(delivery_raw)

            seller_group_id = _leg_hash({
                "trade_date": str(trade_date),
                "counterparty": seller_raw,
                "product": product_raw,
                "vintage": vintage_raw,
                "price": str(price),
            })

            leg_records.append({
                "run_id": run_id,
                leg_id_key: f"{this_row_id}_seller",
                row_id_key: this_row_id,
                group_id_key: seller_group_id,
                "leg_type": "seller_leg",
                "side": "Buy",
                "trade_date": trade_date,
                "counterparty_raw": seller_raw,
                "counterparty_canonical": seller_canonical,
                "product_raw": product_raw,
                "product_canonical": canonicalize_product(product_raw),
                "vintage_raw": vintage_raw,
                "vintage_canonical": (
                    delivery_to_quarter(delivery_match_date)
                    if canonicalize_product(product_raw) in DELIVERY_QUARTER_PRODUCTS
                    else vintage_raw
                ),
                "quantity": quantity,
                "price": price,
                "delivery_raw": delivery_raw,
                "delivery_match_date": delivery_match_date,
                "row_hash": _leg_hash({
                    row_id_key: this_row_id,
                    "leg_type": "seller_leg",
                    "trade_date": str(trade_date),
                    "counterparty_raw": seller_raw,
                    "quantity": quantity,
                    "price": price,
                    "delivery_raw": delivery_raw,
                }),
            })

        # ---- Buyer leg (side=Sell) ----
        buyer_raw = str(row.get("Buyer", "")).strip() or None
        if buyer_raw and buyer_raw.upper() not in IGNORED_COUNTERPARTIES:
            buyer_canonical = customer_lookup.get(buyer_raw)
            if buyer_canonical is None:
                unmapped.append({
                    "type": "counterparty",
                    "source_system": customer_source_system,
                    "raw_value": buyer_raw,
                })
            trade_date = clean_date(row.get("Date.1"))
            quantity = clean_numeric(row.get("Volume.1"))
            quantity = -abs(quantity) if quantity is not None else None
            price = clean_numeric(row.get("Price.1"))
            delivery_raw = str(row.get("Shipment out", "")).strip() or None
            delivery_match_date = clean_date(delivery_raw)

            buyer_group_id = _leg_hash({
                "trade_date": str(trade_date),
                "counterparty": buyer_raw,
                "product": product_raw,
                "vintage": vintage_raw,
                "price": str(price),
            })

            leg_records.append({
                "run_id": run_id,
                leg_id_key: f"{this_row_id}_buyer",
                row_id_key: this_row_id,
                group_id_key: buyer_group_id,
                "leg_type": "buyer_leg",
                "side": "Sell",
                "trade_date": trade_date,
                "counterparty_raw": buyer_raw,
                "counterparty_canonical": buyer_canonical,
                "product_raw": product_raw,
                "product_canonical": canonicalize_product(product_raw),
                "vintage_raw": vintage_raw,
                "vintage_canonical": (
                    delivery_to_quarter(delivery_match_date)
                    if canonicalize_product(product_raw) in DELIVERY_QUARTER_PRODUCTS
                    else vintage_raw
                ),
                "quantity": quantity,
                "price": price,
                "delivery_raw": delivery_raw,
                "delivery_match_date": delivery_match_date,
                "row_hash": _leg_hash({
                    row_id_key: this_row_id,
                    "leg_type": "buyer_leg",
                    "trade_date": str(trade_date),
                    "counterparty_raw": buyer_raw,
                    "quantity": quantity,
                    "price": price,
                    "delivery_raw": delivery_raw,
                }),
            })

    seen = set()
    unique_unmapped = []
    for u in unmapped:
        key = (u["source_system"], u["raw_value"])
        if key not in seen:
            seen.add(key)
            unique_unmapped.append(u)

    return row_records, leg_records, unique_unmapped


# ---------------------------------------------------------------------------
# MO leg expansion
# ---------------------------------------------------------------------------

def expand_mo_legs(
    mo_df: pd.DataFrame,
    run_id: str,
    vendor_lookup: dict[str, str],
    customer_lookup: dict[str, str],
    conn: duckdb.DuckDBPyConnection,
) -> tuple[pd.DataFrame, list[dict]]:
    """
    Expand each MO row into up to two legs and insert into mo_rows + mo_legs.
    """
    row_records, leg_records, unmapped = _expand_tradesheet_legs(
        mo_df, run_id, vendor_lookup, customer_lookup,
        id_prefix="mo",
        vendor_source_system="mo_vendor",
        customer_source_system="mo_customer",
    )

    if row_records:
        rows_df = pd.DataFrame(row_records)
        conn.execute("INSERT INTO mo_rows SELECT * FROM rows_df")

    legs_df = pd.DataFrame(leg_records) if leg_records else pd.DataFrame()
    if not legs_df.empty:
        conn.execute("INSERT INTO mo_legs SELECT * FROM legs_df")

    return legs_df, unmapped


# ---------------------------------------------------------------------------
# Management leg expansion
# ---------------------------------------------------------------------------

def expand_mgmt_legs(
    mgmt_df: pd.DataFrame,
    run_id: str,
    vendor_lookup: dict[str, str],
    customer_lookup: dict[str, str],
    conn: duckdb.DuckDBPyConnection,
) -> tuple[pd.DataFrame, list[dict]]:
    """
    Expand each Management row into up to two legs and insert into
    mgmt_rows + mgmt_legs.
    """
    row_records, leg_records, unmapped = _expand_tradesheet_legs(
        mgmt_df, run_id, vendor_lookup, customer_lookup,
        id_prefix="mgmt",
        vendor_source_system="mgmt_vendor",
        customer_source_system="mgmt_customer",
    )

    if row_records:
        rows_df = pd.DataFrame(row_records)
        conn.execute("INSERT INTO mgmt_rows SELECT * FROM rows_df")

    legs_df = pd.DataFrame(leg_records) if leg_records else pd.DataFrame()
    if not legs_df.empty:
        conn.execute("INSERT INTO mgmt_legs SELECT * FROM legs_df")

    return legs_df, unmapped


# ---------------------------------------------------------------------------
# Fuels staging
# ---------------------------------------------------------------------------

def _fuels_delivery_match_date(row: pd.Series) -> Optional[date]:
    """
    Delivery match rule:
    - If Open/Closed == "Closed" → use Delivery Date
    - Else (Open) → use Delivery Close
    """
    open_closed = str(row.get("Open/Closed", "")).strip()
    if open_closed == "Closed":
        return clean_date(row.get("Delivery Date"))
    return clean_date(row.get("Delivery Close"))


def stage_fuels(
    fuels_df: pd.DataFrame,
    run_id: str,
    fuels_lookup: dict[str, str],
    conn: duckdb.DuckDBPyConnection,
) -> tuple[pd.DataFrame, list[dict]]:
    """
    Normalize the Fuels DataFrame and insert into fuels_lines.

    Returns:
      (lines_df, unmapped_list)
    """
    records = []
    unmapped = []

    for idx, row in fuels_df.iterrows():
        source_row_number = int(idx) + 2
        fuels_line_id = f"{run_id}_{source_row_number}"

        trade_number = str(row.get("Trade Number", "")).strip() or None
        line_id = str(row.get("Line ID", "")).strip() or None
        open_closed = str(row.get("Open/Closed", "")).strip() or None
        status = str(row.get("Status", "")).strip() or None
        side = str(row.get("Buy/Sell", "")).strip() or None
        product_raw = str(row.get("Product", "")).strip()
        if not product_raw or product_raw.lower() == "nan":
            continue
        vintage_raw = str(row.get("Vintage", "")).strip() or None

        counterparty_raw = str(row.get("Counterparty", "")).strip() or None
        counterparty_canonical = fuels_lookup.get(counterparty_raw) if counterparty_raw else None
        if counterparty_raw and counterparty_canonical is None:
            unmapped.append({
                "type": "counterparty",
                "source_system": "fuels",
                "raw_value": counterparty_raw,
            })

        trade_date = clean_date(row.get("Trade Date"))
        quantity = clean_numeric(row.get("Volume"))
        price = clean_numeric(row.get("Price/RIN"))
        delivery_date = clean_date(row.get("Delivery Date"))
        delivery_close = clean_date(row.get("Delivery Close"))
        delivery_match_date = _fuels_delivery_match_date(row)

        row_dict = {
            "run_id": run_id,
            "fuels_line_id": fuels_line_id,
            "trade_number": trade_number,
            "line_id": line_id,
            "trade_date": trade_date,
            "counterparty_raw": counterparty_raw,
            "counterparty_canonical": counterparty_canonical,
            "side": side,
            "product_raw": product_raw,
            "product_canonical": canonicalize_product(product_raw),
            "vintage_raw": vintage_raw,
            "vintage_canonical": (
                delivery_to_quarter(delivery_date or delivery_close)
                if canonicalize_product(product_raw) in DELIVERY_QUARTER_PRODUCTS
                else vintage_raw
            ),
            "quantity": quantity,
            "price": price,
            "delivery_date": delivery_date,
            "delivery_close": delivery_close,
            "delivery_match_date": delivery_match_date,
            "open_closed": open_closed,
            "status": status,
            "row_hash": _leg_hash({
                "trade_number": trade_number,
                "line_id": line_id,
                "counterparty_raw": counterparty_raw,
                "quantity": quantity,
                "price": price,
            }),
        }
        records.append(row_dict)

    lines_df = pd.DataFrame(records) if records else pd.DataFrame()
    if not lines_df.empty:
        conn.execute("INSERT INTO fuels_lines SELECT * FROM lines_df")

    seen = set()
    unique_unmapped = []
    for u in unmapped:
        key = (u["source_system"], u["raw_value"])
        if key not in seen:
            seen.add(key)
            unique_unmapped.append(u)

    return lines_df, unique_unmapped
