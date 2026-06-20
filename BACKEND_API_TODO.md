# Backend API — routes the Accounting frontend needs

The React Accounting module (`frontend/src/modules/accounting/`) is finished and
calls the endpoints below. Some already exist in `standalone/server.py`; the rest
**404 today** and need thin routes added. Each `[add]` route is a one-liner that
wraps an existing, tested function in the `standalone` package — **do not
reimplement billing logic in the route** (all of it lives in `creekside_core`
SQL views; the route just serializes the result to JSON).

This doc is the contract the frontend depends on: paths, params, and the exact
JSON field names/types the UI reads. Match these names exactly — the client does
no transformation.

---

## Conventions (apply to every route)

- **Base path:** all routes are under `/api/*`, same origin. Dev: the Vite dev
  server proxies `/api` → `http://localhost:8100` (see `frontend/vite.config.js`).
- **JSON in/out** unless noted. Dates are ISO `YYYY-MM-DD` strings. Money is a
  JSON number with 2dp. Quantities are numbers.
- **Success:** return `200` with the JSON body described below. List endpoints
  return a top-level JSON **array** (not wrapped in an object) unless stated.
- **Errors:** return a non-2xx status with a JSON body `{"error": "<message>"}`.
  The frontend's fetch client surfaces `body.error` verbatim in the UI, so make
  the string human-readable. If you return a non-JSON error body, the client
  falls back to `"<status> <statusText>: <snippet>"`.
- **Auth:** none today (same as the existing page/webhook). The client *may* send
  an `Authorization: Bearer <token>` header; ignore it. Treat **`POST /api/bill/post`**
  and **`POST /api/labor/request`** as privileged if/when auth is added.
