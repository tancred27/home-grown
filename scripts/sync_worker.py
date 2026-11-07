"""State-sync worker: poll -> process due repos -> daily refreshes, forever (or once).

    python -I scripts/sync_worker.py <org> [<org> ...] [--once] [--mirror-dir DIR]

Run several of these in parallel: the per-repo lease keeps them off each other's repos.
--mirror-dir: read repos from local bare mirrors (<DIR>/<org>/<name>.git) instead of
the GitHub API, which is the path for very large orgs (no API rate limit on reads).
"""

from __future__ import annotations

import socket
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from langgraph.checkpoint.postgres import PostgresSaver

from homegrown.collector import adapters as A
from homegrown.collector.poller import poll_org
from homegrown.collector.source import GitHubSource, LocalGitSource
from homegrown.collector.state import claim_daily, complete, release_lease
from homegrown.collector.sync import Syncer
from homegrown.config import settings


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    once = "--once" in sys.argv
    mirror = (Path(sys.argv[sys.argv.index("--mirror-dir") + 1])
              if "--mirror-dir" in sys.argv else None)
    orgs = [a for a in args if mirror is None or a != str(mirror)]
    owner = f"{socket.gethostname()}-{uuid.uuid4().hex[:6]}"
    if mirror:
        roots = {f"{p.parent.name}/{p.name.removesuffix('.git')}": p
                 for p in mirror.glob("*/*.git")}
        source = LocalGitSource(roots)
    else:
        source = GitHubSource()
    deps = {"contributors": A.github_contributors, "releases": A.github_releases}
    since: dict[str, datetime | None] = {o: None for o in orgs}
    last_poll = 0.0
    with PostgresSaver.from_conn_string(settings.admin_dsn) as saver:
        saver.setup()
        normal = Syncer(source, checkpointer=saver, **deps)
        daily = Syncer(source, checkpointer=saver, daily=True, **deps)
        while True:
            if time.time() - last_poll >= settings.sync_poll_s:
                for o in orgs:
                    r = poll_org(o, since[o])
                    since[o] = r["until"]
                    print(f"poll {o}: {r['changed']} changed, {r['calls']} calls", flush=True)
                last_poll = time.time()
            n = 0
            while (r := normal.run_once(owner)) is not None:
                n += 1
                print(f"  {r['repo']}: {r['status']} "
                      f"({r.get('changed_units', 0)} units changed)", flush=True)
            while (job := claim_daily(owner)) is not None:
                try:
                    r = daily.sync(job["repo"], job["last_processed_sha"])
                    complete(job["repo"], owner, r["head"], job["started_at"],
                             head_ci=r.get("ci"))
                except Exception as e:
                    print(f"  daily {job['repo']} failed: {e}", flush=True)
                    release_lease(job["repo"], owner)
            if once:
                return 0
            time.sleep(max(5, min(60, settings.sync_quiet_s // 2)) if n == 0 else 0)


if __name__ == "__main__":
    raise SystemExit(main())
