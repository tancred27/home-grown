"""One state-based sync pass for one repo.

    head of the tracked (default) branch
      -> unchanged since last pass?            stop (cost: 1 source call)
      -> full tree with Git SHAs (1 call)
      -> per unit: folder tree SHA unchanged?  skip the unit entirely
      -> per changed unit: which files the collector READS changed (manifest, README,
         tests)? none -> just record the new fingerprint (source-only edits change no
         memory); some -> materialise only those units' files, run the collection graph
         on them, then record the new fingerprints
      -> CI status of the head, kept on the assets as information

Merges, squash merges, rebases, force-pushes and reverts need no special case: two
STATES are compared, never a commit history. Nothing is deleted when a unit disappears
(the agent never deletes); its last state simply stops changing.
"""

from __future__ import annotations

import json
import re
import shutil
from collections import defaultdict
from pathlib import Path, PurePosixPath

from ..db import admin_conn
from ..extract.filters import is_noise_path
from ..extract.tests_extractor import is_test_file
from ..extract.units import MANIFESTS, SKIP_DIRS
from .events import ChangedFile, Event
from .graph import Deps, build_collector, run_event
from .source import RepoSource
from .state import claim_due, complete, fail

CACHE = Path(__file__).resolve().parents[3] / ".cache"
_README = re.compile(r"(^|/)readme(\.[a-z0-9]+)?$", re.I)
MAX_TESTS_PER_UNIT = 300