- **Long actions** (`/api/ingest`, `/api/rates/mirror`, `/api/classification/refresh`)
  run synchronously and can take seconds — that's fine, the UI shows a spinner and
  waits for the response. Return the result counts when done (don't return early).

---

## Status of each route

| Method | Path | Status | Wraps |
|---|---|---|---|
| GET | `/api/meta` | **[add]** | counts + `db_ok` (see below) |
| GET | `/api/bill?start&end&status` | [exists] | — |
| GET | `/api/invoice.pdf?start&end&status` | [exists] | — |
| GET | `/api/bill/candidates` | **[add]** | `queries.bill_candidates()` |
| GET | `/api/bill/summary` | **[add]** | `queries.bill_summary()` |
| POST | `/api/bill/post` | **[add]** | `bill.post(week)` |
| GET | `/api/status/summary` | **[add]** | `queries.summary()` |
| GET | `/api/status?wh_status&billing_status&limit&offset` | **[add]** | `v_repack_status` filtered |
| GET | `/api/chain/{tag}` | **[add]** | `queries.chain(tag)` |
| GET | `/api/labor/needed?week` | **[add]** | `labor.needed(week)` |
| GET | `/api/labor/status` | **[add]** | `labor.status()` |
| POST | `/api/labor/request` | **[add]** | `labor.email_request(...)` |
| POST | `/api/ingest` | **[add]** | `ingest.ingest_all()` |
| POST | `/api/rates/mirror` | **[add]** | `rates.mirror()` |
| POST | `/api/classification/refresh` | **[add]** | `classify.refresh()` |

---

## Route contracts

### `GET /api/meta`
Dashboard health strip + Admin meta panel. Return a single object:

```json
{
  "repack_lines": 0,
  "repack_inputs": 0,
  "repack_outputs": 0,
  "shipping": 0,
  "billed_tags": 0,
  "rates": 0,
  "classifications": 0,
  "db_ok": true
}
```
- All counts are integers (row counts per source table). `db_ok` is a boolean —
  return `false` (still `200`) if the DB is unreachable so the header dot can go red.

### `GET /api/bill/candidates`
Wraps `queries.bill_candidates()`. Returns an **array**; each item:

```json
{
  "billing_week": "2026-04-19",
  "transaction_type": "PACK",
  "tags": 0,
  "shipped_qty": 0,
  "amount": 0,
  "tags_missing_rate": 0
}
```
- `transaction_type` ∈ `"PACK" | "REPACK"`. One row per week × type.
- `tags_missing_rate` drives a warning chip; `0` is fine.

### `GET /api/bill/summary`
Wraps `queries.bill_summary()`. Returns an **array**; each item:

```json
{ "billing_week": "2026-04-19", "service_amount": 0, "labor_amount": 0, "total_amount": 0 }
```

### `POST /api/bill/post`  *(mutation — posts to Famous ERP)*
Body: `{ "week": "2026-04-19" }`. Wraps `bill.post(week)`. Return:

```json
{
  "ok": true,
  "recorded": 0,
  "results": [
    { "transaction_type": "PACK", "ok": true },
    { "transaction_type": "REPACK", "ok": false, "error": "…" }
  ]
}
```
- `recorded` = count of tags marked billed; shown in the success toast.
- `results[]` — one entry per transaction type posted. The UI inspects
  `results[].ok === false` to warn which types failed (`results[].transaction_type`).
  **Record only the types that posted OK** so retry is safe; the UI tells the user
  retry is safe on partial failure.
- On total failure, either return non-2xx `{"error": "..."}` or `ok:false` — the UI
  handles both (the mutation `onError` shows `error.message`).

### `GET /api/status/summary`
Wraps `queries.summary()` — the crosstab. Returns an **array**; each item:

```json
{ "wh_status": "shipped", "billing_status": "billable", "tags": 0, "shipped_qty": 0 }
```
- `wh_status` ∈ `"shipped" | "repacked" | "on-hand"`.
- `billing_status` ∈ `"billed" | "billable" | "not-billable" | null` (`null` = no
  reason code). **Emit literal JSON `null`**, not the string `"null"` — the UI keys
  the crosstab cell on it and uses the same value to drill down.
- `shipped_qty` may be `null`.

### `GET /api/status?wh_status&billing_status&limit&offset`
Filtered rows from `v_repack_status`, **paginated**. Returns an **array**; each item:

```json
{
  "tagid": 0,
  "role": "output",
  "icrunidx": 0,
  "productname": "",
  "fcreasonidx": null,
  "uom": "",
  "rundate": "2026-04-19",
  "wh_status": "shipped",
  "shipped_qty": null,
  "billing_status": "billable"
}
```
- Query params: `wh_status` and `billing_status` are the selected crosstab cell;
  `limit` (default 25 from the UI) and `offset` (`page * limit`).
- **`billing_status` no-reason cell:** the UI selects the cell whose billing status
  is `null`. The current client omits the param entirely in that case (empty values
  are dropped from the query string). Decide the contract and document it: e.g.
  treat **missing `billing_status` as "filter to NULL reason"**, or have the client
  send a sentinel. Easiest: missing `billing_status` ⇒ `billing_status IS NULL`.
  (Flag back to the frontend if you pick the sentinel approach so we can send it.)
- Pagination total: the UI computes "showing X–Y of N" from the **crosstab tag
  count** for the selected cell (`/api/status/summary`), so this endpoint does
  **not** need to return a total — just honor `limit`/`offset` and return that page.

### `GET /api/chain/{tag}`
Wraps `queries.chain(tag)`. `{tag}` is the shipped tag id (path param). Returns an
**array** of chain nodes:

```json
{
  "shipped_tag": 0,
  "tagid": 0,
  "icrunidx": 0,
  "fcreasonidx": null,
  "productname": "",
  "uom": "",
  "qty": 0,
  "depth": 0,
  "billable": true
}
```
- `depth` drives the indent (0 = the shipped tag at the root). The UI reads
  `shipped_tag` + `productname` of the depth-0 node for the header, and dims nodes
  where `billable` is `false`.

### `GET /api/labor/needed?week`
Wraps `labor.needed(week)`. `week` is optional (omit ⇒ all). Returns an **array**:

```json
{ "icrunidx": 0, "productdescr": "", "rundate": "2026-04-19", "quantity": 0 }
```

### `GET /api/labor/status`
Wraps `labor.status()`. Returns an **array** of request history:

```json
{
  "week": "2026-04-19",
  "to_address": "ops@example.com",
  "run_count": 0,
  "status": "pending",
  "sent_at": "2026-04-19",
  "received_at": null
}
```
- `status` ∈ `"pending" | "received"`. `received_at` may be `null`.

### `POST /api/labor/request`  *(mutation — sends email)*
Body: `{ "to_address": "ops@example.com", "week": "2026-04-19" }`. Wraps
`labor.email_request(to_address, week)`. Return:

```json
{ "ok": true, "status": "pending", "runs": 0 }
```
- `runs` = number of runs included; shown in the success toast.
- Replies are recorded by the mail webhook (`standalone/webhook.py`), **not** here.

### `POST /api/ingest`  *(long)*
Wraps `ingest.ingest_all()`. Return the counts ingested:

```json
{ "repack_lines": 0, "repack_inputs": 0, "repack_outputs": 0, "shipping": 0 }
```
- The Admin toast reports `repack_lines` and `shipping`.

### `POST /api/rates/mirror`  *(long, depends on MSSQL)*
Wraps `rates.mirror()`. Return:

```json
{ "rows": 0 }
```
- Can fail if MSSQL is unreachable — return non-2xx `{"error": "<plain message>"}`.
  The UI surfaces the error string verbatim; **don't swallow it**.

### `POST /api/classification/refresh`
Wraps `classify.refresh()`. Return:

```json
{ "classified": 0 }
```

---

## Already-existing routes (for reference — no work needed)

- **`GET /api/bill?start&end&status`** — `status` ∈ `all | unbilled | billed`
  (default `all`). Returns the `Bill` object: `service[]`, `service_total`,
  `labor_total`, `total`, `tags_missing_rate`, `tags_billed`, `lines[]`. The bill
  screen already consumes this shape; if you change it, update
  `frontend/src/modules/accounting/pages/AccountingBill.jsx`.
- **`GET /api/invoice.pdf?start&end&status`** — streams `application/pdf`. The UI
  fetches it with auth and triggers a download.

---

## Definition of done

1. Every `[add]` route above returns the documented JSON shape and `200` on success.
2. Failures return `{"error": "..."}` with a non-2xx status.
3. `GET /api/meta` returns `db_ok: false` (not a 500) when the DB is down.
4. The no-reason (`billing_status: null`) crosstab cell drills down correctly — pick
   and document the missing-param-means-NULL contract (or coordinate a sentinel).
5. Routes stay read-through to `standalone.{queries,bill,labor,ingest,rates,classify}`
   — no business logic in the route layer.

Once these land, the Accounting screens (Dashboard, Bill, Reconciliation,
Candidates, Chain, Labor, Admin) stop showing error states and are fully functional.
