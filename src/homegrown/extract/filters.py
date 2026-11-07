"""Cheap, deterministic filters applied before anything else (no model calls).

Most comments in real repositories are written by bots, so dropping them first saves
everything downstream.
"""

from __future__ import annotations

import re

BOT_LOGIN = re.compile(r"\[bot\]$|-bot$|^bot-|dependabot|renovate|github-actions|"
                       r"cla-assistant|codecov|sonarcloud|netlify|vercel\[", re.I)

# Paths that are never a reusable unit.
NOISE_PATH = re.compile(
    r"(^|/)("
    r"test|tests|testdata|__tests__|spec|specs|"
    r"fixtures?|__fixtures__|mocks?|__mocks__|"
    r"examples?|example|samples?|demo|playground|"
    r"docs?|benchmarks?|bench|fuzz|"
    r"node_modules|vendor|third_party|dist|build|target|"
    r"\.devcontainer|\.github|\.config"
    r")(/|$)",
    re.I,
)

GENERATED = re.compile(
    r"(^|/)(generated|gen|_generated|\.gen)(/|$)|"
    r"\.(pb|pb2|_pb2|generated)\.(go|py|ts|js)$|"
    r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|go\.sum|Cargo\.lock)$",
    re.I,
)


def is_bot(login: str | None) -> bool:
    return bool(login and BOT_LOGIN.search(login))


def is_noise_path(path: str) -> bool:
    return bool(NOISE_PATH.search(path.replace("\\", "/") + "/"))


def is_generated(path: str) -> bool:
    return bool(GENERATED.search(path.replace("\\", "/")))


def keep_comment(body: str | None, login: str | None, min_chars: int = 0) -> bool:
    """Should this comment reach an extractor at all?

    `min_chars` is a parameter rather than a constant on purpose. Long comments held
    79% of the signal in popular repos but only 37% in obscure ones, so a hard-coded
    gate would silently discard most of the signal on quiet repos. Default 0 = keep
    everything; raise it only with a measurement for that corpus.
    """
    if not body or not body.strip():
        return False
    if is_bot(login):
        return False
    return len(body.strip()) >= min_chars
