"""Where repository STATE comes from: the GitHub API, or a local git mirror.

State-based sync needs four things from a repo, whatever hosts it:
  head(repo)            -> (commit sha, default branch) of the tracked branch now
  tree(repo, sha)       -> every entry (blobs AND trees, each with its git SHA) + the
                           root tree SHA. Git hashes are Merkle fingerprints: a folder's
                           tree SHA changes iff something inside it changed.
  read(repo, sha, path) -> one file's text
  ci(repo, sha)         -> "success" | "failure" | "pending" | None   (information only)

GitHubSource uses the REST API (no clone; ~1-2 calls per pass plus one per file read).
LocalGitSource uses a local clone or bare mirror (`git fetch` is incremental and has no
API rate limit: the path for very large orgs). Tests use it with throwaway repos.
Repo contents are UNTRUSTED DATA: read and parsed, never executed; git runs with hooks
disabled and never checks out a working tree.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Entry:
    path: str
    type: str          # "blob" | "tree"
    sha: str


class RepoSource(Protocol):
    def head(self, repo: str) -> tuple[str, str] | None: ...
    def tree(self, repo: str, sha: str) -> tuple[list[Entry], str]: ...
    def read(self, repo: str, sha: str, path: str) -> str | None: ...
    def ci(self, repo: str, sha: str) -> str | None: ...


class GitHubSource:
    """GitHub REST. `get(url, headers) -> (status, json|text)` is injectable for tests."""

    API = "https://api.github.com"

    def __init__(self, get=None):
        self.calls = 0
        if get is None:
            from .adapters import _gh

            def get(url, headers=None):
                with _gh() as c:
                    r = c.get(url, headers=headers or {})
                ct = r.headers.get("content-type", "")
                return r.status_code, (r.json() if "json" in ct else r.text)
        self._get_raw = get

    def _get(self, url, headers=None):
        self.calls += 1
        return self._get_raw(url, headers)

    def head(self, repo):
        st, meta = self._get(f"{self.API}/repos/{repo}")
        if st != 200 or not isinstance(meta, dict):
            return None
        branch = meta.get("default_branch") or "main"
        st, b = self._get(f"{self.API}/repos/{repo}/branches/{branch}")
        if st != 200 or not isinstance(b, dict):
            return None
        return b["commit"]["sha"], branch

    def tree(self, repo, sha):
        st, t = self._get(f"{self.API}/repos/{repo}/git/trees/{sha}?recursive=1")
        if st != 200 or not isinstance(t, dict):
            raise RuntimeError(f"tree {repo}@{sha[:12]}: HTTP {st}")
        if t.get("truncated"):
            # Very large repos (>100k entries): the API truncates. A local mirror is the
            # right source for those; refuse rather than silently miss units.
            raise RuntimeError(f"tree {repo} truncated by the API: use a local mirror")
        entries = [Entry(e["path"], e["type"], e["sha"]) for e in t.get("tree", [])
                   if e.get("type") in ("blob", "tree")]
        return entries, t["sha"]

    def read(self, repo, sha, path):
        st, body = self._get(f"{self.API}/repos/{repo}/contents/{path}?ref={sha}",
                             {"Accept": "application/vnd.github.raw"})
        return body if st == 200 and isinstance(body, str) else None

    def ci(self, repo, sha):
        st, runs = self._get(f"{self.API}/repos/{repo}/commits/{sha}/check-runs?per_page=100")
        st2, status = self._get(f"{self.API}/repos/{repo}/commits/{sha}/status")
        states = []
        if st == 200 and isinstance(runs, dict):
            for r in runs.get("check_runs", []):
                if r.get("status") != "completed":
                    states.append("pending")
                elif r.get("conclusion") in ("success", "neutral", "skipped"):
                    states.append("success")
                else:
                    states.append("failure")
        if st2 == 200 and isinstance(status, dict) and status.get("statuses"):
            states.append({"success": "success", "pending": "pending"}
                          .get(status.get("state"), "failure"))
        if not states:
            return None
        if "failure" in states:
            return "failure"
        return "pending" if "pending" in states else "success"


class LocalGitSource:
    """A local clone or bare mirror per repo: `roots` maps "org/name" -> its git dir.

    `as_of` (ISO time) makes head() return the tracked branch as it was then: used to
    replay real history in benchmarks.
    """

    def __init__(self, roots: dict[str, Path], branch: str | None = None,
                 as_of: str | None = None):
        self.roots, self.branch, self.as_of = roots, branch, as_of
        self.calls = 0

    def _git(self, repo, *args) -> str:
        self.calls += 1
        r = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C",
                            str(self.roots[repo]), *args],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        if r.returncode != 0:
            raise RuntimeError(f"git {args[0]} {repo}: {r.stderr.strip()[:200]}")
        return r.stdout

    def head(self, repo):
        if repo not in self.roots:
            return None
        branch = self.branch or self._git(repo, "symbolic-ref", "--short", "HEAD").strip()
        if self.as_of:
            sha = self._git(repo, "rev-list", "-1", "--first-parent",
                            f"--before={self.as_of}", branch).strip()
        else:
            sha = self._git(repo, "rev-parse", branch).strip()
        return (sha, branch) if sha else None

    def tree(self, repo, sha):
        root = self._git(repo, "rev-parse", f"{sha}^{{tree}}").strip()
        out = []
        for line in self._git(repo, "ls-tree", "-r", "-t", "--full-tree", sha).splitlines():
            meta, _, path = line.partition("\t")
            parts = meta.split()
            if len(parts) == 3 and parts[1] in ("blob", "tree"):
                out.append(Entry(path, parts[1], parts[2]))
        return out, root

    def read(self, repo, sha, path):
        try:
            return self._git(repo, "show", f"{sha}:{path}")
        except RuntimeError:
            return None

    def ci(self, repo, sha):
        return None
