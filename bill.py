"""Assemble + post a week's bill, then record the billed lines.

Replaces the graph's load_billing -> apply_charges -> post_to_erp -> mark_billed.
Selection + amounts are SQL (v_bill_lines); posting reuses the Famous client; a
tag becomes 'billed' by being written to cs_packing_billing_lines. The approval
gate is the dashboard calling post() — no interrupt, no suspended run.
"""

import pandas as pd

from . import actions, db

_DEFAULT_CUSTOMER = "1680"
_PACKING_SERVICES_CHARGE_ID = "1106"
# Famous charge id for storage service orders. No known default — set it here (or pass
# storage_charge_id=) once the ERP charge is created; until then post() skips the storage
# ERP push (and won't mark storage billed) so nothing is posted under the wrong charge.
_STORAGE_SERVICES_CHARGE_ID: str | None = None


def candidates() -> pd.DataFrame:
    """What to bill, rolled up by billing week x PACK/REPACK (with $ and missing-rate count)."""
    return db.query(f"SELECT * FROM {db.qualified('v_bill_candidates')}")


def lines(billing_week: str) -> pd.DataFrame:
    """The per-tag billable charge lines for one billing week."""
    return db.query(
        f"SELECT * FROM {db.qualified('v_bill_lines')} WHERE billing_week = :w ORDER BY transaction_type, tagid",
        w=billing_week,
    )


def for_period(start: str, end: str, status: str = "all") -> dict:
    """The bill for a ship-date period [start, end]: service charges, labor, total, lines.

    Sourced from v_charges (rate x shipped qty), so amounts are correct regardless
    of whether a tag was already billed. `status` scopes the period:
      'all'      every billable-reason carton shipped in the window (the full bill)
      'unbilled' only those not yet in cs_packing_billing_lines (what to invoice)
      'billed'   only those already billed (a reprint)
    """
    where = "ship_date BETWEEN :s AND :e"
    if status == "unbilled":
        where += " AND NOT billed"
    elif status == "billed":
        where += " AND billed"
    src = db.qualified("v_charges")

    svc = db.query(
        f"SELECT transaction_type, count(*) AS tags, sum(shipped_qty) AS qty, "
        f"       sum(amount) AS amount, count(*) FILTER (WHERE rate_missing) AS tags_missing_rate, "
        f"       count(*) FILTER (WHERE billed) AS tags_billed "
        f"FROM {src} WHERE {where} "
        f"GROUP BY transaction_type ORDER BY transaction_type",
        s=start, e=end,
    )
    labor = db.query(
        f"SELECT coalesce(sum(labor_charge), 0) AS amount FROM {db.qualified('labor_charges')} "
        r"WHERE week_end ~ '^\d{4}-\d{2}-\d{2}$' "
        f"AND to_date(week_end, 'YYYY-MM-DD') BETWEEN :s AND :e",
        s=start, e=end,
    )
    # Storage: per-carton days-in-storage charges over the same ship-date window/status.
    storage = db.query(
        f"SELECT count(*) AS pallets, coalesce(sum(amount), 0) AS amount, "
        f"       count(*) FILTER (WHERE rate_missing) AS pallets_missing_rate "
        f"FROM {db.qualified('v_storage_charges')} WHERE {where}",
        s=start, e=end,
    )
    lines_df = db.query(
        f"SELECT tagid, productname, transaction_type, ship_date, billing_week, "
        f"       commodity, style, bagtype, rate, shipped_qty, amount, rate_missing, billed "
        f"FROM {src} WHERE {where} "
        f"ORDER BY transaction_type, productname, tagid",
        s=start, e=end,
    )

    for col in ("ship_date", "billing_week"):
        if col in lines_df.columns:
            lines_df[col] = pd.to_datetime(lines_df[col], errors="coerce").dt.strftime("%Y-%m-%d")

    service_total = float(svc["amount"].fillna(0).sum()) if not svc.empty else 0.0
    labor_total = float(labor["amount"].iloc[0]) if not labor.empty else 0.0
    storage_total = float(storage["amount"].iloc[0]) if not storage.empty else 0.0
    return {
        "start": start,
        "end": end,
        "status": status,
        "service": svc.fillna(0).to_dict("records"),
        "service_total": round(service_total, 2),
        "labor_total": round(labor_total, 2),
        "storage_total": round(storage_total, 2),
        "storage_pallets": int(storage["pallets"].iloc[0]) if not storage.empty else 0,
        "storage_missing_rate": int(storage["pallets_missing_rate"].iloc[0]) if not storage.empty else 0,
        "total": round(service_total + labor_total + storage_total, 2),
        "tags_missing_rate": int(svc["tags_missing_rate"].sum()) if not svc.empty else 0,
        "tags_billed": int(svc["tags_billed"].sum()) if not svc.empty else 0,
        "lines": lines_df.astype(object).where(lines_df.notna(), "").to_dict("records"),
    }


