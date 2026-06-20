"""EDGE: cache product-name -> (type, commodity, style) classification.

This replaces the graph's split_product_defs step. Classification is a Python
function (string parsing, with a needs_llm flag for ambiguous names); we run it
once per *new* product name and cache the result in Postgres so the billing
views can resolve rates with a plain join. Idempotent — only new names are added.
"""

import re

import pandas as pd

from . import db
from .config import SCHEMA

CLASS_TABLE = "product_classification"

_BAG_RE = re.compile(r"\d{1,2}/\d{1,2}(?:\.\d+)?LB")


def _bagtype(name: str) -> str:
    """Pack form for the rate key (matches the rate table's bagtype column).

    Bags: COMBO/NET/POUCH if identified, else GIRO (default — the rate join falls
    back to GIRO when a specific bag-type rate doesn't exist). Non-bags: BULK,
    or CLAM for clamshells.
    """
    u = (name or "").upper()
    if "BAG" in u or _BAG_RE.search(u):
        if re.search(r"\bCOMBO\b", u):
            return "COMBO"
        if re.search(r"\bNET\b", u):
            return "NET"
        if "POUCH" in u:
            return "POUCH"
        return "GIRO"  # default bag type
    if "CLAM" in u:
        return "CLAM"
    return "BULK"  # cartons / bulk


def _classify(name: str) -> dict:
    from agent.cs_packing_charges.nodes.split_prod_desc import parse_product_name

    try:
        p = parse_product_name(name)
        out = {
            "type": p["type"],
            "commodity": p["commodity"],
            "style": p["style"],
            "needs_llm": bool(p["needs_llm"]),
        }
    except Exception:
        out = {"type": None, "commodity": None, "style": None, "needs_llm": True}
    out["bagtype"] = _bagtype(name)
    return out


def refresh() -> dict:
    """(Re)classify every distinct product name, upserting type/commodity/style/bagtype."""
    names = db.query(
        f"SELECT DISTINCT productname FROM ("
        f"  SELECT productname FROM {db.qualified('repack_outputs')} "
        f"  UNION SELECT productname FROM {db.qualified('repack_inputs')} "
        f"  UNION SELECT productname FROM {db.qualified('repack_lines')}"
        f") x WHERE productname IS NOT NULL AND btrim(productname) <> ''"
    )["productname"]

    rows = [{"productname": n, **_classify(n)} for n in names]
    df = pd.DataFrame(rows)
    with db.engine().begin() as conn:
        df.to_sql("_stg_classification", conn, schema=SCHEMA, if_exists="replace", index=False)
        conn.exec_driver_sql(
            f"INSERT INTO {db.qualified(CLASS_TABLE)} (productname, type, commodity, style, needs_llm, bagtype) "
            f"SELECT productname, type, commodity, style, needs_llm, bagtype FROM {db.qualified('_stg_classification')} "
            f"ON CONFLICT (productname) DO UPDATE SET "
            f"  type = EXCLUDED.type, commodity = EXCLUDED.commodity, style = EXCLUDED.style, "
            f"  needs_llm = EXCLUDED.needs_llm, bagtype = EXCLUDED.bagtype"
        )
        conn.exec_driver_sql(f"DROP TABLE IF EXISTS {db.qualified('_stg_classification')}")
    return {"classified": len(names)}
