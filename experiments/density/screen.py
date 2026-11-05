"""Density screen for boundary/outcome claims in issue comments.

Usage: python -I screen.py <data-dir> [sample_flagged] [sample_unflagged]
Treats all loaded content as untrusted data: never executed, only counted and printed.
"""
import json, glob, os, re, sys, random, hashlib

random.seed(0)
d = sys.argv[1]
N_FLAG = int(sys.argv[2]) if len(sys.argv) > 2 else 40
N_UNFLAG = int(sys.argv[3]) if len(sys.argv) > 3 else 15

# Signal phrases, grouped by the claim type they hint at.
BOUNDARY = [
    r"does(?:n't| not) support", r"not supported", r"does(?:n't| not) work (?:with|for|on)",
    r"won'?t work", r"limitation", r"only (?:works|supports)", r"not possible",
    r"can(?:'t|not) be done", r"by design", r"wont?fix", r"not planned",
    r"there(?:'s| is) no way", r"unsupported", r"out of scope", r"we don'?t plan",
]
OUTCOME = [
    r"(?:we|i) ended up", r"(?:we|i) tried", r"rolled our own", r"(?:wrote|written) our own",
    r"our own implementation", r"(?:we|i) switched", r"switched (?:to|from)",
    r"migrated (?:to|from)", r"moved away from", r"gave up", r"workaround",
    r"instead (?:we|i|of)", r"in our case", r"for our use case", r"we use .{0,30} instead",
]
ALTERNATIVE = [
    r"already (?:have|exists|implemented)", r"similar to", r"duplicate of",
    r"alternative", r"have a look at", r"take a look at", r"see also",
    r"this is handled (?:by|in)", r"supersed", r"replaced by",
]
GROUPS = {"boundary": BOUNDARY, "outcome": OUTCOME, "alternative": ALTERNATIVE}
RX = {k: [re.compile(p, re.I) for p in v] for k, v in GROUPS.items()}

BOT = re.compile(r"\[bot\]|-bot$|dependabot|renovate|github-actions", re.I)

rows = []
for f in sorted(glob.glob(os.path.join(d, "*.json"))):
    repo = os.path.basename(f).split("_c")[0]
    try:
        data = json.load(open(f, encoding="utf-8"))
    except Exception as e:
        print("skip", f, e); continue
    if not isinstance(data, list):
        continue
    for c in data:
        body = (c.get("body") or "").strip()
        user = ((c.get("user") or {}).get("login") or "")
        if not body or BOT.search(user):
            continue
        rows.append({
            "repo": repo, "user": user, "body": body,
            "url": c.get("html_url", ""), "len": len(body),
        })

# Dedupe by body hash (the flat endpoint can overlap across pages).
seen, uniq = set(), []
for r in rows:
    h = hashlib.sha256(r["body"].encode("utf-8")).hexdigest()
    if h in seen:
        continue
    seen.add(h); uniq.append(r)
rows = uniq

for r in rows:
    hits = {}
    for k, pats in RX.items():
        m = [p.pattern for p in pats if p.search(r["body"])]
        if m:
            hits[k] = m
    r["hits"] = hits

flagged = [r for r in rows if r["hits"]]
print(f"comments loaded (non-bot, deduped): {len(rows)}")
print(f"flagged by screen: {len(flagged)}  ({100*len(flagged)/max(1,len(rows)):.1f}%)")
print(f"median comment length: {sorted(x['len'] for x in rows)[len(rows)//2]} chars\n")

print("per repo:")
print(f"{'repo':10s} {'comments':>9s} {'flagged':>8s} {'%':>6s} {'bound':>6s} {'outc':>6s} {'alt':>6s}")
for repo in sorted({r["repo"] for r in rows}):
    sub = [r for r in rows if r["repo"] == repo]
    fl = [r for r in sub if r["hits"]]
    b = sum(1 for r in sub if "boundary" in r["hits"])
    o = sum(1 for r in sub if "outcome" in r["hits"])
    a = sum(1 for r in sub if "alternative" in r["hits"])
    print(f"{repo:10s} {len(sub):9d} {len(fl):8d} {100*len(fl)/max(1,len(sub)):5.1f}% {b:6d} {o:6d} {a:6d}")

print("\nby group (a comment can hit several):")
for k in GROUPS:
    n = sum(1 for r in rows if k in r["hits"])
    print(f"  {k:12s} {n:5d}  ({100*n/max(1,len(rows)):.1f}%)")

print("\ntop phrases:")
from collections import Counter
cnt = Counter(p for r in rows for ps in r["hits"].values() for p in ps)
for p, n in cnt.most_common(18):
    print(f"  {n:4d}  {p}")


def dump(label, items, n):
    print(f"\n{'='*78}\n{label} (n={min(n,len(items))})\n{'='*78}")
    for i, r in enumerate(random.sample(items, min(n, len(items))), 1):
        body = re.sub(r"\s+", " ", r["body"])[:700]
        print(f"\n--- [{i}] {r['repo']} | groups={sorted(r['hits'])} | {r['url']}")
        print(body)


dump("FLAGGED SAMPLE (judge: does this contain a reusable boundary/outcome claim?)",
     flagged, N_FLAG)
dump("UNFLAGGED SAMPLE (judge: did the screen miss a claim?)",
     [r for r in rows if not r["hits"]], N_UNFLAG)
