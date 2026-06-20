"""Labor charges, orchestrator-free.

The email round-trip that the graph handled with a suspend-on-interrupt becomes
two independent operations plus a state table:

  needed(week)          repack runs (reason 41) that shipped, still needing labor   [query]
  email_request(...)    send the labor sheet, mark a labor_requests row 'pending'   [Graph + table]
  record_from_reply(..) read ops' reply, write labor_charges, mark 'received'        [Graph + table]
  record(df, week)      low-level: persist labor rows for a week                     [table]
  status()              the labor_requests audit                                     [query]

The mail webhook (standalone/webhook.py) calls record_from_reply() when ops
replies — no run is held open; the mailbox + labor_requests are the state.
"""

import io
import os

import pandas as pd
from sqlalchemy import text

from . import db
from .config import SCHEMA

LABOR_TABLE = "labor_charges"
REQUESTS_TABLE = "labor_requests"
_REPACK = 41
_SUBJECT = "Repacks Needing Labor Charges"


def needed(week: str | None = None) -> pd.DataFrame:
    """Repack runs (reason 41) that shipped and have no labor recorded yet.

    One row per icrunidx, scoped to a billing week when given.
    """
    where = "transaction_type = 'REPACK'"
    params = {}
    if week:
        where += " AND billing_week = :w"
        params["w"] = week
    return db.query(
        f"SELECT icrunidx, min(productname) AS productdescr, "
        f"       min(billing_week::text) AS billing_week, sum(shipped_qty) AS quantity "
        f"FROM {db.qualified('v_bill_lines')} "
        f"WHERE {where} "
        f"  AND icrunidx NOT IN (SELECT icrunidx FROM {db.qualified(LABOR_TABLE)} WHERE icrunidx IS NOT NULL) "
        f"GROUP BY icrunidx ORDER BY icrunidx",
        **params,
    )


def record(df: pd.DataFrame, week: str, hourly_rate: float | None = None) -> int:
    """Persist labor rows for a billing week; computes labor_charge."""
    rate = float(hourly_rate if hourly_rate is not None else os.getenv("LABOR_HOURLY_RATE", "0") or 0)
    out = df.copy()
    out["week_end"] = week
    nl = pd.to_numeric(out.get("num_laborers"), errors="coerce").fillna(0)
    hr = pd.to_numeric(out.get("hours_per_laborer"), errors="coerce").fillna(0)
    out["labor_rate"] = rate
    out["labor_charge"] = (nl * hr * rate).round(2)
    n = db.append_aligned(out, LABOR_TABLE)
    return n


def status() -> pd.DataFrame:
    """The labor-request audit (what was sent, what's still pending)."""
    return db.query(
        f"SELECT week, to_address, run_count, status, sent_at, received_at "
        f"FROM {db.qualified(REQUESTS_TABLE)} ORDER BY week DESC"
    )


# --- external edge: email round-trip (reuses agent.shared Graph helpers) -------

def email_request(to_address: str, week: str) -> dict:
    """Email ops the labor sheet for `week`; record a pending labor_requests row."""
    import requests
    from agent.shared.microsoft.get_microsoft_token import get_graph_token
    from agent.cs_packing_charges.nodes.send_email import (
        ATTACHMENT_NAME, _XLSX_CONTENT_TYPE, _labor_xlsx_b64,
    )

    rows = needed(week).assign(num_laborers="", hours_per_laborer="").to_dict("records")
    if not rows:
        return {"ok": True, "note": f"no repack runs need labor for {week}"}

    token = get_graph_token()
    agent_email = os.getenv("AGENT_EMAIL")
    payload = {
        "message": {
            "subject": f"{_SUBJECT} {week}",
            "body": {"contentType": "HTML", "content":
                     f"<p>Fill in <b>num_laborers</b> / <b>hours_per_laborer</b> in the attached "
                     f"{ATTACHMENT_NAME} and reply.</p>"},
            "toRecipients": [{"emailAddress": {"address": to_address}}],
            "attachments": [{
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": ATTACHMENT_NAME, "contentType": _XLSX_CONTENT_TYPE,
                "contentBytes": _labor_xlsx_b64(rows),
            }],
        },
        "saveToSentItems": True,
    }
    r = requests.post(
        f"https://graph.microsoft.com/v1.0/users/{agent_email}/sendMail",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload, timeout=30,
    )
    ok = r.status_code == 202
    if ok:
        with db.engine().begin() as conn:
            conn.execute(
                text(
                    f"INSERT INTO {db.qualified(REQUESTS_TABLE)} (week, to_address, run_count, status, sent_at) "
                    "VALUES (:w, :a, :n, 'pending', now()) "
                    "ON CONFLICT (week) DO UPDATE SET to_address = EXCLUDED.to_address, "
                    "run_count = EXCLUDED.run_count, status = 'pending', sent_at = now(), received_at = NULL"
                ),
                {"w": week, "a": to_address, "n": len(rows)},
            )
    return {"ok": ok, "status": r.status_code, "runs": len(rows)}


def record_from_reply(week: str, hourly_rate: float | None = None) -> dict:
    """Read ops' reply for `week`, write labor_charges, mark the request received.

    Called by the mail webhook (or manually). No suspended run.
    """
    from agent.shared.microsoft.get_microsoft_token import get_graph_token
    from agent.cs_packing_charges.nodes.read_labor_charges import _find_reply_xlsx

    token = get_graph_token()
    agent_email = os.getenv("AGENT_EMAIL")
    xlsx = _find_reply_xlsx(token, agent_email, week)
    df = pd.read_excel(io.BytesIO(xlsx))
    n = record(df, week, hourly_rate)
    with db.engine().begin() as conn:
        conn.execute(
            text(
                f"UPDATE {db.qualified(REQUESTS_TABLE)} SET status = 'received', received_at = now() "
                "WHERE week = :w"
            ),
            {"w": week},
        )
    return {"ok": True, "week": week, "rows": n}
