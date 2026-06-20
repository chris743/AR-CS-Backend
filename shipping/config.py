"""Shipping-charges config — env + constants for this flow only.

Reuses the same .env as the rest of standalone (loaded by standalone.config).
These are the SharePoint / Document-Intelligence / SMB settings the graph nodes
used, plus the invoice + ERP charge codes.
"""

import os

# Importing standalone.config triggers load_dotenv() for the shared graph_app/.env.
from .. import config as _base  # noqa: F401  (side effect: env loaded)

# --- SMB: the shipping CHARGES report (distinct from the packing shipping report)
SMB_HOST = os.getenv("SMB_HOST", "").strip()
SMB_SHARE = os.getenv("SMB_SHARE", "").strip()
SMB_USERNAME = os.getenv("SMB_USERNAME")
SMB_PASSWORD = os.getenv("SMB_PASSWORD")
SHIPPING_CHARGES_FILE = "shipping charges report - most recent.csv"

# --- SharePoint (Graph) source of the phyto certificate packets
SP_TENANT_ID = os.getenv("AZURE_TENANT_ID")
SP_CLIENT_ID = os.getenv("AZURE_CLIENT_ID")
SP_CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET")
SP_SITE_HOSTNAME = "cobblestonefruit.sharepoint.com"
SP_SITE_PATH = "/sites/CobblestoneReedleyInvoicing"
SP_CREEKSIDE_DIR = "Creekside Organics"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# --- Azure Document Intelligence (PDF -> markdown for the phyto agent)
DOC_INTEL_ENDPOINT = os.getenv("AZURE_DOC_INTEL_ENDPOINT")
DOC_INTEL_KEY = os.getenv("AZURE_DOC_INTEL_KEY")

# --- Local staging
SHIPPING_LOCAL = "/tmp/shipping_charges_report.csv"
TRIMMED_LOCAL = "/tmp/shipping_charges_trimmed.csv"
COMBINED_LOCAL = "/tmp/shipping_charges_combined.csv"
PHYTO_STAGE_DIR = "/tmp/phytos"
XLSX_DIR = "/tmp/shipping_xlsx"

# --- Trimmed report columns + invoice metadata (from the graph nodes)
KEEP_COLS = ["shipdatetime", "sono", "chargedescr", "qnt", "rate", "amt"]
INVOICE_CUSTID = 1680
INVOICE_CHARGE_CODE = 1112  # the on-sheet display code from output_xlsx

# WE folder name, e.g. "WE 05.17.2026"
WE_FOLDER_REGEX = r"^WE (\d{2})\.(\d{2})\.(\d{4})$"
