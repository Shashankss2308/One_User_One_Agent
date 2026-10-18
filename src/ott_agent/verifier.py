"""Step 5: independent verifier. Re-reads raw files; imports nothing from planner/catalogue/goals/facts."""
import csv, json, re
from datetime import datetime, timedelta

TTL = timedelta(minutes=120)
STALE_S = 600
DIALECTS = {f"d{i}" for i in range(1, 7)}
BAD = re.compile(r"system note|assistant instruction|ignore (your|all|previous)|ai agents?|"
                 r"every user|opted out|skip all checks|pre-approved", re.I)
STATUS_GOALS = {"G04": "trial_active", "G05": "never", "G06": "lapsed", "G08": "active", "G09": "active"}
UNMEASURABLE = {"G10", "G11"}


def P(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class Verifier:
    def __init__(self, pack, db):
        self.db = db
        self.titles = {r["title_id"]: r for r in csv.DictReader(open(f"{pack}/data/catalogue.csv", encoding="utf-8"))}
        self.arms = {r["user_id"]: r["arm"] for r in csv.DictReader(open(f"{pack}/replay/arms.csv", encoding="utf-8"))}
        self.profile = {}
        for r in csv.DictReader(open(f"{pack}/data/profiles_snapshot.csv", encoding="utf-8")):
            o = self.profile.get(r["user_id"])
            if o is None or r["updated_at"] > o["updated_at"]:
                self.profile[r["user_id"]] = r
        self.withdrawn = {}

    def withdraw(self, title_id, at):
        t = self.titles.get(title_id)
        if t:
            self.withdrawn[t["content_slug"]] = P(at)

    def status(self, u, at_iso):
        p = self.profile.get(u)
        st, snap = (p["subscription_status"], p["snapshot_at"]) if p else ("unknown", "")
        q = ("SELECT event, properties, best_time FROM events WHERE user_id=? AND best_time<=? AND event IN "
             "('purchase_completed','subscription_renewed','subscription_lapsed') ORDER BY best_time")
        for ev, props, t in self.db.execute(q, (u, at_iso)):
            if t > snap:
                if ev == "subscription_lapsed":
                    st = "lapsed"
                elif ev == "subscription_renewed":
                    st = "active"
                else:
                    st = "trial_active" if str(json.loads(props).get("is_trial")).lower() == "true" else "active"
        return st

    def verify(self, d, trig):
        ok, bad = [], []

        def chk(name, cond, why=""):
            ok.append(name) if cond else bad.append(f"{name}: {why}")

        u = d["viewer_id"]
        arm = self.arms.get(u)
        chk("arm_matches_record", d.get("arm") == arm, f"decision says {d.get('arm')}, file says {arm}")
        if d["action"] == "NO_ACTION":
            return ok, bad

        deliver = P(trig["deliver_at"])
        chk("arm_is_agent", arm == "agent", f"arm is {arm}")
        if trig.get("_repair"):
            ok.append("freshness_skipped_repair")
        else:   # age = server lag + how long the phone says it buffered (robust to a wrong phone clock)
            try:
                age = (deliver - P(trig["received_at"])).total_seconds() + \
                      max(0, (P(trig["sent_at"]) - P(trig["occurred_at"])).total_seconds())
            except (KeyError, ValueError, TypeError):
                age = 1e9
            chk("trigger_fresh", age <= STALE_S, f"trigger age {age:.0f}s")

        page = d.get("page") or {}
        used = d["dialect"]["used"]
        chk("dialect_valid", used in DIALECTS and used == trig.get("app_dialect") == page.get("dialect"),
            f"used={used} app={trig.get('app_dialect')} page={page.get('dialect')}")

        rows = page.get("rows", [])
        ids = [page.get("hero_title_id")] + [t for r in rows for t in r["title_ids"]]
        problems, slugs = [], []
        for tid in ids:
            t = self.titles.get(tid)
            if not t:
                problems.append(f"{tid} not in catalogue")
                continue
            slugs.append(t["content_slug"])
            if t["dialect"] != used:
                problems.append(f"{tid} wrong dialect")
            if P(t["available_from"]) > deliver:
                problems.append(f"{tid} not live yet")
            if t["available_until"] and P(t["available_until"]) < deliver + TTL:
                problems.append(f"{tid} ends within the page life")
            w = self.withdrawn.get(t["content_slug"])
            if w and w <= deliver:
                problems.append(f"{tid} withdrawn")
            if BAD.search(t["title"] + " " + t["description"]):
                problems.append(f"{tid} has instruction-like text")
        if len(slugs) != len(set(slugs)):
            problems.append("duplicate content on page")
        if not rows or len(rows) > 6 or any(not 3 <= len(r["title_ids"]) <= 10 for r in rows):
            problems.append("row shape invalid")
        chk("titles_valid", not problems, "; ".join(problems[:3]))

        g = d.get("goal")
        chk("goal_present", g is not None, "action without a goal")
        if g:
            chk("goal_measurable", g["id"] not in UNMEASURABLE, f"{g['id']} cannot be judged")
            if g["id"] in STATUS_GOALS:
                s = self.status(u, trig["deliver_at"])
                chk("goal_status_matches", s == STATUS_GOALS[g["id"]], f"raw status is {s}")
            else:
                ok.append("goal_not_rechecked_independently")
        return ok, bad