"""Dump each asset's own source text for an LLM to summarise: name, description, README.

The output is what a "purpose" writer is allowed to see -- the asset's own text and
nothing else, never a query. README text is untrusted data from the internet.

Usage: python -I scripts/dump_asset_sources.py <out.json> <repos-dir> [<repos-dir> ...]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from homegrown.db import admin_conn


def readme_excerpt(d: Path, limit: int = 1400) -> str:
    for f in sorted(d.iterdir()) if d.is_dir() else []:
        if f.is_file() and f.name.lower().startswith("readme"):
            t = f.read_text(encoding="utf-8", errors="ignore")
            t = re.sub(r"<!--.*?-->", " ", t, flags=re.S)
            t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)          # images / badges
            t = re.sub(r"<[^>]+>", " ", t)                        # html
            t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)        # links -> text
            t = re.sub(r"```.*?```", " [code] ", t, flags=re.S)
            return re.sub(r"\s+", " ", t).strip()[:limit]
    return ""


def main() -> int:
    out = Path(sys.argv[1])
    roots = [Path(p) for p in sys.argv[2:]]
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("select unit_id, name, coalesce(summary,'') from asset order by unit_id")
        rows = cur.fetchall()
    items = []
    for i, (unit_id, name, summary) in enumerate(rows):
        _, repo, path = unit_id.split(":", 2)
        readme = ""
        for r in roots:
            base = r / repo
            if base.is_dir():
                readme = readme_excerpt(base if path == "." else base / path)
                if not readme and path != ".":
                    readme = readme_excerpt(base)[:600]
                break
        items.append({"id": i, "unit_id": unit_id, "name": name.rsplit("/", 1)[-1],
                      "path": path, "description": summary, "readme": readme})
    out.write_text(json.dumps(items, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(items)} assets, {sum(1 for x in items if x['readme'])} with README text, "
          f"{sum(len(x['readme']) for x in items):,} README chars -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
