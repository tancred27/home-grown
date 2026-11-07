"""Grow the corpus with real distractors, to see how retrieval degrades with size.

Each distractor is a real public repository, indexed from its name and description only
(no clone), under unit_id 'meta:<org>/<repo>'. Repos already in the corpus are skipped,
so a distractor never duplicates a gold asset -- but same-organisation neighbours (other
Grafana datasource plugins, other exporters) are kept, because they are the hard case.

Usage:
  python -I scripts/add_distractors.py <org> [<org> ...] [--max N]
  python -I scripts/add_distractors.py --remove        # back to the clean corpus
"""

from __future__ import annotations

import sys

from homegrown.config import settings
from homegrown.corpus.github import fetch_org_repos
from homegrown.db import admin_conn
from homegrown.embed import embed_texts
from homegrown.store import ensure_team, write_embedding


def main() -> int:
    args = sys.argv[1:]
    with admin_conn() as conn, conn.cursor() as cur:
        if "--remove" in args:
            cur.execute("delete from asset where unit_id like 'meta:%%'")
            print(f"removed {cur.rowcount} distractors")
            return 0
        cap = None
        if "--max" in args:
            cap = int(args[args.index("--max") + 1])
            args = [a for i, a in enumerate(args) if a != "--max" and args[i - 1] != "--max"]

        cur.execute("select unit_id from asset")
        existing = {r[0] for r in cur.fetchall()}
        in_corpus = {u.split(":")[1] for u in existing if u.startswith("github:")}
        team = ensure_team(conn, "team-distractors")

        new = []
        for org in args:
            meta = fetch_org_repos(org)
            for repo, m in sorted(meta.items()):
                uid = f"meta:{org}/{repo}"
                if uid in existing or m.get("archived") or not m.get("description"):
                    continue
                if org == settings.github_org and repo in in_corpus:
                    continue          # never duplicate a gold asset
                new.append((uid, org, repo, m["description"].strip()[:400]))
        if cap is not None:
            new = new[:cap]

        rows = []
        for uid, org, repo, desc in new:
            cur.execute(
                "insert into asset (kind, name, aliases, source_ref, unit_id, owner_team, "
                "discovery_scope, summary) values ('library',%s,%s,%s,%s,%s,%s,%s) "
                "returning id",
                (repo, [repo], f"github:{org}/{repo}", uid, team, team, desc))
            rows.append((cur.fetchone()[0], repo, desc))

    if not rows:
        print("nothing new to add")
        return 0
    texts = [f"{repo} . {desc}" for _, repo, desc in rows]
    summ = [f"{repo}: {desc}" for _, repo, desc in rows]
    with admin_conn() as conn:
        for field, tx in (("card", texts), ("summary", summ)):
            vecs = embed_texts(tx)
            for (aid, _, _), v in zip(rows, vecs, strict=True):
                write_embedding(conn, aid, field, settings.embed_model, v)
        with conn.cursor() as cur:
            cur.execute("select count(*) from asset")
            total = cur.fetchone()[0]
    print(f"added {len(rows)} distractors from {', '.join(args)}; corpus now {total} assets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
