"""Persistence for run-detail submissions.

A save is document-style: upsert the parent, replace all child lines, and compute
the rollups (labor, overhead, materials, total) server-side so the client can't
desync them. Schema (tables + seeded reference data) is applied lazily from
sql/run_detail.sql — idempotent, so first write self-heals an empty DB.
"""

from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from sqlalchemy import text

from .. import db

_SQL = Path(__file__).resolve().parent / "sql" / "run_detail.sql"
_schema_ready = False

_PARENT_COLS = (
    "run_no", "run_date", "run_type", "note", "company", "commodity", "varieties",
    "repack", "restyle", "pack_out_pct", "start_time", "start_lunch", "end_lunch",
    "finish_time", "input_units", "output_units", "overhead_rate",
    "total_labor", "overhead", "new_materials", "total", "status",
)
_LABOR_COLS = ("seq", "position", "num_workers", "reg_hours", "ot_hours",
               "total_hours", "reg_pay", "ot_pay", "total_pay")
_IO_COLS = ("seq", "io", "boxes", "label", "pack_style", "size")
_MATERIAL_COLS = ("seq", "category", "material", "quantity", "price_per_unit", "total")
_DEFECT_COLS = ("seq", "defect_type", "value")

_CHILDREN = {
    "run_labor": ("labor", _LABOR_COLS),
    "run_io": ("io", _IO_COLS),
    "run_material": ("material", _MATERIAL_COLS),
    "run_defect": ("defect", _DEFECT_COLS),
}


def ensure_schema() -> None:
    global _schema_ready
    if not _schema_ready:
        db.run_sql_file(_SQL)
        _schema_ready = True


def _num(v):
    if v in (None, ""):
        return None
    return Decimal(str(v))


def _money(v) -> float:
    return float(Decimal(str(v or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _compute(payload: dict) -> dict:
    """Derive line + rollup amounts from the submitted payload. Returns a fresh dict."""
    p = dict(payload)

    materials = [dict(m) for m in (p.get("material") or p.get("materials") or [])]
    for m in materials:
        qty, price = _num(m.get("quantity")), _num(m.get("price_per_unit"))
        m["total"] = _money(qty * price) if (qty is not None and price is not None) else m.get("total")
    p["material"] = materials

    labor = list(p.get("labor") or [])
    total_labor = sum((_num(l.get("total_pay")) or Decimal(0)) for l in labor)
    rate = _num(p.get("overhead_rate"))
    rate = Decimal("0.20") if rate is None else rate
    new_materials = sum((_num(m.get("total")) or Decimal(0)) for m in materials)

    p["overhead_rate"] = float(rate)
    p["total_labor"] = _money(total_labor)
    p["overhead"] = _money(total_labor * rate)
    p["new_materials"] = _money(new_materials)
    p["total"] = _money(total_labor + total_labor * rate + new_materials)
    return p


def save(payload: dict) -> dict:
    """Upsert a run and its child lines; returns the stored nested record."""
    if payload.get("run_no") in (None, ""):
        raise ValueError("run_no is required")
    ensure_schema()
    p = _compute(payload)
    run_no = int(p["run_no"])

    parent = {c: p.get(c) for c in _PARENT_COLS if c in p}
    parent["run_no"] = run_no
    cols = list(parent)
    set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "run_no")
    insert_sql = (
        f"INSERT INTO {db.qualified('run_detail')} ({', '.join(cols)}) "
        f"VALUES ({', '.join(':' + c for c in cols)}) "
        f"ON CONFLICT (run_no) DO UPDATE SET {set_clause}, updated_at = now()"
    )

    with db.engine().begin() as conn:
        conn.execute(text(insert_sql), parent)
        for table, (key, child_cols) in _CHILDREN.items():
            conn.execute(text(f"DELETE FROM {db.qualified(table)} WHERE run_no = :r"), {"r": run_no})
            rows = p.get(key) or []
            for i, row in enumerate(rows):
                rec = {c: row.get(c) for c in child_cols}
                rec.setdefault("seq", i)
                if rec.get("seq") is None:
                    rec["seq"] = i
                rec["run_no"] = run_no
                names = ["run_no", *child_cols]
                conn.execute(
                    text(f"INSERT INTO {db.qualified(table)} ({', '.join(names)}) "
                         f"VALUES ({', '.join(':' + c for c in names)})"),
                    rec,
                )
    return get(run_no)


def get(run_no: int) -> dict | None:
    ensure_schema()
    parent = db.query(f"SELECT * FROM {db.qualified('run_detail')} WHERE run_no = :r", r=run_no)
    if parent.empty:
        return None
    rec = _json_row(parent.iloc[0].to_dict())
    for table, (key, _) in _CHILDREN.items():
        child = db.query(
            f"SELECT * FROM {db.qualified(table)} WHERE run_no = :r ORDER BY seq, id", r=run_no
        )
        rec[key] = [_json_row(r) for r in child.to_dict("records")]
    return rec


def list_runs(week: str | None = None, status: str | None = None,
              start: str | None = None, end: str | None = None) -> list[dict]:
    ensure_schema()
    clauses, params = ["TRUE"], {}
    if status:
        clauses.append("status = :st")
        params["st"] = status
    if start and end:
        clauses.append("run_date BETWEEN :s AND :e")
        params["s"], params["e"] = start, end
    if week:
        clauses.append("(run_date + mod(7 - extract(dow FROM run_date)::int, 7) * interval '1 day')::date = :w")
        params["w"] = week
    df = db.query(
        f"SELECT run_no, run_date, repack, commodity, varieties, restyle, "
        f"       total_labor, overhead, new_materials, total, status, updated_at "
        f"FROM {db.qualified('run_detail')} WHERE {' AND '.join(clauses)} "
        f"ORDER BY run_date DESC NULLS LAST, run_no DESC",
        **params,
    )
    return [_json_row(r) for r in df.to_dict("records")]


def delete(run_no: int) -> bool:
    ensure_schema()
    with db.engine().begin() as conn:
        res = conn.execute(
            text(f"DELETE FROM {db.qualified('run_detail')} WHERE run_no = :r"), {"r": run_no}
        )
    return bool(res.rowcount)


def _json_row(row: dict) -> dict:
    """Coerce a DB row to JSON-safe python (Decimal/date/NaT -> float/str/None)."""
    import math

    out = {}
    for k, v in row.items():
        if k == "id":
            continue
        if isinstance(v, Decimal):
            out[k] = float(v)
        elif v is not None and hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif isinstance(v, float) and not math.isfinite(v):
            out[k] = None
        else:
            try:
                import pandas as pd
                out[k] = None if (not isinstance(v, (list, dict)) and pd.isna(v)) else v
            except (TypeError, ValueError):
                out[k] = v
    return out
