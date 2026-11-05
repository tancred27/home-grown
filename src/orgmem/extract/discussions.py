"""Layer 3: experience claims from discussions.

The scarcest and most valuable layer: who tried it, who gave up, why, access friction.
Measured at ~4-5% of human comments and ~2.4 strong claims per repo for its entire
life, with four of eight repos producing none at all (experiments/density/RESULTS.md).
So this extractor is expected to return little, and that is the design, not a bug.

Two implementations behind one protocol:

  RuleExtractor   phrase-based. Runs now, no API key, ~41-50% precision measured by
                  hand. Good enough to build and test the whole pipeline end to end.
  ModelExtractor  the real one. Sends the comment to a model that must return
                  schema-conformant claims carrying evidence. NOT YET IMPLEMENTED --
                  it needs a provider key, and claim accuracy is an open gate
                  (docs/design.md section 9).

Whichever runs, the model never gets tools and its output must match the schema, so a
comment reading "ignore previous instructions" cannot cause an action. Source text is
data.
"""

from __future__ import annotations

import hashlib
import re
from typing import Protocol

from ..models import Claim, Evidence, Layer
from .filters import keep_comment

EXTRACTOR = "discussions_rules/0.1"

# Phrases that survived hand-reading 43 flagged comments. The ones that did NOT
# survive are listed at the bottom and deliberately excluded.
SIGNALS: list[tuple[str, str, re.Pattern]] = [
    ("refusal", "does not do this", re.compile(
        r"does(?:n't| not) support|not supported|unsupported|"
        r"does(?:n't| not) (?:work|handle)|won'?t work|not possible|"
        r"can(?:'t|not) be done|by design|wont?fix|not planned|"
        r"there(?:'s| is) no way|out of scope|we don'?t plan", re.I)),
    ("limit", "has a limit", re.compile(
        r"only (?:works|supports)|limitation|limited to|"
        r"fails? (?:on|above|over|when)|breaks? (?:on|above|over|when)|"
        r"over \d+\s*(?:[kmg]i?b|bytes|rows|items)|"
        r"past \d+\s*(?:[kmg]i?b|bytes)", re.I)),
    ("pitfall", "has a pitfall", re.compile(
        r"wrong sense of (?:protection|security)|gotcha|"
        r"reproduces? on|we hit the same|still reproduces|"
        r"not thread[- ]safe|race condition|silently", re.I)),
    ("outcome", "someone tried it", re.compile(
        r"rolled our own|(?:wrote|written) our own|our own implementation|"
        r"(?:we|i) ended up|(?:we|i) gave up|moved away from|"
        r"switched (?:to|from)|migrated (?:to|from) ", re.I)),
    ("pitfall", "needs a workaround", re.compile(
        r"workaround|work[- ]around", re.I)),
    ("lifecycle", "already exists elsewhere", re.compile(
        r"already (?:exists|have|implemented)|duplicate of|"
        r"this is handled (?:by|in)|supersed|replaced by|no longer needed|"
        r"fixed (?:in|by) (?:\d|#|https)", re.I)),
    ("access", "access friction", re.compile(
        r"only have read access|don'?t have (?:access|permission)|"
        r"need(?:s)? (?:access|permission|credentials)|request access", re.I)),
]

# Excluded after hand-reading: these produced noise, not claims.
#   "(we|i) tried"   -> matched "I tried to sign the CLA"
#   "in our case"    -> matched "+1 For our use case"
#   "instead (we|i)" -> matched design proposals and review chatter
# And 'Migrated to <url>' alone is mechanical boilerplate from repo splits; it
# inflated one repo's apparent rate from 2.2% to 23.8%.
BOILERPLATE = re.compile(
    r"^\s*(migrated to|superseded by|replaced by|closing (this|in favou?r))", re.I)


class ClaimExtractor(Protocol):
    def extract(self, body: str, url: str, version: str) -> list[Claim]: ...


class RuleExtractor:
    """Phrase-based. Cheap, runs anywhere, ~41-50% precision."""

    name = EXTRACTOR

    def extract(self, body: str, url: str, version: str) -> list[Claim]:
        body = (body or "").strip()
        if not body or BOILERPLATE.match(body):
            return []
        sha = hashlib.sha256(body.encode("utf-8", "ignore")).hexdigest()[:16]
        claims: list[Claim] = []
        seen: set[str] = set()
        for kind, label, pat in SIGNALS:
            m = pat.search(body)
            if not m or label in seen:
                continue
            seen.add(label)
            excerpt = self._around(body, m.start(), m.end())
            claims.append(Claim(
                layer=Layer.EXPERIENCE,
                kind=kind,  # type: ignore[arg-type]
                body=f"From a discussion ({label}): {excerpt}",
                evidence=Evidence(
                    source="github_discussion", ref=url, source_version=version,
                    content_hash=sha, excerpt=excerpt, extractor_version=EXTRACTOR,
                ),
                extractor=EXTRACTOR,
                # Low on purpose. Prose is not re-verified by anything, unlike a test.
                confidence=0.35,
                # Discussions carry personnel data -- absence, performance, hiring.
                # Until a model can judge that, layer-3 prose stays behind source
                # access rather than appearing on a redacted discoverable card.
                sensitive=True,
            ))
        return claims

    @staticmethod
    def _around(text: str, start: int, end: int, width: int = 170) -> str:
        a = max(0, start - width // 2)
        b = min(len(text), end + width)
        s = re.sub(r"\s+", " ", text[a:b]).strip()
        return ("..." if a > 0 else "") + s + ("..." if b < len(text) else "")


class ModelExtractor:
    """The real layer-3 extractor. Not implemented: needs a provider key.

    Contract when built: no tools, structured output only, every returned claim must
    carry an evidence excerpt that appears verbatim in the input, and it may abstain.
    Personnel data must be refused outright.
    """

    name = "discussions_model/0.0"

    def extract(self, body: str, url: str, version: str) -> list[Claim]:
        raise NotImplementedError(
            "ModelExtractor needs a provider key and a claim-accuracy gate first; "
            "see docs/design.md section 9. Use RuleExtractor until then."
        )


def extract_comments(
    comments: list[dict],
    extractor: ClaimExtractor | None = None,
    min_chars: int = 0,
) -> list[Claim]:
    """Run the funnel over raw GitHub comment dicts."""
    ex = extractor or RuleExtractor()
    out: list[Claim] = []
    for c in comments:
        body = c.get("body")
        login = (c.get("user") or {}).get("login")
        if not keep_comment(body, login, min_chars):
            continue
        out.extend(ex.extract(
            body,
            c.get("html_url") or c.get("url") or "",
            str(c.get("updated_at") or c.get("created_at") or ""),
        ))
    return out
