"""EDGE: state-changing billing actions.

A tag becomes "billed" by writing a row to cs_packing_billing_lines. That's the
only mutation billing needs here. The external side effects that must happen
*around* it — posting the order to the Famous ERP and the labor-sheet email
round-trip — are intentionally out of this module's core: they are plain client
calls (reuse graph_app/src/agent/shared/famous and .../microsoft) triggered by
the dashboard/webhook, not orchestrated state.
"""

import pandas as pd

from . import db

BILLING_LINES = "cs_packing_billing_lines"


def record_billed(rows: pd.DataFrame, source: str = "standalone") -> int:
    """Write billing lines (tags become 'billed'). Columns are aligned to the table.

    Expected/honored columns: tagid, icrunidx, sono, productname, productdescr,
    shipped_qty, qnt, billed_amount, ship_date, week_end. Unknown columns are
    ignored; missing table columns are left NULL.
    """
    rows = rows.copy()
    rows["source"] = source
    return db.append_aligned(rows, BILLING_LINES)


def already_billed_tags() -> set[int]:
    """Distinct tagids currently recorded as billed."""
    df = db.query(
        f"SELECT DISTINCT tagid FROM {db.qualified(BILLING_LINES)} WHERE tagid IS NOT NULL"
    )
    return {int(t) for t in df["tagid"].dropna()}
