# Creekside Cold Storage — Storage Bill Spec

A **standalone Storage Charges bill**, separate from the Packing/Repack bill. The
user picks a **ship-date range**, sees the storage charges for every pallet
**shipped in that window**, and can post them to Famous as their own order and
mark them billed.

Storage is an independent charge stream from packing/repack. It is driven purely
by the **shipping report** (`v_shipped` ← `cs_packing_shipping_raw`), not the
repack tables. See `sql/views.sql` `v_storage_charges` for the source of truth:

- One row per shipped pallet whose `ship_date - recv_date > 7`.
- `billable_days = (ship_date - recv_date) - 7` (7-day free period).
- `rate` is **per carton per day**, keyed on the pallet's pack form
  (`product_classification → storage_rates`, exact then GIRO fallback).
- `amount = shipped_qtya (cartons) × rate × billable_days` (≈ $60/day per full pallet).
- `rate_missing = true` when no per-carton rate matched (excluded from any post).
- `billed = true` when a `STORAGE` line already exists for that tag in
  `cs_packing_billing_lines`.

> **Prereq (one-time, CLI):** storage rates must be loaded first —
> `python -m standalone.cli ingest-storage-rates "CF - Reedley Charges.xlsx"`.
> Without it every pallet is `rate_missing` and nothing is billable. There is no
> HTTP route for this yet (see §5).

---

## 1. Goals

- Show storage charges for **pallets shipped in a chosen date range**, not a fixed
  billing week — the window the user cares about is the ship date.
- Bill (post to Famous) the range's storage as **one order**, and mark those tags
  billed so re-running is idempotent (already-billed tags drop out of `unbilled`).
- Mirror the look/behavior of the Packing Bill screen (§6.1 of the React spec) so
  the two bills feel like one app.

---

## 2. Backend — routes to add

These do **not exist yet**. Implement in `standalone/server.py` (+ a small
`standalone/storage.py` for the post logic, mirroring `bill.py`). The read route
wraps the existing `queries.storage_charges`.

### `GET /api/storage?start=&end=&status=`
Per-pallet storage charges for the ship-date window. `start` and `end` **required**
(`YYYY-MM-DD`, 400 otherwise). `status` ∈ `all` (default) | `unbilled` | `billed`.

Wraps `queries.storage_charges(start, end, status)`. Shape it like `/api/bill`:

```json
{
  "start": "2026-04-01", "end": "2026-04-30", "status": "unbilled",
  "summary": [
    { "commodity": "MANDARIN", "style": "25LB", "bagtype": "BULK",
      "pallets": 12, "carton_days": 3480, "rate": 0.24, "amount": 835.20 }
  ],
  "total": 835.20,
  "pallets": 12,
  "pallets_missing_rate": 3,
  "pallets_billed": 0,
  "lines": [
    { "tagid": 232212, "sono": "SO123", "lastconame": "ACME",
      "commodity": "MANDARIN", "style": "25LB", "bagtype": "BULK",
      "shipped_qty": 49, "recv_date": "2026-04-02", "ship_date": "2026-04-20",
      "billing_week": "2026-04-26", "days_in_storage": 18, "billable_days": 11,
      "rate": 0.24, "amount": 129.36, "rate_missing": false, "billed": false }
  ]
}
```

- `summary[]` = the `lines` grouped by `(commodity, style, bagtype, rate)` with
  `pallets = count`, `carton_days = sum(shipped_qty × billable_days)`,
  `amount = sum(amount)` — one row per invoice line (same grouping `bill.py`
  already uses for the storage section of `invoice_for_period`).
- `total` excludes `rate_missing` pallets (they have `amount:null`).
- `pallets_missing_rate` / `pallets_billed` drive the UI notices.

### `POST /api/storage/post`
Bill the window's storage. Body: `{ "start": "...", "end": "...", "comment": "?" }`.

New `storage.post_period(start, end, comment=None)` (model on `bill.post`):
1. Load unbilled, **rated** storage for the window
   (`v_storage_charges WHERE ship_date BETWEEN … AND NOT billed AND NOT rate_missing`).
2. If empty → `{ "ok": true, "results": [], "note": "nothing to bill for <range>" }`.
3. If `_STORAGE_SERVICES_CHARGE_ID is None` → return `ok:true` with
   `results:[{ "ok": true, "skipped": "no storage_charge_id configured" }]` (do
   **not** record anything — same guard `bill.post` uses).
4. Otherwise build **one** `AROrderFile` order for `sum(amount)` via
   `build_order_payload(..., charge_id=_STORAGE_SERVICES_CHARGE_ID,
   comment=comment or "COLD STORAGE <start>..<end>", po_number="STORAGE <start>..<end>")`,
   post it, and on success `actions.record_billed(_storage_billing_rows(rows, end))`
   with `charge_type="STORAGE"` (reuse the existing helper; pass `end` as `week_end`).

```json
{ "ok": true,
  "results": [ { "ok": true, "amount": 835.20, "response": "…" } ],
  "storage_recorded": 12 }
```

Partial/idempotent: only tags whose order posted OK get recorded, so a failed post
leaves them `unbilled` and a retry is safe (identical to the packing post).

