"""Fetch the README of many public repos via the GitHub API, for scale experiments.

READMEs are UNTRUSTED DATA: saved as text under .cache/readmes/<org>__<repo>.md, never
executed, only read and cleaned (collector.purpose.clean_readme). Cached: a rerun only
fetches what is missing. A repo without a README is recorded as an empty file.

Usage: python -I scripts/fetch_readmes.py <org/repo> [<org/repo> ...]
       python -I scripts/fetch_readmes.py --from-key <key.json>   (all meta:* entries)
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from orgmem.collector.adapters import _gh

CACHE = Path(__file__).parent.parent / ".cache" / "readmes"


def path_for(full: str) -> Path:
    return CACHE / (full.replace("/", "__") + ".md")


def fetch(full: str) -> str:
    f = path_for(full)
    if f.exists():
        return "cached"
    with _gh() as c:
        r = c.get(f"https://api.github.com/repos/{full}/readme",
                  headers={"Accept": "application/vnd.github.raw"})
    if r.status_code == 404:
        f.write_text("", encoding="utf-8")
        return "none"
    if r.status_code != 200:
        return f"error {r.status_code}"
    f.write_text(r.text[:200_000], encoding="utf-8")
    return "fetched"


def main() -> int:
    args = sys.argv[1:]
    if args[:1] == ["--from-key"]:
        key = json.loads(Path(args[1]).read_text("utf-8"))
        repos = sorted({v["key"].removeprefix("meta:") for v in key.values()
                        if v["key"].startswith("meta:")})
    else:
        repos = args
    CACHE.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(fetch, repos))
    from collections import Counter
    print(f"{len(repos)} repos: {dict(Counter(r.split()[0] for r in results))}")
    errors = [(r, s) for r, s in zip(repos, results) if s.startswith("error")]
    for r, s in errors[:10]:
        print("  ", r, s)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
