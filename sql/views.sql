-- Billing logic for Creekside packing charges, expressed entirely as SQL views
-- over the creekside_core schema. No application code computes billing.
--
-- Source tables (loaded by ingest.py):
--   repack_lines / repack_inputs / repack_outputs  -- split repack report
--   cs_packing_shipping_raw                         -- shipping report (numeric cols normalized)
--   cs_packing_billing_lines                        -- billed lines; a tag is "billed" iff present here

-- ---------------------------------------------------------------------------
-- Reference/cache tables the billing views join to (populated by Python edges):
--   packing_rates           <- mirror.py    (MSSQL creekside_packing_rates)
--   product_classification  <- classify.py  (product name -> commtype/commodity/style)
--   labor_charges           <- labor.py     (ops-entered labor per repack run)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS creekside_core.packing_rates (
    commtype text, commodity text, style text, bagtype text, method text, charge numeric
);
ALTER TABLE creekside_core.packing_rates ADD COLUMN IF NOT EXISTS bagtype text;
ALTER TABLE creekside_core.packing_rates ADD COLUMN IF NOT EXISTS method text;
CREATE TABLE IF NOT EXISTS creekside_core.product_classification (
    productname text PRIMARY KEY, type text, commodity text, style text, needs_llm boolean, bagtype text
);
ALTER TABLE creekside_core.product_classification ADD COLUMN IF NOT EXISTS bagtype text;
CREATE TABLE IF NOT EXISTS creekside_core.labor_charges (
    icrunidx bigint, week_end text, productdescr text, quantity numeric,
    num_laborers numeric, hours_per_laborer numeric, labor_rate numeric, labor_charge numeric
);
-- Storage rates: per-carton cold-storage rate by pack form, ingested from the
-- Reedley Charges workbook (rates.ingest_storage_rates / cli ingest-storage-rates).
-- Keyed like packing_rates — (commtype, commodity, style, bagtype) — so v_storage_charges
-- can join it through product_classification. rate is $ per carton per day; a full
-- pallet works out to ~$60/day (rate = 60 / ctns_per_pallet). See v_storage_charges.
CREATE TABLE IF NOT EXISTS creekside_core.storage_rates (
    commtype text, commodity text, style text, bagtype text,
    ctns_per_pallet numeric, rate numeric
);
-- charge_type distinguishes a tag's storage billing from its packing billing on the
-- shared billing-lines table: NULL/'PACKING' = the packing/repack service charge (all
-- 5k legacy rows), 'STORAGE' = a storage charge. Lets a pallet be billed for both,
-- independently. (IF EXISTS: the table is created by the ERP/ingest side, not here.)
ALTER TABLE IF EXISTS creekside_core.cs_packing_billing_lines
    ADD COLUMN IF NOT EXISTS charge_type text;
