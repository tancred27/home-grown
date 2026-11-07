"""Replay the real history of busy repos through the full state-sync pass.

ONLY against the throwaway database (HOMEGROWN_ADMIN_DSN must point at homegrown_bench).

    python -I scripts/bench_busy_repos.py <mirrors-dir> [--hours 24] [--step 600]

<mirrors-dir>/<org>/<name>.git are bare, partial (blob:none) clones; file contents are
fetched lazily, like API reads. For each repo:
  1. backfill: the first pass at (window start), which processes every unit once
  2. steady state: one pass every `step` seconds through the window, at the real head
     of the default branch at that moment (git rev-list --before)
and reports commits in the window, passes, how many processed anything, units changed,
source reads per pass and time per pass. Compare: the per-commit design ran the full
graph once per commit.
"""

from __future__ import annotations

import json
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from homegrown.collector.source import LocalGitSource
from homegrown.collector.sync import Syncer
from homegrown.config import settings
from homegrown.db import admin_conn

if "homegrown_bench" not in settings.admin_dsn:
    raise SystemExit("refusing to run: HOMEGROWN_ADMIN_DSN must point at homegrown_bench")


def arg(name, default, cast=int):
    return cast(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default


def git(path, *a) -> str:
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(path), *a],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=True).stdout


def main() -> int:
    root = Path(sys.argv[1])
    hours, step = arg("--hours", 24), arg("--step", 600)
    settings.llm_provider = "disabled"          # purpose lines fail safe; measured apart
    repos = {f"{p.parent.name}/{p.name.removesuffix('.git')}": p
             for p in sorted(root.glob("*/*.git"))}
    report = []
    for full, path in repos.items():
        branch = git(path, "symbolic-ref", "--short", "HEAD").strip()
        # The BUSIEST `hours` window of the default branch's first-parent line (each
        # first-parent commit is one change landing on main). Skip the oldest commit,
        # whose parents are cut off by the shallow clone.
        times = sorted(datetime.fromisoformat(x).astimezone(UTC) for x in
                       git(path, "log", "--first-parent", "--format=%cI", branch).split())[1:]
        best, j = (0, times[0]), 0
        for i, t_end in enumerate(times):
            while times[j] < t_end - timedelta(hours=hours):
                j += 1
            if i - j + 1 > best[0] and t_end - timedelta(hours=hours) >= times[0]:
                best = (i - j + 1, t_end)
        if best[0] == 0:                  # less history than the window: use what exists
            start, end = times[0], times[-1]
        else:
            end = best[1]
            start = end - timedelta(hours=hours)
        commits = int(git(path, "rev-list", "--count", "--first-parent",
                          f"--since={start.isoformat()}", f"--until={end.isoformat()}",
                          branch).strip() or 0)
        all_commits = int(git(path, "rev-list", "--count",
                              f"--since={start.isoformat()}", f"--until={end.isoformat()}",
                              branch).strip() or 0)
        with admin_conn() as conn:
            name = full.split("/")[1]
            conn.execute("delete from unit_fingerprint where repo = %s", (full,))
            conn.execute("delete from asset where unit_id like %s", (f"github:{name}:%",))

        src = LocalGitSource({full: path}, as_of=start.isoformat())
        syncer = Syncer(src)
        t0 = time.perf_counter()
        first = syncer.sync(full, None)
        backfill = {"seconds": round(time.perf_counter() - t0, 1), "units": first["units"],
                    "units_processed": first["changed_units"], "reads": src.calls}
        print(f"{full}: {all_commits} commits ({commits} on the first-parent line) in "
              f"{hours}h; backfill {backfill}", flush=True)

        last, passes, times, calls, units_changed, processed = first["head"], 0, [], [], [], 0
        t = start + timedelta(seconds=step)
        while t <= end:
            src.as_of = t.isoformat()
            src.calls = 0
            p0 = time.perf_counter()
            r = syncer.sync(full, last)
            times.append(time.perf_counter() - p0)
            calls.append(src.calls)
            passes += 1
            if r["status"] == "processed":
                processed += 1
                units_changed.append(r["changed_units"])
            last = r["head"]
            t += timedelta(seconds=step)
        q95 = lambda xs: sorted(xs)[int(.95 * (len(xs) - 1))] if xs else 0   # noqa: E731
        row = {"repo": full, "window_h": round((end - start).total_seconds() / 3600, 1), "commits": all_commits,
               "first_parent_commits": commits, "backfill": backfill, "passes": passes,
               "passes_processed": processed,
               "units_changed_per_processed_pass": round(statistics.mean(units_changed), 2)
               if units_changed else 0,
               "pass_seconds_mean": round(statistics.mean(times), 2),
               "pass_seconds_p95": round(q95(times), 2),
               "source_calls_per_pass_mean": round(statistics.mean(calls), 1),
               "worker_seconds_per_day": round(sum(times) * 24 / hours)}
        report.append(row)
        print(json.dumps(row), flush=True)
    out = Path(__file__).parent.parent / "experiments" / "sync" / "busy_repos.json"
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"written {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
