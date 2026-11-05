"""Build (or rebuild) the lightweight map: a purpose line for every unit.

The setup step of the design: once over the whole corpus, then the collection agent keeps
it current on capability-changing commits. Uses the configured LLM provider (Groq by
default here); every line is also cached on disk under experiments/map/ so a rerun costs
nothing and the exact text that was evaluated is committed.

Usage: python -I scripts/build_map.py <repos-dir> [<repos-dir> ...] [--only-missing]
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from orgmem import llm
from orgmem.collector.purpose import readme_excerpt, store, write_line
from orgmem.config import settings
from orgmem.corpus.github import fetch_org_repos
from orgmem.db import admin_conn

ROOT = Path(__file__).parent.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="+")
    ap.add_argument("--only-missing", action="store_true")
    args = ap.parse_args()
    roots = [Path(r) for r in args.roots]
    meta = fetch_org_repos(settings.github_org)
    model = llm.resolve_model()
    cache = ROOT / "experiments" / "map" / f"purpose_{settings.llm_provider}.json"

    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("select id, unit_id, name, coalesce(summary,''), purpose from asset "
                    "where unit_id like 'github:%%' order by unit_id")
        rows = cur.fetchall()
    todo = [r for r in rows if not (args.only_missing and r[4])]
    print(f"model {settings.llm_provider}/{model}: {len(todo)} of {len(rows)} units")

    ok = failed = 0
    t0 = time.time()
    for aid, unit_id, name, summary, _ in todo:
        _, repo, path = unit_id.split(":", 2)
        base = next((r / repo for r in roots if (r / repo).is_dir()), None)
        readme, readme_name = ("", None)
        if base is not None:
            readme, readme_name = readme_excerpt(base if path == "." else base / path)
            if not readme and path != ".":
                readme, readme_name = readme_excerpt(base)
                readme = readme[:600]
        desc = summary or (meta.get(repo) or {}).get("description")
        try:
            line = llm.cached_json(cache, [model, name, desc or "", readme],
                                   lambda: write_line(name, desc, readme))
        except Exception as e:      # keep whatever line the unit already had
            failed += 1
            print(f"  ! {unit_id}: {type(e).__name__}: {str(e)[:100]}")
            continue
        ref = f"{repo}/{path}/{readme_name}" if readme_name else f"{repo}/{path}"
        with admin_conn() as conn:
            store(conn, aid, line, model, ref.replace("/./", "/"), readme)
        ok += 1
        if ok % 10 == 0:
            print(f"  {ok} written ({time.time() - t0:.0f}s)")
    print(f"\nwritten {ok}, failed {failed}, in {time.time() - t0:.0f}s")
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("select purpose_confidence, count(*) from asset where purpose is not "
                    "null group by 1 order by 1")
        print("confidence:", dict(cur.fetchall()))
    (ROOT / "experiments" / "map").mkdir(parents=True, exist_ok=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
