"""Step 3: goal engine. Plain rules over facts and events. No model is involved."""
import json, sqlite3, sys
from collections import Counter
from datetime import timedelta

try:
    from .facts import build_facts, load_profiles, parse
except ImportError:
    from facts import build_facts, load_profiles, parse

LAPSED_RECENT_DAYS = 30     # goals.json says only "recently lapsed": my documented choice
UNMEASURABLE = {
    "G10": "success event is null in goals.json, so it cannot be judged",
    "G11": "tv_app_open never appears in the pack, so it cannot be judged",
}
PRIORITY = ["G04", "G06", "G01", "G03", "G12", "G07", "G05", "G09", "G08", "G02"]
IST = timedelta(hours=5, minutes=30)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def ist_day(t):
    return (parse(t) + IST).date()


class GoalBook:
    """Goal rules with versions: a mid-day correction is a new version, not an overwrite."""
    def __init__(self, path):
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        self.goals = {g["goal_id"]: g for g in d["goals"]}
        self.history = {gid: [{"rule_version": 1, "success": g["success"], "effective_from": None}]
                        for gid, g in self.goals.items()}

    def current_success(self, goal_id):
        return self.history[goal_id][-1]["success"]

    def apply_correction(self, goal_id, new_success, at):
        h = self.history[goal_id]
        h.append({"rule_version": len(h) + 1, "success": new_success, "effective_from": at})


def times(db, user_id, name, as_of_iso, since_iso="", exclude_id=None):
    sql = ("SELECT best_time FROM events WHERE user_id=? AND event=? AND best_time<=? "
           "AND best_time>=? AND event_id IS NOT ?")
    return [r[0] for r in db.execute(sql, (user_id, name, as_of_iso, since_iso, exclude_id))]


def evaluate(db, F, profile, user_id, as_of_iso, trigger_id=None):
    as_of = parse(as_of_iso)
    status = F["subscription_status"]
    st, st_conf = status["value"], status["confidence"]
    st_stale = "plan_ends_at_passed" in status["conflicts"]
    signup = F["signup_at"]["value"]
    age_days = (as_of - parse(signup)).total_seconds() / 86400 if signup else None
    out = {}

    def put(gid, ok, reason, evidence=(), conf=0.0, assumptions=()):
        out[gid] = {"goal_id": gid, "eligible": ok, "reason": reason,
                    "evidence": list(evidence), "confidence": conf, "assumptions": list(assumptions)}

    def by_status(gid, wanted):
        if st == "unknown":
            put(gid, False, "subscription status unknown (unknown is not a match)")
        elif st_stale:
            put(gid, False, "status may be stale: the plan end date has already passed")
        elif st != wanted:
            put(gid, False, f"status is {st}, goal needs {wanted}")
        else:
            put(gid, True, f"status {st} from {status['source']}", [status["source"]], st_conf)

    for gid, why in UNMEASURABLE.items():
        put(gid, False, "unmeasurable: " + why)

    # G01 first play: a user who signed up <=7 days ago has their whole life inside our 30-day window,
    # so "never played" is reliable evidence for them.
    plays = times(db, user_id, "play_started", as_of_iso)
    if age_days is None:
        put("G01", False, "signup date unknown")
    elif age_days > 7:
        put("G01", False, f"signed up {age_days:.1f} days ago (limit 7)")
    elif plays:
        put("G01", False, "has already started a play")
    else:
        put("G01", True, "signed up within 7 days and no play_started", [F["signup_at"]["source"]],
            F["signup_at"]["confidence"])

    # G02 first full episode: "never" is provable only if we can see the user's whole life (<=30 days)
    if times(db, user_id, "episode_completed", as_of_iso):
        put("G02", False, "has already completed an episode")
    elif age_days is None or age_days > 30:
        put("G02", False, "cannot prove 'never completed': we only see 30 days of this user's history")
    else:
        put("G02", True, "signed up within 30 days and no episode_completed in their whole visible life",
            ["events_30d_window", F["signup_at"]["source"]], 0.9)

    # G03 second-day return
    if age_days is not None and age_days <= 2:
        put("G03", True, "signed up within 2 days", [F["signup_at"]["source"]], F["signup_at"]["confidence"])
    else:
        put("G03", False, "signup unknown or older than 2 days")

    by_status("G04", "trial_active")
    by_status("G05", "never")

    # G06 reactivation: the lapse must be dated, and "recent" is my constant
    if st != "lapsed":
        put("G06", False, f"status is {st}")
    else:
        lapses = times(db, user_id, "subscription_lapsed", as_of_iso)
        lapse_at = max(lapses) if lapses else (profile["plan_ends_at"] if profile and profile["plan_ends_at"] else None)
        if lapse_at is None:
            put("G06", False, "cannot date the lapse")
        else:
            d = (as_of - parse(lapse_at)).days
            put("G06", d <= LAPSED_RECENT_DAYS, f"lapsed {d} days ago (recent = {LAPSED_RECENT_DAYS} days)",
                [status["source"]], st_conf, ["'recently lapsed' is undefined in goals.json; I chose 30 days"])

    put("G07", False, "not built yet: needs catalogue and episodes (added in Step 4)")

    by_status("G08", "active")

    # G09 weekly habit: active, plays on at most 1 IST day in the last 14 days
    by_status("G09", "active")
    if out["G09"]["eligible"]:
        since = iso(as_of - timedelta(days=14))
        days = {ist_day(t) for t in times(db, user_id, "play_started", as_of_iso, since)}
        if len(days) > 1:
            out["G09"]["eligible"] = False
            out["G09"]["reason"] = f"plays on {len(days)} different days in the last 14 (limit 1)"
        else:
            out["G09"]["reason"] += f"; plays on {len(days)} day(s) in the last 14"

    # G12 return after absence: exclude the trigger itself, and require proof of earlier visits
    since = iso(as_of - timedelta(days=14))
    recent = times(db, user_id, "app_open", as_of_iso, since, trigger_id)
    if recent:
        put("G12", False, f"{len(recent)} app_open(s) in the 14 days before the trigger")
    elif age_days is None or age_days <= 14:
        put("G12", False, "no evidence the user visited before, so we cannot say they stopped")
    else:
        put("G12", True, "no app_open in the 14 days before the trigger", ["events_30d_window"], 0.8,
            ["buffered opens from the phone may still arrive; re-judge if they do"])
    return out


