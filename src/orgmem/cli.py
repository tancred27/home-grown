"""`orgmem find` -- the same find_existing, from a terminal or any coding agent's shell.

The design (section 4) keeps REST/CLI/MCP as thin routes onto one function, so gate 2
can measure which route agents actually use. Every call is logged to the `question`
table with route='cli' (adherence is a number, not a hope).

Usage:
  orgmem find "turn request paths with ids into route templates" [-k cardinality,metrics]
              [--user gate2-dev] [--json]

Identity: the caller is the app_user named by --user or $ORGMEM_USER. A real deployment
gets this from the OAuth token (design section 8); the env var is the local stand-in.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .db import admin_conn, as_user
from .search import find_existing

ICON = {"use_as_is": "USE AS-IS", "extend": "EXTEND", "not_a_fit": "not a fit",
        "unknown": "unknown"}


def resolve_user(login: str) -> str:
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("select id from app_user where login = %s", (login,))
        row = cur.fetchone()
    if not row:
        raise SystemExit(f"unknown user {login!r}; pass --user or set ORGMEM_USER")
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="orgmem")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find", help="has someone here already built this?")
    f.add_argument("intent")
    f.add_argument("-k", "--keywords", default="",
                   help="comma-separated synonyms / other names for the thing")
    f.add_argument("--user", default=os.environ.get("ORGMEM_USER", "gate2-dev"))
    f.add_argument("--json", action="store_true")
    f.add_argument("--top", type=int, default=5)
    a = sub.add_parser("ask", help="has someone done X? who, which team/LOB, what stage?")
    a.add_argument("question")
    a.add_argument("-k", "--keywords", default="")
    a.add_argument("--user", default=os.environ.get("ORGMEM_USER", "gate2-dev"))
    a.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    user = resolve_user(args.user)
    if args.cmd == "ask":
        from .answer import ask
        kws = [k.strip() for k in args.keywords.split(",") if k.strip()]
        out = ask(user, args.question, kws)
        print(json.dumps(out, indent=1, default=str) if args.json else out["text"])
        return 0
    kws = [k.strip() for k in args.keywords.split(",") if k.strip()]
    verdicts = find_existing(user, args.intent, kws, top=args.top)
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
