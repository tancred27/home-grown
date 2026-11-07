"""Gate 2: do coding agents check the memory before building something that exists?

Pass line: find_existing is called in >= 60% of runs where a
duplicate exists, under the strongest nudges.

Runs every (variant x ticket x repeat) through the LangGraph agent in homegrown.agent, with
the REAL find_existing over the real corpus, a Postgres checkpointer, and a simulated
developer who answers each reuse question: "reuse" if the asset shown is the right one
(the ticket's gold asset), "build" otherwise.

  --model claude            the measurement: claude-opus-5-5 drives the agent
  --model scripted-eager    plumbing: a script that always writes immediately
  --model scripted-careful  plumbing: a script that always checks first

Only `--model claude` answers gate 2. The scripted runs still measure something real for
the hook condition, because the hook's check uses the ticket text and the real retriever:
whether that check finds the duplicate, and how often it raises a false alarm on a
ticket with no duplicate.

Usage: python -I scripts/gate2_run.py --model scripted-eager [--variants hook,none]
                                      [--repeats 1] [--tasks experiments/gate2/tasks.json]
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
import uuid
from collections import defaultdict
from pathlib import Path

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command

from homegrown.agent import (
    VARIANTS,
    Workspace,
    build_graph,
    compact_verdicts,
    initial_state,
    make_tools,
    score_run,
)
from homegrown.config import settings
from homegrown.db import admin_conn
from homegrown.search import find_existing

ROOT = Path(__file__).parent.parent
SEED = {
    "README.md": "# orders-service\n\nA small internal Python service. Add new code as "
                 "modules next to main.py.\n",
    "main.py": "def main():\n    print('orders-service')\n\n\nif __name__ == '__main__':\n"
               "    main()\n",
}


def dev_user() -> str:
    """A developer who may DISCOVER every team's assets but read none of their code --
    the siloed-org case the product exists for."""
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("insert into team (name) values ('team-gate2') on conflict (name) "
                    "do update set name = excluded.name returning id")
        t = cur.fetchone()[0]
        cur.execute("insert into app_user (login, team_id) values ('gate2-dev', %s) "
                    "on conflict (login) do update set team_id = excluded.team_id "
                    "returning id", (t,))
        u = cur.fetchone()[0]
        cur.execute("insert into membership (user_id, group_id) select %s, id from team "
                    "on conflict do nothing", (u,))
    return str(u)


def make_model(name: str):
    if name.startswith("scripted-"):
        from homegrown.agent.scripted import ScriptedModel
        return ScriptedModel(behaviour=name.removeprefix("scripted-"))
    if name == "claude":
        from langchain_anthropic import ChatAnthropic
        # No temperature: current models reject it. Default effort.
        return ChatAnthropic(model="claude-opus-5-5", max_tokens=8000, max_retries=5)
    raise SystemExit(f"unknown --model {name}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="scripted-eager")
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--tasks", default=str(ROOT / "experiments" / "gate2" / "tasks.json"))
    ap.add_argument("--work", default=str(ROOT / ".cache" / "gate2-work"))
    args = ap.parse_args()

    tasks = json.loads(Path(args.tasks).read_text(encoding="utf-8"))["tasks"]
    variants = [v for v in args.variants.split(",") if v]
    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute("select unit_id, id from asset")
        by_unit = {u: str(i) for u, i in cur.fetchall()}
    user = dev_user()
    model = make_model(args.model)

    def finder(intent, keywords):
        return compact_verdicts(find_existing(user, intent, keywords))

    out_file = ROOT / "experiments" / "gate2" / f"runs_{args.model}.jsonl"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    work = Path(args.work)
    rows = []
    with PostgresSaver.from_conn_string(settings.admin_dsn) as saver, \
            out_file.open("w", encoding="utf-8") as fh:
        saver.setup()
        for variant in variants:
            for t in tasks:
                gold = by_unit.get(t["gold_unit_id"]) if t["gold_unit_id"] else None
                for rep in range(args.repeats):
                    ws_dir = work / f"{args.model}-{variant}-{t['id']}-{rep}"
                    shutil.rmtree(ws_dir, ignore_errors=True)
                    ws = Workspace(ws_dir)
                    for name, body in SEED.items():
                        ws.write(name, body)
                    graph = build_graph(model, make_tools(ws, variant, finder), finder,
                                        checkpointer=saver)
                    cfg = {"configurable": {"thread_id": f"gate2-{uuid.uuid4().hex}"},
                           "recursion_limit": 60}
                    t0 = time.time()
                    try:
                        out = graph.invoke(initial_state(t["ticket"], variant), cfg)
                        interrupts = 0
                        while out.get("__interrupt__"):
                            interrupts += 1
                            cands = out["__interrupt__"][0].value.get("candidates", [])
                            hit = gold and any(c["asset_id"] == gold for c in cands)
                            out = graph.invoke(Command(resume={
                                "decision": "reuse" if hit else "build",
                                "asset_id": gold if hit else None}), cfg)
                        err = None
                    except Exception as e:     # recorded, never silently dropped
                        out, interrupts, err = {"events": []}, 0, f"{type(e).__name__}: {e}"
                    s = score_run(out.get("events", []), gold)
                    row = {"task": t["id"], "kind": t["kind"], "variant": variant,
                           "model": args.model, "repeat": rep, "interrupts": interrupts,
                           "secs": round(time.time() - t0, 2), "error": err, **s,
                           "events": out.get("events", [])}
                    rows.append(row)
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                    if err:
                        print(f"  ! {variant}/{t['id']}: {err[:160]}")

    # ---------------------------------------------------------------- summary
    def pct(xs):
        return f"{100 * sum(xs) / len(xs):5.0f}%" if xs else "    -"

    agg = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r["error"]:
            continue
        a = agg[r["variant"]]
        if r["kind"] == "duplicate":
            a["model_called"].append(r["model_called_before_write"])
            a["checked"].append(r["checked_before_write"])
            a["found"].append(r["found_gold"])
            a["reused"].append(r["outcome"] == "reused")
        else:
            a["false_alarm"].append(r["interrupts"] > 0)

    errs = sum(1 for r in rows if r["error"])
    print(f"\nmodel={args.model}  runs={len(rows)}  errors={errs}  -> {out_file.name}")
    print(f"{'variant':12s} {'model checked':>14s} {'any check':>10s} {'found dup':>10s} "
          f"{'reused':>8s} {'false alarm':>12s}")
    print(f"{'':12s} {'(gate 2 metric)':>14s} {'pre-write':>10s} {'':>10s} {'':>8s} "
          f"{'(controls)':>12s}")
    for v in variants:
        a = agg[v]
        print(f"{v:12s} {pct(a['model_called']):>14s} {pct(a['checked']):>10s} "
              f"{pct(a['found']):>10s} {pct(a['reused']):>8s} {pct(a['false_alarm']):>12s}")
    if args.model.startswith("scripted-"):
        print("\nScripted model: the 'model checked' column reflects the script, not a real "
              "agent. Only the hook row's found/false-alarm figures are real measurements.")
    return 1 if errs and errs == len(rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