def choose(results):
    for gid in PRIORITY:
        if results[gid]["eligible"]:
            return gid, results[gid]
    return None, None


if __name__ == "__main__":
    # usage: python src/ott_agent/goals.py <pack_dir> <state.db>
    pack, db_path = sys.argv[1], sys.argv[2]
    AS_OF = "2026-09-30T18:30:00.000Z"
    profiles = load_profiles(f"{pack}/data/profiles_snapshot.csv")
    db = sqlite3.connect(db_path)
    users = [r[0] for r in db.execute("SELECT DISTINCT user_id FROM events")]

    eligible, chosen, example = Counter(), Counter(), None
    for u in users:
        F = build_facts(db, profiles.get(u), u, AS_OF)
        res = evaluate(db, F, profiles.get(u), u, AS_OF)
        for gid, r in res.items():
            eligible[gid] += r["eligible"]
        gid, r = choose(res)
        chosen[gid or "NO_GOAL"] += 1
        if example is None and gid:
            example = (u, gid, r)

    print("users:", len(users))
    print("\nusers ELIGIBLE per goal:")
    for gid in sorted(eligible):
        print(f"  {gid}  {eligible[gid]}")
    print("\nusers by CHOSEN goal:")
    for gid, n in chosen.most_common():
        print(f"  {gid:8s}{n}")
    print("\nexample decision:", json.dumps(example, indent=1))

    print("\n== the 3 users the model_faults file claims are G04-eligible ==")
    for u in ["U-01991", "U-02509", "U-04860"]:
        F = build_facts(db, profiles.get(u), u, AS_OF)
        r = evaluate(db, F, profiles.get(u), u, AS_OF)["G04"]
        print(u, "G04 eligible:", r["eligible"], "|", r["reason"])

    book = GoalBook(f"{pack}/config/goals.json")
    book.apply_correction("G03", {"event": "play_started"}, "2026-10-01T08:30:00.000Z")
    print("\nG03 rule history:", json.dumps(book.history["G03"], indent=1))