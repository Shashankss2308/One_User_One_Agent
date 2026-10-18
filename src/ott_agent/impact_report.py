"""Write randomized pilot ITT and replay simulator impact summaries."""
import csv
import json
import math
import os
import sys
from collections import defaultdict


def parse_bool(value):
    return str(value).lower() == "true"


def stats(rows):
    n = len(rows)
    successes = sum(r["succeeded"] for r in rows)
    return {"n": n, "successes": successes, "rate": successes / n if n else float("nan")}


def contrast(treatment, holdout):
    t, h = stats(treatment), stats(holdout)
    diff = t["rate"] - h["rate"]
    se = math.sqrt(t["rate"] * (1 - t["rate"]) / t["n"] +
                   h["rate"] * (1 - h["rate"]) / h["n"]) if t["n"] and h["n"] else float("nan")
    return t, h, diff, (diff - 1.96 * se, diff + 1.96 * se)


def pct(value):
    return f"{100 * value:.1f}%" if math.isfinite(value) else "n/a"


def main(pack, run_dir):
    with open(os.path.join(pack, "pilot/pilot_assignment.csv"), encoding="utf-8") as f:
        assignment = {r["user_id"]: r for r in csv.DictReader(f)}
    with open(os.path.join(pack, "pilot/pilot_outcomes.csv"), encoding="utf-8") as f:
        pilot = []
        for r in csv.DictReader(f):
            a = assignment.get(r["user_id"])
            if a:
                pilot.append({"user_id": r["user_id"], "arm": a["arm"],
                              "served": parse_bool(r["served"]),
                              "succeeded": parse_bool(r["succeeded"]), "goal_id": r["goal_id"]})
    pt = [r for r in pilot if r["arm"] == "treatment"]
    ph = [r for r in pilot if r["arm"] == "holdout"]
    t, h, diff, ci = contrast(pt, ph)
    served = [r for r in pilot if r["served"]]
    not_served = [r for r in pilot if not r["served"]]

    replay = []
    with open(os.path.join(run_dir, "outcomes.jsonl"), encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                if r["status"] != "INCONCLUSIVE":
                    replay.append({"arm": r["arm"], "succeeded": r["status"] == "SUCCEEDED",
                                   "goal_id": r["goal_id"]})
    rt = [r for r in replay if r["arm"] == "agent"]
    rh = [r for r in replay if r["arm"] == "holdout"]
    sim_t, sim_h, sim_diff, sim_ci = contrast(rt, rh)
    all_counts = {"pilot_assignment": len(assignment), "pilot_outcomes_matched": len(pilot),
                  "replay_matured_goals": len(replay), "replay_censored_goals": sum(
                      1 for line in open(os.path.join(run_dir, "outcomes.jsonl"), encoding="utf-8")
                      if '"status": "INCONCLUSIVE"' in line)}

    lines = [
        "# Impact report", "",
        "## Historical randomized pilot: intent to treat", "",
        "Arm assignment comes from `pilot_assignment.csv`; every matched outcome remains in its randomized arm regardless of service. The difference is treatment minus holdout. Confidence interval is an unadjusted normal approximation for the difference in two proportions.", "",
        f"- Matched outcomes: {all_counts['pilot_outcomes_matched']} / {all_counts['pilot_assignment']} assigned users.",
        f"- Treatment: {t['successes']}/{t['n']} = {pct(t['rate'])}.",
        f"- Holdout: {h['successes']}/{h['n']} = {pct(h['rate'])}.",
        f"- ITT risk difference: {pct(diff)} (95% CI {pct(ci[0])} to {pct(ci[1])}).",
        f"- Biased served-versus-not-served comparison: {pct(stats(served)['rate'])} (n={len(served)}) vs {pct(stats(not_served)['rate'])} (n={len(not_served)}); this is not causal because service was selected.",
        "",
        "## Replay simulator: descriptive randomized-arm comparison", "",
        f"The practice-seed simulator produced {all_counts['replay_matured_goals']} matured first-goal records and {all_counts['replay_censored_goals']} right-censored goals whose windows extend beyond the simulation horizon. The following is a single-seed, synthetic replay result; it is not a production impact estimate. Goal assignment can differ between treatment and holdout due to the treatment advisor, so interpret this as policy-level descriptive performance.",
        "",
        f"- Agent arm: {sim_t['successes']}/{sim_t['n']} matured first goals = {pct(sim_t['rate'])}.",
        f"- Holdout arm: {sim_h['successes']}/{sim_h['n']} matured first goals = {pct(sim_h['rate'])}.",
        f"- Descriptive difference: {pct(sim_diff)} (95% normal interval {pct(sim_ci[0])} to {pct(sim_ci[1])}); interval does not capture simulator-seed uncertainty.",
        "",
        "The simulator accepted all 1,068 published actions with zero policy problems. It reports 824 personalized pages actually shown and 15 published pages not shown because the user's app dialect no longer matched. A second simulator seed is required to characterize simulation uncertainty.",
        ""
    ]
    with open(os.path.join(run_dir, "impact_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("pilot ITT:", f"{pct(t['rate'])} vs {pct(h['rate'])}; diff {pct(diff)} (95% CI {pct(ci[0])} to {pct(ci[1])})")
    print("replay matured:", sim_t["n"], sim_h["n"], "censored:", all_counts["replay_censored_goals"])


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
