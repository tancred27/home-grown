"""Thread-level density and the length-signal test. Usage: python -I stats.py <data-dir>"""
import json, glob, os, re, sys, collections

d = sys.argv[1]
BOT = re.compile(r"\[bot\]|-bot$|dependabot|renovate|github-actions", re.I)
# Patterns judged (by reading 29 flagged samples) to indicate a STRONG claim,
# minus the ones that turned out to be noise or mechanical boilerplate.
STRONG = [re.compile(p, re.I) for p in [
    r"does(?:n't| not) support", r"not supported", r"does(?:n't| not) work (?:with|for|on)",
    r"won'?t work", r"limitation", r"only (?:works|supports)", r"not possible",
    r"can(?:'t|not) be done", r"by design", r"wont?fix", r"not planned",
    r"there(?:'s| is) no way", r"unsupported", r"out of scope", r"we don'?t plan",
    r"rolled our own", r"(?:wrote|written) our own", r"our own implementation",
    r"gave up", r"workaround", r"in our case", r"for our use case",
    r"(?:we|i) ended up", r"moved away from", r"already (?:have|exists|implemented)",
    r"duplicate of", r"no longer needed", r"reproduces on", r"we hit the same",
    r"in production", r"in our deployment",
]]
# Mechanical boilerplate that matched the first screen but carries little content.
BOILER = re.compile(r"^\s*(migrated to|superseded by|replaced by|closing (this|in favou?r))",
                    re.I)

rows = []
for f in sorted(glob.glob(os.path.join(d, "*.json"))):
    for c in json.load(open(f, encoding="utf-8")):
        body = (c.get("body") or "").strip()
        user = ((c.get("user") or {}).get("login") or "")
        if not body or BOT.search(user):
            continue
        rows.append({
            "repo": os.path.basename(f).split("_c")[0],
            "body": body, "len": len(body),
            "thread": c.get("issue_url", ""),
            "strong": bool([p for p in STRONG if p.search(body)]),
            "boiler": bool(BOILER.match(body)),
        })

n = len(rows)
threads = collections.defaultdict(list)
for r in rows:
    threads[r["thread"]].append(r)

print(f"human comments: {n}")
print(f"distinct threads touched: {len(threads)}")
print(f"comments per thread (in sample): {n/max(1,len(threads)):.2f}")
print(f"boilerplate ('migrated to...'): {sum(1 for r in rows if r['boiler'])}")
strong = [r for r in rows if r["strong"] and not r["boiler"]]
print(f"strong-signal comments: {len(strong)}  ({100*len(strong)/n:.1f}% of human comments)")
th_strong = sum(1 for t, cs in threads.items() if any(c["strong"] and not c["boiler"] for c in cs))
print(f"threads with >=1 strong-signal comment: {th_strong}/{len(threads)} "
      f"({100*th_strong/max(1,len(threads)):.1f}%)")

print("\n--- length as a signal ---")
print(f"{'bucket':>14s} {'comments':>9s} {'strong':>7s} {'rate':>7s}")
buckets = [(0, 100), (100, 250), (250, 500), (500, 1000), (1000, 2500), (2500, 10**9)]
for lo, hi in buckets:
    sub = [r for r in rows if lo <= r["len"] < hi and not r["boiler"]]
    st = [r for r in sub if r["strong"]]
    label = f"{lo}-{hi if hi < 10**9 else '+'}"
    if sub:
        print(f"{label:>14s} {len(sub):9d} {len(st):7d} {100*len(st)/len(sub):6.1f}%")

long_ = [r for r in rows if r["len"] >= 500 and not r["boiler"]]
print(f"\ncomments >=500 chars: {len(long_)} ({100*len(long_)/n:.0f}% of human comments)"
      f" and hold {sum(1 for r in long_ if r['strong'])}/{len(strong)} "
      f"({100*sum(1 for r in long_ if r['strong'])/max(1,len(strong)):.0f}%) of strong signals")

print("\n--- per repo, boilerplate removed ---")
print(f"{'repo':10s} {'human':>6s} {'boiler':>7s} {'strong':>7s} {'rate':>7s}")
for repo in sorted({r["repo"] for r in rows}):
    sub = [r for r in rows if r["repo"] == repo]
    bo = sum(1 for r in sub if r["boiler"])
    st = sum(1 for r in sub if r["strong"] and not r["boiler"])
    base = len(sub) - bo
    print(f"{repo:10s} {len(sub):6d} {bo:7d} {st:7d} {100*st/max(1,base):6.1f}%")
