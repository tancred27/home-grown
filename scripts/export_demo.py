"""Freeze the dashboard into a static demo: site/demo/index.html + site/demo/snapshot.json.

The demo is display only. It reads the snapshot and never calls the API or the database,
so it can live on GitHub Pages. The page is src/homegrown/web/dashboard.html, copied with
a <meta name="hg-snapshot"> tag that switches it to demo mode.

Each viewer is a real app_user, and their map is computed exactly as /api/org-map does,
under row-level security. Two viewers with different access show the permission rules.

Run (needs a collected corpus):
    python -I scripts/export_demo.py
    python -I scripts/export_demo.py --viewer gate2-dev "Someone who can find everything" EV
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from homegrown.cli import resolve_user
from homegrown.orgmap import org_map

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "src" / "homegrown" / "web" / "dashboard.html"
OUT = ROOT / "site" / "demo"

DEFAULT_VIEWERS = [
    ("gate2-dev", "Someone who can find everything", "EV"),
    ("dev-cloudcost-exporter", "Someone on team-cloudcost-exporter", "CC"),
]
NOTE = ("Taken on {date} from a test run on {repos} of Grafana's public repositories. "
        "Public code has no org chart, so the teams, lines of business and access rules "
        "are generated. Nothing on this page talks to a server.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--viewer", nargs=3, action="append", metavar=("LOGIN", "LABEL", "INITIALS"),
                    help="a user to include (repeatable); defaults to two contrasting users")
    args = ap.parse_args()
    viewers = args.viewer or DEFAULT_VIEWERS

    out = []
    for login, label, initials in viewers:
        m = org_map(resolve_user(login))
        for lob in m["lobs"]:
            for c in lob["components"]:
                c.pop("id", None)           # internal ids mean nothing outside the database
        out.append({"label": label, "initials": initials, "map": m})

    repos = max(v["map"]["repositories"] for v in out)
    today = dt.date.today()
    snapshot = {
        "taken": today.isoformat(),
        "note": NOTE.format(date=f"{today.day} {today:%B %Y}", repos=repos),
        "viewers": out,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "snapshot.json").write_text(json.dumps(snapshot, indent=1, ensure_ascii=False) + "\n",
                                       encoding="utf-8", newline="\n")

    page = PAGE.read_text(encoding="utf-8")
    marker = '<meta charset="utf-8">'
    assert marker in page, "dashboard.html lost its charset meta"
    page = page.replace(marker, marker + '\n<meta name="hg-snapshot" content="snapshot.json">', 1)
    page = page.replace("<title>HomeGrown · Org map</title>",
                        "<title>HomeGrown · Org map demo</title>", 1)
    (OUT / "index.html").write_text(page, encoding="utf-8", newline="\n")
    print(f"wrote {OUT / 'index.html'} and snapshot.json "
          f"({', '.join(f'{v['label']}: {v['map']['components']}' for v in out)})")


if __name__ == "__main__":
    main()
