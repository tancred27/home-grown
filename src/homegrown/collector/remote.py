"""Sparse remote checkouts: process a repo without cloning it.

At thousands of repos (let alone millions), keeping a full local mirror of every repo is
the write path's real bottleneck: disk, sync time, and credentials for all of them. The
collection agent reads very little of a repo: the file tree, the manifests, READMEs, and
the test files of the units a change touched. So it fetches exactly that.

`sparse_checkout()` builds a directory that LOOKS like the repo to every existing
extractor:
  * manifests, README files and (capped) test files are fetched with their content;
  * every other path in the tree becomes an empty placeholder file, so directory and
    file names (which code signals and unit detection use) are all present.

The result is cached per tree SHA, so re-processing the same commit costs nothing.
Contents are UNTRUSTED DATA: written as files, parsed, never executed.

API cost per new commit: 1 tree call + one call per fetched file (typically 5-30).
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Callable

from ..extract.tests_extractor import is_test_file
from ..extract.units import MANIFESTS

CACHE = Path(__file__).resolve().parents[3] / ".cache" / "sparse"
_README = re.compile(r"(^|/)readme(\.[a-z]+)?$", re.I)
MAX_PATHS, MAX_TESTS = 20000, 300


def wanted(path: str, test_prefixes: list[str] | None) -> bool:
    """Which files are fetched with content (everything else is a placeholder)."""
    name = PurePosixPath(path).name
    if name in MANIFESTS or _README.search(path):
        return True
    if is_test_file(path):
        return test_prefixes is None or any(path.startswith(p) for p in test_prefixes)
    return False


def sparse_checkout(full: str, ref: str = "HEAD",
                    get: Callable[[str, dict | None], tuple[int, object]] | None = None,
                    test_prefixes: list[str] | None = None,
                    cache: Path = CACHE) -> Path | None:
    """-> a local directory mirroring `full` at `ref`, or None if the repo is unreadable.

    get(url, headers) -> (status, json-or-text). Default: the authenticated GitHub client.
    test_prefixes: only fetch test files under these unit paths (None = all, capped).
    """
    if get is None:
        from .adapters import _gh

        def get(url, headers=None):
            with _gh() as c:
                r = c.get(url, headers=headers or {})
            return r.status_code, (r.json() if "json" in r.headers.get("content-type", "")
                                   else r.text)

    status, tree = get(f"https://api.github.com/repos/{full}/git/trees/{ref}?recursive=1",
                       None)
    if status != 200 or not isinstance(tree, dict):
        return None
    sha = tree.get("sha", ref)
    root = cache / f"{full.replace('/', '__')}@{sha[:12]}"
    done = root / ".complete"
    if done.exists():
        return root / "repo"
    paths = [t["path"] for t in tree.get("tree", []) if t.get("type") == "blob"][:MAX_PATHS]
    repo_dir = root / "repo"
    tests = 0
    for p in paths:
        if ".." in PurePosixPath(p).parts or p.startswith("/"):
            continue                                  # never write outside the checkout
        dest = repo_dir / p
        dest.parent.mkdir(parents=True, exist_ok=True)
        content = ""
        if wanted(p, test_prefixes) and (not is_test_file(p) or tests < MAX_TESTS):
            tests += is_test_file(p)
            st, body = get(f"https://api.github.com/repos/{full}/contents/{p}?ref={sha}",
                           {"Accept": "application/vnd.github.raw"})
            if st == 200 and isinstance(body, str):
                content = body[:500_000]
        dest.write_text(content, encoding="utf-8")
    done.write_text(sha, encoding="utf-8")
    return repo_dir
