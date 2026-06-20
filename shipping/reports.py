"""The shipping CHARGES report: download from SMB, trim to billable columns.

Ports get_report_file.py + process_report.py. smbclient is imported lazily so the
server can boot without it; pandas is a base dep.
"""

from pathlib import Path

import pandas as pd

from . import config


def download_shipping_report(local_dest: str = config.SHIPPING_LOCAL) -> str:
    """Download the latest shipping charges report from the SMB share. Returns local path."""
    import smbclient

    smbclient.ClientConfig(username=config.SMB_USERNAME, password=config.SMB_PASSWORD)
    remote_path = rf"\\{config.SMB_HOST}\{config.SMB_SHARE}\{config.SHIPPING_CHARGES_FILE}"
    with smbclient.open_file(remote_path, mode="rb") as src, open(local_dest, "wb") as dst:
        dst.write(src.read())
    return local_dest


def trim_report(input_path: str, output_path: str = config.TRIMMED_LOCAL) -> str:
    """Keep only config.KEEP_COLS (case-insensitive). Writes trimmed CSV; returns path."""
    df = pd.read_csv(input_path)

    lower_to_actual = {c.lower(): c for c in df.columns}
    missing = [c for c in config.KEEP_COLS if c not in lower_to_actual]
    if missing:
        raise KeyError(f"Missing columns in report: {missing}. Got: {list(df.columns)}")

    actual_cols = [lower_to_actual[c] for c in config.KEEP_COLS]
    trimmed = df[actual_cols]

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    trimmed.to_csv(output_path, index=False)
    return output_path
