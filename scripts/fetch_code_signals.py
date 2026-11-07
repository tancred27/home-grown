"""Fetch code-derived signals (file tree + root manifest) for many public repos via the
GitHub API, for scale experiments without cloning thousands of repos.

Cached under .cache/codesig/<org>__<repo>.json as {"paths": [...], "manifest": [name, text]}.
Manifests are UNTRUSTED DATA: stored as text and parsed, never executed.

Usage: python -I scripts/fetch_code_signals.py --from-key <key.json>
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from homegrown.collector.adapters import _gh
from homegrown.collector.signals import MANIFESTS

CACHE = Path(__file__).parent.parent / ".cache" / "codesig"


def path_for(full: str) -> Path:
    return CACHE / (full.replace("/", "__") + ".json")


def fetch(full: str) -> str:
    f = path_for(full)
    if f.exists():
        return "cached"
    with _gh() as c:
        r = c.get(f"https://api.github.com/repos/{full}/git/trees/HEAD",
                  params={"recursive": "1"})
        if r.status_code in (404, 409):           # empty repo
            f.write_text(json.dumps({"paths": [], "manifest": None}), encoding="utf-8")
            return "empty"
        if r.status_code != 200:
            return f"error {r.status_code}"
        paths = [t["path"] for t in r.json().get("tree", []) if t.get("type") == "blob"]
        manifest = None
        for m in MANIFESTS:
            if m in paths:
                mr = c.get(f"https://api.github.com/repos/{full}/contents/{m}",
                           headers={"Accept": "application/vnd.github.raw"})
                if mr.status_code == 200:
                    manifest = [m, mr.text[:20000]]
                break
    f.write_text(json.dumps({"paths": paths[:20000], "manifest": manifest}), encoding="utf-8")
    return "fetched"


def main() -> int:
    key = json.loads(Path(sys.argv[sys.argv.index("--from-key") + 1]).read_text("utf-8"))
    repos = sorted({v["key"].removeprefix("meta:") for v in key.values()
                    if v["key"].startswith("meta:")})
    CACHE.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=8) as ex:
        res = list(ex.map(fetch, repos))
    print(f"{len(repos)} repos: {dict(Counter(r.split()[0] for r in res))}")
    errs = [(r, s) for r, s in zip(repos, res) if s.startswith("error")]
    for r, s in errs[:10]:
        print("  ", r, s)
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
