"""Starlette routes for the shipping-charges flow.

Imported by standalone.server, which extends its route list with `routes`.
Self-contained helpers avoid a circular import back into server.py. All flow
functions already return JSON-safe dicts.
"""

from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from . import flow


async def _body(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:
        return {}


def _err(msg: str, status: int = 500):
    return JSONResponse({"error": msg}, status_code=status)


async def api_shipping_run(request: Request) -> JSONResponse:
    body = await _body(request)
    week = body.get("week_folder") or None
    try:
        return JSONResponse(await flow.run(week))
    except Exception as e:
        return _err(str(e))


async def api_shipping_runs(request: Request) -> JSONResponse:
    try:
        return JSONResponse(flow.list_runs())
    except Exception as e:
        return _err(str(e))


async def api_shipping_folders(request: Request) -> JSONResponse:
    try:
        return JSONResponse(flow.list_folders())
    except Exception as e:
        return _err(str(e))


async def api_shipping_run_get(request: Request) -> JSONResponse:
    week = request.query_params.get("week_folder") or None
    try:
        run = flow.get(week)
        if run is None:
            return _err("no shipping run found", 404)
        return JSONResponse(run)
    except Exception as e:
        return _err(str(e))


async def api_shipping_review(request: Request) -> JSONResponse:
    body = await _body(request)
    week = body.get("week_folder")
    corrections = body.get("corrections")
    if not week or not isinstance(corrections, list):
        return _err("week_folder and corrections[] are required", 400)
    try:
        return JSONResponse(flow.review(week, corrections))
    except KeyError as e:
        return _err(str(e), 404)
    except Exception as e:
        return _err(str(e))


async def api_shipping_post(request: Request) -> JSONResponse:
    body = await _body(request)
    week = body.get("week_folder")
    if not week:
        return _err("week_folder is required", 400)
    approve = bool(body.get("approve"))
    overrides = {k: body[k] for k in ("customer_id", "charge_id", "po_number", "comment")
                 if body.get(k) is not None}
    try:
        return JSONResponse(flow.post(week, approve=approve, **overrides))
    except KeyError as e:
        return _err(str(e), 404)
    except Exception as e:
        return _err(str(e))


async def api_shipping_xlsx(request: Request) -> FileResponse | JSONResponse:
    week = request.query_params.get("week_folder") or None
    try:
        path = flow.xlsx_path(week)
        if not path or not Path(path).exists():
            return _err("no xlsx for that week (run it first)", 404)
        return FileResponse(
            path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=Path(path).name,
        )
    except Exception as e:
        return _err(str(e))


routes = [
    Route("/api/shipping/run", api_shipping_run, methods=["POST"]),
    Route("/api/shipping/runs", api_shipping_runs, methods=["GET"]),
    Route("/api/shipping/folders", api_shipping_folders, methods=["GET"]),
    Route("/api/shipping/run", api_shipping_run_get, methods=["GET"]),
    Route("/api/shipping/review", api_shipping_review, methods=["POST"]),
    Route("/api/shipping/post", api_shipping_post, methods=["POST"]),
    Route("/api/shipping/xlsx", api_shipping_xlsx, methods=["GET"]),
]