### `GET /api/storage/invoice.pdf?start=&end=&status=` (optional, phase 2)
Streams a PDF of just the storage lines. `invoice_for_period` in `bill.py` already
renders `COLD STORAGE` lines — factor its storage half into a storage-only invoice
if a standalone PDF is wanted. Skip for v1 if not needed.

> **Do not** change `bill.post` — the weekly packing bill still posts its own
> storage per `billing_week`. This range-based post is a separate entry point. If
> both are used, `billed` de-dupes tags so a pallet is never billed twice.

---

## 3. Data types (TypeScript)

```ts
type StorageLine = {
  tagid: number; sono: string | null; lastconame: string | null;
  commodity: string | null; style: string | null; bagtype: string | null;
  shipped_qty: number; recv_date: string | null; ship_date: string | null;
  billing_week: string; days_in_storage: number; billable_days: number;
  rate: number | null; amount: number | null;
  rate_missing: boolean; billed: boolean;
};
type StorageSummaryRow = {
  commodity: string | null; style: string | null; bagtype: string | null;
  pallets: number; carton_days: number; rate: number | null; amount: number;
};
type StorageBill = {
  start: string; end: string; status: "all" | "unbilled" | "billed";
  summary: StorageSummaryRow[]; total: number; pallets: number;
  pallets_missing_rate: number; pallets_billed: number; lines: StorageLine[];
};
type StoragePostResult = {
  ok: boolean;
  results: { ok: boolean; amount?: number; response?: string;
             skipped?: string; error?: string; stage?: string }[];
  storage_recorded: number; note?: string;
};
```

---

## 4. Screen — Storage Bill (`/storage`)

Add to top nav after **Bill**: `… · Bill · Storage · …`. Deep-linkable:
`/storage?start=…&end=…&status=…` hydrates the form.

**Controls:** `start` date, `end` date (default last 30 days — storage windows are
longer than packing weeks), `status` select (All / To invoice / Billed reprint),
**Load** button.

**On load** → `GET /api/storage`. Render:

- **Header:** `Cold Storage · {status label} · {start} → {end}`.
- **Summary panel** (`summary[]`): one row per pack form — Commodity · Style ·
  Bag type | pallets | carton-days | rate ($/carton/day) | $amount. **Total** row
  (`total`), bold.
- **Notices:**
  - `pallets_missing_rate > 0` → warn: *"N pallets have no storage rate and are
    excluded from the total. Add mappings to `storage_rates` (ingest-storage-rates)."*
  - `status==='all' && pallets_billed > 0` → info: *"N of these are already billed."*
- **Actions:**
  - **Bill this range** → guarded mutation (see below). Disabled when `total===0`.
  - **Download lines (CSV)** — client-side from `lines`.
  - (phase 2) **Download invoice (PDF)** → `window.open('/api/storage/invoice.pdf?…')`.
- **Line items:** collapsible table of `lines[]` — Tag, Product/SO, Consignee,
  Bag type, Recv date, Ship date, Days in storage, Billable days, Rate, Cartons,
  Amount, `billed` badge. `rate_missing` rows show "—" for rate/amount and a muted
  "no rate" tag. Sortable; client-side filter box. Tag → `/chain/:tag`.

**Empty:** "No storage charges in this window." If `status==='unbilled'` returned 0
but `all` would not, hint to switch to **All charges** (mirror the packing gotcha).

---

## 5. Bill this range (mutation)

- Confirmation modal showing: range, pallet count, **total to post**, and the
  warning *"This posts one COLD STORAGE order to Famous and marks these pallets
  billed."* If `pallets_missing_rate > 0`, note those are excluded.
- On confirm → `POST /api/storage/post { start, end, comment? }`.
- Show `results[]` + `storage_recorded`, then refetch the storage query (billed
  pallets drop out of `unbilled`). On `skipped: "no storage_charge_id configured"`,
  surface it plainly — the charge was **computed but not posted** (config gap, not
  an error). On partial failure, unposted pallets remain `unbilled` — safe retry.

---

## 6. Cross-cutting (inherit from the React spec §7)

- Loading / empty / error states per fetch; render backend `{error}` strings.
- Money `$#,##0.00`; qty integer when whole. Rate shows 2–4 dp ($/carton/day).
- Mutation disables its trigger while pending and invalidates the storage query.
- No silent truncation: if `lines` is large, paginate and show "showing X of N".

---

## 7. Open questions

- **Storage charge id:** confirm `_STORAGE_SERVICES_CHARGE_ID` is set in `bill.py`
  (currently gated `None` → post is skipped). Frontend must handle the skip case.
- **Overlap with weekly bill:** if the weekly packing post already bills storage
  per week, decide whether the Storage screen should default `status` to `unbilled`
  only (so it never re-bills). Recommended: yes, default `unbilled`.
- **PDF:** is a storage-only invoice PDF needed for v1, or is posting + CSV enough?

---

## 8. Suggested build order

1. Backend `GET /api/storage` (wrap `queries.storage_charges` + summary rollup).
2. Frontend `/storage` read-only screen (form → table → summary → notices).
3. Backend `storage.post_period` + `POST /api/storage/post`.
4. Frontend Bill-this-range mutation + confirm modal.
5. (optional) storage-only invoice PDF.
