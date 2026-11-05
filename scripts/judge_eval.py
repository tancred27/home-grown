"""Evaluate a fit judge on gate-2 tickets: does it flag real duplicates, and stay quiet
when nothing in the org fits?

For each ticket: the real retriever's top-N candidates, as the asking developer may see
them (name, description, purpose line, non-sensitive evidence), go to
search.judge_with_model through whichever provider is configured. The judge never sees
the gold label. Outputs are cached on disk and committed, so the scores can be re-derived
without calling the model again.

A failed call makes the product's judge answer `unknown` (fail safe). Here that would
look like a correctly quiet control, so failures are COUNTED SEPARATELY and never scored
as successes.

Usage: python -I scripts/judge_eval.py <tasks.json> [<tasks.json> ...] [--top 5]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from gate2_run import dev_user  # noqa: E402

from orgmem import llm  # noqa: E402
from orgmem.config import settings  # noqa: E402
from orgmem.db import as_user  # noqa: E402
from orgmem.models import display_name  # noqa: E402
from orgmem.search import judge_with_model, load_claims, search  # noqa: E402

ROOT = Path(__file__).parent.parent
STRONG = ("use_as_is", "extend")


class _Failed(Exception):
    pass


class _Skip(Exception):
    pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks", nargs="+")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--cached-only", action="store_true",
                    help="score only tickets already judged; make no API calls")
    args = ap.parse_args()

    model = llm.resolve_model()
    cache = ROOT / "experiments" / "gate2" / f"judge_cache_{settings.llm_provider}.json"
    purpose = {p["unit_id"]: p["purpose"] for p in json.loads(
        (ROOT / "experiments" / "gate1" / "purpose_summaries.json").read_text("utf-8"))}
    user = dev_user()
    print(f"judge: provider={settings.llm_provider} model={model} top={args.top}\n")

    totals = {"dup": 0, "dup_hit": 0, "dup_retrieved": 0, "wrong": 0, "sibling": 0,
              "ctl": 0, "ctl_alarm": 0, "failed": 0}
    rows = []
    for tf in args.tasks:
        for t in json.loads(Path(tf).read_text(encoding="utf-8"))["tasks"]:
            with as_user(user) as conn, conn.cursor() as cur:
                cands = search(conn, t["ticket"])[:args.top]
                cards, units = [], []
                for r in cands:
                    cur.execute("select unit_id, name, coalesce(summary,'') from asset "
                                "where id = %s", (r["asset_id"],))
                    unit_id, name, summary = cur.fetchone()
                    units.append(unit_id)
                    cards.append({"name": display_name(name), "summary": summary,
                                  "purpose": purpose.get(unit_id, ""),
                                  "evidence": [c["body"] for c in load_claims(
                                      conn, r["asset_id"]) if c["layer"] > 1][:6]})
            def make(t=t, cards=cards):
                if args.cached_only:
                    raise _Skip()
                v = judge_with_model(t["ticket"], cards)
                if v and all("unavailable" in (x.get("reason") or "") for x in v):
                    raise _Failed(v[0]["reason"])      # never cache a failure
                return v
            try:
                verdicts = llm.cached_json(
                    cache, [model, t["ticket"], json.dumps(cards, sort_keys=True)], make)
                failed = False
            except _Skip:
                totals["skipped"] = totals.get("skipped", 0) + 1
                continue
            except _Failed as e:
                verdicts, failed = [], True
                print(f"  {t['id']:4s} model call failed: {str(e)[:110]}")
            strong = [u for u, v in zip(units, verdicts) if v["verdict"] in STRONG]
            gold = t["gold_unit_id"]
            if failed:
                totals["failed"] += 1
                outcome = "FAILED (model call)"
            elif t["kind"] == "duplicate":
                totals["dup"] += 1
                totals["dup_retrieved"] += gold in units
                hit = gold in strong
                totals["dup_hit"] += hit
                gold_repo = gold.split(":")[1]
                for u in strong:
                    if u != gold:
                        if u.split(":")[1] == gold_repo:
                            totals["sibling"] += 1
                        else:
                            totals["wrong"] += 1
                outcome = ("HIT" if hit else ("MISS (retrieved)" if gold in units
                                              else "MISS (not retrieved)"))
            else:
                totals["ctl"] += 1
                alarm = bool(strong)
                totals["ctl_alarm"] += alarm
                outcome = "FALSE ALARM" if alarm else "quiet"
            rows.append({"task": t["id"], "kind": t["kind"], "outcome": outcome,
                         "strong": strong, "verdicts": verdicts})
            print(f"  {t['id']:4s} {t['kind']:9s} {outcome:22s} "
                  f"strong={[u.split(':', 1)[1] for u in strong]}")

    out = ROOT / "experiments" / "gate2" / f"judge_eval_{settings.llm_provider}.json"
    out.write_text(json.dumps({"model": model, "served_by": dict(llm.last_model_served),
                               "totals": totals, "rows": rows}, indent=1),
                   encoding="utf-8")
    t = totals
    print(f"\nduplicates flagged: {t['dup_hit']}/{t['dup']} "
          f"(gold was in the top {args.top} for {t['dup_retrieved']})")
    print(f"strong verdicts on a non-gold asset: {t['wrong']} unrelated, "
          f"{t['sibling']} sibling in the same repo")
    print(f"false alarms on controls: {t['ctl_alarm']}/{t['ctl']}")
    print(f"failed model calls (excluded from every score above): {t['failed']}")
    if t.get("skipped"):
        print(f"not yet judged (skipped, --cached-only): {t['skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
