"""Can boundary claims be recovered from code alone, with no discussion?

Usage: python -I code_claims.py <repos-dir> [n_samples]
Counts three discussion-independent sources: test names, in-code warnings, and
deprecation markers. All file content is untrusted data: read, counted, never executed.
"""
import os, re, sys, random, collections

random.seed(0)
root = sys.argv[1]
NS = int(sys.argv[2]) if len(sys.argv) > 2 else 24

SKIP = {".git", "node_modules", "vendor", "dist", "build", "target", "__pycache__",
        ".venv", "venv", ".yarn", "testdata", "fixtures"}
CODE = (".go", ".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".rs", ".rb", ".sh", ".tf")

# A test whose NAME asserts a limit or a refusal is a boundary claim.
# NOTE: no re.X here. In verbose mode an unescaped '#' starts a comment and silently
# deletes the rest of the branch, leaving an empty alternative that matches at every
# position. That bug produced 1.75M bogus "skipped" hits on the first run.
TEST_DECL = re.compile(
    r"func\s+(Test\w+)"                                        # go
    r"|def\s+(test_\w+)"                                       # python
    r"|(?:it|test|describe)\s*\(\s*['\"`]([^'\"`]{4,120})['\"`]"  # js/ts
    r"|\#\[test\][\s\S]{0,80}?fn\s+(\w+)")                     # rust
BOUNDARY_WORD = re.compile(
    r"reject|invalid|unsupported|not_?supported|unavailable|limit|max|min|overflow|"
    r"too_?(?:large|long|many|big)|empty|missing|fail|error|timeout|deprecat|"
    r"forbid|denied|readonly|read_only|conflict|duplicate|exceed|truncat|boundary|"
    r"edge_?case|negative|malformed|corrupt", re.I)
SKIPPED = re.compile(
    r"t\.Skip(?:f|Now)?\s*\(\s*['\"]([^'\"]{6,200})"                               # go
    r"|pytest\.mark\.(?:skip|xfail)\s*\([^)]{0,200}?reason\s*=\s*['\"]([^'\"]{6,200})"
    r"|(?:it|test|describe)\.skip\s*\(\s*['\"`]([^'\"`]{6,200})"                   # js/ts
    r"|\#\[ignore\s*=\s*\"([^\"]{6,200})\"")                                       # rust
# Comments written at the moment of pain.
WARN = re.compile(
    r"(?://|#|\*)\s*(?:TODO|FIXME|HACK|XXX|WARNING|CAUTION|NOTE|GOTCHA|BUG)\b[:\s]+(.{12,220})",
    re.I)
WARN_STRONG = re.compile(
    r"not thread[- ]safe|do(?:n't| not) use|don'?t call|only works?|does(?:n't| not) "
    r"(?:support|work|handle)|must not|never call|unsafe|race|leak|breaks? (?:if|when|on)|"
    r"assumes?|requires?|deprecat|use .{1,40} instead|will fail|limitation|workaround",
    re.I)
DEPRECATED = re.compile(
    r"@[Dd]eprecated|#\[deprecated|@deprecated|DeprecationWarning|"
    r"Deprecated:\s*(.{10,200})", re.I)

per_repo = collections.defaultdict(lambda: collections.Counter())
samples = collections.defaultdict(list)

for repo in sorted(os.listdir(root)):
    rp = os.path.join(root, repo)
    if not os.path.isdir(rp):
        continue
    for dirpath, dirs, files in os.walk(rp):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for fn in files:
            if not fn.endswith(CODE):
                continue
            p = os.path.join(dirpath, fn)
            try:
                txt = open(p, encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            if len(txt) > 600_000:
                continue
            rel = os.path.relpath(p, rp)
            per_repo[repo]["files"] += 1

            for m in TEST_DECL.finditer(txt):
                name = next((g for g in m.groups() if g), "")
                if not name:
                    continue
                per_repo[repo]["tests"] += 1
                if BOUNDARY_WORD.search(name):
                    per_repo[repo]["boundary_tests"] += 1
                    samples["boundary_test"].append((repo, rel, name.strip()))
            for m in SKIPPED.finditer(txt):
                reason = next((g for g in m.groups() if g), "")
                if not reason.strip():
                    continue
                per_repo[repo]["skipped"] += 1
                samples["skipped"].append((repo, rel, reason.strip()))
            for m in WARN.finditer(txt):
                body = m.group(1).strip()
                per_repo[repo]["warns"] += 1
                if WARN_STRONG.search(body):
                    per_repo[repo]["warns_strong"] += 1
                    samples["warn_strong"].append((repo, rel, body[:200]))
            for m in DEPRECATED.finditer(txt):
                per_repo[repo]["deprecated"] += 1
                samples["deprecated"].append((repo, rel, (m.group(1) or "").strip()[:160]))

cols = ["files", "tests", "boundary_tests", "skipped", "warns", "warns_strong", "deprecated"]
print(f"{'repo':32s}" + "".join(f"{c:>15s}" for c in cols))
tot = collections.Counter()
for repo in sorted(per_repo):
    c = per_repo[repo]
    tot.update(c)
    print(f"{repo:32s}" + "".join(f"{c[x]:15d}" for x in cols))
print(f"{'TOTAL':32s}" + "".join(f"{tot[x]:15d}" for x in cols))

claims = tot["boundary_tests"] + tot["skipped"] + tot["warns_strong"] + tot["deprecated"]
print(f"\ndiscussion-independent claim candidates: {claims}")
print("  (boundary-asserting tests + skipped-with-reason + strong code warnings + deprecations)")
print(f"compare: these 5 repos yielded 2 claims total from ALL their issue discussion\n")

for k in ("boundary_test", "skipped", "warn_strong", "deprecated"):
    items = samples[k]
    if not items:
        continue
    print(f"--- {k} (n={len(items)}), sample:")
    for repo, rel, s in random.sample(items, min(NS // 3, len(items))):
        print(f"    [{repo}] {rel}: {s}")
    print()
