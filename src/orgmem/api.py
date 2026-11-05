"""HTTP entry points: webhooks in, answers out.

Webhook handlers ONLY verify and enqueue. GitHub times a delivery out after 10 seconds
and does not redeliver failures, so the agent never runs inside the request: the queue
(table `event`) absorbs the event and a worker (scripts/collector_worker.py) processes
it. The idempotency key makes redeliveries and the nightly reconcile harmless.

  POST /hooks/github             X-Hub-Signature-256 verified against the shared secret
  POST /hooks/confluence         X-Orgmem-Token checked
  POST /hooks/notion             X-Orgmem-Token checked
  GET  /ask?q=...                the answering agent
  GET  /healthz

Run: uvicorn orgmem.api:app --port 8080

Identity on /ask is X-Orgmem-User, a LOCAL STAND-IN: a real deployment validates an
OAuth 2.1 token here and maps it to app_user (docs/design.md section 8).
"""

from __future__ import annotations

import hmac
import json

from fastapi import FastAPI, Header, HTTPException, Request

from .answer import ask
from .cli import resolve_user
from .collector import adapters as A
from .collector.events import enqueue
from .config import settings

app = FastAPI(title="org-memory")


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.post("/hooks/github")
async def github_hook(request: Request,
                      x_github_event: str | None = Header(default=None),
                      x_hub_signature_256: str | None = Header(default=None)) -> dict:
    body = await request.body()
    if not A.verify_github_signature(settings.github_webhook_secret, body,
                                     x_hub_signature_256):
        raise HTTPException(status_code=401, detail="bad signature")
    if x_github_event == "ping":
        return {"ok": True, "pong": True}
    if x_github_event != "push":
        return {"ok": True, "ignored": x_github_event}
    events = A.from_github_push(json.loads(body))
    new = sum(enqueue(e) for e in events)
    return {"ok": True, "received": len(events), "queued": new}


def _check_token(token: str | None) -> None:
    if not settings.docs_webhook_secret or not token or not hmac.compare_digest(
            token, settings.docs_webhook_secret):
        raise HTTPException(status_code=401, detail="bad token")


@app.post("/hooks/confluence")
async def confluence_hook(request: Request,
                          x_orgmem_token: str | None = Header(default=None)) -> dict:
    _check_token(x_orgmem_token)
    ev = A.from_confluence_webhook(await request.json())
    return {"ok": True, "queued": enqueue(ev)}


@app.post("/hooks/notion")
async def notion_hook(request: Request,
                      x_orgmem_token: str | None = Header(default=None)) -> dict:
    _check_token(x_orgmem_token)
    ev = A.from_notion_webhook(await request.json())
    return {"ok": True, "queued": enqueue(ev)}


@app.get("/ask")
def ask_endpoint(q: str, keywords: str = "",
                 x_orgmem_user: str | None = Header(default=None)) -> dict:
    if not x_orgmem_user:
        raise HTTPException(status_code=401, detail="no user")
    try:
        user = resolve_user(x_orgmem_user)
    except SystemExit:
        raise HTTPException(status_code=403, detail="unknown user") from None
    kws = [k.strip() for k in keywords.split(",") if k.strip()]
    return ask(user, q, kws)
