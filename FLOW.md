# Running without the orchestrator

The LangGraph workflow can be retired. Nothing in this pipeline needs durable
suspension — every step is either a **SQL view** (logic), a **scheduled/CLI step**,
a **dashboard action**, or a **webhook handler that writes a table**. State lives in
Postgres rows, not graph checkpoints.

## Old graph node → new home

| Former graph node | Replaced by | Kind |
|---|---|---|
| `fetch_report` | `cli ingest` (or `ingest.fetch_reports()`), run by cron | scheduled |
| `ingest_reports` | `cli ingest` → `repack_lines/inputs/outputs`, `cs_packing_shipping_raw` | scheduled |
| `process_report`, `process_shipping_report`, `join_reports`, `split_product_defs` | `v_shipped`, `v_repack_status`, `v_repack_chain` | **SQL view** |
| `collect_labor` + `send_labor_email` | `labor.request()` — build the sheet from a view, email via Graph client | edge (email) |
| `read_labor_charges` *(interrupt: wait for reply)* | mail webhook → `labor.record()` writes a `labor_charges` row | webhook → table |
| `persist_billing` | nothing — "billed" *is* a row in `cs_packing_billing_lines`; "what's due" is `v_bill_candidates` | n/a |
| `load_billing` | `cli bill-candidates` (query) | query |
| `apply_charges` | rate join at post time (mirror `creekside_packing_rates` + a `product_classification` lookup) | query/edge |
| `dump_xlsx` | query → xlsx export | query |
| `post_to_erp` *(interrupt: approval)* | dashboard **Approve** → `bill.post()` (Famous client) | dashboard → edge |
| `mark_billed` | `actions.record_billed()` inserts the billing lines | table write |

The two things LangGraph's interrupts handled — **waiting for the labor reply** and
**the ERP approval gate** — become a table flag + the webhook, and a dashboard button.
No run stays open for days; the data sits in Postgres and any step can run independently.

## The weekly cycle, orchestrator-free

1. **Cron (nightly/weekly):** `cli ingest` — refresh `repack_*` + shipping. Views recompute automatically.
2. **Labor request:** `labor.request(week)` emails ops the repacks needing labor; logs a `labor_requests` row (`pending`).
3. **Labor reply (async, days later):** ops replies → the mail webhook calls `labor.record(msg)` → parses the xlsx into `labor_charges`, flips the request to `received`. *No suspended run.*
4. **Review (the following Monday):** `cli bill-candidates` (or the dashboard) shows what's due, by billing week × PACK/REPACK, plus labor.
5. **Approve + post:** dashboard **Approve** → `bill.post(week)` posts to Famous, then `actions.record_billed(lines)` writes the billing lines. Those tags now read `billed` in `v_repack_status`.

## What still needs an external client (not orchestration)

- **Email** (labor sheet out / reply in) — Microsoft Graph client.
- **ERP post** — Famous client.
- **Rates** live in MSSQL — mirror `creekside_packing_rates` into `creekside_core` so the
  bill is a pure SQL join. Product → (commodity, style) classification (the old
  `split_product_defs`, with its LLM fallback) becomes a cached `product_classification`
  table, refreshed only for new product names.

These are plain client calls reused from `graph_app/src/agent/shared/{famous,microsoft}` —
none of them require LangGraph. The dashboard (`webhook_app.py` `/manage`) already covers
the approval UI; its mail webhook just needs to call `labor.record()` instead of resuming a graph.
```
```
