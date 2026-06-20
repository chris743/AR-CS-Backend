-- Run Detail capture — the DB model behind the "Creekside Organics Labor Detail"
-- webform. One run per submission (run_no = the repack icrunidx); the form's
-- Labor / New Materials / Defects / Inputs-Outputs sections are child tables.
-- Totals (labor, overhead, materials, grand total) are computed on save by
-- store.py and persisted here so billing can read them directly.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS + seed inserts with ON CONFLICT DO NOTHING.

-- ---------------------------------------------------------------------------
-- Parent: one row per run
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS creekside_core.run_detail (
    run_no        bigint PRIMARY KEY,             -- the "Run #" (= repack icrunidx)
    run_date      date,
    run_type      text,                           -- e.g. "XDOCK" (the D4 banner); nullable
    note          text,                           -- "NOTE: CTN TO BAG"
    company       text DEFAULT 'CREEKSIDE',
    commodity     text,                           -- "ORG ORANGE"
    varieties     text,                           -- "VALENCIA"
    repack        text,                           -- "REPACK-QUALITY" | "PACK" | "RESTYLE"
    restyle       text,                           -- conversion, e.g. "CTN TO BAG" / "10/4 LBS"
    pack_out_pct  numeric,                         -- 0..1
    start_time    text,                            -- "HH:MM" as entered
    start_lunch   text,
    end_lunch     text,
    finish_time   text,
    input_units   numeric,
    output_units  numeric,
    overhead_rate numeric NOT NULL DEFAULT 0.20,   -- overhead = total_labor * overhead_rate
    total_labor   numeric NOT NULL DEFAULT 0,       -- computed: sum(run_labor.total_pay)
    overhead      numeric NOT NULL DEFAULT 0,       -- computed: total_labor * overhead_rate
    new_materials numeric NOT NULL DEFAULT 0,       -- computed: sum(run_material.total)
    total         numeric NOT NULL DEFAULT 0,       -- computed: labor + overhead + materials
    status        text NOT NULL DEFAULT 'draft',    -- draft | submitted
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Children (replaced wholesale on each save; FK cascades on delete)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS creekside_core.run_labor (
    id          bigserial PRIMARY KEY,
    run_no      bigint NOT NULL REFERENCES creekside_core.run_detail(run_no) ON DELETE CASCADE,
    seq         int,
    position    text,                              -- "Packers", "Grader", ...
    num_workers numeric,
    reg_hours   numeric,
    ot_hours    numeric,
    total_hours numeric,
    reg_pay     numeric,
    ot_pay      numeric,
    total_pay   numeric
);
CREATE INDEX IF NOT EXISTS run_labor_run_no_idx ON creekside_core.run_labor(run_no);

CREATE TABLE IF NOT EXISTS creekside_core.run_io (
    id         bigserial PRIMARY KEY,
    run_no     bigint NOT NULL REFERENCES creekside_core.run_detail(run_no) ON DELETE CASCADE,
    seq        int,
    io         text,                               -- 'input' | 'output'
    boxes      numeric,
    label      text,
    pack_style text,
    size       text
);
CREATE INDEX IF NOT EXISTS run_io_run_no_idx ON creekside_core.run_io(run_no);

CREATE TABLE IF NOT EXISTS creekside_core.run_material (
    id             bigserial PRIMARY KEY,
    run_no         bigint NOT NULL REFERENCES creekside_core.run_detail(run_no) ON DELETE CASCADE,
    seq            int,
    category       text,                           -- 'pallet' | 'corner_board' | 'other'
    material       text,
    quantity       numeric,
    price_per_unit numeric,
    total          numeric                          -- quantity * price_per_unit
);
CREATE INDEX IF NOT EXISTS run_material_run_no_idx ON creekside_core.run_material(run_no);

CREATE TABLE IF NOT EXISTS creekside_core.run_defect (
    id          bigserial PRIMARY KEY,
    run_no      bigint NOT NULL REFERENCES creekside_core.run_detail(run_no) ON DELETE CASCADE,
    seq         int,
    defect_type text,                              -- "Scarring", "Decay", ...
    value       text                                -- free-form (count / note)
);
CREATE INDEX IF NOT EXISTS run_defect_run_no_idx ON creekside_core.run_defect(run_no);

-- ---------------------------------------------------------------------------
-- Reference tables — drive the form's dropdowns. ref_material.default_price is
-- the maintainable "rate" (seeded from the current sheet; ops edits as needed).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS creekside_core.ref_position (
    name text PRIMARY KEY, seq int
);
CREATE TABLE IF NOT EXISTS creekside_core.ref_material (
    name text PRIMARY KEY, category text, default_price numeric, unit_label text, seq int
);
CREATE TABLE IF NOT EXISTS creekside_core.ref_defect (
    name text PRIMARY KEY, seq int
);

INSERT INTO creekside_core.ref_position (name, seq) VALUES
    ('Input Set-on', 1), ('Grader', 2), ('Packers', 3), ('Machine Operator', 4),
    ('Palletizers', 5), ('Forklift Driver', 6), ('Tally', 7), ('QC', 8),
    ('Clean Up', 9), ('Supervisor', 10)
ON CONFLICT (name) DO NOTHING;

INSERT INTO creekside_core.ref_material (name, category, default_price, unit_label, seq) VALUES
    ('CHEP',             'pallet',       15.00, 'pallet', 1),
    ('Heat Treated',     'pallet',       15.00, 'pallet', 2),
    ('Bag Masters',      'pallet',        2.14, 'pallet', 3),
    ('6.5 Generic Euro', 'pallet',        2.05, 'pallet', 4),
    ('84"',              'corner_board',  1.20, 'unit',   5),
    ('Pallet Cover',     'corner_board',  4.00, 'unit',   6),
    ('Tri Walls 36"',    'corner_board', 23.28, 'unit',   7),
    ('40lb Bottoms',     'other',         0.75, 'unit',   8),
    ('40lb Tops',        'other',         0.77, 'unit',   9)
ON CONFLICT (name) DO NOTHING;

INSERT INTO creekside_core.ref_defect (name, seq) VALUES
    ('Scarring', 1), ('Scale', 2), ('Bruising', 3), ('Soft', 4),
    ('Stem Pulls', 5), ('Decay', 6), ('Sooty Mold', 7)
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Billing-facing view: per-run T&M charge + the billing week (Sunday on/after
-- run_date), so repack billing can consume run totals directly.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW creekside_core.v_run_detail_charges AS
SELECT run_no, run_date,
       (run_date + mod(7 - extract(dow FROM run_date)::int, 7) * interval '1 day')::date AS billing_week,
       repack, commodity, varieties, restyle,
       total_labor, overhead, new_materials, total, status
FROM creekside_core.run_detail
WHERE run_date IS NOT NULL;
