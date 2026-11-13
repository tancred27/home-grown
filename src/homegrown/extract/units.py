"""Layer 1: what exists. Deterministic, no model calls.

Finds units (packages, modules, services) from their manifest files. It indexes
broadly; deciding whether a unit fits a request happens at question time.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from pathlib import Path

from ..models import Claim, Evidence, Layer, Unit
from .filters import is_noise_path

EXTRACTOR = "units/0.1"

MANIFESTS = {
    "package.json": "npm", "Cargo.toml": "cargo", "pyproject.toml": "py",
    "setup.py": "py", "go.mod": "go", "pom.xml": "mvn",
    "build.gradle": "gradle", "Chart.yaml": "helm",
}
SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", "target",
             "__pycache__", ".venv", "venv", ".yarn", "third_party"}

# Rough signals for what kind of thing a unit is.
SERVICE_HINT = re.compile(r"(^|/)(cmd|server|service|api|daemon|worker)(/|$)", re.I)


def _read_manifest(d: Path) -> tuple[str | None, str | None, list[str]]:
    """(name, summary, ecosystems) from whatever manifests are present."""
    name = summary = None
    eco: list[str] = []

    p = d / "package.json"
    if p.is_file():
        eco.append("npm")
        try:
            j = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
            name = j.get("name") or name
            summary = j.get("description") or summary
        except (OSError, json.JSONDecodeError):
            pass

    p = d / "Cargo.toml"
    if p.is_file():
        eco.append("cargo")
        try:
            pkg = tomllib.loads(p.read_text(encoding="utf-8", errors="ignore")).get("package", {})
            name = pkg.get("name") or name
            summary = pkg.get("description") or summary
        except (OSError, tomllib.TOMLDecodeError):
            pass

    p = d / "pyproject.toml"
    if p.is_file():
        eco.append("py")
        try:
            j = tomllib.loads(p.read_text(encoding="utf-8", errors="ignore"))
            pkg = j.get("project") or j.get("tool", {}).get("poetry", {}) or {}
            name = pkg.get("name") or name
            summary = pkg.get("description") or summary
        except (OSError, tomllib.TOMLDecodeError):
            pass

    p = d / "go.mod"
    if p.is_file():
        eco.append("go")
        try:
            first = p.read_text(encoding="utf-8", errors="ignore").splitlines()[:1]
            if first and first[0].startswith("module "):
                name = name or first[0].split(None, 1)[1].strip()
        except (OSError, IndexError):
            pass

    # A manifest may inherit a field (`description.workspace = true` in Cargo, a dynamic
    # field elsewhere): the value is then a table, not text, and tells us nothing here.
    name = name if isinstance(name, str) else None
    summary = summary if isinstance(summary, str) else None
    return name, summary, eco


# Lines that look like prose but say nothing about what the thing does. Gate 1 showed
# the old "first non-heading line" rule returning the repo's own title
# ("carbon-relay-ng"), a markdown admonition ("> [!NOTE]") and an experimental-status
# notice -- and no retrieval method can find a thing described by its own name.
_BADGE = re.compile(r"^\s*(\[!\[|!\[|\[\!\[|<img|<p|<div|<a\s|</)", re.I)
_ADMONITION = re.compile(r"^\s*>\s*(\[!\w+\])?", re.I)
_NOISE_LINE = re.compile(
    r"^\s*(\||---|===|```|<!--|\d+\.\s|[-*+]\s|table of contents|toc\b"
    r"|installation\b|usage\b|license\b|contributing\b|getting started\b"
    r"|status:|note:|warning:|deprecated:)", re.I)
_STATUS_NOTICE = re.compile(
    r"\b(experimental|work in progress|wip|unmaintained|archived|do not use|"
    r"still in|may break|under development|pre-?release|unstable)\b", re.I)


def _looks_like_name(line: str, *candidates: str | None) -> bool:
    """Is this line just the project's own name (possibly slugified)?"""
    norm = re.sub(r"[^a-z0-9]+", "", line.lower())
    if len(norm) < 3:
        return True
    for c in candidates:
        if c and norm == re.sub(r"[^a-z0-9]+", "", c.lower()):
            return True
    return False


