"""The collection agent: decide whether a change is worth remembering, and store it.

Runs once per event: a sync pass over a changed repo (collector/sync.py), or a created
or updated documentation page (see adapters.py). Each event goes through:

  triage       no model. Drop bots, dependency bumps, lockfile/generated-only changes,
               trivially short pages. Most events should end here, cheaply.
  locate       which unit(s) does this touch? Changed paths -> nearest unit in the repo;
               a new manifest -> a new unit; a doc page -> a solved-problem page.
  decide       per unit: create, update, or ignore. Rules by default; an optional model
               decider (fails safe to the rules).
  extract      layer-1 (what it is), layer-2 (tests), layer-3 (discussion, docs, commit
               messages), every claim with evidence.
  update_map   rewrite the unit's one-line "what is this for" -- only when the change
               plausibly alters what it does; most commits skip this at no cost.
  attribute    who built it -> pseudonymous person -> team -> LOB -> manager.
  assess_stage dev / beta / prod / retired, inferred from evidence, unconfirmed.
  validate     every claim has evidence; personnel data is dropped.
  owner_gate   OPTIONAL (settings.owner_gate, default off). Off: new assets get
               settings.default_visibility and nobody is asked. On: interrupt() asks the
               owner about org-wide visibility and any deprecation. Either way the agent
               never applies a deprecation by itself.
  apply        one transaction: asset, claims, card, embeddings, contributors, stage,
               audit.

With a Postgres checkpointer (thread_id = event id), an event paused for its owner can
resume days later, in another process, from where it stopped.
"""

from __future__ import annotations

import functools
import hashlib
import operator
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .. import telemetry
from ..config import settings
from ..db import admin_conn
from ..embed import asset_text, embed_texts
from ..extract import tests_extractor as tx
from ..extract import units as units_mod
from ..extract.discussions import RuleExtractor
from ..extract.filters import is_bot, is_generated, is_noise_path
from ..models import Claim, Evidence, Layer, Unit
from ..store import rebuild_card, upsert_asset, write_claims, write_embedding
from . import directory
from .events import Event
from .stage import Stage, infer

ORG_WIDE = "org-wide"

_DEP_BUMP = re.compile(
    r"^(chore\(deps|build\(deps|fix\(deps|deps:|chore: (bump|update) |bump |update dependency|"
    r"update module |update .* digest|lock file maintenance)", re.I)
_LOCKFILES = re.compile(r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|go\.sum|"
                        r"Cargo\.lock|poetry\.lock|uv\.lock|\.github/)", re.I)
_SOLVED_SIGNAL = re.compile(
    r"\b(how (we|to)|we solved|solution|approach|decision|we decided|trade-?offs?|"
    r"postmortem|post-mortem|incident|runbook|troubleshoot|pitfall|lessons? learned|"
    r"architecture|design|migration guide|workaround|known issues?)\b", re.I)
_MEETING = re.compile(r"\b(meeting notes|agenda|attendees|1:1|standup|retro notes)\b",
                      re.I)
# Discussions and docs sometimes mention people's absence, hiring or performance. Claims
# that do are dropped.
_PERSONNEL = re.compile(
    r"\b(vacation|on leave|parental leave|sick|recruit\w*|hiring|interview(ing)?|"
    r"performance review|pip\b|salary|compensation|promotion|fired|laid off|layoff|"
    r"resign\w*)\b", re.I)


