"""Shapes passed between extractors and the store.

A claim never exists without its evidence: they are constructed together, which is
how `claim.evidence_id NOT NULL` in the schema is upheld by construction rather than
by remembering to check.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Literal

from pydantic import BaseModel, Field


class Layer(IntEnum):
    """The three knowledge layers. They fail independently -- see docs/design.md 2.

    ARTIFACT is always available. BEHAVIOUR is nearly always available because CI
    enforces it. EXPERIENCE is scarce and uneven (~2.4 claims per repo, ever).
    """

    ARTIFACT = 1
    BEHAVIOUR = 2
    EXPERIENCE = 3


ClaimKind = Literal[
    "capability", "limit", "refusal", "scale", "pitfall", "outcome", "access", "lifecycle"
]

EvidenceSource = Literal[
    "github_code", "github_test", "github_discussion", "github_commit",
    "confluence", "elicited", "reported",
]


class Evidence(BaseModel):
    source: EvidenceSource
    ref: str                       # file path, test name, or comment URL
    source_version: str            # commit sha or page version
    content_hash: str
    excerpt: str | None = None
    extractor_version: str


class Claim(BaseModel):
    layer: Layer
    kind: ClaimKind
    body: str                      # one sentence, written to be read by a human
    evidence: Evidence
    extractor: str
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    sensitive: bool = False        # true => detail layer only, needs source access


class Unit(BaseModel):
    """A candidate thing worth a card, found before any model call."""

    unit_id: str                   # stable across renames
    repo: str
    path: str                      # relative to repo root; "." for repo-level
    kind: str
    name: str
    summary: str | None = None
    aliases: list[str] = Field(default_factory=list)
    ecosystems: list[str] = Field(default_factory=list)


class Verdict(BaseModel):
    """What the read path returns. `unknown` is a first-class answer."""

    asset_id: str
    name: str
    verdict: Literal["use_as_is", "extend", "not_a_fit", "unknown"]
    reasons: list[dict] = Field(default_factory=list)   # {text, claim_id, layer}
    unknowns: list[str] = Field(default_factory=list)
    score: float = 0.0