def _skipped(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return bool(set(parts[:-1]) & SKIP_DIRS) or is_noise_path(path)


def read_kind(path: str) -> str | None:
    """Files the collector reads: manifest | readme | test. Everything else is ignored."""
    if _skipped(path):
        return None
    name = PurePosixPath(path).name
    if name in MANIFESTS:
        return "manifest"
    if _README.search(path):
        return "readme"
    if is_test_file(path):
        return "test"
    return None


def unit_dirs(blobs: dict[str, str]) -> list[str]:
    """Folders holding a manifest, deepest first; '.' (the repo) always last."""
    dirs = {str(PurePosixPath(p).parent).replace("\\", "/") for p in blobs
            if PurePosixPath(p).name in MANIFESTS and not _skipped(p)}
    dirs = {"." if d in ("", ".") else d for d in dirs} | {"."}
    return sorted(dirs - {"."}, key=lambda d: (-d.count("/"), d)) + ["."]


def owner_unit(path: str, units: list[str]) -> str:
    for u in units:
        if u == "." or path == u or path.startswith(u + "/"):
            return u
    return "."


class Syncer:
    """Holds one compiled collection graph; `sync()` runs one pass for one repo."""

    def __init__(self, source: RepoSource, checkpointer=None, daily: bool = False,
                 **deps_kwargs):
        """daily=True: the once-a-day refresh. Every unit is revisited and contributors
        and stage are refreshed (they cost API calls, so normal passes skip them)."""
        self.source = source
        self.daily = daily
        self._dir: dict[str, Path | None] = {"dir": None}
        deps = Deps(repos_roots=[], remote=lambda full, ref: self._dir["dir"],
                    daily_fields=daily, **deps_kwargs)
        self.graph = build_collector(deps, checkpointer=checkpointer)

    # --------------------------------------------------------------- one pass
    def sync(self, repo: str, last_sha: str | None, force: bool | None = None) -> dict:
        force = self.daily if force is None else force
        h = self.source.head(repo)
        if h is None:
            raise RuntimeError(f"cannot read head of {repo}")
        sha, branch = h
        stats = {"repo": repo, "head": sha, "branch": branch, "status": "unchanged",
                 "units": 0, "changed_units": 0, "files_changed": 0, "graph": None}
        if sha == last_sha and not force:
            return stats
        entries, root_tree = self.source.tree(repo, sha)
        trees = {e.path: e.sha for e in entries if e.type == "tree"}
        trees["."] = root_tree
        blobs = {e.path: e.sha for e in entries if e.type == "blob"}
        units = unit_dirs(blobs)
        name = repo.split("/", 1)[-1]
        uid = {u: f"github:{name}:{u}" for u in units}

        files = defaultdict(dict)                    # unit -> {path: blob sha}
        for p, s in blobs.items():
            if read_kind(p):
                files[owner_unit(p, units)][p] = s

        with admin_conn() as conn:
            fps = {u: (t, f) for u, t, f in conn.execute(
                "select unit_id, tree_sha, files from unit_fingerprint where repo = %s",
                (repo,)).fetchall()}

        changed, changes, new_fp = [], [], {}
        for u in units:
            cur_tree = trees.get(u, root_tree if u == "." else None)
            old = fps.get(uid[u])
            if old and old[0] == cur_tree and not force:
                continue                              # folder untouched: skip outright
            new_files = files.get(u, {})
            old_files = (old[1] if old else {}) or {}
            if isinstance(old_files, str):
                old_files = json.loads(old_files)
            diff = ([(p, "added") for p in new_files if p not in old_files]
                    + [(p, "removed") for p in old_files if p not in new_files]
                    + [(p, "modified") for p in new_files
                       if p in old_files and old_files[p] != new_files[p]])
            new_fp[u] = (cur_tree, new_files)
            if old and not diff and not force:
                continue                              # only source code changed
            changed.append(u)
            changes += diff or [(p, "modified") for p in new_files]
        stats.update(units=len(units), changed_units=len(changed), files_changed=len(changes))

        if changed:
            work = self._materialise(repo, sha, blobs, changed)
            self._dir["dir"] = work
            try:
                ev = Event(source="github", kind="commit", object_ref=repo, version=sha,
                           title="state sync", body="",
                           files=[ChangedFile(path=p, status=s) for p, s in changes])
                out = run_event(self.graph, ev, f"sync-{repo}-{sha[:12]}")
                stats["graph"] = out.get("status")
            finally:
                self._dir["dir"] = None
                shutil.rmtree(work.parent, ignore_errors=True)

        ci = self.source.ci(repo, sha)
        with admin_conn() as conn, conn.cursor() as cur:
            for u, (t, f) in new_fp.items():
                cur.execute(
                    "insert into unit_fingerprint (unit_id, repo, tree_sha, files) "
                    "values (%s,%s,%s,%s) on conflict (unit_id) do update set "
                    "tree_sha = excluded.tree_sha, files = excluded.files, updated_at = now()",
                    (uid[u], repo, t, json.dumps(f)))
            if ci is not None:
                cur.execute("update asset set ci_status = %s, ci_sha = %s, ci_checked_at = now() "
                            "where unit_id like %s", (ci, sha, f"github:{name}:%"))
        stats.update(status="processed" if changed else "fingerprints-only", ci=ci)
        return stats

    def _materialise(self, repo: str, sha: str, blobs: dict[str, str],
                     changed: list[str]) -> Path:
        """A minimal directory the extractors can walk: every manifest in the repo (unit
        detection), plus READMEs and tests of the CHANGED units only. Blob contents are
        cached by SHA (immutable), so an unchanged file is never fetched twice."""
        # Short folder name: deep repos (kubernetes) exceed Windows' 260-char path limit
        # under a long prefix. Files still too long are skipped and counted, not fatal.
        import hashlib
        work = CACHE / "s" / hashlib.sha1(f"{repo}@{sha}".encode()).hexdigest()[:10] / "r"
        self.skipped_long_paths = 0
        prefixes = ["" if u == "." else u + "/" for u in changed]
        tests = defaultdict(int)
        for p, s in blobs.items():
            kind = read_kind(p)
            if not kind or ".." in PurePosixPath(p).parts or p.startswith("/"):
                continue
            in_changed = any(p.startswith(px) for px in prefixes)
            if kind != "manifest" and not in_changed:
                continue
            if kind == "test":
                u = next(px for px in prefixes if p.startswith(px))
                if tests[u] >= MAX_TESTS_PER_UNIT:
                    continue
                tests[u] += 1
            blob = CACHE / "blobs" / s[:2] / s
            if blob.exists():
                text = blob.read_text(encoding="utf-8", errors="replace")
            else:
                text = self.source.read(repo, sha, p) or ""
                blob.parent.mkdir(parents=True, exist_ok=True)
                blob.write_text(text[:500_000], encoding="utf-8")
            dest = work / p
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(text[:500_000], encoding="utf-8")
            except OSError:
                self.skipped_long_paths += 1
        work.mkdir(parents=True, exist_ok=True)
        return work

    # --------------------------------------------------------------- worker step
    def run_once(self, owner: str, now=None) -> dict | None:
        """Claim one due repo, sync it, record the result. None when nothing is due."""
        job = claim_due(owner, now=now)
        if job is None:
            return None
        try:
            r = self.sync(job["repo"], job["last_processed_sha"])
        except Exception as e:                   # stays dirty; retried with backoff
            fail(job["repo"], owner, f"{type(e).__name__}: {e}", now=now)
            return {"repo": job["repo"], "status": "failed", "error": str(e)[:200]}
        complete(job["repo"], owner, r["head"], job["started_at"], head_ci=r.get("ci"),
                 default_branch=r.get("branch"), now=now)
        return r
