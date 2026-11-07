"""Build the corpus: repos + discussions -> assets, claims, cards, embeddings.

Usage:
  python -I scripts/ingest.py <repos-dir> [comments-dir ...]

<repos-dir> holds shallow clones, one directory per repo. Comment dirs hold the JSON
from experiments/density/fetch.sh. Both are UNTRUSTED DATA: read, parsed, never
executed, and this script is kept outside those directories deliberately.

Teams are assigned synthetically, one per repo, which is how a siloed org is
simulated on public data. Each user gets membership in their own team only -- so
cross-team discovery is genuinely blocked by row-level security, not by convention.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from homegrown.config import settings
from homegrown.corpus.github import fetch_org_repos
from homegrown.db import admin_conn
from homegrown.embed import asset_text, embed_texts
from homegrown.extract import discussions, units
from homegrown.extract import tests_extractor as tx
from homegrown.store import (
    ensure_team,
    ensure_user,
    grant_membership,
    grant_source_access,
    rebuild_card,
    upsert_asset,
    write_claims,
    write_embedding,
)


def load_comments(dirs: list[Path]) -> dict[str, list[dict]]:
    """{repo: [comment, ...]} from fetch.sh output. Filenames are <repo>_cN.json."""
    by_repo: dict[str, list[dict]] = {}
    for d in dirs:
        for f in sorted(d.glob("*.json")):
            repo = f.stem.split("_c")[0]
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, list):
                by_repo.setdefault(repo, []).extend(data)
    return by_repo


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    repos_dir = Path(sys.argv[1])
    comment_dirs = [Path(p) for p in sys.argv[2:]]
    comments = load_comments(comment_dirs)
    print(f"comment corpus: {sum(len(v) for v in comments.values())} raw across "
          f"{len(comments)} repos")

    # The source system's own one-line description is the best layer-1 summary we can
    # get; gate 1 showed README scraping producing titles and notices instead.
    try:
        meta = fetch_org_repos()
        print(f"repo metadata: {len(meta)} repos "
              f"({sum(1 for v in meta.values() if v.get('description'))} with a description)")
    except Exception as e:
        print(f"repo metadata unavailable ({type(e).__name__}); falling back to READMEs")
        meta = {}

    stats = {"assets": 0, "l1": 0, "l2": 0, "l3": 0}

    with admin_conn() as conn:
        for repo_path in sorted(p for p in repos_dir.iterdir() if p.is_dir()):
            repo = repo_path.name
            # One synthetic team per repo = one access silo.
            team = ensure_team(conn, f"team-{repo}")
            dev = ensure_user(conn, f"dev-{repo}", team)
            grant_membership(conn, dev, team)
            grant_source_access(conn, dev, f"github:{repo}")

            detected = units.detect(
                repo_path, repo,
                repo_description=(meta.get(repo) or {}).get("description"))
            test_claims = tx.extract_repo(repo_path, commit="HEAD")
            repo_comment_claims = discussions.extract_comments(
                comments.get(repo, []), min_chars=settings.min_comment_chars)

            print(f"\n{repo}: {len(detected)} units, "
                  f"{sum(len(v) for v in test_claims.values())} test claims, "
                  f"{len(repo_comment_claims)} discussion claims")

            # Attach test claims to the nearest enclosing unit, and discussion claims
            # to the repo-level unit (a comment is about the repo, not a subdirectory).
            unit_paths = sorted((u.path for u, _ in detected), key=len, reverse=True)

            def nearest(test_dir: str) -> str:
                for up in unit_paths:
                    if up == "." or test_dir == up or test_dir.startswith(up + "/"):
                        if up != "." or not any(
                            test_dir == o or test_dir.startswith(o + "/")
                            for o in unit_paths if o != "."
                        ):
                            return up
                return "."

            buckets: dict[str, list] = {}
            for tdir, cl in test_claims.items():
                buckets.setdefault(nearest(tdir), []).extend(cl)

            for unit, l1 in detected:
                asset_id = upsert_asset(conn, unit, owner_team=team, discovery_scope=team)
                claims = list(l1)
                claims.extend(buckets.get(unit.path, []))
                if unit.path == ".":
                    claims.extend(repo_comment_claims)
                n = write_claims(conn, asset_id, claims)
                rebuild_card(conn, asset_id)

                stats["assets"] += 1
                stats["l1"] += sum(1 for c in claims if int(c.layer) == 1)
                stats["l2"] += sum(1 for c in claims if int(c.layer) == 2)
                stats["l3"] += sum(1 for c in claims if int(c.layer) == 3)
                if n:
                    print(f"    {unit.path:48s} +{n:3d} claims  ({unit.name[:34]})")

        # Embed every asset from its name, summary and claim bodies.
        print("\nembedding...")
        with conn.cursor() as cur:
            cur.execute("""
                select a.id, a.name, a.summary, a.aliases,
                       coalesce(array_agg(c.body order by c.layer, c.id)
                                filter (where c.id is not null), '{}')
                  from asset a
                  left join claim c on c.asset_id = a.id and c.valid_to is null
                 group by a.id, a.name, a.summary, a.aliases
            """)
            rows = cur.fetchall()
        texts = [asset_text(r[1], r[2], list(r[3] or []), list(r[4] or [])) for r in rows]
        vecs = embed_texts(texts)
        print(f"  {len(rows)} assets -> {vecs.shape} with {settings.embed_model}")
        for (asset_id, *_), v in zip(rows, vecs, strict=True):
            write_embedding(conn, asset_id, "card", settings.embed_model, v)

    print(f"\n{stats['assets']} assets | layer1 {stats['l1']} | layer2 {stats['l2']} "
          f"| layer3 {stats['l3']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
