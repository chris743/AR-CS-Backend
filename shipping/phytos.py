"""Fetch phyto certificate packets from SharePoint (Microsoft Graph).

Ports SP_get_phytos.py. msal/httpx are imported lazily so the server boots
without them; only a `run` needs them.
"""

import re
from datetime import datetime
from pathlib import Path

from . import config

_WE_RE = re.compile(config.WE_FOLDER_REGEX)


def _token() -> str:
    import msal

    app = msal.ConfidentialClientApplication(
        client_id=config.SP_CLIENT_ID,
        client_credential=config.SP_CLIENT_SECRET,
        authority=f"https://login.microsoftonline.com/{config.SP_TENANT_ID}",
    )
    result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in result:
        raise RuntimeError(f"Token acquisition failed: {result}")
    return result["access_token"]


def _client():
    import httpx

    return httpx.Client(
        base_url=config.GRAPH_BASE,
        headers={"Authorization": f"Bearer {_token()}"},
        timeout=60,
    )


def _site_id(client) -> str:
    r = client.get(f"/sites/{config.SP_SITE_HOSTNAME}:{config.SP_SITE_PATH}")
    r.raise_for_status()
    return r.json()["id"]


def _drive_id(client, site_id: str) -> str:
    r = client.get(f"/sites/{site_id}/drive")
    r.raise_for_status()
    return r.json()["id"]


def _children(client, drive_id: str, item_path: str) -> list[dict]:
    url = (f"/drives/{drive_id}/root:/{item_path}:/children"
           if item_path else f"/drives/{drive_id}/root/children")
    r = client.get(url)
    r.raise_for_status()
    return r.json()["value"]


def _most_recent_we_folder(items: list[dict]) -> dict:
    dated = []
    for item in items:
        if "folder" not in item:
            continue
        m = _WE_RE.match(item["name"])
        if not m:
            continue
        mm, dd, yyyy = m.groups()
        try:
            dated.append((datetime(int(yyyy), int(mm), int(dd)), item))
        except ValueError:
            continue
    if not dated:
        raise RuntimeError(f"No 'WE mm.dd.yyyy' folders found under {config.SP_CREEKSIDE_DIR}")
    dated.sort(key=lambda x: x[0], reverse=True)
    return dated[0][1]


def list_we_folders() -> list[dict]:
    """Every 'WE mm.dd.yyyy' folder under the Creekside dir, newest first.

    Returns [{"week_folder": "WE 05.17.2026", "date": "2026-05-17"}]. The name
    encodes the date, so callers can group by year/month without extra fetches.
    """
    with _client() as client:
        site_id = _site_id(client)
        drive_id = _drive_id(client, site_id)
        children = _children(client, drive_id, config.SP_CREEKSIDE_DIR)

    dated = []
    for item in children:
        if "folder" not in item:
            continue
        m = _WE_RE.match(item["name"])
        if not m:
            continue
        mm, dd, yyyy = m.groups()
        try:
            d = datetime(int(yyyy), int(mm), int(dd))
        except ValueError:
            continue
        dated.append((d, item["name"]))
    dated.sort(key=lambda x: x[0], reverse=True)
    return [{"week_folder": name, "date": d.date().isoformat()} for d, name in dated]


def _download(client, drive_id: str, item_id: str, dest: Path) -> None:
    r = client.get(f"/drives/{drive_id}/items/{item_id}/content", follow_redirects=True)
    r.raise_for_status()
    dest.write_bytes(r.content)


def fetch_phyto_reports(week_folder: str | None = None) -> tuple[list[str], str]:
    """Stage every file whose name contains 'phyto' from a WE folder.

    week_folder=None -> the most recent 'WE mm.dd.yyyy' folder.
    Returns (staged_paths, resolved_week_folder).
    """
    stage = Path(config.PHYTO_STAGE_DIR)
    stage.mkdir(parents=True, exist_ok=True)

    with _client() as client:
        site_id = _site_id(client)
        drive_id = _drive_id(client, site_id)

        if week_folder is None:
            children = _children(client, drive_id, config.SP_CREEKSIDE_DIR)
            week_folder = _most_recent_we_folder(children)["name"]

        week_children = _children(client, drive_id, f"{config.SP_CREEKSIDE_DIR}/{week_folder}")
        staged = []
        for item in week_children:
            if "file" not in item or "phyto" not in item["name"].lower():
                continue
            dest = stage / item["name"]
            _download(client, drive_id, item["id"], dest)
            staged.append(str(dest))
        return staged, week_folder
