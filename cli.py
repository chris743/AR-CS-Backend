"""CLI: python -m standalone.cli <command>

Commands:
  init-views                 create/refresh the SQL views
  fetch                      download both reports from the SMB share
  ingest [--repacks P] [--shipping P]   load CSVs into Postgres (default: /tmp files)
  summary                    wh_status x billing_status crosstab
  status                     full per-tag reconciliation (head)
  billable                   tags eligible to bill but not yet billed
  unbilled-shipped           shipped but not billed (any reason)
  chain <tag>                full repack chain behind one shipped tag
  billable-chain             one row per billable chain node (chain-aware billing)
"""

import argparse
from pathlib import Path

import pandas as pd

from . import bill, classify, config, db, ingest, labor, queries, rates

pd.set_option("display.width", 200)
pd.set_option("display.max_colwidth", 46)

_VIEWS = Path(__file__).resolve().parent / "sql" / "views.sql"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="standalone.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-views")
    sub.add_parser("fetch")
    ing = sub.add_parser("ingest")
    ing.add_argument("--repacks", default=config.REPACKING_LOCAL)
    ing.add_argument("--shipping", default=config.SHIPPING_LOCAL)
    sub.add_parser("summary")
    sub.add_parser("status")
    sub.add_parser("billable")
    sub.add_parser("unbilled-shipped")
    sub.add_parser("mirror-rates")
    sub.add_parser("refresh-classification")
    sub.add_parser("bill-candidates")
    bl = sub.add_parser("bill-lines")
    bl.add_argument("week")
    bp = sub.add_parser("bill-post")
    bp.add_argument("week")
    bi = sub.add_parser("bill-invoice")
    bi.add_argument("start")
    bi.add_argument("end")
    bi.add_argument("--status", default="all")
    bi.add_argument("--out", default=None)
    bi.add_argument("--invoice-number", default=None)
    ln = sub.add_parser("labor-needed")
    ln.add_argument("--week", default=None)
    lr = sub.add_parser("labor-request")
    lr.add_argument("to_address")
    lr.add_argument("week")
    lrec = sub.add_parser("labor-record")
    lrec.add_argument("week")
    sub.add_parser("labor-status")
    sub.add_parser("bill-summary")
    ch = sub.add_parser("chain")
    ch.add_argument("tag", type=int)
    sub.add_parser("billable-chain")
    sr = sub.add_parser("shipping-run")
    sr.add_argument("--week", default=None, help="WE folder, e.g. 'WE 05.17.2026' (default: most recent)")
    sub.add_parser("shipping-runs")
    spost = sub.add_parser("shipping-post")
    spost.add_argument("week")
    spost.add_argument("--approve", action="store_true")

    args = p.parse_args(argv)

    if args.cmd == "init-views":
        db.run_sql_file(_VIEWS)
        print(f"views created/refreshed from {_VIEWS}")
    elif args.cmd == "fetch":
        print(ingest.fetch_reports())
    elif args.cmd == "ingest":
        print(ingest.ingest_all(args.repacks, args.shipping))
    elif args.cmd == "summary":
        df = queries.summary()
        print(df.to_string(index=False))
        print(f"\ntotal tags: {int(df['tags'].sum())}")
    elif args.cmd == "status":
        print(queries.status().head(30).to_string(index=False))
    elif args.cmd == "billable":
        df = queries.billable_unbilled()
        print(df.head(40).to_string(index=False))
        print(f"\n{len(df)} billable-unbilled tags")
    elif args.cmd == "unbilled-shipped":
        df = queries.unbilled_shipped()
        print(f"{len(df)} shipped-but-unbilled tags; by billing_status:")
        print(df["billing_status"].fillna("(no reason)").value_counts().to_string())
    elif args.cmd == "mirror-rates":
        print(f"mirrored {rates.mirror()} rate rows into packing_rates")
    elif args.cmd == "refresh-classification":
        print(classify.refresh())
    elif args.cmd == "bill-candidates":
        df = queries.bill_candidates()
        print(df.to_string(index=False))
        print(f"\n{int(df['tags'].sum())} tags, {df['shipped_qty'].sum()} cartons, "
              f"${df['amount'].sum():,.2f}; {int(df['tags_missing_rate'].sum())} tags missing a rate")
    elif args.cmd == "bill-lines":
        print(bill.lines(args.week).to_string(index=False))
    elif args.cmd == "bill-post":
        print(bill.post(args.week))
    elif args.cmd == "bill-invoice":
        meta = {"invoice_number": args.invoice_number} if args.invoice_number else {}
        print(bill.invoice_for_period(args.start, args.end, args.status, args.out, **meta))
    elif args.cmd == "labor-needed":
        df = labor.needed(args.week)
        print(df.to_string(index=False))
        print(f"\n{len(df)} repack runs need labor entry")
    elif args.cmd == "labor-request":
        print(labor.email_request(args.to_address, args.week))
    elif args.cmd == "labor-record":
        print(labor.record_from_reply(args.week))
    elif args.cmd == "labor-status":
        print(labor.status().to_string(index=False))
    elif args.cmd == "bill-summary":
        df = queries.bill_summary()
        print(df.to_string(index=False))
        print(f"\ngrand total: ${df['total_amount'].sum():,.2f} "
              f"(service ${df['service_amount'].sum():,.2f} + labor ${df['labor_amount'].sum():,.2f} "
              f"+ materials ${df['materials_amount'].sum():,.2f})")
    elif args.cmd == "chain":
        print(queries.chain(args.tag).to_string(index=False))
    elif args.cmd == "billable-chain":
        df = queries.billable_chain()
        print(df.head(40).to_string(index=False))
        print(f"\n{len(df)} billable chain nodes; "
              f"{df['shipped_tag'].nunique()} shipped tags; "
              f"multi-step: {(df.groupby('shipped_tag').size() > 1).sum()}")
    elif args.cmd == "shipping-run":
        import asyncio
        from . import shipping
        print(asyncio.run(shipping.run(args.week)))
    elif args.cmd == "shipping-runs":
        from . import shipping
        for r in shipping.list_runs():
            print(r)
    elif args.cmd == "shipping-post":
        from . import shipping
        print(shipping.post(args.week, approve=args.approve))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
