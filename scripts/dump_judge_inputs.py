"""Build the input for a blind fit judge: each ticket plus its top-5 candidate cards.

The judge file holds the ticket and what a developer in another team could see of each
candidate (name, description, purpose line, non-sensitive claims) under letters A-E.
It never holds the gold label. The letter -> asset mapping goes to a separate key file
that the judge does not see.

Usage: python -I scripts/dump_judge_inputs.py <tasks.json> <judge_in.json> <key.json>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from gate2_run import dev_user  # noqa: E402

from orgmem.db import as_user  # noqa: E402
from orgmem.search import load_claims, search  # noqa: E402

ROOT = Path(__file__).parent.parent


def main() -> int:
    tasks = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["tasks"]
    purpose = {p["unit_id"]: p["purpose"] for p in json.loads(
        (ROOT / "experiments" / "gate1" / "purpose_summaries.json").read_text("utf-8"))}
    user = dev_user()
    judge_in, key = [], {}
    with as_user(user) as conn, conn.cursor() as cur:
        for t in tasks:
            rows = search(conn, t["ticket"])[:5]
            cands = []
            for letter, r in zip("ABCDE", rows, strict=False):
                cur.execute("select unit_id, name, coalesce(summary,'') from asset "
                            "where id = %s", (r["asset_id"],))
                unit_id, name, summary = cur.fetchone()
                claims = [c["body"].replace("Asserted by its own tests: ", "test: ")
                          for c in load_claims(conn, r["asset_id"]) if c["layer"] > 1][:6]
                cands.append({"candidate": letter, "name": name.rsplit("/", 1)[-1],
                              "description": summary, "purpose": purpose.get(unit_id, ""),
                              "evidence": claims})
                key[f"{t['id']}:{letter}"] = str(r["asset_id"])
            judge_in.append({"ticket_id": t["id"], "ticket": t["ticket"],
                             "candidates": cands})
    Path(sys.argv[2]).write_text(json.dumps(judge_in, indent=1, ensure_ascii=False),
                                 encoding="utf-8")
    Path(sys.argv[3]).write_text(json.dumps(key, indent=1), encoding="utf-8")
    print(f"{len(judge_in)} tickets x up to 5 candidates -> {sys.argv[2]} (key kept apart)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
