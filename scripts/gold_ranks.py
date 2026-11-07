"""Where does the gold asset rank in retrieval, for every gate-2 duplicate ticket?

Decides how deep the model judge's shortlist must be: the judge can only flag what it is
shown. No LLM calls; database only. Run at different corpus sizes (scripts/add_distractors.py)
to see how the needed depth grows.

Usage: python -I scripts/gold_ranks.py [--label NAME]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from gate2_run import dev_user  # noqa: E402

from homegrown.db import as_user  # noqa: E402
from homegrown.search import search  # noqa: E402

ROOT = Path(__file__).parent.parent
SETS = ["tasks.json", "tasks_heldout.json", "tasks_judge_fresh.json",
        "tasks_judge_fresh2.json"]
DEPTHS = (1, 5, 10, 15, 20, 30, 50)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="")
    args = ap.parse_args()
    rows = []
    with as_user(dev_user()) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from asset")
        visible = cur.fetchone()[0]
        for name in SETS:
            for t in json.loads((ROOT / "experiments/gate2" / name).read_text("utf-8"))["tasks"]:
                if t["kind"] != "duplicate":
                    continue
                ids = []
                for r in search(conn, t["ticket"]):
                    cur.execute("select unit_id from asset where id = %s", (r["asset_id"],))
                    ids.append(cur.fetchone()[0])
                g = t["gold_unit_id"]
                rows.append({"set": name, "task": t["id"],
                             "rank": ids.index(g) + 1 if g in ids else None})
    n = len(rows)
    within = {k: sum(1 for r in rows if r["rank"] and r["rank"] <= k) for k in DEPTHS}
    print(f"visible assets: {visible}; duplicate tickets: {n}")
    for k in DEPTHS:
        print(f"  gold within top {k:2d}: {within[k]}/{n}")
    print("  ranks > 5: " + ", ".join(f"{r['task']}={r['rank']}" for r in rows
                                       if not r["rank"] or r["rank"] > 5))
    tag = f"_{args.label}" if args.label else ""
    (ROOT / "experiments/gate2" / f"gold_ranks{tag}.json").write_text(
        json.dumps({"visible_assets": visible, "within": within, "rows": rows}, indent=1),
        encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
