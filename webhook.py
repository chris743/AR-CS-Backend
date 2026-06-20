"""Standalone Microsoft Graph mail webhook (no LangGraph).

Replaces the graph-resume in graph_app/webhook_app.py: when operations replies
with the labor sheet, this records the labor into Postgres directly via
labor.record_from_reply(). State lives in tables, not a suspended run.

Run it:  venv/bin/python -m uvicorn standalone.webhook:app --port 8200
Point the Graph subscription's notificationUrl at /webhooks/graph-mail.
"""

import os
import re

import requests
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route

try:  # works whether launched as `standalone.webhook` or as a top-level module
    from . import labor
except ImportError:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from standalone import labor

_GRAPH = "https://graph.microsoft.com/v1.0"
_SUBJECT = "Repacks Needing Labor Charges"
_WEEK_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _subject(message_id: str) -> str | None:
    from agent.shared.microsoft.get_microsoft_token import get_graph_token

    agent_email = os.getenv("AGENT_EMAIL")
    token = get_graph_token()
    r = requests.get(
        f"{_GRAPH}/users/{agent_email}/messages/{message_id}?$select=subject",
        headers={"Authorization": f"Bearer {token}"}, timeout=30,
    )
    return r.json().get("subject") if r.status_code == 200 else None


def _process(notifications: list[dict]) -> None:
    for n in notifications:
        message_id = (n.get("resourceData") or {}).get("id")
        if not message_id:
            continue
        subject = _subject(message_id)
        if not subject or _SUBJECT.lower() not in subject.lower():
            continue
        m = _WEEK_RE.search(subject)
        if not m:
            continue  # can't key the reply to a billing week
        try:
            labor.record_from_reply(m.group(0))
        except Exception:
            pass  # idempotent: a retry/redelivery will re-run


async def graph_mail_webhook(request: Request) -> Response:
    token = request.query_params.get("validationToken")
    if token is not None:
        return PlainTextResponse(token, status_code=200)

    body = await request.json()
    expected = os.getenv("GRAPH_WEBHOOK_CLIENT_STATE")
    accepted = [
        n for n in body.get("value", [])
        if not expected or n.get("clientState") == expected
    ]
    # Graph needs a 202 within seconds; do the work in the background.
    return Response(status_code=202, background=BackgroundTask(_process, accepted))


app = Starlette(routes=[Route("/webhooks/graph-mail", graph_mail_webhook, methods=["POST"])])
