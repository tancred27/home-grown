"""Build the fit judge's inputs at 109 and ~9,610 assets (experiments/scale/PREREG_judge.md).

ONLY against orgmem_bench. One rolled-back transaction; production search + query
rewriting as the gate-2 dev user under RLS; the top 5 per ticket become judge cards
exactly as search.find_existing builds them.

    python -I scripts/scale_judge_inputs.py <out-dir>

Writes <out-dir>/judge_in_<size>.json  (what a blind judge sees: request + lettered cards)
and    <out-dir>/judge_key.json        (gold, kind, letter -> unit_id; judges never see it)
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

from orgmem.config import settings  # noqa: E402
from orgmem.embed import embed_texts  # noqa: E402
from orgmem.models import display_name  # noqa: E402
from orgmem.search import load_claims, search  # noqa: E402

ROOT = Path(__file__).parent.parent
SETS = ["tasks.json", "tasks_heldout.json", "tasks_judge_fresh.json", "tasks_judge_fresh2.json"]
LETTERS = "ABCDE"

if "orgmem_bench" not in settings.admin_dsn:
    raise SystemExit("refusing to run: ORGMEM_ADMIN_DSN must point at orgmem_bench")


def tickets():
    out = []
    for name in SETS:
        for t in json.loads((ROOT / "experiments/gate2" / name).read_text("utf-8"))["tasks"]:
            out.append({"tid": f"{name.removesuffix('.json')}:{t['id']}", "kind": t["kind"],
                        "ticket": t["ticket"], "gold": t.get("gold_unit_id")})
    return out


def cards_for(conn, user, ts, rw):
    with conn.cursor() as cur:
        cur.execute("set role orgmem_app")
        cur.execute("select set_config('app.user_id', %s, true)", (user,))
        cur.execute("select id, unit_id, name, coalesce(summary,''), coalesce(purpose,'') "
                    "from asset")
        info = {r[0]: r[1:] for r in cur.fetchall()}
    inputs, keys = [], {}
    for t in ts:
        rows = search(conn, t["ticket"], [rw[t["ticket"]]])[:5]
        cards, letter_unit = [], {}
        for i, r in enumerate(rows):
            unit, name, summary, purpose = info[r["asset_id"]]
            cards.append({"letter": LETTERS[i], "name": display_name(name),
                          "description": summary, "purpose": purpose,
                          "evidence": [c["body"] for c in load_claims(conn, r["asset_id"])
                                       if c["layer"] > 1][:6]})
            letter_unit[LETTERS[i]] = unit
        inputs.append({"tid": t["tid"], "request": t["ticket"], "candidates": cards})
        keys[t["tid"]] = {"kind": t["kind"], "gold": t["gold"], "letters": letter_unit}
    with conn.cursor() as cur:
        cur.execute("reset role")
    return inputs, keys


def main() -> int:
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)
    rw = {}
    for f in (ROOT / "experiments/scale/rewrites_claude.json",
              ROOT / "experiments/scale/rewrites_claude_controls.json"):
        rw.update({r["request"]: r["purpose"] for r in json.loads(f.read_text("utf-8"))})
    ts = tickets()
    assert all(t["ticket"] in rw for t in ts), "every ticket needs a rewrite"
    base, extra = ROOT / "experiments/scale/purpose_lines", ROOT / "experiments/scale/curve_lines"
    key, lines = load_lines(base / "key.json", base)
    xkey, xlines = load_lines(extra / "key.json", extra)
    real = {k: v for k, v in key.items() if v["kind"] == "real"}
    order = [(v, lines[k]["purpose"]) for k, v in key.items() if v["kind"] == "distractor"]
    seen = {v["key"] for v, _ in order}
    ex = [(v, xlines[k]["purpose"]) for k, v in xkey.items() if v["key"] not in seen]
    random.Random(11).shuffle(ex)
    order += ex
    model = settings.embed_model
    print(f"embedding {len(order)} distractors ...", flush=True)
    vecs = {f: embed_texts(t) for f, t in (
        ("card", [f"{v['name']} . {v['description']}" for v, _ in order]),
        ("summary", [f"{v['name']}: {v['description']}" for v, _ in order]),
        ("purpose", [f"{v['name']}: {p}" for v, p in order]))}
    r_ids = list(real)
    r_purp = embed_texts([f"{real[i]['name']}: {lines[i]['purpose']}" for i in r_ids])
    user = dev_user()
    all_keys = {}
    with psycopg.connect(settings.admin_dsn) as conn:
        register_vector(conn)
        try:
            with conn.cursor() as cur:
                cur.execute("select id, unit_id from asset where unit_id like 'github:%%'")
                by_unit = {u: a for a, u in cur.fetchall()}
                cur.executemany(
                    "update asset set purpose = %s where id = %s",
                    [(lines[i]["purpose"], by_unit[real[i]["key"]]) for i in r_ids
                     if real[i]["key"] in by_unit])
                cur.executemany(
                    "insert into embedding (asset_id, field, model, vec) values (%s,%s,%s,%s) "
                    "on conflict (asset_id, field, model) do update set vec = excluded.vec",
                    [(by_unit[real[i]["key"]], "purpose", model, v)
                     for i, v in zip(r_ids, r_purp, strict=True) if real[i]["key"] in by_unit])
            inp, keys = cards_for(conn, user, ts, rw)
            (out_dir / "judge_in_109.json").write_text(json.dumps(inp, indent=1), "utf-8")
            all_keys["109"] = keys
            with conn.cursor() as cur:
                cur.execute("insert into team (name) values ('team-distractors') "
                            "on conflict (name) do update set name = excluded.name returning id")
                team = cur.fetchone()[0]
                cur.execute("insert into membership (user_id, group_id) values (%s,%s) "
                            "on conflict do nothing", (user, team))
                for j, (v, p) in enumerate(order):
                    cur.execute(
                        "insert into asset (kind, name, aliases, source_ref, unit_id, owner_team, "
                        "discovery_scope, summary, purpose) values "
                        "('library',%s,%s,%s,%s,%s,%s,%s,%s) on conflict (unit_id) do nothing "
                        "returning id",
                        (v["name"], [v["name"]], "github:" + v["key"].removeprefix("meta:"),
                         v["key"], team, team, v["description"], p))
                    row = cur.fetchone()
                    if row:
                        cur.executemany("insert into embedding (asset_id, field, model, vec) "
                                        "values (%s,%s,%s,%s)",
                                        [(row[0], f, model, vecs[f][j]) for f in vecs])
            size = 109 + len(order)
            inp, keys = cards_for(conn, user, ts, rw)
            (out_dir / f"judge_in_{size}.json").write_text(json.dumps(inp, indent=1), "utf-8")
            all_keys[str(size)] = keys
        finally:
            conn.rollback()
    (out_dir / "judge_key.json").write_text(json.dumps(all_keys, indent=1), "utf-8")
    for s, ks in all_keys.items():
        in5 = sum(1 for k in ks.values() if k["kind"] == "duplicate"
                  and k["gold"] in k["letters"].values())
        print(f"size {s}: gold in top 5 for {in5}/30 duplicates", flush=True)
    print("database unchanged (rolled back)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
