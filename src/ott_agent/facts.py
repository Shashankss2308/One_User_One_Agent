"""Step 2: facts with source, freshness and visible conflicts."""
import csv, json, sqlite3, sys
from collections import Counter
from datetime import datetime, timedelta

PLAN_DAYS = {"P_TRIAL": 3, "P_MONTH": 30, "P_QUARTER": 90}   # from config/pricing.json

def parse(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_profiles(path):
    """One row per user; if a user has two versions, keep the latest updated_at."""
    latest = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            old = latest.get(r["user_id"])
            if old is None or r["updated_at"] > old["updated_at"]:
                latest[r["user_id"]] = r
    return latest


def fact(value, source, observed_at, as_of, confidence, conflicts=None):
    age = (as_of - parse(observed_at)).total_seconds() if observed_at else None
    return {"value": value, "source": source, "observed_at": observed_at,
            "age_s": age, "confidence": confidence, "conflicts": conflicts or []}


def status_from(e):
    if e["event"] == "subscription_lapsed":
        return "lapsed"
    if e["event"] == "subscription_renewed":
        return "active"
    is_trial = str(e["props"].get("is_trial")).lower() == "true"
    return "trial_active" if is_trial else "active"


SUB_EVENTS = ("purchase_completed", "subscription_renewed", "subscription_lapsed")


def build_facts(db, profile, user_id, as_of_iso):
    as_of = parse(as_of_iso)
    cols = ("id", "event", "t", "dialect", "props")
    ev = [dict(zip(cols, r)) for r in db.execute(
        "SELECT event_id,event,best_time,app_dialect,properties FROM events "
        "WHERE user_id=? AND best_time<=? ORDER BY best_time", (user_id, as_of_iso))]
    for e in ev:
        e["props"] = json.loads(e["props"])
    F = {}

    # --- subscription status ---
    sub_ev = [e for e in ev if e["event"] in SUB_EVENTS]
    snap = profile["subscription_status"] if profile else None
    newer = [e for e in sub_ev if not profile or e["t"] > profile["snapshot_at"]]
    if newer:
        last = newer[-1]
        val = status_from(last)
        conf = [f"profile:{snap}"] if snap and snap != val else []
        c = 0.9
        days = PLAN_DAYS.get(last["props"].get("plan_id"))
        if val in ("trial_active", "active") and days and parse(last["t"]) + timedelta(days=days) < as_of:
            conf.append("plan_ends_at_passed")   # plan should have ended; no renewal or lapse seen
            c = 0.5
        F["subscription_status"] = fact(val, f"event:{last['id']}", last["t"], as_of, c, conf)
    elif profile:
        conf, c = [], 0.8
        ends = profile["plan_ends_at"]
        if snap in ("trial_active", "active") and ends and ends < as_of_iso:
            conf.append("plan_ends_at_passed")
            c = 0.5
        F["subscription_status"] = fact(snap, "profile_snapshot", profile["snapshot_at"], as_of, c, conf)
    else:
        F["subscription_status"] = fact("unknown", "none", None, as_of, 0.0)

    # --- consent (unknown = no consent) ---
    opt_outs = [e for e in ev if e["event"] == "notification_opt_out"]
    for name, field, channel in (("push_consent", "notifications_opt_in", "push"),
                                 ("whatsapp_consent", "whatsapp_opt_in", "whatsapp")):
        value, src, at, c = False, "no_profile_default_deny", None, 0.0
        if profile:
            value, src, at, c = profile[field] == "true", "profile_snapshot", profile["snapshot_at"], 0.8
        for e in opt_outs:
            ch = e["props"].get("channel")
            # an opt-out for an unknown channel name is treated as opting out of both
            if ch == channel or ch not in ("push", "whatsapp"):
                if not profile or e["t"] > profile["snapshot_at"]:
                    value, src, at, c = False, f"event:{e['id']}", e["t"], 0.95
        F[name] = fact(value, src, at, as_of, c)

    # --- dialect (app setting wins; others stay visible as conflicts) ---
    last_ev = ev[-1] if ev else None
    others = []
    if profile:
        others.append(("profile", profile["preferred_dialect"]))
    last_open = next((e for e in reversed(ev) if e["event"] == "app_open"
                      and e["props"].get("deeplink")), None)
    if last_open:
        parts = last_open["props"]["deeplink"].split("/")   # ott://c/<campaign>/<dialect>/title/<id>
        if len(parts) > 4:
            others.append(("deeplink", parts[4]))
    if last_ev and last_ev["dialect"]:
        v = last_ev["dialect"]
        F["dialect"] = fact(v, f"app_setting:event:{last_ev['id']}", last_ev["t"], as_of, 0.85,
                            [f"{s}:{d}" for s, d in others if d != v])
    elif profile:
        F["dialect"] = fact(profile["preferred_dialect"], "profile_snapshot", profile["snapshot_at"], as_of, 0.4)
    else:
        F["dialect"] = fact(None, "none", None, as_of, 0.0)

    # --- behaviour facts (only the 30-day window is visible) ---
    def first(name):
        return next((e for e in ev if e["event"] == name), None)
    su = first("signup_completed")
    if profile:
        F["signup_at"] = fact(profile["signup_at"], "profile_snapshot", profile["snapshot_at"], as_of, 0.8)
    elif su:
        F["signup_at"] = fact(su["t"], f"event:{su['id']}", su["t"], as_of, 0.9)
    else:
        F["signup_at"] = fact(None, "none", None, as_of, 0.0)
    F["has_started_play"] = fact(first("play_started") is not None, "events_30d_window", None, as_of, 0.6)
    F["has_completed_episode"] = fact(first("episode_completed") is not None, "events_30d_window", None, as_of, 0.6)
    opens = [e for e in ev if e["event"] == "app_open"]
    F["last_app_open"] = fact(opens[-1]["t"] if opens else None,
                              f"event:{opens[-1]['id']}" if opens else "none",
                              opens[-1]["t"] if opens else None, as_of, 0.9 if opens else 0.0)
    return F


if __name__ == "__main__":
    # usage: python src/ott_agent/facts.py <pack_dir> <state.db>
    pack, db_path = sys.argv[1], sys.argv[2]
    AS_OF = "2026-09-30T18:30:00.000Z"        # just after the last history event
    profiles = load_profiles(f"{pack}/data/profiles_snapshot.csv")
    db = sqlite3.connect(db_path)
    users = [r[0] for r in db.execute("SELECT DISTINCT user_id FROM events")]
    tally, examples = Counter(), {}
    for u in users:
        F = build_facts(db, profiles.get(u), u, AS_OF)
        tally["no_profile"] += u not in profiles
        tally["status_unknown"] += F["subscription_status"]["value"] == "unknown"
        for c in F["subscription_status"]["conflicts"]:
            tally["status_conflict:" + c.split(":")[0]] += 1
        for c in F["dialect"]["conflicts"]:
            tally["dialect_conflict:" + c.split(":")[0]] += 1
        if F["dialect"]["conflicts"]:
            examples.setdefault("dialect_conflict", u)
        if u not in profiles:
            examples.setdefault("no_profile", u)
    print("users analysed:", len(users))
    for k, v in sorted(tally.items()):
        print(f"  {k:35s}{v}")
    for label, u in examples.items():
        print("\n== example:", label, u)
        print(json.dumps(build_facts(db, profiles.get(u), u, AS_OF), indent=1)[:1800])