@dataclass
class Deps:
    """Everything the graph reaches outside itself. Injected, so tests stub it."""
    repos_roots: list[Path]
    repo_meta: Callable[[str], dict] = lambda full: {}            # archived, description
    releases: Callable[[str], list[dict]] = lambda full: []
    contributors: Callable[[str, str], list[dict]] = lambda full, path: []
    decider: Callable[[dict, list[dict]], list[dict]] | None = None
    # (name, description, readme) -> {"purpose", "confidence"}; None = use the LLM via
    # collector.purpose.write_line. Tests inject a stub.
    purpose_writer: Callable[[str, str | None, str], dict] | None = None
    # (title, body) -> {"keep", "kind", "reason"}; None = collector.pages.judge_page.
    # Any failure falls back to the keyword rule.
    page_judge: Callable[[str, str], dict] | None = None
    use_llm_pages: bool = True
    # None = settings.owner_gate. Tests of the approval flow pass True explicitly.
    owner_gate: bool | None = None
    # (full_name, ref) -> local dir, for repos with no local clone. Production passes
    # collector.remote.sparse_checkout: fetch tree + manifests + READMEs + touched tests
    # instead of mirroring every repo. None = local checkouts only.
    remote: Callable[[str, str], Path | None] | None = None
    # False in state-sync passes: contributors and stage of EXISTING assets are left to
    # the daily job (they cost GitHub API calls and change slowly). New assets still get
    # them at once.
    daily_fields: bool = True
    org: str = "grafana"
    extra: dict = field(default_factory=dict)

    def clone(self, repo: str) -> Path | None:
        for r in self.repos_roots:
            if (r / repo).is_dir():
                return r / repo
        return None

    def unit_dir(self, t: dict) -> Path:
        """The folder of a target unit, in its checkout."""
        root = Path(t["root"]) if t.get("root") else self.clone(t["repo"])
        return root if t["path"] == "." else root / t["path"]


class CollectState(TypedDict, total=False):
    event: dict
    event_id: str
    decision: str                 # ignore | process
    reason: str
    targets: list                 # [{unit_id, repo, path, kind, action, ...}]
    claims: dict                  # unit_id -> [claim dict]
    contributors: dict            # unit_id -> [{login, commits, first_at, last_at}]
    stages: dict                  # unit_id -> Stage dict
    purposes: dict                # unit_id -> {purpose, confidence, model, ref, readme}
    questions: list
    answers: dict
    log: Annotated[list, operator.add]
    result: dict


def _repo_of(ev: Event) -> tuple[str, str]:
    """(full_name, repo) for any event source."""
    ref = ev.object_ref
    if ev.source == "docs-markdown":
        full = "/".join(ref.removeprefix("docs:").split("/")[:2])
    elif ev.source == "github":
        full = ref
    else:
        full = ""
    return full, (full.split("/", 1)[1] if "/" in full else full)


