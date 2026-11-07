"""Layer 2: claims about behaviour, from test names.

A test describes one behaviour by convention, and it stays true or the build breaks,
which makes test names a reliable source of "what does this actually do".

Note: none of these patterns use re.VERBOSE. In verbose mode an unescaped '#' starts a
comment and silently cuts the pattern short.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from ..models import Claim, Evidence, Layer
from .filters import is_generated

EXTRACTOR = "tests_extractor/0.1"

TEST_FILE = re.compile(
    r"(_test\.(go|py|rs)$)|(\.(test|spec)\.(ts|tsx|js|jsx|mjs)$)|"
    r"(^|/)(test_[^/]+\.py)$|(^|/)tests?/.*\.(py|go|ts|tsx|js|rs)$",
    re.I,
)

TEST_DECL = re.compile(
    r"func\s+(Test\w+)"                                           # go
    r"|def\s+(test_\w+)"                                          # python
    r"|(?:it|test)\s*\(\s*['\"`]([^'\"`\n]{4,160})['\"`]"         # js/ts
    r"|\#\[test\][\s\S]{0,120}?fn\s+(\w+)"                        # rust
)

SKIP_REASON = re.compile(
    r"t\.Skip(?:f|Now)?\s*\(\s*['\"]([^'\"\n]{6,240})"
    r"|pytest\.mark\.(?:skip|xfail)\s*\([^)]{0,240}?reason\s*=\s*['\"]([^'\"\n]{6,240})"
    r"|(?:it|test|describe)\.skip\s*\(\s*['\"`]([^'\"`\n]{6,240})"
    r"|\#\[ignore\s*=\s*\"([^\"\n]{6,240})\""
)

# A test name asserting a refusal is a stronger claim than one asserting a limit,
# and both are stronger than a generic smoke test. Order matters: first match wins.
KIND_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("refusal", re.compile(
        r"reject|refus|forbid|denie|denied|not_?allow|disallow|unauthori[sz]ed|"
        r"invalid|malformed|unsupported|not_?support|must_?not|cannot|can_?not|"
        r"raises|throws|errors?_?(?:on|when|if)|fails?_?(?:on|when|if)|"
        r"after_?close|read_?only", re.I)),
    ("limit", re.compile(
        r"limit|max(?:imum)?|min(?:imum)?|clamp|truncat|overflow|exceed|"
        r"too_?(?:large|long|many|big|small)|boundary|cap(?:ped)?|threshold|"
        r"out_?of_?range|below_?range|above_?range", re.I)),
    ("scale", re.compile(r"concurren|parallel|race|large|bulk|batch|throughput|timeout|"
                         r"slow|load|stress|many_", re.I)),
    ("pitfall", re.compile(r"empty|missing|null|nil|none|undefined|duplicate|conflict|"
                           r"corrupt|partial|unicode|utf8|escape|edge_?case|negative",
                           re.I)),
]

# Names that assert nothing useful about a boundary.
GENERIC = re.compile(
    r"^(test)?_?(it_)?(should_)?(render|work|init|initiali[sz]e|construct|exist|"
    r"be_?defined|not_?crash|smoke|basic|simple|sanity|happy_?path|ok|pass)"
    r"(s|_without_error|_correctly|_properly)?$",
    re.I,
)


def _norm(name: str) -> str:
    """TestRejectsGRPCTarget / should reject grpc target -> reject grpc target."""
    s = re.sub(r"^(Test|test_|it_|should_)", "", name.strip())
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s)      # camelCase -> words
    s = s.replace("_", " ").strip()
    return re.sub(r"\s+", " ", s)


def classify(name: str) -> str | None:
    """Which claim kind does this test name assert, if any?"""
    flat = re.sub(r"[\s_]+", "_", _norm(name)).strip("_")
    if GENERIC.match(flat):
        return None
    for kind, pat in KIND_PATTERNS:
        if pat.search(name) or pat.search(flat):
            return kind
    return None


def is_test_file(rel_path: str) -> bool:
    p = rel_path.replace("\\", "/")
    return bool(TEST_FILE.search(p)) and not is_generated(p)


def extract_file(rel_path: str, text: str, commit: str) -> list[Claim]:
    """Boundary claims from one test file."""
    claims: list[Claim] = []
    sha = hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()[:16]

    for m in TEST_DECL.finditer(text):
        raw = next((g for g in m.groups() if g), "")
        if not raw:
            continue
        kind = classify(raw)
        if kind is None:
            continue
        phrase = _norm(raw)
        claims.append(Claim(
            layer=Layer.BEHAVIOUR,
            kind=kind,  # type: ignore[arg-type]
            body=f"Asserted by its own tests: {phrase}.",
            evidence=Evidence(
                source="github_test",
                ref=f"{rel_path}::{raw}",
                source_version=commit,
                content_hash=sha,
                excerpt=raw,
                extractor_version=EXTRACTOR,
            ),
            extractor=EXTRACTOR,
            # High, and deliberately so: CI re-verifies this claim on every run.
            confidence=0.8,
        ))

    for m in SKIP_REASON.finditer(text):
        reason = next((g for g in m.groups() if g), "").strip()
        if not reason:
            continue
        claims.append(Claim(
            layer=Layer.BEHAVIOUR,
            kind="limit",
            body=f"A test is disabled with the reason: {reason}",
            evidence=Evidence(
                source="github_test", ref=rel_path, source_version=commit,
                content_hash=sha, excerpt=reason, extractor_version=EXTRACTOR,
            ),
            extractor=EXTRACTOR,
            confidence=0.7,
        ))
    return claims


def extract_repo(repo_root: Path, commit: str = "HEAD") -> dict[str, list[Claim]]:
    """Walk a repo and return {directory of the test file: claims}.

    Keyed by directory so the caller can attach claims to the nearest unit.
    """
    out: dict[str, list[Claim]] = {}
    skip = {".git", "node_modules", "vendor", "dist", "build", "target",
            "__pycache__", ".venv", ".yarn"}
    for p in repo_root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in skip for part in p.parts):
            continue
        rel = p.relative_to(repo_root).as_posix()
        if not is_test_file(rel):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if len(text) > 600_000:
            continue
        claims = extract_file(rel, text, commit)
        if claims:
            out.setdefault(str(p.parent.relative_to(repo_root).as_posix()), []).extend(claims)
    return out
