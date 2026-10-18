"""Quick data checks: duplicates, profiles, cold-start users."""
import csv, json, sqlite3, sys
from collections import Counter

pack, db_path = sys.argv[1], sys.argv[2]

# 1. What differs between duplicate copies of the same event_id?
first, diff_fields, n, later = {}, Counter(), 0, 0
with open(f"{pack}/data/events.jsonl", encoding="utf-8") as f:
    for line in f:
        e = json.loads(line)
        old = first.get(e["event_id"])
        if old is None:
            first[e["event_id"]] = e
            continue
        n += 1
        for k in e:
            if e[k] != old.get(k):
                diff_fields[k] += 1
        if e["received_at"] > old["received_at"]:
            later += 1
print("duplicate copies:", n)
print("fields that differ:", dict(diff_fields))
print("copy arrived later than the first:", later)

# 2. Profile snapshot problems
with open(f"{pack}/data/profiles_snapshot.csv", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))
ids = Counter(r["user_id"] for r in rows)
print("profile rows:", len(rows), "| distinct users:", len(ids),
      "| users with 2+ rows:", sum(1 for c in ids.values() if c > 1))

db = sqlite3.connect(db_path)
ev_users = {r[0] for r in db.execute("SELECT DISTINCT user_id FROM events")}
print("users with events but no profile:", len(ev_users - set(ids)))

# 3. Replay-day users: how many are cold-start?
trig_users = set()
with open(f"{pack}/replay/triggers.jsonl", encoding="utf-8") as f:
    for line in f:
        trig_users.add(json.loads(line)["user_id"])
print("replay trigger users:", len(trig_users))
print("  with no history events:", len(trig_users - ev_users))
print("  with no profile row:", len(trig_users - set(ids)))
print("  with neither:", len(trig_users - ev_users - set(ids)))