def _billing_rows(df: pd.DataFrame, week: str) -> pd.DataFrame:
    """Map v_bill_lines rows to cs_packing_billing_lines columns for record_billed."""
    return pd.DataFrame({
        "tagid": df["tagid"],
        "icrunidx": df["icrunidx"],
        "productname": df["productname"],
        "productdescr": df["productname"],
        "fcreasonidx": df["fcreasonidx"],
        "shipped_qty": df["shipped_qty"],
        "qnt": df["shipped_qty"],
        "billed_amount": df["amount"],
        "ship_date": df["ship_date"].astype(str),
        "week_end": week,
        "charge_type": "PACKING",
    })


def _storage_billing_rows(df: pd.DataFrame, week: str) -> pd.DataFrame:
    """Map v_storage_charges rows to cs_packing_billing_lines columns (charge_type STORAGE)."""
    return pd.DataFrame({
        "tagid": df["tagid"],
        "sono": df["sono"],
        "billed_amount": df["amount"],
        "ship_date": df["ship_date"].astype(str),
        "week_end": week,
        "charge_type": "STORAGE",
    })


def invoice_for_period(start: str, end: str, status: str = "all",
                       output_path: str | None = None, **meta) -> dict:
    """Generate a PDF invoice for the period and return {path, lines, total}.

    Aggregates v_charges into one invoice line per (txn, type, commodity, style,
    bagtype, rate) and renders it with generate_pdf_invoice.generate_invoice.
    Rate-missing charges are excluded (they'd have no amount); the count is returned.
    """
    from datetime import date

    from standalone.generate_pdf_invoice.app import generate_invoice

    where = "ship_date BETWEEN :s AND :e"
    if status == "unbilled":
        where += " AND NOT billed"
    elif status == "billed":
        where += " AND billed"

    # Repacks bill on time & materials, not a per-carton service rate, so they're
    # not service-invoice lines here (their charge comes from labor + materials).
    g = db.query(
        f"SELECT transaction_type, type, commodity, style, bagtype, rate, "
        f"       sum(shipped_qty) AS quantity, sum(amount) AS amount, "
        f"       count(*) FILTER (WHERE rate_missing) AS missing "
        f"FROM {db.qualified('v_charges')} WHERE {where} AND NOT rate_missing "
        f"  AND transaction_type <> 'REPACK' "
        f"GROUP BY transaction_type, type, commodity, style, bagtype, rate "
        f"ORDER BY transaction_type, commodity, style",
        s=start, e=end,
    )
    skipped = int(db.query(
        f"SELECT count(*) AS n FROM {db.qualified('v_charges')} WHERE {where} AND rate_missing",
        s=start, e=end,
    ).iloc[0]["n"])

    # Storage charges: per-carton, billed on carton-days past the 7-day free period,
    # one invoice line per pack form. Rate-missing pallets are excluded like packing.
    storage = db.query(
        f"SELECT commodity, style, bagtype, rate, count(*) AS pallets, "
        f"       sum(shipped_qty * billable_days) AS carton_days, sum(amount) AS amount "
        f"FROM {db.qualified('v_storage_charges')} WHERE {where} AND NOT rate_missing "
        f"GROUP BY commodity, style, bagtype, rate ORDER BY commodity, style, bagtype",
        s=start, e=end,
    )
    skipped += int(db.query(
        f"SELECT count(*) AS n FROM {db.qualified('v_storage_charges')} WHERE {where} AND rate_missing",
        s=start, e=end,
    ).iloc[0]["n"])

    items = []
    for r in g.itertuples():
        prefix = "REPACK SERVICES" if r.transaction_type == "REPACK" else "PACKING SERVICES"
        parts = [str(p) for p in (r.type, r.commodity, r.style) if p]
        desc = f"{prefix} - " + " ".join(parts)
        if r.bagtype in ("COMBO", "NET", "POUCH"):
            desc += f" {r.bagtype}"
        items.append({
            "description": desc,
            "quantity": float(r.quantity or 0),
            "price": float(r.rate or 0),
            "amount": float(r.amount or 0),
        })
    for r in storage.itertuples():
        parts = [str(p) for p in (r.commodity, r.style, r.bagtype) if p]
        items.append({
            "description": "COLD STORAGE - " + " ".join(parts),
            "quantity": float(r.carton_days or 0),
            "price": float(r.rate or 0),
            "amount": float(r.amount or 0),
        })

    if not output_path:
        output_path = f"/tmp/invoice_{start}_to_{end}.pdf"

    period_meta: dict = {}
    try:
        end_d = date.fromisoformat(end)
        period_meta = {
            "invoice_date": date.today().strftime("%b %d, %Y"),
            "ship_date": end_d.strftime("%b %d, %Y"),
            "cust_po": f"PACKING WE {end_d.strftime('%m%d%y')}",
        }
    except ValueError:
        pass
    period_meta.update({k: v for k, v in meta.items() if v is not None})

    generate_invoice(items, output_path, **period_meta)
    return {
        "path": output_path,
        "lines": len(items),
        "total": round(sum(i["amount"] for i in items), 2),
        "skipped_no_rate": skipped,
    }


