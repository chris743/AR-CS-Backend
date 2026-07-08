"""Configuration: load env (shared with the existing graph_app/.env)."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Reuse the same .env the graph app uses (PG_DATABASE_URL, SMB_*, etc.).
load_dotenv(Path(__file__).resolve().parents[1] / "graph_app" / ".env")

PG_DATABASE_URL = os.environ["PG_DATABASE_URL"]
SCHEMA = "creekside_core"

# Report sources on the SMB share (used by ingest.fetch_reports()).
SMB_HOST = os.getenv("SMB_HOST", "").strip()
SMB_SHARE = os.getenv("SMB_SHARE", "").strip()
SMB_USERNAME = os.getenv("SMB_USERNAME")
SMB_PASSWORD = os.getenv("SMB_PASSWORD")
REPACKING_FILE = "repack outputs report - most recent.csv"
SHIPPING_FILE = "shipping report - most recent.csv"

# Local landing spots (defaults for the CLI).
REPACKING_LOCAL = "/home/administrator/mnt/creekside-share/repack outputs report - most recent.csv"
SHIPPING_LOCAL = "/home/administrator/mnt/creekside-share/shipping report - most recent.csv"
SHIPPING_ENCODING = "cp1252"
