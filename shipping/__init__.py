"""Creekside shipping-charges flow, ported from the LangGraph cs_shipping_charges
graph into the standalone server — orchestrator-free.

The graph's nodes become plain functions; its two interrupts become state + an
endpoint each (mirroring the labor module):

  graph node            standalone equivalent
  --------------------  ----------------------------------------------------
  fetch_report          reports.download_shipping_report
  process_report        reports.trim_report
  fetch_phytos          phytos.fetch_phyto_reports
  match_phyto           extract.run_phyto_agent + extract.match_debit_row
  combine_reports       invoice.combine_reports
  output_xlsx           invoice.build_xlsx
  review_phyto (interrupt)  flow.run() persists needs_review; flow.review() resolves it
  post_to_erp (interrupt)   flow.post() is the approval gate (dashboard calls it)

State lives in creekside_core.shipping_runs (store.py), keyed by week_folder.
The package public API is re-exported here for the server/CLI.
"""

from .flow import run, review, post, get, list_runs, xlsx_path

__all__ = ["run", "review", "post", "get", "list_runs", "xlsx_path"]
