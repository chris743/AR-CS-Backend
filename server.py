"""Billing UI + API server (no LangGraph).

Serves the bill page and the JSON/PDF API the React Accounting module consumes
(see BACKEND_API_TODO.md). Every route is read-through to the standalone package;
all billing logic lives in the creekside_core SQL views.

Run:  venv/bin/python -m uvicorn standalone.server:app --port 8100
"""

import datetime as dt
import math
from pathlib import Path

import numpy as np
import pandas as pd
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.routing import Route

try:  # works whether launched as `standalone.server` or as a top-level module
    from . import bill, classify, ingest, labor, queries, rates
    from .shipping.routes import routes as shipping_routes
    from .run_detail.routes import routes as run_detail_routes
except ImportError:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from standalone import bill, classify, ingest, labor, queries, rates
    from standalone.shipping.routes import routes as shipping_routes
    from standalone.run_detail.routes import routes as run_detail_routes

_UI = Path(__file__).with_name("bill_ui.html")


# --- JSON serialization -----------------------------------------------------

def _json_safe(v):
    """Coerce a DataFrame cell to a JSON-serializable value (NaN/NaT->None, etc.)."""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    try:
        if v is None or (not isinstance(v, (list, dict, tuple)) and pd.isna(v)):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return float(v)
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, (dt.date, dt.datetime, pd.Timestamp)):
        return v.isoformat()
    return v


def _records(df: pd.DataFrame) -> list[dict]:
    return [{k: _json_safe(val) for k, val in row.items()} for row in df.to_dict("records")]


