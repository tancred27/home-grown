"""How many commits per second can the write path absorb?

Run ONLY against a throwaway copy of the database:
    HOMEGROWN_ADMIN_DSN=.../homegrown_bench HOMEGROWN_APP_DSN=.../homegrown_bench \
        python -I scripts/bench_write_path.py <repos-dir> [--events 400] [--mode per-event|coalesce]

Synthetic but realistic commits: real repos and real file paths from local checkouts,
1-3 modified files per commit, human actors (coding-agent commits usually carry a
person's account, so triage can't drop them as bots), and a skewed repo distribution
(a few hot repos take most commits, as in a real org). No network: contributors and
releases are stubbed, and the LLM is disabled (purpose lines fail safe). So this
measures the pipeline itself: queue, graph, extraction and database writes.

Phases:
  enqueue      inserting N webhook events
  per-event    the current worker: one graph run per commit
  coalesce     one graph run per repo per batch (collector.events.claim_repo_batch)
"""

from __future__ import annotations

import random
import sys
import time
import uuid
from pathlib import Path

from langgraph.checkpoint.postgres import PostgresSaver

from homegrown.collector import ChangedFile, Deps, Event, build_collector, run_event
from homegrown.collector.events import claim_next, enqueue, finish, release
from homegrown.config import settings

if "homegrown_bench" not in settings.admin_dsn:
    raise SystemExit("refusing to run: HOMEGROWN_ADMIN_DSN must point at homegrown_bench")


def arg(name, default, cast=int):
    return cast(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default


def make_events(root: Path, n: int, seed: int = 3) -> list[Event]:
    rng = random.Random(seed)
    repos = sorted(p for p in root.iterdir() if p.is_dir())
    files = {}
    for r in repos:
        fs = [p.relative_to(r).as_posix() for p in r.rglob("*")
              if p.is_file() and ".git" not in p.parts and p.suffix in
              (".go", ".ts", ".tsx", ".py", ".rs", ".js", ".java", ".md")]
        if fs:
            files[r.name] = fs
    names = sorted(files)
    weights = [1 / (i + 1) for i in range(len(names))]          # Zipf-like: hot repos
    out = []
    for i in range(n):
        repo = rng.choices(names, weights)[0]
        changed = rng.sample(files[repo], k=min(len(files[repo]), rng.randint(1, 3)))
        out.append(Event(source="github", kind="commit", object_ref=f"grafana/{repo}",
                         version=uuid.uuid4().hex, actor=f"dev-{rng.randint(1, 400)}",
                         title=rng.choice(["Fix flaky test", "Add option", "Refactor client",
                                           "Handle timeout", "Update handler"]),
                         occurred_at="2026-11-07T10:00:00Z",
                         files=[ChangedFile(path=f) for f in changed]))
    return out


def main() -> int:
    root = Path(sys.argv[1])
    n = arg("--events", 400)
    mode = arg("--mode", "per-event", str)
    settings.llm_provider = "disabled"
    events = make_events(root, n)
    repos_touched = len({e.object_ref for e in events})

    t0 = time.perf_counter()
    for e in events:
        enqueue(e)
    t_enq = time.perf_counter() - t0
    print(f"enqueue: {n} events in {t_enq:.1f}s = {n / t_enq:.0f}/s "
          f"({repos_touched} repos touched)", flush=True)

    deps = Deps(repos_roots=[root], repo_meta=lambda full: {},
                releases=lambda full: [],
                contributors=lambda full, path: [{"login": "dev-1", "commits": 5,
                                                  "first_at": "2026-01-01T00:00:00Z",
                                                  "last_at": "2026-11-01T00:00:00Z"}],
                purpose_writer=lambda name, desc, readme: (_ for _ in ()).throw(
                    RuntimeError("llm disabled in benchmark")))
    stats = {"runs": 0, "applied": 0, "ignored": 0, "error": 0}
    with PostgresSaver.from_conn_string(settings.admin_dsn) as saver:
        saver.setup()
        graph = build_collector(deps, checkpointer=saver)
        t0 = time.perf_counter()
        if mode == "per-event":
            while (job := claim_next()) is not None:
                eid, ev = job
                try:
                    out = run_event(graph, ev, eid)
                    stats[out["status"]] = stats.get(out["status"], 0) + 1
                    finish(eid, out["status"], {"n": 1})
                except Exception as e:
                    stats["error"] += 1
                    release(eid, f"{type(e).__name__}: {e}")
                stats["runs"] += 1
        else:
            from homegrown.collector.events import claim_repo_batch, finish_batch
            while (job := claim_repo_batch()) is not None:
                ids, ev = job
                try:
                    out = run_event(graph, ev, ids[0])
                    stats[out["status"]] = stats.get(out["status"], 0) + 1
                    finish_batch(ids, out["status"])
                except Exception as e:
                    stats["error"] += 1
                    for i in ids:
                        release(i, f"{type(e).__name__}: {e}")
                stats["runs"] += 1
        t = time.perf_counter() - t0
    print(f"{mode}: {n} commits in {t:.1f}s = {n / t:.1f} commits/s per worker; "
          f"{stats['runs']} graph runs; {stats}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
