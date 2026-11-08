"""The org map: every component the caller may discover, grouped by line of business.

Everything runs as the caller, so row-level security decides what is listed. A component
the caller cannot discover is not listed and not counted. "Used by" counts only edges
whose both ends the caller can see (the edge policy), so a hidden dependent never shows
up as a number either.
"""

from __future__ import annotations

from collections import defaultdict

from .db import as_user, fetch_all
from .models import display_name

STAGES = ("prod", "beta", "dev", "deprecated", "retired", "unknown")
NO_LOB = "No line of business"


def org_map(user_id: str) -> dict:
    with as_user(user_id) as conn:
        rows = fetch_all(conn, """
            select a.id, a.name, a.unit_id, a.source_ref, a.stage, a.stage_confirmed,
                   coalesce(a.purpose, a.summary), t.name, l.name,
                   caller_can_read_source(a.source_ref)
              from asset a
              left join team t on t.id = a.owner_team
              left join lob l on l.id = t.lob_id
             where a.kind <> 'solved_problem'""")
        used = dict(fetch_all(conn, """
            select e.dst, count(distinct s.owner_team)
              from edge e
              join asset s on s.id = e.src
              join asset d on d.id = e.dst
             where e.type = 'depends_on' and e.valid_to is null
               and s.owner_team is distinct from d.owner_team
             group by e.dst"""))
        docs = fetch_all(conn, "select count(*) from asset where kind = 'solved_problem'")[0][0]

    by_lob: dict[str, list[dict]] = defaultdict(list)
    by_stage = dict.fromkeys(STAGES, 0)
    repos = set()
    for (aid, name, unit_id, source_ref, stage, confirmed, purpose, team, lob,
         can_read) in rows:
        parts = (unit_id or "").split(":", 2)
        path = parts[2] if len(parts) == 3 and parts[2] not in ("", ".") else ""
        stage = stage if stage in by_stage else "unknown"
        by_stage[stage] += 1
        repos.add(source_ref)
        by_lob[lob or NO_LOB].append({
            "id": str(aid),
            "name": display_name(name),
            "path": path,
            "repo": source_ref,
            "team": team,
            "stage": stage,
            "stage_confirmed": bool(confirmed),
            "purpose": purpose,
            "used_by_teams": used.get(aid, 0),
            "can_read_code": bool(can_read),
        })

    lobs = []
    for name, comps in by_lob.items():
        comps.sort(key=lambda c: (-c["used_by_teams"], STAGES.index(c["stage"]),
                                  c["name"].lower()))
        lobs.append({"name": name, "teams": len({c["team"] for c in comps if c["team"]}),
                     "components": comps})
    lobs.sort(key=lambda lob: (lob["name"] == NO_LOB, lob["name"].lower()))
    return {"components": len(rows), "repositories": len(repos), "doc_pages": docs,
            "by_stage": by_stage, "lobs": lobs}
