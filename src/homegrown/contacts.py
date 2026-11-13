"""Who to ask about an asset: the most recent committers who are in the company
directory, each with their manager and a reason built from data (no model).

Runs on whatever cursor it is given. On the answer path that is an as_user() connection,
so row-level security decides what the asker may see: contributors of an asset they
cannot discover come back empty, exactly as in `_enrich`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

_CONTRIB = """
    select p.id, p.display_name, p.handle, pt.name, m.display_name, ac.commits,
           ac.last_at, p.synthetic, p.active
      from asset_contributor ac
      join person p on p.id = ac.person_id
      left join team pt on pt.id = p.team_id
      left join person m on m.id = p.manager_id
     where ac.asset_id = %s
     order by ac.last_at desc nulls last, ac.commits desc nulls last
     limit 10"""


def asker_person(cur, user_id) -> str | None:
    """The directory person behind an app_user (app_user.person_id), or None."""
    if not user_id:
        return None
    cur.execute("select person_id from app_user where id = %s", (str(user_id),))
    row = cur.fetchone()
    return str(row[0]) if row and row[0] else None


def _reason(commits, last_at, stale: bool) -> str:
    parts = []
    if last_at:
        parts.append(f"last commit {last_at.date().isoformat()}")
    if commits:
        parts.append(f"{commits} commit{'s' if commits != 1 else ''}")
    text = ", ".join(parts) or "listed as a contributor"
    return text + ("; no recent activity" if stale else "")


def who_to_ask(cur, asset_id, asker_person_id=None, limit: int = 2,
               stale_days: int = 365) -> list[dict]:
    cur.execute("select parent_id, owner_team from asset where id = %s", (str(asset_id),))
    row = cur.fetchone()
    if not row:
        return []
    parent_id, owner_team = row
    cur.execute(_CONTRIB, (str(asset_id),))
    rows = cur.fetchall()
    if not rows and parent_id:      # a crawler unit has no history of its own
        cur.execute(_CONTRIB, (str(parent_id),))
        rows = cur.fetchall()

    cur.execute("select exists (select 1 from person where not synthetic)")
    synced = cur.fetchone()[0]
    picks = []
    for pid, name, handle, team, manager, commits, last_at, synthetic, active in rows:
        if not active or (synced and synthetic):
            continue
        if asker_person_id and str(pid) == str(asker_person_id):
            continue
        picks.append((name, handle, team, manager, commits, last_at))
        if len(picks) >= limit:
            break

    if picks:
        cutoff = datetime.now(UTC) - timedelta(days=stale_days)
        return [{"name": n, "handle": h, "team": t, "manager": m,
                 "last_active": la.date().isoformat() if la else None, "commits": k,
                 "reason": _reason(k, la, bool(la and la < cutoff))}
                for n, h, t, m, k, la in picks]

    if owner_team:
        cur.execute("select p.display_name, p.handle, t.name from person p "
                    "join team t on t.id = p.team_id where p.team_id = %s and "
                    "p.handle like 'lead-%%' and p.active limit 1", (owner_team,))
        lead = cur.fetchone()
        if lead:
            return [{"name": lead[0], "handle": lead[1], "team": lead[2], "manager": None,
                     "last_active": None, "commits": None,
                     "reason": "no recent committer found in the directory; "
                               "lead of the owning team"}]
    return []


def format_ask(p: dict) -> str:
    who = f"{p['name']} ({p['team']})" if p.get("team") else p["name"]
    mgr = f", manager {p['manager']}" if p.get("manager") else ""
    return f"{who}{mgr}: {p['reason']}"


def ask_for_assets(user_id, asset_ids: list[str]) -> dict[str, list[dict]]:
    """who_to_ask for several assets, as the user (CLI use)."""
    from .db import as_user
    out: dict[str, list[dict]] = {}
    with as_user(user_id) as conn, conn.cursor() as cur:
        me = asker_person(cur, user_id)
        for a in asset_ids:
            out[a] = who_to_ask(cur, a, me)
    return out
