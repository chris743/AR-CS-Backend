"""Starlette routes for the run-detail webform backend.

Mounted by standalone.server. Self-contained helpers (no import back into
server.py). store/refs return JSON-safe dicts already.
"""

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import refs, store


async def _body(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:
        return {}


def _err(msg: str, status: int = 500):
    return JSONResponse({"error": msg}, status_code=status)


async def api_refs(request: Request) -> JSONResponse:
    try:
        return JSONResponse(refs.all_refs())
    except Exception as e:
        return _err(str(e))


async def api_material_price(request: Request) -> JSONResponse:
    body = await _body(request)
    name, price = body.get("name"), body.get("default_price")
    if not name or price is None:
        return _err("name and default_price are required", 400)
    try:
        return JSONResponse(refs.set_material_price(
            name, float(price), category=body.get("category"),
            unit_label=body.get("unit_label"), seq=body.get("seq")))
    except Exception as e:
        return _err(str(e))


async def api_save(request: Request) -> JSONResponse:
    body = await _body(request)
    try:
        return JSONResponse(store.save(body))
    except ValueError as e:
        return _err(str(e), 400)
    except Exception as e:
        return _err(str(e))


async def api_list(request: Request) -> JSONResponse:
    qp = request.query_params
    try:
        return JSONResponse(store.list_runs(
            week=qp.get("week") or None, status=qp.get("status") or None,
            start=qp.get("start") or None, end=qp.get("end") or None))
    except Exception as e:
        return _err(str(e))


async def api_get(request: Request) -> JSONResponse:
    try:
        run_no = int(request.path_params["run_no"])
    except (KeyError, ValueError):
        return _err("run_no must be an integer", 400)
    try:
        rec = store.get(run_no)
        return JSONResponse(rec) if rec else _err("run not found", 404)
    except Exception as e:
        return _err(str(e))


async def api_delete(request: Request) -> JSONResponse:
    try:
        run_no = int(request.path_params["run_no"])
    except (KeyError, ValueError):
        return _err("run_no must be an integer", 400)
    try:
        return JSONResponse({"deleted": store.delete(run_no)})
    except Exception as e:
        return _err(str(e))


routes = [
    Route("/api/run-detail/refs", api_refs, methods=["GET"]),
    Route("/api/run-detail/refs/material", api_material_price, methods=["POST"]),
    Route("/api/run-detail", api_save, methods=["POST"]),
    Route("/api/run-detail", api_list, methods=["GET"]),
    Route("/api/run-detail/{run_no}", api_get, methods=["GET"]),
    Route("/api/run-detail/{run_no}", api_delete, methods=["DELETE"]),
]
