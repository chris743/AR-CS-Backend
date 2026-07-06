"""EDGE: mirror the MSSQL packing-rate table into Postgres.

Rates live in MSSQL (DM03.creekside_packing_rates). Mirroring them into
creekside_core.packing_rates lets the bill be a pure SQL join. Run this whenever
rates change (it's a full refresh). Reuses the existing MSSQL loader.
"""

from . import db
from .config import SCHEMA

RATES_TABLE = "packing_rates"
STORAGE_RATES_TABLE = "storage_rates"
STORAGE_SHEET = "STORAGE"


def _storage_commodity_group(a):
    """A STORAGE-tab group header -> (commtype, commodity), else None."""
    if not isinstance(a, str):
        return None
    up = a.upper()
    if "PACKING RATES" not in up and "COLD STORAGE RATES" not in up:
        return None
    commtype = "ORG" if "ORGANIC" in up else "COV"
    if "STEM" in up:
        commodity = "STEMLEAF"
    elif "LEMON" in up:
        commodity = "LEMON"
    elif "MANDARIN" in up:
        commodity = "MANDARIN"
    elif "BLOOD" in up:
        commodity = "BLOOD"
    elif "ORANGE" in up or "CARAS" in up:
        commodity = "ORANGE"
    else:
        return None
    return commtype, commodity


def _storage_bagtype(label: str) -> str:
    up = label.upper()
    for kw, bt in (("GIRO", "GIRO"), ("NET", "NET"), ("POUCH", "POUCH"),
                   ("COMBO", "COMBO"), ("CLAM", "CLAM")):
        if kw in up:
            return bt
    return "BULK"  # VF / Carton / Box / Tri-Wall / Euro Tray / Consumer / Loose Fill / Hi Pack


def _storage_style(label: str) -> str | None:
    """Normalize a pack-form label to a product_classification-style token."""
    import re
    up = label.upper()
    m = re.search(r"(\d+)\s*/\s*(\d+)#", up)          # 12/2#  -> 12/2LB
    if m:
        return f"{m.group(1)}/{m.group(2)}LB"
    if "TRI-WALL" in up or "TRIWALL" in up:
        return "TRIWALL"
    m = re.search(r"(\d+)#", up)                       # 25# VF -> 25LB
    if m:
        return f"{m.group(1)}LB"
    return None


def _parse_storage_sheet(path: str):
    """Parse the STORAGE tab into structured (commtype, commodity, style, bagtype,
    ctns_per_pallet, rate) rows. Each rate is $ per carton per day; the sheet derives
    it as 60 / cartons-per-pallet (a full pallet ~= $60/day). Oranges carry a
    'USE THIS RATE' override in column D, so col D wins when numeric."""
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[STORAGE_SHEET]
    grp, rows = None, []
    for r in ws.iter_rows(values_only=True):
        a, b, c, d = (r + (None, None, None, None))[:4]
        g = _storage_commodity_group(a)
        if g:
            grp = g
            continue
        if grp is None or not isinstance(a, str):
            continue
        rate = d if isinstance(d, (int, float)) else c
        if not isinstance(rate, (int, float)) or not isinstance(b, (int, float)):
            continue
        style = _storage_style(a)
        if style is None:
            continue
        rows.append({"commtype": grp[0], "commodity": grp[1], "style": style,
                     "bagtype": _storage_bagtype(a), "ctns_per_pallet": float(b),
                     "rate": round(float(rate), 4)})
    return rows


def ingest_storage_rates(path: str) -> int:
    """Load per-carton cold-storage rates from the Reedley Charges workbook.

    Parses the STORAGE tab into storage_rates keyed by (commtype, commodity, style,
    bagtype) — the same key packing_rates uses, so v_storage_charges can join it via
    product_classification. Full refresh. Dedupes on the key (a plain pack form beats
    its RPC/Euro variant, kept by sheet order). Returns rows written.
    """
    import pandas as pd

    rows = _parse_storage_sheet(path)
    df = pd.DataFrame(rows, columns=["commtype", "commodity", "style", "bagtype",
                                     "ctns_per_pallet", "rate"])
    df = df.drop_duplicates(subset=["commtype", "commodity", "style", "bagtype"], keep="first")
    with db.engine().begin() as conn:
        conn.exec_driver_sql(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
        conn.exec_driver_sql(
            f"CREATE TABLE IF NOT EXISTS {db.qualified(STORAGE_RATES_TABLE)} "
            "(commtype text, commodity text, style text, bagtype text, "
            " ctns_per_pallet numeric, rate numeric)"
        )
        conn.exec_driver_sql(f"TRUNCATE {db.qualified(STORAGE_RATES_TABLE)}")
        df.to_sql(STORAGE_RATES_TABLE, conn, schema=SCHEMA, if_exists="append", index=False)
    return len(df)


def mirror() -> int:
    """Refresh creekside_core.packing_rates from MSSQL. Returns rows written."""
    from agent.cs_packing_billing.nodes.apply_charges import load_charges

    rates = load_charges()
    rates.columns = [c.lower() for c in rates.columns]
    for col in ("commtype", "commodity", "style", "bagtype", "method"):
        if col in rates.columns:
            rates[col] = rates[col].fillna("").astype(str).str.strip()
    keep = [c for c in ("commtype", "commodity", "style", "bagtype", "method", "charge") if c in rates.columns]
    rates = rates[keep]
    # The rate key is (commtype, commodity, style, bagtype). A given key can still
    # have multiple `method` rows (blank vs RPC); take the blank/standard method to
    # avoid a join fan-out (RPC is a separate dimension, not handled yet).
    if "method" in rates.columns:
        rates = rates.sort_values("method").drop_duplicates(
            subset=[c for c in ("commtype", "commodity", "style", "bagtype") if c in rates.columns],
            keep="first",
        )

    with db.engine().begin() as conn:
        conn.exec_driver_sql(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
        conn.exec_driver_sql(
            f"CREATE TABLE IF NOT EXISTS {db.qualified(RATES_TABLE)} "
            "(commtype text, commodity text, style text, bagtype text, method text, charge numeric)"
        )
        for col in ("bagtype", "method"):
            conn.exec_driver_sql(
                f"ALTER TABLE {db.qualified(RATES_TABLE)} ADD COLUMN IF NOT EXISTS {col} text"
            )
        conn.exec_driver_sql(f"TRUNCATE {db.qualified(RATES_TABLE)}")
        rates.to_sql(RATES_TABLE, conn, schema=SCHEMA, if_exists="append", index=False)
    return len(rates)
