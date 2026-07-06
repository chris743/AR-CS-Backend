"""Read API over the billing views. Thin wrappers — the logic lives in SQL."""

import pandas as pd

from . import db


def status() -> pd.DataFrame:
    """Full per-tag reconciliation (v_repack_status)."""
    return db.query(f"SELECT * FROM {db.qualified('v_repack_status')}")


def summary() -> pd.DataFrame:
    """wh_status x billing_status crosstab with tag + qty totals."""
    return db.query(
        f"SELECT wh_status, billing_status, count(*) AS tags, "
        f"sum(shipped_qty) AS shipped_qty "
        f"FROM {db.qualified('v_repack_status')} "
        f"GROUP BY wh_status, billing_status ORDER BY wh_status, billing_status"
    )


def billable_unbilled() -> pd.DataFrame:
    """Carton tags with a billable reason that aren't in cs_packing_billing_lines yet."""
    return db.query(f"SELECT * FROM {db.qualified('v_billable_unbilled')}")


def unbilled_shipped() -> pd.DataFrame:
    """Everything that shipped but isn't billed (any reason)."""
    return db.query(
        f"SELECT * FROM {db.qualified('v_repack_status')} "
        f"WHERE wh_status = 'shipped' AND billing_status IS DISTINCT FROM 'billed'"
    )


def chain(shipped_tag: int) -> pd.DataFrame:
    """The full repack chain behind one shipped tag (every node, billable flagged)."""
    return db.query(
        f"SELECT * FROM {db.qualified('v_repack_chain')} "
        f"WHERE shipped_tag = :t ORDER BY depth",
        t=shipped_tag,
    )


def status_page(wh_status: str | None = None, billing_status: str | None = None,
                billing_null: bool = False, limit: int = 25, offset: int = 0) -> pd.DataFrame:
    """A filtered, paginated page of v_repack_status (for crosstab drill-down)."""
    clauses, params = [], {"lim": int(limit), "off": int(offset)}
    if wh_status:
        clauses.append("wh_status = :wh")
        params["wh"] = wh_status
    if billing_null:
        clauses.append("billing_status IS NULL")
    elif billing_status is not None:
        clauses.append("billing_status = :bs")
        params["bs"] = billing_status
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return db.query(
        f"SELECT tagid, role, icrunidx, productname, fcreasonidx, uom, rundate, "
        f"wh_status, shipped_qty, billing_status FROM {db.qualified('v_repack_status')}"
        f"{where} ORDER BY tagid LIMIT :lim OFFSET :off",
        **params,
    )


def _count(table: str) -> int:
    if db.query("SELECT to_regclass(:t) AS r", t=db.qualified(table)).iloc[0]["r"] is None:
        return 0
    return int(db.query(f"SELECT count(*) AS n FROM {db.qualified(table)}").iloc[0]["n"])


def meta() -> dict:
    """Row counts per source table + db_ok (for the dashboard health strip)."""
    tables = {
        "repack_lines": "repack_lines",
        "repack_inputs": "repack_inputs",
        "repack_outputs": "repack_outputs",
        "shipping": "cs_packing_shipping_raw",
        "rates": "packing_rates",
        "classifications": "product_classification",
    }
    try:
        out = {key: _count(t) for key, t in tables.items()}
        if db.query("SELECT to_regclass(:t) AS r",
                    t=db.qualified("cs_packing_billing_lines")).iloc[0]["r"] is not None:
            out["billed_tags"] = int(db.query(
                f"SELECT count(DISTINCT tagid) AS n FROM {db.qualified('cs_packing_billing_lines')} "
                f"WHERE tagid IS NOT NULL").iloc[0]["n"])
        else:
            out["billed_tags"] = 0
        out["db_ok"] = True
        return out
    except Exception:
        out = {key: 0 for key in tables}
        out["billed_tags"] = 0
        out["db_ok"] = False
        return out


def bill_candidates(start: str | None = None, end: str | None = None,
                    status: str = "unbilled") -> pd.DataFrame:
    """Charges by billing week x PACK/REPACK — the per-week breakdown of a bill.

    Same source/filters as bill.for_period (v_charges, ship_date period, status),
    just grouped by billing_week too, so sum(amount) reconciles with the bill's
    service_total for the same (period, status). With no period it returns all
    weeks; default status='unbilled' = what's left to bill.
    """
    where, params = "TRUE", {}
    if start and end:
        where += " AND ship_date BETWEEN :s AND :e"
        params["s"], params["e"] = start, end
    if status == "unbilled":
        where += " AND NOT billed"
    elif status == "billed":
        where += " AND billed"
    return db.query(
        f"SELECT billing_week, transaction_type, count(*) AS tags, "
        f"       sum(shipped_qty) AS shipped_qty, sum(amount) AS amount, "
        f"       count(*) FILTER (WHERE rate_missing) AS tags_missing_rate "
        f"FROM {db.qualified('v_charges')} WHERE {where} "
        f"GROUP BY billing_week, transaction_type ORDER BY billing_week, transaction_type",
        **params,
    )


def bill_summary() -> pd.DataFrame:
    """The full weekly bill: packing/repack service + labor + materials + storage + total."""
    return db.query(f"SELECT * FROM {db.qualified('v_bill_summary')}")


def storage_charges(start: str | None = None, end: str | None = None,
                    status: str = "unbilled") -> pd.DataFrame:
    """Per-pallet storage charges (v_storage_charges), scoped by ship-date period + status.

    Mirrors bill_candidates: 'unbilled' = storage left to invoice, 'billed' = a reprint,
    'all' = every pallet that incurred storage in the window.
    """
    where, params = "TRUE", {}
    if start and end:
        where += " AND ship_date BETWEEN :s AND :e"
        params["s"], params["e"] = start, end
    if status == "unbilled":
        where += " AND NOT billed"
    elif status == "billed":
        where += " AND billed"
    return db.query(
        f"SELECT tagid, sono, lastconame, commodity, style, bagtype, shipped_qty, "
        f"       recv_date, ship_date, billing_week, days_in_storage, billable_days, "
        f"       rate, amount, rate_missing, billed "
        f"FROM {db.qualified('v_storage_charges')} WHERE {where} "
        f"ORDER BY billing_week, amount DESC NULLS LAST",
        **params,
    )


def billable_chain() -> pd.DataFrame:
    """One row per billable chain node across all shipped tags (chain-aware billing)."""
    return db.query(
        f"SELECT shipped_tag, tagid, icrunidx, fcreasonidx, productname, qty, depth "
        f"FROM {db.qualified('v_repack_chain')} WHERE billable ORDER BY shipped_tag, depth"
    )
