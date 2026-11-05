"""Drive the collection agent over real data: backfill, then replay recent activity.

  1. Backfill: one `reconcile` event per repo -- every unit gets contributors, team,
     LOB, manager and stage. (Design: backfill is a driver of the same pipeline.)
  2. Replay: each repo's most recent real commits from the GitHub API, and the markdown
     pages under docs/, as the events a webhook would have delivered.

Events go through the real queue (`event` table, idempotent) and the real graph, with a
Postgres checkpointer. A simulated owner answers the owner-gate questions: new assets
become discoverable org-wide (so the demo can find them), and every proposed
deprecation is REJECTED -- a demo must never deprecate anything.

GitHub responses are cached under .cache/github/ so a rerun costs no API calls.

Usage: python -I scripts/collect_replay.py <repos-dir> [<repos-dir> ...]
                                           [--commits 3] [--pages 3] [--no-replay]
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path

from langgraph.checkpoint.postgres import PostgresSaver

from orgmem.collector import Deps, Event, build_collector, enqueue, run_event
from orgmem.collector import adapters as A
from orgmem.collector.events import claim_next, finish, release
from orgmem.config import settings
from orgmem.corpus.github import fetch_org_repos

ROOT = Path(__file__).parent.parent
CACHE = ROOT / ".cache" / "github"


def cached(name: str, fn, *args):
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{name}-{hashlib.sha1(repr(args).encode()).hexdigest()[:16]}.json"
    if f.is_file():
        return json.loads(f.read_text(encoding="utf-8"))
    out = fn(*args)
    data = [e.model_dump(mode="json") if hasattr(e, "model_dump") else e for e in out]
    f.write_text(json.dumps(data), encoding="utf-8")
    return data


def owner_policy(questions):
    return {f"{q['unit_id']}:{q['ask']}":
            ("org-wide" if q["ask"] == "visibility" else "reject") for q in questions}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="+")
    ap.add_argument("--commits", type=int, default=3)
    ap.add_argument("--pages", type=int, default=3)
    ap.add_argument("--no-replay", action="store_true")
    args = ap.parse_args()
    roots = [Path(r) for r in args.roots]
    org = settings.github_org
    meta = fetch_org_repos(org)
    repos = sorted({p.name for r in roots for p in r.iterdir() if p.is_dir()})

    deps = Deps(
        repos_roots=roots,
        repo_meta=lambda full: meta.get(full.split("/")[-1], {}),
        releases=lambda full: cached("releases", A.github_releases, full),
        contributors=lambda full, path: cached("contrib", A.github_contributors, full,
                                               path),
        org=org)

    queued = dup = 0
    for repo in repos:
        full = f"{org}/{repo}"
        evs = [Event(source="github", kind="reconcile", object_ref=full,
                     version="backfill-1", title="backfill")]
        if not args.no_replay:
            evs += [Event.model_validate(e) for e in
                    cached("commits", A.github_recent_commits, full, args.commits)]
            root = next((r / repo for r in roots if (r / repo).is_dir()), None)
            if root is not None:
                evs += A.docs_markdown_pages(root, full, limit=args.pages)
        for ev in evs:
            if enqueue(ev):
                queued += 1
            else:
                dup += 1
    print(f"queued {queued} events ({dup} duplicates skipped by idempotency key)")

    stats = collections.Counter()
    ignored = collections.Counter()
    with PostgresSaver.from_conn_string(settings.admin_dsn) as saver:
        saver.setup()
        graph = build_collector(deps, checkpointer=saver)
        while (job := claim_next()) is not None:
            eid, ev = job
            try:
                out = run_event(graph, ev, eid, owner_policy=owner_policy)
            except Exception as e:      # back on the queue, up to 3 attempts
                release(eid, f"{type(e).__name__}: {e}")
                stats["error"] += 1
                print(f"  ! {ev.kind} {ev.object_ref}: {type(e).__name__}: {str(e)[:120]}")
                continue
            st = out["status"]
            stats[f"{ev.kind}:{st}"] += 1
            if st == "ignored":
                ignored[out.get("reason", "")[:60]] += 1
                finish(eid, "ignored", {"reason": out.get("reason")})
            else:
                for r in (out.get("result") or {}).values():
                    stats[f"asset_{r['action']}"] += 1
                finish(eid, "applied", out.get("result"))

    print("\nevents by kind and outcome:")
    for k, v in sorted(stats.items()):
        print(f"  {k:28s} {v}")
    print("\nwhy events were ignored:")
    for k, v in ignored.most_common(10):
        print(f"  {v:4d}  {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
