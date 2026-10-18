"""Steps 6-7: replay runtime. Idempotent triggers, fallbacks, repairs, audit outputs."""
import json, os, sqlite3, sys, time
from collections import Counter, defaultdict, deque
from datetime import timedelta
try:
    from .planner import Planner
    from .ingest import parse, fmt, SKEW_LIMIT_S, BUFFER_LIMIT_S
    from .providers import MockProvider
    from .verifier import Verifier
except ImportError:
    from planner import Planner
    from ingest import parse, fmt, SKEW_LIMIT_S, BUFFER_LIMIT_S
    from providers import MockProvider
    from verifier import Verifier

TTL = timedelta(minutes=120)


def ingest_one(db, e):
    """Same clock rules as ingest.py. Returns False if this event_id was already stored."""
    received = parse(e["received_at"])
    best, offset, buf, sk, bf = received, None, None, 0, 0
    try:
        occ, sent = parse(e["occurred_at"]), parse(e["sent_at"])
        offset, buf = (sent - received).total_seconds(), (sent - occ).total_seconds()
        sk, bf = int(abs(offset) > SKEW_LIMIT_S), int(buf > BUFFER_LIMIT_S)
        best = min(occ - timedelta(seconds=offset) if sk else occ, received)
    except (KeyError, ValueError, TypeError):
        pass
    cur = db.execute("INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (e["event_id"], e["user_id"], e["event"], e.get("occurred_at"), e.get("sent_at"),
                      e["received_at"], fmt(best), offset, buf, sk, bf, e.get("app_dialect"),
                      e.get("app_language"), e.get("session_id"), json.dumps(e.get("properties", {}))))
    return cur.rowcount == 1


def load(path):
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


