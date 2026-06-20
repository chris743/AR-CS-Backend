# Creekside Billing — API Routes (for the frontend agent)

Every implemented route on the standalone server (`standalone/server.py`), grouped
by area. This is the complete, current contract — build the UI against this.

- **Base URL:** the server runs at `http://<host>:8100` by default
  (`venv/bin/python -m uvicorn standalone.server:app --port 8100`).
- **Content type:** all JSON routes return `application/json`. Errors are
  `{"error": "<message>"}` with a non-200 status (400 = bad/missing params,
  404 = not found, 500 = server/DB error).
- **Dates:** `YYYY-MM-DD` everywhere **except** the shipping flow, which keys off a
  SharePoint **week folder** named `WE MM.DD.YYYY` (e.g. `"WE 05.17.2026"`).
- **Null-safety:** numeric cells that are NaN/empty serialize as `null`.
- **DB down:** only `/api/meta` degrades gracefully (`db_ok:false`); other routes
  return a 500 `{"error": ...}` if the database is unreachable.

---

## 1. App + health

### `GET /`
The bill UI (HTML). Not an API route.

### `GET /api/meta`
Dashboard health strip. Never errors — returns `db_ok:false` when the DB is down.
```json
{ "repack_lines": 0, "repack_inputs": 0, "repack_outputs": 0,
  "shipping": 0, "rates": 0, "classifications": 0,
  "billed_tags": 0, "db_ok": true }
```

---

## 2. Packing bill (PACK service charges; repacks are T&M — see §3)

### `GET /api/bill?start=&end=&status=`
The bill for a **ship-date period**. `status` ∈ `all` (default) | `unbilled` | `billed`.
`start` and `end` required (400 otherwise).
```json
{
  "start": "2026-04-27", "end": "2026-05-03", "status": "all",
  "service": [ { "transaction_type": "PACK", "tags": 110, "qty": 5800,
                 "amount": 51185.10, "tags_missing_rate": 0, "tags_billed": 1 } ],
  "service_total": 51644.10,
  "labor_total": 0.0,
  "total": 51644.10,
  "tags_missing_rate": 0,
  "tags_billed": 1,
  "lines": [ { "tagid": 232212, "productname": "...", "transaction_type": "PACK",
               "ship_date": "2026-04-29", "billing_week": "2026-05-03",
               "commodity": "MANDARIN", "style": "25LB", "bagtype": "BULK",
               "rate": 7.9, "shipped_qty": 49, "amount": 387.10,
               "rate_missing": false, "billed": false } ]
}
```
> Note: REPACK rows appear in `lines` with `rate:null`, `amount:null` — repacks
> bill on time & materials, not a per-carton rate.

### `GET /api/invoice.pdf?start=&end=&status=`
Streams a PDF invoice for the period (one line per txn/commodity/style/rate;
rate-missing and REPACK lines excluded). Returns `application/pdf`.

### `GET /api/bill/candidates?start=&end=&status=`
What's left to bill, rolled up by week × type. `status` default `unbilled`.
Period optional; omit `start`/`end` for all weeks. Returns an **array**:
```json
[ { "billing_week": "2026-05-03", "transaction_type": "PACK",
    "tags": 110, "shipped_qty": 5800, "amount": 51185.10, "tags_missing_rate": 0 } ]
```

### `GET /api/bill/summary`
Per-week bill totals. Returns an **array**:
```json
[ { "billing_week": "2026-05-03", "service_amount": 51644.10,
    "labor_amount": 0, "materials_amount": 0, "total_amount": 51644.10 } ]
```
> `total_amount = service_amount + labor_amount + materials_amount`.
> `materials_amount` is repack materials (added with the T&M change).

### `POST /api/bill/post`
Body: `{ "week": "YYYY-MM-DD" }` (required). Posts the week's PACK orders to Famous
and records billed lines. REPACK is skipped (bills via T&M).
```json
{ "ok": true,
  "results": [ { "transaction_type": "PACK", "ok": true, "amount": 51185.10, "response": "..." },
               { "transaction_type": "REPACK", "ok": true, "skipped": "repack billed via T&M (labor + materials)" } ],
  "recorded": 110 }
```
> ⚠️ Guarded action: this posts real orders to Famous and marks tags billed.
> Confirm in the UI before calling.

