"""State for the shipping-charges flow: one row per WE folder.

This replaces the graph's in-flight State + interrupt suspension. A run is
persisted here after it's built; the review and post steps read/update it. The
table is created lazily (idempotent) on first write, like the rest of standalone
leans on creekside_core.
"""

import json

from sqlalchemy import text

from .. import db

TABLE = "shipping_runs"

_DDL = f"""
CREATE TABLE IF NOT EXISTS {db.qualified(TABLE)} (
    week_folder   text PRIMARY KEY,
    status        text NOT NULL DEFAULT 'built',  -- built | needs_review | posted | error
    report_path   text,
    trimmed_path  text,
    combined_path text,
    xlsx_path     text,
    result        jsonb,   -- [{{file, order_number, match:{{...}}}}]
    needs_review  jsonb,   -- subset of result needing human attention
    total         numeric,
    erp_result    jsonb,
    error         text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    posted_at     timestamptz
)
"""

_COLS = ("status", "report_path", "trimmed_path", "combined_path", "xlsx_path",
         "result", "needs_review", "total", "erp_result", "error", "posted_at")
_JSON_COLS = {"result", "needs_review", "erp_result"}


def ensure_schema() -> None:
    with db.engine().begin() as conn:
        conn.exec_driver_sql(_DDL)


def _dump(col: str, val):
    return json.dumps(val) if col in _JSON_COLS and val is not None else val


def upsert(week_folder: str, **fields) -> None:
    """Insert or update a run row. JSON columns accept python objects."""
    ensure_schema()
    cols = [c for c in _COLS if c in fields]
    params = {"week_folder": week_folder, **{c: _dump(c, fields[c]) for c in cols}}

    def expr(c):  # cast json text -> jsonb
        return f"CAST(:{c} AS jsonb)" if c in _JSON_COLS else f":{c}"

    insert_cols = ", ".join(["week_folder", *cols])
    insert_vals = ", ".join([":week_folder", *[expr(c) for c in cols]])
    updates = ", ".join([f"{c} = {expr(c)}" for c in cols] + ["updated_at = now()"])
    sql = (f"INSERT INTO {db.qualified(TABLE)} ({insert_cols}) VALUES ({insert_vals}) "
           f"ON CONFLICT (week_folder) DO UPDATE SET {updates}")
    with db.engine().begin() as conn:
        conn.execute(text(sql), params)


def mark_posted(week_folder: str, erp_result: dict) -> None:
    """Record an ERP post: success stamps posted_at + status='posted'; failure keeps status."""
    ensure_schema()
    ok = bool(erp_result.get("ok"))
    sql = (f"UPDATE {db.qualified(TABLE)} SET erp_result = CAST(:e AS jsonb), updated_at = now()"
           + (", status = 'posted', posted_at = now()" if ok else "")
           + " WHERE week_folder = :w")
    with db.engine().begin() as conn:
        conn.execute(text(sql), {"e": json.dumps(erp_result), "w": week_folder})


def get(week_folder: str) -> dict | None:
    ensure_schema()
    df = db.query(f"SELECT * FROM {db.qualified(TABLE)} WHERE week_folder = :w", w=week_folder)
    return None if df.empty else df.iloc[0].to_dict()


def latest() -> dict | None:
    ensure_schema()
    df = db.query(f"SELECT * FROM {db.qualified(TABLE)} ORDER BY created_at DESC LIMIT 1")
    return None if df.empty else df.iloc[0].to_dict()


def list_all() -> list[dict]:
    ensure_schema()
    df = db.query(
        f"SELECT week_folder, status, total, xlsx_path, created_at, updated_at, posted_at "
        f"FROM {db.qualified(TABLE)} ORDER BY created_at DESC"
    )
    return df.to_dict("records")