def build_collector(deps: Deps, checkpointer=None):

    # ------------------------------------------------------------ triage (no model)
    def triage(state: CollectState) -> dict:
        ev = Event.model_validate(state["event"])
        why = None
        if ev.actor and is_bot(ev.actor):
            why = f"bot actor ({ev.actor})"
        elif ev.kind == "commit":
            subject = (ev.title or "").strip()
            paths = [f.path for f in ev.files]
            if _DEP_BUMP.search(subject):
                why = f"dependency bump: {subject[:60]}"
            elif paths and all(_LOCKFILES.search(p) or is_generated(p) for p in paths):
                why = "only lockfiles, CI config or generated files changed"
            elif not paths and not subject:
                why = "empty commit"
        elif ev.kind in ("page_created", "page_updated"):
            text = (ev.body or "").strip()
            if len(text) < 200:
                why = "page too short to hold a reusable solution"
            elif _MEETING.search((ev.title or "") + " " + text[:400]):
                why = "meeting notes"
        if why:
            return {"decision": "ignore", "reason": why,
                    "log": [{"node": "triage", "ignored": why}]}
        return {"decision": "process", "log": [{"node": "triage", "kept": True}]}

    # ------------------------------------------------------------ locate
    def locate(state: CollectState) -> dict:
        ev = Event.model_validate(state["event"])
        full, repo = _repo_of(ev)
        targets: list[dict] = []

        if ev.source in ("docs-markdown", "confluence", "notion"):
            text = f"{ev.title or ''}\n{ev.body or ''}"
            verdict, how = None, "rule"
            if deps.use_llm_pages:
                try:
                    from .pages import judge_page
                    verdict = (deps.page_judge or judge_page)(ev.title or "", ev.body or "")
                    how = "model"
                except Exception:          # model unavailable: fall back to the rule
                    verdict = None
            keep = (verdict["keep"] if verdict is not None
                    else bool(_SOLVED_SIGNAL.search(text[:6000])))
            if not keep:
                why = (f"page judged {verdict['kind']}: {verdict['reason'][:120]}"
                       if verdict is not None
                       else "page does not describe a solution, decision or pitfall")
                return {"decision": "ignore", "reason": why,
                        "log": [{"node": "locate", "ignored": why, "by": how}]}
            targets.append({"unit_id": f"page:{ev.object_ref}", "repo": repo or ev.source,
                            "full": full, "path": ev.object_ref, "kind": "solved_problem",
                            "name": (ev.title or ev.object_ref)[:120]})
            return {"targets": targets, "log": [{"node": "locate", "page": ev.object_ref}]}

        root = deps.clone(repo)
        if root is None and deps.remote is not None and full:
            ref = ev.version if re.fullmatch(r"[0-9a-f]{7,40}", ev.version or "") else "HEAD"
            root = deps.remote(full, ref)
        if root is None:
            return {"decision": "ignore", "reason": f"no checkout of {repo} available",
                    "log": [{"node": "locate", "ignored": "no checkout"}]}
        found = units_mod.detect(root, repo)
        unit_paths = sorted({u.path for u, _ in found}, key=len, reverse=True)
        added_manifests = {str(Path(f.path).parent.as_posix()) or "."
                           for f in ev.files if f.status == "added"
                           and Path(f.path).name in units_mod.MANIFESTS}
        hit: set[str] = set(unit_paths) if ev.kind == "reconcile" else set()
        for f in ev.files:
            p = f.path.replace("\\", "/")
            if _LOCKFILES.search(p) or is_generated(p):
                continue
            owner = next((u for u in unit_paths if u == "." or p.startswith(u + "/")
                          or p == u), None)
            if owner is not None and not (owner != "." and is_noise_path(owner)):
                hit.add(owner)
        for u, _ in found:
            if u.path in hit or u.path in added_manifests:
                targets.append({"unit_id": u.unit_id, "repo": repo, "full": full,
                                "path": u.path, "kind": u.kind, "name": u.name,
                                "root": str(root)})
        if not targets:
            return {"decision": "ignore", "reason": "no reusable unit touched",
                    "log": [{"node": "locate", "ignored": "no unit"}]}
        return {"targets": targets,
                "log": [{"node": "locate", "units": [t["unit_id"] for t in targets]}]}

    # ------------------------------------------------------------ decide
    def decide(state: CollectState) -> dict:
        targets = [dict(t) for t in state["targets"]]
        with admin_conn() as conn, conn.cursor() as cur:
            for t in targets:
                cur.execute("select id from asset where unit_id = %s", (t["unit_id"],))
                t["action"] = "update" if cur.fetchone() else "create"
        if deps.decider is not None:
            try:      # model decider may veto or reword; fails safe to the rules
                for t, d in zip(targets, deps.decider(state["event"], targets), strict=True):
                    if d.get("action") in ("create", "update", "ignore"):
                        t["action"], t["why"] = d["action"], d.get("reason", "")
            except Exception as e:
                for t in targets:
                    t["why"] = f"model decider unavailable ({type(e).__name__}); rules"
        kept = [t for t in targets if t["action"] != "ignore"]
        if not kept:
            return {"decision": "ignore", "reason": "decider: nothing worth storing",
                    "targets": [], "log": [{"node": "decide", "ignored": True}]}
        return {"targets": kept,
                "log": [{"node": "decide",
                         "actions": {t["unit_id"]: t["action"] for t in kept}}]}

    # ------------------------------------------------------------ extract
    def extract(state: CollectState) -> dict:
        ev = Event.model_validate(state["event"])
        out: dict[str, list] = {}
        version = ev.version
        for t in state["targets"]:
            claims: list[Claim] = []
            if t["kind"] == "solved_problem":
                body = ev.body or ""
                first = next((ln.strip() for ln in body.splitlines()
                              if len(ln.strip()) > 40 and not ln.startswith("#")), "")
                if first:
                    claims.append(Claim(
                        layer=Layer.ARTIFACT, kind="capability", body=first[:300],
                        evidence=Evidence(source="github_code" if ev.source ==
                                          "docs-markdown" else "confluence",
                                          ref=ev.url or ev.object_ref, source_version=version,
                                          content_hash=version, excerpt=first[:300],
                                          extractor_version="collector/page/0.1"),
                        extractor="collector/page/0.1", confidence=0.6))
                for c in RuleExtractor().extract(body, ev.url or ev.object_ref, version):
                    claims.append(c)
            else:
                root = (Path(t["root"]) if t.get("root") else deps.clone(t["repo"]))
                meta = deps.repo_meta(t["full"]) or {}
                detected = {u.unit_id: (u, cl) for u, cl in units_mod.detect(
                    root, t["repo"], commit=version,
                    repo_description=meta.get("description"))}
                if t["unit_id"] in detected:
                    unit, l1 = detected[t["unit_id"]]
                    t["summary"], t["name"], t["kind"] = unit.summary, unit.name, unit.kind
                    t["aliases"] = unit.aliases
                    claims.extend(l1)
                for _, cl in tx.extract_repo(deps.unit_dir(t), commit=version).items():
                    claims.extend(cl)
                # A commit message can itself be experience: "fix X breaking above 1MB".
                if ev.kind == "commit" and ev.body:
                    claims.extend(RuleExtractor().extract(ev.body, ev.url or ev.version,
                                                          version))
            out[t["unit_id"]] = [c.model_dump(mode="json") for c in claims]
        return {"claims": out,
                "log": [{"node": "extract", "claims": {k: len(v) for k, v in out.items()}}]}

    # ------------------------------------------------------------ update_map
    def update_map(state: CollectState) -> dict:
        """Rewrite a unit's purpose line only when the change plausibly alters what the
        unit DOES (README / manifest / docs touched, a new unit, or an explicit
        regeneration). Most commits do not, and pay nothing here."""
        from . import purpose as P
        ev = Event.model_validate(state["event"])
        paths = [f.path for f in ev.files]
        regen = ev.kind == "reconcile" and bool(ev.extra.get("regenerate"))
        out, skipped, failed = {}, 0, 0
        for t in state["targets"]:
            if t["kind"] == "solved_problem":
                continue
            with admin_conn() as conn, conn.cursor() as cur:
                cur.execute("select purpose from asset where unit_id = %s", (t["unit_id"],))
                r = cur.fetchone()
            has_line = bool(r and r[0])
            if not P.needs_refresh(paths, t["path"], has_line, regenerate=regen):
                skipped += 1
                continue
            readme, rname = P.readme_excerpt(deps.unit_dir(t))
            try:
                writer = deps.purpose_writer or P.write_line
                line = writer(t["name"], t.get("summary"), readme)
                from ..llm import resolve_model
                model = "stub" if deps.purpose_writer else resolve_model()
            except Exception as e:      # keep the previous line; never block the event
                failed += 1
                t["purpose_error"] = f"{type(e).__name__}"
                continue
            ref = f"{t['repo']}/{t['path']}/{rname}".replace("/./", "/") if rname else None
            out[t["unit_id"]] = {**line, "model": model, "ref": ref, "readme": readme}
        return {"purposes": out,
                "log": [{"node": "update_map", "written": len(out), "unchanged": skipped,
                         "failed": failed}]}

    # ------------------------------------------------------------ attribute
    def attribute(state: CollectState) -> dict:
        ev = Event.model_validate(state["event"])
        out: dict[str, list] = {}
        for t in state["targets"]:
            if not deps.daily_fields and t.get("action") != "create":
                continue            # refreshed by the daily job, not on every sync pass
            people = [] if t["kind"] == "solved_problem" else \
                deps.contributors(t["full"], t["path"])
            people = [dict(p) for p in people]
            if ev.actor and not is_bot(ev.actor):
                me = next((p for p in people if p["login"] == ev.actor), None)
                if me is None:
                    people.append({"login": ev.actor, "commits": 1,
                                   "first_at": ev.occurred_at, "last_at": ev.occurred_at})
            out[t["unit_id"]] = people[:10]
        return {"contributors": out,
                "log": [{"node": "attribute", "people": {k: len(v) for k, v in out.items()}}]}

    # ------------------------------------------------------------ assess_stage
    def assess_stage(state: CollectState) -> dict:
        ev = Event.model_validate(state["event"])
        out = {}
        for t in state["targets"]:
            if not deps.daily_fields and t.get("action") != "create":
                continue            # refreshed by the daily job, not on every sync pass
            if t["kind"] == "solved_problem":
                out[t["unit_id"]] = Stage(stage="unknown", confidence=0.0,
                                          reasons=["documentation page"]).model_dump()
                continue
            meta = deps.repo_meta(t["full"]) or {}
            unit_dir = deps.unit_dir(t)
            readme = ""
            for f in sorted(unit_dir.glob("[Rr][Ee][Aa][Dd][Mm][Ee]*")):
                readme = f.read_text(encoding="utf-8", errors="ignore")[:6000]
                break
            version = None
            pj = unit_dir / "package.json"
            if pj.is_file():
                m = re.search(r'"version"\s*:\s*"([^"]+)"',
                              pj.read_text(encoding="utf-8", errors="ignore"))
                version = m.group(1) if m else None
            st = infer(archived=bool(meta.get("archived")),
                       releases=deps.releases(t["full"]) if t["path"] == "." else [],
                       manifest_version=version, readme=readme,
                       change_text=(ev.body or "") if ev.kind == "commit" else "")
            out[t["unit_id"]] = st.model_dump()
        return {"stages": out,
                "log": [{"node": "assess_stage",
                         "stages": {k: v["stage"] for k, v in out.items()}}]}

    # ------------------------------------------------------------ validate
    def validate(state: CollectState) -> dict:
        dropped = {"no_evidence": 0, "personnel": 0}
        out = {}
        for uid, claims in state["claims"].items():
            keep = []
            for c in claims:
                ev = c.get("evidence") or {}
                if not ev.get("ref") or not ev.get("source_version"):
                    dropped["no_evidence"] += 1
                    continue
                if _PERSONNEL.search(c.get("body", "")):
                    dropped["personnel"] += 1
                    continue
                keep.append(c)
            out[uid] = keep[:250]
        return {"claims": out, "log": [{"node": "validate", "dropped": dropped}]}

    # ------------------------------------------------------------ owner_gate
    def owner_gate(state: CollectState) -> dict:
        gate_on = settings.owner_gate if deps.owner_gate is None else deps.owner_gate
        if not gate_on:
            # Nobody is asked. New assets take the configured default visibility; a
            # deprecation proposal gets no "confirm", so apply() never applies it.
            auto = {f"{t['unit_id']}:visibility": settings.default_visibility
                    for t in state["targets"] if t["action"] == "create"}
            return {"questions": [], "answers": auto,
                    "log": [{"node": "owner_gate", "skipped": "owner_gate disabled",
                             "auto": auto}]}
        questions = []
        for t in state["targets"]:
            if t["action"] == "create":
                questions.append({"unit_id": t["unit_id"], "ask": "visibility",
                                  "question": f"New asset '{t['name']}'. Make its "
                                              "redacted card discoverable org-wide, or "
                                              "keep it to your team?",
                                  "options": ["org-wide", "team-only"]})
            st = state["stages"].get(t["unit_id"], {})
            if st.get("needs_owner"):
                questions.append({"unit_id": t["unit_id"], "ask": "stage",
                                  "question": f"Mark '{t['name']}' as "
                                              f"{st.get('proposed')}? "
                                              + "; ".join(st.get("reasons", [])[-1:]),
                                  "options": ["confirm", "reject"]})
        if not questions:
            return {"questions": [], "answers": {}}
        # Deterministic up to here: on resume this node re-runs and interrupt() returns
        # the owner's answers instead of pausing again.
        answers = interrupt({"event": state["event"].get("object_ref"),
                             "questions": questions})
        return {"questions": questions, "answers": answers or {},
                "log": [{"node": "owner_gate", "asked": len(questions),
                         "answers": answers}]}

    # ------------------------------------------------------------ apply
    def apply(state: CollectState) -> dict:
        ev = Event.model_validate(state["event"])
        answers = state.get("answers", {}) or {}
        written = {}
        with admin_conn() as conn:
            org_team = directory.org_wide_team(conn, ORG_WIDE)
            emb_rows = []
            for t in state["targets"]:
                team_name = f"team-{t['repo']}"
                team_id = directory.ensure_team_in_lob(conn, team_name, t["repo"])
                vis = answers.get(f"{t['unit_id']}:visibility")
                with conn.cursor() as cur:
                    cur.execute("select discovery_scope from asset where unit_id = %s",
                                (t["unit_id"],))
                    r = cur.fetchone()
                scope = org_team if vis == "org-wide" else \
                    (r[0] if r and vis is None else team_id)
                unit = Unit(unit_id=t["unit_id"], repo=t["repo"], path=t["path"],
                            kind=t["kind"], name=t["name"],
                            summary=t.get("summary") or (
                                (state["claims"].get(t["unit_id"]) or [{}])[0].get("body")
                                if t["kind"] == "solved_problem" else None),
                            aliases=t.get("aliases", []))
                aid = upsert_asset(conn, unit, owner_team=team_id, discovery_scope=scope)
                claims = [Claim.model_validate(c) for c in state["claims"].get(t["unit_id"], [])]
                n = write_claims(conn, aid, claims)

                # who built it
                people = []
                with conn.cursor() as cur:
                    for i, p in enumerate(state["contributors"].get(t["unit_id"], [])):
                        pid = directory.resolve(conn, ev.source if ev.source != "docs-markdown"
                                                else "github", p["login"], team_id)
                        role = "maintainer" if i == 0 else "contributor"
                        cur.execute(
                            "insert into asset_contributor (asset_id, person_id, role, "
                            "commits, first_at, last_at) values (%s,%s,%s,%s,%s,%s) "
                            "on conflict (asset_id, person_id) do update set "
                            "commits = greatest(asset_contributor.commits, excluded.commits),"
                            " last_at = greatest(asset_contributor.last_at, excluded.last_at)",
                            (aid, pid, role, int(p.get("commits", 1)), p.get("first_at"),
                             p.get("last_at")))
                        people.append(str(pid))

                # stage: deprecation only if the owner confirmed it. Absent when this
                # pass skipped it (sync mode, existing asset): keep the stored stage.
                st = state["stages"].get(t["unit_id"])
                with conn.cursor() as cur:
                    if st is None:
                        cur.execute("select stage from asset where id = %s", (aid,))
                        stage = cur.fetchone()[0]
                    else:
                        stage = st.get("stage", "unknown")
                        confirmed = False
                        if st.get("needs_owner"):
                            if answers.get(f"{t['unit_id']}:stage") == "confirm":
                                stage, confirmed = st.get("proposed", stage), True
                        cur.execute(
                            "insert into evidence (source, ref, source_version, content_hash, "
                            "excerpt, extractor_version) values (%s,%s,%s,%s,%s,%s) "
                            "returning id",
                            ("github_code", ev.url or ev.object_ref, ev.version, ev.version,
                             "; ".join(st.get("reasons", []))[:500], "collector/stage/0.1"))
                        sev = cur.fetchone()[0]
                        cur.execute(
                            "update asset set stage = %s, stage_confidence = %s, "
                            "stage_evidence_id = %s, stage_confirmed = %s where id = %s",
                            (stage, st.get("confidence"), sev, confirmed, aid))
                    cur.execute(
                        "insert into audit (action, target, reason) values (%s,%s,%s)",
                        (f"collector:{t['action']}", aid,
                         f"{ev.source}:{ev.kind} {ev.version[:12]} -> {n} new claims"))
                pl = (state.get("purposes") or {}).get(t["unit_id"])
                if pl:
                    from .purpose import store as store_purpose
                    store_purpose(conn, aid, pl, pl["model"], pl["ref"], pl["readme"])
                rebuild_card(conn, aid)
                emb_rows.append((aid, unit, claims))
                written[t["unit_id"]] = {"asset_id": str(aid), "action": t["action"],
                                         "new_claims": n, "contributors": len(people),
                                         "stage": stage, "scope":
                                         "org-wide" if scope == org_team else "team-only"}
            if emb_rows:
                _embed_changed(conn, emb_rows)
        return {"result": written, "log": [{"node": "apply", "written": written}]}

    # ------------------------------------------------------------ wiring
    def after(state: CollectState, nxt: str) -> str:
        return END if state.get("decision") == "ignore" else nxt

    g = StateGraph(CollectState)
    for name, fn in (("triage", triage), ("locate", locate), ("decide", decide),
                     ("extract", extract), ("update_map", update_map),
                     ("attribute", attribute),
                     ("assess_stage", assess_stage), ("validate", validate),
                     ("owner_gate", owner_gate), ("apply", apply)):
        g.add_node(name, _traced(name, fn))
    g.add_edge(START, "triage")
    g.add_conditional_edges("triage", lambda s: after(s, "locate"), ["locate", END])
    g.add_conditional_edges("locate", lambda s: after(s, "decide"), ["decide", END])
    g.add_conditional_edges("decide", lambda s: after(s, "extract"), ["extract", END])
    g.add_edge("extract", "update_map")
    g.add_edge("update_map", "attribute")
    g.add_edge("attribute", "assess_stage")
    g.add_edge("assess_stage", "validate")
    g.add_edge("validate", "owner_gate")
    g.add_edge("owner_gate", "apply")
    g.add_edge("apply", END)
    return g.compile(checkpointer=checkpointer)


