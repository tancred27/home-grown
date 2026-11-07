"""Score the LLM page decider against the hand labels in experiments/pages/labels.json.

The labels were written BEFORE the decider existed, by one rater, with the same written
criterion the decider's prompt uses. So this measures whether the model applies that
criterion the way the rater did; it does not prove the criterion is the right one.

Page bodies are read from the cloned repos (object_ref docs:<org>/<repo>/<path>).
Model outputs are cached (experiments/pages/judge_cache_<provider>.json); failures are
never cached and are reported separately, never scored.

Usage: python -I scripts/page_eval.py <repos-dir> [<repos-dir> ...] [--cached-only]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from homegrown import llm
from homegrown.collector.graph import _SOLVED_SIGNAL
from homegrown.collector.pages import judge_page
from homegrown.config import settings

ROOT = Path(__file__).parent.parent


class _Skip(Exception):
    pass


def prf(pairs):
    tp = sum(1 for p, g in pairs if p and g)
    fp = sum(1 for p, g in pairs if p and not g)
    fn = sum(1 for p, g in pairs if not p and g)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    acc = sum(1 for p, g in pairs if p == g) / len(pairs) if pairs else 0.0
    return prec, rec, acc, tp, fp, fn


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="+")
    ap.add_argument("--cached-only", action="store_true")
    args = ap.parse_args()
    roots = [Path(r) for r in args.roots]
    labels = json.loads((ROOT / "experiments/pages/labels.json").read_text("utf-8"))["pages"]
    model = llm.resolve_model()
    cache = ROOT / "experiments" / "pages" / f"judge_cache_{settings.llm_provider}.json"
    print(f"page decider: provider={settings.llm_provider} model={model}, "
          f"{len(labels)} labelled pages\n")

    rule_pairs, model_pairs, rows, failed, missing = [], [], [], 0, 0
    for p in labels:
        _, full_path = p["object_ref"].split(":", 1)
        parts = full_path.split("/")
        repo, rel = parts[1], "/".join(parts[2:])
        f = next((r / repo / rel for r in roots if (r / repo / rel).is_file()), None)
        if f is None:
            missing += 1
            print(f"  missing body: {p['object_ref']}")
            continue
        body = f.read_text(encoding="utf-8", errors="ignore")
        text = f"{p['title']}\n{body}"
        rule = bool(_SOLVED_SIGNAL.search(text[:6000]))

        def make(title=p["title"], body=body):
            if args.cached_only:
                raise _Skip()
            return judge_page(title, body)
        try:
            v = llm.cached_json(cache, [model, p["title"], body], make)
        except _Skip:
            continue
        except llm.QuotaExhausted as e:
            print(f"\nSTOPPED: {e}\nNothing was scored or written; rerun later "
                  f"(judged pages are cached).")
            return 2
        except Exception as e:
            failed += 1
            print(f"  FAILED {p['object_ref']}: {type(e).__name__}: {str(e)[:100]}")
            continue
        rule_pairs.append((rule, p["keep"]))
        model_pairs.append((bool(v["keep"]), p["keep"]))
        mark = "" if bool(v["keep"]) == p["keep"] else "  <-- disagrees with label"
        print(f"  label={'KEEP' if p['keep'] else 'ign '} model={'KEEP' if v['keep'] else 'ign '}"
              f" ({v['kind']:15s}) {p['title'][:50]}{mark}")
        rows.append({**p, "rule_kept_now": rule, "model": v})

    out = {"model": model, "rows": rows, "failed": failed, "missing": missing}
    for name, pairs in (("keyword rule", rule_pairs), ("model", model_pairs)):
        pr, rc, acc, tp, fp, fn = prf(pairs)
        out[name] = {"precision": pr, "recall": rc, "accuracy": acc,
                     "tp": tp, "fp": fp, "fn": fn, "n": len(pairs)}
        print(f"\n{name:13s} precision {pr:.2f}  recall {rc:.2f}  accuracy {acc:.2f}  "
              f"(kept {tp + fp}: {tp} right, {fp} wrong; missed {fn}; n={len(pairs)})")
    print(f"failed model calls (not scored): {failed}; pages without a body: {missing}")
    (ROOT / "experiments" / "pages" / f"eval_{settings.llm_provider}.json").write_text(
        json.dumps(out, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
