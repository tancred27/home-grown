"""Layer 1: what exists. Deterministic, no model calls.

Manifest-based unit detection. Measured at 96-100% recall against declared workspace
members across 10 public repos (experiments/RESULTS.md run A). The hard part was never
finding units -- babel yields 163 of them -- it was deciding which deserve a card.
That judgment was removed from ingestion entirely and moved into the answer, where it
can be labelled and measured. We index broadly and let the verdict discriminate.
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

    return name, summary, eco


def _readme(d: Path) -> tuple[str | None, str | None]:
    """(filename, first meaningful paragraph)."""
    for f in sorted(d.iterdir()) if d.is_dir() else []:
        if f.is_file() and f.name.lower().startswith("readme"):
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                return f.name, None
            for line in text.splitlines():
                s = line.strip()
                if s and not s.startswith(("#", "!", "[!", "<", "---", "=")):
                    return f.name, s[:400]
            return f.name, None
    return None, None


def detect(repo_root: Path, repo: str, commit: str = "HEAD") -> list[tuple[Unit, list[Claim]]]:
    """Find units in a repo and the layer-1 claims that come free with them."""
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
        readme_name, readme_line = _readme(d)
        name = name or (repo if rel == "." else rel.rsplit("/", 1)[-1])
        summary = summary or readme_line

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
