"""Run Detail capture — backend for the "Creekside Organics Labor Detail" webform.

Replaces the per-run spreadsheet tab (and the labor email round-trip) with a
structured, computed record. One submission per run (run_no = repack icrunidx),
with Labor / New Materials / Defects / Inputs-Outputs as child collections and
server-computed rollups (labor + 20% overhead + materials = the run's T&M total).

Public API re-exported for the server/CLI; HTTP routes in routes.py.
"""

from .store import save, get, list_runs, delete, ensure_schema
from .refs import all_refs, set_material_price

__all__ = ["save", "get", "list_runs", "delete", "ensure_schema",
           "all_refs", "set_material_price"]
