"""Reconcile replay goals against the simulator's observed post-trigger events."""
import csv
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
SIMULATION_END = datetime(2026, 10, 16, tzinfo=IST)  # 14 simulated days after Oct 1 starts


def parse(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_trial(props):
    return str(props.get("is_trial", "")).lower() == "true"


def g09_success_events(user_events):
    days = sorted({parse(e["received_at"]).astimezone(IST).date() for e in user_events
                   if e["event"] == "play_started"})
    if len(days) < 3:
        return []
    third_day = days[2]
    return [next(e for e in user_events if e["event"] == "play_started" and
                 parse(e["received_at"]).astimezone(IST).date() == third_day)]


def main(pack, run_dir):
    sim_dir = os.path.join(run_dir, "sim")
    events = defaultdict(list)
    with open(os.path.join(sim_dir, "outcome_events.jsonl"), encoding="utf-8") as f:
        for line in f:
            if line.strip():
                e = json.loads(line)
                events[e["user_id"]].append(e)

    titles = {}
    with open(os.path.join(pack, "data/catalogue.csv"), encoding="utf-8") as f:
        for row in csv.DictReader(f):
            titles[row["title_id"]] = row

    db = sqlite3.connect(os.path.join(run_dir, "run.db"))
    event_title = {r[0]: json.loads(r[1]).get("title_id") for r in db.execute(
        "SELECT event_id, properties FROM events WHERE event='play_started'")}
    db.close()

    decisions = {}
    with open(os.path.join(run_dir, "decisions.jsonl"), encoding="utf-8") as f:
        for line in f:
            if line.strip():
                d = json.loads(line)
                decisions[d["decision_id"]] = d

    ledger = []
    with open(os.path.join(run_dir, "goals_ledger.jsonl"), encoding="utf-8") as f:
        for line in f:
            if line.strip():
                ledger.append(json.loads(line))

    results = []
    for assignment in ledger:
        d = decisions[assignment["decision_id"]]
        goal = d.get("goal") or {}
        gid = goal.get("id") or assignment.get("goal_id")
        success = goal.get("success") or {}
        start, deadline = parse(assignment["start"]), parse(assignment["deadline"])
        user_events = [e for e in events.get(assignment["viewer_id"], [])
                       if start <= parse(e["received_at"]) <= deadline]
        signal = success.get("event")
        matching = []
        target_slug = None
        if gid == "G07":
            prior_ids = [x.split(":", 1)[1] for x in d.get("evidence", [])
                         if x.startswith("event:") and x.split(":", 1)[1] != d["trigger"]["event_id"]]
            for eid in prior_ids:
                tid = event_title.get(eid)
                if tid in titles:
                    target_slug = titles[tid]["content_slug"]
                    break

        for e in user_events:
            props = e.get("properties") or {}
            ev = e["event"]
            if ev != signal:
                continue
            qualifies = False
            if gid in ("G01", "G12"):
                qualifies = True
            elif gid == "G02":
                qualifies = True
            elif gid in ("G04", "G05"):
                qualifies = ev == "purchase_completed" and not is_trial(props)
            elif gid == "G06":
                qualifies = (ev == "purchase_completed" and
                             (not is_trial(props) or props.get("plan_id") not in (None, "P_TRIAL")))
            elif gid == "G07":
                tid = props.get("title_id")
                qualifies = bool(target_slug and tid in titles and titles[tid]["content_slug"] == target_slug)
            elif gid == "G08":
                tid = props.get("title_id")
                released = titles.get(tid, {}).get("release_date")
                start_day = start.astimezone(IST).date()
                qualifies = bool(released and
                                 (start_day - timedelta(days=30)).isoformat() <= released <= start_day.isoformat())
            elif gid == "G03":
                event_day = parse(e["received_at"]).astimezone(IST).date()
                qualifies = event_day > start.astimezone(IST).date()
            if qualifies:
                matching.append(e)

        if gid == "G09":
            matching = g09_success_events(user_events)

        if matching:
            status = "SUCCEEDED"
            result_event = min(matching, key=lambda e: e["received_at"])
            reason = "qualifying success signal observed within the goal window"
            event_id = result_event["event_id"]
            observed_at = result_event["received_at"]
        elif deadline > SIMULATION_END.astimezone(deadline.tzinfo):
            status, reason, event_id, observed_at = "INCONCLUSIVE", "simulator horizon ended before the goal deadline", None, None
        else:
            status, reason, event_id, observed_at = "FAILED", "full goal window observed without a qualifying success signal", None, None

        results.append({"viewer_id": assignment["viewer_id"], "arm": assignment["arm"],
                        "decision_id": assignment["decision_id"], "goal_id": gid,
                        "goal_start": assignment["start"], "deadline": assignment["deadline"],
                        "rule_version": assignment.get("rule_version", goal.get("rule_version")),
                        "status": status, "reason": reason, "success_event_id": event_id,
                        "success_observed_at": observed_at, "target_content_slug": target_slug})

    with open(os.path.join(run_dir, "outcomes.jsonl"), "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    by_viewer = defaultdict(list)
    for r in results:
        by_viewer[r["viewer_id"]].append(r)
    state_dir = os.path.join(run_dir, "viewer_state")
    for u, rs in by_viewer.items():
        path = os.path.join(state_dir, f"{u}.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                state = json.load(f)
            state["outcomes"] = rs
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=1)
    counts = {s: sum(r["status"] == s for r in results) for s in ("SUCCEEDED", "FAILED", "INCONCLUSIVE")}
    print("goal outcomes:", len(results), counts)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
