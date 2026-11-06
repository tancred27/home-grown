"""Scale test with purpose lines for EVERYONE (experiments/scale/PREREG.md).

Runs every condition inside ONE database transaction that is rolled back at the end:
the live database is never changed. Searches run as orgmem_app with the gate-2 dev user
under row-level security, exactly as the product does (SET ROLE inside the transaction).

  A0  109 assets, real units keep their production (Groq, README) purpose lines
  A   + 1,823 distractors without purpose lines           (the earlier, flattered run)
  C   + distractors, and EVERY asset's purpose line from Claude, name + description only
  C0  109 assets, real units' purpose lines from Claude, name + description only

Usage: python -I scripts/scale_fair.py <key.json> <out-dir>
  key.json   opaque id -> {key: unit_id, kind, name, description, org?}
  out-dir    out_*.json from the blind writer: [{id, purpose, confidence}]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector

sys.path.insert(0, str(Path(__file__).parent))
from gate2_run import dev_user  # noqa: E402

from orgmem.config import settings  # noqa: E402
from orgmem.embed import embed_texts  # noqa: E402
from orgmem.search import search  # noqa: E402

ROOT = Path(__file__).parent.parent
G2 = ["tasks.json", "tasks_heldout.json", "tasks_judge_fresh.json", "tasks_judge_fresh2.json"]


def load_lines(key_file: Path, out_dir: Path) -> tuple[dict, dict]:
    key = json.loads(key_file.read_text("utf-8"))
    lines = {}
    for f in sorted(out_dir.glob("out_*.json")):
        for r in json.loads(f.read_text("utf-8")):
            lines[r["id"]] = r
    missing = [i for i in key if i not in lines or not lines[i].get("purpose")]
    if missing:
        raise SystemExit(f"{len(missing)} items have no purpose line, e.g. {missing[:5]}")
    return key, lines


def measure(conn, user: str, label: str) -> dict:
    """Run both question sets as the dev user under RLS. Returns the metrics."""
    with conn.cursor() as cur:
        cur.execute("set role orgmem_app")
        cur.execute("select set_config('app.user_id', %s, true)", (user,))
        cur.execute("select id, unit_id from asset")
        uid_of = {r[0]: r[1] for r in cur.fetchall()}
        visible = len(uid_of)

    def ranks(q: str) -> list[str]:
        return [uid_of.get(r["asset_id"]) for r in search(conn, q)]

    g1 = json.loads((ROOT / "experiments/gate1/queries_test.json").read_text("utf-8"))
    present = set(uid_of.values())
    g1 = [q for q in g1 if q["gold_unit_id"] in present]     # gold must exist to be found
    r1 = []
    for q in g1:
        got = ranks(q["query"])
        r1.append(got.index(q["gold_unit_id"]) + 1 if q["gold_unit_id"] in got else None)
    r2 = []
    for name in G2:
        for t in json.loads((ROOT / "experiments/gate2" / name).read_text("utf-8"))["tasks"]:
            if t["kind"] != "duplicate":
                continue
            got = ranks(t["ticket"])
            r2.append(got.index(t["gold_unit_id"]) + 1 if t["gold_unit_id"] in got else None)
    with conn.cursor() as cur:
        cur.execute("reset role")

    def within(rs, k):
        return sum(1 for r in rs if r and r <= k)
    out = {"label": label, "visible_assets": visible,
           "gate1_test": {"n": len(r1), **{f"r@{k}": round(within(r1, k) / len(r1), 3)
                                          for k in (1, 5, 10)}},
           "gate2_dups": {"n": len(r2), **{f"top{k}": within(r2, k)
                                          for k in (1, 5, 15, 30)}},
           "ranks": {"gate1": r1, "gate2": r2}}
    g1s, g2s = out["gate1_test"], out["gate2_dups"]
    print(f"{label:4s} {visible:5d} assets | gate-1 test r@1 {g1s['r@1']:.2f} r@5 {g1s['r@5']:.2f}"
          f" r@10 {g1s['r@10']:.2f} | gate-2 dups top1 {g2s['top1']}/{g2s['n']} top5 "
          f"{g2s['top5']} top15 {g2s['top15']} top30 {g2s['top30']}", flush=True)
    return out


def purpose_vecs(items: list[tuple[str, str]]):
    """[(name, purpose)] -> vectors, in the same text form purpose.store() embeds."""
    return embed_texts([f"{n}: {p}" for n, p in items])


def main() -> int:
    key, lines = load_lines(Path(sys.argv[1]), Path(sys.argv[2]))
    real = {k: v for k, v in key.items() if v["kind"] == "real"}
    dist = {k: v for k, v in key.items() if v["kind"] == "distractor"}
    model = settings.embed_model
    user = dev_user()                         # committed separately, before the experiment

    print(f"embedding {len(dist)} distractors (card, summary, purpose) and "
          f"{len(real)} real purpose lines...", flush=True)
    d_ids = list(dist)
    d_card = embed_texts([f"{dist[i]['name']} . {dist[i]['description']}" for i in d_ids])
    d_summ = embed_texts([f"{dist[i]['name']}: {dist[i]['description']}" for i in d_ids])
    d_purp = purpose_vecs([(dist[i]["name"], lines[i]["purpose"]) for i in d_ids])
    r_ids = list(real)
    r_purp = purpose_vecs([(real[i]["name"], lines[i]["purpose"]) for i in r_ids])

    results = []
    with psycopg.connect(settings.admin_dsn) as conn:       # autocommit off: one transaction
        register_vector(conn)
        try:
            results.append(measure(conn, user, "A0"))
            with conn.cursor() as cur:
                cur.execute("savepoint before_distractors")
                cur.execute("insert into team (name) values ('team-distractors') "
                            "on conflict (name) do update set name = excluded.name "
                            "returning id")
                team = cur.fetchone()[0]
                cur.execute("insert into membership (user_id, group_id) values (%s,%s) "
                            "on conflict do nothing", (user, team))
                aid = {}
                for i in d_ids:
                    org_repo = dist[i]["key"].removeprefix("meta:")
                    cur.execute(
                        "insert into asset (kind, name, aliases, source_ref, unit_id, "
                        "owner_team, discovery_scope, summary) values "
                        "('library',%s,%s,%s,%s,%s,%s,%s) returning id",
                        (dist[i]["name"], [dist[i]["name"]], f"github:{org_repo}",
                         dist[i]["key"], team, team, dist[i]["description"]))
                    aid[i] = cur.fetchone()[0]
                for field, vecs in (("card", d_card), ("summary", d_summ)):
                    cur.executemany(
                        "insert into embedding (asset_id, field, model, vec) "
                        "values (%s,%s,%s,%s)",
                        [(aid[i], field, model, v) for i, v in zip(d_ids, vecs, strict=True)])
            results.append(measure(conn, user, "A"))

            def swap_real_purpose(cur):
                cur.execute("select id, unit_id from asset where unit_id like 'github:%%'")
                by_unit = {u: a for a, u in cur.fetchall()}
                cur.executemany(
                    "insert into embedding (asset_id, field, model, vec) values (%s,%s,%s,%s) "
                    "on conflict (asset_id, field, model) do update set vec = excluded.vec",
                    [(by_unit[real[i]["key"]], "purpose", model, v)
                     for i, v in zip(r_ids, r_purp, strict=True) if real[i]["key"] in by_unit])

            with conn.cursor() as cur:
                cur.executemany(
                    "insert into embedding (asset_id, field, model, vec) values (%s,%s,%s,%s)",
                    [(aid[i], "purpose", model, v) for i, v in zip(d_ids, d_purp, strict=True)])
                swap_real_purpose(cur)
            results.append(measure(conn, user, "C"))

            with conn.cursor() as cur:
                cur.execute("rollback to savepoint before_distractors")
                swap_real_purpose(cur)
            results.append(measure(conn, user, "C0"))
        finally:
            conn.rollback()                   # nothing from this experiment is kept

    out = ROOT / "experiments" / "scale" / "results.json"
    out.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"\nwritten {out.relative_to(ROOT)}; database unchanged (rolled back)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