def _traced(name: str, fn):
    """Run a node in a span with counts only: targets, claims and the decision."""
    @functools.wraps(fn)
    def node(state: CollectState) -> dict:
        with telemetry.span(f"collect.{name}") as sp:
            out = fn(state)
            attrs = {"homegrown.targets": len(out.get("targets", state.get("targets"))
                                              or [])}
            if "claims" in out:
                attrs["homegrown.claims"] = sum(len(v) for v in out["claims"].values())
            if "decision" in out:
                attrs["homegrown.decision"] = out["decision"]
            sp.set_attributes(attrs)
            return out
    return node


def _embed_changed(conn, rows: list[tuple]) -> None:
    """Write the card and summary embeddings for [(asset_id, unit, claims)], skipping
    any whose text is unchanged (a hash of the text is stored with each vector).
    Embedding is the slowest step of a pass, so this matters."""
    cards = [asset_text(u.name, u.summary, u.aliases, [c.body for c in cl])
             for _, u, cl in rows]
    summs = [f"{u.name.rsplit('/', 1)[-1]}: {u.summary}" if u.summary else u.name
             for _, u, _ in rows]
    for field_name, texts in (("card", cards), ("summary", summs)):
        todo = []
        with conn.cursor() as cur:
            for (aid, _, _), txt in zip(rows, texts, strict=True):
                h = hashlib.sha256(txt.encode("utf-8")).hexdigest()[:32]
                cur.execute("select text_hash from embedding where asset_id = %s "
                            "and field = %s and model = %s",
                            (aid, field_name, settings.embed_model))
                row = cur.fetchone()
                if not row or row[0] != h:
                    todo.append((aid, txt, h))
        if todo:
            vecs = embed_texts([txt for _, txt, _ in todo])
            for (aid, _, h), v in zip(todo, vecs, strict=True):
                write_embedding(conn, aid, field_name, settings.embed_model, v, text_hash=h)