-- Repack materials, ops-entered per repack run (icrunidx) — the materials half of
-- repack time-&-materials billing (labor_charges is the labor half). Repacks carry
-- no per-carton pack rate (see v_charges); their charge is labor + materials.
CREATE TABLE IF NOT EXISTS creekside_core.repack_materials (
    icrunidx bigint, week_end text, productdescr text, quantity numeric,
    material_descr text, material_qty numeric, unit_cost numeric, materials_charge numeric
);
-- Tracks the labor-sheet email round-trip (replaces the graph's suspend-on-interrupt).
CREATE TABLE IF NOT EXISTS creekside_core.labor_requests (
    week text PRIMARY KEY,
    to_address text,
    run_count integer,
    status text NOT NULL DEFAULT 'pending',
    sent_at timestamptz DEFAULT now(),
    received_at timestamptz
);

-- Drop derived views first so re-running is shape-independent (CREATE OR REPLACE
-- can't change a column's type/order; a redefinition would otherwise fail).
DROP VIEW IF EXISTS creekside_core.v_bill_summary;
DROP VIEW IF EXISTS creekside_core.v_billed_lines;
DROP VIEW IF EXISTS creekside_core.v_bill_candidates;
DROP VIEW IF EXISTS creekside_core.v_billable_unbilled;
DROP VIEW IF EXISTS creekside_core.v_bill_lines;
DROP VIEW IF EXISTS creekside_core.v_storage_candidates;
DROP VIEW IF EXISTS creekside_core.v_storage_charges;
DROP VIEW IF EXISTS creekside_core.v_charges;
DROP VIEW IF EXISTS creekside_core.v_repack_chain;
DROP VIEW IF EXISTS creekside_core.v_repack_status;
DROP VIEW IF EXISTS creekside_core.v_shipped;

-- ---------------------------------------------------------------------------
-- v_shipped: one row per shipped pallet/tag, qty summed across its ship lines.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW creekside_core.v_shipped AS
SELECT s.pallet::bigint            AS tagid,
       sum(s.icqnt)::numeric       AS shipped_qty,
       min(s.sono::text)           AS sono,
       min(s.lastconame)           AS lastconame,
       -- ship date (guard the cast; some footer rows carry junk)
       min(CASE WHEN s.shipdatetime ~ '^\d{2}/\d{2}/\d{4}'
                THEN to_date(left(s.shipdatetime, 10), 'MM/DD/YYYY') END) AS ship_date,
       -- receive date (same guard) — drives storage days = ship_date - recv_date
       min(CASE WHEN s.recvdate ~ '^\d{2}/\d{2}/\d{4}'
                THEN to_date(left(s.recvdate, 10), 'MM/DD/YYYY') END) AS recv_date,
       -- product name off the shipping report (most shipped pallets never hit the
       -- repack tables, so this is the only route to a classification for storage)
       min(s.productdescr) AS productname
FROM creekside_core.cs_packing_shipping_raw s
WHERE s.pallet IS NOT NULL
  AND s.recordtype = 5
  AND s.trxtype = 1
GROUP BY s.pallet::bigint;

-- ---------------------------------------------------------------------------
-- v_repack_chain: for each shipped output tag, walk the repack chain backward
-- (this run's input tags that were themselves prior outputs), recursively.
--   qty   = shipped_qty at the shipped node; consumed input qty upstream.
--   billable = reason 39/41 AND a carton (product name has no SETBACK/FIELD BIN).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW creekside_core.v_repack_chain AS
WITH RECURSIVE chain AS (
    SELECT sh.tagid                       AS shipped_tag,
           o.tagid::bigint                AS tagid,
           o.icrunidx                     AS icrunidx,
           o.fcreasonidx                  AS fcreasonidx,
           o.productname                  AS productname,
           o.uom                          AS uom,
           sh.shipped_qty                 AS qty,
           0                              AS depth,
           ARRAY[o.tagid::bigint]         AS visited
    FROM creekside_core.v_shipped sh
    JOIN creekside_core.repack_outputs o ON o.tagid::bigint = sh.tagid
    UNION ALL
    SELECT c.shipped_tag,
           po.tagid::bigint,
           po.icrunidx,
           po.fcreasonidx,
           po.productname,
           po.uom,
           i.qnt::numeric,
           c.depth + 1,
           c.visited || po.tagid::bigint
    FROM chain c
    JOIN creekside_core.repack_inputs  i  ON i.icrunidx = c.icrunidx AND i.tagid IS NOT NULL
    JOIN creekside_core.repack_outputs po ON po.tagid::bigint = i.tagid::bigint
    WHERE NOT (po.tagid::bigint = ANY(c.visited))   -- cycle guard
)
-- Dedup: a run fans out by icrunidx, so the same upstream tag can be reached via
-- several depth-1 siblings. Bill each distinct (shipped_tag, tagid) once, at its
-- shallowest depth, so a chain node is never double-counted.
SELECT DISTINCT ON (shipped_tag, tagid)
       shipped_tag, tagid, icrunidx, fcreasonidx, productname, uom, qty, depth,
       (fcreasonidx IN (39, 41)
        AND productname !~* 'SETBACK BIN|FIELD BIN') AS billable
FROM chain
ORDER BY shipped_tag, tagid, depth;

-- ---------------------------------------------------------------------------
-- v_repack_status: one row per repack tag — warehouse + billing reconciliation.
--   wh_status      shipped | repacked | on-hand
--   billing_status billed | billable | not-billable | NULL(no reason code)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW creekside_core.v_repack_status AS
WITH billed AS (
    -- packing "billed" = a packing/repack service line for the tag; storage lines
    -- (charge_type='STORAGE') are a separate charge and must not count here.
    SELECT DISTINCT tagid::bigint AS tagid
    FROM creekside_core.cs_packing_billing_lines
    WHERE tagid IS NOT NULL AND coalesce(charge_type, 'PACKING') <> 'STORAGE'
),
consumed AS (
    SELECT DISTINCT tagid::bigint AS tagid
    FROM creekside_core.repack_inputs WHERE tagid IS NOT NULL
),
tags AS (
    -- every produced (output) tag
    SELECT * FROM (
        SELECT DISTINCT ON (o.tagid::bigint)
               o.tagid::bigint AS tagid, o.icrunidx, o.productname, o.fcreasonidx,
               o.qnt, o.uom, o.rundate, 'output'::text AS role
        FROM creekside_core.repack_outputs o
        WHERE o.tagid IS NOT NULL
        ORDER BY o.tagid::bigint
    ) op
    UNION ALL
    -- plus input-only tags that shipped (e.g. repack-loss), never produced here
    SELECT * FROM (
        SELECT DISTINCT ON (i.tagid::bigint)
               i.tagid::bigint AS tagid, i.icrunidx, i.productname, i.fcreasonidx,
               i.qnt, i.uom, i.rundate, 'input_only'::text AS role
        FROM creekside_core.repack_inputs i
        JOIN creekside_core.v_shipped sh ON sh.tagid = i.tagid::bigint
        WHERE i.tagid IS NOT NULL
          AND i.tagid::bigint NOT IN (
              SELECT o2.tagid::bigint FROM creekside_core.repack_outputs o2 WHERE o2.tagid IS NOT NULL
          )
        ORDER BY i.tagid::bigint
    ) ip
)
SELECT t.tagid, t.role, t.icrunidx, t.productname, t.fcreasonidx, t.qnt, t.uom, t.rundate,
       CASE WHEN sh.tagid IS NOT NULL THEN 'shipped'
            WHEN c.tagid  IS NOT NULL THEN 'repacked'
            ELSE 'on-hand' END                         AS wh_status,
       sh.shipped_qty,
       CASE
           WHEN b.tagid IS NOT NULL THEN 'billed'
           WHEN t.fcreasonidx IS NULL THEN NULL
           WHEN t.fcreasonidx IN (39, 41)
                AND t.productname !~* 'SETBACK BIN|FIELD BIN' THEN 'billable'
           ELSE 'not-billable'
       END                                             AS billing_status
FROM tags t
LEFT JOIN billed    b  ON b.tagid  = t.tagid
LEFT JOIN consumed  c  ON c.tagid  = t.tagid
LEFT JOIN creekside_core.v_shipped sh ON sh.tagid = t.tagid;

-- ---------------------------------------------------------------------------
-- v_billable_unbilled: tags eligible to bill that aren't yet in billing_lines.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW creekside_core.v_billable_unbilled AS
SELECT *
FROM creekside_core.v_repack_status
WHERE billing_status = 'billable';

-- ---------------------------------------------------------------------------
-- v_bill_candidates: what to bill next — shipped, billable, not-yet-billed tags
-- rolled up by billing week (Sunday on/after ship date) and PACK/REPACK.
-- (Charge amount is applied at post time from the rate table; this is the qty.)
-- ---------------------------------------------------------------------------
-- v_charges: one charge line per shipped, billable-reason carton — rate joined in
-- (via cached classification), amount computed, and a `billed` flag (whether the
-- tag is already in cs_packing_billing_lines). The amount is computed from rates,
-- so it's correct even for tags billed before amounts were stored.
CREATE OR REPLACE VIEW creekside_core.v_charges AS
SELECT st.tagid,
       st.icrunidx,
       st.productname,
       st.fcreasonidx,
       CASE st.fcreasonidx WHEN 39 THEN 'PACK' WHEN 41 THEN 'REPACK' ELSE 'OTHER' END AS transaction_type,
       sh.ship_date,
       (sh.ship_date + mod(7 - extract(dow FROM sh.ship_date)::int, 7) * interval '1 day')::date
           AS billing_week,
       pc.type, pc.commodity, pc.style, pc.bagtype,
       -- Repacks (reason 41) are billed on time & materials (labor_charges +
       -- repack_materials), NOT the straight pack rate, so they carry no per-carton
       -- service charge here. PACK/OTHER keep rate x shipped_qty as before.
       CASE WHEN st.fcreasonidx = 41 THEN NULL
            ELSE coalesce(r.charge, rg.charge) END              AS rate,
       st.shipped_qty,
       CASE WHEN st.fcreasonidx = 41 THEN NULL
            ELSE round(st.shipped_qty * coalesce(r.charge, rg.charge), 2) END AS amount,
       -- a repack is intentionally un-rated, not "missing" a rate
       CASE WHEN st.fcreasonidx = 41 THEN false
            ELSE (coalesce(r.charge, rg.charge) IS NULL) END     AS rate_missing,
       CASE WHEN st.fcreasonidx = 41 THEN false
            ELSE (r.charge IS NULL AND rg.charge IS NOT NULL) END AS rate_giro_fallback,
       (st.billing_status = 'billed')                           AS billed
FROM creekside_core.v_repack_status st
JOIN creekside_core.v_shipped sh ON sh.tagid = st.tagid
LEFT JOIN creekside_core.product_classification pc ON pc.productname = st.productname
-- exact rate for the product's pack form (style + bagtype)
LEFT JOIN creekside_core.packing_rates r
       ON btrim(r.commtype) = pc.type
      AND btrim(r.commodity) = pc.commodity
      AND btrim(r.style) = pc.style
      AND coalesce(btrim(r.bagtype), '') = coalesce(pc.bagtype, '')
-- fallback to the GIRO rate when a specific bag-type rate doesn't exist
LEFT JOIN creekside_core.packing_rates rg
       ON btrim(rg.commtype) = pc.type
      AND btrim(rg.commodity) = pc.commodity
      AND btrim(rg.style) = pc.style
      AND btrim(rg.bagtype) = 'GIRO'
WHERE st.wh_status = 'shipped'
  AND st.billing_status IN ('billed', 'billable');

-- v_bill_lines: the not-yet-billed charges (what to invoice).
CREATE OR REPLACE VIEW creekside_core.v_bill_lines AS
SELECT * FROM creekside_core.v_charges WHERE NOT billed;

-- v_bill_candidates: rollup of v_bill_lines — what to bill, by week x PACK/REPACK.
CREATE OR REPLACE VIEW creekside_core.v_bill_candidates AS
SELECT billing_week,
       transaction_type,
       count(*)                              AS tags,
       sum(shipped_qty)                      AS shipped_qty,
       sum(amount)                           AS amount,
       count(*) FILTER (WHERE rate_missing)  AS tags_missing_rate
FROM creekside_core.v_bill_lines
GROUP BY billing_week, transaction_type
ORDER BY billing_week, transaction_type;

-- v_billed_lines: the already-billed charges (reprint), amount recomputed from
-- rates + shipping-report ship date — correct even for tags billed before amounts
-- were stored on cs_packing_billing_lines.
CREATE OR REPLACE VIEW creekside_core.v_billed_lines AS
SELECT * FROM creekside_core.v_charges WHERE billed;

-- ---------------------------------------------------------------------------
-- v_storage_charges: one row per shipped pallet that sat past the 7-day free period.
--   days_in_storage = ship_date - recv_date; billable_days = days beyond 7.
--   The rate is per carton per day, looked up by the pallet's pack form
--   (product_classification -> storage_rates, exact then GIRO fallback, like
--   v_charges). amount = shipped_qty (cartons) x rate x billable_days, so a full
--   pallet is ~$60/day. rate_missing flags pallets with no matching per-carton rate
--   (most setback/bin/unclassified pallets — add mappings to storage_rates to price
--   them); `billed` reflects a STORAGE line already recorded for the tag.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW creekside_core.v_storage_charges AS
WITH billed_storage AS (
    SELECT DISTINCT tagid::bigint AS tagid
    FROM creekside_core.cs_packing_billing_lines
    WHERE tagid IS NOT NULL AND charge_type = 'STORAGE'
)
SELECT sh.tagid,
       sh.sono,
       sh.lastconame,
       sh.productname,
       pc.type       AS commtype,
       pc.commodity,
       pc.style,
       pc.bagtype,
       sh.shipped_qty,
       sh.recv_date,
       sh.ship_date,
       (sh.ship_date + mod(7 - extract(dow FROM sh.ship_date)::int, 7) * interval '1 day')::date
           AS billing_week,
       (sh.ship_date - sh.recv_date)         AS days_in_storage,
       7                                     AS free_days,
       (sh.ship_date - sh.recv_date) - 7     AS billable_days,
       coalesce(sr.rate, srg.rate)           AS rate,
       round(sh.shipped_qty * coalesce(sr.rate, srg.rate)
             * ((sh.ship_date - sh.recv_date) - 7), 2) AS amount,
       (coalesce(sr.rate, srg.rate) IS NULL) AS rate_missing,
       (srg.rate IS NOT NULL AND sr.rate IS NULL) AS rate_giro_fallback,
       (b.tagid IS NOT NULL)                 AS billed
FROM creekside_core.v_shipped sh
LEFT JOIN creekside_core.product_classification pc ON pc.productname = sh.productname
-- exact per-carton rate for the pallet's pack form
LEFT JOIN creekside_core.storage_rates sr
       ON sr.commtype = pc.type AND sr.commodity = pc.commodity
      AND sr.style = pc.style AND sr.bagtype = pc.bagtype
-- GIRO fallback within (commtype, commodity, style), mirroring v_charges
LEFT JOIN creekside_core.storage_rates srg
       ON srg.commtype = pc.type AND srg.commodity = pc.commodity
      AND srg.style = pc.style AND srg.bagtype = 'GIRO'
LEFT JOIN billed_storage b ON b.tagid = sh.tagid
WHERE sh.recv_date IS NOT NULL
  AND sh.ship_date IS NOT NULL
  AND (sh.ship_date - sh.recv_date) > 7;

-- v_storage_candidates: storage rollup by billing week (mirrors v_bill_candidates),
-- over the not-yet-billed storage charges — what storage there is left to invoice.
CREATE OR REPLACE VIEW creekside_core.v_storage_candidates AS
SELECT billing_week,
       count(*)                              AS pallets,
       coalesce(sum(amount), 0)              AS amount,
       count(*) FILTER (WHERE rate_missing)  AS pallets_missing_rate
FROM creekside_core.v_storage_charges
WHERE NOT billed
GROUP BY billing_week
ORDER BY billing_week;

-- v_bill_summary: the full weekly bill — pack service (PACK only; repacks are
-- un-rated in v_charges) + repack labor + repack materials + storage + total.
CREATE OR REPLACE VIEW creekside_core.v_bill_summary AS
WITH svc AS (
    SELECT billing_week, sum(amount) AS service_amount
    FROM creekside_core.v_bill_lines
    GROUP BY billing_week
),
sto AS (
    SELECT billing_week, sum(amount) AS storage_amount
    FROM creekside_core.v_storage_charges
    WHERE NOT billed
    GROUP BY billing_week
),
lab AS (
    SELECT to_date(week_end, 'YYYY-MM-DD') AS billing_week, sum(labor_charge) AS labor_amount
    FROM creekside_core.labor_charges
    WHERE week_end ~ '^\d{4}-\d{2}-\d{2}$'
    GROUP BY to_date(week_end, 'YYYY-MM-DD')
),
mat AS (
    SELECT to_date(week_end, 'YYYY-MM-DD') AS billing_week, sum(materials_charge) AS materials_amount
    FROM creekside_core.repack_materials
    WHERE week_end ~ '^\d{4}-\d{2}-\d{2}$'
    GROUP BY to_date(week_end, 'YYYY-MM-DD')
),
weeks AS (
    SELECT billing_week FROM svc
    UNION SELECT billing_week FROM lab
    UNION SELECT billing_week FROM mat
    UNION SELECT billing_week FROM sto
)
SELECT w.billing_week,
       coalesce(svc.service_amount, 0)    AS service_amount,
       coalesce(lab.labor_amount, 0)      AS labor_amount,
       coalesce(mat.materials_amount, 0)  AS materials_amount,
       coalesce(sto.storage_amount, 0)    AS storage_amount,
       coalesce(svc.service_amount, 0)
         + coalesce(lab.labor_amount, 0)
         + coalesce(mat.materials_amount, 0)
         + coalesce(sto.storage_amount, 0) AS total_amount
FROM weeks w
LEFT JOIN svc ON svc.billing_week = w.billing_week
LEFT JOIN lab ON lab.billing_week = w.billing_week
LEFT JOIN mat ON mat.billing_week = w.billing_week
LEFT JOIN sto ON sto.billing_week = w.billing_week
ORDER BY w.billing_week;
