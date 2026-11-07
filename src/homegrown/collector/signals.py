"""Code-derived signals: what a unit is for, from things the code keeps current.

READMEs go stale. They are written for the first version, rarely updated, and often
missing in private repos. A purpose line written mainly from a README describes what the
code used to do. These signals change when the code changes:

  * directory and file names     (pkg/ring/, ratelimit/, outlier.rs)
  * the manifest                 (module name, the dependencies it actually imports)
  * test file names              (CI keeps the tests honest)
  * the primary language

`signal_text()` turns them into a compact block for the purpose-line writer. It is pure,
so the same function serves a local checkout (`local_signals`) and a remote file listing
from the GitHub API (scripts/fetch_code_signals.py).
"""

from __future__ import annotations

import json
import re
import tomllib
from collections import Counter
from pathlib import PurePosixPath

from ..extract.filters import is_generated, is_noise_path

MANIFESTS = ("go.mod", "package.json", "Cargo.toml", "pyproject.toml", "setup.py",
             "pom.xml", "build.gradle", "Chart.yaml")
_SKIP_PARTS = {".git", ".github", "node_modules", "vendor", "dist", "build", "target",
               "third_party", "testdata", "fixtures", "__pycache__", ".venv", "docs",
               "examples", "example", "assets", "static", "public", "img", "images"}
_SOURCE_EXT = {".go", ".py", ".rs", ".ts", ".tsx", ".js", ".jsx", ".java", ".kt", ".scala",
               ".rb", ".cs", ".cpp", ".cc", ".c", ".h", ".hpp", ".swift", ".php", ".tf",
               ".proto", ".sql", ".lua", ".ex", ".erl", ".clj"}
_TEST = re.compile(r"(_test\.|\.test\.|\.spec\.|(^|/)test_|(^|/)tests?/)", re.I)
_GENERIC = {"main", "index", "init", "__init__", "mod", "lib", "util", "utils", "helpers",
            "types", "constants", "config", "doc", "version", "errors", "setup"}


def _deps(manifest_name: str, text: str) -> tuple[str | None, list[str]]:
    """(module name, dependency names) from one manifest. Best effort, never raises."""
    name, deps = None, []
    try:
        if manifest_name == "go.mod":
            m = re.search(r"^module\s+(\S+)", text, re.M)
            name = m.group(1) if m else None
            for line in re.findall(r"^\s*([a-z0-9.\-]+/[^\s]+)\s+v[\d.]+[^\n]*$", text, re.M):
                if "// indirect" not in line:
                    deps.append("/".join(line.split()[0].split("/")[-2:]))
        elif manifest_name == "package.json":
            d = json.loads(text)
            name = d.get("name")
            deps = list(d.get("dependencies", {}))[:40]
        elif manifest_name == "Cargo.toml":
            d = tomllib.loads(text)
            name = (d.get("package") or {}).get("name")
            deps = list(d.get("dependencies", {}))
        elif manifest_name == "pyproject.toml":
            d = tomllib.loads(text)
            p = d.get("project") or {}
            name = p.get("name")
            deps = [re.split(r"[<>=!~\[; ]", x)[0] for x in p.get("dependencies", [])]
        elif manifest_name == "pom.xml":
            m = re.search(r"<artifactId>([^<]+)</artifactId>", text)
            name = m.group(1) if m else None
            deps = re.findall(r"<dependency>.*?<artifactId>([^<]+)</artifactId>", text, re.S)
        elif manifest_name == "Chart.yaml":
            m = re.search(r"^name:\s*(\S+)", text, re.M)
            name = m.group(1) if m else None
    except Exception:
        pass
    seen, out = set(), []
    for d in deps:
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return name, out[:30]


def signal_text(name: str, description: str | None, language: str | None,
                paths: list[str], unit_path: str = ".",
                manifest: tuple[str, str] | None = None) -> str:
    """Compact, code-derived description of one unit. `paths` are repo-relative files."""
    prefix = "" if unit_path in ("", ".") else unit_path.rstrip("/") + "/"
    rel = [p[len(prefix):] for p in paths if p.startswith(prefix)]
    rel = [p for p in rel if not is_noise_path(p) and not is_generated(p)
           and not (set(PurePosixPath(p).parts[:-1]) & _SKIP_PARTS)]

    dirs: Counter = Counter()
    sources, tests = [], []
    for p in rel:
        pp = PurePosixPath(p)
        for part in pp.parts[:-1][:3]:
            dirs[part] += 1
        if pp.suffix.lower() in _SOURCE_EXT:
            stem = pp.stem.lower()
            (tests if _TEST.search(p) else sources).append(stem)
    src = [s for s in dict.fromkeys(sources) if s not in _GENERIC]
    tst = list(dict.fromkeys(re.sub(r"(_test|\.test|\.spec|^test_)", "", t) for t in tests))

    lines = [f"name: {name}"]
    if description:
        lines.append(f"one-line description: {description.strip()[:300]}")
    if language:
        lines.append(f"language: {language}")
    if manifest:
        mod, deps = _deps(*manifest)
        if mod:
            lines.append(f"module: {mod}")
        if deps:
            lines.append("imports: " + ", ".join(deps))
    if dirs:
        lines.append("directories: " + ", ".join(d for d, _ in dirs.most_common(25)))
    if src:
        lines.append("source files: " + ", ".join(src[:40]))
    if tst:
        lines.append("tested behaviours (test files): " + ", ".join(tst[:15]))
    return "\n".join(lines)


def local_signals(repo_root, unit_path: str, name: str, description: str | None,
                  language: str | None = None) -> str:
    """signal_text() for a local checkout."""
    from pathlib import Path
    root = Path(repo_root)
    paths = [p.relative_to(root).as_posix() for p in root.rglob("*")
             if p.is_file() and ".git" not in p.parts][:20000]
    unit_dir = root if unit_path in ("", ".") else root / unit_path
    manifest = None
    for m in MANIFESTS:
        f = unit_dir / m
        if f.is_file():
            manifest = (m, f.read_text(encoding="utf-8", errors="ignore")[:20000])
            break
    return signal_text(name, description, language, paths, unit_path, manifest)