def _storage_lines(billing_week: str) -> pd.DataFrame:
    """Unbilled, rated storage charges for one billing week (what to post/record)."""
    return db.query(
        f"SELECT tagid, sono, ship_date, billable_days, rate, amount "
        f"FROM {db.qualified('v_storage_charges')} "
        f"WHERE billing_week = :w AND NOT billed AND NOT rate_missing "
        f"ORDER BY tagid",
        w=billing_week,
    )


def post(billing_week: str, customer_id: str = _DEFAULT_CUSTOMER,
         charge_id: str = _PACKING_SERVICES_CHARGE_ID,
         storage_charge_id: str | None = _STORAGE_SERVICES_CHARGE_ID,
         comment: str | None = None) -> dict:
    """Post the week's PACK and STORAGE orders to Famous, then record billed lines.

    External: requires the Famous ERP. Repacks bill via T&M (nothing to post). Each
    charge that posts OK is recorded as billed (charge_type PACKING/STORAGE), so a
    partial failure is safe to retry. Storage is skipped unless a storage_charge_id
    is configured (no wrong-charge posts).
    """
    from datetime import date
    from agent.shared.famous.base.client import FamousClient, FamousError
    from agent.shared.famous.salesorder.import_order_file import build_order_payload

    df = lines(billing_week)
    sto = _storage_lines(billing_week)
    if df.empty and sto.empty:
        return {"ok": True, "results": [], "note": f"nothing to bill for {billing_week}"}

    week_end = date.fromisoformat(billing_week)
    we_label = week_end.strftime("%m.%d.%Y")
    results, posted = [], []

    for txn, grp in df.groupby("transaction_type"):
        if txn == "REPACK":
            # Repacks bill on time & materials (labor_charges + repack_materials),
            # not a per-carton service order, so there's nothing to post here.
            results.append({"transaction_type": txn, "ok": True,
                            "skipped": "repack billed via T&M (labor + materials)"})
            continue
        amount = float(grp["amount"].sum())
        try:
            payload_lines, header = build_order_payload(
                customer_id=customer_id, ship_date=week_end, week_end=week_end,
                order_date=week_end, delivery_date=week_end, amount=amount, quantity=0,
                charge_id=charge_id, comment=comment or f"{txn} SERVICES - {we_label}",
                po_number=f"{txn} WE {we_label}",
            )
            client = FamousClient()
            token = client.login()
            try:
                resp = client.fapi(token, "AROrderFile", f"<Payload>\n{payload_lines}</Payload>")
            finally:
                client.logout(token)
            results.append({"transaction_type": txn, "ok": True, "amount": amount, "response": resp})
            posted.append(txn)
        except FamousError as e:
            results.append({"transaction_type": txn, "ok": False, "stage": e.stage, "error": str(e)})
        except Exception as e:
            results.append({"transaction_type": txn, "ok": False, "error": str(e)})

    recorded = 0
    if posted:
        recorded = actions.record_billed(_billing_rows(df[df["transaction_type"].isin(posted)], billing_week))

    # Storage: one service order for the week's storage total (like a PACK order).
    storage_recorded = 0
    if not sto.empty:
        if storage_charge_id is None:
            results.append({"transaction_type": "STORAGE", "ok": True,
                            "skipped": "no storage_charge_id configured (see _STORAGE_SERVICES_CHARGE_ID)"})
        else:
            amount = float(sto["amount"].sum())
            try:
                payload_lines, header = build_order_payload(
                    customer_id=customer_id, ship_date=week_end, week_end=week_end,
                    order_date=week_end, delivery_date=week_end, amount=amount, quantity=0,
                    charge_id=storage_charge_id, comment=comment or f"STORAGE - {we_label}",
                    po_number=f"STORAGE WE {we_label}",
                )
                client = FamousClient()
                token = client.login()
                try:
                    resp = client.fapi(token, "AROrderFile", f"<Payload>\n{payload_lines}</Payload>")
                finally:
                    client.logout(token)
                results.append({"transaction_type": "STORAGE", "ok": True, "amount": amount, "response": resp})
                storage_recorded = actions.record_billed(_storage_billing_rows(sto, billing_week))
            except FamousError as e:
                results.append({"transaction_type": "STORAGE", "ok": False, "stage": e.stage, "error": str(e)})
            except Exception as e:
                results.append({"transaction_type": "STORAGE", "ok": False, "error": str(e)})

    return {"ok": all(r["ok"] for r in results), "results": results,
            "recorded": recorded, "storage_recorded": storage_recorded}
