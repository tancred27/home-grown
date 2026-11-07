"""HTTP entry points: webhooks in, answers out.

Webhook handlers only verify and record; the agents never run inside a request.
  * GitHub pushes to a repo's default branch mark the repo as changed (repo_state).
    The sync worker (scripts/sync_worker.py) reads the repo's current state later, so
    delivery order and redeliveries don't matter.
  * Confluence / Notion page events go into the `event` queue, which
    scripts/collector_worker.py drains.

  POST /hooks/github             X-Hub-Signature-256 verified against the shared secret
  POST /hooks/confluence         X-Homegrown-Token checked
  POST /hooks/notion             X-Homegrown-Token checked
  GET  /ask?q=...                the answering agent
  GET  /healthz

Run: uvicorn homegrown.api:app --port 8080

Identity on /ask comes from the X-Homegrown-User header. That is a local stand-in: a
real deployment would validate a sign-in token here and map it to an app_user.
"""

from __future__ import annotations

import hmac
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request

from . import telemetry
from .answer import ask
from .cli import resolve_user
from .collector import adapters as A
from .collector.events import enqueue
from .config import settings


@asynccontextmanager
async def _lifespan(app: FastAPI):
    telemetry.init("api")
    yield


# No HTTP-server spans on purpose: the URL of /ask carries the question.
app = FastAPI(title="org-memory", lifespan=_lifespan)


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
    # State-based sync: a push only marks the repo dirty, and only when it hits the
    # tracked (default) branch. The worker reads the current state itself, so delivery
    # order and redeliveries don't matter.
    payload = json.loads(body)
    repo = (payload.get("repository") or {}).get("full_name")
    branch = (payload.get("repository") or {}).get("default_branch")
    if not repo:
        return {"ok": True, "ignored": "no repository"}
    if branch and payload.get("ref") not in (None, f"refs/heads/{branch}"):
        return {"ok": True, "ignored": f"not the default branch ({payload.get('ref')})"}
    from .collector.state import mark_dirty
    mark_dirty(repo, default_branch=branch)
    return {"ok": True, "dirty": repo}


def _check_token(token: str | None) -> None:
    if not settings.docs_webhook_secret or not token or not hmac.compare_digest(
            token, settings.docs_webhook_secret):
        raise HTTPException(status_code=401, detail="bad token")


@app.post("/hooks/confluence")
async def confluence_hook(request: Request,
                          x_homegrown_token: str | None = Header(default=None)) -> dict:
    _check_token(x_homegrown_token)
    ev = A.from_confluence_webhook(await request.json())
    return {"ok": True, "queued": enqueue(ev)}


@app.post("/hooks/notion")
async def notion_hook(request: Request,
                      x_homegrown_token: str | None = Header(default=None)) -> dict:
    _check_token(x_homegrown_token)
    ev = A.from_notion_webhook(await request.json())
    return {"ok": True, "queued": enqueue(ev)}


@app.get("/ask")
def ask_endpoint(q: str, keywords: str = "",
                 x_homegrown_user: str | None = Header(default=None)) -> dict:
    if not x_homegrown_user:
        raise HTTPException(status_code=401, detail="no user")
    try:
        user = resolve_user(x_homegrown_user)
    except SystemExit:
        raise HTTPException(status_code=403, detail="unknown user") from None
    kws = [k.strip() for k in keywords.split(",") if k.strip()]
    return ask(user, q, kws, route="http")
