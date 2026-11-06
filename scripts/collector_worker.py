"""The collection agent's worker: drain the event queue; park what needs an owner.

  python -I scripts/collector_worker.py run <repos-dir> [...]   process until empty
  python -I scripts/collector_worker.py pending                 list owner questions
  python -I scripts/collector_worker.py answer <event-id> '<answers-json>' <repos-dir>

An event that needs its owner is left in status 'pending_owner'; its graph state stays in
the Postgres checkpointer, so `answer` resumes it later, from another process, exactly
where it paused. Nothing is stored for that event until the owner answers.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from langgraph.checkpoint.postgres import PostgresSaver

from orgmem.collector import Deps, build_collector, run_event
from orgmem.collector import adapters as A
from orgmem.collector.events import claim_next, finish, release
from orgmem.collector.graph import _repo_of, resume_event
from orgmem.config import settings
from orgmem.corpus.github import fetch_org_repos
from orgmem.db import admin_conn


def make_deps(roots: list[Path]) -> Deps:
    meta = fetch_org_repos(settings.github_org)
    from orgmem.collector.remote import sparse_checkout
    # Local clones are optional: any repo without one is read through a sparse remote
    # checkout (tree + manifests + READMEs + tests), so the worker scales past what fits
    # on disk.
    return Deps(repos_roots=roots, repo_meta=lambda full: meta.get(full.split("/")[-1], {}),
                releases=A.github_releases, contributors=A.github_contributors,
                remote=sparse_checkout)


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "pending":
        with admin_conn() as conn, conn.cursor() as cur:
            cur.execute("select id, kind, payload->>'object_ref', outcome from event "
                        "where status = 'pending_owner' order by received_at")
            for eid, kind, ref, outcome in cur.fetchall():
                print(f"{eid}  {kind}  {ref}")
                for q in (outcome or {}).get("questions", []):
                    print(f"    [{q['unit_id']}:{q['ask']}] {q['question']} "
                          f"options={q['options']}")
        return 0

    roots = [Path(p) for p in sys.argv[2 if cmd == "run" else 4:]]
    with PostgresSaver.from_conn_string(settings.admin_dsn) as saver:
        saver.setup()
        graph = build_collector(make_deps(roots), checkpointer=saver)
        if cmd == "answer":
            eid, answers = sys.argv[2], json.loads(sys.argv[3])
            out = resume_event(graph, eid, answers)
            finish(eid, out["status"] if out["status"] != "applied" else "applied",
                   out.get("result") or {"questions": out.get("questions")})
            print(json.dumps(out, indent=1, default=str))
            return 0
        n, touched = 0, set()
        while (job := claim_next()) is not None:
            eid, ev = job
            touched.add(_repo_of(ev)[1])
            try:
                out = run_event(graph, ev, eid, owner_policy=None)
            except Exception as e:
                release(eid, f"{type(e).__name__}: {e}")
                continue
            n += 1
            if out["status"] == "pending_owner":
                finish(eid, "pending_owner", {"questions": out["questions"]})
            elif out["status"] == "ignored":
                finish(eid, "ignored", {"reason": out.get("reason")})
            else:
                finish(eid, "applied", out.get("result"))
        if n:
            from orgmem.collector.usage import refresh
            # Only the repos this batch touched: cost follows change, not org size.
            u = refresh(roots, repos=touched)
            print(f"usage refreshed for {u['scanned_repos']} repo(s): {u['edges']} edges, "
                  f"{len(u['lifted'])} stages lifted")
        print(f"processed {n} events")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