class Runtime:
    def __init__(self, pack, base_db, out, load_limit=40):
        self.pack, self.out, self.load_limit = pack, out, load_limit
        os.makedirs(out, exist_ok=True)
        run_db = os.path.join(out, "run.db")
        for s in ("", "-wal", "-shm"):
            if os.path.exists(run_db + s):
                os.remove(run_db + s)
        src, self.db = sqlite3.connect(base_db), sqlite3.connect(run_db)
        src.backup(self.db)
        src.close()
        self.db.execute("CREATE TABLE IF NOT EXISTS decisions (trigger_id TEXT PRIMARY KEY, body TEXT)")
        self.P = Planner(pack, self.db)
        self.provider = MockProvider(f"{pack}/replay/model_faults.jsonl")
        self.V = Verifier(pack, self.db)
        self.P.advisor = self.advise
        self.open, self.assigned, self.pages = {}, {}, {}
        self.outages, self.store_degraded, self.recent, self.shed = [], [], deque(), False
        self.stats, self.decisions, self.actions = Counter(), [], []
        self.corrections, self.controls_seen = [], []
        self.live_log = defaultdict(list)

    # ---- the model is only an advisor; rules stay in charge ----
    def advise(self, res, gid, g, trig, deliver):
        note, u = {"model_called": False}, trig["user_id"]
        eligible = [k for k, v in res.items() if v["eligible"]]
        if eligible and self.shed:
            note["fallback"] = "rules_only: load_shed"
            self.stats["model_skipped_load_shed"] += 1
        elif eligible and not trig.get("_repair"):
            self.provider.down = any(a <= deliver < b for a, b in self.outages)
            try:
                out = self.provider.advise(trig["event_id"], eligible, gid)
                note["model_called"] = True
                claimed = out.get("goal_id") if isinstance(out, dict) else None
                note["model_claim"] = {"goal_id": claimed, "reason": str(out.get("reason", ""))[:200]
                                       if isinstance(out, dict) else ""}
                if claimed in eligible:
                    if claimed != gid:
                        gid, g, note["reranked"] = claimed, res[claimed], True
                else:
                    note["rejected_claim"] = {"goal_id": claimed,
                                              "rules_say": res.get(claimed, {}).get("reason", "unknown goal")}
                    self.stats["model_claims_rejected"] += 1
            except Exception as ex:
                note["fallback"] = f"rules_only: {type(ex).__name__}: {ex}"
                self.stats["model_failure_fallbacks"] += 1
        cur = self.open.get(u)   # one active plan: keep the open goal while it is still eligible
        if cur and parse(cur["deadline"]) > deliver and res.get(cur["goal_id"], {}).get("eligible") \
                and cur["goal_id"] != gid:
            gid, g, note["kept_active_goal"] = cur["goal_id"], res[cur["goal_id"]], cur["goal_id"]
        return gid, g, note

    # ---- one trigger ----
    def handle(self, trig):
        tid = trig["event_id"]
        if self.db.execute("SELECT 1 FROM decisions WHERE trigger_id=?", (tid,)).fetchone():
            self.stats["duplicate_trigger_ignored"] += 1
            return
        repair = bool(trig.get("_repair"))
        if not repair:
            ingest_one(self.db, trig)
        deliver = parse(trig["deliver_at"])
        self.recent.append(deliver)
        while (deliver - self.recent[0]).total_seconds() > 60:
            self.recent.popleft()
        self.shed = len(self.recent) > self.load_limit
        self.stats["load_shed_decisions"] += self.shed
        t0 = time.perf_counter()
        try:
            if any(start <= deliver < end for start, end in self.store_degraded):
                self.P.n += 1
                d = {"decision_id": f"D-{self.P.n:05d}", "viewer_id": trig["user_id"],
                     "arm": self.V.arms.get(trig["user_id"]),
                     "trigger": {"event": trig["event"], "event_id": tid,
                                 "received_at": trig["received_at"]},
                     "goal": None, "action": "NO_ACTION", "no_action_code": "dependency_degraded",
                     "no_action_reason": "user data store is degraded; failed closed without planning",
                     "content": [], "model": {"model_called": False,
                                                   "fallback": "rules_only: user_data_store_degraded"},
                     "dialect": {"used": trig.get("app_dialect"), "conflicts": []},
                     "evidence": [f"event:{tid}"], "assumptions": [],
                     "status": "NO_ACTION_RECORDED"}
                self.stats["data_store_degraded_no_actions"] += 1
            else:
                d = self.P.plan(trig)
        except Exception as ex:   # fail safe: do nothing, record why
            d = {"decision_id": f"D-{self.P.n:05d}", "viewer_id": trig["user_id"], "arm": self.V.arms.get(trig["user_id"]),
                 "trigger": {"event": trig["event"], "event_id": tid, "received_at": trig["received_at"]},
                 "goal": None, "action": "NO_ACTION", "no_action_code": "planner_error",
                 "no_action_reason": f"planner failed: {ex}", "content": [], "model": None,
                 "dialect": {"used": trig.get("app_dialect"), "conflicts": []}, "evidence": [f"event:{tid}"],
                 "assumptions": [], "status": "NO_ACTION_RECORDED"}
            self.stats["planner_errors"] += 1
        if repair:
            d["trigger"]["repair"] = True
        try:
            ok, bad = self.V.verify(d, trig)
        except Exception as ex:   # fail closed
            ok, bad = [], [f"verifier_error: {ex}"]
        d["checks"] = {"passed": ok, "failed": bad, "status": "vetoed" if bad else "verified"}
        if bad and d["action"] != "NO_ACTION":
            d.update(action="NO_ACTION", no_action_code="verifier_veto", content=[], status="NO_ACTION_RECORDED",
                     no_action_reason="verifier vetoed the planned action: " + "; ".join(bad))
            d.pop("page", None)
            if d.get("goal"):
                d["goal"]["committed"] = False
            self.stats["verifier_vetoes"] += 1
        ms = round((time.perf_counter() - t0) * 1000, 1)
        d["decided_in_ms"] = ms
        d["budget"] = {"model_calls": int(bool(d.get("model") and d["model"].get("model_called"))),
                       "max_model_calls": 8, "load_shed": self.shed}
        u = d["viewer_id"]
        if d.get("goal") and u not in self.assigned and not repair:   # same rule for both arms (fair comparison)
            self.assigned[u] = {"viewer_id": u, "arm": d["arm"], "goal_id": d["goal"]["id"],
                                "rule_version": d["goal"]["rule_version"], "start": trig["deliver_at"],
                                "deadline": d["goal"]["deadline"], "decision_id": d["decision_id"]}
        if d["action"] == "HOME_PAGE":
            pub = fmt(deliver + timedelta(milliseconds=ms))
            d["status"], d["published_at"] = "PUBLISHED", pub
            pg = d["page"]
            self.actions.append({"action_id": f"A-{len(self.actions)+1:05d}", "decision_id": d["decision_id"],
                                 "user_id": u, "trigger_event_id": tid, "type": "home_page", "published_at": pub,
                                 "dialect": pg["dialect"], "hero_title_id": pg["hero_title_id"], "rows": pg["rows"]})
            self.open[u] = {"goal_id": d["goal"]["id"], "deadline": d["goal"]["deadline"]}
            ids = [pg["hero_title_id"]] + [t for r in pg["rows"] for t in r["title_ids"]]
            self.pages[u] = {"published_at": pub, "trigger": {k: v for k, v in trig.items() if k != "_repair"},
                             "slugs": {self.P.cat.titles[i]["content_slug"] for i in ids}}
        self.db.execute("INSERT INTO decisions VALUES (?,?)", (tid, json.dumps(d)))
        self.decisions.append(d)
        if len(self.decisions) % 200 == 0:
            self.db.commit()

    # ---- control events ----
    def control(self, c):
        t = c["type"]
        if t == "title_withdrawn":
            self.P.apply_control(c)
            self.V.withdraw(c["title_id"], c["at"])
            tt = self.P.cat.titles.get(c["title_id"])
            slug, at, n = tt and tt["content_slug"], parse(c["at"]), 0
            for u, p in list(self.pages.items()):   # only viewers whose live page contains it
                if slug in p["slugs"] and parse(p["published_at"]) + TTL > at:
                    n += 1
                    self.handle(dict(p["trigger"], event_id=f"repair:{c['title_id']}:{p['trigger']['event_id']}",
                                     deliver_at=c["at"], _repair=True))
            self.stats["viewers_repaired_after_withdrawal"] += n
        elif t == "goal_rule_corrected":
            self.P.apply_control(c)
            aff = [u for u, a in self.assigned.items() if a["goal_id"] == c["goal_id"]
                   and parse(a["deadline"]) > parse(c["at"])]
            self.corrections.append({"at": c["at"], "goal_id": c["goal_id"], "new_success": c["new_success"],
                                     "affected_open_goals": len(aff), "viewers": aff})
        elif t == "dependency_unavailable" and c.get("dependency") == "model_provider":
            self.outages.append((parse(c["at"]), parse(c["until"])))
        elif t == "dependency_degraded" and c.get("dependency") == "user_data_store":
            self.store_degraded.append((parse(c["at"]), parse(c["until"])))
            self.stats["data_store_degradation_windows"] += 1
        else:
            self.controls_seen.append(c)
            self.stats["control_logged_" + t] += 1

    def run(self):
        P = self.pack
        items = [(t["deliver_at"], 1, "t", t) for t in load(f"{P}/replay/triggers.jsonl")]
        items += [(e["deliver_at"], 1, "l", e) for e in load(f"{P}/replay/live_events.jsonl")]
        items += [(c["at"], 0, "c", c) for c in load(f"{P}/replay/control_events.jsonl")]
        items.sort(key=lambda x: (x[0], x[1]))
        for _, _, kind, obj in items:
            if kind == "c":
                self.control(obj)
            elif kind == "l":
                if ingest_one(self.db, obj):
                    self.live_log[obj["user_id"]].append({"event": obj["event"], "at": obj["received_at"]})
                    self.stats["live_events_ingested"] += 1
                else:
                    self.stats["live_event_duplicates"] += 1
            else:
                self.handle(obj)
        self.db.commit()
        self.write()

    def write(self):
        o = self.out
        dump = lambda name, rows: open(os.path.join(o, name), "w", encoding="utf-8").write(
            "".join(json.dumps(r) + "\n" for r in rows))
        dump("decisions.jsonl", self.decisions)
        dump("actions.jsonl", self.actions)
        dump("goals_ledger.jsonl", self.assigned.values())
        dump("corrections.jsonl", self.corrections)
        ms = sorted(d["decided_in_ms"] for d in self.decisions)
        q = lambda p: ms[min(len(ms) - 1, int(len(ms) * p))]
        json.dump({"decisions": len(ms), "p50_ms": q(.5), "p95_ms": q(.95), "p99_ms": q(.99), "max_ms": ms[-1],
                   "over_60s": sum(m > 60000 for m in ms), "over_5min": sum(m > 300000 for m in ms),
                   "stats": dict(self.stats)},
                  open(os.path.join(o, "latency_report.json"), "w"), indent=1)
        vs = os.path.join(o, "viewer_state")
        os.makedirs(vs, exist_ok=True)
        by = defaultdict(list)
        for d in self.decisions:
            by[d["viewer_id"]].append(d)
        acts = defaultdict(list)
        for a in self.actions:
            acts[a["user_id"]].append(a["action_id"])
        for u, ds in by.items():
            json.dump({"viewer_id": u, "arm": ds[0]["arm"], "assigned_goal": self.assigned.get(u),
                       "decisions": [{"decision_id": d["decision_id"], "action": d["action"],
                                      "goal": d["goal"] and d["goal"]["id"], "reason": d["no_action_code"],
                                      "checks_failed": d["checks"]["failed"]} for d in ds],
                       "actions": acts[u], "live_events_seen": self.live_log.get(u, []), "outcomes": []},
                      open(os.path.join(vs, f"{u}.json"), "w"), indent=1)


