"""Step 4b: dialect resolution, goal choice (with G07) and home-page planning."""
import csv, time
from collections import Counter
from datetime import timedelta

try:
    from .catalogue import Catalogue, popularity, progress, g07, NEW_RELEASE_DAYS
    from .facts import build_facts, load_profiles, parse
    from .goals import GoalBook, evaluate, choose, iso
except ImportError:
    from catalogue import Catalogue, popularity, progress, g07, NEW_RELEASE_DAYS
    from facts import build_facts, load_profiles, parse
    from goals import GoalBook, evaluate, choose, iso

DIALECTS = {f"d{i}" for i in range(1, 7)}
STALE_TRIGGER_S = 600                      # my constant: an open older than 10 minutes cannot use a page
FREE_FIRST = {"G01", "G02", "G03", "G12"}  # assumption: is_premium means a paid plan is needed
ROW_ORDER = {
    "default": ["next_episode", "because_you_watched", "new_releases", "popular_in_dialect"],
    "newcomer": ["popular_in_dialect", "new_releases", "because_you_watched", "next_episode"],
    "G08": ["new_releases", "because_you_watched", "popular_in_dialect", "next_episode"],
}


def trigger_time(e):
    """Best estimate of when the open happened (same rule as ingest.py)."""
    received = parse(e["received_at"])
    try:
        occurred, sent = parse(e["occurred_at"]), parse(e["sent_at"])
        offset = (sent - received).total_seconds()
        best = occurred - timedelta(seconds=offset) if abs(offset) > 300 else occurred
        return min(best, received)
    except (KeyError, ValueError, TypeError):
        return received


def resolve_dialect(trig, F, profile):
    used = trig.get("app_dialect")
    others = []
    if profile:
        others.append(("profile", profile["preferred_dialect"]))
    link = (trig.get("properties") or {}).get("deeplink")
    if link:
        parts = link.split("/")            # ott://c/<campaign>/<dialect>/title/<id>
        if len(parts) > 4:
            others.append(("deeplink", parts[4]))
    if F["dialect"]["value"]:
        others.append(("last_history_setting", F["dialect"]["value"]))
    conflicts = sorted({f"{s}:{d}" for s, d in others if d and d != used})
    return {"used": used, "source": "app_setting:trigger", "conflicts": conflicts}


