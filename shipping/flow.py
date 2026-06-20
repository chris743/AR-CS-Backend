"""Orchestrates the shipping-charges flow without LangGraph.

run()    fetch -> trim -> phytos -> extract+match -> combine -> xlsx -> persist
review() apply human corrections to flagged phytos, then re-combine/re-build
post()   the approval gate: compute total + submit ImportOrderFile to Famous

The graph fanned out fetch_report || fetch_phytos; here run() does them in
sequence (the slow part is per-PDF extraction, run concurrently with asyncio).
"""

import asyncio
import json

from . import config, erp, invoice, phytos, reports, store

_JSON_FIELDS = ("result", "needs_review", "erp_result")


# --- helpers ----------------------------------------------------------------

def _needs_review(entry: dict) -> bool:
    """A phyto needs human attention if it has no order number or no debit match."""
    if entry.get("order_number") in (0, None):
        return True
    match = entry.get("match") or {}
    return not match.get("matched", False)


def _coerce(run: dict | None) -> dict | None:
    """Parse jsonb columns that may come back as strings; make JSON-safe."""
    if run is None:
        return None
    out = dict(run)
    for f in _JSON_FIELDS:
        v = out.get(f)
        if isinstance(v, str):
            try:
                out[f] = json.loads(v)
            except (ValueError, TypeError):
                pass
    if out.get("total") is not None:
        out["total"] = float(out["total"])
    for f in ("created_at", "updated_at", "posted_at"):
        if out.get(f) is not None and hasattr(out[f], "isoformat"):
            out[f] = out[f].isoformat()
    return out


def _summary(run: dict) -> dict:
    """The shape returned to API callers: status + counts + paths, no heavy blobs."""
    result = run.get("result") or []
    needs = run.get("needs_review") or []
    return {
        "week_folder": run.get("week_folder"),
        "status": run.get("status"),
        "total": run.get("total"),
        "phyto_count": len(result),
        "needs_review_count": len(needs),
        "needs_review": needs,
        "xlsx_path": run.get("xlsx_path"),
        "posted_at": run.get("posted_at"),
        "erp_result": run.get("erp_result"),
    }


async def _extract_one(pdf_path: str) -> dict:
    from .extract import run_phyto_agent, match_debit_row

    extraction = await run_phyto_agent(pdf_path)
    matched = match_debit_row(extraction.current_certificate_number, extraction.debit_rows)
    return {"file": pdf_path, "order_number": extraction.order_number, "match": matched}


# --- public API -------------------------------------------------------------

async def run(week_folder: str | None = None) -> dict:
    """Build a week's shipping-charges invoice and persist it. Returns a summary."""
    try:
        report_path = reports.download_shipping_report()
        trimmed_path = reports.trim_report(report_path)

        phyto_paths, week_folder = phytos.fetch_phyto_reports(week_folder)
        result = list(await asyncio.gather(*(_extract_one(p) for p in phyto_paths)))

        combined_path = invoice.combine_reports(trimmed_path, result)
        xlsx_path_ = invoice.build_xlsx(combined_path, week_folder, result)
        total = float(erp.compute_total(combined_path, result))

        needs = [r for r in result if _needs_review(r)]
        status = "needs_review" if needs else "built"

        store.upsert(week_folder, status=status, report_path=report_path,
                     trimmed_path=trimmed_path, combined_path=combined_path,
                     xlsx_path=xlsx_path_, result=result, needs_review=needs,
                     total=total, error=None)
        return _summary(_coerce(store.get(week_folder)))
    except Exception as e:
        if week_folder:
            store.upsert(week_folder, status="error", error=str(e))
        raise


def review(week_folder: str, corrections: list[dict]) -> dict:
    """Apply human corrections (matched by 'file') and re-combine / re-build.

    Each correction: {"file": <path>, "order_number": int, "match": {...}}.
    """
    run = _coerce(store.get(week_folder))
    if run is None:
        raise KeyError(f"no shipping run for {week_folder!r}")

    result = list(run.get("result") or [])
    by_file = {c["file"]: c for c in corrections}
    result = [by_file.get(r["file"], r) for r in result]

    combined_path = invoice.combine_reports(run["trimmed_path"], result)
    xlsx_path_ = invoice.build_xlsx(combined_path, week_folder, result)
    total = float(erp.compute_total(combined_path, result))
    needs = [r for r in result if _needs_review(r)]
    status = "needs_review" if needs else "built"

    store.upsert(week_folder, status=status, combined_path=combined_path,
                 xlsx_path=xlsx_path_, result=result, needs_review=needs, total=total)
    return _summary(_coerce(store.get(week_folder)))


def post(week_folder: str, approve: bool = False, **overrides) -> dict:
    """Approval gate: submit the week's ImportOrderFile to Famous, then record it.

    External: requires the Famous ERP. Pass approve=True to submit.
    """
    run = _coerce(store.get(week_folder))
    if run is None:
        raise KeyError(f"no shipping run for {week_folder!r}")
    if not approve:
        return {"ok": False, "stage": "review", "error": "not approved",
                "week_folder": week_folder, "total": run.get("total")}

    res = erp.post_to_erp(combined_csv_path=run["combined_path"],
                          phyto_results=run.get("result") or [],
                          week_folder=week_folder, **overrides)
    store.mark_posted(week_folder, res)
    return res


def get(week_folder: str | None = None) -> dict | None:
    """Full run row (most recent if week_folder omitted)."""
    return _coerce(store.get(week_folder) if week_folder else store.latest())


def list_runs() -> list[dict]:
    runs = store.list_all()
    for r in runs:
        if r.get("total") is not None:
            r["total"] = float(r["total"])
        for f in ("created_at", "updated_at", "posted_at"):
            if r.get(f) is not None and hasattr(r[f], "isoformat"):
                r[f] = r[f].isoformat()
    return runs


def xlsx_path(week_folder: str | None = None) -> str | None:
    run = store.get(week_folder) if week_folder else store.latest()
    return run.get("xlsx_path") if run else None