if __name__ == "__main__":
    # usage: python src/ott_agent/runtime.py <pack_dir> <state.db> <out_dir>
    pack, base, out = sys.argv[1:4]
    R = Runtime(pack, base, out)
    R.run()
    acts, codes = Counter(d["action"] for d in R.decisions), Counter(d["no_action_code"] for d in R.decisions)
    print("decisions:", len(R.decisions), dict(acts))
    print("no-action codes:", dict(codes))
    print("stats:", dict(R.stats))
    print("latency:", {k: v for k, v in json.load(open(f"{out}/latency_report.json")).items() if k != "stats"})
    print("corrections:", [{k: v for k, v in c.items() if k != "viewers"} for c in R.corrections])
    print("other control events:", R.controls_seen)
    byid = {d["trigger"]["event_id"]: d for d in R.decisions}
    print("\nmodel faults:")
    for f in load(f"{pack}/replay/model_faults.jsonl"):
        d = byid.get(f["trigger_event_id"])
        m = d and d.get("model")
        what = "no decision found" if not d else (
            "REJECTED model claim: " + str(m["rejected_claim"]) if m and m.get("rejected_claim") else
            "fallback: " + m["fallback"] if m and m.get("fallback") else
            f"model not used (arm={d['arm']}, action={d['action']})")
        print(" ", f["user_id"], f["fault"], "->", what)
