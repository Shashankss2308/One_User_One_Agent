"""Pilot check: the report's comparison vs the fair one (treatment vs holdout)."""
import csv, sys
from collections import defaultdict

rows = list(csv.DictReader(open(f"{sys.argv[1]}/pilot/pilot_outcomes.csv", encoding="utf-8")))
for r in rows:
    r["ok"] = r["succeeded"] == "true"
    r["srv"] = r["served"] == "true"

def rate(rs):
    return (sum(r["ok"] for r in rs) / len(rs), len(rs)) if rs else (float("nan"), 0)

def show(label, rs):
    p, n = rate(rs)
    print(f"  {label:28s} n={n:5d}  success={p:6.1%}")

print("== The report's comparison (biased) ==")
show("served", [r for r in rows if r["srv"]])
show("everyone else", [r for r in rows if not r["srv"]])
print("== Fair comparison: assignment arm (intent-to-treat) ==")
T = [r for r in rows if r["arm"] == "treatment"]
H = [r for r in rows if r["arm"] == "holdout"]
show("treatment (all assigned)", T)
show("holdout", H)
print("== Inside the treatment arm ==")
show("treatment, served", [r for r in T if r["srv"]])
show("treatment, NOT served", [r for r in T if not r["srv"]])
print("== Per goal: treatment vs holdout ==")
for g in sorted({r["goal_id"] for r in rows}):
    t, h = rate([r for r in T if r["goal_id"] == g]), rate([r for r in H if r["goal_id"] == g])
    print(f"  {g}  treat {t[0]:6.1%} (n={t[1]:4d})   holdout {h[0]:6.1%} (n={h[1]:4d})   diff {t[0]-h[0]:+.1%}")
print("== Treatment arm: served vs not, within each goal ==")
for g in sorted({r["goal_id"] for r in T}):
    gs = [r for r in T if r["goal_id"] == g]
    sv, ns = [r for r in gs if r["srv"]], [r for r in gs if not r["srv"]]
    print(f"  {g}  assigned={len(gs):4d}  served={len(sv):4d}  served success={rate(sv)[0]:6.1%}  not-served success={rate(ns)[0]:6.1%}")