---

## 3. Repack reconciliation + labor (T&M inputs)

### `GET /api/status/summary`
`wh_status` × `billing_status` crosstab. Array of:
```json
[ { "wh_status": "shipped", "billing_status": "billable", "tags": 42, "shipped_qty": 2100 } ]
```
- `wh_status` ∈ `shipped` | `repacked` | `on-hand`
- `billing_status` ∈ `billed` | `billable` | `not-billable` | `null` (no reason code)

### `GET /api/status?wh_status=&billing_status=&limit=&offset=`
Paginated drill-down of per-tag reconciliation. `limit` default 25, `offset` 0
(both must be ints → 400). For the no-reason bucket, pass `billing_status=null`
(or omit it). Array of:
```json
[ { "tagid": 232212, "role": "output", "icrunidx": 4641, "productname": "...",
    "fcreasonidx": 39, "uom": "CS", "rundate": "2026-04-29",
    "wh_status": "shipped", "shipped_qty": 49, "billing_status": "billable" } ]
```

### `GET /api/chain/{tag}`
The full repack chain behind one shipped tag (`tag` int → 400 if not). Array of
chain nodes (`shipped_tag, tagid, icrunidx, fcreasonidx, productname, uom, qty,
depth, billable`).

### `GET /api/labor/needed?week=`
Repack runs (reason 41) that shipped and still need labor entry. `week` optional.
Array of `{ icrunidx, productdescr, billing_week, quantity }`.

### `GET /api/labor/status`
The labor-request audit. Array of `{ week, to_address, run_count, status,
sent_at, received_at }`.

### `POST /api/labor/request`
Body: `{ "to_address": "...", "week": "YYYY-MM-DD" }` (both required). Emails ops
the labor sheet and logs a pending request.
```json
{ "ok": true, "status": "pending", "runs": 7 }
```

---

## 4. Maintenance

### `POST /api/ingest`
Loads the latest CSVs into Postgres (repack split + shipping). Returns row counts
`{ "repack_lines": n, "repack_inputs": n, "repack_outputs": n, "shipping": n }`.
Long-ish (seconds) — show a spinner.

### `POST /api/rates/mirror`
Mirrors the MSSQL rate table into `packing_rates`. → `{ "rows": n }`.

### `POST /api/classification/refresh`
Re-classifies product names (type/commodity/style/bagtype). → `{ ...counts }`.

---

## 5. Shipping charges flow  *(contained in `standalone/shipping/`)*

Weekly Creekside **materials/shipping** invoice: pulls the shipping-charges
report (SMB) + the phyto certificate packets (SharePoint), runs AI extraction to
match each phyto to its debit row, joins them onto the shipping rows, builds an
xlsx, and posts one AR order to Famous. State is keyed by the **week folder**
(`WE MM.DD.YYYY`) and persisted in `creekside_core.shipping_runs`.

**Lifecycle:** `run` → (if `needs_review_count > 0`) `review` → `post`.

The **run summary** object (returned by `run` and `review`):
```json
{ "week_folder": "WE 05.17.2026", "status": "needs_review",
  "total": 4231.55, "phyto_count": 6, "needs_review_count": 1,
  "needs_review": [ { "file": "/tmp/phytos/abc-phyto.pdf", "order_number": 0,
      "match": { "matched": false, "current_certificate_number": "S-C-...",
                 "matched_certificate_number": null, "date": null,
                 "debit_amount": null, "reason": "No Exact Match found" } } ],
  "xlsx_path": "/tmp/shipping_xlsx/WE_05-17-2026.xlsx",
  "posted_at": null, "erp_result": null }
```
`status` ∈ `built` (ready to post) | `needs_review` (has unmatched phytos) |
`posted` | `error`.

### `POST /api/shipping/run`
Body: `{ "week_folder": "WE 05.17.2026" }` — **optional**; omit to use the most
recent WE folder. Runs the whole pipeline and upserts the run. Returns the run
summary. **Slow** (downloads + per-PDF AI extraction; tens of seconds) and
requires the AI/SharePoint/SMB stack — show a long spinner; surface
`needs_review` when present.