def resume_event(graph, event_id: str, answers: dict) -> dict[str, Any]:
    """Answer an owner's pending questions and finish the event -- from any process,
    days later. answers: {"<unit_id>:visibility": "org-wide" | "team-only",
                          "<unit_id>:stage": "confirm" | "reject"}"""
    from langgraph.types import Command

    cfg = {"configurable": {"thread_id": f"collect-{event_id}"}}
    out = graph.invoke(Command(resume=answers), cfg)
    if out.get("__interrupt__"):
        return {"status": "pending_owner",
                "questions": out["__interrupt__"][0].value["questions"]}
    return {"status": "applied", "result": out.get("result")}


def run_event(graph, event: Event, event_id: str, owner_policy: Callable | None = None,
              ) -> dict[str, Any]:
    """Run one event to completion, or to a pause awaiting its owner.

    owner_policy(questions) -> answers lets a demo or test answer immediately; in
    production it is None and the event waits in 'pending_owner' until answered.
    """
    from langgraph.types import Command

    cfg = {"configurable": {"thread_id": f"collect-{event_id}"}}
    # With the owner gate off nothing pauses, so save the state once at the end instead
    # of after every node.
    kw = {}
    if getattr(graph, "checkpointer", None):
        kw["durability"] = "sync" if settings.owner_gate else "exit"
    out = graph.invoke({"event": event.model_dump(mode="json"), "event_id": event_id,
                        "log": []}, cfg, **kw)
    while out.get("__interrupt__"):
        qs = out["__interrupt__"][0].value["questions"]
        if owner_policy is None:
            return {"status": "pending_owner", "questions": qs}
        answers = owner_policy(qs)
        out = graph.invoke(Command(resume=answers), cfg)
    if out.get("decision") == "ignore":
        return {"status": "ignored", "reason": out.get("reason"), "log": out.get("log")}
    return {"status": "applied", "result": out.get("result"), "log": out.get("log")}
