# Run Detail Webform — Backend Contract (for the frontend bot)

This backend replaces the **"Creekside Organics Labor Detail"** spreadsheet —
where ops copied the MASTER tab per run and filled it in by hand. Build a webform
that captures **one run per submission**. The server stores it and computes all
totals; the client never computes money.

- **Base URL:** same server as everything else (`http://<host>:8100`). Full route
  catalog in [API_ROUTES.md](API_ROUTES.md); this doc is the deep dive for the form.
- **Schema is auto-created** on first call — no setup step.
- **Errors:** `{"error": "..."}` with status 400 (bad input) / 404 (missing) / 500.

## Mental model

One submission = one **run**, keyed by **`run_no`** (the "Run #" on the sheet —
it's the repack run id / icrunidx). The form has the same sections as the sheet:

| Sheet section | Payload key | Notes |
|---|---|---|
| Header (Date, Commodity, Varieties, Repack, Restyle, Run #, Pack Out %, Note, type) | top-level fields | |
| Time clock (Start/Lunch/Finish) | top-level `start_time`… | strings as entered, e.g. `"06:00"` |
| HOURS + WAGES by position | `labor[]` | one row per position |
| INPUTS / OUTPUTS | `io[]` | each row tagged `io: "input" \| "output"` |
| New Materials | `material[]` | qty × price; server fills `total` |
| Defects | `defect[]` | type + free-form value |

**Server-computed — display these from the response, never send authoritative values:**
- `material[].total` = `quantity × price_per_unit`
- `total_labor` = Σ `labor[].total_pay`
- `overhead` = `total_labor × overhead_rate` (rate defaults to **0.20**)
- `new_materials` = Σ `material[].total`
- `total` = `total_labor + overhead + new_materials`  ← the run's T&M charge

(The form may still show per-line hours/pay the user types; the server only
re-derives the four rollups above and each material line total.)

---

## 1. Load the form options — `GET /api/run-detail/refs`

Call once on mount to populate dropdowns. `materials[].default_price` is the
current rate (prefill the price when a material is chosen; user can override per run).

```json
{
  "positions": [ { "name": "Input Set-on", "seq": 1 }, { "name": "Grader", "seq": 2 }, ... ],
  "materials": [
    { "name": "CHEP", "category": "pallet", "default_price": 15.0, "unit_label": "pallet", "seq": 1 },
    { "name": "Bag Masters", "category": "pallet", "default_price": 2.14, "unit_label": "pallet", "seq": 3 },
    { "name": "40lb Bottoms", "category": "other", "default_price": 0.75, "unit_label": "unit", "seq": 8 },
    ...
  ],
  "defects": [ { "name": "Scarring", "seq": 1 }, { "name": "Decay", "seq": 6 }, ... ],
  "material_categories": ["pallet", "corner_board", "other"],
  "run_types": ["REPACK-QUALITY", "PACK", "RESTYLE"]
}
```

### Maintain a material rate — `POST /api/run-detail/refs/material`
Ops-only "edit prices" action. Body: `{ "name": "CHEP", "default_price": 16.0 }`
(optional `category`, `unit_label`, `seq`). Upserts and returns the row. New
material names are created on the fly.

---

## 2. Save a run — `POST /api/run-detail`

Upsert (idempotent on `run_no` — saving again overwrites that run, child lines and
all). `run_no` is the only required field. Send what the user filled; omit blanks.

**Request body:**
```json
{
  "run_no": 4967,
  "run_date": "2026-06-16",
  "run_type": "XDOCK",
  "note": "CTN TO BAG",
  "commodity": "ORG ORANGE",
  "varieties": "VALENCIA",
  "repack": "REPACK-QUALITY",
  "restyle": "10/4 LBS",
  "pack_out_pct": 0.8187,
  "start_time": "06:00", "start_lunch": "", "end_lunch": "", "finish_time": "06:25",
  "input_units": 27, "output_units": 21,
  "overhead_rate": 0.20,
  "labor": [
    { "position": "Input Set-on", "num_workers": 1, "reg_hours": 0.42, "ot_hours": 0,
      "total_hours": 0.42, "reg_pay": 7.08, "ot_pay": 0, "total_pay": 7.08 },
    { "position": "Grader", "num_workers": 3, "total_pay": 21.25 }
  ],
  "io": [
    { "io": "input",  "boxes": 27, "label": "FRUIT WORLD", "pack_style": "40 LBS",  "size": "72" },
    { "io": "output", "boxes": 21, "label": "FRUIT WORLD", "pack_style": "10/4 LBS", "size": "." }
  ],
  "material": [
    { "category": "pallet", "material": "CHEP", "quantity": 2, "price_per_unit": 15 }
  ],
  "defect": [
    { "defect_type": "Scarring", "value": "light" }
  ]
}
```
- `labor`/`io`/`material`/`defect` are arrays; send `[]` or omit if empty.
- Material/IO/labor rows keep submission order (server stamps `seq`).
- `material[].total` is ignored on input — the server computes it.
- Aliases accepted: `materials`/`labor`/`io`/`defect` (plural `materials` also works).

**Response = the full stored record** (use it to render the saved state + totals):
```json
{
  "run_no": 4967, "run_date": "2026-06-16", "run_type": "XDOCK", "note": "CTN TO BAG",
  "company": "CREEKSIDE", "commodity": "ORG ORANGE", "varieties": "VALENCIA",
  "repack": "REPACK-QUALITY", "restyle": "10/4 LBS", "pack_out_pct": 0.8187,
  "start_time": "06:00", "finish_time": "06:25", "input_units": 27, "output_units": 21,
  "overhead_rate": 0.2,
  "total_labor": 125.79, "overhead": 25.16, "new_materials": 30.0, "total": 180.95,
  "status": "draft", "created_at": "...", "updated_at": "...",
  "labor":    [ { "seq": 0, "position": "Input Set-on", "num_workers": 1, ... } ],
  "io":       [ { "seq": 0, "io": "input", "boxes": 27, ... } ],
  "material": [ { "seq": 0, "category": "pallet", "material": "CHEP", "quantity": 2,
                  "price_per_unit": 15, "total": 30.0 } ],
  "defect":   [ { "seq": 0, "defect_type": "Scarring", "value": "light" } ]
}
```

**Status:** send `"status": "submitted"` when the user finalizes; default is
`"draft"`. (No separate submit endpoint — it's a field on save.)

---

## 3. Read / list / delete

### `GET /api/run-detail/{run_no}`
Full nested record (same shape as the save response). 404 if not found.

### `GET /api/run-detail?week=&status=&start=&end=`
Lightweight list (no child lines), newest first. All filters optional:
- `week` — billing week (`YYYY-MM-DD`, Sunday on/after run_date)
- `status` — `draft` | `submitted`
- `start` & `end` — run_date range (`YYYY-MM-DD`)
```json
[ { "run_no": 4967, "run_date": "2026-06-16", "repack": "REPACK-QUALITY",
    "commodity": "ORG ORANGE", "varieties": "VALENCIA", "restyle": "10/4 LBS",
    "total_labor": 125.79, "overhead": 25.16, "new_materials": 30.0,
    "total": 180.95, "status": "draft", "updated_at": "..." } ]
```

### `DELETE /api/run-detail/{run_no}`
Removes the run and its child lines (cascade). → `{ "deleted": true }`.

---

## Field reference

**Top-level (all optional except `run_no`):**
`run_no`(int, required), `run_date`(date), `run_type`(str), `note`(str),
`company`(str, default CREEKSIDE), `commodity`(str), `varieties`(str),
`repack`(str ∈ run_types), `restyle`(str), `pack_out_pct`(0..1),
`start_time`/`start_lunch`/`end_lunch`/`finish_time`(str), `input_units`/`output_units`(num),
`overhead_rate`(num, default 0.20), `status`(`draft`|`submitted`).

**`labor[]`:** `position`, `num_workers`, `reg_hours`, `ot_hours`, `total_hours`,
`reg_pay`, `ot_pay`, `total_pay`.
**`io[]`:** `io`(`input`|`output`), `boxes`, `label`, `pack_style`, `size`.
**`material[]`:** `category`(`pallet`|`corner_board`|`other`), `material`, `quantity`,
`price_per_unit` (→ server adds `total`).
**`defect[]`:** `defect_type`, `value`.

## TypeScript types

```ts
type RunType = 'REPACK-QUALITY' | 'PACK' | 'RESTYLE';
type Status = 'draft' | 'submitted';
type MaterialCategory = 'pallet' | 'corner_board' | 'other';

interface LaborLine { position:string; num_workers?:number; reg_hours?:number; ot_hours?:number;
  total_hours?:number; reg_pay?:number; ot_pay?:number; total_pay?:number; seq?:number; }
interface IoLine { io:'input'|'output'; boxes?:number; label?:string; pack_style?:string; size?:string; seq?:number; }
interface MaterialLine { category:MaterialCategory; material:string; quantity?:number;
  price_per_unit?:number; total?:number; seq?:number; }     // total is server-computed
interface DefectLine { defect_type:string; value?:string; seq?:number; }

interface RunDetail {
  run_no:number; run_date?:string; run_type?:string; note?:string; company?:string;
  commodity?:string; varieties?:string; repack?:RunType; restyle?:string; pack_out_pct?:number;
  start_time?:string; start_lunch?:string; end_lunch?:string; finish_time?:string;
  input_units?:number; output_units?:number; overhead_rate?:number;
  total_labor:number; overhead:number; new_materials:number; total:number;  // server-computed
  status:Status; created_at?:string; updated_at?:string;
  labor:LaborLine[]; io:IoLine[]; material:MaterialLine[]; defect:DefectLine[];
}

interface Refs { positions:{name:string;seq:number}[];
  materials:{name:string;category:MaterialCategory;default_price:number;unit_label:string;seq:number}[];
  defects:{name:string;seq:number}[]; material_categories:MaterialCategory[]; run_types:RunType[]; }
```

## Where this feeds (FYI)
Each run's `total` (Labor + Overhead + Materials) is the repack **time-&-materials
charge**. The view `creekside_core.v_run_detail_charges` exposes per-run totals
keyed by `billing_week`, so the weekly bill can consume run-detail directly. The
frontend only needs the routes above.
