"""Dry run: plan every replay trigger. No publishing, and no verifier yet."""
import csv, json, sqlite3, sys, time
from collections import Counter, defaultdict
try:
    from .planner import Planner
except ImportError:
    from planner import Planner

pack, db_path = sys.argv[1], sys.argv[2]
db = sqlite3.connect(db_path)
P = Planner(pack, db)
load = lambda p: [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
trigs = load(f"{pack}/replay/triggers.jsonl")
ctrl = [c for c in load(f"{pack}/replay/control_events.jsonl")
        if c["type"] in ("title_withdrawn", "goal_rule_corrected")]
raw = {r["title_id"]: (r["dialect"], r["content_slug"])
       for r in csv.DictReader(open(f"{pack}/data/catalogue.csv", encoding="utf-8"))}

acts, codes, goals, shadow, by_dialect, times = Counter(), Counter(), Counter(), Counter(), defaultdict(Counter), []
dups, seen_ids, bad, ex = 0, set(), Counter(), {}
for t in trigs:
    while ctrl and ctrl[0]["at"] <= t["deliver_at"]:
        P.apply_control(ctrl.pop(0))
    dups += t["event_id"] in seen_ids
    seen_ids.add(t["event_id"])
    d = P.plan(t)
    times.append(d["decided_in_ms"])
    acts[d["action"]] += 1
    codes[d["no_action_code"] or "-"] += 1
    by_dialect[d["dialect"]["used"]][d["no_action_code"] or "HOME_PAGE"] += 1
    if d["goal"]:
        (shadow if d["goal"]["shadow"] else goals)[d["goal"]["id"]] += 1
    if d["action"] == "HOME_PAGE":
        pg = d["page"]
        ids = [pg["hero_title_id"]] + [x for r in pg["rows"] for x in r["title_ids"]]
        slugs = [raw[i][1] for i in ids]
        bad["wrong_dialect"] += any(raw[i][0] != pg["dialect"] for i in ids)
        bad["duplicate_slug"] += len(slugs) != len(set(slugs))
        bad["withdrawn_T0001_after_0700Z"] += ("T0001" in ids and t["deliver_at"] >= "2026-10-01T07:00")
        bad["quarantined_T0002_T0522"] += bool({"T0002", "T0522"} & set(ids))
        if d["goal"]["id"] == "G07":
            ex.setdefault("g07_example", d)
    elif d["no_action_code"] == "no_goal":
        ex.setdefault("no_goal_example", d)
    elif d["no_action_code"] == "stale_trigger":
        ex.setdefault("stale_example", d)
    ex.setdefault(d["action"] + "_example", d)

times.sort()
print("triggers:", len(trigs), "| repeated event_ids:", dups)
print("actions:", dict(acts))
print("no-action reasons:", dict(codes))
print("goals (agent arm):", dict(goals.most_common()))
print("goals (holdout/unknown arm, shadow):", dict(shadow.most_common()))
print("per dialect:", {k: dict(v) for k, v in sorted(by_dialect.items(), key=lambda x: str(x[0]))})
print(f"decision time ms: mean={sum(times)/len(times):.1f} p95={times[int(len(times)*.95)]} max={times[-1]}")
print("sanity violations (dry-run only, NOT the verifier):", dict(bad) or "none")
for k, d in ex.items():
    if k.endswith("_example") and k != "NO_ACTION_example":
        print("\n==", k)
        print(json.dumps(d, indent=1)[:1600])