def _readme(d: Path, *names: str | None) -> tuple[str | None, str | None]:
    """(filename, a line that actually describes the thing).

    Scans the first ~60 lines and returns the first that survives every filter, rather
    than the first non-heading line.
    """
    for f in sorted(d.iterdir()) if d.is_dir() else []:
        if not (f.is_file() and f.name.lower().startswith("readme")):
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return f.name, None
        for line in text.splitlines()[:60]:
            s = line.strip()
            if not s or s.startswith(("#", "=")):
                continue
            if _BADGE.match(s) or _ADMONITION.match(s) or _NOISE_LINE.match(s):
                continue
            s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)      # unwrap md links
            s = re.sub(r"[`*_]", "", s).strip()
            if len(s) < 40 or " " not in s:
                continue
            if _looks_like_name(s, *names):
                continue
            if _STATUS_NOTICE.search(s) and len(s) < 160:
                continue      # a status banner, not a description
            return f.name, s[:400]
        return f.name, None
    return None, None


def detect(repo_root: Path, repo: str, commit: str = "HEAD",
           repo_description: str | None = None) -> list[tuple[Unit, list[Claim]]]:
    """Find units in a repo and the layer-1 claims that come free with them.

    `repo_description` is the source system's own one-line description (for GitHub, the
    repo's `description` field). It is human-written, purpose-focused and almost always
    better than anything scraped out of a README, so it wins for the repo-level unit.
    Sub-packages keep their manifest description, which is more specific to them.
    """
    found: dict[str, set[str]] = {}
    for d, dirs, files in __import__("os").walk(repo_root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        rel = Path(d).relative_to(repo_root).as_posix()
        for f in files:
            if f in MANIFESTS:
                found.setdefault(rel, set()).add(MANIFESTS[f])
            elif f.endswith(".tf"):
                found.setdefault(rel, set()).add("tf")

    out: list[tuple[Unit, list[Claim]]] = []
    for rel, ecos in sorted(found.items()):
        if rel != "." and is_noise_path(rel):
            continue
        d = repo_root / rel if rel != "." else repo_root
        name, summary, eco2 = _read_manifest(d)
        readme_name, readme_line = _readme(d, name, repo, rel.rsplit("/", 1)[-1])
        name = name or (repo if rel == "." else rel.rsplit("/", 1)[-1])

        # A manifest description that is just the package name tells us nothing.
        if summary and _looks_like_name(summary, name, repo):
            summary = None
        # Order of preference, best first.
        if rel == ".":
            summary = summary or repo_description or readme_line
        else:
            summary = summary or readme_line or repo_description

        kind = "service" if SERVICE_HINT.search(rel) else "library"
        unit = Unit(
            unit_id=f"github:{repo}:{rel}",
            repo=repo, path=rel, kind=kind, name=name, summary=summary,
            aliases=sorted({name, rel.rsplit("/", 1)[-1]} - {""}),
            ecosystems=sorted(set(ecos) | set(eco2)),
        )

        claims: list[Claim] = []
        if summary:
            src = f"{rel}/{readme_name}" if readme_name and not name else f"{rel}/manifest"
            claims.append(Claim(
                layer=Layer.ARTIFACT,
                kind="capability",
                body=summary,
                evidence=Evidence(
                    source="github_code", ref=src.lstrip("./"), source_version=commit,
                    content_hash=hashlib.sha256(summary.encode()).hexdigest()[:16],
                    excerpt=summary[:300], extractor_version=EXTRACTOR,
                ),
                extractor=EXTRACTOR, confidence=0.6,
            ))
        out.append((unit, claims))
    return out