### `GET /api/shipping/runs`
All runs, newest first (lightweight list). Array of:
```json
[ { "week_folder": "WE 05.17.2026", "status": "posted", "total": 4231.55,
    "xlsx_path": "...", "created_at": "...", "updated_at": "...", "posted_at": "..." } ]
```

### `GET /api/shipping/run?week_folder=`
Full stored run row (includes `result` = every phyto extraction+match, and
`needs_review`). Omit `week_folder` for the most recent. 404 if none.

### `POST /api/shipping/review`
Body: `{ "week_folder": "...", "corrections": [ { "file": "...",
"order_number": 12345, "match": { ... } } ] }` (both required → 400; unknown week
→ 404). Replaces flagged phytos by `file`, re-joins, rebuilds the xlsx,
recomputes the total. Returns the updated run summary.

### `POST /api/shipping/post`
Body: `{ "week_folder": "...", "approve": true }` (week required → 400; unknown
→ 404). Optional overrides: `customer_id`, `charge_id`, `po_number`, `comment`.
This is the **approval gate** — without `approve:true` it returns
`{ "ok": false, "stage": "review", "error": "not approved", ... }` and posts
nothing. On approve it submits one ImportOrderFile to Famous:
```json
{ "ok": true, "week_end": "2026-05-17", "customer_id": "1680",
  "charge_id": "1128", "amount": "4231.55", "header": { ... },
  "famousResponseText": "..." }
```
On failure: `{ "ok": false, "stage": "load|build|<famous-stage>", "error": "..." }`.
> ⚠️ Guarded action: posts a real AR order to Famous. Confirm in the UI.

### `GET /api/shipping/xlsx?week_folder=`
Streams the generated invoice xlsx for the week (most recent if omitted). 404 if
not built yet. `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`.

---

## 6. Run detail  *(contained in `standalone/run_detail/`)*

Backend for the **Creekside Organics Labor Detail** webform — one structured,
server-computed record per repack run (replaces the per-run spreadsheet tab).
Each run's `total` = Labor + 20% Overhead + New Materials = the run's
time-&-materials charge.

| Method | Route | Purpose |
|---|---|---|
| `GET`  | `/api/run-detail/refs` | dropdown data: positions, materials (+ default prices), defects, categories, run types |
| `POST` | `/api/run-detail/refs/material` | upsert a material rate `{name, default_price, ...}` |
| `POST` | `/api/run-detail` | save a run (upsert by `run_no`); returns the full computed record |
| `GET`  | `/api/run-detail?week=&status=&start=&end=` | list runs (no child lines) |
| `GET`  | `/api/run-detail/{run_no}` | one full nested record |
| `DELETE` | `/api/run-detail/{run_no}` | delete a run + its lines |

**Full payloads, field reference, and TS types: [RUN_DETAIL_FRONTEND.md](RUN_DETAIL_FRONTEND.md).**

---

## TypeScript types (suggested)

```ts
type Txn = 'PACK' | 'REPACK' | 'OTHER';
type WhStatus = 'shipped' | 'repacked' | 'on-hand';
type BillingStatus = 'billed' | 'billable' | 'not-billable' | null;
type ShippingStatus = 'built' | 'needs_review' | 'posted' | 'error';

interface Meta { repack_lines:number; repack_inputs:number; repack_outputs:number;
  shipping:number; rates:number; classifications:number; billed_tags:number; db_ok:boolean; }

interface WeekSummary { billing_week:string; service_amount:number; labor_amount:number;
  materials_amount:number; total_amount:number; }

interface BillCandidate { billing_week:string; transaction_type:Txn; tags:number;
  shipped_qty:number; amount:number|null; tags_missing_rate:number; }

interface PhytoMatch { matched:boolean; current_certificate_number:string;
  matched_certificate_number:string|null; date:string|null; debit_amount:number|null; reason:string; }
interface PhytoEntry { file:string; order_number:number|null; match:PhytoMatch; }
interface ShippingRunSummary { week_folder:string; status:ShippingStatus; total:number|null;
  phyto_count:number; needs_review_count:number; needs_review:PhytoEntry[];
  xlsx_path:string|null; posted_at:string|null; erp_result:unknown|null; }
```
