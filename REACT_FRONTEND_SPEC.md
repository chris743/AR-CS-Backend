# Creekside Packing Charges — React Frontend Spec

A single-page React app over the standalone billing backend (`standalone/`). The
backend is SQL-first (all billing logic lives in Postgres views under
`creekside_core`); this frontend is a thin, read-mostly UI that drives the same
operations the CLI exposes.

Status legend for endpoints below: **[exists]** already in `server.py`;
**[add]** a thin route to add that wraps an existing `standalone` function.

---

## 1. Goals

1. Define a **billing period** and produce the **bill** (service + labor + total), with line detail.
2. Download the **PDF invoice** and a **CSV** of lines.
3. See the **reconciliation** (what shipped vs. what's billed) and drill in.
4. See **what's left to bill** (candidates) and **labor** that's outstanding.
5. Run the **maintenance/actions** that feed billing: ingest reports, mirror rates, refresh classification; post a week to the ERP; send the labor request.

Non-goals: editing rates/classification by hand (done in source systems), auth/identity (see §9), mobile-first layout (desktop-first is fine).

---

## 2. Architecture

- **App:** React + TypeScript + Vite. Client-side routing (React Router). Data fetching via TanStack Query (caching, refetch, mutation states). Styling: the existing dark theme tokens (see §8) — Tailwind or CSS Modules, builder's choice.
- **Backend:** the existing Starlette app in `standalone/server.py`. Today it serves the legacy HTML page plus `/api/bill` and `/api/invoice.pdf`. The React build should be served as static files from `/` (replace `index`), with the API under `/api/*` (same origin → no CORS).
- **No client-side business logic.** Amounts, billed/unbilled status, chains, bagtype rates are all computed in SQL. The UI only displays and triggers.

```
React SPA  ──fetch──>  /api/*  (Starlette)  ──>  standalone.{queries,bill,labor,ingest,rates,classify}  ──>  Postgres creekside_core (views)
```

---

## 3. API contract

All JSON unless noted. Dates are ISO `YYYY-MM-DD`. Money is a number (2dp), quantities numbers.

### Billing
- **`GET /api/bill?start&end&status`** **[exists]** — the period bill.
  `status` ∈ `all` | `unbilled` | `billed` (default `all`). Returns `Bill` (§4).
- **`GET /api/invoice.pdf?start&end&status`** **[exists]** — streams `application/pdf`. UI just links/opens it.
- **`GET /api/bill/candidates`** **[add]** → `queries.bill_candidates()` → `BillCandidate[]` (what to bill by week × PACK/REPACK).
- **`GET /api/bill/summary`** **[add]** → `queries.bill_summary()` → `WeekSummary[]` (service + labor + total per week).
- **`POST /api/bill/post`** **[add]** body `{week}` → `bill.post(week)` → `{ok, results[], recorded}`. **Mutation** — posts to Famous ERP and records billed lines. Guard behind a confirm (see §6.4).

### Reconciliation
- **`GET /api/status/summary`** **[add]** → `queries.summary()` → `StatusBucket[]` (wh_status × billing_status crosstab with tag + qty totals).
- **`GET /api/status?wh_status&billing_status&limit&offset`** **[add]** → rows from `v_repack_status` filtered → paginated `RepackStatusRow[]`. Used for drill-down tables.
- **`GET /api/chain/:shippedTag`** **[add]** → `queries.chain(tag)` → `ChainNode[]` (the repack chain behind a shipped tag).

### Labor
- **`GET /api/labor/needed?week`** **[add]** → `labor.needed(week)` → `LaborNeeded[]`.
- **`GET /api/labor/status`** **[add]** → `labor.status()` → `LaborRequest[]`.
- **`POST /api/labor/request`** **[add]** body `{to_address, week}` → `labor.email_request(...)` → `{ok, status, runs}`. **Mutation** (sends email).

### Maintenance / data
- **`POST /api/ingest`** **[add]** → `ingest.ingest_all()` → `{repack_lines, repack_inputs, repack_outputs, shipping}`. **Long-ish** (seconds); show spinner.
- **`POST /api/rates/mirror`** **[add]** → `rates.mirror()` → `{rows}`. Depends on MSSQL reachability — handle failure.
- **`POST /api/classification/refresh`** **[add]** → `classify.refresh()` → `{classified}`.
- **`GET /api/meta`** **[add]** → light status: counts (rows in each repack table, shipping rows, billed tags, rates, classifications), and `db_ok: bool`. Used for the dashboard health strip.

> The **[add]** routes are one-liners wrapping functions that already exist and are tested. Keep them read-through to the `standalone` package; do not reimplement logic in the route.

---

## 4. Data types (TypeScript)

```ts
type Txn = 'PACK' | 'REPACK' | 'OTHER';
type BillStatus = 'all' | 'unbilled' | 'billed';
type WhStatus = 'shipped' | 'repacked' | 'on-hand';
type BillingStatus = 'billed' | 'billable' | 'not-billable' | null; // null = no reason code

interface BillServiceRow { transaction_type: Txn; tags: number; qty: number; amount: number;
                           tags_missing_rate: number; tags_billed: number; }
interface BillLine { tagid: number; productname: string; transaction_type: Txn;
                     ship_date: string; billing_week: string; commodity: string | '';
                     style: string | ''; bagtype: string | ''; rate: number | '';
                     shipped_qty: number; amount: number | ''; rate_missing: boolean; billed: boolean; }
interface Bill { start: string; end: string; status: BillStatus;
                 service: BillServiceRow[]; service_total: number; labor_total: number;
                 total: number; tags_missing_rate: number; tags_billed: number; lines: BillLine[]; }

interface BillCandidate { billing_week: string; transaction_type: Txn; tags: number;
                          shipped_qty: number; amount: number; tags_missing_rate: number; }
interface WeekSummary { billing_week: string; service_amount: number; labor_amount: number; total_amount: number; }

interface StatusBucket { wh_status: WhStatus; billing_status: BillingStatus; tags: number; shipped_qty: number | null; }
interface RepackStatusRow { tagid: number; role: 'output' | 'input_only'; icrunidx: number;
                            productname: string; fcreasonidx: number | null; uom: string;
                            rundate: string; wh_status: WhStatus; shipped_qty: number | null;
                            billing_status: BillingStatus; }
interface ChainNode { shipped_tag: number; tagid: number; icrunidx: number; fcreasonidx: number | null;
                      productname: string; uom: string; qty: number; depth: number; billable: boolean; }

interface LaborNeeded { icrunidx: number; productdescr: string; rundate: string; quantity: number; }
interface LaborRequest { week: string; to_address: string; run_count: number;
                         status: 'pending' | 'received'; sent_at: string; received_at: string | null; }
```

---

## 5. Routes / screens

```
/                      Dashboard (health + quick links + this-period snapshot)
/bill                  Generate Bill  (the primary screen)
/reconciliation        Reconciliation crosstab + drill-down
/candidates            What to bill (by week) + weekly summary
/chain/:tag            Chain explorer (also reachable from any line/row)
/labor                 Labor: needed + request + request history
/admin                 Maintenance actions
```

Top nav: Dashboard · Bill · Reconciliation · Candidates · Labor · Admin. A persistent header health dot (green when `/api/meta` `db_ok`).

---

## 6. Screen detail

### 6.1 Generate Bill (`/bill`) — primary
**Controls:** `start` date, `end` date (default last 14 days), `status` select (All charges / To invoice / Billed reprint), **Generate** button.

**On generate** → `GET /api/bill`. Render:
- **Bill panel:** header `Packing Charges · {status label} · {start} → {end}`.
  - Service rows: one per `service[]` entry — `{Txn} service | tags | cartons | $amount`.
  - **Labor** row (`labor_total`).
  - **Total** row (`total`), bold.
  - Notices: if `tags_missing_rate > 0` → warn "N tags have no rate, excluded from amount"; if `status==='all' && tags_billed>0` → info "N of these are already billed".
- **Actions:** **Download invoice (PDF)** (`window.open('/api/invoice.pdf?…')`), **Download lines (CSV)** (client-side from `lines`).
- **Line items:** collapsible table of `lines[]` — Tag, Product, Type, Ship date, Bag type, Rate, Qty, Amount; a `billed` badge per row; `rate==='' ` renders "—". Sortable, client-side filter box. Each Tag links to `/chain/:tag`.

**Empty:** "No charges in this period" — and if `status==='unbilled'` returned 0 but `all` would not, hint to switch to **All charges** (this is the exact 4/19–4/26 gotcha; surface it).

### 6.2 Reconciliation (`/reconciliation`)
- **Crosstab** from `/api/status/summary`: rows = `wh_status` (shipped/repacked/on-hand), columns = `billing_status` (billed/billable/not-billable/no-reason), cells = tag counts (and qty on hover/expand). Cells are clickable.
- Clicking a cell → drill-down table via `/api/status?wh_status=&billing_status=` (paginated). Columns: tag, product, reason, uom, wh_status, billing_status, shipped_qty. Tag → `/chain/:tag`.
- Callouts: highlight **shipped + no-reason** (the open business question) and **billable** (eligible, not yet billed).

### 6.3 Candidates (`/candidates`)
- Table from `/api/bill/candidates`: billing_week × PACK/REPACK, tags, cartons, $amount, `tags_missing_rate` (warn icon if >0).
- Below it, **Weekly summary** from `/api/bill/summary`: per week service / labor / total, with a grand-total footer.
- Each week row has a **Bill this week** action → confirm → `POST /api/bill/post {week}` (see 6.4).

### 6.4 Posting to ERP (mutation, used from Candidates and optionally Bill)
- A guarded action: confirmation modal showing what will post (week, PACK/REPACK totals from candidates) and the warning "this posts orders to Famous and marks tags billed."
- On confirm → `POST /api/bill/post`. Show per-transaction results (`results[]`), the `recorded` count, and refetch candidates/summary/status. On partial failure, show which transaction types failed (the backend records only the ones that posted OK, so retry is safe).

### 6.5 Chain explorer (`/chain/:tag`)
- `GET /api/chain/:tag`. Render the chain as an indented list / simple tree by `depth`: each node shows tag, run (icrunidx), reason, uom, productname, qty, and a **billable** badge. Non-billable nodes are dimmed (bins / non-39-41 reasons). Header: the shipped tag + its product.

### 6.6 Labor (`/labor`)
- **Needed:** `/api/labor/needed?week` (week optional filter) — runs needing labor entry.
- **Request:** form (`to_address`, `week`) → `POST /api/labor/request`. Success toast with run count.
- **History:** `/api/labor/status` — table of requests (week, recipient, run_count, status pending/received, sent/received timestamps).
- Note in UI copy: replies are recorded automatically by the mail webhook (`standalone/webhook.py`), not from this screen.

### 6.7 Admin (`/admin`)
- Buttons (each a mutation with spinner + result toast): **Ingest reports** (`/api/ingest`), **Mirror rates** (`/api/rates/mirror`), **Refresh classification** (`/api/classification/refresh`).
- A read panel from `/api/meta`: row counts per table, billed tags, rate/classification counts, `db_ok`, and (if surfaced) last-run timestamps.
- `mirror-rates` can fail if MSSQL is unreachable — show the error message plainly, don't swallow it.

---

## 7. Cross-cutting UI behavior

- **Loading / empty / error** states for every fetch (TanStack Query gives `isLoading`/`isError`). Errors render the backend's `{error}` string.
- **Money/qty formatting** in one util: money `$#,##0.00`; qty integer when whole else `g`.
- **Mutations** disable their trigger while pending and refetch the queries they invalidate (e.g. post → invalidate candidates, summary, status, bill).
- **Deep-linkable:** `/bill?start=…&end=…&status=…` should hydrate the form from query params so a bill view is shareable.
- **No silent truncation:** drill-down tables paginate; show total count and "showing X of N".

---

## 8. Styling tokens (from the existing UI)

```
bg #0f1115  panel #181b22  line #272b35  text #e6e8ee  muted #9aa3b2
accent #4f8cff  good #36b37e  warn #e2b53d  bad #e0556b
```
Tabular-nums for all numeric columns. Status chips: billed=good, billable=accent, not-billable=muted, no-reason=warn; shipped=good, repacked=accent, on-hand=muted.

---

## 9. Non-functional

- **Auth:** none today (same as the current page/webhook). If exposed beyond localhost, add a shared-secret header or put it behind the same gate as the rest of the deployment. Treat **post-to-ERP** and **labor-request** as privileged.
- **Config:** API base is same-origin `/api`. No secrets in the client.
- **Build/serve:** `vite build` → static assets served by Starlette at `/` (add a `StaticFiles` mount; keep `/api/*` routes). Dev: Vite dev server proxying `/api` to the Python server (e.g. `localhost:8100`).
- **Performance:** line tables can be a few thousand rows (e.g. a wide period) — virtualize the line-item table, or rely on pagination for `/api/status`.

---

## 10. Out of scope / open questions

- **Invoice number** source — currently a placeholder/`--invoice-number`; if the frontend triggers invoices, where does the number come from (AR sequence)? (See PDF spec.)
- **Labor on the invoice** — invoice PDF currently lists service lines only; decide if a labor line is added.
- **Editing** rates/classification — assumed read-only here (source-of-truth is MSSQL / the classifier).
- **Multi-customer** — everything is currently Creekside (customer 1680); the period bill isn't customer-scoped. If multi-customer billing is needed, the API and screens grow a customer dimension.

---

## 11. Suggested build order

1. Scaffold (Vite+TS+Router+Query), health header (`/api/meta`), shared fetch + format utils, theme.
2. **Generate Bill** screen against the two existing endpoints (`/api/bill`, `/api/invoice.pdf`) + CSV — delivers value immediately with no backend work.
3. Add the **[add]** read endpoints; build Reconciliation, Candidates, Chain.
4. Labor screens.
5. Mutations (post-to-ERP, labor-request) + Admin actions, with confirmations.
6. Static-serve the build from Starlette; retire the legacy HTML page.
