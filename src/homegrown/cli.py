"""`homegrown find`: find_existing from a terminal or a coding agent's shell.

Usage:
  homegrown find "turn request paths with ids into route templates" [-k cardinality,metrics]
              [--user alice] [--json]
  homegrown ask "has anyone built a rate limiter?" [--user alice]
  homegrown add-user <login> [--team team-<repo> ...]

The CLI, the API and the MCP server all call the same function, and every call is
logged to the `question` table with its route.

Identity: the app_user named by --user or $HOMEGROWN_USER. A real deployment would
take it from the caller's sign-in token instead.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import telemetry
from .db import admin_conn, as_user
from .search import find_existing

ICON = {"use_as_is": "USE AS-IS", "extend": "EXTEND", "not_a_fit": "not a fit",
        "unknown": "unknown"}


def resolve_user(login: str | None) -> str:
    if not login:
        raise SystemExit("who is asking? pass --user or set HOMEGROWN_USER "
                         "(create one with: homegrown add-user <login>)")
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("select id from app_user where login = %s", (login,))
        row = cur.fetchone()
    if not row:
        raise SystemExit(f"unknown user {login!r}; create it with: homegrown add-user {login}")
    return str(row[0])


def log_question(user_id: str, intent: str, keywords: list[str], route: str,
                 verdicts) -> None:
    """Best-effort: a logging failure must never break the answer."""
    try:
        with as_user(user_id) as conn, conn.cursor() as cur:
            cur.execute(
                "insert into question (asker_id, intent, keywords, route, results) "
                "values (%s, %s, %s, %s, %s)",
                (user_id, intent, keywords, route, json.dumps(
                    [{"asset_id": v.asset_id, "verdict": v.verdict} for v in verdicts])))
            conn.commit()
    except Exception as e:  # pragma: no cover
        print(f"(question not logged: {type(e).__name__})", file=sys.stderr)


def add_user(login: str, teams: list[str]) -> None:
    """Create (or update) a user who can discover everything published org-wide, plus
    anything scoped to the named teams."""
    from .collector.directory import org_wide_team
    from .store import ensure_team, ensure_user, grant_membership

    with admin_conn() as conn:
        everyone = org_wide_team(conn)
        uid = ensure_user(conn, login, everyone)
        grant_membership(conn, uid, everyone)
        for name in teams:
            grant_membership(conn, uid, ensure_team(conn, name))
    print(f"user {login!r} ready: org-wide" + "".join(f", {t}" for t in teams))


def main(argv: list[str] | None = None) -> int:
    # Model output can contain characters a Windows console can't encode; show '?'.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(prog="homegrown")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find", help="has someone here already built this?")
    f.add_argument("intent")
    f.add_argument("-k", "--keywords", default="",
                   help="comma-separated synonyms / other names for the thing")
    f.add_argument("--user", default=os.environ.get("HOMEGROWN_USER"))
    f.add_argument("--json", action="store_true")
    f.add_argument("--top", type=int, default=5)
    a = sub.add_parser("ask", help="has someone done X? who, which team/LOB, what stage?")
    a.add_argument("question")
    a.add_argument("-k", "--keywords", default="")
    a.add_argument("--user", default=os.environ.get("HOMEGROWN_USER"))
    a.add_argument("--json", action="store_true")
    u = sub.add_parser("add-user", help="create a user who can ask questions")
    u.add_argument("login")
    u.add_argument("--team", action="append", default=[],
                   help="also see assets scoped to this team (repeatable)")
    args = ap.parse_args(argv)
    telemetry.init("cli")

    if args.cmd == "add-user":
        add_user(args.login, args.team)
        return 0
    user = resolve_user(args.user)
    if args.cmd == "ask":
        from .answer import ask
        kws = [k.strip() for k in args.keywords.split(",") if k.strip()]
        out = ask(user, args.question, kws, route="cli")
        print(json.dumps(out, indent=1, default=str) if args.json else out["text"])
        return 0
    kws = [k.strip() for k in args.keywords.split(",") if k.strip()]
    t0 = time.monotonic()
    verdicts = find_existing(user, args.intent, kws, top=args.top)
    telemetry.record_question("cli", "find", time.monotonic() - t0)
    log_question(user, args.intent, kws, "cli", verdicts)

    if args.json:
        print(json.dumps({"results": [v.model_dump() for v in verdicts]}, indent=1))
        return 0
    if not verdicts:
        print("Nothing found. Returning nothing is a valid answer.")
        return 0
    for i, v in enumerate(verdicts, 1):
        print(f"{i}. {v.name.rsplit('/', 1)[-1]}  [{ICON.get(v.verdict, v.verdict)}]")
        for r in v.reasons[:2]:
            print(f"     - {r['text'][:110]}")
        for u in v.unknowns[:1]:
            print(f"     ? {u[:110]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
