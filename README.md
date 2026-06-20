# Creekside packing-charges — standalone billing

A LangGraph-free billing system. All billing *logic* lives in SQL views over the
`creekside_core` Postgres schema; Python is a thin layer for I/O and querying.

```
standalone/
  config.py     env (PG_DATABASE_URL, schema, SMB creds, file names)
  db.py         engine + helpers (query / execute / replace / run SQL files)
  sql/views.sql the billing logic: v_shipped, v_repack_chain, v_repack_status, v_billable_unbilled
  ingest.py     EDGE: load the CSV reports into the repack/shipping tables (split + normalize)
  queries.py    read API over the views (status, summary, billable, chain, ...)
  actions.py    EDGE: record billed lines (and where ERP-post / labor plug in)
  cli.py        `python -m standalone.cli <command>`
```

## The three layers

| Layer | What | Where |
|---|---|---|
| **Calculate** | split, join, classify, **chain (recursive CTE)**, reconcile, what-to-bill | `sql/views.sql` — pure SQL |
| **External I/O** | fetch CSVs (SMB), load tables, post to ERP, email labor | `ingest.py`, `actions.py` |
| **Human gates** | labor reply, ERP approval | *not orchestrated here* — drive from a table + the dashboard/webhook |

## Tables (in `creekside_core`, shared with the existing system)

- `repack_lines` / `repack_inputs` / `repack_outputs` — the split repack report
- `cs_packing_shipping_raw` — the shipping report
- `cs_packing_billing_lines` — **billed** lines (incl. historical); a tag is *billed* iff it appears here

## Views (the logic)

- `v_shipped` — shipping filtered to detail rows, summed per pallet → `shipped_qty`
- `v_repack_chain` — for each shipped tag, the full repack chain (recursive output→input walk), each node flagged `billable`
- `v_repack_status` — one row per repack tag: `wh_status` (shipped/repacked/on-hand) + `billing_status` (billed/billable/not-billable/NULL)
- `v_billable_unbilled` — shipped-or-not cartons eligible to bill that aren't in `cs_packing_billing_lines` yet

## Usage

Run from the repo root with the venv (`cd /Users/chrism/development/agents`):

```bash
# one-time / on-change setup
venv/bin/python -m standalone.cli init-views              # create/refresh views + reference tables
venv/bin/python -m standalone.cli mirror-rates            # MSSQL creekside_packing_rates -> packing_rates
venv/bin/python -m standalone.cli refresh-classification  # product name -> commodity/style cache

# each cycle
venv/bin/python -m standalone.cli ingest                  # load /tmp CSVs (or --repacks/--shipping)
venv/bin/python -m standalone.cli summary                 # wh_status x billing_status crosstab
venv/bin/python -m standalone.cli bill-candidates         # what to bill: week x PACK/REPACK, qty + $
venv/bin/python -m standalone.cli bill-summary            # full weekly bill: service + labor + total
venv/bin/python -m standalone.cli bill-lines 2026-05-03   # per-tag charge detail for a billing week
venv/bin/python -m standalone.cli chain 231467            # full chain behind a shipped tag

# labor email round-trip (Graph)
venv/bin/python -m standalone.cli labor-needed --week 2026-05-31    # repack runs needing labor
venv/bin/python -m standalone.cli labor-request ops@co.com 2026-05-31  # email the sheet, mark pending
venv/bin/python -m standalone.cli labor-record 2026-05-31          # read reply -> labor_charges, mark received
venv/bin/python -m standalone.cli labor-status                     # request audit (pending/received)

# act (external)
venv/bin/python -m standalone.cli bill-post 2026-05-03    # post the week to Famous, then record billed
```

The labor reply can also arrive event-driven: run the webhook and point a Graph
mail subscription at it — it calls `labor.record_from_reply()` (no suspended run):

```bash
venv/bin/python -m uvicorn standalone.webhook:app --port 8200   # POST /webhooks/graph-mail
```

`mirror-rates` (MSSQL), `bill-post` (Famous), and the labor email steps (Graph) are
the only things that touch external systems; everything else is local SQL over Postgres.

## Billing = inserting rows

There is no "billing engine" — a tag is billed when it's a row in `cs_packing_billing_lines`.
`queries.billable_unbilled()` / the `billable` command lists candidates; once approved + posted to
the ERP, `actions.record_billed(df)` writes them as billed lines. ERP posting and the labor-reply
round-trip are the only pieces that still need an external action (see `actions.py`).
