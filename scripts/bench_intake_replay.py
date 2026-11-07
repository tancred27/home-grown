"""Replay one real hour of GitHub push traffic (GH Archive) through intake + scheduler.

ONLY against the throwaway database (HOMEGROWN_ADMIN_DSN must point at homegrown_bench).
All public GitHub is treated as ONE organisation: a far harsher load than any company.

    python -I scripts/bench_intake_replay.py <hour.json.gz> [--batch 500] [--tick 30]

Measures
  intake      dirty-marks per second, one per request (webhook style) on a sample, and
              batched (poller style) for the whole hour
  scheduler   on a SIMULATED clock stepping `tick` seconds through the hour, the real
              claim_due/complete SQL: repo passes vs pushes (coalescing ratio), the
              staleness of every pass (completion time - oldest pending change), and how
              many claims per second the scheduler itself sustains (wall clock)
Processing is modelled as instant here: the scheduler, not the extractor, is under
test. The per-pass cost is measured separately (bench_busy_repos.py).
Only pushes to main/master count (GH Archive has no default-branch field).
"""

from __future__ import annotations

import gzip
import json
import statistics
import sys
import time
from datetime import datetime, timedelta

from homegrown.collector.state import claim_due, complete, mark_dirty, mark_dirty_many
from homegrown.config import settings
from homegrown.db import admin_conn

if "homegrown_bench" not in settings.admin_dsn:
    raise SystemExit("refusing to run: HOMEGROWN_ADMIN_DSN must point at homegrown_bench")


def arg(name, default, cast=int):
    return cast(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default


def main() -> int:
    path = sys.argv[1]
    batch, tick = arg("--batch", 500), arg("--tick", 30)
    pushes = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            e = json.loads(line)
            if e["type"] == "PushEvent" and e["payload"].get("ref") in (
                    "refs/heads/main", "refs/heads/master"):
                pushes.append((datetime.fromisoformat(e["created_at"].replace("Z", "+00:00")),
                               e["repo"]["name"]))
    pushes.sort()
    t_start, t_end = pushes[0][0], pushes[-1][0]
    print(f"{len(pushes):,} pushes to main/master over {t_end - t_start}, "
          f"{len({r for _, r in pushes}):,} repos", flush=True)
    with admin_conn() as conn:
        conn.execute("truncate repo_state")

    # 1) intake, webhook style: one request = one upsert (sample)
    sample = pushes[:2000]
    t0 = time.perf_counter()
    for ts, repo in sample:
        mark_dirty(repo, ts)
    single = len(sample) / (time.perf_counter() - t0)
    with admin_conn() as conn:
        conn.execute("truncate repo_state")
    print(f"intake, one per request: {single:,.0f} dirty-marks/s", flush=True)

    # 2) the whole hour on a simulated clock: intake in batches + scheduler every tick
    passes, stale, claim_time, claims = 0, [], 0.0, 0
    first_pending: dict[str, datetime] = {}
    i, now = 0, t_start
    intake_time = 0.0
    end = t_end + timedelta(seconds=settings.sync_max_wait_s + 2 * tick)
    while now <= end:
        chunk = []
        while i < len(pushes) and pushes[i][0] <= now:
            chunk.append(pushes[i])
            first_pending.setdefault(pushes[i][1], pushes[i][0])
            i += 1
        for k in range(0, len(chunk), batch):
            t0 = time.perf_counter()
            mark_dirty_many([(r, ts) for ts, r in chunk[k:k + batch]])
            intake_time += time.perf_counter() - t0
        while True:
            t0 = time.perf_counter()
            job = claim_due("sim", now=now)
            claim_time += time.perf_counter() - t0
            if job is None:
                break
            claims += 1
            complete(job["repo"], "sim", "x", job["started_at"], now=now)
            passes += 1
            fp = first_pending.pop(job["repo"], None)
            if fp is not None:
                stale.append((now - fp).total_seconds())
        now += timedelta(seconds=tick)
    stale.sort()
    q = lambda p: stale[min(len(stale) - 1, int(p * len(stale)))]   # noqa: E731
    out = {
        "pushes": len(pushes), "repos": len({r for _, r in pushes}),
        "commits_hour_equiv": None, "passes": passes,
        "coalescing_ratio": round(len(pushes) / passes, 2),
        "intake_single_per_s": round(single),
        "intake_batched_per_s": round(len(pushes) / intake_time),
        "scheduler_claims_per_s": round(claims / claim_time),
        "staleness_s": {"p50": q(.5), "p95": q(.95), "p99": q(.99), "max": stale[-1]},
        "settings": {"quiet_s": settings.sync_quiet_s, "max_wait_s": settings.sync_max_wait_s,
                     "tick_s": tick, "batch": batch},
    }
    print(json.dumps(out, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
