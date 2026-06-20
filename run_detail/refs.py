"""Reference data for the run-detail form: positions, materials (with their
maintainable default prices), and defect types. These drive the form's dropdowns.

ref_material.default_price is the editable "rate" — seeded from the current sheet
and maintained by ops via set_material_price().
"""

from sqlalchemy import text

from .. import db
from .store import ensure_schema, _json_row


def all_refs() -> dict:
    """Everything the form needs to render its selects, in display order."""
    ensure_schema()
    positions = db.query(f"SELECT name, seq FROM {db.qualified('ref_position')} ORDER BY seq, name")
    materials = db.query(
        f"SELECT name, category, default_price, unit_label, seq "
        f"FROM {db.qualified('ref_material')} ORDER BY seq, name"
    )
    defects = db.query(f"SELECT name, seq FROM {db.qualified('ref_defect')} ORDER BY seq, name")
    return {
        "positions": [_json_row(r) for r in positions.to_dict("records")],
        "materials": [_json_row(r) for r in materials.to_dict("records")],
        "defects": [_json_row(r) for r in defects.to_dict("records")],
        "material_categories": ["pallet", "corner_board", "other"],
        "run_types": ["REPACK-QUALITY", "PACK", "RESTYLE"],
    }


def set_material_price(name: str, default_price: float, *, category: str | None = None,
                       unit_label: str | None = None, seq: int | None = None) -> dict:
    """Upsert a material rate (ops maintenance). Returns the row."""
    ensure_schema()
    with db.engine().begin() as conn:
        conn.execute(
            text(f"INSERT INTO {db.qualified('ref_material')} (name, category, default_price, unit_label, seq) "
                 f"VALUES (:n, :c, :p, :u, :s) "
                 f"ON CONFLICT (name) DO UPDATE SET default_price = EXCLUDED.default_price, "
                 f"  category = COALESCE(EXCLUDED.category, {db.qualified('ref_material')}.category), "
                 f"  unit_label = COALESCE(EXCLUDED.unit_label, {db.qualified('ref_material')}.unit_label), "
                 f"  seq = COALESCE(EXCLUDED.seq, {db.qualified('ref_material')}.seq)"),
            {"n": name, "c": category, "p": default_price, "u": unit_label, "s": seq},
        )
    row = db.query(f"SELECT name, category, default_price, unit_label, seq "
                   f"FROM {db.qualified('ref_material')} WHERE name = :n", n=name)
    return _json_row(row.iloc[0].to_dict())
