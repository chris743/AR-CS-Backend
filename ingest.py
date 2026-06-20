"""EDGE: load the CSV reports into Postgres (split repacks, normalize shipping).

This is the only place that touches the file format. The repack report is split
by line type into three tables; the shipping report is normalized (footer rows
make recordtype/trxtype/icqnt/pallet string-typed) and loaded as one.
"""

import pandas as pd

from . import config, db

REPACK_LINES = "repack_lines"
REPACK_INPUTS = "repack_inputs"
REPACK_OUTPUTS = "repack_outputs"
SHIPPING = "cs_packing_shipping_raw"


def split_repacks(df: pd.DataFrame):
    """(lines, inputs, outputs) by line type; drops rectype 3 + charge sub-lines."""
    df.columns = [str(c).strip().lower() for c in df.columns]
    lines = df[(df["rectype"] == 1) & df["productname"].notna()]
    tags = df[(df["rectype"] == 2) & df["tagid"].notna()]
    inputs = tags[tags["outputflag"] == "N"]
    outputs = tags[tags["outputflag"] == "Y"]
    return lines, inputs, outputs


def load_repacks(csv_path: str = config.REPACKING_LOCAL) -> dict:
    df = pd.read_csv(csv_path, low_memory=False)
    lines, inputs, outputs = split_repacks(df)
    return {
        REPACK_LINES: db.replace(lines, REPACK_LINES),
        REPACK_INPUTS: db.replace(inputs, REPACK_INPUTS),
        REPACK_OUTPUTS: db.replace(outputs, REPACK_OUTPUTS),
    }


def load_shipping(csv_path: str = config.SHIPPING_LOCAL) -> dict:
    df = pd.read_csv(csv_path, encoding=config.SHIPPING_ENCODING, low_memory=False)
    df.columns = [str(c).strip().lower() for c in df.columns]
    # Footer/title rows turn these string-typed; coerce so the views' casts hold.
    for col in ("recordtype", "trxtype", "icqnt", "pallet"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return {SHIPPING: db.replace(df, SHIPPING)}


def fetch_reports() -> dict:
    """Download both reports from the SMB share to the local landing paths."""
    import smbclient

    smbclient.ClientConfig(username=config.SMB_USERNAME, password=config.SMB_PASSWORD)
    jobs = [
        (config.REPACKING_FILE, config.REPACKING_LOCAL),
        (config.SHIPPING_FILE, config.SHIPPING_LOCAL),
    ]
    out = {}
    for remote, local in jobs:
        path = rf"\\{config.SMB_HOST}\{config.SMB_SHARE}\{remote}"
        with smbclient.open_file(path, mode="rb") as src, open(local, "wb") as dst:
            dst.write(src.read())
        out[remote] = local
    return out


def ingest_all(
    repacks: str = config.REPACKING_LOCAL,
    shipping: str = config.SHIPPING_LOCAL,
) -> dict:
    counts = {}
    counts.update(load_repacks(repacks))
    counts.update(load_shipping(shipping))
    return counts
