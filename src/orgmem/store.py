"""Writing assets, claims and evidence. Ingestion side, runs as the schema owner.

Every claim is written together with its evidence row in one transaction, so
`claim.evidence_id NOT NULL` cannot be violated by a partial write.
"""

from __future__ import annotations

import json
import uuid

import psycopg

from .models import Claim, Unit


def ensure_team(conn: psycopg.Connection, name: str) -> uuid.UUID:
    with conn.cursor() as cur:
        cur.execute(
            "insert into team (name) values (%s) on conflict (name) do update "
            "set name = excluded.name returning id", (name,))
        return cur.fetchone()[0]


def ensure_user(conn: psycopg.Connection, login: str, team_id: uuid.UUID) -> uuid.UUID:
    with conn.cursor() as cur:
        cur.execute(
            "insert into app_user (login, team_id) values (%s, %s) "
            "on conflict (login) do update set team_id = excluded.team_id returning id",
            (login, team_id))
        return cur.fetchone()[0]


def grant_membership(conn: psycopg.Connection, user_id: uuid.UUID, team_id: uuid.UUID) -> None:
    with conn.cursor() as cur:
        cur.execute("insert into membership (user_id, group_id) values (%s, %s) "
                    "on conflict do nothing", (user_id, team_id))


def grant_source_access(conn: psycopg.Connection, user_id: uuid.UUID, source_ref: str) -> None:
    with conn.cursor() as cur:
        cur.execute("insert into source_acl (user_id, source_ref) values (%s, %s) "
                    "on conflict do nothing", (user_id, source_ref))


def upsert_asset(conn: psycopg.Connection, unit: Unit, owner_team: uuid.UUID,
                 discovery_scope: uuid.UUID) -> uuid.UUID:
    source_ref = f"github:{unit.repo}"
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into asset (kind, name, aliases, source_ref, unit_id,
                               owner_team, discovery_scope, summary)
            values (%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (unit_id) do update set
              name = excluded.name, aliases = excluded.aliases,
              summary = excluded.summary, owner_team = excluded.owner_team,
              discovery_scope = excluded.discovery_scope
            returning id
            """,
            (unit.kind, unit.name, unit.aliases, source_ref, unit.unit_id,
             owner_team, discovery_scope, unit.summary),
        )
        return cur.fetchone()[0]


def write_claims(conn: psycopg.Connection, asset_id: uuid.UUID,
                 claims: list[Claim]) -> int:
    """Insert claims with their evidence. Returns how many were written.

    Idempotent on (asset, evidence ref, claim body): re-ingesting the same commit does
    not duplicate claims. A genuinely changed source produces a new content_hash and
    therefore a new claim.
    """
    written = 0
    with conn.cursor() as cur:
        for c in claims:
            cur.execute(
                "select 1 from claim cl join evidence e on e.id = cl.evidence_id "
                "where cl.asset_id = %s and e.ref = %s and e.content_hash = %s "
                "and cl.body = %s and cl.valid_to is null",
                (asset_id, c.evidence.ref, c.evidence.content_hash, c.body))
            if cur.fetchone():
                continue
            cur.execute(
                """insert into evidence
                     (source, ref, source_version, content_hash, excerpt, extractor_version)
                   values (%s,%s,%s,%s,%s,%s) returning id""",
                (c.evidence.source, c.evidence.ref, c.evidence.source_version,
                 c.evidence.content_hash, c.evidence.excerpt,
                 c.evidence.extractor_version))
            ev_id = cur.fetchone()[0]
            cur.execute(
                """insert into claim
                     (asset_id, layer, kind, body, evidence_id, extractor,
                      confidence, sensitive)
                   values (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (asset_id, int(c.layer), c.kind, c.body, ev_id, c.extractor,
                 c.confidence, c.sensitive))
            written += 1
    return written


def rebuild_card(conn: psycopg.Connection, asset_id: uuid.UUID) -> None:
    """Snapshot current claims into a new card version.

    The split is the whole point: `fields` is the redacted card anyone in
    discovery_scope may see; `detail` needs read access to the source. Sensitive
    claims never appear in `fields`.
    """
    with conn.cursor() as cur:
        cur.execute(
            "select id, layer, kind, body, sensitive, confidence from claim "
            "where asset_id = %s and valid_to is null order by layer, kind", (asset_id,))
        rows = cur.fetchall()
        cur.execute("select name, kind, summary, status, source_ref from asset "
                    "where id = %s", (asset_id,))
        name, kind, summary, status, source_ref = cur.fetchone()

        public = [r for r in rows if not r[4]]
        private = [r for r in rows if r[4]]

        fields = {
            "name": name, "kind": kind, "summary": summary, "status": status,
            "claims": [{"id": str(r[0]), "layer": r[1], "kind": r[2], "body": r[3]}
                       for r in public],
            "layer_counts": {str(L): sum(1 for r in public if r[1] == L) for L in (1, 2, 3)},
            # Honesty is part of the card: say what we do not know.
            "gaps": [g for g in (
                "no behavioural claims (no tests found)"
                if not any(r[1] == 2 for r in public) else None,
                "nobody is known to have used this outside its own team"
                if not any(r[1] == 3 for r in rows) else None,
            ) if g],
        }
        detail = {
            "source_ref": source_ref,
            "claims": [{"id": str(r[0]), "layer": r[1], "kind": r[2], "body": r[3]}
                       for r in private],
        }
        cur.execute("update card_version set valid_to = now() "
                    "where asset_id = %s and valid_to is null", (asset_id,))
        cur.execute("select coalesce(max(version), 0) + 1 from card_version "
                    "where asset_id = %s", (asset_id,))
        version = cur.fetchone()[0]
        cur.execute(
            "insert into card_version (asset_id, version, fields, detail, claim_ids) "
            "values (%s,%s,%s,%s,%s)",
            (asset_id, version, json.dumps(fields), json.dumps(detail),
             [r[0] for r in rows]))


def write_embedding(conn: psycopg.Connection, asset_id: uuid.UUID, field: str,
                    model: str, vec) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "insert into embedding (asset_id, field, model, vec) values (%s,%s,%s,%s) "
            "on conflict (asset_id, field, model) do update set vec = excluded.vec",
            (asset_id, field, model, vec))
