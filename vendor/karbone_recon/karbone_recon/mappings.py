"""
Counterparty, product, and vintage mapping.

Counterparty mapping sources (from counterparty_mapping.csv):
  - MO sellers   → 'Tradesheet Vendor Name'   → canonical 'Legal Name'
  - MO buyers    → 'Tradesheet Customer Name'  → canonical 'Legal Name'
  - Fuels        → 'Fuels TS Name'             → canonical 'Legal Name'

Vintage rules (hardcoded per spec):
  Products that use vintage: LCFS, OCFP, HO, BO  (and RIN products by D-code)
  Products that ignore vintage: RTCs
"""

from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

# Root of the project (one level up from src/)
DATA_DIR = Path(__file__).parent.parent / "data"
COUNTERPARTY_MAP_FILE = DATA_DIR / "counterparty_mapping.csv"

# Products where vintage is ignored (set canonical vintage to None)
VINTAGE_IGNORE = {"RTCs", "RTC"}

# Products excluded from cross-system reconciliation and drift detection.
# These products only exist in MO and have no Fuels counterpart.
RECON_EXCLUDED_PRODUCTS = {"RTC", "RTCs", "Biodiesel"}

# Hardcoded vintage rules: canonical_product → use_vintage
VINTAGE_RULES = {
    "LCFS": True,
    "OCFP": True,
    "HO": True,
    "BO": True,
    "RTCs": False,
    "RTC": False,
    # RIN products use vintage by default (D3, D4, D6, D9)
    "D3 RIN": True,
    "D4 RIN": True,
    "D6 RIN": True,
    "D9 RIN": True,
}


def load_counterparty_map(conn: duckdb.DuckDBPyConnection) -> None:
    """
    Seed the counterparty_map table from counterparty_mapping.csv.
    All rows from the CSV are treated as 'approved'.
    Uses INSERT OR IGNORE semantics via DuckDB's ON CONFLICT DO NOTHING.
    """
    df = pd.read_csv(COUNTERPARTY_MAP_FILE, dtype=str)

    rows = []
    today = date.today()

    for _, row in df.iterrows():
        legal_name = str(row.get("Legal Name", "")).strip()
        if not legal_name:
            continue

        # MO vendor (seller) names
        vendor_name = str(row.get("Tradesheet Vendor Name", "")).strip()
        if vendor_name and vendor_name.lower() not in ("nan", ""):
            rows.append({
                "source_system": "mo_vendor",
                "raw_name": vendor_name,
                "canonical_name": legal_name,
                "approval_status": "approved",
                "effective_date": today,
            })

        # MO customer (buyer) names
        customer_name = str(row.get("Tradesheet Customer Name", "")).strip()
        if customer_name and customer_name.lower() not in ("nan", ""):
            rows.append({
                "source_system": "mo_customer",
                "raw_name": customer_name,
                "canonical_name": legal_name,
                "approval_status": "approved",
                "effective_date": today,
            })

        # Fuels counterparty names
        fuels_name = str(row.get("Fuels TS Name", "")).strip()
        if fuels_name and fuels_name.lower() not in ("nan", ""):
            rows.append({
                "source_system": "fuels",
                "raw_name": fuels_name,
                "canonical_name": legal_name,
                "approval_status": "approved",
                "effective_date": today,
            })

    if not rows:
        return

    seed_df = pd.DataFrame(rows).drop_duplicates(subset=["source_system", "raw_name"])

    # Insert only rows not already present
    conn.execute("""
        INSERT OR IGNORE INTO counterparty_map
        SELECT * FROM seed_df
    """)


def load_vintage_rules(conn: duckdb.DuckDBPyConnection) -> None:
    """Seed vintage_rule_map from the hardcoded VINTAGE_RULES dict."""
    rows = [
        {"canonical_product": k, "use_vintage_flag": v}
        for k, v in VINTAGE_RULES.items()
    ]
    rules_df = pd.DataFrame(rows)
    conn.execute("""
        INSERT OR IGNORE INTO vintage_rule_map
        SELECT * FROM rules_df
    """)


def _normalize_name(name: str) -> str:
    """
    Strip trivial punctuation differences for lookup purposes.
    e.g. "ICE U.S. OTC Commodity Markets, LLC" → "ICE U.S. OTC Commodity Markets LLC"
    This does NOT change the mapping table — just helps find existing matches.
    """
    import re
    # Remove commas directly before common suffixes (LLC, Inc, Corp, etc.)
    name = re.sub(r",\s*(LLC|Inc|Corp|Co|LP|LLP|Ltd)\b", r" \1", name)
    # Collapse multiple spaces
    name = re.sub(r"\s+", " ", name).strip()
    return name


def build_counterparty_lookup(
    conn: duckdb.DuckDBPyConnection,
    source_system: str,
) -> dict[str, str]:
    """
    Return {raw_name: canonical_name} for approved mappings in the given source system.
    Also builds a normalized-name index so 'Foo, LLC' matches if 'Foo LLC' is mapped.
    source_system is one of: 'mo_vendor', 'mo_customer', 'fuels'
    """
    result = conn.execute("""
        SELECT raw_name, canonical_name
        FROM counterparty_map
        WHERE source_system = ? AND approval_status = 'approved'
    """, [source_system]).fetchall()

    exact = {row[0]: row[1] for row in result}
    # Build a normalized fallback: normalized_raw_name → canonical
    normalized = {_normalize_name(row[0]): row[1] for row in result}

    # Return a lookup that tries exact first, then normalized
    class _Lookup(dict):
        def get(self, key, default=None):
            v = super().get(key)
            if v is not None:
                return v
            return normalized.get(_normalize_name(key), default) if key else default

    lookup = _Lookup(exact)
    return lookup


def apply_counterparty_canonical(
    series: pd.Series,
    lookup: dict[str, str],
) -> pd.Series:
    """
    Map a Series of raw counterparty names to canonical names.
    Unmapped values return NaN (will be flagged as unmapped breaks).
    """
    return series.map(lookup)


def apply_vintage_canonical(
    product_canonical: pd.Series,
    vintage_raw: pd.Series,
) -> pd.Series:
    """
    For each row, return the canonical vintage:
    - If the product's use_vintage_flag is False (e.g. RTCs) → None
    - Otherwise → vintage_raw value as-is (already a year string)
    """
    result = []
    for prod, vin in zip(product_canonical, vintage_raw):
        prod_str = str(prod).strip() if pd.notna(prod) else ""
        rule = VINTAGE_RULES.get(prod_str, True)  # default: use vintage
        if not rule:
            result.append(None)
        else:
            result.append(vin if pd.notna(vin) and str(vin).strip() not in ("nan", "") else None)
    return pd.Series(result, dtype=object)


def collect_unmapped_counterparties(
    raw_names: pd.Series,
    canonical: pd.Series,
    source_system: str,
) -> list[dict]:
    """Return list of unmapped counterparty dicts for the unmapped_values report."""
    mask = canonical.isna() & raw_names.notna() & (raw_names.str.strip() != "")
    unmapped = raw_names[mask].dropna().unique()
    return [
        {"type": "counterparty", "source_system": source_system, "raw_value": v}
        for v in unmapped
    ]
