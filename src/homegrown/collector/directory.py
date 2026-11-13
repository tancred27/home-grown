"""The org directory: people, teams, lines of business (LOB), managers.

In a real deployment this is synced from the company's directory (Workday, Okta, an
HR feed). Public repositories have none of it, so this module GENERATES one:

  * each repo's team is placed in an LOB by a fixed keyword mapping,
  * every team and every LOB gets a synthetic lead (the manager),
  * every source login becomes a PSEUDONYMOUS person -- handle `eng-<hash>` -- placed
    in the team of the repo they committed to, reporting to that team's lead.

The login itself is never stored: source_identity keeps sha256(login), and the app role
cannot read that table at all. So no real person's login is ever joined, anywhere the
app can see, to a made-up manager or LOB.
"""

from __future__ import annotations

import hashlib
import uuid

import psycopg

LOB_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Data Science", ("augurs",)),
    ("Developer Tools & Testing",
     ("k6", "xk6", "crocochrome", "dashboard-linter", "fake-data-gen", "flagger")),
    ("Plugins & Integrations",
     ("datasource", "business-", "clock-panel", "canvas", "panel")),
    ("Observability Data",
     ("loki", "tempo", "mimir", "pyroscope", "alloy", "faro", "carbon", "otel",
      "celery-exporter", "cloudcost", "github-traffic", "rate-limits")),
    ("Platform Engineering", ()),     # default
]


def lob_for_repo(repo: str) -> str:
    r = repo.lower()
    for lob, keys in LOB_RULES:
        if any(k in r for k in keys):
            return lob
    return "Platform Engineering"


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _person(cur, handle: str, display: str, title: str, team_id, manager_id) -> uuid.UUID:
    cur.execute(
        "insert into person (handle, display_name, title, team_id, manager_id) "
        "values (%s,%s,%s,%s,%s) on conflict (handle) do update set "
        "team_id = coalesce(person.team_id, excluded.team_id) returning id",
        (handle, display, title, team_id, manager_id))
    return cur.fetchone()[0]


def ensure_lob(conn: psycopg.Connection, name: str) -> uuid.UUID:
    with conn.cursor() as cur:
        cur.execute("insert into lob (name) values (%s) on conflict (name) do update "
                    "set name = excluded.name returning id", (name,))
        lob_id = cur.fetchone()[0]
        # The LOB head: a synthetic person at the top of that LOB.
        _person(cur, f"lob-head-{_hash(name)[:6]}", f"Head of {name}", "VP Engineering",
                None, None)
        return lob_id


def ensure_team_in_lob(conn: psycopg.Connection, team_name: str, repo: str) -> uuid.UUID:
    """Place a team in its LOB and give it a synthetic lead, who reports to the head."""
    with conn.cursor() as cur:
        lob_name = lob_for_repo(repo)
        lob_id = ensure_lob(conn, lob_name)
        cur.execute("insert into team (name, lob_id) values (%s,%s) on conflict (name) "
                    "do update set lob_id = coalesce(team.lob_id, excluded.lob_id) "
                    "returning id", (team_name, lob_id))
        team_id = cur.fetchone()[0]
        cur.execute("select id from person where handle = %s",
                    (f"lob-head-{_hash(lob_name)[:6]}",))
        head = cur.fetchone()[0]
        _person(cur, f"lead-{_hash(team_name)[:6]}", f"Lead, {team_name}",
                "Engineering Manager", team_id, head)
        return team_id


def org_wide_team(conn: psycopg.Connection, name: str = "org-wide") -> uuid.UUID:
    """The discovery scope for 'everyone may know this exists'. Not in any LOB; every
    user is a member, so a card scoped to it is discoverable org-wide -- while reading
    its details still requires source access."""
    with conn.cursor() as cur:
        cur.execute("insert into team (name) values (%s) on conflict (name) do update "
                    "set name = excluded.name returning id", (name,))
        tid = cur.fetchone()[0]
        cur.execute("insert into membership (user_id, group_id) select id, %s from "
                    "app_user on conflict do nothing", (tid,))
        return tid


def team_lead(conn: psycopg.Connection, team_id) -> uuid.UUID | None:
    with conn.cursor() as cur:
        cur.execute("select id from person where team_id = %s and handle like 'lead-%%'",
                    (team_id,))
        r = cur.fetchone()
        return r[0] if r else None


def identity_hash(source: str, value: str) -> str:
    """The one hash used for every identity: sha256 of '<source>:<value lower-cased>'."""
    return _hash(f"{source}:{value.lower()}")


def _usable_email(email: str | None) -> str | None:
    e = (email or "").strip()
    if not e or "@" not in e or e.lower().endswith("@users.noreply.github.com"):
        return None
    return e


def resolve(conn: psycopg.Connection, source: str, login: str, team_id,
            email: str | None = None) -> uuid.UUID:
    """Source login (and commit email, when known) -> person. A person from the company
    directory wins (the email is the strongest key); otherwise a pseudonymous person is
    created in the given team. Only hashes are stored."""
    h = _hash(f"{source}:{login.lower()}")
    email = _usable_email(email)
    eh = identity_hash("email", email) if email else None
    with conn.cursor() as cur:
        r = None
        if eh:
            cur.execute("select person_id from source_identity where source='email' and "
                        "login_hash=%s", (eh,))
            r = cur.fetchone()
        if not r:
            cur.execute("select person_id from source_identity where source=%s and "
                        "login_hash=%s", (source, h))
            r = cur.fetchone()
        if r:
            pid = r[0]
        else:
            lead = team_lead(conn, team_id)
            pid = _person(cur, f"eng-{h[:6]}", f"Engineer {h[:6]}", "Software Engineer",
                          team_id, lead)
            cur.execute("insert into source_identity (source, login_hash, person_id) "
                        "values (%s,%s,%s) on conflict do nothing", (source, h, pid))
        if eh:
            cur.execute("insert into source_identity (source, login_hash, person_id) "
                        "values ('email',%s,%s) on conflict do nothing", (eh, pid))
        return pid
