"""The core product mechanic, demonstrated: an answer about code you cannot see.

Three states for the same query, same database:

  1. A developer on team X searches. They see only their own team's assets --
     enforced by row-level security, not by a WHERE clause.
  2. The same developer, after the owner makes a card DISCOVERABLE to them. They now
     learn the thing exists and what it does, WITHOUT read access to the code.
  3. With source access too, so the detail layer opens.

If step 2 returns nothing, the whole premise of the project is unimplemented.
"""

from __future__ import annotations

import sys

from homegrown.db import admin_conn, as_user
from homegrown.search import find_existing


def show(label: str, rows) -> None:
    print(f"\n  {label}")
    if not rows:
        print("    (nothing - invisible to this user)")
        return
    for r in rows:
        print(f"    {r.name:34s} {r.verdict:10s} score={r.score:.4f}")
        for reason in r.reasons[:2]:
            print(f"        L{reason['layer']} {reason['text'][:92]}")
        for u in r.unknowns[:2]:
            print(f"        ? {u}")


def main() -> int:
    query = sys.argv[1] if len(sys.argv) > 1 else "reject a payload that is too large"

    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) from asset")
        total = cur.fetchone()[0]
        # A developer who owns one repo, and an asset belonging to a different one.
        cur.execute("""
            select u.id, u.login, t.name
              from app_user u join team t on t.id = u.team_id
             where u.login = 'dev-xk6-kafka'
        """)
        row = cur.fetchone()
        if not row:
            print("No corpus. Run scripts/ingest.py first.")
            return 1
        user_id, login, team = row
        cur.execute("""
            select a.id, a.name, a.source_ref, a.discovery_scope
              from asset a join team t on t.id = a.discovery_scope
             where t.name <> %s and a.name not like '%%kafka%%'
             order by (select count(*) from claim c
                        where c.asset_id = a.id and c.layer = 2) desc
             limit 1
        """, (team,))
        other = cur.fetchone()
        if not other:
            print("Need at least two teams' assets. Ingest more repos.")
            return 1
        other_id, other_name, other_src, other_scope = other
        cur.execute("select id from team where name = %s", (team,))
        my_team = cur.fetchone()[0]

    print(f"corpus: {total} assets")
    print(f"asking as: {login} (team {team})")
    print(f"target:    {other_name}  [{other_src}]  owned by another team")
    print(f"query:     {query!r}")

    print("\n" + "=" * 78)
    print("STATE 1 - default: the other team's repo is invisible")
    print("=" * 78)
    show("results:", find_existing(user_id, query))
    with as_user(user_id) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from asset")
        print(f"\n  assets visible to this user: {cur.fetchone()[0]} of {total}")

    print("\n" + "=" * 78)
    print("STATE 2 - the owner makes the card DISCOVERABLE (no code access granted)")
    print("=" * 78)
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("update asset set discovery_scope = %s where id = %s",
                    (my_team, other_id))
    show("results:", find_existing(user_id, query))
    with as_user(user_id) as conn, conn.cursor() as cur:
        cur.execute("select fields->>'name', has_source_access, "
                    "jsonb_array_length(fields->'claims'), fields->'gaps' "
                    "from card_visible where asset_id = %s", (other_id,))
        r = cur.fetchone()
        if r:
            print(f"\n  card: {r[0]}  source_access={r[1]}  public_claims={r[2]}")
            print(f"  declared gaps: {r[3]}")
        cur.execute("select count(*) from claim where asset_id = %s and sensitive",
                    (other_id,))
        print(f"  sensitive claims withheld: {cur.fetchone()[0]}")

    print("\n" + "=" * 78)
    print("STATE 3 - source access granted too: the detail layer opens")
    print("=" * 78)
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("insert into source_acl (user_id, source_ref) values (%s,%s) "
                    "on conflict do nothing", (user_id, other_src))
    with as_user(user_id) as conn, conn.cursor() as cur:
        cur.execute("select has_source_access, jsonb_array_length(detail->'claims') "
                    "from card_visible where asset_id = %s", (other_id,))
        r = cur.fetchone()
        print(f"\n  source_access={r[0]}  detail_claims_now_visible={r[1]}")

    # Leave the database as we found it.
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("update asset set discovery_scope = %s where id = %s",
                    (other_scope, other_id))
        cur.execute("delete from source_acl where user_id = %s and source_ref = %s",
                    (user_id, other_src))
    print("\n(reset to initial state)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
