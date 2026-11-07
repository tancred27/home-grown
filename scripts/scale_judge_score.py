"""Score the blind fit judges against the hidden key (experiments/scale/PREREG_judge.md).

    python -I scripts/scale_judge_score.py <judge_scale-dir>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

STRONG = ("use_as_is", "extend")


def main() -> int:
    d = Path(sys.argv[1])
    key = json.loads((d / "judge_key.json").read_text("utf-8"))
    report = {}
    for size, keys in key.items():
        out = {r["tid"]: r for r in json.loads((d / f"judge_out_{size}.json").read_text("utf-8"))}
        m = {"dup": 0, "gold_in_top5": 0, "caught": 0, "wrong_yes_sibling": 0,
             "wrong_yes_unrelated": 0, "dup_tickets_with_unrelated_yes": 0,
             "ctl": 0, "ctl_false_alarm": 0, "answer": {"yes": 0, "no": 0, "unknown": 0},
             "missing": 0, "examples": []}
        for tid, k in keys.items():
            v = out.get(tid)
            if v is None:
                m["missing"] += 1
                continue
            verdict = {x["candidate"]: x["verdict"] for x in v["verdicts"]}
            strong = [L for L in k["letters"] if verdict.get(L) in STRONG]
            ans = ("yes" if strong else
                   "no" if verdict and all(verdict.get(L) == "not_a_fit" for L in k["letters"])
                   else "unknown")
            m["answer"][ans] += 1
            if k["kind"] == "duplicate":
                m["dup"] += 1
                gold = k["gold"]
                if gold in k["letters"].values():
                    m["gold_in_top5"] += 1
                if any(k["letters"][L] == gold for L in strong):
                    m["caught"] += 1
                unrelated = 0
                for L in strong:
                    u = k["letters"][L]
                    if u == gold:
                        continue
                    if u.split(":")[1] == gold.split(":")[1]:
                        m["wrong_yes_sibling"] += 1
                    else:
                        m["wrong_yes_unrelated"] += 1
                        unrelated += 1
                        m["examples"].append({"tid": tid, "unit": u, "verdict": verdict[L],
                                              "reason": next(x["reason"] for x in v["verdicts"]
                                                             if x["candidate"] == L)})
                m["dup_tickets_with_unrelated_yes"] += bool(unrelated)
            else:
                m["ctl"] += 1
                if strong:
                    m["ctl_false_alarm"] += 1
                    for L in strong:
                        m["examples"].append({"tid": tid, "unit": k["letters"][L],
                                              "verdict": verdict[L], "control": True,
                                              "reason": next(x["reason"] for x in v["verdicts"]
                                                             if x["candidate"] == L)})
        report[size] = m
        print(f"size {size}: caught {m['caught']}/{m['dup']} duplicates "
              f"(gold in top 5: {m['gold_in_top5']}); wrong 'yes' on duplicate tickets: "
              f"{m['wrong_yes_unrelated']} unrelated ({m['dup_tickets_with_unrelated_yes']} "
              f"tickets), {m['wrong_yes_sibling']} sibling; false alarms on controls: "
              f"{m['ctl_false_alarm']}/{m['ctl']}; answers {m['answer']}; missing {m['missing']}")
    (d / "judge_score.json").write_text(json.dumps(report, indent=1), "utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