async def _body(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:
        return {}


def _ok(payload):
    return JSONResponse(payload)


def _err(msg: str, status: int = 500):
    return JSONResponse({"error": msg}, status_code=status)


# --- existing routes --------------------------------------------------------

async def index(request: Request) -> HTMLResponse:
    return HTMLResponse(_UI.read_text(encoding="utf-8"))


async def api_bill(request: Request) -> JSONResponse:
    start = request.query_params.get("start")
    end = request.query_params.get("end")
    status = request.query_params.get("status", "all")
    if not start or not end:
        return _err("start and end (YYYY-MM-DD) are required", 400)
    try:
        return _ok(bill.for_period(start, end, status))
    except Exception as e:
        return _err(str(e))


async def api_invoice(request: Request) -> FileResponse | JSONResponse:
    start = request.query_params.get("start")
    end = request.query_params.get("end")
    status = request.query_params.get("status", "all")
    if not start or not end:
        return _err("start and end (YYYY-MM-DD) are required", 400)
    try:
        out = f"/tmp/invoice_{start}_to_{end}.pdf"
        bill.invoice_for_period(start, end, status, output_path=out)
        return FileResponse(out, media_type="application/pdf",
                            filename=f"invoice_{start}_{end}.pdf")
    except Exception as e:
        return _err(str(e))


# --- meta -------------------------------------------------------------------

async def api_meta(request: Request) -> JSONResponse:
    # queries.meta() already returns db_ok:false (not an error) when the DB is down.
    return _ok(queries.meta())


# --- billing ----------------------------------------------------------------

async def api_bill_candidates(request: Request) -> JSONResponse:
    qp = request.query_params
    start = qp.get("start") or None
    end = qp.get("end") or None
    status = qp.get("status", "unbilled")
    try:
        return _ok(_records(queries.bill_candidates(start, end, status)))
    except Exception as e:
        return _err(str(e))


async def api_bill_summary(request: Request) -> JSONResponse:
    try:
        return _ok(_records(queries.bill_summary()))
    except Exception as e:
        return _err(str(e))


async def api_bill_post(request: Request) -> JSONResponse:
    body = await _body(request)
    week = body.get("week")
    if not week:
        return _err("week (YYYY-MM-DD) is required", 400)
    try:
        return _ok(bill.post(week))
    except Exception as e:
        return _err(str(e))


# --- reconciliation ---------------------------------------------------------

async def api_status_summary(request: Request) -> JSONResponse:
    try:
        return _ok(_records(queries.summary()))
    except Exception as e:
        return _err(str(e))


async def api_status(request: Request) -> JSONResponse:
    qp = request.query_params
    wh = qp.get("wh_status") or None
    # Contract: missing/empty/"null" billing_status => filter to NULL reason code.
    raw_bs = qp.get("billing_status")
    billing_null = raw_bs in (None, "", "null")
    bs = None if billing_null else raw_bs
    try:
        limit = int(qp.get("limit", 25))
        offset = int(qp.get("offset", 0))
    except ValueError:
        return _err("limit and offset must be integers", 400)
    try:
        df = queries.status_page(wh_status=wh, billing_status=bs,
                                 billing_null=billing_null, limit=limit, offset=offset)
        return _ok(_records(df))
    except Exception as e:
        return _err(str(e))


async def api_chain(request: Request) -> JSONResponse:
    try:
        tag = int(request.path_params["tag"])
    except (KeyError, ValueError):
        return _err("tag must be an integer", 400)
    try:
        return _ok(_records(queries.chain(tag)))
    except Exception as e:
        return _err(str(e))


# --- labor ------------------------------------------------------------------

async def api_labor_needed(request: Request) -> JSONResponse:
    week = request.query_params.get("week") or None
    try:
        return _ok(_records(labor.needed(week)))
    except Exception as e:
        return _err(str(e))


async def api_labor_status(request: Request) -> JSONResponse:
    try:
        return _ok(_records(labor.status()))
    except Exception as e:
        return _err(str(e))


async def api_labor_request(request: Request) -> JSONResponse:
    body = await _body(request)
    to_address, week = body.get("to_address"), body.get("week")
    if not to_address or not week:
        return _err("to_address and week are required", 400)
    try:
        res = labor.email_request(to_address, week)
        ok = bool(res.get("ok"))
        # Shape to the documented contract: {ok, status, runs}
        return _ok({"ok": ok, "status": "pending" if ok else "error",
                    "runs": int(res.get("runs", 0))})
    except Exception as e:
        return _err(str(e))


# --- maintenance ------------------------------------------------------------

async def api_ingest(request: Request) -> JSONResponse:
    try:
        return _ok(ingest.ingest_all())
    except Exception as e:
        return _err(str(e))


async def api_rates_mirror(request: Request) -> JSONResponse:
    try:
        return _ok({"rows": rates.mirror()})
    except Exception as e:
        return _err(str(e))


async def api_classification_refresh(request: Request) -> JSONResponse:
    try:
        return _ok(classify.refresh())
    except Exception as e:
        return _err(str(e))


app = Starlette(
    routes=[
        Route("/", index, methods=["GET"]),
        Route("/api/meta", api_meta, methods=["GET"]),
        Route("/api/bill", api_bill, methods=["GET"]),
        Route("/api/invoice.pdf", api_invoice, methods=["GET"]),
        Route("/api/bill/candidates", api_bill_candidates, methods=["GET"]),
        Route("/api/bill/summary", api_bill_summary, methods=["GET"]),
        Route("/api/bill/post", api_bill_post, methods=["POST"]),
        Route("/api/status/summary", api_status_summary, methods=["GET"]),
        Route("/api/status", api_status, methods=["GET"]),
        Route("/api/chain/{tag}", api_chain, methods=["GET"]),
        Route("/api/labor/needed", api_labor_needed, methods=["GET"]),
        Route("/api/labor/status", api_labor_status, methods=["GET"]),
        Route("/api/labor/request", api_labor_request, methods=["POST"]),
        Route("/api/ingest", api_ingest, methods=["POST"]),
        Route("/api/rates/mirror", api_rates_mirror, methods=["POST"]),
        Route("/api/classification/refresh", api_classification_refresh, methods=["POST"]),
        # shipping-charges flow (contained in standalone/shipping/)
        *shipping_routes,
        # run-detail webform backend (contained in standalone/run_detail/)
        *run_detail_routes,
    ]
)
