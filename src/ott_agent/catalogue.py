"""Step 4a: catalogue, availability, quarantine and dialect-safe content selection."""
import csv, json, re
from collections import Counter, defaultdict
from datetime import datetime, timedelta

PAGE_TTL = timedelta(minutes=120)     # interventions.json: home_page.ttl_minutes
NEW_RELEASE_DAYS = 30                 # goals.json G08 uses 30 days
INJECTION = re.compile(r"system note|assistant instruction|ignore (your|all|previous)|"
                       r"ai agents?|every user|opted out|skip all checks|pre-approved", re.I)


def parse(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class Catalogue:
    def __init__(self, pack):
        self.titles, self.by_slug = {}, defaultdict(list)
        self.slugs_by_dialect = defaultdict(list)
        self.episodes = defaultdict(list)
        self.withdrawn = {}        # content_slug -> withdrawal time
        self.quarantined = {}      # title_id -> reason
        with open(f"{pack}/data/catalogue.csv", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                r["from_dt"] = parse(r["available_from"])
                r["until_dt"] = parse(r["available_until"]) if r["available_until"] else None
                r["premium"] = r["is_premium"] == "true"
                r["genre_list"] = r["genres"].split("|")
                self.titles[r["title_id"]] = r
                if r["content_slug"] not in self.by_slug:
                    self.slugs_by_dialect[r["dialect"]].append(r["content_slug"])
                self.by_slug[r["content_slug"]].append(r["title_id"])
                # Data is not instruction: flagged records are never shown or named.
                if INJECTION.search(r["title"] + " " + r["description"]):
                    self.quarantined[r["title_id"]] = "instruction-like text in catalogue record"
        with open(f"{pack}/data/episodes.csv", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                self.episodes[r["content_slug"]].append((int(r["season"]), int(r["episode"]), r["episode_id"]))
        for v in self.episodes.values():
            v.sort()

    def withdraw(self, title_id, at_iso):
        t = self.titles.get(title_id)
        if t:   # conservative: block every language variant of the same content
            self.withdrawn[t["content_slug"]] = parse(at_iso)

    def usable(self, title_id, at, dialect):
        """In the dialect, live now, and still live for the whole life of a page."""
        t = self.titles.get(title_id)
        if not t or title_id in self.quarantined or t["dialect"] != dialect:
            return False
        if t["from_dt"] > at:
            return False
        if t["until_dt"] and t["until_dt"] < at + PAGE_TTL:
            return False
        w = self.withdrawn.get(t["content_slug"])
        return not (w and w <= at)

    def candidates(self, dialect, language, at):
        """slug -> title_id: one usable title per content, preferring the viewer's language, then Hindi."""
        out = {}
        for slug in self.slugs_by_dialect.get(dialect, []):
            ok = [i for i in self.by_slug[slug] if self.usable(i, at, dialect)]
            if ok:
                ok.sort(key=lambda i: (self.titles[i]["language"] != language,
                                       self.titles[i]["language"] != "hin"))
                out[slug] = ok[0]
        return out


def popularity(db, cat):
    pop = Counter()
    for (props,) in db.execute("SELECT properties FROM events WHERE event='play_started'"):
        t = cat.titles.get(json.loads(props).get("title_id"))
        if t:
            pop[t["content_slug"]] += 1
    return pop


def progress(db, cat, user_id, as_of_iso):
    """Per content_slug: last play time, furthest episode reached, number of plays."""
    out = {}
    for eid, bt, props in db.execute(
            "SELECT event_id,best_time,properties FROM events "
            "WHERE user_id=? AND event='play_started' AND best_time<=? ORDER BY best_time",
            (user_id, as_of_iso)):
        p = json.loads(props)
        t = cat.titles.get(p.get("title_id"))
        if not t:
            continue
        slug = t["content_slug"]
        ids = [e[2] for e in cat.episodes.get(slug, [])]
        idx = ids.index(p["episode_id"]) if p.get("episode_id") in ids else 0
        o = out.setdefault(slug, {"furthest": idx, "plays": 0, "n_eps": len(ids)})
        o["furthest"] = max(o["furthest"], idx)
        o["plays"] += 1
        o["last_play"], o["event"] = bt, eid
    return out


def g07(cat, prog, avail, at):
    """G07: a show/micro-drama with episodes left, idle 3+ days, showable in the viewer's dialect now."""
    found = []
    for slug, p in prog.items():
        if slug not in avail or cat.titles[cat.by_slug[slug][0]]["content_type"] == "film":
            continue
        if p["furthest"] >= p["n_eps"] - 1:
            continue
        if (at - parse(p["last_play"])).days < 3:
            continue
        found.append({"slug": slug, "title_id": avail[slug], "event": p["event"], "last_play": p["last_play"]})
    return sorted(found, key=lambda x: x["last_play"], reverse=True)