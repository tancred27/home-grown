"""Minimal company-directory sync: a full snapshot import.

Record shape (anything else in a record is ignored, so HR fields are never stored):

    {"id": "e123", "name": "Ada Lovelace", "team": "Payments", "lob": "Finance Tech",
     "manager_id": "e7", "active": true, "emails": ["ada@corp.example"],
     "logins": {"github": ["ada-l"]}}

Emails and logins are stored only as sha256 hashes in source_identity (hashed exactly as
collector.directory.resolve hashes them), so commits map to directory people without
the raw values ever being kept. People missing from a later snapshot are marked
inactive, never deleted. This is ingestion: it runs as admin, never on the answer path.
An SCIM / Okta / Entra connector would produce the same records.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import psycopg

from .collector.directory import identity_hash


def _handle(external_id: str) -> str:
    return "emp-" + hashlib.sha256(external_id.encode("utf-8")).hexdigest()[:8]


def _merge(cur, old, new) -> None:
    """Move the old person's contributor rows and identities onto the new person."""
    cur.execute(
        "insert into asset_contributor (asset_id, person_id, role, commits, first_at, "
        "last_at, evidence_id) select asset_id, %s, role, commits, first_at, last_at, "
        "evidence_id from asset_contributor where person_id = %s "
        "on conflict (asset_id, person_id) do update set "
        "commits = asset_contributor.commits + excluded.commits, "
        "first_at = least(asset_contributor.first_at, excluded.first_at), "
        "last_at = greatest(asset_contributor.last_at, excluded.last_at)", (new, old))
    cur.execute("delete from asset_contributor where person_id = %s", (old,))
    cur.execute("update source_identity set person_id = %s where person_id = %s", (new, old))
    cur.execute("update app_user set person_id = %s where person_id = %s", (new, old))


def apply(conn: psycopg.Connection, records: list[dict]) -> dict:
    counts = {"people": 0, "managers_set": 0, "identities": 0, "merged": 0,
              "deactivated": 0}
    now = datetime.now(UTC)
    ext: dict[str, object] = {}
    with conn.transaction(), conn.cursor() as cur:
        for r in records:
            eid = str(r["id"])
            team_id = None
            if r.get("team"):
                lob_id = None
                if r.get("lob"):
                    cur.execute("insert into lob (name) values (%s) on conflict (name) "
                                "do update set name = excluded.name returning id",
                                (r["lob"],))
                    lob_id = cur.fetchone()[0]
                cur.execute("insert into team (name, lob_id) values (%s,%s) "
                            "on conflict (name) do update set "
                            "lob_id = coalesce(excluded.lob_id, team.lob_id) returning id",
                            (r["team"], lob_id))
                team_id = cur.fetchone()[0]
            cur.execute(
                "insert into person (handle, display_name, team_id, synthetic, external_id,"
                " active, synced_at) values (%s,%s,%s,false,%s,%s,%s) "
                "on conflict (external_id) do update set display_name = excluded.display_name,"
                " team_id = excluded.team_id, synthetic = false, active = excluded.active,"
                " synced_at = excluded.synced_at returning id",
                (_handle(eid), r["name"], team_id, eid, bool(r.get("active", True)), now))
            ext[eid] = cur.fetchone()[0]
            counts["people"] += 1

        for r in records:
            mid = ext.get(str(r["manager_id"])) if r.get("manager_id") is not None else None
            cur.execute("update person set manager_id = %s where id = %s",
                        (mid, ext[str(r["id"])]))
            counts["managers_set"] += mid is not None

        merged_from = set()
        for r in records:
            pid = ext[str(r["id"])]
            idents = [("email", e) for e in r.get("emails") or []]
            idents += [(src, v) for src, vals in (r.get("logins") or {}).items()
                       for v in vals or []]
            for source, value in idents:
                if not value:
                    continue
                h = identity_hash(source, value)
                cur.execute("select si.person_id, p.synthetic from source_identity si "
                            "join person p on p.id = si.person_id "
                            "where si.source = %s and si.login_hash = %s", (source, h))
                old = cur.fetchone()
                if old and old[0] != pid:
                    if not old[1]:
                        continue        # already a different directory person: leave it
                    _merge(cur, old[0], pid)
                    merged_from.add(old[0])
                cur.execute("insert into source_identity (source, login_hash, person_id) "
                            "values (%s,%s,%s) on conflict (source, login_hash) do update "
                            "set person_id = excluded.person_id", (source, h, pid))
                counts["identities"] += 1
        counts["merged"] = len(merged_from)

        # Link app users to their directory person by GitHub login.
        cur.execute("select id, login from app_user")
        for uid, login in cur.fetchall():
            cur.execute("select si.person_id from source_identity si join person p on "
                        "p.id = si.person_id where si.source = 'github' and "
                        "si.login_hash = %s and not p.synthetic",
                        (identity_hash("github", login),))
            row = cur.fetchone()
            if row:
                cur.execute("update app_user set person_id = %s where id = %s",
                            (row[0], uid))

        cur.execute("update person set active = false where external_id is not null "
                    "and active and external_id <> all(%s)", (list(ext),))
        counts["deactivated"] = cur.rowcount
    return counts


def import_file(path: str) -> dict:
    from .db import admin_conn
    with open(path, encoding="utf-8") as f:
        records = json.load(f)
    if not isinstance(records, list):
        raise SystemExit("the file must hold a JSON list of records")
    with admin_conn() as conn:
        return apply(conn, records)
