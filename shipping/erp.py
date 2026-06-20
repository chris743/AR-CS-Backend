"""Compute the week's total and submit one ImportOrderFile to Famous.

Ports post_to_erp.py's non-interrupt half (the interrupt becomes flow.post()'s
approval gate). The Famous client + payload builder are imported lazily, the same
way standalone.bill.post() reaches the shared ERP code.
"""

import re
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

import pandas as pd

from . import config

_WE_RE = re.compile(config.WE_FOLDER_REGEX)
_DOCUMENT_TYPE = "AROrderFile"


def week_end_from_folder(week_folder: str) -> date:
    m = _WE_RE.match(week_folder or "")
    if not m:
        raise ValueError(f"Invalid week_folder format: {week_folder!r}")
    mm, dd, yyyy = m.groups()
    return date(int(yyyy), int(mm), int(dd))


def compute_total(combined_csv_path: str, phyto_results: list[dict] | None) -> Decimal:
    """Shipping-charge amounts + matched phyto debit amounts, rounded to cents."""
    df = pd.read_csv(combined_csv_path)
    shipping_total = Decimal(str(df["amt"].fillna(0).sum()))
    phyto_total = Decimal("0")
    for r in phyto_results or []:
        amt = (r.get("match") or {}).get("debit_amount")
        if amt is not None:
            phyto_total += Decimal(str(amt))
    return (shipping_total + phyto_total).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def post_to_erp(*, combined_csv_path: str, phyto_results: list[dict] | None,
                week_folder: str, customer_id: str | None = None,
                charge_id: str | None = None, po_number: str | None = None,
                comment: str | None = None) -> dict:
    """Build and submit one ImportOrderFile for the week. Never raises."""
    from agent.shared.famous.base.client import FamousClient, FamousError, FamousLogoutError
    from agent.shared.famous.salesorder.import_order_file import (
        build_order_payload, default_charge_id, default_customer_id,
    )

    try:
        amount = compute_total(combined_csv_path, phyto_results)
    except Exception as e:
        return {"ok": False, "stage": "load", "error": str(e)}
    if amount <= 0:
        return {"ok": False, "stage": "load", "error": f"Computed amount is {amount}; nothing to post."}

    try:
        week_end_date = week_end_from_folder(week_folder)
    except ValueError as e:
        return {"ok": False, "stage": "load", "error": str(e)}

    bill_to = (customer_id or default_customer_id()).strip()
    cid = (charge_id or default_charge_id()).strip()

    try:
        payload_lines, header_meta = build_order_payload(
            customer_id=bill_to, ship_date=week_end_date, week_end=week_end_date,
            amount=amount, charge_id=cid, comment=comment, po_number=po_number,
        )
    except ValueError as e:
        return {"ok": False, "stage": "build", "error": str(e)}
    except Exception as e:
        return {"ok": False, "stage": "build", "error": str(e)}

    payload = f"<Payload>\n{payload_lines}\n</Payload>"
    client = FamousClient()
    token = None
    try:
        token = client.login()
        response_text = client.fapi(token, _DOCUMENT_TYPE, payload)
    except FamousError as e:
        return {"ok": False, "stage": e.stage, "error": str(e),
                "famousResponseText": getattr(e, "response_text", None),
                "week_end": week_end_date.isoformat()}
    finally:
        if token:
            try:
                client.logout(token)
            except FamousLogoutError:
                pass

    return {"ok": True, "week_end": week_end_date.isoformat(),
            "customer_id": bill_to, "charge_id": cid,
            "amount": header_meta["amount"], "header": header_meta,
            "famousResponseText": response_text}