class Planner:
    def __init__(self, pack, db):
        self.db = db
        self.cat = Catalogue(pack)
        self.book = GoalBook(f"{pack}/config/goals.json")
        self.profiles = load_profiles(f"{pack}/data/profiles_snapshot.csv")
        with open(f"{pack}/replay/arms.csv", encoding="utf-8") as f:
            self.arms = {r["user_id"]: r["arm"] for r in csv.DictReader(f)}
        self.pop = popularity(db, self.cat)
        self.n = 0
        self.advisor = None

    def apply_control(self, ev):
        if ev["type"] == "title_withdrawn":
            self.cat.withdraw(ev["title_id"], ev["at"])
        elif ev["type"] == "goal_rule_corrected":
            self.book.apply_correction(ev["goal_id"], ev["new_success"], ev["at"])

    # ---- page building ----
    def _lists(self, goal_id, avail, prog, at, g07slugs):
        cat, pop = self.cat, self.pop
        free_first = goal_id in FREE_FIRST
        played = set(prog)
        T = lambda s: cat.titles[avail[s]]
        ff = (lambda lst: sorted(lst, key=lambda s: T(s)["premium"])) if free_first else (lambda lst: lst)
        gc = Counter()
        for s, p in prog.items():
            for g in cat.titles[cat.by_slug[s][0]]["genre_list"]:
                gc[g] += p["plays"]
        genres = {g for g, _ in gc.most_common(2)}
        cut = (at - timedelta(days=NEW_RELEASE_DAYS)).date().isoformat()
        unfinished = [s for s, p in prog.items() if s in avail and T(s)["content_type"] != "film"
                      and p["furthest"] < p["n_eps"] - 1]
        unfinished = sorted(unfinished, key=lambda s: prog[s]["last_play"], reverse=True)
        unfinished = sorted(unfinished, key=lambda s: s not in g07slugs)       # G07 series first
        fresh = sorted([s for s in avail if T(s)["release_date"] >= cut and s not in played],
                       key=lambda s: T(s)["release_date"], reverse=True)
        liked = sorted([s for s in avail if s not in played and set(T(s)["genre_list"]) & genres],
                       key=lambda s: -pop[s])
        popular = sorted([s for s in avail if s not in played], key=lambda s: -pop[s])
        return {"next_episode": unfinished, "because_you_watched": ff(liked),
                "new_releases": ff(fresh), "popular_in_dialect": ff(popular)}

    def _page(self, goal_id, dialect, avail, prog, at, g07slugs):
        lists = self._lists(goal_id, avail, prog, at, g07slugs)
        key = "newcomer" if goal_id in FREE_FIRST else ("G08" if goal_id == "G08" else "default")
        order = ROW_ORDER[key]
        hero = next((lists[r][0] for r in order if lists[r]), None)
        if hero is None:
            return None
        seen, rows = {hero}, []
        for r in order:
            titles = [s for s in lists[r] if s not in seen][:10]
            if len(titles) >= 3 and len(rows) < 6:
                rows.append({"row_type": r, "title_ids": [avail[s] for s in titles]})
                seen.update(titles)
        if not rows:
            return None
        return {"dialect": dialect, "hero_title_id": avail[hero], "rows": rows}

    # ---- the decision ----
    def plan(self, trig):
        t0 = time.perf_counter()
        ms = lambda: round((time.perf_counter() - t0) * 1000, 1)
        self.n += 1
        u, at_iso = trig["user_id"], trig["deliver_at"]
        deliver = parse(at_iso)
        age = (deliver - trigger_time(trig)).total_seconds()
        arm = self.arms.get(u)
        profile = self.profiles.get(u)
        F = build_facts(self.db, profile, u, at_iso)
        dialect = resolve_dialect(trig, F, profile)
        lang = trig.get("app_language") or "hin"
        valid_dialect = dialect["used"] in DIALECTS
        avail = self.cat.candidates(dialect["used"], lang, deliver) if valid_dialect else {}
        prog = progress(self.db, self.cat, u, at_iso)

        res = evaluate(self.db, F, profile, u, at_iso, trig["event_id"])
        series = g07(self.cat, prog, avail, deliver)
        if series:
            res["G07"] = {"goal_id": "G07", "eligible": True,
                          "reason": f"started {series[0]['slug']}, episodes left, idle 3+ days",
                          "evidence": [f"event:{series[0]['event']}"], "confidence": 0.8,
                          "assumptions": ["'episodes left' = the furthest episode played is not the last one"]}
        else:
            res["G07"] = {"goal_id": "G07", "eligible": False, "confidence": 0.0, "evidence": [],
                          "assumptions": [], "reason": "no unfinished series idle 3+ days that is showable in this dialect"}
        gid, g = choose(res)
        model_note = None
        if self.advisor and arm == "agent" and (age <= STALE_TRIGGER_S or trig.get("_repair")):
            gid, g, model_note = self.advisor(res, gid, g, trig, deliver)

        d = {"decision_id": f"D-{self.n:05d}", "viewer_id": u,
             "trigger": {"event": trig["event"], "event_id": trig["event_id"],
                         "received_at": trig["received_at"], "event_age_s": round(age, 1)},
             "arm": arm, "goal": None, "action": "NO_ACTION", "no_action_code": None,
             "no_action_reason": None, "dialect": dialect, "content": [],
             "confidence": 0.0, "confidence_reason": "",
             "evidence": [f"event:{trig['event_id']}"], "assumptions": [],
             "checks": {"passed": [], "failed": [], "status": "pending_verifier"},
             "status": "PLANNED"}
        if gid:
            hours = self.book.goals[gid]["success"]["window_hours"]
            d["goal"] = {"id": gid, "rule_version": len(self.book.history[gid]),
                         "deadline": iso(deliver + timedelta(hours=hours)),
                         "success": self.book.current_success(gid),
                         "shadow": arm != "agent", "committed": False}
            d["evidence"] += g["evidence"]
            d["assumptions"] += g["assumptions"]
            d["model"] = model_note

        def stop(code, text):
            d.update(no_action_code=code, no_action_reason=text, status="NO_ACTION_RECORDED",
                     decided_in_ms=ms())
            if not gid:
                d["considered"] = {k: v["reason"] for k, v in res.items()}
            return d

        if arm == "holdout":
            return stop("holdout_arm", "holdout arm: policy 5 forbids any agent action "
                                       "(the goal shown is a shadow goal, kept for fair measurement)")
        if arm != "agent":
            return stop("arm_unknown", "cannot prove this user is in the agent arm")
        if not valid_dialect:
            return stop("invalid_dialect", f"app dialect {dialect['used']!r} is not one of d1-d6")
        if age > STALE_TRIGGER_S and not trig.get("_repair"):
            return stop("stale_trigger", f"the open happened {age/3600:.1f} h before delivery; "
                                         "kept as evidence, no action")
        if not gid:
            return stop("no_goal", "no goal is provably eligible")
        page = self._page(gid, dialect["used"], avail, prog, deliver, {s["slug"] for s in series})
        if page is None:
            return stop("thin_content", f"fewer than 3 valid titles for a row in {dialect['used']}")

        ids = [page["hero_title_id"]] + [t for r in page["rows"] for t in r["title_ids"]]
        d.update(action="HOME_PAGE", page=page, content=[f"title:{t}" for t in ids],
                 decided_in_ms=ms())
        d["goal"]["committed"] = True
        d["evidence"].append(f"catalogue:{page['hero_title_id']}")
        d["assumptions"].append("title variant per content chosen by app language, falling back to Hindi")
        d["confidence"] = round(min(g["confidence"], 0.85), 2)
        d["confidence_reason"] = (f"goal evidence {g['confidence']} ({g['reason']}); dialect is the live app "
                                  f"setting (0.85); dialect conflicts: {dialect['conflicts'] or 'none'}")
        return d