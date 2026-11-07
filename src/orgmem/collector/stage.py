"""Is a solution in dev, beta or prod? Inferred from evidence, marked unconfirmed.

Code alone cannot prove something runs in production; a real deployment would read this
from its deploy system (Argo CD, a service catalog, Kubernetes). From public data the
signals are: the archived flag, releases and their version numbers, the manifest
version, and status notices in the README or commit messages. Every inference carries
its reasons and a confidence, and is stored as UNCONFIRMED until the owner confirms.

Deprecation is special: the design's rule is that the agent never deprecates anything on
its own. A deprecation signal is reported as `needs_owner`, never applied.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

_SEMVER = re.compile(r"v?(\d+)\.(\d+)(?:\.(\d+))?")
_DEV_NOTICE = re.compile(
    r"\b(experimental|alpha|work in progress|wip|proof of concept|poc|prototype|"
    r"not (yet )?production[- ]ready|early (stage|preview)|unstable|may break)\b", re.I)
_BETA_NOTICE = re.compile(r"\b(beta|preview|release candidate)\b", re.I)
_DEPRECATED = re.compile(r"\b(deprecated|deprecate|no longer maintained|unmaintained|"
                         r"end[- ]of[- ]life|superseded by|archived)\b", re.I)


class Stage(BaseModel):
    stage: str = "unknown"            # dev | beta | prod | deprecated | retired | unknown
    confidence: float = 0.0
    reasons: list[str] = Field(default_factory=list)
    needs_owner: bool = False         # a human must decide before this is applied
    proposed: str | None = None       # what needs_owner is asking to set


def _major(v: str | None) -> int | None:
    m = _SEMVER.search(v or "")
    return int(m.group(1)) if m else None


def infer(archived: bool = False, releases: list[dict] | None = None,
          manifest_version: str | None = None, readme: str = "",
          change_text: str = "", _check_deprecation: bool = True) -> Stage:
    """releases: [{"tag": "v1.2.0", "prerelease": False, "published_at": "..."}], newest first."""
    releases = releases or []
    if archived:
        return Stage(stage="retired", confidence=0.95,
                     reasons=["the repository is archived"])

    # Deprecation signals are reported, never applied.
    for text, where in ((change_text, "the change"), (readme[:3000], "the README")):
        m = _DEPRECATED.search(text or "") if _check_deprecation else None
        if m:
            # The stage underneath the deprecation signal. Must not re-check the same
            # README, or it recurses forever (found on kubernetes READMEs).
            base = infer(False, releases, manifest_version, readme, "",
                         _check_deprecation=False)
            base.needs_owner = True
            base.proposed = "deprecated"
            base.reasons.append(f"'{m.group(0)}' appears in {where}: owner must confirm")
            return base

    notice = _DEV_NOTICE.search(readme[:3000] or "")
    stable = [r for r in releases if not r.get("prerelease")]
    if stable:
        tag = stable[0]["tag"]
        major = _major(tag)
        if notice:
            return Stage(stage="dev", confidence=0.6,
                         reasons=[f"released ({tag}) but the README says "
                                  f"'{notice.group(0)}'"])
        if major is not None and major >= 1:
            return Stage(stage="prod", confidence=0.75,
                         reasons=[f"stable release {tag} (major version {major})",
                                  f"{len(stable)} stable release(s)"])
        return Stage(stage="beta", confidence=0.6,
                     reasons=[f"released, but still 0.x ({tag})"])
    if releases:
        return Stage(stage="beta", confidence=0.5,
                     reasons=[f"pre-releases only (latest {releases[0]['tag']})"])
    if notice:
        return Stage(stage="dev", confidence=0.6,
                     reasons=[f"no releases; the README says '{notice.group(0)}'"])
    if _BETA_NOTICE.search(readme[:3000] or ""):
        return Stage(stage="beta", confidence=0.45, reasons=["the README mentions beta"])
    major = _major(manifest_version)
    if major is not None:
        st = "beta" if major >= 1 else "dev"
        return Stage(stage=st, confidence=0.4,
                     reasons=[f"no releases; manifest version {manifest_version}"])
    return Stage(stage="dev", confidence=0.3,
                 reasons=["no releases, no version, no status notice"])
