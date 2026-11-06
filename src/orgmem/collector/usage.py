"""Who depends on what -- and what that says about a solution's stage.

Stage inferred from releases is right for released products and wrong for internal
libraries, which are consumed by commit hash, not by release: on real data the
release-based rule called dskit "dev" while four other repositories in the corpus depend
on it (experiments/collector/RESULTS.md). Usage is the better signal.

This pass, run after a batch of events (it is a property across assets, not of one
event):
  1. reads go.mod requires and package.json dependencies and writes `depends_on` edges
     between assets, with the manifest as evidence. INCREMENTAL: pass `repos=` and only
     those repos' manifests are read; their published names are stored in
     `package_name` (db/005_scale.sql), so a dependency on a repo that was not rescanned
     still resolves by lookup. Cost is proportional to what changed, not to the org;
  2. lifts a stage the owner has not confirmed: an asset that two or more OTHER teams
     depend on, or that a prod asset depends on, is in production use.
It only ever lifts (dev/beta/unknown -> prod), never lowers, and never touches a stage
an owner confirmed.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from ..db import admin_conn

_GO_REQ = re.compile(r"^\s*(?:require\s+)?([a-z0-9.\-]+\.[a-z]+/\S+)\s+v\S+", re.I)
SKIP = {"vendor", "node_modules", ".git", "testdata", "dist", "build"}


def _manifests(root: Path):
    for p in root.rglob("*"):
        if p.name in ("go.mod", "package.json") and not (set(p.parts) & SKIP):
            yield p


def scan(roots: list[Path], repos: set[str] | None = None) -> tuple[dict, list[tuple]]:
    """-> (published name -> unit_id, [(src_unit_id, dep_name, manifest_rel_path)]).
    `repos`: only these repo directories (None = every checkout)."""
    published: dict[str, str] = {}
    uses: list[tuple] = []
    for r in roots:
        for repo_dir in sorted(p for p in r.iterdir() if p.is_dir()
                               and (repos is None or p.name in repos)):
            repo = repo_dir.name
            for m in _manifests(repo_dir):
                rel_dir = m.parent.relative_to(repo_dir).as_posix() or "."
                unit = f"github:{repo}:{rel_dir}"
                rel = f"{repo}/{m.relative_to(repo_dir).as_posix()}"
                text = m.read_text(encoding="utf-8", errors="ignore")
                if m.name == "go.mod":
                    first = text.splitlines()[:1]
                    if first and first[0].startswith("module "):
                        published[first[0].split()[1]] = unit
                    for line in text.splitlines():
                        g = _GO_REQ.match(line)
                        if g and "// indirect" not in line:
                            uses.append((unit, g.group(1), rel))
                else:
                    try:
                        j = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    if j.get("name"):
                        published[j["name"]] = unit
                    for sec in ("dependencies", "peerDependencies"):
                        for dep in (j.get(sec) or {}):
                            uses.append((unit, dep, rel))
    return published, uses


def _resolve(published: dict, dep: str) -> str | None:
    if dep in published:
        return published[dep]
    # Go: a require may name a sub-package or a /vN of a published module.
    best = None
    for mod, unit in published.items():
        if dep.startswith(mod + "/") and (best is None or len(mod) > len(best[0])):
            best = (mod, unit)
    return best[1] if best else None


def refresh(roots: list[Path], repos: set[str] | list[str] | None = None) -> dict:
    """Rebuild dependency edges for `repos` (None = all checkouts) and lift stages."""
    repos = set(repos) if repos is not None else None
    scanned = {r.name for root in roots for r in root.iterdir() if r.is_dir()
               and (repos is None or r.name in repos)}
    local_published, uses = scan(roots, repos)
    edges = 0
    lifted: list[tuple[str, str]] = []
    with admin_conn() as conn, conn.cursor() as cur:
        # Keep the stored name index current for the scanned repos, then resolve against
        # ALL stored names (other repos' names come from earlier scans).
        cur.execute("delete from package_name where repo = any(%s)", (list(scanned),))
        for name, unit in local_published.items():
            cur.execute("insert into package_name (name, unit_id, repo) values (%s,%s,%s) "
                        "on conflict (name) do update set unit_id = excluded.unit_id, "
                        "repo = excluded.repo, updated_at = now()",
                        (name, unit, unit.split(":")[1]))
        cur.execute("select name, unit_id from package_name")
        published = dict(cur.fetchall())
        cur.execute("select unit_id, id, owner_team, stage, stage_confirmed from asset")
        assets = {u: (i, t, s, c) for u, i, t, s, c in cur.fetchall()}

        def asset_of(unit_id: str):
            if unit_id in assets:
                return assets[unit_id]
            repo_unit = ":".join(unit_id.split(":")[:2]) + ":."
            return assets.get(repo_unit)

        pairs = set()
        for src_unit, dep, manifest in uses:
            dst_unit = _resolve(published, dep)
            if not dst_unit:
                continue
            src, dst = asset_of(src_unit), asset_of(dst_unit)
            if not src or not dst or src[0] == dst[0]:
                continue
            pairs.add((src[0], dst[0], manifest, dep))

        # Replace only the edges whose SOURCE is in the repos just scanned: refreshing
        # one repo must not wipe what every other repo depends on.
        src_ids = [v[0] for u, v in assets.items()
                   if u.startswith("github:") and u.split(":")[1] in scanned]
        cur.execute("delete from edge where type = 'depends_on' and src = any(%s)",
                    (src_ids,))
        for src_id, dst_id, manifest, dep in sorted(pairs, key=str):
            cur.execute(
                "insert into evidence (source, ref, source_version, content_hash, excerpt, "
                "extractor_version) values ('github_code', %s, 'HEAD', %s, %s, "
                "'collector/usage/0.1') returning id",
                (manifest, hashlib.sha256(f"{manifest}:{dep}".encode()).hexdigest()[:16],
                 f"requires {dep}"))
            ev = cur.fetchone()[0]
            cur.execute("insert into edge (src, dst, type, confidence, evidence_id) "
                        "values (%s, %s, 'depends_on', 0.9, %s)", (src_id, dst_id, ev))
            edges += 1

        # Stage from usage: iterate to a fixed point so prod propagates down the chain.
        by_id = {v[0]: [u, v[1], v[2], v[3]] for u, v in assets.items()}
        # Dependents from ALL current edges, not just this scan's: an incremental refresh
        # must still count the teams whose repos were scanned earlier.
        dependents: dict = {}
        cur.execute("select src, dst from edge where type = 'depends_on' "
                    "and valid_to is null")
        for src_id, dst_id in cur.fetchall():
            if src_id in by_id and dst_id in by_id:
                dependents.setdefault(dst_id, set()).add(src_id)
        changed = True
        while changed:
            changed = False
            for dst_id, srcs in dependents.items():
                unit, team, stage, confirmed = by_id[dst_id]
                if confirmed or stage in ("prod", "deprecated", "retired"):
                    continue
                other_teams = {by_id[s][1] for s in srcs if by_id[s][1] != team}
                prod_users = [by_id[s][0] for s in srcs if by_id[s][2] == "prod"]
                if len(other_teams) >= 2 or prod_users:
                    why = (f"depended on by {len(other_teams)} other teams"
                           if len(other_teams) >= 2 else
                           f"a production asset depends on it ({prod_users[0]})")
                    by_id[dst_id][2] = "prod"
                    lifted.append((unit, why))
                    changed = True
                    cur.execute(
                        "insert into evidence (source, ref, source_version, content_hash, "
                        "excerpt, extractor_version) values ('github_code', %s, 'HEAD', %s, "
                        "%s, 'collector/usage/0.1') returning id",
                        (unit, hashlib.sha256(why.encode()).hexdigest()[:16],
                         f"in use: {why}"))
                    ev = cur.fetchone()[0]
                    cur.execute("update asset set stage = 'prod', stage_confidence = 0.65, "
                                "stage_evidence_id = %s where id = %s", (ev, dst_id))
    return {"published": len(published), "scanned_repos": len(scanned), "edges": edges, "lifted": lifted}
