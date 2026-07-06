"""EDGE: mirror the MSSQL packing-rate table into Postgres.

Rates live in MSSQL (DM03.creekside_packing_rates). Mirroring them into
creekside_core.packing_rates lets the bill be a pure SQL join. Run this whenever
rates change (it's a full refresh). Reuses the existing MSSQL loader.
"""

from . import db
from .config import SCHEMA

RATES_TABLE = "packing_rates"
STORAGE_RATES_TABLE = "storage_rates"


def set_storage_rate(rate_per_day: float, free_days: int = 7) -> dict:
    """Set the flat storage rate: `rate_per_day` per pallet per day after `free_days`.

    Upserts the default (commodity IS NULL) row in storage_rates — the rate every
    shipped pallet accrues once it has sat past the free period. Returns the row set.
    """
    with db.engine().begin() as conn:
        conn.exec_driver_sql(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
        conn.exec_driver_sql(
            f"CREATE TABLE IF NOT EXISTS {db.qualified(STORAGE_RATES_TABLE)} "
            "(commodity text, free_days integer NOT NULL DEFAULT 7, rate_per_day numeric)"
        )
        # One default row: replace it wholesale (there's no unique key on a NULL commodity).
        conn.exec_driver_sql(
            f"DELETE FROM {db.qualified(STORAGE_RATES_TABLE)} WHERE commodity IS NULL"
        )
        conn.exec_driver_sql(
            f"INSERT INTO {db.qualified(STORAGE_RATES_TABLE)} (commodity, free_days, rate_per_day) "
            f"VALUES (NULL, {int(free_days)}, {float(rate_per_day)})"
        )
    return {"commodity": None, "free_days": int(free_days), "rate_per_day": float(rate_per_day)}


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
