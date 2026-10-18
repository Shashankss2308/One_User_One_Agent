"""Catalogue check: variants, availability, injected text, dialect behaviour."""
import csv, re, sqlite3, sys, json
from collections import Counter, defaultdict
from datetime import datetime, timedelta

def parse(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))

pack, db_path = sys.argv[1], sys.argv[2]
NOW = parse("2026-09-30T18:30:00Z")          # replay day starts 2026-10-01 00:00 IST
rows = list(csv.DictReader(open(f"{pack}/data/catalogue.csv", encoding="utf-8")))
print("titles:", len(rows), "| distinct title_id:", len({r["title_id"] for r in rows}))
print("per dialect:", dict(sorted(Counter(r["dialect"] for r in rows).items())))
print("per language:", dict(Counter(r["language"] for r in rows)))

by_slug = defaultdict(list)
for r in rows:
    by_slug[r["content_slug"]].append(r)
multi = {s: v for s, v in by_slug.items() if len(v) > 1}
print("slugs:", len(by_slug), "| slugs with 2+ title_ids:", len(multi),
      "| slugs spanning 2+ dialects:", sum(1 for v in by_slug.values() if len({x['dialect'] for x in v}) > 1))
for s, v in list(multi.items())[:2]:
    print("  example", s, [(x["title_id"], x["dialect"], x["language"]) for x in v])

def state(r):
    if parse(r["available_from"]) > NOW: return "not_yet"
    if r["available_until"] and parse(r["available_until"]) < NOW: return "ended"
    return "available"
print("availability:", dict(Counter(state(r) for r in rows)))
soon = [r["title_id"] for r in rows if r["available_until"] and NOW <= parse(r["available_until"]) <= NOW + timedelta(days=3)]
print("ending within 3 days:", len(soon), soon[:5])

print("== per dialect: available now / non-premium / released in last 30 days ==")
cut = (NOW - timedelta(days=30)).date().isoformat()
for d in sorted({r["dialect"] for r in rows}):
    av = [r for r in rows if r["dialect"] == d and state(r) == "available"]
    print(f"  {d}  available={len(av):4d}  free={sum(r['is_premium']=='false' for r in av):4d}"
          f"  new30d={sum(r['release_date']>=cut for r in av):3d}")

pat = re.compile(r"system note|ignore (your|all|previous)|ai agent|every user|opted out", re.I)
bad = [r["title_id"] for r in rows if pat.search(r["description"] + " " + r["title"])]
print("titles with instruction-like text:", len(bad), bad[:10])

eps = {r["content_slug"] for r in csv.DictReader(open(f"{pack}/data/episodes.csv", encoding="utf-8"))}
print("catalogue slugs with no episodes:", sum(1 for s in by_slug if s not in eps))

db = sqlite3.connect(db_path)
cat = {r["title_id"]: r for r in rows}
n = unknown = mismatch = 0
for (dia, props) in db.execute("SELECT app_dialect, properties FROM events WHERE event='play_started'"):
    t = json.loads(props).get("title_id")
    n += 1
    if t not in cat: unknown += 1
    elif cat[t]["dialect"] != dia: mismatch += 1
print(f"plays: {n} | title not in catalogue: {unknown} | title dialect != app dialect: {mismatch}")