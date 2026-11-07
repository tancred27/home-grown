"""Recall vs corpus size, with and without query rewriting (experiments/scale/PREREG.md,
addendum 2). One rolled-back transaction; searches run as the gate-2 dev user under RLS.

Everyone gets a description-only purpose line from the same blind writer setup:
  <base-dir>   key.json + out_*.json: the 86 real units and the first 1,823 distractors
  <extra-dir>  key.json + out_*.json: distractors from large orgs, added after those

Distractors are added in a fixed nested order (the original 1,823 first, then the extras
shuffled with seed 11), and both methods are measured at each checkpoint.

Usage: python -I scripts/scale_curve.py <base-dir> <extra-dir> <rewrites.json>
                                        [--sizes 109,1932,4000,7000,0]   (0 = everything)
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector

sys.path.insert(0, str(Path(__file__).parent))
from gate2_run import dev_user  # noqa: E402
from scale_fair import load_lines  # noqa: E402
from scale_variants import questions  # noqa: E402

from homegrown.config import settings  # noqa: E402
from homegrown.embed import embed_texts  # noqa: E402
from homegrown.search import search  # noqa: E402

ROOT = Path(__file__).parent.parent


def measure(conn, user, sets, rw, size) -> dict:
    out = {"size": size}
    with conn.cursor() as cur:
        cur.execute("set role homegrown_app")
        cur.execute("select set_config('app.user_id', %s, true)", (user,))
        cur.execute("select id, unit_id from asset")
        uid = dict(cur.fetchall())
        visible = len(uid)
    present = set(uid.values())
    line = f"{visible:6d} assets |"
    for name, qs in sets.items():
        qs = [(q, g) for q, g in qs if g in present]
        for method in ("base", "rw+"):
            ranks = []
            for q, g in qs:
                kw = [rw[q]] if method == "rw+" else None
                got = [uid.get(r["asset_id"]) for r in search(conn, q, kw)]
                ranks.append(got.index(g) + 1 if g in got else None)
            r = {f"r@{k}": round(sum(1 for x in ranks if x and x <= k) / len(ranks), 3)
                 for k in (1, 5, 10)}
            out[f"{name}/{method}"] = {**r, "n": len(ranks), "ranks": ranks}
            line += f" {name} {method} r@5 {r['r@5']:.2f} r@1 {r['r@1']:.2f} |"
    out["visible"] = visible
    with conn.cursor() as cur:
        cur.execute("reset role")
    print(line, flush=True)
    return out


def main() -> int:
    base_dir, extra_dir, rw_file = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    sizes = [int(x) for x in (sys.argv[sys.argv.index("--sizes") + 1]
                              if "--sizes" in sys.argv else "109,1932,4000,7000,0").split(",")]
    key, lines = load_lines(base_dir / "key.json", base_dir)
    xkey, xlines = load_lines(extra_dir / "key.json", extra_dir)
    rw = {r["request"]: r["purpose"] for r in json.loads(rw_file.read_text("utf-8"))}
    sets = {"test": questions("test"), "tickets": questions("gate2")}
    user = dev_user()
    model = settings.embed_model

    real = {k: v for k, v in key.items() if v["kind"] == "real"}
    first = [(k, key[k], lines[k]["purpose"]) for k in key if key[k]["kind"] == "distractor"]
    extra = [(k, xkey[k], xlines[k]["purpose"]) for k in xkey]
    seen = {v["key"] for _, v, _ in first}
    extra = [e for e in extra if e[1]["key"] not in seen]
    random.Random(11).shuffle(extra)
    order = first + extra
    total = 109 + len(order)
    sizes = [total if s == 0 else s for s in sizes]
    print(f"{len(first)} first distractors + {len(extra)} extra = {total} assets max; "
          f"embedding ...", flush=True)
    vecs = {f: embed_texts(texts) for f, texts in (
        ("card", [f"{v['name']} . {v['description']}" for _, v, _ in order]),
        ("summary", [f"{v['name']}: {v['description']}" for _, v, _ in order]),
        ("purpose", [f"{v['name']}: {p}" for _, v, p in order]))}
    r_ids = list(real)
    r_purp = embed_texts([f"{real[i]['name']}: {lines[i]['purpose']}" for i in r_ids])

    results = []
    with psycopg.connect(settings.admin_dsn) as conn:
        register_vector(conn)
        try:
            with conn.cursor() as cur:
                cur.execute("select id, unit_id from asset where unit_id like 'github:%%'")
                by_unit = {u: a for a, u in cur.fetchall()}
                cur.executemany(
                    "insert into embedding (asset_id, field, model, vec) values (%s,%s,%s,%s) "
                    "on conflict (asset_id, field, model) do update set vec = excluded.vec",
                    [(by_unit[real[i]["key"]], "purpose", model, v)
                     for i, v in zip(r_ids, r_purp, strict=True) if real[i]["key"] in by_unit])
                cur.execute("insert into team (name) values ('team-distractors') "
                            "on conflict (name) do update set name = excluded.name returning id")
                team = cur.fetchone()[0]
                cur.execute("insert into membership (user_id, group_id) values (%s,%s) "
                            "on conflict do nothing", (user, team))
            done = 0
            for target in sorted(set(sizes)):
                need = max(0, target - 109) - done
                with conn.cursor() as cur:
                    for j in range(done, done + need):
                        _, v, _ = order[j]
                        org_repo = v["key"].removeprefix("meta:")
                        cur.execute(
                            "insert into asset (kind, name, aliases, source_ref, unit_id, "
                            "owner_team, discovery_scope, summary) values "
                            "('library',%s,%s,%s,%s,%s,%s,%s) on conflict (unit_id) do nothing "
                            "returning id",
                            (v["name"], [v["name"]], f"github:{org_repo}", v["key"], team,
                             team, v["description"]))
                        row = cur.fetchone()
                        if row is None:
                            continue
                        cur.executemany(
                            "insert into embedding (asset_id, field, model, vec) "
                            "values (%s,%s,%s,%s)",
                            [(row[0], f, model, vecs[f][j]) for f in vecs])
                done += need
                results.append(measure(conn, user, sets, rw, target))
        finally:
            conn.rollback()
    out = ROOT / "experiments" / "scale" / "curve.json"
    out.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"written {out.relative_to(ROOT)}; database unchanged (rolled back)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
